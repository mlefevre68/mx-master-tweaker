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


class DragTests(unittest.TestCase):
    """Grabbing a window and carrying it about.

    The Win32 calls are replaced so that no real window is ever moved; what is checked
    is that the engine asks for the right thing at the right moment.
    """

    WINDOW = 4242

    def setUp(self) -> None:
        from app import engine as engine_module
        self.moves: list[tuple[int, int, int]] = []
        self.resizes: list[tuple[int, int, int]] = []
        self.restored: list[int] = []
        self.window_at = self.WINDOW
        self.rect = (100, 200, 800, 600)

        self._saved = {name: getattr(w, name) for name in
                       ("draggable_window_at", "window_rect", "move_window",
                        "resize_window", "unmaximise", "window_class",
                        "snap_zone_at", "apply_snap")}
        w.draggable_window_at = lambda x, y: self.window_at
        w.window_rect = lambda hwnd: self.rect
        w.move_window = lambda hwnd, x, y: self.moves.append((hwnd, x, y))
        w.resize_window = lambda hwnd, cx, cy: self.resizes.append((hwnd, cx, cy))
        w.unmaximise = lambda hwnd: self.restored.append(hwnd)
        w.window_class = lambda hwnd: "TestWindow"
        # No zone unless a test says otherwise: real screen coordinates would
        # occasionally, accidentally be near a real edge on whatever machine runs this.
        self.zone = None
        self.snapped: list[tuple[int, str, tuple]] = []
        w.snap_zone_at = lambda x, y: self.zone
        w.apply_snap = lambda hwnd, zone, area: self.snapped.append((hwnd, zone, area))
        self._is_window = w.user32.IsWindow
        w.user32.IsWindow = lambda hwnd: 1
        self.engine_module = engine_module

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(w, name, value)
        w.user32.IsWindow = self._is_window

    def build(self, bindings: dict, **settings) -> Engine:
        base = {"move_threshold": 30, "tap_milliseconds": 700, "wheel_notch": 120}
        base.update(settings)
        engine = Engine(Config(enabled=True, settings=base, bindings=bindings))
        engine.dispatcher = RecordingDispatcher()
        return engine

    def grab(self) -> Engine:
        return self.build({"gesture": {"drag": {"action": "grab_window"}}})

    def test_the_window_follows_the_mouse(self):
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 540))
        self.assertEqual(self.moves[-1], (self.WINDOW, 160, 240),
                         "the window should move by the same amount as the mouse")

    def test_it_keeps_following(self):
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        for x, y in ((510, 505), (530, 520), (600, 560)):
            engine._track_move(event(x, y))
        self.assertEqual(len(self.moves), 3)
        self.assertEqual(self.moves[-1], (self.WINDOW, 200, 260))

    def test_moving_is_relative_to_where_the_grab_began(self):
        # Not to where the window was last put, which would drift.
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(600, 600))
        engine._track_move(event(500, 500))
        self.assertEqual(self.moves[-1], (self.WINDOW, 100, 200), "back where it started")

    def test_letting_go_stops_the_drag(self):
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 540))
        engine.on_button("gesture", False, (560, 540))
        moved = len(self.moves)
        engine._track_move(event(700, 700))
        self.assertEqual(len(self.moves), moved, "it should not still be following")

    def test_a_maximised_window_is_restored_before_moving(self):
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        self.assertEqual(self.restored, [self.WINDOW])

    def test_resizing_changes_the_size_and_not_the_position(self):
        engine = self.build({"gesture": {"drag": {"action": "grab_resize"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 540))
        self.assertEqual(self.moves, [])
        # The window started 800x600 and the mouse moved 60 right and 40 down.
        self.assertEqual(self.resizes[-1], (self.WINDOW, 860, 640))

    def test_resizing_leaves_a_maximised_window_alone(self):
        engine = self.build({"gesture": {"drag": {"action": "grab_resize"}}})
        engine.on_button("gesture", True, (500, 500))
        self.assertEqual(self.restored, [])

    def test_a_grab_swallows_the_press(self):
        engine = self.build({"gesture": {"drag": {"action": "grab_window"},
                                         "tap": {"action": "task_view"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 540))
        engine.on_button("gesture", False, (560, 540))
        self.assertEqual(engine.dispatcher.fired, [],
                         "a drag is not also a click")

    def test_a_grab_that_never_moved_still_swallows_the_press(self):
        # The button was used to grab, not to click, even if nothing came of it.
        engine = self.build({"gesture": {"drag": {"action": "grab_window"},
                                         "tap": {"action": "task_view"}}})
        engine.on_button("gesture", True, (500, 500))
        engine.on_button("gesture", False, (500, 500))
        self.assertEqual(engine.dispatcher.fired, [])

    def test_directions_do_not_fire_while_dragging(self):
        engine = self.build({"gesture": {"drag": {"action": "grab_window"},
                                         "right": {"action": "desktop_next"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(700, 500))
        engine.on_button("gesture", False, (700, 500))
        self.assertEqual(engine.dispatcher.fired, [])

    def test_a_tiny_wobble_does_not_move_the_window(self):
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(501, 500))
        self.assertEqual(self.moves, [], "pressing the button should not nudge it")

    def test_nothing_happens_when_there_is_no_window_under_the_cursor(self):
        self.window_at = None
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(600, 600))
        self.assertEqual(self.moves, [])

    def test_a_window_that_goes_away_mid_drag_is_dropped(self):
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 540))
        w.user32.IsWindow = lambda hwnd: 0
        engine._track_move(event(600, 600))
        self.assertEqual(len(self.moves), 1, "it should stop rather than keep trying")

    def test_a_button_without_a_drag_binding_grabs_nothing(self):
        engine = self.build({"gesture": {"tap": {"action": "task_view"}}})
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(600, 600))
        self.assertEqual(self.moves, [])

    def test_the_press_still_works_on_a_button_that_can_also_drag(self):
        engine = self.build({"gesture": {"drag": {"action": "grab_window"},
                                         "tap": {"action": "task_view"}}})
        # No window under the cursor, so no grab happens and the press is just a press.
        self.window_at = None
        engine.on_button("gesture", True, (500, 500))
        engine.on_button("gesture", False, (500, 500))
        self.assertEqual(engine.dispatcher.fired, [("task_view", "")])

    # -- snapping to a screen edge ------------------------------------------

    def test_releasing_near_an_edge_snaps_the_window(self):
        engine = self.grab()
        area = (0, 0, 1920, 1040)
        self.zone = ("left", area)
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(10, 500))  # dragged to the left edge
        engine.on_button("gesture", False, (10, 500))
        self.assertEqual(self.snapped, [(self.WINDOW, "left", area)])

    def test_a_free_release_does_not_snap(self):
        engine = self.grab()
        self.zone = None  # nowhere near an edge
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(560, 540))
        engine.on_button("gesture", False, (560, 540))
        self.assertEqual(self.snapped, [])
        # The window was still moved normally.
        self.assertEqual(self.moves[-1], (self.WINDOW, 160, 240))

    def test_moving_away_from_the_edge_again_cancels_the_snap(self):
        # The zone is re-checked on every move, so drifting back off the edge before
        # letting go must not leave a stale snap queued up.
        engine = self.grab()
        engine.on_button("gesture", True, (500, 500))
        self.zone = ("right", (0, 0, 1920, 1040))
        engine._track_move(event(1910, 500))
        self.zone = None
        engine._track_move(event(700, 500))
        engine.on_button("gesture", False, (700, 500))
        self.assertEqual(self.snapped, [])

    def test_resizing_never_snaps(self):
        # Windows only offers this from a title-bar drag, never from a resize handle,
        # and pinning a resize to a screen edge would be a surprise, not a convenience.
        engine = self.build({"gesture": {"drag": {"action": "grab_resize"}}})
        self.zone = ("left", (0, 0, 1920, 1040))
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(10, 500))
        engine.on_button("gesture", False, (10, 500))
        self.assertEqual(self.snapped, [])

    def test_turning_the_setting_off_stops_it_snapping(self):
        engine = self.build({"gesture": {"drag": {"action": "grab_window"}}},
                            snap_on_drag=False)
        self.zone = ("maximize", (0, 0, 1920, 1040))
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(500, 5))
        engine.on_button("gesture", False, (500, 5))
        self.assertEqual(self.snapped, [])

    def test_a_window_that_closed_mid_drag_is_not_snapped(self):
        engine = self.grab()
        self.zone = ("left", (0, 0, 1920, 1040))
        engine.on_button("gesture", True, (500, 500))
        engine._track_move(event(10, 500))
        w.user32.IsWindow = lambda hwnd: 0
        engine.on_button("gesture", False, (10, 500))
        self.assertEqual(self.snapped, [])


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
            "scroll_left", "scroll_right", "middle_click", "lock_pc", "keys", "launch",
        }
        # Drag actions are a mode the engine runs while the button is held, so they are
        # never passed to run() at all.
        runnable |= actions.DRAG_ACTIONS
        for action in actions.CATALOGUE:
            self.assertIn(action.id, runnable, f"{action.id} is offered but not handled")

    def test_drag_actions_are_marked_as_such(self):
        from app import actions
        self.assertEqual(actions.DRAG_ACTIONS, {"grab_window", "grab_resize"})
        for action_id in actions.DRAG_ACTIONS:
            self.assertTrue(actions.BY_ID[action_id].drag)
            # They must not also be keystrokes, or they would fire on release as well
            # as running for the whole hold.
            self.assertNotIn(action_id, actions._COMBOS)

    def test_locking_does_not_go_through_a_keystroke(self):
        # Win+L belongs to winlogon's secure attention path, which injected input
        # cannot reach. Sending it delivers the keys and leaves the machine unlocked,
        # so this has to use LockWorkStation instead - and must not quietly regress
        # into the combos table, where it would look implemented and do nothing.
        from app import actions
        self.assertNotIn("lock_pc", actions._COMBOS)
        self.assertTrue(hasattr(w.user32, "LockWorkStation"))

    def test_locking_calls_the_api(self):
        from app import actions
        called = []
        original = w.user32.LockWorkStation
        w.user32.LockWorkStation = lambda: (called.append(True), 1)[1]
        try:
            actions.run("lock_pc")
        finally:
            w.user32.LockWorkStation = original
        self.assertEqual(called, [True], "lock_pc did not call LockWorkStation")

    def test_a_refused_lock_is_reported_rather_than_ignored(self):
        from app import actions
        original = w.user32.LockWorkStation
        w.user32.LockWorkStation = lambda: 0  # as Windows reports a refusal
        try:
            with self.assertLogs("app.actions", level="WARNING") as captured:
                actions.run("lock_pc")
        finally:
            w.user32.LockWorkStation = original
        self.assertTrue(any("lock" in line.lower() for line in captured.output))

    def test_snapping_repairs_the_always_on_top_bug(self):
        # Confirmed on a real desktop: an injected Win+Arrow can leave the window it
        # acted on flagged always-on-top, so it stays ahead of everything else until
        # something clears the flag. The repair must target whichever window was
        # active *before* the keys went out, since that is what Win+Arrow acts on.
        from app import actions
        target = 424242
        original_fg = w.user32.GetForegroundWindow
        original_clear = w.clear_topmost
        cleared = []
        w.user32.GetForegroundWindow = lambda: target
        w.clear_topmost = lambda hwnd: cleared.append(hwnd)
        original_timer = actions.threading.Timer

        class ImmediateTimer:
            def __init__(self, interval, function, args=()):
                self._function, self._args = function, args

            def start(self):
                self._function(*self._args)

        actions.threading.Timer = ImmediateTimer
        try:
            actions.run("snap_left")
        finally:
            w.user32.GetForegroundWindow = original_fg
            w.clear_topmost = original_clear
            actions.threading.Timer = original_timer
        self.assertEqual(cleared, [target])

    def test_only_the_snap_family_gets_the_repair(self):
        from app import actions
        self.assertEqual(actions.SNAP_FAMILY,
                         {"snap_left", "snap_right", "maximise_window", "minimise_window"})
        # An ordinary combo must not pay for a GetForegroundWindow call and a timer it
        # does not need.
        original = w.user32.GetForegroundWindow
        called = []
        w.user32.GetForegroundWindow = lambda: called.append(True) or 0
        try:
            actions.run("copy")
        finally:
            w.user32.GetForegroundWindow = original
        self.assertEqual(called, [])

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
