from __future__ import annotations

import os
import subprocess
from pathlib import Path


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, int(pid)
            )
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, OverflowError):
        return False


def process_command_line(pid: int | None) -> str:
    if not pid:
        return ""
    if os.name == "nt":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").CommandLine",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                timeout=10,
            )
        except Exception:
            return ""
        return result.stdout or ""
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except OSError:
        try:
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(int(pid))],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                timeout=5,
            )
        except Exception:
            return ""
        return result.stdout or ""
    return " ".join(
        part for part in raw.decode("utf-8", errors="replace").split("\x00") if part
    )


def process_matches(
    pid: int | None, round_id: int | None, job_id: int | None
) -> bool:
    if not pid or round_id is None or job_id is None:
        return False
    text = process_command_line(pid)
    return (
        "codata.jobs" in text
        and f"--round-id {round_id}" in text
        and f"--job-id {job_id}" in text
    )
