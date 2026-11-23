from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .config import Settings
from .db import Database
from .identity import canonical_profile_url, normalize_account_id
from .platforms import load_registry
from .providers import ProviderError, build_provider


def _json_values(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    return list(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))


def _lead_source_fields(
    db: Database, round_id: int, value: Any, label_value: Any = "[]"
) -> tuple[str, str]:
    values = _json_values(value)
    existing_labels = _json_values(label_value)
    if not values:
        labels = existing_labels or ["线索转换"]
        return "[]", json.dumps(labels, ensure_ascii=False)
    round_words = db.round_keyword_words(round_id)
    keywords = [item for item in values if item in round_words]
    labels = list(dict.fromkeys([
        *(item for item in values if item not in round_words),
        *existing_labels,
    ]))
    return (
        json.dumps(keywords, ensure_ascii=False),
        json.dumps(labels, ensure_ascii=False),
    )


def convert_lead(
    db: Database,
    settings: Settings,
    lead_id: int,
    *,
    account_id: str = "",
    profile_url: str = "",
    fetch: bool = True,
    provider_code: str = "auto",
    actor_id: int | None = None,
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise ValueError("线索不存在")
    status = str(lead.get("status") or "")
    if status == "converted":
        raise ValueError("该线索已转为达人")
    if status not in {"unverified", "verifying"}:
        raise ValueError("当前线索状态不可转换")
    token = uuid.uuid4().hex
    if not db.acquire_lead_conversion(lead_id, token):
        raise ValueError("该线索正在转换中，请稍后重试")
    try:
        platform = str(lead["platform"] or "xhs")
        source_url = (profile_url or lead.get("profile_url") or "").strip()
        real_id = normalize_account_id(
            platform,
            account_id or lead.get("account_id") or source_url,
        )
        if not real_id:
            raise ValueError("需要真实平台账号 ID 或主页 URL 才能转为达人")
        rounds = db.lead_rounds(lead_id)
        if not rounds and lead.get("source_round_id"):
            rounds = [{"round_id": lead["source_round_id"]}]
        if not rounds and lead.get("matched_round_id"):
            rounds = [{"round_id": lead["matched_round_id"]}]
        if not rounds:
            raise ValueError("线索没有关联轮次，无法转为达人")
        rounds = [
            source
            for source in rounds
            if (round_row := db.get_round(int(source["round_id"])))
            and not round_row.get("archived_at")
        ]
        if not rounds:
            raise ValueError("线索关联的轮次已不存在或已归档，请先恢复轮次或关联新的轮次")
        creator = db.get_creator(platform, real_id)
        if not fetch and not creator:
            raise ValueError("本地没有该达人资料，请开启抓取后重试")

        creator_id = real_id
        fetched: dict[str, Any] | None = None
        provider_id = ""
        cost_tokens = 0
        cost_native = 0.0
        cost_currency = "token"
        profile_for_lead = source_url or canonical_profile_url(platform, creator_id)

        if fetch:
            registry = load_registry(settings)
            if provider_code in {"auto", "cheapest", "default"}:
                option = registry.cheapest_user(platform)
                if option is None:
                    raise ValueError("没有可用的采集服务商，请先在设置中配置")
            else:
                option = registry.resolve(platform, provider_code)
            provider = build_provider(settings, option, platform)
            started = time.perf_counter()
            try:
                payload = provider.user(real_id, source_url)
                fetched = provider.normalize_user(payload, real_id)
            except ProviderError as exc:
                db.add_lead_attempt(
                    lead_id,
                    "error",
                    provider=provider_code,
                    query=real_id,
                    error=str(exc),
                    created_by=actor_id,
                )
                raise
            finally:
                provider.close()
            duration_ms = int((time.perf_counter() - started) * 1000)
            price = provider.user_price
            attempts = max(1, int(getattr(provider.client, "last_attempts", 0) or 1))
            cost_tokens = int(getattr(provider.client, "last_cost", 0) or 0) or provider.user_cost_tokens * attempts
            cost_native = float(price.amount) if price is not None else 0.0
            cost_currency = price.currency if price is not None else "token"
            provider_id = provider.provider_id
            db.log_api_call(
                int(rounds[0]["round_id"]),
                platform,
                "user",
                {"userId": real_id},
                None,
                200,
                cost_tokens,
                duration_ms,
                path=provider.user_path,
                provider=provider_id,
                cost_native=cost_native,
                cost_currency=cost_currency,
                estimated_cost=cost_tokens,
                attempt=attempts,
            )
            fetched_id = str(fetched.get("user_id") or "").strip()
            if fetched_id:
                creator_id = fetched_id
            if not fetched.get("user_id"):
                fetched["user_id"] = creator_id
            canonical = canonical_profile_url(platform, creator_id)
            if not fetched.get("profile_url"):
                fetched["profile_url"] = canonical
            source_account = normalize_account_id(platform, source_url) if source_url else ""
            profile_for_lead = source_url if source_account == creator_id else canonical

        try:
            with db.transaction():
                if fetched is not None:
                    db.upsert_creator(fetched, commit=False)
                    creator = db.get_creator(platform, creator_id)
                db.add_lead_attempt(
                    lead_id,
                    "matched",
                    provider=provider_id,
                    query=creator_id,
                    candidate_user_id=creator_id,
                    candidate_nickname=(fetched or creator or {}).get("nickname") or "",
                    candidate_profile_url=(fetched or creator or {}).get("profile_url") or "",
                    cost=cost_tokens,
                    cost_native=cost_native,
                    cost_currency=cost_currency,
                    created_by=actor_id,
                    commit=False,
                )
                owner_user_id = lead.get("owner_user_id") or actor_id
                for source in rounds:
                    round_id = int(source["round_id"])
                    source_keywords, source_labels = _lead_source_fields(
                        db, round_id, source.get("source_keywords")
                    )
                    db.add_creator_round(
                        round_id,
                        platform,
                        creator_id,
                        source_keywords=source_keywords,
                        source_labels=source_labels,
                        raw_title=source.get("raw_title") or lead.get("raw_title") or "",
                        account_id=creator_id,
                        data_round=source.get("data_round") or "",
                        top_note_id=source.get("top_note_id") or "",
                        sample_notes=int(source.get("sample_notes") or 0),
                        sample_likes=int(source.get("sample_likes") or 0),
                        sample_collected=int(source.get("sample_collected") or 0),
                        sample_comments=int(source.get("sample_comments") or 0),
                        sample_interactions=int(source.get("sample_interactions") or 0),
                        owner_user_id=int(owner_user_id) if owner_user_id is not None else None,
                        commit=False,
                    )
                update_fields: dict[str, Any] = {
                    "account_id": creator_id,
                    "profile_url": profile_for_lead,
                }
                if lead.get("owner_user_id") is None and actor_id is not None:
                    update_fields["owner_user_id"] = actor_id
                db.update_lead(lead_id, commit=False, **update_fields)
                db.mark_lead_converted(lead_id, platform, creator_id, commit=False)
        except Exception as exc:
            db.add_lead_attempt(
                lead_id,
                "error",
                provider=provider_id,
                query=creator_id,
                error=str(exc),
                created_by=actor_id,
            )
            raise
        if db._has_fts:
            for source in rounds:
                db.rebuild_search_index(int(source["round_id"]))
        return {
            "lead": db.get_lead(lead_id),
            "creator": db.get_creator(platform, creator_id),
            "rounds": [int(source["round_id"]) for source in rounds],
        }
    finally:
        db.release_lead_conversion(lead_id, token)
