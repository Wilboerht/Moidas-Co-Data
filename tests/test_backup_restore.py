from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.admin_cli import _backup, _restore, _verify_backup
from codata.config import Settings
from codata.db import Database


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.settings = Settings(host="", token="", output_dir=self.data)
        self.runtime_env = self.data / "runtime.env"
        (self.root / ".env").write_text("A=1\n", encoding="utf-8")
        self.runtime_env.write_text("TOKEN=runtime\n", encoding="utf-8")
        self.db = Database(self.data)
        self.round_id = self.db.create_round(
            ["咖啡"], pages=1, max_creators=None, name="BACKUP-ROUND"
        )
        attachments = self.data / "cooperation_attachments" / "1"
        attachments.mkdir(parents=True)
        (attachments / "quote.txt").write_text("evidence", encoding="utf-8")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _create_backup(self) -> Path:
        backup_root = self.root / "backups"
        _backup(
            self.db,
            self.settings,
            str(backup_root),
            project_root=self.root,
            runtime_env=self.runtime_env,
        )
        return next(backup_root.iterdir())

    def test_backup_manifest_and_verify(self) -> None:
        target = self._create_backup()
        report = _verify_backup(target)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["has_manifest"])
        self.assertTrue((target / "manifest.json").is_file())
        self.assertTrue(
            (target / "cooperation_attachments" / "1" / "quote.txt").is_file()
        )

    def test_restore_overwrites_database_env_and_attachments(self) -> None:
        target = self._create_backup()
        self.db.delete_round(self.round_id, force=True)
        (self.data / "cooperation_attachments" / "1" / "quote.txt").unlink()
        (self.root / ".env").write_text("A=2\n", encoding="utf-8")
        self.runtime_env.write_text("TOKEN=changed\n", encoding="utf-8")
        self.db.close()
        _restore(
            self.settings,
            str(target),
            check_only=False,
            yes=True,
            project_root=self.root,
            runtime_env=self.runtime_env,
        )
        self.db = Database(self.data)
        self.assertIsNotNone(self.db.get_round(self.round_id))
        self.assertEqual((self.root / ".env").read_text(encoding="utf-8"), "A=1\n")
        self.assertEqual(
            self.runtime_env.read_text(encoding="utf-8"), "TOKEN=runtime\n"
        )
        self.assertTrue(
            (self.data / "cooperation_attachments" / "1" / "quote.txt").is_file()
        )
        self.assertTrue(
            list(self.data.glob("codata.sqlite.bak-restore-before-*"))
        )

    def test_verify_detects_missing_file(self) -> None:
        target = self._create_backup()
        (target / "cooperation_attachments" / "1" / "quote.txt").unlink()
        report = _verify_backup(target)
        self.assertFalse(report["ok"])
        self.assertTrue(any("缺少文件" in item for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
