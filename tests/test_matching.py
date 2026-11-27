from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.config import Settings
from codata.db import Database
from codata.leads import convert_lead
from codata.matching import (
    backfill_matches,
    match_lead_against_creators,
    match_round_leads,
)
from codata.normalize import normalize_user_info, utc_now
from helpers import user_payload


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))
        self.settings = Settings(host="", token="", output_dir=Path(self.tmp.name))
        self.round_id = self.db.create_round(
            ["测试"],
            pages=1,
            max_creators=None,
            name="MATCH-TEST",
            category="测试",
            categories={"测试": "测试"},
        )

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _creator(self, user_id: str, nickname: str, fans: int = 1000) -> None:
        self.db.upsert_creator(
            normalize_user_info(user_payload(user_id, nickname, fans=fans))
        )
        self.db.add_creator_round(self.round_id, "xhs", user_id)

    def test_round_match_updates_lead_without_converting(self) -> None:
        self._creator("u1", "达人昵称", fans=8888)
        lead_id = self.db.create_lead(
            platform="xhs", nickname="线索昵称", account_id="u1"
        )
        stats = match_round_leads(self.db, self.round_id)
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["updated"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "unverified")
        self.assertEqual(lead["nickname"], "达人昵称")
        self.assertEqual(lead["account_id"], "u1")
        self.assertEqual(lead["matched_user_id"], "u1")
        self.assertEqual(lead["matched_round_id"], self.round_id)
        self.assertEqual(lead["match_confidence"], "account_id")
        self.assertEqual(lead["matched_round_name"], "MATCH-TEST")
        self.assertEqual(
            [item["round_id"] for item in self.db.lead_rounds(lead_id)],
            [self.round_id],
        )
        attempts = self.db.lead_attempts(lead_id)
        self.assertTrue(any(item["status"] == "matched" for item in attempts))
        self.assertEqual(attempts[0]["round_id"], self.round_id)
        self.assertEqual(len(self.db.creator_table(self.round_id)), 1)
        again = match_round_leads(self.db, self.round_id)
        self.assertEqual(again["matched"], 1)
        self.assertEqual(len(self.db.lead_attempts(lead_id)), 1)

    def test_profile_url_is_normalized_before_matching(self) -> None:
        self._creator("u2", "主页达人")
        lead_id, created, revived = self.db.create_lead_dedup(
            platform="xhs",
            nickname="客户线索",
            profile_url="https://www.xiaohongshu.com/user/profile/u2?xsec_token=abc",
        )
        self.assertTrue(created)
        self.assertFalse(revived)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], "u2")
        self.assertEqual(
            lead["profile_url"], "https://www.xiaohongshu.com/user/profile/u2"
        )
        match_round_leads(self.db, self.round_id)
        self.assertEqual(self.db.get_lead(lead_id)["matched_user_id"], "u2")

    def test_basic_creator_links_without_overwriting(self) -> None:
        self.db.upsert_creator_basic(
            {"platform": "xhs", "user_id": "u3", "nickname": "搜索昵称"}
        )
        self.db.add_creator_round(self.round_id, "xhs", "u3")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="客户昵称", account_id="u3"
        )
        match_round_leads(self.db, self.round_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["nickname"], "客户昵称")
        self.assertEqual(lead["matched_user_id"], "u3")

    def test_create_dedup_by_account_and_profile(self) -> None:
        first_id, first_created, first_revived = self.db.create_lead_dedup(
            platform="xhs", nickname="第一条", account_id="u9"
        )
        second_id, second_created, second_revived = self.db.create_lead_dedup(
            platform="xhs", nickname="第二条", account_id="u9"
        )
        third_id, third_created, third_revived = self.db.create_lead_dedup(
            platform="xhs",
            nickname="第三条",
            profile_url="https://www.xiaohongshu.com/user/profile/u9?xsec_token=1",
        )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertFalse(third_created)
        self.assertFalse(first_revived or second_revived or third_revived)
        self.assertEqual({first_id, second_id, third_id}, {first_id})
        self.assertEqual(
            self.db.get_lead(first_id)["profile_url"],
            "https://www.xiaohongshu.com/user/profile/u9",
        )
        indexes = self.db.try_create_lead_unique_indexes()
        self.assertTrue(indexes["account"])
        self.assertTrue(indexes["profile"])

    def test_existing_lead_adds_additional_source_round(self) -> None:
        second_round = self.db.create_round(["第二轮"], name="MATCH-TEST-2")
        lead_id, created, _ = self.db.create_lead_dedup(
            platform="xhs",
            nickname="多轮线索",
            account_id="multi-round",
            source_round_id=self.round_id,
            source_keywords='["测试"]',
        )
        self.assertTrue(created)
        same_id, created, revived = self.db.create_lead_dedup(
            platform="xhs",
            nickname="重复提交",
            account_id="multi-round",
            source_round_id=second_round,
            source_keywords='["第二轮"]',
        )
        self.assertEqual(same_id, lead_id)
        self.assertFalse(created)
        self.assertFalse(revived)
        self.assertEqual(
            {item["round_id"] for item in self.db.lead_rounds(lead_id)},
            {self.round_id, second_round},
        )
        self.assertEqual(self.db.query_leads(round_id=second_round)["total"], 1)
        self.assertEqual(self.db.get_lead(lead_id)["source_round_id"], self.round_id)

    def test_dedupe_leads_marks_duplicates_and_enables_index(self) -> None:
        self.db._conn.execute("DROP INDEX IF EXISTS uq_leads_identity_account")
        self.db._conn.execute("DROP INDEX IF EXISTS uq_leads_identity_profile")
        now = utc_now()
        for nickname in ("重复一", "重复二", "重复三"):
            self.db._conn.execute(
                """
                INSERT INTO leads (platform, nickname, account_id, profile_url,
                    raw_title, source, status, legacy_user_id, raw_json,
                    created_at, updated_at)
                VALUES ('xhs', ?, 'dup1', '', '', 'manual', 'unverified', '',
                        '{}', ?, ?)
                """,
                (nickname, now, now),
            )
            self.db._conn.execute(
                """
                INSERT INTO lead_rounds (lead_id, round_id, source_keywords, raw_title,
                    data_round, updated_at)
                VALUES (last_insert_rowid(), ?, '[]', '', '', ?)
                """,
                (self.round_id, now),
            )
        self.db._conn.commit()
        dry = self.db.dedupe_leads(apply=False)
        self.assertEqual(dry["groups"], 1)
        self.assertEqual(dry["duplicates"], 2)
        applied = self.db.dedupe_leads(apply=True)
        self.assertGreaterEqual(applied["duplicates"], 2)
        rows = self.db._conn.execute(
            "SELECT status, account_id FROM leads WHERE nickname LIKE '重复%' ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [row["status"] for row in rows],
            ["unverified", "duplicate", "duplicate"],
        )
        self.assertEqual(rows[0]["account_id"], "dup1")
        self.assertEqual(rows[1]["account_id"], "dup1")
        self.assertTrue((applied.get("indexes") or {}).get("account"))

    def test_dedupe_merges_handle_identity_and_events(self) -> None:
        for name in (
            "uq_leads_identity_account",
            "uq_leads_identity_handle",
            "uq_leads_identity_profile",
        ):
            self.db._conn.execute(f"DROP INDEX IF EXISTS {name}")
        primary_id = self.db.create_lead(
            platform="xhs", nickname="主线索", account_id="dedupe1"
        )
        cursor = self.db._conn.execute(
            """
            INSERT INTO leads (platform, nickname, account_id, platform_handle,
                profile_url, raw_title, source, status, legacy_user_id, raw_json,
                created_at, updated_at)
            VALUES ('xhs', '重复线索', 'dedupe1', 'red_handle', '', '',
                    'manual', 'unverified', '', '{}', ?, ?)
            """,
            (utc_now(), utc_now()),
        )
        duplicate_id = int(cursor.lastrowid)
        self.db._conn.commit()
        self.db.apply_lead_verification(
            duplicate_id,
            user_id="dedupe1",
            handle="red_handle",
            identity_status="verified",
            confidence="account_id",
            provider="dataflow",
        )
        self.db.add_lead_identity_event(
            duplicate_id,
            "verify_verified",
            "verified",
            new_user_id="dedupe1",
            new_handle="red_handle",
            provider="dataflow",
            confidence="account_id",
        )
        report = self.db.dedupe_leads(apply=True)
        self.assertGreaterEqual(report["groups"], 1)
        primary = self.db.get_lead(primary_id)
        self.assertEqual(primary["platform_handle"], "red_handle")
        self.assertEqual(primary["identity_status"], "verified")
        self.assertEqual(self.db.get_lead(duplicate_id)["status"], "duplicate")
        events = self.db.lead_identity_events(primary_id)
        self.assertTrue(any(item["event_type"] == "verify_verified" for item in events))

    def test_identity_health_reports_missing_indexes(self) -> None:
        health = self.db.lead_identity_health()
        self.assertTrue(health["account_index"])
        self.assertEqual(health["duplicate_account_groups"], 0)
        self.db._conn.execute("DROP INDEX IF EXISTS uq_leads_identity_account")
        now = utc_now()
        for nickname in ("重复甲", "重复乙"):
            self.db._conn.execute(
                """
                INSERT INTO leads (platform, nickname, account_id, profile_url,
                    raw_title, source, status, legacy_user_id, raw_json,
                    created_at, updated_at)
                VALUES ('xhs', ?, 'health1', '', '', 'manual', 'unverified', '',
                        '{}', ?, ?)
                """,
                (nickname, now, now),
            )
        self.db._conn.commit()
        health = self.db.lead_identity_health()
        self.assertFalse(health["account_index"])
        self.assertEqual(health["duplicate_account_groups"], 1)

    def test_create_revives_discarded_and_duplicate_leads(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs", nickname="旧线索", account_id="u14", owner_user_id=1
        )
        self.db.update_lead(lead_id, status="discarded")
        revived_id, created, revived = self.db.create_lead_dedup(
            platform="xhs",
            nickname="重新导入",
            account_id="u14",
            source="customer",
        )
        self.assertEqual(revived_id, lead_id)
        self.assertFalse(created)
        self.assertTrue(revived)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "unverified")
        self.assertEqual(lead["nickname"], "重新导入")
        self.assertEqual(lead["source"], "customer")
        self.assertFalse(lead["matched_user_id"])

        self.db.update_lead(lead_id, status="duplicate")
        revived_id, created, revived = self.db.create_lead_dedup(
            platform="xhs", nickname="再次导入", account_id="u14"
        )
        self.assertEqual(revived_id, lead_id)
        self.assertFalse(created)
        self.assertTrue(revived)
        self.assertEqual(self.db.get_lead(lead_id)["status"], "unverified")

    def test_restoring_duplicate_conflicting_with_active_lead_is_rejected(self) -> None:
        active_id = self.db.create_lead(
            platform="xhs", nickname="活跃线索", account_id="u16"
        )
        now = utc_now()
        self.db._conn.execute(
            """
            INSERT INTO leads (platform, nickname, account_id, profile_url,
                raw_title, source, status, legacy_user_id, raw_json,
                created_at, updated_at)
            VALUES ('xhs', '重复线索', 'u16', '', '', 'manual', 'duplicate', '',
                    '{}', ?, ?)
            """,
            (now, now),
        )
        duplicate_id = int(self.db._conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.db._conn.commit()
        with self.assertRaises(ValueError):
            self.db.update_lead(duplicate_id, status="unverified")
        self.assertEqual(self.db.get_lead(duplicate_id)["status"], "duplicate")
        self.assertEqual(self.db.get_lead(active_id)["status"], "unverified")

    def test_round_account_match_records_real_account(self) -> None:
        self.db.upsert_creator(
            normalize_user_info(user_payload("import:abc", "导入达人"))
        )
        self.db.add_creator_round(
            self.round_id, "xhs", "import:abc", account_id="real123"
        )
        lead_id = self.db.create_lead(
            platform="xhs", nickname="真实账号线索", account_id="real123"
        )
        match_round_leads(self.db, self.round_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "import:abc")
        self.assertEqual(lead["account_id"], "real123")
        self.assertEqual(lead["match_confidence"], "round_account_id")

    def test_matching_does_not_overwrite_reviewed_lead(self) -> None:
        self._creator("u30", "抓取昵称")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="人工昵称", account_id="u30"
        )
        self.db.update_lead(lead_id, status="verifying")
        match_round_leads(self.db, self.round_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["nickname"], "人工昵称")
        self.assertEqual(lead["matched_user_id"], "u30")

    def test_failed_rematch_clears_stale_match(self) -> None:
        self._creator("u31", "即将删除")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="线索", account_id="u31"
        )
        match_lead_against_creators(self.db, lead_id)
        self.assertEqual(self.db.get_lead(lead_id)["matched_user_id"], "u31")
        self.db._conn.execute("DELETE FROM creator_rounds WHERE user_id = 'u31'")
        self.db._conn.execute("DELETE FROM creators WHERE user_id = 'u31'")
        self.db._conn.commit()
        stats = match_lead_against_creators(self.db, lead_id)
        self.assertEqual(stats["skipped"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertFalse(lead["matched_user_id"])
        self.assertIsNone(lead["matched_round_id"])

    def test_archived_round_is_not_matched(self) -> None:
        self._creator("u32", "归档达人")
        self.db.create_lead(
            platform="xhs", nickname="归档线索", account_id="u32"
        )
        self.db.archive_round(self.round_id)
        stats = match_round_leads(self.db, self.round_id)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["matched"], 0)

    def test_reverse_match_and_conversion_without_source_round(self) -> None:
        self._creator("u4", "反向达人")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="反向线索", account_id="u4"
        )
        stats = match_lead_against_creators(self.db, lead_id)
        self.assertEqual(stats["matched"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "u4")
        self.assertEqual(lead["matched_round_id"], self.round_id)
        result = convert_lead(self.db, self.settings, lead_id, fetch=False)
        self.assertEqual(result["lead"]["status"], "converted")
        self.assertEqual(result["rounds"], [self.round_id])

    def test_delete_round_clears_match(self) -> None:
        self._creator("u5", "删除达人")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="删除线索", account_id="u5"
        )
        match_round_leads(self.db, self.round_id)
        self.assertEqual(self.db.get_lead(lead_id)["matched_user_id"], "u5")
        self.db.delete_round(self.round_id, force=True)
        self.assertFalse(self.db.get_lead(lead_id)["matched_user_id"])

    def test_backfill_dry_run_then_apply(self) -> None:
        self._creator("u6", "回填达人")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="回填线索", account_id="u6"
        )
        preview = backfill_matches(self.db, apply=False)
        self.assertGreaterEqual(preview["matched"], 1)
        self.assertFalse(self.db.get_lead(lead_id)["matched_user_id"])
        report = backfill_matches(self.db, apply=True)
        self.assertGreaterEqual(report["matched"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "u6")
        self.assertEqual(lead["status"], "unverified")

    def test_match_prefers_lead_with_matching_account(self) -> None:
        self._creator("u12", "账号达人")
        legacy_id = self.db.create_lead(
            platform="xhs", nickname="历史线索", legacy_user_id="u12"
        )
        account_id = self.db.create_lead(
            platform="xhs", nickname="账号线索", account_id="u12"
        )
        stats = match_round_leads(self.db, self.round_id)
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(self.db.get_lead(account_id)["matched_user_id"], "u12")
        self.assertFalse(self.db.get_lead(legacy_id)["matched_user_id"])
        self.assertEqual(self.db.get_lead(legacy_id)["status"], "unverified")

    def test_nickname_suggestion_does_not_change_lead(self) -> None:
        self._creator("u8", "同名达人")
        lead_id = self.db.create_lead(platform="xhs", nickname="同名达人")
        stats = match_round_leads(self.db, self.round_id)
        self.assertEqual(stats["suggested"], 1)
        self.assertEqual(stats["matched"], 0)
        lead = self.db.get_lead(lead_id)
        self.assertFalse(lead["account_id"])
        self.assertFalse(lead["matched_user_id"])
        attempts = self.db.lead_attempts(lead_id)
        self.assertEqual(attempts[0]["status"], "suggested")
        self.assertEqual(attempts[0]["candidate_user_id"], "u8")
        self.assertEqual(attempts[0]["confidence"], "nickname")

    def test_nickname_suggestion_skips_ambiguous_names(self) -> None:
        self._creator("u10", "重名达人")
        self._creator("u11", "重名达人")
        lead_id = self.db.create_lead(platform="xhs", nickname="重名达人")
        stats = match_round_leads(self.db, self.round_id)
        self.assertEqual(stats["suggested"], 0)
        self.assertEqual(self.db.lead_attempts(lead_id), [])

    def test_legacy_identity_match(self) -> None:
        self._creator("u7", "历史达人")
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="历史线索",
            legacy_user_id="u7",
            raw_json=json.dumps({"source": "legacy"}, ensure_ascii=False),
        )
        match_round_leads(self.db, self.round_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "u7")
        self.assertEqual(lead["match_confidence"], "legacy_user_id")
        self.assertEqual(lead["identity_status"], "unverified")

    def test_account_match_beats_newer_handle_match(self) -> None:
        self.db.upsert_creator(normalize_user_info(user_payload("acct1", "账号本人")))
        self.db.add_creator_round(self.round_id, "xhs", "acct1")
        newer_round = self.db.create_round(
            ["测试"],
            pages=1,
            max_creators=None,
            name="MATCH-TEST-2",
            category="测试",
            categories={"测试": "测试"},
        )
        self.db.upsert_creator_basic(
            {
                "platform": "xhs",
                "user_id": "uA",
                "nickname": "同号达人",
                "red_id": "red_acct1",
            }
        )
        self.db.add_creator_round(newer_round, "xhs", "uA")
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="冲突线索",
            account_id="acct1",
            platform_handle="red_acct1",
        )
        stats = match_lead_against_creators(self.db, lead_id)
        self.assertEqual(stats["matched"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "acct1")
        self.assertEqual(lead["account_id"], "acct1")
        self.assertEqual(lead["match_confidence"], "account_id")

    def test_verified_lead_is_not_relinked_to_conflicting_creator(self) -> None:
        self._creator("u1", "达人一号")
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="已校验线索",
            account_id="real9",
            platform_handle="red_u1",
        )
        self.db.apply_lead_verification(
            lead_id,
            user_id="real9",
            handle="red_u1",
            identity_status="verified",
            confidence="account_id",
            provider="dataflow",
        )
        stats = match_lead_against_creators(self.db, lead_id)
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(stats["skipped"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], "real9")
        self.assertFalse(lead["matched_user_id"])
        self.assertEqual(lead["identity_provider"], "dataflow")

    def test_verified_lead_links_creator_with_matching_account(self) -> None:
        self._creator("real9", "达人九号")
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="已校验线索",
            account_id="real9",
            platform_handle="red_real9",
        )
        self.db.apply_lead_verification(
            lead_id,
            user_id="real9",
            handle="red_real9",
            identity_status="verified",
            confidence="account_id",
            provider="dataflow",
        )
        stats = match_lead_against_creators(self.db, lead_id)
        self.assertEqual(stats["matched"], 1)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "real9")
        self.assertEqual(lead["identity_status"], "verified")
        self.assertEqual(lead["identity_provider"], "dataflow")

    def test_weak_local_match_keeps_identity_unverified(self) -> None:
        self._creator("u7", "历史达人")
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="历史线索",
            legacy_user_id="u7",
        )
        match_lead_against_creators(self.db, lead_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "u7")
        self.assertEqual(lead["identity_status"], "unverified")

    def test_editing_lead_identity_clears_stale_match(self) -> None:
        self._creator("u7", "历史达人")
        lead_id = self.db.create_lead(
            platform="xhs", nickname="线索", account_id="u7"
        )
        match_round_leads(self.db, self.round_id)
        self.assertEqual(self.db.get_lead(lead_id)["matched_user_id"], "u7")
        self.db.update_lead(lead_id, account_id="u8")
        lead = self.db.get_lead(lead_id)
        self.assertFalse(lead["matched_user_id"])
        self.assertIsNone(lead["matched_round_id"])
        self.assertEqual(lead["identity_status"], "unverified")


if __name__ == "__main__":
    unittest.main()
