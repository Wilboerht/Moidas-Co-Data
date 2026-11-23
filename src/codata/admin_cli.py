from __future__ import annotations

import argparse
import getpass
import sys

from .auth import hash_password, normalize_username, validate_password, validate_username
from .config import load_settings
from .db import Database
from .matching import backfill_matches


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
    backups = sub.add_parser("backups-prune", help="按保留策略清理数据库备份（默认仅报告）")
    backups.add_argument("--apply", action="store_true", help="删除超出保留策略的备份")
    dedupe = sub.add_parser("leads-dedupe", help="清理重复线索并启用唯一索引")
    dedupe.add_argument("--apply", action="store_true", help="写入清理结果（默认仅报告）")
    matches = sub.add_parser("leads-backfill-matches", help="按账号/主页回填线索与达人的匹配")
    matches.add_argument("--apply", action="store_true", help="写入匹配结果（默认仅报告）")
    matches.add_argument("--round-id", type=int, help="只处理指定轮次")
    matches.add_argument("--limit", type=int, help="最多处理多少个轮次")

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
            elif args.command == "backups-prune":
                report = db.prune_backups(
                    settings.backup_retention_days,
                    settings.backup_retention_count,
                    apply=args.apply,
                )
                print(
                    f"备份 {report['backups']} 份，待清理 {report['expired']} 份，"
                    f"约 {report['bytes']} 字节；已删除 {report['deleted']} 份"
                )
                for error in report["errors"]:
                    print(f"- {error}", file=sys.stderr)
                if not args.apply:
                    print("dry-run：未删除，加 --apply 执行清理")
            elif args.command == "leads-dedupe":
                if args.apply:
                    print(f"已备份: {db.backup('leads-dedupe')}")
                report = db.dedupe_leads(apply=args.apply)
                print(
                    f"重复组 {report['groups']}，重复线索 {report['duplicates']}，"
                    f"合并轮次关联 {report['merged_rounds']}，"
                    f"身份规范化 {report.get('identity_updates', 0)}，"
                    f"字段冲突组 {report.get('conflict_groups', 0)}"
                )
                for item in report["samples"]:
                    print(
                        f"- 保留 #{item['keep']}，标记重复 {item['drop']} "
                        f"({item['platform']} / {item['account_id'] or '-'})"
                    )
                if args.apply:
                    indexes = report.get("indexes") or {}
                    for name, ready in indexes.items():
                        print(f"唯一索引 {name}: {'已启用' if ready else '未启用（仍有重复）'}")
                    db.audit(
                        None,
                        "leads.dedupe",
                        details={
                            "groups": report["groups"],
                            "duplicates": report["duplicates"],
                            "merged_rounds": report["merged_rounds"],
                            "conflict_groups": report.get("conflict_groups", 0),
                        },
                    )
                else:
                    print("dry-run：未写入，加 --apply 执行清理")
            elif args.command == "leads-backfill-matches":
                if args.apply:
                    print(f"已备份: {db.backup('leads-backfill-matches')}")
                report = backfill_matches(
                    db,
                    apply=args.apply,
                    round_id=args.round_id,
                    limit=args.limit,
                )
                print(
                    f"扫描轮次 {report['rounds']}，达人 {report['creators']}，"
                    f"命中线索 {report['matched']}，更新 {report['updated']}，"
                    f"冲突 {report['ambiguous']}，疑似昵称 {report['suggested']}，"
                    f"匹配记录 {report['attempts']}"
                )
                for item in report["examples"]:
                    print(
                        f"- 线索 #{item['lead_id']} → 达人 {item['user_id']} "
                        f"({item['confidence']})"
                    )
                if args.apply:
                    db.audit(
                        None,
                        "leads.backfill_matches",
                        details={
                            "rounds": report["rounds"],
                            "matched": report["matched"],
                            "updated": report["updated"],
                            "ambiguous": report["ambiguous"],
                        },
                    )
                else:
                    print("dry-run：未写入，加 --apply 执行匹配")
        except ValueError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
