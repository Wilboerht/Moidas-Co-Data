from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from codata.client import DataFlowClient, ProviderError, RnoteClient
from codata.config import Settings
from codata.db import Database
from codata.normalize import normalize_rnote_user, normalize_search_item
from codata.normalize_platform import normalize_search_item_platform, normalize_user_platform
from codata.platforms import (
    PlatformConfigError,
    Price,
    _validate_path,
    load_registry,
    validate_provider_host,
)
from codata.pipeline import Pipeline
from codata.providers import BalanceResponse, RnoteProvider, SearchResponse, build_provider
from codata.providers_multi import EveryInfraProvider, _has_more
from helpers import search_item, user_payload


class ProviderTests(unittest.TestCase):
    def test_registry_prices_and_cheapest(self) -> None:
        settings = Settings(
            host="https://dataflowserver.org",
            token="dataflow-key",
            rnote_host="https://rnote.dev",
            rnote_api_key="rnote-key",
            dataflow_allowed_hosts=("dataflowserver.org",),
            rnote_allowed_hosts=("rnote.dev",),
        )
        with patch.dict(os.environ, {"EVERYINFRA_API_KEY": "", "TIKHUB_API_KEY": "", "JUSTONEAPI_TOKEN": "", "SOCIALDATAX_API_KEY": "", "REDFOX_API_KEY": ""}):
            registry = load_registry(settings)
        self.assertEqual(registry.cheapest("xhs").provider.id, "dataflow")
        prices = {item.provider.id: item.search.price for item in registry.options("xhs")}
        self.assertEqual(prices["dataflow"].tokens(registry.usd_cny, registry.token_cny), 45)
        self.assertEqual(prices["rnote"].tokens(registry.usd_cny, registry.token_cny), 72)
        self.assertEqual(str(prices["rnote"].cny(registry.usd_cny, registry.token_cny)), "0.072")

    def test_host_allowlist_rejects_untrusted_host(self) -> None:
        with self.assertRaises(PlatformConfigError):
            validate_provider_host("https://evil.example", ("rnote.dev",))
        with self.assertRaises(PlatformConfigError):
            validate_provider_host("http://rnote.dev", ("rnote.dev",))
        self.assertEqual(
            validate_provider_host("https://rnote.dev", ("rnote.dev",)),
            "rnote.dev",
        )

    def test_balance_path_must_be_relative(self) -> None:
        with self.assertRaises(PlatformConfigError):
            _validate_path("//evil.example/steal", "balance_path")
        with self.assertRaises(PlatformConfigError):
            _validate_path("https://evil.example/steal", "balance_path")
        self.assertEqual(_validate_path("/api/balance", "balance_path"), "/api/balance")

    def test_rnote_falsy_success_is_rejected(self) -> None:
        settings = Settings(
            host="",
            token="",
            rnote_host="https://rnote.dev",
            rnote_api_key="key",
            rnote_allowed_hosts=("rnote.dev",),
            retries=1,
            delay=0,
        )
        client = RnoteClient(settings, None)
        client._client.close()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": 0, "error": "nope"})

        client._client = httpx.Client(
            base_url="https://rnote.dev", transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ProviderError):
                client.get("/api/v2/crawler/search/notes")
        finally:
            client.close()

    def test_dataflow_accepts_string_success_code(self) -> None:
        settings = Settings(
            host="https://dataflowserver.org",
            token="key",
            dataflow_allowed_hosts=("dataflowserver.org",),
            retries=1,
            delay=0,
        )
        client = DataFlowClient(settings)
        client._client.close()
        responses = iter(
            [
                httpx.Response(200, json={"code": "0", "data": {"ok": True}}),
                httpx.Response(200, json={"code": 1, "msg": "bad"}),
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return next(responses)

        client._client = httpx.Client(
            base_url="https://dataflowserver.org", transport=httpx.MockTransport(handler)
        )
        try:
            self.assertTrue(client.get("/api/xhs/v3/appsearch")["data"]["ok"])
            with self.assertRaises(ProviderError):
                client.get("/api/xhs/v3/appsearch")
        finally:
            client.close()

    def test_retry_cost_is_counted_per_upstream_response(self) -> None:
        settings = Settings(
            host="https://dataflowserver.org",
            token="key",
            dataflow_allowed_hosts=("dataflowserver.org",),
            retries=2,
            delay=0,
        )
        client = DataFlowClient(settings)
        client._client.close()
        responses = iter(
            [
                httpx.Response(503, json={"code": 503, "msg": "busy"}),
                httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
            ]
        )
        client._client = httpx.Client(
            base_url="https://dataflowserver.org",
            transport=httpx.MockTransport(lambda request: next(responses)),
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with Database(Path(tmp)) as db:
                    pipeline = Pipeline(settings, db)
                    provider = SimpleNamespace(client=client)
                    result = pipeline._call(
                        1,
                        "xhs",
                        "search",
                        lambda: client.get("/api/xhs/v3/appsearch", cost=45),
                        {},
                        45,
                        "retry-test",
                        "/api/xhs/v3/appsearch",
                        cost_native=Price(45, "token"),
                        provider_obj=provider,
                    )
                    self.assertTrue(result["data"]["ok"])
                    call = db.recent_api_calls(1, limit=1)[0]
                    self.assertEqual(call["attempt"], 2)
                    self.assertEqual(call["cost"], 90)
                    self.assertEqual(call["estimated_cost"], 90)
                    self.assertEqual(client.cost, 90)
                    self.assertEqual(client.last_cost, 90)
        finally:
            client.close()

    def test_has_more_handles_string_false(self) -> None:
        self.assertFalse(_has_more({"hasMore": "false"}))
        self.assertTrue(_has_more({"hasMore": "true"}))
        self.assertIsNone(_has_more({}))

    def test_rnote_pagination_state(self) -> None:
        payload = {
            "success": True,
            "data": {
                "data": {"items": [{"id": "n1"}]},
                "search_id": "sid",
                "search_session_id": "session",
            },
        }
        self.assertEqual(
            RnoteProvider._pagination(payload),
            {"search_id": "sid", "search_session_id": "session"},
        )

    def test_normalize_rnote_user(self) -> None:
        payload = {
            "success": True,
            "data": {
                "result": {
                    "nickname": "Rnote 博主",
                    "image": "https://example.com/avatar.jpg",
                    "desc": "咖啡内容",
                    "fans": 12345,
                },
                "follows": 12,
                "collected": 345,
                "liked": 6789,
                "note_count": 88,
                "ip_location": "上海",
            },
        }
        creator = normalize_rnote_user(payload, "u-rnote")
        self.assertEqual(creator["platform"], "xhs")
        self.assertEqual(creator["user_id"], "u-rnote")
        self.assertEqual(creator["nickname"], "Rnote 博主")
        self.assertEqual(creator["fans"], 12345)
        self.assertEqual(creator["follows"], 12)
        self.assertEqual(creator["liked"], 6789)
        self.assertEqual(creator["collected"], 345)
        self.assertEqual(creator["posted"], 88)
        self.assertEqual(creator["interaction"], 7134)
        self.assertEqual(creator["avatar"], "https://example.com/avatar.jpg")
        self.assertEqual(creator["ip_location"], "上海")

    def test_profile_normalizers_prefer_upstream_identity(self) -> None:
        rnote = normalize_rnote_user(
            {"data": {"result": {"user_id": "upstream"}}}, "requested"
        )
        self.assertEqual(rnote["user_id"], "upstream")
        cases = (
            ("xhs", {"data": {"user_id": "upstream"}}, "upstream"),
            ("douyin", {"data": {"sec_uid": "upstream-sec"}}, "upstream-sec"),
            ("bilibili", {"data": {"mid": 123456}}, "123456"),
            ("zhihu", {"data": {"author_id": "upstream"}}, "upstream"),
        )
        for platform, payload, expected in cases:
            with self.subTest(platform=platform):
                creator = normalize_user_platform(platform, payload, "requested")
                self.assertEqual(creator["user_id"], expected)

    def test_pipeline_falls_back_before_round_start(self) -> None:
        class FakeProvider:
            def __init__(self, provider_id: str, fail_balance: bool = False) -> None:
                self.provider_id = provider_id
                self.label = provider_id
                self.search_path = f"/{provider_id}/search"
                self.user_path = f"/{provider_id}/user"
                self.search_price = Price(45, "token")
                self.user_price = Price(45, "token")
                self.search_cost_tokens = 45
                self.user_cost_tokens = 45
                self.fail_balance = fail_balance
                self.closed = False

            def balance(self) -> BalanceResponse:
                if self.fail_balance:
                    raise RuntimeError("balance failed")
                return BalanceResponse(10000, "token", 10000, "ok")

            def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
                return SearchResponse(
                    {"code": 0}, [search_item("n1", "u1", "咖啡师")], {}
                )

            def user(self, user_id: str, profile_url: str = ""):
                return user_payload(user_id, "咖啡师", fans=100)

            def normalize_search(self, item):
                return normalize_search_item(item)

            def normalize_user(self, payload, user_id: str):
                from codata.normalize import normalize_user_info

                return normalize_user_info(payload, user_id=user_id)

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            with Database(Path(tmp)) as db:
                round_id = db.create_round(["咖啡"], max_creators=1, provider="auto")
                pipeline = Pipeline(
                    Settings(host="", token="", output_dir=Path(tmp)),
                    db,
                )
                bad = FakeProvider("dataflow", fail_balance=True)
                good = FakeProvider("rnote")
                pipeline._provider_candidates = lambda platform, requested: [bad, good]
                summary = pipeline.run_round(round_id)
                self.assertEqual(summary["provider"], "rnote")
                self.assertTrue(bad.closed)
                self.assertTrue(good.closed)
                self.assertEqual(db.get_round(round_id)["provider"], "auto")
                self.assertEqual(summary["cost_tokens"], 90)

    def test_rnote_client_uses_api_key(self) -> None:
        settings = Settings(
            host="",
            token="",
            rnote_host="https://rnote.dev",
            rnote_api_key="rnote-key",
            rnote_allowed_hosts=("rnote.dev",),
        )
        registry = load_registry(settings)
        provider = registry.providers["rnote"]
        client = RnoteClient(settings, provider)
        client._client.close()
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers.get("X-API-Key")
            return httpx.Response(200, json={"success": True, "data": {"ok": 1}})

        client._client = httpx.Client(
            base_url="https://rnote.dev",
            headers={"X-API-Key": "rnote-key"},
            transport=httpx.MockTransport(handler),
        )
        try:
            payload = client.get("/api/v2/crawler/search/notes", {"keyword": "咖啡"})
            self.assertEqual(payload["data"]["ok"], 1)
            self.assertEqual(seen["key"], "rnote-key")
        finally:
            client.close()

    def test_everyinfra_search_and_user(self) -> None:
        settings = Settings(host="", token="", output_dir=Path("data"))
        with patch.dict(os.environ, {"EVERYINFRA_API_KEY": "every-key"}):
            registry = load_registry(settings)
            option = registry.platforms["xhs"].providers["everyinfra"]
            provider = EveryInfraProvider(settings, option, "xhs")
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(__import__("json").loads(request.content or b"{}"))
            if request.url.path.endswith("/social"):
                return httpx.Response(
                    200,
                    json={
                        "platform": "xiaohongshu",
                        "action": "search",
                        "data": [
                            {
                                "id": "n-every",
                                "text": "咖啡 #咖啡",
                                "author_id": "u-every",
                                "author_name": "咖啡师",
                                "like_count": 10,
                                "collect_count": 2,
                                "comment_count": 1,
                                "share_count": 1,
                                "posted_at": "2026-01-01 10:00:00",
                            }
                        ],
                        "next_page_token": "next",
                    },
                )
            return httpx.Response(200, json={"data": {"nickname": "咖啡师", "follower_count": 100}})

        provider.client._client.close()
        provider.client._client = httpx.Client(
            base_url="https://api.everyinfra.com",
            transport=httpx.MockTransport(handler),
        )
        try:
            result = provider.search("咖啡", 1, {})
            self.assertEqual(result.items[0]["id"], "n-every")
            self.assertEqual(result.state["page_token"], "next")
            note, author = provider.normalize_search(result.items[0])
            self.assertEqual(note["platform"], "xhs")
            self.assertEqual(note["note_id"], "n-every")
            self.assertEqual(author["user_id"], "u-every")
            provider.user("u-every")
            self.assertEqual(seen[0]["platform"], "xiaohongshu")
            self.assertEqual(seen[0]["action"], "search")
        finally:
            provider.close()

    def test_multi_provider_request_contracts(self) -> None:
        settings = Settings(host="", token="", output_dir=Path("data"))
        credentials = {
            "TIKHUB_API_KEY": "tikhub-key",
            "JUSTONEAPI_TOKEN": "justone-key",
            "SOCIALDATAX_API_KEY": "social-key",
            "REDFOX_API_KEY": "redfox-key",
            "CODATA_PROVIDER_DELAY": "0",
        }
        cases = {
            "tikhub": (200, "GET", "user_id"),
            "justoneapi": (0, "GET", "userId"),
            "socialdatax": (None, "POST", "user_id"),
            "redfox": (2000, "POST", "accountId"),
        }
        with patch.dict(os.environ, credentials):
            registry = load_registry(settings)
            for provider_id, (success_code, method, user_key) in cases.items():
                with self.subTest(provider=provider_id):
                    option = registry.platforms["xhs"].providers[provider_id]
                    provider = build_provider(settings, option, "xhs")
                    requests: list[httpx.Request] = []

                    def handler(request: httpx.Request, option=option, success_code=success_code):
                        requests.append(request)
                        envelope = {} if success_code is None else {"code": success_code}
                        if request.url.path == option.search.path:
                            envelope["data"] = {"items": [{
                                "note_id": "n-contract",
                                "title": "契约测试",
                                "user_id": "u-contract",
                                "nickname": "达人",
                            }]}
                        else:
                            envelope["data"] = {
                                "user_id": "u-contract",
                                "nickname": "达人",
                                "fans": 100,
                            }
                        if provider_id == "socialdatax":
                            envelope["success"] = True
                        return httpx.Response(200, json=envelope)

                    provider.client._client.close()
                    provider.client._client = httpx.Client(
                        base_url=option.provider.base_url,
                        transport=httpx.MockTransport(handler),
                    )
                    try:
                        result = provider.search("咖啡", 1, {})
                        self.assertEqual(result.items[0]["note_id"], "n-contract")
                        provider.user("u-contract")
                        self.assertEqual(str(requests[0].method), method)
                        self.assertEqual(requests[0].url.path, option.search.path)
                        self.assertEqual(requests[1].url.path, option.user.path)
                        if method == "GET":
                            self.assertEqual(
                                requests[1].url.params.get(user_key), "u-contract"
                            )
                        else:
                            body = __import__("json").loads(requests[1].content)
                            self.assertEqual(body[user_key], "u-contract")
                        if provider_id == "justoneapi":
                            self.assertEqual(
                                requests[0].url.params.get("token"), "justone-key"
                            )
                    finally:
                        provider.close()

    def test_everyinfra_platform_actions(self) -> None:
        settings = Settings(host="", token="", output_dir=Path("data"))
        with patch.dict(os.environ, {"EVERYINFRA_API_KEY": "every-key"}):
            registry = load_registry(settings)
        cases = {
            "bilibili": ("search", "user_id", {"bvid": "BV1x", "title": "B站视频", "author_id": "123", "author_name": "UP"}),
            "zhihu": ("search", "url", {"id": "z1", "type": "answer", "title": "标题", "author_id": "u1", "author_name": "答主", "author_url": "https://www.zhihu.com/people/u1"}),
            "instagram": ("reels_search", "username", {"post_id": "ig1", "text": "IG", "author_username": "creator", "author_id": "42"}),
        }
        for platform, (expected_action, expected_user_key, sample) in cases.items():
            option = registry.platforms[platform].providers["everyinfra"]
            provider = EveryInfraProvider(settings, option, platform)
            seen: list[dict] = []

            def handler(request: httpx.Request, expected_action=expected_action, sample=sample) -> httpx.Response:
                body = __import__("json").loads(request.content or b"{}")
                seen.append(body)
                if body.get("action") in {"search", "reels_search"}:
                    return httpx.Response(200, json={"data": [sample], "next_page_token": None})
                return httpx.Response(200, json={"data": {"name": "author"}})

            provider.client._client.close()
            provider.client._client = httpx.Client(
                base_url=option.provider.base_url,
                transport=httpx.MockTransport(handler),
            )
            try:
                result = provider.search("咖啡", 1, {})
                self.assertEqual(seen[0]["action"], expected_action)
                self.assertEqual(seen[0]["platform"], platform)
                note, author = provider.normalize_search(result.items[0])
                self.assertEqual(note["platform"], platform)
                self.assertIsNotNone(author)
                profile_url = "https://www.zhihu.com/people/u1" if platform == "zhihu" else ""
                provider.user("u1", profile_url)
                self.assertIn(expected_user_key, seen[1]["params"])
            finally:
                provider.close()

    def test_normalize_new_platforms(self) -> None:
        bili, bili_author = normalize_search_item_platform(
            {
                "bvid": "BV1xx",
                "title": "B站视频",
                "author_id": 123,
                "author_name": "UP",
                "author_url": "https://space.bilibili.com/123",
                "stat": {"like": 5, "reply": 2},
            },
            "bilibili",
        )
        self.assertEqual(bili["platform"], "bilibili")
        self.assertEqual(bili["note_id"], "BV1xx")
        self.assertEqual(bili_author["user_id"], "123")
        self.assertEqual(bili_author["profile_url"], "https://space.bilibili.com/123")

        zhihu, zhihu_author = normalize_search_item_platform(
            {
                "id": "z1",
                "type": "answer",
                "title": "知乎回答",
                "author_id": "u1",
                "author_name": "答主",
                "author_url": "https://www.zhihu.com/people/u1",
                "like_count": 5,
            },
            "zhihu",
        )
        self.assertEqual(zhihu["platform"], "zhihu")
        self.assertEqual(zhihu_author["user_id"], "u1")
        self.assertEqual(zhihu_author["profile_url"], "https://www.zhihu.com/people/u1")

        insta, insta_author = normalize_search_item_platform(
            {
                "post_id": "ig1",
                "text": "IG content",
                "author_username": "creator",
                "like_count": 5,
                "taken_at": 1767225600,
            },
            "instagram",
        )
        self.assertEqual(insta["platform"], "instagram")
        self.assertEqual(insta["note_id"], "ig1")
        self.assertEqual(insta_author["user_id"], "creator")
        self.assertEqual(insta_author["profile_url"], "https://www.instagram.com/creator/")

        user = normalize_user_platform("instagram", {"data": {"username": "creator", "follower_count": 10}}, "creator")
        self.assertEqual(user["platform"], "instagram")
        self.assertEqual(user["fans"], 10)

    def test_normalize_everyinfra_profile_results(self) -> None:
        user = normalize_user_platform(
            "xhs",
            {
                "platform": "xiaohongshu",
                "action": "profile",
                "results": {
                    "user_id": "u1",
                    "nickname": "creator",
                    "follower_count": 91,
                    "following_count": 406,
                    "like_count": 210,
                    "collect_count": 52,
                    "post_count": 18,
                    "bio": "bio",
                },
            },
            "u1",
        )
        self.assertEqual(user["nickname"], "creator")
        self.assertEqual(user["fans"], 91)
        self.assertEqual(user["posted"], 18)
        self.assertEqual(user["interaction"], 262)

    def test_round_provider_and_api_call_cost(self) -> None:



        with tempfile.TemporaryDirectory() as tmp:
            with Database(Path(tmp)) as db:
                round_id = db.create_round(["咖啡"], platform="xhs", provider="rnote")
                round_row = db.get_round(round_id)
                self.assertEqual(round_row["platform"], "xhs")
                self.assertEqual(round_row["provider"], "rnote")
                db.log_api_call(
                    round_id,
                    "xhs",
                    "search",
                    {"keyword": "咖啡"},
                    None,
                    None,
                    72,
                    10,
                    path="/api/v2/crawler/search/notes",
                    provider="rnote",
                    cost_native=0.01,
                    cost_currency="USD",
                )
                call = db.recent_api_calls(round_id, limit=1)[0]
                self.assertEqual(call["provider"], "rnote")
                self.assertEqual(call["cost"], 72)
                self.assertEqual(call["cost_native"], 0.01)
                self.assertEqual(call["cost_currency"], "USD")


if __name__ == "__main__":
    unittest.main()
