from __future__ import annotations

import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.email_crypto import (
    EmailKeyError,
    decrypt_secret,
    default_key_path,
    encrypt_secret,
    key_fingerprint,
)


class EmailCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.key_path = Path(self.tmp.name) / "email.key"
        self.old = os.environ.get("CODATA_EMAIL_KEY_FILE")
        os.environ["CODATA_EMAIL_KEY_FILE"] = str(self.key_path)

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("CODATA_EMAIL_KEY_FILE", None)
        else:
            os.environ["CODATA_EMAIL_KEY_FILE"] = self.old
        self.tmp.cleanup()

    def test_key_path_override(self) -> None:
        self.assertEqual(default_key_path(), self.key_path)

    def test_roundtrip_creates_key(self) -> None:
        token = encrypt_secret("smtp-secret")
        self.assertNotIn("smtp-secret", token)
        self.assertTrue(self.key_path.is_file())
        self.assertEqual(decrypt_secret(token), "smtp-secret")
        self.assertEqual(len(key_fingerprint() or ""), 12)

    def test_wrong_key_cannot_decrypt(self) -> None:
        token = encrypt_secret("smtp-secret")
        self.key_path.unlink()
        self.key_path.write_bytes(b"invalid-key")
        with self.assertRaises(EmailKeyError):
            decrypt_secret(token)

    def test_invalid_key_file_raises_email_key_error(self) -> None:
        self.key_path.write_bytes(b"not-a-valid-fernet-key")
        with self.assertRaises(EmailKeyError):
            encrypt_secret("value")
        self.assertIsNone(key_fingerprint())

    def test_missing_key_does_not_create_on_read(self) -> None:
        with self.assertRaises(EmailKeyError):
            decrypt_secret("token")
        self.assertFalse(self.key_path.exists())
    def test_concurrent_first_use_shares_one_key(self) -> None:
        with ThreadPoolExecutor(max_workers=12) as executor:
            tokens = list(executor.map(lambda index: encrypt_secret(f"secret-{index}"), range(24)))
        for index, token in enumerate(tokens):
            self.assertEqual(decrypt_secret(token), f"secret-{index}")
        self.assertTrue(self.key_path.is_file())
        self.assertEqual(len(self.key_path.read_bytes().strip()), 44)


if __name__ == "__main__":
    unittest.main()
