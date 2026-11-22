from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_settings
from .db import Database


def _columns(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db._conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _executor_counts(db: Database) -> dict[str, int]:
    creator_rows = 0
    if "executor" in _columns(db, "creator_rounds"):
        creator_rows = db._conn.execute(
            "SELECT COUNT(*) AS n FROM creator_rounds "
            "WHERE TRIM(COALESCE(executor, '')) != ''"
        ).fetchone()["n"]
    lead_rows = 0
    if "executor" in _columns(db, "lead_rounds"):
        lead_rows = db._conn.execute(
            "SELECT COUNT(*) AS n FROM lead_rounds "
            "WHERE TRIM(COALESCE(executor, '')) != ''"
        ).fetchone()["n"]
    raw_rows = db._conn.execute(
        "SELECT COUNT(*) AS n FROM creators WHERE raw_json LIKE '%执行者%'"
    ).fetchone()["n"]
    lead_raw_rows = db._conn.execute(
        "SELECT COUNT(*) AS n FROM leads WHERE raw_json LIKE '%执行者%'"
    ).fetchone()["n"]
    return {
        "creator_rounds": int(creator_rows),
        "lead_rounds": int(lead_rows),
        "creator_raw_json": int(raw_rows),
        "lead_raw_json": int(lead_raw_rows),
    }


def _strip_executor_key(value: Any) -> bool:
    if isinstance(value, dict):
        changed = False
        for key in list(value):
            if key == "执行者":
                del value[key]
                changed = True
            elif _strip_executor_key(value[key]):
                changed = True
        return changed
    if isinstance(value, list):
        changed = False
        for item in value:
            if _strip_executor_key(item):
                changed = True
        return changed
    return False


def _scrub_raw_json(db: Database, table: str) -> int:
    if table == "creators":
        rows = db._conn.execute(
            "SELECT platform, user_id, raw_json FROM creators "
            "WHERE raw_json LIKE '%执行者%'"
        ).fetchall()
        where = "platform = ? AND user_id = ?"
    else:
        rows = db._conn.execute(
            f"SELECT id, raw_json FROM {table} WHERE raw_json LIKE '%执行者%'"
        ).fetchall()
        where = "id = ?"
    scrubbed = 0
    with db._conn:
        for row in rows:
            try:
                payload = json.loads(row["raw_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not _strip_executor_key(payload):
                continue
            if table == "creators":
                params = (json.dumps(payload, ensure_ascii=False), row["platform"], row["user_id"])
            else:
                params = (json.dumps(payload, ensure_ascii=False), row["id"])
            db._conn.execute(
                f"UPDATE {table} SET raw_json = ? WHERE {where}",
                params,
            )
            scrubbed += 1
    return scrubbed


def _backup(db: Database) -> Path:
    path = db.root / f"codata.sqlite.bak-executor-{datetime.now():%Y%m%d-%H%M%S}"
    target = sqlite3.connect(path)
    try:
        db._conn.backup(target)
    finally:
        target.close()
    return path


def cleanup(db: Database, *, apply: bool) -> dict[str, Any]:
    before = _executor_counts(db)
    report: dict[str, Any] = {"before": before, "applied": apply}
    if not apply:
        return report
    if not any(before.values()):
        report["noop"] = True
        report["after"] = before
        return report

    report["backup"] = str(_backup(db))
    with db._conn:
        creator_cleared = 0
        if "executor" in _columns(db, "creator_rounds"):
            creator_cleared = db._conn.execute(
                "UPDATE creator_rounds SET executor = NULL "
                "WHERE TRIM(COALESCE(executor, '')) != ''"
            ).rowcount
        lead_cleared = 0
        if "executor" in _columns(db, "lead_rounds"):
            lead_cleared = db._conn.execute(
                "UPDATE lead_rounds SET executor = NULL "
                "WHERE TRIM(COALESCE(executor, '')) != ''"
            ).rowcount
    report["cleared"] = {
        "creator_rounds": max(0, creator_cleared),
        "lead_rounds": max(0, lead_cleared),
        "creator_raw_json": _scrub_raw_json(db, "creators"),
        "lead_raw_json": _scrub_raw_json(db, "leads"),
    }
    report["search_rows_rebuilt"] = db.rebuild_search_index()
    report["after"] = _executor_counts(db)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="codata-cleanup-executor",
        description="清理旧的 Excel 导入执行者数据（默认 dry-run）",
    )
    parser.add_argument("--apply", action="store_true", help="执行清理并自动备份数据库")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = load_settings(require_credentials=False)
    with Database(settings.output_dir) as db:
        report = cleanup(db, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run：未修改数据库；确认后加 --apply")


if __name__ == "__main__":
    main()
