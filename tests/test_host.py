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

    def test_vendor_collections_are_picked_out_for_deep_listening(self):
        # A mouse puts buttons Windows has no standard slot for on a vendor-defined
        # page, so those are exactly the ones the detector has to subscribe to.
        vendor = [d for d in w.input_devices()
                  if d["type"] == "hid" and (d["usage_page"] or 0) >= 0xFF00]
        for device in vendor:
            self.assertIsNotNone(device["usage"])


if __name__ == "__main__":
    unittest.main()
