from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
from unittest.mock import patch

import httpx

from codata.client import RnoteClient
from codata.config import Settings
from codata.db import Database
from codata.normalize import normalize_rnote_user, normalize_search_item
from codata.platforms import PlatformConfigError, Price, load_registry, validate_provider_host
from codata.pipeline import Pipeline
from codata.providers import BalanceResponse, RnoteProvider, SearchResponse
from codata.providers_multi import EveryInfraProvider
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

            def user(self, user_id: str):
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
                self.assertEqual(db.get_round(round_id)["provider"], "rnote")
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
                                "text": "?? #??",
                                "author_id": "u-every",
                                "author_name": "???",
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
            return httpx.Response(200, json={"data": {"nickname": "???", "follower_count": 100}})

        provider.client._client.close()
        provider.client._client = httpx.Client(
            base_url="https://api.everyinfra.com",
            transport=httpx.MockTransport(handler),
        )
        try:
            result = provider.search("??", 1, {})
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
