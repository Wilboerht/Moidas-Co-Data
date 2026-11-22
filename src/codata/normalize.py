from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .statuses import STATUS_OPTIONS
from .storage import unwrap_data, unwrap_item

UNCLASSIFIED = "未分类"

_HASHTAG_RE = re.compile(r"#([^#\s@，。！？、；：,.!?;:]+)")


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "" or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    multiplier = 1
    if text.endswith(("万", "w", "W")):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            return default
        return int(float(match.group()) * multiplier)


def first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def note_url(note_id: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""


def profile_url(user_id: str) -> str:
    return f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else ""


def extract_hashtags(*texts: Any, limit: int = 8) -> list[str]:
    tags: list[str] = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for match in _HASHTAG_RE.finditer(text):
            tag = match.group(1).strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags[:limit]


def note_hashtags(note: dict[str, Any]) -> list[str]:
    tags = extract_hashtags(note.get("title"), note.get("desc"), note.get("content"))
    for tag in note.get("hash_tag") or note.get("tags") or []:
        if isinstance(tag, dict):
            name = first_str(tag.get("name"), tag.get("title"))
        else:
            name = first_str(tag)
        if name and name not in tags:
            tags.append(name)
    return tags


def _metric(note: dict[str, Any], *keys: str) -> int:
    interact = first_dict(note.get("interact_info"), note.get("interactInfo"))
    for key in keys:
        if key in note and note[key] is not None:
            return as_int(note[key])
        if key in interact and interact[key] is not None:
            return as_int(interact[key])
    return 0


def normalize_search_item(item: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raw_note = unwrap_item(item)
    if not isinstance(raw_note, dict):
        return {}, None

    user = first_dict(
        raw_note.get("user"),
        raw_note.get("author"),
        raw_note.get("user_info"),
        raw_note.get("userInfo"),
    )
    note_id = first_str(
        raw_note.get("id"),
        raw_note.get("note_id"),
        raw_note.get("noteId"),
    )
    user_id = first_str(
        user.get("userid"),
        user.get("user_id"),
        user.get("userId"),
        user.get("id"),
        raw_note.get("userid"),
        raw_note.get("user_id"),
        raw_note.get("userId"),
        raw_note.get("author_id"),
    )

    desc = first_str(
        raw_note.get("desc"),
        raw_note.get("description"),
        raw_note.get("content"),
    )
    title = first_str(
        raw_note.get("title"),
        raw_note.get("display_title"),
        raw_note.get("displayTitle"),
    )
    if not title and desc:
        title = desc.splitlines()[0][:80]

    published_at = as_int(
        raw_note.get("timestamp")
        or raw_note.get("time")
        or raw_note.get("publish_time")
        or raw_note.get("publishTime")
        or raw_note.get("create_time")
        or raw_note.get("createTime")
    )
    if published_at > 10_000_000_000:
        published_at //= 1000

    note = {
        "platform": "xhs",
        "note_id": note_id,
        "author_id": user_id or None,
        "title": title,
        "desc": desc,
        "note_type": first_str(raw_note.get("type"), raw_note.get("note_type"), raw_note.get("noteType")),
        "published_at": published_at or None,
        "liked": _metric(raw_note, "liked_count", "likedCount", "like_count", "likes"),
        "collected": _metric(raw_note, "collected_count", "collectedCount", "collect_count", "collects"),
        "comments": _metric(raw_note, "comments_count", "commentsCount", "comment_count", "comments"),
        "shares": _metric(raw_note, "shared_count", "sharedCount", "share_count", "shares"),
        "url": note_url(note_id),
        "hashtags": json.dumps(note_hashtags(raw_note), ensure_ascii=False),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }

    author = None
    if user_id:
        author = {
            "platform": "xhs",
            "user_id": user_id,
            "red_id": first_str(user.get("red_id"), user.get("redId")),
            "nickname": first_str(user.get("nickname"), user.get("nick_name"), user.get("nickName")),
            "avatar": first_str(user.get("images"), user.get("image"), user.get("avatar")),
            "profile_url": profile_url(user_id),
        }
    return note, author


def normalize_user_info(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("用户信息响应格式异常")

    user_id = first_str(
        data.get("userid"),
        data.get("user_id"),
        data.get("userId"),
        data.get("id"),
        user_id,
    )
    stats = first_dict(data.get("note_num_stat"), data.get("noteNumStat"))
    liked = as_int(data.get("liked") or stats.get("liked"))
    collected = as_int(data.get("collected") or stats.get("collected"))
    interaction = liked + collected
    for item in data.get("interactions") or []:
        if isinstance(item, dict) and item.get("type") == "interaction":
            interaction = as_int(item.get("count"), interaction)
            break

    tags = []
    for tag in data.get("tags") or []:
        if isinstance(tag, dict):
            name = first_str(tag.get("name"), tag.get("title"))
        else:
            name = first_str(tag)
        if name and name not in tags:
            tags.append(name)

    verify_info = first_str(
        data.get("red_official_verify_content"),
        data.get("red_official_verify_base_info"),
    )
    return {
        "platform": "xhs",
        "user_id": user_id,
        "red_id": first_str(data.get("red_id"), data.get("redId")),
        "nickname": first_str(data.get("nickname"), data.get("nick_name"), data.get("nickName")),
        "avatar": first_str(data.get("images"), data.get("image"), data.get("imageb"), data.get("avatar")),
        "fans": as_int(data.get("fans") or data.get("fans_count") or data.get("fansCount")),
        "follows": as_int(data.get("follows") or data.get("follow_count")),
        "liked": liked,
        "collected": collected,
        "posted": as_int(stats.get("posted") or data.get("ndiscovery") or data.get("note_count")),
        "interaction": interaction,
        "desc": first_str(data.get("desc"), data.get("description")),
        "tags_json": json.dumps(tags, ensure_ascii=False),
        "ip_location": first_str(data.get("ip_location"), data.get("ipLocation")),
        "verify_info": verify_info,
        "profile_url": profile_url(user_id),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def _rnote_tags(*values: Any) -> list[str]:
    tags: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                name = first_str(item.get("name"), item.get("title"), item.get("tag"))
            else:
                name = first_str(item)
            if name and name not in tags:
                tags.append(name)
    return tags


def normalize_rnote_user(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("Rnote 用户信息响应格式异常")
    result = first_dict(data.get("result"), data.get("user"), data.get("user_info"), data)
    stats = first_dict(result.get("note_num_stat"), data.get("note_num_stat"))
    resolved_id = first_str(
        user_id,
        result.get("user_id"),
        result.get("userid"),
        result.get("userId"),
        data.get("user_id"),
        data.get("userid"),
        data.get("userId"),
    )
    liked = as_int(
        data.get("liked")
        or data.get("liked_count")
        or result.get("liked")
        or stats.get("liked")
    )
    collected = as_int(
        data.get("collected")
        or data.get("collected_count")
        or result.get("collected")
        or stats.get("collected")
    )
    interaction = liked + collected
    for value in (data.get("interaction"), result.get("interaction")):
        if value not in (None, ""):
            interaction = as_int(value)
            break
    fans = (
        data.get("fans")
        or data.get("fans_count")
        or data.get("fansCount")
        or result.get("fans")
        or result.get("fans_count")
        or result.get("fansCount")
    )
    avatar_values = (
        result.get("image"),
        result.get("images"),
        result.get("avatar"),
        data.get("image"),
        data.get("images"),
        data.get("avatar"),
    )
    avatar = ""
    for value in avatar_values:
        if isinstance(value, str) and value.strip():
            avatar = value.strip()
            break
    return {
        "platform": "xhs",
        "user_id": resolved_id,
        "red_id": first_str(result.get("red_id"), result.get("redId"), data.get("red_id"), data.get("redId")),
        "nickname": first_str(
            result.get("nickname"),
            result.get("nick_name"),
            result.get("nickName"),
            result.get("name"),
            data.get("nickname"),
            data.get("nick_name"),
        ),
        "avatar": avatar,
        "fans": as_int(fans),
        "follows": as_int(
            data.get("follows")
            or data.get("follow_count")
            or result.get("follows")
            or result.get("follow_count")
        ),
        "liked": liked,
        "collected": collected,
        "posted": as_int(
            stats.get("posted")
            or data.get("posted")
            or data.get("note_count")
            or result.get("posted")
            or result.get("note_count")
        ),
        "interaction": interaction,
        "desc": first_str(
            result.get("desc"),
            result.get("description"),
            data.get("desc"),
            data.get("description"),
        ),
        "tags_json": json.dumps(
            _rnote_tags(result.get("tags"), data.get("tags")), ensure_ascii=False
        ),
        "ip_location": first_str(
            data.get("ip_location"),
            data.get("ipLocation"),
            result.get("ip_location"),
            result.get("ipLocation"),
        ),
        "verify_info": first_str(
            result.get("verify_info"),
            result.get("verified"),
            data.get("verify_info"),
        ),
        "profile_url": profile_url(resolved_id),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


TIER_LABELS = ("素人", "尾部", "腰部", "头部", "顶流")


def fans_tier(fans: Any) -> str:
    value = as_int(fans)
    if value >= 1_000_000:
        return "顶流"
    if value >= 500_000:
        return "头部"
    if value >= 100_000:
        return "腰部"
    if value >= 10_000:
        return "尾部"
    return "素人"


def normalize_tier_label(fans: Any, label: Any = None) -> str:
    """Return a canonical tier. Fan counts always win over free-text labels."""
    if fans is not None and str(fans).strip() != "":
        return fans_tier(fans)
    text = str(label or "").strip()
    return text if text in TIER_LABELS else ""


def format_fans_wan(fans: Any) -> str:
    value = as_int(fans)
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


def split_tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[,，、;；|/]+", str(value))
    tags = []
    for tag in raw:
        tag = tag.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def derive_tags(
    categories: Iterable[str],
    hashtags: Iterable[str],
    verify_info: str = "",
    extra: Iterable[str] = (),
    limit: int = 10,
) -> list[str]:
    tags: list[str] = []
    for group in (categories, [verify_info], extra, hashtags):
        for value in group:
            value = str(value).strip()
            if value and value not in tags:
                tags.append(value)
    return tags[:limit]


def derive_match_direction(categories: Iterable[str]) -> str:
    unique = []
    for category in categories:
        category = str(category).strip()
        if category and category not in unique:
            unique.append(category)
    if not unique:
        return UNCLASSIFIED
    if len(unique) == 1:
        return unique[0]
    return "多垂类"
