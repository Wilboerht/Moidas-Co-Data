from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codata.identity import normalize_identity, profile_id_from_url
from codata.normalize import (
    as_optional_int,
    derive_match_direction,
    derive_tags,
    fans_tier,
    first_str,
    format_fans_wan,
    normalize_search_item,
    normalize_tier_label,
    normalize_user_info,
)
from codata.normalize_platform import as_timestamp, normalize_user_platform
from helpers import search_item, user_payload


class NormalizeTests(unittest.TestCase):
    def test_normalize_search_item(self) -> None:
        item = search_item("n1", "u1", "咖啡师", title="手冲入门 #咖啡 #手冲")
        note, author = normalize_search_item(item)
        self.assertEqual(note["note_id"], "n1")
        self.assertEqual(note["author_id"], "u1")
        self.assertEqual(note["title"], "手冲入门 #咖啡 #手冲")
        self.assertEqual(note["liked"], 100)
        self.assertEqual(note["collected"], 10)
        self.assertEqual(note["comments"], 5)
        self.assertEqual(note["shares"], 1)
        self.assertEqual(note["url"], "https://www.xiaohongshu.com/explore/n1")
        self.assertEqual(json.loads(note["hashtags"]), ["咖啡", "手冲"])
        self.assertIsNotNone(author)
        self.assertEqual(author["user_id"], "u1")
        self.assertEqual(author["nickname"], "咖啡师")
        self.assertEqual(author["profile_url"], "https://www.xiaohongshu.com/user/profile/u1")

    def test_normalize_user_info(self) -> None:
        creator = normalize_user_info(user_payload())
        self.assertEqual(creator["user_id"], "u1")
        self.assertEqual(creator["fans"], 19360)
        self.assertEqual(creator["posted"], 399)
        self.assertEqual(creator["liked"], 79178)
        self.assertEqual(creator["collected"], 41243)
        self.assertEqual(creator["interaction"], 120421)
        self.assertEqual(creator["verify_info"], "咖啡师")
        self.assertEqual(creator["ip_location"], "上海")
        self.assertEqual(json.loads(creator["tags_json"]), ["女", "上海"])
        self.assertIsNotNone(creator["fetched_at"])

    def test_tiers_and_format(self) -> None:
        self.assertEqual(fans_tier(0), "素人")
        self.assertEqual(fans_tier(9_999), "素人")
        self.assertEqual(fans_tier(10_000), "尾部")
        self.assertEqual(fans_tier(100_000), "腰部")
        self.assertEqual(fans_tier(500_000), "头部")
        self.assertEqual(fans_tier(1_000_000), "顶流")
        self.assertEqual(normalize_tier_label(19_360), "尾部")
        self.assertEqual(normalize_tier_label(None, "腰部"), "腰部")
        self.assertEqual(normalize_tier_label(None, "腰部（30-100w）"), "")
        self.assertEqual(normalize_tier_label(123_456, "头部（100w+）"), "腰部")
        self.assertEqual(format_fans_wan(19_360), "1.9万")
        self.assertEqual(format_fans_wan(500), "500")
        self.assertEqual(format_fans_wan(100_000_000), "1.00亿")

    def test_tags_and_direction(self) -> None:
        tags = derive_tags(["咖啡"], ["手冲", "咖啡"], "咖啡师")
        self.assertEqual(tags, ["咖啡", "咖啡师", "手冲"])
        self.assertEqual(derive_match_direction([]), "未分类")
        self.assertEqual(derive_match_direction(["咖啡"]), "咖啡")
        self.assertEqual(derive_match_direction(["咖啡", "母婴"]), "多垂类")

    def test_metric_parsing_handles_units_and_invalid_values(self) -> None:
        self.assertEqual(as_optional_int("1.2万+"), 12_000)
        self.assertEqual(as_optional_int("10K"), 10_000)
        self.assertEqual(as_optional_int("2亿+"), 200_000_000)
        self.assertEqual(as_optional_int("1.5千"), 1_500)
        self.assertIsNone(as_optional_int("NaN"))
        self.assertIsNone(as_optional_int(float("inf")))
        self.assertIsNone(as_optional_int("-1万"))
        self.assertEqual(as_optional_int(0), 0)
        self.assertEqual(first_str(12345), "12345")
        self.assertEqual(first_str("", None, 42), "42")

    def test_zero_fans_are_not_treated_as_missing(self) -> None:
        creator = normalize_user_platform(
            "xhs",
            {"user_id": "u0", "nickname": "零粉", "fans": 0, "note_count": 0},
        )
        self.assertEqual(creator["fans"], 0)
        self.assertEqual(creator["posted"], 0)

    def test_timestamp_formats(self) -> None:
        self.assertEqual(as_timestamp(1_700_000_000), 1_700_000_000)
        self.assertEqual(as_timestamp(1_700_000_000_000), 1_700_000_000)
        self.assertEqual(as_timestamp(1_700_000_000_000_000), 1_700_000_000)
        self.assertEqual(as_timestamp("20240101"), 1_704_067_200)

    def test_identity_rejects_spoofed_hosts_and_conflicts(self) -> None:
        self.assertEqual(
            profile_id_from_url("xhs", "https://evil.example/user/profile/u1"), ""
        )
        self.assertEqual(
            profile_id_from_url("xhs", "https://www.xiaohongshu.com/user/profile/u1?x=1"),
            "u1",
        )
        self.assertEqual(profile_id_from_url("instagram", "https://www.instagram.com/stories/a/1"), "")
        account, profile = normalize_identity(
            "xhs",
            "u1",
            "https://www.xiaohongshu.com/user/profile/u2",
        )
        self.assertEqual(account, "u1")
        self.assertEqual(profile, "https://www.xiaohongshu.com/user/profile/u1")


if __name__ == "__main__":
    unittest.main()
