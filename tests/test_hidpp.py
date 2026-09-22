"""The HID++ conversation with the mouse.

The frame building and notification decoding are checked against the byte layouts
observed on a real MX Master 3, so a change that breaks them fails here rather than
showing up as a gesture button that quietly stops working.

The one test that touches hardware is skipped when no Logitech device is attached, and
it only reads - nothing here ever changes the mouse's configuration.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import hidpp  # noqa: E402


class FrameTests(unittest.TestCase):
    def test_a_request_is_laid_out_the_way_the_mouse_expects(self):
        frame = hidpp._frame(0xFF, 0x00, 0x01, bytes([0x00, 0x00, 0xAA]))
        # report id, device index, feature index, (function << 4) | software id
        self.assertEqual(frame[0], hidpp.HIDPP_LONG)
        self.assertEqual(frame[1], 0xFF)
        self.assertEqual(frame[2], 0x00)
        self.assertEqual(frame[3], (0x01 << 4) | hidpp.SW_ID)
        self.assertEqual(frame[4:7], bytes([0x00, 0x00, 0xAA]))

    def test_the_software_id_is_never_zero(self):
        # Zero is what the mouse uses for notifications it sends of its own accord.
        # A request carrying it would be indistinguishable from one.
        self.assertNotEqual(hidpp.SW_ID, 0)
        self.assertEqual(hidpp.SW_ID & 0x0F, hidpp.SW_ID)

    def test_divert_and_undivert_both_carry_the_change_bit(self):
        # Sending the setting without the bit that says "this setting is meaningful"
        # is accepted by the mouse and then ignored, which was confirmed on a device.
        self.assertTrue(hidpp.DIVERT & 0x02)
        self.assertTrue(hidpp.UNDIVERT & 0x02)
        self.assertTrue(hidpp.DIVERT & 0x01)
        self.assertFalse(hidpp.UNDIVERT & 0x01)


class NotificationTests(unittest.TestCase):
    REPROG = 0x09

    def notification(self, *cids: int) -> bytes:
        payload = bytearray(20)
        payload[0] = hidpp.HIDPP_LONG
        payload[1] = 0xFF
        payload[2] = self.REPROG
        payload[3] = 0x00  # function 0, software id 0: sent by the mouse, not asked for
        for slot, cid in enumerate(cids):
            payload[4 + slot * 2] = (cid >> 8) & 0xFF
            payload[5 + slot * 2] = cid & 0xFF
        return bytes(payload)

    def test_a_press_is_the_control_appearing(self):
        held = hidpp.diverted_controls(self.notification(hidpp.CID_GESTURE), self.REPROG)
        self.assertEqual(held, {hidpp.CID_GESTURE})

    def test_a_release_is_the_control_being_absent(self):
        # The mouse reports the whole set each time; nothing held is an empty frame.
        held = hidpp.diverted_controls(self.notification(), self.REPROG)
        self.assertEqual(held, set())

    def test_several_controls_at_once(self):
        held = hidpp.diverted_controls(
            self.notification(hidpp.CID_GESTURE, 0x0053), self.REPROG)
        self.assertEqual(held, {hidpp.CID_GESTURE, 0x0053})

    def test_a_reply_to_our_own_request_is_not_a_notification(self):
        frame = bytearray(self.notification(hidpp.CID_GESTURE))
        frame[3] = (0x03 << 4) | hidpp.SW_ID
        self.assertIsNone(hidpp.diverted_controls(bytes(frame), self.REPROG))

    def test_another_feature_is_ignored(self):
        self.assertIsNone(
            hidpp.diverted_controls(self.notification(hidpp.CID_GESTURE), 0x0A))

    def test_a_short_or_foreign_frame_is_ignored(self):
        self.assertIsNone(hidpp.diverted_controls(b"\x11\xff\x09", self.REPROG))
        self.assertIsNone(hidpp.diverted_controls(bytes(20), self.REPROG))


class DiscoveryTests(unittest.TestCase):
    def test_finding_collections_never_throws(self):
        self.assertIsInstance(hidpp.find_vendor_collections(), list)

    def test_anything_found_is_a_plausible_hidpp_collection(self):
        found = hidpp.find_vendor_collections()
        if not found:
            self.skipTest("no Logitech device is attached")
        for candidate in found:
            self.assertEqual(candidate.vendor, hidpp.LOGITECH)
            self.assertGreaterEqual(candidate.usage_page, 0xFF00)
            self.assertEqual(candidate.report_length, hidpp.HIDPP_LONG_SIZE)
            self.assertTrue(candidate.path.startswith("\\\\?\\"))


class ShutdownTests(unittest.TestCase):
    """The listener is a Thread, so it must not tread on Thread's own attributes."""

    def setUp(self) -> None:
        # Never started, so it neither opens a device nor touches the real mouse.
        self.button = hidpp.GestureButton(lambda pressed, position: None)

    def test_the_stop_flag_does_not_shadow_thread_internals(self):
        # threading.Thread calls its own private _stop() from join(). An attribute of
        # that name replaces the method, and join() then raises TypeError instead of
        # waiting - so the app could never finish shutting down and stayed alive with
        # no window, holding the single-instance mutex.
        self.assertTrue(callable(getattr(self.button, "_stop", None)),
                        "GestureButton has shadowed Thread._stop")

    def test_asking_it_to_stop_sets_the_flag(self):
        self.assertFalse(self.button._stopping.is_set())
        self.button.stop()
        self.assertTrue(self.button._stopping.is_set())

    def test_it_is_a_daemon_so_it_cannot_hold_the_process_open(self):
        self.assertTrue(self.button.daemon)


if __name__ == "__main__":
    unittest.main()
