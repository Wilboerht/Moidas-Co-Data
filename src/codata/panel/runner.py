from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..config import PROJECT_ROOT, Settings
from ..db import Database


def start_round_job(
    db: Database,
    settings: Settings,
    round_id: int,
    refresh_users: bool = False,
    force: bool = False,
) -> tuple[int, Path]:
    job_id = db.claim_job(round_id, settings.max_active_jobs)
    log_dir = settings.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"round_{round_id}_{job_id}.log"

    command = [
        sys.executable,
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
    try:
        with open(log_path, "ab") as log:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
    except Exception as exc:
        db.update_job(job_id, status="failed", stage="启动失败", error=str(exc))
        raise
    db.update_job(job_id, pid=process.pid)
    return job_id, log_path
