"""The Windows end of the app: a hidden window, the mouse hook and the tray icon.

All three have to live on the same thread, and that thread has to spend its life in a
message loop. A low-level mouse hook is not a callback Windows makes on a thread of its
choosing: the events are delivered to the message queue of the thread that installed the
hook, and if that thread ever stops pumping messages the hook stops working and is
eventually removed. So this runs as its own thread and never blocks.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from . import winapi as w

log = logging.getLogger(__name__)

WINDOW_CLASS = "MxMasterTweakerHost"
TRAY_ID = 1

ID_SETTINGS = 1
ID_ENABLED = 2
ID_STARTUP = 3
ID_QUIT = 4


class Host(threading.Thread):
    """Owns the hook and the tray icon, and pumps their messages."""

    def __init__(self, engine, callbacks: dict) -> None:
        super().__init__(name="win32-host", daemon=True)
        self.engine = engine
        self.callbacks = callbacks
        self.hwnd = None
        self.ready = threading.Event()
        self._hook = None
        # Windows keeps a raw pointer to these. If Python collects them, the next mouse
        # event runs off the end of a freed object, so they are held deliberately.
        self._hook_proc = None
        self._wnd_proc = None
        self._icon_data = None
        self._icon = None
        self._taskbar_created = w.user32.RegisterWindowMessageW("TaskbarCreated")
        self._raw_registered = False

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        try:
            self._create_window()
            self._install_hook()
            self._add_tray_icon()
        except Exception:
            log.exception("Could not start the Windows host")
            self.ready.set()
            return
        self.ready.set()
        self._pump()
        self._teardown()

    def _create_window(self) -> None:
        self._wnd_proc = w.WNDPROC(self._on_message)
        instance = w.kernel32.GetModuleHandleW(None)
        cls = w.WNDCLASSW()
        cls.lpfnWndProc = self._wnd_proc
        cls.hInstance = instance
        cls.lpszClassName = WINDOW_CLASS
        w.user32.RegisterClassW(ctypes.byref(cls))
        # A real window, never shown. A message-only window would be lighter, but the
        # notification area will not talk to one.
        self.hwnd = w.user32.CreateWindowExW(
            0, WINDOW_CLASS, "MX Master Tweaker", 0, 0, 0, 0, 0,
            None, None, instance, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _install_hook(self) -> None:
        self._hook_proc = w.HOOKPROC(self._on_mouse)
        self._hook = w.user32.SetWindowsHookExW(w.WH_MOUSE_LL, self._hook_proc, None, 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())
        log.info("Mouse hook installed")

    def _pump(self) -> None:
        message = wintypes.MSG()
        while True:
            result = w.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result in (0, -1):
                return
            w.user32.TranslateMessage(ctypes.byref(message))
            w.user32.DispatchMessageW(ctypes.byref(message))

    def _teardown(self) -> None:
        self._remove_tray_icon()
        if self._hook:
            w.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self.hwnd:
            w.user32.DestroyWindow(self.hwnd)
            self.hwnd = None

    # -- the hook ----------------------------------------------------------

    def _on_mouse(self, code, wparam, lparam):
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(w.MSLLHOOKSTRUCT)).contents
            try:
                if self.engine.on_mouse(int(wparam), info):
                    return 1  # swallowed: no application will see this event
            except Exception:
                log.exception("Hook failed")
        return w.user32.CallNextHookEx(self._hook, code, wparam, lparam)

    # -- window messages ---------------------------------------------------

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == w.WM_TRAY_CALLBACK:
            self._on_tray_event(int(lparam) & 0xFFFF)
            return 0
        if message == w.WM_COMMAND:
            self._on_menu(int(wparam) & 0xFFFF)
            return 0
        if message == w.WM_APP_REFRESH_TRAY:
            self._update_tray_icon()
            return 0
        if message == w.WM_APP_RAW_INPUT:
            self._set_raw_input(bool(wparam))
            return 0
        if message == w.WM_INPUT:
            self._on_raw_input(lparam)
            return w.user32.DefWindowProcW(hwnd, message, wparam, lparam)
        if message == w.WM_APP_QUIT:
            w.user32.PostQuitMessage(0)
            return 0
        if message == w.WM_APP_SHOW:
            self._call("open_settings")
            return 0
        if message == self._taskbar_created:
            # Explorer restarted and took the notification area with it.
            self._add_tray_icon()
            return 0
        if message == w.WM_DESTROY:
            w.user32.PostQuitMessage(0)
            return 0
        return w.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _on_tray_event(self, mouse_message: int) -> None:
        if mouse_message in (w.WM_LBUTTONUP, 0x0203):  # click or double click
            self._call("open_settings")
        elif mouse_message == w.WM_RBUTTONUP:
            self._show_menu()

    def _on_menu(self, command: int) -> None:
        if command == ID_SETTINGS:
            self._call("open_settings")
        elif command == ID_ENABLED:
            self._call("toggle_enabled")
        elif command == ID_STARTUP:
            self._call("toggle_startup")
        elif command == ID_QUIT:
            self._call("quit")

    def _call(self, name: str) -> None:
        callback = self.callbacks.get(name)
        if callback is None:
            return
        try:
            callback()
        except Exception:
            log.exception("Tray action %r failed", name)

    # -- tray --------------------------------------------------------------

    def _notify_data(self, flags: int) -> w.NOTIFYICONDATAW:
        data = w.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(w.NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = TRAY_ID
        data.uFlags = flags
        data.uCallbackMessage = w.WM_TRAY_CALLBACK
        if self._icon is None:
            # Extracted once and kept. Asking for it again on every tooltip change
            # would leak an icon handle each time.
            self._icon = w.tray_icon()
        data.hIcon = self._icon
        enabled = bool(getattr(self.engine.config, "enabled", True))
        data.szTip = ("MX Master Tweaker - bindings "
                      + ("on" if enabled else "off"))[:127]
        return data

    def _add_tray_icon(self) -> None:
        self._icon_data = self._notify_data(w.NIF_MESSAGE | w.NIF_ICON | w.NIF_TIP)
        if not w.shell32.Shell_NotifyIconW(w.NIM_ADD, ctypes.byref(self._icon_data)):
            log.warning("Could not add the tray icon")

    def _update_tray_icon(self) -> None:
        data = self._notify_data(w.NIF_ICON | w.NIF_TIP)
        w.shell32.Shell_NotifyIconW(w.NIM_MODIFY, ctypes.byref(data))

    def _remove_tray_icon(self) -> None:
        if self._icon_data is None:
            return
        w.shell32.Shell_NotifyIconW(w.NIM_DELETE, ctypes.byref(self._icon_data))
        self._icon_data = None

    def _show_menu(self) -> None:
        menu = w.user32.CreatePopupMenu()
        if not menu:
            return
        enabled = bool(getattr(self.engine.config, "enabled", True))
        at_startup = bool(self._ask("startup_enabled", False))
        w.user32.AppendMenuW(menu, w.MF_STRING, ID_SETTINGS, "Settings...")
        w.user32.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)
        w.user32.AppendMenuW(menu, w.MF_STRING | (w.MF_CHECKED if enabled else 0),
                             ID_ENABLED, "Bindings active")
        w.user32.AppendMenuW(menu, w.MF_STRING | (w.MF_CHECKED if at_startup else 0),
                             ID_STARTUP, "Start with Windows")
        w.user32.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)
        w.user32.AppendMenuW(menu, w.MF_STRING, ID_QUIT, "Quit")

        point = wintypes.POINT()
        w.user32.GetCursorPos(ctypes.byref(point))
        # Without this the menu refuses to close when you click elsewhere, which is a
        # documented quirk of showing a menu from a window that is not in the foreground.
        w.user32.SetForegroundWindow(self.hwnd)
        chosen = w.user32.TrackPopupMenu(
            menu, w.TPM_RIGHTBUTTON | w.TPM_RETURNCMD,
            point.x, point.y, 0, self.hwnd, None)
        w.user32.DestroyMenu(menu)
        w.user32.PostMessageW(self.hwnd, 0x0000, 0, 0)  # WM_NULL, the other half of the fix
        if chosen:
            self._on_menu(int(chosen))

    def _ask(self, name: str, fallback):
        """Read a value the app keeps for us.

        Whatever answers this has to answer immediately. This thread is the one the
        mouse hook is delivered to, and anything that blocks it - a subprocess, a disk
        wait - stalls every click in the system and eventually gets the hook removed.
        """
        callback = self.callbacks.get(name)
        if callback is None:
            return fallback
        try:
            return callback()
        except Exception:
            log.exception("Tray query %r failed", name)
            return fallback

    # -- raw input, used only by the detector in the settings window -------

    def _set_raw_input(self, on: bool) -> None:
        """Listen to every input device, or stop listening.

        This is off unless the detector is open. Registered with RIDEV_INPUTSINK it
        delivers a message for every mouse movement in the system, which is a lot of
        work to do for a panel nobody is looking at.
        """
        if on == self._raw_registered:
            return
        wanted = [(0x01, 0x02), (0x01, 0x06), (0x0C, 0x01), (0x01, 0x80)]
        # Also listen to the vendor-defined collections that are actually present, which
        # is where a mouse puts buttons Windows has no standard slot for.
        for device in w.input_devices():
            page, usage = device.get("usage_page"), device.get("usage")
            if device["type"] == "hid" and page and page >= 0xFF00:
                if (page, usage) not in wanted:
                    wanted.append((page, usage))

        entries = (w.RAWINPUTDEVICE * len(wanted))()
        for index, (page, usage) in enumerate(wanted):
            entries[index].usUsagePage = page
            entries[index].usUsage = usage
            entries[index].dwFlags = w.RIDEV_INPUTSINK if on else 0x00000001  # RIDEV_REMOVE
            entries[index].hwndTarget = self.hwnd if on else None
        if w.user32.RegisterRawInputDevices(entries, len(wanted),
                                            ctypes.sizeof(w.RAWINPUTDEVICE)):
            self._raw_registered = on
        else:
            log.warning("RegisterRawInputDevices failed: %s", ctypes.get_last_error())

    def _on_raw_input(self, lparam) -> None:
        callback = self.callbacks.get("on_raw")
        if callback is None:
            return
        size = wintypes.UINT(0)
        header = ctypes.sizeof(w.RAWINPUTHEADER)
        w.user32.GetRawInputData(lparam, w.RID_INPUT, None, ctypes.byref(size), header)
        if not size.value:
            return
        buffer = ctypes.create_string_buffer(size.value)
        if w.user32.GetRawInputData(lparam, w.RID_INPUT, buffer,
                                    ctypes.byref(size), header) == 0xFFFFFFFF:
            return
        raw = ctypes.cast(buffer, ctypes.POINTER(w.RAWINPUT)).contents
        try:
            described = self._describe_raw(raw, buffer)
        except Exception:
            log.exception("Could not read a raw input report")
            return
        if described:
            try:
                callback(described)
            except Exception:
                log.exception("Raw input observer failed")

    def _describe_raw(self, raw, buffer) -> tuple[str, str] | None:
        if raw.header.dwType == w.RIM_TYPEMOUSE:
            flags = raw.mouse.buttons.usButtonFlags
            if not flags:
                return None  # movement only, which would drown out everything else
            return "mouse", f"button flags 0x{flags:04X}"
        if raw.header.dwType == w.RIM_TYPEKEYBOARD:
            if raw.keyboard.Message not in (w.WM_KEYDOWN, w.WM_SYSKEYDOWN):
                return None
            return "keyboard", f"virtual key 0x{raw.keyboard.VKey:02X}"
        if raw.header.dwType == w.RIM_TYPEHID:
            size = raw.hid.dwSizeHid
            count = raw.hid.dwCount
            offset = w.RAWINPUT.u.offset + w.RAWHID.bRawData.offset
            payload = buffer.raw[offset:offset + size * count]
            if not any(payload[1:]):
                return None  # an all-zero report is a release, and there is one of
                             # those after every press
            return "hid", payload.hex(" ")
        return None

    # -- asking the host thread to do something ---------------------------

    def post(self, message: int, wparam: int = 0, lparam: int = 0) -> None:
        if self.hwnd:
            w.user32.PostMessageW(self.hwnd, message, wparam, lparam)

    def refresh_tray(self) -> None:
        self.post(w.WM_APP_REFRESH_TRAY)

    def set_detecting(self, on: bool) -> None:
        self.post(w.WM_APP_RAW_INPUT, 1 if on else 0)

    def request_quit(self) -> None:
        self.post(w.WM_APP_QUIT)
