from __future__ import annotations

from typing import Any

# Canonical review workflow. The database stores codes; labels are display-only.
STATUS_DEFS = [
    {"code": "pending", "label": "待审核", "color": "gray", "order": 10},
    {"code": "candidate", "label": "候选", "color": "blue", "order": 20},
    {"code": "shortlisted", "label": "备选", "color": "amber", "order": 30},
    {"code": "recommended", "label": "已推荐", "color": "green", "order": 40},
    {"code": "contacted", "label": "已联系", "color": "purple", "order": 50},
    {"code": "adopted", "label": "已采用", "color": "green", "order": 60},
    {"code": "rejected", "label": "未采用", "color": "red", "order": 70},
    {"code": "dropped", "label": "已淘汰", "color": "red", "order": 80},
]

STATUS_CODES = [item["code"] for item in STATUS_DEFS]
STATUS_LABELS = {item["code"]: item["label"] for item in STATUS_DEFS}
STATUS_LABEL_TO_CODE = {item["label"]: item["code"] for item in STATUS_DEFS}
DEFAULT_STATUS = "pending"

# Backwards-compatible display labels used by the legacy Streamlit panel.
STATUS_OPTIONS = [item["label"] for item in STATUS_DEFS]


def normalize_status(value: Any, default: str = DEFAULT_STATUS) -> str:
    text = str(value or "").strip()
    if text in STATUS_LABELS:
        return text
    return STATUS_LABEL_TO_CODE.get(text, default)


def status_label(value: Any) -> str:
    text = str(value or "")
    return STATUS_LABELS.get(text, text)
