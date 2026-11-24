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
from codata.identity import split_platform_identity
from codata.lead_identity import LeadIdentityMismatch, verify_lead_identity
from codata.leads import convert_lead
from codata.matching import match_round_leads
from codata.normalize import normalize_user_info
from codata.platforms import Price
from codata.providers import SearchResponse
from helpers import user_payload

USER_A = "5f1e5057000000000100429d"
USER_B = "61b2a3c4000000001f00abcd"


def profile_payload(user_id: str, nickname: str, red_id: str) -> dict:
    data = {
        "userid": user_id,
        "nickname": nickname,
        "red_id": red_id,
        "fans": 1000,
        "follows": 10,
        "liked": 100,
        "collected": 50,
        "note_num_stat": {"posted": 10, "liked": 100, "collected": 50},
        "images": f"https://example.com/{user_id}.jpg",
        "ip_location": "上海",
    }
    return {"code": 0, "msg": "ok", "data": json.dumps({"code": 0, "data": data}, ensure_ascii=False)}


class FakeProvider(SimpleNamespace):
    def __init__(
        self,
        payload: dict,
        *,
        search_items: list[dict] | None = None,
        user_error: Exception | None = None,
        search_error: Exception | None = None,
        user_cost_tokens: int = 1,
        search_cost_tokens: int = 1,
        last_attempts: int = 1,
        last_cost: int = 1,
    ) -> None:
        super().__init__(
            provider_id="fake",
            platform="xhs",
            user_price=Price(user_cost_tokens, "token"),
            user_path="/user",
            client=SimpleNamespace(
                last_attempts=last_attempts, last_cost=last_cost
            ),
            user_cost_tokens=user_cost_tokens,
            search_price=Price(search_cost_tokens, "token"),
            search_path="/search",
            search_cost_tokens=search_cost_tokens,
        )
        self._payload = payload
        self._search_items = search_items or []
        self._user_error = user_error
        self._search_error = search_error
        self.user_calls: list[str] = []
        self.search_calls: list[str] = []
        self.user = self._user
        self.search = self._search
        self.normalize_user = normalize_user_info
        self.normalize_search = lambda item: ({}, item)
        self.close = lambda: None

    def _user(self, user_id: str, profile_url: str = "") -> dict:
        self.user_calls.append(user_id)
        if self._user_error is not None:
            raise self._user_error
        self.client.last_attempts = 1
        self.client.last_cost = self.user_cost_tokens
        return self._payload

    def _search(self, keyword: str, page: int, state: dict) -> SearchResponse:
        self.search_calls.append(keyword)
        if self._search_error is not None:
            raise self._search_error
        self.client.last_attempts = 1
        self.client.last_cost = self.search_cost_tokens
        return SearchResponse(payload={}, items=self._search_items, state={})


class LeadIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))
        self.settings = Settings(host="", token="", output_dir=Path(self.tmp.name))
        self.round_id = self.db.create_round(
            ["测试"],
            pages=1,
            max_creators=None,
            name="IDENTITY-TEST",
            category="测试",
            categories={"测试": "测试"},
        )

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _patched(self, payload: dict | None = None, provider: FakeProvider | None = None):
        provider = provider or FakeProvider(payload or {})
        option = object()
        registry = SimpleNamespace(cheapest_user=lambda platform: option)
        return (
            patch("codata.lead_identity.load_registry", return_value=registry),
            patch("codata.lead_identity.build_provider", return_value=provider),
        )

    def test_split_platform_identity(self) -> None:
        self.assertEqual(
            split_platform_identity("xhs", "5f1e5057000000000100429d"),
            ("5f1e5057000000000100429d", ""),
        )
        self.assertEqual(
            split_platform_identity("xhs", "jcq1557457164"),
            ("", "jcq1557457164"),
        )
        self.assertEqual(
            split_platform_identity("weibo", "1871974895"),
            ("1871974895", ""),
        )
        self.assertEqual(
            split_platform_identity("instagram", "some_handle"),
            ("some_handle", "some_handle"),
        )

    def test_verify_preview_and_apply(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="张三",
            account_id=USER_A,
            platform_handle="handle1",
            source_round_id=self.round_id,
        )
        patches = self._patched(profile_payload(USER_A, "张三", "handle1"))
        with patches[0], patches[1]:
            preview = verify_lead_identity(self.db, self.settings, lead_id)
        self.assertEqual(preview["status"], "verified")
        self.assertEqual(preview["confidence"], "account_id")
        self.assertEqual(preview["resolved_user_id"], USER_A)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], USER_A)
        self.assertEqual(lead["identity_status"], "unverified")

        patches = self._patched(profile_payload(USER_A, "张三", "handle1"))
        with patches[0], patches[1]:
            applied = verify_lead_identity(
                self.db, self.settings, lead_id, apply=True
            )
        self.assertTrue(applied["applied"])
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], USER_A)
        self.assertEqual(lead["platform_handle"], "handle1")
        self.assertEqual(lead["identity_status"], "verified")
        self.assertTrue(
            any(
                event["event_type"] == "verify_verified"
                for event in self.db.lead_identity_events(lead_id)
            )
        )

    def test_conversion_blocks_and_confirms_mismatch(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="张三",
            account_id=USER_A,
            platform_handle="handle1",
            source_round_id=self.round_id,
        )
        patches = self._patched(profile_payload(USER_B, "李四", "other_handle"))
        with patches[0], patches[1]:
            with self.assertRaises(LeadIdentityMismatch):
                convert_lead(self.db, self.settings, lead_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["status"], "unverified")
        self.assertEqual(lead["account_id"], USER_A)
        self.assertEqual(lead["platform_handle"], "handle1")
        self.assertTrue(
            any(
                attempt["status"] == "suggested"
                for attempt in self.db.lead_attempts(lead_id)
            )
        )
        self.assertTrue(
            any(
                event["event_type"] == "mismatch_blocked"
                for event in self.db.lead_identity_events(lead_id)
            )
        )

        patches = self._patched(profile_payload(USER_B, "李四", "other_handle"))
        with patches[0], patches[1]:
            result = convert_lead(
                self.db, self.settings, lead_id, confirm_mismatch=True
            )
        lead = result["lead"]
        self.assertEqual(lead["status"], "converted")
        self.assertEqual(lead["account_id"], USER_B)
        self.assertEqual(lead["platform_handle"], "other_handle")
        self.assertEqual(lead["identity_status"], "verified")
        self.assertEqual(lead["identity_confidence"], "manual_mismatch")
        self.assertTrue(
            any(
                event["event_type"] == "mismatch_confirmed"
                for event in self.db.lead_identity_events(lead_id)
            )
        )

    def test_matching_uses_platform_handle(self) -> None:
        self.db.upsert_creator(normalize_user_info(user_payload("u1", "达人")))
        self.db.add_creator_round(self.round_id, "xhs", "u1", account_id="alias")
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="线索达人",
            platform_handle="red_u1",
        )
        match_round_leads(self.db, self.round_id)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["matched_user_id"], "u1")
        self.assertEqual(lead["account_id"], "u1")
        self.assertEqual(lead["match_confidence"], "platform_handle")
        self.assertEqual(lead["identity_status"], "verified")


    def test_handle_resolves_via_search(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="兔兔的日记",
            platform_handle="jcq1557457164",
            source_round_id=self.round_id,
        )
        provider = FakeProvider(
            profile_payload("real1", "兔兔的日记", "jcq1557457164"),
            search_items=[
                {
                    "user_id": "real1",
                    "red_id": "jcq1557457164",
                    "nickname": "兔兔的日记",
                    "profile_url": "https://www.xiaohongshu.com/user/profile/real1",
                }
            ],
            user_cost_tokens=45,
            search_cost_tokens=45,
        )
        patches = self._patched(provider=provider)
        with patches[0], patches[1]:
            result = verify_lead_identity(
                self.db, self.settings, lead_id, apply=True
            )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["resolution"], "search")
        self.assertEqual(result["search_cost"], 45)
        self.assertEqual(result["cost"], 90)
        self.assertEqual(provider.search_calls, ["兔兔的日记"])
        self.assertEqual(provider.user_calls, ["real1"])
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], "real1")
        self.assertEqual(lead["platform_handle"], "jcq1557457164")
        self.assertEqual(lead["identity_status"], "verified")

    def test_handle_search_without_match_skips_user_call(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="兔兔的日记",
            platform_handle="jcq1557457164",
            source_round_id=self.round_id,
        )
        provider = FakeProvider(
            profile_payload("real1", "兔兔的日记", "jcq1557457164"),
            search_items=[
                {
                    "user_id": "other",
                    "red_id": "other_handle",
                    "nickname": "其他人",
                }
            ],
            user_cost_tokens=45,
            search_cost_tokens=45,
        )
        patches = self._patched(provider=provider)
        with patches[0], patches[1]:
            result = verify_lead_identity(
                self.db, self.settings, lead_id, apply=True
            )
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["cost"], 45)
        self.assertEqual(provider.search_calls, ["兔兔的日记"])
        self.assertEqual(provider.user_calls, [])
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], "")
        self.assertEqual(lead["identity_status"], "unverified")

    def test_provider_error_is_not_billed(self) -> None:
        lead_id = self.db.create_lead(
            platform="xhs",
            nickname="张三",
            account_id=USER_A,
            source_round_id=self.round_id,
        )
        provider = FakeProvider(
            profile_payload(USER_A, "张三", "handle1"),
            user_error=RuntimeError(
                "[dataflow/100500] 服务器内部错误，本次请求不计费"
            ),
            user_cost_tokens=45,
            last_attempts=3,
            last_cost=0,
        )
        patches = self._patched(provider=provider)
        with patches[0], patches[1]:
            result = verify_lead_identity(
                self.db, self.settings, lead_id, apply=True
            )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["cost"], 0)
        self.assertEqual(result["estimated_cost"], 135)
        self.assertEqual(result["attempts"], 3)
        lead = self.db.get_lead(lead_id)
        self.assertEqual(lead["account_id"], USER_A)
        self.assertEqual(lead["identity_status"], "unverified")
        attempt = self.db.lead_attempts(lead_id)[0]
        self.assertEqual(attempt["status"], "error")
        self.assertEqual(attempt["cost"], 0)


if __name__ == "__main__":
    unittest.main()
