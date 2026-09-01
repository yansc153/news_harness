"""Regression tests: command timeouts must not orphan descendant processes.

The VPS incident (2026-09-01) showed ~9.4k leaked chromium/crashpad processes:
subprocess.run(timeout=...) SIGKILLs only the direct child, so headless browser
trees survived every timed-out cycle until the container could not fork at all.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

from news_harness.direct_cli_backend import _run_command
from news_harness.xueqiu_targeted import _kill_process_group


def _wait_for_exit(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _spawner_script(pid_file: str) -> str:
    return textwrap.dedent(
        f"""
        import subprocess, time
        child = subprocess.Popen(["sleep", "60"])
        with open({pid_file!r}, "w") as fh:
            fh.write(str(child.pid))
        time.sleep(60)
        """
    )


def test_run_command_timeout_kills_descendant_tree(tmp_path) -> None:
    pid_file = tmp_path / "child.pid"
    result = _run_command(
        [sys.executable, "-c", _spawner_script(str(pid_file))],
        timeout=1,
    )

    assert result["status"] == "failed"
    assert result["timeout"] is True
    child_pid = int(pid_file.read_text().strip())
    assert _wait_for_exit(child_pid), (
        f"descendant pid {child_pid} survived a timed-out _run_command call"
    )


def test_kill_process_group_reaps_descendants(tmp_path) -> None:
    pid_file = tmp_path / "child.pid"
    parent = subprocess.Popen(
        [sys.executable, "-c", _spawner_script(str(pid_file))],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    child_pid = int(pid_file.read_text().strip())

    _kill_process_group(parent)
    parent.wait(timeout=5)

    assert _wait_for_exit(child_pid), (
        f"descendant pid {child_pid} survived _kill_process_group"
    )
