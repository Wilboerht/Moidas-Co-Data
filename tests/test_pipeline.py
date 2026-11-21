from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.config import Settings
from codata.db import Database
from codata.pipeline import Pipeline
from helpers import search_item, search_payload, user_payload


class FakeAPI:
    def __init__(self) -> None:
        self.search_calls = 0
        self.user_calls: list[str] = []
        self.client = SimpleNamespace(cost=0)
        self.search_result = search_payload(
            [
                search_item("n1", "u1", "咖啡师", liked=100, collected=10, comments=5, shares=1),
                search_item("n2", "u1", "咖啡师", liked=50, collected=2),
                search_item("n3", "u2", "新人", liked=10),
            ]
        )
        self.users = {
            "u1": user_payload("u1", "咖啡师", fans=19360),
            "u2": user_payload("u2", "新人", fans=5000, posted=20),
        }

    def balance(self):
        return {"code": 0, "data": {"remaining_calls": 100000}}

    def xhs_search(self, keyword: str, page: int = 1):
        self.search_calls += 1
        return self.search_result

    def xhs_user(self, user_id: str):
        self.user_calls.append(user_id)
        return self.users[user_id]


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))
        self.settings = Settings(host="", token="", output_dir=Path(self.tmp.name))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _round(self, max_creators: int | None) -> int:
        return self.db.create_round(
            ["咖啡"],
            pages=1,
            max_creators=max_creators,
            category="咖啡",
            categories={"咖啡": "咖啡"},
        )

    def test_run_round_limits_enrichment(self) -> None:
        api = FakeAPI()
        round_id = self._round(1)
        summary = Pipeline(self.settings, self.db, api=api).run_round(round_id)

        self.assertEqual(api.search_calls, 1)
        self.assertEqual(api.user_calls, ["u1"])
        self.assertEqual(summary["cost_tokens"], 90)
        self.assertEqual(summary["authors"], 2)
        self.assertEqual(summary["enriched"], 1)

        rows = self.db.creator_table(round_id)
        u1 = next(row for row in rows if row["user_id"] == "u1")
        u2 = next(row for row in rows if row["user_id"] == "u2")
        self.assertEqual(u1["fans"], 19360)
        self.assertIsNone(u2["fans"])
        self.assertEqual(u1["sample_notes"], 2)
        self.assertEqual(u1["sample_likes"], 150)

        round_row = self.db.get_round(round_id)
        self.assertEqual(round_row["status"], "done")
        job = self.db.latest_job(round_id)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["cost"], 90)

    def test_run_round_uses_cache(self) -> None:
        first = FakeAPI()
        Pipeline(self.settings, self.db, api=first).run_round(self._round(1))
        self.assertEqual(first.user_calls, ["u1"])

        second = FakeAPI()
        round_id = self._round(None)
        Pipeline(self.settings, self.db, api=second).run_round(round_id)
        self.assertEqual(second.user_calls, ["u2"])
        self.assertEqual(self.db.get_round(round_id)["cost_tokens"], 90)

    def test_run_round_refresh_users(self) -> None:
        Pipeline(self.settings, self.db, api=FakeAPI()).run_round(self._round(None))

        api = FakeAPI()
        round_id = self._round(None)
        Pipeline(self.settings, self.db, api=api).run_round(round_id, refresh_users=True)
        self.assertEqual(sorted(api.user_calls), ["u1", "u2"])
        self.assertEqual(self.db.get_round(round_id)["cost_tokens"], 135)


if __name__ == "__main__":
    unittest.main()
