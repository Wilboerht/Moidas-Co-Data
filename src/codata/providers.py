from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .api import DataFlowAPI, RnoteAPI, XHS_COST
from .client import DataFlowClient, ProviderError, RnoteClient
from .config import Settings
from .normalize import as_int, normalize_rnote_user, normalize_search_item, normalize_user_info
from .platforms import PlatformProviderRuntime, Price, Registry, load_registry
from .storage import as_items, unwrap_data


@dataclass(frozen=True)
class SearchResponse:
    payload: Any
    items: list[dict[str, Any]]
    state: dict[str, str]


@dataclass(frozen=True)
class BalanceResponse:
    remaining_native: Decimal | None
    currency: str
    remaining_tokens: int | None
    text: str


class Provider:
    provider_id = ""
    label = ""
    platform = "xhs"
    supports_balance = True
    search_path = ""
    user_path = ""
    search_cost_tokens = 0
    user_cost_tokens = 0
    search_price: Price | None = None
    user_price: Price | None = None

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        raise NotImplementedError

    def user(self, user_id: str, profile_url: str = "") -> Any:
        raise NotImplementedError

    def balance(self) -> BalanceResponse:
        raise NotImplementedError

    def normalize_search(self, item: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return normalize_search_item(item)

    def normalize_user(self, payload: Any, user_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class DataFlowProvider(Provider):
    provider_id = "dataflow"
    label = "DataFlow"

    def __init__(self, settings: Settings, option: PlatformProviderRuntime, platform: str = "xhs") -> None:
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
        self.client = DataFlowClient(settings, self.provider)
        self.api = DataFlowAPI(self.client)

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        payload = self.client.get(
            self.search_path,
            {"keyword": keyword, "page": page},
            cost=self.search_cost_tokens,
        )
        return SearchResponse(payload=payload, items=as_items(payload), state={})

    def user(self, user_id: str, profile_url: str = "") -> Any:
        return self.client.get(
            self.user_path,
            {"userId": user_id},
            cost=self.user_cost_tokens,
        )

    def balance(self) -> BalanceResponse:
        payload = self.client.get(self.provider.balance_path, cost=0)
        data = unwrap_data(payload)
        if not isinstance(data, dict) or data.get("remaining_calls") is None:
            return BalanceResponse(None, "token", None, "余额接口未返回剩余额度")
        remaining = as_int(data.get("remaining_calls"))
        return BalanceResponse(
            Decimal(remaining),
            "token",
            remaining,
            f"剩余 {remaining} token（后台约 10 分钟刷新一次）",
        )

    def normalize_user(self, payload: Any, user_id: str) -> dict[str, Any]:
        return normalize_user_info(payload, user_id=user_id)

    def close(self) -> None:
        self.client.close()


class LegacyDataFlowProvider(Provider):
    provider_id = "dataflow"
    label = "DataFlow"
    search_cost_tokens = XHS_COST
    user_cost_tokens = XHS_COST
    search_price = Price(Decimal(XHS_COST), "token")
    user_price = Price(Decimal(XHS_COST), "token")

    def __init__(self, api: Any, platform: str = "xhs") -> None:
        self.api = api
        self.platform = platform

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        payload = self.api.xhs_search(keyword, page)
        return SearchResponse(payload=payload, items=as_items(payload), state={})

    def user(self, user_id: str, profile_url: str = "") -> Any:
        return self.api.xhs_user(user_id)

    def balance(self) -> BalanceResponse:
        payload = self.api.balance()
        data = unwrap_data(payload)
        if not isinstance(data, dict) or data.get("remaining_calls") is None:
            return BalanceResponse(None, "token", None, "余额接口未返回剩余额度")
        remaining = as_int(data.get("remaining_calls"))
        return BalanceResponse(
            Decimal(remaining),
            "token",
            remaining,
            f"剩余 {remaining} token（后台约 10 分钟刷新一次）",
        )

    def normalize_user(self, payload: Any, user_id: str) -> dict[str, Any]:
        return normalize_user_info(payload, user_id=user_id)

    def close(self) -> None:
        client = getattr(self.api, "client", None)
        if client is not None and hasattr(client, "close"):
            client.close()


class RnoteProvider(Provider):
    provider_id = "rnote"
    label = "Rnote"

    def __init__(self, settings: Settings, option: PlatformProviderRuntime, platform: str = "xhs") -> None:
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
        self.client = RnoteClient(settings, self.provider)
        self.api = RnoteAPI(self.client)

    def search(self, keyword: str, page: int, state: dict[str, str]) -> SearchResponse:
        payload = self.api.search_notes(
            keyword,
            page=page,
            search_id=state.get("search_id"),
            search_session_id=state.get("search_session_id"),
            path=self.search_path,
            cost=self.search_cost_tokens,
        )
        return SearchResponse(
            payload=payload,
            items=as_items(payload),
            state=self._pagination(payload),
        )

    @staticmethod
    def _pagination(payload: Any) -> dict[str, str]:
        candidates: list[Any] = [payload]
        if isinstance(payload, dict):
            candidates.append(payload.get("data"))
            data = payload.get("data")
            if isinstance(data, dict):
                candidates.append(data.get("data"))
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("search_id") or item.get("search_session_id"):
                return {
                    "search_id": str(item.get("search_id") or ""),
                    "search_session_id": str(item.get("search_session_id") or ""),
                }
        return {}

    def user(self, user_id: str, profile_url: str = "") -> Any:
        return self.api.user(
            user_id,
            path=self.user_path,
            cost=self.user_cost_tokens,
        )

    def balance(self) -> BalanceResponse:
        payload = self.api.balance()
        data = unwrap_data(payload)
        if not isinstance(data, dict):
            return BalanceResponse(None, "USD", None, "余额接口未返回余额")
        raw = data.get("balance")
        if raw is None and isinstance(data.get("data"), dict):
            raw = data["data"].get("balance")
        if raw is None:
            return BalanceResponse(None, "USD", None, "余额接口未返回余额")
        try:
            remaining = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return BalanceResponse(None, "USD", None, f"余额格式异常: {raw}")
        token_value = (
            remaining * Decimal(str(self.settings.usd_cny)) / Decimal(str(self.settings.token_cny))
            if self.settings.token_cny > 0
            else Decimal(0)
        )
        return BalanceResponse(
            remaining,
            "USD",
            int(token_value),
            f"剩余 ${remaining.quantize(Decimal('0.000001'))}（约 {int(token_value)} token）",
        )

    def normalize_user(self, payload: Any, user_id: str) -> dict[str, Any]:
        return normalize_rnote_user(payload, user_id=user_id)

    def close(self) -> None:
        self.client.close()


def build_provider(
    settings: Settings,
    option: PlatformProviderRuntime,
    platform: str = "xhs",
) -> Provider:
    if option.provider.adapter == "dataflow":
        return DataFlowProvider(settings, option, platform)
    if option.provider.adapter == "rnote":
        return RnoteProvider(settings, option, platform)
    from .providers_multi import build_multi_provider

    provider = build_multi_provider(settings, option, platform)
    if provider is not None:
        return provider
    raise ProviderError(None, f"不支持的服务商适配器: {option.provider.adapter}")


def resolve_provider(
    settings: Settings,
    platform: str,
    requested: str | None,
    api: Any | None = None,
) -> tuple[Provider, PlatformProviderRuntime | None]:
    if api is not None:
        return LegacyDataFlowProvider(api, platform), None
    registry: Registry = load_registry(settings)
    option = registry.resolve(platform, requested)
    return build_provider(settings, option, platform), option


def provider_options(settings: Settings, platform: str = "xhs") -> list[PlatformProviderRuntime]:
    return load_registry(settings).options(platform)
