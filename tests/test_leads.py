from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.config import Settings
from codata.db import Database
from codata.leads import convert_lead
from codata.normalize import normalize_user_info
from helpers import user_payload


class LeadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))
        self.settings = Settings(host="", token="", output_dir=Path(self.tmp.name))
        self.round_id = self.db.create_round(
            ["测试"],
            pages=1,
            max_creators=None,
            name="LEAD-TEST",
            category="测试",
            categories={"测试": "测试"},
        )
        self.db.upsert_creator(normalize_user_info(user_payload("u1", "测试达人", fans=1000)))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_lead_owner_and_conversion(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="线索达人",
            account_id="u1",
            source_round_id=self.round_id,
            owner_user_id=1,
        )
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "unverified")
        self.assertEqual(lead["owner_user_id"], 1)
        self.assertEqual(self.db.query_leads(statuses=["unverified"])["total"], 1)
        self.assertEqual(self.db.lead_metrics(self.round_id)["unverified"], 1)

        result = convert_lead(self.db, self.settings, lead_id, fetch=False)
        self.assertEqual(result["lead"]["status"], "converted")
        self.assertEqual(result["rounds"], [self.round_id])
        self.assertEqual(self.db.query_leads(statuses=["unverified"])["total"], 0)
        rows = self.db.creator_table(self.round_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "u1")

    def test_lead_update_and_facets(self) -> None:
        lead_id = self.db.create_lead(platform="weibo", nickname="微博线索")
        self.db.update_lead(lead_id, status="discarded", owner_user_id=None)
        self.assertEqual(self.db.get_lead(lead_id)["status"], "discarded")
        facets = self.db.lead_facets()
        self.assertTrue(any(item["value"] == "weibo" for item in facets["platforms"]))
        self.assertTrue(any(item["value"] == "discarded" for item in facets["statuses"]))


if __name__ == "__main__":
    unittest.main()
