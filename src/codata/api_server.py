from __future__ import annotations

import csv
import hashlib
import io
import hmac
import json
import mimetypes
import sqlite3
import os
import re
import tempfile
import threading
import uuid
import zipfile
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Generator, Iterable

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import set_key
from starlette.concurrency import run_in_threadpool
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field, StringConstraints
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request

from .api import DOUYIN_COST, XHS_COST, DataFlowAPI, RnoteAPI
from .auth import (
    CSRF_HEADER,
    ROLES,
    SESSION_COOKIE,
    LoginRateLimiter,
    hash_password,
    hash_session_token,
    new_csrf_token,
    new_session_token,
    normalize_username,
    session_expiry,
    validate_password,
    validate_username,
    verify_dummy_password,
    verify_password,
)
from .client import DataFlowClient, RnoteClient
from .config import PROJECT_ROOT, Settings, load_settings, runtime_env_path
from .cooperation_statuses import (
    COOPERATION_OPEN_CODES,
    COOPERATION_STATUS_CODES,
    COOPERATION_STATUS_DEFS,
    COOPERATION_STATUS_LABELS,
    normalize_cooperation_status,
)
from .db import Database, JobBusyError, SCHEMA_VERSION
from .normalize import (
    as_optional_int,
    format_fans_wan,
    normalize_tier_label,
    split_tags,
)
from .platforms import PlatformConfigError, load_registry
from .providers import build_provider
from .panel.queries import EXPORT_COLUMNS, JOB_STATUS_LABELS, PLATFORM_LABELS, ROUND_STATUS_LABELS
from .panel.runner import start_round_job, terminate_job_process
from .lead_statuses import (
    LEAD_IDENTITY_DEFS,
    LEAD_IDENTITY_LABELS,
    LEAD_SOURCE_CODES,
    LEAD_SOURCE_LABELS,
    LEAD_STATUS_CODES,
    LEAD_STATUS_DEFS,
    LEAD_STATUS_LABELS,
    normalize_lead_source,
    normalize_lead_status,
)
from .lead_identity import LeadIdentityMismatch, verify_lead_identity
from .leads import convert_lead
from .matching import match_lead_against_creators
from .statuses import STATUS_CODES, STATUS_DEFS, STATUS_LABELS, normalize_status
from .storage import unwrap_data

settings = load_settings(require_credentials=False)
ENV_PATH = runtime_env_path()
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
settings_update_lock = threading.Lock()


def serialized_settings_update(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with settings_update_lock:
            return fn(*args, **kwargs)

    return wrapped

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_API_PATHS = {"/api/health", "/api/auth/login", "/api/auth/me"}
_ROUND_NAMES_VISIBLE: ContextVar[bool] = ContextVar(
    "codata_round_names_visible", default=True
)


def _round_names_visible() -> bool:
    return _ROUND_NAMES_VISIBLE.get()


def _set_round_names_visibility(user: dict[str, Any] | None) -> None:
    _ROUND_NAMES_VISIBLE.set(
        bool(user and (user.get("role") == "admin" or user.get("can_collect")))
    )
login_limiter = LoginRateLimiter(
    max_attempts=settings.max_login_attempts,
    window_seconds=settings.login_window_minutes * 60,
)
action_limiter = LoginRateLimiter(max_attempts=60, window_seconds=60)


def _enforce_action_limit(key: str) -> None:
    bucket = f"action:{key}"
    if action_limiter.blocked(bucket):
        retry = action_limiter.retry_after(bucket)
        raise HTTPException(
            429,
            "操作过于频繁，请稍后再试",
            headers={"Retry-After": str(retry or 1)},
        )
    action_limiter.fail(bucket)


class LocalOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        return await call_next(request)


class RequestSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        raw = request.headers.get("content-length")
        if raw:
            try:
                limit = settings.max_request_bytes
                path = request.url.path
                if path.startswith("/api/cooperation-cases/") and path.endswith("/attachments"):
                    limit = settings.attachment_max_bytes + 1_000_000
                if int(raw) > limit:
                    return JSONResponse({"detail": "请求体过大"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "无效的 Content-Length"}, status_code=400)
        elif request.method not in SAFE_METHODS and request.headers.get(
            "transfer-encoding"
        ):
            return JSONResponse(
                {"detail": "分块请求体不受支持，请提供 Content-Length"},
                status_code=411,
            )
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            not path.startswith("/api/")
            or path in PUBLIC_API_PATHS
            or request.method == "OPTIONS"
        ):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return JSONResponse({"detail": "请先登录"}, status_code=401)

        db = Database(settings.output_dir)
        request.state.auth_db = db
        try:
            session = await run_in_threadpool(
                db.get_session, hash_session_token(token)
            )
            if not session:
                return JSONResponse({"detail": "登录已失效，请重新登录"}, status_code=401)
            if request.method not in SAFE_METHODS:
                csrf = request.headers.get(CSRF_HEADER)
                if not csrf or not hmac.compare_digest(csrf, session["session"]["csrf_token"]):
                    return JSONResponse({"detail": "CSRF 校验失败"}, status_code=403)
            if (
                session["user"].get("must_change_password")
                and path not in {"/api/auth/change-password", "/api/auth/logout"}
            ):
                return JSONResponse({"detail": "请先修改初始密码"}, status_code=403)
            request.state.user = session["user"]
            request.state.session = session["session"]
            _set_round_names_visibility(session["user"])
            return await call_next(request)
        finally:
            db.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if _secure_request(request):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: https:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
                "base-uri 'self'; form-action 'self'",
            )
        return response


app = FastAPI(
    title="Co-data API",
    version="0.2.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(AuthMiddleware)
app.add_middleware(LocalOriginMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeMiddleware)


@app.on_event("startup")
def reconcile_jobs_on_startup() -> None:
    with Database(settings.output_dir) as db:
        db.reconcile_stale_jobs(settings.job_stale_minutes)
        db.prune_maintenance(settings.audit_retention_days)
        db.prune_raw_and_calls(
            settings.raw_retention_days, settings.api_call_retention_days
        )
        db.prune_backups(
            settings.backup_retention_days,
            settings.backup_retention_count,
            apply=True,
        )


def get_db(request: Request) -> Generator[Database, None, None]:
    existing = getattr(request.state, "auth_db", None)
    if existing is not None:
        yield existing
        return
    db = Database(settings.output_dir)
    try:
        yield db
    finally:
        db.close()


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


EXPORT_FIELDS = {
    "昵称": "nickname",
    "粉丝数": "fans",
    "粉丝数(万)": "fans_wan",
    "粉丝层级": "fans_tier",
    "数据轮次": "data_round",
    "采用状态": "status_label",
    "合作状态": "cooperation_status_label",
    "合作更新时间": "cooperation_updated_at",
    "匹配方向": "match_direction",
    "备注": "remark",
    "笔记数": "posted",
    "总点赞": "liked",
    "互动量": "interaction",
    "标签": "tags",
    "主页URL": "profile_url",
    "账号ID": "account_id",
    "代表作": "top_title",
    "命中关键词": "matched_keywords",
    "来源标签": "source",
    "平台": "platform_label",
    "负责人": "owner_username",
    "合作主平台": "cooperation_platform",
    "合作次数": "cooperation_count",
    "最近完成合作": "cooperation_last_completed_at",
    "原始标题": "raw_title",
    "样本笔记": "sample_notes",
    "样本点赞": "sample_likes",
    "样本互动": "sample_interactions",
}


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


_SECRET_QUERY_RE = re.compile(
    r"([?&](?:token|api_?key|apikey|access_token|auth)=)[^&\s\"']+", re.IGNORECASE
)


def _public_error(exc: Any) -> str:
    text = str(exc or "")
    text = _SECRET_QUERY_RE.sub(r"\1***", text)
    text = re.sub(r"(https?://[^\s?#\"']+)\?[^\s\"']*", r"\1", text)
    return text[:300]


def _export_value(item: dict[str, Any], header: str) -> Any:
    key = EXPORT_FIELDS[header]
    value = item.get(key)
    if key in ("fans", "posted", "liked", "interaction") and value is None:
        value = ""
    return _safe_cell(value)


def _creator_export_columns(actor: dict[str, Any]) -> list[str]:
    if actor.get("role") == "admin" or actor.get("can_collect"):
        return list(EXPORT_COLUMNS)
    return [column for column in EXPORT_COLUMNS if column != "数据轮次"]


def _lead_export_columns(actor: dict[str, Any]) -> list[str]:
    if actor.get("role") == "admin" or actor.get("can_collect"):
        return list(LEAD_EXPORT_COLUMNS)
    return [column for column in LEAD_EXPORT_COLUMNS if column not in ("来源轮次", "匹配轮次")]


def _iter_export_items(
    db: Database,
    round_id: int | None,
    filters: dict[str, Any],
    page_size: int = 500,
    sort: str = "fans",
    order: str = "desc",
) -> Generator[dict[str, Any], None, None]:
    with db._conn:
        db._conn.execute("BEGIN")
        page = 1
        while True:
            result = db.query_creators(
                round_id,
                sort=sort,
                order=order,
                page=page,
                page_size=page_size,
                **filters,
            )
            for raw in result["items"]:
                yield _creator_dict(raw)
            if page * page_size >= result["total"]:
                break
            page += 1


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


PROVIDER_LABELS = {
    "auto": "自动",
    "cheapest": "自动（最便宜）",
    "default": "自动",
    "dataflow": "DataFlow",
    "rnote": "Rnote",
    "everyinfra": "EveryInfra",
    "tikhub": "TikHub",
    "justoneapi": "JustOneAPI",
    "socialdatax": "SocialDataX",
    "redfox": "RedFoxHub",
}


def _round_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    platform = str(row.get("platform") or "xhs")
    provider = str(row.get("provider") or "dataflow")
    payload["status_label"] = ROUND_STATUS_LABELS.get(str(row.get("status")), str(row.get("status")))
    payload["platform"] = platform
    payload["platform_label"] = PLATFORM_LABELS.get(platform, platform)
    payload["provider"] = provider
    payload["provider_label"] = PROVIDER_LABELS.get(provider, provider)
    payload["archived"] = bool(row.get("archived_at"))
    if payload["archived"]:
        payload["status_label"] = "已归档"
    if not _round_names_visible():
        payload["name"] = ""
    return payload


def _job_dict(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    payload = dict(job)
    payload.pop("pid", None)
    payload["status_label"] = JOB_STATUS_LABELS.get(str(job.get("status")), str(job.get("status")))
    return payload


def _round_payload(db: Database, round_id: int) -> dict[str, Any]:
    row = db.get_round(round_id)
    if not row:
        raise HTTPException(404, "轮次不存在")
    keywords = db.get_round_keywords(round_id, include_disabled=True)
    payload = _round_dict(row)
    payload["keywords"] = "、".join(item["word"] for item in keywords)
    payload["keyword_details"] = keywords
    payload["summary"] = db.round_summary(round_id)
    payload["job"] = _job_dict(db.latest_job(round_id))
    if not _round_names_visible():
        payload["keywords"] = ""
        payload["keyword_details"] = []
        payload["summary"] = {}
        payload["job"] = None
    return payload


def _cooperation_label(value: Any) -> str:
    code = normalize_cooperation_status(value, default="")
    if not code:
        return "未进入"
    return COOPERATION_STATUS_LABELS.get(code, code)


COOPERATION_ATTACHMENT_CATEGORIES = {
    "contract": "合同/协议",
    "quote": "报价/沟通",
    "screenshot": "截图",
    "settlement": "结算凭证",
    "other": "其他",
}


def _case_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    status = normalize_cooperation_status(payload.get("status"), default="")
    payload["status"] = status
    payload["status_label"] = COOPERATION_STATUS_LABELS.get(status, status)
    payload["active"] = status in COOPERATION_OPEN_CODES
    return payload


def _case_event_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["from_status_label"] = _cooperation_label(
        payload.get("status_from") or ""
    )
    payload["to_status_label"] = _cooperation_label(payload.get("status_to") or "")
    return payload


def _attachment_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "case_id": int(row["case_id"]),
        "event_id": row.get("event_id"),
        "original_name": row.get("original_name") or "",
        "content_type": row.get("content_type") or "",
        "size_bytes": int(row.get("size_bytes") or 0),
        "sha256": row.get("sha256") or "",
        "category": row.get("category") or "other",
        "category_label": COOPERATION_ATTACHMENT_CATEGORIES.get(
            row.get("category") or "other", "其他"
        ),
        "description": row.get("description") or "",
        "uploaded_by": row.get("uploaded_by"),
        "uploaded_by_username": row.get("uploaded_by_username") or "",
        "created_at": row.get("created_at") or "",
        "download_url": f"/api/cooperation-attachments/{int(row['id'])}",
    }


def _work_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "case_id": int(row["case_id"]),
        "platform": row.get("platform") or "",
        "url": row.get("url") or "",
        "title": row.get("title") or "",
        "published_at": row.get("published_at") or "",
        "note": row.get("note") or "",
        "created_by": row.get("created_by"),
        "created_by_username": row.get("created_by_username") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }


def _cooperation_case_payload(db: Database, case_id: int) -> dict[str, Any]:
    detail = db.cooperation_case_detail(int(case_id))
    if not detail:
        raise HTTPException(404, "合作记录不存在")
    case = _case_dict(detail["case"])
    case["summary_info"] = detail["summary"]
    creator_platform = case["platform"]
    creator_user_id = case["user_id"]
    creator = db.get_creator(creator_platform, creator_user_id) or {}
    case["creator"] = {
        "platform": creator_platform,
        "user_id": creator_user_id,
        "nickname": creator.get("nickname") or "",
        "avatar": creator.get("avatar") or "",
        "follow_up_user_id": creator.get("follow_up_user_id"),
    }
    return {
        "case": case,
        "summary": detail["summary"],
        "events": [_case_event_dict(item) for item in detail["events"]],
        "attachments": [
            _attachment_dict(item) for item in detail["attachments"]
        ],
        "works": [_work_dict(item) for item in detail["works"]],
    }


def _validate_work_payload(work: Any) -> dict[str, Any]:
    if not isinstance(work, dict):
        raise HTTPException(400, "作品数据格式错误")
    url = str(work.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(400, "作品链接必须是 http/https 地址")
    if len(url) > 2000:
        raise HTTPException(400, "作品链接过长")
    title = str(work.get("title") or "").strip()
    note = str(work.get("note") or "").strip()
    if len(title) > 500 or len(note) > 2000:
        raise HTTPException(400, "作品标题或备注过长")
    return {
        "platform": str(work.get("platform") or "").strip()[:40],
        "url": url,
        "title": title,
        "published_at": str(work.get("published_at") or "").strip()[:40] or None,
        "note": note,
    }


def _attachment_root() -> Path:
    return settings.output_dir / "cooperation_attachments"


def _case_actor(request: Request) -> dict[str, Any]:
    return require_user(request)


def _case_error(exc: ValueError, *, conflict: bool = True) -> HTTPException:
    message = str(exc)
    return HTTPException(409 if conflict else 400, message)


def _creator_dict(row: dict[str, Any]) -> dict[str, Any]:
    auto_tags = [str(tag) for tag in _json_list(row.get("auto_tags"))]
    manual_tags = split_tags(row.get("tags_manual"))
    tags = list(dict.fromkeys([*auto_tags, *manual_tags]))
    source_keywords = [str(item) for item in _json_list(row.get("source_keywords"))]
    source_labels = [str(item) for item in _json_list(row.get("source_labels"))]
    fans = row.get("fans")
    user_id = row["user_id"]
    account_id = row.get("account_id")
    if account_id is None:
        account_id = "" if str(user_id).startswith("import:") else user_id
    tier_label = normalize_tier_label(fans, row.get("tier_label")) or "—"
    data_round = row.get("data_round") or row.get("round_name") or ""
    row_status = normalize_status(row.get("status"))
    cooperation_status = normalize_cooperation_status(
        row.get("cooperation_status"), default=""
    )
    payload = {
        "key": f"{row['platform']}|{row['user_id']}",
        "round_id": row.get("round_id"),
        "platform": row["platform"],
        "platform_label": PLATFORM_LABELS.get(row["platform"], row["platform"]),
        "user_id": row["user_id"],
        "account_id": account_id,
        "nickname": row.get("nickname") or "(未补全)",
        "avatar": row.get("avatar") or "",
        "fans": fans,
        "follows": row.get("follows"),
        "collected": row.get("collected"),
        "fans_wan": format_fans_wan(fans) if fans is not None else "—",
        "fans_tier": tier_label,
        "round_name": row.get("round_name") or "",
        "data_round": data_round,
        "status": row_status,
        "status_label": STATUS_LABELS.get(row_status, row_status),
        "round_status": normalize_status(row.get("round_status") or row.get("status")),
        "round_status_label": STATUS_LABELS.get(
            normalize_status(row.get("round_status") or row.get("status")),
            normalize_status(row.get("round_status") or row.get("status")),
        ),
        "cooperation_status": cooperation_status,
        "cooperation_status_label": (
            _cooperation_label(cooperation_status) if cooperation_status else ""
        ),
        "cooperation_updated_at": row.get("cooperation_updated_at") or "",
        "cooperation_count": int(row.get("cooperation_count") or 0),
        "cooperation_last_completed_at": row.get("cooperation_last_completed_at") or "",
        "cooperation_case_id": row.get("cooperation_case_id"),
        "updated_at": row.get("updated_at") or "",
        "enrichment_status": row.get("enrichment_status") or "not_fetched",
        "match_direction": row.get("match_direction") or "",
        "posted": row.get("posted"),
        "liked": row.get("liked"),
        "interaction": row.get("interaction"),
        "tags": "、".join(tags),
        "auto_tags": auto_tags,
        "tags_manual": row.get("tags_manual") or "",
        "profile_url": row.get("profile_url") or "",
        "red_id": row.get("red_id") or "",
        "top_title": row.get("top_title") or "",
        "top_url": row.get("top_url") or "",
        "top_liked": row.get("top_liked"),
        "source_keywords": source_keywords,
        "source_labels": source_labels,
        "matched_keywords": "、".join(source_keywords),
        "source": "、".join(source_labels),
        "remark": row.get("remark") or "",
        "owner_user_id": row.get("owner_user_id"),
        "owner_username": row.get("owner_username") or "",
        "claimed_at": row.get("claimed_at"),
        "cooperation_platform": row.get("cooperation_platform") or "",
        "raw_title": row.get("raw_title") or "",
        "sample_notes": row.get("sample_notes") or 0,
        "sample_likes": row.get("sample_likes") or 0,
        "sample_collected": row.get("sample_collected") or 0,
        "sample_comments": row.get("sample_comments") or 0,
        "sample_interactions": row.get("sample_interactions") or 0,
        "ip_location": row.get("ip_location") or "",
        "verify_info": row.get("verify_info") or "",
        "fetched_at": row.get("fetched_at") or "",
    }
    if not _round_names_visible():
        payload["round_name"] = ""
        payload["data_round"] = ""
    return payload


def _queue_item(row: dict[str, Any]) -> dict[str, Any]:
    status = normalize_status(row.get("status"))
    payload = {
        "key": f"{row['platform']}|{row['user_id']}",
        "round_id": int(row["round_id"]),
        "round_name": row.get("round_name") or "",
        "platform": row["platform"],
        "platform_label": PLATFORM_LABELS.get(row["platform"], row["platform"]),
        "user_id": row["user_id"],
        "nickname": row.get("nickname") or "(未补全)",
        "avatar": row.get("avatar") or "",
        "fans": row.get("fans"),
        "fans_wan": format_fans_wan(row.get("fans")) if row.get("fans") is not None else "—",
        "interaction": row.get("interaction"),
        "tier": row.get("derived_tier") or "—",
        "sample_notes": int(row.get("sample_notes") or 0),
        "sample_interactions": int(row.get("sample_interactions") or 0),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "owner_user_id": row.get("owner_user_id"),
        "owner_username": row.get("owner_username") or "",
        "match_direction": row.get("match_direction") or "",
        "source_keywords": [str(item) for item in _json_list(row.get("source_keywords"))],
        "updated_at": row.get("updated_at") or "",
    }
    if not _round_names_visible():
        payload["round_name"] = ""
    return payload


def _efficiency_item(row: dict[str, Any]) -> dict[str, Any]:
    platform = str(row.get("platform") or "xhs")
    status = str(row.get("status") or "")
    payload = {
        **row,
        "platform_label": PLATFORM_LABELS.get(platform, platform),
        "status_label": ROUND_STATUS_LABELS.get(status, status),
    }
    if not _round_names_visible():
        payload["name"] = ""
        payload.pop("cost", None)
    return payload


def _lead_dict(row: dict[str, Any]) -> dict[str, Any]:
    status = normalize_lead_status(row.get("status"))
    payload = {
        "id": int(row["id"]),
        "platform": row.get("platform") or "xhs",
        "platform_label": PLATFORM_LABELS.get(row.get("platform") or "xhs", row.get("platform") or "xhs"),
        "nickname": row.get("nickname") or "",
        "account_id": row.get("account_id") or "",
        "platform_handle": row.get("platform_handle") or "",
        "identity_status": row.get("identity_status") or "unverified",
        "identity_status_label": LEAD_IDENTITY_LABELS.get(
            row.get("identity_status") or "unverified",
            row.get("identity_status") or "unverified",
        ),
        "identity_confidence": row.get("identity_confidence") or "",
        "identity_provider": row.get("identity_provider") or "",
        "identity_checked_at": row.get("identity_checked_at"),
        "identity_note": row.get("identity_note") or "",
        "profile_url": row.get("profile_url") or "",
        "raw_title": row.get("raw_title") or "",
        "source": row.get("source") or "",
        "source_label": LEAD_SOURCE_LABELS.get(row.get("source") or "", row.get("source") or ""),
        "source_round_id": row.get("source_round_id"),
        "source_round_name": row.get("source_round_name") or "",
        "owner_user_id": row.get("owner_user_id"),
        "owner_username": row.get("owner_username") or "",
        "status": status,
        "status_label": LEAD_STATUS_LABELS.get(status, status),
        "legacy_user_id": row.get("legacy_user_id") or "",
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "converted_platform": row.get("converted_platform") or "",
        "converted_user_id": row.get("converted_user_id") or "",
        "converted_at": row.get("converted_at"),
        "matched_platform": row.get("matched_platform") or "",
        "matched_user_id": row.get("matched_user_id") or "",
        "matched_round_id": row.get("matched_round_id"),
        "matched_round_name": row.get("matched_round_name") or "",
        "matched_at": row.get("matched_at"),
        "match_confidence": row.get("match_confidence") or "",
    }
    if not _round_names_visible():
        payload["source_round_name"] = ""
        payload["matched_round_name"] = ""
        if payload["match_confidence"] == "round_account_id":
            payload["match_confidence"] = "auto"
        if payload["identity_confidence"] == "round_account_id":
            payload["identity_confidence"] = "auto"
    return payload


def _lead_detail(db: Database, lead_id: int) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    payload = _lead_dict(lead)
    payload["raw_json"] = _safe_json_object(lead.get("raw_json"))
    rounds = db.lead_rounds(lead_id)
    attempts = db.lead_attempts(lead_id)
    identity_events = db.lead_identity_events(lead_id)
    if not _round_names_visible():
        for item in rounds:
            item["round_name"] = ""
            if "data_round" in item:
                item["data_round"] = ""
        for item in (*attempts, *identity_events):
            if item.get("confidence") == "round_account_id":
                item["confidence"] = "auto"
    payload["rounds"] = rounds
    payload["attempts"] = attempts
    payload["identity_events"] = identity_events
    matched_platform = str(lead.get("matched_platform") or "")
    matched_user_id = str(lead.get("matched_user_id") or "")
    if matched_platform and matched_user_id:
        creator = db.get_creator(matched_platform, matched_user_id)
        payload["matched_creator"] = (
            {
                "platform": matched_platform,
                "user_id": matched_user_id,
                "nickname": (creator or {}).get("nickname") or "",
                "avatar": (creator or {}).get("avatar") or "",
                "fans": (creator or {}).get("fans"),
                "profile_url": (creator or {}).get("profile_url") or "",
            }
            if creator
            else None
        )
    else:
        payload["matched_creator"] = None
    return payload


def _checked_raw_json(value: Any) -> Any:
    if value is None:
        return {}
    text = json.dumps(value, ensure_ascii=False)
    if len(text.encode("utf-8")) > 100_000:
        raise HTTPException(400, "原始数据字段过大")
    return value


def _validate_owner(db: Database, owner_user_id: int | None) -> None:
    if owner_user_id is None:
        return
    user = db.get_user(int(owner_user_id))
    if not user or user.get("disabled"):
        raise HTTPException(400, "负责人不存在或已禁用")
    if user.get("role") != "admin" and not user.get("can_edit_leads"):
        raise HTTPException(400, "负责人必须拥有线索权限")


def _ensure_lead_owner(
    lead: dict[str, Any], actor: dict[str, Any], action: str = "处理"
) -> None:
    if actor.get("role") == "admin":
        return
    owner = lead.get("owner_user_id")
    if owner is not None and int(owner) != int(actor["id"]):
        raise HTTPException(403, f"只能{action}自己负责的线索")


def _check_owner_change(actor: dict[str, Any], owner_user_id: int | None) -> None:
    if actor.get("role") != "admin" and owner_user_id not in (None, actor["id"]):
        raise HTTPException(403, "只能将线索分配给自己")


def _validate_lead_status(value: str | None) -> str | None:
    if value is None:
        return None
    code = normalize_lead_status(value, default="")
    if code not in LEAD_STATUS_CODES:
        raise HTTPException(400, "无效的线索状态")
    return code


def _detail_payload(db: Database, round_id: int, platform: str, user_id: str) -> dict[str, Any]:
    rows = db.creator_table(round_id, platform=platform, user_id=user_id)
    row = rows[0] if rows else None
    if not row:
        raise HTTPException(404, "达人不存在")
    detail = db.creator_detail(round_id, platform, user_id)
    if not detail:
        raise HTTPException(404, "达人详情不存在")
    payload = _creator_dict(row)
    payload.update(
        {
            "desc": detail.get("desc") or "",
            "creator_tags": _json_list(detail.get("tags_json")),
            "raw_json": _safe_json_object(detail.get("raw_json")),
            "cooperation_cases": [
                {
                    **item,
                    "status_label": _cooperation_label(item.get("status")),
                }
                for item in db.cooperation_cases(platform, user_id)
            ],
            "cooperation_summary": db.cooperation_summary(platform, user_id),
            "cooperation_history": db.cooperation_history(platform, user_id, 20),
            "notes": [
                {
                    "note_id": note.get("note_id"),
                    "title": note.get("title") or "",
                    "type": note.get("type") or "",
                    "published_at": note.get("published_at"),
                    "liked": note.get("liked") or 0,
                    "collected": note.get("collected") or 0,
                    "comments": note.get("comments") or 0,
                    "shares": note.get("shares") or 0,
                    "url": note.get("url") or "",
                    "keywords": note.get("keywords") or [],
                }
                for note in detail.get("notes") or []
            ],
        }
    )
    return payload


def _safe_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _validated_status(value: str | None) -> str | None:
    if value is None:
        return None
    code = normalize_status(value, default="")
    if code not in STATUS_CODES:
        raise HTTPException(400, "无效的采用状态")
    return code


def _validated_cooperation_status(
    value: str | None, *, allow_empty: bool = False
) -> str | None:
    if value is None:
        return None
    if not str(value).strip():
        if allow_empty:
            return ""
        raise HTTPException(400, "无效的合作状态")
    code = normalize_cooperation_status(value, default="")
    if code not in COOPERATION_STATUS_CODES:
        raise HTTPException(400, "无效的合作状态")
    return code


KeywordText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class UpdateCreatorRequest(BaseModel):
    status: str | None = Field(None, max_length=40)
    cooperation_status: str | None = Field(None, max_length=40)
    match_direction: str | None = Field(None, max_length=100)
    tags_manual: str | None = Field(None, max_length=500)
    remark: str | None = Field(None, max_length=2000)
    expected_updated_at: str | None = Field(None, max_length=40)
    expected_status: str | None = Field(None, max_length=40)
    expected_cooperation_status: str | None = Field(None, max_length=40)
    expected_cooperation_updated_at: str | None = Field(None, max_length=40)


class BatchItem(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    user_id: str = Field(min_length=1, max_length=100)
    expected_updated_at: str | None = Field(None, max_length=40)
    expected_status: str | None = Field(None, max_length=40)
    expected_cooperation_status: str | None = Field(None, max_length=40)
    expected_cooperation_updated_at: str | None = Field(None, max_length=40)


class BatchUpdateRequest(UpdateCreatorRequest):
    items: list[BatchItem] = Field(max_length=500)
    set_owner: bool = False
    owner_user_id: int | None = None


class BatchClaimRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=500)


class CooperationUpdateRequest(BaseModel):
    status: str = Field(max_length=40)
    expected_status: str | None = Field(None, max_length=40)
    expected_updated_at: str | None = Field(None, max_length=40)


class CooperationBatchItem(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    user_id: str = Field(min_length=1, max_length=100)
    expected_status: str | None = Field(None, max_length=40)
    expected_updated_at: str | None = Field(None, max_length=40)


class CooperationBatchRequest(BaseModel):
    status: str = Field(max_length=40)
    items: list[CooperationBatchItem] = Field(min_length=1, max_length=500)


class CooperationCaseCreateRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    user_id: str = Field(min_length=1, max_length=100)
    status: str | None = Field(None, max_length=40)
    note: str | None = Field(None, max_length=5000)


class CooperationCaseStatusRequest(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    note: str | None = Field(None, max_length=5000)
    expected_updated_at: str | None = Field(None, max_length=60)


class CooperationCaseSummaryRequest(BaseModel):
    summary: str = Field("", max_length=5000)
    expected_updated_at: str | None = Field(None, max_length=60)


class CooperationCaseNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=5000)


class CooperationWorkRequest(BaseModel):
    platform: str = Field("", max_length=40)
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field("", max_length=500)
    published_at: str = Field("", max_length=40)
    note: str = Field("", max_length=2000)


class CooperationCompleteRequest(BaseModel):
    note: str | None = Field(None, max_length=5000)
    expected_updated_at: str | None = Field(None, max_length=60)
    works: list[CooperationWorkRequest] = Field(default_factory=list, max_length=50)


class CooperationCorrectionRequest(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=5000)


class LeadCreateRequest(BaseModel):
    platform: str = Field("xhs", min_length=1, max_length=20)
    nickname: str = Field("", max_length=200)
    account_id: str = Field("", max_length=200)
    platform_handle: str = Field("", max_length=200)
    profile_url: str = Field("", max_length=2000)
    raw_title: str = Field("", max_length=500)
    source: str = Field("manual", max_length=30)
    source_round_id: int | None = None
    owner_user_id: int | None = None
    raw_json: dict[str, Any] | None = None


class LeadUpdateRequest(BaseModel):
    platform: str | None = Field(None, min_length=1, max_length=20)
    nickname: str | None = Field(None, max_length=200)
    account_id: str | None = Field(None, max_length=200)
    platform_handle: str | None = Field(None, max_length=200)
    profile_url: str | None = Field(None, max_length=2000)
    raw_title: str | None = Field(None, max_length=500)
    source: str | None = Field(None, max_length=30)
    source_round_id: int | None = None
    owner_user_id: int | None = None
    status: str | None = Field(None, max_length=30)


class LeadVerifyRequest(BaseModel):
    provider: str = Field("auto", min_length=1, max_length=20)
    account_id: str | None = Field(None, max_length=200)
    platform_handle: str | None = Field(None, max_length=200)
    profile_url: str | None = Field(None, max_length=2000)
    apply: bool = False
    confirm_mismatch: bool = False


class LeadBulkRequest(BaseModel):
    items: list[LeadCreateRequest] = Field(min_length=1, max_length=500)


class LeadAssignRequest(BaseModel):
    owner_user_id: int | None = None


class CreatorAssignRequest(BaseModel):
    owner_user_id: int | None = None


class LeadConvertRequest(BaseModel):
    account_id: str = Field("", max_length=200)
    platform_handle: str = Field("", max_length=200)
    profile_url: str = Field("", max_length=2000)
    fetch: bool = True
    provider: str = Field("auto", min_length=1, max_length=20)
    confirm_mismatch: bool = False


class CreateRoundRequest(BaseModel):
    keywords: list[KeywordText] = Field(min_length=1, max_length=50)
    category: str | None = Field(None, max_length=40)
    pages: int = Field(1, ge=1, le=20)
    max_creators: int | None = Field(20, ge=0, le=500)
    name: str | None = Field(None, max_length=100)
    platform: str = Field("xhs", min_length=1, max_length=20)
    provider: str = Field("auto", min_length=1, max_length=20)
    refresh_users: bool = False
    start: bool = True


class RunRoundRequest(BaseModel):
    refresh_users: bool = False
    force: bool = False
    provider: str | None = Field(None, min_length=1, max_length=20)


class PurgeRoundRequest(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=100)


class KeywordCreateRequest(BaseModel):
    word: KeywordText
    category: str | None = Field(None, max_length=40)
    enabled: bool = True


class KeywordUpdateRequest(BaseModel):
    category: str | None = Field(None, max_length=40)
    enabled: bool | None = None


class DataflowTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4000)
    verify: bool = True


class RnoteTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4000)
    verify: bool = True


class ProviderTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4000)
    verify: bool = True


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    role: str = Field(default="member", max_length=20)
    can_collect: bool = False
    can_edit_leads: bool = False


class UserUpdateRequest(BaseModel):
    role: str | None = Field(None, max_length=20)
    disabled: bool | None = None
    can_collect: bool | None = None
    can_edit_leads: bool | None = None


class UserResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "disabled": bool(user.get("disabled")),
        "can_collect": bool(user.get("can_collect")),
        "can_edit_leads": bool(user.get("can_edit_leads")),
        "must_change_password": bool(user.get("must_change_password")),
        "auth_source": user.get("auth_source") or "password",
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "last_login_at": user.get("last_login_at"),
    }


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if peer in settings.trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return peer[:64]


def _secure_request(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    proto = ""
    if peer in settings.trusted_proxies:
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return settings.cookie_secure or request.url.scheme == "https" or proto == "https"


def require_admin(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_collector(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user or (user.get("role") != "admin" and not user.get("can_collect")):
        raise HTTPException(403, "当前账号没有发起采集的权限，请联系管理员")
    return user


def require_lead_editor(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user or (
        user.get("role") != "admin" and not user.get("can_edit_leads")
    ):
        raise HTTPException(403, "当前账号没有维护线索的权限，请联系管理员")
    return user


def require_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def _set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    path = settings.output_dir / "codata.sqlite"
    if not path.exists():
        raise HTTPException(503, "数据库不可用")
    try:
        conn = sqlite3.connect(path, timeout=3)
        try:
            conn.execute("SELECT 1").fetchone()
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:
        raise HTTPException(503, "数据库不可用") from exc
    if version != SCHEMA_VERSION:
        raise HTTPException(503, "数据库 schema 版本与应用不兼容")
    return {"ok": True, "database": "ok", "schema_version": version}


@app.post("/api/auth/login")
def auth_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    username = normalize_username(payload.username)
    ip = _client_ip(request)
    user_agent = request.headers.get("user-agent", "")
    keys = [f"ip:{ip}", f"user:{username}:{ip}"]
    blocked = [key for key in keys if login_limiter.blocked(key)]
    if blocked:
        retry = max(login_limiter.retry_after(key) for key in blocked)
        raise HTTPException(
            429,
            "登录尝试过多，请稍后再试",
            headers={"Retry-After": str(retry or 1)},
        )

    user = db.get_user_by_username(username)
    if not user:
        verify_dummy_password(payload.password)
    valid = bool(
        user
        and not user.get("disabled")
        and user.get("auth_source") == "password"
        and verify_password(payload.password, user.get("password_hash"))
    )
    if not valid:
        for key in keys:
            login_limiter.fail(key)
        db.audit(
            None,
            "auth.login_failed",
            username,
            ip,
            user_agent,
            {"reason": "invalid_credentials"},
        )
        raise HTTPException(401, "用户名或密码错误")

    for key in keys:
        login_limiter.reset(key)
    token = new_session_token()
    csrf_token = new_csrf_token()
    db.create_session(
        user["id"],
        hash_session_token(token),
        csrf_token,
        session_expiry(settings.session_days),
        ip,
        user_agent,
    )
    db.update_last_login(user["id"])
    db.audit(user["id"], "auth.login", username, ip, user_agent)
    _set_session_cookie(response, token, _secure_request(request))
    return {"user": _public_user(db.get_user(user["id"]) or user), "csrf_token": csrf_token}


@app.get("/api/auth/me")
def auth_me(request: Request, db: Database = Depends(get_db)) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return {"user": None, "csrf_token": ""}
    session = db.get_session(hash_session_token(token))
    if not session:
        return {"user": None, "csrf_token": ""}
    return {
        "user": _public_user(session["user"]),
        "csrf_token": session["session"]["csrf_token"],
    }


@app.post("/api/auth/logout")
def auth_logout(
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    session = getattr(request.state, "session", None)
    user = getattr(request.state, "user", None)
    if session:
        db.delete_session(session["token_hash"])
    if user:
        db.audit(user["id"], "auth.logout", user["username"], _client_ip(request), request.headers.get("user-agent", ""))
    _clear_session_cookie(response, _secure_request(request))
    return {"ok": True}


@app.post("/api/auth/change-password")
def auth_change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    current_user = getattr(request.state, "user", None)
    if not current_user:
        raise HTTPException(401, "请先登录")
    user = db.get_user(current_user["id"])
    if not user or not verify_password(payload.current_password, user.get("password_hash")):
        raise HTTPException(400, "当前密码不正确")
    if payload.new_password == payload.current_password:
        raise HTTPException(400, "新密码不能与当前密码相同")
    try:
        new_hash = hash_password(validate_password(payload.new_password))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    current_session = getattr(request.state, "session", None)
    db.update_user(user["id"], password_hash=new_hash, must_change_password=False)
    db.delete_user_sessions(
        user["id"],
        except_token_hash=current_session["token_hash"] if current_session else None,
    )
    db.audit(user["id"], "auth.password_changed", user["username"], _client_ip(request), request.headers.get("user-agent", ""))
    return {"ok": True}


@app.get("/api/users")
def list_users(
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    return [_public_user(user) for user in db.list_users()]


@app.post("/api/users")
def create_user(
    payload: UserCreateRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    role = (payload.role or "member").strip().lower()
    if role not in ROLES:
        raise HTTPException(400, "无效的角色")
    try:
        username = validate_username(payload.username)
        password_hash = hash_password(validate_password(payload.password))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        user_id = db.create_user(
            username,
            password_hash,
            role=role,
            can_collect=bool(payload.can_collect),
            can_edit_leads=bool(payload.can_edit_leads),
            must_change_password=True,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        admin["id"],
        "user.created",
        username,
        _client_ip(request),
        request.headers.get("user-agent", ""),
        {
            "role": role,
            "can_collect": bool(payload.can_collect),
            "can_edit_leads": bool(payload.can_edit_leads),
        },
    )
    return _public_user(db.get_user(user_id) or {})


@app.patch("/api/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    role = payload.role.strip().lower() if payload.role is not None else None
    if role is not None and role not in ROLES:
        raise HTTPException(400, "无效的角色")
    if target["id"] == admin["id"]:
        if payload.disabled:
            raise HTTPException(400, "不能停用自己的账号")
        if role is not None and role != target["role"]:
            raise HTTPException(400, "不能修改自己的角色，请由其他管理员操作")
    changes: dict[str, dict[str, Any]] = {}
    if role is not None and role != target["role"]:
        changes["role"] = {"from": target["role"], "to": role}
    if payload.disabled is not None and bool(payload.disabled) != bool(target["disabled"]):
        changes["disabled"] = {
            "from": bool(target["disabled"]),
            "to": bool(payload.disabled),
        }
    if payload.can_collect is not None and bool(payload.can_collect) != bool(
        target["can_collect"]
    ):
        changes["can_collect"] = {
            "from": bool(target["can_collect"]),
            "to": bool(payload.can_collect),
        }
    if payload.can_edit_leads is not None and bool(payload.can_edit_leads) != bool(
        target["can_edit_leads"]
    ):
        changes["can_edit_leads"] = {
            "from": bool(target["can_edit_leads"]),
            "to": bool(payload.can_edit_leads),
        }
    if not changes:
        return _public_user(target)
    try:
        db.update_user_with_admin_guard(
            target["id"],
            role=role,
            disabled=payload.disabled,
            can_collect=payload.can_collect,
            can_edit_leads=payload.can_edit_leads,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if changes.get("disabled", {}).get("to") or "role" in changes:
        db.delete_user_sessions(target["id"])
    db.audit(
        admin["id"],
        "user.updated",
        target["username"],
        _client_ip(request),
        request.headers.get("user-agent", ""),
        {"changes": changes},
    )
    return _public_user(db.get_user(target["id"]) or target)


@app.post("/api/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    payload: UserResetPasswordRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target["id"] == admin["id"]:
        raise HTTPException(400, "请通过右上角“修改密码”修改自己的密码")
    try:
        password_hash = hash_password(validate_password(payload.new_password))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.update_user(
        target["id"],
        password_hash=password_hash,
        must_change_password=True,
    )
    db.delete_user_sessions(target["id"])
    db.audit(
        admin["id"],
        "user.password_reset",
        target["username"],
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return {"ok": True}


@app.post("/api/users/{user_id}/revoke-sessions")
def revoke_user_sessions(
    user_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    kept_current = target["id"] == admin["id"]
    current_session = getattr(request.state, "session", None)
    except_token_hash = (
        current_session.get("token_hash") if kept_current and current_session else None
    )
    revoked = db.delete_user_sessions(
        target["id"], except_token_hash=except_token_hash
    )
    db.audit(
        admin["id"],
        "user.sessions_revoked",
        target["username"],
        _client_ip(request),
        request.headers.get("user-agent", ""),
        {"revoked": revoked, "kept_current": kept_current},
    )
    return {"ok": True, "revoked": revoked, "kept_current": kept_current}


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    return {
        "statuses": STATUS_DEFS,
        "default_status": "pending",
        "cooperation_statuses": COOPERATION_STATUS_DEFS,
        "cooperation_attachment_categories": [
            {"code": code, "label": label}
            for code, label in COOPERATION_ATTACHMENT_CATEGORIES.items()
        ],
        "lead_statuses": LEAD_STATUS_DEFS,
        "lead_identity_statuses": LEAD_IDENTITY_DEFS,
        "lead_sources": [
            {"code": code, "label": LEAD_SOURCE_LABELS[code]} for code in LEAD_SOURCE_CODES
        ],
        "platforms": [
            {"code": code, "label": label} for code, label in PLATFORM_LABELS.items()
        ],
    }


def _settings_payload(include_paths: bool = False) -> dict[str, Any]:
    registry = None
    platforms_payload: list[dict[str, Any]] = []
    providers_payload: list[dict[str, Any]] = []
    xhs_cost = XHS_COST
    credentials_ok = bool(settings.host and settings.token)
    try:
        registry = load_registry(settings)
        platforms_payload = registry.public_platforms()
        providers_payload = registry.public_providers()
        option = registry.cheapest("xhs")
        credentials_ok = bool(option)
        if option:
            xhs_cost = option.search.price.tokens(registry.usd_cny, registry.token_cny)
    except PlatformConfigError:
        credentials_ok = False
    return {
        "host": settings.host,
        "token_configured": bool(settings.token),
        "rnote_host": settings.rnote_host,
        "rnote_token_configured": bool(settings.rnote_api_key),
        "output_dir": str(settings.output_dir) if include_paths else "",
        "cache_days": settings.cache_days,
        "xhs_cost": xhs_cost,
        "douyin_cost": DOUYIN_COST,
        "token_cny": settings.token_cny,
        "usd_cny": settings.usd_cny,
        "credentials_ok": credentials_ok,
        "providers": providers_payload,
        "platforms": platforms_payload,
        "default_provider": platforms_payload[0].get("default_provider") if platforms_payload else None,
    }


@app.get("/api/settings")
def api_settings(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not _round_names_visible():
        return {
            "host": "",
            "token_configured": False,
            "rnote_host": "",
            "rnote_token_configured": False,
            "output_dir": "",
            "cache_days": 0,
            "xhs_cost": 0,
            "douyin_cost": 0,
            "token_cny": 0,
            "usd_cny": 0,
            "credentials_ok": False,
            "providers": [],
            "platforms": [],
            "default_provider": None,
        }
    return _settings_payload(include_paths=bool(user and user.get("role") == "admin"))


def _fetch_balance(target: Settings, provider: str = "dataflow") -> dict[str, Any]:
    if provider == "rnote":
        with RnoteClient(target) as client:
            payload = RnoteAPI(client).balance()
        data = unwrap_data(payload)
        raw = data.get("balance") if isinstance(data, dict) else None
        if raw is None and isinstance(data, dict) and isinstance(data.get("data"), dict):
            raw = data["data"].get("balance")
        try:
            remaining_native = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            remaining_native = None
        token_value = (
            int(remaining_native * target.usd_cny / target.token_cny)
            if remaining_native is not None and target.token_cny > 0
            else None
        )
        return {
            "remaining": token_value,
            "remaining_native": remaining_native,
            "currency": "USD",
            "tokens": token_value,
            "text": f"剩余 ${remaining_native:.6f}（约 {token_value} token）"
            if remaining_native is not None
            else "验证成功",
        }
    with DataFlowClient(target) as client:
        payload = DataFlowAPI(client).balance()
    data = unwrap_data(payload)
    raw_remaining = data.get("remaining_calls") if isinstance(data, dict) else None
    remaining_tokens = as_optional_int(raw_remaining)
    return {
        "remaining": remaining_tokens,
        "remaining_native": remaining_tokens,
        "currency": "token",
        "tokens": remaining_tokens,
        "text": f"剩余 {remaining_tokens} token（后台约 10 分钟刷新一次）" if remaining_tokens is not None else "验证成功",
    }


@app.put("/api/settings/dataflow-token")
@serialized_settings_update
def update_dataflow_token(
    payload: DataflowTokenRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    global settings
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(400, "访问令牌不能为空")
    if len(token) > 4000:
        raise HTTPException(400, "访问令牌过长")
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise HTTPException(400, "访问令牌不能包含换行或控制字符")

    candidate = replace(settings, token=token)
    balance = None
    if payload.verify:
        if not candidate.host:
            raise HTTPException(400, "请先配置 DATAFLOW_HOST 再验证令牌")
        try:
            balance = _fetch_balance(candidate)
        except Exception as exc:
            raise HTTPException(400, f"令牌验证失败：{_public_error(exc)}") from exc
        if (
            isinstance(balance, dict)
            and balance.get("remaining") is None
            and balance.get("remaining_native") is None
        ):
            raise HTTPException(400, "令牌验证失败：余额接口未返回有效余额")

    try:
        set_key(str(ENV_PATH), "DATAFLOW_TOKEN", token, quote_mode="always")
    except Exception as exc:
        raise HTTPException(500, f"保存访问令牌失败：{exc}") from exc
    os.environ["DATAFLOW_TOKEN"] = token
    settings = candidate
    db.audit(admin["id"], "settings.dataflow_token_updated", "dataflow", _client_ip(request), request.headers.get("user-agent", ""))
    return {**_settings_payload(include_paths=True), "balance": balance}


@app.put("/api/settings/rnote-token")
@serialized_settings_update
def update_rnote_token(
    payload: RnoteTokenRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    global settings
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(400, "访问令牌不能为空")
    if len(token) > 4000:
        raise HTTPException(400, "访问令牌过长")
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise HTTPException(400, "访问令牌不能包含换行或控制字符")

    candidate = replace(settings, rnote_api_key=token)
    balance = None
    if payload.verify:
        try:
            balance = _fetch_balance(candidate, "rnote")
        except Exception as exc:
            raise HTTPException(400, f"令牌验证失败：{_public_error(exc)}") from exc
        if (
            isinstance(balance, dict)
            and balance.get("remaining") is None
            and balance.get("remaining_native") is None
        ):
            raise HTTPException(400, "令牌验证失败：余额接口未返回有效余额")

    try:
        set_key(str(ENV_PATH), "RNOTE_API_KEY", token, quote_mode="always")
    except Exception as exc:
        raise HTTPException(500, f"保存访问令牌失败：{exc}") from exc
    os.environ["RNOTE_API_KEY"] = token
    settings = candidate
    db.audit(
        admin["id"],
        "settings.rnote_token_updated",
        "rnote",
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return {**_settings_payload(include_paths=True), "balance": balance}


@app.put("/api/settings/providers/{provider_id}/token")
@serialized_settings_update
def update_provider_token(
    provider_id: str,
    payload: ProviderTokenRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    global settings
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(400, "访问令牌不能为空")
    if len(token) > 4000:
        raise HTTPException(400, "访问令牌过长")
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise HTTPException(400, "访问令牌不能包含换行或控制字符")
    try:
        registry = load_registry(settings)
        provider = registry.provider(provider_id)
    except PlatformConfigError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not provider.api_key_env:
        raise HTTPException(400, "该服务商不支持在设置页更新令牌")
    verified = False
    balance = None
    if payload.verify:
        try:
            candidate_settings = load_settings(require_credentials=False)
            candidate_registry = load_registry(
                candidate_settings,
                credential_overrides={provider.api_key_env: token},
            )
            option = None
            platform_code = "xhs"
            for platform in candidate_registry.platforms.values():
                if provider_id in platform.providers:
                    option = platform.providers[provider_id]
                    platform_code = platform.code
                    break
            if option is None:
                raise RuntimeError("未找到该服务商的平台配置")
            candidate = build_provider(candidate_settings, option, platform_code)
            try:
                if candidate.supports_balance:
                    result = candidate.balance()
                    if (
                        result.remaining_tokens is None
                        and result.remaining_native is None
                    ):
                        raise RuntimeError("余额接口未返回有效余额")
                    balance = {
                        "remaining": result.remaining_tokens,
                        "remaining_native": float(result.remaining_native)
                        if result.remaining_native is not None
                        else None,
                        "currency": result.currency,
                        "tokens": result.remaining_tokens,
                        "text": result.text,
                    }
                    verified = True
            finally:
                candidate.close()
        except Exception as exc:
            raise HTTPException(400, "令牌校验失败，请检查后重试") from exc
    try:
        set_key(str(ENV_PATH), provider.api_key_env, token, quote_mode="always")
    except Exception as exc:
        raise HTTPException(500, "保存访问令牌失败") from exc
    os.environ[provider.api_key_env] = token
    settings = load_settings(require_credentials=False)
    db.audit(
        admin["id"],
        "settings.provider_token_updated",
        provider_id,
        _client_ip(request),
        request.headers.get("user-agent", ""),
        details={"verified": verified},
    )
    return {**_settings_payload(include_paths=True), "balance": balance, "verified": verified}


def _fetch_extra_balance(provider_id: str) -> dict[str, Any]:
    registry = load_registry(settings)
    provider = registry.provider(provider_id)
    if not provider.configured:
        return {"remaining": None, "text": "未配置 API Key", "currency": provider.balance_currency}
    option = None
    platform_code = "xhs"
    for platform in registry.platforms.values():
        if provider_id in platform.providers:
            option = platform.providers[provider_id]
            platform_code = platform.code
            break
    if option is None:
        return {"remaining": None, "text": "未找到该服务商的平台配置", "currency": provider.balance_currency}
    candidate = build_provider(settings, option, platform_code)
    try:
        if not getattr(candidate, "supports_balance", False):
            return {"remaining": None, "text": f"{provider.label} 未提供余额接口", "currency": provider.balance_currency}
        result = candidate.balance()
        return {
            "remaining": result.remaining_tokens,
            "remaining_native": float(result.remaining_native)
            if result.remaining_native is not None
            else None,
            "currency": result.currency,
            "tokens": result.remaining_tokens,
            "text": result.text,
        }
    finally:
        candidate.close()


@app.get("/api/balance")
def api_balance(
    http_request: Request, provider: str = "dataflow"
) -> dict[str, Any]:
    actor = require_user(http_request)
    _enforce_action_limit(f"balance:{actor['id']}")
    provider = provider.strip().lower()
    if provider in {"dataflow", "rnote"}:
        if provider == "rnote":
            if not settings.rnote_api_key:
                return {"remaining": None, "text": "未配置 RNOTE_API_KEY", "currency": "USD"}
        elif not settings.host or not settings.token:
            return {"remaining": None, "text": "未配置 Host/Token", "currency": "token"}
        try:
            return _fetch_balance(settings, provider)
        except Exception as exc:
            return {
                "remaining": None,
                "text": f"查询失败：{_public_error(exc)}",
                "currency": "USD" if provider == "rnote" else "token",
            }
    try:
        return _fetch_extra_balance(provider)
    except Exception as exc:
        return {
            "remaining": None,
            "text": f"查询失败：{_public_error(exc)}",
            "currency": "",
        }


@app.get("/api/rounds")
def list_rounds(
    include_archived: bool = False,
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    jobs = db.latest_jobs_by_round()
    visible = _round_names_visible()
    items: list[dict[str, Any]] = []
    for row in db.list_rounds(include_archived=include_archived):
        payload = _round_dict(row)
        payload["job"] = _job_dict(jobs.get(int(row["id"]))) if visible else None
        items.append(payload)
    return items


@app.post("/api/rounds")
def create_round(
    request: CreateRoundRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    collector: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    words = []
    for word in request.keywords:
        word = str(word).strip()
        if word and word not in words:
            words.append(word)
    if not words:
        raise HTTPException(400, "至少需要一个关键词")
    platform_code = request.platform.strip().lower()
    provider_code = request.provider.strip().lower()
    try:
        registry = load_registry(settings)
        platform = registry.platform(platform_code)
        if provider_code not in {"auto", "cheapest", "default", *platform.providers.keys()}:
            raise PlatformConfigError(f"未知服务商: {provider_code}")
        if request.start:
            registry.resolve(platform_code, provider_code)
    except PlatformConfigError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        round_id = db.create_round(
            words,
            pages=max(1, int(request.pages)),
            max_creators=int(request.max_creators) if request.max_creators else None,
            name=(request.name or "").strip() or None,
            category=(request.category or "").strip() or None,
            platform=platform_code,
            provider=provider_code,
        )
    except ValueError as exc:
        text = str(exc)
        message = (
            "轮次名称已存在"
            if "UNIQUE constraint failed: rounds.name" in text
            else text
        )
        raise HTTPException(400, message) from exc
    except Exception as exc:
        text = str(exc)
        message = (
            "轮次名称已存在"
            if "UNIQUE constraint failed: rounds.name" in text
            else "创建轮次失败，请检查参数"
        )
        raise HTTPException(400, message) from exc

    job_id = None
    if request.start:
        try:
            job_id, _ = start_round_job(
                db,
                settings,
                round_id,
                refresh_users=request.refresh_users,
                created_by=int(collector["id"]),
            )
        except JobBusyError as exc:
            db.delete_round(round_id)
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            db.delete_round(round_id)
            raise HTTPException(500, "任务启动失败，请稍后重试") from exc
    db.audit(
        collector["id"],
        "round.created",
        str(round_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"keywords": words, "platform": platform_code, "provider": provider_code},
    )
    return {"round": _round_payload(db, round_id), "job_id": job_id}


@app.get("/api/rounds/{round_id}")
def get_round(round_id: int, db: Database = Depends(get_db)) -> dict[str, Any]:
    return _round_payload(db, round_id)


@app.get("/api/rounds/{round_id}/impact")
def round_impact(
    round_id: int,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        return db.round_impact(round_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/rounds/{round_id}/archive")
def archive_round(
    round_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        db.archive_round(round_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        admin["id"],
        "round.archived",
        str(round_id),
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return _round_payload(db, round_id)


@app.post("/api/rounds/{round_id}/restore")
def restore_round(
    round_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        db.restore_round(round_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.audit(
        admin["id"],
        "round.restored",
        str(round_id),
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return _round_payload(db, round_id)


@app.delete("/api/rounds/{round_id}")
def purge_round(
    round_id: int,
    payload: PurgeRoundRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    round_row = db.get_round(round_id)
    if not round_row:
        raise HTTPException(404, "轮次不存在")
    if (payload.confirm_name or "").strip() != str(round_row.get("name") or ""):
        raise HTTPException(400, "轮次名称确认不匹配")
    impact = db.round_impact(round_id)
    if not impact["can_purge"]:
        if impact["jobs_active"]:
            raise HTTPException(400, "该轮次仍有运行中的任务，请先取消")
        raise HTTPException(400, "该轮次仍关联线索，不能永久删除；请使用归档")
    backup = db.backup(f"round-{round_id}-delete")
    try:
        result = db.delete_round(round_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        admin["id"],
        "round.purged",
        str(round_id),
        _client_ip(request),
        request.headers.get("user-agent", ""),
        details={"impact": impact, "backup": str(backup)},
    )
    db.prune_backups(
        settings.backup_retention_days, settings.backup_retention_count, apply=True
    )
    return {**result, "backup": str(backup), "impact": impact}


@app.post("/api/rounds/{round_id}/run")
def run_round(
    round_id: int,
    request: RunRoundRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    collector: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    round_row = db.get_round(round_id)
    if not round_row:
        raise HTTPException(404, "轮次不存在")
    if round_row.get("archived_at"):
        raise HTTPException(400, "已归档轮次不能运行采集，请先恢复")
    previous_provider = round_row.get("provider") or "auto"
    platform_code = str(round_row.get("platform") or "xhs")
    provider_code = (request.provider or round_row.get("provider") or "auto").strip().lower()
    try:
        registry = load_registry(settings)
        registry.resolve(platform_code, provider_code)
        if provider_code not in {"auto", "cheapest", "default"}:
            db.update_round(round_id, provider=provider_code)
        elif request.provider:
            db.update_round(round_id, provider=provider_code)
    except PlatformConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        job_id, log_path = start_round_job(
            db,
            settings,
            round_id,
            refresh_users=request.refresh_users,
            force=request.force,
            created_by=int(collector["id"]),
        )
    except JobBusyError as exc:
        db.update_round(round_id, provider=previous_provider)
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        db.update_round(round_id, provider=previous_provider)
        raise HTTPException(500, "任务启动失败，请稍后重试") from exc
    db.audit(
        collector["id"],
        "round.started",
        str(round_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"job_id": job_id, "provider": provider_code, "force": request.force},
    )
    return {"job_id": job_id, "log": str(log_path), "job": _job_dict(db.latest_job(round_id))}


def _validated_statuses(value: str | None) -> list[str]:
    codes: list[str] = []
    for item in _csv(value):
        code = normalize_status(item, default="")
        if code not in STATUS_CODES:
            raise HTTPException(400, "无效的采用状态筛选")
        codes.append(code)
    return codes


def _validated_cooperation_filters(value: str | None) -> list[str]:
    codes: list[str] = []
    for item in _csv(value):
        if item == "none":
            codes.append("none")
            continue
        code = normalize_cooperation_status(item, default="")
        if code not in COOPERATION_STATUS_CODES:
            raise HTTPException(400, "无效的合作状态筛选")
        codes.append(code)
    return codes


def _validated_owner_filter(value: str | None) -> str | None:
    if value in (None, "", "all"):
        return None
    if value == "unassigned" or value.isdigit():
        return value
    raise HTTPException(400, "无效的负责人筛选")


def _validated_lead_statuses(value: str | None) -> list[str]:
    codes: list[str] = []
    for item in _csv(value):
        code = normalize_lead_status(item, default="")
        if code not in LEAD_STATUS_CODES:
            raise HTTPException(400, "无效的线索状态筛选")
        codes.append(code)
    return codes


def _all_creator_filters(
    status: str | None,
    tier: str | None,
    direction: str | None,
    source: str | None,
    cooperation: str | None = None,
    cooperation_stale_days: int | None = None,
) -> dict[str, Any]:
    return {
        "statuses": _validated_statuses(status),
        "tiers": _csv(tier),
        "directions": _csv(direction),
        "sources": _csv(source),
        "cooperation": _validated_cooperation_filters(cooperation),
        "cooperation_stale_days": cooperation_stale_days,
    }


@app.get("/api/creators")
def list_all_creators(
    http_request: Request,
    round_id: int | None = None,
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    source_mode: str = Query("any", pattern="^(any|all)$"),
    label: str | None = None,
    owner: str | None = None,
    cooperation: str | None = None,
    cooperation_stale_days: int | None = Query(None, ge=1, le=365),
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    needs_action: bool = False,
    sort: str = "fans",
    order: str = "desc",
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(50, ge=1, le=200),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    filters = _all_creator_filters(
        status, tier, direction, source, cooperation, cooperation_stale_days
    )
    actor = require_user(http_request)
    result = db.query_creators(
        round_id,
        platform=platform,
        search=search,
        source_mode=source_mode,
        labels=_csv(label),
        owner=_validated_owner_filter(owner),
        min_fans=min_fans,
        max_fans=max_fans,
        only_unenriched=only_unenriched,
        needs_action=needs_action,
        action_actor_id=int(actor["id"]),
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        **filters,
    )
    result["items"] = [_creator_dict(row) for row in result["items"]]
    return result


@app.get("/api/creators/owners")
def creator_owners(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.creator_owner_options()


@app.get("/api/creators/facets")
def all_creator_facets(
    round_id: int | None = None,
    platform: str | None = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    return db.creator_facets(round_id, platform=platform)


@app.get("/api/rounds/{round_id}/creators")
def list_creators(
    round_id: int,
    http_request: Request,
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    source_mode: str = Query("any", pattern="^(any|all)$"),
    label: str | None = None,
    owner: str | None = None,
    cooperation: str | None = None,
    cooperation_stale_days: int | None = Query(None, ge=1, le=365),
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    needs_action: bool = False,
    sort: str = "fans",
    order: str = "desc",
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(50, ge=1, le=200),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    actor = require_user(http_request)
    result = db.query_creators(
        round_id,
        platform=platform,
        search=search,
        statuses=_validated_statuses(status),
        tiers=_csv(tier),
        directions=_csv(direction),
        sources=_csv(source),
        source_mode=source_mode,
        labels=_csv(label),
        owner=_validated_owner_filter(owner),
        cooperation=_validated_cooperation_filters(cooperation),
        cooperation_stale_days=cooperation_stale_days,
        min_fans=min_fans,
        max_fans=max_fans,
        only_unenriched=only_unenriched,
        needs_action=needs_action,
        action_actor_id=int(actor["id"]),
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    result["items"] = [_creator_dict(row) for row in result["items"]]
    return result


@app.get("/api/rounds/{round_id}/facets")
def creator_facets(
    round_id: int,
    platform: str | None = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    return db.creator_facets(round_id, platform=platform)


def _ensure_creator_exists(
    db: Database,
    round_id: int,
    platform: str,
    user_id: str,
) -> dict[str, Any]:
    owner = db.creator_owner(round_id, platform, user_id)
    if not owner:
        raise HTTPException(404, "达人不存在")
    return owner


@app.get("/api/rounds/{round_id}/creators/{platform}/{user_id}")
def get_creator(
    round_id: int,
    platform: str,
    user_id: str,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    return _detail_payload(db, round_id, platform, user_id)


@app.patch("/api/rounds/{round_id}/creators/{platform}/{user_id}")
def update_creator(
    round_id: int,
    platform: str,
    user_id: str,
    request: UpdateCreatorRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    owner_before = _ensure_creator_exists(db, round_id, platform, user_id)
    creator_before = db.get_creator(platform, user_id) or {}
    status = _validated_status(request.status)
    cooperation_status = _validated_cooperation_status(
        request.cooperation_status, allow_empty=True
    )
    expected_cooperation_status = _validated_cooperation_status(
        request.expected_cooperation_status, allow_empty=True
    )
    if request.expected_status is not None:
        expected = normalize_status(request.expected_status, default="")
        if expected not in STATUS_CODES:
            raise HTTPException(400, "无效的期望状态")
    has_review_changes = any(
        value is not None
        for value in (
            request.status,
            request.match_direction,
            request.tags_manual,
            request.remark,
        )
    )
    try:
        db.update_review(
            round_id,
            platform,
            user_id,
            status=status,
            cooperation_status=cooperation_status,
            match_direction=request.match_direction,
            tags_manual=request.tags_manual,
            remark=request.remark,
            expected_updated_at=request.expected_updated_at,
            expected_status=request.expected_status,
            expected_cooperation_status=expected_cooperation_status,
            expected_cooperation_updated_at=request.expected_cooperation_updated_at,
            claim_user_id=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    creator_after = db.get_creator(platform, user_id) or {}
    if (
        owner_before.get("owner_user_id") is None
        and creator_after.get("follow_up_user_id") == actor["id"]
    ):
        db.audit(
            actor["id"],
            "creator.claimed",
            f"{round_id}:{platform}:{user_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "auto": True,
                "source": "review" if has_review_changes else "cooperation",
            },
        )
    if has_review_changes:
        db.audit(
            actor["id"],
            "creator.review_updated",
            f"{round_id}:{platform}:{user_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "status": status,
                "match_direction": request.match_direction,
                "tags_manual": request.tags_manual,
                "remark": request.remark,
            },
        )
    cooperation_before = creator_before.get("cooperation_status") or ""
    cooperation_after = creator_after.get("cooperation_status") or ""
    if cooperation_before != cooperation_after:
        db.audit(
            actor["id"],
            "creator.cooperation_updated",
            f"{platform}:{user_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "from": cooperation_before,
                "to": cooperation_after,
                "auto": request.cooperation_status is None,
                "batch": False,
            },
        )
    return _detail_payload(db, round_id, platform, user_id)


@app.post("/api/rounds/{round_id}/creators/batch")
def batch_update(
    round_id: int,
    request: BatchUpdateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    actor = require_user(http_request)
    if request.set_owner and actor.get("role") != "admin":
        raise HTTPException(403, "只有管理员可以批量调整跟进人")
    if request.set_owner and request.owner_user_id is not None:
        target_user = db.get_user(int(request.owner_user_id))
        if not target_user or target_user.get("disabled"):
            raise HTTPException(400, "跟进人不存在或已禁用")
    auto_claim_targets = 0
    has_review_changes = any(
        value is not None
        for value in (
            request.status,
            request.cooperation_status,
            request.match_direction,
            request.tags_manual,
            request.remark,
        )
    )
    if not request.set_owner and has_review_changes:
        for item in request.items:
            owner = db.creator_owner(round_id, item.platform, item.user_id)
            if owner and owner.get("owner_user_id") is None:
                auto_claim_targets += 1
    status = _validated_status(request.status)
    cooperation_status = _validated_cooperation_status(
        request.cooperation_status, allow_empty=True
    )
    for item in request.items:
        _validated_cooperation_status(
            item.expected_cooperation_status, allow_empty=True
        )
    try:
        result = db.bulk_update_review(
            round_id,
            [item.model_dump() for item in request.items],
            status=status,
            cooperation_status=cooperation_status,
            match_direction=request.match_direction,
            tags_manual=request.tags_manual,
            remark=request.remark,
            claim_user_id=int(actor["id"]),
            set_owner=request.set_owner,
            owner_user_id=request.owner_user_id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if auto_claim_targets > 0:
        db.audit(
            actor["id"],
            "creator.claimed_bulk",
            str(round_id),
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={"count": auto_claim_targets, "auto": True},
        )
    has_review_fields = any(
        value is not None
        for value in (
            request.status,
            request.match_direction,
            request.tags_manual,
            request.remark,
        )
    )
    if has_review_fields or request.set_owner:
        db.audit(
            actor["id"],
            "creator.review_bulk_updated",
            str(round_id),
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "changed": result["changed"],
                "conflicts": len(result["conflicts"]),
                "status": status,
                "match_direction": request.match_direction,
                "tags_manual": request.tags_manual,
                "remark": request.remark,
                "set_owner": request.set_owner,
                "owner_user_id": request.owner_user_id,
            },
        )
    for change in result.get("cooperation_changes", []):
        db.audit(
            actor["id"],
            "creator.cooperation_updated",
            f"{change['platform']}:{change['user_id']}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "from": change["previous_status"],
                "to": change["status"],
                "auto": False,
                "batch": True,
            },
        )
    for item in result.get("enrolled", []):
        db.audit(
            actor["id"],
            "creator.cooperation_updated",
            f"{item['platform']}:{item['user_id']}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "from": "",
                "to": "not_contacted",
                "auto": True,
                "batch": True,
            },
        )
    return result


@app.get("/api/cooperation-cases/{case_id}")
def get_cooperation_case(
    case_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    require_user(http_request)
    return _cooperation_case_payload(db, case_id)


@app.post("/api/cooperation-cases")
def create_cooperation_case(
    request: CooperationCaseCreateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    status = None
    if request.status is not None and str(request.status).strip():
        status = _validated_cooperation_status(request.status, allow_empty=False)
    try:
        created = db.create_cooperation_case(
            request.platform,
            request.user_id,
            status,
            note=request.note,
            created_by=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise _case_error(exc) from exc
    db.audit(
        actor["id"],
        "cooperation.case_created",
        f"cooperation_case:{created['case_id']}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"platform": request.platform, "user_id": request.user_id},
    )
    payload = _cooperation_case_payload(db, int(created["case_id"]))
    payload["created"] = created
    return payload


@app.post("/api/cooperation-cases/{case_id}/status")
def update_cooperation_case_status(
    case_id: int,
    request: CooperationCaseStatusRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    status = _validated_cooperation_status(request.status, allow_empty=False)
    try:
        result = db.update_cooperation_case_status(
            case_id,
            status,
            note=request.note,
            expected_updated_at=request.expected_updated_at,
            actor_user_id=int(actor["id"]),
            claim_user_id=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise _case_error(exc) from exc
    db.audit(
        actor["id"],
        "cooperation.status_changed",
        f"cooperation_case:{case_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={
            "from": result.get("previous_status") or "",
            "to": result.get("status") or "",
            "note": bool(request.note),
        },
    )
    payload = _cooperation_case_payload(db, case_id)
    payload["change"] = result
    return payload


@app.post("/api/cooperation-cases/{case_id}/complete")
def complete_cooperation_case(
    case_id: int,
    request: CooperationCompleteRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    works = [_validate_work_payload(item.model_dump()) for item in request.works]
    try:
        result = db.complete_cooperation_case(
            case_id,
            note=request.note,
            works=works,
            expected_updated_at=request.expected_updated_at,
            actor_user_id=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit(
        actor["id"],
        "cooperation.completed",
        f"cooperation_case:{case_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"works": len(works)},
    )
    payload = _cooperation_case_payload(db, case_id)
    payload["change"] = result
    return payload


@app.post("/api/cooperation-cases/{case_id}/notes")
def add_cooperation_case_note(
    case_id: int,
    request: CooperationCaseNoteRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    try:
        result = db.add_cooperation_note(
            case_id, request.note, actor_user_id=int(actor["id"])
        )
    except ValueError as exc:
        raise _case_error(exc) from exc
    db.audit(
        actor["id"],
        "cooperation.note_added",
        f"cooperation_case:{case_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
    )
    payload = _cooperation_case_payload(db, case_id)
    payload["change"] = result
    return payload


@app.patch("/api/cooperation-cases/{case_id}")
def update_cooperation_case_summary(
    case_id: int,
    request: CooperationCaseSummaryRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    try:
        result = db.update_cooperation_case_summary(
            case_id,
            request.summary,
            expected_updated_at=request.expected_updated_at,
            actor_user_id=int(actor["id"]),
        )
    except ValueError as exc:
        raise _case_error(exc) from exc
    payload = _cooperation_case_payload(db, case_id)
    payload["change"] = result
    return payload


@app.post("/api/cooperation-cases/{case_id}/correct")
def correct_cooperation_case_status(
    case_id: int,
    request: CooperationCorrectionRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_admin(http_request)
    status = _validated_cooperation_status(request.status, allow_empty=False)
    try:
        result = db.correct_cooperation_status(
            case_id,
            status,
            note=request.note,
            actor_user_id=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise _case_error(exc) from exc
    db.audit(
        actor["id"],
        "cooperation.status_corrected",
        f"cooperation_case:{case_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"to": status, "note": request.note},
    )
    payload = _cooperation_case_payload(db, case_id)
    payload["change"] = result
    return payload


@app.post("/api/cooperation-cases/{case_id}/attachments")
async def upload_cooperation_attachment(
    case_id: int,
    http_request: Request,
    file: UploadFile = File(...),
    category: str = Form("other"),
    description: str = Form(""),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    if category not in COOPERATION_ATTACHMENT_CATEGORIES:
        raise HTTPException(400, "无效的附件分类")
    if len(description) > 500:
        raise HTTPException(400, "附件说明过长")
    detail = db.cooperation_case_detail(int(case_id))
    if not detail:
        raise HTTPException(404, "合作记录不存在")
    original_name = re.sub(
        r"[\r\n\"]+", "_", Path(file.filename or "attachment").name
    )[:255]
    if not original_name:
        raise HTTPException(400, "附件文件名不能为空")
    content_type = (
        file.content_type
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )
    if content_type not in settings.attachment_allowed_types:
        raise HTTPException(400, "不支持该附件类型")
    case_dir = _attachment_root() / str(int(case_id))
    case_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name).suffix[:20]
    storage_name = f"{uuid.uuid4().hex}{suffix}"
    storage_key = f"cooperation_attachments/{int(case_id)}/{storage_name}"
    final_path = settings.output_dir / storage_key
    temp_path = case_dir / f".{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.attachment_max_bytes:
                    raise HTTPException(413, "附件超过大小限制")
                digest.update(chunk)
                target.write(chunk)
        temp_path.replace(final_path)
        created = db.add_cooperation_attachment(
            case_id,
            original_name=original_name,
            storage_key=storage_key,
            content_type=content_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
            category=category,
            description=description.strip(),
            uploaded_by=int(actor["id"]),
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    db.audit(
        actor["id"],
        "cooperation.attachment_uploaded",
        f"cooperation_case:{case_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={
            "attachment_id": created["id"],
            "name": original_name,
            "size": size,
            "category": category,
        },
    )
    payload = _cooperation_case_payload(db, case_id)
    payload["uploaded_attachment_id"] = created["id"]
    return payload


@app.get("/api/cooperation-attachments/{attachment_id}")
def download_cooperation_attachment(
    attachment_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> FileResponse:
    require_user(http_request)
    row = db.get_cooperation_attachment(attachment_id)
    if not row or row.get("deleted_at"):
        raise HTTPException(404, "附件不存在")
    path = (settings.output_dir / str(row["storage_key"])).resolve()
    root = settings.output_dir.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "附件文件不存在")
    return FileResponse(
        path,
        media_type=row.get("content_type") or "application/octet-stream",
        filename=row.get("original_name") or "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.delete("/api/cooperation-attachments/{attachment_id}")
def delete_cooperation_attachment(
    attachment_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    row = db.get_cooperation_attachment(attachment_id)
    if not row or row.get("deleted_at"):
        raise HTTPException(404, "附件不存在")
    if actor.get("role") != "admin" and int(row.get("uploaded_by") or 0) != int(actor["id"]):
        raise HTTPException(403, "只能删除自己上传的附件")
    try:
        result = db.delete_cooperation_attachment(
            attachment_id, actor_user_id=int(actor["id"])
        )
    except ValueError as exc:
        raise _case_error(exc) from exc
    db.audit(
        actor["id"],
        "cooperation.attachment_deleted",
        f"cooperation_case:{row['case_id']}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"attachment_id": attachment_id, "name": row.get("original_name")},
    )
    payload = _cooperation_case_payload(db, int(row["case_id"]))
    payload["change"] = result
    return payload


@app.post("/api/cooperation-cases/{case_id}/works")
def add_cooperation_work(
    case_id: int,
    request: CooperationWorkRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    work = _validate_work_payload(request.model_dump())
    try:
        result = db.add_cooperation_work(
            case_id, work, actor_user_id=int(actor["id"])
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit(
        actor["id"],
        "cooperation.work_added",
        f"cooperation_case:{case_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"work_id": result["id"]},
    )
    payload = _cooperation_case_payload(db, case_id)
    payload["change"] = result
    return payload


@app.patch("/api/cooperation-works/{work_id}")
def update_cooperation_work(
    work_id: int,
    request: CooperationWorkRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    existing = db.get_cooperation_work(work_id)
    if not existing:
        raise HTTPException(404, "作品不存在")
    work = _validate_work_payload(request.model_dump())
    try:
        result = db.update_cooperation_work(
            work_id, work, actor_user_id=int(actor["id"])
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit(
        actor["id"],
        "cooperation.work_updated",
        f"cooperation_case:{existing['case_id']}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"work_id": work_id},
    )
    payload = _cooperation_case_payload(db, int(existing["case_id"]))
    payload["change"] = result
    return payload


@app.delete("/api/cooperation-works/{work_id}")
def delete_cooperation_work(
    work_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    existing = db.get_cooperation_work(work_id)
    if not existing:
        raise HTTPException(404, "作品不存在")
    result = db.delete_cooperation_work(
        work_id, actor_user_id=int(actor["id"])
    )
    db.audit(
        actor["id"],
        "cooperation.work_deleted",
        f"cooperation_case:{existing['case_id']}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"work_id": work_id},
    )
    payload = _cooperation_case_payload(db, int(existing["case_id"]))
    payload["change"] = result
    return payload


@app.get("/api/cooperation-cases/{case_id}/export")
def export_cooperation_case(
    case_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> FileResponse:
    actor = require_user(http_request)
    payload = _cooperation_case_payload(db, case_id)
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(temp.name)
    temp.close()
    manifest: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            case_json = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            archive.writestr("case.json", case_json)
            manifest.append({
                "path": "case.json",
                "sha256": hashlib.sha256(case_json.encode("utf-8")).hexdigest(),
            })
            for attachment in payload["attachments"]:
                row = db.get_cooperation_attachment(int(attachment["id"]))
                if not row or row.get("deleted_at"):
                    continue
                path = (settings.output_dir / str(row["storage_key"])).resolve()
                if not path.is_relative_to(settings.output_dir.resolve()) or not path.is_file():
                    continue
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", attachment["original_name"])[:120]
                archive_name = f"attachments/{attachment['id']}_{safe_name or 'attachment'}"
                archive.write(path, archive_name)
                manifest.append({
                    "path": archive_name,
                    "sha256": attachment["sha256"],
                    "attachment_id": attachment["id"],
                    "original_name": attachment["original_name"],
                })
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2)
            archive.writestr("manifest.json", manifest_bytes)
        db.audit(
            actor["id"],
            "cooperation.evidence_exported",
            f"cooperation_case:{case_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={"attachments": len(payload["attachments"])},
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=f"cooperation_case_{case_id}.zip",
        background=BackgroundTask(os.unlink, temp_path),
    )


@app.patch("/api/creators/{platform}/{user_id}/cooperation")
def update_creator_cooperation(
    platform: str,
    user_id: str,
    request: CooperationUpdateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    status = _validated_cooperation_status(request.status, allow_empty=True)
    expected = _validated_cooperation_status(
        request.expected_status, allow_empty=True
    )
    try:
        result = db.update_cooperation(
            platform,
            user_id,
            status,
            expected_status=expected,
            expected_updated_at=request.expected_updated_at,
            claim_user_id=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = str(exc)
        if "不存在" in message:
            raise HTTPException(404, message) from exc
        raise HTTPException(409, message) from exc
    if result.get("changed"):
        db.audit(
            actor["id"],
            "creator.cooperation_updated",
            f"{platform}:{user_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "from": result.get("previous_status") or "",
                "to": result.get("status") or "",
                "auto": False,
                "batch": False,
            },
        )
    if result.get("claimed"):
        db.audit(
            actor["id"],
            "creator.claimed",
            f"{platform}:{user_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={"auto": True, "source": "cooperation"},
        )
    result["cooperation_status"] = result.get("status") or ""
    result["cooperation_status_label"] = _cooperation_label(
        result.get("status")
    )
    return result


@app.post("/api/creators/cooperation/batch")
def batch_update_cooperation(
    request: CooperationBatchRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    status = _validated_cooperation_status(request.status, allow_empty=True)
    for item in request.items:
        _validated_cooperation_status(item.expected_status, allow_empty=True)
    try:
        result = db.bulk_update_cooperation(
            [item.model_dump() for item in request.items],
            status,
            claim_user_id=int(actor["id"]),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    for change in result.get("changes", []):
        db.audit(
            actor["id"],
            "creator.cooperation_updated",
            f"{change['platform']}:{change['user_id']}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "from": change.get("previous_status") or "",
                "to": change.get("status") or "",
                "auto": False,
                "batch": True,
            },
        )
    claimed = sum(1 for change in result.get("changes", []) if change.get("claimed"))
    if claimed:
        db.audit(
            actor["id"],
            "creator.claimed_bulk",
            "cooperation",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={"count": claimed, "auto": True, "source": "cooperation"},
        )
    return {
        "changed": result.get("changed", 0),
        "conflicts": result.get("conflicts", []),
        "claimed": claimed,
    }


@app.post("/api/rounds/{round_id}/creators/{platform}/{user_id}/claim")
def claim_creator(
    round_id: int,
    platform: str,
    user_id: str,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    try:
        result = db.claim_creator(round_id, platform, user_id, int(actor["id"]))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not result["ok"]:
        owner = result.get("owner_username") or "其他成员"
        raise HTTPException(409, f"该达人已由 {owner} 跟进")
    db.audit(
        actor["id"],
        "creator.claimed",
        f"{round_id}:{platform}:{user_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"already_mine": result.get("already_mine", False)},
    )
    return _detail_payload(db, round_id, platform, user_id)


@app.post("/api/rounds/{round_id}/creators/{platform}/{user_id}/release")
def release_creator(
    round_id: int,
    platform: str,
    user_id: str,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    owner = db.creator_owner(round_id, platform, user_id)
    if not owner:
        raise HTTPException(404, "达人不存在")
    current = owner.get("owner_user_id")
    if current is None:
        return _detail_payload(db, round_id, platform, user_id)
    if int(current) != int(actor["id"]) and actor.get("role") != "admin":
        raise HTTPException(403, "只能结束自己的跟进")
    result = db.release_creator(
        round_id,
        platform,
        user_id,
        expected_owner_user_id=int(current),
    )
    if not result["ok"]:
        raise HTTPException(409, "跟进状态已变化，请刷新后重试")
    previous = result.get("previous")
    if previous:
        db.audit(
            actor["id"],
            "creator.released",
            f"{round_id}:{platform}:{user_id}",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={"owner_user_id": previous.get("owner_user_id")},
        )
    return _detail_payload(db, round_id, platform, user_id)


@app.post("/api/rounds/{round_id}/creators/{platform}/{user_id}/assign")
def assign_creator(
    round_id: int,
    platform: str,
    user_id: str,
    request: CreatorAssignRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
    owner = db.creator_owner(round_id, platform, user_id)
    if not owner:
        raise HTTPException(404, "达人不存在")
    current = owner.get("owner_user_id")
    if actor.get("role") != "admin":
        if current is None or int(current) != int(actor["id"]):
            raise HTTPException(403, "只能转交自己跟进的达人")
    target = request.owner_user_id
    if target is not None:
        target_user = db.get_user(int(target))
        if not target_user or target_user.get("disabled"):
            raise HTTPException(400, "跟进人不存在或已禁用")
    try:
        db.assign_creator(
            round_id,
            platform,
            user_id,
            owner_user_id=target,
            expected_owner_user_id=current,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit(
        actor["id"],
        "creator.assigned",
        f"{round_id}:{platform}:{user_id}",
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"previous_owner_user_id": current, "owner_user_id": target},
    )
    return _detail_payload(db, round_id, platform, user_id)


@app.post("/api/rounds/{round_id}/creators/claim")
def batch_claim_creators(
    round_id: int,
    request: BatchClaimRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    actor = require_user(http_request)
    result = db.claim_creators(
        round_id,
        [(item.platform, item.user_id) for item in request.items],
        int(actor["id"]),
    )
    db.audit(
        actor["id"],
        "creator.claimed_bulk",
        str(round_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details=result,
    )
    return result


@app.get("/api/rounds/{round_id}/job")
def latest_job(round_id: int, db: Database = Depends(get_db)) -> dict[str, Any] | None:
    if not _round_names_visible():
        return None
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    return _job_dict(db.latest_job(round_id))


@app.get("/api/leads")
def list_leads(
    round_id: int | None = None,
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    sort: str = "updated_at",
    order: str = "desc",
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(50, ge=1, le=200),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    result = db.query_leads(
        round_id=round_id,
        platform=platform,
        search=search,
        statuses=_validated_lead_statuses(status),
        sources=_csv(source),
        owner=_validated_owner_filter(owner),
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    result["items"] = [_lead_dict(row) for row in result["items"]]
    return result


@app.get("/api/leads/facets")
def lead_facets(
    round_id: int | None = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    result = db.lead_facets(round_id)
    if not _round_names_visible():
        result["rounds"] = []
    return result


@app.get("/api/leads/owners")
def lead_owners(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.lead_owner_options()


LEAD_EXPORT_COLUMNS = [
    "昵称",
    "平台",
    "账号 ID",
    "平台用户 ID",
    "身份状态",
    "主页 URL",
    "原始标题",
    "来源",
    "来源轮次",
    "负责人",
    "线索状态",
    "创建时间",
    "更新时间",
    "关联达人 ID",
    "匹配轮次",
    "匹配时间",
    "转换达人 ID",
]


def _iter_lead_export_rows(
    db: Database,
    round_id: int | None,
    platform: str | None,
    search: str | None,
    statuses: list[str],
    source: str | None,
    owner: str | None,
    sort: str = "updated_at",
    order: str = "desc",
) -> Generator[dict[str, Any], None, None]:
    page = 1
    while True:
        result = db.query_leads(
            round_id=round_id,
            platform=platform,
            search=search,
            statuses=statuses,
            sources=_csv(source),
            owner=owner,
            sort=sort,
            order=order,
            page=page,
            page_size=200,
        )
        items = result["items"]
        for item in items:
            yield _lead_dict(item)
        if not items or page * 200 >= int(result["total"]):
            break
        page += 1


def _lead_export_value(row: dict[str, Any], column: str) -> Any:
    mapping = {
        "昵称": "nickname",
        "平台": "platform_label",
        "账号 ID": "platform_handle",
        "平台用户 ID": "account_id",
        "身份状态": "identity_status_label",
        "主页 URL": "profile_url",
        "原始标题": "raw_title",
        "来源": "source_label",
        "来源轮次": "source_round_name",
        "负责人": "owner_username",
        "线索状态": "status_label",
        "创建时间": "created_at",
        "更新时间": "updated_at",
        "关联达人 ID": "matched_user_id",
        "匹配轮次": "matched_round_name",
        "匹配时间": "matched_at",
        "转换达人 ID": "converted_user_id",
    }
    value = row.get(mapping.get(column, column))
    if column == "负责人" and not value:
        return "未分配"
    return _safe_cell(value if value is not None else "")


@app.get("/api/leads/export")
def export_leads(
    export_format: str = Query("csv", alias="format", pattern="^(csv|xlsx)$"),
    round_id: int | None = None,
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    sort: str = "updated_at",
    order: str = "desc",
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_user),
) -> Response:
    if actor.get("role") != "admin" and not actor.get("can_collect"):
        round_id = None
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    statuses = _validated_lead_statuses(status)
    owner_filter = _validated_owner_filter(owner)
    columns = _lead_export_columns(actor)
    if export_format == "csv":
        filename = "codata_leads.csv"

        def generate() -> Generator[bytes, None, None]:
            yield "\ufeff".encode("utf-8")
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(columns)
            yield buffer.getvalue().encode("utf-8")
            with Database(settings.output_dir) as stream_db:
                for row in _iter_lead_export_rows(
                    stream_db, round_id, platform, search, statuses, source, owner_filter, sort, order
                ):
                    buffer.seek(0)
                    buffer.truncate(0)
                    writer.writerow(
                        [_lead_export_value(row, column) for column in columns]
                    )
                    yield buffer.getvalue().encode("utf-8")

        return StreamingResponse(
            generate(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    temp_path = Path(temp.name)
    temp.close()
    try:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("线索")
        sheet.freeze_panes = "A2"
        sheet.append(columns)
        for row in _iter_lead_export_rows(
            db, round_id, platform, search, statuses, source, owner_filter, sort, order
        ):
            sheet.append([_lead_export_value(row, column) for column in columns])
        workbook.save(temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return FileResponse(
        temp_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="codata_leads.xlsx",
        background=BackgroundTask(os.unlink, temp_path),
    )


@app.post("/api/leads")
def create_lead(
    payload: LeadCreateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_lead_editor),
) -> dict[str, Any]:
    if payload.source_round_id is not None and not db.get_round(payload.source_round_id):
        raise HTTPException(400, "来源轮次不存在")
    _validate_owner(db, payload.owner_user_id)
    _check_owner_change(actor, payload.owner_user_id)
    owner_id = payload.owner_user_id if payload.owner_user_id is not None else actor["id"]
    lead_id, created, revived = db.create_lead_dedup(
        platform=payload.platform.strip().lower(),
        nickname=payload.nickname.strip(),
        account_id=payload.account_id.strip(),
        platform_handle=payload.platform_handle.strip(),
        profile_url=payload.profile_url.strip(),
        raw_title=payload.raw_title.strip(),
        source=normalize_lead_source(payload.source),
        source_round_id=payload.source_round_id,
        owner_user_id=owner_id,
        raw_json=_checked_raw_json(payload.raw_json),
        created_by=actor["id"],
    )
    match_error = ""
    if created or revived:
        if getattr(settings, "lead_match_enabled", True):
            try:
                match_lead_against_creators(db, lead_id, actor_id=actor["id"])
            except Exception as exc:
                match_error = str(exc)
                print(f"[lead-match] 线索 {lead_id} 匹配失败: {exc}")
        db.audit(
            actor["id"],
            "lead.revived" if revived else "lead.created",
            str(lead_id),
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
        )
    else:
        db.audit(
            actor["id"],
            "lead.duplicate_reused",
            str(lead_id),
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
        )
    result = _lead_detail(db, lead_id)
    result["created"] = created
    result["revived"] = revived
    if match_error:
        result["match_error"] = match_error
    return result


@app.post("/api/leads/bulk")
def create_leads_bulk(
    payload: LeadBulkRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_lead_editor),
) -> dict[str, Any]:
    _enforce_action_limit(f"leads-bulk:{actor['id']}")
    created: list[int] = []
    revived: list[int] = []
    existing: list[int] = []
    errors: list[dict[str, Any]] = []
    match_errors = 0
    for index, item in enumerate(payload.items, 1):
        try:
            if item.source_round_id is not None and not db.get_round(item.source_round_id):
                raise ValueError("来源轮次不存在")
            _validate_owner(db, item.owner_user_id)
            _check_owner_change(actor, item.owner_user_id)
            lead_id, is_new, is_revived = db.create_lead_dedup(
                platform=item.platform.strip().lower(),
                nickname=item.nickname.strip(),
                account_id=item.account_id.strip(),
                platform_handle=item.platform_handle.strip(),
                profile_url=item.profile_url.strip(),
                raw_title=item.raw_title.strip(),
                source=normalize_lead_source(item.source),
                source_round_id=item.source_round_id,
                owner_user_id=item.owner_user_id if item.owner_user_id is not None else actor["id"],
                raw_json=_checked_raw_json(item.raw_json),
                created_by=actor["id"],
            )
            if is_new:
                created.append(lead_id)
            elif is_revived:
                revived.append(lead_id)
            else:
                existing.append(lead_id)
            if is_new or is_revived:
                if getattr(settings, "lead_match_enabled", True):
                    try:
                        match_lead_against_creators(db, lead_id, actor_id=actor["id"])
                    except Exception as exc:
                        match_errors += 1
                        print(f"[lead-match] 线索 {lead_id} 匹配失败: {exc}")
        except Exception as exc:
            errors.append({"index": index, "error": _public_error(exc)})
    if created:
        db.audit(
            actor["id"],
            "lead.bulk_created",
            f"{len(created)} leads",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
        )
    if revived:
        db.audit(
            actor["id"],
            "lead.bulk_revived",
            f"{len(revived)} leads",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
        )
    return {
        "created": len(created),
        "revived": len(revived),
        "existing": len(existing),
        "ids": created,
        "revived_ids": revived,
        "existing_ids": existing,
        "match_errors": match_errors,
        "errors": errors,
    }


@app.get("/api/leads/identity-health")
def lead_identity_health(db: Database = Depends(get_db)) -> dict[str, Any]:
    return db.lead_identity_health()


@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: int, db: Database = Depends(get_db)) -> dict[str, Any]:
    return _lead_detail(db, lead_id)


@app.patch("/api/leads/{lead_id}")
def update_lead(
    lead_id: int,
    payload: LeadUpdateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_lead_editor),
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    _ensure_lead_owner(lead, actor, "修改")
    if lead.get("status") == "converted":
        raise HTTPException(400, "已转达人的线索不可修改")
    changes = payload.model_dump(exclude_unset=True)
    if "source_round_id" in changes and changes["source_round_id"] is not None:
        if not db.get_round(int(changes["source_round_id"])):
            raise HTTPException(400, "来源轮次不存在")
    if "owner_user_id" in changes:
        _validate_owner(db, changes["owner_user_id"])
        _check_owner_change(actor, changes["owner_user_id"])
    if "status" in changes:
        status = _validate_lead_status(changes["status"])
        if status == "converted":
            raise HTTPException(400, "请使用线索转换接口")
        changes["status"] = status
    if "platform" in changes:
        platform_value = str(changes["platform"] or "").strip().lower()
        if not platform_value:
            raise HTTPException(400, "平台不能为空")
        changes["platform"] = platform_value
    if "source" in changes:
        changes["source"] = normalize_lead_source(changes["source"])
    try:
        db.update_lead(lead_id, **changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    match_error = ""
    if {"account_id", "profile_url", "platform"} & set(changes) and getattr(
        settings, "lead_match_enabled", True
    ):
        try:
            match_lead_against_creators(db, lead_id, actor_id=actor["id"])
        except Exception as exc:
            match_error = str(exc)
            print(f"[lead-match] 线索 {lead_id} 匹配失败: {exc}")
    db.audit(
        actor["id"],
        "lead.updated",
        str(lead_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"changes": changes},
    )
    result = _lead_detail(db, lead_id)
    if match_error:
        result["match_error"] = match_error
    return result


@app.post("/api/leads/{lead_id}/assign")
def assign_lead(
    lead_id: int,
    payload: LeadAssignRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_lead_editor),
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    _ensure_lead_owner(lead, actor, "分配")
    if lead.get("status") == "converted":
        raise HTTPException(400, "已转达人的线索不可分配")
    _validate_owner(db, payload.owner_user_id)
    _check_owner_change(actor, payload.owner_user_id)
    previous = lead.get("owner_user_id")
    db.update_lead(lead_id, owner_user_id=payload.owner_user_id)
    db.audit(
        actor["id"],
        "lead.assigned",
        str(lead_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"from": previous, "to": payload.owner_user_id},
    )
    return _lead_detail(db, lead_id)


@app.post("/api/leads/{lead_id}/match")
def match_lead_endpoint(
    lead_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_lead_editor),
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    _ensure_lead_owner(lead, actor, "匹配")
    _enforce_action_limit(f"lead-match:{actor['id']}")
    if lead.get("status") == "converted":
        raise HTTPException(400, "已转达人的线索无需重新匹配")
    stats = match_lead_against_creators(db, lead_id, actor_id=actor["id"])
    db.audit(
        actor["id"],
        "lead.match_retried",
        str(lead_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details=stats,
    )
    result = _lead_detail(db, lead_id)
    result["match"] = stats
    return result


@app.post("/api/leads/{lead_id}/verify")
def verify_lead_endpoint(
    lead_id: int,
    payload: LeadVerifyRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    _ensure_lead_owner(lead, actor, "校验")
    _enforce_action_limit(f"lead-verify:{actor['id']}")
    try:
        result = verify_lead_identity(
            db,
            settings,
            lead_id,
            provider_code=payload.provider,
            account_id=payload.account_id,
            platform_handle=payload.platform_handle,
            profile_url=payload.profile_url,
            apply=payload.apply,
            confirm_mismatch=payload.confirm_mismatch,
            actor_id=actor["id"],
        )
    except (ValueError, RuntimeError, PlatformConfigError) as exc:
        raise HTTPException(400, _public_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"校验失败：{_public_error(exc)}") from exc
    result.pop("fetched", None)
    result["note"] = _public_error(result.get("note") or "")
    result["error"] = _public_error(result.get("error") or "")
    if result.get("applied"):
        db.audit(
            actor["id"],
            "lead.identity_verified",
            str(lead_id),
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
            details={
                "confidence": result.get("confidence") or "",
                "status": result.get("status") or "",
            },
        )
    return result


@app.post("/api/leads/{lead_id}/convert")
def convert_lead_endpoint(
    lead_id: int,
    payload: LeadConvertRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    _ensure_lead_owner(lead, actor, "转换")
    _enforce_action_limit(f"lead-convert:{actor['id']}")
    try:
        result = convert_lead(
            db,
            settings,
            lead_id,
            account_id=payload.account_id,
            platform_handle=payload.platform_handle,
            profile_url=payload.profile_url,
            fetch=payload.fetch,
            provider_code=payload.provider,
            confirm_mismatch=payload.confirm_mismatch,
            actor_id=actor["id"],
        )
    except LeadIdentityMismatch as exc:
        raise HTTPException(409, exc.to_dict()) from exc
    except (ValueError, RuntimeError, PlatformConfigError) as exc:
        raise HTTPException(400, _public_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"转换失败：{_public_error(exc)}") from exc
    db.audit(
        actor["id"],
        "lead.converted",
        str(lead_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"owner_user_id": lead.get("owner_user_id") or actor["id"]},
    )
    return result


@app.get("/api/jobs/active")
def active_job(db: Database = Depends(get_db)) -> dict[str, Any] | None:
    if not _round_names_visible():
        return None
    db.reconcile_stale_jobs(settings.job_stale_minutes)
    payload = _job_dict(db.latest_active_job())
    if payload is not None:
        payload["active_count"] = db.count_active_jobs()
    return payload


@app.get("/api/jobs/latest")
def latest_job_overall(db: Database = Depends(get_db)) -> dict[str, Any] | None:
    if not _round_names_visible():
        return None
    db.reconcile_stale_jobs(settings.job_stale_minutes)
    payload = _job_dict(db.latest_job_overall())
    if payload is not None:
        payload["active_count"] = db.count_active_jobs()
    return payload


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.get("status") not in ("pending", "running"):
        raise HTTPException(409, "任务已结束")
    if actor.get("role") != "admin":
        starter = job.get("created_by")
        if starter is None or int(starter) != int(actor["id"]):
            raise HTTPException(403, "只能取消自己发起的任务")
    updated = db.cancel_job(job_id, cancelled_by=int(actor["id"]))
    process_terminated = terminate_job_process(
        job.get("pid"), round_id=job.get("round_id"), job_id=job_id
    )
    db.audit(
        actor["id"],
        "job.cancelled",
        str(job_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"round_id": job.get("round_id")},
    )
    return {**(_job_dict(updated) or {}), "process_terminated": process_terminated}


@app.get("/api/rounds/{round_id}/calls")
def recent_calls(
    round_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    if not _round_names_visible():
        raise HTTPException(403, "当前账号没有采集权限")
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    actor = require_user(http_request)
    job = db.latest_job(round_id)
    rows = db.recent_api_calls(
        round_id,
        limit=30,
        job_id=int(job["id"]) if job else None,
    )
    if actor.get("role") != "admin":
        for row in rows:
            row.pop("raw_file", None)
    return rows


@app.get("/api/rounds/{round_id}/log")
def job_log(
    round_id: int,
    http_request: Request,
    lines: int = Query(200, ge=1, le=2000),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not _round_names_visible():
        raise HTTPException(403, "当前账号没有采集权限")
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    actor = require_user(http_request)
    show_path = actor.get("role") == "admin"
    job = db.latest_job(round_id)
    if not job:
        return {"path": "", "content": ""}
    path = settings.output_dir / "logs" / f"round_{round_id}_{job['id']}.log"
    if not path.exists():
        return {"path": str(path) if show_path else "", "content": ""}
    size = path.stat().st_size
    read_size = min(size, 512_000)
    with path.open("rb") as handle:
        handle.seek(size - read_size)
        text = handle.read().decode("utf-8", errors="replace")
    if read_size < size:
        text = text.split("\n", 1)[-1]
    content = text.splitlines()
    return {
        "path": str(path) if show_path else "",
        "content": "\n".join(content[-lines:]),
    }


def _xlsx_response(
    items: Iterable[dict[str, Any]],
    filename: str,
    columns: list[str] | None = None,
) -> FileResponse:
    headers = columns or list(EXPORT_COLUMNS)
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    temp_path = Path(temp.name)
    temp.close()
    try:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("达人数据")
        sheet.freeze_panes = "A2"
        for index, header in enumerate(headers, 1):
            sheet.column_dimensions[get_column_letter(index)].width = min(
                max(10, len(header) * 2 + 4), 40
            )
        sheet.append(headers)
        for item in items:
            sheet.append([_export_value(item, header) for header in headers])
        workbook.save(temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return FileResponse(
        temp_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        background=BackgroundTask(os.unlink, temp_path),
    )


@app.get("/api/rounds/{round_id}/export")
def export_round(
    round_id: int,
    http_request: Request,
    export_format: str = Query("xlsx", alias="format", pattern="^(csv|xlsx)$"),
    scope: str = Query("filtered", pattern="^(filtered|all)$"),
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    source_mode: str = Query("any", pattern="^(any|all)$"),
    label: str | None = None,
    owner: str | None = None,
    cooperation: str | None = None,
    cooperation_stale_days: int | None = Query(None, ge=1, le=365),
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    needs_action: bool = False,
    sort: str = "fans",
    order: str = "desc",
    db: Database = Depends(get_db),
) -> Response:
    if not _round_names_visible():
        raise HTTPException(403, "当前账号没有采集权限")
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    if scope == "all":
        platform = None
        search = None
        status = None
        tier = None
        direction = None
        source = None
        source_mode = "any"
        label = None
        owner = None
        cooperation = None
        cooperation_stale_days = None
        min_fans = None
        max_fans = None
        only_unenriched = False
        needs_action = False
    actor = require_user(http_request)
    columns = _creator_export_columns(actor)
    filters = {
        "platform": platform,
        "search": search,
        **_all_creator_filters(
            status, tier, direction, source, cooperation, cooperation_stale_days
        ),
        "source_mode": source_mode,
        "labels": _csv(label),
        "owner": _validated_owner_filter(owner),
        "min_fans": min_fans,
        "max_fans": max_fans,
        "only_unenriched": only_unenriched,
        "needs_action": needs_action,
        "action_actor_id": int(actor["id"]),
    }
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", platform or "all")[:40]
    if export_format == "csv":
        filename = f"codata_round_{round_id}_{suffix}_{scope}.csv"

        def generate_csv() -> Generator[bytes, None, None]:
            yield "\ufeff".encode("utf-8")
            header_buffer = io.StringIO()
            header_writer = csv.writer(header_buffer, lineterminator="\r\n")
            header_writer.writerow(columns)
            yield header_buffer.getvalue().encode("utf-8")
            row_buffer = io.StringIO()
            row_writer = csv.writer(row_buffer, lineterminator="\r\n")
            with Database(settings.output_dir) as stream_db:
                for item in _iter_export_items(stream_db, round_id, filters, sort=sort, order=order):
                    row_buffer.seek(0)
                    row_buffer.truncate(0)
                    row_writer.writerow(
                        [_export_value(item, header) for header in columns]
                    )
                    yield row_buffer.getvalue().encode("utf-8")

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"codata_round_{round_id}_{suffix}_{scope}.xlsx"
    return _xlsx_response(
        _iter_export_items(db, round_id, filters, sort=sort, order=order),
        filename,
        columns,
    )


@app.get("/api/creators/export")
def export_all_creators(
    http_request: Request,
    export_format: str = Query("xlsx", alias="format", pattern="^(csv|xlsx)$"),
    scope: str = Query("filtered", pattern="^(filtered|all)$"),
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    source_mode: str = Query("any", pattern="^(any|all)$"),
    label: str | None = None,
    owner: str | None = None,
    cooperation: str | None = None,
    cooperation_stale_days: int | None = Query(None, ge=1, le=365),
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    needs_action: bool = False,
    sort: str = "fans",
    order: str = "desc",
    db: Database = Depends(get_db),
) -> Response:
    if scope == "all":
        platform = None
        search = None
        status = None
        tier = None
        direction = None
        source = None
        source_mode = "any"
        label = None
        owner = None
        cooperation = None
        cooperation_stale_days = None
        min_fans = None
        max_fans = None
        only_unenriched = False
        needs_action = False
    actor = require_user(http_request)
    columns = _creator_export_columns(actor)
    filters = {
        "platform": platform,
        "search": search,
        **_all_creator_filters(
            status, tier, direction, source, cooperation, cooperation_stale_days
        ),
        "source_mode": source_mode,
        "labels": _csv(label),
        "owner": _validated_owner_filter(owner),
        "min_fans": min_fans,
        "max_fans": max_fans,
        "only_unenriched": only_unenriched,
        "needs_action": needs_action,
        "action_actor_id": int(actor["id"]),
    }
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", platform or "all")[:40]
    if export_format == "csv":
        filename = f"codata_all_{suffix}_{scope}.csv"

        def generate_csv() -> Generator[bytes, None, None]:
            yield "\ufeff".encode("utf-8")
            header_buffer = io.StringIO()
            header_writer = csv.writer(header_buffer, lineterminator="\r\n")
            header_writer.writerow(columns)
            yield header_buffer.getvalue().encode("utf-8")
            row_buffer = io.StringIO()
            row_writer = csv.writer(row_buffer, lineterminator="\r\n")
            with Database(settings.output_dir) as stream_db:
                for item in _iter_export_items(stream_db, None, filters, sort=sort, order=order):
                    row_buffer.seek(0)
                    row_buffer.truncate(0)
                    row_writer.writerow(
                        [_export_value(item, header) for header in columns]
                    )
                    yield row_buffer.getvalue().encode("utf-8")

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"codata_all_{suffix}_{scope}.xlsx"
    return _xlsx_response(
        _iter_export_items(db, None, filters, sort=sort, order=order),
        filename,
        columns,
    )


@app.get("/api/keywords")
def list_keywords(
    actor: dict[str, Any] = Depends(require_collector),
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    return db.list_keywords()


@app.post("/api/keywords")
def create_keyword(
    request: KeywordCreateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_collector(http_request)
    keyword_id = db.ensure_keyword(request.word, request.category)
    if not request.enabled:
        db.update_keyword(keyword_id, enabled=False)
    db.audit(
        actor["id"],
        "keyword.created",
        str(keyword_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"word": request.word, "category": request.category},
    )
    return {"id": keyword_id}


@app.patch("/api/keywords/{keyword_id}")
def update_keyword(
    keyword_id: int,
    request: KeywordUpdateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_keyword(keyword_id):
        raise HTTPException(404, "关键词不存在")
    actor = require_collector(http_request)
    db.update_keyword(keyword_id, category=request.category, enabled=request.enabled)
    db.audit(
        actor["id"],
        "keyword.updated",
        str(keyword_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"category": request.category, "enabled": request.enabled},
    )
    return {"ok": True}


@app.delete("/api/keywords/{keyword_id}")
def delete_keyword(
    keyword_id: int,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_keyword(keyword_id):
        raise HTTPException(404, "关键词不存在")
    actor = require_collector(http_request)
    try:
        db.delete_keyword(keyword_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        actor["id"],
        "keyword.deleted",
        str(keyword_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
    )
    return {"ok": True}


@app.get("/api/counts")
def counts_endpoint(db: Database = Depends(get_db)) -> dict[str, int]:
    counts = db.counts()
    if not _round_names_visible():
        counts.update({"rounds": 0, "keywords": 0, "creator_rounds": 0})
    return counts


@app.get("/api/dashboard/queue")
def dashboard_queue(
    http_request: Request,
    round_id: int | None = None,
    scope: str = "all",
    sort: str = "potential",
    include_others: bool = False,
    limit: int = Query(6, ge=1, le=20),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if scope not in {"all", "pending", "unclaimed"}:
        raise HTTPException(400, "scope 参数无效")
    if sort not in {"potential", "ratio", "updated"}:
        raise HTTPException(400, "sort 参数无效")
    actor = require_user(http_request)
    result = db.action_queue(
        actor_id=int(actor["id"]),
        is_admin=actor.get("role") == "admin",
        round_id=round_id,
        scope=scope,
        sort=sort,
        limit=limit,
        include_others=include_others,
    )
    return {
        "counts": result["counts"],
        "items": [_queue_item(row) for row in result["items"]],
    }


@app.get("/api/dashboard/activity")
def dashboard_activity(
    days: int = Query(365, ge=30, le=730),
    tz_offset: int = Query(480, ge=-720, le=840),
    round_id: int | None = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    return db.activity_calendar(days, tz_offset, round_id=round_id)


@app.get("/api/dashboard/efficiency")
def dashboard_efficiency(
    limit: int = Query(6, ge=1, le=20),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    result = db.round_efficiency(limit)
    items = [_efficiency_item(row) for row in result["items"]]
    totals = result["totals"]
    if not _round_names_visible():
        for item in items:
            item.pop("cost", None)
        totals.pop("cost", None)
    return {"items": items, "totals": totals}


@app.get("/api/dashboard")
def dashboard(
    round_id: int | None = None,
    stale_days: int = Query(7, ge=1, le=365),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not _round_names_visible():
        round_id = None
    counts = db.counts()
    if not _round_names_visible():
        counts.update({"rounds": 0, "keywords": 0, "creator_rounds": 0})
    payload: dict[str, Any] = {"counts": counts}
    if round_id is None:
        breakdown = db.dashboard_breakdown(None, stale_days=stale_days)
        metrics = breakdown["metrics"]
        metrics["cost"] = db.sum_round_cost(None)
        if not _round_names_visible():
            metrics.pop("cost", None)
        payload.update(
            {
                "round": None,
                "tier_counts": breakdown["tier_counts"],
                "status_counts": breakdown["status_counts"],
                "cooperation_counts": breakdown["cooperation_counts"],
                "cooperation_metrics": breakdown["cooperation_metrics"],
                "cooperation_stale_days": breakdown["cooperation_stale_days"],
                "metrics": metrics,
                "lead_metrics": db.lead_metrics(),
            }
        )
        return payload
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    round_payload = _round_payload(db, round_id)
    breakdown = db.dashboard_breakdown(round_id, stale_days=stale_days)
    metrics = breakdown["metrics"]
    metrics["cost"] = int(round_payload.get("summary", {}).get("cost", 0))
    payload.update(
        {
            "round": round_payload,
            "tier_counts": breakdown["tier_counts"],
            "status_counts": breakdown["status_counts"],
            "cooperation_counts": breakdown["cooperation_counts"],
            "cooperation_metrics": breakdown["cooperation_metrics"],
            "cooperation_stale_days": breakdown["cooperation_stale_days"],
            "metrics": metrics,
            "lead_metrics": db.lead_metrics(round_id),
        }
    )
    return payload


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str = "") -> Response:
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return PlainTextResponse(
            "React frontend is not built. Run: cd frontend && npm install && npm run build",
            status_code=503,
        )
    dist_root = FRONTEND_DIST.resolve()
    candidate = (dist_root / full_path).resolve()
    if full_path and candidate.is_file() and candidate.is_relative_to(dist_root):
        return FileResponse(candidate)
    return FileResponse(index)


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="codata-web", description="Co-data React panel + API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args, _ = parser.parse_known_args()
    uvicorn.run("codata.api_server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
