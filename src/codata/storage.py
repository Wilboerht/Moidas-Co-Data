from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unwrap_data(payload: Any) -> Any:
    """Unwrap DataFlow envelopes, including nested JSON strings in ``data``."""
    value = payload
    for _ in range(6):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                break
            continue
        if not isinstance(value, dict):
            break
        has_items = any(isinstance(value.get(key), list) for key in ("items", "notes", "comments", "note_list", "list", "records"))
        if has_items:
            break
        if "data" in value and isinstance(value.get("data"), (str, dict, list)):
            value = value["data"]
            continue
        break
    return value


def unwrap_item(item: Any) -> dict[str, Any]:
    """Return the actual note/comment object from a wrapped search item."""
    if not isinstance(item, dict):
        return {}
    for key in ("note", "note_card", "noteCard"):
        nested = item.get(key)
        if isinstance(nested, dict):
            return nested
    return item


def _items_from(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 6:
        return []
    value = unwrap_data(value)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "notes", "comments", "note_list", "list", "records", "users", "data"):
            if key in value:
                found = _items_from(value[key], depth + 1)
                if found:
                    return found
    return []


def as_items(payload: Any) -> list[dict[str, Any]]:
    return _items_from(payload)


def as_cursor(payload: Any) -> str | None:
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        return None
    for key in ("cursor", "next_cursor", "nextCursor", "end_cursor"):
        value = data.get(key)
        if value not in (None, "", 0):
            return str(value)
    if data.get("has_more") in (True, 1, "1", "true"):
        return data.get("cursor")
    return None


def item_id(item: dict[str, Any]) -> str | None:
    item = unwrap_item(item)
    for key in (
        "note_id",
        "noteId",
        "aweme_id",
        "awemeId",
        "comment_id",
        "commentId",
        "userid",
        "user_id",
        "userId",
        "id",
    ):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    for key in ("note_card", "noteCard", "note"):
        nested = item.get(key)
        if isinstance(nested, dict):
            value = item_id(nested)
            if value:
                return value
    return None


def item_title(item: dict[str, Any]) -> str:
    item = unwrap_item(item)
    for key in ("title", "display_title", "displayTitle", "desc", "content", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def item_author(item: dict[str, Any]) -> str:
    item = unwrap_item(item)
    for key in ("user", "author", "user_info", "userInfo"):
        nested = item.get(key)
        if isinstance(nested, dict):
            for name_key in ("nickname", "nick_name", "nickName", "name", "user_name"):
                value = nested.get(name_key)
                if isinstance(value, str) and value:
                    return value
    for key in ("nickname", "nick_name", "author_name"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw_dir = root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(root / "codata.sqlite", timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=8000")
        self._init_db()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                platform TEXT NOT NULL,
                kind TEXT NOT NULL,
                params TEXT,
                raw_file TEXT,
                code INTEGER
            );
            CREATE TABLE IF NOT EXISTS records (
                platform TEXT NOT NULL,
                kind TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT,
                author TEXT,
                payload TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                PRIMARY KEY (platform, kind, item_id)
            );
            """
        )

    def save_raw(self, label: str, payload: Any) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
        path = self.raw_dir / f"{stamp}_{safe}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def log_call(self, platform: str, kind: str, params: dict[str, Any] | None, raw_file: Path, code: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO calls (ts, platform, kind, params, raw_file, code) VALUES (?, ?, ?, ?, ?, ?)",
                (utc_now(), platform, kind, json.dumps(params, ensure_ascii=False), str(raw_file), code),
            )

    def save_items(self, platform: str, kind: str, items: Iterable[dict[str, Any]]) -> int:
        saved = 0
        now = utc_now()
        with self._conn:
            for item in items:
                identifier = item_id(item)
                if not identifier:
                    continue
                payload_item = unwrap_item(item)
                self._conn.execute(
                    """
                    INSERT INTO records (platform, kind, item_id, title, author, payload, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, kind, item_id) DO UPDATE SET
                        title = excluded.title,
                        author = excluded.author,
                        payload = excluded.payload,
                        last_seen = excluded.last_seen
                    """,
                    (
                        platform,
                        kind,
                        identifier,
                        item_title(item),
                        item_author(item),
                        json.dumps(payload_item, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                saved += 1
        return saved

    def count(self, platform: str | None = None, kind: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM records"
        conditions, values = [], []
        if platform:
            conditions.append("platform = ?")
            values.append(platform)
        if kind:
            conditions.append("kind = ?")
            values.append(kind)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        return int(self._conn.execute(query, values).fetchone()[0])
