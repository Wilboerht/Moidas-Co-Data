from __future__ import annotations

import os
import smtplib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.email_sender import (
    EmailSendError,
    _open_server,
    _validate_host,
    send_plain_email,
    test_smtp,
)

ACCOUNT = {
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "username": "ops@example.com",
    "from_name": "运营",
    "from_email": "ops@example.com",
    "reply_to": "",
}


class FakeSMTP:
    def __init__(self, *args, **kwargs) -> None:
        self.messages = []
        self.started_tls = False
        self.logged_in = False
        self.quit_called = False
        self.close_called = False

    def ehlo(self) -> None:
        return None

    def starttls(self, context=None) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = True

    def send_message(self, message) -> None:
        self.messages.append(message)

    def quit(self) -> None:
        self.quit_called = True

    def close(self) -> None:
        self.close_called = True


class EmailSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_private = os.environ.pop("CODATA_SMTP_ALLOW_PRIVATE", None)
        self.old_plain = os.environ.pop("CODATA_SMTP_ALLOW_PLAINTEXT", None)

    def tearDown(self) -> None:
        for key, value in (
            ("CODATA_SMTP_ALLOW_PRIVATE", self.old_private),
            ("CODATA_SMTP_ALLOW_PLAINTEXT", self.old_plain),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_private_host_is_rejected_by_default(self) -> None:
        with patch(
            "codata.email_sender.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("10.0.0.5", 0))],
        ):
            with self.assertRaises(EmailSendError):
                _validate_host("smtp.internal")

    def test_private_host_allowed_with_flag(self) -> None:
        os.environ["CODATA_SMTP_ALLOW_PRIVATE"] = "true"
        with patch(
            "codata.email_sender.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("10.0.0.5", 0))],
        ):
            self.assertEqual(_validate_host("smtp.internal"), "smtp.internal")

    def test_plaintext_smtp_requires_flag(self) -> None:
        account = {**ACCOUNT, "security": "none"}
        with patch("codata.email_sender._validate_host", return_value="smtp.example.com"):
            with self.assertRaises(EmailSendError):
                _open_server(account, timeout=1)

    def test_header_injection_is_sanitized(self) -> None:
        fake = FakeSMTP()
        with patch("codata.email_sender._validate_host", return_value="smtp.example.com"):
            with patch("codata.email_sender.smtplib.SMTP", return_value=fake):
                message_id = send_plain_email(
                    ACCOUNT,
                    "password",
                    to="to@example.com",
                    subject="Hello\r\nBcc: evil@example.com",
                    body="正文",
                )
        self.assertTrue(message_id)
        self.assertTrue(fake.started_tls)
        self.assertTrue(fake.logged_in)
        message = fake.messages[0]
        self.assertNotIn("\r", message["Subject"])
        self.assertNotIn("\n", message["Subject"])
        self.assertNotIn("Bcc", message)

    def test_test_smtp_connect_only(self) -> None:
        fake = FakeSMTP()
        with patch("codata.email_sender._validate_host", return_value="smtp.example.com"):
            with patch("codata.email_sender.smtplib.SMTP", return_value=fake):
                result = test_smtp(ACCOUNT, "password")
        self.assertEqual(result, {"ok": True, "sent_test": False})
        self.assertTrue(fake.logged_in)
    def test_starttls_failure_closes_connection(self) -> None:
        fake = FakeSMTP()
        with patch.object(
            fake, "starttls", side_effect=smtplib.SMTPException("TLS unavailable")
        ):
            with patch(
                "codata.email_sender._validate_host", return_value="smtp.example.com"
            ):
                with patch("codata.email_sender.smtplib.SMTP", return_value=fake):
                    with self.assertRaises(EmailSendError):
                        _open_server(ACCOUNT)
        self.assertTrue(fake.quit_called)

    def test_fixed_message_id_and_unsubscribe_headers(self) -> None:
        fake = FakeSMTP()
        with patch("codata.email_sender._validate_host", return_value="smtp.example.com"):
            with patch("codata.email_sender.smtplib.SMTP", return_value=fake):
                result = send_plain_email(
                    ACCOUNT,
                    "password",
                    to="to@example.com",
                    subject="Subject",
                    body="Body",
                    message_id="<fixed-id@example.com>",
                    headers={"List-Unsubscribe": "<https://example.com/unsubscribe>"},
                )
        self.assertEqual(result, "<fixed-id@example.com>")
        self.assertEqual(fake.messages[0]["Message-ID"], "<fixed-id@example.com>")
        self.assertEqual(
            fake.messages[0]["List-Unsubscribe"],
            "<https://example.com/unsubscribe>",
        )

    def test_network_failure_during_send_is_unknown(self) -> None:
        fake = FakeSMTP()
        with patch.object(fake, "send_message", side_effect=OSError("connection lost")):
            with patch(
                "codata.email_sender._validate_host", return_value="smtp.example.com"
            ):
                with patch("codata.email_sender.smtplib.SMTP", return_value=fake):
                    with self.assertRaises(EmailSendError) as raised:
                        send_plain_email(
                            ACCOUNT,
                            "password",
                            to="to@example.com",
                            subject="Subject",
                            body="Body",
                        )
        self.assertEqual(raised.exception.kind, "unknown")
        self.assertTrue(fake.quit_called)


if __name__ == "__main__":
    unittest.main()
