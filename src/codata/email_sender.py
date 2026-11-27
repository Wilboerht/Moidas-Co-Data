from __future__ import annotations

import ipaddress
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from .db import normalize_email_address


class EmailSendError(RuntimeError):
    """Raised when SMTP connection, authentication or delivery fails."""

    def __init__(self, message: str, *, kind: str = "transient") -> None:
        super().__init__(message)
        self.kind = kind


def _allow_plaintext() -> bool:
    return os.getenv("CODATA_SMTP_ALLOW_PLAINTEXT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _allow_private() -> bool:
    return os.getenv("CODATA_SMTP_ALLOW_PRIVATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validate_host(host: str) -> str:
    host = str(host or "").strip().lower()
    if not host or any(ch in host for ch in " \r\n/:"):
        raise EmailSendError("SMTP 主机格式不正确", kind="config")
    if _allow_private():
        return host
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, None)
        }
    except socket.gaierror as exc:
        raise EmailSendError(f"无法解析 SMTP 主机: {host}", kind="config") from exc
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
            raise EmailSendError(
                "SMTP 主机解析到内网地址，已拒绝；如确需内网邮件服务器，"
                "请设置 CODATA_SMTP_ALLOW_PRIVATE=true",
                kind="config",
            )
    return host


def _clean_header(value: str, limit: int = 200) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _close_server(server: smtplib.SMTP) -> None:
    try:
        server.quit()
    except (OSError, smtplib.SMTPException):
        try:
            server.close()
        except OSError:
            pass


def _validate_peer(server: smtplib.SMTP) -> None:
    if _allow_private():
        return
    sock = getattr(server, "sock", None)
    if sock is None:
        return
    try:
        address = str(sock.getpeername()[0])
        parsed = ipaddress.ip_address(address)
    except (AttributeError, IndexError, OSError, ValueError) as exc:
        raise EmailSendError("无法确认 SMTP 实际连接地址", kind="config") from exc
    if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
        raise EmailSendError(
            "SMTP 实际连接到内网地址，已拒绝；如确需内网邮件服务器，"
            "请设置 CODATA_SMTP_ALLOW_PRIVATE=true",
            kind="config",
        )


def _open_server(account: dict[str, Any], timeout: int = 15) -> smtplib.SMTP:
    host = _validate_host(str(account.get("host") or ""))
    port = int(account.get("port") or 0)
    if port < 1 or port > 65535:
        raise EmailSendError("SMTP 端口无效", kind="config")
    security = str(account.get("security") or "starttls").strip().lower()
    if security == "none" and not _allow_plaintext():
        raise EmailSendError(
            "未启用明文 SMTP；如需连接无加密的邮件服务器，"
            "请设置 CODATA_SMTP_ALLOW_PLAINTEXT=true",
            kind="config",
        )
    context = ssl.create_default_context()
    server: smtplib.SMTP | None = None
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=context
            )
            _validate_peer(server)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            _validate_peer(server)
            server.ehlo()
            if security == "starttls":
                server.starttls(context=context)
                server.ehlo()
    except EmailSendError:
        if server is not None:
            _close_server(server)
        raise
    except (OSError, smtplib.SMTPException) as exc:
        if server is not None:
            _close_server(server)
        raise EmailSendError(f"连接 SMTP 失败: {exc}") from exc
    return server


def _login(server: smtplib.SMTP, account: dict[str, Any], password: str) -> None:
    try:
        server.login(str(account.get("username") or ""), password)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailSendError(
            "SMTP 认证失败，请检查用户名和授权码/应用密码；"
            "部分企业邮箱需要开启 SMTP 或使用客户端专用密码",
            kind="auth",
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailSendError(f"SMTP 登录失败: {exc}") from exc


def test_smtp(
    account: dict[str, Any],
    password: str,
    *,
    send_test_to: str = "",
    timeout: int = 15,
) -> dict[str, Any]:
    server = _open_server(account, timeout=timeout)
    try:
        _login(server, account, password)
        if send_test_to:
            recipient = normalize_email_address(send_test_to)
            message = EmailMessage()
            message["Subject"] = "Co-data SMTP 测试"
            message["From"] = formataddr(
                (
                    _clean_header(str(account.get("from_name") or "")),
                    normalize_email_address(str(account.get("from_email") or "")),
                )
            )
            message["To"] = recipient
            message["Message-ID"] = make_msgid(
                domain=normalize_email_address(
                    str(account.get("from_email") or "")
                ).split("@")[-1]
            )
            message.set_content("这是一封来自 Co-data 的 SMTP 连接测试邮件。")
            server.send_message(message)
    except EmailSendError:
        raise
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        raise EmailSendError(f"SMTP 测试失败: {exc}") from exc
    finally:
        _close_server(server)
    return {"ok": True, "sent_test": bool(send_test_to)}


def send_plain_email(
    account: dict[str, Any],
    password: str,
    *,
    to: str,
    subject: str,
    body: str,
    message_id: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> str:
    recipient = normalize_email_address(to)
    from_email = normalize_email_address(str(account.get("from_email") or ""))
    message = EmailMessage()
    message["Subject"] = _clean_header(subject, 200)
    message["From"] = formataddr(
        (_clean_header(str(account.get("from_name") or "")), from_email)
    )
    message["To"] = recipient
    reply_to = str(account.get("reply_to") or "").strip()
    if reply_to:
        message["Reply-To"] = normalize_email_address(reply_to)
    if message_id is not None and (
        not message_id.strip() or "\r" in message_id or "\n" in message_id
    ):
        raise EmailSendError("Message-ID 格式无效", kind="config")
    message["Message-ID"] = message_id or make_msgid(domain=from_email.split("@")[-1])
    for name, value in (headers or {}).items():
        cleaned_name = _clean_header(name, 80)
        cleaned_value = _clean_header(value, 1000)
        if cleaned_name and cleaned_value:
            message[cleaned_name] = cleaned_value
    message.set_content(body)
    server = _open_server(account, timeout=timeout)
    try:
        _login(server, account, password)
        try:
            server.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            kind = "unknown"
            if isinstance(exc, smtplib.SMTPRecipientsRefused):
                kind = "permanent"
            elif isinstance(exc, smtplib.SMTPResponseException):
                kind = "permanent" if int(exc.smtp_code or 0) >= 500 else "transient"
            raise EmailSendError(f"发送失败: {exc}", kind=kind) from exc
    finally:
        _close_server(server)
    return str(message["Message-ID"])
