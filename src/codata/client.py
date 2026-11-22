from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import Settings
from .platforms import validate_provider_host

RETRYABLE_CODES = {100500, 100501, 100504}
BUSY_CODE = 100501
BUSY_WAIT_SECONDS = 60.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FATAL_STATUS = {401, 402, 403}


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: int | None,
        msg: str,
        payload: Any = None,
        *,
        provider: str = "",
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.msg = msg
        self.payload = payload
        self.provider = provider
        self.status = status
        self.retryable = retryable
        prefix = f"[{provider}/{code}]" if provider else f"[{code}]"
        super().__init__(f"{prefix} {msg}")


class DataFlowError(ProviderError):
    def __init__(self, code: int | None, msg: str, payload: Any = None) -> None:
        super().__init__(code, msg, payload, provider="dataflow")


class RnoteError(ProviderError):
    def __init__(self, code: int | None, msg: str, payload: Any = None, status: int | None = None) -> None:
        super().__init__(code, msg, payload, provider="rnote", status=status)



def _same_host_request(
    client: httpx.Client,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
    allowed_hosts: tuple[str, ...],
    provider_id: str,
    max_redirects: int = 3,
) -> httpx.Response:
    current_path = path
    current_params = params
    allowed = tuple(host.lower() for host in allowed_hosts if host)
    for _ in range(max_redirects + 1):
        response = client.request(
            method.upper(),
            current_path,
            params=_clean_params(current_params),
            json=json_body,
        )
        if not 300 <= response.status_code < 400:
            return response
        location = response.headers.get("location", "")
        if not location:
            raise ProviderError(
                response.status_code,
                "服务商返回重定向但缺少 Location",
                provider=provider_id,
                status=response.status_code,
            )
        target = httpx.URL(str(response.url)).join(location)
        if target.scheme != "https" and target.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ProviderError(
                response.status_code,
                f"服务商重定向到非 HTTPS 地址，已拒绝: {target}",
                provider=provider_id,
                status=response.status_code,
            )
        if allowed and (target.host or "").lower() not in allowed:
            raise ProviderError(
                response.status_code,
                f"服务商重定向到非允许域名，已拒绝: {target}",
                provider=provider_id,
                status=response.status_code,
            )
        if not target.query and response.request.url.query:
            target = target.copy_with(query=response.request.url.query)
        current_path = str(target)
        current_params = None
    raise ProviderError(
        301,
        "服务商重定向次数过多，已拒绝",
        provider=provider_id,
        status=301,
    )

def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    cleaned = {k: v for k, v in params.items() if v is not None and v != ""}
    return cleaned or None


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError(
            response.status_code,
            f"响应不是合法 JSON: {response.text[:200]}",
            status=response.status_code,
        ) from exc


def _retry_delay(attempt: int, base: float, response: httpx.Response | None = None) -> float:
    retry_after = (response.headers.get("Retry-After") if response is not None else None) or ""
    try:
        if retry_after:
            return max(0.0, min(float(retry_after), 120.0))
    except ValueError:
        pass
    return min(max(base, 0.1) * (2 ** max(0, attempt - 1)), 30.0)


class DataFlowClient:
    def __init__(
        self,
        settings: Settings,
        provider: Any | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        base_url = getattr(provider, "base_url", None) or settings.host
        api_key = getattr(provider, "api_key", None) or settings.token
        allowed = getattr(provider, "allowed_hosts", None) or settings.dataflow_allowed_hosts
        if not base_url:
            raise ProviderError(None, "未配置 DataFlow 地址", provider="dataflow")
        validate_provider_host(base_url, tuple(allowed))
        header = getattr(provider, "auth_header", "Authorization")
        prefix = getattr(provider, "auth_prefix", "Token ")
        self._client = httpx.Client(
            base_url=base_url,
            headers={header: f"{prefix}{api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(getattr(provider, "timeout", settings.timeout)),
            follow_redirects=False,
        )
        self.cost = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DataFlowClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, path: str, params: dict[str, Any] | None = None, cost: int = 0) -> Any:
        delay = getattr(self.provider, "delay", self.settings.delay)
        retries = max(1, int(getattr(self.provider, "retries", self.settings.retries)))
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            if delay:
                time.sleep(delay)
            response: httpx.Response | None = None
            try:
                response = _same_host_request(
                    self._client,
                    "GET",
                    path,
                    params,
                    None,
                    getattr(self.provider, "allowed_hosts", None) or self.settings.dataflow_allowed_hosts,
                    "dataflow",
                )
                if response.status_code in FATAL_STATUS:
                    raise ProviderError(
                        response.status_code,
                        f"服务商拒绝请求: HTTP {response.status_code}",
                        provider="dataflow",
                        status=response.status_code,
                    )
                if response.status_code in RETRYABLE_STATUS:
                    raise ProviderError(
                        response.status_code,
                        f"服务商暂时不可用: HTTP {response.status_code}",
                        provider="dataflow",
                        status=response.status_code,
                        retryable=True,
                    )
                if 400 <= response.status_code < 500:
                    raise ProviderError(
                        response.status_code,
                        f"服务商拒绝请求: HTTP {response.status_code}",
                        provider="dataflow",
                        status=response.status_code,
                    )
                response.raise_for_status()
                payload = _parse_json(response)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < retries:
                    delay = _retry_delay(attempt, delay, response)
                    continue
                raise DataFlowError(None, f"请求失败: {exc}") from exc
            except ProviderError as exc:
                last_error = exc
                if exc.retryable and attempt < retries:
                    delay = _retry_delay(attempt, delay, response)
                    continue
                raise

            code = payload.get("code") if isinstance(payload, dict) else None
            if code in (0, None):
                self.cost += cost
                return payload

            message = payload.get("msg") or payload.get("message") or "未知错误"
            if code in RETRYABLE_CODES and attempt < retries:
                last_error = DataFlowError(code, message, payload)
                delay = BUSY_WAIT_SECONDS if code == BUSY_CODE else min(delay * 2, 30)
                continue
            raise DataFlowError(code, message, payload)

        raise DataFlowError(None, f"重试后仍失败: {last_error}")


class RnoteClient:
    def __init__(
        self,
        settings: Settings,
        provider: Any | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        base_url = getattr(provider, "base_url", None) or settings.rnote_host
        api_key = getattr(provider, "api_key", None) or settings.rnote_api_key
        allowed = getattr(provider, "allowed_hosts", None) or settings.rnote_allowed_hosts
        if not base_url:
            raise ProviderError(None, "未配置 Rnote 地址", provider="rnote")
        validate_provider_host(base_url, tuple(allowed))
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(getattr(provider, "timeout", settings.timeout)),
            follow_redirects=False,
        )
        self.cost = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RnoteClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, path: str, params: dict[str, Any] | None = None, cost: int = 0) -> Any:
        delay = getattr(self.provider, "delay", self.settings.delay)
        retries = max(1, int(getattr(self.provider, "retries", self.settings.retries)))
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            if delay:
                time.sleep(delay)
            response: httpx.Response | None = None
            try:
                response = _same_host_request(
                    self._client,
                    "GET",
                    path,
                    params,
                    None,
                    getattr(self.provider, "allowed_hosts", None) or self.settings.rnote_allowed_hosts,
                    "rnote",
                )
                if response.status_code in FATAL_STATUS:
                    raise RnoteError(
                        response.status_code,
                        f"服务商拒绝请求: HTTP {response.status_code}",
                        status=response.status_code,
                    )
                if response.status_code in RETRYABLE_STATUS:
                    raise RnoteError(
                        response.status_code,
                        f"服务商暂时不可用: HTTP {response.status_code}",
                        status=response.status_code,
                    )
                if 400 <= response.status_code < 500:
                    raise RnoteError(
                        response.status_code,
                        f"服务商拒绝请求: HTTP {response.status_code}",
                        status=response.status_code,
                    )
                response.raise_for_status()
                payload = _parse_json(response)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < retries:
                    delay = _retry_delay(attempt, delay, response)
                    continue
                raise RnoteError(None, f"请求失败: {exc}") from exc
            except ProviderError as exc:
                last_error = exc
                if exc.status in RETRYABLE_STATUS and attempt < retries:
                    delay = _retry_delay(attempt, delay, response)
                    continue
                raise

            if isinstance(payload, dict) and payload.get("success") is False:
                message = str(payload.get("error") or payload.get("detail") or "未知错误")
                retryable = response.status_code in RETRYABLE_STATUS or bool(payload.get("retry_after"))
                error = RnoteError(response.status_code, message, payload, status=response.status_code)
                if retryable and attempt < retries:
                    last_error = error
                    retry_after = payload.get("retry_after")
                    if retry_after:
                        try:
                            delay = max(0.0, min(float(retry_after), 120.0))
                        except (TypeError, ValueError):
                            delay = min(delay * 2, 30)
                    else:
                        delay = _retry_delay(attempt, delay, response)
                    continue
                raise error

            self.cost += cost
            return payload

        raise RnoteError(None, f"重试后仍失败: {last_error}")

class JsonProviderClient:
    def __init__(self, settings: Settings, provider: Any) -> None:
        self.settings = settings
        self.provider = provider
        base_url = provider.base_url
        if not base_url:
            raise ProviderError(None, f"未配置 {provider.label} 地址", provider=provider.id)
        validate_provider_host(base_url, tuple(provider.allowed_hosts))
        headers = {"Accept": "application/json"}
        if provider.auth_header and provider.api_key:
            headers[provider.auth_header] = f"{provider.auth_prefix}{provider.api_key}"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(provider.timeout),
            follow_redirects=False,
        )
        self.cost = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JsonProviderClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _success(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return True
        if payload.get("success") is False:
            return False
        if payload.get("error") and "success" not in payload:
            return False
        code = payload.get("code")
        if code is not None:
            if self.provider.success_codes:
                try:
                    return int(code) in self.provider.success_codes
                except (TypeError, ValueError):
                    return False
            try:
                return int(code) in {0, 200, 2000}
            except (TypeError, ValueError):
                return False
        return True

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        cost: int = 0,
    ) -> Any:
        delay = self.provider.delay
        retries = max(1, int(self.provider.retries))
        request_params = dict(params or {})
        if self.provider.auth_query and self.provider.api_key:
            request_params[self.provider.auth_query] = self.provider.api_key
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            if delay:
                time.sleep(delay)
            response: httpx.Response | None = None
            try:
                response = _same_host_request(
                    self._client,
                    method,
                    path,
                    request_params,
                    json_body,
                    self.provider.allowed_hosts,
                    self.provider.id,
                )
                if response.status_code in FATAL_STATUS:
                    raise ProviderError(
                        response.status_code,
                        f"服务商拒绝请求: HTTP {response.status_code}",
                        provider=self.provider.id,
                        status=response.status_code,
                    )
                if response.status_code in RETRYABLE_STATUS:
                    raise ProviderError(
                        response.status_code,
                        f"服务商暂时不可用: HTTP {response.status_code}",
                        provider=self.provider.id,
                        status=response.status_code,
                        retryable=True,
                    )
                if 400 <= response.status_code < 500:
                    raise ProviderError(
                        response.status_code,
                        f"服务商拒绝请求: HTTP {response.status_code}",
                        provider=self.provider.id,
                        status=response.status_code,
                    )
                response.raise_for_status()
                payload = _parse_json(response)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < retries:
                    delay = _retry_delay(attempt, delay, response)
                    continue
                raise ProviderError(None, f"请求失败: {exc}", provider=self.provider.id) from exc
            except ProviderError as exc:
                last_error = exc
                if exc.retryable and attempt < retries:
                    delay = _retry_delay(attempt, delay, response)
                    continue
                raise
            if not self._success(payload):
                code = payload.get("code") if isinstance(payload, dict) else response.status_code
                message = ""
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        message = str(error.get("message") or error.get("reason") or "")
                    message = message or str(payload.get("message") or payload.get("msg") or error or "")
                raise ProviderError(
                    int(code) if str(code).lstrip("-").isdigit() else response.status_code,
                    message or "服务商返回失败",
                    payload,
                    provider=self.provider.id,
                    status=response.status_code,
                )
            self.cost += cost
            return payload
        raise ProviderError(None, f"重试后仍失败: {last_error}", provider=self.provider.id)

