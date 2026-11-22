from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .api import DOUYIN_COST, XHS_COST, DataFlowAPI
from .client import DataFlowClient, DataFlowError
from .config import load_settings
from .db import Database
from .normalize import (
    as_int,
    normalize_search_item,
    normalize_tier_label,
    normalize_user_info,
    split_tags,
    utc_now,
)
from .panel.queries import PLATFORM_LABELS
from .pipeline import Pipeline
from .statuses import normalize_status
from .storage import Store, as_cursor, as_items, unwrap_data, unwrap_item


LABEL_TO_PLATFORM = {label: code for code, label in PLATFORM_LABELS.items()}


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def _cell_int(value: Any) -> int | None:
    text = _cell_text(value)
    if not text:
        return None
    return as_int(text)


def _out(text: str = "") -> None:
    print(text)


def _dump(payload: Any, limit: int = 4000) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) > limit:
        text = text[:limit] + f"\n... (截断，共 {len(text)} 字符，完整内容见本次原始响应文件)"
    _out(text)


class Runner:
    def __init__(self, api: DataFlowAPI, store: Store) -> None:
        self.api = api
        self.store = store

    def call(
        self,
        platform: str,
        kind: str,
        fn: Callable[[], Any],
        params: dict[str, Any] | None = None,
        save_items: bool = True,
        item_kind: str | None = None,
    ) -> Any:
        payload = fn()
        raw_file = self.store.save_raw(f"{platform}_{kind}", payload)
        code = payload.get("code") if isinstance(payload, dict) else None
        self.store.log_call(platform, kind, params, raw_file, code)

        code_desc = "成功" if code in (0, None) else f"code={code}"
        _out(f"[{platform}/{kind}] {code_desc}，原始响应: {raw_file.relative_to(raw_file.parents[2])}")
        items = as_items(payload)
        if save_items and items:
            count = self.store.save_items(platform, item_kind or kind, items)
            _out(f"[{platform}/{kind}] 入库 {count} 条")
            _print_items(items)
        elif not items:
            _dump(payload)
        cursor = as_cursor(payload)
        if cursor:
            _out(f"[{platform}/{kind}] 下一页 cursor: {cursor}")
        return payload


def _print_items(items: list[dict[str, Any]], limit: int = 10) -> None:
    for raw in items[:limit]:
        item = unwrap_item(raw)
        title = (
            item.get("title")
            or item.get("display_title")
            or item.get("displayTitle")
            or item.get("desc")
            or item.get("content")
            or item.get("text")
            or ""
        )
        title = str(title).replace("\n", " ")[:60]
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        nickname = user.get("nickname") or user.get("nick_name") or user.get("nickName") or ""
        _out(f"  - {title}  {nickname}".rstrip())
    if len(items) > limit:
        _out(f"  ... 还有 {len(items) - limit} 条")


def cmd_balance(runner: Runner, args: argparse.Namespace) -> None:
    runner.call("account", "balance", runner.api.balance, save_items=False)


def cmd_xhs_search(runner: Runner, args: argparse.Namespace) -> None:
    runner.call(
        "xhs",
        "search",
        lambda: runner.api.xhs_search(args.keyword, args.page, args.sort_type, args.note_type, args.note_time),
        {"keyword": args.keyword, "page": args.page},
        item_kind="note",
    )


def cmd_xhs_note(runner: Runner, args: argparse.Namespace) -> None:
    if args.full:
        fn = lambda: runner.api.xhs_note_full(args.note_id)
        kind = "note_full"
    elif args.video:
        fn = lambda: runner.api.xhs_note_video(args.note_id)
        kind = "note_video"
    else:
        fn = lambda: runner.api.xhs_note(args.note_id, args.channel)
        kind = "note"
    runner.call("xhs", kind, fn, {"noteId": args.note_id}, item_kind="note")


def cmd_xhs_comments(runner: Runner, args: argparse.Namespace) -> None:
    payload = runner.call(
        "xhs",
        "comments",
        lambda: runner.api.xhs_comments(args.note_id, args.cursor, args.sort_strategy),
        {"noteId": args.note_id, "cursor": args.cursor},
        item_kind="comment",
    )
    if args.sub:
        for item in as_items(payload):
            comment_id = item.get("comment_id") or item.get("commentId") or item.get("id")
            if not comment_id:
                continue
            runner.call(
                "xhs",
                "sub_comments",
                lambda comment_id=comment_id: runner.api.xhs_sub_comments(args.note_id, str(comment_id)),
                {"noteId": args.note_id, "commentId": str(comment_id)},
                item_kind="comment",
            )


def cmd_xhs_user(runner: Runner, args: argparse.Namespace) -> None:
    runner.call("xhs", "user", lambda: runner.api.xhs_user(args.user_id), {"userId": args.user_id}, item_kind="user")


def cmd_xhs_posts(runner: Runner, args: argparse.Namespace) -> None:
    runner.call(
        "xhs",
        "user_posted",
        lambda: runner.api.xhs_user_posted(args.user_id, args.cursor),
        {"userId": args.user_id, "cursor": args.cursor},
        item_kind="note",
    )


def cmd_xhs_topic(runner: Runner, args: argparse.Namespace) -> None:
    runner.call(
        "xhs",
        "topic_feed",
        lambda: runner.api.xhs_topic_feed(args.page_id, args.sort, args.cursor),
        {"page_id": args.page_id, "cursor": args.cursor},
        item_kind="note",
    )


def cmd_dy_video(runner: Runner, args: argparse.Namespace) -> None:
    runner.call("douyin", "video", lambda: runner.api.douyin_video(args.aweme_id), {"awemeId": args.aweme_id})


def cmd_dy_comments(runner: Runner, args: argparse.Namespace) -> None:
    runner.call(
        "douyin",
        "comments",
        lambda: runner.api.douyin_comments(args.aweme_id, args.cursor),
        {"awemeId": args.aweme_id, "cursor": args.cursor},
        item_kind="comment",
    )


def cmd_dy_sub_comments(runner: Runner, args: argparse.Namespace) -> None:
    runner.call(
        "douyin",
        "sub_comments",
        lambda: runner.api.douyin_sub_comments(args.aweme_id, args.comment_id, args.cursor),
        {"awemeId": args.aweme_id, "commentId": args.comment_id, "cursor": args.cursor},
        item_kind="comment",
    )


def cmd_crawl(runner: Runner, args: argparse.Namespace) -> None:
    settings = runner.api.client.settings
    calls = args.pages + args.max_notes + (args.max_notes if args.with_comments else 0)
    estimate = calls * XHS_COST
    if not args.yes:
        _out(f"将发起约 {calls} 次请求，预计消耗约 {estimate} token（单价 {XHS_COST}/次）。加 --yes 跳过确认。")
        return

    seen: set[str] = set()
    note_ids: list[str] = []
    for page in range(1, args.pages + 1):
        payload = runner.call(
            "xhs",
            "search",
            lambda page=page: runner.api.xhs_search(args.keyword, page),
            {"keyword": args.keyword, "page": page},
            item_kind="note",
        )
        for item in as_items(payload):
            note = unwrap_item(item)
            note_id = note.get("note_id") or note.get("noteId") or note.get("id")
            if note_id and str(note_id) not in seen:
                seen.add(str(note_id))
                note_ids.append(str(note_id))

    time.sleep(settings.delay)
    for note_id in note_ids[: args.max_notes]:
        runner.call("xhs", "note", lambda note_id=note_id: runner.api.xhs_note(note_id), {"noteId": note_id}, item_kind="note")
        if args.with_comments:
            runner.call(
                "xhs",
                "comments",
                lambda note_id=note_id: runner.api.xhs_comments(note_id),
                {"noteId": note_id},
                item_kind="comment",
            )
        time.sleep(settings.delay)

    _out(f"完成。累计花费 token: {runner.api.client.cost}")


def _print_stats(settings: Any) -> None:
    from .config import Settings

    assert isinstance(settings, Settings)
    store = Store(settings.output_dir)
    db = Database(settings.output_dir)
    try:
        _out(f"records: {store.count()}")
        _out(f"xhs notes: {store.count('xhs', 'note')}")
        _out(f"comments: {store.count(kind='comment')}")
        counts = db.counts()
        _out(f"rounds: {counts['rounds']}")
        _out(f"keywords: {counts['keywords']}")
        _out(f"notes(rounds): {counts['notes']}")
        _out(f"creators: {counts['creators']}")
        _out(f"creator_rounds: {counts['creator_rounds']}")
    finally:
        db.close()
        store.close()


def cmd_round_create(settings: Any, args: argparse.Namespace) -> None:
    if args.dry_run:
        estimate = Pipeline.estimate(len(args.keyword), args.pages, args.max_creators or None)
        _out(f"将创建轮次，关键词 {len(args.keyword)} 个，每词 {args.pages} 页")
        _out(f"预估最多 {estimate['total_calls']} 次调用，约 {estimate['tokens']} token")
        return
    categories = {word: args.category for word in args.keyword} if args.category else None
    with Database(settings.output_dir) as db:
        round_id = db.create_round(
            args.keyword,
            pages=args.pages,
            max_creators=args.max_creators,
            name=args.name,
            categories=categories,
        )
        row = db.get_round(round_id)
        keywords = db.get_round_keywords(round_id)
        estimate = Pipeline.estimate(len(keywords), args.pages, args.max_creators or None)
    _out(f"已创建轮次 #{round_id} {row['name']}（关键词 {len(keywords)} 个，每词 {args.pages} 页）")
    _out(f"预估最多 {estimate['total_calls']} 次调用，约 {estimate['tokens']} token")
    _out(f"运行：uv run codata round run {round_id}")


def cmd_round_list(settings: Any, args: argparse.Namespace) -> None:
    with Database(settings.output_dir) as db:
        rounds = db.list_rounds()
    if not rounds:
        _out("暂无轮次")
        return
    _out(f"{'ID':>4}  {'名称':<16} {'状态':<8} {'关键词':<24} {'花费':>6}  创建时间")
    for row in rounds:
        _out(
            f"{row['id']:>4}  {row['name']:<16} {row['status']:<8} "
            f"{(row['keywords'] or ''):<24} {row['cost_tokens'] or 0:>6}  {row['created_at']}"
        )


def cmd_round_delete(settings: Any, args: argparse.Namespace) -> None:
    if args.dry_run:
        _out(f"将删除轮次 #{args.round_id}（dry-run 不写库）")
        return
    if not args.yes:
        _out("删除轮次会移除该轮达人、笔记关联、任务记录；确认请加 --yes")
        return
    with Database(settings.output_dir) as db:
        try:
            result = db.delete_round(args.round_id)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    _out(
        f"已删除轮次 #{result['id']} {result['name']}："
        f"达人行 {result['creator_rounds']}、笔记关联 {result['note_hits']}，"
        f"清理孤立达人 {result['orphan_creators']} 个、笔记 {result['orphan_notes']} 条"
    )


def cmd_round_run(settings: Any, args: argparse.Namespace) -> None:
    with Database(settings.output_dir) as db:
        pipeline = Pipeline(settings, db)
        try:
            summary = pipeline.run_round(
                args.round_id,
                refresh_users=args.refresh_users,
                force=args.force,
            )
        except DataFlowError as exc:
            _out(f"请求失败: {exc}")
            raise SystemExit(2) from exc
        except Exception as exc:
            _out(f"采集失败: {exc}")
            raise SystemExit(2) from exc
    _out(
        f"轮次 #{summary['round_id']} 完成：笔记 {summary['notes']} 条，"
        f"作者 {summary['authors']} 个，补全 {summary['enriched']} 个，"
        f"花费 {summary['cost_tokens']} token"
    )


def cmd_import_raw(settings: Any, args: argparse.Namespace) -> None:
    if args.dry_run:
        _out(f"将导入 {len(args.files)} 个文件到轮次，关键词：{args.keyword}（dry-run 不写库）")
        return
    with Database(settings.output_dir) as db:
        if args.round_id:
            round_id = args.round_id
            if not db.get_round(round_id):
                raise SystemExit(f"轮次不存在: {round_id}")
        else:
            categories = {args.keyword: args.category} if args.category else None
            round_id = db.create_round(
                [args.keyword],
                pages=1,
                max_creators=None,
                name=args.round_name,
                categories=categories,
            )

        imported_notes = 0
        imported_users = 0
        for file_name in args.files:
            path = Path(file_name)
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = unwrap_data(payload)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                for rank, item in enumerate(data["items"], 1):
                    note, author = normalize_search_item(item)
                    if not note.get("note_id"):
                        continue
                    if author:
                        db.upsert_creator_basic(author)
                    db.upsert_note(note)
                    if note.get("author_id"):
                        db.add_note_hit(round_id, note["platform"], note["note_id"], args.keyword, 1, rank)
                    imported_notes += 1
                _out(f"导入搜索响应: {path.name} -> {len(data['items'])} 条")
            elif isinstance(data, dict) and (data.get("userid") or data.get("user_id")):
                creator = normalize_user_info(payload)
                if creator.get("user_id"):
                    db.upsert_creator(creator)
                    imported_users += 1
                _out(f"导入用户信息: {path.name} -> {creator.get('nickname') or creator.get('user_id')}")
            else:
                _out(f"跳过无法识别的文件: {path.name}")

        creators = db.refresh_creator_rounds(round_id)
        db.update_round(round_id, status="done", finished_at=utc_now(), error=None)
        row = db.get_round(round_id)
        _out(
            f"导入完成：轮次 #{round_id} {row['name']}，笔记 {imported_notes} 条，"
            f"用户 {imported_users} 个，达人 {creators} 个"
        )


def cmd_import_excel(settings: Any, args: argparse.Namespace) -> None:
    import pandas as pd

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")
    if args.dry_run:
        _out(f"将导入 Excel: {path}（dry-run 不写库）")
        return

    sheets = pd.read_excel(path, sheet_name=None)
    skip_sheets = {"平台核对", "说明"}
    generated_at = ""
    notes_sheet = sheets.get("说明")
    if notes_sheet is not None:
        for _, row in notes_sheet.iterrows():
            if _cell_text(row.iloc[0]) == "生成时间":
                generated_at = _cell_text(row.iloc[1])
                break
    keyword = args.keyword or "护肤"
    category = args.category or keyword
    base_name = args.round_name or f"{path.stem}-导入"

    with Database(settings.output_dir) as db:
        existing_names = {row["name"] for row in db.list_rounds()}
        round_name = base_name
        suffix = 2
        while round_name in existing_names:
            round_name = f"{base_name}-{suffix}"
            suffix += 1
        round_id = db.create_round(
            [keyword],
            pages=1,
            max_creators=None,
            name=round_name,
            categories={keyword: category},
        )

        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        with_id = 0
        enriched = 0
        for sheet_name, frame in sheets.items():
            if sheet_name in skip_sheets:
                continue
            platform = LABEL_TO_PLATFORM.get(sheet_name, "other")
            for index, row in frame.iterrows():
                nickname = _cell_text(row.get("昵称"))
                if not nickname:
                    continue
                excel_row = int(index) + 2
                account_id = _cell_text(row.get("账号ID"))
                profile_url = _cell_text(row.get("主页URL"))
                raw_title = _cell_text(row.get("原始标题"))
                fans = _cell_int(row.get("粉丝数"))
                posted = _cell_int(row.get("笔记数"))
                liked = _cell_int(row.get("总点赞"))
                interaction = _cell_int(row.get("互动量"))
                tier_label = normalize_tier_label(fans)
                data_round = _cell_text(row.get("数据轮次"))
                status = normalize_status(_cell_text(row.get("采用状态")))
                direction = _cell_text(row.get("匹配方向"))
                tags = _cell_text(row.get("标签"))
                source = _cell_text(row.get("数据来源")) or "护肤达人池"
                cooperation_platform = _cell_text(row.get("合作主平台"))
                top_title = _cell_text(row.get("代表作"))

                if account_id:
                    user_id = account_id
                    if user_id in seen:
                        user_id = f"{account_id}~{sheet_name}-{excel_row}"
                    with_id += 1
                else:
                    digest = hashlib.sha1(
                        f"{sheet_name}|{excel_row}|{nickname}|{raw_title}|{profile_url}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:20]
                    user_id = f"import:{digest}"
                    while user_id in seen:
                        user_id = f"import:{digest}-{excel_row}"
                seen.add(user_id)
                if fans is not None:
                    enriched += 1

                sanitized = {}
                for key, value in row.items():
                    if isinstance(value, float) and math.isnan(value):
                        sanitized[str(key)] = None
                    else:
                        sanitized[str(key)] = value
                raw_json = json.dumps(sanitized, ensure_ascii=False, default=str)

                top_note_id = None
                note = None
                if top_title:
                    note_digest = hashlib.sha1(
                        f"{platform}|{user_id}|{top_title}".encode("utf-8")
                    ).hexdigest()[:20]
                    top_note_id = f"import:{note_digest}"
                    note = {
                        "platform": platform,
                        "note_id": top_note_id,
                        "author_id": user_id,
                        "title": top_title,
                        "desc": "",
                        "note_type": "import",
                        "published_at": None,
                        "liked": 0,
                        "collected": 0,
                        "comments": 0,
                        "shares": 0,
                        "url": "",
                        "hashtags": "[]",
                        "raw_json": "{}",
                    }

                items.append(
                    {
                        "creator": {
                            "platform": platform,
                            "user_id": user_id,
                            "red_id": "",
                            "nickname": nickname,
                            "avatar": "",
                            "fans": fans,
                            "follows": None,
                            "liked": liked,
                            "collected": None,
                            "posted": posted,
                            "interaction": interaction,
                            "desc": "",
                            "tags_json": json.dumps(split_tags(tags), ensure_ascii=False),
                            "ip_location": "",
                            "verify_info": "",
                            "profile_url": profile_url,
                            "fetched_at": (
                                f"{generated_at}T00:00:00+00:00"
                                if fans is not None and generated_at
                                else (utc_now() if fans is not None else None)
                            ),
                            "raw_json": raw_json,
                        },
                        "note": note,
                        "round": {
                            "platform": platform,
                            "user_id": user_id,
                            "source_keywords": json.dumps([source], ensure_ascii=False),
                            "sample_notes": 0,
                            "sample_likes": 0,
                            "sample_collected": 0,
                            "sample_comments": 0,
                            "sample_interactions": 0,
                            "top_note_id": top_note_id,
                            "auto_tags": "[]",
                            "status": status,
                            "match_direction": direction,
                            "tags_manual": tags,
                            "remark": "",
                            "data_round": data_round,
                            "tier_label": tier_label,
                            "account_id": account_id,
                            "cooperation_platform": cooperation_platform,
                            "raw_title": raw_title,
                        },
                    }
                )

        db.import_creator_rows(round_id, items)
        db.update_round(round_id, status="done", finished_at=utc_now(), error=None)
        _out(
            f"Excel 导入完成：轮次 #{round_id} {round_name}，共 {len(items)} 条；"
            f"有账号ID {with_id} 条，有粉丝数据 {enriched} 条"
        )
        _out("打开面板：uv run codata-panel")


def cmd_panel(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "codata.api_server:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8501",
    ]
    raise SystemExit(subprocess.call(command))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codata", description="DataFlow 小红书/抖音数据采集客户端")
    parser.add_argument("--dry-run", action="store_true", help="只校验配置，不发请求")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("balance", help="查询剩余 token").set_defaults(func=cmd_balance)
    sub.add_parser("stats", help="查看本地入库统计")
    sub.add_parser("panel", help="启动本地面板")

    round_parser = sub.add_parser("round", help="关键词轮次采集")
    round_sub = round_parser.add_subparsers(dest="action", required=True)

    round_create = round_sub.add_parser("create", help="创建轮次")
    round_create.add_argument("--keyword", action="append", required=True, help="关键词，可重复")
    round_create.add_argument("--category", default=None, help="新关键词的垂类")
    round_create.add_argument("--pages", type=int, default=1, help="每个关键词页数（默认 1）")
    round_create.add_argument("--max-creators", type=int, default=20, help="最多补全多少个达人（0=全部）")
    round_create.add_argument("--name", default=None, help="轮次名称，不填自动生成")

    round_sub.add_parser("list", help="查看轮次")

    round_delete = round_sub.add_parser("delete", help="删除轮次及其达人/笔记关联")
    round_delete.add_argument("round_id", type=int)
    round_delete.add_argument("--yes", action="store_true", help="确认删除")

    round_run = round_sub.add_parser("run", help="运行轮次")
    round_run.add_argument("round_id", type=int)
    round_run.add_argument("--refresh-users", action="store_true", help="强制刷新已缓存达人资料")
    round_run.add_argument("--force", action="store_true", help="跳过余额不足保护")

    import_raw = sub.add_parser("import-raw", help="从已保存的原始 JSON 导入（不消耗 token）")
    import_raw.add_argument("files", nargs="+")
    import_raw.add_argument("--keyword", default="导入", help="搜索关键词")
    import_raw.add_argument("--category", default=None)
    import_raw.add_argument("--round-name", default=None)
    import_raw.add_argument("--round-id", type=int, default=None, help="导入到已有轮次")

    import_excel = sub.add_parser("import-excel", help="导入 Excel 达人池（不消耗 token）")
    import_excel.add_argument("file")
    import_excel.add_argument("--round-name", default=None)
    import_excel.add_argument("--keyword", default="护肤")
    import_excel.add_argument("--category", default=None)

    crawl = sub.add_parser("crawl", help="关键词搜索 → 详情（→ 评论）流水线")
    crawl.add_argument("keyword", help="搜索关键词")
    crawl.add_argument("--pages", type=int, default=1, help="搜索页数（默认 1）")
    crawl.add_argument("--max-notes", type=int, default=5, help="最多抓取多少篇笔记详情（默认 5）")
    crawl.add_argument("--with-comments", action="store_true", help="同时抓取每篇笔记的一级评论")
    crawl.add_argument("--yes", action="store_true", help="跳过 token 预估确认")
    crawl.set_defaults(func=cmd_crawl)

    xhs = sub.add_parser("xhs", help="小红书接口")
    xhs_sub = xhs.add_subparsers(dest="action", required=True)

    search = xhs_sub.add_parser("search", help="/api/xhs/v3/appsearch")
    search.add_argument("keyword")
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--sort-type", default=None)
    search.add_argument("--note-type", default=None)
    search.add_argument("--note-time", default=None)
    search.set_defaults(func=cmd_xhs_search)

    note = xhs_sub.add_parser("note", help="/api/xhs/v3/notedetail")
    note.add_argument("note_id")
    note.add_argument("--channel", choices=["old", "new"], default="old", help="new=notedetailv2")
    note.add_argument("--full", action="store_true", help="/api/xhs/v3/fullnotedetail")
    note.add_argument("--video", action="store_true", help="/api/xhs/v3/videonotedetail")
    note.set_defaults(func=cmd_xhs_note)

    comments = xhs_sub.add_parser("comments", help="/api/xhs/v3/getcomment")
    comments.add_argument("note_id")
    comments.add_argument("--cursor", default=None)
    comments.add_argument("--sort-strategy", default=None)
    comments.add_argument("--sub", action="store_true", help="同时抓取二级评论")
    comments.set_defaults(func=cmd_xhs_comments)

    user = xhs_sub.add_parser("user", help="/api/xhs/v3/appuserinfo")
    user.add_argument("user_id")
    user.set_defaults(func=cmd_xhs_user)

    posts = xhs_sub.add_parser("posts", help="/api/xhs/v3/appuserposted")
    posts.add_argument("user_id")
    posts.add_argument("--cursor", default=None)
    posts.set_defaults(func=cmd_xhs_posts)

    topic = xhs_sub.add_parser("topic", help="/api/xhs/v3/topicfeed")
    topic.add_argument("page_id")
    topic.add_argument("--sort", default=None)
    topic.add_argument("--cursor", default=None)
    topic.set_defaults(func=cmd_xhs_topic)

    dy = sub.add_parser("douyin", help="抖音接口")
    dy_sub = dy.add_subparsers(dest="action", required=True)

    video = dy_sub.add_parser("video", help="/api/douyin/videodetail")
    video.add_argument("aweme_id")
    video.set_defaults(func=cmd_dy_video)

    dy_comments = dy_sub.add_parser("comments", help="/api/douyin/comment")
    dy_comments.add_argument("aweme_id")
    dy_comments.add_argument("--cursor", default=None)
    dy_comments.set_defaults(func=cmd_dy_comments)

    dy_sub_comments = dy_sub.add_parser("sub-comments", help="/api/douyin/subcomment")
    dy_sub_comments.add_argument("aweme_id")
    dy_sub_comments.add_argument("comment_id")
    dy_sub_comments.add_argument("--cursor", default=None)
    dy_sub_comments.set_defaults(func=cmd_dy_sub_comments)

    return parser


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    settings = load_settings(require_credentials=False)

    if args.command == "stats":
        _print_stats(settings)
        return
    if args.command == "panel":
        cmd_panel(args)
        return
    if args.command == "round" and args.action == "create":
        cmd_round_create(settings, args)
        return
    if args.command == "round" and args.action == "list":
        cmd_round_list(settings, args)
        return
    if args.command == "round" and args.action == "delete":
        cmd_round_delete(settings, args)
        return
    if args.command == "import-raw":
        cmd_import_raw(settings, args)
        return
    if args.command == "import-excel":
        cmd_import_excel(settings, args)
        return

    if args.dry_run:
        _out(f"Host: {settings.host or '(未配置)'}")
        _out(f"Token: {'已配置' if settings.token else '(未配置)'}")
        _out(f"输出目录: {settings.output_dir}")
        return

    settings = load_settings(require_credentials=True)
    if args.command == "round" and args.action == "run":
        cmd_round_run(settings, args)
        return

    store = Store(settings.output_dir)
    client = DataFlowClient(settings)
    try:
        runner = Runner(DataFlowAPI(client), store)
        args.func(runner, args)
    except DataFlowError as exc:
        message = str(exc)
        if exc.code == 1001:
            message += "（余额不足，请在 dataflow 后台充值或订阅后再试；小红书单次 45 token、抖音单次 90 token）"
        print(f"请求失败: {message}", file=sys.stderr)
        raise SystemExit(2)
    finally:
        print(f"本次累计消费 token: {client.cost}", file=sys.stderr)
        client.close()
        store.close()


if __name__ == "__main__":
    main()
