from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .config import Settings
from .db import Database
from .platforms import load_registry
from .providers import ProviderError, build_provider


def _profile_id(platform: str, value: str) -> str:
    text = (value or "").strip().strip("/")
    if not text:
        return ""
    if platform == "xhs":
        match = re.search(r"/user/profile/([^/?#]+)", text)
        return match.group(1) if match else text
    if platform == "bilibili":
        match = re.search(r"space\.bilibili\.com/(\d+)", text)
        return match.group(1) if match else text
    if platform == "zhihu":
        match = re.search(r"zhihu\.com/people/([^/?#]+)", text)
        return match.group(1) if match else text
    if platform == "instagram":
        match = re.search(r"instagram\.com/([^/?#]+)", text)
        return match.group(1) if match else text
    return text


def _profile_url(platform: str, user_id: str) -> str:
    if platform == "xhs":
        return f"https://www.xiaohongshu.com/user/profile/{user_id}"
    if platform == "bilibili":
        return f"https://space.bilibili.com/{user_id}"
    if platform == "zhihu":
        return f"https://www.zhihu.com/people/{user_id}"
    if platform == "instagram":
        return f"https://www.instagram.com/{user_id}/"
    return ""


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
        real_id = _profile_id(
            platform,
            account_id or lead.get("account_id") or source_url,
        )
        if not real_id:
            raise ValueError("需要真实平台账号 ID 或主页 URL 才能转达人")
        rounds = db.lead_rounds(lead_id)
        if not rounds and lead.get("source_round_id"):
            rounds = [{"round_id": lead["source_round_id"]}]
        if not rounds:
            raise ValueError("线索没有关联轮次，无法转为达人")
        rounds = [
            source for source in rounds if db.get_round(int(source["round_id"]))
        ]
        if not rounds:
            raise ValueError("线索关联的轮次已不存在，无法转为达人")
        creator = db.get_creator(platform, real_id)
        if not fetch and not creator:
            raise ValueError("本地没有该达人资料，请开启抓取后重试")

        if fetch:
            registry = load_registry(settings)
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
            cost_native = float(price.amount) if price is not None else 0.0
            cost_currency = price.currency if price is not None else "token"
            db.log_api_call(
                int(rounds[0]["round_id"]),
                platform,
                "user",
                {"userId": real_id},
                None,
                200,
                provider.user_cost_tokens,
                duration_ms,
                path=provider.user_path,
                provider=provider.provider_id,
                cost_native=cost_native,
                cost_currency=cost_currency,
            )
            if not fetched.get("user_id"):
                fetched["user_id"] = real_id
            if not fetched.get("profile_url"):
                fetched["profile_url"] = source_url or _profile_url(platform, real_id)
            db.upsert_creator(fetched)
            creator = db.get_creator(platform, real_id)
            db.add_lead_attempt(
                lead_id,
                "matched",
                provider=provider.provider_id,
                query=real_id,
                candidate_user_id=real_id,
                candidate_nickname=fetched.get("nickname") or "",
                candidate_profile_url=fetched.get("profile_url") or "",
                cost=provider.user_cost_tokens,
                cost_native=cost_native,
                cost_currency=cost_currency,
                created_by=actor_id,
            )
        else:
            db.add_lead_attempt(
                lead_id,
                "matched",
                query=real_id,
                candidate_user_id=real_id,
                candidate_nickname=(creator or {}).get("nickname") or "",
                candidate_profile_url=(creator or {}).get("profile_url") or "",
                created_by=actor_id,
            )

        for source in rounds:
            source_keywords = source.get("source_keywords") or json.dumps(["线索转换"], ensure_ascii=False)
            db.add_creator_round(
                int(source["round_id"]),
                platform,
                real_id,
                source_keywords=source_keywords,
                raw_title=source.get("raw_title") or lead.get("raw_title") or "",
                account_id=real_id,
                data_round=source.get("data_round") or "",
                top_note_id=source.get("top_note_id") or "",
                sample_notes=int(source.get("sample_notes") or 0),
                sample_likes=int(source.get("sample_likes") or 0),
                sample_collected=int(source.get("sample_collected") or 0),
                sample_comments=int(source.get("sample_comments") or 0),
                sample_interactions=int(source.get("sample_interactions") or 0),
            )
        update_fields: dict[str, Any] = {
            "account_id": real_id,
            "profile_url": source_url or _profile_url(platform, real_id),
        }
        if lead.get("owner_user_id") is None and actor_id is not None:
            update_fields["owner_user_id"] = actor_id
        db.update_lead(lead_id, **update_fields)
        db.mark_lead_converted(lead_id, platform, real_id)
        return {
            "lead": db.get_lead(lead_id),
            "creator": db.get_creator(platform, real_id),
            "rounds": [int(source["round_id"]) for source in rounds],
        }
    finally:
        db.release_lead_conversion(lead_id, token)
