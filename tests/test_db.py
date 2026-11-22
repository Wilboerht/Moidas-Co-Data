from __future__ import annotations

import json
import sys
import tempfile
import unittest
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
                        "executor": "执行者A",
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
        self.assertEqual(row["executor"], "执行者A")
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


if __name__ == "__main__":
    unittest.main()
