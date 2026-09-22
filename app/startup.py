"""Starting the app when you sign in, without needing administrator rights.

A scheduled task is used rather than a shortcut in the Startup folder because a task can
be told to restart the app if it dies, runs before the desktop has finished settling,
and is visible and removable from one place. Everything here runs as you, in your own
session; nothing is installed for other users and nothing touches the registry.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

TASK_NAME = "MX Master Tweaker"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks.exe", *arguments],
        capture_output=True, text=True, creationflags=_NO_WINDOW, check=False)


def launcher() -> tuple[str, str]:
    """The interpreter and script the task should run.

    pythonw.exe, not python.exe: the app has no console output worth showing, and
    python.exe would flash a black window across the screen at every sign-in.
    """
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    interpreter = windowless if windowless.exists() else executable
    return str(interpreter), str(PROJECT_ROOT / "startup.pyw")


def is_installed() -> bool:
    return _run(["/Query", "/TN", TASK_NAME]).returncode == 0


def install() -> bool:
    interpreter, script = launcher()
    if not Path(script).exists():
        log.error("%s is missing, so there is nothing to start", script)
        return False
    command = f'"{interpreter}" "{script}"'
    result = _run(["/Create", "/TN", TASK_NAME, "/SC", "ONLOGON",
                   "/TR", command, "/F", "/IT", "/RL", "LIMITED"])
    if result.returncode != 0:
        log.error("Could not create the sign-in task: %s",
                  (result.stderr or result.stdout).strip())
        return False
    log.info("Installed the '%s' sign-in task", TASK_NAME)
    return True


def remove() -> bool:
    result = _run(["/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0 and is_installed():
        log.error("Could not remove the sign-in task: %s",
                  (result.stderr or result.stdout).strip())
        return False
    return True


def set_installed(wanted: bool) -> bool:
    """Make reality match the checkbox. Returns whether it now does."""
    if wanted == is_installed():
        return True
    return install() if wanted else remove()


def describe() -> str:
    interpreter, script = launcher()
    return f"{os.path.basename(interpreter)} {script}"
