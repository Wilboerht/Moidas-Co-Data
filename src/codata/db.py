from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .cooperation_statuses import (
    COOPERATION_OPEN_CODES,
    COOPERATION_STATUS_CODES,
    COOPERATION_TERMINAL_CODES,
    DEFAULT_COOPERATION_STATUS,
    NEW_COOPERATION_STATUS,
    normalize_cooperation_status,
)
from .identity import (
    handle_from_raw_title,
    normalize_account_id,
    normalize_identity,
    split_platform_identity,
)
from .lead_statuses import (
    DEFAULT_LEAD_SOURCE,
    DEFAULT_LEAD_STATUS,
    LEAD_STATUS_CODES,
    normalize_lead_source,
    normalize_lead_status,
)
from .normalize import TIER_LABELS, derive_tags, normalize_tier_label, utc_now
from .processes import pid_alive, process_matches
from .statuses import DEFAULT_STATUS, normalize_status



EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_SECURITY_MODES = {"ssl", "starttls", "none"}
EMAIL_ACCOUNT_SCOPES = {"personal", "shared"}


def _cooldown_until(value: Any, days: int) -> datetime | None:
    if not value or days <= 0:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(
        stamp.timestamp() + days * 86400, tz=timezone.utc
    )


def normalize_email_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or not EMAIL_PATTERN.match(text):
        raise ValueError("邮箱格式不正确")
    return text


class JobBusyError(RuntimeError):
    """A round already has a pending or running job."""


SCHEMA_VERSION = 21

LEAD_IDENTITY_STATUSES = {"unverified", "verified", "mismatch", "not_found"}


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
    latest_run_id INTEGER,
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
    last_run_id INTEGER,
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
    cooperation_status TEXT,
    cooperation_updated_at TEXT,
    cooperation_count INTEGER NOT NULL DEFAULT 0,
    cooperation_last_completed_at TEXT,
    cooperation_case_id INTEGER,
    cooperation_reset_at TEXT,
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
    created_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (round_id, platform, user_id)
);

CREATE TABLE IF NOT EXISTS cooperation_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    status_updated_at TEXT NOT NULL,
    completed_at TEXT,
    closed_at TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (platform, user_id, seq)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cooperation_cases_one_open
ON cooperation_cases (platform, user_id)
WHERE status IN ('not_contacted', 'contacted', 'negotiating', 'cooperating');
CREATE INDEX IF NOT EXISTS idx_cooperation_cases_creator
ON cooperation_cases (platform, user_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_cooperation_cases_status
ON cooperation_cases (status);

CREATE TABLE IF NOT EXISTS cooperation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status_from TEXT,
    status_to TEXT,
    note TEXT,
    metadata TEXT,
    actor_user_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cooperation_events_case
ON cooperation_events (case_id, id);

CREATE TABLE IF NOT EXISTS cooperation_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    event_id INTEGER,
    original_name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    description TEXT NOT NULL DEFAULT '',
    uploaded_by INTEGER,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    deleted_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cooperation_attachments_case
ON cooperation_attachments (case_id, id);
CREATE INDEX IF NOT EXISTS idx_cooperation_attachments_event
ON cooperation_attachments (event_id);

CREATE TABLE IF NOT EXISTS cooperation_works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    deleted_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cooperation_works_case
ON cooperation_works (case_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cooperation_works_active_url
ON cooperation_works (case_id, url)
WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'xhs',
    nickname TEXT,
    account_id TEXT,
    platform_handle TEXT,
    identity_status TEXT NOT NULL DEFAULT 'unverified',
    identity_confidence TEXT NOT NULL DEFAULT '',
    identity_provider TEXT NOT NULL DEFAULT '',
    identity_checked_at TEXT,
    identity_note TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS lead_identity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    old_user_id TEXT,
    new_user_id TEXT,
    old_handle TEXT,
    new_handle TEXT,
    provider TEXT,
    confidence TEXT,
    status TEXT NOT NULL,
    note TEXT,
    actor_id INTEGER,
    created_at TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_lead_identity_events_lead ON lead_identity_events (lead_id, id);

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
    error TEXT,
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
    can_edit_leads INTEGER NOT NULL DEFAULT 0,
    can_send_email INTEGER NOT NULL DEFAULT 0,
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


CREATE TABLE IF NOT EXISTS email_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'personal',
    owner_user_id INTEGER,
    label TEXT NOT NULL DEFAULT '',
    from_name TEXT NOT NULL DEFAULT '',
    from_email TEXT NOT NULL,
    reply_to TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    security TEXT NOT NULL DEFAULT 'starttls',
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    key_fingerprint TEXT NOT NULL DEFAULT '',
    daily_limit INTEGER NOT NULL DEFAULT 20,
    min_interval_seconds INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_test_at TEXT,
    last_test_error TEXT,
    auth_failed_at TEXT,
    auth_error TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_accounts_owner ON email_accounts (owner_user_id);
CREATE INDEX IF NOT EXISTS idx_email_accounts_scope ON email_accounts (scope, enabled);

CREATE TABLE IF NOT EXISTS creator_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    is_primary INTEGER NOT NULL DEFAULT 0,
    last_sent_at TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (platform, user_id, email_normalized)
);

CREATE INDEX IF NOT EXISTS idx_creator_emails_creator
ON creator_emails (platform, user_id);
CREATE INDEX IF NOT EXISTS idx_creator_emails_email
ON creator_emails (email_normalized);
CREATE UNIQUE INDEX IF NOT EXISTS idx_creator_emails_primary
ON creator_emails (platform, user_id) WHERE is_primary = 1;


CREATE TABLE IF NOT EXISTS email_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    account_id INTEGER NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    scheduled_start TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    window_start TEXT NOT NULL DEFAULT '09:00',
    window_end TEXT NOT NULL DEFAULT '18:00',
    weekdays TEXT NOT NULL DEFAULT '[1,2,3,4,5]',
    interval_seconds INTEGER NOT NULL DEFAULT 60,
    jitter_seconds INTEGER NOT NULL DEFAULT 30,
    daily_limit INTEGER NOT NULL DEFAULT 20,
    auto_mark_contacted INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    paused_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_email_campaigns_status
ON email_campaigns (status, scheduled_start);
CREATE INDEX IF NOT EXISTS idx_email_campaigns_creator
ON email_campaigns (created_by, id);

CREATE TABLE IF NOT EXISTS email_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    creator_platform TEXT NOT NULL,
    creator_user_id TEXT NOT NULL,
    creator_nickname TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    manual_retries INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    claimed_at TEXT,
    sent_at TEXT,
    sent_date TEXT,
    message_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (campaign_id, creator_platform, creator_user_id)
);

CREATE INDEX IF NOT EXISTS idx_email_recipients_campaign
ON email_recipients (campaign_id, status, id);
CREATE INDEX IF NOT EXISTS idx_email_recipients_due
ON email_recipients (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS email_suppressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_suppressions_email
ON email_suppressions (email_normalized);


CREATE TABLE IF NOT EXISTS email_worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    owner TEXT NOT NULL DEFAULT '',
    heartbeat_at TEXT,
    last_tick_at TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);



CREATE INDEX IF NOT EXISTS idx_email_recipients_email_sent
ON email_recipients (email_normalized, status, sent_at);
CREATE INDEX IF NOT EXISTS idx_email_recipients_creator_sent
ON email_recipients (creator_platform, creator_user_id, status, sent_at);

CREATE TABLE IF NOT EXISTS email_unsubscribe_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL,
    creator_platform TEXT NOT NULL DEFAULT '',
    creator_user_id TEXT NOT NULL DEFAULT '',
    campaign_id INTEGER,
    recipient_id INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_unsubscribe_email
ON email_unsubscribe_tokens (email_normalized, expires_at);

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
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log (target, id);

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

ENRICHMENT_SQL = """
CASE
    WHEN c.fetched_at IS NULL OR c.fetched_at = '' THEN 'not_fetched'
    WHEN c.fans IS NOT NULL AND c.posted IS NOT NULL AND c.liked IS NOT NULL THEN 'complete'
    ELSE 'partial'
END
"""

ALL_ROUNDS_FROM_SQL = """
FROM (
    SELECT cr.*,
           COALESCE(sources.keyword_values, '[]') AS source_keywords_all,
           COALESCE(sources.label_values, '[]') AS source_labels_all
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
    LEFT JOIN (
        SELECT platform, user_id,
               MAX(CASE WHEN kind = 'keyword' THEN source_values END) AS keyword_values,
               MAX(CASE WHEN kind = 'label' THEN source_values END) AS label_values
        FROM (
            SELECT s.platform, s.user_id, s.kind, json_group_array(s.value) AS source_values
            FROM (
                SELECT DISTINCT s.platform, s.user_id, s.kind, s.value
                FROM creator_round_sources s
                JOIN rounds active2 ON active2.id = s.round_id
                WHERE active2.archived_at IS NULL
                ORDER BY s.platform, s.user_id, s.kind, s.value
            ) s
            GROUP BY s.platform, s.user_id, s.kind
        )
        GROUP BY platform, user_id
    ) sources ON sources.platform = cr.platform AND sources.user_id = cr.user_id
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
       cr.auto_tags, cr.status, cr.status AS round_status, cr.match_direction, cr.tags_manual, cr.remark,
       cr.data_round, cr.tier_label, cr.account_id,
       c.follow_up_user_id AS owner_user_id, c.followed_at AS claimed_at,
       COALESCE(fu.username, '') AS owner_username,
       c.cooperation_status, c.cooperation_updated_at,
       c.cooperation_count, c.cooperation_last_completed_at, c.cooperation_case_id,
       cr.updated_at, cr.cooperation_platform, cr.raw_title,
       n.title AS top_title, n.url AS top_url, n.liked AS top_liked,
       r.name AS round_name, {TIER_SQL} AS derived_tier,
       {ENRICHMENT_SQL} AS enrichment_status
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
    "latest_run_id",
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
        self.last_import_warnings: list[str] = []
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
                try:
                    self._migrate_schema()
                except Exception:
                    self._conn.close()
                    raise
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
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库 schema v{current} 高于当前支持的 v{SCHEMA_VERSION}，请升级应用后再启动"
            )
        if current == SCHEMA_VERSION:
            self._detect_fts()
            self._ensure_current_compat_columns()
            self._ensure_creator_sources()
            return
        has_data = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rounds'"
        ).fetchone()
        if has_data:
            self._backup_before_migration(current)
        self._init_db()
        self._migrate_cooperation_v11(current)
        self._migrate_source_labels()
        self._rebuild_creator_sources()
        self._migrate_cooperation_cases_v13(current)
        self._migrate_lead_identity_v14(current)
        self._migrate_cooperation_v15(current)
        self._migrate_snapshot_v16(current)
        self._migrate_email_v18(current)
        self._migrate_email_v19(current)
        self._migrate_email_v20(current)
        self._migrate_email_v21(current)
        self._ensure_creator_round_added_at()
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def _ensure_current_compat_columns(self) -> None:
        with self._conn:
            user_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "can_collect" not in user_columns:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN can_collect INTEGER NOT NULL DEFAULT 0"
                )
            if "can_edit_leads" not in user_columns:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN can_edit_leads INTEGER NOT NULL DEFAULT 0"
                )
                self._conn.execute(
                    "UPDATE users SET can_edit_leads = 1 WHERE can_collect = 1"
                )
            if "can_send_email" not in user_columns:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN can_send_email INTEGER NOT NULL DEFAULT 0"
                )
            if "must_change_password" not in user_columns:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
                )
            creator_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(creators)").fetchall()
            }
            if "cooperation_count" not in creator_columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "cooperation_last_completed_at" not in creator_columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_last_completed_at TEXT"
                )
            if "cooperation_case_id" not in creator_columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_case_id INTEGER"
                )
            if "cooperation_reset_at" not in creator_columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_reset_at TEXT"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_creators_cooperation_count "
                "ON creators (cooperation_count)"
            )
            round_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(rounds)").fetchall()
            }
            if "latest_run_id" not in round_columns:
                self._conn.execute(
                    "ALTER TABLE rounds ADD COLUMN latest_run_id INTEGER"
                )
            hit_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(note_hits)").fetchall()
            }
            if "last_run_id" not in hit_columns:
                self._conn.execute(
                    "ALTER TABLE note_hits ADD COLUMN last_run_id INTEGER"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_note_hits_run "
                "ON note_hits (round_id, last_run_id)"
            )
            self._ensure_lead_identity_columns()
        self._ensure_creator_round_added_at()
        self.try_create_lead_unique_indexes()

    def _ensure_creator_round_added_at(self) -> None:
        with self._conn:
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(creator_rounds)").fetchall()
            }
            if "created_at" not in columns:
                self._conn.execute("ALTER TABLE creator_rounds ADD COLUMN created_at TEXT")
            self._conn.execute(
                "UPDATE creator_rounds SET created_at = updated_at "
                "WHERE created_at IS NULL OR created_at = ''"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_creator_rounds_added "
                "ON creator_rounds (round_id, created_at)"
            )

    def _ensure_lead_identity_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        for column, ddl in (
            ("platform_handle", "TEXT"),
            ("identity_status", "TEXT NOT NULL DEFAULT 'unverified'"),
            ("identity_confidence", "TEXT NOT NULL DEFAULT ''"),
            ("identity_provider", "TEXT NOT NULL DEFAULT ''"),
            ("identity_checked_at", "TEXT"),
            ("identity_note", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_handle ON leads (platform, platform_handle)"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_identity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                old_user_id TEXT,
                new_user_id TEXT,
                old_handle TEXT,
                new_handle TEXT,
                provider TEXT,
                confidence TEXT,
                status TEXT NOT NULL,
                note TEXT,
                actor_id INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_identity_events_lead "
            "ON lead_identity_events (lead_id, id)"
        )

    def _migrate_lead_identity_v14(self, current: int) -> None:
        if current >= 14:
            return
        with self._conn:
            self._ensure_lead_identity_columns()
            rows = self._conn.execute(
                """
                SELECT id, platform, account_id, profile_url, raw_title, status,
                       converted_user_id, converted_at
                FROM leads
                """
            ).fetchall()
            now = utc_now()
            updates: list[tuple[Any, ...]] = []
            for row in rows:
                platform = str(row["platform"] or "xhs")
                account = str(row["account_id"] or "").strip()
                profile = str(row["profile_url"] or "").strip()
                user_id, handle = split_platform_identity(platform, account, profile)
                status = str(row["status"] or "")
                identity_status = "unverified"
                confidence = ""
                checked_at = None
                if status == "converted":
                    user_id = str(row["converted_user_id"] or user_id or account).strip()
                    handle = handle or handle_from_raw_title(row["raw_title"] or "")
                    identity_status = "verified"
                    confidence = "conversion"
                    checked_at = row["converted_at"] or now
                updates.append(
                    (
                        user_id,
                        handle,
                        identity_status,
                        confidence,
                        checked_at,
                        int(row["id"]),
                    )
                )
            if updates:
                self._conn.executemany(
                    """
                    UPDATE leads
                    SET account_id = ?, platform_handle = ?, identity_status = ?,
                        identity_confidence = ?, identity_checked_at = ?
                    WHERE id = ?
                    """,
                    updates,
                )
        self.try_create_lead_unique_indexes()

    def _migrate_cooperation_v15(self, current: int) -> None:
        if current >= 15:
            return
        with self._conn:
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(creators)").fetchall()
            }
            if "cooperation_reset_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_reset_at TEXT"
                )

    def _migrate_snapshot_v16(self, current: int) -> None:
        if current >= 16:
            return
        with self._conn:
            round_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(rounds)").fetchall()
            }
            if "latest_run_id" not in round_columns:
                self._conn.execute(
                    "ALTER TABLE rounds ADD COLUMN latest_run_id INTEGER"
                )
            hit_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(note_hits)").fetchall()
            }
            if "last_run_id" not in hit_columns:
                self._conn.execute(
                    "ALTER TABLE note_hits ADD COLUMN last_run_id INTEGER"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_note_hits_run "
                "ON note_hits (round_id, last_run_id)"
            )


    def _migrate_email_v18(self, current: int) -> None:
        if current >= 18:
            return
        with self._conn:
            user_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "can_send_email" not in user_columns:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN can_send_email INTEGER NOT NULL DEFAULT 0"
                )


    def _migrate_email_v19(self, current: int) -> None:
        if current >= 19:
            return
        with self._conn:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_campaigns_status "
                "ON email_campaigns (status, scheduled_start)"
            )


    def _migrate_email_v20(self, current: int) -> None:
        if current >= 20:
            return
        with self._conn:
            account_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(email_accounts)"
                ).fetchall()
            }
            if "auth_failed_at" not in account_columns:
                self._conn.execute(
                    "ALTER TABLE email_accounts ADD COLUMN auth_failed_at TEXT"
                )
            if "auth_error" not in account_columns:
                self._conn.execute(
                    "ALTER TABLE email_accounts ADD COLUMN auth_error TEXT NOT NULL DEFAULT ''"
                )
            recipient_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(email_recipients)"
                ).fetchall()
            }
            if "sent_date" not in recipient_columns:
                self._conn.execute(
                    "ALTER TABLE email_recipients ADD COLUMN sent_date TEXT"
                )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS email_worker_state "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), owner TEXT NOT NULL DEFAULT '', "
                "heartbeat_at TEXT)"
            )


    def _migrate_email_v21(self, current: int) -> None:
        if current >= 21:
            return
        with self._conn:
            account_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(email_accounts)"
                ).fetchall()
            }
            if "min_interval_seconds" not in account_columns:
                self._conn.execute(
                    "ALTER TABLE email_accounts ADD COLUMN "
                    "min_interval_seconds INTEGER NOT NULL DEFAULT 60"
                )
            recipient_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(email_recipients)"
                ).fetchall()
            }
            if "manual_retries" not in recipient_columns:
                self._conn.execute(
                    "ALTER TABLE email_recipients ADD COLUMN "
                    "manual_retries INTEGER NOT NULL DEFAULT 0"
                )
            worker_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(email_worker_state)"
                ).fetchall()
            }
            if "last_tick_at" not in worker_columns:
                self._conn.execute(
                    "ALTER TABLE email_worker_state ADD COLUMN last_tick_at TEXT"
                )
            if "last_error" not in worker_columns:
                self._conn.execute(
                    "ALTER TABLE email_worker_state ADD COLUMN "
                    "last_error TEXT NOT NULL DEFAULT ''"
                )
            campaigns = self._conn.execute(
                "SELECT id, weekdays FROM email_campaigns"
            ).fetchall()
            for campaign in campaigns:
                try:
                    days = [int(day) for day in json.loads(campaign["weekdays"])]
                except (TypeError, ValueError):
                    continue
                if 0 in days and all(0 <= day <= 6 for day in days):
                    self._conn.execute(
                        "UPDATE email_campaigns SET weekdays = ? WHERE id = ?",
                        (json.dumps(sorted({day + 1 for day in days})), campaign["id"]),
                    )

    def _migrate_cooperation_cases_v13(self, current: int) -> None:
        if current >= 13:
            return
        with self._conn:
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(creators)").fetchall()
            }
            if "cooperation_count" not in columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "cooperation_last_completed_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_last_completed_at TEXT"
                )
            if "cooperation_case_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE creators ADD COLUMN cooperation_case_id INTEGER"
                )
            rows = self._conn.execute(
                "SELECT platform, user_id, cooperation_status, cooperation_updated_at "
                "FROM creators WHERE COALESCE(cooperation_status, '') != ''"
            ).fetchall()
            for row in rows:
                code = normalize_cooperation_status(
                    row["cooperation_status"], default=""
                )
                if code not in COOPERATION_STATUS_CODES:
                    continue
                existing = self._conn.execute(
                    "SELECT 1 FROM cooperation_cases "
                    "WHERE platform = ? AND user_id = ? LIMIT 1",
                    (row["platform"], row["user_id"]),
                ).fetchone()
                if existing:
                    continue
                now = utc_now()
                stamp = str(row["cooperation_updated_at"] or now)
                completed_at = stamp if code == "completed" else None
                closed_at = (
                    stamp if code in ("declined", "cancelled") else None
                )
                cur = self._conn.execute(
                    """
                    INSERT INTO cooperation_cases
                    (platform, user_id, seq, status, summary, started_at,
                     status_updated_at, completed_at, closed_at, created_at, updated_at)
                    VALUES (?, ?, 1, ?, '', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["platform"],
                        row["user_id"],
                        code,
                        stamp,
                        stamp,
                        completed_at,
                        closed_at,
                        stamp,
                        stamp,
                    ),
                )
                case_id = int(cur.lastrowid)
                self._conn.execute(
                    """
                    INSERT INTO cooperation_events
                    (case_id, event_type, status_from, status_to, note, metadata, created_at)
                    VALUES (?, 'migrated', '', ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        code,
                        "由旧版合作状态迁移",
                        json.dumps({"legacy_status": code}, ensure_ascii=False),
                        stamp,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE creators
                    SET cooperation_count = 1,
                        cooperation_last_completed_at = ?,
                        cooperation_case_id = ?
                    WHERE platform = ? AND user_id = ?
                    """,
                    (
                        completed_at,
                        case_id,
                        row["platform"],
                        row["user_id"],
                    ),
                )

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

    def _migrate_cooperation_v11(self, current: int) -> None:
        if current >= 11:
            return
        now = utc_now()
        with self._conn:
            if current < 10:
                # v8/v9 stored the latest review result in cooperation_status.
                # Reset it and enroll creators that were actually adopted in a round.
                self._conn.execute(
                    """
                    UPDATE creators
                    SET cooperation_status = NULL, cooperation_updated_at = NULL
                    WHERE cooperation_status IN ('pending', 'shortlisted', 'adopted', 'rejected')
                    """
                )
                self._conn.execute(
                    """
                    UPDATE creators
                    SET cooperation_status = ?, cooperation_updated_at = ?
                    WHERE COALESCE(cooperation_status, '') = ''
                      AND EXISTS (
                          SELECT 1 FROM creator_rounds cr
                          WHERE cr.platform = creators.platform
                            AND cr.user_id = creators.user_id
                            AND cr.status = 'adopted'
                      )
                    """,
                    (DEFAULT_COOPERATION_STATUS, now),
                )
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(creators)").fetchall()
            }
            self._conn.execute("DROP INDEX IF EXISTS idx_creators_adoption_status")
            if "adoption_updated_at" in columns:
                self._conn.execute("ALTER TABLE creators DROP COLUMN adoption_updated_at")
            if "adoption_status" in columns:
                self._conn.execute("ALTER TABLE creators DROP COLUMN adoption_status")

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

    def prune_trash(
        self, retention_days: int = 90, *, apply: bool = True
    ) -> dict[str, Any]:
        trash_dir = self.root / "trash"
        files = (
            [path for path in trash_dir.rglob("*") if path.is_file()]
            if trash_dir.exists()
            else []
        )
        cutoff = datetime.now().timestamp() - max(1, retention_days) * 86400
        stale = [path for path in files if path.stat().st_mtime < cutoff]
        report: dict[str, Any] = {
            "files": len(files),
            "expired": len(stale),
            "bytes": sum(path.stat().st_size for path in stale),
            "deleted": 0,
            "errors": [],
        }
        if not apply:
            return report
        for path in stale:
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
        if "cooperation_status" not in creator_columns:
            self._conn.execute("ALTER TABLE creators ADD COLUMN cooperation_status TEXT")
            self._conn.execute("ALTER TABLE creators ADD COLUMN cooperation_updated_at TEXT")
        if "cooperation_count" not in creator_columns:
            self._conn.execute(
                "ALTER TABLE creators ADD COLUMN cooperation_count "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "cooperation_last_completed_at" not in creator_columns:
            self._conn.execute(
                "ALTER TABLE creators ADD COLUMN cooperation_last_completed_at TEXT"
            )
        if "cooperation_case_id" not in creator_columns:
            self._conn.execute(
                "ALTER TABLE creators ADD COLUMN cooperation_case_id INTEGER"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creators_cooperation_status ON creators (cooperation_status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creators_cooperation_owner "
            "ON creators (cooperation_status, follow_up_user_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creators_cooperation_count "
            "ON creators (cooperation_count)"
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
        if "error" not in api_columns:
            self._conn.execute("ALTER TABLE api_calls ADD COLUMN error TEXT")
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
        if "can_edit_leads" not in user_columns:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN can_edit_leads INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "UPDATE users SET can_edit_leads = 1 WHERE can_collect = 1"
            )
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
        error: str | None = None,
        commit: bool = True,
    ) -> None:
        with self._write(commit):
            self._conn.execute(
                """
                INSERT INTO api_calls
                (round_id, job_id, platform, provider, kind, path, params, code, cost,
                 cost_native, cost_currency, estimated_cost, attempt, status, error,
                 duration_ms, raw_file, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    (error or None) and str(error)[:1000],
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
            raise ValueError("该轮次已有任务在运行，请取消或等待结束")
        if self._round_has_live_job_process(round_id):
            raise ValueError("上一任务进程仍在退出，请稍后再归档")
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
            "WHERE cr.round_id = ? AND cr.status <> 'pending'",
            "creators_claimed": "SELECT COUNT(DISTINCT cr.platform || '|' || cr.user_id) AS n FROM creator_rounds cr "
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
        counts["live_process"] = self._round_has_live_job_process(round_id)
        counts["can_purge"] = (
            counts["jobs_active"] == 0
            and counts["leads"] == 0
            and not counts["live_process"]
        )
        return counts

    def round_keyword_words(self, round_id: int) -> set[str]:
        rows = self._conn.execute(
            "SELECT k.word FROM round_keywords rk "
            "JOIN keywords k ON k.id = rk.keyword_id WHERE rk.round_id = ?",
            (round_id,),
        ).fetchall()
        return {str(row["word"]) for row in rows}

    def get_round_keywords(
        self, round_id: int, *, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"""
            SELECT k.id, k.word, k.category, k.enabled, COALESCE(rk.pages, r.pages) AS pages
            FROM round_keywords rk
            JOIN keywords k ON k.id = rk.keyword_id
            JOIN rounds r ON r.id = rk.round_id
            WHERE rk.round_id = ? {"AND k.enabled = 1" if not include_disabled else ""}
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
                AND NOT EXISTS (
                    SELECT 1 FROM cooperation_cases cc
                    WHERE cc.platform = creators.platform AND cc.user_id = creators.user_id
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
                "AND NOT EXISTS (SELECT 1 FROM cooperation_cases cc "
                "WHERE cc.platform = creators.platform AND cc.user_id = creators.user_id) "
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

    def prune_snapshot_creators(self, round_id: int, run_id: int) -> int:
        current = {
            (entry["platform"], entry["user_id"])
            for entry in self.round_author_stats(round_id, run_id=run_id)
        }
        rows = self._conn.execute(
            """
            SELECT DISTINCT cr.platform, cr.user_id
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.round_id = ? AND cr.status = 'pending'
              AND c.follow_up_user_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM note_hits nh
                  JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
                  WHERE nh.round_id = cr.round_id
                    AND n.platform = cr.platform AND n.author_id = cr.user_id
              )
            """,
            (round_id,),
        ).fetchall()
        stale = [
            (row["platform"], row["user_id"])
            for row in rows
            if (row["platform"], row["user_id"]) not in current
        ]
        if not stale:
            return 0
        with self._conn:
            for platform, user_id in stale:
                self._conn.execute(
                    "DELETE FROM creator_rounds WHERE round_id = ? AND platform = ? "
                    "AND user_id = ? AND status = 'pending'",
                    (round_id, platform, user_id),
                )
        return len(stale)

    def update_round(self, round_id: int, **fields: Any) -> None:
        updates = {key: value for key, value in fields.items() if key in ROUND_FIELDS}
        if not updates:
            return
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            self._conn.execute(f"UPDATE rounds SET {sets} WHERE id = ?", [*updates.values(), round_id])

    # ---- notes / creators ----

    def upsert_note(self, note: dict[str, Any], *, commit: bool = True) -> None:
        now = utc_now()
        with self._write(commit):
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
                    liked = COALESCE(excluded.liked, notes.liked),
                    collected = COALESCE(excluded.collected, notes.collected),
                    comments = COALESCE(excluded.comments, notes.comments),
                    shares = COALESCE(excluded.shares, notes.shares),
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
        run_id: int | None = None,
        commit: bool = True,
    ) -> None:
        with self._write(commit):
            self._conn.execute(
                """
                INSERT INTO note_hits
                (round_id, platform, note_id, keyword, page, rank, last_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(round_id, platform, note_id, keyword) DO UPDATE SET
                    page = COALESCE(excluded.page, note_hits.page),
                    rank = COALESCE(excluded.rank, note_hits.rank),
                    last_run_id = COALESCE(excluded.last_run_id, note_hits.last_run_id)
                """,
                (round_id, platform, note_id, keyword, page, rank, run_id),
            )

    def upsert_creator_basic(
        self, creator: dict[str, Any], *, commit: bool = True
    ) -> None:
        if not creator.get("user_id"):
            return
        with self._write(commit):
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

    @staticmethod
    def _normalize_cooperation_timestamp(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d",
                "%Y/%m/%d",
            ):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")

    def _creator_exists(self, platform: str, user_id: str) -> bool:
        if self._conn.execute(
            "SELECT 1 FROM creators WHERE platform = ? AND user_id = ? LIMIT 1",
            (platform, user_id),
        ).fetchone():
            return True
        if self._conn.execute(
            "SELECT 1 FROM creator_rounds WHERE platform = ? AND user_id = ? LIMIT 1",
            (platform, user_id),
        ).fetchone():
            return True
        return bool(
            self._conn.execute(
                "SELECT 1 FROM leads WHERE converted_platform = ? AND converted_user_id = ? LIMIT 1",
                (platform, user_id),
            ).fetchone()
        )

    def _ensure_creator_stub(self, platform: str, user_id: str) -> None:
        self._conn.execute(
            "INSERT INTO creators (platform, user_id) VALUES (?, ?) "
            "ON CONFLICT(platform, user_id) DO NOTHING",
            (platform, user_id),
        )

    def _cooperation_summary_row_locked(
        self, platform: str, user_id: str
    ) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT cooperation_status, cooperation_updated_at, cooperation_count, "
            "cooperation_last_completed_at, cooperation_case_id "
            "FROM creators WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()

    def _latest_cooperation_case_locked(
        self, platform: str, user_id: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM cooperation_cases "
            "WHERE platform = ? AND user_id = ? ORDER BY seq DESC LIMIT 1",
            (platform, user_id),
        ).fetchone()
        return dict(row) if row else None

    def _open_cooperation_case_locked(
        self, platform: str, user_id: str
    ) -> dict[str, Any] | None:
        placeholders = ",".join("?" * len(COOPERATION_OPEN_CODES))
        row = self._conn.execute(
            f"SELECT * FROM cooperation_cases WHERE platform = ? AND user_id = ? "
            f"AND status IN ({placeholders}) ORDER BY seq DESC LIMIT 1",
            (platform, user_id, *COOPERATION_OPEN_CODES),
        ).fetchone()
        return dict(row) if row else None

    def _sync_cooperation_summary_locked(
        self, platform: str, user_id: str
    ) -> dict[str, Any]:
        self._ensure_creator_stub(platform, user_id)
        latest = self._latest_cooperation_case_locked(platform, user_id)
        count_row = self._conn.execute(
            "SELECT COUNT(*) AS n, MAX(completed_at) AS completed "
            "FROM cooperation_cases WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        count = int(count_row["n"] or 0) if count_row else 0
        completed = count_row["completed"] if count_row else None
        if latest:
            self._conn.execute(
                "UPDATE creators SET cooperation_status = ?, cooperation_updated_at = ?, "
                "cooperation_count = ?, cooperation_last_completed_at = ?, "
                "cooperation_case_id = ? WHERE platform = ? AND user_id = ?",
                (
                    latest["status"],
                    latest["updated_at"],
                    count,
                    completed,
                    latest["id"],
                    platform,
                    user_id,
                ),
            )
        else:
            self._conn.execute(
                "UPDATE creators SET cooperation_status = NULL, cooperation_updated_at = NULL, "
                "cooperation_count = 0, cooperation_last_completed_at = NULL, "
                "cooperation_case_id = NULL WHERE platform = ? AND user_id = ?",
                (platform, user_id),
            )
        return {
            "cooperation_status": latest["status"] if latest else "",
            "cooperation_updated_at": latest["updated_at"] if latest else "",
            "cooperation_count": count,
            "cooperation_last_completed_at": completed or "",
            "cooperation_case_id": latest["id"] if latest else None,
        }

    def _add_cooperation_event_locked(
        self,
        case_id: int,
        event_type: str,
        *,
        status_from: str | None = None,
        status_to: str | None = None,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_user_id: int | None = None,
        now: str | None = None,
    ) -> int:
        stamp = now or utc_now()
        cur = self._conn.execute(
            "INSERT INTO cooperation_events "
            "(case_id, event_type, status_from, status_to, note, metadata, actor_user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id,
                event_type,
                status_from,
                status_to,
                note,
                json.dumps(metadata or {}, ensure_ascii=False),
                actor_user_id,
                stamp,
            ),
        )
        self._conn.execute(
            "UPDATE cooperation_cases SET updated_at = ? WHERE id = ?",
            (stamp, case_id),
        )
        return int(cur.lastrowid)

    def _claim_creator_locked(
        self, platform: str, user_id: str, claim_user_id: int | None, now: str
    ) -> bool:
        if claim_user_id is None:
            return False
        cur = self._conn.execute(
            "UPDATE creators SET follow_up_user_id = ?, followed_at = ? "
            "WHERE platform = ? AND user_id = ? AND follow_up_user_id IS NULL",
            (int(claim_user_id), now, platform, user_id),
        )
        return cur.rowcount > 0

    def _insert_cooperation_case_locked(
        self,
        platform: str,
        user_id: str,
        status: str,
        *,
        note: str | None = None,
        created_by: int | None = None,
        allow_terminal: bool = False,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_creator_stub(platform, user_id)
        code = normalize_cooperation_status(status, default="")
        if code not in COOPERATION_STATUS_CODES:
            raise ValueError("无效的合作状态")
        if not allow_terminal and code not in COOPERATION_OPEN_CODES:
            raise ValueError("新合作必须从未联系、已联系、洽谈中或合作中开始")
        if self._open_cooperation_case_locked(platform, user_id):
            raise ValueError("该达人已有进行中的合作")
        self._conn.execute(
            "UPDATE creators SET cooperation_reset_at = NULL "
            "WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        )
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM cooperation_cases "
            "WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        seq = int(seq_row["next_seq"] or 1)
        now = occurred_at or utc_now()
        completed_at = now if code == "completed" else None
        closed_at = now if code in ("declined", "cancelled") else None
        cur = self._conn.execute(
            "INSERT INTO cooperation_cases "
            "(platform, user_id, seq, status, summary, started_at, status_updated_at, "
            "completed_at, closed_at, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)",
            (
                platform,
                user_id,
                seq,
                code,
                now,
                now,
                completed_at,
                closed_at,
                created_by,
                now,
                now,
            ),
        )
        case_id = int(cur.lastrowid)
        self._add_cooperation_event_locked(
            case_id,
            "created",
            status_to=code,
            note=note or "开始合作跟进",
            metadata={"seq": seq, "auto": created_by is None},
            actor_user_id=created_by,
            now=now,
        )
        self._sync_cooperation_summary_locked(platform, user_id)
        return {
            "case_id": case_id,
            "seq": seq,
            "status": code,
            "created": True,
        }

    def _ensure_cooperation_enrolled_locked(
        self, platform: str, user_id: str
    ) -> dict[str, Any]:
        self._ensure_creator_stub(platform, user_id)
        creator = self._conn.execute(
            "SELECT cooperation_reset_at FROM creators "
            "WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        if creator and creator["cooperation_reset_at"]:
            summary = self._cooperation_summary_row_locked(platform, user_id)
            return {
                "enrolled": False,
                "previous_status": summary["cooperation_status"] if summary else "",
                "status": summary["cooperation_status"] if summary else "",
                "cooperation_updated_at": "",
                "case_id": summary["cooperation_case_id"] if summary else None,
            }
        existing = self._conn.execute(
            "SELECT COUNT(*) AS n FROM cooperation_cases WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        if int(existing["n"] or 0) > 0:
            summary = self._cooperation_summary_row_locked(platform, user_id)
            return {
                "enrolled": False,
                "previous_status": summary["cooperation_status"] if summary else "",
                "status": summary["cooperation_status"] if summary else "",
                "cooperation_updated_at": "",
                "case_id": summary["cooperation_case_id"] if summary else None,
            }
        created = self._insert_cooperation_case_locked(
            platform,
            user_id,
            DEFAULT_COOPERATION_STATUS,
            note="采用后自动进入合作跟进",
        )
        return {
            "enrolled": True,
            "previous_status": "",
            "status": created["status"],
            "cooperation_updated_at": utc_now(),
            "case_id": created["case_id"],
        }

    def _cooperation_case_locked(self, case_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM cooperation_cases WHERE id = ?", (int(case_id),)
        ).fetchone()
        return dict(row) if row else None

    def _update_cooperation_case_status_locked(
        self,
        case_id: int,
        status: str,
        *,
        note: str | None = None,
        expected_updated_at: str | None = None,
        actor_user_id: int | None = None,
        claim_user_id: int | None = None,
        allow_terminal_correction: bool = False,
    ) -> dict[str, Any]:
        case = self._cooperation_case_locked(case_id)
        if not case:
            raise ValueError("合作记录不存在")
        code = normalize_cooperation_status(status, default="")
        if code not in COOPERATION_STATUS_CODES:
            raise ValueError("无效的合作状态")
        if expected_updated_at is not None and case["updated_at"] != expected_updated_at:
            raise ValueError("合作记录已被其他人更新，请刷新后重试")
        current = case["status"]
        if current in COOPERATION_TERMINAL_CODES and code != current:
            if not allow_terminal_correction:
                raise ValueError("已结束的合作不能直接修改状态，请开启新一轮合作")
        placeholders = ",".join("?" * len(COOPERATION_OPEN_CODES))
        other_open = self._conn.execute(
            f"SELECT id FROM cooperation_cases WHERE platform = ? AND user_id = ? "
            f"AND status IN ({placeholders}) AND id != ? LIMIT 1",
            (case["platform"], case["user_id"], *COOPERATION_OPEN_CODES, int(case_id)),
        ).fetchone()
        if code in COOPERATION_OPEN_CODES and other_open:
            raise ValueError("该达人已有其他进行中的合作")
        now = utc_now()
        if current == code:
            if note and str(note).strip():
                self._add_cooperation_event_locked(
                    case_id,
                    "note_added",
                    note=str(note).strip(),
                    actor_user_id=actor_user_id,
                    now=now,
                )
                self._sync_cooperation_summary_locked(
                    case["platform"], case["user_id"]
                )
                return {
                    "changed": False,
                    "note_added": True,
                    "previous_status": current,
                    "status": code,
                    "cooperation_updated_at": now,
                    "case_id": int(case_id),
                    "claimed": False,
                }
            return {
                "changed": False,
                "note_added": False,
                "previous_status": current,
                "status": code,
                "cooperation_updated_at": case["updated_at"],
                "case_id": int(case_id),
                "claimed": False,
            }
        completed_at = case["completed_at"]
        closed_at = case["closed_at"]
        if code == "completed":
            completed_at = now
            closed_at = None
        elif code in ("declined", "cancelled"):
            closed_at = now
            completed_at = None
        elif allow_terminal_correction:
            completed_at = None
            closed_at = None
        self._conn.execute(
            "UPDATE cooperation_cases SET status = ?, status_updated_at = ?, "
            "completed_at = ?, closed_at = ?, updated_at = ? WHERE id = ?",
            (code, now, completed_at, closed_at, now, int(case_id)),
        )
        self._add_cooperation_event_locked(
            case_id,
            "status_corrected" if allow_terminal_correction else "status_changed",
            status_from=current,
            status_to=code,
            note=str(note).strip() if note and str(note).strip() else None,
            actor_user_id=actor_user_id,
            now=now,
        )
        claimed = self._claim_creator_locked(
            case["platform"], case["user_id"], claim_user_id, now
        )
        self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
        return {
            "changed": True,
            "note_added": bool(note and str(note).strip()),
            "previous_status": current,
            "status": code,
            "cooperation_updated_at": now,
            "case_id": int(case_id),
            "claimed": claimed,
        }

    def _cooperation_eligible(self, platform: str, user_id: str) -> bool:
        if self._open_cooperation_case_locked(platform, user_id):
            return True
        return bool(
            self._conn.execute(
                "SELECT 1 FROM creator_rounds "
                "WHERE platform = ? AND user_id = ? AND status = 'adopted' LIMIT 1",
                (platform, user_id),
            ).fetchone()
        )

    def _require_cooperation_eligible(self, platform: str, user_id: str) -> None:
        if not self._cooperation_eligible(platform, user_id):
            raise ValueError("只有已采用达人可写合作状态")

    def _update_cooperation_locked(
        self,
        platform: str,
        user_id: str,
        status: str | None,
        expected_status: str | None = None,
        expected_updated_at: str | None = None,
        claim_user_id: int | None = None,
        allow_adoption: bool = False,
    ) -> dict[str, Any]:
        self._ensure_creator_stub(platform, user_id)
        if not self._creator_exists(platform, user_id):
            raise ValueError("达人不存在")
        if not allow_adoption:
            self._require_cooperation_eligible(platform, user_id)
        code = normalize_cooperation_status(status, default="")
        if not code:
            raise ValueError("合作状态不能为空，请使用已取消结束合作")
        if code not in COOPERATION_STATUS_CODES:
            raise ValueError("无效的合作状态")
        summary = self._cooperation_summary_row_locked(platform, user_id)
        current = summary["cooperation_status"] if summary else ""
        current_updated = summary["cooperation_updated_at"] if summary else ""
        if expected_status is not None:
            expected = normalize_cooperation_status(expected_status, default="")
            if str(expected_status).strip() and expected not in COOPERATION_STATUS_CODES:
                raise ValueError("无效的期望合作状态")
            if (current or "") != expected:
                raise ValueError("合作状态已被其他人更新，请刷新后重试")
        if expected_updated_at is not None and (current_updated or "") != expected_updated_at:
            raise ValueError("合作状态已被其他人更新，请刷新后重试")
        open_case = self._open_cooperation_case_locked(platform, user_id)
        if open_case:
            result = self._update_cooperation_case_status_locked(
                int(open_case["id"]),
                code,
                expected_updated_at=expected_updated_at,
                actor_user_id=claim_user_id,
                claim_user_id=claim_user_id,
            )
            result["previous_status"] = current or ""
            result["status"] = code
            return result
        count = int(summary["cooperation_count"] or 0) if summary else 0
        if count > 0:
            raise ValueError("当前没有进行中的合作，请开启新一轮合作")
        if code not in COOPERATION_OPEN_CODES:
            raise ValueError("新合作必须从进行中状态开始")
        created = self._insert_cooperation_case_locked(
            platform, user_id, code, created_by=claim_user_id
        )
        now = utc_now()
        claimed = self._claim_creator_locked(platform, user_id, claim_user_id, now)
        return {
            "changed": True,
            "previous_status": "",
            "status": code,
            "cooperation_updated_at": now,
            "case_id": created["case_id"],
            "claimed": claimed,
        }

    def update_cooperation(
        self,
        platform: str,
        user_id: str,
        status: str | None,
        expected_status: str | None = None,
        expected_updated_at: str | None = None,
        claim_user_id: int | None = None,
    ) -> dict[str, Any]:
        with self._conn:
            return self._update_cooperation_locked(
                platform,
                user_id,
                status,
                expected_status=expected_status,
                expected_updated_at=expected_updated_at,
                claim_user_id=claim_user_id,
            )

    def bulk_update_cooperation(
        self,
        items: Iterable[Any],
        status: str | None,
        claim_user_id: int | None = None,
    ) -> dict[str, Any]:
        code = normalize_cooperation_status(status, default="")
        if code not in COOPERATION_OPEN_CODES:
            raise ValueError("批量只能更新进行中的合作状态")
        item_list = [
            item if isinstance(item, dict) else {"platform": item[0], "user_id": item[1]}
            for item in items
        ]
        changes: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        with self._conn:
            for item in item_list:
                platform = str(item["platform"])
                user_id = str(item["user_id"])
                summary = self._cooperation_summary_row_locked(platform, user_id)
                if summary is None:
                    conflicts.append({
                        "platform": platform,
                        "user_id": user_id,
                        "reason": "missing",
                        "cooperation_status": "",
                        "cooperation_updated_at": "",
                    })
                    continue
                open_case = self._open_cooperation_case_locked(platform, user_id)
                if not open_case and int(summary["cooperation_count"] or 0) > 0:
                    conflicts.append({
                        "platform": platform,
                        "user_id": user_id,
                        "reason": "no_open_case",
                        "cooperation_status": summary["cooperation_status"] or "",
                        "cooperation_updated_at": summary["cooperation_updated_at"] or "",
                    })
                    continue
                if not open_case and not self._cooperation_eligible(platform, user_id):
                    conflicts.append({
                        "platform": platform,
                        "user_id": user_id,
                        "reason": "not_adopted",
                        "cooperation_status": "",
                        "cooperation_updated_at": "",
                    })
                    continue
                if not open_case:
                    try:
                        created = self._insert_cooperation_case_locked(
                            platform, user_id, code, created_by=claim_user_id
                        )
                    except ValueError as exc:
                        conflicts.append({
                            "platform": platform,
                            "user_id": user_id,
                            "reason": "no_open_case",
                            "cooperation_status": "",
                            "cooperation_updated_at": "",
                            "message": str(exc),
                        })
                        continue
                    now = utc_now()
                    claimed = self._claim_creator_locked(
                        platform, user_id, claim_user_id, now
                    )
                    changes.append({
                        "platform": platform,
                        "user_id": user_id,
                        "previous_status": "",
                        "status": code,
                        "cooperation_updated_at": now,
                        "case_id": created["case_id"],
                        "claimed": claimed,
                    })
                    continue
                current = summary["cooperation_status"] or ""
                current_updated = summary["cooperation_updated_at"] or ""
                conflict_reason = ""
                if item.get("expected_status") is not None:
                    expected = normalize_cooperation_status(
                        item["expected_status"], default=""
                    )
                    if (
                        str(item["expected_status"]).strip()
                        and expected not in COOPERATION_STATUS_CODES
                    ):
                        raise ValueError("无效的期望合作状态")
                    if current != expected:
                        conflict_reason = "cooperation_status"
                if not conflict_reason and item.get("expected_updated_at") is not None:
                    if current_updated != item["expected_updated_at"]:
                        conflict_reason = "cooperation_updated_at"
                if conflict_reason:
                    conflicts.append({
                        "platform": platform,
                        "user_id": user_id,
                        "reason": conflict_reason,
                        "cooperation_status": current,
                        "cooperation_updated_at": current_updated,
                    })
                    continue
                result = self._update_cooperation_case_status_locked(
                    int(open_case["id"]),
                    code,
                    expected_updated_at=item.get("expected_updated_at"),
                    claim_user_id=claim_user_id,
                )
                if result["changed"]:
                    changes.append({
                        "platform": platform,
                        "user_id": user_id,
                        "previous_status": result["previous_status"],
                        "status": result["status"],
                        "cooperation_updated_at": result["cooperation_updated_at"],
                        "case_id": result["case_id"],
                        "claimed": result["claimed"],
                    })
        return {
            "changed": len(changes),
            "conflicts": conflicts,
            "changes": changes,
        }

    def create_cooperation_case(
        self,
        platform: str,
        user_id: str,
        status: str | None = None,
        *,
        note: str | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        with self._conn:
            self._ensure_creator_stub(platform, user_id)
            if not self._creator_exists(platform, user_id):
                raise ValueError("达人不存在")
            if not self._cooperation_eligible(platform, user_id):
                raise ValueError("只有已采用达人可开始合作")
            summary = self._cooperation_summary_row_locked(platform, user_id)
            count = int(summary["cooperation_count"] or 0) if summary else 0
            code = normalize_cooperation_status(status, default="")
            if not code:
                code = NEW_COOPERATION_STATUS if count > 0 else DEFAULT_COOPERATION_STATUS
            if code not in COOPERATION_OPEN_CODES:
                raise ValueError("新合作必须从进行中状态开始")
            created = self._insert_cooperation_case_locked(
                platform, user_id, code, note=note, created_by=created_by
            )
            claimed = self._claim_creator_locked(
                platform, user_id, created_by, utc_now()
            )
            created["claimed"] = claimed
            created["summary"] = self._sync_cooperation_summary_locked(
                platform, user_id
            )
            return created

    def update_cooperation_case_status(
        self,
        case_id: int,
        status: str,
        *,
        note: str | None = None,
        expected_updated_at: str | None = None,
        actor_user_id: int | None = None,
        claim_user_id: int | None = None,
    ) -> dict[str, Any]:
        with self._conn:
            result = self._update_cooperation_case_status_locked(
                case_id,
                status,
                note=note,
                expected_updated_at=expected_updated_at,
                actor_user_id=actor_user_id,
                claim_user_id=claim_user_id,
            )
            case = self._cooperation_case_locked(case_id)
            if case:
                result["summary"] = self._sync_cooperation_summary_locked(
                    case["platform"], case["user_id"]
                )
            return result

    def complete_cooperation_case(
        self,
        case_id: int,
        *,
        note: str | None = None,
        works: Iterable[dict[str, Any]] | None = None,
        expected_updated_at: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        with self._conn:
            case = self._cooperation_case_locked(case_id)
            if not case:
                raise ValueError("合作记录不存在")
            if case["status"] in COOPERATION_TERMINAL_CODES:
                raise ValueError("该合作已经结束")
            if expected_updated_at is not None and case["updated_at"] != expected_updated_at:
                raise ValueError("合作记录已被其他人更新，请刷新后重试")
            now = utc_now()
            work_ids: list[int] = []
            for work in works or []:
                cur = self._conn.execute(
                    "INSERT INTO cooperation_works "
                    "(case_id, platform, url, title, published_at, note, created_by, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(case_id),
                        str(work.get("platform") or ""),
                        str(work["url"]),
                        str(work.get("title") or ""),
                        work.get("published_at") or None,
                        str(work.get("note") or ""),
                        actor_user_id,
                        now,
                        now,
                    ),
                )
                work_id = int(cur.lastrowid)
                work_ids.append(work_id)
                self._add_cooperation_event_locked(
                    case_id,
                    "work_added",
                    status_from=case["status"],
                    status_to=case["status"],
                    metadata={"work_id": work_id, "url": work["url"]},
                    actor_user_id=actor_user_id,
                    now=now,
                )
            result = self._update_cooperation_case_status_locked(
                case_id,
                "completed",
                note=note,
                expected_updated_at=None,
                actor_user_id=actor_user_id,
                claim_user_id=actor_user_id,
            )
            result["work_ids"] = work_ids
            result["summary"] = self._sync_cooperation_summary_locked(
                case["platform"], case["user_id"]
            )
            return result

    def correct_cooperation_status(
        self,
        case_id: int,
        status: str,
        *,
        note: str,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        if not str(note or "").strip():
            raise ValueError("更正合作状态必须填写原因")
        with self._conn:
            result = self._update_cooperation_case_status_locked(
                case_id,
                status,
                note=note,
                actor_user_id=actor_user_id,
                allow_terminal_correction=True,
            )
            case = self._cooperation_case_locked(case_id)
            result["summary"] = self._sync_cooperation_summary_locked(
                case["platform"], case["user_id"]
            )
            return result

    def update_cooperation_case_summary(
        self,
        case_id: int,
        summary: str,
        *,
        expected_updated_at: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        with self._conn:
            case = self._cooperation_case_locked(case_id)
            if not case:
                raise ValueError("合作记录不存在")
            if expected_updated_at is not None and case["updated_at"] != expected_updated_at:
                raise ValueError("合作记录已被其他人更新，请刷新后重试")
            value = str(summary or "").strip()
            if value == str(case["summary"] or ""):
                return {"changed": False, "case_id": int(case_id)}
            now = utc_now()
            self._conn.execute(
                "UPDATE cooperation_cases SET summary = ?, updated_at = ? WHERE id = ?",
                (value, now, int(case_id)),
            )
            self._add_cooperation_event_locked(
                case_id,
                "summary_updated",
                note=value or "清空合作摘要",
                metadata={"previous": case["summary"] or "", "summary": value},
                actor_user_id=actor_user_id,
                now=now,
            )
            self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {"changed": True, "case_id": int(case_id), "summary": value}

    def add_cooperation_note(
        self,
        case_id: int,
        note: str,
        *,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        value = str(note or "").strip()
        if not value:
            raise ValueError("备注不能为空")
        with self._conn:
            case = self._cooperation_case_locked(case_id)
            if not case:
                raise ValueError("合作记录不存在")
            now = utc_now()
            event_id = self._add_cooperation_event_locked(
                case_id,
                "note_added",
                note=value,
                actor_user_id=actor_user_id,
                now=now,
            )
            self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {"event_id": event_id, "case_id": int(case_id)}

    def cooperation_cases(
        self, platform: str, user_id: str
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT cc.*,
                   (SELECT COUNT(*) FROM cooperation_events ce WHERE ce.case_id = cc.id)
                       AS event_count,
                   (SELECT COUNT(*) FROM cooperation_attachments ca
                    WHERE ca.case_id = cc.id AND ca.deleted_at IS NULL)
                       AS attachment_count,
                   (SELECT COUNT(*) FROM cooperation_works cw
                    WHERE cw.case_id = cc.id AND cw.deleted_at IS NULL)
                       AS work_count
            FROM cooperation_cases cc
            WHERE cc.platform = ? AND cc.user_id = ?
            ORDER BY cc.seq DESC
            """,
            (platform, user_id),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["active"] = item["status"] in COOPERATION_OPEN_CODES
            result.append(item)
        return result

    def cooperation_summary(self, platform: str, user_id: str) -> dict[str, Any]:
        row = self._cooperation_summary_row_locked(platform, user_id)
        if not row:
            return {
                "cooperation_status": "",
                "cooperation_updated_at": "",
                "cooperation_count": 0,
                "cooperation_last_completed_at": "",
                "cooperation_case_id": None,
            }
        return {
            "cooperation_status": row["cooperation_status"] or "",
            "cooperation_updated_at": row["cooperation_updated_at"] or "",
            "cooperation_count": int(row["cooperation_count"] or 0),
            "cooperation_last_completed_at": row["cooperation_last_completed_at"] or "",
            "cooperation_case_id": row["cooperation_case_id"],
        }

    def cooperation_case_detail(self, case_id: int) -> dict[str, Any] | None:
        case = self._cooperation_case_locked(case_id)
        if not case:
            return None
        events = [
            dict(row)
            for row in self._conn.execute(
                """
                SELECT e.*, COALESCE(u.username, '') AS actor_username
                FROM cooperation_events e
                LEFT JOIN users u ON u.id = e.actor_user_id
                WHERE e.case_id = ? ORDER BY e.id ASC
                """,
                (int(case_id),),
            ).fetchall()
        ]
        for event in events:
            try:
                event["metadata"] = json.loads(event.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                event["metadata"] = {}
        attachments = [
            dict(row)
            for row in self._conn.execute(
                """
                SELECT a.*, COALESCE(u.username, '') AS uploaded_by_username
                FROM cooperation_attachments a
                LEFT JOIN users u ON u.id = a.uploaded_by
                WHERE a.case_id = ? AND a.deleted_at IS NULL
                ORDER BY a.id DESC
                """,
                (int(case_id),),
            ).fetchall()
        ]
        works = [
            dict(row)
            for row in self._conn.execute(
                """
                SELECT w.*, COALESCE(u.username, '') AS created_by_username
                FROM cooperation_works w
                LEFT JOIN users u ON u.id = w.created_by
                WHERE w.case_id = ? AND w.deleted_at IS NULL
                ORDER BY w.id ASC
                """,
                (int(case_id),),
            ).fetchall()
        ]
        return {
            "case": case,
            "events": events,
            "attachments": attachments,
            "works": works,
            "summary": self.cooperation_summary(
                case["platform"], case["user_id"]
            ),
        }

    def add_cooperation_attachment(
        self,
        case_id: int,
        *,
        original_name: str,
        storage_key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        category: str,
        description: str,
        uploaded_by: int | None,
    ) -> dict[str, Any]:
        with self._conn:
            case = self._cooperation_case_locked(case_id)
            if not case:
                raise ValueError("合作记录不存在")
            now = utc_now()
            event_id = self._add_cooperation_event_locked(
                case_id,
                "attachment_added",
                note=description or None,
                metadata={
                    "name": original_name,
                    "category": category,
                    "size": int(size_bytes),
                    "sha256": sha256,
                },
                actor_user_id=uploaded_by,
                now=now,
            )
            cur = self._conn.execute(
                "INSERT INTO cooperation_attachments "
                "(case_id, event_id, original_name, storage_key, content_type, "
                "size_bytes, sha256, category, description, uploaded_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(case_id),
                    event_id,
                    original_name,
                    storage_key,
                    content_type,
                    int(size_bytes),
                    sha256,
                    category,
                    description,
                    uploaded_by,
                    now,
                ),
            )
            self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {
                "id": int(cur.lastrowid),
                "case_id": int(case_id),
                "event_id": event_id,
                "original_name": original_name,
                "storage_key": storage_key,
                "content_type": content_type,
                "size_bytes": int(size_bytes),
                "sha256": sha256,
                "category": category,
                "description": description,
                "uploaded_by": uploaded_by,
                "created_at": now,
            }

    def get_cooperation_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM cooperation_attachments WHERE id = ?",
            (int(attachment_id),),
        ).fetchone()
        return dict(row) if row else None

    def delete_cooperation_attachment(
        self, attachment_id: int, *, actor_user_id: int | None
    ) -> dict[str, Any]:
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM cooperation_attachments WHERE id = ?",
                (int(attachment_id),),
            ).fetchone()
            if not row:
                raise ValueError("附件不存在")
            if row["deleted_at"]:
                return {"changed": False, "case_id": int(row["case_id"])}
            now = utc_now()
            self._conn.execute(
                "UPDATE cooperation_attachments SET deleted_at = ?, deleted_by = ? "
                "WHERE id = ?",
                (now, actor_user_id, int(attachment_id)),
            )
            case = self._cooperation_case_locked(int(row["case_id"]))
            self._add_cooperation_event_locked(
                int(row["case_id"]),
                "attachment_removed",
                metadata={"attachment_id": int(attachment_id), "name": row["original_name"]},
                actor_user_id=actor_user_id,
                now=now,
            )
            if case:
                self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {"changed": True, "case_id": int(row["case_id"])}

    def add_cooperation_work(
        self,
        case_id: int,
        work: dict[str, Any],
        *,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        with self._conn:
            case = self._cooperation_case_locked(case_id)
            if not case:
                raise ValueError("合作记录不存在")
            now = utc_now()
            cur = self._conn.execute(
                "INSERT INTO cooperation_works "
                "(case_id, platform, url, title, published_at, note, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(case_id),
                    str(work.get("platform") or ""),
                    str(work["url"]),
                    str(work.get("title") or ""),
                    work.get("published_at") or None,
                    str(work.get("note") or ""),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            work_id = int(cur.lastrowid)
            self._add_cooperation_event_locked(
                case_id,
                "work_added",
                metadata={"work_id": work_id, "url": work["url"]},
                actor_user_id=actor_user_id,
                now=now,
            )
            self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {"id": work_id, "case_id": int(case_id), "created_at": now}

    def get_cooperation_work(self, work_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM cooperation_works WHERE id = ? AND deleted_at IS NULL",
            (int(work_id),),
        ).fetchone()
        return dict(row) if row else None

    def update_cooperation_work(
        self,
        work_id: int,
        work: dict[str, Any],
        *,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM cooperation_works WHERE id = ? AND deleted_at IS NULL",
                (int(work_id),),
            ).fetchone()
            if not row:
                raise ValueError("作品不存在")
            case = self._cooperation_case_locked(int(row["case_id"]))
            if not case:
                raise ValueError("合作记录不存在")
            now = utc_now()
            self._conn.execute(
                "UPDATE cooperation_works SET platform = ?, url = ?, title = ?, "
                "published_at = ?, note = ?, updated_at = ? WHERE id = ?",
                (
                    str(work.get("platform") or ""),
                    str(work["url"]),
                    str(work.get("title") or ""),
                    work.get("published_at") or None,
                    str(work.get("note") or ""),
                    now,
                    int(work_id),
                ),
            )
            self._add_cooperation_event_locked(
                int(row["case_id"]),
                "work_updated",
                metadata={
                    "work_id": int(work_id),
                    "before": {
                        "url": row["url"],
                        "title": row["title"],
                        "published_at": row["published_at"],
                    },
                    "after": {
                        "url": str(work["url"]),
                        "title": str(work.get("title") or ""),
                        "published_at": work.get("published_at") or None,
                    },
                },
                actor_user_id=actor_user_id,
                now=now,
            )
            self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {"id": int(work_id), "case_id": int(row["case_id"]), "updated_at": now}

    def delete_cooperation_work(
        self, work_id: int, *, actor_user_id: int | None
    ) -> dict[str, Any]:
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM cooperation_works WHERE id = ? AND deleted_at IS NULL",
                (int(work_id),),
            ).fetchone()
            if not row:
                return {"changed": False}
            now = utc_now()
            self._conn.execute(
                "UPDATE cooperation_works SET deleted_at = ?, deleted_by = ? WHERE id = ?",
                (now, actor_user_id, int(work_id)),
            )
            case = self._cooperation_case_locked(int(row["case_id"]))
            self._add_cooperation_event_locked(
                int(row["case_id"]),
                "work_removed",
                metadata={"work_id": int(work_id), "url": row["url"]},
                actor_user_id=actor_user_id,
                now=now,
            )
            if case:
                self._sync_cooperation_summary_locked(case["platform"], case["user_id"])
            return {"changed": True, "case_id": int(row["case_id"])}

    def delete_cooperation_case(self, case_id: int) -> dict[str, Any]:
        with self._conn:
            case = self._cooperation_case_locked(case_id)
            if not case:
                raise ValueError("合作记录不存在")
            case_id = int(case_id)
            counts = self._conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM cooperation_events WHERE case_id = ?) AS events,
                    (SELECT COUNT(*) FROM cooperation_works WHERE case_id = ?) AS works,
                    (SELECT COUNT(*) FROM cooperation_works
                     WHERE case_id = ? AND deleted_at IS NULL) AS active_works,
                    (SELECT COUNT(*) FROM cooperation_attachments WHERE case_id = ?) AS attachments,
                    (SELECT COUNT(*) FROM cooperation_attachments
                     WHERE case_id = ? AND deleted_at IS NULL) AS active_attachments
                """,
                (case_id, case_id, case_id, case_id, case_id),
            ).fetchone()
            attachment_rows = self._conn.execute(
                "SELECT storage_key FROM cooperation_attachments WHERE case_id = ?",
                (case_id,),
            ).fetchall()
            self._conn.execute(
                "DELETE FROM cooperation_attachments WHERE case_id = ?", (case_id,)
            )
            self._conn.execute(
                "DELETE FROM cooperation_works WHERE case_id = ?", (case_id,)
            )
            self._conn.execute(
                "DELETE FROM cooperation_events WHERE case_id = ?", (case_id,)
            )
            self._conn.execute(
                "DELETE FROM cooperation_cases WHERE id = ?", (case_id,)
            )
            remaining = self._conn.execute(
                "SELECT COUNT(*) AS n FROM cooperation_cases "
                "WHERE platform = ? AND user_id = ?",
                (case["platform"], case["user_id"]),
            ).fetchone()
            reset = int(remaining["n"] or 0) == 0
            if reset:
                self._conn.execute(
                    "UPDATE creators SET cooperation_reset_at = ? "
                    "WHERE platform = ? AND user_id = ?",
                    (utc_now(), case["platform"], case["user_id"]),
                )
            summary = self._sync_cooperation_summary_locked(
                case["platform"], case["user_id"]
            )
        return {
            "case_id": case_id,
            "platform": case["platform"],
            "user_id": case["user_id"],
            "seq": int(case["seq"]),
            "status": case["status"],
            "reset": reset,
            "storage_keys": [row["storage_key"] for row in attachment_rows],
            "impact": {
                "events": int(counts["events"] or 0),
                "works": int(counts["works"] or 0),
                "active_works": int(counts["active_works"] or 0),
                "attachments": int(counts["attachments"] or 0),
                "active_attachments": int(counts["active_attachments"] or 0),
            },
            "summary": summary,
        }

    def cooperation_history(
        self, platform: str, user_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        case = self._latest_cooperation_case_locked(platform, user_id)
        if not case:
            return []
        rows = self._conn.execute(
            """
            SELECT e.*, COALESCE(u.username, '') AS actor_username
            FROM cooperation_events e
            LEFT JOIN users u ON u.id = e.actor_user_id
            WHERE e.case_id = ? ORDER BY e.id DESC LIMIT ?
            """,
            (int(case["id"]), max(1, min(100, int(limit)))),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            result.append({
                "id": int(row["id"]),
                "case_id": int(row["case_id"]),
                "ts": row["created_at"],
                "from_status": str(row["status_from"] or ""),
                "to_status": str(row["status_to"] or ""),
                "event_type": str(row["event_type"] or ""),
                "note": str(row["note"] or ""),
                "auto": bool(metadata.get("auto")),
                "batch": False,
                "actor_username": row["actor_username"] or "",
            })
        return result

    def creator_is_fresh(self, platform: str, user_id: str, cache_days: int) -> bool:
        row = self._conn.execute(
            "SELECT fetched_at, fans, posted, liked FROM creators WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        if not row or not row["fetched_at"]:
            return False
        if any(row[field] is None for field in ("fans", "posted", "liked")):
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

    def _effective_run_id(self, round_id: int, run_id: int | None = None) -> int | None:
        if run_id is not None:
            return int(run_id)
        row = self._conn.execute(
            "SELECT latest_run_id FROM rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row and row["latest_run_id"] is not None:
            return int(row["latest_run_id"])
        return None

    def round_author_stats(
        self, round_id: int, run_id: int | None = None
    ) -> list[dict[str, Any]]:
        conditions = [
            "nh.round_id = ?",
            "n.author_id IS NOT NULL",
            "n.author_id != ''",
        ]
        params: list[Any] = [round_id]
        effective_run = self._effective_run_id(round_id, run_id)
        if effective_run is not None:
            conditions.append("nh.last_run_id = ?")
            params.append(effective_run)
        rows = self._conn.execute(
            f"""
            SELECT nh.keyword, nh.page, n.*, c.profile_url AS profile_url,
                   c.verify_info AS creator_verify_info
            FROM note_hits nh
            JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
            LEFT JOIN creators c ON c.platform = n.platform AND c.user_id = n.author_id
            WHERE {' AND '.join(conditions)}
            """,
            params,
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

    def refresh_creator_rounds(self, round_id: int, run_id: int | None = None) -> int:
        round_row = self._conn.execute(
            "SELECT category FROM rounds WHERE id = ?", (round_id,)
        ).fetchone()
        round_category = (round_row["category"] or "").strip() if round_row else ""
        stats = self.round_author_stats(round_id, run_id=run_id)
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
                     auto_tags, status, match_direction, tags_manual, remark, created_at, updated_at)
                    VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
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
                        now,
                    ),
                )
        self.rebuild_search_index(round_id)
        return len(stats)

    def import_creator_rows(self, round_id: int, items: list[dict[str, Any]]) -> int:
        """Bulk import creators/notes/round rows in one transaction (Excel imports)."""
        self.last_import_warnings = []
        now = utc_now()
        identities = {
            (
                str(item["round"].get("platform", "xhs")),
                str(item["round"]["user_id"]),
            )
            for item in items
        }
        previous_adopted: dict[tuple[str, str], bool] = {}
        for platform, user_id in identities:
            previous_adopted[(platform, user_id)] = bool(
                self._conn.execute(
                    "SELECT 1 FROM creator_rounds "
                    "WHERE platform = ? AND user_id = ? AND status = 'adopted' LIMIT 1",
                    (platform, user_id),
                ).fetchone()
            )
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
                     tier_label, account_id, cooperation_platform, raw_title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        now,
                    ),
                )
        explicit_cooperation: dict[tuple[str, str], tuple[str, str | None]] = {}
        for item in items:
            row = item["round"]
            identity = (str(row.get("platform", "xhs")), str(row["user_id"]))
            code = normalize_cooperation_status(item.get("cooperation_status"), default="")
            if code:
                imported_at = self._normalize_cooperation_timestamp(
                    item.get("cooperation_updated_at")
                )
                if identity in explicit_cooperation:
                    self.last_import_warnings.append(
                        f"{identity[0]}:{identity[1]} 多行合作状态，以最后一行为准"
                    )
                explicit_cooperation[identity] = (code, imported_at)
        with self._conn:
            for identity in identities:
                platform, user_id = identity
                if identity in explicit_cooperation:
                    if not self._cooperation_eligible(platform, user_id):
                        self.last_import_warnings.append(
                            f"{platform}:{user_id} 未采用，跳过导入的合作状态"
                        )
                        continue
                    status, imported_at = explicit_cooperation[identity]
                    existing_case = self._open_cooperation_case_locked(platform, user_id)
                    if existing_case:
                        try:
                            self._update_cooperation_case_status_locked(
                                int(existing_case["id"]),
                                status,
                                note="Excel 导入合作状态",
                            )
                        except ValueError as exc:
                            self.last_import_warnings.append(
                                f"{platform}:{user_id} 合作状态未更新：{exc}"
                            )
                    else:
                        latest_case = self._latest_cooperation_case_locked(
                            platform, user_id
                        )
                        already_current = bool(
                            latest_case
                            and latest_case["status"] == status
                            and (
                                not imported_at
                                or str(latest_case["updated_at"] or "") >= imported_at
                            )
                        )
                        if already_current:
                            self.last_import_warnings.append(
                                f"{platform}:{user_id} 合作状态与最近记录一致，跳过"
                            )
                        else:
                            self._insert_cooperation_case_locked(
                                platform,
                                user_id,
                                status,
                                note="Excel 导入合作状态",
                                allow_terminal=True,
                                occurred_at=imported_at,
                            )
                    if imported_at:
                        latest_case = self._latest_cooperation_case_locked(
                            platform, user_id
                        )
                        if latest_case:
                            completed_at = (
                                imported_at if status == "completed" else latest_case["completed_at"]
                            )
                            closed_at = (
                                imported_at
                                if status in ("declined", "cancelled")
                                else latest_case["closed_at"]
                            )
                            started_at = latest_case["started_at"] or imported_at
                            if started_at > imported_at:
                                started_at = imported_at
                            self._conn.execute(
                                "UPDATE cooperation_cases SET started_at = ?, "
                                "status_updated_at = ?, updated_at = ?, completed_at = ?, "
                                "closed_at = ? WHERE id = ?",
                                (
                                    started_at,
                                    imported_at,
                                    imported_at,
                                    completed_at,
                                    closed_at,
                                    int(latest_case["id"]),
                                ),
                            )
                            self._sync_cooperation_summary_locked(platform, user_id)
                    continue
                if previous_adopted.get(identity):
                    continue
                adopted = self._conn.execute(
                    "SELECT 1 FROM creator_rounds "
                    "WHERE platform = ? AND user_id = ? AND status = 'adopted' LIMIT 1",
                    (platform, user_id),
                ).fetchone()
                if adopted:
                    self._ensure_cooperation_enrolled_locked(platform, user_id)
        self.rebuild_search_index(round_id)
        return len(items)

    def update_review(
        self,
        round_id: int,
        platform: str,
        user_id: str,
        status: str | None = None,
        cooperation_status: str | None = None,
        match_direction: str | None = None,
        tags_manual: str | None = None,
        remark: str | None = None,
        expected_updated_at: str | None = None,
        expected_status: str | None = None,
        expected_cooperation_status: str | None = None,
        expected_cooperation_updated_at: str | None = None,
        claim_user_id: int | None = None,
    ) -> None:
        updates: dict[str, Any] = {}
        new_status: str | None = None
        if status is not None:
            new_status = normalize_status(status)
            updates["status"] = new_status
        if match_direction is not None:
            updates["match_direction"] = match_direction
        if tags_manual is not None:
            updates["tags_manual"] = tags_manual
        if remark is not None:
            updates["remark"] = remark
        updates = {key: value for key, value in updates.items() if key in REVIEW_FIELDS}
        if not updates and cooperation_status is None:
            return
        sets = ""
        values: list[Any] = []
        where = "round_id = ? AND platform = ? AND user_id = ?"
        if updates:
            updates["updated_at"] = utc_now()
            sets = ", ".join(f"{key} = ?" for key in updates)
            values = [*updates.values(), round_id, platform, user_id]
            if expected_updated_at is not None:
                where += " AND updated_at = ?"
                values.append(expected_updated_at)
            if expected_status is not None:
                expected = normalize_status(expected_status, default="")
                if not expected:
                    raise ValueError("无效的期望状态")
                where += " AND status = ?"
                values.append(expected)
        if cooperation_status is not None and not str(cooperation_status or "").strip():
            raise ValueError("合作状态不能为空，请使用已取消结束合作")
        with self._conn:
            if cooperation_status is not None:
                self._update_cooperation_locked(
                    platform,
                    user_id,
                    cooperation_status,
                    expected_status=expected_cooperation_status,
                    expected_updated_at=expected_cooperation_updated_at,
                    claim_user_id=claim_user_id,
                    allow_adoption=new_status == "adopted",
                )
            if updates:
                current_status: str | None = None
                if status is not None:
                    current = self._conn.execute(
                        "SELECT status FROM creator_rounds "
                        "WHERE round_id = ? AND platform = ? AND user_id = ?",
                        (round_id, platform, user_id),
                    ).fetchone()
                    current_status = current["status"] if current else None
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
                if status is not None:
                    if (
                        new_status == "adopted"
                        and current_status != "adopted"
                        and cooperation_status is None
                    ):
                        self._ensure_cooperation_enrolled_locked(platform, user_id)
        if tags_manual is not None:
            self._refresh_search_row(round_id, platform, user_id)

    def bulk_update_review(
        self,
        round_id: int,
        items: Iterable[Any],
        status: str | None = None,
        cooperation_status: str | None = None,
        match_direction: str | None = None,
        tags_manual: str | None = None,
        remark: str | None = None,
        claim_user_id: int | None = None,
        set_owner: bool = False,
        owner_user_id: int | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        new_status = normalize_status(status) if status is not None else None
        new_cooperation = (
            normalize_cooperation_status(cooperation_status, default="")
            if cooperation_status is not None
            else None
        )
        if cooperation_status is not None:
            if not new_cooperation:
                raise ValueError("合作状态不能为空，请使用已取消结束合作")
            if new_cooperation not in COOPERATION_OPEN_CODES:
                raise ValueError("批量只能更新进行中的合作状态")
        if status is not None:
            updates["status"] = new_status
        if match_direction is not None:
            updates["match_direction"] = match_direction
        if tags_manual is not None:
            updates["tags_manual"] = tags_manual
        if remark is not None:
            updates["remark"] = remark
        updates = {key: value for key, value in updates.items() if key in REVIEW_FIELDS}
        if not updates and cooperation_status is None and not set_owner:
            return {
                "changed": 0,
                "conflicts": [],
                "enrolled": [],
                "cooperation_changes": [],
            }
        if updates:
            updates["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values())
        item_list = [
            item if isinstance(item, dict) else {"platform": item[0], "user_id": item[1]}
            for item in items
        ]
        changed = 0
        conflicts: list[dict[str, Any]] = []
        enrolled: list[dict[str, str]] = []
        cooperation_changes: list[dict[str, Any]] = []
        with self._conn:
            for item in item_list:
                platform = str(item["platform"])
                user_id = str(item["user_id"])
                current = self._conn.execute(
                    "SELECT status, updated_at FROM creator_rounds "
                    "WHERE round_id = ? AND platform = ? AND user_id = ?",
                    (round_id, platform, user_id),
                ).fetchone()
                creator = self._conn.execute(
                    "SELECT cooperation_status, cooperation_updated_at, cooperation_count "
                    "FROM creators WHERE platform = ? AND user_id = ?",
                    (platform, user_id),
                ).fetchone()
                conflict_reason = ""
                if current is None:
                    conflict_reason = "missing"
                elif (
                    updates
                    and item.get("expected_updated_at") is not None
                    and current["updated_at"] != item["expected_updated_at"]
                ):
                    conflict_reason = "updated_at"
                elif (
                    updates
                    and item.get("expected_status") is not None
                    and current["status"] != normalize_status(item["expected_status"], default="")
                ):
                    conflict_reason = "status"
                if cooperation_status is not None and not conflict_reason:
                    current_cooperation = (creator["cooperation_status"] if creator else None) or ""
                    current_cooperation_updated = creator["cooperation_updated_at"] if creator else None
                    if (
                        item.get("expected_cooperation_status") is not None
                        and current_cooperation != normalize_cooperation_status(
                            item["expected_cooperation_status"], default=""
                        )
                    ):
                        conflict_reason = "cooperation_status"
                    elif (
                        item.get("expected_cooperation_updated_at") is not None
                        and (current_cooperation_updated or "")
                        != item["expected_cooperation_updated_at"]
                    ):
                        conflict_reason = "cooperation_updated_at"
                    if not conflict_reason:
                        open_case = self._open_cooperation_case_locked(platform, user_id)
                        if not open_case and int(
                            (creator["cooperation_count"] if creator else 0) or 0
                        ) > 0:
                            conflict_reason = "no_open_case"
                        elif (
                            not open_case
                            and new_status != "adopted"
                            and not self._cooperation_eligible(platform, user_id)
                        ):
                            conflict_reason = "not_adopted"
                if conflict_reason:
                    conflicts.append({
                        "platform": platform,
                        "user_id": user_id,
                        "reason": conflict_reason,
                        "status": current["status"] if current else None,
                        "updated_at": current["updated_at"] if current else None,
                        "cooperation_status": (creator["cooperation_status"] if creator else None) or "",
                        "cooperation_updated_at": creator["cooperation_updated_at"] if creator else None,
                    })
                    continue
                item_changed = False
                if updates:
                    cur = self._conn.execute(
                        f"UPDATE creator_rounds SET {sets} WHERE round_id = ? AND platform = ? AND user_id = ?",
                        [*values, round_id, platform, user_id],
                    )
                    item_changed = item_changed or cur.rowcount > 0
                    if status is not None:
                        previous_status = current["status"] if current else None
                        if (
                            new_status == "adopted"
                            and previous_status != "adopted"
                            and cooperation_status is None
                        ):
                            enrollment = self._ensure_cooperation_enrolled_locked(
                                platform, user_id
                            )
                            if enrollment.get("enrolled"):
                                enrolled.append({
                                    "platform": platform,
                                    "user_id": user_id,
                                })
                                item_changed = True
                if cooperation_status is not None:
                    previous_status = current_cooperation
                    change = self._update_cooperation_locked(
                        platform,
                        user_id,
                        new_cooperation,
                        expected_status=(
                            item.get("expected_cooperation_status")
                            if item.get("expected_cooperation_status") is not None
                            else None
                        ),
                        expected_updated_at=(
                            item.get("expected_cooperation_updated_at")
                            if item.get("expected_cooperation_updated_at") is not None
                            else None
                        ),
                        claim_user_id=claim_user_id,
                    )
                    if change.get("changed"):
                        cooperation_changes.append({
                            "platform": platform,
                            "user_id": user_id,
                            "previous_status": previous_status,
                            "status": change.get("status") or "",
                            "cooperation_updated_at": change.get("cooperation_updated_at") or "",
                            "case_id": change.get("case_id"),
                        })
                        item_changed = True
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
            for item in item_list:
                self._refresh_search_row(round_id, item["platform"], item["user_id"])
        return {
            "changed": changed,
            "conflicts": conflicts,
            "enrolled": enrolled,
            "cooperation_changes": cooperation_changes,
        }

    def creator_owner_options(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, username, role, can_collect, can_edit_leads, can_send_email FROM users "
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
                   cr.auto_tags, cr.status, cr.status AS round_status, cr.match_direction, cr.tags_manual, cr.remark,
                   cr.data_round, cr.tier_label, cr.account_id,
                   c.follow_up_user_id AS owner_user_id, c.followed_at AS claimed_at,
                   COALESCE(u.username, '') AS owner_username,
                   c.cooperation_status, c.cooperation_updated_at,
                   c.cooperation_count, c.cooperation_last_completed_at, c.cooperation_case_id,
                   cr.updated_at, cr.cooperation_platform, cr.raw_title,
                   n.title AS top_title, n.url AS top_url, n.liked AS top_liked,
                   r.name AS round_name, {ENRICHMENT_SQL} AS enrichment_status
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
        cooperation: Iterable[str] | None = None,
        cooperation_stale_days: int | None = None,
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
            status_sql = "cr.status"
            conditions.append(f"{status_sql} IN ({','.join('?' * len(status_values))})")
            params.extend(status_values)
        cooperation_values: list[str] = []
        for value in cooperation or []:
            if not value:
                continue
            if str(value) == "none":
                cooperation_values.append("none")
                continue
            code = normalize_cooperation_status(value, default="")
            if code in COOPERATION_STATUS_CODES:
                cooperation_values.append(code)
        if cooperation_values:
            parts: list[str] = []
            codes = [value for value in cooperation_values if value != "none"]
            if codes:
                parts.append(
                    f"c.cooperation_status IN ({','.join('?' * len(codes))})"
                )
                params.extend(codes)
            if "none" in cooperation_values:
                parts.append("COALESCE(c.cooperation_status, '') = ''")
            conditions.append("(" + " OR ".join(parts) + ")")
        if cooperation_stale_days is not None:
            days = max(1, min(365, int(cooperation_stale_days)))
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat(timespec="milliseconds")
            placeholders = ",".join("?" * len(COOPERATION_OPEN_CODES))
            conditions.append(
                f"c.cooperation_status IN ({placeholders}) "
                "AND c.cooperation_updated_at IS NOT NULL "
                "AND c.cooperation_updated_at < ?"
            )
            params.extend([*COOPERATION_OPEN_CODES, cutoff])
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
            conditions.append(f"({ENRICHMENT_SQL}) != 'complete'")
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
        cooperation: Iterable[str] | None = None,
        cooperation_stale_days: int | None = None,
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
            cooperation=cooperation,
            cooperation_stale_days=cooperation_stale_days,
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
            "ratio": (
                "CASE WHEN c.fans > 0 "
                f"THEN CAST(cr.sample_interactions AS REAL) / c.fans ELSE 0 END {direction}"
            ),
            "sample_average": (
                "CASE WHEN cr.sample_notes > 0 "
                f"THEN CAST(cr.sample_interactions AS REAL) / cr.sample_notes ELSE 0 END {direction}"
            ),
            "nickname": f"c.nickname {direction}",
            "data_round": f"({DATA_ROUND_SQL}) {direction}",
            "updated_at": f"cr.updated_at {direction}",
            "cooperation_updated_at": (
                f"(c.cooperation_updated_at IS NULL) ASC, "
                f"c.cooperation_updated_at {direction}"
            ),
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
        status_sql = "cr.status"
        status_rows = self._conn.execute(
            f"SELECT {status_sql} AS value, COUNT(*) AS count {from_sql} "
            f"WHERE {base_condition}{platform_condition} GROUP BY value ORDER BY count DESC",
            params,
        ).fetchall()
        cooperation_rows = self._conn.execute(
            "SELECT c.cooperation_status AS value, COUNT(*) AS count "
            f"{from_sql} WHERE {base_condition}{platform_condition} "
            "GROUP BY c.cooperation_status ORDER BY count DESC",
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
            "cooperation_statuses": [
                {
                    "value": "none" if row["value"] in (None, "") else str(row["value"]),
                    "count": int(row["count"]),
                }
                for row in cooperation_rows
            ],
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
        run_id = self._effective_run_id(round_id)
        conditions = ["nh.round_id = ?", "n.platform = ?", "n.author_id = ?"]
        params: list[Any] = [round_id, platform, user_id]
        if run_id is not None:
            current = self._conn.execute(
                "SELECT 1 FROM note_hits nh JOIN notes n "
                "ON n.platform = nh.platform AND n.note_id = nh.note_id "
                "WHERE nh.round_id = ? AND n.platform = ? AND n.author_id = ? "
                "AND nh.last_run_id = ? LIMIT 1",
                (round_id, platform, user_id, run_id),
            ).fetchone()
            if current:
                conditions.append("nh.last_run_id = ?")
                params.append(run_id)
        rows = self._conn.execute(
            f"""
            SELECT nh.keyword, n.*
            FROM note_hits nh
            JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
            WHERE {' AND '.join(conditions)}
            ORDER BY n.liked DESC
            """,
            params,
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
                "OR l.account_id LIKE ? ESCAPE '\\' OR l.platform_handle LIKE ? ESCAPE '\\' "
                "OR l.profile_url LIKE ? ESCAPE '\\' "
                "OR l.legacy_user_id LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 6)
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
            SELECT l.id, l.platform, l.nickname, l.account_id, l.platform_handle,
                   l.identity_status, l.identity_confidence, l.identity_provider,
                   l.identity_checked_at, l.identity_note,
                   l.profile_url, l.raw_title,
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
            "SELECT id, platform, account_id, platform_handle, profile_url FROM leads"
        ).fetchall()
        updates: list[tuple[str, str, str, int]] = []
        for row in rows:
            platform = str(row["platform"] or "xhs")
            account, profile = normalize_identity(
                platform, row["account_id"], row["profile_url"]
            )
            handle = normalize_account_id(platform, row["platform_handle"] or "")
            if (
                account != (row["account_id"] or "")
                or handle != (row["platform_handle"] or "")
                or profile != (row["profile_url"] or "")
            ):
                updates.append((account, handle, profile, int(row["id"])))
        if updates:
            with self._conn:
                self._conn.executemany(
                    "UPDATE leads SET account_id = ?, platform_handle = ?, profile_url = ? "
                    "WHERE id = ?",
                    updates,
                )
        return len(updates)

    def try_create_lead_unique_indexes(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        statements = {
            "account": (
                "CREATE UNIQUE INDEX uq_leads_identity_account "
                "ON leads (platform, account_id) "
                "WHERE account_id IS NOT NULL AND account_id <> '' "
                "AND status NOT IN ('duplicate', 'discarded')",
                "account_id",
            ),
            "handle": (
                "CREATE UNIQUE INDEX uq_leads_identity_handle "
                "ON leads (platform, platform_handle) "
                "WHERE platform_handle IS NOT NULL AND platform_handle <> '' "
                "AND status NOT IN ('duplicate', 'discarded')",
                "platform_handle",
            ),
            "profile": (
                "CREATE UNIQUE INDEX uq_leads_identity_profile "
                "ON leads (platform, profile_url) "
                "WHERE profile_url IS NOT NULL AND profile_url <> '' "
                "AND status NOT IN ('duplicate', 'discarded')",
                "profile_url",
            ),
        }
        for key, (sql, column) in statements.items():
            if column not in columns:
                result[key] = False
                continue
            name = f"uq_leads_identity_{key}"
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
        platform_handle: str = "",
        exclude_id: int | None = None,
    ) -> dict[str, Any] | None:
        account, profile = normalize_identity(platform, account_id, profile_url)
        handle = normalize_account_id(
            str(platform or "xhs").strip().lower(), platform_handle
        )
        if not account and not profile and not handle:
            return None
        conditions = ["platform = ?"]
        params: list[Any] = [str(platform or "xhs").strip().lower()]
        identity: list[str] = []
        if account:
            identity.append("account_id = ?")
            params.append(account)
        if handle:
            identity.append("platform_handle = ?")
            params.append(handle)
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
        platform_handle: str = "",
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
            platform_handle=platform_handle,
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
        platform_handle: str = "",
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
        handle = normalize_account_id(code, platform_handle)
        existing = self.find_lead_by_identity(
            code, account, profile, platform_handle=handle
        )
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
                            UPDATE leads SET nickname = ?, account_id = ?, platform_handle = ?,
                                profile_url = ?, raw_title = ?, source = ?, source_round_id = ?,
                                owner_user_id = ?, status = 'unverified', legacy_user_id = ?,
                                raw_json = ?, matched_platform = NULL, matched_user_id = NULL,
                                matched_round_id = NULL, matched_at = NULL,
                                match_confidence = NULL, conversion_lock = NULL,
                                conversion_locked_at = NULL, converted_platform = NULL,
                                converted_user_id = NULL, converted_at = NULL,
                                identity_status = 'unverified', identity_confidence = '',
                                identity_provider = '', identity_checked_at = NULL,
                                identity_note = '', updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                nickname or existing.get("nickname") or "",
                                account or existing.get("account_id") or "",
                                handle or existing.get("platform_handle") or "",
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
                    (platform, nickname, account_id, platform_handle, profile_url, raw_title, source,
                     source_round_id, owner_user_id, status, legacy_user_id, raw_json,
                     identity_status, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        nickname or "",
                        account,
                        handle,
                        profile,
                        raw_title or "",
                        normalize_lead_source(source),
                        source_round_id,
                        owner_user_id,
                        normalize_lead_status(status),
                        legacy_user_id or "",
                        raw_text,
                        "verified" if normalize_lead_status(status) == "converted" else "unverified",
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
            existing = self.find_lead_by_identity(
                code, account, profile, platform_handle=handle
            )
            if existing:
                return int(existing["id"]), False, False
            raise
        return lead_id, True, False

    def update_lead(self, lead_id: int, *, commit: bool = True, **fields: Any) -> None:
        allowed = {
            "platform",
            "nickname",
            "account_id",
            "platform_handle",
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
        identity_changed = False
        if {"platform", "account_id", "platform_handle", "profile_url"} & set(updates):
            platform = str(updates.get("platform", current.get("platform") or "xhs")).strip().lower()
            account, profile = normalize_identity(
                platform,
                updates.get("account_id", current.get("account_id") or ""),
                updates.get("profile_url", current.get("profile_url") or ""),
            )
            handle = normalize_account_id(
                platform,
                updates.get("platform_handle", current.get("platform_handle") or ""),
            )
            updates["account_id"] = account
            updates["profile_url"] = profile
            updates["platform_handle"] = handle
            identity_changed = (
                account != (current.get("account_id") or "")
                or handle != (current.get("platform_handle") or "")
                or profile != (current.get("profile_url") or "")
            )
            duplicate = self.find_lead_by_identity(
                platform,
                account,
                profile,
                platform_handle=handle,
                exclude_id=lead_id,
            )
            if duplicate:
                raise ValueError(f"该账号已存在线索 #{duplicate['id']}，请直接编辑原线索")
        if identity_changed:
            updates["identity_status"] = "unverified"
            updates["identity_confidence"] = ""
            updates["identity_provider"] = ""
            updates["identity_checked_at"] = None
            updates["identity_note"] = ""
            updates["matched_platform"] = None
            updates["matched_user_id"] = None
            updates["matched_round_id"] = None
            updates["matched_at"] = None
            updates["match_confidence"] = None
        if (
            "status" in updates
            and updates["status"] in ("unverified", "verifying")
            and normalize_lead_status(current.get("status")) in ("duplicate", "discarded")
        ):
            duplicate = self.find_lead_by_identity(
                str(current.get("platform") or "xhs"),
                current.get("account_id") or "",
                current.get("profile_url") or "",
                platform_handle=current.get("platform_handle") or "",
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

    def add_lead_identity_event(
        self,
        lead_id: int,
        event_type: str,
        status: str,
        *,
        old_user_id: str = "",
        new_user_id: str = "",
        old_handle: str = "",
        new_handle: str = "",
        provider: str = "",
        confidence: str = "",
        note: str = "",
        actor_id: int | None = None,
        commit: bool = True,
    ) -> None:
        with self._write(commit):
            self._conn.execute(
                """
                INSERT INTO lead_identity_events
                (lead_id, event_type, old_user_id, new_user_id, old_handle,
                 new_handle, provider, confidence, status, note, actor_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    event_type,
                    old_user_id or "",
                    new_user_id or "",
                    old_handle or "",
                    new_handle or "",
                    provider or "",
                    confidence or "",
                    status,
                    note or "",
                    actor_id,
                    utc_now(),
                ),
            )

    def lead_identity_events(
        self, lead_id: int, limit: int = 30
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM lead_identity_events WHERE lead_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (lead_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def apply_lead_verification(
        self,
        lead_id: int,
        *,
        user_id: str,
        handle: str,
        identity_status: str,
        confidence: str,
        provider: str,
        note: str = "",
        profile_url: str = "",
        event_type: str = "",
        actor_id: int | None = None,
        commit: bool = True,
    ) -> bool:
        if identity_status not in LEAD_IDENTITY_STATUSES:
            raise ValueError("无效的身份校验状态")
        now = utc_now()
        with self._write(commit):
            row = self._conn.execute(
                "SELECT platform, account_id, platform_handle, profile_url, status "
                "FROM leads WHERE id = ?",
                (lead_id,),
            ).fetchone()
            if not row:
                raise ValueError("线索不存在")
            if row["status"] == "converted":
                raise ValueError("已转达人的线索不可修改")
            platform = str(row["platform"] or "xhs")
            account = normalize_account_id(platform, user_id or "") or ""
            normalized_handle = normalize_account_id(platform, handle or "") or ""
            _, normalized_profile = normalize_identity(
                platform, "", profile_url or ""
            )
            old_user = str(row["account_id"] or "")
            old_handle = str(row["platform_handle"] or "")
            old_profile = str(row["profile_url"] or "")
            conflict_conditions: list[str] = []
            conflict_params: list[Any] = [platform]
            if account and account != old_user:
                conflict_conditions.append("account_id = ?")
                conflict_params.append(account)
            if normalized_handle and normalized_handle != old_handle:
                conflict_conditions.append("platform_handle = ?")
                conflict_params.append(normalized_handle)
            if normalized_profile and normalized_profile != old_profile:
                conflict_conditions.append("profile_url = ?")
                conflict_params.append(normalized_profile)
            if conflict_conditions:
                conflict = self._conn.execute(
                    f"""
                    SELECT id FROM leads
                    WHERE platform = ? AND ({' OR '.join(conflict_conditions)})
                      AND id <> ? AND status NOT IN ('duplicate', 'discarded')
                    LIMIT 1
                    """,
                    [*conflict_params, lead_id],
                ).fetchone()
                if conflict:
                    raise ValueError(
                        f"该账号已存在线索 #{conflict['id']}，无法写入校验结果"
                    )
            self._conn.execute(
                """
                UPDATE leads
                SET account_id = ?, platform_handle = ?, identity_status = ?,
                    identity_confidence = ?, identity_provider = ?, identity_checked_at = ?,
                    identity_note = ?, profile_url = COALESCE(NULLIF(?, ''), profile_url),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    account,
                    normalized_handle,
                    identity_status,
                    confidence or "",
                    provider or "",
                    now,
                    note or "",
                    normalized_profile,
                    now,
                    lead_id,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO lead_identity_events
                (lead_id, event_type, old_user_id, new_user_id, old_handle,
                 new_handle, provider, confidence, status, note, actor_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    event_type or f"verify_{identity_status}",
                    old_user,
                    account,
                    old_handle,
                    normalized_handle,
                    provider or "",
                    confidence or "",
                    identity_status,
                    note or "",
                    actor_id,
                    now,
                ),
            )
        return True

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
                confidence = str(match.get("confidence") or "matched")
                overwrite = (
                    bool(match.get("overwrite"))
                    and not row["matched_user_id"]
                    and row["status"] == "unverified"
                    and not row["converted_at"]
                )
                creator_nickname = str(match.get("nickname") or "").strip()
                creator_profile = str(match.get("profile_url") or "").strip()
                creator_raw = match.get("raw_json")
                _, resolved_handle = split_platform_identity(
                    platform,
                    match_account,
                    creator_profile,
                )
                creator_handle = normalize_account_id(
                    platform, str(match.get("handle") or "")
                )
                if str(user_id).startswith("import:"):
                    candidate_account = resolved_handle or match_account
                    candidate_handle = resolved_handle or creator_handle or match_account
                else:
                    candidate_account = user_id
                    candidate_handle = resolved_handle or creator_handle or ""
                current_account = str(row["account_id"] or "").strip()
                current_handle = str(row["platform_handle"] or "").strip()
                current_identity = str(row["identity_status"] or "unverified")
                current_confidence = str(row["identity_confidence"] or "")
                current_provider = str(row["identity_provider"] or "")
                provider_verified = (
                    current_identity == "verified"
                    and current_provider not in ("", "match")
                )
                # Local matching may only link; it must not silently replace an
                # identity that a provider or a human already verified.
                if provider_verified and current_account != candidate_account:
                    stats["skipped"] += 1
                    continue
                if (
                    confidence == "platform_handle"
                    and current_account
                    and current_account != candidate_account
                ):
                    stats["skipped"] += 1
                    continue
                strong = confidence == "account_id" or (
                    confidence == "platform_handle" and not current_account
                )
                account_id = current_account
                handle = current_handle
                identity_status = current_identity
                identity_confidence = current_confidence
                identity_provider = current_provider
                identity_checked_at = row["identity_checked_at"]
                identity_note = str(row["identity_note"] or "")
                if strong and not provider_verified:
                    account_id = candidate_account
                    handle = candidate_handle or current_handle
                    identity_status = "verified"
                    identity_confidence = confidence
                    identity_provider = "match"
                    identity_checked_at = now
                    identity_note = "本地达人匹配"
                elif not provider_verified:
                    if not account_id:
                        account_id = candidate_account
                    if not handle:
                        handle = candidate_handle
                nickname = row["nickname"] or ""
                raw_json = row["raw_json"] or ""
                if overwrite and creator_nickname:
                    nickname = creator_nickname
                if overwrite and isinstance(creator_raw, str) and creator_raw.strip():
                    raw_json = creator_raw
                changed = nickname != (row["nickname"] or "") or raw_json != (row["raw_json"] or "")
                current_profile = row["profile_url"] or ""
                if overwrite and creator_profile:
                    profile_url = creator_profile
                elif not current_profile:
                    profile_url = creator_profile
                else:
                    profile_url = current_profile
                if account_id != (row["account_id"] or ""):
                    changed = True
                if handle != (row["platform_handle"] or ""):
                    changed = True
                if profile_url != current_profile:
                    changed = True
                if identity_status != current_identity:
                    changed = True
                if identity_confidence != current_confidence:
                    changed = True
                if identity_provider != current_provider:
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
                if handle and handle != (row["platform_handle"] or ""):
                    conflict_conditions.append("platform_handle = ?")
                    conflict_params.append(handle)
                if profile_url and profile_url != (row["profile_url"] or ""):
                    conflict_conditions.append("profile_url = ?")
                    conflict_params.append(profile_url)
                if conflict_conditions:
                    conflict = self._conn.execute(
                        f"""
                        SELECT id FROM leads
                        WHERE platform = ? AND ({' OR '.join(conflict_conditions)})
                          AND id <> ? AND status NOT IN ('duplicate', 'discarded')
                        LIMIT 1
                        """,
                        [*conflict_params, lead_id],
                    ).fetchone()
                    if conflict:
                        stats["skipped"] += 1
                        continue
                cur = self._conn.execute(
                    """
                    UPDATE leads SET nickname = ?, account_id = ?, platform_handle = ?,
                        profile_url = ?, raw_json = ?, matched_platform = ?, matched_user_id = ?,
                        matched_round_id = ?, matched_at = ?, match_confidence = ?,
                        identity_status = ?, identity_confidence = ?,
                        identity_provider = ?, identity_checked_at = ?,
                        identity_note = ?, updated_at = ?
                    WHERE id = ? AND status IN ('unverified', 'verifying')
                    """,
                    (
                        nickname,
                        account_id,
                        handle,
                        profile_url,
                        raw_json,
                        platform,
                        user_id,
                        matched_round_id,
                        now,
                        match.get("confidence") or "",
                        identity_status,
                        identity_confidence,
                        identity_provider,
                        identity_checked_at,
                        identity_note,
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
        handle_seen: dict[tuple[str, str], int] = {}
        profile_seen: dict[tuple[str, str], int] = {}
        for index, row in enumerate(rows):
            platform = str(row.get("platform") or "xhs")
            account = str(row.get("account_id") or "")
            handle = str(row.get("platform_handle") or "")
            profile = str(row.get("profile_url") or "")
            if account:
                key = (platform, account)
                if key in account_seen:
                    union(index, account_seen[key])
                else:
                    account_seen[key] = index
            if handle:
                key = (platform, handle)
                if key in handle_seen:
                    union(index, handle_seen[key])
                else:
                    handle_seen[key] = index
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
                    for field in (
                        "nickname",
                        "account_id",
                        "platform_handle",
                        "profile_url",
                        "owner_user_id",
                    )
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
                for field in ("nickname", "account_id", "platform_handle", "profile_url", "raw_title", "raw_json"):
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
                verified_identity = next(
                    (
                        item
                        for item in [primary, *duplicates]
                        if item.get("identity_status") == "verified"
                    ),
                    None,
                )
                if verified_identity:
                    for field in (
                        "identity_status",
                        "identity_confidence",
                        "identity_provider",
                        "identity_checked_at",
                        "identity_note",
                    ):
                        merged[field] = verified_identity.get(field)
                matched_source = next(
                    (
                        item
                        for item in [primary, *duplicates]
                        if item.get("matched_user_id")
                    ),
                    None,
                )
                if matched_source:
                    for field in (
                        "matched_platform",
                        "matched_user_id",
                        "matched_round_id",
                        "matched_at",
                        "match_confidence",
                    ):
                        if not merged.get(field):
                            merged[field] = matched_source.get(field)
                if converted:
                    merged["status"] = "converted"
                    merged["converted_platform"] = converted.get("converted_platform")
                    merged["converted_user_id"] = converted.get("converted_user_id")
                    merged["converted_at"] = converted.get("converted_at")
                self._conn.execute(
                    """
                    UPDATE leads SET nickname = ?, account_id = ?, platform_handle = ?,
                        profile_url = ?, raw_title = ?, raw_json = ?,
                        owner_user_id = ?, source_round_id = ?, status = ?,
                        converted_platform = ?, converted_user_id = ?, converted_at = ?,
                        identity_status = ?, identity_confidence = ?, identity_provider = ?,
                        identity_checked_at = ?, identity_note = ?,
                        matched_platform = ?, matched_user_id = ?, matched_round_id = ?,
                        matched_at = ?, match_confidence = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        merged.get("nickname") or "",
                        merged.get("account_id") or "",
                        merged.get("platform_handle") or "",
                        merged.get("profile_url") or "",
                        merged.get("raw_title") or "",
                        merged.get("raw_json") or "",
                        merged.get("owner_user_id"),
                        merged.get("source_round_id"),
                        merged.get("status") or "unverified",
                        merged.get("converted_platform"),
                        merged.get("converted_user_id"),
                        merged.get("converted_at"),
                        merged.get("identity_status") or "unverified",
                        merged.get("identity_confidence") or "",
                        merged.get("identity_provider") or "",
                        merged.get("identity_checked_at"),
                        merged.get("identity_note") or "",
                        merged.get("matched_platform"),
                        merged.get("matched_user_id"),
                        merged.get("matched_round_id"),
                        merged.get("matched_at"),
                        merged.get("match_confidence"),
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
                        "UPDATE lead_identity_events SET lead_id = ? WHERE lead_id = ?",
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
            "handle_index": "uq_leads_identity_handle" in indexes,
            "profile_index": "uq_leads_identity_profile" in indexes,
            "duplicate_account_groups": duplicate_groups("account_id"),
            "duplicate_handle_groups": duplicate_groups("platform_handle"),
            "duplicate_profile_groups": duplicate_groups("profile_url"),
        }

    def lead_owner_options(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, username, role, can_collect, can_edit_leads, can_send_email FROM users "
            "WHERE disabled = 0 AND (role = 'admin' OR can_edit_leads = 1) "
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
        normalized_status = normalize_status(status)
        previous = self._conn.execute(
            "SELECT status FROM creator_rounds "
            "WHERE round_id = ? AND platform = ? AND user_id = ?",
            (round_id, platform, user_id),
        ).fetchone()
        previous_status = previous["status"] if previous else None
        with self._write(commit):
            cur = self._conn.execute(
                """
                INSERT INTO creator_rounds
                (round_id, platform, user_id, source_keywords, source_labels, sample_notes, sample_likes,
                 sample_collected, sample_comments, sample_interactions, top_note_id,
                 auto_tags, status, match_direction, tags_manual, remark, data_round,
                 tier_label, account_id, cooperation_platform, raw_title, owner_user_id,
                 claimed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, NULL, '', '', ?, NULL, ?, '', ?, ?, ?, ?, ?)
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
                    normalized_status,
                    data_round or "",
                    account_id or user_id,
                    raw_title or "",
                    owner_user_id,
                    now if owner_user_id is not None else None,
                    now,
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
            if normalized_status == "adopted" and previous_status != "adopted":
                stored = self._conn.execute(
                    "SELECT status FROM creator_rounds "
                    "WHERE round_id = ? AND platform = ? AND user_id = ?",
                    (round_id, platform, user_id),
                ).fetchone()
                if stored and stored["status"] == "adopted":
                    self._ensure_cooperation_enrolled_locked(platform, user_id)
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
        self,
        lead_id: int,
        platform: str,
        user_id: str,
        *,
        conversion_token: str | None = None,
        commit: bool = True,
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
                  AND (? IS NULL OR conversion_lock = ?)
                """,
                (platform, user_id, now, now, lead_id, conversion_token, conversion_token),
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
        run_id = self._effective_run_id(round_id, None)
        hit_filter = "AND last_run_id = ?" if run_id is not None else ""
        nh_filter = "AND nh.last_run_id = ?" if run_id is not None else ""
        params: list[Any] = [round_id, round_id, round_id]
        if run_id is not None:
            params.append(run_id)
        params.append(round_id)
        if run_id is not None:
            params.append(run_id)
        params.extend([round_id, round_id])
        row = self._conn.execute(
            f"""
            SELECT
                (SELECT COUNT(*) FROM creator_rounds WHERE round_id = ?) AS creators,
                (SELECT COUNT(*) FROM lead_rounds WHERE round_id = ?) AS leads,
                (SELECT COUNT(*) FROM note_hits WHERE round_id = ? {hit_filter}) AS note_hits,
                (SELECT COUNT(DISTINCT note_id) FROM (
                    SELECT n.note_id FROM note_hits nh
                    JOIN notes n ON n.platform = nh.platform AND n.note_id = nh.note_id
                    WHERE nh.round_id = ? {nh_filter}
                    UNION
                    SELECT top_note_id AS note_id FROM creator_rounds
                    WHERE round_id = ? AND top_note_id IS NOT NULL
                      AND top_note_id != ''
                )) AS notes,
                (SELECT COALESCE(SUM(cost), 0) FROM api_calls WHERE round_id = ?) AS cost
            """,
            params,
        ).fetchone()
        return dict(row)

    def round_metrics(self, round_id: int | None) -> dict[str, int]:
        where = "" if round_id is None else "WHERE cr.round_id = ?"
        params = () if round_id is None else (round_id,)
        row = self._conn.execute(
            f"""
            SELECT
                COUNT(*) AS creators,
                SUM(CASE WHEN {ENRICHMENT_SQL} = 'complete' THEN 1 ELSE 0 END) AS enriched,
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
                SUM(CASE WHEN {ENRICHMENT_SQL} = 'complete' THEN 1 ELSE 0 END) AS enriched,
                SUM(CASE WHEN cr.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN cr.status = 'adopted' THEN 1 ELSE 0 END) AS adopted
            FROM latest cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE cr.rn = 1
            """
        ).fetchone()
        return {key: int(row[key] or 0) for key in ("creators", "enriched", "pending", "adopted")}

    def dashboard_breakdown(
        self, round_id: int | None = None, stale_days: int = 7
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
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            """
            where = "cr.rn = 1"
            params: tuple[Any, ...] = ()
            status_sql = "cr.status"
        else:
            from_sql = """
            FROM creator_rounds cr
            LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            """
            where = "cr.round_id = ?"
            params = (round_id,)
            status_sql = "cr.status"
        rows = self._conn.execute(
            f"""
            SELECT {status_sql} AS status_value,
                   {TIER_SQL} AS label_value,
                   COUNT(*) AS n,
                   SUM(CASE WHEN {ENRICHMENT_SQL} = 'complete' THEN 1 ELSE 0 END) AS enriched
            {from_sql}
            WHERE {where}
            GROUP BY status_value, label_value
            """,
            params,
        ).fetchall()
        metrics = {"creators": 0, "enriched": 0, "pending": 0, "adopted": 0}
        status_counts: dict[str, int] = {}
        tier_counts = {label: 0 for label in TIER_LABELS}
        tier_counts["未补全"] = 0
        for row in rows:
            count = int(row["n"] or 0)
            metrics["creators"] += count
            metrics["enriched"] += int(row["enriched"] or 0)
            status = str(row["status_value"] or "")
            status_counts[status] = status_counts.get(status, 0) + count
            if status == "pending":
                metrics["pending"] += count
            elif status == "adopted":
                metrics["adopted"] += count
            label = str(row["label_value"] or "未补全")
            tier_counts[label] = tier_counts.get(label, 0) + count

        cooperation_rows = self._conn.execute(
            f"""
            SELECT c.cooperation_status AS cooperation_status_value,
                   COUNT(*) AS n
            {from_sql}
            WHERE {where}
            GROUP BY c.cooperation_status
            """,
            params,
        ).fetchall()
        cooperation_counts = {
            str(row["cooperation_status_value"] or "none"): int(row["n"] or 0)
            for row in cooperation_rows
        }
        days = max(1, min(365, int(stale_days)))
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat(timespec="milliseconds")
        placeholders = ",".join("?" * len(COOPERATION_OPEN_CODES))
        stale_row = self._conn.execute(
            f"""
            SELECT COUNT(*) AS n
            {from_sql}
            WHERE {where}
              AND c.cooperation_status IN ({placeholders})
              AND c.cooperation_updated_at IS NOT NULL
              AND c.cooperation_updated_at < ?
            """,
            [*params, *COOPERATION_OPEN_CODES, cutoff],
        ).fetchone()
        case_stats = self._conn.execute(
            f"""
            WITH scoped AS (
                SELECT DISTINCT cr.platform, cr.user_id
                {from_sql}
                WHERE {where}
            )
            SELECT COUNT(cc.id) AS total_cases,
                   COALESCE(SUM(CASE WHEN cc.status = 'completed' THEN 1 ELSE 0 END), 0)
                       AS completed_cases,
                   (
                       SELECT COUNT(*) FROM (
                           SELECT s.platform, s.user_id, COUNT(*) AS n
                           FROM cooperation_cases cc2
                           JOIN scoped s
                             ON s.platform = cc2.platform AND s.user_id = cc2.user_id
                           GROUP BY s.platform, s.user_id
                           HAVING n > 1
                       )
                   ) AS repeat_creators
            FROM cooperation_cases cc
            JOIN scoped s ON s.platform = cc.platform AND s.user_id = cc.user_id
            """,
            params,
        ).fetchone()
        cooperation_metrics = {
            "enrolled": sum(
                count
                for status, count in cooperation_counts.items()
                if status != "none"
            ),
            "open": sum(
                cooperation_counts.get(code, 0)
                for code in COOPERATION_OPEN_CODES
            ),
            "stale": int(stale_row["n"] or 0),
            "cooperating": cooperation_counts.get("cooperating", 0),
            "completed": cooperation_counts.get("completed", 0),
            "declined": cooperation_counts.get("declined", 0),
            "cancelled": cooperation_counts.get("cancelled", 0),
            "total_cases": int(case_stats["total_cases"] or 0) if case_stats else 0,
            "completed_cases": int(case_stats["completed_cases"] or 0) if case_stats else 0,
            "repeat_creators": int(case_stats["repeat_creators"] or 0) if case_stats else 0,
        }
        return {
            "metrics": metrics,
            "status_counts": status_counts,
            "tier_counts": tier_counts,
            "cooperation_counts": cooperation_counts,
            "cooperation_metrics": cooperation_metrics,
            "cooperation_stale_days": days,
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

    def activity_calendar(
        self,
        days: int = 365,
        tz_offset: int = 480,
        round_id: int | None = None,
    ) -> dict[str, Any]:
        days = max(30, min(730, int(days)))
        tz_offset = max(-720, min(840, int(tz_offset)))
        end = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
        start = end - timedelta(days=days - 1)
        modifier = f"{tz_offset:+d} minutes"
        cutoff = f"-{days + 5} days"
        collected_from = (
            "creator_rounds cr JOIN creators ON creators.platform = cr.platform "
            "AND creators.user_id = cr.user_id"
            if round_id is not None
            else "creators"
        )
        collected_timestamp = (
            "COALESCE(NULLIF(cr.created_at, ''), cr.updated_at, creators.created_at)"
            if round_id is not None
            else "creators.created_at"
        )
        collected_round = "AND cr.round_id = ?" if round_id is not None else ""
        collected_params: list[Any] = [modifier, cutoff]
        if round_id is not None:
            collected_params.append(int(round_id))
        collected_rows = self._conn.execute(
            f"""
            SELECT date(datetime(substr({collected_timestamp}, 1, 19), ?)) AS day,
                   COUNT(*) AS n
            FROM {collected_from}
            WHERE {collected_timestamp} IS NOT NULL AND {collected_timestamp} <> ''
              AND substr({collected_timestamp}, 1, 19) >= datetime('now', ?)
              {collected_round}
            GROUP BY day
            """,
            collected_params,
        ).fetchall()
        processed_round = ""
        processed_params: list[Any] = [modifier, cutoff]
        if round_id is not None:
            processed_round = (
                "AND ((action = 'creator.review_updated' AND target LIKE ?) "
                "OR (action = 'creator.review_bulk_updated' AND target = ?))"
            )
            processed_params.extend([f"{int(round_id)}:%", str(int(round_id))])
        processed_rows = self._conn.execute(
            f"""
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
              AND datetime(ts) > COALESCE(
                  (
                      SELECT datetime(MAX(ts)) FROM audit_log
                      WHERE action = 'data.review_reset'
                  ),
                  '0001-01-01'
              )
              {processed_round}
            GROUP BY day
            """,
            processed_params,
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
            f"""
            SELECT r.id AS round_id, r.name, r.platform, r.status, r.created_at, r.finished_at,
                   COUNT(cr.user_id) AS creators,
                   COALESCE(SUM(CASE WHEN {ENRICHMENT_SQL} = 'complete' THEN 1 ELSE 0 END), 0) AS enriched,
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
            if row["status"] == "running" and process_matches(
                row["pid"], row["round_id"], row["id"]
            ):
                continue
            if row["status"] == "pending" and pid_alive(row["pid"]):
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

    def _round_has_live_job_process(self, round_id: int) -> bool:
        row = self._conn.execute(
            "SELECT id, pid, status FROM jobs WHERE round_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (round_id,),
        ).fetchone()
        if not row or row["status"] != "cancelled" or not row["pid"]:
            return False
        return process_matches(row["pid"], round_id, row["id"])

    def claim_job(
        self,
        round_id: int,
        max_active: int | None = None,
        stale_minutes: int = 30,
        created_by: int | None = None,
    ) -> int:
        """Atomically create a job unless the round already has an active one."""
        self.reconcile_stale_jobs(stale_minutes)
        if self._round_has_live_job_process(round_id):
            raise JobBusyError("上一任务进程仍在退出，请稍后再试")
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

    def jobs_for_round(self, round_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE round_id = ? ORDER BY id DESC LIMIT ?",
            (int(round_id), max(1, min(100, int(limit)))),
        ).fetchall()
        return [dict(row) for row in rows]

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
        can_edit_leads: bool = False,
        can_send_email: bool = False,
        must_change_password: bool = False,
    ) -> int:
        now = utc_now()
        try:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO users
                    (username, password_hash, auth_source, external_id, role, disabled,
                     can_collect, can_edit_leads, can_send_email, must_change_password,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        password_hash,
                        auth_source,
                        external_id,
                        role,
                        1 if can_collect else 0,
                        1 if can_edit_leads else 0,
                        1 if can_send_email else 0,
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
            SELECT id, username, role, disabled, can_collect, can_edit_leads,
                   can_send_email, must_change_password, auth_source, created_at,
                   updated_at, last_login_at
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
        can_edit_leads: bool | None = None,
        can_send_email: bool | None = None,
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
        if can_edit_leads is not None:
            updates["can_edit_leads"] = 1 if can_edit_leads else 0
        if can_send_email is not None:
            updates["can_send_email"] = 1 if can_send_email else 0
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
        can_edit_leads: bool | None = None,
        can_send_email: bool | None = None,
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
            if can_edit_leads is not None:
                updates["can_edit_leads"] = 1 if can_edit_leads else 0
            if can_send_email is not None:
                updates["can_send_email"] = 1 if can_send_email else 0
            sets = ", ".join(f"{key} = ?" for key in updates)
            self._conn.execute(
                f"UPDATE users SET {sets} WHERE id = ?",
                [*updates.values(), user_id],
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


    def create_email_account(
        self,
        *,
        scope: str,
        owner_user_id: int | None,
        label: str,
        from_name: str,
        from_email: str,
        reply_to: str,
        host: str,
        port: int,
        security: str,
        username: str,
        password_encrypted: str,
        key_fingerprint: str,
        daily_limit: int,
        min_interval_seconds: int = 60,
        created_by: int | None,
    ) -> int:
        scope = str(scope or "personal").strip().lower()
        if scope not in EMAIL_ACCOUNT_SCOPES:
            raise ValueError("发件账号范围无效")
        security = str(security or "starttls").strip().lower()
        if security not in EMAIL_SECURITY_MODES:
            raise ValueError("SMTP 加密方式无效")
        if scope == "personal" and owner_user_id is None:
            raise ValueError("个人发件账号必须指定所属用户")
        host = str(host or "").strip().lower()
        if not host or any(ch in host for ch in " \r\n/:"):
            raise ValueError("SMTP 主机格式不正确")
        try:
            port = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError("SMTP 端口无效") from exc
        if port < 1 or port > 65535:
            raise ValueError("SMTP 端口无效")
        normalized_from = normalize_email_address(from_email)
        normalized_reply = (
            normalize_email_address(reply_to) if str(reply_to or "").strip() else ""
        )
        username = str(username or "").strip()
        if not username:
            raise ValueError("SMTP 用户名不能为空")
        if not password_encrypted:
            raise ValueError("SMTP 密码不能为空")
        limit = max(1, min(500, int(daily_limit or 20)))
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO email_accounts
                (scope, owner_user_id, label, from_name, from_email, reply_to,
                 host, port, security, username, password_encrypted,
                 key_fingerprint, daily_limit, min_interval_seconds,
                 enabled, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    scope,
                    int(owner_user_id) if owner_user_id is not None else None,
                    str(label or "").strip()[:80],
                    str(from_name or "").strip()[:80],
                    normalized_from,
                    normalized_reply,
                    host,
                    port,
                    security,
                    username[:200],
                    password_encrypted,
                    str(key_fingerprint or ""),
                    limit,
                    max(1, min(3600, int(min_interval_seconds or 60))),
                    created_by,
                    now,
                    now,
                ),
            )
        return int(cur.lastrowid)

    def list_email_accounts(self, user_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT *,
                   (SELECT COUNT(*) FROM email_campaigns c
                    WHERE c.account_id = email_accounts.id AND c.status = 'paused')
                       AS paused_campaigns
            FROM email_accounts
            WHERE scope = 'shared' OR owner_user_id = ?
            ORDER BY CASE scope WHEN 'shared' THEN 0 ELSE 1 END, id
            """,
            (int(user_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_email_account(self, account_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM email_accounts WHERE id = ?", (int(account_id),)
        ).fetchone()
        return dict(row) if row else None

    def count_email_accounts(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM email_accounts"
        ).fetchone()
        return int(row["n"] or 0)

    def update_email_account(self, account_id: int, **fields: Any) -> None:
        allowed = {
            "label",
            "from_name",
            "from_email",
            "reply_to",
            "host",
            "port",
            "security",
            "username",
            "password_encrypted",
            "key_fingerprint",
            "daily_limit",
            "min_interval_seconds",
            "enabled",
            "last_test_at",
            "last_test_error",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        if "from_email" in updates:
            updates["from_email"] = normalize_email_address(updates["from_email"])
        if "reply_to" in updates:
            updates["reply_to"] = (
                normalize_email_address(updates["reply_to"])
                if str(updates["reply_to"] or "").strip()
                else ""
            )
        if "security" in updates:
            security = str(updates["security"] or "starttls").strip().lower()
            if security not in EMAIL_SECURITY_MODES:
                raise ValueError("SMTP 加密方式无效")
            updates["security"] = security
        if "port" in updates:
            port = int(updates["port"])
            if port < 1 or port > 65535:
                raise ValueError("SMTP 端口无效")
            updates["port"] = port
        if "host" in updates:
            host = str(updates["host"] or "").strip().lower()
            if not host or any(ch in host for ch in " \r\n/:"):
                raise ValueError("SMTP 主机格式不正确")
            updates["host"] = host
        if "daily_limit" in updates:
            updates["daily_limit"] = max(1, min(500, int(updates["daily_limit"] or 20)))
        if "min_interval_seconds" in updates:
            updates["min_interval_seconds"] = max(
                1, min(3600, int(updates["min_interval_seconds"] or 60))
            )
        connection_fields = {
            "from_email",
            "host",
            "port",
            "security",
            "username",
            "password_encrypted",
        }
        if connection_fields.intersection(updates):
            updates.update(
                last_test_at=None,
                last_test_error="",
                auth_failed_at=None,
                auth_error="",
            )
        updates["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE email_accounts SET {sets} WHERE id = ?",
                [*updates.values(), int(account_id)],
            )
        if cur.rowcount == 0:
            raise ValueError("发件账号不存在")

    def mark_email_account_test(self, account_id: int, error: str | None) -> None:
        now = utc_now()
        with self._conn:
            self._conn.execute(
                "UPDATE email_accounts SET last_test_at = ?, last_test_error = ?, "
                "updated_at = ? WHERE id = ?",
                (now, (error or "")[:500], now, int(account_id)),
            )

    def delete_email_account(self, account_id: int) -> None:
        with self._conn:
            used = self._conn.execute(
                "SELECT 1 FROM email_campaigns WHERE account_id = ? LIMIT 1",
                (int(account_id),),
            ).fetchone()
            if used:
                raise ValueError("该账号已被发件任务使用，请改为停用而不是删除")
            cur = self._conn.execute(
                "DELETE FROM email_accounts WHERE id = ?", (int(account_id),)
            )
        if cur.rowcount == 0:
            raise ValueError("发件账号不存在")

    def creator_emails(self, platform: str, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM creator_emails WHERE platform = ? AND user_id = ? "
            "ORDER BY is_primary DESC, id ASC",
            (platform, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _add_creator_email_locked(
        self,
        platform: str,
        user_id: str,
        email: str,
        *,
        label: str = "",
        is_primary: bool = False,
        source: str = "manual",
        created_by: int | None = None,
    ) -> dict[str, Any]:
        creator = self._conn.execute(
            "SELECT 1 FROM creators WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        if not creator:
            raise ValueError("达人不存在")
        normalized = normalize_email_address(email)
        now = utc_now()
        existing = self._conn.execute(
            "SELECT COUNT(*) AS n FROM creator_emails "
            "WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        primary = bool(is_primary) or int(existing["n"] or 0) == 0
        if primary:
            self._conn.execute(
                "UPDATE creator_emails SET is_primary = 0 WHERE platform = ? AND user_id = ?",
                (platform, user_id),
            )
        self._conn.execute(
            """
            INSERT INTO creator_emails
            (platform, user_id, email, email_normalized, label, source,
             is_primary, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, user_id, email_normalized) DO UPDATE SET
                label = excluded.label,
                is_primary = CASE
                    WHEN excluded.is_primary = 1 THEN 1
                    ELSE creator_emails.is_primary
                END,
                updated_at = excluded.updated_at
            """,
            (
                platform,
                user_id,
                str(email).strip(),
                normalized,
                str(label or "").strip()[:80],
                str(source or "manual")[:40],
                1 if primary else 0,
                created_by,
                now,
                now,
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM creator_emails WHERE platform = ? AND user_id = ? "
            "AND email_normalized = ?",
            (platform, user_id, normalized),
        ).fetchone()
        return dict(row) if row else {}

    def add_creator_email(
        self,
        platform: str,
        user_id: str,
        email: str,
        *,
        label: str = "",
        is_primary: bool = False,
        source: str = "manual",
        created_by: int | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            return self._add_creator_email_locked(
                platform,
                user_id,
                email,
                label=label,
                is_primary=is_primary,
                source=source,
                created_by=created_by,
            )

    def delete_creator_email(self, email_id: int) -> dict[str, Any] | None:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM creator_emails WHERE id = ?", (int(email_id),)
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "DELETE FROM creator_emails WHERE id = ?", (int(email_id),)
            )
            if row["is_primary"]:
                replacement = self._conn.execute(
                    "SELECT id FROM creator_emails WHERE platform = ? AND user_id = ? "
                    "ORDER BY id ASC LIMIT 1",
                    (row["platform"], row["user_id"]),
                ).fetchone()
                if replacement:
                    self._conn.execute(
                        "UPDATE creator_emails SET is_primary = 1, updated_at = ? WHERE id = ?",
                        (utc_now(), int(replacement["id"])),
                    )
        return dict(row)

    def set_primary_creator_email(self, email_id: int) -> dict[str, Any] | None:
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM creator_emails WHERE id = ?", (int(email_id),)
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE creator_emails SET is_primary = 0 WHERE platform = ? AND user_id = ?",
                (row["platform"], row["user_id"]),
            )
            self._conn.execute(
                "UPDATE creator_emails SET is_primary = 1, updated_at = ? WHERE id = ?",
                (utc_now(), int(email_id)),
            )
        updated = self._conn.execute(
            "SELECT * FROM creator_emails WHERE id = ?", (int(email_id),)
        ).fetchone()
        return dict(updated) if updated else None


    def _creator_primary_email_locked(
        self, platform: str, user_id: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM creator_emails WHERE platform = ? AND user_id = ? "
            "ORDER BY is_primary DESC, id ASC LIMIT 1",
            (platform, user_id),
        ).fetchone()
        return dict(row) if row else None

    def email_targets(
        self,
        *,
        search: str = "",
        platform: str = "",
        round_id: int | None = None,
        owner: str = "all",
        has_email: bool | None = None,
        page: int = 1,
        page_size: int = 50,
        cooldown_days: int = 30,
        creator_cooldown_days: int = 90,
    ) -> dict[str, Any]:
        conditions = [
            "c.cooperation_status = 'not_contacted'",
            "EXISTS (SELECT 1 FROM creator_rounds cr "
            "WHERE cr.platform = c.platform AND cr.user_id = c.user_id "
            "AND cr.status = 'adopted'"
            + (" AND cr.round_id = ?" if round_id is not None else "")
            + ")",
        ]
        params: list[Any] = []
        if round_id is not None:
            params.append(int(round_id))
        if platform:
            conditions.append("c.platform = ?")
            params.append(platform)
        query = search.strip()
        if query:
            like = f"%{query}%"
            conditions.append(
                "(c.nickname LIKE ? OR c.user_id LIKE ? OR COALESCE(e.email, '') LIKE ?)"
            )
            params.extend([like, like, like])
        owner_filter = str(owner or "all")
        if owner_filter == "unassigned":
            conditions.append("c.follow_up_user_id IS NULL")
        elif owner_filter not in {"", "all"}:
            conditions.append("c.follow_up_user_id = ?")
            params.append(int(owner_filter))
        if has_email is True:
            conditions.append("e.email IS NOT NULL")
        elif has_email is False:
            conditions.append("e.email IS NULL")
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM email_suppressions s "
            "WHERE s.email_normalized = e.email_normalized)"
        )
        where = " AND ".join(f"({item})" for item in conditions)
        cte = """
            WITH primary_emails AS (
                SELECT platform, user_id, email, email_normalized,
                       ROW_NUMBER() OVER (
                           PARTITION BY platform, user_id
                           ORDER BY is_primary DESC, id ASC
                       ) AS rn
                FROM creator_emails
            )
        """
        from_sql = """
            FROM creators c
            LEFT JOIN users u ON u.id = c.follow_up_user_id
            LEFT JOIN primary_emails e
                ON e.platform = c.platform AND e.user_id = c.user_id AND e.rn = 1
        """
        total = int(
            self._conn.execute(
                cte + "SELECT COUNT(*) AS n " + from_sql + " WHERE " + where,
                params,
            ).fetchone()["n"]
            or 0
        )
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        rows = self._conn.execute(
            cte
            + """
            SELECT c.platform, c.user_id, c.nickname, c.avatar, c.fans,
                   c.profile_url, c.follow_up_user_id,
                   COALESCE(u.username, '') AS owner_username,
                   e.email,
                   (SELECT COUNT(*) FROM creator_emails ce
                    WHERE ce.platform = c.platform AND ce.user_id = c.user_id)
                       AS email_count,
                   (SELECT MAX(r.sent_at) FROM email_recipients r
                    WHERE r.creator_platform = c.platform
                      AND r.creator_user_id = c.user_id AND r.status = 'sent')
                       AS last_creator_sent_at,
                   (SELECT MAX(r.sent_at) FROM email_recipients r
                    WHERE r.email_normalized = e.email_normalized
                      AND r.status = 'sent')
                       AS last_email_sent_at
            """
            + from_sql
            + " WHERE "
            + where
            + " ORDER BY COALESCE(c.fans, 0) DESC, c.platform ASC, c.user_id ASC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        now = datetime.now(timezone.utc)
        items = []
        for row in rows:
            item = dict(row)
            email_until = _cooldown_until(
                item.get("last_email_sent_at"), cooldown_days
            )
            creator_until = _cooldown_until(
                item.get("last_creator_sent_at"), creator_cooldown_days
            )
            until = max(
                [value for value in (email_until, creator_until) if value],
                default=None,
            )
            reason = ""
            if until and email_until == until:
                reason = "email"
            elif until and creator_until == until:
                reason = "creator"
            item["cooldown_until"] = (
                until.isoformat() if until and until > now else ""
            )
            item["cooldown_reason"] = reason if until and until > now else ""
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def create_email_campaign(
        self,
        *,
        name: str,
        account_id: int,
        subject: str,
        body: str,
        timezone: str,
        window_start: str,
        window_end: str,
        weekdays: str,
        interval_seconds: int,
        jitter_seconds: int,
        daily_limit: int,
        auto_mark_contacted: bool,
        recipients: list[dict[str, Any]],
        created_by: int | None,
        scheduled_start: str | None = None,
        schedule: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        inserted = 0
        skipped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        seen_emails: set[str] = set()
        with self.transaction():
            account = self._conn.execute(
                "SELECT * FROM email_accounts WHERE id = ?", (int(account_id),)
            ).fetchone()
            if not account:
                raise ValueError("发件账号不存在")
            if schedule and not bool(account["enabled"]):
                raise ValueError("发件账号已停用")
            cur = self._conn.execute(
                """
                INSERT INTO email_campaigns
                (name, account_id, subject, body, status, timezone,
                 window_start, window_end, weekdays, interval_seconds,
                 jitter_seconds, daily_limit, auto_mark_contacted,
                 created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(name or "").strip()[:80],
                    int(account_id),
                    str(subject or "").strip()[:200],
                    str(body or ""),
                    str(timezone or "Asia/Shanghai")[:64],
                    str(window_start or "09:00"),
                    str(window_end or "18:00"),
                    str(weekdays or "[1,2,3,4,5]"),
                    max(1, min(3600, int(interval_seconds or 60))),
                    max(0, min(600, int(jitter_seconds or 0))),
                    max(1, min(500, int(daily_limit or 20))),
                    1 if auto_mark_contacted else 0,
                    created_by,
                    now,
                    now,
                ),
            )
            campaign_id = int(cur.lastrowid)
            for recipient in recipients:
                platform = str(recipient.get("platform") or "").strip()
                creator_user_id = str(recipient.get("user_id") or "").strip()
                if not platform or not creator_user_id:
                    skipped.append({"user_id": creator_user_id, "reason": "缺少达人标识"})
                    continue
                try:
                    email_value = normalize_email_address(recipient.get("email"))
                except ValueError:
                    skipped.append({"user_id": creator_user_id, "reason": "邮箱格式不正确"})
                    continue
                key = (platform, creator_user_id)
                if key in seen or email_value in seen_emails:
                    skipped.append({"user_id": creator_user_id, "reason": "重复收件人"})
                    continue
                seen.add(key)
                seen_emails.add(email_value)
                creator = self._conn.execute(
                    "SELECT cooperation_status, nickname FROM creators "
                    "WHERE platform = ? AND user_id = ?",
                    (platform, creator_user_id),
                ).fetchone()
                if not creator:
                    skipped.append({"user_id": creator_user_id, "reason": "达人不存在"})
                    continue
                if str(creator["cooperation_status"] or "") != "not_contacted":
                    skipped.append({"user_id": creator_user_id, "reason": "合作状态不是未联系"})
                    continue
                active = self._conn.execute(
                    """
                    SELECT c.id FROM email_recipients r
                    JOIN email_campaigns c ON c.id = r.campaign_id
                    WHERE r.creator_platform = ? AND r.creator_user_id = ?
                      AND r.status IN ('pending', 'sending')
                      AND c.status IN ('draft', 'scheduled', 'sending', 'paused')
                    LIMIT 1
                    """,
                    (platform, creator_user_id),
                ).fetchone()
                if active:
                    skipped.append(
                        {
                            "user_id": creator_user_id,
                            "reason": f"已在未结束的发件任务 #{active['id']} 中",
                        }
                    )
                    continue
                suppressed = self._conn.execute(
                    "SELECT 1 FROM email_suppressions WHERE email_normalized = ?",
                    (email_value,),
                ).fetchone()
                if suppressed:
                    skipped.append({"user_id": creator_user_id, "reason": "邮箱在退订名单"})
                    continue
                self._add_creator_email_locked(
                    platform,
                    creator_user_id,
                    email_value,
                    source="campaign",
                    created_by=created_by,
                )
                self._conn.execute(
                    """
                    INSERT INTO email_recipients
                    (campaign_id, creator_platform, creator_user_id, creator_nickname,
                     email, email_normalized, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        campaign_id,
                        platform,
                        creator_user_id,
                        str(recipient.get("nickname") or creator["nickname"] or ""),
                        email_value,
                        email_value,
                        now,
                        now,
                    ),
                )
                inserted += 1
            if inserted == 0:
                raise ValueError("没有可发送的有效收件人，未创建发件任务")
            if schedule:
                self._conn.execute(
                    """
                    UPDATE email_campaigns SET status = 'scheduled', scheduled_start = ?,
                        updated_at = ? WHERE id = ?
                    """,
                    (scheduled_start, now, campaign_id),
                )
        return {
            "campaign_id": campaign_id,
            "inserted": inserted,
            "skipped": skipped,
        }

    def update_email_campaign(self, campaign_id: int, **fields: Any) -> None:
        allowed = {
            "name",
            "account_id",
            "subject",
            "body",
            "timezone",
            "window_start",
            "window_end",
            "weekdays",
            "interval_seconds",
            "jitter_seconds",
            "daily_limit",
            "auto_mark_contacted",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        now = utc_now()
        with self._conn:
            row = self._conn.execute(
                "SELECT status FROM email_campaigns WHERE id = ?", (int(campaign_id),)
            ).fetchone()
            if not row:
                raise ValueError("发件任务不存在")
            if row["status"] != "draft":
                raise ValueError("只有草稿状态可以编辑")
            updates["updated_at"] = now
            sets = ", ".join(f"{key} = ?" for key in updates)
            self._conn.execute(
                f"UPDATE email_campaigns SET {sets} WHERE id = ?",
                [*updates.values(), int(campaign_id)],
            )

    def schedule_email_campaign(
        self,
        campaign_id: int,
        *,
        scheduled_start: str | None,
        timezone: str,
        window_start: str,
        window_end: str,
        weekdays: str,
        interval_seconds: int,
        jitter_seconds: int,
        daily_limit: int,
    ) -> None:
        now = utc_now()
        with self.transaction():
            row = self._conn.execute(
                """
                SELECT c.status, a.enabled, a.auth_error
                FROM email_campaigns c
                JOIN email_accounts a ON a.id = c.account_id
                WHERE c.id = ?
                """,
                (int(campaign_id),),
            ).fetchone()
            if not row:
                raise ValueError("发件任务或发件账号不存在")
            if row["status"] != "draft":
                raise ValueError("只有草稿状态可以计划发送")
            if not bool(row["enabled"]):
                raise ValueError("发件账号已停用")
            if str(row["auth_error"] or "").strip():
                raise ValueError("发件账号认证失败，请先更新密码并测试")
            recipients = self._conn.execute(
                "SELECT COUNT(*) AS n FROM email_recipients "
                "WHERE campaign_id = ? AND status = 'pending'",
                (int(campaign_id),),
            ).fetchone()
            if int(recipients["n"] or 0) == 0:
                raise ValueError("发件任务没有待发送收件人")
            self._conn.execute(
                """
                UPDATE email_campaigns SET status = 'scheduled',
                    scheduled_start = ?, timezone = ?, window_start = ?,
                    window_end = ?, weekdays = ?, interval_seconds = ?,
                    jitter_seconds = ?, daily_limit = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    scheduled_start,
                    str(timezone or "Asia/Shanghai")[:64],
                    str(window_start or "09:00"),
                    str(window_end or "18:00"),
                    str(weekdays or "[1,2,3,4,5]"),
                    max(1, min(3600, int(interval_seconds or 60))),
                    max(0, min(600, int(jitter_seconds or 0))),
                    max(1, min(500, int(daily_limit or 20))),
                    now,
                    int(campaign_id),
                ),
            )

    def cancel_email_campaign(self, campaign_id: int, reason: str = "") -> None:
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE email_campaigns SET status = 'cancelled', finished_at = ?, "
                "paused_reason = ?, updated_at = ? "
                "WHERE id = ? AND status IN ('draft', 'scheduled', 'sending', 'paused')",
                (now, str(reason or "")[:200], now, int(campaign_id)),
            )
        if cur.rowcount == 0:
            raise ValueError("当前状态不能取消")

    def delete_email_campaign(self, campaign_id: int) -> None:
        with self._conn:
            row = self._conn.execute(
                "SELECT status FROM email_campaigns WHERE id = ?", (int(campaign_id),)
            ).fetchone()
            if not row:
                raise ValueError("发件任务不存在")
            if row["status"] != "draft":
                raise ValueError("只有草稿可以删除；已计划/已发送请取消保留记录")
            self._conn.execute(
                "DELETE FROM email_recipients WHERE campaign_id = ?", (int(campaign_id),)
            )
            self._conn.execute(
                "DELETE FROM email_campaigns WHERE id = ?", (int(campaign_id),)
            )

    def list_email_campaigns(
        self, user_id: int, *, is_admin: bool = False
    ) -> list[dict[str, Any]]:
        where = "" if is_admin else "WHERE c.created_by = ?"
        params: tuple[Any, ...] = () if is_admin else (int(user_id),)
        rows = self._conn.execute(
            f"""
            SELECT c.*, a.label AS account_label, a.from_email AS account_from_email,
                   a.scope AS account_scope,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id) AS recipients,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id AND r.status = 'sent') AS sent,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id AND r.status = 'failed') AS failed,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id AND r.status = 'skipped') AS skipped
            FROM email_campaigns c
            LEFT JOIN email_accounts a ON a.id = c.account_id
            {where}
            ORDER BY c.id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def email_campaign_detail(self, campaign_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT c.*, a.label AS account_label, a.from_email AS account_from_email,
                   a.scope AS account_scope, a.enabled AS account_enabled,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id) AS recipients,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id AND r.status = 'sent') AS sent,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id AND r.status = 'failed') AS failed,
                   (SELECT COUNT(*) FROM email_recipients r
                    WHERE r.campaign_id = c.id AND r.status = 'skipped') AS skipped
            FROM email_campaigns c
            LEFT JOIN email_accounts a ON a.id = c.account_id
            WHERE c.id = ?
            """,
            (int(campaign_id),),
        ).fetchone()
        return dict(row) if row else None

    def email_campaign_recipients(
        self,
        campaign_id: int,
        *,
        status: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        conditions = ["campaign_id = ?"]
        params: list[Any] = [int(campaign_id)]
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions)
        total = int(
            self._conn.execute(
                f"SELECT COUNT(*) AS n FROM email_recipients WHERE {where}",
                params,
            ).fetchone()["n"]
            or 0
        )
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        rows = self._conn.execute(
            f"SELECT * FROM email_recipients WHERE {where} "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def email_suppressions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM email_suppressions ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def add_email_suppression(
        self,
        email: str,
        *,
        reason: str = "",
        source: str = "manual",
        created_by: int | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_email_address(email)
        now = utc_now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO email_suppressions
                (email, email_normalized, reason, source, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(email_normalized) DO UPDATE SET
                    reason = excluded.reason, source = excluded.source
                """,
                (
                    str(email).strip(),
                    normalized,
                    str(reason or "")[:200],
                    str(source or "manual")[:40],
                    created_by,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM email_suppressions WHERE email_normalized = ?",
                (normalized,),
            ).fetchone()
        return dict(row) if row else {}

    def delete_email_suppression(self, suppression_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM email_suppressions WHERE id = ?", (int(suppression_id),)
            )
        return cur.rowcount > 0


    def mark_contacted_from_email(
        self,
        platform: str,
        user_id: str,
        *,
        note: str,
        actor_user_id: int | None = None,
    ) -> bool:
        with self._conn:
            summary = self._cooperation_summary_row_locked(platform, user_id)
            if not summary or str(summary["cooperation_status"] or "") != "not_contacted":
                return False
            open_case = self._open_cooperation_case_locked(platform, user_id)
            if not open_case:
                return False
            try:
                self._update_cooperation_case_status_locked(
                    int(open_case["id"]),
                    "contacted",
                    note=note,
                    actor_user_id=actor_user_id,
                )
            except ValueError:
                return False
            self._sync_cooperation_summary_locked(platform, user_id)
        return True

    def mark_email_account_auth_failed(self, account_id: int, error: str) -> None:
        now = utc_now()
        with self._conn:
            self._conn.execute(
                "UPDATE email_accounts SET auth_failed_at = ?, auth_error = ?, "
                "updated_at = ? WHERE id = ?",
                (now, str(error or "")[:500], now, int(account_id)),
            )

    def clear_email_account_auth(self, account_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE email_accounts SET auth_failed_at = NULL, auth_error = '' "
                "WHERE id = ?",
                (int(account_id),),
            )

    def pause_email_campaign(self, campaign_id: int, reason: str) -> int:
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE email_campaigns SET status = 'paused', paused_reason = ?, "
                "updated_at = ? WHERE id = ? AND status IN ('scheduled', 'sending')",
                (str(reason or "")[:200], now, int(campaign_id)),
            )
        return max(0, cur.rowcount)

    def resume_email_campaign(self, campaign_id: int) -> int:
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE email_campaigns SET status = 'scheduled', paused_reason = '', "
                "updated_at = ? WHERE id = ? AND status = 'paused'",
                (now, int(campaign_id)),
            )
        return max(0, cur.rowcount)

    def retry_failed_email_recipients(self, campaign_id: int) -> int:
        now = utc_now()
        with self._conn:
            campaign = self._conn.execute(
                "SELECT status FROM email_campaigns WHERE id = ?",
                (int(campaign_id),),
            ).fetchone()
            if not campaign:
                raise ValueError("发件任务不存在")
            if str(campaign["status"]) == "cancelled":
                raise ValueError("已取消的任务不能重试，请新建任务")
            cur = self._conn.execute(
                """
                UPDATE email_recipients SET status = 'pending', attempts = 0,
                    manual_retries = manual_retries + 1,
                    next_attempt_at = NULL, error_code = '', error_message = '',
                    updated_at = ?
                WHERE campaign_id = ? AND status = 'failed' AND manual_retries < 3
                """,
                (now, int(campaign_id)),
            )
            changed = max(0, cur.rowcount)
            if changed:
                self._conn.execute(
                    "UPDATE email_campaigns SET status = 'scheduled', "
                    "finished_at = NULL, updated_at = ? "
                    "WHERE id = ? AND status IN ('completed', 'paused')",
                    (now, int(campaign_id)),
                )
        return changed

    def finish_email_campaign_if_done(self, campaign_id: int) -> bool:
        now = utc_now()
        with self._conn:
            remaining = self._conn.execute(
                "SELECT COUNT(*) AS n FROM email_recipients "
                "WHERE campaign_id = ? AND status IN ('pending', 'sending')",
                (int(campaign_id),),
            ).fetchone()
            if int(remaining["n"] or 0) > 0:
                return False
            cur = self._conn.execute(
                "UPDATE email_campaigns SET status = 'completed', finished_at = ?, "
                "updated_at = ? WHERE id = ? AND status IN ('sending', 'scheduled')",
                (now, now, int(campaign_id)),
            )
        return cur.rowcount > 0

    def acquire_email_worker(
        self, owner: str, *, stale_seconds: int = 60, now: str | None = None
    ) -> bool:
        stamp = now or utc_now()
        cutoff = (
            datetime.fromisoformat(stamp) - timedelta(seconds=max(5, stale_seconds))
        ).isoformat()
        with self.transaction():
            row = self._conn.execute(
                "SELECT owner, heartbeat_at FROM email_worker_state WHERE id = 1"
            ).fetchone()
            if (
                row
                and row["owner"]
                and row["owner"] != owner
                and str(row["heartbeat_at"] or "") > cutoff
            ):
                return False
            self._conn.execute(
                "INSERT INTO email_worker_state (id, owner, heartbeat_at) "
                "VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "owner = excluded.owner, heartbeat_at = excluded.heartbeat_at",
                (owner, stamp),
            )
        return True

    def renew_email_worker(self, owner: str, *, now: str | None = None) -> bool:
        stamp = now or utc_now()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE email_worker_state SET heartbeat_at = ? "
                "WHERE id = 1 AND owner = ?",
                (stamp, owner),
            )
        return cur.rowcount > 0


    def record_email_worker_tick(self, owner: str, error: str = "") -> None:
        now = utc_now()
        with self._conn:
            self._conn.execute(
                "UPDATE email_worker_state SET heartbeat_at = ?, last_tick_at = ?, "
                "last_error = ? WHERE id = 1 AND owner = ?",
                (now, now, str(error or "")[:500], owner),
            )

    def email_worker_status(self) -> dict[str, Any]:
        state = self._conn.execute(
            "SELECT * FROM email_worker_state WHERE id = 1"
        ).fetchone()
        counts = self._conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM email_campaigns
                 WHERE status IN ('scheduled', 'sending')) AS active_campaigns,
                (SELECT COUNT(*) FROM email_recipients r
                 JOIN email_campaigns c ON c.id = r.campaign_id
                 WHERE r.status = 'pending'
                   AND c.status IN ('scheduled', 'sending')) AS pending_recipients,
                (SELECT COUNT(*) FROM email_recipients
                 WHERE status = 'sending') AS sending_recipients,
                (SELECT COUNT(*) FROM email_recipients r
                 JOIN email_campaigns c ON c.id = r.campaign_id
                 WHERE r.status = 'failed'
                   AND c.status IN ('scheduled', 'sending')) AS failed_recipients
            """
        ).fetchone()
        result = dict(state) if state else {}
        result.update(dict(counts) if counts else {})
        return result

    def resume_paused_campaigns_for_account(self, account_id: int) -> int:
        now = utc_now()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE email_campaigns SET status = 'scheduled', paused_reason = '', "
                "updated_at = ? WHERE account_id = ? AND status = 'paused'",
                (now, int(account_id)),
            )
        return max(0, cur.rowcount)

    def create_email_unsubscribe_token(
        self,
        *,
        email: str,
        creator_platform: str,
        creator_user_id: str,
        campaign_id: int,
        recipient_id: int,
        expires_days: int = 90,
    ) -> str:
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(
            now.timestamp() + max(1, int(expires_days)) * 86400,
            tz=timezone.utc,
        )
        normalized = normalize_email_address(email)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO email_unsubscribe_tokens
                (token_hash, email, email_normalized, creator_platform,
                 creator_user_id, campaign_id, recipient_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    str(email).strip(),
                    normalized,
                    creator_platform,
                    creator_user_id,
                    int(campaign_id),
                    int(recipient_id),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
        return raw

    def get_email_unsubscribe_token(self, raw_token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT * FROM email_unsubscribe_tokens WHERE token_hash = ?",
            (digest,),
        ).fetchone()
        if not row:
            return None
        try:
            expires = datetime.fromisoformat(str(row["expires_at"]))
        except ValueError:
            return None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            return None
        item = dict(row)
        item["already_used"] = bool(item.get("used_at"))
        item.pop("token_hash", None)
        return item

    def apply_email_unsubscribe(self, raw_token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM email_unsubscribe_tokens WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            if not row:
                return None
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]))
            except ValueError:
                return None
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < now:
                return None
            normalized = str(row["email_normalized"])
            self._conn.execute(
                """
                INSERT INTO email_suppressions
                (email, email_normalized, reason, source, created_by, created_at)
                VALUES (?, ?, '用户退订', 'link', NULL, ?)
                ON CONFLICT(email_normalized) DO UPDATE SET
                    reason = excluded.reason, source = excluded.source
                """,
                (row["email"], normalized, now.isoformat()),
            )
            self._conn.execute(
                "UPDATE email_unsubscribe_tokens SET used_at = COALESCE(used_at, ?) "
                "WHERE id = ?",
                (now.isoformat(), int(row["id"])),
            )
            affected = self._conn.execute(
                "SELECT DISTINCT campaign_id FROM email_recipients "
                "WHERE email_normalized = ? AND status = 'pending'",
                (normalized,),
            ).fetchall()
            skipped_cur = self._conn.execute(
                "UPDATE email_recipients SET status = 'skipped', claimed_at = NULL, "
                "error_code = 'unsubscribed', error_message = '用户通过邮件退订', "
                "next_attempt_at = NULL, updated_at = ? "
                "WHERE email_normalized = ? AND status = 'pending'",
                (now.isoformat(), normalized),
            )
            skipped_count = max(0, skipped_cur.rowcount)
        for item in affected:
            if item["campaign_id"] is not None:
                self.finish_email_campaign_if_done(int(item["campaign_id"]))
        return {
            "email": row["email"],
            "already_used": bool(row["used_at"]),
            "skipped_recipients": skipped_count,
        }

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
            tokens = self._conn.execute(
                "DELETE FROM email_unsubscribe_tokens "
                "WHERE expires_at < ? OR (used_at IS NOT NULL AND used_at < ?)",
                (now, cutoff),
            ).rowcount
        return {
            "sessions": max(0, expired) + max(0, overflow),
            "sessions_expired": max(0, expired),
            "sessions_overflow": max(0, overflow),
            "audit_log": max(0, audits),
            "email_unsubscribe_tokens": max(0, tokens),
        }

    def prune_raw_and_calls(
        self, raw_retention_days: int = 365, api_call_retention_days: int = 365
    ) -> dict[str, int]:
        calls = 0
        if api_call_retention_days > 0:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=api_call_retention_days)
            ).isoformat(timespec="milliseconds")
            with self._conn:
                calls = max(
                    0,
                    self._conn.execute(
                        "DELETE FROM api_calls WHERE ts < ?", (cutoff,)
                    ).rowcount,
                )
        raw = 0
        if raw_retention_days > 0:
            referenced = {
                str(Path(row["raw_file"]).resolve())
                for row in self._conn.execute(
                    "SELECT DISTINCT raw_file FROM api_calls "
                    "WHERE raw_file IS NOT NULL AND raw_file != ''"
                ).fetchall()
            }
            cutoff = datetime.now().timestamp() - raw_retention_days * 86400
            for path in self.raw_dir.glob("*.json"):
                try:
                    if str(path.resolve()) in referenced or path.stat().st_mtime >= cutoff:
                        continue
                    path.unlink()
                    raw += 1
                except OSError:
                    continue
        return {"api_calls": calls, "raw_files": raw}

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
            "SELECT id, username, role, disabled, can_collect, can_edit_leads, can_send_email, "
            "must_change_password, auth_source, created_at, updated_at, last_login_at "
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
