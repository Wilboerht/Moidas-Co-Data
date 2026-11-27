from __future__ import annotations

from typing import Any

from .client import ProviderError
from .config import Settings
from .db import Database
from .identity import (
    canonical_profile_url,
    is_probable_user_id,
    normalize_account_id,
    normalize_identity,
)
from .matching import match_lead_against_creators
from .normalize import utc_now
from .platforms import load_registry
from .provider_audit import call_provider, provider_call_cost
from .providers import build_provider


class LeadIdentityMismatch(Exception):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__("抓取到的账号与线索不一致，请确认后再转换")

    def to_dict(self) -> dict[str, Any]:
        result = self.result
        return {
            "code": "lead_identity_mismatch",
            "message": str(self),
            "lead_id": result.get("lead_id"),
            "platform": result.get("platform"),
            "requested_user_id": result.get("requested_user_id") or "",
            "requested_handle": result.get("requested_handle") or "",
            "requested_profile_url": result.get("requested_profile_url") or "",
            "resolved_user_id": result.get("resolved_user_id") or "",
            "resolved_handle": result.get("resolved_handle") or "",
            "resolved_nickname": result.get("resolved_nickname") or "",
            "resolved_profile_url": result.get("resolved_profile_url") or "",
            "confidence": result.get("confidence") or "",
            "status": result.get("status") or "",
            "note": result.get("note") or "",
            "provider": result.get("provider") or "",
        }


def _nickname_key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _lead_round_id(db: Database, lead: dict[str, Any]) -> int | None:
    if lead.get("source_round_id") is not None:
        return int(lead["source_round_id"])
    rounds = db.lead_rounds(int(lead["id"]))
    if rounds:
        return int(rounds[0]["round_id"])
    if lead.get("matched_round_id") is not None:
        return int(lead["matched_round_id"])
    return None


def _is_not_found_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    if status in {400, 404}:
        return True
    text = str(exc).lower()
    return any(token in text for token in ("not found", "不存在", "未找到"))


_DIGIT_ID_PLATFORMS = {"weibo", "weibo_live", "bilibili", "douyin", "taobao", "kuaishou"}
_HANDLE_IDENTIFIER_PLATFORMS = {"instagram", "zhihu"}


def _candidate_kind(platform: str, candidate: str) -> str:
    if not candidate:
        return "none"
    if platform == "xhs":
        return "user_id" if is_probable_user_id(platform, candidate) else "handle"
    if platform in _DIGIT_ID_PLATFORMS:
        return "user_id" if is_probable_user_id(platform, candidate) else "handle"
    if platform in _HANDLE_IDENTIFIER_PLATFORMS:
        return "handle_identifier"
    return "user_id"


def _search_user_by_handle(
    db: Database,
    provider,
    provider_id: str,
    platform: str,
    handle: str,
    nickname: str,
    lead_id: int,
    round_id: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    keyword = (nickname or handle).strip()
    empty_cost = {
        "tokens": 0,
        "attempts": 0,
        "native": 0.0,
        "estimated_tokens": 0,
        "currency": "token",
    }
    if not keyword:
        return None, empty_cost, "缺少可用于搜索的昵称或账号"
    try:
        response = call_provider(
            db,
            provider,
            "search",
            lambda: provider.search(keyword, 1, {}),
            params={"keyword": keyword, "page": 1},
            round_id=round_id,
            label=f"lead{lead_id}_resolve_{provider_id}_{keyword}",
        )
    except Exception as exc:
        estimated = provider_call_cost(provider, "search")
        cost = {
            "tokens": 0,
            "attempts": estimated["attempts"],
            "native": 0.0,
            "estimated_tokens": estimated["tokens"],
            "currency": estimated["currency"],
        }
        return None, cost, str(exc)[:500]
    cost = provider_call_cost(provider, "search")
    cost["estimated_tokens"] = cost["tokens"]
    for item in getattr(response, "items", []) or []:
        try:
            _, author = provider.normalize_search(item)
        except Exception:
            continue
        if not isinstance(author, dict):
            continue
        red_id = normalize_account_id(platform, str(author.get("red_id") or ""))
        user_id = normalize_account_id(platform, str(author.get("user_id") or ""))
        if handle and red_id and red_id.casefold() == handle.casefold() and user_id:
            return {
                "user_id": user_id,
                "red_id": red_id,
                "nickname": str(author.get("nickname") or ""),
                "profile_url": str(author.get("profile_url") or ""),
            }, cost, ""
        if handle and user_id and user_id.casefold() == handle.casefold():
            return {
                "user_id": user_id,
                "red_id": red_id,
                "nickname": str(author.get("nickname") or ""),
                "profile_url": str(author.get("profile_url") or ""),
            }, cost, ""
    return None, cost, ""


def _provider_for(db: Database, settings: Settings, platform: str, provider_code: str):
    registry = load_registry(settings)
    if provider_code in {"auto", "cheapest", "default"}:
        option = registry.cheapest_user(platform)
        if option is None:
            raise ValueError("没有可用的采集服务商，请先在设置中配置")
    else:
        option = registry.resolve(platform, provider_code)
    return build_provider(settings, option, platform)


def resolve_lead_identity(
    db: Database,
    settings: Settings,
    lead: dict[str, Any],
    *,
    provider_code: str = "auto",
    account_id: str | None = None,
    platform_handle: str | None = None,
    profile_url: str | None = None,
) -> dict[str, Any]:
    lead_id = int(lead["id"])
    platform = str(lead.get("platform") or "xhs").strip().lower()
    requested_user_id = normalize_account_id(
        platform,
        lead.get("account_id") if account_id is None else account_id,
    )
    requested_handle = normalize_account_id(
        platform,
        lead.get("platform_handle") if platform_handle is None else platform_handle,
    )
    requested_profile = str(
        lead.get("profile_url") if profile_url is None else profile_url
    ).strip()
    profile_id = (
        normalize_account_id(platform, requested_profile) if requested_profile else ""
    )
    profile_candidate = (
        profile_id
        if profile_id and (platform != "xhs" or is_probable_user_id(platform, profile_id))
        else ""
    )
    if profile_candidate:
        candidate = profile_candidate
    else:
        candidate = requested_user_id or requested_handle
        if not candidate and requested_profile:
            candidate = normalize_account_id(platform, requested_profile)
    result: dict[str, Any] = {
        "lead_id": lead_id,
        "platform": platform,
        "requested_user_id": requested_user_id,
        "requested_handle": requested_handle,
        "requested_profile_url": requested_profile,
        "resolved_user_id": "",
        "resolved_handle": "",
        "resolved_nickname": "",
        "resolved_profile_url": "",
        "avatar": "",
        "fans": None,
        "confidence": "",
        "status": "not_found",
        "note": "",
        "error": "",
        "provider": "",
        "cost": 0,
        "cost_native": 0.0,
        "cost_currency": "token",
        "estimated_cost": 0,
        "attempts": 0,
        "search_cost": 0,
        "resolution": "",
        "fetched": None,
        "checked_at": utc_now(),
    }
    if not candidate:
        result["note"] = "缺少账号 ID、平台账号或主页 URL"
        return result

    provider = _provider_for(db, settings, platform, provider_code)
    provider_id = provider.provider_id
    result["provider"] = provider_id
    round_id = _lead_round_id(db, lead)
    total_cost = 0
    total_native = 0.0
    total_estimated = 0
    total_attempts = 0
    total_currency = "token"
    search_matched_handle = ""
    search_nickname = ""
    try:
        kind = (
            "user_id"
            if (profile_candidate and candidate == profile_candidate)
            or (requested_user_id and candidate == requested_user_id)
            else _candidate_kind(platform, candidate)
        )
        if kind == "handle":
            if platform != "xhs":
                result.update(
                    status="not_found",
                    note="该平台需要平台用户 ID 或主页 URL，无法直接用平台账号查询",
                )
                return result
            resolved, search_cost, search_error = _search_user_by_handle(
                db,
                provider,
                provider_id,
                platform,
                requested_handle or candidate,
                str(lead.get("nickname") or ""),
                lead_id,
                round_id,
            )
            total_cost += int(search_cost.get("tokens") or 0)
            total_native += float(search_cost.get("native") or 0)
            total_estimated += int(search_cost.get("estimated_tokens") or 0)
            total_attempts += int(search_cost.get("attempts") or 0)
            total_currency = str(search_cost.get("currency") or total_currency)
            result["search_cost"] = int(search_cost.get("tokens") or 0)
            result["resolution"] = "search"
            if not resolved:
                result.update(
                    status="error" if search_error else "not_found",
                    note=search_error
                    or "小红书号未能在搜索结果中解析为用户 ID；请填写主页 URL 或平台用户 ID",
                    error=search_error,
                    cost=total_cost,
                    cost_native=total_native,
                    cost_currency=total_currency,
                    estimated_cost=total_estimated,
                    attempts=total_attempts,
                )
                return result
            candidate = str(resolved.get("user_id") or "")
            search_matched_handle = str(resolved.get("red_id") or "")
            search_nickname = str(resolved.get("nickname") or "")
            if not candidate:
                result.update(
                    status="not_found",
                    note="搜索结果缺少平台用户 ID",
                    cost=total_cost,
                    cost_native=total_native,
                    cost_currency=total_currency,
                    estimated_cost=total_estimated,
                    attempts=total_attempts,
                )
                return result
        resolution = result["resolution"] or "direct"
        try:
            payload = call_provider(
                db,
                provider,
                "user",
                lambda: provider.user(candidate, requested_profile),
                params={"userId": candidate},
                round_id=round_id,
                label=f"lead{lead_id}_verify_{provider_id}_{candidate}",
            )
        except Exception as exc:
            estimated = provider_call_cost(provider, "user")
            total_estimated += estimated["tokens"]
            total_attempts += estimated["attempts"]
            result.update(
                cost=total_cost,
                cost_native=total_native,
                cost_currency=total_currency if total_cost or total_native else estimated["currency"],
                estimated_cost=total_estimated,
                attempts=total_attempts,
                error=str(exc)[:1000],
                note=str(exc)[:500],
                status="not_found" if _is_not_found_error(exc) else "error",
                resolution=resolution,
            )
            return result
        try:
            fetched = provider.normalize_user(payload, candidate)
        except Exception as exc:
            cost = provider_call_cost(provider, "user")
            total_cost += cost["tokens"]
            total_native += cost["native"]
            total_estimated += cost["tokens"]
            total_attempts += cost["attempts"]
            result.update(
                cost=total_cost,
                cost_native=total_native,
                cost_currency=cost["currency"],
                estimated_cost=total_estimated,
                attempts=total_attempts,
                error=str(exc)[:1000],
                note=str(exc)[:500],
                status="error",
                resolution=resolution,
            )
            return result
        cost = provider_call_cost(provider, "user")
        total_cost += cost["tokens"]
        total_native += cost["native"]
        total_estimated += cost["tokens"]
        total_attempts += cost["attempts"]
        fetched_id = normalize_account_id(
            platform, str(fetched.get("user_id") or "")
        ) or candidate
        fetched_handle = normalize_account_id(
            platform,
            str(fetched.get("red_id") or fetched.get("account_id") or ""),
        )
        if not fetched_handle and search_matched_handle:
            fetched_handle = search_matched_handle
        if not fetched.get("user_id"):
            fetched["user_id"] = fetched_id
        fetched_profile = str(fetched.get("profile_url") or "")
        canonical = canonical_profile_url(platform, fetched_id) if fetched_id else ""
        if not fetched_profile:
            fetched_profile = canonical
        confidence = "none"
        if (
            search_matched_handle
            and requested_handle
            and search_matched_handle.casefold() == requested_handle.casefold()
        ):
            confidence = "platform_handle"
        elif requested_user_id and fetched_id and requested_user_id == fetched_id:
            confidence = "account_id"
        elif requested_handle and fetched_handle and requested_handle == fetched_handle:
            confidence = "platform_handle"
        elif requested_handle and fetched_id and requested_handle == fetched_id:
            confidence = "platform_handle"
        elif (
            requested_profile
            and fetched_profile
            and normalize_identity(platform, "", requested_profile)[1]
            == normalize_identity(platform, fetched_id, fetched_profile)[1]
        ):
            confidence = "profile_url"
        elif _nickname_key(lead.get("nickname")) and _nickname_key(
            fetched.get("nickname")
        ) == _nickname_key(lead.get("nickname")):
            confidence = "nickname"

        if confidence in {"account_id", "platform_handle", "profile_url"}:
            status = "verified"
            note = "身份一致"
        elif confidence == "nickname":
            status = "mismatch"
            note = "仅昵称一致，需要人工确认"
        else:
            status = "mismatch"
            note = "抓取结果与线索账号不一致"
        resolved_nickname = str(fetched.get("nickname") or "") or search_nickname
        result.update(
            resolved_user_id=fetched_id,
            resolved_handle=fetched_handle,
            resolved_nickname=resolved_nickname,
            resolved_profile_url=fetched_profile,
            avatar=str(fetched.get("avatar") or ""),
            fans=fetched.get("fans"),
            confidence=confidence,
            status=status,
            note=note,
            cost=total_cost,
            cost_native=total_native,
            cost_currency=cost["currency"],
            estimated_cost=total_estimated,
            attempts=total_attempts,
            resolution=resolution,
            fetched=fetched,
        )
        return result
    finally:
        provider.close()


def record_identity_attempt(
    db: Database,
    lead: dict[str, Any],
    result: dict[str, Any],
    *,
    applied: bool,
    actor_id: int | None,
    attempt_status: str | None = None,
) -> None:
    status = result.get("status") or ""
    resolved_status = attempt_status or ("matched" if applied else "suggested")
    if attempt_status is None and status == "error":
        resolved_status = "error"
    elif attempt_status is None and status == "not_found":
        resolved_status = "no_match"
    db.add_lead_attempt(
        int(lead["id"]),
        resolved_status,
        provider=result.get("provider") or "",
        query=result.get("requested_user_id")
        or result.get("requested_handle")
        or result.get("requested_profile_url")
        or "",
        candidate_user_id=result.get("resolved_user_id") or "",
        candidate_nickname=result.get("resolved_nickname") or "",
        candidate_profile_url=result.get("resolved_profile_url") or "",
        confidence=result.get("confidence") or "",
        error=result.get("error") or "",
        cost=int(result.get("cost") or 0),
        cost_native=float(result.get("cost_native") or 0),
        cost_currency=result.get("cost_currency") or "token",
        round_id=_lead_round_id(db, lead),
        created_by=actor_id,
    )


def verify_lead_identity(
    db: Database,
    settings: Settings,
    lead_id: int,
    *,
    provider_code: str = "auto",
    account_id: str | None = None,
    platform_handle: str | None = None,
    profile_url: str | None = None,
    apply: bool = False,
    confirm_mismatch: bool = False,
    actor_id: int | None = None,
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise ValueError("线索不存在")
    if lead.get("status") == "converted":
        raise ValueError("已转达人的线索不可修改")
    result = resolve_lead_identity(
        db,
        settings,
        lead,
        provider_code=provider_code,
        account_id=account_id,
        platform_handle=platform_handle,
        profile_url=profile_url,
    )
    applied = False
    if apply and result["status"] == "verified":
        db.apply_lead_verification(
            lead_id,
            user_id=result.get("resolved_user_id") or "",
            handle=result.get("resolved_handle")
            or lead.get("platform_handle")
            or "",
            identity_status="verified",
            confidence=result.get("confidence") or "",
            provider=result.get("provider") or "",
            note=result.get("note") or "",
            profile_url=result.get("resolved_profile_url") or "",
            actor_id=actor_id,
        )
        applied = True
    elif apply and result["status"] == "mismatch" and confirm_mismatch:
        db.apply_lead_verification(
            lead_id,
            user_id=result.get("resolved_user_id") or "",
            handle=result.get("resolved_handle")
            or lead.get("platform_handle")
            or "",
            identity_status="verified",
            confidence="manual_mismatch",
            provider=result.get("provider") or "",
            note="用户确认采用抓取到的账号",
            event_type="mismatch_confirmed",
            profile_url=result.get("resolved_profile_url") or "",
            actor_id=actor_id,
        )
        applied = True
    record_identity_attempt(
        db,
        lead,
        result,
        applied=applied,
        actor_id=actor_id,
    )
    if apply and not applied and result["status"] in {"mismatch", "not_found", "error"}:
        db.add_lead_identity_event(
            lead_id,
            f"verify_{result['status']}",
            result["status"],
            old_user_id=lead.get("account_id") or "",
            new_user_id=result.get("resolved_user_id") or "",
            old_handle=lead.get("platform_handle") or "",
            new_handle=result.get("resolved_handle") or "",
            provider=result.get("provider") or "",
            confidence=result.get("confidence") or "",
            note=result.get("note") or "",
            actor_id=actor_id,
        )
    if applied:
        match_lead_against_creators(db, lead_id, actor_id=actor_id)
        result["lead"] = db.get_lead(lead_id)
    result["applied"] = applied
    return result
