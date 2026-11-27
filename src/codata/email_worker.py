from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import make_msgid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database, utc_now
from .email_crypto import EmailKeyError, decrypt_secret, key_fingerprint
from .email_sender import EmailSendError, send_plain_email
from .email_templates import creator_context, render_template

TICK_SECONDS = 5
MAX_ATTEMPTS = 3
STUCK_MINUTES = 10
RETRY_BACKOFF_SECONDS = (60, 300, 900)
RECIPIENT_TERMINAL = {"sent", "failed", "skipped"}

logger = logging.getLogger("codata.email")


def _parse_time(value: str, default: str) -> tuple[int, int]:
    text = str(value or default)
    try:
        hour, minute = text.split(":", 1)
        return int(hour), int(minute)
    except (TypeError, ValueError):
        hour, minute = default.split(":", 1)
        return int(hour), int(minute)


def _campaign_timezone(campaign: dict[str, Any]) -> ZoneInfo | None:
    try:
        return ZoneInfo(str(campaign.get("timezone") or "Asia/Shanghai"))
    except Exception:
        return None


def _scheduled_at(campaign: dict[str, Any], tz: ZoneInfo) -> datetime | None:
    raw = str(campaign.get("scheduled_start") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(timezone.utc)


def _within_window(campaign: dict[str, Any], tz: ZoneInfo, now: datetime) -> bool:
    try:
        weekdays = {
            int(item) for item in json.loads(campaign.get("weekdays") or "[]")
        }
    except (TypeError, ValueError):
        weekdays = {1, 2, 3, 4, 5}
    local_now = now.astimezone(tz)
    if weekdays and local_now.isoweekday() not in weekdays:
        return False
    start_hour, start_minute = _parse_time(str(campaign.get("window_start")), "09:00")
    end_hour, end_minute = _parse_time(str(campaign.get("window_end")), "18:00")
    current = local_now.hour * 60 + local_now.minute
    return start_hour * 60 + start_minute <= current < end_hour * 60 + end_minute


def _count_sent_today(db: Database, campaign_id: int, today: str) -> int:
    row = db._conn.execute(
        "SELECT COUNT(*) AS n FROM email_recipients "
        "WHERE campaign_id = ? AND status = 'sent' AND sent_date = ?",
        (int(campaign_id), today),
    ).fetchone()
    return int(row["n"] or 0)


def _count_account_sent_today(db: Database, account_id: int, day: str) -> int:
    row = db._conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM email_recipients r
        JOIN email_campaigns c ON c.id = r.campaign_id
        WHERE c.account_id = ? AND r.status = 'sent'
          AND date(r.sent_at) = ?
        """,
        (int(account_id), day),
    ).fetchone()
    return int(row["n"] or 0)


def _recently_emailed_creator(
    db: Database,
    platform: str,
    user_id: str,
    *,
    days: int,
    exclude_campaign_id: int,
    now: datetime,
) -> bool:
    if days <= 0:
        return False
    cutoff = datetime.fromtimestamp(
        now.timestamp() - days * 86400, tz=timezone.utc
    ).isoformat()
    row = db._conn.execute(
        """
        SELECT 1 FROM email_recipients r
        WHERE r.creator_platform = ? AND r.creator_user_id = ?
          AND r.status = 'sent' AND r.sent_at IS NOT NULL AND r.sent_at >= ?
          AND r.campaign_id <> ?
        LIMIT 1
        """,
        (platform, user_id, cutoff, int(exclude_campaign_id)),
    ).fetchone()
    return bool(row)


def _last_sent_at(db: Database, *, campaign_id: int | None, account_id: int | None) -> datetime | None:
    if campaign_id is not None:
        row = db._conn.execute(
            "SELECT MAX(sent_at) AS last FROM email_recipients "
            "WHERE campaign_id = ? AND status = 'sent'",
            (int(campaign_id),),
        ).fetchone()
    else:
        row = db._conn.execute(
            """
            SELECT MAX(r.sent_at) AS last
            FROM email_recipients r
            JOIN email_campaigns c ON c.id = r.campaign_id
            WHERE c.account_id = ? AND r.status = 'sent'
            """,
            (int(account_id or 0),),
        ).fetchone()
    raw = str((row["last"] if row else "") or "")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _campaign_ready(
    db: Database, campaign: dict[str, Any], now: datetime
) -> tuple[bool, str]:
    tz = _campaign_timezone(campaign)
    if tz is None:
        return False, "时区无效"
    scheduled = _scheduled_at(campaign, tz)
    if scheduled is not None and scheduled > now:
        return False, "尚未到计划开始时间"
    if not _within_window(campaign, tz, now):
        return False, "不在发送时段"
    local_today = now.astimezone(tz).date().isoformat()
    utc_today = now.astimezone(timezone.utc).date().isoformat()
    daily_limit = int(campaign.get("daily_limit") or 20)
    if _count_sent_today(db, int(campaign["id"]), local_today) >= daily_limit:
        return False, "已达到任务每日上限"
    account_limit = int(campaign.get("account_daily_limit") or 20)
    if (
        _count_account_sent_today(db, int(campaign["account_id"]), utc_today)
        >= account_limit
    ):
        return False, "已达到账号每日上限"
    interval = max(1, int(campaign.get("interval_seconds") or 60))
    jitter = max(0, int(campaign.get("jitter_seconds") or 0))
    last_campaign = _last_sent_at(
        db, campaign_id=int(campaign["id"]), account_id=None
    )
    if last_campaign is not None and (now - last_campaign).total_seconds() < interval:
        return False, "任务发送间隔未到"
    account_interval = max(
        interval, int(campaign.get("account_min_interval") or 60)
    )
    last_account = _last_sent_at(
        db, campaign_id=None, account_id=int(campaign["account_id"])
    )
    if last_account is not None and (
        now - last_account
    ).total_seconds() < account_interval + random.randint(0, jitter):
        return False, "账号发送间隔未到"
    return True, ""


def _recently_emailed(
    db: Database,
    email: str,
    *,
    days: int,
    exclude_campaign_id: int,
    now: datetime,
) -> bool:
    if days <= 0:
        return False
    cutoff = datetime.fromtimestamp(
        now.timestamp() - days * 86400, tz=timezone.utc
    ).isoformat()
    row = db._conn.execute(
        """
        SELECT 1
        FROM email_recipients r
        WHERE r.email_normalized = ? AND r.status = 'sent'
          AND r.sent_at IS NOT NULL AND r.sent_at >= ?
          AND r.campaign_id <> ?
        LIMIT 1
        """,
        (str(email or "").strip().lower(), cutoff, int(exclude_campaign_id)),
    ).fetchone()
    return bool(row)


def _is_suppressed(db: Database, email: str) -> bool:
    row = db._conn.execute(
        "SELECT 1 FROM email_suppressions WHERE email_normalized = ? LIMIT 1",
        (str(email or "").strip().lower(),),
    ).fetchone()
    return bool(row)


def _release_recipient(db: Database, recipient_id: int, *, error: str = "") -> None:
    _update_recipient(
        db,
        int(recipient_id),
        status="pending",
        claimed_at=None,
        error_code="paused" if not error else "released",
        error_message=error[:500],
        next_attempt_at=None,
    )


class EmailDeliveryStateError(RuntimeError):
    """SMTP accepted a message but its local terminal state could not be saved."""


def _claim_recipient(
    db: Database, campaign_id: int, now_iso: str
) -> dict[str, Any] | None:
    with db.transaction():
        campaign = db._conn.execute(
            "SELECT status FROM email_campaigns WHERE id = ?", (int(campaign_id),)
        ).fetchone()
        if not campaign or str(campaign["status"]) not in {"scheduled", "sending"}:
            return None
        row = db._conn.execute(
            """
            SELECT * FROM email_recipients
            WHERE campaign_id = ? AND status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY id ASC LIMIT 1
            """,
            (int(campaign_id), now_iso),
        ).fetchone()
        if not row:
            return None
        cur = db._conn.execute(
            "UPDATE email_recipients SET status = 'sending', attempts = attempts + 1, "
            "claimed_at = ?, last_attempt_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now_iso, now_iso, now_iso, int(row["id"])),
        )
        if cur.rowcount == 0:
            return None
        changed = db._conn.execute(
            "UPDATE email_campaigns SET status = 'sending', "
            "started_at = COALESCE(started_at, ?), updated_at = ? "
            "WHERE id = ? AND status IN ('scheduled', 'sending')",
            (now_iso, now_iso, int(campaign_id)),
        )
        if changed.rowcount == 0:
            return None
    claimed = dict(row)
    claimed["attempts"] = int(row["attempts"] or 0) + 1
    claimed["status"] = "sending"
    claimed["claimed_at"] = now_iso
    claimed["last_attempt_at"] = now_iso
    return claimed


def _update_recipient(db: Database, recipient_id: int, **fields: Any) -> None:
    fields["updated_at"] = utc_now()
    sets = ", ".join(f"{key} = ?" for key in fields)
    with db._conn:
        db._conn.execute(
            f"UPDATE email_recipients SET {sets} WHERE id = ?",
            [*fields.values(), int(recipient_id)],
        )


def _record_delivery_updates(
    db: Database,
    campaign: dict[str, Any],
    recipient: dict[str, Any],
    message_id: str,
) -> None:
    try:
        db.audit(
            None,
            "email.sent",
            f"email_campaign:{campaign['id']}",
            details={
                "recipient_id": recipient["id"],
                "email": recipient["email"],
                "message_id": message_id,
            },
        )
    except Exception:
        logger.exception("email.sent audit failed for recipient %s", recipient["id"])
    if bool(campaign.get("auto_mark_contacted")):
        try:
            marked = db.mark_contacted_from_email(
                str(recipient["creator_platform"]),
                str(recipient["creator_user_id"]),
                note=f"自动发件：{campaign.get('name') or campaign['id']}",
                actor_user_id=campaign.get("created_by"),
            )
            if marked:
                db.audit(
                    None,
                    "email.auto_contacted",
                    f"{recipient['creator_platform']}:{recipient['creator_user_id']}",
                    details={"campaign_id": campaign["id"]},
                )
        except Exception:
            logger.exception(
                "auto contacted update failed for recipient %s", recipient["id"]
            )
    try:
        db.finish_email_campaign_if_done(int(campaign["id"]))
    except Exception:
        logger.exception("campaign completion update failed: %s", campaign["id"])


def _send_one(
    db: Database,
    campaign: dict[str, Any],
    recipient: dict[str, Any],
    now: datetime,
    sender: Callable[..., str],
    *,
    public_url: str = "",
) -> str:
    now_iso = now.isoformat()
    account = {
        "host": campaign["host"],
        "port": campaign["port"],
        "security": campaign["security"],
        "username": campaign["username"],
        "from_name": campaign["from_name"],
        "from_email": campaign["from_email"],
        "reply_to": campaign["reply_to"],
    }
    message_id = str(recipient.get("message_id") or "").strip()
    if not message_id:
        message_id = make_msgid(domain=str(campaign["from_email"]).split("@")[-1])
        _update_recipient(db, int(recipient["id"]), message_id=message_id)
        recipient["message_id"] = message_id
    fingerprint = str(campaign.get("key_fingerprint") or "")
    current_fingerprint = key_fingerprint()
    if fingerprint and current_fingerprint and fingerprint != current_fingerprint:
        raise EmailKeyError("加密密钥与发件账号不匹配，请重新保存 SMTP 密码")
    password = decrypt_secret(str(campaign.get("password_encrypted") or ""))
    creator = db.get_creator(
        str(recipient["creator_platform"]), str(recipient["creator_user_id"])
    ) or {}
    context = creator_context(
        {
            **creator,
            "platform": recipient["creator_platform"],
            "user_id": recipient["creator_user_id"],
            "nickname": creator.get("nickname") or recipient["creator_nickname"],
        }
    )
    subject = render_template(str(campaign.get("subject") or ""), context)
    body = render_template(str(campaign.get("body") or ""), context)
    headers: dict[str, str] = {}
    if public_url:
        token = db.create_email_unsubscribe_token(
            email=str(recipient["email"]),
            creator_platform=str(recipient["creator_platform"]),
            creator_user_id=str(recipient["creator_user_id"]),
            campaign_id=int(campaign["id"]),
            recipient_id=int(recipient["id"]),
        )
        unsubscribe_url = (
            f"{public_url.rstrip('/')}/api/email/unsubscribe?token={token}"
        )
        body = (
            body.rstrip()
            + "\n\n---\n如不希望再收到此类邮件，请点击退订："
            + unsubscribe_url
        )
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    else:
        contact = str(campaign.get("reply_to") or campaign.get("from_email") or "")
        body = (
            body.rstrip()
            + "\n\n---\n如不希望再收到此类邮件，请回复本邮件退订"
            + (f"（{contact}）" if contact else "")
            + "。"
        )
    send_kwargs: dict[str, Any] = {
        "to": str(recipient["email"]),
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }
    if headers:
        send_kwargs["headers"] = headers
    sender(account, password, **send_kwargs)
    tz = _campaign_timezone(campaign)
    sent_date = (now.astimezone(tz) if tz else now).date().isoformat()
    try:
        with db.transaction():
            cur = db._conn.execute(
                """
                UPDATE email_recipients SET status = 'sent', sent_at = ?, sent_date = ?,
                    message_id = ?, claimed_at = NULL, error_code = '',
                    error_message = '', next_attempt_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'sending'
                """,
                (now_iso, sent_date, message_id, now_iso, int(recipient["id"])),
            )
            if cur.rowcount != 1:
                raise EmailDeliveryStateError("已发送邮件的本地状态不是发送中")
    except Exception as exc:
        logger.critical(
            "SMTP accepted recipient %s but sent state was not saved; automatic retry disabled",
            recipient["id"],
            exc_info=True,
        )
        if isinstance(exc, EmailDeliveryStateError):
            raise
        raise EmailDeliveryStateError("SMTP 已返回成功，但发送状态保存失败") from exc
    _record_delivery_updates(db, campaign, recipient, message_id)
    return message_id


def _handle_failure(
    db: Database,
    campaign: dict[str, Any],
    recipient: dict[str, Any],
    exc: Exception,
    attempts: int,
    now: datetime,
) -> str:
    now_iso = now.isoformat()
    kind = getattr(exc, "kind", "transient")
    message = str(exc)[:500]
    if kind == "auth":
        db.mark_email_account_auth_failed(int(campaign["account_id"]), message)
        db.pause_email_campaign(
            int(campaign["id"]), "发件账号认证失败，请更新密码或授权码后恢复"
        )
        _update_recipient(
            db,
            int(recipient["id"]),
            status="pending",
            claimed_at=None,
            error_code="auth",
            error_message=message,
            next_attempt_at=None,
        )
        outcome = "paused"
    elif kind in {"permanent", "unknown"} or attempts >= MAX_ATTEMPTS:
        if kind == "unknown":
            message = f"发送结果未知，请先人工确认是否已收到邮件：{message}"[:500]
        _update_recipient(
            db,
            int(recipient["id"]),
            status="failed",
            claimed_at=None,
            error_code=kind,
            error_message=message,
            next_attempt_at=None,
        )
        outcome = "failed"
    else:
        backoff = RETRY_BACKOFF_SECONDS[
            min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)
        ]
        next_attempt = datetime.fromtimestamp(
            now.timestamp() + backoff, tz=timezone.utc
        ).isoformat()
        _update_recipient(
            db,
            int(recipient["id"]),
            status="pending",
            claimed_at=None,
            error_code=kind,
            error_message=message,
            next_attempt_at=next_attempt,
        )
        outcome = "retried"
    db.audit(
        None,
        "email.send_failed",
        f"email_campaign:{campaign['id']}",
        details={
            "recipient_id": recipient["id"],
            "email": recipient["email"],
            "kind": kind,
            "attempts": attempts,
            "error": message,
        },
    )
    if outcome == "failed":
        db.finish_email_campaign_if_done(int(campaign["id"]))
    return outcome


def _recover_stuck(db: Database, now: datetime) -> int:
    cutoff = (
        datetime.fromtimestamp(
            now.timestamp() - STUCK_MINUTES * 60, tz=timezone.utc
        ).isoformat()
    )
    with db._conn:
        rows = db._conn.execute(
            "SELECT DISTINCT campaign_id FROM email_recipients "
            "WHERE status = 'sending' AND claimed_at IS NOT NULL AND claimed_at < ?",
            (cutoff,),
        ).fetchall()
        cur = db._conn.execute(
            "UPDATE email_recipients SET status = 'failed', claimed_at = NULL, "
            "error_code = 'unknown', "
            "error_message = '发送状态未知，请人工确认后重试', updated_at = ? "
            "WHERE status = 'sending' AND claimed_at IS NOT NULL AND claimed_at < ?",
            (now.isoformat(), cutoff),
        )
        changed = max(0, cur.rowcount)
    for row in rows:
        db.finish_email_campaign_if_done(int(row["campaign_id"]))
    return changed


def run_once(
    db: Database,
    *,
    sender: Callable[..., str] = send_plain_email,
    now: datetime | None = None,
    max_sends: int = 5,
    cooldown_days: int = 30,
    creator_cooldown_days: int = 90,
    public_url: str = "",
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    stats = {
        "sent": 0,
        "failed": 0,
        "retried": 0,
        "paused": 0,
        "recovered": 0,
        "skipped": 0,
    }
    stats["recovered"] = _recover_stuck(db, now)
    campaigns = db._conn.execute(
        """
        SELECT c.*, a.id AS account_id, a.host, a.port, a.security, a.username,
               a.password_encrypted, a.key_fingerprint, a.from_name, a.from_email,
               a.reply_to, a.daily_limit AS account_daily_limit,
               a.min_interval_seconds AS account_min_interval,
               a.enabled AS account_enabled, a.auth_error AS account_auth_error,
               u.role AS creator_role, u.can_send_email AS creator_can_send_email,
               u.disabled AS creator_disabled
        FROM email_campaigns c
        JOIN email_accounts a ON a.id = c.account_id
        LEFT JOIN users u ON u.id = c.created_by
        WHERE c.status IN ('scheduled', 'sending')
        ORDER BY COALESCE(
            (SELECT MAX(r.last_attempt_at) FROM email_recipients r
             WHERE r.campaign_id = c.id), ''
        ) ASC, c.id ASC
        """
    ).fetchall()
    sent_total = 0
    for raw in campaigns:
        campaign = dict(raw)
        if sent_total >= max_sends:
            break
        if not bool(campaign.get("account_enabled")):
            db.pause_email_campaign(int(campaign["id"]), "发件账号已停用")
            stats["paused"] += 1
            continue
        if str(campaign.get("account_auth_error") or "").strip():
            db.pause_email_campaign(
                int(campaign["id"]), "发件账号认证失败，请更新密码后恢复"
            )
            stats["paused"] += 1
            continue
        if campaign.get("created_by") is not None:
            creator_active = (
                not bool(campaign.get("creator_disabled"))
                and (
                    str(campaign.get("creator_role") or "") == "admin"
                    or bool(campaign.get("creator_can_send_email"))
                )
            )
            if not creator_active:
                db.pause_email_campaign(
                    int(campaign["id"]), "创建人已无发件权限或已停用"
                )
                stats["paused"] += 1
                continue
        ready, _reason = _campaign_ready(db, campaign, now)
        if not ready:
            continue
        recipient = _claim_recipient(db, int(campaign["id"]), now_iso)
        if not recipient:
            db.finish_email_campaign_if_done(int(campaign["id"]))
            continue
        current_campaign = db._conn.execute(
            "SELECT status FROM email_campaigns WHERE id = ?", (int(campaign["id"]),)
        ).fetchone()
        if not current_campaign or str(current_campaign["status"]) not in (
            "scheduled",
            "sending",
        ):
            _release_recipient(db, int(recipient["id"]))
            continue
        creator_row = db.get_creator(
            str(recipient["creator_platform"]), str(recipient["creator_user_id"])
        )
        if (
            not creator_row
            or str(creator_row.get("cooperation_status") or "") != "not_contacted"
        ):
            _update_recipient(
                db,
                int(recipient["id"]),
                status="skipped",
                claimed_at=None,
                error_code="status_changed",
                error_message="达人合作状态已变化，不再发送",
                next_attempt_at=None,
            )
            db.finish_email_campaign_if_done(int(campaign["id"]))
            stats["skipped"] += 1
            continue
        if _is_suppressed(db, str(recipient["email"])):
            _update_recipient(
                db,
                int(recipient["id"]),
                status="skipped",
                claimed_at=None,
                error_code="suppressed",
                error_message="邮箱在退订名单",
                next_attempt_at=None,
            )
            db.audit(
                None,
                "email.skipped_suppressed",
                f"email_campaign:{campaign['id']}",
                details={"recipient_id": recipient["id"], "email": recipient["email"]},
            )
            db.finish_email_campaign_if_done(int(campaign["id"]))
            stats["skipped"] += 1
            continue
        if _recently_emailed(
            db,
            str(recipient["email"]),
            days=cooldown_days,
            exclude_campaign_id=int(campaign["id"]),
            now=now,
        ):
            _update_recipient(
                db,
                int(recipient["id"]),
                status="skipped",
                claimed_at=None,
                error_code="cooldown",
                error_message="该邮箱在冷却期内已发送过邮件",
                next_attempt_at=None,
            )
            db.audit(
                None,
                "email.skipped_cooldown",
                f"email_campaign:{campaign['id']}",
                details={"recipient_id": recipient["id"], "email": recipient["email"]},
            )
            db.finish_email_campaign_if_done(int(campaign["id"]))
            stats["skipped"] += 1
            continue
        if _recently_emailed_creator(
            db,
            str(recipient["creator_platform"]),
            str(recipient["creator_user_id"]),
            days=creator_cooldown_days,
            exclude_campaign_id=int(campaign["id"]),
            now=now,
        ):
            _update_recipient(
                db,
                int(recipient["id"]),
                status="skipped",
                claimed_at=None,
                error_code="creator_cooldown",
                error_message="该达人在冷却期内已发送过邮件",
                next_attempt_at=None,
            )
            db.audit(
                None,
                "email.skipped_creator_cooldown",
                f"email_campaign:{campaign['id']}",
                details={"recipient_id": recipient["id"]},
            )
            db.finish_email_campaign_if_done(int(campaign["id"]))
            stats["skipped"] += 1
            continue
        try:
            _send_one(
                db,
                campaign,
                recipient,
                now,
                sender,
                public_url=public_url,
            )
            stats["sent"] += 1
            sent_total += 1
        except EmailKeyError as exc:
            db.pause_email_campaign(
                int(campaign["id"]), "加密密钥缺失或不匹配，请重新保存 SMTP 密码"
            )
            _update_recipient(
                db,
                int(recipient["id"]),
                status="pending",
                claimed_at=None,
                error_code="key",
                error_message=str(exc)[:500],
                next_attempt_at=None,
            )
            stats["paused"] += 1
        except EmailDeliveryStateError:
            stats["failed"] += 1
        except EmailSendError as exc:
            outcome = _handle_failure(
                db, campaign, recipient, exc, int(recipient["attempts"]), now
            )
            stats[outcome] = stats.get(outcome, 0) + 1
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("email send crashed before SMTP acceptance: %s", exc)
            outcome = _handle_failure(
                db,
                campaign,
                recipient,
                EmailSendError(f"内部错误: {exc}"),
                int(recipient["attempts"]),
                now,
            )
            stats[outcome] = stats.get(outcome, 0) + 1
    return stats


def _configure_logging(settings: Settings) -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    try:
        log_dir = settings.output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "email_worker.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.StreamHandler())


def start_email_worker(settings: Settings) -> None:
    _configure_logging(settings)
    owner = f"{uuid.uuid4().hex}"
    with Database(settings.output_dir) as db:
        while True:
            now = utc_now()
            try:
                if not db.renew_email_worker(owner, now=now):
                    if not db.acquire_email_worker(
                        owner, now=now, stale_seconds=900
                    ):
                        time.sleep(TICK_SECONDS)
                        continue
                stats = run_once(
                    db,
                    cooldown_days=settings.email_cooldown_days,
                    creator_cooldown_days=settings.email_creator_cooldown_days,
                    public_url=settings.public_url,
                )
                try:
                    db.record_email_worker_tick(owner)
                except Exception:
                    logger.exception("email worker heartbeat update failed")
                if any(value for value in stats.values()):
                    logger.info("email worker stats: %s", stats)
            except Exception as exc:
                logger.exception("email worker tick failed")
                try:
                    db.record_email_worker_tick(owner, str(exc))
                except Exception:
                    logger.exception("email worker heartbeat update failed")
            time.sleep(TICK_SECONDS)
