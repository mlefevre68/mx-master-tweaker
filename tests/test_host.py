"""The raw input decoding behind the Detect panel.

Raw HID reports are laid out by hand in memory here, because the alternative is to plug
in a device and press a button - which is exactly the situation the Detect panel exists
to help with, and not something a test can do.

Nothing here starts the host thread, so no hook is installed and the real mouse is never
touched.
"""

from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import winapi as w  # noqa: E402
from app.config import default_config  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.host import Host  # noqa: E402

HID_PAYLOAD_OFFSET = w.RAWINPUT.u.offset + w.RAWHID.bRawData.offset


def hid_report(payload: bytes):
    """A WM_INPUT buffer carrying one HID report, as Windows would hand it over."""
    buffer = ctypes.create_string_buffer(HID_PAYLOAD_OFFSET + len(payload))
    raw = ctypes.cast(buffer, ctypes.POINTER(w.RAWINPUT)).contents
    raw.header.dwType = w.RIM_TYPEHID
    raw.header.dwSize = len(buffer)
    raw.hid.dwSizeHid = len(payload)
    raw.hid.dwCount = 1
    ctypes.memmove(ctypes.addressof(buffer) + HID_PAYLOAD_OFFSET, payload, len(payload))
    return raw, buffer


def mouse_report(button_flags: int):
    buffer = ctypes.create_string_buffer(ctypes.sizeof(w.RAWINPUT))
    raw = ctypes.cast(buffer, ctypes.POINTER(w.RAWINPUT)).contents
    raw.header.dwType = w.RIM_TYPEMOUSE
    raw.header.dwSize = len(buffer)
    raw.mouse.buttons.usButtonFlags = button_flags
    return raw, buffer


def keyboard_report(vkey: int, message: int):
    buffer = ctypes.create_string_buffer(ctypes.sizeof(w.RAWINPUT))
    raw = ctypes.cast(buffer, ctypes.POINTER(w.RAWINPUT)).contents
    raw.header.dwType = w.RIM_TYPEKEYBOARD
    raw.header.dwSize = len(buffer)
    raw.keyboard.VKey = vkey
    raw.keyboard.Message = message
    return raw, buffer


class RawInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Constructed but never started: no thread, no window, no hook.
        cls.host = Host(Engine(default_config()), {})

    def test_a_hid_report_is_shown_as_hex(self):
        raw, buffer = hid_report(bytes([0x11, 0x01, 0x0C, 0x3E]))
        self.assertEqual(self.host._describe_raw(raw, buffer),
                         ("hid", "11 01 0c 3e"))

    def test_the_payload_is_read_from_the_right_place(self):
        # If this offset is wrong the panel shows plausible-looking rubbish, which is
        # worse than showing nothing.
        payload = bytes(range(0x20, 0x28))
        raw, buffer = hid_report(payload)
        _, text = self.host._describe_raw(raw, buffer)
        self.assertEqual(bytes.fromhex(text.replace(" ", "")), payload)

    def test_an_empty_hid_report_is_ignored(self):
        # Every press is followed by an all-zero release report; showing both would
        # double the noise for no information.
        raw, buffer = hid_report(bytes([0x11, 0x00, 0x00, 0x00]))
        self.assertIsNone(self.host._describe_raw(raw, buffer))

    def test_mouse_buttons_are_reported(self):
        raw, buffer = mouse_report(w.RI_MOUSE_BUTTON_5_DOWN)
        kind, detail = self.host._describe_raw(raw, buffer)
        self.assertEqual(kind, "mouse")
        self.assertIn("0100", detail)

    def test_mouse_movement_alone_is_ignored(self):
        raw, buffer = mouse_report(0)
        self.assertIsNone(self.host._describe_raw(raw, buffer))

    def test_key_presses_are_reported_and_releases_are_not(self):
        raw, buffer = keyboard_report(0x41, w.WM_KEYDOWN)
        self.assertEqual(self.host._describe_raw(raw, buffer),
                         ("keyboard", "virtual key 0x41"))
        raw, buffer = keyboard_report(0x41, w.WM_KEYUP)
        self.assertIsNone(self.host._describe_raw(raw, buffer))


class DeviceListTests(unittest.TestCase):
    def test_this_machine_has_input_devices(self):
        devices = w.input_devices()
        self.assertTrue(devices, "Windows reported no input devices at all")
        for device in devices:
            self.assertIn(device["type"], ("mouse", "keyboard", "hid", "other"))
            self.assertIsInstance(device["name"], str)

    def test_at_least_one_mouse_is_present(self):
        self.assertTrue(any(d["type"] == "mouse" for d in w.input_devices()))


class TopmostRepairTests(unittest.TestCase):
    """Undoing the Windows bug where a snapped window sticks always-on-top.

    Confirmed on a real desktop while this bug was reported: an injected Win+Arrow
    left both a PowerPoint and an Edge window flagged always-on-top, so each stayed
    ahead of everything else - including whatever was clicked on the taskbar - until
    the flag was cleared by hand.
    """

    def setUp(self) -> None:
        import subprocess
        import time
        self.process = subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)
        self.hwnd = w.user32.FindWindowW("Notepad", None)
        if not self.hwnd:
            self.process.terminate()
            self.skipTest("could not open a Notepad window to test against")

    def tearDown(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=5)

    def _set_topmost(self) -> None:
        w.user32.SetWindowPos(self.hwnd, w.HWND_TOPMOST, 0, 0, 0, 0,
                              w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE)
        # SWP_ASYNCWINDOWPOS is not used here, so this one takes effect immediately -
        # the point is to set up the "already topmost" state to repair afterwards.

    def _settle(self) -> None:
        import time
        time.sleep(0.4)  # clear_topmost uses SWP_ASYNCWINDOWPOS, which lands shortly after

    def test_a_window_that_is_not_topmost_is_left_alone(self):
        self.assertFalse(w.is_topmost(self.hwnd))
        w.clear_topmost(self.hwnd)
        self._settle()
        self.assertFalse(w.is_topmost(self.hwnd))

    def test_a_topmost_window_is_repaired(self):
        self._set_topmost()
        self.assertTrue(w.is_topmost(self.hwnd), "the test setup itself did not work")
        w.clear_topmost(self.hwnd)
        self._settle()
        self.assertFalse(w.is_topmost(self.hwnd))

    def test_a_closed_window_is_not_a_problem(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        import time
        time.sleep(0.5)
        w.clear_topmost(self.hwnd)  # must not raise

    def test_a_null_handle_is_not_a_problem(self):
        w.clear_topmost(0)
        w.clear_topmost(None)

    def test_vendor_collections_are_picked_out_for_deep_listening(self):
        # A mouse puts buttons Windows has no standard slot for on a vendor-defined
        # page, so those are exactly the ones the detector has to subscribe to.
        vendor = [d for d in w.input_devices()
                  if d["type"] == "hid" and (d["usage_page"] or 0) >= 0xFF00]
        for device in vendor:
            self.assertIsNotNone(device["usage"])


if __name__ == "__main__":
    unittest.main()
