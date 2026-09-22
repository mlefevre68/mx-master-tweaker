"""Start MX Master Tweaker.

This is what the sign-in task and the desktop shortcut both run, through pythonw.exe so
that no console window ever appears. It is a thin wrapper: the app itself refuses to
start twice, so running this again simply brings the settings window forward.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
