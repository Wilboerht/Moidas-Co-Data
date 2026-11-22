from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

from .config import load_settings
from .db import Database
from .platforms import load_registry
from .providers import build_provider
from .providers_multi import EveryInfraProvider


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def extract_nickname(raw_json: Any, fallback: str = "") -> str:
    try:
        data = json.loads(raw_json or "{}")
    except (TypeError, json.JSONDecodeError):
        data = {}
    if isinstance(data, dict):
        for key in ("昵称", "nickname", "nick_name", "name", "display_name"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(fallback or "").strip()


def candidate_name(item: dict[str, Any]) -> str:
    for key in ("nickname", "nick_name", "name", "display_name", "user_name", "username"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def candidate_id(item: dict[str, Any]) -> str:
    for key in ("user_id", "userId", "userid", "id", "uid"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def candidate_followers(item: dict[str, Any]) -> int:
    for key in ("follower_count", "fans", "fans_count", "followers"):
        value = item.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def choose_exact(items: list[dict[str, Any]], nickname: str) -> tuple[dict[str, Any] | None, str]:
    raw_exact = [item for item in items if candidate_name(item).strip() == nickname.strip()]
    candidates = raw_exact or [
        item for item in items if normalize_name(candidate_name(item)) == normalize_name(nickname)
    ]
    if not candidates:
        return None, "no exact match"
    if len(candidates) == 1:
        return candidates[0], ""
    ranked = sorted(candidates, key=candidate_followers, reverse=True)
    if candidate_followers(ranked[0]) <= 0 or candidate_followers(ranked[0]) == candidate_followers(ranked[1]):
        return None, "ambiguous exact matches"
    return ranked[0], f"ambiguous, chose highest followers ({candidate_followers(ranked[0])})"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="codata-backfill", description="按昵称精确匹配并补全待审核导入达人")
    parser.add_argument("--platform", default="xhs")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--retry-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings(require_credentials=True)
    with Database(settings.output_dir) as db:
        candidates = db.backfill_candidates(args.platform, args.limit, args.retry_days)
        print(f"待处理: {len(candidates)} 条 ({args.platform})")
        if not candidates:
            return
        for item in candidates:
            nickname = extract_nickname(item.get("raw_json"), item.get("nickname") or "")
            print(f"- {item['user_id']} | {nickname or '(无昵称)'}")
        if args.dry_run:
            print("dry-run，不发起 API 请求")
            return

        registry = load_registry(settings)
        option = registry.resolve(args.platform, "everyinfra")
        provider = build_provider(settings, option, args.platform)
        if not isinstance(provider, EveryInfraProvider):
            provider.close()
            raise SystemExit("该平台当前没有可用的 EveryInfra 适配器")

        matched = 0
        skipped = 0
        failed = 0
        try:
            for index, item in enumerate(candidates, 1):
                imported_id = item["user_id"]
                nickname = extract_nickname(item.get("raw_json"), item.get("nickname") or "")
                if not nickname:
                    db.record_backfill_attempt(args.platform, imported_id, "no_match", error="empty nickname")
                    skipped += 1
                    print(f"[{index}/{len(candidates)}] {imported_id}: 无昵称，跳过")
                    continue
                try:
                    started = time.perf_counter()
                    result = provider.search_users(nickname)
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    search_price = provider.search_price
                    db.log_api_call(
                        None,
                        args.platform,
                        "search_users",
                        {"keyword": nickname},
                        None,
                        200,
                        provider.search_cost_tokens,
                        duration_ms,
                        path=provider.search_path,
                        provider=provider.provider_id,
                        cost_native=float(search_price.amount) if search_price else 0.0,
                        cost_currency=search_price.currency if search_price else "token",
                    )
                    selected, note = choose_exact(result.items, nickname)
                    if selected is None:
                        db.record_backfill_attempt(
                            args.platform,
                            imported_id,
                            "no_match",
                            query=nickname,
                            error=note,
                        )
                        skipped += 1
                        print(f"[{index}/{len(candidates)}] {nickname}: {note}，跳过")
                        continue
                    real_id = candidate_id(selected)
                    if not real_id:
                        db.record_backfill_attempt(
                            args.platform,
                            imported_id,
                            "no_match",
                            query=nickname,
                            error="matched candidate has no id",
                        )
                        skipped += 1
                        print(f"[{index}/{len(candidates)}] {nickname}: 匹配结果无 ID，跳过")
                        continue
                    started = time.perf_counter()
                    payload = provider.user(real_id)
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    user_price = provider.user_price
                    db.log_api_call(
                        None,
                        args.platform,
                        "user",
                        {"userId": real_id},
                        None,
                        200,
                        provider.user_cost_tokens,
                        duration_ms,
                        path=provider.user_path,
                        provider=provider.provider_id,
                        cost_native=float(user_price.amount) if user_price else 0.0,
                        cost_currency=user_price.currency if user_price else "token",
                    )
                    creator = provider.normalize_user(payload, real_id)
                    db.enrich_imported_creator(
                        args.platform,
                        imported_id,
                        creator,
                        account_id=real_id,
                        query=nickname,
                        selection_note=note,
                    )
                    matched += 1
                    print(
                        f"[{index}/{len(candidates)}] {nickname}: 匹配 {real_id}，"
                        f"粉丝 {creator.get('fans', 0)}{f'（{note}）' if note else ''}"
                    )
                except Exception as exc:
                    db.record_backfill_attempt(
                        args.platform,
                        imported_id,
                        "error",
                        query=nickname,
                        error=str(exc),
                    )
                    failed += 1
                    print(f"[{index}/{len(candidates)}] {nickname}: 失败 {exc}")
        finally:
            provider.close()

        print(f"完成: 匹配 {matched}，跳过 {skipped}，失败 {failed}")


if __name__ == "__main__":
    main()
