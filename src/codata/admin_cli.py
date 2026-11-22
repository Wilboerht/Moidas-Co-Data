from __future__ import annotations

import argparse
import getpass
import sys

from .auth import hash_password, normalize_username, validate_password, validate_username
from .config import load_settings
from .db import Database


def _read_password(provided: str | None) -> str:
    if provided is not None:
        return validate_password(provided)
    password = getpass.getpass("密码: ")
    confirm = getpass.getpass("确认密码: ")
    if password != confirm:
        raise ValueError("两次输入的密码不一致")
    return validate_password(password)


def _create_admin(db: Database, username: str, password: str | None) -> None:
    normalized = validate_username(username)
    password_hash = hash_password(_read_password(password))
    user_id = db.create_user(normalized, password_hash, role="admin")
    print(f"已创建管理员 #{user_id} {normalized}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="codata-admin", description="Co-data 账号管理")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="创建管理员账号")
    create.add_argument("--username", required=True)
    create.add_argument("--password", help="仅用于自动化；交互使用时省略")

    ensure = sub.add_parser("ensure-admin", help="没有任何用户时创建首个管理员")
    ensure.add_argument("--username", default="admin")
    ensure.add_argument("--password", help="仅用于自动化；交互使用时省略")

    reset = sub.add_parser("reset-password", help="重置账号密码并注销其会话")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password", help="仅用于自动化；交互使用时省略")

    sub.add_parser("list-users", help="列出账号")

    args = parser.parse_args(argv)
    settings = load_settings(require_credentials=False)
    with Database(settings.output_dir) as db:
        try:
            if args.command == "create-admin":
                _create_admin(db, args.username, args.password)
            elif args.command == "ensure-admin":
                if db.count_users() > 0:
                    print("已有账号，跳过初始化")
                else:
                    _create_admin(db, args.username, args.password)
            elif args.command == "reset-password":
                username = normalize_username(args.username)
                user = db.get_user_by_username(username)
                if not user:
                    raise ValueError("用户不存在")
                password_hash = hash_password(_read_password(args.password))
                db.update_user(
                    user["id"],
                    password_hash=password_hash,
                    must_change_password=True,
                )
                db.delete_user_sessions(user["id"])
                print(f"已重置 {username} 的密码，并注销其会话")
            elif args.command == "list-users":
                users = db.list_users()
                if not users:
                    print("暂无账号")
                for user in users:
                    status = "停用" if user["disabled"] else "启用"
                    print(
                        f"#{user['id']} {user['username']} role={user['role']} "
                        f"{status} collect={'yes' if user.get('can_collect') else 'no'} "
                        f"last_login={user['last_login_at'] or '-'}"
                    )
        except ValueError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
