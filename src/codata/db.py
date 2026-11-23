from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .identity import normalize_identity
from .lead_statuses import (
    DEFAULT_LEAD_SOURCE,
    DEFAULT_LEAD_STATUS,
    LEAD_STATUS_CODES,
    normalize_lead_source,
    normalize_lead_status,
)
from .normalize import TIER_LABELS, derive_tags, normalize_tier_label, utc_now
from .statuses import DEFAULT_STATUS, normalize_status


class JobBusyError(RuntimeError):
    """A round already has a pending or running job."""


SCHEMA_VERSION = 8


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
    archived_at TEXT,
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
    created_at TEXT,
    raw_json TEXT,
    follow_up_user_id INTEGER,
    followed_at TEXT,
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS creator_rounds (
    round_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_keywords TEXT,
    source_labels TEXT,
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
    owner_user_id INTEGER,
    claimed_at TEXT,
    cooperation_platform TEXT,
    raw_title TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (round_id, platform, user_id)
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'xhs',
    nickname TEXT,
    account_id TEXT,
    profile_url TEXT,
    raw_title TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_round_id INTEGER,
    owner_user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'unverified',
    legacy_user_id TEXT,
    raw_json TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    converted_platform TEXT,
    converted_user_id TEXT,
    converted_at TEXT,
    conversion_lock TEXT,
    conversion_locked_at TEXT,
    matched_platform TEXT,
    matched_user_id TEXT,
    matched_round_id INTEGER,
    matched_at TEXT,
    match_confidence TEXT
);

CREATE TABLE IF NOT EXISTS lead_rounds (
    lead_id INTEGER NOT NULL,
    round_id INTEGER NOT NULL,
    source_keywords TEXT,
    raw_title TEXT,
    data_round TEXT,
    top_note_id TEXT,
    sample_notes INTEGER NOT NULL DEFAULT 0,
    sample_likes INTEGER NOT NULL DEFAULT 0,
    sample_collected INTEGER NOT NULL DEFAULT 0,
    sample_comments INTEGER NOT NULL DEFAULT 0,
    sample_interactions INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, round_id)
);

CREATE TABLE IF NOT EXISTS lead_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    provider TEXT,
    query TEXT,
    candidate_user_id TEXT,
    candidate_nickname TEXT,
    candidate_profile_url TEXT,
    confidence TEXT,
    status TEXT NOT NULL,
    error TEXT,
    cost INTEGER NOT NULL DEFAULT 0,
    cost_native REAL NOT NULL DEFAULT 0,
    cost_currency TEXT NOT NULL DEFAULT 'token',
    round_id INTEGER,
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_status_owner ON leads (status, owner_user_id);
CREATE INDEX IF NOT EXISTS idx_leads_platform ON leads (platform, status);
CREATE INDEX IF NOT EXISTS idx_leads_round ON leads (source_round_id);
CREATE INDEX IF NOT EXISTS idx_leads_updated ON leads (updated_at);
CREATE INDEX IF NOT EXISTS idx_leads_account ON leads (platform, account_id);
CREATE INDEX IF NOT EXISTS idx_leads_profile ON leads (platform, profile_url);
CREATE INDEX IF NOT EXISTS idx_lead_rounds_round ON lead_rounds (round_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_attempts_lead ON lead_attempts (lead_id, id);

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
    created_by INTEGER,
    cancelled_by INTEGER,
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT
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
    estimated_cost INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ok',
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

CREATE TABLE IF NOT EXISTS creator_round_sources (
    round_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (round_id, platform, user_id, kind, value)
);

CREATE INDEX IF NOT EXISTS idx_crs_creator_kind
ON creator_round_sources (platform, user_id, kind, value);
CREATE INDEX IF NOT EXISTS idx_crs_round_kind
ON creator_round_sources (round_id, kind, value);
CREATE INDEX IF NOT EXISTS idx_crs_kind_value
ON creator_round_sources (kind, value);

CREATE TRIGGER IF NOT EXISTS trg_creator_round_sources_insert
AFTER INSERT ON creator_rounds
BEGIN
    DELETE FROM creator_round_sources
    WHERE round_id = NEW.round_id AND platform = NEW.platform AND user_id = NEW.user_id;
    INSERT OR IGNORE INTO creator_round_sources (round_id, platform, user_id, kind, value)
    SELECT NEW.round_id, NEW.platform, NEW.user_id, 'keyword', TRIM(je.value)
    FROM json_each(CASE WHEN json_valid(NEW.source_keywords)
                        THEN NEW.source_keywords ELSE '[]' END) AS je
    WHERE TRIM(COALESCE(je.value, '')) != '';
    INSERT OR IGNORE INTO creator_round_sources (round_id, platform, user_id, kind, value)
    SELECT NEW.round_id, NEW.platform, NEW.user_id, 'label', TRIM(je.value)
    FROM json_each(CASE WHEN json_valid(NEW.source_labels)
                        THEN NEW.source_labels ELSE '[]' END) AS je
    WHERE TRIM(COALESCE(je.value, '')) != '';
END;

CREATE TRIGGER IF NOT EXISTS trg_creator_round_sources_update
AFTER UPDATE OF round_id, platform, user_id, source_keywords, source_labels ON creator_rounds
BEGIN
    DELETE FROM creator_round_sources
    WHERE round_id = OLD.round_id AND platform = OLD.platform AND user_id = OLD.user_id;
    INSERT OR IGNORE INTO creator_round_sources (round_id, platform, user_id, kind, value)
    SELECT NEW.round_id, NEW.platform, NEW.user_id, 'keyword', TRIM(je.value)
    FROM json_each(CASE WHEN json_valid(NEW.source_keywords)
                        THEN NEW.source_keywords ELSE '[]' END) AS je
    WHERE TRIM(COALESCE(je.value, '')) != '';
    INSERT OR IGNORE INTO creator_round_sources (round_id, platform, user_id, kind, value)
    SELECT NEW.round_id, NEW.platform, NEW.user_id, 'label', TRIM(je.value)
    FROM json_each(CASE WHEN json_valid(NEW.source_labels)
                        THEN NEW.source_labels ELSE '[]' END) AS je
    WHERE TRIM(COALESCE(je.value, '')) != '';
END;

CREATE TRIGGER IF NOT EXISTS trg_creator_round_sources_delete
AFTER DELETE ON creator_rounds
BEGIN
    DELETE FROM creator_round_sources
    WHERE round_id = OLD.round_id AND platform = OLD.platform AND user_id = OLD.user_id;
END;
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
LEFT JOIN users fu ON fu.id = c.follow_up_user_id
"""

ALL_ROUNDS_FROM_SQL = """
FROM (
    SELECT cr.*,
           ROW_NUMBER() OVER (
               PARTITION BY cr.platform, cr.user_id
               ORDER BY cr.round_id DESC, cr.updated_at DESC
           ) AS rn,
           (
               SELECT json_group_array(value)
               FROM (
                   SELECT DISTINCT s.value AS value
                   FROM creator_round_sources s
                   JOIN rounds active2 ON active2.id = s.round_id
                   WHERE active2.archived_at IS NULL
                     AND s.platform = cr.platform AND s.user_id = cr.user_id
                     AND s.kind = 'keyword'
                   ORDER BY value
               )
           ) AS source_keywords_all,
           (
               SELECT json_group_array(value)
               FROM (
                   SELECT DISTINCT s.value AS value
                   FROM creator_round_sources s
                   JOIN rounds active2 ON active2.id = s.round_id
                   WHERE active2.archived_at IS NULL
                     AND s.platform = cr.platform AND s.user_id = cr.user_id
                     AND s.kind = 'label'
                   ORDER BY value
               )
           ) AS source_labels_all
    FROM creator_rounds cr
    JOIN rounds active_round ON active_round.id = cr.round_id
    WHERE active_round.archived_at IS NULL
) cr
JOIN rounds r ON r.id = cr.round_id
LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
LEFT JOIN notes n ON n.platform = cr.platform AND n.note_id = cr.top_note_id
LEFT JOIN users fu ON fu.id = c.follow_up_user_id
"""

CREATOR_SELECT_SQL = f"""
SELECT cr.round_id, cr.platform, cr.user_id,
       c.nickname, c.avatar, c.fans, c.follows, c.posted, c.liked, c.collected, c.interaction,
       c.red_id, c.profile_url, c.verify_info, c.ip_location, c.tags_json AS creator_tags,
       cr.source_keywords, cr.source_labels,
       cr.sample_notes, cr.sample_likes, cr.sample_collected,
       cr.sample_comments, cr.sample_interactions, cr.top_note_id,
       cr.auto_tags, cr.status, cr.match_direction, cr.tags_manual, cr.remark,
       cr.data_round, cr.tier_label, cr.account_id,
       c.follow_up_user_id AS owner_user_id, c.followed_at AS claimed_at,
       COALESCE(fu.username, '') AS owner_username,
       cr.cooperation_platform, cr.raw_title,
       n.title AS top_title, n.url AS top_url, n.liked AS top_liked,
       r.name AS round_name, {TIER_SQL} AS derived_tier
"""

CREATOR_SELECT_ALL_SQL = CREATOR_SELECT_SQL.replace(
    "cr.source_keywords, cr.source_labels,",
    "COALESCE(cr.source_keywords_all, cr.source_keywords) AS source_keywords, "
    "COALESCE(cr.source_labels_all, cr.source_labels) AS source_labels,",
)

LEAD_FROM_SQL = """
FROM leads l
LEFT JOIN users u ON u.id = l.owner_user_id
LEFT JOIN rounds r ON r.id = l.source_round_id
LEFT JOIN rounds mr ON mr.id = l.matched_round_id
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
    "created_by",
    "cancelled_by",
    "cancelled_at",
    "started_at",
    "finished_at",
    "heartbeat_at",
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
                self._migrate_schema()
                _SCHEMA_READY[db_key] = (self._has_fts, self._fts_trigram)
            else:
                self._has_fts, self._fts_trigram = state

    def _detect_fts(self) -> None:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'creator_search'"
        ).fetchone()
        sql = str(row["sql"] or "").lower() if row else ""
        self._has_fts = bool(row)
        self._fts_trigram = "trigram" in sql

    def _migrate_schema(self) -> None:
        current = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if current >= SCHEMA_VERSION:
            self._detect_fts()
            self._ensure_creator_sources()
            return
        has_data = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rounds'"
        ).fetchone()
        if has_data:
            self._backup_before_migration(current)
        self._init_db()
        self._migrate_source_labels()
        self._rebuild_creator_sources()
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def _migrate_source_labels(self) -> None:
        rows = self._conn.execute(
            """
            SELECT cr.round_id, cr.platform, cr.user_id, cr.source_keywords,
                   cr.top_note_id, c.raw_json AS creator_raw_json
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE TRIM(COALESCE(cr.source_keywords, '')) NOT IN ('', '[]')
            """
        ).fetchall()
        keyword_cache: dict[int, set[str]] = {}
        updates: list[tuple[str, str, int, str, str]] = []
        for row in rows:
            try:
                values = json.loads(row["source_keywords"] or "[]")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(values, list):
                continue
            values = list(
                dict.fromkeys(
                    str(value).strip() for value in values if str(value).strip()
                )
            )
            raw_json = str(row["creator_raw_json"] or "")
            imported = (
                str(row["user_id"] or "").startswith("import:")
                or str(row["top_note_id"] or "").startswith("import:")
                or '"数据来源"' in raw_json
                or '"序号"' in raw_json
            )
            if imported:
                keywords: list[str] = []
                labels = values
            else:
                round_id = int(row["round_id"])
                if round_id not in keyword_cache:
                    keyword_cache[round_id] = {
                        str(item["word"])
                        for item in self._conn.execute(
                            "SELECT k.word FROM round_keywords rk "
                            "JOIN keywords k ON k.id = rk.keyword_id WHERE rk.round_id = ?",
                            (round_id,),
                        ).fetchall()
                    }
                words = keyword_cache[round_id]
                keywords = [value for value in values if value in words]
                labels = [value for value in values if value not in words]
            if labels or len(keywords) != len(values):
                updates.append(
                    (
                        json.dumps(keywords, ensure_ascii=False),
                        json.dumps(labels, ensure_ascii=False),
                        int(row["round_id"]),
                        str(row["platform"]),
                        str(row["user_id"]),
                    )
                )
        if updates:
            with self._conn:
                self._conn.executemany(
                    "UPDATE creator_rounds SET source_keywords = ?, source_labels = ? "
                    "WHERE round_id = ? AND platform = ? AND user_id = ?",
                    updates,
                )
            self.rebuild_search_index()

    def _rebuild_creator_sources(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM creator_round_sources")
            for kind, column in (("keyword", "source_keywords"), ("label", "source_labels")):
                self._conn.execute(
                    f"""
                    INSERT OR IGNORE INTO creator_round_sources
                    (round_id, platform, user_id, kind, value)
                    SELECT cr.round_id, cr.platform, cr.user_id, ?,
                           TRIM(je.value)
                    FROM creator_rounds cr
                    JOIN json_each(
                        CASE WHEN json_valid(cr.{column})
                        THEN cr.{column} ELSE '[]' END
                    ) AS je
                    WHERE TRIM(COALESCE(je.value, '')) != ''
                    """,
                    (kind,),
                )

    def _ensure_creator_sources(self) -> None:
        actual = int(
            self._conn.execute("SELECT COUNT(*) FROM creator_round_sources").fetchone()[0]
        )
        expected = int(
            self._conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT cr.round_id, cr.platform, cr.user_id, 'keyword' AS kind,
                           TRIM(je.value) AS value
                    FROM creator_rounds cr
                    JOIN json_each(CASE WHEN json_valid(cr.source_keywords)
                                   THEN cr.source_keywords ELSE '[]' END) AS je
                    WHERE TRIM(COALESCE(je.value, '')) != ''
                    UNION
                    SELECT cr.round_id, cr.platform, cr.user_id, 'label' AS kind,
                           TRIM(je.value) AS value
                    FROM creator_rounds cr
                    JOIN json_each(CASE WHEN json_valid(cr.source_labels)
                                   THEN cr.source_labels ELSE '[]' END) AS je
                    WHERE TRIM(COALESCE(je.value, '')) != ''
                )
                """
            ).fetchone()[0]
        )
        if actual != expected:
            self._rebuild_creator_sources()

    def _backup_before_migration(self, version: int) -> Path:
        return self.backup(f"schema-v{version}")

    def backup(self, label: str = "manual") -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:60]
        path = self.root / f"codata.sqlite.bak-{safe or 'manual'}-{stamp}"
        target = sqlite3.connect(path)
        try:
            self._conn.backup(target)
        finally:
            target.close()
        return path

    def prune_backups(
        self,
        retention_days: int = 90,
        retention_count: int = 10,
        *,
        apply: bool = False,
    ) -> dict[str, Any]:
        paths = [
            path
            for path in self.root.glob("codata.sqlite.bak-*")
            if path.is_file()
        ]
        stale: set[Path] = set()
        if retention_days > 0:
            cutoff = datetime.now().timestamp() - retention_days * 86400
            stale.update(path for path in paths if path.stat().st_mtime < cutoff)
        ordered = sorted(
            paths,
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
        if retention_count > 0:
            stale.update(ordered[retention_count:])
        report: dict[str, Any] = {
            "backups": len(paths),
            "expired": len(stale),
            "bytes": sum(path.stat().st_size for path in stale),
            "deleted": 0,
            "errors": [],
        }
        if not apply:
            return report
        for path in sorted(stale):
            try:
                path.unlink()
                report["deleted"] += 1
            except OSError as exc:
                report["errors"].append(f"{path.name}: {exc}")
        return report

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self):
        if self._conn.in_transaction:
            yield
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _write(self, commit: bool = True):
        return self._conn if commit else nullcontext()

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
        if "archived_at" not in round_columns:
            self._conn.execute("ALTER TABLE rounds ADD COLUMN archived_at TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rounds_archived ON rounds (archived_at)"
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
        creator_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(creators)").fetchall()
        }
        if "created_at" not in creator_columns:
            self._conn.execute("ALTER TABLE creators ADD COLUMN created_at TEXT")
            self._conn.execute(
                """
                UPDATE creators SET created_at = COALESCE(
                    (SELECT MIN(n.first_seen) FROM notes n
                     WHERE n.platform = creators.platform
                       AND n.author_id = creators.user_id),
                    fetched_at
                )
                WHERE created_at IS NULL
                """
            )
        if "follow_up_user_id" not in creator_columns:
            self._conn.execute("ALTER TABLE creators ADD COLUMN follow_up_user_id INTEGER")
            self._conn.execute("ALTER TABLE creators ADD COLUMN followed_at TEXT")
            self._conn.execute(
                """
                UPDATE creators SET
                    follow_up_user_id = (
                        SELECT cr.owner_user_id FROM creator_rounds cr
                        WHERE cr.platform = creators.platform
                          AND cr.user_id = creators.user_id
                          AND cr.owner_user_id IS NOT NULL
                        ORDER BY cr.round_id DESC, cr.updated_at DESC
                        LIMIT 1
                    ),
                    followed_at = (
                        SELECT cr.claimed_at FROM creator_rounds cr
                        WHERE cr.platform = creators.platform
                          AND cr.user_id = creators.user_id
                          AND cr.owner_user_id IS NOT NULL
                        ORDER BY cr.round_id DESC, cr.updated_at DESC
                        LIMIT 1
                    )
                WHERE EXISTS (
                    SELECT 1 FROM creator_rounds cr
                    WHERE cr.platform = creators.platform
                      AND cr.user_id = creators.user_id
                      AND cr.owner_user_id IS NOT NULL
                )
                """
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creators_follow_up ON creators (follow_up_user_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creators_created_at ON creators (created_at)"
        )
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(creator_rounds)").fetchall()
        }
        for column in (
            "data_round",
            "tier_label",
            "account_id",
            "cooperation_platform",
            "raw_title",
            "source_labels",
        ):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE creator_rounds ADD COLUMN {column} TEXT")
        if "owner_user_id" not in existing:
            self._conn.execute("ALTER TABLE creator_rounds ADD COLUMN owner_user_id INTEGER")
        if "claimed_at" not in existing:
            self._conn.execute("ALTER TABLE creator_rounds ADD COLUMN claimed_at TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_rounds_owner "
            "ON creator_rounds (round_id, owner_user_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_rounds_owner_user "
            "ON creator_rounds (owner_user_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_rounds_lookup "
            "ON creator_rounds (platform, user_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_rounds_latest "
            "ON creator_rounds (platform, user_id, round_id DESC, updated_at DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_note_hits_note "
            "ON note_hits (platform, note_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_legacy "
            "ON leads (platform, legacy_user_id)"
        )
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
        if "estimated_cost" not in api_columns:
            self._conn.execute(
                "ALTER TABLE api_calls ADD COLUMN estimated_cost INTEGER NOT NULL DEFAULT 0"
            )
        if "attempt" not in api_columns:
            self._conn.execute(
                "ALTER TABLE api_calls ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1"
            )
        if "status" not in api_columns:
            self._conn.execute(
                "ALTER TABLE api_calls ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'"
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
        lead_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        if "conversion_lock" not in lead_columns:
            self._conn.execute("ALTER TABLE leads ADD COLUMN conversion_lock TEXT")
        if "conversion_locked_at" not in lead_columns:
            self._conn.execute("ALTER TABLE leads ADD COLUMN conversion_locked_at TEXT")
        lead_round_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(lead_rounds)").fetchall()
        }
        if "source_labels" not in lead_round_columns:
            self._conn.execute("ALTER TABLE lead_rounds ADD COLUMN source_labels TEXT")
        needs_lead_normalization = False
        for column, ddl in (
            ("matched_platform", "TEXT"),
            ("matched_user_id", "TEXT"),
            ("matched_round_id", "INTEGER"),
            ("matched_at", "TEXT"),
            ("match_confidence", "TEXT"),
        ):
            if column not in lead_columns:
                self._conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")
                needs_lead_normalization = True
        attempt_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(lead_attempts)").fetchall()
        }
        if "round_id" not in attempt_columns:
            self._conn.execute("ALTER TABLE lead_attempts ADD COLUMN round_id INTEGER")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_account ON leads (platform, account_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_profile ON leads (platform, profile_url)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_matched_round ON leads (matched_round_id)"
        )
        self._conn.commit()
        if needs_lead_normalization:
            self.normalize_lead_identities()
        job_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "heartbeat_at" not in job_columns:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT")
        if "created_by" not in job_columns:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN created_by INTEGER")
        if "cancelled_by" not in job_columns:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN cancelled_by INTEGER")
        if "cancelled_at" not in job_columns:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN cancelled_at TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status "
            "ON jobs (status, heartbeat_at)"
        )
        self._conn.commit()

        existing_fts = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'creator_search'"
        ).fetchone()
        if existing_fts:
            sql = str(existing_fts["sql"] or "").lower()
            self._has_fts = True
            self._fts_trigram = "trigram" in sql
        else:
            for tokenizer in ("trigram", "unicode61"):
                try:
                    self._conn.execute(
                        "CREATE VIRTUAL TABLE creator_search USING fts5("
                        "nickname, account_id, raw_title, tags_manual, source_keywords, "
                        "source_labels, round_id UNINDEXED, platform UNINDEXED, "
                        "user_id UNINDEXED, "
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
        indexes = self.try_create_lead_unique_indexes()
        if indexes and not all(indexes.values()):
            print("[leads] 存在重复线索，唯一索引未启用；可运行 codata-admin leads-dedupe --apply 清理")

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
        estimated_cost: int | None = None,
        attempt: int = 1,
        status: str = "ok",
        commit: bool = True,
    ) -> None:
        with self._write(commit):
            self._conn.execute(
                """
                INSERT INTO api_calls
                (round_id, job_id, platform, provider, kind, path, params, code, cost,
                 cost_native, cost_currency, estimated_cost, attempt, status,
                 duration_ms, raw_file, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    int(estimated_cost if estimated_cost is not None else cost),
                    max(1, int(attempt or 1)),
                    status,
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

    def sum_failed_estimate(self, job_id: int | None = None) -> int:
        if job_id is None:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) AS total FROM api_calls "
                "WHERE status = 'error'"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) AS total FROM api_calls "
                "WHERE status = 'error' AND job_id = ?",
                (job_id,),
            ).fetchone()
        return int(row["total"] or 0)

    # ---- keywords / rounds ----

    def ensure_keyword(self, word: str, category: str | None = None, commit: bool = True) -> int:
        word = (word or "").strip()
        if not word:
            raise ValueError("关键词不能为空")
        category = (category or "").strip() or None
        row = self._conn.execute(
            "SELECT id, category FROM keywords WHERE word = ? COLLATE NOCASE",
            (word,),
        ).fetchone()
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

    def get_keyword(self, keyword_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM keywords WHERE id = ?", (keyword_id,)
        ).fetchone()
        return dict(row) if row else None

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
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            used = self._conn.execute(
                "SELECT COUNT(*) AS n FROM round_keywords WHERE keyword_id = ?",
                (keyword_id,),
            ).fetchone()["n"]
            if used:
                raise ValueError("该关键词已被轮次使用，请改为停用")
            self._conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

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
                keyword_row = self._conn.execute(
                    "SELECT enabled FROM keywords WHERE id = ?", (keyword_id,)
                ).fetchone()
                if keyword_row is not None and not int(keyword_row["enabled"]):
                    raise ValueError(f"关键词已停用，请先在关键词库启用：{word}")
                self._conn.execute(
                    "INSERT OR IGNORE INTO round_keywords (round_id, keyword_id, pages) VALUES (?, ?, ?)",
                    (round_id, keyword_id, pages),
                )
        return round_id

    def get_round(self, round_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
        return dict(row) if row else None

    def list_rounds(
        self, limit: int | None = None, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        limit_sql = " LIMIT ?" if limit is not None else ""
        params: list[Any] = [int(limit)] if limit is not None else []
        where = "" if include_archived else "WHERE r.archived_at IS NULL"
        rows = self._conn.execute(
            f"""
            SELECT r.*,
                   COALESCE((
                       SELECT GROUP_CONCAT(k.word, '、')
                       FROM round_keywords rk JOIN keywords k ON k.id = rk.keyword_id
                       WHERE rk.round_id = r.id
                   ), '') AS keywords,
                   (SELECT COUNT(*) FROM creator_rounds cr WHERE cr.round_id = r.id) AS creators
            FROM rounds r
            {where}
            ORDER BY r.id DESC{limit_sql}
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def archive_round(self, round_id: int) -> dict[str, Any]:
        row = self.get_round(round_id)
        if not row:
            raise ValueError(f"轮次不存在: {round_id}")
        active = self._conn.execute(
            "SELECT 1 FROM jobs WHERE round_id = ? AND status IN ('pending', 'running') LIMIT 1",
            (round_id,),
        ).fetchone()
        if active:
            raise ValueError("该轮次仍有运行中的任务，请先取消或等待结束")
        if row.get("archived_at"):
            return row
        with self._conn:
            self._conn.execute(
                "UPDATE rounds SET archived_at = ? WHERE id = ?",
                (utc_now(), round_id),
            )
        return self.get_round(round_id) or row

    def restore_round(self, round_id: int) -> dict[str, Any]:
        row = self.get_round(round_id)
        if not row:
            raise ValueError(f"轮次不存在: {round_id}")
        if not row.get("archived_at"):
            return row
        with self._conn:
            self._conn.execute(
                "UPDATE rounds SET archived_at = NULL WHERE id = ?", (round_id,)
            )
        return self.get_round(round_id) or row

    def lead_link_count(self, round_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM lead_rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        return int(row["n"] or 0)

    def round_impact(self, round_id: int) -> dict[str, Any]:
        row = self.get_round(round_id)
        if not row:
            raise ValueError(f"轮次不存在: {round_id}")
        counts: dict[str, Any] = {
            "round_id": round_id,
            "name": row.get("name") or "",
            "archived_at": row.get("archived_at"),
        }
        queries = {
            "creators": "SELECT COUNT(*) AS n FROM creator_rounds WHERE round_id = ?",
            "creators_reviewed": "SELECT COUNT(*) AS n FROM creator_rounds cr "
            "LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id "
            "WHERE cr.round_id = ? AND (cr.status <> 'pending' OR c.follow_up_user_id IS NOT NULL)",
            "creators_claimed": "SELECT COUNT(*) AS n FROM creator_rounds cr "
            "LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id "
            "WHERE cr.round_id = ? AND c.follow_up_user_id IS NOT NULL",
            "notes": "SELECT COUNT(DISTINCT nh.note_id) AS n FROM note_hits nh "
            "WHERE nh.round_id = ?",
            "note_hits": "SELECT COUNT(*) AS n FROM note_hits WHERE round_id = ?",
            "leads": "SELECT COUNT(DISTINCT lead_id) AS n FROM lead_rounds WHERE round_id = ?",
            "leads_converted": "SELECT COUNT(DISTINCT lr.lead_id) AS n FROM lead_rounds lr "
            "JOIN leads l ON l.id = lr.lead_id WHERE lr.round_id = ? AND l.status = 'converted'",
            "jobs": "SELECT COUNT(*) AS n FROM jobs WHERE round_id = ?",
            "jobs_active": "SELECT COUNT(*) AS n FROM jobs "
            "WHERE round_id = ? AND status IN ('pending', 'running')",
            "api_calls": "SELECT COUNT(*) AS n FROM api_calls WHERE round_id = ?",
        }
        for key, sql in queries.items():
            counts[key] = int(self._conn.execute(sql, (round_id,)).fetchone()["n"] or 0)
        cost = self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS n FROM api_calls WHERE round_id = ?",
            (round_id,),
        ).fetchone()["n"]
        counts["cost_tokens"] = int(cost or 0)
        raw_files = [
            item["raw_file"]
            for item in self._conn.execute(
                "SELECT DISTINCT raw_file FROM api_calls "
                "WHERE round_id = ? AND raw_file IS NOT NULL AND raw_file != ''",
                (round_id,),
            ).fetchall()
        ]
        counts["raw_files"] = len(raw_files)
        log_dir = self.root / "logs"
        counts["log_files"] = len(list(log_dir.glob(f"round_{round_id}_*.log")))
        counts["can_purge"] = counts["jobs_active"] == 0 and counts["leads"] == 0
        return counts

    def round_keyword_words(self, round_id: int) -> set[str]:
        rows = self._conn.execute(
            "SELECT k.word FROM round_keywords rk "
            "JOIN keywords k ON k.id = rk.keyword_id WHERE rk.round_id = ?",
            (round_id,),
        ).fetchall()
        return {str(row["word"]) for row in rows}

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

    def delete_round(self, round_id: int, force: bool = False) -> dict[str, Any]:
        row = self.get_round(round_id)
        if not row:
            raise ValueError(f"轮次不存在: {round_id}")
        active = self._conn.execute(
            "SELECT 1 FROM jobs WHERE round_id = ? AND status IN ('pending', 'running') LIMIT 1",
            (round_id,),
        ).fetchone()
        if active:
            raise ValueError("该轮次仍有运行中的任务，请先取消或等待结束")
        lead_links = self.lead_link_count(round_id)
        if lead_links and not force:
            raise ValueError(
                f"该轮次关联 {lead_links} 条线索，不能永久删除；请改为归档。"
                f"如确需强制删除，请使用命令行 --force 并先备份数据库。"
            )
        raw_files = [
            Path(item["raw_file"])
            for item in self._conn.execute(
                "SELECT DISTINCT raw_file FROM api_calls "
                "WHERE round_id = ? AND raw_file IS NOT NULL AND raw_file != ''",
                (round_id,),
            ).fetchall()
        ]
        counts: dict[str, Any] = {"lead_links": lead_links}
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM lead_rounds WHERE round_id = ?", (round_id,)
            )
            counts["lead_rounds"] = max(0, cur.rowcount)
            cur = self._conn.execute(
                "UPDATE leads SET source_round_id = NULL, updated_at = ? "
                "WHERE source_round_id = ?",
                (utc_now(), round_id),
            )
            counts["leads_unlinked"] = max(0, cur.rowcount)
            self._conn.execute(
                "UPDATE lead_attempts SET round_id = NULL WHERE round_id = ?",
                (round_id,),
            )
            cur = self._conn.execute(
                """
                UPDATE leads SET matched_platform = NULL, matched_user_id = NULL,
                    matched_round_id = NULL, matched_at = NULL, match_confidence = NULL
                WHERE matched_round_id = ?
                """,
                (round_id,),
            )
            counts["leads_unmatched"] = max(0, cur.rowcount)
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
                AND NOT EXISTS (
                    SELECT 1 FROM leads l
                    WHERE l.converted_platform = creators.platform
                      AND l.converted_user_id = creators.user_id
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
        log_files = list((self.root / "logs").glob(f"round_{round_id}_*.log"))
        trash = self.root / "trash" / f"round_{round_id}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        moved_files = 0
        for path in [*raw_files, *log_files]:
            try:
                path = Path(path)
                if not path.exists():
                    continue
                trash.mkdir(parents=True, exist_ok=True)
                target = trash / path.name
                if target.exists():
                    target = trash / f"{path.stem}-{moved_files}{path.suffix}"
                shutil.move(str(path), str(target))
                moved_files += 1
            except OSError:
                pass
        counts["raw_files"] = len(raw_files)
        counts["log_files"] = len(log_files)
        counts["trashed_files"] = moved_files
        counts["trash_dir"] = str(trash) if moved_files else ""
        return {"id": round_id, "name": row["name"], **counts}

    def cleanup_orphans(self, keep_enriched: bool = True) -> dict[str, int]:
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
                "AND cr.user_id = creators.user_id) "
                "AND NOT EXISTS (SELECT 1 FROM leads l "
                "WHERE l.converted_platform = creators.platform "
                "AND l.converted_user_id = creators.user_id) "
                "AND (creators.fetched_at IS NULL OR ? = 0)",
                (1 if keep_enriched else 0,),
            )
            counts["creators"] = max(0, cur.rowcount)
            cur = self._conn.execute(
                """
                UPDATE leads SET matched_platform = NULL, matched_user_id = NULL,
                    matched_round_id = NULL, matched_at = NULL, match_confidence = NULL
                WHERE matched_user_id IS NOT NULL AND matched_user_id <> ''
                  AND NOT EXISTS (
                      SELECT 1 FROM creators c
                      WHERE c.platform = leads.matched_platform
                        AND c.user_id = leads.matched_user_id
                  )
                """
            )
            counts["lead_matches"] = max(0, cur.rowcount)
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
                    "(SELECT 1 FROM creator_rounds cr "
                    "WHERE cr.platform = creator_search.platform "
                    "AND cr.user_id = creator_search.user_id "
                    "AND CAST(cr.round_id AS TEXT) = creator_search.round_id)"
                )
                counts["search_rows"] = max(0, cur.rowcount)
        return counts

    def prune_round_creators(self, round_id: int, max_creators: int | None) -> int:
        if not max_creators or max_creators <= 0:
            return 0
        protected = [
            (row["platform"], row["user_id"])
            for row in self._conn.execute(
                "SELECT cr.platform, cr.user_id FROM creator_rounds cr "
                "LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id "
                "WHERE cr.round_id = ? AND (cr.status <> 'pending' OR c.follow_up_user_id IS NOT NULL)",
                (round_id,),
            ).fetchall()
        ]
        remaining = max(0, int(max_creators) - len(protected))
        pending = [
            (row["platform"], row["user_id"])
            for row in self._conn.execute(
                """
                SELECT cr.platform, cr.user_id
                FROM creator_rounds cr
                LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
                WHERE cr.round_id = ? AND cr.status = 'pending'
                  AND c.follow_up_user_id IS NULL
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
        keep = protected + pending
        if not keep:
            return 0
        keep_sql = " OR ".join("(platform = ? AND user_id = ?)" for _ in keep)
        keep_params = [value for pair in keep for value in pair]
        with self._conn:
            cur = self._conn.execute(
                f"DELETE FROM creator_rounds WHERE round_id = ? AND status = 'pending' "
                f"AND NOT ({keep_sql})",
                (round_id, *keep_params),
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
                    author_id = COALESCE(NULLIF(excluded.author_id, ''), notes.author_id),
                    title = COALESCE(NULLIF(excluded.title, ''), notes.title),
                    desc = COALESCE(NULLIF(excluded.desc, ''), notes.desc),
                    note_type = COALESCE(NULLIF(excluded.note_type, ''), notes.note_type),
                    published_at = COALESCE(excluded.published_at, notes.published_at),
                    liked = COALESCE(NULLIF(excluded.liked, 0), notes.liked),
                    collected = COALESCE(NULLIF(excluded.collected, 0), notes.collected),
                    comments = COALESCE(NULLIF(excluded.comments, 0), notes.comments),
                    shares = COALESCE(NULLIF(excluded.shares, 0), notes.shares),
                    url = COALESCE(NULLIF(excluded.url, ''), notes.url),
                    hashtags = COALESCE(NULLIF(excluded.hashtags, ''), notes.hashtags),
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
                INSERT INTO creators
                (platform, user_id, red_id, nickname, avatar, profile_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    red_id = COALESCE(NULLIF(excluded.red_id, ''), creators.red_id),
                    nickname = COALESCE(NULLIF(excluded.nickname, ''), creators.nickname),
                    avatar = COALESCE(NULLIF(excluded.avatar, ''), creators.avatar),
                    profile_url = COALESCE(NULLIF(excluded.profile_url, ''), creators.profile_url)
                """,
                (
                    creator.get("platform", "xhs"),
                    creator["user_id"],
                    creator.get("red_id"),
                    creator.get("nickname"),
                    creator.get("avatar"),
                    creator.get("profile_url"),
                    utc_now(),
                ),
            )

    def upsert_creator(self, creator: dict[str, Any], *, commit: bool = True) -> None:
        if not creator.get("user_id"):
            raise ValueError("达人 user_id 不能为空")
        with self._write(commit):
            self._conn.execute(
                """
                INSERT INTO creators
                (platform, user_id, red_id, nickname, avatar, fans, follows, liked, collected,
                 posted, interaction, desc, tags_json, ip_location, verify_info, profile_url,
                 fetched_at, created_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    red_id = COALESCE(NULLIF(excluded.red_id, ''), creators.red_id),
                    nickname = COALESCE(NULLIF(excluded.nickname, ''), creators.nickname),
                    avatar = COALESCE(NULLIF(excluded.avatar, ''), creators.avatar),
                    fans = COALESCE(excluded.fans, creators.fans),
                    follows = COALESCE(excluded.follows, creators.follows),
                    liked = COALESCE(excluded.liked, creators.liked),
                    collected = COALESCE(excluded.collected, creators.collected),
                    posted = COALESCE(excluded.posted, creators.posted),
                    interaction = COALESCE(excluded.interaction, creators.interaction),
                    desc = COALESCE(NULLIF(excluded.desc, ''), creators.desc),
                    tags_json = CASE WHEN excluded.tags_json IN ('', '[]', 'null')
                        THEN creators.tags_json ELSE excluded.tags_json END,
                    ip_location = COALESCE(NULLIF(excluded.ip_location, ''), creators.ip_location),
                    verify_info = COALESCE(NULLIF(excluded.verify_info, ''), creators.verify_info),
                    profile_url = COALESCE(NULLIF(excluded.profile_url, ''), creators.profile_url),
                    fetched_at = COALESCE(excluded.fetched_at, creators.fetched_at),
                    raw_json = COALESCE(NULLIF(excluded.raw_json, ''), creators.raw_json)
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
                    utc_now(),
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
                    AND substr(ba.attempted_at, 1, 23) >=
                        strftime('%Y-%m-%dT%H:%M:%f', 'now', ?)
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
    ) -> list[int]:
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
                    red_id = COALESCE(NULLIF(?, ''), red_id),
                    nickname = COALESCE(NULLIF(?, ''), nickname),
                    avatar = COALESCE(NULLIF(?, ''), avatar),
                    fans = COALESCE(?, fans),
                    follows = COALESCE(?, follows),
                    liked = COALESCE(?, liked),
                    collected = COALESCE(?, collected),
                    posted = COALESCE(?, posted),
                    interaction = COALESCE(?, interaction),
                    desc = COALESCE(NULLIF(?, ''), desc),
                    tags_json = COALESCE(NULLIF(NULLIF(?, ''), '[]'), tags_json),
                    ip_location = COALESCE(NULLIF(?, ''), ip_location),
                    verify_info = COALESCE(NULLIF(?, ''), verify_info),
                    profile_url = COALESCE(NULLIF(?, ''), profile_url),
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
        round_ids = [
            int(row["round_id"])
            for row in self._conn.execute(
                "SELECT DISTINCT round_id FROM creator_rounds "
                "WHERE platform = ? AND user_id = ?",
                (platform, imported_user_id),
            ).fetchall()
        ]
        if self._has_fts:
            for round_id in round_ids:
                self.rebuild_search_index(round_id)
        return round_ids

    # ---- aggregation ----

    def round_author_stats(self, round_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT nh.keyword, nh.page, n.*, c.profile_url AS profile_url,
                   c.verify_info AS creator_verify_info
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
                    "verify_info": row["creator_verify_info"] or "",
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
            entry["keywords"].sort()
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
                auto_tags = derive_tags(
                    [round_category] if round_category else [],
                    entry["hashtags"],
                    entry.get("verify_info") or "",
                )
                self._conn.execute(
                    """
                    INSERT INTO creator_rounds
                    (round_id, platform, user_id, source_keywords, source_labels, sample_notes, sample_likes,
                     sample_collected, sample_comments, sample_interactions, top_note_id,
                     auto_tags, status, match_direction, tags_manual, remark, updated_at)
                    VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)
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
                     verify_info, profile_url, fetched_at, created_at, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, user_id) DO UPDATE SET
                        red_id = COALESCE(NULLIF(excluded.red_id, ''), creators.red_id),
                        nickname = COALESCE(NULLIF(excluded.nickname, ''), creators.nickname),
                        avatar = COALESCE(NULLIF(excluded.avatar, ''), creators.avatar),
                        fans = COALESCE(excluded.fans, creators.fans),
                        follows = COALESCE(excluded.follows, creators.follows),
                        liked = COALESCE(excluded.liked, creators.liked),
                        collected = COALESCE(excluded.collected, creators.collected),
                        posted = COALESCE(excluded.posted, creators.posted),
                        interaction = COALESCE(excluded.interaction, creators.interaction),
                        desc = COALESCE(NULLIF(excluded.desc, ''), creators.desc),
                        tags_json = CASE WHEN excluded.tags_json IN ('', '[]', 'null')
                            THEN creators.tags_json ELSE excluded.tags_json END,
                        ip_location = COALESCE(NULLIF(excluded.ip_location, ''), creators.ip_location),
                        verify_info = COALESCE(NULLIF(excluded.verify_info, ''), creators.verify_info),
                        profile_url = COALESCE(NULLIF(excluded.profile_url, ''), creators.profile_url),
                        fetched_at = COALESCE(excluded.fetched_at, creators.fetched_at),
                        raw_json = COALESCE(NULLIF(excluded.raw_json, ''), creators.raw_json)
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
                        now,
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
                    (round_id, platform, user_id, source_keywords, source_labels, sample_notes, sample_likes,
                     sample_collected, sample_comments, sample_interactions, top_note_id,
                     auto_tags, status, match_direction, tags_manual, remark, data_round,
                     tier_label, account_id, cooperation_platform, raw_title, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(round_id, platform, user_id) DO UPDATE SET
                        source_keywords = CASE WHEN excluded.source_keywords IN ('', '[]')
                            THEN creator_rounds.source_keywords ELSE excluded.source_keywords END,
                        source_labels = CASE WHEN excluded.source_labels IN ('', '[]')
                            THEN creator_rounds.source_labels ELSE excluded.source_labels END,
                        sample_notes = excluded.sample_notes,
                        sample_likes = excluded.sample_likes,
                        sample_collected = excluded.sample_collected,
                        sample_comments = excluded.sample_comments,
                        sample_interactions = excluded.sample_interactions,
                        top_note_id = COALESCE(NULLIF(excluded.top_note_id, ''), creator_rounds.top_note_id),
                        auto_tags = CASE WHEN excluded.auto_tags IN ('', '[]')
                            THEN creator_rounds.auto_tags ELSE excluded.auto_tags END,
                        data_round = COALESCE(NULLIF(excluded.data_round, ''), creator_rounds.data_round),
                        tier_label = COALESCE(NULLIF(excluded.tier_label, ''), creator_rounds.tier_label),
                        account_id = COALESCE(NULLIF(excluded.account_id, ''), creator_rounds.account_id),
                        cooperation_platform = COALESCE(NULLIF(excluded.cooperation_platform, ''), creator_rounds.cooperation_platform),
                        raw_title = COALESCE(NULLIF(excluded.raw_title, ''), creator_rounds.raw_title),
                        updated_at = excluded.updated_at
                    """,
                    (
                        round_id,
                        row.get("platform", "xhs"),
                        row["user_id"],
                        row.get("source_keywords", "[]"),
                        row.get("source_labels", "[]"),
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
        expected_updated_at: str | None = None,
        expected_status: str | None = None,
        claim_user_id: int | None = None,
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
        values: list[Any] = [*updates.values(), round_id, platform, user_id]
        where = "round_id = ? AND platform = ? AND user_id = ?"
        if expected_updated_at is not None:
            where += " AND updated_at = ?"
            values.append(expected_updated_at)
        if expected_status is not None:
            expected = normalize_status(expected_status, default="")
            if not expected:
                raise ValueError("无效的期望状态")
            where += " AND status = ?"
            values.append(expected)
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE creator_rounds SET {sets} WHERE {where}",
                values,
            )
            if (expected_updated_at is not None or expected_status is not None) and cur.rowcount == 0:
                raise ValueError("数据已被其他人更新，请刷新后重试")
            if claim_user_id is not None:
                self._conn.execute(
                    "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                    "ON CONFLICT(platform, user_id) DO NOTHING",
                    (platform, user_id),
                )
                self._conn.execute(
                    "UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
                    "WHERE platform = ? AND user_id = ? AND follow_up_user_id IS NULL",
                    (claim_user_id, utc_now(), platform, user_id),
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
        claim_user_id: int | None = None,
        set_owner: bool = False,
        owner_user_id: int | None = None,
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
        if not updates and not set_owner:
            return 0
        if updates:
            updates["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values())
        item_list = list(items)
        changed = 0
        with self._conn:
            for platform, user_id in item_list:
                item_changed = False
                if updates:
                    cur = self._conn.execute(
                        f"UPDATE creator_rounds SET {sets} WHERE round_id = ? AND platform = ? AND user_id = ?",
                        [*values, round_id, platform, user_id],
                    )
                    item_changed = item_changed or cur.rowcount > 0
                if set_owner:
                    self._conn.execute(
                        "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                        "ON CONFLICT(platform, user_id) DO NOTHING",
                        (platform, user_id),
                    )
                    now = utc_now()
                    cur = self._conn.execute(
                        "UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
                        "WHERE platform = ? AND user_id = ?",
                        (
                            owner_user_id,
                            now if owner_user_id is not None else None,
                            platform,
                            user_id,
                        ),
                    )
                    item_changed = item_changed or cur.rowcount > 0
                elif claim_user_id is not None:
                    self._conn.execute(
                        "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                        "ON CONFLICT(platform, user_id) DO NOTHING",
                        (platform, user_id),
                    )
                    cur = self._conn.execute(
                        "UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
                        "WHERE platform = ? AND user_id = ? AND follow_up_user_id IS NULL",
                        (claim_user_id, utc_now(), platform, user_id),
                    )
                    item_changed = item_changed or cur.rowcount > 0
                if item_changed:
                    changed += 1
        if tags_manual is not None:
            for platform, user_id in item_list:
                self._refresh_search_row(round_id, platform, user_id)
        return changed

    def creator_owner_options(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, username, role, can_collect FROM users "
            "WHERE disabled = 0 ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]

    def assign_creator(
        self,
        round_id: int,
        platform: str,
        user_id: str,
        owner_user_id: int | None,
        expected_owner_user_id: int | None = None,
    ) -> dict[str, Any]:
        row = self._creator_owner_row(round_id, platform, user_id)
        if not row:
            raise ValueError("达人不存在")
        current = row["owner_user_id"]
        if expected_owner_user_id is None:
            condition = "follow_up_user_id IS NULL"
            params: list[Any] = []
        else:
            condition = "follow_up_user_id = ?"
            params = [int(expected_owner_user_id)]
        now = utc_now()
        claimed_at = now if owner_user_id is not None else None
        with self._conn:
            self._conn.execute(
                "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                "ON CONFLICT(platform, user_id) DO NOTHING",
                (platform, user_id),
            )
            cur = self._conn.execute(
                f"UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
                f"WHERE platform = ? AND user_id = ? AND {condition}",
                [owner_user_id, claimed_at, platform, user_id, *params],
            )
            if cur.rowcount == 0:
                raise ValueError("跟进人已变化，请刷新后重试")
        return {"previous_owner_user_id": current, "owner_user_id": owner_user_id}

    def _creator_owner_row(
        self, round_id: int, platform: str, user_id: str
    ) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT c.follow_up_user_id AS owner_user_id, c.followed_at AS claimed_at,
                   COALESCE(u.username, '') AS owner_username
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            LEFT JOIN users u ON u.id = c.follow_up_user_id
            WHERE cr.round_id = ? AND cr.platform = ? AND cr.user_id = ?
            """,
            (round_id, platform, user_id),
        ).fetchone()

    def creator_owner(self, round_id: int, platform: str, user_id: str) -> dict[str, Any] | None:
        row = self._creator_owner_row(round_id, platform, user_id)
        return dict(row) if row else None

    def claim_creator(
        self, round_id: int, platform: str, user_id: str, owner_user_id: int
    ) -> dict[str, Any]:
        row = self._creator_owner_row(round_id, platform, user_id)
        if not row:
            raise ValueError("达人不存在")
        current = row["owner_user_id"]
        if current is not None and int(current) != int(owner_user_id):
            return {
                "ok": False,
                "owner_user_id": int(current),
                "owner_username": row["owner_username"],
            }
        if current is None:
            now = utc_now()
            with self._conn:
                self._conn.execute(
                    "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                    "ON CONFLICT(platform, user_id) DO NOTHING",
                    (platform, user_id),
                )
                cur = self._conn.execute(
                    "UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
                    "WHERE platform = ? AND user_id = ? AND follow_up_user_id IS NULL",
                    (owner_user_id, now, platform, user_id),
                )
            if cur.rowcount > 0:
                return {"ok": True, "owner_user_id": int(owner_user_id), "already_mine": False}
            latest = self._creator_owner_row(round_id, platform, user_id)
            if not latest:
                raise ValueError("达人不存在")
            if latest["owner_user_id"] is not None:
                if int(latest["owner_user_id"]) == int(owner_user_id):
                    return {"ok": True, "owner_user_id": int(owner_user_id), "already_mine": True}
                return {
                    "ok": False,
                    "owner_user_id": int(latest["owner_user_id"]),
                    "owner_username": latest["owner_username"],
                }
            return {"ok": False, "owner_user_id": None, "owner_username": ""}
        return {"ok": True, "owner_user_id": int(owner_user_id), "already_mine": True}

    def release_creator(
        self,
        round_id: int,
        platform: str,
        user_id: str,
        expected_owner_user_id: int | None = None,
    ) -> dict[str, Any]:
        row = self._creator_owner_row(round_id, platform, user_id)
        if not row:
            raise ValueError("达人不存在")
        current = row["owner_user_id"]
        if current is None:
            return {"ok": True, "released": False, "previous": None}
        if expected_owner_user_id is None or int(current) != int(expected_owner_user_id):
            return {
                "ok": False,
                "owner_user_id": int(current),
                "owner_username": row["owner_username"],
            }
        with self._conn:
            cur = self._conn.execute(
                "UPDATE creators SET follow_up_user_id = NULL, followed_at = NULL "
                "WHERE platform = ? AND user_id = ? AND follow_up_user_id = ?",
                (platform, user_id, int(current)),
            )
        if cur.rowcount == 0:
            latest = self._creator_owner_row(round_id, platform, user_id)
            if latest and latest["owner_user_id"] is not None:
                return {
                    "ok": False,
                    "owner_user_id": int(latest["owner_user_id"]),
                    "owner_username": latest["owner_username"],
                }
            return {"ok": True, "released": False, "previous": None}
        return {
            "ok": True,
            "released": True,
            "previous": {
                "owner_user_id": int(current),
                "owner_username": row["owner_username"],
            },
        }

    def claim_creators(
        self,
        round_id: int,
        items: Iterable[tuple[str, str]],
        owner_user_id: int,
    ) -> dict[str, int]:
        now = utc_now()
        result = {"claimed": 0, "already_mine": 0, "skipped": 0, "missing": 0}
        with self._conn:
            for platform, user_id in items:
                row = self._conn.execute(
                    "SELECT c.follow_up_user_id AS owner_user_id "
                    "FROM creator_rounds cr "
                    "LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id "
                    "WHERE cr.round_id = ? AND cr.platform = ? AND cr.user_id = ?",
                    (round_id, platform, user_id),
                ).fetchone()
                if not row:
                    result["missing"] += 1
                    continue
                current = row["owner_user_id"]
                if current is not None:
                    if int(current) == int(owner_user_id):
                        result["already_mine"] += 1
                    else:
                        result["skipped"] += 1
                    continue
                self._conn.execute(
                    "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                    "ON CONFLICT(platform, user_id) DO NOTHING",
                    (platform, user_id),
                )
                cur = self._conn.execute(
                    "UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
                    "WHERE platform = ? AND user_id = ? AND follow_up_user_id IS NULL",
                    (owner_user_id, now, platform, user_id),
                )
                if cur.rowcount > 0:
                    result["claimed"] += 1
        return result

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
                   cr.source_keywords, cr.source_labels,
                   cr.sample_notes, cr.sample_likes, cr.sample_collected,
                   cr.sample_comments, cr.sample_interactions, cr.top_note_id,
                   cr.auto_tags, cr.status, cr.match_direction, cr.tags_manual, cr.remark,
                   cr.data_round, cr.tier_label, cr.account_id,
                   c.follow_up_user_id AS owner_user_id, c.followed_at AS claimed_at,
                   COALESCE(u.username, '') AS owner_username,
                   cr.cooperation_platform, cr.raw_title,
                   n.title AS top_title, n.url AS top_url, n.liked AS top_liked,
                   r.name AS round_name
            FROM creator_rounds cr
            JOIN rounds r ON r.id = cr.round_id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            LEFT JOIN notes n ON n.platform = cr.platform AND n.note_id = cr.top_note_id
            LEFT JOIN users u ON u.id = c.follow_up_user_id
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
        source_mode: str = "any",
        labels: Iterable[str] | None = None,
        owner: str | None = None,
        min_fans: int | None = None,
        max_fans: int | None = None,
        only_unenriched: bool = False,
        needs_action: bool = False,
        action_actor_id: int | None = None,
        action_include_others: bool = False,
    ) -> tuple[str, list[Any]]:
        conditions = ["cr.rn = 1"] if round_id is None else ["cr.round_id = ?"]
        params: list[Any] = [] if round_id is None else [round_id]
        if platform:
            conditions.append("cr.platform = ?")
            params.append(platform)
        if owner == "unassigned":
            conditions.append("c.follow_up_user_id IS NULL")
        elif owner and str(owner).isdigit():
            conditions.append("c.follow_up_user_id = ?")
            params.append(int(owner))
        if search:
            text = search.strip()
            if self._has_fts and self._fts_trigram and len(text) >= 3:
                fts_query = '"' + text.replace('"', '""') + '"'
                if round_id is None:
                    conditions.append(
                        "(cr.platform || '|' || cr.user_id) IN ("
                        "SELECT creator_search.platform || '|' || creator_search.user_id "
                        "FROM creator_search "
                        "JOIN rounds search_round "
                        "ON search_round.id = CAST(creator_search.round_id AS INTEGER) "
                        "WHERE creator_search MATCH ? "
                        "AND search_round.archived_at IS NULL)"
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
                source_search = (
                    "EXISTS (SELECT 1 FROM creator_round_sources s "
                    "WHERE s.platform = cr.platform AND s.user_id = cr.user_id "
                    "AND s.kind IN ('keyword', 'label') "
                )
                source_params: list[Any] = []
                if round_id is None:
                    source_search += (
                        "AND s.round_id IN "
                        "(SELECT id FROM rounds WHERE archived_at IS NULL) "
                    )
                else:
                    source_search += "AND s.round_id = ? "
                    source_params.append(round_id)
                source_search += "AND s.value LIKE ? ESCAPE '\\')"
                source_params.append(like)
                conditions.append(
                    "("
                    "c.nickname LIKE ? ESCAPE '\\' OR "
                    "cr.account_id LIKE ? ESCAPE '\\' OR "
                    "cr.user_id LIKE ? ESCAPE '\\' OR "
                    "cr.raw_title LIKE ? ESCAPE '\\' OR "
                    "cr.tags_manual LIKE ? ESCAPE '\\' OR "
                    + source_search
                    + ")"
                )
                params.extend([like] * 5)
                params.extend(source_params)
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
        def add_source_filter(
            kind: str, values: list[str], mode: str = "any"
        ) -> None:
            prefix = (
                "EXISTS (SELECT 1 FROM creator_round_sources s "
                "WHERE s.platform = cr.platform AND s.user_id = cr.user_id "
                "AND s.kind = ? "
            )
            base_params: list[Any] = [kind]
            if round_id is None:
                prefix += "AND s.round_id IN (SELECT id FROM rounds WHERE archived_at IS NULL) "
            else:
                prefix += "AND s.round_id = ? "
                base_params.append(round_id)
            if mode == "all":
                for value in values:
                    conditions.append(prefix + "AND s.value = ?)")
                    params.extend([*base_params, value])
            else:
                placeholders = ",".join("?" * len(values))
                conditions.append(prefix + f"AND s.value IN ({placeholders}))")
                params.extend([*base_params, *values])

        source_values = [value for value in (sources or []) if value]
        if source_values:
            add_source_filter("keyword", source_values, source_mode)
        label_values = [value for value in (labels or []) if value]
        if label_values:
            add_source_filter("label", label_values)
        if only_unenriched:
            conditions.append("c.fans IS NULL")
        if needs_action:
            unclaimed = "(c.follow_up_user_id IS NULL AND cr.status IN ('pending', 'shortlisted'))"
            if action_actor_id is not None and not action_include_others:
                pending = (
                    "(cr.status = 'pending' AND "
                    "(c.follow_up_user_id IS NULL OR c.follow_up_user_id = ?))"
                )
                params.append(int(action_actor_id))
            else:
                pending = "cr.status = 'pending'"
            conditions.append(f"(({pending}) OR ({unclaimed}))")
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
                (round_id, platform, user_id, nickname, account_id,
                 raw_title, tags_manual, source_keywords, source_labels)
                SELECT CAST(cr.round_id AS TEXT), cr.platform, cr.user_id, c.nickname,
                       cr.account_id, cr.raw_title, cr.tags_manual,
                       cr.source_keywords, cr.source_labels
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
                (round_id, platform, user_id, nickname, account_id,
                 raw_title, tags_manual, source_keywords, source_labels)
                SELECT CAST(cr.round_id AS TEXT), cr.platform, cr.user_id, c.nickname,
                       cr.account_id, cr.raw_title, cr.tags_manual,
                       cr.source_keywords, cr.source_labels
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
        source_mode: str = "any",
        labels: Iterable[str] | None = None,
        owner: str | None = None,
        min_fans: int | None = None,
        max_fans: int | None = None,
        only_unenriched: bool = False,
        needs_action: bool = False,
        action_actor_id: int | None = None,
        action_include_others: bool = False,
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
            source_mode=source_mode,
            labels=labels,
            owner=owner,
            min_fans=min_fans,
            max_fans=max_fans,
            only_unenriched=only_unenriched,
            needs_action=needs_action,
            action_actor_id=action_actor_id,
            action_include_others=action_include_others,
        )
        direction = "DESC" if str(order).lower() != "asc" else "ASC"
        sort_map = {
            "fans": f"(c.fans IS NULL) ASC, c.fans {direction}",
            "interaction": f"(c.interaction IS NULL) ASC, c.interaction {direction}",
            "liked": f"(c.liked IS NULL) ASC, c.liked {direction}",
            "posted": f"(c.posted IS NULL) ASC, c.posted {direction}",
            "sample_likes": f"cr.sample_likes {direction}",
            "sample_average": (
                "CASE WHEN cr.sample_notes > 0 "
                f"THEN CAST(cr.sample_interactions AS REAL) / cr.sample_notes ELSE 0 END {direction}"
            ),
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
        select_sql = CREATOR_SELECT_ALL_SQL if round_id is None else CREATOR_SELECT_SQL
        rows = self._conn.execute(
            f"{select_sql} {from_sql} WHERE {where} "
            f"ORDER BY {sort_clause}, cr.platform ASC, cr.user_id ASC LIMIT ? OFFSET ?",
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
        def source_facet(kind: str) -> list[Any]:
            source_conditions = ["s.kind = ?"]
            source_params: list[Any] = [kind]
            if round_id is not None:
                source_conditions.append("s.round_id = ?")
                source_params.append(round_id)
            else:
                source_conditions.append("r.archived_at IS NULL")
            if platform:
                source_conditions.append("s.platform = ?")
                source_params.append(platform)
            return self._conn.execute(
                "SELECT s.value AS value, "
                "COUNT(DISTINCT s.platform || '|' || s.user_id) AS count "
                "FROM creator_round_sources s "
                "JOIN rounds r ON r.id = s.round_id "
                f"WHERE {' AND '.join(source_conditions)} "
                "GROUP BY s.value ORDER BY count DESC",
                source_params,
            ).fetchall()

        source_rows = source_facet("keyword")
        label_rows = source_facet("label")

        def rows_to_list(rows: list[Any]) -> list[dict[str, Any]]:
            return [{"value": row["value"], "count": int(row["count"])} for row in rows]

        return {
            "platforms": rows_to_list(platform_rows),
            "statuses": rows_to_list(status_rows),
            "tiers": rows_to_list(tier_rows),
            "directions": rows_to_list(direction_rows),
            "sources": rows_to_list(source_rows),
            "labels": rows_to_list(label_rows),
        }

    def creator_detail(self, round_id: int, platform: str, user_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT cr.*, c.*, r.name AS round_name, COALESCE(u.username, '') AS owner_username
            FROM creator_rounds cr
            JOIN rounds r ON r.id = cr.round_id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            LEFT JOIN users u ON u.id = cr.owner_user_id
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

    # ---- leads ----

    def query_leads(
        self,
        round_id: int | None = None,
        platform: str | None = None,
        search: str | None = None,
        statuses: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
        owner: str | None = None,
        sort: str = "updated_at",
        order: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        conditions: list[str] = ["1 = 1"]
        params: list[Any] = []
        if round_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM lead_rounds lr "
                "WHERE lr.lead_id = l.id AND lr.round_id = ?)"
            )
            params.append(round_id)
        if platform:
            conditions.append("l.platform = ?")
            params.append(platform)
        if search and search.strip():
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            conditions.append(
                "(l.nickname LIKE ? ESCAPE '\\' OR l.raw_title LIKE ? ESCAPE '\\' "
                "OR l.account_id LIKE ? ESCAPE '\\' OR l.profile_url LIKE ? ESCAPE '\\' "
                "OR l.legacy_user_id LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 5)
        status_values = [
            normalize_lead_status(value, default="")
            for value in (statuses or [])
            if value
        ]
        status_values = [value for value in status_values if value in LEAD_STATUS_CODES]
        if status_values:
            conditions.append(f"l.status IN ({','.join('?' * len(status_values))})")
            params.extend(status_values)
        source_values = [
            normalize_lead_source(value, default="")
            for value in (sources or [])
            if value
        ]
        source_values = [value for value in source_values if value]
        if source_values:
            conditions.append(f"l.source IN ({','.join('?' * len(source_values))})")
            params.extend(source_values)
        if owner:
            if owner == "unassigned":
                conditions.append("l.owner_user_id IS NULL")
            elif owner.isdigit():
                conditions.append("l.owner_user_id = ?")
                params.append(int(owner))
        where = " AND ".join(conditions)
        sort_map = {
            "updated_at": "l.updated_at",
            "created_at": "l.created_at",
            "nickname": "l.nickname",
            "status": "l.status",
        }
        sort_sql = sort_map.get(sort, sort_map["updated_at"])
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        total = self._conn.execute(
            f"SELECT COUNT(*) AS n {LEAD_FROM_SQL} WHERE {where}", params
        ).fetchone()["n"]
        rows = self._conn.execute(
            f"""
            SELECT l.id, l.platform, l.nickname, l.account_id, l.profile_url, l.raw_title,
                   l.source, l.source_round_id, l.owner_user_id, l.status, l.legacy_user_id,
                   l.created_by, l.created_at, l.updated_at,
                   l.converted_platform, l.converted_user_id, l.converted_at,
                   l.matched_platform, l.matched_user_id, l.matched_round_id, l.matched_at,
                   l.match_confidence,
                   u.username AS owner_username,
                   COALESCE(
                       r.name,
                       (SELECT r2.name FROM lead_rounds lr2
                        JOIN rounds r2 ON r2.id = lr2.round_id
                        WHERE lr2.lead_id = l.id ORDER BY lr2.round_id DESC LIMIT 1)
                   ) AS source_round_name,
                   mr.name AS matched_round_name
            {LEAD_FROM_SQL} WHERE {where}
            ORDER BY {sort_sql} {direction}, l.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }

    def get_lead(self, lead_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"""
            SELECT l.*, u.username AS owner_username, r.name AS source_round_name,
                   mr.name AS matched_round_name
            {LEAD_FROM_SQL} WHERE l.id = ?
            """,
            (lead_id,),
        ).fetchone()
        return dict(row) if row else None

    def lead_rounds(self, lead_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT lr.*, r.name AS round_name
            FROM lead_rounds lr
            LEFT JOIN rounds r ON r.id = lr.round_id
            WHERE lr.lead_id = ?
            ORDER BY lr.round_id DESC
            """,
            (lead_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def lead_attempts(self, lead_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM lead_attempts WHERE lead_id = ? ORDER BY id DESC LIMIT ?",
            (lead_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def normalize_lead_identities(self) -> int:
        rows = self._conn.execute(
            "SELECT id, platform, account_id, profile_url FROM leads"
        ).fetchall()
        updates: list[tuple[str, str, int]] = []
        for row in rows:
            account, profile = normalize_identity(
                row["platform"], row["account_id"], row["profile_url"]
            )
            if account != (row["account_id"] or "") or profile != (row["profile_url"] or ""):
                updates.append((account, profile, int(row["id"])))
        if updates:
            with self._conn:
                self._conn.executemany(
                    "UPDATE leads SET account_id = ?, profile_url = ? WHERE id = ?",
                    updates,
                )
        return len(updates)

    def try_create_lead_unique_indexes(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        statements = {
            "account": (
                "CREATE UNIQUE INDEX uq_leads_identity_account "
                "ON leads (platform, account_id) "
                "WHERE account_id IS NOT NULL AND account_id <> '' "
                "AND status NOT IN ('duplicate', 'discarded')"
            ),
            "profile": (
                "CREATE UNIQUE INDEX uq_leads_identity_profile "
                "ON leads (platform, profile_url) "
                "WHERE profile_url IS NOT NULL AND profile_url <> '' "
                "AND status NOT IN ('duplicate', 'discarded')"
            ),
        }
        for key, sql in statements.items():
            name = f"uq_leads_identity_{key}"
            column = "account_id" if key == "account" else "profile_url"
            existing = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
            existing_sql = str(existing["sql"] or "") if existing else ""
            if existing_sql and "status NOT IN" in existing_sql:
                result[key] = True
                continue
            duplicate = self._conn.execute(
                f"SELECT 1 FROM leads WHERE {column} IS NOT NULL AND {column} <> '' "
                "AND status NOT IN ('duplicate', 'discarded') "
                f"GROUP BY platform, {column} HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            if duplicate:
                result[key] = False
                continue
            try:
                if existing_sql:
                    self._conn.execute(f"DROP INDEX IF EXISTS {name}")
                self._conn.execute(sql)
                self._conn.commit()
                result[key] = True
            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                self._conn.rollback()
                result[key] = False
        return result

    def find_lead_by_identity(
        self,
        platform: str,
        account_id: str = "",
        profile_url: str = "",
        *,
        exclude_id: int | None = None,
    ) -> dict[str, Any] | None:
        account, profile = normalize_identity(platform, account_id, profile_url)
        if not account and not profile:
            return None
        conditions = ["platform = ?"]
        params: list[Any] = [str(platform or "xhs").strip().lower()]
        identity: list[str] = []
        if account:
            identity.append("account_id = ?")
            params.append(account)
        if profile:
            identity.append("profile_url = ?")
            params.append(profile)
        conditions.append(f"({' OR '.join(identity)})")
        if exclude_id is not None:
            conditions.append("id <> ?")
            params.append(int(exclude_id))
        row = self._conn.execute(
            f"""
            SELECT * FROM leads WHERE {' AND '.join(conditions)}
            ORDER BY CASE status
                WHEN 'converted' THEN 0
                WHEN 'verifying' THEN 1
                WHEN 'unverified' THEN 2
                WHEN 'duplicate' THEN 3
                ELSE 4
            END, id
            LIMIT 1
            """,
            params,
        ).fetchone()
        return dict(row) if row else None

    def create_lead(
        self,
        *,
        platform: str,
        nickname: str = "",
        account_id: str = "",
        profile_url: str = "",
        raw_title: str = "",
        source: str = DEFAULT_LEAD_SOURCE,
        source_round_id: int | None = None,
        owner_user_id: int | None = None,
        raw_json: Any = None,
        created_by: int | None = None,
        legacy_user_id: str = "",
        status: str = DEFAULT_LEAD_STATUS,
        source_keywords: str = "[]",
        data_round: str = "",
    ) -> int:
        lead_id, _, _ = self.create_lead_dedup(
            platform=platform,
            nickname=nickname,
            account_id=account_id,
            profile_url=profile_url,
            raw_title=raw_title,
            source=source,
            source_round_id=source_round_id,
            owner_user_id=owner_user_id,
            raw_json=raw_json,
            created_by=created_by,
            legacy_user_id=legacy_user_id,
            status=status,
            source_keywords=source_keywords,
            data_round=data_round,
        )
        return lead_id

    def create_lead_dedup(
        self,
        *,
        platform: str,
        nickname: str = "",
        account_id: str = "",
        profile_url: str = "",
        raw_title: str = "",
        source: str = DEFAULT_LEAD_SOURCE,
        source_round_id: int | None = None,
        owner_user_id: int | None = None,
        raw_json: Any = None,
        created_by: int | None = None,
        legacy_user_id: str = "",
        status: str = DEFAULT_LEAD_STATUS,
        source_keywords: str = "[]",
        data_round: str = "",
    ) -> tuple[int, bool, bool]:
        code = str(platform or "xhs").strip().lower()
        account, profile = normalize_identity(code, account_id, profile_url)
        existing = self.find_lead_by_identity(code, account, profile)
        if existing:
            lead_id = int(existing["id"])
            if normalize_lead_status(existing.get("status")) in ("discarded", "duplicate"):
                now = utc_now()
                raw_provided = raw_json not in (None, "", {})
                raw_text = (
                    raw_json
                    if isinstance(raw_json, str)
                    else json.dumps(raw_json or {}, ensure_ascii=False)
                )
                round_id = (
                    source_round_id
                    if source_round_id is not None
                    else existing.get("source_round_id")
                )
                try:
                    with self._conn:
                        self._conn.execute(
                            """
                            UPDATE leads SET nickname = ?, account_id = ?, profile_url = ?,
                                raw_title = ?, source = ?, source_round_id = ?,
                                owner_user_id = ?, status = 'unverified', legacy_user_id = ?,
                                raw_json = ?, matched_platform = NULL, matched_user_id = NULL,
                                matched_round_id = NULL, matched_at = NULL,
                                match_confidence = NULL, conversion_lock = NULL,
                                conversion_locked_at = NULL, converted_platform = NULL,
                                converted_user_id = NULL, converted_at = NULL, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                nickname or existing.get("nickname") or "",
                                account or existing.get("account_id") or "",
                                profile or existing.get("profile_url") or "",
                                raw_title or existing.get("raw_title") or "",
                                normalize_lead_source(source),
                                round_id,
                                owner_user_id
                                if owner_user_id is not None
                                else existing.get("owner_user_id"),
                                legacy_user_id or existing.get("legacy_user_id") or "",
                                raw_text if raw_provided else (existing.get("raw_json") or ""),
                                now,
                                lead_id,
                            ),
                        )
                        if round_id is not None and self.get_round(int(round_id)):
                            self._conn.execute(
                                """
                                INSERT OR IGNORE INTO lead_rounds
                                (lead_id, round_id, source_keywords, raw_title, data_round,
                                 sample_notes, sample_likes, sample_collected, sample_comments,
                                 sample_interactions, updated_at)
                                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?)
                                """,
                                (
                                    lead_id,
                                    int(round_id),
                                    source_keywords or "[]",
                                    raw_title or existing.get("raw_title") or "",
                                    data_round or "",
                                    now,
                                ),
                            )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(
                        "该账号或主页已被其他线索占用，无法恢复该线索"
                    ) from exc
                return lead_id, False, True
            if source_round_id is not None and self.get_round(source_round_id):
                now = utc_now()
                with self._conn:
                    cur = self._conn.execute(
                        """
                        INSERT OR IGNORE INTO lead_rounds
                        (lead_id, round_id, source_keywords, raw_title, data_round,
                         sample_notes, sample_likes, sample_collected, sample_comments,
                         sample_interactions, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?)
                        """,
                        (
                            lead_id,
                            source_round_id,
                            source_keywords or "[]",
                            raw_title or existing.get("raw_title") or "",
                            data_round or "",
                            now,
                        ),
                    )
                    if cur.rowcount:
                        self._conn.execute(
                            "UPDATE leads SET updated_at = ? WHERE id = ?",
                            (now, lead_id),
                        )
            return lead_id, False, False
        now = utc_now()
        raw_text = raw_json if isinstance(raw_json, str) else json.dumps(raw_json or {}, ensure_ascii=False)
        try:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO leads
                    (platform, nickname, account_id, profile_url, raw_title, source,
                     source_round_id, owner_user_id, status, legacy_user_id, raw_json,
                     created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        nickname or "",
                        account,
                        profile,
                        raw_title or "",
                        normalize_lead_source(source),
                        source_round_id,
                        owner_user_id,
                        normalize_lead_status(status),
                        legacy_user_id or "",
                        raw_text,
                        created_by,
                        now,
                        now,
                    ),
                )
                lead_id = int(cur.lastrowid)
                if source_round_id is not None and self.get_round(source_round_id):
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO lead_rounds
                        (lead_id, round_id, source_keywords, raw_title, data_round,
                         sample_notes, sample_likes, sample_collected, sample_comments,
                         sample_interactions, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?)
                        """,
                        (
                            lead_id,
                            source_round_id,
                            source_keywords or "[]",
                            raw_title or "",
                            data_round or "",
                            now,
                        ),
                    )
        except sqlite3.IntegrityError:
            existing = self.find_lead_by_identity(code, account, profile)
            if existing:
                return int(existing["id"]), False, False
            raise
        return lead_id, True, False

    def update_lead(self, lead_id: int, *, commit: bool = True, **fields: Any) -> None:
        allowed = {
            "platform",
            "nickname",
            "account_id",
            "profile_url",
            "raw_title",
            "source",
            "source_round_id",
            "owner_user_id",
            "status",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        current = self.get_lead(lead_id)
        if not current:
            raise ValueError("线索不存在")
        if current.get("status") == "converted":
            raise ValueError("已转达人的线索不可修改")
        if "status" in updates:
            updates["status"] = normalize_lead_status(updates["status"])
            if updates["status"] == "converted":
                raise ValueError("请使用线索转换接口")
        if "source" in updates:
            updates["source"] = normalize_lead_source(updates["source"])
        if {"platform", "account_id", "profile_url"} & set(updates):
            platform = str(updates.get("platform", current.get("platform") or "xhs")).strip().lower()
            account, profile = normalize_identity(
                platform,
                updates.get("account_id", current.get("account_id") or ""),
                updates.get("profile_url", current.get("profile_url") or ""),
            )
            updates["account_id"] = account
            updates["profile_url"] = profile
            duplicate = self.find_lead_by_identity(
                platform, account, profile, exclude_id=lead_id
            )
            if duplicate:
                raise ValueError(f"该账号已存在线索 #{duplicate['id']}，请直接编辑原线索")
        if (
            "status" in updates
            and updates["status"] in ("unverified", "verifying")
            and normalize_lead_status(current.get("status")) in ("duplicate", "discarded")
        ):
            duplicate = self.find_lead_by_identity(
                str(current.get("platform") or "xhs"),
                current.get("account_id") or "",
                current.get("profile_url") or "",
                exclude_id=lead_id,
            )
            if duplicate:
                raise ValueError(
                    f"该账号已被线索 #{duplicate['id']} 占用，无法恢复这条重复线索"
                )
        new_round = updates.get("source_round_id", current.get("source_round_id"))
        if new_round is not None and not self.get_round(int(new_round)):
            raise ValueError("来源轮次不存在")
        old_round = current.get("source_round_id")
        updates["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._write(commit):
            try:
                self._conn.execute(
                    f"UPDATE leads SET {sets} WHERE id = ?", [*updates.values(), lead_id]
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("账号或主页与现有线索冲突，无法保存") from exc
            if "source_round_id" in updates:
                if new_round is not None:
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO lead_rounds
                        (lead_id, round_id, source_keywords, raw_title, data_round, updated_at)
                        VALUES (?, ?, '[]', ?, '', ?)
                        """,
                        (lead_id, int(new_round), current.get("raw_title") or "", utc_now()),
                    )
                if old_round is not None and int(old_round) != int(new_round or 0):
                    self._conn.execute(
                        "DELETE FROM lead_rounds WHERE lead_id = ? AND round_id = ?",
                        (lead_id, int(old_round)),
                    )

    def add_lead_attempt(
        self,
        lead_id: int,
        status: str,
        provider: str = "",
        query: str = "",
        candidate_user_id: str = "",
        candidate_nickname: str = "",
        candidate_profile_url: str = "",
        confidence: str = "",
        error: str = "",
        cost: int = 0,
        cost_native: float = 0,
        cost_currency: str = "token",
        round_id: int | None = None,
        created_by: int | None = None,
        commit: bool = True,
    ) -> None:
        with self._write(commit):
            self._conn.execute(
                """
                INSERT INTO lead_attempts
                (lead_id, provider, query, candidate_user_id, candidate_nickname,
                 candidate_profile_url, confidence, status, error, cost, cost_native,
                 cost_currency, round_id, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    provider or "",
                    query or "",
                    candidate_user_id or "",
                    candidate_nickname or "",
                    candidate_profile_url or "",
                    confidence or "",
                    status,
                    error or "",
                    int(cost or 0),
                    float(cost_native or 0),
                    cost_currency or "token",
                    round_id,
                    created_by,
                    utc_now(),
                ),
            )

    def apply_lead_matches(
        self, matches: list[dict[str, Any]], *, actor_id: int | None = None
    ) -> dict[str, int]:
        stats = {"matched": 0, "updated": 0, "attempts": 0, "skipped": 0}
        now = utc_now()
        if not matches:
            return stats
        audit_events: list[dict[str, Any]] = []
        with self._conn:
            for match in matches:
                lead_id = int(match["lead_id"])
                row = self._conn.execute(
                    "SELECT * FROM leads WHERE id = ?", (lead_id,)
                ).fetchone()
                if not row:
                    stats["skipped"] += 1
                    continue
                if row["status"] not in ("unverified", "verifying"):
                    stats["skipped"] += 1
                    continue
                platform = str(match.get("platform") or row["platform"] or "xhs")
                user_id = str(match.get("user_id") or "").strip()
                if not user_id:
                    stats["skipped"] += 1
                    continue
                match_account = str(match.get("account_id") or user_id).strip() or user_id
                overwrite = (
                    bool(match.get("overwrite"))
                    and not row["matched_user_id"]
                    and row["status"] == "unverified"
                    and not row["converted_at"]
                )
                creator_nickname = str(match.get("nickname") or "").strip()
                creator_profile = str(match.get("profile_url") or "").strip()
                creator_raw = match.get("raw_json")
                account, profile = normalize_identity(
                    platform,
                    match_account,
                    creator_profile,
                )
                nickname = row["nickname"] or ""
                raw_json = row["raw_json"] or ""
                if overwrite and creator_nickname:
                    nickname = creator_nickname
                if overwrite and isinstance(creator_raw, str) and creator_raw.strip():
                    raw_json = creator_raw
                changed = nickname != (row["nickname"] or "") or raw_json != (row["raw_json"] or "")
                current_profile = row["profile_url"] or ""
                if overwrite and profile:
                    profile_url = profile
                elif not current_profile:
                    profile_url = profile
                else:
                    profile_url = current_profile
                account_id = (
                    match_account
                    if overwrite
                    else (row["account_id"] or account or match_account)
                )
                if account_id != (row["account_id"] or ""):
                    changed = True
                if profile_url != current_profile:
                    changed = True
                matched_round_id = match.get("round_id")
                if (
                    row["matched_user_id"] == user_id
                    and row["matched_round_id"] is not None
                    and matched_round_id is not None
                    and int(row["matched_round_id"]) > int(matched_round_id)
                ):
                    matched_round_id = int(row["matched_round_id"])
                conflict_conditions: list[str] = []
                conflict_params: list[Any] = [platform]
                if account_id and account_id != (row["account_id"] or ""):
                    conflict_conditions.append("account_id = ?")
                    conflict_params.append(account_id)
                if profile_url and profile_url != (row["profile_url"] or ""):
                    conflict_conditions.append("profile_url = ?")
                    conflict_params.append(profile_url)
                if conflict_conditions:
                    conflict = self._conn.execute(
                        f"""
                        SELECT id FROM leads
                        WHERE platform = ? AND ({' OR '.join(conflict_conditions)})
                          AND id <> ?
                        LIMIT 1
                        """,
                        [*conflict_params, lead_id],
                    ).fetchone()
                    if conflict:
                        stats["skipped"] += 1
                        continue
                cur = self._conn.execute(
                    """
                    UPDATE leads SET nickname = ?, account_id = ?, profile_url = ?,
                        raw_json = ?, matched_platform = ?, matched_user_id = ?,
                        matched_round_id = ?, matched_at = ?, match_confidence = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN ('unverified', 'verifying')
                    """,
                    (
                        nickname,
                        account_id,
                        profile_url,
                        raw_json,
                        platform,
                        user_id,
                        matched_round_id,
                        now,
                        match.get("confidence") or "",
                        now if changed else (row["updated_at"] or now),
                        lead_id,
                    ),
                )
                if cur.rowcount == 0:
                    stats["skipped"] += 1
                    continue
                duplicate_attempt = self._conn.execute(
                    """
                    SELECT 1 FROM lead_attempts
                    WHERE lead_id = ? AND status = 'matched'
                      AND candidate_user_id = ?
                      AND COALESCE(round_id, -1) = COALESCE(?, -1)
                    LIMIT 1
                    """,
                    (lead_id, user_id, matched_round_id),
                ).fetchone()
                if not duplicate_attempt:
                    self._conn.execute(
                        """
                        INSERT INTO lead_attempts
                        (lead_id, provider, query, candidate_user_id, candidate_nickname,
                         candidate_profile_url, confidence, status, round_id, created_by,
                         created_at)
                        VALUES (?, 'round', ?, ?, ?, ?, ?, 'matched', ?, ?, ?)
                        """,
                        (
                            lead_id,
                            user_id,
                            user_id,
                            creator_nickname,
                            creator_profile,
                            match.get("confidence") or "",
                            matched_round_id,
                            actor_id,
                            now,
                        ),
                    )
                    stats["attempts"] += 1
                if matched_round_id is not None:
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO lead_rounds
                        (lead_id, round_id, source_keywords, raw_title, data_round,
                         sample_notes, sample_likes, sample_collected, sample_comments,
                         sample_interactions, updated_at)
                        VALUES (?, ?, '[]', ?, '', 0, 0, 0, 0, 0, ?)
                        """,
                        (lead_id, int(matched_round_id), row["raw_title"] or "", now),
                    )
                if changed and overwrite:
                    audit_events.append(
                        {
                            "lead_id": lead_id,
                            "platform": platform,
                            "user_id": user_id,
                            "round_id": matched_round_id,
                            "confidence": match.get("confidence") or "",
                        }
                    )
                stats["matched"] += 1
                if changed:
                    stats["updated"] += 1
        for event in audit_events:
            self.audit(
                actor_id,
                "lead.match_overwrite",
                f"lead:{event['lead_id']}",
                details=event,
            )
        return stats

    def clear_lead_match(
        self, lead_id: int, *, actor_id: int | None = None, reason: str = "no_match"
    ) -> bool:
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE leads SET matched_platform = NULL, matched_user_id = NULL,
                    matched_round_id = NULL, matched_at = NULL, match_confidence = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, lead_id),
            )
            if cur.rowcount:
                self._conn.execute(
                    """
                    INSERT INTO lead_attempts
                    (lead_id, provider, query, confidence, status, error, created_by, created_at)
                    VALUES (?, 'round', '', '', 'unmatched', ?, ?, ?)
                    """,
                    (lead_id, reason, actor_id, now),
                )
            changed = bool(cur.rowcount)
        if changed:
            self.audit(
                actor_id,
                "lead.match_cleared",
                f"lead:{lead_id}",
                details={"reason": reason},
            )
        return changed

    def apply_lead_suggestions(
        self, suggestions: list[dict[str, Any]], *, actor_id: int | None = None
    ) -> int:
        inserted = 0
        now = utc_now()
        if not suggestions:
            return inserted
        with self._conn:
            for suggestion in suggestions:
                lead_id = int(suggestion["lead_id"])
                row = self._conn.execute(
                    "SELECT status, account_id, matched_user_id FROM leads WHERE id = ?",
                    (lead_id,),
                ).fetchone()
                if not row or row["status"] not in ("unverified", "verifying"):
                    continue
                if row["account_id"] or row["matched_user_id"]:
                    continue
                user_id = str(suggestion.get("user_id") or "")
                round_id = suggestion.get("round_id")
                existing = self._conn.execute(
                    """
                    SELECT 1 FROM lead_attempts
                    WHERE lead_id = ? AND status = 'suggested' AND provider = 'round'
                      AND candidate_user_id = ?
                      AND COALESCE(round_id, -1) = COALESCE(?, -1)
                    LIMIT 1
                    """,
                    (lead_id, user_id, round_id),
                ).fetchone()
                if existing:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO lead_attempts
                    (lead_id, provider, query, candidate_user_id, candidate_nickname,
                     candidate_profile_url, confidence, status, round_id, created_by,
                     created_at)
                    VALUES (?, 'round', ?, ?, ?, ?, 'nickname', 'suggested', ?, ?, ?)
                    """,
                    (
                        lead_id,
                        user_id,
                        user_id,
                        suggestion.get("nickname") or "",
                        suggestion.get("profile_url") or "",
                        round_id,
                        actor_id,
                        now,
                    ),
                )
                inserted += 1
        return inserted

    def dedupe_leads(self, *, apply: bool = False) -> dict[str, Any]:
        if apply:
            self.normalize_lead_identities()
        rows = [
            dict(row)
            for row in self._conn.execute(
                "SELECT * FROM leads ORDER BY id"
            ).fetchall()
        ]
        identity_updates = 0
        if not apply:
            for row in rows:
                account, profile = normalize_identity(
                    str(row.get("platform") or "xhs"),
                    row.get("account_id") or "",
                    row.get("profile_url") or "",
                )
                if account != (row.get("account_id") or "") or profile != (
                    row.get("profile_url") or ""
                ):
                    identity_updates += 1
                    row["account_id"] = account
                    row["profile_url"] = profile
        parents = list(range(len(rows)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(first: int, second: int) -> None:
            root_a, root_b = find(first), find(second)
            if root_a != root_b:
                parents[max(root_a, root_b)] = min(root_a, root_b)

        account_seen: dict[tuple[str, str], int] = {}
        profile_seen: dict[tuple[str, str], int] = {}
        for index, row in enumerate(rows):
            platform = str(row.get("platform") or "xhs")
            account = str(row.get("account_id") or "")
            profile = str(row.get("profile_url") or "")
            if account:
                key = (platform, account)
                if key in account_seen:
                    union(index, account_seen[key])
                else:
                    account_seen[key] = index
            if profile:
                key = (platform, profile)
                if key in profile_seen:
                    union(index, profile_seen[key])
                else:
                    profile_seen[key] = index

        groups: dict[int, list[int]] = {}
        for index in range(len(rows)):
            groups.setdefault(find(index), []).append(index)
        status_rank = {"converted": 0, "verifying": 1, "unverified": 2, "duplicate": 3}
        report: dict[str, Any] = {
            "groups": 0,
            "duplicates": 0,
            "merged_rounds": 0,
            "conflict_groups": 0,
            "identity_updates": identity_updates,
            "samples": [],
        }
        now = utc_now()
        with self._conn:
            for members in groups.values():
                if len(members) < 2:
                    continue
                members.sort(
                    key=lambda index: (
                        status_rank.get(str(rows[index].get("status") or ""), 4),
                        rows[index].get("created_at") or "",
                        rows[index]["id"],
                    )
                )
                primary = rows[members[0]]
                duplicates = [rows[index] for index in members[1:]]
                report["groups"] += 1
                report["duplicates"] += len(duplicates)
                conflicts = [
                    field
                    for field in ("nickname", "account_id", "profile_url", "owner_user_id")
                    if primary.get(field)
                    and any(
                        item.get(field)
                        and item.get(field) != primary.get(field)
                        for item in duplicates
                    )
                ]
                if conflicts:
                    report["conflict_groups"] += 1
                if len(report["samples"]) < 10:
                    report["samples"].append(
                        {
                            "keep": primary["id"],
                            "drop": [item["id"] for item in duplicates],
                            "platform": primary["platform"],
                            "account_id": primary.get("account_id") or "",
                            "conflicts": conflicts,
                        }
                    )
                if not apply:
                    continue
                converted = next(
                    (item for item in [primary, *duplicates] if item.get("status") == "converted"),
                    None,
                )
                merged = dict(primary)
                for field in ("nickname", "account_id", "profile_url", "raw_json"):
                    if not merged.get(field):
                        merged[field] = next(
                            (item[field] for item in duplicates if item.get(field)), ""
                        )
                if merged.get("owner_user_id") is None:
                    merged["owner_user_id"] = next(
                        (
                            item["owner_user_id"]
                            for item in duplicates
                            if item.get("owner_user_id") is not None
                        ),
                        None,
                    )
                if merged.get("source_round_id") is None:
                    merged["source_round_id"] = next(
                        (
                            item["source_round_id"]
                            for item in duplicates
                            if item.get("source_round_id") is not None
                        ),
                        None,
                    )
                if converted:
                    merged["status"] = "converted"
                    merged["converted_platform"] = converted.get("converted_platform")
                    merged["converted_user_id"] = converted.get("converted_user_id")
                    merged["converted_at"] = converted.get("converted_at")
                self._conn.execute(
                    """
                    UPDATE leads SET nickname = ?, account_id = ?, profile_url = ?,
                        raw_json = ?, owner_user_id = ?, source_round_id = ?, status = ?,
                        converted_platform = ?, converted_user_id = ?, converted_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        merged.get("nickname") or "",
                        merged.get("account_id") or "",
                        merged.get("profile_url") or "",
                        merged.get("raw_json") or "",
                        merged.get("owner_user_id"),
                        merged.get("source_round_id"),
                        merged.get("status") or "unverified",
                        merged.get("converted_platform"),
                        merged.get("converted_user_id"),
                        merged.get("converted_at"),
                        now,
                        primary["id"],
                    ),
                )
                for duplicate in duplicates:
                    rows_moved = self._conn.execute(
                        """
                        INSERT OR IGNORE INTO lead_rounds
                        (lead_id, round_id, source_keywords, raw_title, data_round,
                         top_note_id, sample_notes, sample_likes, sample_collected,
                         sample_comments, sample_interactions, updated_at)
                        SELECT ?, round_id, source_keywords, raw_title, data_round,
                               top_note_id, sample_notes, sample_likes, sample_collected,
                               sample_comments, sample_interactions, updated_at
                        FROM lead_rounds WHERE lead_id = ?
                        """,
                        (primary["id"], duplicate["id"]),
                    )
                    report["merged_rounds"] += max(0, rows_moved.rowcount)
                    self._conn.execute(
                        "DELETE FROM lead_rounds WHERE lead_id = ?", (duplicate["id"],)
                    )
                    self._conn.execute(
                        "UPDATE lead_attempts SET lead_id = ? WHERE lead_id = ?",
                        (primary["id"], duplicate["id"]),
                    )
                    self._conn.execute(
                        """
                        UPDATE leads SET status = 'duplicate',
                            matched_platform = NULL, matched_user_id = NULL,
                            matched_round_id = NULL, matched_at = NULL,
                            match_confidence = NULL,
                            conversion_lock = NULL, conversion_locked_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, duplicate["id"]),
                    )
        if apply:
            report["indexes"] = self.try_create_lead_unique_indexes()
        return report

    def lead_metrics(self, round_id: int | None = None) -> dict[str, int]:
        where = ""
        params: list[Any] = []
        if round_id is not None:
            where = (
                "WHERE EXISTS (SELECT 1 FROM lead_rounds lr "
                "WHERE lr.lead_id = l.id AND lr.round_id = ?)"
            )
            params = [round_id]
        rows = self._conn.execute(
            f"SELECT l.status, COUNT(*) AS n FROM leads l {where} GROUP BY l.status",
            params,
        ).fetchall()
        result = {code: 0 for code in LEAD_STATUS_CODES}
        for row in rows:
            result[normalize_lead_status(row["status"])] += int(row["n"])
        result["leads"] = sum(result.values())
        return result

    def lead_facets(self, round_id: int | None = None) -> dict[str, Any]:
        where = ""
        params: list[Any] = []
        if round_id is not None:
            where = (
                "WHERE EXISTS (SELECT 1 FROM lead_rounds lr "
                "WHERE lr.lead_id = l.id AND lr.round_id = ?)"
            )
            params = [round_id]

        def facet(column: str) -> list[dict[str, Any]]:
            rows = self._conn.execute(
                f"SELECT {column} AS value, COUNT(*) AS count FROM leads l {where} "
                f"GROUP BY {column} ORDER BY count DESC",
                params,
            ).fetchall()
            return [
                {"value": str(row["value"] or ""), "count": int(row["count"])}
                for row in rows
            ]

        round_where = "WHERE lr.round_id = ?" if round_id is not None else ""
        round_params: list[Any] = [round_id] if round_id is not None else []
        round_rows = self._conn.execute(
            f"SELECT lr.round_id AS value, COUNT(DISTINCT lr.lead_id) AS count "
            f"FROM lead_rounds lr JOIN leads l ON l.id = lr.lead_id {round_where} "
            f"GROUP BY lr.round_id ORDER BY count DESC",
            round_params,
        ).fetchall()

        return {
            "platforms": facet("l.platform"),
            "statuses": facet("l.status"),
            "sources": facet("l.source"),
            "owners": facet("l.owner_user_id"),
            "rounds": [
                {"value": str(row["value"] or ""), "count": int(row["count"])}
                for row in round_rows
            ],
        }

    def lead_identity_health(self) -> dict[str, Any]:
        indexes = {
            row["name"]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'leads'"
            ).fetchall()
        }

        def duplicate_groups(column: str) -> int:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM (
                    SELECT 1 FROM leads
                    WHERE {column} IS NOT NULL AND {column} <> ''
                      AND status NOT IN ('duplicate', 'discarded')
                    GROUP BY platform, {column}
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()
            return int(row["n"] or 0)

        return {
            "account_index": "uq_leads_identity_account" in indexes,
            "profile_index": "uq_leads_identity_profile" in indexes,
            "duplicate_account_groups": duplicate_groups("account_id"),
            "duplicate_profile_groups": duplicate_groups("profile_url"),
        }

    def lead_owner_options(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, username, role, can_collect FROM users "
            "WHERE disabled = 0 AND (role = 'admin' OR can_collect = 1) "
            "ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]

    def add_creator_round(
        self,
        round_id: int,
        platform: str,
        user_id: str,
        *,
        source_keywords: str = "[]",
        source_labels: str = "[]",
        raw_title: str = "",
        account_id: str = "",
        data_round: str = "",
        top_note_id: str = "",
        sample_notes: int = 0,
        sample_likes: int = 0,
        sample_collected: int = 0,
        sample_comments: int = 0,
        sample_interactions: int = 0,
        status: str = DEFAULT_STATUS,
        owner_user_id: int | None = None,
        commit: bool = True,
    ) -> bool:
        if not self.get_round(round_id):
            raise ValueError(f"轮次不存在: {round_id}")
        now = utc_now()
        with self._write(commit):
            cur = self._conn.execute(
                """
                INSERT INTO creator_rounds
                (round_id, platform, user_id, source_keywords, source_labels, sample_notes, sample_likes,
                 sample_collected, sample_comments, sample_interactions, top_note_id,
                 auto_tags, status, match_direction, tags_manual, remark, data_round,
                 tier_label, account_id, cooperation_platform, raw_title, owner_user_id,
                 claimed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, NULL, '', '', ?, NULL, ?, '', ?, ?, ?, ?)
                ON CONFLICT(round_id, platform, user_id) DO UPDATE SET
                    owner_user_id = COALESCE(creator_rounds.owner_user_id, excluded.owner_user_id),
                    claimed_at = COALESCE(creator_rounds.claimed_at, excluded.claimed_at),
                    source_keywords = CASE WHEN excluded.source_keywords IN ('', '[]')
                        THEN creator_rounds.source_keywords ELSE excluded.source_keywords END,
                    source_labels = CASE WHEN excluded.source_labels IN ('', '[]')
                        THEN creator_rounds.source_labels ELSE excluded.source_labels END,
                    account_id = COALESCE(NULLIF(excluded.account_id, ''), creator_rounds.account_id),
                    data_round = COALESCE(NULLIF(excluded.data_round, ''), creator_rounds.data_round),
                    top_note_id = COALESCE(NULLIF(excluded.top_note_id, ''), creator_rounds.top_note_id),
                    raw_title = COALESCE(NULLIF(excluded.raw_title, ''), creator_rounds.raw_title),
                    updated_at = excluded.updated_at
                """,
                (
                    round_id,
                    platform,
                    user_id,
                    source_keywords or "[]",
                    source_labels or "[]",
                    int(sample_notes or 0),
                    int(sample_likes or 0),
                    int(sample_collected or 0),
                    int(sample_comments or 0),
                    int(sample_interactions or 0),
                    top_note_id or "",
                    normalize_status(status),
                    data_round or "",
                    account_id or user_id,
                    raw_title or "",
                    owner_user_id,
                    now if owner_user_id is not None else None,
                    now,
                ),
            )
            inserted = cur.rowcount > 0
            if owner_user_id is not None:
                self._conn.execute(
                    "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
                    "ON CONFLICT(platform, user_id) DO NOTHING",
                    (platform, user_id),
                )
                self._conn.execute(
                    "UPDATE creators SET "
                    "follow_up_user_id = COALESCE(follow_up_user_id, ?), "
                    "followed_at = COALESCE(followed_at, ?) "
                    "WHERE platform = ? AND user_id = ?",
                    (owner_user_id, now, platform, user_id),
                )
        if commit and self._has_fts:
            self.rebuild_search_index(round_id)
        return inserted

    def acquire_lead_conversion(
        self, lead_id: int, token: str, stale_minutes: int = 15
    ) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, stale_minutes))).isoformat(
            timespec="seconds"
        )
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE leads SET conversion_lock = ?, conversion_locked_at = ?, updated_at = ?
                WHERE id = ? AND status IN ('unverified', 'verifying')
                  AND (
                      conversion_lock IS NULL OR conversion_locked_at IS NULL
                      OR conversion_locked_at < ?
                  )
                """,
                (token, now, now, lead_id, cutoff),
            )
        return cur.rowcount > 0

    def release_lead_conversion(self, lead_id: int, token: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE leads SET conversion_lock = NULL, conversion_locked_at = NULL "
                "WHERE id = ? AND conversion_lock = ?",
                (lead_id, token),
            )

    def mark_lead_converted(
        self, lead_id: int, platform: str, user_id: str, *, commit: bool = True
    ) -> None:
        now = utc_now()
        with self._write(commit):
            cur = self._conn.execute(
                """
                UPDATE leads SET status = 'converted', converted_platform = ?,
                    converted_user_id = ?, converted_at = ?, updated_at = ?,
                    conversion_lock = NULL, conversion_locked_at = NULL,
                    matched_platform = NULL, matched_user_id = NULL,
                    matched_round_id = NULL, matched_at = NULL, match_confidence = NULL
                WHERE id = ? AND status IN ('unverified', 'verifying')
                """,
                (platform, user_id, now, now, lead_id),
            )
            if cur.rowcount == 0:
                raise ValueError("线索状态已变化，无法完成转换")

    def counts(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "rounds": count("rounds"),
            "keywords": count("keywords"),
            "notes": count("notes"),
            "creators": count("creators"),
            "creator_rounds": count("creator_rounds"),
            "leads": count("leads"),
        }

    def round_summary(self, round_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM creator_rounds WHERE round_id = ?) AS creators,
                (SELECT COUNT(*) FROM lead_rounds WHERE round_id = ?) AS leads,
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
            (round_id, round_id, round_id, round_id, round_id, round_id),
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
            WITH latest AS (
                SELECT cr.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY cr.platform, cr.user_id
                           ORDER BY cr.round_id DESC, cr.updated_at DESC
                       ) AS rn
                FROM creator_rounds cr
                JOIN rounds active_round ON active_round.id = cr.round_id
                WHERE active_round.archived_at IS NULL
            )
            SELECT
                COUNT(*) AS creators,
                SUM(CASE WHEN c.fans IS NOT NULL THEN 1 ELSE 0 END) AS enriched,
                SUM(CASE WHEN cr.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN cr.status = 'adopted' THEN 1 ELSE 0 END) AS adopted
            FROM latest cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.rn = 1
            """
        ).fetchone()
        return {key: int(row[key] or 0) for key in ("creators", "enriched", "pending", "adopted")}

    def dashboard_breakdown(self, round_id: int | None = None) -> dict[str, Any]:
        if round_id is None:
            query = f"""
            WITH latest AS (
                SELECT cr.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY cr.platform, cr.user_id
                           ORDER BY cr.round_id DESC, cr.updated_at DESC
                       ) AS rn
                FROM creator_rounds cr
                JOIN rounds active_round ON active_round.id = cr.round_id
                WHERE active_round.archived_at IS NULL
            )
            SELECT cr.status AS status,
                   {TIER_SQL} AS label,
                   COUNT(*) AS n,
                   SUM(CASE WHEN c.fans IS NOT NULL THEN 1 ELSE 0 END) AS enriched
            FROM latest cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.rn = 1
            GROUP BY status, label
            """
            params: tuple[Any, ...] = ()
        else:
            query = f"""
            SELECT cr.status AS status,
                   {TIER_SQL} AS label,
                   COUNT(*) AS n,
                   SUM(CASE WHEN c.fans IS NOT NULL THEN 1 ELSE 0 END) AS enriched
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.round_id = ?
            GROUP BY status, label
            """
            params = (round_id,)
        rows = self._conn.execute(query, params).fetchall()
        metrics = {"creators": 0, "enriched": 0, "pending": 0, "adopted": 0}
        status_counts: dict[str, int] = {}
        tier_counts = {label: 0 for label in TIER_LABELS}
        tier_counts["未补全"] = 0
        for row in rows:
            count = int(row["n"] or 0)
            metrics["creators"] += count
            metrics["enriched"] += int(row["enriched"] or 0)
            status = str(row["status"] or "")
            status_counts[status] = status_counts.get(status, 0) + count
            if status == "pending":
                metrics["pending"] += count
            elif status == "adopted":
                metrics["adopted"] += count
            label = str(row["label"] or "未补全")
            tier_counts[label] = tier_counts.get(label, 0) + count
        return {
            "metrics": metrics,
            "status_counts": status_counts,
            "tier_counts": tier_counts,
        }

    def action_queue(
        self,
        *,
        actor_id: int,
        is_admin: bool,
        round_id: int | None,
        scope: str = "all",
        sort: str = "potential",
        limit: int = 6,
        include_others: bool = False,
    ) -> dict[str, Any]:
        if round_id is None:
            from_sql = """
            FROM (
                SELECT cr.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY cr.platform, cr.user_id
                           ORDER BY cr.round_id DESC, cr.updated_at DESC
                       ) AS rn
                FROM creator_rounds cr
                JOIN rounds active_round ON active_round.id = cr.round_id
                WHERE active_round.archived_at IS NULL
            ) cr
            JOIN rounds r ON r.id = cr.round_id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            LEFT JOIN users u ON u.id = c.follow_up_user_id
            """
            base_where = "cr.rn = 1"
        else:
            from_sql = """
            FROM creator_rounds cr
            JOIN rounds r ON r.id = cr.round_id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            LEFT JOIN users u ON u.id = c.follow_up_user_id
            """
            base_where = f"cr.round_id = {int(round_id)}"
        if is_admin and include_others:
            pending_visible = "cr.status = 'pending'"
        else:
            pending_visible = (
                "(cr.status = 'pending' AND "
                f"(c.follow_up_user_id IS NULL OR c.follow_up_user_id = {int(actor_id)}))"
            )
        unclaimed = "(c.follow_up_user_id IS NULL AND cr.status IN ('pending', 'shortlisted'))"
        scope_condition = {
            "all": f"(({pending_visible}) OR ({unclaimed}))",
            "pending": pending_visible,
            "unclaimed": unclaimed,
        }.get(scope, f"(({pending_visible}) OR ({unclaimed}))")

        counts_row = self._conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN {pending_visible} THEN 1 ELSE 0 END), 0) AS pending,
                COALESCE(SUM(CASE WHEN {unclaimed} THEN 1 ELSE 0 END), 0) AS unclaimed,
                COALESCE(SUM(CASE WHEN ({pending_visible}) OR ({unclaimed}) THEN 1 ELSE 0 END), 0) AS all_count
            {from_sql}
            WHERE {base_where}
            """
        ).fetchone()
        if sort == "ratio":
            order = (
                "CASE WHEN c.fans > 0 "
                "THEN CAST(cr.sample_interactions AS REAL) / c.fans ELSE 0 END DESC, "
                "cr.updated_at DESC, cr.user_id ASC"
            )
        elif sort == "potential":
            order = (
                "CASE WHEN cr.sample_notes > 0 "
                "THEN CAST(cr.sample_interactions AS REAL) / cr.sample_notes ELSE 0 END DESC, "
                "cr.updated_at DESC, cr.user_id ASC"
            )
        else:
            order = "cr.updated_at DESC, cr.user_id ASC"
        rows = self._conn.execute(
            f"""
            SELECT cr.round_id, cr.platform, cr.user_id, cr.status,
                   c.follow_up_user_id AS owner_user_id,
                   cr.sample_notes, cr.sample_interactions, cr.match_direction, cr.source_keywords,
                   cr.updated_at, cr.tier_label, c.nickname, c.avatar, c.fans, c.interaction,
                   r.name AS round_name, COALESCE(u.username, '') AS owner_username,
                   {TIER_SQL} AS derived_tier
            {from_sql}
            WHERE {base_where} AND ({scope_condition})
            ORDER BY {order}
            LIMIT ?
            """,
            (max(1, min(20, int(limit))),),
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "counts": {
                "all": int(counts_row["all_count"] or 0),
                "pending": int(counts_row["pending"] or 0),
                "unclaimed": int(counts_row["unclaimed"] or 0),
            },
        }

    def activity_calendar(self, days: int = 365, tz_offset: int = 480) -> dict[str, Any]:
        days = max(30, min(730, int(days)))
        tz_offset = max(-720, min(840, int(tz_offset)))
        end = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
        start = end - timedelta(days=days - 1)
        modifier = f"{tz_offset:+d} minutes"
        cutoff = f"-{days + 5} days"
        collected_rows = self._conn.execute(
            """
            SELECT date(datetime(substr(created_at, 1, 19), ?)) AS day,
                   COUNT(*) AS n
            FROM creators
            WHERE created_at IS NOT NULL AND created_at <> ''
              AND substr(created_at, 1, 19) >= datetime('now', ?)
            GROUP BY day
            """,
            (modifier, cutoff),
        ).fetchall()
        processed_rows = self._conn.execute(
            """
            SELECT date(datetime(substr(ts, 1, 19), ?)) AS day,
                   SUM(CASE
                       WHEN action = 'creator.review_updated' THEN 1
                       WHEN action = 'creator.review_bulk_updated' THEN
                           COALESCE(CAST(json_extract(details, '$.changed') AS INTEGER), 0)
                       ELSE 0
                   END) AS n
            FROM audit_log
            WHERE action IN ('creator.review_updated', 'creator.review_bulk_updated')
              AND substr(ts, 1, 19) >= datetime('now', ?)
            GROUP BY day
            """,
            (modifier, cutoff),
        ).fetchall()
        collected = {
            str(row["day"]): int(row["n"] or 0)
            for row in collected_rows
            if row["day"]
        }
        processed = {
            str(row["day"]): int(row["n"] or 0)
            for row in processed_rows
            if row["day"]
        }
        items: list[dict[str, Any]] = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            items.append(
                {
                    "date": key,
                    "collected": collected.get(key, 0),
                    "processed": processed.get(key, 0),
                }
            )
        return {
            "days": days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "items": items,
            "totals": {
                "collected": sum(item["collected"] for item in items),
                "processed": sum(item["processed"] for item in items),
            },
        }

    def round_efficiency(self, limit: int = 6) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT r.id AS round_id, r.name, r.platform, r.status, r.created_at, r.finished_at,
                   COUNT(cr.user_id) AS creators,
                   COALESCE(SUM(CASE WHEN c.fans IS NOT NULL THEN 1 ELSE 0 END), 0) AS enriched,
                   COALESCE(SUM(CASE WHEN cr.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                   COALESCE(SUM(CASE WHEN cr.status <> 'pending' THEN 1 ELSE 0 END), 0) AS reviewed,
                   COALESCE(SUM(CASE WHEN cr.status = 'adopted' THEN 1 ELSE 0 END), 0) AS adopted,
                   COALESCE((SELECT SUM(ac.cost) FROM api_calls ac WHERE ac.round_id = r.id), 0) AS cost
            FROM rounds r
            LEFT JOIN creator_rounds cr ON cr.round_id = r.id
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE r.archived_at IS NULL
            GROUP BY r.id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (max(1, min(20, int(limit))),),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "round_id": int(row["round_id"]),
                    "name": row["name"] or "",
                    "platform": row["platform"] or "",
                    "status": row["status"] or "",
                    "created_at": row["created_at"] or "",
                    "finished_at": row["finished_at"],
                    "creators": int(row["creators"] or 0),
                    "enriched": int(row["enriched"] or 0),
                    "pending": int(row["pending"] or 0),
                    "reviewed": int(row["reviewed"] or 0),
                    "adopted": int(row["adopted"] or 0),
                    "cost": int(row["cost"] or 0),
                }
            )
        totals = {
            key: sum(item[key] for item in items)
            for key in ("creators", "enriched", "pending", "reviewed", "adopted", "cost")
        }
        return {"items": items, "totals": totals}

    def all_status_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            """
            WITH latest AS (
                SELECT cr.status,
                       ROW_NUMBER() OVER (
                           PARTITION BY cr.platform, cr.user_id
                           ORDER BY cr.round_id DESC, cr.updated_at DESC
                       ) AS rn
                FROM creator_rounds cr
                JOIN rounds active_round ON active_round.id = cr.round_id
                WHERE active_round.archived_at IS NULL
            )
            SELECT status, COUNT(*) AS n FROM latest WHERE rn = 1 GROUP BY status
            """
        ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def all_tier_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            f"""
            WITH latest AS (
                SELECT cr.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY cr.platform, cr.user_id
                           ORDER BY cr.round_id DESC, cr.updated_at DESC
                       ) AS rn
                FROM creator_rounds cr
                JOIN rounds active_round ON active_round.id = cr.round_id
                WHERE active_round.archived_at IS NULL
            )
            SELECT {TIER_SQL} AS label, COUNT(*) AS n
            FROM latest cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.rn = 1
            GROUP BY label
            """
        ).fetchall()
        result = {label: 0 for label in TIER_LABELS}
        result["未补全"] = 0
        for row in rows:
            result[row["label"]] = result.get(row["label"], 0) + int(row["n"])
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

    def create_job(self, round_id: int, created_by: int | None = None) -> int:
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO jobs (round_id, status, stage, created_by, created_at, heartbeat_at) "
                "VALUES (?, 'pending', '排队中', ?, ?, ?)",
                (round_id, created_by, now, now),
            )
        return int(cur.lastrowid)

    def reconcile_stale_jobs(self, stale_minutes: int = 30) -> list[int]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(stale_minutes)))
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status IN ('pending', 'running')"
        ).fetchall()
        stale: list[int] = []
        for row in rows:
            raw = row["heartbeat_at"] or row["started_at"] or row["created_at"]
            try:
                last = datetime.fromisoformat(str(raw))
            except (TypeError, ValueError):
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last >= cutoff:
                continue
            finished = utc_now()
            stage = "启动超时" if row["status"] == "pending" else "执行超时"
            error = "任务进程已失联，系统已自动标记失败"
            job_cost = self.sum_job_cost(int(row["id"]))
            round_cost = (
                self.sum_round_cost(int(row["round_id"]))
                if row["round_id"] is not None
                else 0
            )
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE jobs SET status = 'failed', stage = ?, error = ?, cost = ?, "
                    "finished_at = ?, heartbeat_at = ? WHERE id = ? "
                    "AND status IN ('pending', 'running') "
                    "AND COALESCE(heartbeat_at, started_at, created_at) = ?",
                    (stage, error, job_cost, finished, finished, row["id"], raw),
                )
                if cur.rowcount == 0:
                    continue
                if row["round_id"] is not None:
                    self._conn.execute(
                        "UPDATE rounds SET status = 'failed', finished_at = ?, error = ?, "
                        "cost_tokens = ? WHERE id = ? AND status IN ('pending', 'running')",
                        (finished, error, round_cost, row["round_id"]),
                    )
            stale.append(int(row["id"]))
        return stale

    def claim_job(
        self,
        round_id: int,
        max_active: int | None = None,
        stale_minutes: int = 30,
        created_by: int | None = None,
    ) -> int:
        """Atomically create a job unless the round already has an active one."""
        self.reconcile_stale_jobs(stale_minutes)
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
            now = utc_now()
            cur = self._conn.execute(
                "INSERT INTO jobs (round_id, status, stage, created_by, created_at, heartbeat_at) "
                "VALUES (?, 'pending', ?, ?, ?, ?)",
                (round_id, "\u6392\u961f\u4e2d", created_by, now, now),
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
        updates.setdefault("heartbeat_at", utc_now())
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            self._conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*updates.values(), job_id])

    def finish_job(
        self,
        job_id: int,
        round_id: int | None,
        *,
        status: str,
        stage: str,
        error: str | None = None,
        progress_done: int | None = None,
        cost: int | None = None,
        round_cost: int | None = None,
        finished_at: str | None = None,
    ) -> bool:
        finished = finished_at or utc_now()
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, error = ?,
                    progress_done = COALESCE(?, progress_done),
                    cost = COALESCE(?, cost),
                    finished_at = ?, heartbeat_at = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (
                    status,
                    stage,
                    error,
                    progress_done,
                    cost,
                    finished,
                    finished,
                    job_id,
                ),
            )
            if cur.rowcount == 0:
                return False
            if round_id is not None:
                self._conn.execute(
                    """
                    UPDATE rounds SET status = ?, finished_at = ?, error = ?,
                        cost_tokens = COALESCE(?, cost_tokens)
                    WHERE id = ? AND status IN ('pending', 'running', 'failed')
                    """,
                    (status, finished, error, round_cost, round_id),
                )
        return True

    def cancel_job(
        self, job_id: int, cancelled_by: int | None = None
    ) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        if job["status"] not in ("pending", "running"):
            return job
        finished = utc_now()
        job_cost = self.sum_job_cost(job_id)
        round_cost = (
            self.sum_round_cost(int(job["round_id"]))
            if job["round_id"] is not None
            else 0
        )
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'cancelled', stage = '已取消', error = '任务已取消', "
                "cost = ?, cancelled_by = ?, cancelled_at = ?, finished_at = ?, heartbeat_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (job_cost, cancelled_by, finished, finished, finished, job_id),
            )
            if cur.rowcount and job["round_id"] is not None:
                self._conn.execute(
                    "UPDATE rounds SET status = 'cancelled', finished_at = ?, error = '任务已取消', "
                    "cost_tokens = ? WHERE id = ? AND status IN ('pending', 'running', 'failed')",
                    (finished, round_cost, job["round_id"]),
                )
        return self.get_job(job_id)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def latest_job(self, round_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE round_id = ? ORDER BY id DESC LIMIT 1", (round_id,)
        ).fetchone()
        return dict(row) if row else None

    def latest_job_overall(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def latest_jobs_by_round(self) -> dict[int, dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT j.*
            FROM jobs j
            JOIN (
                SELECT round_id, MAX(id) AS id FROM jobs GROUP BY round_id
            ) latest ON latest.id = j.id
            """
        ).fetchall()
        return {int(row["round_id"]): dict(row) for row in rows if row["round_id"] is not None}

    def latest_active_job(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE status IN ('pending', 'running') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def count_active_jobs(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('pending', 'running')"
        ).fetchone()
        return int(row["n"])

    def recent_api_calls(
        self, round_id: int, limit: int = 20, job_id: int | None = None
    ) -> list[dict[str, Any]]:
        if job_id is None:
            rows = self._conn.execute(
                "SELECT * FROM api_calls WHERE round_id = ? ORDER BY id DESC LIMIT ?",
                (round_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM api_calls WHERE round_id = ? AND job_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (round_id, job_id, limit),
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

    def update_user_with_admin_guard(
        self,
        user_id: int,
        role: str | None = None,
        disabled: bool | None = None,
        can_collect: bool | None = None,
    ) -> None:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            target = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not target:
                raise ValueError("用户不存在")
            removes_admin = target["role"] == "admin" and (
                bool(disabled) or (role is not None and role != "admin")
            )
            if removes_admin:
                count = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0"
                ).fetchone()["n"]
                if int(count) <= 1:
                    raise ValueError("至少保留一个可用管理员")
            updates: dict[str, Any] = {"updated_at": utc_now()}
            if role is not None:
                updates["role"] = role
            if disabled is not None:
                updates["disabled"] = 1 if disabled else 0
            if can_collect is not None:
                updates["can_collect"] = 1 if can_collect else 0
            sets = ", ".join(f"{key} = ?" for key in updates)
            self._conn.execute(
                f"UPDATE users SET {sets} WHERE id = ?",
                [*updates.values(), user_id],
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def prune_maintenance(self, audit_retention_days: int = 365) -> dict[str, int]:
        now = utc_now()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(audit_retention_days)))
        ).isoformat(timespec="seconds")
        with self._conn:
            expired = self._conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (now,)
            ).rowcount
            overflow = self._conn.execute(
                """
                DELETE FROM sessions WHERE token_hash IN (
                    SELECT token_hash FROM (
                        SELECT token_hash,
                               ROW_NUMBER() OVER (
                                   PARTITION BY user_id
                                   ORDER BY created_at DESC, last_seen DESC, token_hash DESC
                               ) AS rn
                        FROM sessions
                    )
                    WHERE rn > 20
                )
                """
            ).rowcount
            audits = self._conn.execute(
                "DELETE FROM audit_log WHERE ts < ?", (cutoff,)
            ).rowcount
        return {
            "sessions": max(0, expired) + max(0, overflow),
            "sessions_expired": max(0, expired),
            "sessions_overflow": max(0, overflow),
            "audit_log": max(0, audits),
        }

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
            self._conn.execute(
                """
                DELETE FROM sessions
                WHERE user_id = ?
                  AND token_hash NOT IN (
                      SELECT token_hash FROM sessions
                      WHERE user_id = ?
                      ORDER BY created_at DESC
                      LIMIT 20
                  )
                """,
                (user_id, user_id),
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
            (token_hash, utc_now()),
        ).fetchone()
        if not row:
            return None
        try:
            last_seen = datetime.fromisoformat(str(row["last_seen"]))
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            stale = datetime.now(timezone.utc) - last_seen > timedelta(minutes=5)
        except (TypeError, ValueError):
            stale = True
        if stale:
            with self._conn:
                self._conn.execute(
                    "UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
                    (utc_now(), token_hash),
                )
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

    def delete_user_sessions(self, user_id: int, except_token_hash: str | None = None) -> int:
        with self._conn:
            if except_token_hash:
                cur = self._conn.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
                    (user_id, except_token_hash),
                )
            else:
                cur = self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return max(0, cur.rowcount)

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
