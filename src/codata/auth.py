from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

SESSION_COOKIE = "codata_session"
CSRF_HEADER = "x-csrf-token"
ROLES = {"admin", "member"}
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
USERNAME_RE = re.compile(r"^[\w.@-]{2,32}$", re.UNICODE)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("用户名需为 2-32 个字符，仅支持字母、数字、下划线、点、@ 和 -")
    return normalized


def validate_password(password: str) -> str:
    value = password or ""
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
    if len(value) > MAX_PASSWORD_LENGTH:
        raise ValueError("密码过长")
    return value


def hash_password(password: str, validate: bool = True) -> str:
    value = validate_password(password) if validate else (password or "")
    if not value:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        value.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.scrypt(
            (password or "").encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def session_expiry(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=max(1, days))).isoformat(timespec="seconds")


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 10, window_seconds: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        attempts = [stamp for stamp in self._attempts.get(key, []) if now - stamp < self.window_seconds]
        if attempts:
            self._attempts[key] = attempts
        else:
            self._attempts.pop(key, None)
        return attempts

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return len(self._prune(key, now)) >= self.max_attempts

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._attempts.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
