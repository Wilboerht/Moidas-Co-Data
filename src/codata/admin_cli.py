from __future__ import annotations

import argparse
import getpass
import json
import sys

from .auth import hash_password, normalize_username, validate_password, validate_username
from .config import load_settings
from .db import Database
from .lead_identity import verify_lead_identity
from .matching import backfill_matches
from .platforms import load_registry
from .provider_audit import call_provider, provider_call_cost
from .providers import build_provider


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


def _refresh_creators(
    db: Database,
    settings,
    *,
    platform: str,
    provider_code: str,
    limit: int | None,
    apply: bool,
    allow_partial: bool,
) -> None:
    rows = db._conn.execute(
        """
        SELECT platform, user_id, nickname, red_id, fans, fetched_at
        FROM creators
        WHERE platform = ? AND user_id NOT LIKE 'import:%'
        ORDER BY COALESCE(fetched_at, '') ASC, user_id
        LIMIT ?
        """,
        (platform, int(limit) if limit else 1_000_000),
    ).fetchall()
    rows = list(rows)
    total = len(rows)
    print(f"达人库待刷新: {total} 条 ({platform})")
    if not total:
        return

    registry = load_registry(settings)
    if provider_code in {"auto", "cheapest", "default"}:
        options = sorted(
            [
                option
                for option in registry.options(platform)
                if option.enabled and option.ready
            ],
            key=lambda option: (
                option.user.price.tokens(registry.usd_cny, registry.token_cny),
                list(registry.platform(platform).providers).index(option.provider.id),
            ),
        )
    else:
        options = [registry.resolve(platform, provider_code)]

    selected = None
    best_shortfall = None
    for option in options:
        unit_cost = int(
            option.user.price.tokens(registry.usd_cny, registry.token_cny)
        )
        needed = unit_cost * total
        try:
            provider = build_provider(settings, option, platform)
            balance = provider.balance()
        except Exception as exc:
            print(f"- {option.provider.id}: 余额检查失败，跳过（{exc}）")
            continue
        remaining = balance.remaining_tokens
        print(
            f"- {option.provider.id}: 单价 {unit_cost} token，"
            f"余额 {remaining if remaining is not None else '未知'}，{balance.text}"
        )
        if remaining is None or remaining >= needed:
            selected = (option, provider, unit_cost)
            break
        if remaining >= unit_cost and allow_partial:
            affordable = max(1, remaining // unit_cost)
            print(
                f"  余额不足全量，按 --allow-partial 只刷新前 {affordable} 条"
            )
            rows = rows[:affordable]
            total = len(rows)
            selected = (option, provider, unit_cost)
            break
        if best_shortfall is None or remaining > best_shortfall[1]:
            best_shortfall = (option, remaining, needed, unit_cost)
        provider.close()

    if selected is None:
        if best_shortfall:
            option, remaining, needed, unit_cost = best_shortfall
            print(
                f"余额不足：最佳服务商 {option.provider.id} 需要 {needed} token，"
                f"当前 {remaining} token，缺 {max(0, needed - remaining)} token"
            )
        raise SystemExit("没有可用且有足够余额的服务商")

    option, provider, unit_cost = selected
    print(f"使用服务商: {option.provider.label} ({option.provider.id})，预计 {unit_cost * total} token")
    if not apply:
        provider.close()
        print("dry-run：未调用接口；加 --apply 执行刷新（--allow-partial 可在余额不足时部分执行）")
        return

    print(f"已备份: {db.backup('creators-refresh')}")
    success = 0
    failed = 0
    spent = 0
    try:
        for index, row in enumerate(rows, 1):
            user_id = str(row["user_id"])
            nickname = str(row["nickname"] or "")
            try:
                payload = call_provider(
                    db,
                    provider,
                    "user",
                    lambda: provider.user(user_id),
                    params={"userId": user_id},
                    round_id=None,
                    label=f"refresh_{platform}_user_{user_id}",
                )
                creator = provider.normalize_user(payload, user_id)
                db.upsert_creator(creator)
                spent += int(provider_call_cost(provider, "user")["tokens"])
                success += 1
                print(
                    f"[{index}/{total}] {user_id} "
                    f"{creator.get('nickname') or nickname} "
                    f"粉丝 {creator.get('fans')}"
                )
            except Exception as exc:
                failed += 1
                print(f"[{index}/{total}] {user_id} {nickname} 失败: {exc}")
    finally:
        provider.close()
    if db._has_fts:
        db.rebuild_search_index()
    db.audit(
        None,
        "creators.refresh",
        details={
            "platform": platform,
            "provider": option.provider.id,
            "requested": total,
            "success": success,
            "failed": failed,
        },
    )
    print(f"完成：成功 {success}，失败 {failed}，预计消耗 {spent} token")


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
    verify = sub.add_parser("leads-verify", help="校验线索账号（默认仅报告，--apply 会调用采集接口）")
    verify.add_argument("--apply", action="store_true", help="调用采集接口并写入校验结果")
    verify.add_argument("--round-id", type=int, help="只处理指定轮次")
    verify.add_argument("--limit", type=int, default=50, help="最多校验多少条线索（默认 50）")
    verify.add_argument("--provider", default="auto", help="采集服务商，默认 auto")
    refresh = sub.add_parser("creators-refresh", help="重新抓取达人库资料（默认仅报告）")
    refresh.add_argument("--apply", action="store_true", help="调用采集接口并更新达人资料")
    refresh.add_argument("--platform", default="xhs", help="平台，默认 xhs")
    refresh.add_argument("--provider", default="auto", help="采集服务商，默认自动选余额足够的最便宜项")
    refresh.add_argument("--limit", type=int, help="最多刷新多少个达人")
    refresh.add_argument(
        "--allow-partial",
        action="store_true",
        help="余额不足时只刷新余额可覆盖的部分",
    )

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
                        f"leads={'yes' if user.get('can_edit_leads') else 'no'} "
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
            elif args.command == "leads-verify":
                limit = max(1, min(500, int(args.limit or 50)))
                conditions = [
                    "l.status IN ('unverified', 'verifying')",
                    "l.identity_status <> 'verified'",
                    "(TRIM(COALESCE(l.account_id, '')) <> '' "
                    "OR TRIM(COALESCE(l.platform_handle, '')) <> '' "
                    "OR TRIM(COALESCE(l.profile_url, '')) <> '')",
                ]
                params: list[object] = []
                if args.round_id is not None:
                    conditions.append(
                        "EXISTS (SELECT 1 FROM lead_rounds lr "
                        "WHERE lr.lead_id = l.id AND lr.round_id = ?)"
                    )
                    params.append(int(args.round_id))
                where = " AND ".join(conditions)
                rows = db._conn.execute(
                    f"SELECT id, platform, nickname, account_id, platform_handle, "
                    f"identity_status FROM leads l WHERE {where} ORDER BY l.id LIMIT ?",
                    [*params, limit],
                ).fetchall()
                status_counts: dict[str, int] = {}
                for row in rows:
                    status_counts[row["identity_status"] or "unverified"] = (
                        status_counts.get(row["identity_status"] or "unverified", 0) + 1
                    )
                print(
                    f"待校验线索 {len(rows)} 条，状态分布 "
                    f"{json.dumps(status_counts, ensure_ascii=False)}"
                )
                for row in rows[:20]:
                    print(
                        f"- #{row['id']} {row['platform']} {row['nickname'] or '-'} "
                        f"账号={row['platform_handle'] or row['account_id'] or '-'}"
                    )
                if not args.apply:
                    print("dry-run：未调用采集接口；加 --apply 执行校验（会产生接口费用）")
                else:
                    print(f"已备份: {db.backup('leads-verify')}")
                    verified = mismatched = not_found = failed = 0
                    for row in rows:
                        lead = db.get_lead(int(row["id"]))
                        if not lead:
                            continue
                        try:
                            result = verify_lead_identity(
                                db,
                                settings,
                                int(row["id"]),
                                provider_code=args.provider,
                                apply=True,
                                confirm_mismatch=False,
                            )
                        except Exception as exc:
                            failed += 1
                            print(f"- #{row['id']} 校验失败: {exc}", file=sys.stderr)
                            continue
                        status = result.get("status")
                        if result.get("applied"):
                            verified += 1
                        elif status == "mismatch":
                            mismatched += 1
                        elif status == "not_found":
                            not_found += 1
                        else:
                            failed += 1
                    print(
                        f"校验完成：已确认 {verified}，待确认 {mismatched}，"
                        f"未找到 {not_found}，失败 {failed}"
                    )
                    db.audit(
                        None,
                        "leads.verify",
                        details={
                            "verified": verified,
                            "mismatch": mismatched,
                            "not_found": not_found,
                            "failed": failed,
                            "provider": args.provider,
                        },
                    )
            elif args.command == "creators-refresh":
                _refresh_creators(
                    db,
                    settings,
                    platform=args.platform,
                    provider_code=args.provider,
                    limit=args.limit,
                    apply=args.apply,
                    allow_partial=args.allow_partial,
                )
        except ValueError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
