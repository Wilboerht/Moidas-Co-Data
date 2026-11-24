from __future__ import annotations

from typing import Any

# Cooperation follow-up workflow. The database stores codes; labels are display-only.
# Empty string / NULL means the creator has not entered the cooperation pipeline.
COOPERATION_STATUS_DEFS = [
    {"code": "not_contacted", "label": "未联系", "color": "gray", "order": 10},
    {"code": "contacted", "label": "已联系", "color": "blue", "order": 20},
    {"code": "negotiating", "label": "洽谈中", "color": "amber", "order": 30},
    {"code": "cooperating", "label": "合作中", "color": "violet", "order": 40},
    {"code": "completed", "label": "合作完成", "color": "green", "order": 50},
    {"code": "declined", "label": "已拒绝", "color": "red", "order": 60},
    {"code": "cancelled", "label": "已取消", "color": "slate", "order": 70},
]

COOPERATION_STATUS_CODES = [item["code"] for item in COOPERATION_STATUS_DEFS]
COOPERATION_STATUS_LABELS = {
    item["code"]: item["label"] for item in COOPERATION_STATUS_DEFS
}
COOPERATION_STATUS_LABEL_TO_CODE = {
    item["label"]: item["code"] for item in COOPERATION_STATUS_DEFS
}
COOPERATION_OPEN_CODES = (
    "not_contacted",
    "contacted",
    "negotiating",
    "cooperating",
)
COOPERATION_TERMINAL_CODES = ("completed", "declined", "cancelled")
DEFAULT_COOPERATION_STATUS = "not_contacted"
NEW_COOPERATION_STATUS = "contacted"

# Legacy labels remain readable for imports and manual API writes.
LEGACY_COOPERATION_MAP = {
    "待联系": "not_contacted",
    "未触达": "not_contacted",
    "联系中": "contacted",
    "已触达": "contacted",
    "沟通中": "negotiating",
    "谈判中": "negotiating",
    "已合作": "cooperating",
    "执行中": "cooperating",
    "已完成": "completed",
    "完成": "completed",
    "已结束": "completed",
    "拒绝": "declined",
    "婉拒": "declined",
    "不合作": "declined",
    "取消": "cancelled",
    "已取消": "cancelled",
    "搁置": "cancelled",
    "已搁置": "cancelled",
}


def normalize_cooperation_status(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if text in COOPERATION_STATUS_LABELS:
        return text
    if text in LEGACY_COOPERATION_MAP:
        return LEGACY_COOPERATION_MAP[text]
    return COOPERATION_STATUS_LABEL_TO_CODE.get(text, default)


def cooperation_status_label(value: Any) -> str:
    text = str(value or "")
    normalized = normalize_cooperation_status(text, default="")
    return COOPERATION_STATUS_LABELS.get(normalized, text)
