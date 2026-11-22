from __future__ import annotations

from typing import Any

# Canonical review workflow. The database stores codes; labels are display-only.
STATUS_DEFS = [
    {"code": "pending", "label": "待审核", "color": "gray", "order": 10},
    {"code": "shortlisted", "label": "备选", "color": "amber", "order": 20},
    {"code": "adopted", "label": "已采用", "color": "green", "order": 30},
    {"code": "rejected", "label": "未采用", "color": "red", "order": 40},
]

STATUS_CODES = [item["code"] for item in STATUS_DEFS]
STATUS_LABELS = {item["code"]: item["label"] for item in STATUS_DEFS}
STATUS_LABEL_TO_CODE = {item["label"]: item["code"] for item in STATUS_DEFS}
DEFAULT_STATUS = "pending"

# Legacy values remain readable while old rows/backups are migrated.
LEGACY_STATUS_MAP = {
    "candidate": "shortlisted",
    "recommended": "adopted",
    "contacted": "shortlisted",
    "dropped": "rejected",
    "候选": "shortlisted",
    "已推荐": "adopted",
    "已联系": "shortlisted",
    "已淘汰": "rejected",
}

# Backwards-compatible display labels used by the legacy Streamlit panel.
STATUS_OPTIONS = [item["label"] for item in STATUS_DEFS]


def normalize_status(value: Any, default: str = DEFAULT_STATUS) -> str:
    text = str(value or "").strip()
    if text in STATUS_LABELS:
        return text
    if text in LEGACY_STATUS_MAP:
        return LEGACY_STATUS_MAP[text]
    return STATUS_LABEL_TO_CODE.get(text, default)


def status_label(value: Any) -> str:
    text = str(value or "")
    normalized = normalize_status(text, default="")
    return STATUS_LABELS.get(normalized, text)
