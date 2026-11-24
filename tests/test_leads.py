from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.config import Settings
from codata.db import Database
from codata.leads import convert_lead
from codata.normalize import normalize_user_info
from codata.platforms import Price
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
        self.assertEqual(rows[0]["owner_user_id"], 1)
        self.assertEqual(json.loads(rows[0]["source_keywords"]), [])
        self.assertEqual(json.loads(rows[0]["source_labels"]), ["线索转换"])

    def test_conversion_splits_source_keywords_and_labels(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="来源线索",
            account_id="u1",
            source_round_id=self.round_id,
        )
        self.db._conn.execute(
            "UPDATE lead_rounds SET source_keywords = ? WHERE lead_id = ? AND round_id = ?",
            (json.dumps(["测试", "小红书"], ensure_ascii=False), lead_id, self.round_id),
        )
        self.db._conn.commit()
        convert_lead(self.db, self.settings, lead_id, fetch=False)
        rows = self.db.creator_table(self.round_id)
        self.assertEqual(json.loads(rows[0]["source_keywords"]), ["测试"])
        self.assertEqual(json.loads(rows[0]["source_labels"]), ["小红书"])

    def test_conversion_rolls_back_all_writes_on_failure(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="回滚线索",
            account_id="u1",
            source_round_id=self.round_id,
        )
        with patch.object(
            self.db, "add_creator_round", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                convert_lead(self.db, self.settings, lead_id, fetch=False)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "unverified")
        self.assertIsNone(lead["conversion_lock"])
        self.assertEqual(self.db.creator_table(self.round_id), [])
        attempts = self.db.lead_attempts(lead_id)
        self.assertTrue(any(item["status"] == "error" for item in attempts))

    def test_conversion_clears_stale_match_fields(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="已匹配线索",
            account_id="u1",
            source_round_id=self.round_id,
        )
        self.db._conn.execute(
            """
            UPDATE leads SET matched_platform = 'xhs', matched_user_id = 'old',
                matched_round_id = ?, matched_at = '2024-01-01', match_confidence = 'legacy'
            WHERE id = ?
            """,
            (self.round_id, lead_id),
        )
        self.db._conn.commit()
        result = convert_lead(self.db, self.settings, lead_id, fetch=False)
        lead = result["lead"]
        self.assertEqual(lead["status"], "converted")
        self.assertIsNone(lead["matched_user_id"])
        self.assertIsNone(lead["matched_round_id"])
        self.assertEqual(lead["account_id"], "u1")

    def test_lead_update_and_facets(self) -> None:
        lead_id = self.db.create_lead(platform="weibo", nickname="微博线索")
        self.db.update_lead(lead_id, status="discarded", owner_user_id=None)
        self.assertEqual(self.db.get_lead(lead_id)["status"], "discarded")
        facets = self.db.lead_facets()
        self.assertTrue(any(item["value"] == "weibo" for item in facets["platforms"]))
        self.assertTrue(any(item["value"] == "discarded" for item in facets["statuses"]))

    def test_conversion_reuses_matched_internal_id(self) -> None:
        self.db.add_creator_round(self.round_id, "xhs", "u1", account_id="alias")
        lead_id = self.db.create_lead(
            platform="xhs", account_id="alias", source_round_id=self.round_id
        )
        self.db._conn.execute(
            "UPDATE leads SET matched_platform = 'xhs', matched_user_id = 'u1', "
            "matched_round_id = ? WHERE id = ?",
            (self.round_id, lead_id),
        )
        self.db._conn.commit()
        result = convert_lead(self.db, self.settings, lead_id, fetch=False)
        self.assertEqual(result["lead"]["status"], "converted")
        self.assertEqual(result["lead"]["converted_user_id"], "u1")

    def test_conversion_normalization_failure_records_cost_and_attempt(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            account_id="5f1e5057000000000100429d",
            source_round_id=self.round_id,
        )
        provider = SimpleNamespace(
            provider_id="fake",
            platform="xhs",
            user_price=Price(1, "token"),
            user_path="/user",
            client=SimpleNamespace(last_attempts=1, last_cost=1),
            user_cost_tokens=1,
            search_price=Price(1, "token"),
            search_path="/search",
            search_cost_tokens=1,
        )
        provider.user = lambda *args: {"data": {}}
        provider.normalize_user = lambda *args: (_ for _ in ()).throw(
            ValueError("bad payload")
        )
        provider.close = lambda: None
        option = object()
        registry = SimpleNamespace(cheapest_user=lambda platform: option)
        with (
            patch("codata.lead_identity.load_registry", return_value=registry),
            patch("codata.lead_identity.build_provider", return_value=provider),
        ):
            with self.assertRaisesRegex(ValueError, "bad payload"):
                convert_lead(self.db, self.settings, lead_id)
        call = self.db._conn.execute("SELECT * FROM api_calls").fetchone()
        self.assertIsNotNone(call)
        self.assertEqual(call["cost"], 1)
        self.assertEqual(call["status"], "ok")
        attempt = self.db.lead_attempts(lead_id)[0]
        self.assertEqual(attempt["status"], "error")
        self.assertEqual(attempt["error"], "bad payload")
        self.assertEqual(attempt["cost"], 1)


if __name__ == "__main__":
    unittest.main()
