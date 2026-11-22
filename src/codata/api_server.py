from __future__ import annotations

import csv
import io
import hmac
import json
import sqlite3
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Generator, Iterable

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import set_key
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
    verify_password,
)
from .client import DataFlowClient, RnoteClient
from .config import PROJECT_ROOT, Settings, load_settings
from .db import Database, JobBusyError, SCHEMA_VERSION
from .normalize import as_int, format_fans_wan, normalize_tier_label, split_tags
from .platforms import PlatformConfigError, load_registry
from .providers import build_provider
from .panel.queries import EXPORT_COLUMNS, JOB_STATUS_LABELS, PLATFORM_LABELS, ROUND_STATUS_LABELS
from .panel.runner import start_round_job, terminate_job_process
from .lead_statuses import (
    LEAD_SOURCE_CODES,
    LEAD_SOURCE_LABELS,
    LEAD_STATUS_CODES,
    LEAD_STATUS_DEFS,
    LEAD_STATUS_LABELS,
    normalize_lead_source,
    normalize_lead_status,
)
from .leads import convert_lead
from .statuses import STATUS_CODES, STATUS_DEFS, STATUS_LABELS, normalize_status
from .storage import unwrap_data

settings = load_settings(require_credentials=False)
ENV_PATH = PROJECT_ROOT / ".env"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_API_PATHS = {"/api/health", "/api/auth/login", "/api/auth/me"}
login_limiter = LoginRateLimiter(
    max_attempts=settings.max_login_attempts,
    window_seconds=settings.login_window_minutes * 60,
)


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
                if int(raw) > settings.max_request_bytes:
                    return JSONResponse({"detail": "请求体过大"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "无效的 Content-Length"}, status_code=400)
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
        try:
            session = db.get_session(hash_session_token(token))
        finally:
            db.close()
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
        return await call_next(request)


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


def get_db() -> Generator[Database, None, None]:
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
    "匹配方向": "match_direction",
    "备注": "remark",
    "笔记数": "posted",
    "总点赞": "liked",
    "互动量": "interaction",
    "标签": "tags",
    "主页URL": "profile_url",
    "账号ID": "account_id",
    "代表作": "top_title",
    "数据来源": "source",
    "平台": "platform_label",
    "经手人": "owner_username",
    "合作主平台": "cooperation_platform",
    "原始标题": "raw_title",
    "样本笔记": "sample_notes",
    "样本点赞": "sample_likes",
    "样本互动": "sample_interactions",
}


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _export_value(item: dict[str, Any], header: str) -> Any:
    key = EXPORT_FIELDS[header]
    value = item.get(key)
    if key in ("fans", "posted", "liked", "interaction") and value is None:
        value = ""
    return _safe_cell(value)


def _iter_export_items(
    db: Database,
    round_id: int | None,
    filters: dict[str, Any],
    page_size: int = 500,
) -> Generator[dict[str, Any], None, None]:
    page = 1
    while True:
        result = db.query_creators(
            round_id,
            sort="fans",
            order="desc",
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


PROVIDER_LABELS = {"auto": "自动", "dataflow": "DataFlow", "rnote": "Rnote"}


def _round_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    platform = str(row.get("platform") or "xhs")
    provider = str(row.get("provider") or "dataflow")
    payload["status_label"] = ROUND_STATUS_LABELS.get(str(row.get("status")), str(row.get("status")))
    payload["platform"] = platform
    payload["platform_label"] = PLATFORM_LABELS.get(platform, platform)
    payload["provider"] = provider
    payload["provider_label"] = PROVIDER_LABELS.get(provider, provider)
    return payload


def _job_dict(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    payload = dict(job)
    payload["status_label"] = JOB_STATUS_LABELS.get(str(job.get("status")), str(job.get("status")))
    return payload


def _round_payload(db: Database, round_id: int) -> dict[str, Any]:
    row = db.get_round(round_id)
    if not row:
        raise HTTPException(404, "轮次不存在")
    keywords = db.get_round_keywords(round_id)
    payload = _round_dict(row)
    payload["keywords"] = "、".join(item["word"] for item in keywords)
    payload["keyword_details"] = keywords
    payload["summary"] = db.round_summary(round_id)
    payload["job"] = _job_dict(db.latest_job(round_id))
    return payload


def _creator_dict(row: dict[str, Any]) -> dict[str, Any]:
    auto_tags = [str(tag) for tag in _json_list(row.get("auto_tags"))]
    manual_tags = split_tags(row.get("tags_manual"))
    tags = list(dict.fromkeys([*auto_tags, *manual_tags]))
    source_keywords = [str(item) for item in _json_list(row.get("source_keywords"))]
    fans = row.get("fans")
    user_id = row["user_id"]
    account_id = row.get("account_id")
    if account_id is None:
        account_id = "" if str(user_id).startswith("import:") else user_id
    tier_label = normalize_tier_label(fans, row.get("tier_label")) or "—"
    data_round = row.get("data_round") or row.get("round_name") or ""
    return {
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
        "status": normalize_status(row.get("status")),
        "status_label": STATUS_LABELS.get(normalize_status(row.get("status")), normalize_status(row.get("status"))),
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
        "source": "、".join(source_keywords),
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


def _lead_dict(row: dict[str, Any]) -> dict[str, Any]:
    status = normalize_lead_status(row.get("status"))
    return {
        "id": int(row["id"]),
        "platform": row.get("platform") or "xhs",
        "platform_label": PLATFORM_LABELS.get(row.get("platform") or "xhs", row.get("platform") or "xhs"),
        "nickname": row.get("nickname") or "",
        "account_id": row.get("account_id") or "",
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
    }


def _lead_detail(db: Database, lead_id: int) -> dict[str, Any]:
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "线索不存在")
    payload = _lead_dict(lead)
    payload["raw_json"] = _safe_json_object(lead.get("raw_json"))
    payload["rounds"] = db.lead_rounds(lead_id)
    payload["attempts"] = db.lead_attempts(lead_id)
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
    if user.get("role") != "admin" and not user.get("can_collect"):
        raise HTTPException(400, "负责人必须是有采集权限的成员")


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


KeywordText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class UpdateCreatorRequest(BaseModel):
    status: str | None = Field(None, max_length=40)
    match_direction: str | None = Field(None, max_length=100)
    tags_manual: str | None = Field(None, max_length=500)
    remark: str | None = Field(None, max_length=2000)


class BatchItem(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    user_id: str = Field(min_length=1, max_length=100)


class BatchUpdateRequest(UpdateCreatorRequest):
    items: list[BatchItem] = Field(max_length=500)


class BatchClaimRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=500)


class LeadCreateRequest(BaseModel):
    platform: str = Field("xhs", min_length=1, max_length=20)
    nickname: str = Field("", max_length=200)
    account_id: str = Field("", max_length=200)
    profile_url: str = Field("", max_length=2000)
    raw_title: str = Field("", max_length=500)
    source: str = Field("manual", max_length=30)
    source_round_id: int | None = None
    owner_user_id: int | None = None
    raw_json: dict[str, Any] | None = None


class LeadUpdateRequest(BaseModel):
    nickname: str | None = Field(None, max_length=200)
    account_id: str | None = Field(None, max_length=200)
    profile_url: str | None = Field(None, max_length=2000)
    raw_title: str | None = Field(None, max_length=500)
    source: str | None = Field(None, max_length=30)
    source_round_id: int | None = None
    owner_user_id: int | None = None
    status: str | None = Field(None, max_length=30)


class LeadBulkRequest(BaseModel):
    items: list[LeadCreateRequest] = Field(min_length=1, max_length=500)


class LeadAssignRequest(BaseModel):
    owner_user_id: int | None = None


class LeadConvertRequest(BaseModel):
    account_id: str = Field("", max_length=200)
    profile_url: str = Field("", max_length=2000)
    fetch: bool = True
    provider: str = Field("auto", min_length=1, max_length=20)


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


class UserUpdateRequest(BaseModel):
    role: str | None = Field(None, max_length=20)
    disabled: bool | None = None
    can_collect: bool | None = None


class UserResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "disabled": bool(user.get("disabled")),
        "can_collect": bool(user.get("can_collect")),
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
    if version < SCHEMA_VERSION:
        raise HTTPException(503, "数据库迁移未完成")
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
    keys = [f"ip:{ip}", f"user:{username}"]
    if any(login_limiter.blocked(key) for key in keys):
        raise HTTPException(429, "登录尝试过多，请稍后再试")

    user = db.get_user_by_username(username)
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

    login_limiter.reset(keys[1])
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
        {"role": role},
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
    if payload.disabled and target["id"] == admin["id"]:
        raise HTTPException(400, "不能停用自己的账号")
    try:
        db.update_user_with_admin_guard(
            target["id"],
            role=role,
            disabled=payload.disabled,
            can_collect=payload.can_collect,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.disabled or (role is not None and role != target.get("role")):
        db.delete_user_sessions(target["id"])
    db.audit(
        admin["id"],
        "user.updated",
        target["username"],
        _client_ip(request),
        request.headers.get("user-agent", ""),
        {"role": role, "disabled": payload.disabled},
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
    db.delete_user_sessions(target["id"])
    db.audit(
        admin["id"],
        "user.sessions_revoked",
        target["username"],
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return {"ok": True}


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    return {
        "statuses": STATUS_DEFS,
        "default_status": "pending",
        "lead_statuses": LEAD_STATUS_DEFS,
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
        "credentials_ok": credentials_ok,
        "providers": providers_payload,
        "platforms": platforms_payload,
        "default_provider": platforms_payload[0].get("default_provider") if platforms_payload else None,
    }


@app.get("/api/settings")
def api_settings(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
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
    remaining_tokens = as_int(data.get("remaining_calls")) if isinstance(data, dict) else None
    return {
        "remaining": remaining_tokens,
        "remaining_native": remaining_tokens,
        "currency": "token",
        "tokens": remaining_tokens,
        "text": f"剩余 {remaining_tokens} token（后台约 10 分钟刷新一次）" if remaining_tokens is not None else "验证成功",
    }


@app.put("/api/settings/dataflow-token")
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
            raise HTTPException(400, f"令牌验证失败：{exc}") from exc

    try:
        set_key(str(ENV_PATH), "DATAFLOW_TOKEN", token, quote_mode="always")
    except Exception as exc:
        raise HTTPException(500, f"保存访问令牌失败：{exc}") from exc
    os.environ["DATAFLOW_TOKEN"] = token
    settings = candidate
    db.audit(admin["id"], "settings.dataflow_token_updated", "dataflow", _client_ip(request), request.headers.get("user-agent", ""))
    return {**_settings_payload(include_paths=True), "balance": balance}


@app.put("/api/settings/rnote-token")
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
            raise HTTPException(400, f"令牌验证失败：{exc}") from exc

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
    previous_env = os.environ.get(provider.api_key_env)
    if payload.verify:
        os.environ[provider.api_key_env] = token
        try:
            candidate_settings = load_settings(require_credentials=False)
            candidate_registry = load_registry(candidate_settings)
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
            if previous_env is None:
                os.environ.pop(provider.api_key_env, None)
            else:
                os.environ[provider.api_key_env] = previous_env
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
def api_balance(provider: str = "dataflow") -> dict[str, Any]:
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
                "text": f"查询失败：{exc}",
                "currency": "USD" if provider == "rnote" else "token",
            }
    try:
        return _fetch_extra_balance(provider)
    except Exception as exc:
        return {"remaining": None, "text": f"查询失败：{exc}", "currency": ""}


@app.get("/api/rounds")
def list_rounds(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    jobs = db.latest_jobs_by_round()
    items: list[dict[str, Any]] = []
    for row in db.list_rounds():
        payload = _round_dict(row)
        payload["job"] = _job_dict(jobs.get(int(row["id"])))
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


def _validated_owner_filter(value: str | None) -> str | None:
    if value in (None, "", "all"):
        return None
    if value == "unassigned" or value.isdigit():
        return value
    raise HTTPException(400, "无效的经手人筛选")


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
) -> dict[str, Any]:
    return {
        "statuses": _validated_statuses(status),
        "tiers": _csv(tier),
        "directions": _csv(direction),
        "sources": _csv(source),
    }


@app.get("/api/creators")
def list_all_creators(
    round_id: int | None = None,
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    sort: str = "fans",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    filters = _all_creator_filters(status, tier, direction, source)
    result = db.query_creators(
        round_id,
        platform=platform,
        search=search,
        owner=_validated_owner_filter(owner),
        min_fans=min_fans,
        max_fans=max_fans,
        only_unenriched=only_unenriched,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        **filters,
    )
    result["items"] = [_creator_dict(row) for row in result["items"]]
    return result


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
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    sort: str = "fans",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    result = db.query_creators(
        round_id,
        platform=platform,
        search=search,
        statuses=_validated_statuses(status),
        tiers=_csv(tier),
        directions=_csv(direction),
        sources=_csv(source),
        owner=_validated_owner_filter(owner),
        min_fans=min_fans,
        max_fans=max_fans,
        only_unenriched=only_unenriched,
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
    status = _validated_status(request.status)
    db.update_review(
        round_id,
        platform,
        user_id,
        status=status,
        match_direction=request.match_direction,
        tags_manual=request.tags_manual,
        remark=request.remark,
    )
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
    status = _validated_status(request.status)
    changed = db.bulk_update_review(
        round_id,
        [(item.platform, item.user_id) for item in request.items],
        status=status,
        match_direction=request.match_direction,
        tags_manual=request.tags_manual,
        remark=request.remark,
    )
    db.audit(
        actor["id"],
        "creator.review_bulk_updated",
        str(round_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={
            "changed": changed,
            "status": status,
            "match_direction": request.match_direction,
            "tags_manual": request.tags_manual,
            "remark": request.remark,
        },
    )
    return {"changed": changed}


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
        raise HTTPException(409, f"该达人已被 {owner} 认领")
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
        raise HTTPException(403, "只能释放自己认领的达人")
    result = db.release_creator(
        round_id,
        platform,
        user_id,
        expected_owner_user_id=int(current),
    )
    if not result["ok"]:
        raise HTTPException(409, "认领状态已变化，请刷新后重试")
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
    page: int = Query(1, ge=1),
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
    return db.lead_facets(round_id)


@app.get("/api/leads/owners")
def lead_owners(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.lead_owner_options()


LEAD_EXPORT_COLUMNS = [
    "昵称",
    "平台",
    "账号 ID",
    "主页 URL",
    "原始标题",
    "来源",
    "来源轮次",
    "负责人",
    "线索状态",
    "创建时间",
    "更新时间",
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
        "账号 ID": "account_id",
        "主页 URL": "profile_url",
        "原始标题": "raw_title",
        "来源": "source_label",
        "来源轮次": "source_round_name",
        "负责人": "owner_username",
        "线索状态": "status_label",
        "创建时间": "created_at",
        "更新时间": "updated_at",
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
    db: Database = Depends(get_db),
) -> Response:
    if round_id is not None and not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    statuses = _validated_lead_statuses(status)
    owner_filter = _validated_owner_filter(owner)
    if export_format == "csv":
        filename = "codata_leads.csv"

        def generate() -> Generator[bytes, None, None]:
            yield "\ufeff".encode("utf-8")
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(LEAD_EXPORT_COLUMNS)
            yield buffer.getvalue().encode("utf-8")
            with Database(settings.output_dir) as stream_db:
                for row in _iter_lead_export_rows(
                    stream_db, round_id, platform, search, statuses, source, owner_filter
                ):
                    buffer.seek(0)
                    buffer.truncate(0)
                    writer.writerow(
                        [_lead_export_value(row, column) for column in LEAD_EXPORT_COLUMNS]
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
        sheet.append(LEAD_EXPORT_COLUMNS)
        for row in _iter_lead_export_rows(
            db, round_id, platform, search, statuses, source, owner_filter
        ):
            sheet.append([_lead_export_value(row, column) for column in LEAD_EXPORT_COLUMNS])
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
    actor: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    if payload.source_round_id is not None and not db.get_round(payload.source_round_id):
        raise HTTPException(400, "来源轮次不存在")
    _validate_owner(db, payload.owner_user_id)
    _check_owner_change(actor, payload.owner_user_id)
    owner_id = payload.owner_user_id if payload.owner_user_id is not None else actor["id"]
    lead_id = db.create_lead(
        platform=payload.platform.strip().lower(),
        nickname=payload.nickname.strip(),
        account_id=payload.account_id.strip(),
        profile_url=payload.profile_url.strip(),
        raw_title=payload.raw_title.strip(),
        source=normalize_lead_source(payload.source),
        source_round_id=payload.source_round_id,
        owner_user_id=owner_id,
        raw_json=_checked_raw_json(payload.raw_json),
        created_by=actor["id"],
    )
    db.audit(actor["id"], "lead.created", str(lead_id), _client_ip(http_request), http_request.headers.get("user-agent", ""))
    return _lead_detail(db, lead_id)


@app.post("/api/leads/bulk")
def create_leads_bulk(
    payload: LeadBulkRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    created: list[int] = []
    errors: list[dict[str, Any]] = []
    for index, item in enumerate(payload.items, 1):
        try:
            if item.source_round_id is not None and not db.get_round(item.source_round_id):
                raise ValueError("来源轮次不存在")
            _validate_owner(db, item.owner_user_id)
            _check_owner_change(actor, item.owner_user_id)
            lead_id = db.create_lead(
                platform=item.platform.strip().lower(),
                nickname=item.nickname.strip(),
                account_id=item.account_id.strip(),
                profile_url=item.profile_url.strip(),
                raw_title=item.raw_title.strip(),
                source=normalize_lead_source(item.source),
                source_round_id=item.source_round_id,
                owner_user_id=item.owner_user_id if item.owner_user_id is not None else actor["id"],
                raw_json=_checked_raw_json(item.raw_json),
                created_by=actor["id"],
            )
            created.append(lead_id)
        except Exception as exc:
            errors.append({"index": index, "error": str(exc)})
    if created:
        db.audit(
            actor["id"],
            "lead.bulk_created",
            f"{len(created)} leads",
            _client_ip(http_request),
            http_request.headers.get("user-agent", ""),
        )
    return {"created": len(created), "ids": created, "errors": errors}


@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: int, db: Database = Depends(get_db)) -> dict[str, Any]:
    return _lead_detail(db, lead_id)


@app.patch("/api/leads/{lead_id}")
def update_lead(
    lead_id: int,
    payload: LeadUpdateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_collector),
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
    if "source" in changes:
        changes["source"] = normalize_lead_source(changes["source"])
    try:
        db.update_lead(lead_id, **changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        actor["id"],
        "lead.updated",
        str(lead_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"changes": changes},
    )
    return _lead_detail(db, lead_id)


@app.post("/api/leads/{lead_id}/assign")
def assign_lead(
    lead_id: int,
    payload: LeadAssignRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    actor: dict[str, Any] = Depends(require_collector),
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
    try:
        result = convert_lead(
            db,
            settings,
            lead_id,
            account_id=payload.account_id,
            profile_url=payload.profile_url,
            fetch=payload.fetch,
            provider_code=payload.provider,
            actor_id=actor["id"],
        )
    except (ValueError, RuntimeError, PlatformConfigError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"转换失败：{exc}") from exc
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
    db.reconcile_stale_jobs(settings.job_stale_minutes)
    return _job_dict(db.latest_active_job())


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
    updated = db.cancel_job(job_id)
    terminate_job_process(job.get("pid"))
    db.audit(
        actor["id"],
        "job.cancelled",
        str(job_id),
        _client_ip(http_request),
        http_request.headers.get("user-agent", ""),
        details={"round_id": job.get("round_id")},
    )
    return _job_dict(updated)


@app.get("/api/rounds/{round_id}/calls")
def recent_calls(round_id: int, db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    job = db.latest_job(round_id)
    return db.recent_api_calls(
        round_id,
        limit=30,
        job_id=int(job["id"]) if job else None,
    )


@app.get("/api/rounds/{round_id}/log")
def job_log(
    round_id: int,
    lines: int = Query(200, ge=1, le=2000),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    job = db.latest_job(round_id)
    if not job:
        return {"path": "", "content": ""}
    path = settings.output_dir / "logs" / f"round_{round_id}_{job['id']}.log"
    if not path.exists():
        return {"path": str(path), "content": ""}
    size = path.stat().st_size
    read_size = min(size, 512_000)
    with path.open("rb") as handle:
        handle.seek(size - read_size)
        text = handle.read().decode("utf-8", errors="replace")
    if read_size < size:
        text = text.split("\n", 1)[-1]
    content = text.splitlines()
    return {"path": str(path), "content": "\n".join(content[-lines:])}


def _xlsx_response(items: Iterable[dict[str, Any]], filename: str) -> FileResponse:
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    temp_path = Path(temp.name)
    temp.close()
    try:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("达人数据")
        sheet.freeze_panes = "A2"
        for index, header in enumerate(EXPORT_COLUMNS, 1):
            sheet.column_dimensions[get_column_letter(index)].width = min(
                max(10, len(header) * 2 + 4), 40
            )
        sheet.append(list(EXPORT_COLUMNS))
        for item in items:
            sheet.append([_export_value(item, header) for header in EXPORT_COLUMNS])
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
    export_format: str = Query("xlsx", alias="format", pattern="^(csv|xlsx)$"),
    scope: str = Query("filtered", pattern="^(filtered|all)$"),
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    db: Database = Depends(get_db),
) -> Response:
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    if scope == "all":
        platform = None
        search = None
        status = None
        tier = None
        direction = None
        source = None
        owner = None
        min_fans = None
        max_fans = None
        only_unenriched = False
    filters = {
        "platform": platform,
        "search": search,
        **_all_creator_filters(status, tier, direction, source),
        "owner": _validated_owner_filter(owner),
        "min_fans": min_fans,
        "max_fans": max_fans,
        "only_unenriched": only_unenriched,
    }
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", platform or "all")[:40]
    if export_format == "csv":
        filename = f"codata_round_{round_id}_{suffix}_{scope}.csv"

        def generate_csv() -> Generator[bytes, None, None]:
            yield "\ufeff".encode("utf-8")
            header_buffer = io.StringIO()
            header_writer = csv.writer(header_buffer, lineterminator="\r\n")
            header_writer.writerow(EXPORT_COLUMNS)
            yield header_buffer.getvalue().encode("utf-8")
            row_buffer = io.StringIO()
            row_writer = csv.writer(row_buffer, lineterminator="\r\n")
            with Database(settings.output_dir) as stream_db:
                for item in _iter_export_items(stream_db, round_id, filters):
                    row_buffer.seek(0)
                    row_buffer.truncate(0)
                    row_writer.writerow(
                        [_export_value(item, header) for header in EXPORT_COLUMNS]
                    )
                    yield row_buffer.getvalue().encode("utf-8")

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"codata_round_{round_id}_{suffix}_{scope}.xlsx"
    return _xlsx_response(_iter_export_items(db, round_id, filters), filename)


@app.get("/api/creators/export")
def export_all_creators(
    export_format: str = Query("xlsx", alias="format", pattern="^(csv|xlsx)$"),
    scope: str = Query("filtered", pattern="^(filtered|all)$"),
    platform: str | None = None,
    search: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    min_fans: int | None = Query(None, ge=0),
    max_fans: int | None = Query(None, ge=0),
    only_unenriched: bool = False,
    db: Database = Depends(get_db),
) -> Response:
    if scope == "all":
        platform = None
        search = None
        status = None
        tier = None
        direction = None
        source = None
        owner = None
        min_fans = None
        max_fans = None
        only_unenriched = False
    filters = {
        "platform": platform,
        "search": search,
        **_all_creator_filters(status, tier, direction, source),
        "owner": _validated_owner_filter(owner),
        "min_fans": min_fans,
        "max_fans": max_fans,
        "only_unenriched": only_unenriched,
    }
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", platform or "all")[:40]
    if export_format == "csv":
        filename = f"codata_all_{suffix}_{scope}.csv"

        def generate_csv() -> Generator[bytes, None, None]:
            yield "\ufeff".encode("utf-8")
            header_buffer = io.StringIO()
            header_writer = csv.writer(header_buffer, lineterminator="\r\n")
            header_writer.writerow(EXPORT_COLUMNS)
            yield header_buffer.getvalue().encode("utf-8")
            row_buffer = io.StringIO()
            row_writer = csv.writer(row_buffer, lineterminator="\r\n")
            with Database(settings.output_dir) as stream_db:
                for item in _iter_export_items(stream_db, None, filters):
                    row_buffer.seek(0)
                    row_buffer.truncate(0)
                    row_writer.writerow(
                        [_export_value(item, header) for header in EXPORT_COLUMNS]
                    )
                    yield row_buffer.getvalue().encode("utf-8")

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"codata_all_{suffix}_{scope}.xlsx"
    return _xlsx_response(_iter_export_items(db, None, filters), filename)


@app.get("/api/keywords")
def list_keywords(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.list_keywords()


@app.post("/api/keywords")
def create_keyword(
    request: KeywordCreateRequest,
    http_request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    actor = require_user(http_request)
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
    actor = require_user(http_request)
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
    actor = require_user(http_request)
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


@app.get("/api/dashboard")
def dashboard(
    round_id: int | None = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    counts = db.counts()
    rounds = [_round_dict(row) for row in db.list_rounds(limit=8)]
    payload: dict[str, Any] = {"counts": counts, "recent_rounds": rounds}
    if round_id is None:
        top_rows = []
        seen: set[tuple[str, str]] = set()
        for row in db.query_creators(None, sort="interaction", page_size=100)["items"]:
            key = (str(row["platform"]), str(row["user_id"]))
            if key in seen:
                continue
            seen.add(key)
            top_rows.append(row)
            if len(top_rows) >= 5:
                break
        metrics = db.all_round_metrics()
        metrics["cost"] = db.sum_round_cost(None)
        payload.update(
            {
                "round": None,
                "tier_counts": db.all_tier_counts(),
                "status_counts": db.all_status_counts(),
                "top_creators": [_creator_dict(row) for row in top_rows],
                "metrics": metrics,
                "lead_metrics": db.lead_metrics(),
            }
        )
        return payload
    if not db.get_round(round_id):
        raise HTTPException(404, "轮次不存在")
    top_rows = db.query_creators(round_id, sort="interaction", page_size=5)["items"]
    round_payload = _round_payload(db, round_id)
    metrics = db.round_metrics(round_id)
    metrics["cost"] = int(round_payload.get("summary", {}).get("cost", 0))
    payload.update(
        {
            "round": round_payload,
            "tier_counts": db.tier_counts(round_id),
            "status_counts": db.status_counts(round_id),
            "top_creators": [_creator_dict(row) for row in top_rows],
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
