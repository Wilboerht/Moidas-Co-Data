from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import time

from ..config import PROJECT_ROOT, Settings
from ..db import Database
from ..normalize import utc_now
from ..processes import process_matches


def _mark_start_failure(
    db: Database, job_id: int, round_id: int, message: str
) -> None:
    try:
        db.finish_job(
            job_id,
            round_id,
            status="failed",
            stage="启动失败",
            error=message,
            cost=db.sum_job_cost(job_id),
            round_cost=db.sum_round_cost(round_id),
            finished_at=utc_now(),
        )
    except Exception:
        pass


def start_round_job(
    db: Database,
    settings: Settings,
    round_id: int,
    refresh_users: bool = False,
    force: bool = False,
    created_by: int | None = None,
) -> tuple[int, Path]:
    log_dir = settings.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    job_id = db.claim_job(
        round_id,
        settings.max_active_jobs,
        getattr(settings, "job_stale_minutes", 30),
        created_by=created_by,
    )
    cutoff = time.time() - max(1, getattr(settings, "log_retention_days", 90)) * 86400
    for old in log_dir.glob("round_*.log"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    log_path = log_dir / f"round_{round_id}_{job_id}.log"

    command = [
        sys.executable,
        "-u",
        "-m",
        "codata.jobs",
        "--round-id",
        str(round_id),
        "--job-id",
        str(job_id),
    ]
    if refresh_users:
        command.append("--refresh-users")
    if force:
        command.append("--force")

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        with open(log_path, "ab") as log:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=creationflags,
            )
    except Exception as exc:
        _mark_start_failure(db, job_id, round_id, f"启动失败: {exc}")
        raise
    try:
        db.update_job(job_id, pid=process.pid)
    except Exception as exc:
        try:
            process.kill()
        except OSError:
            pass
        _mark_start_failure(db, job_id, round_id, f"记录进程信息失败: {exc}")
        raise
    return job_id, log_path


def terminate_job_process(
    pid: int | None,
    round_id: int | None = None,
    job_id: int | None = None,
) -> bool:
    if not pid or not process_matches(int(pid), round_id, job_id):
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
            return result.returncode == 0
        os.kill(int(pid), 15)
        return True
    except Exception:
        return False
