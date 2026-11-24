from __future__ import annotations

import json
import uuid
from typing import Any

from .config import Settings
from .db import Database
from .identity import canonical_profile_url, normalize_account_id
from .lead_identity import (
    LeadIdentityMismatch,
    record_identity_attempt,
    resolve_lead_identity,
)


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


def _conversion_rounds(db: Database, lead: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = db.lead_rounds(int(lead["id"]))
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
    return rounds


def _local_creator(
    db: Database, platform: str, user_id: str, handle: str
) -> tuple[dict[str, Any] | None, str]:
    if user_id:
        creator = db.get_creator(platform, user_id)
        if creator:
            return creator, user_id
    if handle:
        row = db._conn.execute(
            """
            SELECT cr.user_id
            FROM creator_rounds cr
            LEFT JOIN creators c
                ON c.platform = cr.platform AND c.user_id = cr.user_id
            JOIN rounds r ON r.id = cr.round_id
            WHERE cr.platform = ? AND r.archived_at IS NULL
              AND (cr.account_id = ? OR c.red_id = ?)
            ORDER BY cr.round_id DESC, cr.updated_at DESC
            LIMIT 1
            """,
            (platform, handle, handle),
        ).fetchone()
        if row:
            creator = db.get_creator(platform, str(row["user_id"]))
            if creator:
                return creator, str(row["user_id"])
    return None, user_id


def convert_lead(
    db: Database,
    settings: Settings,
    lead_id: int,
    *,
    account_id: str = "",
    platform_handle: str = "",
    profile_url: str = "",
    fetch: bool = True,
    provider_code: str = "auto",
    confirm_mismatch: bool = False,
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
        explicit_account = normalize_account_id(platform, account_id)
        explicit_handle = normalize_account_id(platform, platform_handle)
        stored_account = normalize_account_id(platform, lead.get("account_id") or "")
        stored_handle = normalize_account_id(platform, lead.get("platform_handle") or "")
        source_url = (profile_url or lead.get("profile_url") or "").strip()
        requested_account = explicit_account or stored_account
        requested_handle = explicit_handle or stored_handle
        matched_id = str(lead.get("matched_user_id") or "").strip()
        matched_platform = str(lead.get("matched_platform") or platform)
        matched_creator = (
            db.get_creator(matched_platform, matched_id)
            if matched_id and matched_platform == platform
            else None
        )
        can_use_match = bool(
            matched_creator
            and (not explicit_account or explicit_account == stored_account)
            and (not explicit_handle or explicit_handle == stored_handle)
        )

        real_id = explicit_account or stored_account
        creator: dict[str, Any] | None = None
        if can_use_match and not explicit_account and not explicit_handle:
            real_id = matched_id
            creator = matched_creator
        else:
            creator, real_id = _local_creator(
                db, platform, real_id, requested_handle
            )

        fetched: dict[str, Any] | None = None
        provider_id = ""
        cost_tokens = 0
        cost_native = 0.0
        cost_currency = "token"
        identity_confidence = str(lead.get("match_confidence") or "local")
        identity_note = ""
        confirmed_mismatch = False
        identity_result: dict[str, Any] | None = None

        if fetch:
            if not (requested_account or requested_handle or source_url):
                raise ValueError(
                    "需要真实平台账号 ID、平台账号或主页 URL 才能转为达人"
                )
            identity_result = resolve_lead_identity(
                db,
                settings,
                lead,
                provider_code=provider_code,
                account_id=requested_account or None,
                platform_handle=requested_handle or None,
                profile_url=source_url or None,
            )
            provider_id = str(identity_result.get("provider") or "")
            cost_tokens = int(identity_result.get("cost") or 0)
            cost_native = float(identity_result.get("cost_native") or 0)
            cost_currency = str(identity_result.get("cost_currency") or "token")
            identity_status = str(identity_result.get("status") or "")
            if identity_status == "verified":
                fetched = identity_result.get("fetched")
                real_id = (
                    str(identity_result.get("resolved_user_id") or "").strip()
                    or real_id
                )
                requested_handle = (
                    str(identity_result.get("resolved_handle") or "").strip()
                    or requested_handle
                )
                identity_confidence = str(identity_result.get("confidence") or "")
                identity_note = str(identity_result.get("note") or "")
            elif identity_status == "mismatch":
                record_identity_attempt(
                    db,
                    lead,
                    identity_result,
                    applied=False,
                    actor_id=actor_id,
                    attempt_status="suggested",
                )
                db.add_lead_identity_event(
                    lead_id,
                    "mismatch_blocked",
                    "mismatch",
                    old_user_id=stored_account,
                    new_user_id=str(identity_result.get("resolved_user_id") or ""),
                    old_handle=stored_handle,
                    new_handle=str(identity_result.get("resolved_handle") or ""),
                    provider=provider_id,
                    confidence=str(identity_result.get("confidence") or ""),
                    note=str(identity_result.get("note") or ""),
                    actor_id=actor_id,
                )
                if not confirm_mismatch:
                    raise LeadIdentityMismatch(identity_result)
                fetched = identity_result.get("fetched")
                real_id = (
                    str(identity_result.get("resolved_user_id") or "").strip()
                    or real_id
                )
                requested_handle = (
                    str(identity_result.get("resolved_handle") or "").strip()
                    or requested_handle
                )
                identity_confidence = "manual_mismatch"
                identity_note = "用户确认采用抓取到的账号"
                confirmed_mismatch = True
            else:
                record_identity_attempt(
                    db, lead, identity_result, applied=False, actor_id=actor_id
                )
                raise ValueError(
                    str(identity_result.get("note") or "账号校验失败，请检查账号或主页")
                )
            if not real_id:
                raise ValueError("未能解析出真实平台账号 ID")
            if fetched is not None:
                fetched_id = str(fetched.get("user_id") or "").strip()
                if fetched_id:
                    real_id = fetched_id
                if not fetched.get("user_id"):
                    fetched["user_id"] = real_id
            creator = db.get_creator(platform, real_id)
        else:
            if not creator:
                raise ValueError(
                    "本地没有该达人资料，请开启抓取后重试"
                    if (requested_account or requested_handle)
                    else "需要真实平台账号 ID、平台账号或主页 URL 才能转为达人"
                )
            real_id = str(creator.get("user_id") or real_id)
            if not requested_handle:
                requested_handle = normalize_account_id(
                    platform, str(creator.get("red_id") or "")
                )
            identity_confidence = identity_confidence or "local"
            identity_note = "使用本地达人资料"

        existing = db.get_creator(platform, real_id)
        if existing and not creator:
            creator = existing
        fetched_profile = str((fetched or {}).get("profile_url") or "")
        profile_for_lead = (
            fetched_profile or source_url or canonical_profile_url(platform, real_id)
        )
        rounds = _conversion_rounds(db, lead)

        try:
            with db.transaction():
                if fetched is not None:
                    fetched_profile = str(fetched.get("profile_url") or "")
                    if not fetched_profile:
                        fetched["profile_url"] = canonical_profile_url(
                            platform, real_id
                        )
                    db.upsert_creator(fetched, commit=False)
                    creator = db.get_creator(platform, real_id)
                db.add_lead_attempt(
                    lead_id,
                    "matched",
                    provider=provider_id or "local",
                    query=real_id,
                    candidate_user_id=real_id,
                    candidate_nickname=(fetched or creator or {}).get("nickname") or "",
                    candidate_profile_url=(fetched or creator or {}).get("profile_url") or "",
                    confidence=identity_confidence,
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
                        real_id,
                        source_keywords=source_keywords,
                        source_labels=source_labels,
                        raw_title=source.get("raw_title") or lead.get("raw_title") or "",
                        account_id=real_id,
                        data_round=source.get("data_round") or "",
                        top_note_id=source.get("top_note_id") or "",
                        sample_notes=int(source.get("sample_notes") or 0),
                        sample_likes=int(source.get("sample_likes") or 0),
                        sample_collected=int(source.get("sample_collected") or 0),
                        sample_comments=int(source.get("sample_comments") or 0),
                        sample_interactions=int(source.get("sample_interactions") or 0),
                        owner_user_id=(
                            int(owner_user_id) if owner_user_id is not None else None
                        ),
                        commit=False,
                    )
                if confirmed_mismatch:
                    db.add_lead_identity_event(
                        lead_id,
                        "mismatch_confirmed",
                        "verified",
                        old_user_id=stored_account,
                        new_user_id=real_id,
                        old_handle=stored_handle,
                        new_handle=requested_handle,
                        provider=provider_id,
                        confidence="manual_mismatch",
                        note="用户确认使用核实账号",
                        actor_id=actor_id,
                        commit=False,
                    )
                db.apply_lead_verification(
                    lead_id,
                    user_id=real_id,
                    handle=requested_handle,
                    identity_status="verified",
                    confidence=identity_confidence,
                    provider=provider_id or "local",
                    note=identity_note,
                    profile_url=profile_for_lead,
                    event_type="converted",
                    actor_id=actor_id,
                    commit=False,
                )
                if lead.get("owner_user_id") is None and actor_id is not None:
                    db.update_lead(
                        lead_id, owner_user_id=actor_id, commit=False
                    )
                db.mark_lead_converted(
                    lead_id,
                    platform,
                    real_id,
                    conversion_token=token,
                    commit=False,
                )
        except LeadIdentityMismatch:
            raise
        except Exception as exc:
            db.add_lead_attempt(
                lead_id,
                "error",
                provider=provider_id,
                query=real_id,
                error=str(exc),
                cost=cost_tokens,
                cost_native=cost_native,
                cost_currency=cost_currency,
                created_by=actor_id,
            )
            raise
        if db._has_fts:
            for source in rounds:
                db.rebuild_search_index(int(source["round_id"]))
        return {
            "lead": db.get_lead(lead_id),
            "creator": db.get_creator(platform, real_id),
            "rounds": [int(source["round_id"]) for source in rounds],
        }
    finally:
        db.release_lead_conversion(lead_id, token)
