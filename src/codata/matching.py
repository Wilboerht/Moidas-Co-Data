from __future__ import annotations

from typing import Any, Iterable

from .db import Database
from .identity import normalize_identity

ACTIVE_LEAD_STATUSES = ("unverified", "verifying")
SUGGESTION_LIMIT = 500


def _nickname_key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _chunks(values: Iterable[str], size: int = 300) -> list[list[str]]:
    items = [str(value).strip() for value in values if str(value or "").strip()]
    return [items[index : index + size] for index in range(0, len(items), size)]


def _round_creator_rows(db: Database, round_id: int) -> list[dict[str, Any]]:
    rows = db._conn.execute(
        """
        SELECT cr.platform, cr.user_id, cr.account_id AS round_account_id,
               c.nickname, c.profile_url, c.raw_json, c.fetched_at,
               c.red_id AS creator_handle
        FROM creator_rounds cr
        JOIN rounds r ON r.id = cr.round_id
        LEFT JOIN creators c
            ON c.platform = cr.platform AND c.user_id = cr.user_id
        WHERE cr.round_id = ? AND r.archived_at IS NULL
        """,
        (round_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _leads_by_identity(
    db: Database,
    platform: str,
    account_keys: set[str],
    legacy_keys: set[str],
    handle_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    leads: dict[int, dict[str, Any]] = {}
    handle_keys = handle_keys or set()
    identity_keys = {str(value).strip() for value in [*account_keys, *handle_keys] if str(value).strip()}
    for chunk in _chunks(identity_keys):
        placeholders = ",".join("?" * len(chunk))
        rows = db._conn.execute(
            f"""
            SELECT * FROM leads
            WHERE platform = ? AND status IN (?, ?)
              AND (account_id IN ({placeholders}) OR platform_handle IN ({placeholders}))
            """,
            [platform, *ACTIVE_LEAD_STATUSES, *chunk, *chunk],
        ).fetchall()
        for row in rows:
            leads[int(row["id"])] = dict(row)
    for chunk in _chunks(legacy_keys):
        placeholders = ",".join("?" * len(chunk))
        rows = db._conn.execute(
            f"""
            SELECT * FROM leads
            WHERE platform = ? AND status IN (?, ?) AND legacy_user_id IN ({placeholders})
            """,
            [platform, *ACTIVE_LEAD_STATUSES, *chunk],
        ).fetchall()
        for row in rows:
            leads[int(row["id"])] = dict(row)
    return list(leads.values())


def _pick_lead(
    leads: list[dict[str, Any]],
    user_id: str,
    round_account_id: str = "",
    creator_handle: str = "",
) -> tuple[dict[str, Any], int]:
    handle_keys = {value for value in (round_account_id, creator_handle) if value}

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        account = str(item.get("account_id") or "")
        handle = str(item.get("platform_handle") or "")
        if account and account == user_id:
            return 0, int(item["id"])
        if handle and handle in handle_keys:
            return 1, int(item["id"])
        if round_account_id and account and account == round_account_id:
            return 1, int(item["id"])
        if (item.get("matched_user_id") or "") == user_id:
            return 2, int(item["id"])
        return 3, int(item["id"])

    ordered = sorted(leads, key=rank)
    return ordered[0], max(0, len(ordered) - 1)


def _confidence(
    lead: dict[str, Any],
    user_id: str,
    round_account_id: str,
    creator_handle: str = "",
) -> str:
    account = str(lead.get("account_id") or "")
    handle = str(lead.get("platform_handle") or "")
    if account and account == user_id:
        return "account_id"
    if handle and handle in {value for value in (round_account_id, creator_handle) if value}:
        return "platform_handle"
    if round_account_id and account == round_account_id:
        return "round_account_id"
    return "legacy_user_id"


def _record_nickname_suggestions(
    db: Database,
    platform: str,
    creators: list[dict[str, Any]],
    round_id: int,
    *,
    exclude: set[int],
    dry_run: bool,
    actor_id: int | None,
) -> int:
    nickname_map: dict[str, list[dict[str, Any]]] = {}
    for creator in creators:
        user_id = str(creator.get("user_id") or "").strip()
        key = _nickname_key(creator.get("nickname"))
        if not user_id or not key:
            continue
        nickname_map.setdefault(key, []).append(creator)
    if not nickname_map:
        return 0
    rows = db._conn.execute(
        """
        SELECT id, nickname, matched_user_id FROM leads
        WHERE platform = ? AND status IN (?, ?)
          AND (account_id IS NULL OR account_id = '')
          AND nickname IS NOT NULL AND nickname <> ''
        """,
        [platform, *ACTIVE_LEAD_STATUSES],
    ).fetchall()
    suggestions: list[dict[str, Any]] = []
    for row in rows:
        lead_id = int(row["id"])
        if lead_id in exclude or row["matched_user_id"]:
            continue
        matches = nickname_map.get(_nickname_key(row["nickname"]))
        if not matches:
            continue
        user_ids = {str(item.get("user_id") or "") for item in matches}
        if len(user_ids) != 1:
            continue
        creator = matches[0]
        suggestions.append(
            {
                "lead_id": lead_id,
                "user_id": str(creator.get("user_id") or ""),
                "nickname": creator.get("nickname") or "",
                "profile_url": creator.get("profile_url") or "",
                "round_id": round_id,
            }
        )
        if len(suggestions) >= SUGGESTION_LIMIT:
            break
    if dry_run:
        return len(suggestions)
    return db.apply_lead_suggestions(suggestions, actor_id=actor_id)


def match_round_leads(
    db: Database,
    round_id: int,
    *,
    dry_run: bool = False,
    actor_id: int | None = None,
) -> dict[str, Any]:
    rows = _round_creator_rows(db, round_id)
    stats: dict[str, Any] = {
        "scanned": len(rows),
        "matched": 0,
        "updated": 0,
        "ambiguous": 0,
        "skipped": 0,
        "attempts": 0,
        "suggested": 0,
        "lead_ids": [],
        "examples": [],
    }
    if not rows:
        return stats

    platforms: dict[str, dict[str, Any]] = {}
    for row in rows:
        platform = str(row.get("platform") or "xhs")
        user_id = str(row.get("user_id") or "").strip()
        round_account_id = str(row.get("round_account_id") or "").strip()
        creator_handle = str(row.get("creator_handle") or "").strip()
        entry = platforms.setdefault(
            platform,
            {
                "creators": [],
                "account_keys": set(),
                "handle_keys": set(),
                "legacy_keys": set(),
            },
        )
        entry["creators"].append(row)
        if user_id:
            entry["account_keys"].add(user_id)
            entry["legacy_keys"].add(user_id)
        if round_account_id:
            entry["account_keys"].add(round_account_id)
            entry["handle_keys"].add(round_account_id)
        if creator_handle:
            entry["handle_keys"].add(creator_handle)

    matches: dict[int, dict[str, Any]] = {}
    seen_leads: set[int] = set()
    for platform, entry in platforms.items():
        leads = _leads_by_identity(
            db,
            platform,
            entry["account_keys"],
            entry["legacy_keys"],
            entry["handle_keys"],
        )
        by_account: dict[str, list[dict[str, Any]]] = {}
        by_handle: dict[str, list[dict[str, Any]]] = {}
        by_legacy: dict[str, list[dict[str, Any]]] = {}
        for lead in leads:
            account = str(lead.get("account_id") or "")
            handle = str(lead.get("platform_handle") or "")
            legacy = str(lead.get("legacy_user_id") or "")
            if account:
                by_account.setdefault(account, []).append(lead)
            if handle:
                by_handle.setdefault(handle, []).append(lead)
            if legacy:
                by_legacy.setdefault(legacy, []).append(lead)
        for creator in entry["creators"]:
            user_id = str(creator.get("user_id") or "").strip()
            if not user_id:
                continue
            round_account_id = str(creator.get("round_account_id") or "").strip()
            creator_handle = str(creator.get("creator_handle") or "").strip()
            candidates: dict[int, dict[str, Any]] = {}
            for key in {user_id, round_account_id}:
                if not key:
                    continue
                for lead in by_account.get(key, []):
                    candidates[int(lead["id"])] = lead
            for key in {round_account_id, creator_handle}:
                if not key:
                    continue
                for lead in by_handle.get(key, []):
                    candidates[int(lead["id"])] = lead
            for lead in by_legacy.get(user_id, []):
                candidates[int(lead["id"])] = lead
            if not candidates:
                continue
            selected, extra = _pick_lead(
                list(candidates.values()), user_id, round_account_id, creator_handle
            )
            stats["ambiguous"] += extra
            lead_id = int(selected["id"])
            seen_leads.add(lead_id)
            confidence = _confidence(
                selected, user_id, round_account_id, creator_handle
            )
            match = {
                "lead_id": lead_id,
                "platform": platform,
                "user_id": user_id,
                "account_id": round_account_id or user_id,
                "handle": creator_handle,
                "round_id": round_id,
                "confidence": confidence,
                "nickname": creator.get("nickname") or "",
                "profile_url": creator.get("profile_url") or "",
                "raw_json": creator.get("raw_json") or "",
                "overwrite": bool(creator.get("fetched_at")),
            }
            current = matches.get(lead_id)
            if current is None or confidence in {"account_id", "platform_handle"}:
                matches[lead_id] = match
            if len(stats["examples"]) < 10:
                stats["examples"].append(
                    {
                        "lead_id": lead_id,
                        "user_id": user_id,
                        "confidence": confidence,
                    }
                )

    stats["lead_ids"] = sorted(seen_leads)
    suggested = 0
    for platform, entry in platforms.items():
        suggested += _record_nickname_suggestions(
            db,
            platform,
            entry["creators"],
            round_id,
            exclude=seen_leads,
            dry_run=dry_run,
            actor_id=actor_id,
        )
    stats["suggested"] = suggested
    if dry_run:
        stats["matched"] = len(matches)
        return stats
    result = db.apply_lead_matches(list(matches.values()), actor_id=actor_id)
    stats.update(
        matched=result["matched"],
        updated=result["updated"],
        skipped=result["skipped"],
        attempts=result["attempts"],
    )
    return stats


def match_lead_against_creators(
    db: Database,
    lead_id: int,
    *,
    dry_run: bool = False,
    actor_id: int | None = None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "scanned": 1,
        "matched": 0,
        "updated": 0,
        "ambiguous": 0,
        "skipped": 0,
        "attempts": 0,
        "suggested": 0,
        "lead_ids": [],
        "examples": [],
    }
    lead = db.get_lead(lead_id)
    if not lead or lead.get("status") not in ACTIVE_LEAD_STATUSES:
        stats["skipped"] = 1
        return stats
    platform = str(lead.get("platform") or "xhs")
    lead_account = str(lead.get("account_id") or "").strip()
    normalized_account, _ = normalize_identity(
        platform, lead.get("account_id") or "", lead.get("profile_url") or ""
    )
    account_keys = {
        value for value in (lead_account, normalized_account) if value
    }
    handle = str(lead.get("platform_handle") or "").strip()
    legacy = str(lead.get("legacy_user_id") or "").strip()
    keys = {value for value in (*account_keys, handle, legacy) if value}
    if not keys:
        stats["skipped"] = 1
        return stats
    placeholders = ",".join("?" * len(keys))
    rows = db._conn.execute(
        f"""
        SELECT cr.user_id, cr.round_id, cr.account_id, c.red_id AS creator_handle
        FROM creator_rounds cr
        JOIN rounds r ON r.id = cr.round_id
        LEFT JOIN creators c ON c.platform = cr.platform AND c.user_id = cr.user_id
        WHERE cr.platform = ?
          AND r.archived_at IS NULL
          AND (cr.user_id IN ({placeholders}) OR cr.account_id IN ({placeholders})
               OR c.red_id IN ({placeholders}))
        ORDER BY cr.round_id DESC
        LIMIT 50
        """,
        [platform, *keys, *keys, *keys],
    ).fetchall()
    confidence_by_rank = {
        0: "account_id",
        1: "platform_handle",
        2: "round_account_id",
        3: "legacy_user_id",
        4: "legacy_user_id",
    }
    ranked: list[tuple[tuple[int, int], dict[str, Any], dict[str, Any], str]] = []
    for row in rows:
        candidate = db.get_creator(platform, row["user_id"])
        if not candidate:
            continue
        uid = str(candidate.get("user_id") or "")
        round_account = str(row["account_id"] or "").strip()
        creator_handle = str(row["creator_handle"] or "").strip()
        if uid and uid in account_keys:
            rank = 0
        elif handle and handle in {round_account, creator_handle}:
            rank = 1
        elif round_account and round_account in account_keys:
            rank = 2
        elif legacy and legacy in {uid, round_account, creator_handle}:
            rank = 3
        else:
            rank = 4
        ranked.append(
            ((rank, -int(row["round_id"])), row, candidate, confidence_by_rank[rank])
        )
    creator: dict[str, Any] | None = None
    round_id: int | None = None
    matched_user_id = ""
    matched_round_account = ""
    matched_handle = ""
    confidence = "legacy_user_id"
    if ranked:
        ranked.sort(key=lambda entry: entry[0])
        _, row, creator, confidence = ranked[0]
        round_id = int(row["round_id"])
        matched_user_id = str(creator.get("user_id") or "")
        matched_round_account = str(row["account_id"] or "")
        matched_handle = str(row["creator_handle"] or "")
    if creator is None:
        for key in sorted(keys):
            candidate = db.get_creator(platform, key)
            if candidate:
                creator = candidate
                matched_user_id = str(candidate.get("user_id") or "")
                if key in account_keys:
                    confidence = "account_id"
                elif handle and key == handle:
                    confidence = "platform_handle"
                else:
                    confidence = "legacy_user_id"
                break
    if creator is None:
        stats["skipped"] = 1
        if not dry_run and (lead.get("matched_user_id") or lead.get("matched_round_id")):
            db.clear_lead_match(lead_id, actor_id=actor_id)
        return stats
    match = {
        "lead_id": lead_id,
        "platform": platform,
        "user_id": matched_user_id,
        "account_id": matched_round_account or matched_user_id,
        "handle": matched_handle,
        "round_id": round_id,
        "confidence": confidence,
        "nickname": creator.get("nickname") or "",
        "profile_url": creator.get("profile_url") or "",
        "raw_json": creator.get("raw_json") or "",
        "overwrite": bool(creator.get("fetched_at")),
    }
    stats["lead_ids"] = [lead_id]
    stats["examples"] = [
        {"lead_id": lead_id, "user_id": matched_user_id, "confidence": confidence}
    ]
    if dry_run:
        stats["matched"] = 1
        return stats
    result = db.apply_lead_matches([match], actor_id=actor_id)
    stats.update(
        matched=result["matched"],
        updated=result["updated"],
        skipped=result["skipped"],
        attempts=result["attempts"],
    )
    return stats


def backfill_matches(
    db: Database,
    *,
    apply: bool = False,
    round_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if round_id is not None:
        rounds = [int(round_id)]
    else:
        rows = db._conn.execute(
            "SELECT DISTINCT cr.round_id FROM creator_rounds cr "
            "JOIN rounds r ON r.id = cr.round_id "
            "WHERE r.archived_at IS NULL ORDER BY cr.round_id DESC"
        ).fetchall()
        rounds = [int(row["round_id"]) for row in rows]
    if limit is not None:
        rounds = rounds[: max(0, int(limit))]
    report: dict[str, Any] = {
        "apply": apply,
        "rounds": len(rounds),
        "creators": 0,
        "matched": 0,
        "updated": 0,
        "ambiguous": 0,
        "attempts": 0,
        "suggested": 0,
        "lead_ids": [],
        "examples": [],
    }
    matched_ids: set[int] = set()
    for current in rounds:
        stats = match_round_leads(db, current, dry_run=not apply)
        report["creators"] += int(stats.get("scanned") or 0)
        report["ambiguous"] += int(stats.get("ambiguous") or 0)
        report["updated"] += int(stats.get("updated") or 0)
        report["attempts"] += int(stats.get("attempts") or 0)
        report["suggested"] += int(stats.get("suggested") or 0)
        matched_ids.update(stats.get("lead_ids") or [])
        for example in stats.get("examples") or []:
            if len(report["examples"]) < 10:
                report["examples"].append({**example, "round_id": current})
    remaining = db._conn.execute(
        """
        SELECT id FROM leads
        WHERE status IN ('unverified', 'verifying')
          AND (matched_user_id IS NULL OR matched_user_id = '')
        ORDER BY id DESC
        """
    ).fetchall()
    for row in remaining:
        stats = match_lead_against_creators(db, int(row["id"]), dry_run=not apply)
        report["updated"] += int(stats.get("updated") or 0)
        report["attempts"] += int(stats.get("attempts") or 0)
        matched_ids.update(stats.get("lead_ids") or [])
        for example in stats.get("examples") or []:
            if len(report["examples"]) < 10:
                report["examples"].append(example)
    report["matched"] = len(matched_ids)
    report["lead_ids"] = sorted(matched_ids)
    return report
