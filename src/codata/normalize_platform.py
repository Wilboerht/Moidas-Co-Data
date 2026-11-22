from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from .normalize import as_int, first_dict, first_str, note_hashtags, utc_now
from .storage import unwrap_data


def note_url(platform: str, note_id: str) -> str:
    if not note_id:
        return ""
    if platform == "douyin":
        return f"https://www.douyin.com/video/{note_id}"
    if platform == "bilibili":
        return f"https://www.bilibili.com/video/{note_id}"
    if platform == "instagram":
        return f"https://www.instagram.com/p/{note_id}/"
    if platform == "zhihu":
        return ""
    return f"https://www.xiaohongshu.com/explore/{note_id}"


def profile_url(platform: str, user_id: str) -> str:
    if not user_id:
        return ""
    if platform == "douyin":
        return f"https://www.douyin.com/user/{user_id}"
    if platform == "bilibili":
        return f"https://space.bilibili.com/{user_id}"
    if platform == "instagram":
        return f"https://www.instagram.com/{user_id}/"
    if platform == "zhihu":
        return f"https://www.zhihu.com/people/{user_id}"
    return f"https://www.xiaohongshu.com/user/profile/{user_id}"


def as_timestamp(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = int(value)
        return stamp // 1000 if stamp > 10_000_000_000 else stamp
    text = str(value).strip()
    if not text:
        return None
    try:
        stamp = int(float(text))
        return stamp // 1000 if stamp > 10_000_000_000 else stamp
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00").replace("/", "-")
    for parser in (
        lambda item: datetime.fromisoformat(item),
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S"),
        lambda item: datetime.strptime(item, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except ValueError:
            continue
    return None


def _unwrap_any(value: Any, depth: int = 0) -> dict[str, Any]:
    if depth > 4 or not isinstance(value, dict):
        return {}
    for key in ("note", "note_card", "noteCard", "aweme_info", "awemeInfo", "work", "item", "card", "data"):
        nested = value.get(key)
        if isinstance(nested, dict):
            found = _unwrap_any(nested, depth + 1)
            if found:
                return found
    return value


def _metric(item: dict[str, Any], *keys: str) -> int:
    containers = [
        item,
        first_dict(item.get("interact_info"), item.get("interactInfo")),
        first_dict(item.get("statistics"), item.get("stats")),
        first_dict(item.get("stat")),
        first_dict(item.get("counts")),
        first_dict(item.get("author_stats"), item.get("authorStats")),
    ]
    for key in keys:
        for container in containers:
            if key in container and container[key] not in (None, ""):
                return as_int(container[key])
    return 0


def _author(item: dict[str, Any]) -> dict[str, Any]:
    return first_dict(
        item.get("user"),
        item.get("author"),
        item.get("user_info"),
        item.get("userInfo"),
        item.get("author_info"),
        item.get("authorInfo"),
        item.get("owner"),
        item.get("account"),
    )


def _id_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return str(int(value))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _author_id(item: dict[str, Any], user: dict[str, Any], platform: str) -> str:
    if platform == "instagram":
        return _id_str(
            user.get("username"),
            item.get("author_username"),
            item.get("authorUsername"),
            user.get("user_id"),
            item.get("author_id"),
        )
    if platform == "bilibili":
        return _id_str(
            user.get("mid"),
            item.get("author_id"),
            item.get("authorId"),
            item.get("mid"),
            user.get("user_id"),
            user.get("id"),
        )
    if platform == "zhihu":
        return _id_str(
            user.get("id"),
            user.get("user_id"),
            user.get("userId"),
            item.get("author_id"),
            item.get("authorId"),
        )
    if platform == "douyin":
        return _id_str(
            user.get("sec_uid"),
            user.get("secUid"),
            user.get("uid"),
            user.get("user_id"),
            user.get("userId"),
            item.get("sec_uid"),
            item.get("secUid"),
            item.get("authorId"),
            item.get("author_id"),
            item.get("author_uid"),
            item.get("uid"),
            item.get("user_id"),
            item.get("userId"),
        )
    return _id_str(
        user.get("userid"),
        user.get("user_id"),
        user.get("userId"),
        user.get("id"),
        item.get("accountUserid"),
        item.get("account_user_id"),
        item.get("author_id"),
        item.get("authorId"),
        item.get("user_id"),
        item.get("userId"),
        item.get("userid"),
    )


def _avatar(item: dict[str, Any], user: dict[str, Any]) -> str:
    for value in (
        user.get("avatar"),
        user.get("avatar_url"),
        user.get("avatarUrl"),
        item.get("author_avatar_url"),
        item.get("authorAvatar"),
        item.get("accountAvatar"),
        item.get("authorUrl"),
        item.get("avatar"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            url = first_str(value.get("url"), value.get("uri"))
            if url:
                return url
            urls = value.get("url_list") or value.get("urlList")
            if isinstance(urls, list) and urls:
                return first_str(urls[0])
    return ""


def _hashtags(item: dict[str, Any]) -> list[str]:
    tags = note_hashtags(item)
    for value in (item.get("hashtags"), item.get("tag_list"), item.get("tagList"), item.get("topics")):
        if not isinstance(value, list):
            continue
        for tag in value:
            name = first_str(tag.get("name"), tag.get("title")) if isinstance(tag, dict) else first_str(tag)
            if name and name not in tags:
                tags.append(name)
    return tags[:20]


def normalize_search_item_platform(
    item: Any, platform: str = "xhs"
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raw = _unwrap_any(item)
    if not isinstance(raw, dict):
        return {}, None
    user = _author(raw)
    note_id = first_str(
        raw.get("note_id"),
        raw.get("noteId"),
        raw.get("aweme_id"),
        raw.get("awemeId"),
        raw.get("workId"),
        raw.get("work_id"),
        raw.get("item_id"),
        raw.get("bvid"),
        raw.get("post_id"),
        raw.get("shortcode"),
        raw.get("id"),
    )
    user_id = _author_id(raw, user, platform)
    title = first_str(
        raw.get("title"),
        raw.get("display_title"),
        raw.get("displayTitle"),
        raw.get("workTitle"),
        raw.get("work_title"),
        raw.get("desc"),
        raw.get("description"),
        raw.get("text"),
        raw.get("content"),
        raw.get("workDesc"),
    )
    desc = first_str(
        raw.get("desc"),
        raw.get("description"),
        raw.get("content"),
        raw.get("text"),
        raw.get("workDesc"),
        raw.get("workContent"),
    )
    if not title and desc:
        title = desc.splitlines()[0][:80]
    note_type = first_str(
        raw.get("type"),
        raw.get("note_type"),
        raw.get("noteType"),
        raw.get("workType"),
        raw.get("media_type"),
        raw.get("post_type"),
    )
    published_at = as_timestamp(
        raw.get("published_at")
        or raw.get("posted_at")
        or raw.get("publish_time")
        or raw.get("publishTime")
        or raw.get("workPublishTime")
        or raw.get("create_time")
        or raw.get("createTime")
        or raw.get("taken_at")
        or raw.get("timestamp")
        or raw.get("time")
    )
    note = {
        "platform": platform,
        "note_id": note_id,
        "author_id": user_id or None,
        "title": title,
        "desc": desc,
        "note_type": note_type,
        "published_at": published_at,
        "liked": _metric(
            raw,
            "liked_count",
            "likedCount",
            "like_count",
            "likeCount",
            "digg_count",
            "workLikedCount",
            "likes",
            "like",
        ),
        "collected": _metric(
            raw,
            "collected_count",
            "collectedCount",
            "collect_count",
            "collectCount",
            "workCollectedCount",
            "collects",
            "favorite",
            "favorite_count",
        ),
        "comments": _metric(
            raw,
            "comments_count",
            "commentsCount",
            "comment_count",
            "commentCount",
            "workCommentsCount",
            "reply",
        ),
        "shares": _metric(
            raw,
            "shared_count",
            "sharedCount",
            "share_count",
            "shareCount",
            "workSharedCount",
            "share",
        ),
        "url": first_str(raw.get("url"), raw.get("workUrl"), raw.get("share_url"))
        or note_url(platform, note_id),
        "hashtags": json.dumps(_hashtags(raw), ensure_ascii=False),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }
    author = None
    if user_id:
        author = {
            "platform": platform,
            "user_id": user_id,
            "red_id": first_str(
                user.get("red_id"),
                user.get("redId"),
                user.get("unique_id"),
                user.get("uniqueId"),
                raw.get("authorId"),
                raw.get("accountId"),
            ),
            "nickname": first_str(
                user.get("nickname"),
                user.get("nick_name"),
                user.get("nickName"),
                user.get("name"),
                raw.get("author_name"),
                raw.get("authorName"),
                raw.get("accountName"),
                raw.get("accountNickname"),
            ),
            "avatar": _avatar(raw, user),
            "profile_url": first_str(
                raw.get("author_url"),
                user.get("url"),
                user.get("profile_url"),
                raw.get("url") if platform == "zhihu" else "",
            ) or profile_url(platform, user_id),
        }
    return note, author


def _nested_user(payload: Any) -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        return {}
    for key in ("user", "user_info", "userInfo", "author", "result", "results", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return nested
    return data


def normalize_xhs_user(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("小红书用户信息响应格式异常")
    user = _nested_user(payload)
    merged = {**data, **user}
    resolved = first_str(
        user_id,
        merged.get("user_id"),
        merged.get("userid"),
        merged.get("userId"),
        merged.get("id"),
    )
    avatar = _avatar(merged, merged)
    return {
        "platform": "xhs",
        "user_id": resolved,
        "red_id": first_str(merged.get("red_id"), merged.get("redId"), merged.get("accountId")),
        "nickname": first_str(
            merged.get("nickname"),
            merged.get("nick_name"),
            merged.get("nickName"),
            merged.get("accountName"),
            merged.get("name"),
        ),
        "avatar": avatar,
        "fans": as_int(
            merged.get("fans")
            or merged.get("fans_count")
            or merged.get("fansCount")
            or merged.get("follower_count")
            or merged.get("accountFans")
        ),
        "follows": as_int(
            merged.get("follows")
            or merged.get("follow_count")
            or merged.get("following_count")
            or merged.get("accountFollows")
        ),
        "liked": as_int(
            merged.get("liked")
            or merged.get("liked_count")
            or merged.get("like_count")
            or merged.get("accountLikes")
        ),
        "collected": as_int(
            merged.get("collected")
            or merged.get("collected_count")
            or merged.get("collect_count")
            or merged.get("accountCollectes")
        ),
        "posted": as_int(
            merged.get("posted")
            or merged.get("note_count")
            or merged.get("post_count")
            or merged.get("accountTotalWorks")
            or merged.get("video_count")
        ),
        "interaction": as_int(
            merged.get("interaction")
            or merged.get("liked")
            or merged.get("liked_count")
            or merged.get("like_count")
            or merged.get("accountLikes")
        )
        + as_int(
            merged.get("collected")
            or merged.get("collected_count")
            or merged.get("collect_count")
            or merged.get("accountCollectes")
        ),
        "desc": first_str(
            merged.get("desc"),
            merged.get("description"),
            merged.get("bio"),
            merged.get("signature"),
            merged.get("accountDesc"),
        ),
        "tags_json": json.dumps([], ensure_ascii=False),
        "ip_location": first_str(
            merged.get("ip_location"),
            merged.get("ipLocation"),
            merged.get("city"),
        ),
        "verify_info": first_str(merged.get("verify_info"), merged.get("verifyInfo")),
        "profile_url": profile_url("xhs", resolved),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def _douyin_avatar(user: dict[str, Any], data: dict[str, Any]) -> str:
    value = user.get("avatar_larger") or user.get("avatarLarger") or user.get("avatar_thumb")
    if isinstance(value, dict):
        urls = value.get("url_list") or value.get("urlList") or []
        if isinstance(urls, list) and urls:
            return first_str(urls[0])
    return first_str(
        user.get("avatar_url"),
        user.get("avatarUrl"),
        user.get("avatar"),
        data.get("avatarUrl"),
        data.get("avatar"),
    )


def normalize_douyin_user(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("抖音用户信息响应格式异常")
    user = _nested_user(payload)
    merged = {**data, **user}
    resolved = first_str(
        user_id,
        merged.get("sec_uid"),
        merged.get("secUid"),
        merged.get("uid"),
        merged.get("user_id"),
        merged.get("userId"),
        merged.get("unique_id"),
        merged.get("uniqueId"),
    )
    liked = as_int(
        merged.get("liked")
        or merged.get("total_favorited")
        or merged.get("totalFavorited")
        or merged.get("like_count")
        or merged.get("liked_count")
    )
    collected = as_int(
        merged.get("collected")
        or merged.get("collect_count")
        or merged.get("collected_count")
    )
    return {
        "platform": "douyin",
        "user_id": resolved,
        "red_id": first_str(
            merged.get("unique_id"),
            merged.get("uniqueId"),
            merged.get("displayId"),
            merged.get("accountId"),
        ),
        "nickname": first_str(
            merged.get("nickname"),
            merged.get("nick_name"),
            merged.get("nickName"),
            merged.get("accountName"),
            merged.get("name"),
        ),
        "avatar": _douyin_avatar(merged, data),
        "fans": as_int(
            merged.get("follower_count")
            or merged.get("followerCount")
            or merged.get("fans")
            or merged.get("accountFans")
        ),
        "follows": as_int(
            merged.get("following_count")
            or merged.get("followingCount")
            or merged.get("follows")
            or merged.get("accountFollows")
        ),
        "liked": liked,
        "collected": collected,
        "posted": as_int(
            merged.get("aweme_count")
            or merged.get("awemeCount")
            or merged.get("video_count")
            or merged.get("accountTotalWorks")
        ),
        "interaction": liked + collected,
        "desc": first_str(
            merged.get("signature"),
            merged.get("desc"),
            merged.get("description"),
            merged.get("accountDesc"),
        ),
        "tags_json": json.dumps([], ensure_ascii=False),
        "ip_location": first_str(
            merged.get("ip_location"),
            merged.get("ipLocation"),
            merged.get("city"),
        ),
        "verify_info": first_str(
            merged.get("verify_info"),
            merged.get("verifyInfo"),
            merged.get("custom_verify"),
            merged.get("enterprise_verify_reason"),
        ),
        "profile_url": profile_url("douyin", resolved),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def normalize_bilibili_user(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("B站用户信息响应格式异常")
    user = _nested_user(payload)
    merged = {**data, **user}
    resolved = first_str(user_id, merged.get("mid"), merged.get("user_id"), merged.get("id"))
    liked = as_int(merged.get("like_count") or merged.get("liked") or merged.get("total_favorited"))
    return {
        "platform": "bilibili",
        "user_id": resolved,
        "red_id": "",
        "nickname": first_str(merged.get("name"), merged.get("nickname"), merged.get("uname")),
        "avatar": _avatar(merged, merged),
        "fans": as_int(merged.get("follower_count") or merged.get("fans")),
        "follows": as_int(merged.get("following_count") or merged.get("follows")),
        "liked": liked,
        "collected": 0,
        "posted": as_int(merged.get("video_count") or merged.get("posted")),
        "interaction": liked,
        "desc": first_str(merged.get("bio"), merged.get("sign"), merged.get("description")),
        "tags_json": json.dumps([], ensure_ascii=False),
        "ip_location": "",
        "verify_info": first_str(merged.get("verified_title"), merged.get("verify_info")),
        "profile_url": first_str(merged.get("url"), merged.get("author_url")) or profile_url("bilibili", resolved),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def normalize_zhihu_user(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("知乎用户信息响应格式异常")
    user = _nested_user(payload)
    merged = {**data, **user}
    resolved = first_str(
        user_id,
        merged.get("author_id"),
        merged.get("user_id"),
        merged.get("id"),
    )
    answered = as_int(merged.get("answer_count"))
    articles = as_int(merged.get("article_count"))
    liked = as_int(merged.get("received_upvote_count") or merged.get("like_count"))
    return {
        "platform": "zhihu",
        "user_id": resolved,
        "red_id": "",
        "nickname": first_str(
            merged.get("author_name"),
            merged.get("display_name"),
            merged.get("name"),
        ),
        "avatar": _avatar(merged, merged),
        "fans": as_int(merged.get("author_follower_count") or merged.get("follower_count")),
        "follows": as_int(merged.get("following_count")),
        "liked": liked,
        "collected": 0,
        "posted": answered + articles,
        "interaction": liked,
        "desc": first_str(merged.get("author_bio"), merged.get("bio"), merged.get("description")),
        "tags_json": json.dumps([], ensure_ascii=False),
        "ip_location": "",
        "verify_info": first_str(merged.get("author_verification_label"), merged.get("verification_label")),
        "profile_url": first_str(merged.get("author_url"), merged.get("url")) or profile_url("zhihu", resolved),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def normalize_instagram_user(payload: Any, user_id: str = "") -> dict[str, Any]:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise ValueError("Instagram 用户信息响应格式异常")
    user = _nested_user(payload)
    merged = {**data, **user}
    username = first_str(merged.get("username"), merged.get("user_name"), user_id)
    return {
        "platform": "instagram",
        "user_id": username,
        "red_id": "",
        "nickname": first_str(merged.get("display_name"), merged.get("name"), username),
        "avatar": _avatar(merged, merged),
        "fans": as_int(merged.get("follower_count") or merged.get("followers")),
        "follows": as_int(merged.get("following_count") or merged.get("follows")),
        "liked": 0,
        "collected": 0,
        "posted": as_int(merged.get("post_count") or merged.get("media_count")),
        "interaction": 0,
        "desc": first_str(merged.get("bio"), merged.get("biography"), merged.get("description")),
        "tags_json": json.dumps([], ensure_ascii=False),
        "ip_location": "",
        "verify_info": "已认证" if merged.get("is_verified") else "",
        "profile_url": first_str(merged.get("url")) or profile_url("instagram", username),
        "fetched_at": utc_now(),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def normalize_user_platform(platform: str, payload: Any, user_id: str = "") -> dict[str, Any]:
    if platform == "douyin":
        return normalize_douyin_user(payload, user_id)
    if platform == "bilibili":
        return normalize_bilibili_user(payload, user_id)
    if platform == "zhihu":
        return normalize_zhihu_user(payload, user_id)
    if platform == "instagram":
        return normalize_instagram_user(payload, user_id)
    return normalize_xhs_user(payload, user_id)
