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
import tkinter as tk
from tkinter import messagebox

from . import config as cfg, startup, winapi as w
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
        self._requests: queue.Queue = queue.Queue()
        self._open_settings_at_start = open_settings

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
        self.host.refresh_tray()
        log.info("Settings applied (bindings %s)",
                 "on" if self.config.enabled else "off")

    def quit(self) -> None:
        log.info("Shutting down")
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
        if self._open_settings_at_start:
            self.show_settings()
        self.root.after(80, self._pump)
        self.root.mainloop()
        return 0


def wake_existing_instance() -> bool:
    """Bring the copy that is already running to the front instead of starting a second.

    Two copies would both hook the mouse and every binding would fire twice.
    """
    hwnd = w.user32.FindWindowW(WINDOW_CLASS, None)
    if not hwnd:
        return False
    w.user32.PostMessageW(hwnd, w.WM_APP_SHOW, 0, 0)
    return True


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

    first = w.claim_single_instance(MUTEX_NAME)
    if not first:
        if wake_existing_instance():
            log.info("Already running; brought the existing window forward")
            return 0
        # The mutex is held but no window answered: the other copy is still starting,
        # or is wedged. Starting a second hook on top would double every binding.
        log.warning("Another copy is already running")
        return 0

    try:
        return App(open_settings=arguments.settings or first_run).run()
    except Exception:
        log.exception("Fatal error")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
