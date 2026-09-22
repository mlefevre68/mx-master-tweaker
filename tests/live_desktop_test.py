"""Actions that are checked against the running desktop, not just against a key table.

These prove the keystrokes reach Windows and that Windows acts on them. A combination
can be spelled perfectly and still do nothing - shell hotkeys are not obliged to honour
injected input - so the ones that matter are confirmed by their effect.

Each test puts the desktop back the way it found it. They are skipped when there is no
desktop to test against.
"""

from __future__ import annotations

import ctypes
import sys
import time
import unittest
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import actions  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND

# Shell surfaces that are always "visible" and never move, so they say nothing about
# whether an action worked.
SCENERY = {"MSO_KEYTIP_WINDOW_CLASS", "Progman", "WorkerW", "Shell_TrayWnd",
           "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
           "XamlExplorerHostIslandWindow_WASDK", "Xaml_WindowedPopupClass",
           "TopLevelWindowForOverflowXamlIsland"}

SETTLE = 1.6


def foreground_title() -> str:
    title = ctypes.create_unicode_buffer(160)
    user32.GetWindowTextW(user32.GetForegroundWindow(), title, 160)
    return title.value


def on_screen() -> set:
    found = set()

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        cls = ctypes.create_unicode_buffer(120)
        user32.GetClassNameW(hwnd, cls, 120)
        if cls.value not in SCENERY:
            found.add(hwnd)
        return True

    user32.EnumWindows(ENUMPROC(visit), 0)
    return found


def settle():
    time.sleep(SETTLE)


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class LiveActionTests(unittest.TestCase):
    """Slow by nature: each one waits for the desktop to react."""

    def test_task_view_opens_and_closes(self):
        # What a three-finger swipe up does on a touchpad. Task View's window is a XAML
        # island that never reports itself as visible, so the honest signal that it is
        # up is that it has taken the foreground.
        self.assertNotEqual(foreground_title(), "Task View", "Task View was already open")
        try:
            actions.run("task_view")
            settle()
            self.assertEqual(foreground_title(), "Task View",
                             "Win+Tab did not open Task View")
        finally:
            actions.run("escape")
            settle()
        self.assertNotEqual(foreground_title(), "Task View", "Task View would not close")

    def test_show_desktop_hides_windows_and_toggles_back(self):
        actions.run("restore_windows")
        settle()
        start = on_screen()
        if not start:
            self.skipTest("no ordinary windows are open to minimise")

        actions.run("show_desktop")
        settle()
        self.assertLess(len(on_screen()), len(start), "Win+D did not clear the desktop")

        actions.run("show_desktop")
        settle()
        self.assertEqual(on_screen(), start, "pressing it again did not bring them back")

    def test_restore_windows_undoes_show_desktop(self):
        actions.run("restore_windows")
        settle()
        start = on_screen()
        if not start:
            self.skipTest("no ordinary windows are open to minimise")

        actions.run("show_desktop")
        settle()
        self.assertLess(len(on_screen()), len(start))

        actions.run("restore_windows")
        settle()
        self.assertEqual(on_screen(), start,
                         "Win+Shift+M did not bring back the same windows")


if __name__ == "__main__":
    unittest.main()
