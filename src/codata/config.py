from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(os.environ.get("CODATA_ROOT") or Path(__file__).resolve().parents[2])


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())
    return values or default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str
    token: str
    timeout: float = 30.0
    retries: int = 3
    delay: float = 0.6
    cache_days: int = 30
    output_dir: Path = PROJECT_ROOT / "data"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8501",
        "http://localhost:8501",
        "http://[::1]:8501",
    )
    cookie_secure: bool = False
    session_days: int = 7
    enable_docs: bool = False
    max_login_attempts: int = 10
    login_window_minutes: int = 15
    max_active_jobs: int = 2
    job_stale_minutes: int = 30
    lead_match_enabled: bool = True
    trusted_proxies: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    max_request_bytes: int = 5_000_000
    audit_retention_days: int = 365
    log_retention_days: int = 90
    backup_retention_days: int = 90
    backup_retention_count: int = 10
    rnote_host: str = ""
    rnote_api_key: str = ""
    dataflow_allowed_hosts: tuple[str, ...] = ()
    rnote_allowed_hosts: tuple[str, ...] = ()
    usd_cny: float = 7.2
    token_cny: float = 0.001
    provider_trust_env: bool = False


def load_settings(require_credentials: bool = True) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    host = os.getenv("DATAFLOW_HOST", "").strip().rstrip("/")
    token = os.getenv("DATAFLOW_TOKEN", "").strip()
    rnote_host = os.getenv("RNOTE_HOST", "").strip().rstrip("/") or "https://rnote.dev"
    rnote_api_key = os.getenv("RNOTE_API_KEY", "").strip()
    other_provider_keys = [
        os.getenv(name, "").strip()
        for name in ("EVERYINFRA_API_KEY", "TIKHUB_API_KEY", "JUSTONEAPI_TOKEN", "SOCIALDATAX_API_KEY", "REDFOX_API_KEY")
    ]
    if require_credentials:
        missing = [name for name, value in (("DATAFLOW_HOST", host), ("DATAFLOW_TOKEN", token)) if not value]
        if missing and not rnote_api_key and not any(other_provider_keys):
            raise SystemExit(
                f"缺少数采凭证: {', '.join(missing)}。请复制 .env.example 为 .env 并填写 DataFlow 或 Rnote 凭证。"
            )
    if host and not host.startswith(("http://", "https://")):
        raise SystemExit(f"DATAFLOW_HOST 需要包含协议前缀: {host}")
    if rnote_host and not rnote_host.startswith(("http://", "https://")):
        raise SystemExit(f"RNOTE_HOST 需要包含协议前缀: {rnote_host}")

    output_dir = Path(os.getenv("DATA_OUTPUT_DIR", "data")).expanduser()
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    return Settings(
        host=host,
        token=token,
        timeout=float(os.getenv("DATAFLOW_TIMEOUT", "30")),
        retries=int(os.getenv("DATAFLOW_RETRY", "3")),
        delay=float(os.getenv("DATAFLOW_DELAY", "0.6")),
        cache_days=int(os.getenv("DATAFLOW_CACHE_DAYS", "30")),
        output_dir=output_dir,
        allowed_hosts=_env_csv(
            "CODATA_ALLOWED_HOSTS", ("127.0.0.1", "localhost", "::1")
        ),
        allowed_origins=_env_csv(
            "CODATA_ALLOWED_ORIGINS",
            (
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:8501",
                "http://localhost:8501",
                "http://[::1]:8501",
            ),
        ),
        cookie_secure=_env_bool("CODATA_COOKIE_SECURE", False),
        session_days=max(1, int(os.getenv("CODATA_SESSION_DAYS", "7"))),
        enable_docs=_env_bool("CODATA_ENABLE_DOCS", False),
        max_login_attempts=max(1, int(os.getenv("CODATA_MAX_LOGIN_ATTEMPTS", "10"))),
        login_window_minutes=max(1, int(os.getenv("CODATA_LOGIN_WINDOW_MINUTES", "15"))),
        max_active_jobs=max(1, int(os.getenv("CODATA_MAX_ACTIVE_JOBS", "2"))),
        job_stale_minutes=max(1, int(os.getenv("CODATA_JOB_STALE_MINUTES", "30"))),
        lead_match_enabled=_env_bool("CODATA_LEAD_MATCH", True),
        trusted_proxies=_env_csv(
            "CODATA_TRUSTED_PROXIES", ("127.0.0.1", "localhost", "::1")
        ),
        max_request_bytes=max(
            1024, int(os.getenv("CODATA_MAX_REQUEST_BYTES", "5000000"))
        ),
        audit_retention_days=max(1, int(os.getenv("CODATA_AUDIT_RETENTION_DAYS", "365"))),
        log_retention_days=max(1, int(os.getenv("CODATA_LOG_RETENTION_DAYS", "90"))),
        backup_retention_days=max(0, int(os.getenv("CODATA_BACKUP_RETENTION_DAYS", "90"))),
        backup_retention_count=max(0, int(os.getenv("CODATA_BACKUP_RETENTION_COUNT", "10"))),
        rnote_host=rnote_host,
        rnote_api_key=rnote_api_key,
        dataflow_allowed_hosts=_env_csv("DATAFLOW_ALLOWED_HOSTS", ("dataflowserver.org",)),
        rnote_allowed_hosts=_env_csv("RNOTE_ALLOWED_HOSTS", ("rnote.dev",)),
        usd_cny=float(os.getenv("CODATA_USD_CNY", "7.2")),
        token_cny=float(os.getenv("CODATA_TOKEN_CNY", "0.001")),
        provider_trust_env=_env_bool("CODATA_PROVIDER_TRUST_ENV", False),
    )
