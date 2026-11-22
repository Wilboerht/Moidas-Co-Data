from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import time

from ..config import PROJECT_ROOT, Settings
from ..db import Database


def start_round_job(
    db: Database,
    settings: Settings,
    round_id: int,
    refresh_users: bool = False,
    force: bool = False,
) -> tuple[int, Path]:
    job_id = db.claim_job(
        round_id,
        settings.max_active_jobs,
        getattr(settings, "job_stale_minutes", 30),
    )
    log_dir = settings.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
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
        db.update_job(job_id, status="failed", stage="启动失败", error=str(exc))
        raise
    db.update_job(job_id, pid=process.pid)
    return job_id, log_path


def terminate_job_process(pid: int | None) -> None:
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(int(pid), 15)
    except Exception:
        pass
