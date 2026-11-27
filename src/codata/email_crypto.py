from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CODATA_EMAIL_KEY_FILE"
_KEY_LOCK = threading.Lock()


class EmailKeyError(RuntimeError):
    """Raised when the email encryption key is missing or cannot decrypt data."""


def default_key_path() -> Path:
    custom = os.getenv(KEY_ENV, "").strip()
    if custom:
        return Path(custom).expanduser()
    if os.name == "nt":
        base = os.getenv("APPDATA") or str(Path.home())
        return Path(base) / "codata" / "email.key"
    return Path.home() / ".config" / "codata" / "email.key"


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_key(target: Path) -> bytes:
    raw = target.read_bytes().strip()
    if not raw:
        raise EmailKeyError(f"邮件加密密钥文件为空: {target}")
    try:
        Fernet(raw)
    except (ValueError, TypeError) as exc:
        raise EmailKeyError(
            f"邮件加密密钥文件无效: {target}；请确认文件未被修改或覆盖"
        ) from exc
    return raw


def load_key(path: Path | None = None, *, create: bool = False) -> bytes:
    target = Path(path) if path is not None else default_key_path()
    if target.is_file():
        return _read_key(target)
    if not create:
        raise EmailKeyError(
            f"缺少邮件加密密钥: {target}；先运行 codata-admin email-key --init"
        )
    with _KEY_LOCK:
        if target.is_file():
            return _read_key(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return _read_key(target)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_private(target)
        return key


def key_fingerprint(path: Path | None = None) -> str | None:
    try:
        key = load_key(path)
    except EmailKeyError:
        return None
    return hashlib.sha256(key).hexdigest()[:12]


def encrypt_secret(value: str, path: Path | None = None) -> str:
    if not value:
        raise EmailKeyError("邮件账号密码不能为空")
    return Fernet(load_key(path, create=True)).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, path: Path | None = None) -> str:
    if not token:
        return ""
    try:
        return Fernet(load_key(path)).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise EmailKeyError(
            "无法解密邮件账号密码：加密密钥缺失、已更换或与账号不匹配"
        ) from exc
