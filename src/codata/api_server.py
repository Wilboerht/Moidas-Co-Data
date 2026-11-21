from __future__ import annotations

import csv
import io
import json
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
from .db import Database, JobBusyError
from .normalize import as_int, fans_tier, format_fans_wan, split_tags
from .platforms import PlatformConfigError, load_registry
from .providers import build_provider
from .panel.queries import EXPORT_COLUMNS, JOB_STATUS_LABELS, PLATFORM_LABELS, ROUND_STATUS_LABELS
from .panel.runner import start_round_job
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
            if not csrf or csrf != session["session"]["csrf_token"]:
                return JSONResponse({"detail": "CSRF 校验失败"}, status_code=403)
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
    "执行者": "executor",
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
    tier_label = row.get("tier_label") or (fans_tier(fans) if fans is not None else "—")
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
        "executor": row.get("executor") or "",
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
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


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
    return {"ok": True}


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
    if (
        target.get("role") == "admin"
        and (payload.disabled or (role is not None and role != "admin"))
        and db.count_active_admins() <= 1
    ):
        raise HTTPException(400, "至少保留一个可用管理员")
    db.update_user(
        target["id"],
        role=role,
        disabled=payload.disabled,
        can_collect=payload.can_collect,
    )
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
        "platforms": [
            {"code": code, "label": label} for code, label in PLATFORM_LABELS.items()
        ],
    }


def _settings_payload() -> dict[str, Any]:
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
        "output_dir": str(settings.output_dir),
        "cache_days": settings.cache_days,
        "xhs_cost": xhs_cost,
        "douyin_cost": DOUYIN_COST,
        "credentials_ok": credentials_ok,
        "providers": providers_payload,
        "platforms": platforms_payload,
        "default_provider": platforms_payload[0].get("default_provider") if platforms_payload else None,
    }


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    return _settings_payload()


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
    return {**_settings_payload(), "balance": balance}


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
    return {**_settings_payload(), "balance": balance}


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
    try:
        set_key(str(ENV_PATH), provider.api_key_env, token, quote_mode="always")
    except Exception as exc:
        raise HTTPException(500, f"保存访问令牌失败：{exc}") from exc
    os.environ[provider.api_key_env] = token
    settings = load_settings(require_credentials=False)
    db.audit(
        admin["id"],
        "settings.provider_token_updated",
        provider_id,
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return {**_settings_payload(), "balance": balance, "verified": verified}


def _fetch_extra_balance(provider_id: str) -> dict[str, Any]:
    registry = load_registry(settings)
    provider = registry.provider(provider_id)
    if not provider.configured:
        return {"remaining": None, "text": "??? API Key", "currency": provider.balance_currency}
    option = None
    platform_code = "xhs"
    for platform in registry.platforms.values():
        if provider_id in platform.providers:
            option = platform.providers[provider_id]
            platform_code = platform.code
            break
    if option is None:
        return {"remaining": None, "text": "????????????", "currency": provider.balance_currency}
    candidate = build_provider(settings, option, platform_code)
    try:
        if not getattr(candidate, "supports_balance", False):
            return {"remaining": None, "text": f"{provider.label} ???????", "currency": provider.balance_currency}
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
                return {"remaining": None, "text": "??? RNOTE_API_KEY", "currency": "USD"}
        elif not settings.host or not settings.token:
            return {"remaining": None, "text": "??? Host/Token", "currency": "token"}
        try:
            return _fetch_balance(settings, provider)
        except Exception as exc:
            return {
                "remaining": None,
                "text": f"?????{exc}",
                "currency": "USD" if provider == "rnote" else "token",
            }
    try:
        return _fetch_extra_balance(provider)
    except Exception as exc:
        return {"remaining": None, "text": f"?????{exc}", "currency": ""}


@app.get("/api/rounds")
def list_rounds(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return [_round_dict(row) for row in db.list_rounds()]


@app.post("/api/rounds")
def create_round(
    request: CreateRoundRequest,
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
        raise HTTPException(400, f"创建轮次失败：{exc}") from exc

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
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"轮次已创建，但任务启动失败：{exc}") from exc
    return {"round": _round_payload(db, round_id), "job_id": job_id}


@app.get("/api/rounds/{round_id}")
def get_round(round_id: int, db: Database = Depends(get_db)) -> dict[str, Any]:
    return _round_payload(db, round_id)


@app.post("/api/rounds/{round_id}/run")
def run_round(
    round_id: int,
    request: RunRoundRequest,
    db: Database = Depends(get_db),
    collector: dict[str, Any] = Depends(require_collector),
) -> dict[str, Any]:
    round_row = db.get_round(round_id)
    if not round_row:
        raise HTTPException(404, "轮次不存在")
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
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"任务启动失败：{exc}") from exc
    return {"job_id": job_id, "log": str(log_path), "job": _job_dict(db.latest_job(round_id))}


def _all_creator_filters(
    status: str | None,
    tier: str | None,
    direction: str | None,
    source: str | None,
) -> dict[str, Any]:
    status_codes = [normalize_status(item, default="") for item in _csv(status)]
    return {
        "statuses": [code for code in status_codes if code],
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
    status_codes = [normalize_status(item, default="") for item in _csv(status)]
    result = db.query_creators(
        round_id,
        platform=platform,
        search=search,
        statuses=[code for code in status_codes if code],
        tiers=_csv(tier),
        directions=_csv(direction),
        sources=_csv(source),
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
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    db.update_review(
        round_id,
        platform,
        user_id,
        status=_validated_status(request.status),
        match_direction=request.match_direction,
        tags_manual=request.tags_manual,
        remark=request.remark,
    )
    return _detail_payload(db, round_id, platform, user_id)


@app.post("/api/rounds/{round_id}/creators/batch")
def batch_update(
    round_id: int,
    request: BatchUpdateRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    changed = db.bulk_update_review(
        round_id,
        [(item.platform, item.user_id) for item in request.items],
        status=_validated_status(request.status),
        match_direction=request.match_direction,
        tags_manual=request.tags_manual,
        remark=request.remark,
    )
    return {"changed": changed}


@app.get("/api/rounds/{round_id}/job")
def latest_job(round_id: int, db: Database = Depends(get_db)) -> dict[str, Any] | None:
    return _job_dict(db.latest_job(round_id))


@app.get("/api/rounds/{round_id}/calls")
def recent_calls(round_id: int, db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.recent_api_calls(round_id, limit=30)


@app.get("/api/rounds/{round_id}/log")
def job_log(
    round_id: int,
    lines: int = Query(200, ge=1, le=2000),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
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
        min_fans = None
        max_fans = None
        only_unenriched = False
    status_codes = [normalize_status(item, default="") for item in _csv(status)]
    filters = {
        "platform": platform,
        "search": search,
        "statuses": [code for code in status_codes if code],
        "tiers": _csv(tier),
        "directions": _csv(direction),
        "sources": _csv(source),
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
        min_fans = None
        max_fans = None
        only_unenriched = False
    filters = {
        "platform": platform,
        "search": search,
        **_all_creator_filters(status, tier, direction, source),
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
def create_keyword(request: KeywordCreateRequest, db: Database = Depends(get_db)) -> dict[str, Any]:
    keyword_id = db.ensure_keyword(request.word, request.category)
    if not request.enabled:
        db.update_keyword(keyword_id, enabled=False)
    return {"id": keyword_id}


@app.patch("/api/keywords/{keyword_id}")
def update_keyword(
    keyword_id: int,
    request: KeywordUpdateRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    db.update_keyword(keyword_id, category=request.category, enabled=request.enabled)
    return {"ok": True}


@app.delete("/api/keywords/{keyword_id}")
def delete_keyword(keyword_id: int, db: Database = Depends(get_db)) -> dict[str, Any]:
    try:
        db.delete_keyword(keyword_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(
    round_id: int | None = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    counts = db.counts()
    rounds = [_round_dict(row) for row in db.list_rounds()[:8]]
    payload: dict[str, Any] = {"counts": counts, "recent_rounds": rounds}
    if round_id is None:
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
