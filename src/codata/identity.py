from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

_ALLOWED_HOSTS = {
    "xhs": ("xiaohongshu.com",),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "bilibili": ("bilibili.com",),
    "zhihu": ("zhihu.com",),
    "instagram": ("instagram.com",),
}

_SHORT_LINK_HOSTS = {
    "v.douyin.com",
    "xhslink.com",
    "xhslink.cn",
    "b23.tv",
    "t.cn",
}

_INSTAGRAM_RESERVED = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "accounts",
    "direct",
    "about",
    "developer",
}

_PROFILE_URL_BUILDERS = {
    "xhs": "https://www.xiaohongshu.com/user/profile/{account}",
    "douyin": "https://www.douyin.com/user/{account}",
    "bilibili": "https://space.bilibili.com/{account}",
    "zhihu": "https://www.zhihu.com/people/{account}",
    "instagram": "https://www.instagram.com/{account}/",
}


def _is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value, re.IGNORECASE))


def _host_allowed(platform: str, host: str) -> bool:
    host = host.lower().strip(".")
    for domain in _ALLOWED_HOSTS.get(platform, ()):
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _path_segments(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


def _account_from_url(platform: str, text: str) -> str:
    parsed = urlsplit(text)
    host = (parsed.hostname or "").lower().strip(".")
    if not host or host in _SHORT_LINK_HOSTS:
        return ""
    if not _host_allowed(platform, host):
        return ""
    parts = _path_segments(parsed.path)
    if platform == "xhs":
        if "profile" in parts:
            index = parts.index("profile")
            if index + 1 < len(parts):
                return parts[index + 1].strip()
        return ""
    if platform == "douyin":
        if "user" in parts:
            index = parts.index("user")
            if index + 1 < len(parts):
                return parts[index + 1].strip()
        return ""
    if platform == "bilibili":
        if parts and parts[0].isdigit():
            return parts[0].strip()
        if "space" in parts:
            index = parts.index("space")
            if index + 1 < len(parts) and parts[index + 1].isdigit():
                return parts[index + 1].strip()
        return ""
    if platform == "zhihu":
        if "people" in parts:
            index = parts.index("people")
            if index + 1 < len(parts):
                return parts[index + 1].strip()
        return ""
    if platform == "instagram":
        for part in parts:
            lowered = part.lower()
            if lowered in _INSTAGRAM_RESERVED:
                return ""
            return lowered.strip()
        return ""
    return ""


def profile_id_from_url(platform: str, value: str) -> str:
    code = str(platform or "xhs").strip().lower()
    text = str(value or "").strip()
    if not text:
        return ""
    if _is_url(text):
        return _account_from_url(code, text)
    if "://" in text or text.lower().startswith(("javascript:", "data:")):
        return ""
    account = text.strip().strip("/")
    if "/" in account or not account:
        return ""
    if code == "instagram":
        account = account.lower()
    return account


def normalize_account_id(platform: str, value: str) -> str:
    return profile_id_from_url(platform, value)


def canonical_profile_url(platform: str, account_id: str) -> str:
    account = str(account_id or "").strip().strip("/")
    builder = _PROFILE_URL_BUILDERS.get(str(platform or "xhs").strip().lower())
    if not account or not builder:
        return ""
    return builder.format(account=quote(account, safe=""))


def normalize_identity(platform: str, account_id: str, profile_url: str) -> tuple[str, str]:
    code = str(platform or "xhs").strip().lower()
    raw_account = str(account_id or "").strip()
    raw_profile = str(profile_url or "").strip()
    profile_account = normalize_account_id(code, raw_profile) if raw_profile else ""
    input_account = normalize_account_id(code, raw_account) if raw_account else ""
    account = input_account or profile_account
    canonical = canonical_profile_url(code, account) if account else ""
    if not canonical and raw_profile and _is_url(raw_profile):
        parsed = urlsplit(raw_profile)
        if parsed.scheme in {"http", "https"} and _host_allowed(
            code, (parsed.hostname or "").lower()
        ):
            canonical = raw_profile
    return account, canonical
