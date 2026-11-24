from __future__ import annotations

import time
from typing import Any, Callable

from .client import ProviderError
from .db import Database
from .providers import Provider, SearchResponse


def provider_call_cost(
    provider: Provider, kind: str, *, fallback: bool = True
) -> dict[str, Any]:
    client = provider.client
    attempts = max(1, int(getattr(client, "last_attempts", 0) or 1))
    unit_tokens = provider.search_cost_tokens if kind.startswith("search") else provider.user_cost_tokens
    reported = int(getattr(client, "last_cost", 0) or 0)
    tokens = reported
    if tokens <= 0 and fallback:
        tokens = unit_tokens * attempts
    price = provider.search_price if kind.startswith("search") else provider.user_price
    native = (
        float(price.amount) * attempts
        if price is not None and (reported > 0 or fallback)
        else 0.0
    )
    return {
        "tokens": tokens,
        "attempts": attempts,
        "native": native,
        "currency": price.unit if price is not None else "token",
    }


def call_provider(
    db: Database,
    provider: Provider,
    kind: str,
    fn: Callable[[], Any],
    *,
    params: dict[str, Any],
    round_id: int | None,
    label: str,
    job_id: int | None = None,
) -> Any:
    started = time.perf_counter()
    path = provider.search_path if kind.startswith("search") else provider.user_path
    try:
        result = fn()
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        cost = provider_call_cost(provider, kind)
        payload = exc.payload if isinstance(exc, ProviderError) else None
        raw_file = db.save_raw(f"{label}_error", payload) if payload is not None else None
        db.log_api_call(
            round_id,
            provider.platform,
            kind,
            params,
            raw_file,
            getattr(exc, "code", None) or getattr(exc, "status", None),
            0,
            duration_ms,
            path=path,
            job_id=job_id,
            provider=provider.provider_id,
            cost_native=cost["native"],
            cost_currency=cost["currency"],
            estimated_cost=cost["tokens"],
            attempt=cost["attempts"],
            status="error",
            error=str(exc)[:1000],
        )
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    cost = provider_call_cost(provider, kind)
    payload = result.payload if isinstance(result, SearchResponse) else result
    raw_file = db.save_raw(label, payload)
    code = payload.get("code") if isinstance(payload, dict) else None
    db.log_api_call(
        round_id,
        provider.platform,
        kind,
        params,
        raw_file,
        code if code is not None else 200,
        cost["tokens"],
        duration_ms,
        path=path,
        job_id=job_id,
        provider=provider.provider_id,
        cost_native=cost["native"],
        cost_currency=cost["currency"],
        estimated_cost=cost["tokens"],
        attempt=cost["attempts"],
    )
    return result
