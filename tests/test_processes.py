from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.processes import pid_alive, process_matches


class ProcessHelpersTests(unittest.TestCase):
    def test_pid_alive_for_current_process(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))

    def test_pid_alive_for_unknown_process(self) -> None:
        self.assertFalse(pid_alive(999_999_999))

    def test_process_matches_requires_job_tokens(self) -> None:
        self.assertFalse(process_matches(os.getpid(), 1, 2))
        self.assertFalse(process_matches(None, 1, 2))
        self.assertFalse(process_matches(os.getpid(), None, 2))


if __name__ == "__main__":
    unittest.main()
