from __future__ import annotations

import json
from io import BytesIO
from typing import Any

import pandas as pd

from ..db import Database
from ..normalize import format_fans_wan, normalize_tier_label, split_tags
from ..statuses import STATUS_LABELS

PLATFORM_LABELS = {
    "xhs": "小红书",
    "weibo": "微博",
    "taobao": "淘宝逛逛",
    "bilibili": "B站",
    "douyin": "抖音",
    "instagram": "Instagram",
    "other": "其它",
    "kuaishou": "快手",
    "wechat": "公众号",
    "weibo_live": "微博直播",
    "multi": "多平台",
    "zhihu": "知乎",
    "unknown": "未填平台",
}
ROUND_STATUS_LABELS = {
    "pending": "待运行",
    "running": "运行中",
    "done": "已完成",
    "failed": "失败",
}
JOB_STATUS_LABELS = {
    "pending": "排队中",
    "running": "运行中",
    "done": "已完成",
    "failed": "失败",
}

REQUESTED_COLUMNS = [
    "昵称",
    "粉丝数",
    "粉丝数(万)",
    "粉丝层级",
    "数据轮次",
    "采用状态",
    "匹配方向",
    "笔记数",
    "总点赞",
    "互动量",
    "标签",
    "主页URL",
    "账号ID",
    "代表作",
    "数据来源",
    "平台",
]

EXTRA_COLUMNS = [
    "备注",
    "执行者",
    "合作主平台",
    "原始标题",
    "样本笔记",
    "样本点赞",
    "样本互动",
]

ALL_COLUMNS = REQUESTED_COLUMNS + EXTRA_COLUMNS

EXPORT_COLUMNS = ALL_COLUMNS

HIDDEN_COLUMNS = ["_key", "_platform", "_user_id", "_top_url"]


def status_label(value: Any, job: bool = False) -> str:
    labels = JOB_STATUS_LABELS if job else ROUND_STATUS_LABELS
    return labels.get(str(value or ""), str(value or ""))


def format_max_creators(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "全部"
    return str(int(value))


def format_display(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    for column in ("粉丝数", "笔记数", "总点赞", "互动量"):
        if column in display.columns:
            display[column] = display[column].map(
                lambda value: "—" if pd.isna(value) else f"{int(value):,}"
            )
    for column in ("粉丝数(万)", "粉丝层级"):
        if column in display.columns:
            display[column] = display[column].map(
                lambda value: value if str(value).strip() else "—"
            )
    for column in ("样本笔记", "样本点赞", "样本互动"):
        if column in display.columns:
            display[column] = display[column].map(
                lambda value: "0" if pd.isna(value) else f"{int(value):,}"
            )
    if "代表作" in display.columns:
        display["代表作"] = display["代表作"].map(lambda value: _shorten(value, 16))
    return display


def sort_rows(df: pd.DataFrame, sort_key: str) -> pd.DataFrame:
    if sort_key == "粉丝数 ↓":
        return df.sort_values("粉丝数", ascending=False, na_position="last")
    if sort_key == "粉丝数 ↑":
        return df.sort_values("粉丝数", ascending=True, na_position="last")
    if sort_key == "互动量 ↓":
        return df.sort_values("互动量", ascending=False, na_position="last")
    if sort_key == "昵称 A→Z":
        return df.sort_values("昵称", ascending=True)
    return df.sort_values("样本点赞", ascending=False, na_position="last")


def _loads(value: Any, default: list[Any]) -> list[Any]:
    if not value:
        return default
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return loaded if isinstance(loaded, list) else default


def _shorten(value: Any, limit: int = 48) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def creator_rows(db: Database, round_id: int) -> pd.DataFrame:
    rows = []
    for row in db.creator_table(round_id):
        keywords = _loads(row.get("source_keywords"), [])
        auto_tags = _loads(row.get("auto_tags"), [])
        tags = []
        for tag in [*auto_tags, *split_tags(row.get("tags_manual"))]:
            tag = str(tag).strip()
            if tag and tag not in tags:
                tags.append(tag)

        top_title = str(row.get("top_title") or "").strip().replace("\n", " ")
        top_liked = row.get("top_liked")
        if top_title and top_liked:
            top_title = f"{top_title}（{int(top_liked)} 赞）"

        fans = row.get("fans")
        user_id = row["user_id"]
        account_id = row.get("account_id")
        if account_id is None:
            account_id = "" if str(user_id).startswith("import:") else user_id
        tier_label = normalize_tier_label(fans, row.get("tier_label"))
        data_round = row.get("data_round") or row.get("round_name") or ""
        rows.append(
            {
                "_key": f"{row['platform']}|{user_id}",
                "_platform": row["platform"],
                "_user_id": user_id,
                "_top_url": row.get("top_url") or "",
                "昵称": row.get("nickname") or "(未补全)",
                "粉丝数": fans,
                "粉丝数(万)": format_fans_wan(fans) if fans is not None else "",
                "粉丝层级": tier_label,
                "数据轮次": data_round,
                "采用状态": STATUS_LABELS.get(row.get("status"), row.get("status") or "待审核"),
                "匹配方向": row.get("match_direction") or "",
                "备注": row.get("remark") or "",
                "笔记数": row.get("posted"),
                "总点赞": row.get("liked"),
                "互动量": row.get("interaction"),
                "标签": "、".join(tags),
                "主页URL": row.get("profile_url") or "",
                "账号ID": account_id,
                "代表作": top_title,
                "数据来源": "、".join(str(word) for word in keywords),
                "平台": PLATFORM_LABELS.get(row["platform"], row["platform"]),
                "执行者": row.get("executor") or "",
                "合作主平台": row.get("cooperation_platform") or "",
                "原始标题": row.get("raw_title") or "",
                "样本笔记": row.get("sample_notes") or 0,
                "样本点赞": row.get("sample_likes") or 0,
                "样本互动": row.get("sample_interactions") or 0,
            }
        )
    return pd.DataFrame(rows)


def filter_rows(
    df: pd.DataFrame,
    tiers: list[str],
    statuses: list[str],
    directions: list[str],
    keywords: list[str],
    search: str,
    min_fans: int | None,
    max_fans: int | None,
    only_unenriched: bool = False,
) -> pd.DataFrame:
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if tiers:
        mask &= df["粉丝层级"].isin(tiers)
    if statuses:
        mask &= df["采用状态"].isin(statuses)
    if directions:
        mask &= df["匹配方向"].isin(directions)
    if keywords:
        mask &= df["数据来源"].apply(
            lambda value: any(word in str(value).split("、") for word in keywords)
        )
    if search:
        needle = search.strip().lower()
        mask &= df.apply(
            lambda row: needle in str(row["昵称"]).lower() or needle in str(row["账号ID"]).lower(),
            axis=1,
        )
    if only_unenriched:
        mask &= df["粉丝数"].isna()
    if min_fans is not None:
        mask &= df["粉丝数"].fillna(-1) >= min_fans
    if max_fans is not None:
        mask &= df["粉丝数"].fillna(-1) <= max_fans
    return df[mask]


def to_csv_bytes(df: pd.DataFrame, columns: list[str]) -> bytes:
    return df[columns].to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(df: pd.DataFrame, columns: list[str], sheet_name: str = "达人库") -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df[columns].to_excel(writer, index=False, sheet_name=sheet_name)
        sheet = writer.sheets[sheet_name]
        sheet.freeze_panes = "A2"
        for index, column in enumerate(df[columns].columns, 1):
            values = df[column].astype(str) if column in df.columns else pd.Series(dtype=str)
            width = min(max(10, int(values.str.len().max() or 10) + 2), 50)
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    return buffer.getvalue()
