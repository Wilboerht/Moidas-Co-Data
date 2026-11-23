from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.db import Database
from codata.migrate_leads import migrate


class MigrateLeadsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))
        self.round_a = self.db.create_round(["A"], pages=1, max_creators=None, name="A")
        self.round_b = self.db.create_round(["B"], pages=1, max_creators=None, name="B")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _import(self, round_id: int, user_id: str, account_id: str, nickname: str) -> None:
        self.db.import_creator_rows(
            round_id,
            [
                {
                    "creator": {
                        "platform": "xhs",
                        "user_id": user_id,
                        "nickname": nickname,
                        "raw_json": "{}",
                    },
                    "round": {
                        "platform": "xhs",
                        "user_id": user_id,
                        "account_id": account_id,
                        "status": "shortlisted",
                        "match_direction": "护肤",
                        "raw_title": "导入标题",
                    },
                }
            ],
        )

    def test_migration_groups_identities_and_preserves_metadata(self) -> None:
        self._import(self.round_a, "import:legacy1", "real1", "达人甲")
        self._import(self.round_b, "import:legacy1", "real1", "达人甲")
        self._import(self.round_a, "import:legacy2", "", "待核实达人")
        self.db.claim_creator(self.round_a, "xhs", "import:legacy1", 1)

        report = migrate(self.db, apply=True)
        self.assertEqual(report["created"], 2)
        self.assertEqual(report["preserved_unresolved_rows"], 1)
        self.assertEqual(report["resolved_identities"], 1)
        self.assertEqual(report["unresolved_identities"], 1)

        lead = self.db._conn.execute(
            "SELECT * FROM leads WHERE legacy_user_id = ?", ("import:legacy1",)
        ).fetchone()
        self.assertIsNotNone(lead)
        self.assertEqual(lead["owner_user_id"], 1)
        self.assertEqual(lead["status"], "converted")
        rounds = self.db.lead_rounds(int(lead["id"]))
        self.assertEqual({row["round_id"] for row in rounds}, {self.round_a, self.round_b})

        preserved = self.db._conn.execute(
            "SELECT 1 FROM creator_rounds WHERE platform = 'xhs' AND user_id = ?",
            ("import:legacy2",),
        ).fetchone()
        self.assertIsNotNone(preserved)
        self.assertEqual(migrate(self.db, apply=True)["created"], 0)

    def test_migration_remaps_lead_references(self) -> None:
        self._import(self.round_a, "import:legacy3", "real3", "达人丙")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="旧引用线索", account_id="import:legacy3"
        )
        self.db._conn.execute(
            """
            UPDATE leads SET matched_platform = 'xhs', matched_user_id = 'import:legacy3',
                converted_platform = 'xhs', converted_user_id = 'import:legacy3'
            WHERE id = ?
            """,
            (lead_id,),
        )
        self.db._conn.commit()
        migrate(self.db, apply=True)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "duplicate")
        self.assertEqual(lead["matched_user_id"], "real3")
        self.assertEqual(lead["converted_user_id"], "real3")
        active = self.db._conn.execute(
            "SELECT * FROM leads WHERE account_id = 'real3' AND status <> 'duplicate'"
        ).fetchone()
        self.assertIsNotNone(active)
        self.assertEqual(active["status"], "converted")

    def test_migration_skips_existing_account_and_continues(self) -> None:
        self.db.create_lead(
            platform="xhs", nickname="已有账号线索", account_id="real4"
        )
        self._import(self.round_a, "import:legacy4", "real4", "达人丁")
        self._import(self.round_b, "import:legacy5", "real5", "达人戊")
        report = migrate(self.db, apply=True)
        self.assertGreaterEqual(report["skipped_existing"], 1)
        self.assertEqual(report["created"], 1)
        created = self.db._conn.execute(
            "SELECT 1 FROM leads WHERE account_id = 'real5'"
        ).fetchone()
        self.assertIsNotNone(created)


if __name__ == "__main__":
    unittest.main()
