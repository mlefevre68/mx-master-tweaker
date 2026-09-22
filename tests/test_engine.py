"""What the engine is supposed to do, checked without touching a real mouse.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import winapi as w  # noqa: E402
from app.config import Config  # noqa: E402
from app.engine import Engine  # noqa: E402


class RecordingDispatcher:
    """Stands in for the action thread, so tests can see what would have happened."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, str]] = []

    def submit(self, action_id: str, value: str = "") -> None:
        self.fired.append((action_id, value))

    def stop(self) -> None:
        pass


def event(x: int = 0, y: int = 0, data: int = 0, injected: bool = False):
    info = w.MSLLHOOKSTRUCT()
    info.pt.x, info.pt.y = x, y
    info.mouseData = data & 0xFFFFFFFF
    info.dwExtraInfo = w.INJECTED_SIGNATURE if injected else 0
    return info


def wheel_data(delta: int) -> int:
    return (delta & 0xFFFF) << 16


def xbutton_data(which: int) -> int:
    return which << 16


class EngineTests(unittest.TestCase):
    def build(self, bindings: dict, **settings) -> Engine:
        base = {"move_threshold": 30, "tap_milliseconds": 700, "wheel_notch": 120,
                "invert_thumbwheel": False}
        base.update(settings)
        engine = Engine(Config(enabled=True, settings=base, bindings=bindings))
        engine.dispatcher = RecordingDispatcher()
        return engine

    # -- leaving things alone ---------------------------------------------

    def test_unbound_button_is_not_touched(self):
        engine = self.build({})
        self.assertFalse(engine.on_mouse(w.WM_MBUTTONDOWN, event()))
        self.assertFalse(engine.on_mouse(w.WM_MBUTTONUP, event()))
        self.assertEqual(engine.dispatcher.fired, [])

    def test_disabled_engine_is_not_touched(self):
        engine = self.build({"middle": {"tap": {"action": "copy"}}})
        engine.config.enabled = False
        self.assertFalse(engine.on_mouse(w.WM_MBUTTONDOWN, event()))
        self.assertEqual(engine.dispatcher.fired, [])

    def test_our_own_synthesised_input_is_ignored(self):
        engine = self.build({"middle": {"tap": {"action": "copy"}}})
        self.assertFalse(engine.on_mouse(w.WM_MBUTTONDOWN, event(injected=True)))
        self.assertEqual(engine.dispatcher.fired, [])

    def test_a_release_we_never_saw_the_press_for_is_let_through(self):
        # Otherwise a button pressed before the app started would be stuck down.
        engine = self.build({"middle": {"tap": {"action": "copy"}}})
        self.assertFalse(engine.on_mouse(w.WM_MBUTTONUP, event()))

    # -- presses -----------------------------------------------------------

    def test_press_fires_on_release_and_is_swallowed(self):
        engine = self.build({"middle": {"tap": {"action": "teams_mute"}}})
        self.assertTrue(engine.on_mouse(w.WM_MBUTTONDOWN, event(100, 100)))
        self.assertEqual(engine.dispatcher.fired, [], "must not fire until released")
        self.assertTrue(engine.on_mouse(w.WM_MBUTTONUP, event(100, 100)))
        self.assertEqual(engine.dispatcher.fired, [("teams_mute", "")])

    def test_holding_still_for_too_long_cancels(self):
        engine = self.build({"middle": {"tap": {"action": "copy"}}},
                            tap_milliseconds=1)
        engine.on_mouse(w.WM_MBUTTONDOWN, event(10, 10))
        engine._held["middle"].pressed_at -= 1.0
        self.assertTrue(engine.on_mouse(w.WM_MBUTTONUP, event(10, 10)))
        self.assertEqual(engine.dispatcher.fired, [])

    def test_back_button_is_recognised(self):
        engine = self.build({"x1": {"tap": {"action": "nav_back"}}})
        data = xbutton_data(w.XBUTTON1)
        self.assertTrue(engine.on_mouse(w.WM_XBUTTONDOWN, event(0, 0, data)))
        self.assertTrue(engine.on_mouse(w.WM_XBUTTONUP, event(0, 0, data)))
        self.assertEqual(engine.dispatcher.fired, [("nav_back", "")])

    def test_forward_button_does_not_answer_for_back(self):
        engine = self.build({"x1": {"tap": {"action": "nav_back"}}})
        data = xbutton_data(w.XBUTTON2)
        self.assertFalse(engine.on_mouse(w.WM_XBUTTONDOWN, event(0, 0, data)))

    # -- gestures ----------------------------------------------------------

    def test_moving_right_while_held_fires_the_right_direction(self):
        engine = self.build({"gesture": {"tap": {"action": "copy"},
                                         "right": {"action": "desktop_next"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 502))
        engine.on_button("gesture", False, (560, 502))
        self.assertEqual(engine.dispatcher.fired, [("desktop_next", "")])

    def test_moving_up_wins_over_a_smaller_sideways_drift(self):
        engine = self.build({"gesture": {"up": {"action": "maximise_window"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(510, 420))
        engine.on_button("gesture", False, (510, 420))
        self.assertEqual(engine.dispatcher.fired, [("maximise_window", "")])

    def test_a_small_wobble_still_counts_as_a_press(self):
        engine = self.build({"gesture": {"tap": {"action": "task_view"},
                                         "right": {"action": "desktop_next"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(505, 498))
        engine.on_button("gesture", False, (505, 498))
        self.assertEqual(engine.dispatcher.fired, [("task_view", "")])

    def test_a_direction_with_nothing_bound_to_it_does_nothing(self):
        engine = self.build({"gesture": {"tap": {"action": "task_view"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(600, 500))
        engine.on_button("gesture", False, (600, 500))
        self.assertEqual(engine.dispatcher.fired, [])

    # -- hold and scroll ---------------------------------------------------

    def test_holding_and_scrolling_fires_once_per_notch(self):
        engine = self.build({"gesture": {"tap": {"action": "task_view"},
                                         "wheel_up": {"action": "volume_up"}}})
        engine.on_button("gesture", True, (0, 0))
        self.assertTrue(engine.on_mouse(w.WM_MOUSEWHEEL, event(data=wheel_data(120))))
        self.assertTrue(engine.on_mouse(w.WM_MOUSEWHEEL, event(data=wheel_data(120))))
        self.assertEqual(engine.dispatcher.fired,
                         [("volume_up", ""), ("volume_up", "")])

    def test_scrolling_while_held_cancels_the_press(self):
        engine = self.build({"gesture": {"tap": {"action": "task_view"},
                                         "wheel_up": {"action": "volume_up"}}})
        engine.on_button("gesture", True, (0, 0))
        engine.on_mouse(w.WM_MOUSEWHEEL, event(data=wheel_data(120)))
        engine.on_button("gesture", False, (0, 0))
        self.assertEqual(engine.dispatcher.fired, [("volume_up", "")])

    def test_the_wheel_is_left_alone_when_the_held_button_has_no_wheel_binding(self):
        engine = self.build({"gesture": {"tap": {"action": "task_view"}}})
        engine.on_button("gesture", True, (0, 0))
        self.assertFalse(engine.on_mouse(w.WM_MOUSEWHEEL, event(data=wheel_data(120))))

    def test_the_wheel_is_left_alone_when_no_button_is_held(self):
        engine = self.build({"gesture": {"wheel_up": {"action": "volume_up"}}})
        self.assertFalse(engine.on_mouse(w.WM_MOUSEWHEEL, event(data=wheel_data(120))))

    # -- the thumb wheel ---------------------------------------------------

    def test_thumbwheel_right_fires_its_binding(self):
        engine = self.build({"thumbwheel": {"right": {"action": "volume_up"}}})
        self.assertTrue(engine.on_mouse(w.WM_MOUSEHWHEEL, event(data=wheel_data(120))))
        self.assertEqual(engine.dispatcher.fired, [("volume_up", "")])

    def test_thumbwheel_left_fires_its_binding(self):
        engine = self.build({"thumbwheel": {"left": {"action": "volume_down"}}})
        self.assertTrue(engine.on_mouse(w.WM_MOUSEHWHEEL, event(data=wheel_data(-120))))
        self.assertEqual(engine.dispatcher.fired, [("volume_down", "")])

    def test_small_steps_add_up_to_one_notch(self):
        # A high-resolution wheel sends a stream of small deltas. Discarding them would
        # make a slow roll of the thumb wheel do nothing at all.
        engine = self.build({"thumbwheel": {"right": {"action": "volume_up"}}})
        for _ in range(3):
            engine.on_mouse(w.WM_MOUSEHWHEEL, event(data=wheel_data(40)))
        self.assertEqual(engine.dispatcher.fired, [("volume_up", "")])

    def test_inverting_swaps_the_directions(self):
        engine = self.build({"thumbwheel": {"left": {"action": "volume_down"}}},
                            invert_thumbwheel=True)
        engine.on_mouse(w.WM_MOUSEHWHEEL, event(data=wheel_data(120)))
        self.assertEqual(engine.dispatcher.fired, [("volume_down", "")])

    def test_an_unbound_thumbwheel_direction_scrolls_normally(self):
        engine = self.build({"thumbwheel": {"right": {"action": "volume_up"}}})
        self.assertFalse(engine.on_mouse(w.WM_MOUSEHWHEEL, event(data=wheel_data(-120))))

    # -- passing through ---------------------------------------------------

    def test_pass_through_replays_the_button(self):
        sent: list = []
        original = w.send_inputs
        w.send_inputs = lambda items: sent.append(items)
        try:
            engine = self.build({"x1": {"tap": {"action": "passthrough"},
                                        "up": {"action": "copy"}}})
            data = xbutton_data(w.XBUTTON1)
            engine.on_mouse(w.WM_XBUTTONDOWN, event(0, 0, data))
            engine.on_mouse(w.WM_XBUTTONUP, event(0, 0, data))
        finally:
            w.send_inputs = original
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]), 2, "a down and an up")
        self.assertEqual(engine.dispatcher.fired, [])

    def test_pass_through_survives_a_long_hold(self):
        sent: list = []
        original = w.send_inputs
        w.send_inputs = lambda items: sent.append(items)
        try:
            engine = self.build({"x1": {"tap": {"action": "passthrough"}}},
                                tap_milliseconds=1)
            data = xbutton_data(w.XBUTTON1)
            engine.on_mouse(w.WM_XBUTTONDOWN, event(0, 0, data))
            engine._held["x1"].pressed_at -= 1.0
            engine.on_mouse(w.WM_XBUTTONUP, event(0, 0, data))
        finally:
            w.send_inputs = original
        self.assertEqual(len(sent), 1, "a button's own job always gets done")


class ConfigTests(unittest.TestCase):
    def test_unknown_entries_are_discarded(self):
        from app.config import _clean
        cleaned = _clean({"bindings": {"nonsense": {"tap": {"action": "copy"}},
                                       "middle": {"nonsense": {"action": "copy"},
                                                  "tap": {"action": "copy"}}}})
        self.assertEqual(cleaned["bindings"], {"middle": {"tap": {"action": "copy",
                                                                 "value": ""}}})

    def test_defaults_bind_the_thumbwheel_to_the_volume(self):
        from app.config import default_config
        config = default_config()
        self.assertEqual(config.binding("thumbwheel", "right")["action"], "volume_up")
        self.assertEqual(config.binding("thumbwheel", "left")["action"], "volume_down")

    def test_only_configured_buttons_are_intercepted(self):
        from app.config import default_config
        config = default_config()
        self.assertTrue(config.is_bound("gesture"))
        self.assertFalse(config.is_bound("x1"))
        self.assertFalse(config.is_bound("middle"))

    def test_pass_through_on_the_gesture_button_becomes_nothing(self):
        # Windows never receives that button, so there is no event to replay. Keeping
        # the setting would leave a press that looks configured and does nothing.
        from app.config import _clean
        cleaned = _clean({"bindings": {"gesture": {"tap": {"action": "passthrough"}}}})
        self.assertEqual(cleaned["bindings"]["gesture"]["tap"]["action"], "none")

    def test_pass_through_is_kept_for_buttons_that_have_it(self):
        from app.config import _clean
        cleaned = _clean({"bindings": {
            "x1": {"tap": {"action": "passthrough"}},
            "x2": {"tap": {"action": "passthrough"}},
            "middle": {"tap": {"action": "passthrough"}},
        }})
        for source in ("x1", "x2", "middle"):
            self.assertEqual(cleaned["bindings"][source]["tap"]["action"], "passthrough")

    def test_which_buttons_can_pass_through(self):
        from app.config import passes_through
        self.assertFalse(passes_through("gesture"))
        self.assertTrue(passes_through("x1"))
        self.assertTrue(passes_through("x2"))
        self.assertTrue(passes_through("middle"))
        self.assertFalse(passes_through("thumbwheel"))


class ComboTests(unittest.TestCase):
    def test_a_plain_combination(self):
        from app.actions import parse_combo
        modifiers, key = parse_combo("ctrl+shift+m")
        self.assertEqual(modifiers, [w.VK_CONTROL, w.VK_SHIFT])
        self.assertEqual(key, ord("M"))

    def test_named_keys(self):
        from app.actions import parse_combo
        self.assertEqual(parse_combo("alt+f4"), ([w.VK_MENU], 0x73))
        self.assertEqual(parse_combo("win+left"), ([w.VK_LWIN], w.VK_LEFT))

    def test_nonsense_is_rejected_rather_than_half_sent(self):
        from app.actions import parse_combo
        self.assertIsNone(parse_combo("wibble+m"))
        self.assertIsNone(parse_combo(""))
        self.assertIsNone(parse_combo("ctrl+notakey"))

    def test_every_catalogue_action_has_a_home(self):
        from app import actions
        for action in actions.CATALOGUE:
            self.assertTrue(action.label)
            self.assertTrue(action.group)

    def test_every_action_can_actually_be_carried_out(self):
        # An action in the list that nothing knows how to perform would be offered in
        # the settings window and then silently do nothing.
        from app import actions
        runnable = set(actions._COMBOS) | actions.ENGINE_HANDLED | {
            "zoom_in", "zoom_out", "zoom_reset", "scroll_up", "scroll_down",
            "scroll_left", "scroll_right", "middle_click", "keys", "launch",
        }
        for action in actions.CATALOGUE:
            self.assertIn(action.id, runnable, f"{action.id} is offered but not handled")

    def test_showing_the_desktop_has_a_way_back(self):
        from app import actions
        # Win+D toggles, and Win+Shift+M restores what was minimised. Both are needed:
        # the toggle for the same button, the restore for a different one.
        self.assertEqual(actions._COMBOS["show_desktop"], (w.VK_LWIN, ord("D")))
        self.assertEqual(actions._COMBOS["restore_windows"],
                         (w.VK_LWIN, w.VK_SHIFT, ord("M")))
        self.assertEqual(actions._COMBOS["minimise_all"], (w.VK_LWIN, ord("M")))


if __name__ == "__main__":
    unittest.main()
