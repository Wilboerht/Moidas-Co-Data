from __future__ import annotations

import re
from typing import Any

VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

TEMPLATE_VARIABLES = (
    ("nickname", "达人昵称"),
    ("platform", "平台"),
    ("user_id", "平台用户 ID"),
    ("fans", "粉丝数"),
    ("profile_url", "主页链接"),
)


def render_template(text: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            return match.group(0)
        value = context.get(key)
        return "" if value is None else str(value)

    return VARIABLE_PATTERN.sub(replace, str(text or ""))


def creator_context(creator: dict[str, Any]) -> dict[str, Any]:
    return {
        "nickname": creator.get("nickname") or "",
        "platform": creator.get("platform") or "",
        "user_id": creator.get("user_id") or "",
        "fans": creator.get("fans") or "",
        "profile_url": creator.get("profile_url") or "",
    }
