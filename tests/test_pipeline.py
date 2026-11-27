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
from codata.pipeline import JobCancelled, Pipeline
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
        self.assertEqual(len(rows), 1)
        u1 = next(row for row in rows if row["user_id"] == "u1")
        self.assertEqual(u1["fans"], 19360)
        self.assertFalse(any(row["user_id"] == "u2" for row in rows))
        self.assertEqual(u1["sample_notes"], 2)
        self.assertEqual(u1["sample_likes"], 150)

        round_row = self.db.get_round(round_id)
        self.assertEqual(round_row["status"], "done")
        job = self.db.latest_job(round_id)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["cost"], 90)

    def test_run_round_matches_existing_leads(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs", nickname="线索昵称", account_id="u1"
        )
        round_id = self._round(1)
        summary = Pipeline(self.settings, self.db, api=FakeAPI()).run_round(round_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "unverified")
        self.assertEqual(lead["matched_user_id"], "u1")
        self.assertEqual(lead["matched_round_id"], round_id)
        self.assertEqual(lead["nickname"], "咖啡师")
        self.assertEqual(summary["leads_matched"], 1)

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

    def test_run_round_rejects_job_from_another_round(self) -> None:
        first_round = self._round(None)
        second_round = self._round(None)
        job_id = self.db.claim_job(first_round)
        with self.assertRaises(ValueError):
            Pipeline(self.settings, self.db, api=FakeAPI()).run_round(
                second_round, job_id=job_id
            )

    def test_run_round_rejects_finished_job(self) -> None:
        round_id = self._round(None)
        job_id = self.db.claim_job(round_id)
        self.db.finish_job(
            job_id, round_id, status="done", stage="完成", progress_done=1
        )
        with self.assertRaises(JobCancelled):
            Pipeline(self.settings, self.db, api=FakeAPI()).run_round(
                round_id, job_id=job_id
            )

    def test_partial_search_failure_preserves_previous_snapshot(self) -> None:
        from codata.client import ProviderError

        class PartialAPI(FakeAPI):
            def __init__(self, fail_keyword: str | None = None) -> None:
                super().__init__()
                self.fail_keyword = fail_keyword

            def xhs_search(self, keyword: str, page: int = 1):
                self.search_calls += 1
                if keyword == self.fail_keyword:
                    raise ProviderError(None, "temporary", retryable=True)
                user_id = "u1" if keyword == "咖啡" else "u2"
                note_id = "n1" if user_id == "u1" else "n3"
                return search_payload(
                    [search_item(note_id, user_id, user_id)]
                )

        round_id = self.db.create_round(
            ["咖啡", "护肤"], max_creators=None, category="测试"
        )
        first = PartialAPI()
        first_summary = Pipeline(self.settings, self.db, api=first).run_round(round_id)
        first_run_id = self.db.get_round(round_id)["latest_run_id"]
        before = {row["user_id"] for row in self.db.creator_table(round_id)}

        second = PartialAPI("护肤")
        summary = Pipeline(self.settings, self.db, api=second).run_round(round_id)

        self.assertEqual(before, {"u1", "u2"})
        self.assertEqual(
            {row["user_id"] for row in self.db.creator_table(round_id)}, before
        )
        self.assertEqual(self.db.get_round(round_id)["latest_run_id"], first_run_id)
        self.assertFalse(summary["snapshot_updated"])
        self.assertEqual(summary["search_failed_calls"], 1)
        self.assertEqual(second.user_calls, [])
        self.assertIn("保留上一快照", self.db.latest_job(round_id)["stage"])
        self.assertEqual(
            self.db.round_summary(round_id)["notes"], first_summary["notes"]
        )

    def test_pruned_enriched_creator_is_not_fetched_again(self) -> None:
        first = FakeAPI()
        round_id = self._round(2)
        Pipeline(self.settings, self.db, api=first).run_round(round_id)
        self.assertEqual(sorted(first.user_calls), ["u1", "u2"])
        self.db.update_round(round_id, max_creators=1)

        second = FakeAPI()
        Pipeline(self.settings, self.db, api=second).run_round(round_id)
        self.assertEqual(second.user_calls, [])
        rows = self.db.creator_table(round_id)
        self.assertEqual([row["user_id"] for row in rows], ["u1"])
        self.assertIsNotNone(self.db.get_creator("xhs", "u2"))

    def test_call_recomputes_costs_after_concurrent_writes(self) -> None:
        round_id = self._round(1)
        job_id = self.db.claim_job(round_id)
        pipeline = Pipeline(self.settings, self.db)
        pipeline._active_job_id = job_id
        provider = SimpleNamespace(
            client=SimpleNamespace(last_attempts=1, last_cost=45)
        )
        for index in range(2):
            pipeline._call(
                round_id,
                "xhs",
                "search",
                lambda: {"code": 0, "data": []},
                {},
                45,
                f"cost-{index}",
                "/search",
                provider_obj=provider,
            )
            if index == 0:
                self.db.log_api_call(
                    round_id, "xhs", "search", {}, None, 200, 100, 1, job_id=job_id
                )
        self.assertEqual(self.db.get_job(job_id)["cost"], 190)
        self.assertEqual(self.db.get_round(round_id)["cost_tokens"], 190)


if __name__ == "__main__":
    unittest.main()
