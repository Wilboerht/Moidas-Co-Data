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
from .identity import normalize_identity
from .normalize import utc_now


def _rows(db: Database) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db._conn.execute(
            """
            SELECT cr.*, c.nickname AS creator_nickname, c.raw_json AS creator_raw_json,
                   c.profile_url AS creator_profile_url,
                   c.follow_up_user_id AS creator_follow_up_user_id
            FROM creator_rounds cr
            JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
            WHERE c.user_id LIKE 'import:%'
            ORDER BY cr.round_id, cr.user_id
            """
        ).fetchall()
    ]


def _groups(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("platform") or "xhs"), str(row.get("user_id") or ""))
        grouped.setdefault(key, []).append(row)
    return grouped


def _existing_lead(
    db: Database,
    platform: str,
    legacy_user_id: str,
    account_id: str = "",
    profile_url: str = "",
) -> int | None:
    conditions = ["legacy_user_id = ?"]
    params: list[Any] = [legacy_user_id]
    if account_id:
        conditions.append(
            "(account_id = ? AND status NOT IN ('duplicate', 'discarded'))"
        )
        params.append(account_id)
    if profile_url:
        conditions.append(
            "(profile_url = ? AND status NOT IN ('duplicate', 'discarded'))"
        )
        params.append(profile_url)
    row = db._conn.execute(
        f"SELECT id FROM leads WHERE platform = ? AND ({' OR '.join(conditions)}) LIMIT 1",
        [platform, *params],
    ).fetchone()
    return int(row["id"]) if row else None


def _insert_lead(
    db: Database,
    platform: str,
    legacy_user_id: str,
    rows: list[dict[str, Any]],
    now: str,
    *,
    account_id: str = "",
    profile_url: str = "",
) -> int:
    primary = rows[0]
    raw_account = next(
        (str(row.get("account_id") or "").strip() for row in rows if str(row.get("account_id") or "").strip()),
        "",
    )
    account_id, profile_url = normalize_identity(
        platform,
        account_id or raw_account,
        profile_url or primary.get("creator_profile_url") or "",
    )
    converted = bool(account_id)
    identity_status = "verified" if converted else "unverified"
    identity_confidence = "conversion" if converted else ""
    identity_provider = "migration" if converted else ""
    identity_checked_at = now if converted else None
    identity_note = "导入身份迁移" if converted else ""
    lead_id = int(
        db._conn.execute(
            """
            INSERT INTO leads
            (platform, nickname, account_id, profile_url, raw_title, source,
             source_round_id, owner_user_id, status, legacy_user_id, raw_json,
             created_by, created_at, updated_at, identity_status, identity_confidence,
             identity_provider, identity_checked_at, identity_note,
             converted_platform, converted_user_id, converted_at)
            VALUES (?, ?, ?, ?, ?, 'excel_import', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?)
            """,
            (
                platform,
                primary.get("creator_nickname") or "",
                account_id,
                profile_url,
                primary.get("raw_title") or "",
                primary.get("round_id"),
                primary.get("owner_user_id") or primary.get("creator_follow_up_user_id"),
                "converted" if converted else "unverified",
                legacy_user_id,
                primary.get("creator_raw_json") or "{}",
                now,
                now,
                identity_status,
                identity_confidence,
                identity_provider,
                identity_checked_at,
                identity_note,
                platform if converted else None,
                account_id if converted else None,
                now if converted else None,
            ),
        ).lastrowid
    )
    for row in rows:
        db._conn.execute(
            """
            INSERT OR IGNORE INTO lead_rounds
            (lead_id, round_id, source_keywords, source_labels, raw_title, data_round,
             top_note_id, sample_notes, sample_likes, sample_collected, sample_comments,
             sample_interactions, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead_id,
                row.get("round_id"),
                row.get("source_keywords") or "[]",
                row.get("source_labels") or "[]",
                row.get("raw_title") or "",
                row.get("data_round") or "",
                row.get("top_note_id") or "",
                int(row.get("sample_notes") or 0),
                int(row.get("sample_likes") or 0),
                int(row.get("sample_collected") or 0),
                int(row.get("sample_comments") or 0),
                int(row.get("sample_interactions") or 0),
                now,
            ),
        )
    for attempt in db._conn.execute(
        "SELECT * FROM backfill_attempts WHERE platform = ? AND user_id = ?",
        (platform, legacy_user_id),
    ).fetchall():
        db._conn.execute(
            """
            INSERT INTO lead_attempts
            (lead_id, provider, query, candidate_user_id, candidate_nickname,
             candidate_profile_url, confidence, status, error, created_at)
            VALUES (?, 'everyinfra', ?, ?, '', '', '', ?, ?, ?)
            """,
            (
                lead_id,
                attempt["query"] or "",
                attempt["matched_user_id"] or "",
                attempt["status"] or "legacy",
                attempt["error"] or "",
                attempt["attempted_at"] or now,
            ),
        )
    return lead_id


def _merge_resolved_rows(
    db: Database, platform: str, legacy_user_id: str, account_id: str, rows: list[dict[str, Any]], now: str
) -> None:
    existing = db._conn.execute(
        "SELECT 1 FROM creators WHERE platform = ? AND user_id = ?",
        (platform, account_id),
    ).fetchone()
    legacy_case_count = int(
        db._conn.execute(
            "SELECT COUNT(*) AS n FROM cooperation_cases "
            "WHERE platform = ? AND user_id = ?",
            (platform, legacy_user_id),
        ).fetchone()["n"]
        or 0
    )
    legacy_leads = db._conn.execute(
        "SELECT id, owner_user_id, status FROM leads WHERE platform = ? AND account_id = ?",
        (platform, legacy_user_id),
    ).fetchall()
    for legacy_lead in legacy_leads:
        conflict = db._conn.execute(
            "SELECT id FROM leads WHERE platform = ? AND account_id = ? AND id <> ?",
            (platform, account_id, legacy_lead["id"]),
        ).fetchone()
        if conflict:
            db._conn.execute(
                "UPDATE leads SET owner_user_id = COALESCE(owner_user_id, ?) WHERE id = ?",
                (legacy_lead["owner_user_id"], conflict["id"]),
            )
            db._conn.execute(
                "UPDATE leads SET status = 'duplicate', updated_at = ? WHERE id = ? "
                "AND status <> 'converted'",
                (now, legacy_lead["id"]),
            )
        else:
            db._conn.execute(
                "UPDATE leads SET account_id = ? WHERE id = ?",
                (account_id, legacy_lead["id"]),
            )
    db._conn.execute(
        "UPDATE leads SET matched_user_id = ? WHERE platform = ? AND matched_user_id = ?",
        (account_id, platform, legacy_user_id),
    )
    db._conn.execute(
        "UPDATE leads SET converted_user_id = ? "
        "WHERE converted_platform = ? AND converted_user_id = ?",
        (account_id, platform, legacy_user_id),
    )
    db._conn.execute(
        "UPDATE lead_attempts SET candidate_user_id = ? WHERE candidate_user_id = ?",
        (account_id, legacy_user_id),
    )
    db._conn.execute(
        "UPDATE notes SET author_id = ? WHERE platform = ? AND author_id = ?",
        (account_id, platform, legacy_user_id),
    )
    if existing and legacy_case_count:
        real_open = db._open_cooperation_case_locked(platform, account_id)
        legacy_open = db._open_cooperation_case_locked(platform, legacy_user_id)
        if real_open and legacy_open:
            legacy_case_count = -1
        else:
            offset_row = db._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS n FROM cooperation_cases "
                "WHERE platform = ? AND user_id = ?",
                (platform, account_id),
            ).fetchone()
            offset = int(offset_row["n"] or 0)
            db._conn.execute(
                "UPDATE cooperation_cases SET user_id = ?, seq = seq + ? "
                "WHERE platform = ? AND user_id = ?",
                (account_id, offset, platform, legacy_user_id),
            )
            db._sync_cooperation_summary_locked(platform, account_id)
            legacy_case_count = 0
    for row in rows:
        round_id = row.get("round_id")
        if round_id is None:
            continue
        if existing:
            real_round = db._conn.execute(
                "SELECT 1 FROM creator_rounds WHERE round_id = ? AND platform = ? AND user_id = ?",
                (round_id, platform, account_id),
            ).fetchone()
            if real_round:
                db._conn.execute(
                    """
                    UPDATE creator_rounds SET
                        status = CASE WHEN status = 'pending' THEN ? ELSE status END,
                        match_direction = COALESCE(NULLIF(match_direction, ''), ?),
                        tags_manual = COALESCE(NULLIF(tags_manual, ''), ?),
                        remark = COALESCE(NULLIF(remark, ''), ?),
                        owner_user_id = COALESCE(owner_user_id, ?),
                        claimed_at = COALESCE(claimed_at, ?),
                        account_id = ?,
                        data_round = COALESCE(NULLIF(data_round, ''), ?),
                        updated_at = ?
                    WHERE round_id = ? AND platform = ? AND user_id = ?
                    """,
                    (
                        row.get("status") or "pending",
                        row.get("match_direction") or "",
                        row.get("tags_manual") or "",
                        row.get("remark") or "",
                        row.get("owner_user_id"),
                        row.get("claimed_at"),
                        account_id,
                        row.get("data_round") or "",
                        now,
                        round_id,
                        platform,
                        account_id,
                    ),
                )
                db._conn.execute(
                    "DELETE FROM creator_rounds WHERE round_id = ? AND platform = ? AND user_id = ?",
                    (round_id, platform, legacy_user_id),
                )
            else:
                db._conn.execute(
                    "UPDATE creator_rounds SET user_id = ?, account_id = ? "
                    "WHERE round_id = ? AND platform = ? AND user_id = ?",
                    (account_id, account_id, round_id, platform, legacy_user_id),
                )
        else:
            db._conn.execute(
                "UPDATE creators SET user_id = ?, red_id = COALESCE(NULLIF(red_id, ''), ?) "
                "WHERE platform = ? AND user_id = ?",
                (account_id, account_id, platform, legacy_user_id),
            )
            db._conn.execute(
                "UPDATE cooperation_cases SET user_id = ? "
                "WHERE platform = ? AND user_id = ?",
                (account_id, platform, legacy_user_id),
            )
            db._conn.execute(
                "UPDATE creator_rounds SET user_id = ?, account_id = ? "
                "WHERE round_id = ? AND platform = ? AND user_id = ?",
                (account_id, account_id, round_id, platform, legacy_user_id),
            )
            db._conn.execute(
                "UPDATE notes SET author_id = ? WHERE platform = ? AND author_id = ?",
                (account_id, platform, legacy_user_id),
            )
    db._conn.execute(
        "DELETE FROM creators WHERE platform = ? AND user_id = ? "
        "AND NOT EXISTS (SELECT 1 FROM creator_rounds cr WHERE cr.platform = creators.platform "
        "AND cr.user_id = creators.user_id) "
        "AND NOT EXISTS (SELECT 1 FROM cooperation_cases cc "
        "WHERE cc.platform = creators.platform AND cc.user_id = creators.user_id)",
        (platform, legacy_user_id),
    )


def migrate(db: Database, *, apply: bool) -> dict[str, Any]:
    rows = _rows(db)
    grouped = _groups(rows)
    report: dict[str, Any] = {
        "total_import_rows": len(rows),
        "legacy_identities": len(grouped),
        "resolved_identities": 0,
        "unresolved_identities": 0,
        "account_conflicts": 0,
        "conflicts": 0,
        "leads_before": db._conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
        "creators_before": db._conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0],
        "creator_rounds_before": db._conn.execute("SELECT COUNT(*) FROM creator_rounds").fetchone()[0],
    }
    for _key, group in grouped.items():
        if any(str(row.get("account_id") or "").strip() for row in group):
            report["resolved_identities"] += 1
        else:
            report["unresolved_identities"] += 1
    if not apply:
        return report

    now = utc_now()
    created = 0
    skipped = 0
    preserved = 0
    conflicts = 0
    affected_rounds: set[int] = set()
    with db._conn:
        for (platform, legacy_user_id), group in grouped.items():
            raw_accounts = [
                str(row.get("account_id") or "").strip()
                for row in group
                if str(row.get("account_id") or "").strip()
            ]
            account_id, profile_url = normalize_identity(
                platform,
                raw_accounts[0] if raw_accounts else "",
                group[0].get("creator_profile_url") or "",
            )
            if len({normalize_identity(platform, value, "")[0] for value in raw_accounts}) > 1:
                report["account_conflicts"] += 1
            if (
                _existing_lead(
                    db, platform, legacy_user_id, account_id, profile_url
                )
                is not None
            ):
                skipped += 1
                continue
            try:
                _insert_lead(
                    db,
                    platform,
                    legacy_user_id,
                    group,
                    now,
                    account_id=account_id,
                    profile_url=profile_url,
                )
            except sqlite3.IntegrityError:
                conflicts += 1
                continue
            created += 1
            if not account_id:
                preserved += len(group)
                continue
            _merge_resolved_rows(db, platform, legacy_user_id, account_id, group, now)
            affected_rounds.update(
                int(row["round_id"]) for row in group if row.get("round_id") is not None
            )
    for round_id in affected_rounds:
        db.rebuild_search_index(round_id)
    report.update(
        {
            "leads_after": db._conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "created": created,
            "skipped_existing": skipped,
            "preserved_unresolved_rows": preserved,
            "conflicts": conflicts,
            "creators_after": db._conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0],
            "creator_rounds_after": db._conn.execute("SELECT COUNT(*) FROM creator_rounds").fetchone()[0],
        }
    )
    return report


def _backup(db: Database) -> Path:
    path = db.root / f"codata.sqlite.bak-leads-{datetime.now():%Y%m%d-%H%M%S}"
    target = sqlite3.connect(path)
    try:
        db._conn.backup(target)
    finally:
        target.close()
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="codata-migrate-leads")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("--apply 和 --dry-run 不能同时使用")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = load_settings(require_credentials=False)
    with Database(settings.output_dir) as db:
        if args.apply:
            print(f"备份: {_backup(db)}")
        report = migrate(db, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.apply:
            print("dry-run：未修改数据库")


if __name__ == "__main__":
    main()
