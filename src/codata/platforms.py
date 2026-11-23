from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import PROJECT_ROOT, Settings, load_settings


class PlatformConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    amount: Decimal
    currency: str

    @property
    def unit(self) -> str:
        return self.currency.upper() if self.currency.lower() != "token" else "token"

    def cny(self, usd_cny: Decimal, token_cny: Decimal) -> Decimal:
        if self.currency.lower() == "token":
            return self.amount * token_cny
        if self.currency.upper() == "USD":
            return self.amount * usd_cny
        if self.currency.upper() == "CNY":
            return self.amount
        raise PlatformConfigError(f"不支持的计价币种: {self.currency}")

    def tokens(self, usd_cny: Decimal, token_cny: Decimal) -> int:
        if self.currency.lower() == "token":
            return int(self.amount)
        if token_cny <= 0:
            raise PlatformConfigError("token_cny 必须大于 0")
        return int((self.cny(usd_cny, token_cny) / token_cny).quantize(Decimal("1")))

    def display(self) -> str:
        if self.currency.lower() == "token":
            return f"{self.amount.normalize()} token"
        if self.currency.upper() == "USD":
            return f"${self.amount.normalize()}"
        if self.currency.upper() == "CNY":
            return f"¥{self.amount.normalize()}"
        return f"{self.amount.normalize()} {self.currency}"


@dataclass(frozen=True)
class Endpoint:
    path: str
    price: Price


@dataclass(frozen=True)
class ProviderRuntime:
    id: str
    label: str
    adapter: str
    base_url: str
    api_key: str
    allowed_hosts: tuple[str, ...]
    auth_header: str
    auth_prefix: str
    auth_query: str
    success_codes: tuple[int, ...]
    api_key_env: str
    balance_path: str
    balance_currency: str
    balance_token_rate: Decimal
    timeout: float
    retries: int
    delay: float

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""


@dataclass(frozen=True)
class PlatformProviderRuntime:
    provider: ProviderRuntime
    search: Endpoint
    user: Endpoint
    enabled: bool = True

    @property
    def ready(self) -> bool:
        return self.enabled and self.provider.configured


@dataclass(frozen=True)
class PlatformRuntime:
    code: str
    label: str
    enabled: bool
    default_provider: str
    providers: dict[str, PlatformProviderRuntime]


@dataclass(frozen=True)
class Registry:
    providers: dict[str, ProviderRuntime]
    platforms: dict[str, PlatformRuntime]
    usd_cny: Decimal
    token_cny: Decimal

    def platform(self, code: str) -> PlatformRuntime:
        platform = self.platforms.get(code)
        if not platform or not platform.enabled:
            raise PlatformConfigError(f"平台未启用: {code}")
        return platform

    def provider(self, provider_id: str) -> ProviderRuntime:
        provider = self.providers.get(provider_id)
        if not provider:
            raise PlatformConfigError(f"未知服务商: {provider_id}")
        return provider

    def options(self, platform_code: str) -> list[PlatformProviderRuntime]:
        return list(self.platform(platform_code).providers.values())

    def cheapest(self, platform_code: str, require_ready: bool = True) -> PlatformProviderRuntime | None:
        options = [
            option
            for option in self.options(platform_code)
            if not require_ready or option.ready
        ]
        if not options:
            return None
        return min(
            options,
            key=lambda option: (
                option.search.price.tokens(self.usd_cny, self.token_cny),
                list(self.platform(platform_code).providers).index(option.provider.id),
            ),
        )

    def cheapest_user(
        self, platform_code: str, require_ready: bool = True
    ) -> PlatformProviderRuntime | None:
        options = [
            option
            for option in self.options(platform_code)
            if not require_ready or option.ready
        ]
        if not options:
            return None
        return min(
            options,
            key=lambda option: (
                option.user.price.tokens(self.usd_cny, self.token_cny),
                list(self.platform(platform_code).providers).index(option.provider.id),
            ),
        )

    def resolve(self, platform_code: str, requested: str | None) -> PlatformProviderRuntime:
        platform = self.platform(platform_code)
        requested = (requested or platform.default_provider or "cheapest").strip().lower()
        if requested in {"auto", "cheapest", "default"}:
            option = self.cheapest(platform_code)
            if not option:
                raise PlatformConfigError("没有可用的采集服务商，请先配置 DataFlow 或 Rnote")
            return option
        option = platform.providers.get(requested)
        if not option:
            raise PlatformConfigError(f"平台 {platform_code} 不支持服务商: {requested}")
        if not option.enabled:
            raise PlatformConfigError(f"服务商未启用: {requested}")
        if not option.provider.configured:
            raise PlatformConfigError(f"服务商未配置完整: {option.provider.label}")
        return option

    def public_platforms(self) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for platform in self.platforms.values():
            if not platform.enabled:
                continue
            options = []
            for option in platform.providers.values():
                search = option.search.price
                user = option.user.price
                options.append(
                    {
                        "id": option.provider.id,
                        "label": option.provider.label,
                        "ready": option.ready,
                        "enabled": option.enabled,
                        "search_cost_native": str(search.amount),
                        "search_currency": search.unit,
                        "search_cost_tokens": search.tokens(self.usd_cny, self.token_cny),
                        "search_cost_cny": str(search.cny(self.usd_cny, self.token_cny)),
                        "user_cost_native": str(user.amount),
                        "user_currency": user.unit,
                        "user_cost_tokens": user.tokens(self.usd_cny, self.token_cny),
                        "user_cost_cny": str(user.cny(self.usd_cny, self.token_cny)),
                    }
                )
            default = self.cheapest(platform.code)
            payload.append(
                {
                    "code": platform.code,
                    "label": platform.label,
                    "default_provider": default.provider.id if default else None,
                    "providers": options,
                }
            )
        return payload

    def public_providers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": provider.id,
                "label": provider.label,
                "adapter": provider.adapter,
                "host": provider.base_url,
                "hostname": provider.host,
                "token_configured": bool(provider.api_key),
                "ready": provider.configured,
            }
            for provider in self.providers.values()
        ]


def _price(value: Any, currency: Any, field: str) -> Price:
    unit = str(currency or "token").strip()
    if unit.lower() != "token" and unit.upper() not in {"USD", "CNY"}:
        raise PlatformConfigError(f"{field} 币种必须是 token、USD 或 CNY")
    return Price(_decimal(value, field), unit)


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PlatformConfigError(f"{field} 必须是数字") from exc
    if not result.is_finite():
        raise PlatformConfigError(f"{field} 必须是有限数字")
    if result < 0:
        raise PlatformConfigError(f"{field} 不能为负数")
    return result


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip())


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
    return bool(value)


def _validate_path(path: str, field: str) -> str:
    path = (path or "").strip()
    if not path.startswith("/") or path.startswith("//") or "://" in path or ".." in path:
        raise PlatformConfigError(f"{field} 必须是站内相对路径")
    return path


def validate_provider_host(base_url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PlatformConfigError(f"无效的服务商地址: {base_url}")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise PlatformConfigError(f"服务商地址必须使用 HTTPS: {base_url}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PlatformConfigError(f"服务商地址不能包含凭证、查询参数或片段: {base_url}")
    host = parsed.hostname.lower()
    allowed = tuple(item.lower() for item in allowed_hosts if item)
    if allowed and host not in allowed:
        raise PlatformConfigError(
            f"服务商域名不在允许列表: {host}；允许: {', '.join(allowed)}"
        )
    return host


def _read_config(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PlatformConfigError(f"缺少平台配置文件: {path}") from exc
    except OSError as exc:
        raise PlatformConfigError(f"读取平台配置失败: {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlatformConfigError(f"平台配置不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PlatformConfigError("平台配置必须是 JSON 对象")
    return data


def load_registry(
    settings: Settings | None = None,
    path: Path | None = None,
) -> Registry:
    settings = settings or load_settings(require_credentials=False)
    config_path = path or Path(
        os.getenv("CODATA_PLATFORMS_FILE", str(PROJECT_ROOT / "config" / "platforms.json"))
    )
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    data = _read_config(config_path)
    token_cny = _decimal(settings.token_cny, "CODATA_TOKEN_CNY")
    usd_cny = _decimal(settings.usd_cny, "CODATA_USD_CNY")
    if token_cny <= 0:
        raise PlatformConfigError("CODATA_TOKEN_CNY 必须大于 0")
    if usd_cny <= 0:
        raise PlatformConfigError("CODATA_USD_CNY 必须大于 0")

    providers_raw = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    providers: dict[str, ProviderRuntime] = {}
    for provider_id, item in providers_raw.items():
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or provider_id)
        adapter = str(item.get("adapter") or "").strip().lower()
        if not adapter:
            raise PlatformConfigError(f"provider {provider_id} 缺少 adapter")
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        base_url_env = str(item.get("base_url_env") or "")
        if not base_url and base_url_env:
            base_url = os.getenv(base_url_env, "").strip().rstrip("/")
        if provider_id == "dataflow" and settings.host:
            base_url = settings.host.rstrip("/")
        if provider_id == "rnote" and settings.rnote_host:
            base_url = settings.rnote_host.rstrip("/")
        api_key = ""
        if provider_id == "dataflow":
            api_key = settings.token
        elif provider_id == "rnote":
            api_key = settings.rnote_api_key
        else:
            key_env = str(item.get("api_key_env") or "")
            api_key = os.getenv(key_env, "").strip() if key_env else ""

        allowed: tuple[str, ...] = ()
        if provider_id == "dataflow" and settings.dataflow_allowed_hosts:
            allowed = tuple(host.lower() for host in settings.dataflow_allowed_hosts)
        elif provider_id == "rnote" and settings.rnote_allowed_hosts:
            allowed = tuple(host.lower() for host in settings.rnote_allowed_hosts)
        if not allowed:
            allowed = _env_list(str(item.get("allowed_hosts_env") or ""))
        if not allowed:
            configured = item.get("allowed_hosts")
            if isinstance(configured, list):
                allowed = tuple(str(value).strip().lower() for value in configured if str(value).strip())
        if base_url:
            try:
                validate_provider_host(base_url, allowed)
            except PlatformConfigError as exc:
                print(f"[provider] 跳过 {provider_id}: {exc}", file=sys.stderr)
                continue

        raw_codes = item.get("success_codes", [])
        if not isinstance(raw_codes, list):
            raw_codes = []
        try:
            success_codes = tuple(int(value) for value in raw_codes)
        except (TypeError, ValueError):
            success_codes = ()
        raw_balance_path = str(item.get("balance_path") or "").strip()
        balance_path = (
            _validate_path(raw_balance_path, f"{provider_id}.balance_path")
            if raw_balance_path
            else ""
        )
        auth_query = str(item.get("auth_query") or "").strip()
        if auth_query and not re.fullmatch(r"[A-Za-z0-9_.-]+", auth_query):
            auth_query = ""
        providers[provider_id] = ProviderRuntime(
            id=provider_id,
            label=label,
            adapter=adapter,
            base_url=base_url,
            api_key=api_key,
            allowed_hosts=allowed,
            auth_header=str(item.get("auth_header") or "Authorization"),
            auth_prefix=str(item.get("auth_prefix") or ""),
            auth_query=auth_query,
            success_codes=success_codes,
            api_key_env=str(item.get("api_key_env") or ""),
            balance_path=balance_path,
            balance_currency=str(item.get("balance_currency") or "token"),
            balance_token_rate=_decimal(
                item.get("balance_token_rate", 0), f"{provider_id}.balance_token_rate"
            ),
            timeout=float(os.getenv("CODATA_PROVIDER_TIMEOUT", settings.timeout)),
            retries=max(1, int(os.getenv("CODATA_PROVIDER_RETRY", settings.retries))),
            delay=max(0.0, float(os.getenv("CODATA_PROVIDER_DELAY", settings.delay))),
        )

    platforms_raw = data.get("platforms") if isinstance(data.get("platforms"), dict) else {}
    platforms: dict[str, PlatformRuntime] = {}
    for platform_code, item in platforms_raw.items():
        if not isinstance(item, dict):
            continue
        providers_for_platform: dict[str, PlatformProviderRuntime] = {}
        options = item.get("providers") if isinstance(item.get("providers"), dict) else {}
        for provider_id, endpoint_data in options.items():
            provider = providers.get(provider_id)
            if not provider or not isinstance(endpoint_data, dict):
                continue
            search = endpoint_data.get("search")
            user = endpoint_data.get("user")
            if not isinstance(search, dict) or not isinstance(user, dict):
                raise PlatformConfigError(f"{platform_code}/{provider_id} 缺少 search 或 user")
            providers_for_platform[provider_id] = PlatformProviderRuntime(
                provider=provider,
                search=Endpoint(
                    path=_validate_path(str(search.get("path") or ""), f"{platform_code}.{provider_id}.search.path"),
                    price=_price(
                        search.get("cost"),
                        search.get("currency"),
                        f"{platform_code}.{provider_id}.search.cost",
                    ),
                ),
                user=Endpoint(
                    path=_validate_path(str(user.get("path") or ""), f"{platform_code}.{provider_id}.user.path"),
                    price=_price(
                        user.get("cost"),
                        user.get("currency"),
                        f"{platform_code}.{provider_id}.user.cost",
                    ),
                ),
                enabled=_as_bool(endpoint_data.get("enabled", True)),
            )
        default_provider = str(item.get("default_provider") or "cheapest").strip().lower()
        if default_provider not in {"auto", "cheapest", "default"} and default_provider not in providers_for_platform:
            default_provider = "cheapest"
        platforms[platform_code] = PlatformRuntime(
            code=platform_code,
            label=str(item.get("label") or platform_code),
            enabled=_as_bool(item.get("enabled", True)),
            default_provider=default_provider,
            providers=providers_for_platform,
        )

    return Registry(
        providers=providers,
        platforms=platforms,
        usd_cny=usd_cny,
        token_cny=token_cny,
    )
