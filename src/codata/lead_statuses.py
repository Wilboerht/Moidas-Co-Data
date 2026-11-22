from __future__ import annotations

from typing import Any

LEAD_STATUS_DEFS = [
    {"code": "unverified", "label": "待核实", "color": "gray", "order": 10},
    {"code": "verifying", "label": "核实中", "color": "blue", "order": 20},
    {"code": "converted", "label": "已转达人", "color": "green", "order": 30},
    {"code": "duplicate", "label": "重复", "color": "amber", "order": 40},
    {"code": "discarded", "label": "已废弃", "color": "red", "order": 50},
]

LEAD_STATUS_CODES = [item["code"] for item in LEAD_STATUS_DEFS]
LEAD_STATUS_LABELS = {item["code"]: item["label"] for item in LEAD_STATUS_DEFS}
LEAD_STATUS_LABEL_TO_CODE = {item["label"]: item["code"] for item in LEAD_STATUS_DEFS}
DEFAULT_LEAD_STATUS = "unverified"

LEAD_SOURCE_DEFS = [
    {"code": "excel_import", "label": "Excel 导入"},
    {"code": "manual", "label": "手动录入"},
    {"code": "customer", "label": "客户提供"},
    {"code": "crawler", "label": "采集发现"},
    {"code": "other", "label": "其他"},
]
LEAD_SOURCE_CODES = [item["code"] for item in LEAD_SOURCE_DEFS]
LEAD_SOURCE_LABELS = {item["code"]: item["label"] for item in LEAD_SOURCE_DEFS}
DEFAULT_LEAD_SOURCE = "manual"


def normalize_lead_status(value: Any, default: str = DEFAULT_LEAD_STATUS) -> str:
    text = str(value or "").strip()
    if text in LEAD_STATUS_LABELS:
        return text
    return LEAD_STATUS_LABEL_TO_CODE.get(text, default)


def normalize_lead_source(value: Any, default: str = DEFAULT_LEAD_SOURCE) -> str:
    text = str(value or "").strip()
    return text if text in LEAD_SOURCE_CODES else default
