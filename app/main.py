"""MX Master Tweaker: start here.

The process is three threads with clearly separated jobs:

* the main thread runs Tk and owns every widget, because Tk is not thread safe;
* the host thread owns the hidden window, the mouse hook and the tray icon, because a
  low-level hook has to be installed and pumped by one thread that never blocks;
* a dispatcher thread runs the actions, so nothing slow ever happens inside the hook.

They only ever talk to each other through queues and posted window messages. Nothing
here opens a socket, reads anything outside its own settings file, or needs elevation.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox

from . import config as cfg, hidpp, startup, winapi as w
from .engine import Engine
from .host import Host, WINDOW_CLASS
from .ui import SettingsWindow

log = logging.getLogger("mxmaster")

MUTEX_NAME = "Local\\MxMasterTweaker.SingleInstance"


def configure_logging(verbose: bool) -> None:
    handler = logging.handlers.RotatingFileHandler(
        cfg.logs_dir() / "app.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    # pythonw.exe has no stdout at all, so only add a console handler when there is one.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(console)


class App:
    def __init__(self, open_settings: bool) -> None:
        self.config = cfg.load()
        self.engine = Engine(self.config)
        self.settings: SettingsWindow | None = None
        self.gesture: hidpp.GestureButton | None = None
        self.gesture_state = hidpp.Status("off", "Not started")
        self._requests: queue.Queue = queue.Queue()
        self._open_settings_at_start = open_settings
        self._quitting = False

        self.root = tk.Tk()
        self.root.withdraw()
        # Closing the last window must not end the process: the app lives in the tray.
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Answering "is it set to start at sign-in?" means running schtasks, which takes
        # long enough to matter. The tray menu is drawn on the same thread the mouse hook
        # is delivered to, so the answer is cached here and never worked out there.
        self._starts_at_logon = startup.is_installed()

        self.host = Host(self.engine, {
            "open_settings": lambda: self._request("settings"),
            "toggle_enabled": lambda: self._request("toggle"),
            "toggle_startup": lambda: self._request("startup"),
            "quit": lambda: self._request("quit"),
            "host_stopped": lambda: self._request("quit"),
            "startup_enabled": lambda: self._starts_at_logon,
            "on_raw": self._on_raw,
        })

    # -- plumbing between threads -----------------------------------------

    def _request(self, name: str) -> None:
        """Ask the Tk thread to do something. Safe to call from any thread."""
        self._requests.put(name)

    def _pump(self) -> None:
        while True:
            try:
                name = self._requests.get_nowait()
            except queue.Empty:
                break
            try:
                self._serve(name)
            except Exception:
                log.exception("Request %r failed", name)
        self.root.after(80, self._pump)

    def _serve(self, name: str) -> None:
        if name == "settings":
            self.show_settings()
        elif name == "toggle":
            self.set_enabled(not self.config.enabled)
        elif name == "startup":
            self.set_startup(not self._starts_at_logon)
            if self.settings is not None:
                self.settings.at_startup.set(self._starts_at_logon)
        elif name == "quit":
            self.quit()

    def _on_raw(self, described) -> None:
        if self.settings is not None:
            self.settings.observe_raw(described)

    # -- the gesture button ------------------------------------------------

    def _sync_gesture(self) -> None:
        """Start or stop the gesture button listener to match the settings."""
        wanted = bool(self.config.setting("use_gesture_button", True))
        if wanted and self.gesture is None:
            self.gesture = hidpp.GestureButton(self._on_gesture, self._on_gesture_status)
            self.gesture.start()
        elif not wanted and self.gesture is not None:
            self.gesture.stop()
            self.gesture = None
            self.gesture_state = hidpp.Status("off", "Turned off in the settings")

    def _on_gesture(self, pressed: bool, position: tuple[int, int]) -> None:
        """A gesture button press, arriving on the listener's own thread."""
        self.engine.on_button("gesture", pressed, position)

    def _on_gesture_status(self, status) -> None:
        # A plain assignment, read by the settings window on its own timer. Touching Tk
        # from this thread would not be safe.
        self.gesture_state = status

    def gesture_status(self):
        return self.gesture_state

    # -- what the app can be asked to do ----------------------------------

    def show_settings(self) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self)
        self.settings.show()

    def set_enabled(self, enabled: bool) -> None:
        self.config.enabled = enabled
        self.save()
        if self.settings is not None:
            self.settings.enabled.set(enabled)

    def starts_at_logon(self) -> bool:
        return self._starts_at_logon

    def set_startup(self, wanted: bool) -> bool:
        """Register or remove the sign-in task, and remember the answer."""
        succeeded = startup.set_installed(wanted)
        self._starts_at_logon = startup.is_installed()
        return succeeded

    def save(self) -> None:
        cfg.save(self.config)
        self.engine.set_config(self.config)
        self._sync_gesture()
        self.host.refresh_tray()
        log.info("Settings applied (bindings %s)",
                 "on" if self.config.enabled else "off")

    def quit(self) -> None:
        # Reachable twice over: once from the tray menu, and again when the host thread
        # notices it has stopped. Doing the work twice would block on threads that are
        # already gone.
        if self._quitting:
            return
        self._quitting = True
        log.info("Shutting down")
        if self.gesture is not None:
            # Give it a moment to hand the gesture button back to the mouse before the
            # process goes away.
            self.gesture.stop()
            self.gesture.join(timeout=2.0)
            self.gesture = None
        self.engine.stop()
        self.host.request_quit()
        self.root.quit()

    # -- running -----------------------------------------------------------

    def run(self) -> int:
        self.host.start()
        if not self.host.ready.wait(timeout=10) or self.host.hwnd is None:
            messagebox.showerror(
                "MX Master Tweaker",
                "Windows would not let the mouse hook start, so nothing can be "
                f"remapped.\n\nThe log may say why:\n{cfg.logs_dir()}")
            return 1
        log.info("Running. Settings: %s", cfg.config_path())
        self._sync_gesture()
        if self._open_settings_at_start:
            self.show_settings()
        self.root.after(80, self._pump)
        self.root.mainloop()
        return 0


def wake_existing_instance(timeout: float = 0.0) -> bool:
    """Bring the copy that is already running to the front.

    Two copies would both hook the mouse and every binding would fire twice, so a second
    start hands over to the first instead. A copy that is still starting up has not
    created its window yet, which is why this is willing to wait for one.
    """
    deadline = time.monotonic() + timeout
    while True:
        hwnd = w.user32.FindWindowW(WINDOW_CLASS, None)
        if hwnd:
            w.user32.PostMessageW(hwnd, w.WM_APP_SHOW, 0, 0)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mx-master-tweaker",
        description="Personalise the buttons on a Logitech MX Master, locally.")
    parser.add_argument("--settings", action="store_true",
                        help="open the settings window on start")
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    arguments = parser.parse_args(argv)

    configure_logging(arguments.verbose)

    # A machine that has never run this before gets the settings window, so the first
    # thing that happens is not a silent change to how the mouse behaves.
    first_run = not cfg.config_path().exists()

    if not w.claim_single_instance(MUTEX_NAME):
        # Wait a little: the copy holding the mutex may still be starting up, and its
        # window is the last thing it creates.
        if wake_existing_instance(timeout=5.0):
            log.info("Already running; brought the existing window forward")
            return 0
        # The mutex is held but nothing answers. That copy has no window, so it has no
        # tray icon and no hook either - it cannot be doing anything useful, and
        # refusing to start would leave the app permanently unstartable. Carry on.
        log.warning("A copy is running but not responding; starting anyway")

    try:
        return App(open_settings=arguments.settings or first_run).run()
    except Exception:
        log.exception("Fatal error")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
