from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .normalize import TIER_LABELS, derive_tags, normalize_tier_label, utc_now
from .statuses import DEFAULT_STATUS, normalize_status


class JobBusyError(RuntimeError):
    """A round already has a pending or running job."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    pages INTEGER NOT NULL DEFAULT 1,
    max_creators INTEGER,
    platform TEXT NOT NULL DEFAULT 'xhs',
    provider TEXT NOT NULL DEFAULT 'auto',
    started_at TEXT,
    finished_at TEXT,
    cost_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE,
    category TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS round_keywords (
    round_id INTEGER NOT NULL,
    keyword_id INTEGER NOT NULL,
    pages INTEGER,
    PRIMARY KEY (round_id, keyword_id)
);

CREATE TABLE IF NOT EXISTS notes (
    platform TEXT NOT NULL,
    note_id TEXT NOT NULL,
    author_id TEXT,
    title TEXT,
    desc TEXT,
    note_type TEXT,
    published_at INTEGER,
    liked INTEGER NOT NULL DEFAULT 0,
    collected INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    url TEXT,
    hashtags TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (platform, note_id)
);

CREATE TABLE IF NOT EXISTS note_hits (
    round_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    note_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    page INTEGER,
    rank INTEGER,
    PRIMARY KEY (round_id, platform, note_id, keyword)
);

CREATE TABLE IF NOT EXISTS creators (
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    red_id TEXT,
    nickname TEXT,
    avatar TEXT,
    fans INTEGER,
    follows INTEGER,
    liked INTEGER,
    collected INTEGER,
    posted INTEGER,
    interaction INTEGER,
    desc TEXT,
    tags_json TEXT,
    ip_location TEXT,
    verify_info TEXT,
    profile_url TEXT,
    fetched_at TEXT,
    raw_json TEXT,
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS creator_rounds (
    round_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_keywords TEXT,
    sample_notes INTEGER NOT NULL DEFAULT 0,
    sample_likes INTEGER NOT NULL DEFAULT 0,
    sample_collected INTEGER NOT NULL DEFAULT 0,
    sample_comments INTEGER NOT NULL DEFAULT 0,
    sample_interactions INTEGER NOT NULL DEFAULT 0,
    top_note_id TEXT,
    auto_tags TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    match_direction TEXT,
    tags_manual TEXT,
    remark TEXT,
    data_round TEXT,
    tier_label TEXT,
    account_id TEXT,
    executor TEXT,
    cooperation_platform TEXT,
    raw_title TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (round_id, platform, user_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    pid INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER,
    job_id INTEGER,
    platform TEXT,
    provider TEXT NOT NULL DEFAULT 'dataflow',
    kind TEXT,
    path TEXT,
    params TEXT,
    code INTEGER,
    cost INTEGER NOT NULL DEFAULT 0,
    cost_native REAL NOT NULL DEFAULT 0,
    cost_currency TEXT NOT NULL DEFAULT 'token',
    duration_ms INTEGER,
    raw_file TEXT,
    ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_author ON notes (platform, author_id);
CREATE INDEX IF NOT EXISTS idx_note_hits_round ON note_hits (round_id, platform, note_id);
CREATE INDEX IF NOT EXISTS idx_creator_rounds_round ON creator_rounds (round_id, platform, user_id);
CREATE INDEX IF NOT EXISTS idx_creator_rounds_status ON creator_rounds (round_id, platform, status);
CREATE INDEX IF NOT EXISTS idx_creator_rounds_direction ON creator_rounds (round_id, platform, match_direction);
CREATE INDEX IF NOT EXISTS idx_creator_rounds_data_round ON creator_rounds (round_id, platform, data_round);
CREATE INDEX IF NOT EXISTS idx_creator_rounds_tier ON creator_rounds (round_id, platform, tier_label);
CREATE INDEX IF NOT EXISTS idx_creator_rounds_updated ON creator_rounds (round_id, platform, updated_at);
CREATE INDEX IF NOT EXISTS idx_creators_fans ON creators (platform, fans);
CREATE INDEX IF NOT EXISTS idx_api_calls_round ON api_calls (round_id);
CREATE INDEX IF NOT EXISTS idx_jobs_round ON jobs (round_id);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    auth_source TEXT NOT NULL DEFAULT 'password',
    external_id TEXT,
    role TEXT NOT NULL DEFAULT 'member',
    disabled INTEGER NOT NULL DEFAULT 0,
    can_collect INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS backfill_attempts (
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    query TEXT,
    matched_user_id TEXT,
    error TEXT,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    target TEXT,
    ip TEXT,
    user_agent TEXT,
    details TEXT,
    ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts);
"""

TIER_LABELS_SQL = "(" + ", ".join(f"'{label}'" for label in TIER_LABELS) + ")"

TIER_SQL = f"""
CASE
    WHEN c.fans >= 1000000 THEN '顶流'
    WHEN c.fans >= 500000 THEN '头部'
    WHEN c.fans >= 100000 THEN '腰部'
    WHEN c.fans >= 10000 THEN '尾部'
    WHEN c.fans IS NOT NULL THEN '素人'
    WHEN cr.tier_label IN {TIER_LABELS_SQL} THEN cr.tier_label
    ELSE '未补全'
END
"""

DATA_ROUND_SQL = "COALESCE(NULLIF(cr.data_round, ''), r.name)"

CREATOR_FROM_SQL = """
FROM creator_rounds cr
JOIN rounds r ON r.id = cr.round_id
LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
LEFT JOIN notes n ON n.platform = cr.platform AND n.note_id = cr.top_note_id
"""

ALL_ROUNDS_FROM_SQL = """
FROM (
    SELECT cr.*,
           ROW_NUMBER() OVER (
               PARTITION BY cr.platform, cr.user_id
               ORDER BY cr.round_id DESC, cr.updated_at DESC
           ) AS rn
    FROM creator_rounds cr
) cr
JOIN rounds r ON r.id = cr.round_id
LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
LEFT JOIN notes n ON n.platform = cr.platform AND n.note_id = cr.top_note_id
"""

CREATOR_SELECT_SQL = f"""
SELECT cr.round_id, cr.platform, cr.user_id,
       c.nickname, c.avatar, c.fans, c.follows, c.posted, c.liked, c.collected, c.interaction,
       c.red_id, c.profile_url, c.verify_info, c.ip_location, c.tags_json AS creator_tags,
       cr.source_keywords, cr.sample_notes, cr.sample_likes, cr.sample_collected,
       cr.sample_comments, cr.sample_interactions, cr.top_note_id,
       cr.auto_tags, cr.status, cr.match_direction, cr.tags_manual, cr.remark,
       cr.data_round, cr.tier_label, cr.account_id, cr.executor,
       cr.cooperation_platform, cr.raw_title,
       n.title AS top_title, n.url AS top_url, n.liked AS top_liked,
       r.name AS round_name, {TIER_SQL} AS derived_tier
"""

ROUND_FIELDS = {
    "name",
    "status",
    "pages",
    "max_creators",
    "platform",
    "provider",
    "started_at",
    "finished_at",
    "cost_tokens",
    "error",
}
JOB_FIELDS = {
    "status",
    "stage",
    "progress_done",
    "progress_total",
    "cost",
    "error",
    "pid",
    "started_at",
    "finished_at",
}
REVIEW_FIELDS = {"status", "match_direction", "tags_manual", "remark"}

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: dict[str, tuple[bool, bool]] = {}


class Database:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_dir = self.root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.root / "codata.sqlite",
            timeout=30,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=8000")
        db_key = str((self.root / "codata.sqlite").resolve())
        with _SCHEMA_LOCK:
            state = _SCHEMA_READY.get(db_key)
            if state is None:
                self._has_fts = False
                self._fts_trigram = False
                self._init_db()
                _SCHEMA_READY[db_key] = (self._has_fts, self._fts_trigram)
            else:
                self._has_fts, self._fts_trigram = state

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _init_db(self) -> None:
        self._conn.executescript(SCHEMA)
        round_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(rounds)").fetchall()
        }
        if "category" not in round_columns:
            self._conn.execute("ALTER TABLE rounds ADD COLUMN category TEXT")
        if "platform" not in round_columns:
            self._conn.execute(
                "ALTER TABLE rounds ADD COLUMN platform TEXT NOT NULL DEFAULT 'xhs'"
            )
        if "provider" not in round_columns:
            self._conn.execute(
                "ALTER TABLE rounds ADD COLUMN provider TEXT NOT NULL DEFAULT 'dataflow'"
            )
        rows = self._conn.execute(
            """
            SELECT r.id AS round_id,
                   MIN(k.category) AS category,
                   COUNT(DISTINCT NULLIF(TRIM(k.category), '')) AS category_count
            FROM rounds r
            JOIN round_keywords rk ON rk.round_id = r.id
            JOIN keywords k ON k.id = rk.keyword_id
            WHERE COALESCE(TRIM(r.category), '') = ''
              AND COALESCE(TRIM(k.category), '') != ''
            GROUP BY r.id
            """
        ).fetchall()
        for row in rows:
            if int(row["category_count"] or 0) == 1 and row["category"]:
                self._conn.execute(
                    "UPDATE rounds SET category = ? WHERE id = ?",
                    (str(row["category"]).strip(), row["round_id"]),
                )
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(creator_rounds)").fetchall()
        }
        for column in (
            "data_round",
            "tier_label",
            "account_id",
            "executor",
            "cooperation_platform",
            "raw_title",
        ):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE creator_rounds ADD COLUMN {column} TEXT")
        canonical_placeholders = ", ".join("?" for _ in TIER_LABELS)
        self._conn.execute(
            f"""
            UPDATE creator_rounds
            SET tier_label = (
                SELECT CASE
                    WHEN c.fans >= 1000000 THEN '顶流'
                    WHEN c.fans >= 500000 THEN '头部'
                    WHEN c.fans >= 100000 THEN '腰部'
                    WHEN c.fans >= 10000 THEN '尾部'
                    WHEN c.fans IS NOT NULL THEN '素人'
                END
                FROM creators c
                WHERE c.platform = creator_rounds.platform
                  AND c.user_id = creator_rounds.user_id
            )
            WHERE tier_label IS NOT NULL
              AND tier_label NOT IN ({canonical_placeholders})
            """,
            TIER_LABELS,
        )
        status_map = {
            "待审核": "pending",
            "候选": "shortlisted",
            "备选": "shortlisted",
            "已推荐": "adopted",
            "已联系": "shortlisted",
            "已采用": "adopted",
            "未采用": "rejected",
            "已淘汰": "rejected",
            "candidate": "shortlisted",
            "recommended": "adopted",
            "contacted": "shortlisted",
            "dropped": "rejected",
        }
        for old, code in status_map.items():
            self._conn.execute(
                "UPDATE creator_rounds SET status = ? WHERE status = ?",
                (code, old),
            )
        api_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(api_calls)").fetchall()
        }
        if "job_id" not in api_columns:
            self._conn.execute("ALTER TABLE api_calls ADD COLUMN job_id INTEGER")
        if "provider" not in api_columns:
            self._conn.execute(
                "ALTER TABLE api_calls ADD COLUMN provider TEXT NOT NULL DEFAULT 'dataflow'"
            )
        if "cost_native" not in api_columns:
            self._conn.execute(
                "ALTER TABLE api_calls ADD COLUMN cost_native REAL NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "UPDATE api_calls SET cost_native = cost WHERE cost_native = 0"
            )
        if "cost_currency" not in api_columns:
            self._conn.execute(
                "ALTER TABLE api_calls ADD COLUMN cost_currency TEXT NOT NULL DEFAULT 'token'"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_calls_job ON api_calls (job_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_calls_provider ON api_calls (provider)"
        )
        user_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "can_collect" not in user_columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN can_collect INTEGER NOT NULL DEFAULT 0")
        if "must_change_password" not in user_columns:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
            )
        self._conn.commit()

        for tokenizer in ("trigram", "unicode61"):
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS creator_search USING fts5("
                    "nickname, account_id, executor, raw_title, tags_manual, source_keywords, "
                    "round_id UNINDEXED, platform UNINDEXED, user_id UNINDEXED, "
                    f"tokenize='{tokenizer}')"
                )
                self._has_fts = True
                self._fts_trigram = tokenizer == "trigram"
                break
            except sqlite3.OperationalError:
                self._has_fts = False
        if self._has_fts:
            try:
                fts_count = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM creator_search"
                ).fetchone()["n"]
                creator_count = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM creator_rounds"
                ).fetchone()["n"]
                if int(fts_count) != int(creator_count):
                    self.rebuild_search_index()
            except sqlite3.OperationalError:
                self._has_fts = False
        self._conn.commit()

    # ---- raw / audit ----

    def save_raw(self, label: str, payload: Any) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:120]
        path = self.raw_dir / f"{stamp}_{safe}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def log_api_call(
        self,
        round_id: int | None,
        platform: str,
        kind: str,
        params: dict[str, Any] | None,
        raw_file: Path | None,
        code: int | None,
        cost: int,
        duration_ms: int,
        path: str = "",
        job_id: int | None = None,
        provider: str = "dataflow",
        cost_native: float | None = None,
        cost_currency: str = "token",
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO api_calls
                (round_id, job_id, platform, provider, kind, path, params, code, cost,
                 cost_native, cost_currency, duration_ms, raw_file, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    job_id,
                    platform,
                    provider,
                    kind,
                    path,
                    json.dumps(params or {}, ensure_ascii=False),
                    code,
                    cost,
                    float(cost_native if cost_native is not None else cost),
                    cost_currency,
                    duration_ms,
                    str(raw_file) if raw_file else None,
                    utc_now(),
                ),
            )

    def sum_round_cost(self, round_id: int | None) -> int:
        if round_id is None:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost), 0) AS total FROM api_calls"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost), 0) AS total FROM api_calls WHERE round_id = ?",
                (round_id,),
            ).fetchone()
        return int(row["total"] or 0)

    def sum_job_cost(self, job_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS total FROM api_calls WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return int(row["total"] or 0)

    # ---- keywords / rounds ----

    def ensure_keyword(self, word: str, category: str | None = None, commit: bool = True) -> int:
        word = (word or "").strip()
        if not word:
            raise ValueError("关键词不能为空")
        category = (category or "").strip() or None
        row = self._conn.execute("SELECT id, category FROM keywords WHERE word = ?", (word,)).fetchone()
        if row:
            if category and category != row["category"]:
                if commit:
                    with self._conn:
                        self._conn.execute("UPDATE keywords SET category = ? WHERE id = ?", (category, row["id"]))
                else:
                    self._conn.execute("UPDATE keywords SET category = ? WHERE id = ?", (category, row["id"]))
            return int(row["id"])
        if commit:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO keywords (word, category, enabled, created_at) VALUES (?, ?, 1, ?)",
                    (word, category, utc_now()),
                )
        else:
            cur = self._conn.execute(
                "INSERT INTO keywords (word, category, enabled, created_at) VALUES (?, ?, 1, ?)",
                (word, category, utc_now()),
            )
        return int(cur.lastrowid)

    def list_keywords(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT k.*, COUNT(rk.round_id) AS rounds
            FROM keywords k
            LEFT JOIN round_keywords rk ON rk.keyword_id = k.id
            GROUP BY k.id
            ORDER BY k.enabled DESC, k.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def update_keyword(self, keyword_id: int, category: str | None = None, enabled: bool | None = None) -> None:
        sets, values = [], []
        if category is not None:
            sets.append("category = ?")
            values.append(category.strip() or None)
        if enabled is not None:
            sets.append("enabled = ?")
            values.append(1 if enabled else 0)
        if not sets:
            return
        values.append(keyword_id)
        with self._conn:
            self._conn.execute(f"UPDATE keywords SET {', '.join(sets)} WHERE id = ?", values)

    def delete_keyword(self, keyword_id: int) -> None:
        used = self._conn.execute(
            "SELECT COUNT(*) AS n FROM round_keywords WHERE keyword_id = ?", (keyword_id,)
        ).fetchone()["n"]
        if used:
            raise ValueError("该关键词已被轮次使用，请改为停用")
        with self._conn:
            self._conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))

    def create_round(
        self,
        keyword_words: Iterable[str],
        pages: int = 1,
        max_creators: int | None = 20,
        name: str | None = None,
        category: str | None = None,
        categories: dict[str, str] | None = None,
        platform: str = "xhs",
        provider: str = "auto",
    ) -> int:
        words = []
        for word in keyword_words:
            word = str(word).strip()
            if word and word not in words:
                words.append(word)
        if not words:
            raise ValueError("至少需要一个关键词")
        pages = max(1, int(pages))
        if max_creators is not None and int(max_creators) <= 0:
            max_creators = None
        category = (category or "").strip() or None

        now = utc_now()
        if not name:
            prefix = datetime.now().strftime("R%Y%m%d")
            rows = self._conn.execute(
                "SELECT name FROM rounds WHERE name LIKE ?", (prefix + "-%",)
            ).fetchall()
            max_suffix = 0
            for row in rows:
                suffix = str(row["name"]).rsplit("-", 1)[-1]
                if suffix.isdigit():
                    max_suffix = max(max_suffix, int(suffix))
            name = f"{prefix}-{max_suffix + 1:02d}"

        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO rounds
                (name, category, status, pages, max_creators, platform, provider, created_at)
                VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (name, category, pages, max_creators, platform, provider, now),
            )
            round_id = int(cur.lastrowid)
            for word in words:
                keyword_id = self.ensure_keyword(word, (categories or {}).get(word), commit=False)
                self._conn.execute(
                    "INSERT OR IGNORE INTO round_keywords (round_id, keyword_id, pages) VALUES (?, ?, ?)",
                    (round_id, keyword_id, pages),
                )
        return round_id

    def get_round(self, round_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
        return dict(row) if row else None

    def list_rounds(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT r.*,
                   COALESCE((
                       SELECT GROUP_CONCAT(k.word, '、')
                       FROM round_keywords rk JOIN keywords k ON k.id = rk.keyword_id
                       WHERE rk.round_id = r.id
                   ), '') AS keywords,
                   (SELECT COUNT(*) FROM creator_rounds cr WHERE cr.round_id = r.id) AS creators
            FROM rounds r
            ORDER BY r.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_round_keywords(self, round_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT k.word, k.category, COALESCE(rk.pages, r.pages) AS pages
            FROM round_keywords rk
            JOIN keywords k ON k.id = rk.keyword_id
            JOIN rounds r ON r.id = rk.round_id
            WHERE rk.round_id = ? AND k.enabled = 1
            ORDER BY k.id
            """,
            (round_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_round(self, round_id: int) -> dict[str, Any]:
        row = self.get_round(round_id)
        if not row:
            raise ValueError(f"轮次不存在: {round_id}")
        counts: dict[str, int] = {}
        with self._conn:
            for table in ("creator_rounds", "note_hits", "jobs", "api_calls", "round_keywords"):
                cur = self._conn.execute(f"DELETE FROM {table} WHERE round_id = ?", (round_id,))
                counts[table] = max(0, cur.rowcount)
            if self._has_fts:
                self._conn.execute(
                    "DELETE FROM creator_search WHERE round_id = ?", (str(round_id),)
                )
            cur = self._conn.execute("DELETE FROM rounds WHERE id = ?", (round_id,))
            counts["rounds"] = max(0, cur.rowcount)
            cur = self._conn.execute(
                """
                DELETE FROM creators
                WHERE NOT EXISTS (
                    SELECT 1 FROM creator_rounds cr
                    WHERE cr.platform = creators.platform AND cr.user_id = creators.user_id
                )
                """
            )
            counts["orphan_creators"] = max(0, cur.rowcount)
            cur = self._conn.execute(
                """
                DELETE FROM notes
                WHERE NOT EXISTS (
                    SELECT 1 FROM note_hits nh
                    WHERE nh.platform = notes.platform AND nh.note_id = notes.note_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM creator_rounds cr
                    WHERE cr.platform = notes.platform AND cr.top_note_id = notes.note_id
                )
                """
            )
            counts["orphan_notes"] = max(0, cur.rowcount)
        raw_files = list(self.raw_dir.glob(f"*round{round_id}_*"))
        for path in raw_files:
            path.unlink(missing_ok=True)
        log_files = list((self.root / "logs").glob(f"round_{round_id}_*.log"))
        for path in log_files:
            path.unlink(missing_ok=True)
        counts["raw_files"] = len(raw_files)
        counts["log_files"] = len(log_files)
        return {"id": round_id, "name": row["name"], **counts}

    def cleanup_orphans(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM api_calls WHERE round_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM rounds r WHERE r.id = api_calls.round_id)"
            )
            counts["api_calls"] = max(0, cur.rowcount)
            cur = self._conn.execute(
                "DELETE FROM creators WHERE NOT EXISTS "
                "(SELECT 1 FROM creator_rounds cr WHERE cr.platform = creators.platform "
                "AND cr.user_id = creators.user_id)"
            )
            counts["creators"] = max(0, cur.rowcount)
            cur = self._conn.execute(
                "DELETE FROM notes WHERE NOT EXISTS "
                "(SELECT 1 FROM note_hits nh WHERE nh.platform = notes.platform "
                "AND nh.note_id = notes.note_id) AND NOT EXISTS "
                "(SELECT 1 FROM creator_rounds cr WHERE cr.platform = notes.platform "
                "AND cr.top_note_id = notes.note_id)"
            )
            counts["notes"] = max(0, cur.rowcount)
            if self._has_fts:
                cur = self._conn.execute(
                    "DELETE FROM creator_search WHERE NOT EXISTS "
                    "(SELECT 1 FROM creators c WHERE c.platform = creator_search.platform "
                    "AND c.user_id = creator_search.user_id)"
                )
                counts["search_rows"] = max(0, cur.rowcount)
        return counts

    def prune_round_creators(self, round_id: int, max_creators: int | None) -> int:
        if not max_creators or max_creators <= 0:
            return 0
        reviewed = [
            row["user_id"]
            for row in self._conn.execute(
                "SELECT user_id FROM creator_rounds WHERE round_id = ? AND status <> 'pending'",
                (round_id,),
            ).fetchall()
        ]
        remaining = max(0, int(max_creators) - len(reviewed))
        pending = [
            row["user_id"]
            for row in self._conn.execute(
                """
                SELECT cr.user_id
                FROM creator_rounds cr
                LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
                WHERE cr.round_id = ? AND cr.status = 'pending'
                ORDER BY
                    CASE WHEN c.fans IS NULL THEN 1 ELSE 0 END,
                    COALESCE(c.fans, 0) DESC,
                    COALESCE(cr.sample_interactions, 0) DESC,
                    cr.user_id
                LIMIT ?
                """,
                (round_id, remaining),
            ).fetchall()
        ]
        keep = set(reviewed) | set(pending)
        if not keep:
            return 0
        placeholders = ", ".join("?" for _ in keep)
        with self._conn:
            cur = self._conn.execute(
                f"DELETE FROM creator_rounds WHERE round_id = ? AND status = 'pending' "
                f"AND user_id NOT IN ({placeholders})",
                (round_id, *keep),
            )
            deleted = max(0, cur.rowcount)
        if deleted and self._has_fts:
            self.rebuild_search_index(round_id)
        return deleted

    def update_round(self, round_id: int, **fields: Any) -> None:
        updates = {key: value for key, value in fields.items() if key in ROUND_FIELDS}
        if not updates:
            return
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            self._conn.execute(f"UPDATE rounds SET {sets} WHERE id = ?", [*updates.values(), round_id])

    # ---- notes / creators ----

    def upsert_note(self, note: dict[str, Any]) -> None:
        now = utc_now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO notes
                (platform, note_id, author_id, title, desc, note_type, published_at,
                 liked, collected, comments, shares, url, hashtags, raw_json, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, note_id) DO UPDATE SET
                    author_id = excluded.author_id,
                    title = excluded.title,
                    desc = excluded.desc,
                    note_type = excluded.note_type,
                    published_at = COALESCE(excluded.published_at, notes.published_at),
                    liked = excluded.liked,
                    collected = excluded.collected,
                    comments = excluded.comments,
                    shares = excluded.shares,
                    url = excluded.url,
                    hashtags = excluded.hashtags,
                    raw_json = excluded.raw_json,
                    last_seen = excluded.last_seen
                """,
                (
                    note.get("platform", "xhs"),
                    note["note_id"],
                    note.get("author_id"),
                    note.get("title", ""),
                    note.get("desc", ""),
                    note.get("note_type", ""),
                    note.get("published_at"),
                    int(note.get("liked") or 0),
                    int(note.get("collected") or 0),
                    int(note.get("comments") or 0),
                    int(note.get("shares") or 0),
                    note.get("url", ""),
                    note.get("hashtags", "[]"),
                    note.get("raw_json", "{}"),
                    now,
                    now,
                ),
            )

    def add_note_hit(
        self,
        round_id: int,
        platform: str,
        note_id: str,
        keyword: str,
        page: int | None,
        rank: int | None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO note_hits (round_id, platform, note_id, keyword, page, rank)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (round_id, platform, note_id, keyword, page, rank),
            )

    def upsert_creator_basic(self, creator: dict[str, Any]) -> None:
        if not creator.get("user_id"):
            return
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO creators (platform, user_id, red_id, nickname, avatar, profile_url)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    red_id = COALESCE(excluded.red_id, creators.red_id),
                    nickname = COALESCE(excluded.nickname, creators.nickname),
                    avatar = COALESCE(excluded.avatar, creators.avatar),
                    profile_url = COALESCE(excluded.profile_url, creators.profile_url)
                """,
                (
                    creator.get("platform", "xhs"),
                    creator["user_id"],
                    creator.get("red_id"),
                    creator.get("nickname"),
                    creator.get("avatar"),
                    creator.get("profile_url"),
                ),
            )

    def upsert_creator(self, creator: dict[str, Any]) -> None:
        if not creator.get("user_id"):
            raise ValueError("达人 user_id 不能为空")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO creators
                (platform, user_id, red_id, nickname, avatar, fans, follows, liked, collected,
                 posted, interaction, desc, tags_json, ip_location, verify_info, profile_url,
                 fetched_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    red_id = COALESCE(excluded.red_id, creators.red_id),
                    nickname = COALESCE(excluded.nickname, creators.nickname),
                    avatar = COALESCE(excluded.avatar, creators.avatar),
                    fans = COALESCE(excluded.fans, creators.fans),
                    follows = COALESCE(excluded.follows, creators.follows),
                    liked = COALESCE(excluded.liked, creators.liked),
                    collected = COALESCE(excluded.collected, creators.collected),
                    posted = COALESCE(excluded.posted, creators.posted),
                    interaction = COALESCE(excluded.interaction, creators.interaction),
                    desc = COALESCE(excluded.desc, creators.desc),
                    tags_json = COALESCE(excluded.tags_json, creators.tags_json),
                    ip_location = COALESCE(excluded.ip_location, creators.ip_location),
                    verify_info = COALESCE(excluded.verify_info, creators.verify_info),
                    profile_url = COALESCE(excluded.profile_url, creators.profile_url),
                    fetched_at = COALESCE(excluded.fetched_at, creators.fetched_at),
                    raw_json = COALESCE(excluded.raw_json, creators.raw_json)
                """,
                (
                    creator.get("platform", "xhs"),
                    creator["user_id"],
                    creator.get("red_id"),
                    creator.get("nickname"),
                    creator.get("avatar"),
                    creator.get("fans"),
                    creator.get("follows"),
                    creator.get("liked"),
                    creator.get("collected"),
                    creator.get("posted"),
                    creator.get("interaction"),
                    creator.get("desc"),
                    creator.get("tags_json"),
                    creator.get("ip_location"),
                    creator.get("verify_info"),
                    creator.get("profile_url"),
                    creator.get("fetched_at"),
                    creator.get("raw_json"),
                ),
            )

    def get_creator(self, platform: str, user_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM creators WHERE platform = ? AND user_id = ?", (platform, user_id)
        ).fetchone()
        return dict(row) if row else None

    def creator_is_fresh(self, platform: str, user_id: str, cache_days: int) -> bool:
        row = self._conn.execute(
            "SELECT fetched_at FROM creators WHERE platform = ? AND user_id = ?", (platform, user_id)
        ).fetchone()
        if not row or not row["fetched_at"]:
            return False
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
        except ValueError:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched < timedelta(days=max(0, cache_days))

    # ---- backfill ----

    def backfill_candidates(
        self,
        platform: str,
        limit: int = 20,
        retry_days: int = 7,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT cr.platform, cr.user_id, MIN(cr.round_id) AS round_id,
                   c.nickname, c.raw_json, c.fans, c.fetched_at
            FROM creator_rounds cr
            JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.platform = ?
              AND cr.status = 'pending'
              AND c.user_id LIKE 'import:%'
              AND (c.fans IS NULL OR c.fetched_at IS NULL OR c.fetched_at = '')
              AND NOT EXISTS (
                  SELECT 1 FROM backfill_attempts ba
                  WHERE ba.platform = cr.platform AND ba.user_id = cr.user_id
                    AND ba.status = 'matched'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM backfill_attempts ba
                  WHERE ba.platform = cr.platform AND ba.user_id = cr.user_id
                    AND ba.status = 'no_match'
                    AND ba.attempted_at >= datetime('now', ?)
              )
            GROUP BY cr.platform, cr.user_id
            ORDER BY round_id, cr.user_id
            LIMIT ?
            """,
            (platform, f"-{max(0, retry_days)} days", max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_backfill_attempt(
        self,
        platform: str,
        user_id: str,
        status: str,
        query: str = "",
        matched_user_id: str = "",
        error: str = "",
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO backfill_attempts
                (platform, user_id, status, query, matched_user_id, error, attempted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    status = excluded.status,
                    query = excluded.query,
                    matched_user_id = excluded.matched_user_id,
                    error = excluded.error,
                    attempted_at = excluded.attempted_at
                """,
                (platform, user_id, status, query, matched_user_id, error, utc_now()),
            )

    def enrich_imported_creator(
        self,
        platform: str,
        imported_user_id: str,
        creator: dict[str, Any],
        account_id: str,
        query: str,
        selection_note: str = "",
    ) -> None:
        row = self._conn.execute(
            "SELECT raw_json FROM creators WHERE platform = ? AND user_id = ?",
            (platform, imported_user_id),
        ).fetchone()
        try:
            original = json.loads((row["raw_json"] if row else "") or "{}")
        except (TypeError, json.JSONDecodeError):
            original = {}
        if not isinstance(original, dict):
            original = {}
        profile_raw = creator.get("raw_json") or "{}"
        try:
            profile = json.loads(profile_raw)
        except (TypeError, json.JSONDecodeError):
            profile = {}
        if isinstance(profile, dict):
            profile.pop("_backfill", None)
            profile.pop("_profile", None)
        original["_backfill"] = {
            "query": query,
            "matched_user_id": account_id,
            "imported_user_id": imported_user_id,
            "selection_note": selection_note,
        }
        original["_profile"] = profile
        enriched_raw = json.dumps(original, ensure_ascii=False)
        with self._conn:
            self._conn.execute(
                """
                UPDATE creators SET
                    red_id = COALESCE(?, red_id),
                    nickname = COALESCE(?, nickname),
                    avatar = COALESCE(?, avatar),
                    fans = COALESCE(?, fans),
                    follows = COALESCE(?, follows),
                    liked = COALESCE(?, liked),
                    collected = COALESCE(?, collected),
                    posted = COALESCE(?, posted),
                    interaction = COALESCE(?, interaction),
                    desc = COALESCE(?, desc),
                    tags_json = COALESCE(?, tags_json),
                    ip_location = COALESCE(?, ip_location),
                    verify_info = COALESCE(?, verify_info),
                    profile_url = COALESCE(?, profile_url),
                    fetched_at = ?,
                    raw_json = ?
                WHERE platform = ? AND user_id = ?
                """,
                (
                    creator.get("red_id"),
                    creator.get("nickname"),
                    creator.get("avatar"),
                    creator.get("fans"),
                    creator.get("follows"),
                    creator.get("liked"),
                    creator.get("collected"),
                    creator.get("posted"),
                    creator.get("interaction"),
                    creator.get("desc"),
                    creator.get("tags_json"),
                    creator.get("ip_location"),
                    creator.get("verify_info"),
                    creator.get("profile_url"),
                    utc_now(),
                    enriched_raw,
                    platform,
                    imported_user_id,
                ),
            )
            self._conn.execute(
                "UPDATE creator_rounds SET account_id = ? WHERE platform = ? AND user_id = ?",
                (account_id, platform, imported_user_id),
            )
        self.record_backfill_attempt(
            platform,
            imported_user_id,
            "matched",
            query=query,
            matched_user_id=account_id,
            error=selection_note,
        )

    # ---- aggregation ----

    def round_author_stats(self, round_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT nh.keyword, nh.page, n.*, c.profile_url AS profile_url
            FROM note_hits nh
            JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
            LEFT JOIN creators c ON c.platform = n.platform AND c.user_id = n.author_id
            WHERE nh.round_id = ? AND n.author_id IS NOT NULL AND n.author_id != ''
            """,
            (round_id,),
        ).fetchall()

        stats: dict[tuple[str, str], dict[str, Any]] = {}
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            user_id = row["author_id"]
            note_id = row["note_id"]
            key = (row["platform"], user_id)
            entry = stats.setdefault(
                key,
                {
                    "platform": row["platform"],
                    "user_id": user_id,
                    "profile_url": row["profile_url"] or "",
                    "sample_notes": 0,
                    "sample_likes": 0,
                    "sample_collected": 0,
                    "sample_comments": 0,
                    "sample_interactions": 0,
                    "keywords": [],
                    "hashtags": [],
                    "top_note_id": None,
                    "top_note_liked": -1,
                },
            )
            if row["keyword"] not in entry["keywords"]:
                entry["keywords"].append(row["keyword"])
            seen_key = (row["platform"], user_id, note_id)
            if seen_key in seen:
                continue
            seen.add(seen_key)
            liked = int(row["liked"] or 0)
            collected = int(row["collected"] or 0)
            comments = int(row["comments"] or 0)
            shares = int(row["shares"] or 0)
            entry["sample_notes"] += 1
            entry["sample_likes"] += liked
            entry["sample_collected"] += collected
            entry["sample_comments"] += comments
            entry["sample_interactions"] += liked + collected + comments + shares
            if liked > entry["top_note_liked"]:
                entry["top_note_liked"] = liked
                entry["top_note_id"] = note_id
            try:
                hashtags = json.loads(row["hashtags"] or "[]")
            except (TypeError, json.JSONDecodeError):
                hashtags = []
            for tag in hashtags:
                if tag not in entry["hashtags"]:
                    entry["hashtags"].append(tag)

        result = []
        for entry in stats.values():
            entry.pop("top_note_liked", None)
            result.append(entry)
        result.sort(key=lambda item: (-item["sample_likes"], -item["sample_notes"], item["user_id"]))
        return result

    def refresh_creator_rounds(self, round_id: int) -> int:
        round_row = self._conn.execute(
            "SELECT category FROM rounds WHERE id = ?", (round_id,)
        ).fetchone()
        round_category = (round_row["category"] or "").strip() if round_row else ""
        stats = self.round_author_stats(round_id)
        now = utc_now()
        with self._conn:
            for entry in stats:
                creator = self.get_creator(entry["platform"], entry["user_id"]) or {}
                verify_info = creator.get("verify_info") or ""
                auto_tags = derive_tags(
                    [round_category] if round_category else [],
                    entry["hashtags"],
                    verify_info,
                )
                self._conn.execute(
                    """
                    INSERT INTO creator_rounds
                    (round_id, platform, user_id, source_keywords, sample_notes, sample_likes,
                     sample_collected, sample_comments, sample_interactions, top_note_id,
                     auto_tags, status, match_direction, tags_manual, remark, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)
                    ON CONFLICT(round_id, platform, user_id) DO UPDATE SET
                        source_keywords = excluded.source_keywords,
                        sample_notes = excluded.sample_notes,
                        sample_likes = excluded.sample_likes,
                        sample_collected = excluded.sample_collected,
                        sample_comments = excluded.sample_comments,
                        sample_interactions = excluded.sample_interactions,
                        top_note_id = excluded.top_note_id,
                        auto_tags = excluded.auto_tags,
                        updated_at = excluded.updated_at
                    """,
                    (
                        round_id,
                        entry["platform"],
                        entry["user_id"],
                        json.dumps(entry["keywords"], ensure_ascii=False),
                        entry["sample_notes"],
                        entry["sample_likes"],
                        entry["sample_collected"],
                        entry["sample_comments"],
                        entry["sample_interactions"],
                        entry["top_note_id"],
                        json.dumps(auto_tags, ensure_ascii=False),
                        DEFAULT_STATUS,
                        None,
                        now,
                    ),
                )
        self.rebuild_search_index(round_id)
        return len(stats)

    def import_creator_rows(self, round_id: int, items: list[dict[str, Any]]) -> int:
        """Bulk import creators/notes/round rows in one transaction (Excel imports)."""
        now = utc_now()
        with self._conn:
            for item in items:
                creator = item["creator"]
                note = item.get("note")
                row = item["round"]
                tier_label = (
                    normalize_tier_label(creator.get("fans"), row.get("tier_label")) or None
                )
                self._conn.execute(
                    """
                    INSERT INTO creators
                    (platform, user_id, red_id, nickname, avatar, fans, follows, liked,
                     collected, posted, interaction, desc, tags_json, ip_location,
                     verify_info, profile_url, fetched_at, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, user_id) DO UPDATE SET
                        red_id = COALESCE(excluded.red_id, creators.red_id),
                        nickname = COALESCE(excluded.nickname, creators.nickname),
                        avatar = COALESCE(excluded.avatar, creators.avatar),
                        fans = COALESCE(excluded.fans, creators.fans),
                        follows = COALESCE(excluded.follows, creators.follows),
                        liked = COALESCE(excluded.liked, creators.liked),
                        collected = COALESCE(excluded.collected, creators.collected),
                        posted = COALESCE(excluded.posted, creators.posted),
                        interaction = COALESCE(excluded.interaction, creators.interaction),
                        desc = COALESCE(excluded.desc, creators.desc),
                        tags_json = COALESCE(excluded.tags_json, creators.tags_json),
                        ip_location = COALESCE(excluded.ip_location, creators.ip_location),
                        verify_info = COALESCE(excluded.verify_info, creators.verify_info),
                        profile_url = COALESCE(excluded.profile_url, creators.profile_url),
                        fetched_at = COALESCE(excluded.fetched_at, creators.fetched_at),
                        raw_json = COALESCE(excluded.raw_json, creators.raw_json)
                    """,
                    (
                        creator.get("platform", "xhs"),
                        creator["user_id"],
                        creator.get("red_id"),
                        creator.get("nickname"),
                        creator.get("avatar"),
                        creator.get("fans"),
                        creator.get("follows"),
                        creator.get("liked"),
                        creator.get("collected"),
                        creator.get("posted"),
                        creator.get("interaction"),
                        creator.get("desc"),
                        creator.get("tags_json"),
                        creator.get("ip_location"),
                        creator.get("verify_info"),
                        creator.get("profile_url"),
                        creator.get("fetched_at"),
                        creator.get("raw_json"),
                    ),
                )
                if note:
                    self._conn.execute(
                        """
                        INSERT INTO notes
                        (platform, note_id, author_id, title, desc, note_type, published_at,
                         liked, collected, comments, shares, url, hashtags, raw_json,
                         first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(platform, note_id) DO UPDATE SET
                            author_id = excluded.author_id,
                            title = excluded.title,
                            desc = excluded.desc,
                            note_type = excluded.note_type,
                            published_at = excluded.published_at,
                            liked = excluded.liked,
                            collected = excluded.collected,
                            comments = excluded.comments,
                            shares = excluded.shares,
                            url = excluded.url,
                            hashtags = excluded.hashtags,
                            raw_json = excluded.raw_json,
                            last_seen = excluded.last_seen
                        """,
                        (
                            note.get("platform", "xhs"),
                            note["note_id"],
                            note.get("author_id"),
                            note.get("title", ""),
                            note.get("desc", ""),
                            note.get("note_type", ""),
                            note.get("published_at"),
                            int(note.get("liked") or 0),
                            int(note.get("collected") or 0),
                            int(note.get("comments") or 0),
                            int(note.get("shares") or 0),
                            note.get("url", ""),
                            note.get("hashtags", "[]"),
                            note.get("raw_json", "{}"),
                            now,
                            now,
                        ),
                    )
                self._conn.execute(
                    """
                    INSERT INTO creator_rounds
                    (round_id, platform, user_id, source_keywords, sample_notes, sample_likes,
                     sample_collected, sample_comments, sample_interactions, top_note_id,
                     auto_tags, status, match_direction, tags_manual, remark, data_round,
                     tier_label, account_id, executor, cooperation_platform, raw_title, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(round_id, platform, user_id) DO UPDATE SET
                        source_keywords = excluded.source_keywords,
                        sample_notes = excluded.sample_notes,
                        sample_likes = excluded.sample_likes,
                        sample_collected = excluded.sample_collected,
                        sample_comments = excluded.sample_comments,
                        sample_interactions = excluded.sample_interactions,
                        top_note_id = excluded.top_note_id,
                        auto_tags = excluded.auto_tags,
                        data_round = excluded.data_round,
                        tier_label = excluded.tier_label,
                        account_id = excluded.account_id,
                        executor = excluded.executor,
                        cooperation_platform = excluded.cooperation_platform,
                        raw_title = excluded.raw_title,
                        updated_at = excluded.updated_at
                    """,
                    (
                        round_id,
                        row.get("platform", "xhs"),
                        row["user_id"],
                        row.get("source_keywords", "[]"),
                        int(row.get("sample_notes") or 0),
                        int(row.get("sample_likes") or 0),
                        int(row.get("sample_collected") or 0),
                        int(row.get("sample_comments") or 0),
                        int(row.get("sample_interactions") or 0),
                        row.get("top_note_id"),
                        row.get("auto_tags", "[]"),
                        normalize_status(row.get("status")),
                        row.get("match_direction"),
                        row.get("tags_manual"),
                        row.get("remark"),
                        row.get("data_round"),
                        tier_label,
                        row.get("account_id"),
                        row.get("executor"),
                        row.get("cooperation_platform"),
                        row.get("raw_title"),
                        now,
                    ),
                )
        self.rebuild_search_index(round_id)
        return len(items)

    def update_review(
        self,
        round_id: int,
        platform: str,
        user_id: str,
        status: str | None = None,
        match_direction: str | None = None,
        tags_manual: str | None = None,
        remark: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = normalize_status(status)
        if match_direction is not None:
            updates["match_direction"] = match_direction
        if tags_manual is not None:
            updates["tags_manual"] = tags_manual
        if remark is not None:
            updates["remark"] = remark
        updates = {key: value for key, value in updates.items() if key in REVIEW_FIELDS}
        if not updates:
            return
        updates["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            self._conn.execute(
                f"UPDATE creator_rounds SET {sets} WHERE round_id = ? AND platform = ? AND user_id = ?",
                [*updates.values(), round_id, platform, user_id],
            )
        if tags_manual is not None:
            self._refresh_search_row(round_id, platform, user_id)

    def bulk_update_review(
        self,
        round_id: int,
        items: Iterable[tuple[str, str]],
        status: str | None = None,
        match_direction: str | None = None,
        tags_manual: str | None = None,
        remark: str | None = None,
    ) -> int:
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = normalize_status(status)
        if match_direction is not None:
            updates["match_direction"] = match_direction
        if tags_manual is not None:
            updates["tags_manual"] = tags_manual
        if remark is not None:
            updates["remark"] = remark
        updates = {key: value for key, value in updates.items() if key in REVIEW_FIELDS}
        if not updates:
            return 0
        updates["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values())
        item_list = list(items)
        changed = 0
        with self._conn:
            for platform, user_id in item_list:
                cur = self._conn.execute(
                    f"UPDATE creator_rounds SET {sets} WHERE round_id = ? AND platform = ? AND user_id = ?",
                    [*values, round_id, platform, user_id],
                )
                changed += max(0, cur.rowcount)
        if tags_manual is not None:
            for platform, user_id in item_list:
                self._refresh_search_row(round_id, platform, user_id)
        return changed

    # ---- queries for panel ----

    def creator_table(
        self,
        round_id: int,
        platform: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions = ["cr.round_id = ?"]
        params: list[Any] = [round_id]
        if platform:
            conditions.append("cr.platform = ?")
            params.append(platform)
        if user_id:
            conditions.append("cr.user_id = ?")
            params.append(user_id)
        rows = self._conn.execute(
            f"""
            SELECT cr.round_id, cr.platform, cr.user_id,
                   c.nickname, c.avatar, c.fans, c.follows, c.posted, c.liked, c.collected, c.interaction,
                   c.red_id, c.profile_url, c.verify_info, c.ip_location, c.tags_json AS creator_tags,
                   cr.source_keywords, cr.sample_notes, cr.sample_likes, cr.sample_collected,
                   cr.sample_comments, cr.sample_interactions, cr.top_note_id,
                   cr.auto_tags, cr.status, cr.match_direction, cr.tags_manual, cr.remark,
                   cr.data_round, cr.tier_label, cr.account_id, cr.executor,
                   cr.cooperation_platform, cr.raw_title,
                   n.title AS top_title, n.url AS top_url, n.liked AS top_liked,
                   r.name AS round_name
            FROM creator_rounds cr
            JOIN rounds r ON r.id = cr.round_id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            LEFT JOIN notes n ON n.platform = cr.platform AND n.note_id = cr.top_note_id
            WHERE {' AND '.join(conditions)}
            ORDER BY cr.sample_likes DESC, cr.sample_notes DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def _creator_conditions(
        self,
        round_id: int | None,
        platform: str | None = None,
        search: str | None = None,
        statuses: Iterable[str] | None = None,
        tiers: Iterable[str] | None = None,
        directions: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
        min_fans: int | None = None,
        max_fans: int | None = None,
        only_unenriched: bool = False,
    ) -> tuple[str, list[Any]]:
        conditions = ["cr.rn = 1"] if round_id is None else ["cr.round_id = ?"]
        params: list[Any] = [] if round_id is None else [round_id]
        if platform:
            conditions.append("cr.platform = ?")
            params.append(platform)
        if search:
            text = search.strip()
            if self._has_fts and self._fts_trigram and len(text) >= 3:
                fts_query = '"' + text.replace('"', '""') + '"'
                if round_id is None:
                    conditions.append(
                        "(cr.platform || '|' || cr.user_id) IN ("
                        "SELECT platform || '|' || user_id FROM creator_search "
                        "WHERE creator_search MATCH ?)"
                    )
                    params.append(fts_query)
                else:
                    conditions.append(
                        "(cr.platform || '|' || cr.user_id) IN ("
                        "SELECT platform || '|' || user_id FROM creator_search "
                        "WHERE creator_search MATCH ? AND creator_search.round_id = ?)"
                    )
                    params.extend([fts_query, str(round_id)])
            else:
                escaped = (
                    text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                like = f"%{escaped}%"
                conditions.append(
                    "("
                    "c.nickname LIKE ? ESCAPE '\\' OR "
                    "cr.account_id LIKE ? ESCAPE '\\' OR "
                    "cr.user_id LIKE ? ESCAPE '\\' OR "
                    "cr.executor LIKE ? ESCAPE '\\' OR "
                    "cr.raw_title LIKE ? ESCAPE '\\' OR "
                    "cr.tags_manual LIKE ? ESCAPE '\\'"
                    ")"
                )
                params.extend([like] * 6)
        status_values = [value for value in (statuses or []) if value]
        if status_values:
            conditions.append(f"cr.status IN ({','.join('?' * len(status_values))})")
            params.extend(status_values)
        tier_values = [value for value in (tiers or []) if value]
        if tier_values:
            conditions.append(f"({TIER_SQL}) IN ({','.join('?' * len(tier_values))})")
            params.extend(tier_values)
        direction_values = [value for value in (directions or []) if value]
        if direction_values:
            conditions.append(
                f"cr.match_direction IN ({','.join('?' * len(direction_values))})"
            )
            params.extend(direction_values)
        for source in sources or []:
            if not source:
                continue
            conditions.append(
                "EXISTS ("
                "SELECT 1 FROM json_each("
                "CASE WHEN json_valid(cr.source_keywords) "
                "THEN cr.source_keywords ELSE '[]' END"
                ") AS je WHERE je.value = ?)"
            )
            params.append(source)
        if only_unenriched:
            conditions.append("c.fans IS NULL")
        if min_fans is not None:
            conditions.append("c.fans >= ?")
            params.append(min_fans)
        if max_fans is not None:
            conditions.append("c.fans <= ?")
            params.append(max_fans)
        return " AND ".join(conditions), params

    def rebuild_search_index(self, round_id: int | None = None) -> int:
        if not self._has_fts:
            return 0
        where = ""
        params: list[Any] = []
        if round_id is not None:
            where = " WHERE cr.round_id = ?"
            params.append(round_id)
        with self._conn:
            if round_id is None:
                self._conn.execute("DELETE FROM creator_search")
            else:
                self._conn.execute(
                    "DELETE FROM creator_search WHERE round_id = ?", (str(round_id),)
                )
            cur = self._conn.execute(
                f"""
                INSERT INTO creator_search
                (round_id, platform, user_id, nickname, account_id, executor,
                 raw_title, tags_manual, source_keywords)
                SELECT CAST(cr.round_id AS TEXT), cr.platform, cr.user_id, c.nickname,
                       cr.account_id, cr.executor, cr.raw_title, cr.tags_manual,
                       cr.source_keywords
                FROM creator_rounds cr
                LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
                {where}
                """,
                params,
            )
        return max(0, cur.rowcount)

    def _refresh_search_row(self, round_id: int, platform: str, user_id: str) -> None:
        if not self._has_fts:
            return
        with self._conn:
            self._conn.execute(
                "DELETE FROM creator_search "
                "WHERE round_id = ? AND platform = ? AND user_id = ?",
                (str(round_id), platform, user_id),
            )
            self._conn.execute(
                """
                INSERT INTO creator_search
                (round_id, platform, user_id, nickname, account_id, executor,
                 raw_title, tags_manual, source_keywords)
                SELECT CAST(cr.round_id AS TEXT), cr.platform, cr.user_id, c.nickname,
                       cr.account_id, cr.executor, cr.raw_title, cr.tags_manual,
                       cr.source_keywords
                FROM creator_rounds cr
                LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
                WHERE cr.round_id = ? AND cr.platform = ? AND cr.user_id = ?
                """,
                (round_id, platform, user_id),
            )

    def query_creators(
        self,
        round_id: int | None,
        platform: str | None = None,
        search: str | None = None,
        statuses: Iterable[str] | None = None,
        tiers: Iterable[str] | None = None,
        directions: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
        min_fans: int | None = None,
        max_fans: int | None = None,
        only_unenriched: bool = False,
        sort: str = "fans",
        order: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        from_sql = ALL_ROUNDS_FROM_SQL if round_id is None else CREATOR_FROM_SQL
        where, params = self._creator_conditions(
            round_id,
            platform=platform,
            search=search,
            statuses=statuses,
            tiers=tiers,
            directions=directions,
            sources=sources,
            min_fans=min_fans,
            max_fans=max_fans,
            only_unenriched=only_unenriched,
        )
        direction = "DESC" if str(order).lower() != "asc" else "ASC"
        sort_map = {
            "fans": f"(c.fans IS NULL) ASC, c.fans {direction}",
            "interaction": f"(c.interaction IS NULL) ASC, c.interaction {direction}",
            "liked": f"(c.liked IS NULL) ASC, c.liked {direction}",
            "posted": f"(c.posted IS NULL) ASC, c.posted {direction}",
            "sample_likes": f"cr.sample_likes {direction}",
            "nickname": f"c.nickname {direction}",
            "data_round": f"({DATA_ROUND_SQL}) {direction}",
            "updated_at": f"cr.updated_at {direction}",
        }
        sort_clause = sort_map.get(sort, sort_map["fans"])
        page = max(1, int(page))
        page_size = max(1, min(500, int(page_size)))
        offset = (page - 1) * page_size
        total = self._conn.execute(
            f"SELECT COUNT(*) AS n {from_sql} WHERE {where}",
            params,
        ).fetchone()["n"]
        rows = self._conn.execute(
            f"{CREATOR_SELECT_SQL} {from_sql} WHERE {where} "
            f"ORDER BY {sort_clause}, cr.user_id ASC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }

    def creator_facets(self, round_id: int | None, platform: str | None = None) -> dict[str, Any]:
        from_sql = ALL_ROUNDS_FROM_SQL if round_id is None else CREATOR_FROM_SQL
        base_condition = "cr.rn = 1" if round_id is None else "cr.round_id = ?"
        base_params: list[Any] = [] if round_id is None else [round_id]
        platform_rows = self._conn.execute(
            f"SELECT cr.platform AS value, COUNT(*) AS count {from_sql} "
            f"WHERE {base_condition} GROUP BY cr.platform ORDER BY count DESC",
            base_params,
        ).fetchall()
        platform_condition = " AND cr.platform = ?" if platform else ""
        params: list[Any] = [*base_params]
        if platform:
            params.append(platform)
        status_rows = self._conn.execute(
            f"SELECT cr.status AS value, COUNT(*) AS count {from_sql} "
            f"WHERE {base_condition}{platform_condition} GROUP BY cr.status ORDER BY count DESC",
            params,
        ).fetchall()
        tier_rows = self._conn.execute(
            f"SELECT {TIER_SQL} AS value, COUNT(*) AS count {from_sql} "
            f"WHERE {base_condition}{platform_condition} GROUP BY value ORDER BY count DESC",
            params,
        ).fetchall()
        direction_rows = self._conn.execute(
            f"SELECT cr.match_direction AS value, COUNT(*) AS count {from_sql} "
            f"WHERE {base_condition}{platform_condition} AND cr.match_direction != '' "
            "GROUP BY cr.match_direction ORDER BY count DESC",
            params,
        ).fetchall()
        source_rows = self._conn.execute(
            "SELECT je.value AS value, COUNT(*) AS count "
            f"{from_sql} "
            "JOIN json_each(CASE WHEN json_valid(cr.source_keywords) "
            "THEN cr.source_keywords ELSE '[]' END) AS je "
            f"WHERE {base_condition}{platform_condition} "
            "GROUP BY je.value ORDER BY count DESC",
            params,
        ).fetchall()

        def rows_to_list(rows: list[Any]) -> list[dict[str, Any]]:
            return [{"value": row["value"], "count": int(row["count"])} for row in rows]

        return {
            "platforms": rows_to_list(platform_rows),
            "statuses": rows_to_list(status_rows),
            "tiers": rows_to_list(tier_rows),
            "directions": rows_to_list(direction_rows),
            "sources": rows_to_list(source_rows),
        }

    def creator_detail(self, round_id: int, platform: str, user_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT cr.*, c.*, r.name AS round_name
            FROM creator_rounds cr
            JOIN rounds r ON r.id = cr.round_id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.round_id = ? AND cr.platform = ? AND cr.user_id = ?
            """,
            (round_id, platform, user_id),
        ).fetchone()
        if not row:
            return None
        detail = dict(row)
        notes = self.creator_notes(round_id, platform, user_id)
        detail["notes"] = notes
        return detail

    def creator_notes(self, round_id: int, platform: str, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT nh.keyword, n.*
            FROM note_hits nh
            JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
            WHERE nh.round_id = ? AND n.platform = ? AND n.author_id = ?
            ORDER BY n.liked DESC
            """,
            (round_id, platform, user_id),
        ).fetchall()
        notes: dict[str, dict[str, Any]] = {}
        for row in rows:
            note = notes.setdefault(
                row["note_id"],
                {
                    "note_id": row["note_id"],
                    "title": row["title"],
                    "desc": row["desc"],
                    "type": row["note_type"],
                    "published_at": row["published_at"],
                    "liked": row["liked"],
                    "collected": row["collected"],
                    "comments": row["comments"],
                    "shares": row["shares"],
                    "url": row["url"],
                    "keywords": [],
                },
            )
            if row["keyword"] not in note["keywords"]:
                note["keywords"].append(row["keyword"])
        return list(notes.values())

    def counts(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "rounds": count("rounds"),
            "keywords": count("keywords"),
            "notes": count("notes"),
            "creators": count("creators"),
            "creator_rounds": count("creator_rounds"),
        }

    def round_summary(self, round_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM creator_rounds WHERE round_id = ?) AS creators,
                (SELECT COUNT(*) FROM note_hits WHERE round_id = ?) AS note_hits,
                (SELECT COUNT(DISTINCT note_id) FROM (
                    SELECT n.note_id FROM note_hits nh
                    JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
                    WHERE nh.round_id = ?
                    UNION
                    SELECT top_note_id AS note_id FROM creator_rounds
                    WHERE round_id = ? AND top_note_id IS NOT NULL
                )) AS notes,
                (SELECT COALESCE(SUM(cost), 0) FROM api_calls WHERE round_id = ?) AS cost
            """,
            (round_id, round_id, round_id, round_id, round_id),
        ).fetchone()
        return dict(row)

    def round_metrics(self, round_id: int | None) -> dict[str, int]:
        where = "" if round_id is None else "WHERE cr.round_id = ?"
        params = () if round_id is None else (round_id,)
        row = self._conn.execute(
            f"""
            SELECT
                COUNT(*) AS creators,
                SUM(CASE WHEN c.fans IS NOT NULL THEN 1 ELSE 0 END) AS enriched,
                SUM(CASE WHEN cr.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN cr.status = 'adopted' THEN 1 ELSE 0 END) AS adopted
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            {where}
            """,
            params,
        ).fetchone()
        return {key: int(row[key] or 0) for key in ("creators", "enriched", "pending", "adopted")}

    def all_round_metrics(self) -> dict[str, int]:
        row = self._conn.execute(
            """
            WITH unique_creators AS (
                SELECT cr.platform, cr.user_id,
                       MAX(CASE WHEN c.fans IS NOT NULL THEN 1 ELSE 0 END) AS enriched,
                       MAX(CASE cr.status
                           WHEN 'adopted' THEN 3
                           WHEN 'shortlisted' THEN 2
                           WHEN 'pending' THEN 1
                           ELSE 0
                       END) AS status_rank
                FROM creator_rounds cr
                LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
                GROUP BY cr.platform, cr.user_id
            )
            SELECT
                COUNT(*) AS creators,
                SUM(enriched) AS enriched,
                SUM(CASE WHEN status_rank = 1 THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status_rank = 3 THEN 1 ELSE 0 END) AS adopted
            FROM unique_creators
            """
        ).fetchone()
        return {key: int(row[key] or 0) for key in ("creators", "enriched", "pending", "adopted")}

    def all_status_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            """
            WITH unique_creators AS (
                SELECT cr.platform, cr.user_id,
                       MAX(CASE cr.status
                           WHEN 'adopted' THEN 3
                           WHEN 'shortlisted' THEN 2
                           WHEN 'pending' THEN 1
                           ELSE 0
                       END) AS status_rank
                FROM creator_rounds cr
                GROUP BY cr.platform, cr.user_id
            )
            SELECT status_rank, COUNT(*) AS n FROM unique_creators GROUP BY status_rank
            """
        ).fetchall()
        labels = {0: "rejected", 1: "pending", 2: "shortlisted", 3: "adopted"}
        return {labels[int(row["status_rank"])]: int(row["n"]) for row in rows}

    def all_tier_counts(self) -> dict[str, int]:
        from .normalize import fans_tier

        rows = self._conn.execute(
            """
            WITH unique_creators AS (
                SELECT cr.platform, cr.user_id, MAX(c.fans) AS fans,
                       MAX(cr.tier_label) AS tier_label
                FROM creator_rounds cr
                LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
                GROUP BY cr.platform, cr.user_id
            )
            SELECT fans, tier_label FROM unique_creators
            """
        ).fetchall()
        result = {label: 0 for label in TIER_LABELS}
        result["未补全"] = 0
        for row in rows:
            if row["tier_label"]:
                label = row["tier_label"]
            elif row["fans"] is None:
                label = "未补全"
            else:
                label = fans_tier(row["fans"])
            result[label] = result.get(label, 0) + 1
        return result

    def status_counts(self, round_id: int | None) -> dict[str, int]:
        where = "" if round_id is None else "WHERE round_id = ?"
        params = () if round_id is None else (round_id,)
        rows = self._conn.execute(
            f"SELECT status, COUNT(*) AS n FROM creator_rounds {where} GROUP BY status",
            params,
        ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def tier_counts(self, round_id: int | None) -> dict[str, int]:
        where = "" if round_id is None else "WHERE cr.round_id = ?"
        params = () if round_id is None else (round_id,)
        rows = self._conn.execute(
            f"""
            SELECT {TIER_SQL} AS label, COUNT(*) AS n
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            {where}
            GROUP BY label
            """,
            params,
        ).fetchall()
        result = {label: 0 for label in TIER_LABELS}
        result["未补全"] = 0
        for row in rows:
            result[row["label"]] = result.get(row["label"], 0) + int(row["n"])
        return result

    # ---- jobs ----

    def create_job(self, round_id: int) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO jobs (round_id, status, stage, created_at) VALUES (?, 'pending', '排队中', ?)",
                (round_id, utc_now()),
            )
        return int(cur.lastrowid)

    def claim_job(self, round_id: int, max_active: int | None = None) -> int:
        """Atomically create a job unless the round already has an active one."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if max_active is not None and max_active > 0:
                count = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('pending', 'running')"
                ).fetchone()["n"]
                if int(count) >= max_active:
                    raise JobBusyError(
                        "\u5f53\u524d\u91c7\u96c6\u4efb\u52a1\u5df2\u8fbe\u5e76\u53d1\u4e0a\u9650\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5"
                    )
            active = self._conn.execute(
                "SELECT id FROM jobs WHERE round_id = ? AND status IN ('pending', 'running') "
                "ORDER BY id DESC LIMIT 1",
                (round_id,),
            ).fetchone()
            if active:
                raise JobBusyError(
                    "\u8be5\u8f6e\u6b21\u5df2\u6709\u4efb\u52a1\u5728\u8fd0\u884c\uff0c\u8bf7\u7b49\u5f85\u7ed3\u675f\u6216\u5148\u91cd\u7f6e\u4efb\u52a1"
                )
            cur = self._conn.execute(
                "INSERT INTO jobs (round_id, status, stage, created_at) VALUES (?, 'pending', ?, ?)",
                (round_id, "\u6392\u961f\u4e2d", utc_now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except JobBusyError:
            self._conn.rollback()
            raise
        except Exception:
            self._conn.rollback()
            raise

    def update_job(self, job_id: int, **fields: Any) -> None:
        updates = {key: value for key, value in fields.items() if key in JOB_FIELDS}
        if not updates:
            return
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            self._conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*updates.values(), job_id])

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def latest_job(self, round_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE round_id = ? ORDER BY id DESC LIMIT 1", (round_id,)
        ).fetchone()
        return dict(row) if row else None

    def latest_active_job(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE status IN ('pending', 'running') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def recent_api_calls(self, round_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM api_calls WHERE round_id = ? ORDER BY id DESC LIMIT ?",
            (round_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    # ---- users / sessions / audit ----

    def create_user(
        self,
        username: str,
        password_hash: str | None,
        role: str = "member",
        auth_source: str = "password",
        external_id: str | None = None,
        can_collect: bool = False,
        must_change_password: bool = False,
    ) -> int:
        now = utc_now()
        try:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO users
                    (username, password_hash, auth_source, external_id, role, disabled,
                     can_collect, must_change_password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        password_hash,
                        auth_source,
                        external_id,
                        role,
                        1 if can_collect else 0,
                        1 if must_change_password else 0,
                        now,
                        now,
                    ),
                )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT id, username, role, disabled, can_collect, must_change_password,
                   auth_source, created_at, updated_at, last_login_at
            FROM users ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def count_users(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def count_active_admins(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0"
        ).fetchone()
        return int(row["n"])

    def update_user(
        self,
        user_id: int,
        role: str | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
        can_collect: bool | None = None,
        must_change_password: bool | None = None,
    ) -> None:
        updates: dict[str, Any] = {"updated_at": utc_now()}
        if role is not None:
            updates["role"] = role
        if disabled is not None:
            updates["disabled"] = 1 if disabled else 0
        if password_hash is not None:
            updates["password_hash"] = password_hash
        if can_collect is not None:
            updates["can_collect"] = 1 if can_collect else 0
        if must_change_password is not None:
            updates["must_change_password"] = 1 if must_change_password else 0
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            self._conn.execute(f"UPDATE users SET {sets} WHERE id = ?", [*updates.values(), user_id])

    def update_last_login(self, user_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), user_id),
            )

    def create_session(
        self,
        user_id: int,
        token_hash: str,
        csrf_token: str,
        expires_at: str,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        now = utc_now()
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            self._conn.execute(
                """
                INSERT INTO sessions
                (token_hash, user_id, csrf_token, created_at, expires_at, last_seen, ip, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, csrf_token, now, expires_at, now, ip, user_agent),
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
            (token_hash, utc_now()),
        ).fetchone()
        if not row:
            return None
        user = self._conn.execute(
            "SELECT id, username, role, disabled, can_collect, must_change_password, "
            "auth_source, created_at, updated_at, last_login_at "
            "FROM users WHERE id = ? AND disabled = 0",
            (row["user_id"],),
        ).fetchone()
        if not user:
            return None
        return {"session": dict(row), "user": dict(user)}

    def delete_session(self, token_hash: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int, except_token_hash: str | None = None) -> None:
        with self._conn:
            if except_token_hash:
                self._conn.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
                    (user_id, except_token_hash),
                )
            else:
                self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def count_active_jobs(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('pending', 'running')"
        ).fetchone()
        return int(row["n"])

    def audit(
        self,
        user_id: int | None,
        action: str,
        target: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO audit_log (user_id, action, target, ip, user_agent, details, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    action,
                    target,
                    ip,
                    (user_agent or "")[:300],
                    json.dumps(details or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )
