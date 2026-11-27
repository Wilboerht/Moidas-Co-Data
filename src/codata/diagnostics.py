from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .db import Database
from .email_crypto import default_key_path, key_fingerprint


def config_warnings(settings: Settings) -> list[str]:
    warnings: list[str] = []
    if "*" in settings.allowed_hosts:
        warnings.append("CODATA_ALLOWED_HOSTS 含通配符 *，任何 Host 都会被信任")
    if any("*" in origin for origin in settings.allowed_origins):
        warnings.append("CODATA_ALLOWED_ORIGINS 含通配符，写请求来源校验被削弱")
    https_origins = [
        origin for origin in settings.allowed_origins if origin.startswith("https://")
    ]
    if settings.cookie_secure and not https_origins:
        warnings.append(
            "CODATA_COOKIE_SECURE=true 但没有 HTTPS Origin，浏览器可能收不到会话 Cookie"
        )
    if not settings.cookie_secure and https_origins:
        warnings.append(
            "CODATA_COOKIE_SECURE=false 但配置了 HTTPS Origin，公网会话 Cookie 未标记 Secure"
        )
    if settings.enable_docs and not settings.cookie_secure:
        warnings.append(
            "CODATA_ENABLE_DOCS=true 且未启用 HTTPS，/docs 可能暴露在明文网络"
        )
    return warnings


def status_report(db: Database, settings: Settings) -> dict[str, Any]:
    db_path = db.root / "codata.sqlite"
    backups = sorted(db.root.glob("codata.sqlite.bak-*"))
    attachments = settings.output_dir / "cooperation_attachments"
    attachment_files = (
        [path for path in attachments.rglob("*") if path.is_file()]
        if attachments.exists()
        else []
    )
    schema = int(
        db._conn.execute("PRAGMA user_version").fetchone()[0]
    )
    counts = db.counts()
    disk = shutil.disk_usage(db.root)
    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    warnings = config_warnings(settings)
    email_accounts = db.count_email_accounts()
    if email_accounts and not settings.public_url:
        warnings.append(
            "CODATA_PUBLIC_URL 未配置：自动邮件无法附带一键退订链接，只能提示回复退订"
        )
    return {
        "database": str(db_path),
        "database_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "schema_version": schema,
        "counts": counts,
        "active_jobs": db.count_active_jobs(),
        "backups": len(backups),
        "backup_bytes": sum(path.stat().st_size for path in backups),
        "attachment_files": len(attachment_files),
        "attachment_bytes": sum(path.stat().st_size for path in attachment_files),
        "email_accounts": email_accounts,
        "email_worker": db.email_worker_status(),
        "email_key_path": str(default_key_path()),
        "email_key_present": key_fingerprint() is not None,
        "email_key_fingerprint": key_fingerprint(),
        "output_dir": str(settings.output_dir),
        "frontend_ready": (frontend_dist / "index.html").is_file(),
        "disk_free_bytes": disk.free,
        "warnings": warnings,
    }
