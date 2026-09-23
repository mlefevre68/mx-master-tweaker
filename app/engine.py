"""Turning mouse events into actions.

The rules, in one place:

* A button nobody has bound is never touched. It reaches applications exactly as it
  always did, and this app never sees a reason to interfere.
* A button that *is* bound is swallowed entirely, and what happens instead is decided
  when the button is released. That delay is unavoidable: at the moment the button goes
  down there is no way to know whether it will turn out to be a press, a direction or a
  scroll, and guessing wrong is worse than a few tens of milliseconds.
* Holding the button still for a long time and then releasing cancels, which gives you a
  way out once you have started a gesture you did not mean.

Work done inside a low-level hook has to finish in a few milliseconds. Windows silently
removes hooks that take longer than the ``LowLevelHooksTimeout`` registry value, which
is 300 ms by default, and the symptom is a mouse that mysteriously stops responding to
the app after a while. So the hook only ever decides and enqueues; the actions themselves
run on a separate thread.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from . import actions, winapi as w
from .config import Config

log = logging.getLogger(__name__)

XBUTTON_SOURCES = {w.XBUTTON1: "x1", w.XBUTTON2: "x2"}

# How to re-create a button we swallowed, for the "pass through" action.
PASSTHROUGH = {
    "middle": (w.MOUSEEVENTF_MIDDLEDOWN, w.MOUSEEVENTF_MIDDLEUP, 0),
    "x1": (w.MOUSEEVENTF_XDOWN, w.MOUSEEVENTF_XUP, w.XBUTTON1),
    "x2": (w.MOUSEEVENTF_XDOWN, w.MOUSEEVENTF_XUP, w.XBUTTON2),
}


@dataclass
class Held:
    source: str
    pressed_at: float
    origin: tuple[int, int]
    # Set once the hold has already produced something, so releasing does not also
    # fire the press action on top of it.
    consumed: bool = False
    far: bool = False


@dataclass
class Drag:
    """A window being carried around by the mouse while a button is held."""
    hwnd: int
    action: str
    origin: tuple[int, int]
    # Where the window was, and how big, when it actually started moving. Left unset
    # until then: a plain click must never touch the window at all, only a drag that
    # really happens should.
    start: tuple[int, int, int, int] | None = None
    moved: bool = False
    # Set while the cursor is near a screen edge, to (zone, work_area). Only moving a
    # window can end in a snap; resizing keeps whatever size you drag it to.
    snap: tuple[str, tuple[int, int, int, int]] | None = None


@dataclass
class Observation:
    """One input event, for the settings window's detector."""
    kind: str
    detail: str
    at: float = field(default_factory=time.time)


class Dispatcher:
    """Runs actions on their own thread, so the hook can return immediately."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=256)
        self._thread = threading.Thread(target=self._loop, name="actions", daemon=True)
        self._thread.start()

    def submit(self, action_id: str, value: str = "") -> None:
        try:
            self._queue.put_nowait((action_id, value))
        except queue.Full:
            log.warning("Dropping %r: actions are backing up", action_id)

    def _loop(self) -> None:
        while True:
            action_id, value = self._queue.get()
            if action_id is None:
                return
            actions.run(action_id, value)

    def stop(self) -> None:
        self._queue.put((None, ""))


class Engine:
    """Decides, for every mouse event, what happens and whether Windows sees it."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.dispatcher = Dispatcher()
        self._held: dict[str, Held] = {}
        self._drags: dict[str, Drag] = {}
        self._wheel_remainder: dict[str, int] = {}
        self._seen: set[tuple[str, str]] = set()
        self._observers: list = []
        self._lock = threading.Lock()

    # -- wiring ------------------------------------------------------------

    def set_config(self, config: Config) -> None:
        self.config = config
        # Anything mid-hold was decided against the old rules; start clean.
        self._held.clear()
        self._drags.clear()
        self._wheel_remainder.clear()
        # Report the first use of each binding again, so the log shows the new set
        # proving itself rather than staying silent about it.
        self._seen.clear()

    def add_observer(self, callback) -> None:
        with self._lock:
            self._observers.append(callback)

    def remove_observer(self, callback) -> None:
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def _observe(self, kind: str, detail: str) -> None:
        with self._lock:
            watchers = list(self._observers)
        if not watchers:
            return
        event = Observation(kind, detail)
        for callback in watchers:
            try:
                callback(event)
            except Exception:  # an observer is a debugging aid, never a reason to fail
                log.exception("Observer failed")

    # -- settings ----------------------------------------------------------

    @property
    def _tap_limit(self) -> float:
        return max(0.1, float(self.config.setting("tap_milliseconds", 700)) / 1000.0)

    @property
    def _notch(self) -> int:
        return max(1, int(self.config.setting("wheel_notch", 120)))

    # -- the hook's entry point -------------------------------------------

    def on_mouse(self, message: int, info: w.MSLLHOOKSTRUCT) -> bool:
        """Handle one mouse event. Returns True when Windows must not see it."""
        # Our own synthesised input comes straight back through this hook. Letting it
        # through untouched is what stops a binding from triggering itself.
        if info.dwExtraInfo == w.INJECTED_SIGNATURE:
            return False

        try:
            return self._handle(message, info)
        except Exception:
            log.exception("Mouse handling failed; letting the event through")
            return False

    def _handle(self, message: int, info: w.MSLLHOOKSTRUCT) -> bool:
        if message == w.WM_MOUSEMOVE:
            self._track_move(info)
            return False

        if message == w.WM_MOUSEHWHEEL:
            return self._thumbwheel(info)

        if message == w.WM_MOUSEWHEEL:
            return self._wheel(info)

        source = None
        pressed = False
        if message in (w.WM_MBUTTONDOWN, w.WM_MBUTTONUP):
            source, pressed = "middle", message == w.WM_MBUTTONDOWN
        elif message in (w.WM_XBUTTONDOWN, w.WM_XBUTTONUP):
            source = XBUTTON_SOURCES.get(w.high_word_signed(info.mouseData))
            pressed = message == w.WM_XBUTTONDOWN
        if source is None:
            return False

        self._observe("button", f"{source} {'down' if pressed else 'up'}")
        return self.on_button(source, pressed, (info.pt.x, info.pt.y))

    # -- buttons -----------------------------------------------------------

    def on_button(self, source: str, pressed: bool, position: tuple[int, int]) -> bool:
        """A button went down or up. Also the entry point for buttons that do not
        arrive as Windows mouse messages, such as a diverted gesture button."""
        if not self.config.enabled or not self.config.is_bound(source):
            self._held.pop(source, None)
            return False

        if pressed:
            self._held[source] = Held(source, time.monotonic(), position)
            self._begin_drag(source, position)
            return True

        held = self._held.pop(source, None)
        dragged = self._end_drag(source)
        if held is None:
            # We never saw the press - it happened before the app started, or while it
            # was disabled. Swallowing a lone release would strand the button down.
            return False

        if held.consumed or dragged:
            return True

        direction = self._direction(held, position)
        if direction:
            self._fire(source, direction)
            return True

        tap = self.config.binding(source, "tap")
        if tap is None:
            return True
        # "Pass through" always means pass through, however long the button was held,
        # because that is what the button would have done on its own.
        if tap["action"] == "passthrough":
            self._passthrough(source)
            return True
        if time.monotonic() - held.pressed_at <= self._tap_limit:
            self._fire(source, "tap")
        return True

    def _track_move(self, info: w.MSLLHOOKSTRUCT) -> None:
        if self._drags:
            self._carry(info.pt.x, info.pt.y)
        if not self._held:
            return
        # A copy, because the gesture button arrives on its own thread and can add or
        # remove an entry while the hook is part-way through this loop.
        for held in list(self._held.values()):
            if held.far:
                continue
            threshold = self.config.threshold_for(held.source)
            dx = info.pt.x - held.origin[0]
            dy = info.pt.y - held.origin[1]
            if abs(dx) >= threshold or abs(dy) >= threshold:
                held.far = True

    def _direction(self, held: Held, position: tuple[int, int]) -> str | None:
        """Which way the mouse went while the button was down, if far enough."""
        if not held.far:
            return None
        dx = position[0] - held.origin[0]
        dy = position[1] - held.origin[1]
        if max(abs(dx), abs(dy)) < self.config.threshold_for(held.source):
            return None
        # Screen coordinates grow downwards, so a negative dy is "up" on the desk.
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "down" if dy > 0 else "up"

    # -- dragging a window -------------------------------------------------

    def _begin_drag(self, source: str, position: tuple[int, int]) -> None:
        """Note which window is under the cursor, in case this press turns into a drag.

        Nothing about the window is touched here - not even a maximised window is
        restored - because most presses of a button bound to Hold + drag are not a
        drag at all: they are an ordinary click, and a click must work exactly as it
        always has. The window is only actually grabbed once the mouse has moved far
        enough that this really is a drag; see ``_carry``.
        """
        binding = self.config.binding(source, "drag")
        if binding is None or binding["action"] not in actions.DRAG_ACTIONS:
            return
        hwnd = w.draggable_window_at(*position)
        if hwnd is None:
            return
        self._drags[source] = Drag(hwnd=hwnd, action=binding["action"], origin=position)

    def _carry(self, x: int, y: int) -> None:
        """Move or resize every grabbed window to follow the cursor.

        Called for every mouse movement while a button is held, so it must stay cheap:
        the window is positioned asynchronously, and a window that has gone away is
        dropped rather than retried.
        """
        snapping = self.config.setting("snap_on_drag", True)
        # How far a click is allowed to wobble before it counts as a drag: the same
        # distance Windows itself uses everywhere else to tell the two apart, rather
        # than a value invented for this app. A fixed handful of pixels was too easily
        # crossed by an ordinary hand while clicking, which was the reported bug.
        arm_distance = w.drag_arm_distance()
        for source, drag in list(self._drags.items()):
            if not w.user32.IsWindow(drag.hwnd):
                del self._drags[source]
                continue
            dx = x - drag.origin[0]
            dy = y - drag.origin[1]
            if not drag.moved and max(abs(dx), abs(dy)) < arm_distance:
                continue  # ignore the shake of pressing the button

            if not drag.moved:
                # The exact moment a press turns into a real drag. A maximised window
                # is restored only now, the same as dragging a real title bar does,
                # and its rect is captured only now too, so later moves are measured
                # from where the window actually is rather than where it was before
                # being restored.
                if drag.action == "grab_window":
                    w.unmaximise(drag.hwnd)
                drag.start = w.window_rect(drag.hwnd)
                if drag.start is None:
                    del self._drags[source]
                    continue
                drag.moved = True
                self._observe("drag", f"{source} grabbed {w.window_class(drag.hwnd)}")

            left, top, width, height = drag.start
            if drag.action == "grab_resize":
                w.resize_window(drag.hwnd, width + dx, height + dy)
            else:
                w.move_window(drag.hwnd, left + dx, top + dy)
                # Resizing keeps whatever size you drag it to; only a plain move can
                # end in a snap, the same as Windows only offers it from the title bar
                # and not from a resize handle.
                drag.snap = w.snap_zone_at(x, y) if snapping else None

    def _end_drag(self, source: str) -> bool:
        """Let go. Returns whether this button just performed a real drag.

        A press that grabbed a window but never actually moved it is not a drag - it
        is an ordinary click that happened to land on a window with a Hold + drag
        binding, such as clicking Back or Forward while it hovers over a browser. That
        must still fire whatever Press is bound to, Pass through included. Consuming
        the button outright here is exactly the bug reported: an ordinary click no
        longer working because a window happened to be under the cursor.
        """
        drag = self._drags.pop(source, None)
        if drag is None or not drag.moved:
            return False
        if drag.snap and w.user32.IsWindow(drag.hwnd):
            zone, area = drag.snap
            w.apply_snap(drag.hwnd, zone, area)
            self._observe("drag", f"{source} released - snapped {zone}")
        else:
            self._observe("drag", f"{source} released")
        return True

    def _passthrough(self, source: str) -> None:
        recipe = PASSTHROUGH.get(source)
        if recipe is None:
            # Nothing to replay. The gesture button is the case that matters: the mouse
            # reports it as button 6 and the Windows mouse driver maps only 1 to 5, so
            # no event ever existed to hand back. Settings written by an older version
            # can still ask for this, so say why rather than appearing to do it.
            log.warning("%r has no event of its own for Windows, so there is nothing "
                        "to pass through; bind an action to it instead", source)
            return
        down, up, data = recipe
        w.send_inputs([w.mouse_input(down, data=data << 16),
                       w.mouse_input(up, data=data << 16)])

    # -- wheels ------------------------------------------------------------

    def _wheel(self, info: w.MSLLHOOKSTRUCT) -> bool:
        """The main wheel. Only interesting while a bound button is held down."""
        if not self.config.enabled or not self._held:
            return False
        delta = w.high_word_signed(info.mouseData)
        slot = "wheel_up" if delta > 0 else "wheel_down"
        for source, held in list(self._held.items()):
            if self.config.binding(source, slot) is None:
                continue
            for _ in range(self._notches(f"{source}:{slot}", abs(delta))):
                self._fire(source, slot)
            held.consumed = True
            return True
        return False

    def _thumbwheel(self, info: w.MSLLHOOKSTRUCT) -> bool:
        delta = w.high_word_signed(info.mouseData)
        if delta == 0:
            return False
        self._observe("wheel", f"thumbwheel {'right' if delta > 0 else 'left'} ({delta})")
        if not self.config.enabled:
            return False
        if self.config.setting("invert_thumbwheel", False):
            delta = -delta
        slot = "right" if delta > 0 else "left"
        if self.config.binding("thumbwheel", slot) is None:
            return False
        for _ in range(self._notches(f"thumbwheel:{slot}", abs(delta))):
            self._fire("thumbwheel", slot)
        return True

    def _notches(self, key: str, delta: int) -> int:
        """How many whole notches this event completes.

        High-resolution wheels report a stream of small deltas instead of one lump of
        120, so the remainder is carried over rather than thrown away - otherwise a slow
        roll of the thumb wheel would do nothing at all.
        """
        notch = self._notch
        total = self._wheel_remainder.get(key, 0) + delta
        whole, self._wheel_remainder[key] = divmod(total, notch)
        # A direction change should not be paid for out of the other direction's credit.
        for other in list(self._wheel_remainder):
            if other != key:
                self._wheel_remainder[other] = 0
        return max(0, min(whole, 10))

    # -- firing ------------------------------------------------------------

    def _fire(self, source: str, slot: str) -> None:
        binding = self.config.binding(source, slot)
        if binding is None:
            return
        action_id = binding["action"]
        if action_id == "passthrough" and slot != "tap":
            # Pass through only has a meaning for a plain press.
            return
        if action_id == "passthrough":
            self._passthrough(source)
            return
        if action_id == "none":
            return
        if action_id in actions.DRAG_ACTIONS:
            # A mode, not an event. The drag machinery runs it while the button is
            # held; there is nothing to fire on release.
            return
        # The first time a binding fires it is recorded plainly, so the log answers
        # "is this thing working at all?" without having to be turned up first. After
        # that it drops to debug, because a volume roll would otherwise fill the file.
        if (source, slot) in self._seen:
            log.debug("%s %s -> %s %s", source, slot, action_id, binding.get("value", ""))
        else:
            self._seen.add((source, slot))
            log.info("%s %s -> %s %s", source, slot, action_id, binding.get("value", ""))
        self._observe("action", f"{source} {slot} -> {action_id}")
        self.dispatcher.submit(action_id, binding.get("value", ""))

    def stop(self) -> None:
        self.dispatcher.stop()
