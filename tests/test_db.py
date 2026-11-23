from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.db import Database
from codata.normalize import normalize_user_info
from helpers import user_payload


def make_note(
    note_id: str,
    author_id: str,
    liked: int,
    collected: int = 0,
    comments: int = 0,
    shares: int = 0,
    hashtags: list[str] | None = None,
) -> dict:
    return {
        "platform": "xhs",
        "note_id": note_id,
        "author_id": author_id,
        "title": f"笔记 {note_id}",
        "desc": "正文",
        "note_type": "normal",
        "published_at": 1_730_000_000,
        "liked": liked,
        "collected": collected,
        "comments": comments,
        "shares": shares,
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "hashtags": json.dumps(hashtags or ["咖啡"], ensure_ascii=False),
        "raw_json": "{}",
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _seed_round(self) -> int:
        round_id = self.db.create_round(
            ["咖啡"],
            pages=1,
            max_creators=None,
            name="R-TEST-01",
            category="咖啡",
            categories={"咖啡": "咖啡"},
        )
        self.db.upsert_note(make_note("n1", "u1", 100, 10, 5, 1, ["咖啡", "手冲"]))
        self.db.upsert_note(make_note("n2", "u1", 50, 2, 0, 0, ["咖啡"]))
        self.db.upsert_note(make_note("n3", "u2", 10, 0, 0, 0, ["咖啡"]))
        self.db.add_note_hit(round_id, "xhs", "n1", "咖啡", 1, 1)
        self.db.add_note_hit(round_id, "xhs", "n1", "手冲", 1, 2)
        self.db.add_note_hit(round_id, "xhs", "n2", "咖啡", 1, 3)
        self.db.add_note_hit(round_id, "xhs", "n3", "咖啡", 1, 4)
        creator = normalize_user_info(user_payload("u1", "咖啡师", 19360))
        self.db.upsert_creator(creator)
        self.db.upsert_creator_basic(
            {"platform": "xhs", "user_id": "u2", "nickname": "新人", "red_id": "r2"}
        )
        self.db.refresh_creator_rounds(round_id)
        return round_id

    def test_aggregation(self) -> None:
        round_id = self._seed_round()
        rows = self.db.creator_table(round_id)
        self.assertEqual(len(rows), 2)
        u1 = next(row for row in rows if row["user_id"] == "u1")
        u2 = next(row for row in rows if row["user_id"] == "u2")

        self.assertEqual(u1["fans"], 19360)
        self.assertEqual(u1["posted"], 399)
        self.assertEqual(u1["sample_notes"], 2)
        self.assertEqual(u1["sample_likes"], 150)
        self.assertEqual(u1["sample_interactions"], 168)
        self.assertEqual(u1["top_note_id"], "n1")
        self.assertIsNone(u1["match_direction"])
        self.assertEqual(u1["status"], "pending")
        tags = json.loads(u1["auto_tags"])
        self.assertIn("咖啡", tags)
        self.assertIn("咖啡师", tags)
        self.assertIn("手冲", tags)

        self.assertIsNone(u2["fans"])
        self.assertEqual(u2["sample_notes"], 1)

        summary = self.db.round_summary(round_id)
        self.assertEqual(summary["creators"], 2)
        self.assertEqual(summary["notes"], 3)
        self.assertEqual(summary["note_hits"], 4)
        tiers = self.db.tier_counts(round_id)
        self.assertEqual(tiers["尾部"], 1)
        self.assertEqual(tiers["未补全"], 1)

    def test_review_preserved_after_refresh(self) -> None:
        round_id = self._seed_round()
        self.db.update_review(
            round_id,
            "xhs",
            "u1",
            status="adopted",
            match_direction="母婴",
            tags_manual="重点,复投",
            remark="联系过了",
        )
        self.db.refresh_creator_rounds(round_id)
        u1 = next(row for row in self.db.creator_table(round_id) if row["user_id"] == "u1")
        self.assertEqual(u1["status"], "adopted")
        self.assertEqual(u1["match_direction"], "母婴")
        self.assertEqual(u1["tags_manual"], "重点,复投")
        self.assertEqual(u1["remark"], "联系过了")

    def test_delete_round(self) -> None:
        round_id = self._seed_round()
        round2 = self.db.create_round(
            ["护肤"],
            pages=1,
            max_creators=None,
            name="KEEP-TEST",
            category="护肤",
            categories={"护肤": "护肤"},
        )
        for note_id in ("n1", "n2", "n3"):
            self.db.add_note_hit(round2, "xhs", note_id, "护肤", 1, 1)
        self.db.refresh_creator_rounds(round2)

        result = self.db.delete_round(round_id)
        self.assertEqual(result["creator_rounds"], 2)
        self.assertIsNone(self.db.get_round(round_id))
        self.assertIsNotNone(self.db.get_round(round2))
        self.assertEqual(len(self.db.creator_table(round2)), 2)
        self.assertIsNotNone(self.db.get_creator("xhs", "u1"))
        self.assertIsNotNone(self.db.get_creator("xhs", "u2"))

    def test_import_creator_rows(self) -> None:
        round_id = self.db.create_round(
            ["护肤"],
            pages=1,
            max_creators=None,
            name="IMPORT-TEST",
            category="护肤",
            categories={"护肤": "护肤"},
        )
        self.db.import_creator_rows(
            round_id,
            [
                {
                    "creator": {
                        "platform": "xhs",
                        "user_id": "import:abc",
                        "nickname": "导入达人",
                        "fans": 123456,
                        "posted": 10,
                        "liked": 1000,
                        "interaction": 2000,
                        "profile_url": "https://example.com",
                        "raw_json": "{}",
                    },
                    "note": {
                        "platform": "xhs",
                        "note_id": "import:note1",
                        "author_id": "import:abc",
                        "title": "代表作标题",
                        "note_type": "import",
                    },
                    "round": {
                        "platform": "xhs",
                        "user_id": "import:abc",
                        "source_keywords": json.dumps(["护肤达人池"], ensure_ascii=False),
                        "top_note_id": "import:note1",
                        "status": "已推荐",
                        "match_direction": "高端护肤",
                        "tags_manual": "上海",
                        "data_round": "第1轮",
                        "tier_label": "腰部（30-100w）",
                        "account_id": "",
                        "cooperation_platform": "小红书",
                        "raw_title": "原始标题",
                    },
                },
                {
                    "creator": {
                        "platform": "xhs",
                        "user_id": "import:nofans",
                        "nickname": "缺粉丝数",
                        "raw_json": "{}",
                    },
                    "round": {
                        "platform": "xhs",
                        "user_id": "import:nofans",
                        "status": "待审核",
                        "tier_label": "中小（1-30w）",
                    },
                },
            ],
        )
        rows = {
            row["user_id"]: row for row in self.db.creator_table(round_id)
        }
        row = rows["import:abc"]
        self.assertEqual(row["nickname"], "导入达人")
        self.assertEqual(row["account_id"], "")
        self.assertEqual(row["data_round"], "第1轮")
        self.assertEqual(row["tier_label"], "腰部")
        self.assertEqual(row["owner_user_id"], None)
        self.assertEqual(row["owner_username"], "")
        self.assertEqual(row["cooperation_platform"], "小红书")
        self.assertEqual(row["raw_title"], "原始标题")
        self.assertEqual(row["status"], "adopted")
        self.assertEqual(row["top_title"], "代表作标题")
        self.assertIsNone(rows["import:nofans"]["tier_label"])
        tiers = self.db.tier_counts(round_id)
        self.assertEqual(
            set(tiers), {"素人", "尾部", "腰部", "头部", "顶流", "未补全"}
        )
        self.assertEqual(tiers["腰部"], 1)
        self.assertEqual(tiers["未补全"], 1)
        facet_values = {item["value"] for item in self.db.creator_facets(round_id)["tiers"]}
        self.assertTrue(facet_values <= set(tiers))
        self.assertEqual(self.db.round_summary(round_id)["notes"], 1)

    def test_multiple_source_keyword_filter_uses_or(self) -> None:
        round_id = self._seed_round()
        both = self.db.query_creators(
            round_id, sources=["咖啡", "手冲"]
        )["items"]
        self.assertEqual({item["user_id"] for item in both}, {"u1", "u2"})
        only_handbrew = self.db.query_creators(round_id, sources=["手冲"])["items"]
        self.assertEqual({item["user_id"] for item in only_handbrew}, {"u1"})
        all_mode = self.db.query_creators(
            round_id, sources=["咖啡", "手冲"], source_mode="all"
        )["items"]
        self.assertEqual({item["user_id"] for item in all_mode}, {"u1"})
        u1 = next(item for item in both if item["user_id"] == "u1")
        self.assertEqual(set(json.loads(u1["source_keywords"])), {"咖啡", "手冲"})

    def test_all_rounds_merges_keywords(self) -> None:
        first = self.db.create_round(["??"], pages=1, max_creators=None, name="ALL-1")
        second = self.db.create_round(["??"], pages=1, max_creators=None, name="ALL-2")
        self.db.upsert_creator(normalize_user_info(user_payload("u1", "多关键词达人", 1000)))
        self.db.upsert_note(make_note("n-all-1", "u1", 10))
        self.db.upsert_note(make_note("n-all-2", "u1", 20))
        self.db.add_note_hit(first, "xhs", "n-all-1", "??", 1, 1)
        self.db.add_note_hit(second, "xhs", "n-all-2", "??", 1, 1)
        self.db.refresh_creator_rounds(first)
        self.db.refresh_creator_rounds(second)

        found = self.db.query_creators(None, sources=["??"])["items"]
        self.assertEqual({item["user_id"] for item in found}, {"u1"})
        self.assertEqual(
            set(json.loads(found[0]["source_keywords"])), {"??", "??"}
        )
        both = self.db.query_creators(
            None, sources=["??", "??"], source_mode="all"
        )["items"]
        self.assertEqual({item["user_id"] for item in both}, {"u1"})

    def test_source_labels_migration(self) -> None:
        import_round = self.db.create_round(
            ["护肤"], pages=1, max_creators=None, name="IMPORT-SOURCE"
        )
        self.db.import_creator_rows(
            import_round,
            [
                {
                    "creator": {
                        "platform": "xhs",
                        "user_id": "import:legacy",
                        "nickname": "导入达人",
                        "raw_json": "{}",
                    },
                    "round": {
                        "platform": "xhs",
                        "user_id": "import:legacy",
                        "source_keywords": json.dumps(["护肤达人池"], ensure_ascii=False),
                    },
                }
            ],
        )
        self.db._migrate_source_labels()
        row = self.db.creator_table(import_round)[0]
        self.assertEqual(json.loads(row["source_keywords"]), [])
        self.assertEqual(json.loads(row["source_labels"]), ["护肤达人池"])
        by_label = self.db.query_creators(import_round, labels=["护肤达人池"])["items"]
        self.assertEqual({item["user_id"] for item in by_label}, {"import:legacy"})
        self.assertTrue(
            any(
                item["value"] == "护肤达人池"
                for item in self.db.creator_facets(import_round)["labels"]
            )
        )
        self.assertEqual(
            self.db.query_creators(import_round, search="护肤达人池")["total"], 1
        )

    def test_source_table_repairs_when_out_of_sync(self) -> None:
        round_id = self._seed_round()
        with self.db._conn:
            self.db._conn.execute("DELETE FROM creator_round_sources")
        self.db._ensure_creator_sources()
        rows = self.db._conn.execute(
            "SELECT kind, COUNT(*) AS count FROM creator_round_sources "
            "WHERE round_id = ? GROUP BY kind",
            (round_id,),
        ).fetchall()
        self.assertTrue(rows)
        self.assertGreater(
            sum(int(row["count"]) for row in rows),
            0,
        )

    def test_bulk_update_review(self) -> None:
        round_id = self._seed_round()
        changed = self.db.bulk_update_review(
            round_id,
            [("xhs", "u1"), ("xhs", "u2")],
            status="shortlisted",
            match_direction="咖啡",
            remark="批量",
        )
        self.assertEqual(changed, 2)
        rows = {row["user_id"]: row for row in self.db.creator_table(round_id)}
        self.assertEqual(rows["u1"]["status"], "shortlisted")
        self.assertEqual(rows["u2"]["match_direction"], "咖啡")
        self.assertEqual(rows["u2"]["remark"], "批量")

    def test_stale_job_recovery_and_cancel(self) -> None:
        round_id = self._seed_round()
        job_id = self.db.claim_job(round_id)
        self.db.update_job(
            job_id,
            status="running",
            started_at="2000-01-01T00:00:00+00:00",
            heartbeat_at="2000-01-01T00:00:00+00:00",
        )
        stale = self.db.reconcile_stale_jobs(1)
        self.assertIn(job_id, stale)
        self.assertEqual(self.db.get_job(job_id)["status"], "failed")
        job2 = self.db.claim_job(round_id)
        cancelled = self.db.cancel_job(job2)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["stage"], "已取消")
        self.assertEqual(self.db.get_round(round_id)["status"], "cancelled")

    def test_prune_preserves_claimed_creator(self) -> None:
        round_id = self._seed_round()
        self.db.claim_creator(round_id, "xhs", "u2", 1)
        deleted = self.db.prune_round_creators(round_id, 1)
        self.assertEqual(deleted, 1)
        rows = {row["user_id"]: row for row in self.db.creator_table(round_id)}
        self.assertEqual(set(rows), {"u2"})
        self.assertEqual(rows["u2"]["owner_user_id"], 1)

    def test_delete_round_cleans_lead_references(self) -> None:
        round_id = self._seed_round()
        lead_id = self.db.create_lead(
            platform="xhs", nickname="线索", source_round_id=round_id
        )
        self.assertTrue(self.db.lead_rounds(lead_id))
        with self.assertRaises(ValueError):
            self.db.delete_round(round_id)
        self.db.delete_round(round_id, force=True)
        self.assertIsNone(self.db.get_lead(lead_id)["source_round_id"])
        self.assertEqual(self.db.lead_rounds(lead_id), [])

    def test_delete_round_rejects_active_job(self) -> None:
        round_id = self._seed_round()
        self.db.claim_job(round_id)
        with self.assertRaises(ValueError):
            self.db.delete_round(round_id)

    def test_update_lead_source_round_sync(self) -> None:
        first = self._seed_round()
        second = self.db.create_round(
            ["护肤"], pages=1, max_creators=None, name="LEAD-ROUND-2"
        )
        lead_id = self.db.create_lead(
            platform="xhs", nickname="线索", source_round_id=first
        )
        self.db.update_lead(lead_id, source_round_id=second)
        self.assertEqual(
            [row["round_id"] for row in self.db.lead_rounds(lead_id)], [second]
        )

    def test_creator_claim(self) -> None:
        round_id = self._seed_round()
        first = self.db.claim_creator(round_id, "xhs", "u1", 1)
        self.assertTrue(first["ok"])
        self.assertFalse(first["already_mine"])
        owner = self.db.creator_owner(round_id, "xhs", "u1")
        self.assertEqual(owner["owner_user_id"], 1)
        self.assertIsNotNone(owner["claimed_at"])

        conflict = self.db.claim_creator(round_id, "xhs", "u1", 2)
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["owner_user_id"], 1)

        mine = self.db.claim_creators(round_id, [("xhs", "u1"), ("xhs", "u2")], 1)
        self.assertEqual(mine["already_mine"], 1)
        self.assertEqual(mine["claimed"], 1)
        owned = self.db.query_creators(round_id, owner="1")["items"]
        self.assertEqual({item["user_id"] for item in owned}, {"u1", "u2"})
        unassigned = self.db.query_creators(round_id, owner="unassigned")["items"]
        self.assertEqual(unassigned, [])

        conflict_release = self.db.release_creator(
            round_id, "xhs", "u1", expected_owner_user_id=2
        )
        self.assertFalse(conflict_release["ok"])
        released = self.db.release_creator(round_id, "xhs", "u1", expected_owner_user_id=1)
        self.assertTrue(released["released"])
        self.assertEqual(released["previous"]["owner_user_id"], 1)
        self.assertIsNone(self.db.creator_owner(round_id, "xhs", "u1")["owner_user_id"])

    def test_creator_cache_and_jobs(self) -> None:
        self.db.upsert_creator(normalize_user_info(user_payload("u1")))
        self.assertTrue(self.db.creator_is_fresh("xhs", "u1", 7))
        self.assertFalse(self.db.creator_is_fresh("xhs", "u1", 0))
        self.assertFalse(self.db.creator_is_fresh("xhs", "missing", 7))

        job_id = self.db.create_job(1)
        self.db.update_job(job_id, status="running", stage="搜索", progress_done=1, progress_total=3)
        job = self.db.latest_job(1)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["progress_done"], 1)

    def test_keywords_and_rounds(self) -> None:
        keyword_id = self.db.ensure_keyword("咖啡", "咖啡")
        self.db.update_keyword(keyword_id, category="饮品", enabled=False)
        keyword = next(row for row in self.db.list_keywords() if row["id"] == keyword_id)
        self.assertEqual(keyword["category"], "饮品")
        self.assertEqual(keyword["enabled"], 0)
        with self.assertRaises(ValueError):
            self.db.create_round([], pages=1)

    def test_disabled_keyword_blocks_round_creation(self) -> None:
        keyword_id = self.db.ensure_keyword("停用词")
        self.db.update_keyword(keyword_id, enabled=False)
        with self.assertRaises(ValueError):
            self.db.create_round(["停用词"])
        self.assertEqual(self.db.list_rounds(), [])

    def test_archive_hides_round_from_all_round_views(self) -> None:
        round_id = self._seed_round()
        self.db.archive_round(round_id)
        self.assertEqual(self.db.list_rounds(), [])
        self.assertEqual(self.db.list_rounds(include_archived=True)[0]["id"], round_id)
        self.assertEqual(self.db.query_creators(None)["total"], 0)
        self.db.restore_round(round_id)
        self.assertEqual(self.db.list_rounds()[0]["id"], round_id)
        self.assertEqual(self.db.query_creators(None)["total"], 2)

    def test_cancel_updates_costs(self) -> None:
        round_id = self._seed_round()
        job_id = self.db.claim_job(round_id)
        self.db.update_job(job_id, status="running")
        self.db.log_api_call(
            round_id, "xhs", "search", {"keyword": "咖啡"}, None, 200, 45, 10,
            job_id=job_id,
        )
        cancelled = self.db.cancel_job(job_id, cancelled_by=1)
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["cost"], 45)
        self.assertEqual(self.db.get_round(round_id)["cost_tokens"], 45)

    def test_missing_profile_metrics_stay_null(self) -> None:
        creator = normalize_user_info({"code": 0, "data": {"userid": "u-empty"}})
        self.db.upsert_creator(creator)
        row = self.db.get_creator("xhs", "u-empty")
        self.assertIsNone(row["fans"])
        self.assertIsNone(row["posted"])
        updated = normalize_user_info(
            {
                "code": 0,
                "data": {"userid": "u-empty", "fans": 123, "note_num_stat": {"posted": 4}},
            }
        )
        self.db.upsert_creator(updated)
        self.assertEqual(self.db.get_creator("xhs", "u-empty")["fans"], 123)
        missing_again = normalize_user_info({"code": 0, "data": {"userid": "u-empty"}})
        self.db.upsert_creator(missing_again)
        self.assertEqual(self.db.get_creator("xhs", "u-empty")["fans"], 123)

    def test_upsert_creator_does_not_clear_fields_with_empty_values(self) -> None:
        self.db.upsert_creator(normalize_user_info(user_payload()))
        self.db.upsert_creator(
            {
                "platform": "xhs",
                "user_id": "u1",
                "red_id": "",
                "nickname": "",
                "avatar": "",
                "desc": "",
                "tags_json": "[]",
                "ip_location": "",
                "verify_info": "",
                "profile_url": "",
                "raw_json": "",
            }
        )
        row = self.db.get_creator("xhs", "u1")
        self.assertEqual(row["nickname"], "咖啡师")
        self.assertEqual(row["fans"], 19360)
        self.assertEqual(json.loads(row["tags_json"]), ["女", "上海"])
        self.assertEqual(
            row["profile_url"], "https://www.xiaohongshu.com/user/profile/u1"
        )

    def test_dedupe_dry_run_does_not_modify_identities(self) -> None:
        now = self.db._conn.execute("SELECT datetime('now')").fetchone()[0]
        cur = self.db._conn.execute(
            """
            INSERT INTO leads (platform, nickname, account_id, profile_url, raw_title,
                source, status, legacy_user_id, raw_json, created_at, updated_at)
            VALUES ('xhs', '未规范化', '', ?, '', 'manual', 'unverified', '', '{}', ?, ?)
            """,
            (
                "https://www.xiaohongshu.com/user/profile/u20?xsec_token=abc",
                now,
                now,
            ),
        )
        lead_id = int(cur.lastrowid)
        self.db._conn.commit()
        before = self.db.get_lead(lead_id)["profile_url"]
        report = self.db.dedupe_leads(apply=False)
        self.assertGreaterEqual(report["identity_updates"], 1)
        self.assertEqual(self.db.get_lead(lead_id)["profile_url"], before)
        applied = self.db.dedupe_leads(apply=True)
        self.assertEqual(applied["identity_updates"], 0)
        self.assertEqual(
            self.db.get_lead(lead_id)["profile_url"],
            "https://www.xiaohongshu.com/user/profile/u20",
        )

    def test_finish_job_does_not_overwrite_cancelled_job(self) -> None:
        round_id = self._seed_round()
        job_id = self.db.claim_job(round_id)
        self.db.cancel_job(job_id)
        ok = self.db.finish_job(
            job_id,
            round_id,
            status="done",
            stage="完成",
            progress_done=1,
            cost=10,
            round_cost=10,
        )
        self.assertFalse(ok)
        self.assertEqual(self.db.get_job(job_id)["status"], "cancelled")
        self.assertEqual(self.db.get_round(round_id)["status"], "cancelled")

    def test_backup_retention_policy_supports_dry_run_and_apply(self) -> None:
        root = Path(self.tmp.name)
        for index in range(5):
            (root / f"codata.sqlite.bak-test-{index:02d}").write_bytes(b"x" * 100)
        report = self.db.prune_backups(retention_days=0, retention_count=2)
        self.assertEqual(report["backups"], 5)
        self.assertEqual(report["expired"], 3)
        self.assertEqual(report["bytes"], 300)
        self.assertEqual(len(list(root.glob("codata.sqlite.bak-*"))), 5)
        applied = self.db.prune_backups(
            retention_days=0,
            retention_count=2,
            apply=True,
        )
        self.assertEqual(applied["deleted"], 3)
        self.assertEqual(applied["errors"], [])
        self.assertEqual(
            {path.name for path in root.glob("codata.sqlite.bak-*")},
            {"codata.sqlite.bak-test-03", "codata.sqlite.bak-test-04"},
        )

    def test_failed_api_call_records_estimated_cost(self) -> None:
        round_id = self._seed_round()
        job_id = self.db.claim_job(round_id)
        self.db.log_api_call(
            round_id,
            "xhs",
            "search",
            {"keyword": "咖啡"},
            None,
            500,
            0,
            120,
            provider="dataflow",
            cost_native=45,
            cost_currency="token",
            estimated_cost=45,
            attempt=3,
            status="error",
            job_id=job_id,
        )
        self.assertEqual(self.db.sum_job_cost(job_id), 0)
        self.assertEqual(self.db.sum_failed_estimate(job_id), 45)
        row = self.db.recent_api_calls(round_id)[0]
        self.assertEqual(row["attempt"], 3)
        self.assertEqual(row["status"], "error")

    def test_creator_created_at_is_set_on_insert(self) -> None:
        self.db.upsert_creator_basic(
            {"platform": "xhs", "user_id": "created-basic", "nickname": "基础"}
        )
        basic = self.db.get_creator("xhs", "created-basic")
        self.assertTrue(basic["created_at"])
        self.db.upsert_creator(normalize_user_info(user_payload("created-full")))
        full = self.db.get_creator("xhs", "created-full")
        self.assertTrue(full["created_at"])
        self.db.upsert_creator(
            normalize_user_info(user_payload("created-full", "更新后"))
        )
        self.assertEqual(
            self.db.get_creator("xhs", "created-full")["created_at"],
            full["created_at"],
        )

    def test_activity_calendar_counts_creators_and_review_actions(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        yesterday = (now - timedelta(days=1)).date().isoformat()
        self.db._conn.execute(
            "INSERT INTO creators (platform, user_id, created_at) VALUES (?, ?, ?)",
            ("xhs", "cal-today", f"{today}T01:00:00.000+00:00"),
        )
        self.db._conn.execute(
            "INSERT INTO creators (platform, user_id, created_at) VALUES (?, ?, ?)",
            ("xhs", "cal-yesterday", f"{yesterday}T01:00:00.000+00:00"),
        )
        self.db._conn.execute(
            """
            INSERT INTO audit_log (user_id, action, target, details, ts)
            VALUES (NULL, 'creator.review_updated', 'cal-today', ?, ?)
            """,
            ('{"status": "adopted"}', f"{today}T02:00:00.000+00:00"),
        )
        self.db._conn.execute(
            """
            INSERT INTO audit_log (user_id, action, target, details, ts)
            VALUES (NULL, 'creator.review_bulk_updated', 'cal-bulk', ?, ?)
            """,
            ('{"changed": 3, "status": "adopted"}', f"{today}T03:00:00.000+00:00"),
        )
        self.db._conn.commit()

        result = self.db.activity_calendar(days=30, tz_offset=0)
        self.assertEqual(result["days"], 30)
        by_date = {item["date"]: item for item in result["items"]}
        self.assertEqual(by_date[today]["collected"], 1)
        self.assertEqual(by_date[today]["processed"], 4)
        self.assertEqual(by_date[yesterday]["collected"], 1)
        self.assertEqual(by_date[yesterday]["processed"], 0)
        self.assertEqual(result["totals"], {"collected": 2, "processed": 4})


if __name__ == "__main__":
    unittest.main()
