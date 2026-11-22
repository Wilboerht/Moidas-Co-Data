from __future__ import annotations

from decimal import Decimal
from typing import Any

from .client import JsonProviderClient, ProviderError
from .config import Settings
from .normalize_platform import (
    normalize_search_item_platform,
    normalize_user_platform,
)
from .platforms import PlatformProviderRuntime
from .providers import BalanceResponse, Provider, SearchResponse
from .storage import as_items, unwrap_data

PLATFORM_MAP = {
    "xhs": "xiaohongshu",
    "douyin": "douyin",
    "bilibili": "bilibili",
    "zhihu": "zhihu",
    "instagram": "instagram",
}


def _find(payload: Any, keys: tuple[str, ...], depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", []):
                return value
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _find(value, keys, depth + 1)
                if found not in (None, "", []):
                    return found
    elif isinstance(payload, list):
        for value in payload[:5]:
            found = _find(value, keys, depth + 1)
            if found not in (None, "", []):
                return found
    return None


def _items(payload: Any) -> list[dict[str, Any]]:
    items = as_items(payload)
    if items:
        return items
    for key in ("business_data", "results", "videos", "works", "reels", "posts", "items", "data"):
        value = _find(payload, (key,))
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


class JsonProvider(Provider):
    adapter = ""
    supports_balance = False

    def __init__(self, settings: Settings, option: PlatformProviderRuntime, platform: str) -> None:
        self.settings = settings
        self.option = option
        self.provider = option.provider
        self.platform = platform
        self.search_path = option.search.path
        self.user_path = option.user.path
        self.search_price = option.search.price
        self.user_price = option.user.price
        self.search_cost_tokens = option.search.price.tokens(
            Decimal(str(settings.usd_cny)), Decimal(str(settings.token_cny))
        )
        self.user_cost_tokens = option.user.price.tokens(
            Decimal(str(settings.usd_cny)), Decimal(str(settings.token_cny))
        )
        self.client = JsonProviderClient(settings, self.provider)

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        raise NotImplementedError

    def user(self, user_id: str, profile_url: str = "") -> Any:
        raise NotImplementedError

    def normalize_search(self, item: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return normalize_search_item_platform(item, self.platform)

    def normalize_user(self, payload: Any, user_id: str) -> dict[str, Any]:
        return normalize_user_platform(self.platform, payload, user_id)

    def balance(self) -> BalanceResponse:
        return BalanceResponse(None, "token", None, f"{self.label} 未提供余额接口")

    def close(self) -> None:
        self.client.close()


class EveryInfraProvider(JsonProvider):
    provider_id = "everyinfra"
    label = "EveryInfra"
    adapter = "everyinfra"
    supports_balance = True

    def _call(self, action: str, params: dict[str, Any], cost: int) -> Any:
        return self.client.request(
            "POST",
            self.search_path,
            json_body={
                "platform": PLATFORM_MAP[self.platform],
                "action": action,
                "params": params,
            },
            cost=cost,
        )

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        params: dict[str, Any] = {"keyword": keyword}
        token = state.get("page_token")
        if token:
            params["page_token"] = token
        action = "reels_search" if self.platform == "instagram" else "search"
        payload = self._call(action, params, self.search_cost_tokens)
        next_token = _find(payload, ("next_page_token",))
        return SearchResponse(
            payload=payload,
            items=_items(payload),
            state={"page_token": str(next_token)} if next_token else {},
        )

    def user(self, user_id: str, profile_url: str = "") -> Any:
        if self.platform == "zhihu":
            params = {"url": profile_url or f"https://www.zhihu.com/people/{user_id}"}
        elif self.platform == "instagram":
            params = {"username": user_id}
        else:
            params = {"user_id": user_id}
        return self._call("profile", params, self.user_cost_tokens)

    def balance(self) -> BalanceResponse:
        payload = self.client.request("GET", self.provider.balance_path, cost=0)
        data = unwrap_data(payload)
        balance = _find(data, ("total_usd",))
        if balance is None and isinstance(data, dict):
            nested = data.get("balance")
            if isinstance(nested, dict):
                balance = nested.get("total_usd")
        if balance is None:
            return BalanceResponse(None, "USD", None, "EveryInfra 余额接口未返回余额")
        usd = Decimal(str(balance))
        tokens = int(usd * Decimal(str(self.settings.usd_cny)) / Decimal(str(self.settings.token_cny)))
        return BalanceResponse(usd, "USD", tokens, f"剩余 ${usd.quantize(Decimal('0.0001'))}（约 {tokens} token）")


class TikHubProvider(JsonProvider):
    provider_id = "tikhub"
    label = "TikHub"
    adapter = "tikhub"
    supports_balance = True

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        if self.platform == "xhs":
            params: dict[str, Any] = {"keyword": keyword, "page": page}
            if state.get("search_id"):
                params["search_id"] = state["search_id"]
            if state.get("search_session_id"):
                params["search_session_id"] = state["search_session_id"]
            payload = self.client.request(
                "GET", self.search_path, params=params, cost=self.search_cost_tokens
            )
            return SearchResponse(
                payload=payload,
                items=_items(payload),
                state={
                    "search_id": str(_find(payload, ("search_id",)) or ""),
                    "search_session_id": str(_find(payload, ("search_session_id",)) or ""),
                },
            )
        body = {
            "keyword": keyword,
            "cursor": int(state.get("cursor") or 0),
            "sort_type": "0",
            "publish_time": "0",
            "filter_duration": "0",
            "content_type": "0",
        }
        if state.get("search_id"):
            body["search_id"] = state["search_id"]
        if state.get("backtrace"):
            body["backtrace"] = state["backtrace"]
        payload = self.client.request(
            "POST", self.search_path, json_body=body, cost=self.search_cost_tokens
        )
        return SearchResponse(
            payload=payload,
            items=_items(payload),
            state={
                "cursor": str(_find(payload, ("cursor",)) or 0),
                "search_id": str(_find(payload, ("search_id",)) or ""),
                "backtrace": str(_find(payload, ("backtrace",)) or ""),
            },
        )

    def user(self, user_id: str, profile_url: str = "") -> Any:
        params = {"sec_user_id": user_id} if self.platform == "douyin" else {"user_id": user_id}
        return self.client.request(
            "GET", self.user_path, params=params, cost=self.user_cost_tokens
        )

    def balance(self) -> BalanceResponse:
        payload = self.client.request("GET", self.provider.balance_path, cost=0)
        data = unwrap_data(payload)
        user_data = data.get("user_data") if isinstance(data, dict) else None
        if not isinstance(user_data, dict):
            user_data = data if isinstance(data, dict) else {}
        raw_balance = user_data.get("balance")
        free_credit = user_data.get("free_credit")
        if raw_balance is None:
            return BalanceResponse(None, "USD", None, "TikHub 余额接口未返回余额")
        usd = Decimal(str(raw_balance)) + Decimal(str(free_credit or 0))
        tokens = int(usd * Decimal(str(self.settings.usd_cny)) / Decimal(str(self.settings.token_cny)))
        return BalanceResponse(usd, "USD", tokens, f"剩余 ${usd.quantize(Decimal('0.0001'))}（约 {tokens} token）")


class JustOneAPIProvider(JsonProvider):
    provider_id = "justoneapi"
    label = "JustOneAPI"
    adapter = "justoneapi"

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        params: dict[str, Any] = {"keyword": keyword, "page": page}
        if self.platform == "douyin" and state.get("searchId"):
            params["searchId"] = state["searchId"]
        payload = self.client.request(
            "GET", self.search_path, params=params, cost=self.search_cost_tokens
        )
        return SearchResponse(
            payload=payload,
            items=_items(payload),
            state={"searchId": str(_find(payload, ("searchId", "search_id")) or "")},
        )

    def user(self, user_id: str, profile_url: str = "") -> Any:
        params = {"secUid": user_id} if self.platform == "douyin" else {"userId": user_id}
        return self.client.request(
            "GET", self.user_path, params=params, cost=self.user_cost_tokens
        )


class SocialDataXProvider(JsonProvider):
    provider_id = "socialdatax"
    label = "SocialDataX"
    adapter = "socialdatax"
    supports_balance = True

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        body: dict[str, Any] = {"keyword": keyword}
        if state.get("page_token"):
            body["page_token"] = state["page_token"]
        payload = self.client.request(
            "POST", self.search_path, json_body=body, cost=self.search_cost_tokens
        )
        token = _find(payload, ("page_token", "next_page_token"))
        return SearchResponse(
            payload=payload,
            items=_items(payload),
            state={"page_token": str(token)} if token else {},
        )

    def user(self, user_id: str, profile_url: str = "") -> Any:
        return self.client.request(
            "POST", self.user_path, json_body={"user_id": user_id}, cost=self.user_cost_tokens
        )

    def balance(self) -> BalanceResponse:
        payload = self.client.request("GET", self.provider.balance_path, cost=0)
        data = unwrap_data(payload)
        balance = _find(data, ("balance", "points"))
        if balance is None:
            return BalanceResponse(None, "points", None, "SocialDataX 余额接口未返回余额")
        points = Decimal(str(balance))
        tokens = int(points * Decimal("10"))
        return BalanceResponse(points, "points", tokens, f"剩余 {points} 积分（约 {tokens} token）")


class RedFoxProvider(JsonProvider):
    provider_id = "redfox"
    label = "RedFoxHub"
    adapter = "redfox"
    supports_balance = True

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        offset = int(state.get("offset") or (page - 1) * 20)
        body = {"keyword": keyword, "offset": offset, "sortType": "_0"}
        payload = self.client.request(
            "POST", self.search_path, json_body=body, cost=self.search_cost_tokens
        )
        has_more = _find(payload, ("hasMore", "has_more"))
        return SearchResponse(
            payload=payload,
            items=_items(payload),
            state={"offset": str(offset + 20)} if has_more not in (False, 0, "0") else {},
        )

    def user(self, user_id: str, profile_url: str = "") -> Any:
        if self.platform == "douyin":
            body = {"accountId": user_id}
        else:
            body = {"accountId": user_id, "userId": user_id}
        return self.client.request(
            "POST", self.user_path, json_body=body, cost=self.user_cost_tokens
        )

    def balance(self) -> BalanceResponse:
        payload = self.client.request(
            "GET",
            self.provider.balance_path,
            params={"apiKey": self.provider.api_key},
            cost=0,
        )
        data = unwrap_data(payload)
        points = _find(data, ("totalAvailablePoints", "total_available_points"))
        if points is None:
            return BalanceResponse(None, "points", None, "RedFoxHub 余额接口未返回积分")
        amount = Decimal(str(points))
        tokens = int(amount * Decimal("100"))
        return BalanceResponse(amount, "points", tokens, f"剩余 {amount.quantize(Decimal('0.01'))} 积分（约 {tokens} token）")


MULTI_PROVIDERS = {
    "everyinfra": EveryInfraProvider,
    "tikhub": TikHubProvider,
    "justoneapi": JustOneAPIProvider,
    "socialdatax": SocialDataXProvider,
    "redfox": RedFoxProvider,
}


def build_multi_provider(
    settings: Settings,
    option: PlatformProviderRuntime,
    platform: str,
) -> Provider | None:
    adapter = option.provider.adapter
    provider_class = MULTI_PROVIDERS.get(adapter)
    if provider_class is None:
        return None
    return provider_class(settings, option, platform)
