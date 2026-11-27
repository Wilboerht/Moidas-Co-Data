from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .auth import hash_password, normalize_username, validate_password, validate_username
from .config import PROJECT_ROOT, load_settings, runtime_env_path
from .db import Database, SCHEMA_VERSION
from .diagnostics import config_warnings, status_report
from .email_crypto import default_key_path, key_fingerprint, load_key
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_integrity(path: Path) -> tuple[bool, str, int | None]:
    try:
        conn = sqlite3.connect(path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc), None
    return str(result).lower() == "ok", str(result), version


def _verify_backup(source: Path) -> dict:
    source = Path(source).expanduser()
    errors: list[str] = []
    warnings: list[str] = []
    db_path = source / "codata.sqlite"
    manifest_path = source / "manifest.json"
    schema_version: int | None = None
    if not db_path.is_file():
        errors.append("缺少 codata.sqlite")
    else:
        ok, detail, schema_version = _db_integrity(db_path)
        if not ok:
            errors.append(f"数据库完整性检查失败: {detail}")
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"manifest.json 无法解析: {exc}")
        else:
            root = source.resolve()
            for item in manifest.get("files") or []:
                relative = str(item.get("path") or "")
                if not relative:
                    continue
                path = (source / relative).resolve()
                if not path.is_relative_to(root):
                    errors.append(f"manifest 路径越界: {relative}")
                elif not path.is_file():
                    errors.append(f"缺少文件: {relative}")
                elif _sha256(path) != item.get("sha256"):
                    errors.append(f"校验和不匹配: {relative}")
            schema_version = manifest.get("schema_version", schema_version)
            backup_key = manifest.get("email_key_fingerprint") or ""
            current_key = key_fingerprint() or ""
            if backup_key and current_key and backup_key != current_key:
                warnings.append(
                    "备份的邮件加密密钥指纹与当前密钥不一致；恢复后需使用原密钥才能解密 SMTP 密码"
                )
    else:
        warnings.append("备份没有 manifest.json，仅检查数据库完整性")
    if schema_version is not None and schema_version > SCHEMA_VERSION:
        errors.append(
            f"备份 schema v{schema_version} 高于当前程序 v{SCHEMA_VERSION}"
        )
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "schema_version": schema_version,
        "has_manifest": manifest_path.is_file(),
    }


def _backup(
    db: Database,
    settings,
    out_dir: str | None,
    *,
    project_root: Path = PROJECT_ROOT,
    runtime_env: Path | None = None,
) -> None:
    base = Path(out_dir).expanduser() if out_dir else settings.output_dir / "backups"
    if not base.is_absolute():
        base = project_root / base
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = base / f"codata-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    staged = db.backup("manual")
    shutil.copy2(staged, target / "codata.sqlite")
    staged.unlink(missing_ok=True)
    copied = ["codata.sqlite"]
    env_path = project_root / ".env"
    if env_path.is_file():
        shutil.copy2(env_path, target / ".env")
        copied.append(".env")
    runtime = runtime_env if runtime_env is not None else runtime_env_path()
    if runtime.is_file() and runtime.resolve() != env_path.resolve():
        shutil.copy2(runtime, target / "runtime.env")
        copied.append("runtime.env")
    attachments = settings.output_dir / "cooperation_attachments"
    if attachments.is_dir():
        shutil.copytree(
            attachments, target / "cooperation_attachments", dirs_exist_ok=True
        )
        copied.append("cooperation_attachments")
    files = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(target).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": int(db._conn.execute("PRAGMA user_version").fetchone()[0]),
        "email_key_fingerprint": key_fingerprint(),
        "files": files,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"备份完成: {target}")
    print(f"包含: {', '.join(copied)}")
    report = _verify_backup(target)
    if report["ok"]:
        print("备份校验: 通过")
    else:
        for error in report["errors"]:
            print(f"备份校验失败: {error}", file=sys.stderr)


def _restore(
    settings,
    source: str,
    *,
    check_only: bool,
    yes: bool,
    project_root: Path = PROJECT_ROOT,
    runtime_env: Path | None = None,
) -> None:
    source_dir = Path(source).expanduser()
    report = _verify_backup(source_dir)
    print(f"备份目录: {source_dir}")
    print(
        f"schema: {report['schema_version'] if report['schema_version'] is not None else '未知'}"
        f" · manifest: {'有' if report['has_manifest'] else '无'}"
    )
    for warning in report["warnings"]:
        print(f"提示: {warning}")
    if not report["ok"]:
        for error in report["errors"]:
            print(f"校验失败: {error}", file=sys.stderr)
        raise SystemExit("备份校验失败，已中止恢复")
    if check_only:
        print("备份校验通过（未写入任何数据）")
        return
    if not yes:
        raise SystemExit("恢复会覆盖当前数据库、env 和合作附件；确认后加 --yes")
    runtime_env = runtime_env if runtime_env is not None else runtime_env_path()
    try:
        with Database(settings.output_dir) as db:
            print(f"已备份当前数据库: {db.backup('restore-before')}")
    except Exception as exc:
        print(f"提示: 当前数据库无法备份（{exc}），继续恢复")
    target_db = settings.output_dir / "codata.sqlite"
    for suffix in ("-wal", "-shm", "-journal"):
        (settings.output_dir / f"codata.sqlite{suffix}").unlink(missing_ok=True)
    try:
        shutil.copy2(source_dir / "codata.sqlite", target_db)
    except OSError as exc:
        raise SystemExit(f"无法覆盖数据库（面板可能仍在运行）: {exc}") from exc
    env_src = source_dir / ".env"
    if env_src.is_file():
        shutil.copy2(env_src, project_root / ".env")
        print(f"已恢复: {project_root / '.env'}")
    runtime_src = source_dir / "runtime.env"
    if runtime_src.is_file():
        runtime_env.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(runtime_src, runtime_env)
        print(f"已恢复: {runtime_env}")
    attachments_src = source_dir / "cooperation_attachments"
    if attachments_src.is_dir():
        attachments_target = settings.output_dir / "cooperation_attachments"
        shutil.rmtree(attachments_target, ignore_errors=True)
        shutil.copytree(attachments_src, attachments_target)
        print(f"已恢复: {attachments_target}")
    ok, detail, _schema = _db_integrity(target_db)
    if not ok:
        raise SystemExit(f"恢复后的数据库完整性检查失败: {detail}")
    print("恢复完成；请重启面板使数据生效。")


def _config_check(settings) -> None:
    print(f"数据目录: {settings.output_dir}")
    print(f"允许 Host: {', '.join(settings.allowed_hosts)}")
    print(f"允许 Origin: {', '.join(settings.allowed_origins)}")
    print(
        "Cookie Secure: "
        f"{settings.cookie_secure} · Docs: {settings.enable_docs} · "
        f"会话 {settings.session_days} 天"
    )
    print(
        "保留策略: "
        f"审计 {settings.audit_retention_days} 天 · 日志 {settings.log_retention_days} 天 · "
        f"原始响应 {settings.raw_retention_days} 天 · API 调用 {settings.api_call_retention_days} 天 · "
        f"备份 {settings.backup_retention_days} 天/{settings.backup_retention_count} 份"
    )
    try:
        registry = load_registry(settings)
    except Exception as exc:
        print(f"平台配置错误: {exc}")
    else:
        for code, platform in registry.platforms.items():
            if not platform.enabled:
                continue
            options = [
                f"{item.provider.id}={'ready' if item.ready else '未配置'}"
                for item in platform.providers.values()
                if item.enabled
            ]
            print(f"平台 {code}: {', '.join(options) or '无可用服务商'}")
    key_path = default_key_path()
    fingerprint = key_fingerprint()
    print(
        f"邮件加密密钥: {key_path} · "
        f"{'已初始化 ' + (fingerprint or '') if fingerprint else '未初始化'}"
    )
    if fingerprint is None:
        print("提示: 使用自动发件前运行 codata-admin email-key --init")
    enabled = {"1", "true", "yes", "on"}
    print(
        "SMTP 内网地址: "
        f"{'允许' if os.getenv('CODATA_SMTP_ALLOW_PRIVATE', '').lower() in enabled else '禁止'}"
    )
    print(
        "SMTP 明文连接: "
        f"{'允许' if os.getenv('CODATA_SMTP_ALLOW_PLAINTEXT', '').lower() in enabled else '禁止'}"
    )
    warnings = config_warnings(settings)
    if warnings:
        for warning in warnings:
            print(f"警告: {warning}")
    else:
        print("配置检查: 未发现明显风险")


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
    sub.add_parser("config-check", help="打印生效配置与安全告警")
    sub.add_parser("status", help="打印数据库、任务、备份和磁盘状态")
    verify_backup = sub.add_parser("backup-verify", help="校验备份目录的完整性和校验和")
    verify_backup.add_argument("--path", required=True, help="备份目录")
    restore = sub.add_parser("restore", help="从备份目录恢复数据库、env 和合作附件")
    restore.add_argument("--from", dest="source", required=True, help="备份目录")
    restore.add_argument("--yes", action="store_true", help="确认覆盖当前数据")
    restore.add_argument("--check-only", action="store_true", help="只校验、不恢复")
    email_key = sub.add_parser("email-key", help="查看或初始化邮件账号加密密钥")
    email_key.add_argument("--init", action="store_true", help="生成密钥文件")
    backup = sub.add_parser("backup", help="备份数据库、环境文件和合作附件")
    backup.add_argument("--out", help="备份目录，默认 data/backups")
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
    if args.command == "backup-verify":
        report = _verify_backup(Path(args.path))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["ok"] else 1)
    if args.command == "email-key":
        path = default_key_path()
        if args.init:
            load_key(create=True)
            print(f"已初始化邮件加密密钥: {path}")
        fingerprint = key_fingerprint()
        print(f"密钥文件: {path}")
        print(f"状态: {'已初始化 ' + (fingerprint or '') if fingerprint else '未初始化'}")
        return
    if args.command == "restore":
        _restore(
            settings,
            args.source,
            check_only=args.check_only,
            yes=args.yes,
        )
        return
    if args.command == "config-check":
        _config_check(settings)
        return
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
            elif args.command == "status":
                print(json.dumps(status_report(db, settings), ensure_ascii=False, indent=2))
            elif args.command == "backup":
                _backup(db, settings, args.out)
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
