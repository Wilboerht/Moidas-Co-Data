from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.db import Database
from codata.email_crypto import encrypt_secret, key_fingerprint
from codata.email_sender import EmailSendError
from codata.email_worker import _within_window, run_once
from codata.normalize import normalize_user_info
from helpers import user_payload

FIXED_NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)  # Monday


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def __call__(
        self,
        account,
        password,
        *,
        to,
        subject,
        body,
        message_id=None,
        headers=None,
    ):
        self.calls.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "message_id": message_id,
                "headers": headers or {},
            }
        )
        if self.error is not None:
            raise self.error
        return f"<message-{len(self.calls)}@example.com>"


class EmailWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.key = Path(self.tmp.name) / "email.key"
        self.old_key = os.environ.get("CODATA_EMAIL_KEY_FILE")
        os.environ["CODATA_EMAIL_KEY_FILE"] = str(self.key)
        self.db = Database(Path(self.tmp.name) / "data")
        self.db.create_user(
            "owner", "hash", role="admin", can_send_email=True
        )
        self.round_id = self.db.create_round(
            ["咖啡"], pages=1, max_creators=None, name="MAIL-WORKER"
        )
        for user_id, nickname, email in (
            ("u1", "达人一", "one@example.com"),
            ("u2", "达人二", "two@example.com"),
        ):
            self.db.upsert_creator(normalize_user_info(user_payload(user_id, nickname)))
            self.db.add_creator_round(self.round_id, "xhs", user_id)
            self.db.update_review(self.round_id, "xhs", user_id, status="adopted")
            self.db.add_creator_email("xhs", user_id, email, is_primary=True)
        self.account_id = self.db.create_email_account(
            scope="personal",
            owner_user_id=1,
            label="测试",
            from_name="运营",
            from_email="ops@example.com",
            reply_to="",
            host="smtp.example.com",
            port=587,
            security="starttls",
            username="ops@example.com",
            password_encrypted=encrypt_secret("password"),
            key_fingerprint=key_fingerprint() or "",
            daily_limit=20,
            min_interval_seconds=1,
            created_by=1,
        )

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()
        if self.old_key is None:
            os.environ.pop("CODATA_EMAIL_KEY_FILE", None)
        else:
            os.environ["CODATA_EMAIL_KEY_FILE"] = self.old_key

    def _campaign(
        self,
        *,
        users: list[tuple[str, str]],
        daily_limit: int = 20,
        interval: int = 1,
        auto_mark: bool = True,
        created_by: int = 1,
    ) -> int:
        result = self.db.create_email_campaign(
            name="外联",
            account_id=self.account_id,
            subject="Hi {{nickname}}",
            body="你好 {{nickname}}",
            timezone="UTC",
            window_start="00:00",
            window_end="23:59",
            weekdays="[1,2,3,4,5,6,7]",
            interval_seconds=interval,
            jitter_seconds=0,
            daily_limit=daily_limit,
            auto_mark_contacted=auto_mark,
            recipients=[
                {"platform": "xhs", "user_id": user_id, "email": email}
                for user_id, email in users
            ],
            created_by=created_by,
        )
        self.db.schedule_email_campaign(
            result["campaign_id"],
            scheduled_start="2026-01-01T00:00:00+00:00",
            timezone="UTC",
            window_start="00:00",
            window_end="23:59",
            weekdays="[1,2,3,4,5,6,7]",
            interval_seconds=interval,
            jitter_seconds=0,
            daily_limit=daily_limit,
        )
        return result["campaign_id"]

    def test_sends_and_marks_contacted(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender()
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(sender.calls[0]["subject"], "Hi 达人一")
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "sent")
        self.assertTrue(recipient["message_id"])
        self.assertEqual(
            self.db.get_creator("xhs", "u1")["cooperation_status"], "contacted"
        )
        campaign = self.db.email_campaign_detail(campaign_id)
        self.assertEqual(campaign["status"], "completed")

    def test_daily_limit_blocks_second_send(self) -> None:
        campaign_id = self._campaign(
            users=[("u1", "one@example.com"), ("u2", "two@example.com")],
            daily_limit=1,
        )
        sender = FakeSender()
        run_once(self.db, sender=sender, now=FIXED_NOW)
        stats = run_once(
            self.db, sender=sender, now=FIXED_NOW + timedelta(minutes=5)
        )
        self.assertEqual(stats["sent"], 0)
        self.assertEqual(len(sender.calls), 1)
        pending = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM email_recipients "
            "WHERE campaign_id = ? AND status = 'pending'",
            (campaign_id,),
        ).fetchone()["n"]
        self.assertEqual(pending, 1)

    def test_auth_failure_pauses_campaign(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender(EmailSendError("认证失败", kind="auth"))
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["paused"], 1)
        campaign = self.db.email_campaign_detail(campaign_id)
        self.assertEqual(campaign["status"], "paused")
        account = self.db.get_email_account(self.account_id)
        self.assertTrue(account["auth_error"])
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "pending")

    def test_transient_failure_retries_then_fails(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender(EmailSendError("timeout", kind="transient"))
        run_once(self.db, sender=sender, now=FIXED_NOW)
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "pending")
        self.assertEqual(recipient["attempts"], 1)
        self.assertTrue(recipient["next_attempt_at"])
        run_once(self.db, sender=sender, now=FIXED_NOW + timedelta(minutes=2))
        run_once(self.db, sender=sender, now=FIXED_NOW + timedelta(minutes=12))
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "failed")
        self.assertGreaterEqual(recipient["attempts"], 3)

    def test_interval_blocks_immediate_second_send(self) -> None:
        campaign_id = self._campaign(
            users=[("u1", "one@example.com"), ("u2", "two@example.com")],
            interval=60,
        )
        sender = FakeSender()
        run_once(self.db, sender=sender, now=FIXED_NOW)
        run_once(self.db, sender=sender, now=FIXED_NOW + timedelta(seconds=5))
        self.assertEqual(len(sender.calls), 1)
        run_once(self.db, sender=sender, now=FIXED_NOW + timedelta(seconds=61))
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "completed"
        )

    def test_disabled_account_pauses_campaign(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db.update_email_account(self.account_id, enabled=False)
        sender = FakeSender()
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["paused"], 1)
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "paused"
        )
        self.assertEqual(len(sender.calls), 0)

    def test_stuck_sending_recipient_is_marked_failed(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db._conn.execute(
            "UPDATE email_recipients SET status = 'sending', claimed_at = ?, "
            "attempts = 1 WHERE campaign_id = ?",
            ("2000-01-01T00:00:00+00:00", campaign_id),
        )
        self.db._conn.commit()
        stats = run_once(self.db, sender=FakeSender(), now=FIXED_NOW)
        self.assertEqual(stats["recovered"], 1)
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "failed")
        self.assertIn("未知", recipient["error_message"])

    def test_suppressed_recipient_is_skipped(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db.add_email_suppression("one@example.com", reason="退订")
        sender = FakeSender()
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(len(sender.calls), 0)
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "skipped")
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "completed"
        )

    def test_status_change_skips_send(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db.update_cooperation("xhs", "u1", "contacted")
        sender = FakeSender()
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(len(sender.calls), 0)
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "skipped")
        self.assertEqual(recipient["error_code"], "status_changed")

    def test_cross_campaign_cooldown_skips_send(self) -> None:
        self._campaign(users=[("u1", "one@example.com")], auto_mark=False)
        self.assertEqual(
            run_once(self.db, sender=FakeSender(), now=FIXED_NOW)["sent"], 1
        )
        second = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender()
        stats = run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW + timedelta(minutes=1),
            cooldown_days=30,
        )
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(len(sender.calls), 0)
        recipient = self.db.email_campaign_recipients(second)["items"][0]
        self.assertEqual(recipient["error_code"], "cooldown")

    def test_revoked_creator_pauses_campaign(self) -> None:
        self.db.create_user(
            "member", "hash", role="member", can_send_email=True
        )
        campaign_id = self._campaign(
            users=[("u1", "one@example.com")], created_by=2
        )
        self.db.update_user(2, can_send_email=False)
        sender = FakeSender()
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["paused"], 1)
        self.assertEqual(len(sender.calls), 0)
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "paused"
        )

    def test_scheduled_campaign_with_no_pending_finishes(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db._conn.execute(
            "UPDATE email_recipients SET status = 'sent', sent_at = ?, "
            "sent_date = ? WHERE campaign_id = ?",
            (
                (FIXED_NOW - timedelta(minutes=5)).isoformat(),
                FIXED_NOW.date().isoformat(),
                campaign_id,
            ),
        )
        self.db._conn.commit()
        run_once(self.db, sender=FakeSender(), now=FIXED_NOW)
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "completed"
        )

    def test_cancelled_campaign_cannot_retry(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db.cancel_email_campaign(campaign_id)
        with self.assertRaises(ValueError):
            self.db.retry_failed_email_recipients(campaign_id)

    def test_sending_campaign_can_be_cancelled(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db._conn.execute(
            "UPDATE email_campaigns SET status = 'sending' WHERE id = ?",
            (campaign_id,),
        )
        self.db._conn.commit()
        self.db.cancel_email_campaign(campaign_id)
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "cancelled"
        )

    def test_fair_scheduling_does_not_starve_later_campaigns(self) -> None:
        for index in range(3, 9):
            user_id = f"u{index}"
            email = f"user{index}@example.com"
            self.db.upsert_creator(
                normalize_user_info(user_payload(user_id, f"达人{index}"))
            )
            self.db.add_creator_round(self.round_id, "xhs", user_id)
            self.db.update_review(self.round_id, "xhs", user_id, status="adopted")
            self.db.add_creator_email("xhs", user_id, email, is_primary=True)
            self._campaign(users=[(user_id, email)])
        sender = FakeSender()
        for step in range(6):
            run_once(
                self.db,
                sender=sender,
                max_sends=5,
                now=FIXED_NOW + timedelta(seconds=step * 2),
            )
        self.assertEqual(len(sender.calls), 6)
        emails = {call["to"] for call in sender.calls}
        self.assertEqual(len(emails), 6)

    def test_unsubscribe_link_and_headers_are_added(self) -> None:
        self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender()
        run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW,
            public_url="https://codata.example.com",
        )
        call = sender.calls[0]
        unsubscribe = call["headers"].get("List-Unsubscribe", "")
        self.assertIn("https://codata.example.com/api/email/unsubscribe?token=", unsubscribe)
        self.assertEqual(
            call["headers"].get("List-Unsubscribe-Post"),
            "List-Unsubscribe=One-Click",
        )
        self.assertIn("退订", call["body"])
        token = unsubscribe.strip("<>").split("token=", 1)[1]
        info = self.db.get_email_unsubscribe_token(token)
        self.assertIsNotNone(info)
        result = self.db.apply_email_unsubscribe(token)
        self.assertIsNotNone(result)
        normalized = "one@example.com"
        row = self.db._conn.execute(
            "SELECT 1 FROM email_suppressions WHERE email_normalized = ?",
            (normalized,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(
            self.db.get_email_unsubscribe_token(token)["already_used"]
        )

    def test_creator_cooldown_blocks_different_email(self) -> None:
        self._campaign(users=[("u1", "one@example.com")], auto_mark=False)
        run_once(self.db, sender=FakeSender(), now=FIXED_NOW)
        second = self._campaign(users=[("u1", "u1-alt@example.com")])
        sender = FakeSender()
        stats = run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW + timedelta(minutes=1),
        )
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(len(sender.calls), 0)
        recipient = self.db.email_campaign_recipients(second)["items"][0]
        self.assertEqual(recipient["error_code"], "creator_cooldown")

    def test_account_min_interval_applies_across_campaigns(self) -> None:
        self.db.update_email_account(self.account_id, min_interval_seconds=60)
        self._campaign(users=[("u1", "one@example.com")])
        self._campaign(users=[("u2", "two@example.com")])
        sender = FakeSender()
        run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(len(sender.calls), 1)
        run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW + timedelta(seconds=5),
        )
        self.assertEqual(len(sender.calls), 1)
        run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW + timedelta(seconds=61),
        )
        self.assertEqual(len(sender.calls), 2)

    def test_manual_retry_is_limited(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender(EmailSendError("拒绝", kind="permanent"))
        run_once(self.db, sender=sender, now=FIXED_NOW)
        for expected in (1, 2, 3):
            changed = self.db.retry_failed_email_recipients(campaign_id)
            self.assertEqual(changed, 1)
            run_once(
                self.db,
                sender=sender,
                now=FIXED_NOW + timedelta(minutes=expected * 10),
            )
        self.assertEqual(
            self.db.retry_failed_email_recipients(campaign_id), 0
        )
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["manual_retries"], 3)
        self.assertEqual(recipient["status"], "failed")

    def test_worker_lock_is_singleton(self) -> None:
        self.assertTrue(self.db.acquire_email_worker("a", now=FIXED_NOW.isoformat()))
        self.assertFalse(
            self.db.acquire_email_worker(
                "b", now=FIXED_NOW.isoformat(), stale_seconds=60
            )
        )
        self.assertTrue(
            self.db.acquire_email_worker(
                "b",
                now=(FIXED_NOW + timedelta(minutes=2)).isoformat(),
                stale_seconds=60,
            )
        )

    def test_retry_failed_recipients(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender(EmailSendError("timeout", kind="permanent"))
        run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(
            self.db.email_campaign_recipients(campaign_id)["items"][0]["status"],
            "failed",
        )
        retried = self.db.retry_failed_email_recipients(campaign_id)
        self.assertEqual(retried, 1)
        self.assertEqual(
            self.db.email_campaign_detail(campaign_id)["status"], "scheduled"
        )
        run_once(self.db, sender=FakeSender(), now=FIXED_NOW + timedelta(minutes=1))
        self.assertEqual(
            self.db.email_campaign_recipients(campaign_id)["items"][0]["status"],
            "sent",
        )
    def test_message_id_is_stable_across_retry(self) -> None:
        campaign_id = self._campaign(
            users=[("u1", "one@example.com")], auto_mark=False
        )
        failed_sender = FakeSender(EmailSendError("临时失败", kind="transient"))
        run_once(self.db, sender=failed_sender, now=FIXED_NOW)
        stored = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["message_id"], failed_sender.calls[0]["message_id"])
        sender = FakeSender()
        run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW + timedelta(minutes=2),
        )
        self.assertEqual(sender.calls[0]["message_id"], stored["message_id"])
        self.assertEqual(len(sender.calls), 1)

    def test_audit_failure_after_smtp_success_does_not_resend(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender()
        with patch.object(self.db, "audit", side_effect=RuntimeError("审计写失败")):
            stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(
            self.db.email_campaign_recipients(campaign_id)["items"][0]["status"],
            "sent",
        )
        run_once(self.db, sender=sender, now=FIXED_NOW + timedelta(minutes=2))
        self.assertEqual(len(sender.calls), 1)

    def test_sent_state_failure_is_unknown_and_never_auto_resent(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        self.db._conn.execute(
            """
            CREATE TRIGGER block_email_sent BEFORE UPDATE ON email_recipients
            WHEN NEW.status = 'sent'
            BEGIN SELECT RAISE(ABORT, 'blocked sent update'); END
            """
        )
        self.db._conn.commit()
        sender = FakeSender()
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["failed"], 1)
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "sending")
        self.db._conn.execute("DROP TRIGGER block_email_sent")
        self.db._conn.commit()
        recovered = run_once(
            self.db,
            sender=sender,
            now=FIXED_NOW + timedelta(minutes=11),
        )
        self.assertEqual(recovered["recovered"], 1)
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "failed")
        self.assertEqual(recipient["error_code"], "unknown")
        self.assertEqual(len(sender.calls), 1)

    def test_unknown_smtp_result_does_not_auto_retry(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender(EmailSendError("连接中断", kind="unknown"))
        stats = run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["retried"], 0)
        run_once(self.db, sender=sender, now=FIXED_NOW + timedelta(minutes=30))
        recipient = self.db.email_campaign_recipients(campaign_id)["items"][0]
        self.assertEqual(recipient["status"], "failed")
        self.assertEqual(recipient["error_code"], "unknown")
        self.assertEqual(len(sender.calls), 1)

    def test_pause_before_claim_prevents_smtp_call(self) -> None:
        campaign_id = self._campaign(users=[("u1", "one@example.com")])
        sender = FakeSender()

        def pause_after_ready(db, campaign, now):
            db.pause_email_campaign(int(campaign["id"]), "用户暂停")
            return True, ""

        with patch("codata.email_worker._campaign_ready", side_effect=pause_after_ready):
            run_once(self.db, sender=sender, now=FIXED_NOW)
        self.assertEqual(len(sender.calls), 0)
        self.assertEqual(
            self.db.email_campaign_recipients(campaign_id)["items"][0]["status"],
            "pending",
        )
        self.assertEqual(self.db.email_campaign_detail(campaign_id)["status"], "paused")

    def test_iso_weekday_mapping_covers_every_day(self) -> None:
        for day in range(1, 8):
            with self.subTest(day=day):
                campaign = {
                    "weekdays": f"[{day}]",
                    "window_start": "00:00",
                    "window_end": "23:59",
                }
                matching = FIXED_NOW + timedelta(days=day - 1)
                self.assertTrue(
                    _within_window(campaign, ZoneInfo("UTC"), matching)
                )
                self.assertFalse(
                    _within_window(
                        campaign, ZoneInfo("UTC"), matching + timedelta(days=1)
                    )
                )


if __name__ == "__main__":
    unittest.main()
