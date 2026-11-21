from __future__ import annotations

from ..api import DataFlowAPI
from ..client import DataFlowClient
from ..config import Settings
from ..normalize import as_int
from ..storage import unwrap_data


def query_balance(settings: Settings) -> str:
    if not settings.host or not settings.token:
        return "未配置 Host/Token"
    try:
        with DataFlowClient(settings) as client:
            payload = DataFlowAPI(client).balance()
        data = unwrap_data(payload)
        remaining_tokens = as_int(data.get("remaining_calls")) if isinstance(data, dict) else 0
        return f"剩余 {remaining_tokens} token（后台约 10 分钟刷新一次）"
    except Exception as exc:
        return f"查询失败：{exc}"
