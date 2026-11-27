from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata import admin_cli
from codata.config import Settings
from codata.db import Database
from codata.diagnostics import config_warnings, status_report


class DiagnosticsTests(unittest.TestCase):
    def test_config_check_does_not_open_or_migrate_database(self) -> None:
        with patch.object(
            admin_cli,
            "Database",
            side_effect=AssertionError("config-check must not open the database"),
        ):
            admin_cli.main(["config-check"])

    def test_warnings_detect_risky_combinations(self) -> None:
        settings = Settings(
            host="",
            token="",
            allowed_hosts=("*",),
            allowed_origins=("https://panel.example.com",),
            cookie_secure=False,
            enable_docs=True,
        )
        warnings = "\n".join(config_warnings(settings))
        self.assertIn("通配符", warnings)
        self.assertIn("Secure", warnings)
        self.assertIn("docs", warnings)

    def test_secure_config_has_no_warnings(self) -> None:
        settings = Settings(
            host="",
            token="",
            allowed_hosts=("panel.example.com",),
            allowed_origins=("https://panel.example.com",),
            cookie_secure=True,
        )
        self.assertEqual(config_warnings(settings), [])

    def test_status_report_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp))
            settings = Settings(host="", token="", output_dir=Path(tmp))
            report = status_report(db, settings)
            db.close()
        for key in (
            "database",
            "schema_version",
            "counts",
            "active_jobs",
            "backups",
            "attachment_files",
            "frontend_ready",
            "disk_free_bytes",
            "warnings",
        ):
            self.assertIn(key, report)


if __name__ == "__main__":
    unittest.main()
