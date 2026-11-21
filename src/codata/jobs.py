from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .db import Database
from .pipeline import Pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codata-jobs", description="运行一轮采集任务")
    parser.add_argument("--round-id", type=int, required=True, help="轮次 ID")
    parser.add_argument("--job-id", type=int, default=None, help="已有任务 ID（面板启动时传入）")
    parser.add_argument("--refresh-users", action="store_true", help="强制刷新已缓存达人资料")
    parser.add_argument("--force", action="store_true", help="跳过余额不足保护")
    return parser


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    settings = load_settings(require_credentials=True)
    with Database(settings.output_dir) as db:
        pipeline = Pipeline(settings, db)
        try:
            pipeline.run_round(
                args.round_id,
                job_id=args.job_id,
                refresh_users=args.refresh_users,
                force=args.force,
            )
        except Exception as exc:
            print(f"采集失败: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
