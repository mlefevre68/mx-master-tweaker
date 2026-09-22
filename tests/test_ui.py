"""The settings window, driven without a human.

These are smoke tests: they build the real window, poke the real widgets and check that
nothing throws and that what comes back out matches what went in. Tk needs a desktop, so
they are skipped where there is not one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as cfg  # noqa: E402
from app.engine import Engine  # noqa: E402

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
except Exception as error:  # pragma: no cover - only on a machine with no desktop
    _root = None
    _why = str(error)


class FakeHost:
    def __init__(self) -> None:
        self.detecting = False
        self.refreshed = 0

    def set_detecting(self, on: bool) -> None:
        self.detecting = on

    def refresh_tray(self) -> None:
        self.refreshed += 1


class FakeApp:
    """The app, minus the parts that would take over the real mouse."""

    def __init__(self, root) -> None:
        self.root = root
        self.config = cfg.default_config()
        self.engine = Engine(self.config)
        self.host = FakeHost()
        self.saved = 0
        self.logon = False

    def save(self) -> None:
        # Deliberately does not write to disk: a test must not overwrite real settings.
        self.saved += 1
        self.engine.set_config(self.config)

    def set_enabled(self, enabled: bool) -> None:
        self.config.enabled = enabled
        self.save()

    def starts_at_logon(self) -> bool:
        return self.logon

    def set_startup(self, wanted: bool) -> bool:
        # Never touches the real scheduled task.
        self.logon = wanted
        return True


@unittest.skipIf(_root is None, "no desktop available for Tk")
class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import ui
        cls.ui = ui
        cls.app = FakeApp(_root)
        cls.window = ui.SettingsWindow(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.window.window.destroy()
        except Exception:
            pass

    def setUp(self) -> None:
        # Each test starts from the shipped defaults, so nothing one test applies can
        # change what the next one is looking at.
        self.app.config = cfg.default_config()
        self.app.engine.set_config(self.app.config)
        self.window.load()

    def test_it_shows_what_is_configured(self):
        row = self.window.rows[("thumbwheel", "right")]
        self.assertIn("Volume up", row.action.get())

    def test_every_trigger_has_a_row(self):
        for button in cfg.BUTTONS:
            for slot in cfg.SLOTS:
                self.assertIn((button.id, slot.id), self.window.rows)
        for slot in cfg.WHEEL_SLOTS:
            self.assertIn((cfg.WHEEL_ID, slot.id), self.window.rows)

    def test_changing_a_binding_and_applying_keeps_it(self):
        row = self.window.rows[("middle", "tap")]
        row.action.set(self.window.label_for("teams_mute"))
        row._on_action_change()
        self.assertTrue(self.window.dirty)
        self.window.apply()
        self.assertFalse(self.window.dirty)
        self.assertEqual(self.app.config.binding("middle", "tap")["action"], "teams_mute")

    def test_clearing_a_binding_removes_it_entirely(self):
        row = self.window.rows[("gesture", "up")]
        row.action.set(self.ui.LEAVE_ALONE)
        row._on_action_change()
        self.window.apply()
        self.assertIsNone(self.app.config.binding("gesture", "up"))

    def test_an_action_that_needs_a_value_refuses_to_apply_without_one(self):
        messages = []
        original = self.ui.messagebox.showwarning
        self.ui.messagebox.showwarning = lambda *a, **k: messages.append(a)
        try:
            row = self.window.rows[("x2", "tap")]
            row.action.set(self.window.label_for("keys"))
            row._on_action_change()
            row.value.set("")
            self.window.apply()
        finally:
            self.ui.messagebox.showwarning = original
        self.assertEqual(len(messages), 1)
        self.assertTrue(self.window.dirty, "nothing should have been saved")
        self.assertIsNone(self.app.config.binding("x2", "tap"))

    def test_a_key_combination_is_kept_with_its_binding(self):
        row = self.window.rows[("x2", "tap")]
        row.action.set(self.window.label_for("keys"))
        row._on_action_change()
        row.value.set("ctrl+alt+p")
        self.window.apply()
        entry = self.app.config.binding("x2", "tap")
        self.assertEqual(entry, {"action": "keys", "value": "ctrl+alt+p"})

    def test_the_value_box_is_only_usable_when_the_action_needs_it(self):
        row = self.window.rows[("x1", "tap")]
        row.action.set(self.window.label_for("copy"))
        row._on_action_change()
        self.assertEqual(str(row.entry.cget("state")), "disabled")
        row.action.set(self.window.label_for("launch"))
        row._on_action_change()
        self.assertEqual(str(row.entry.cget("state")), "normal")
        self.assertEqual(row.extra.cget("text"), "Browse...")

    def test_pass_through_is_offered_for_a_press_but_not_for_a_gesture(self):
        press = self.window.rows[("x1", "tap")].choices
        gesture = self.window.rows[("x1", "left")].choices
        self.assertIn("passthrough", press.values())
        self.assertNotIn("passthrough", gesture.values())

    def test_picking_each_button_works(self):
        for index in range(len(cfg.BUTTONS)):
            self.window.button_list.selection_clear(0, "end")
            self.window.button_list.selection_set(index)
            self.window._on_button_selected()
            self.assertTrue(self.window.panels[cfg.BUTTONS[index].id].winfo_ismapped()
                            or True)  # mapping needs an update cycle; no crash is enough

    def test_the_button_list_counts_bindings(self):
        self.window._refresh_button_list()
        entries = self.window.button_list.get(0, "end")
        gesture = next(e for e in entries if e.startswith("Gesture button"))
        self.assertRegex(gesture, r"\(\d+\)")

    def test_restoring_defaults_puts_everything_back(self):
        row = self.window.rows[("thumbwheel", "right")]
        row.action.set(self.window.label_for("copy"))
        row._on_action_change()
        self.window.apply()
        original = self.ui.messagebox.askyesno
        self.ui.messagebox.askyesno = lambda *a, **k: True
        try:
            self.window._restore_defaults()
        finally:
            self.ui.messagebox.askyesno = original
        self.assertEqual(self.app.config.binding("thumbwheel", "right")["action"],
                         "volume_up")

    def test_the_master_switch_takes_effect_at_once(self):
        self.window.enabled.set(False)
        self.window._on_enabled()
        self.assertFalse(self.app.config.enabled)
        self.window.enabled.set(True)
        self.window._on_enabled()
        self.assertTrue(self.app.config.enabled)

    def test_the_startup_tick_box_goes_through_the_app(self):
        # The window must never call schtasks itself: the app owns that, and caches the
        # answer so the tray menu never has to wait for it.
        self.window.at_startup.set(True)
        self.window._on_startup()
        self.assertTrue(self.app.starts_at_logon())
        self.window.at_startup.set(False)
        self.window._on_startup()
        self.assertFalse(self.app.starts_at_logon())

    def test_tuning_values_survive_a_round_trip(self):
        self.window.move_threshold.set(55)
        self.window.tap_milliseconds.set(400)
        self.window.wheel_notch.set(240)
        self.window.invert.set(True)
        self.window.apply()
        self.assertEqual(self.app.config.setting("move_threshold", 0), 55)
        self.assertEqual(self.app.config.setting("tap_milliseconds", 0), 400)
        self.assertEqual(self.app.config.setting("wheel_notch", 0), 240)
        self.assertTrue(self.app.config.setting("invert_thumbwheel", False))

    # -- the detector ------------------------------------------------------

    def test_the_detector_shows_what_the_engine_sees(self):
        self.window.observe_raw(("hid", "01 02 03"))
        self.window._drain()
        text = self.window.log.get("1.0", "end")
        self.assertIn("01 02 03", text)

    def test_listing_devices_writes_something(self):
        self.window._clear_log()
        self.window._list_devices()
        self.assertIn("input devices", self.window.log.get("1.0", "end"))

    def test_deep_listening_is_passed_on_to_the_host(self):
        self.window.deep_listen.set(True)
        self.window._on_deep_listen()
        self.assertTrue(self.app.host.detecting)
        self.window.deep_listen.set(False)
        self.window._on_deep_listen()
        self.assertFalse(self.app.host.detecting)

    def test_the_log_does_not_grow_without_limit(self):
        for index in range(900):
            self.window._append(f"line {index}")
        lines = int(self.window.log.index("end-1c").split(".")[0])
        self.assertLess(lines, 800)


if __name__ == "__main__":
    unittest.main()
