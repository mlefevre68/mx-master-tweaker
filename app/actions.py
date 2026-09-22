"""Everything a binding can do, and the code that does it.

Actions are deliberately expressed as synthesised keystrokes rather than as calls into
Windows' audio or shell APIs. Sending VK_VOLUME_UP is what a keyboard with a volume key
does, so the on-screen volume popup appears and per-application mixers behave normally,
and it needs no COM, no elevation and no extra dependency.
"""

from __future__ import annotations

import logging
import os
import shlex
from dataclasses import dataclass

from . import winapi as w

log = logging.getLogger(__name__)

# What extra information an action needs from the user, if any.
NEEDS_NOTHING = None
NEEDS_KEYS = "keys"
NEEDS_PATH = "path"


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    group: str
    needs: str | None = NEEDS_NOTHING
    # True when repeating the action quickly is meaningful, which is what makes an
    # action a good fit for a scroll trigger rather than a click.
    repeats: bool = False


# Straight key combinations, as (modifier virtual keys..., key virtual key).
_COMBOS: dict[str, tuple[int, ...]] = {
    "volume_up": (w.VK_VOLUME_UP,),
    "volume_down": (w.VK_VOLUME_DOWN,),
    "volume_mute": (w.VK_VOLUME_MUTE,),
    "media_play_pause": (w.VK_MEDIA_PLAY_PAUSE,),
    "media_next": (w.VK_MEDIA_NEXT_TRACK,),
    "media_prev": (w.VK_MEDIA_PREV_TRACK,),
    "media_stop": (w.VK_MEDIA_STOP,),

    "desktop_next": (w.VK_LWIN, w.VK_CONTROL, w.VK_RIGHT),
    "desktop_prev": (w.VK_LWIN, w.VK_CONTROL, w.VK_LEFT),
    "task_view": (w.VK_LWIN, w.VK_TAB),
    "show_desktop": (w.VK_LWIN, ord("D")),
    "lock_pc": (w.VK_LWIN, ord("L")),
    "minimise_window": (w.VK_LWIN, w.VK_DOWN),
    "maximise_window": (w.VK_LWIN, w.VK_UP),
    "snap_left": (w.VK_LWIN, w.VK_LEFT),
    "snap_right": (w.VK_LWIN, w.VK_RIGHT),
    "close_window": (w.VK_MENU, 0x73),  # Alt+F4
    "switch_app": (w.VK_MENU, w.VK_TAB),
    "search": (w.VK_LWIN, ord("S")),
    "emoji": (w.VK_LWIN, 0xBE),  # Win+.
    "screenshot": (w.VK_LWIN, w.VK_SHIFT, ord("S")),

    "nav_back": (w.VK_MENU, w.VK_LEFT),
    "nav_forward": (w.VK_MENU, w.VK_RIGHT),
    "new_tab": (w.VK_CONTROL, ord("T")),
    "close_tab": (w.VK_CONTROL, ord("W")),
    "reopen_tab": (w.VK_CONTROL, w.VK_SHIFT, ord("T")),
    "tab_next": (w.VK_CONTROL, w.VK_TAB),
    "tab_prev": (w.VK_CONTROL, w.VK_SHIFT, w.VK_TAB),
    "refresh": (w.VK_CONTROL, ord("R")),

    "copy": (w.VK_CONTROL, ord("C")),
    "paste": (w.VK_CONTROL, ord("V")),
    "cut": (w.VK_CONTROL, ord("X")),
    "undo": (w.VK_CONTROL, ord("Z")),
    "redo": (w.VK_CONTROL, ord("Y")),
    "save": (w.VK_CONTROL, ord("S")),
    "find": (w.VK_CONTROL, ord("F")),
    "select_all": (w.VK_CONTROL, ord("A")),
    "delete": (w.VK_DELETE,),
    "enter": (w.VK_RETURN,),
    "escape": (w.VK_ESCAPE,),
    "page_up": (w.VK_PRIOR,),
    "page_down": (w.VK_NEXT,),

    "teams_mute": (w.VK_CONTROL, w.VK_SHIFT, ord("M")),
    "teams_camera": (w.VK_CONTROL, w.VK_SHIFT, ord("O")),
    "teams_hand": (w.VK_CONTROL, w.VK_SHIFT, ord("K")),
    "teams_share": (w.VK_CONTROL, w.VK_SHIFT, ord("E")),
    "teams_hangup": (w.VK_CONTROL, w.VK_SHIFT, ord("H")),
}

CATALOGUE: tuple[Action, ...] = (
    Action("none", "Nothing (swallow the button)", "Nothing"),
    Action("passthrough", "Pass through (the button's normal job)", "Nothing"),

    Action("volume_up", "Volume up", "Sound", repeats=True),
    Action("volume_down", "Volume down", "Sound", repeats=True),
    Action("volume_mute", "Mute / unmute", "Sound"),
    Action("media_play_pause", "Play / pause", "Sound"),
    Action("media_next", "Next track", "Sound"),
    Action("media_prev", "Previous track", "Sound"),
    Action("media_stop", "Stop", "Sound"),

    Action("teams_mute", "Teams: mute / unmute (Ctrl+Shift+M)", "Meetings"),
    Action("teams_camera", "Teams: camera on / off (Ctrl+Shift+O)", "Meetings"),
    Action("teams_hand", "Teams: raise hand (Ctrl+Shift+K)", "Meetings"),
    Action("teams_share", "Teams: share screen (Ctrl+Shift+E)", "Meetings"),
    Action("teams_hangup", "Teams: hang up (Ctrl+Shift+H)", "Meetings"),

    Action("desktop_next", "Next virtual desktop", "Windows", repeats=True),
    Action("desktop_prev", "Previous virtual desktop", "Windows", repeats=True),
    Action("task_view", "Task view", "Windows"),
    Action("switch_app", "Switch application (Alt+Tab)", "Windows", repeats=True),
    Action("show_desktop", "Show the desktop", "Windows"),
    Action("minimise_window", "Minimise the window", "Windows"),
    Action("maximise_window", "Maximise the window", "Windows"),
    Action("snap_left", "Snap the window left", "Windows"),
    Action("snap_right", "Snap the window right", "Windows"),
    Action("close_window", "Close the window (Alt+F4)", "Windows"),
    Action("search", "Windows search", "Windows"),
    Action("screenshot", "Screenshot selection", "Windows"),
    Action("emoji", "Emoji panel", "Windows"),
    Action("lock_pc", "Lock the computer", "Windows"),

    Action("nav_back", "Back", "Navigation", repeats=True),
    Action("nav_forward", "Forward", "Navigation", repeats=True),
    Action("tab_next", "Next tab", "Navigation", repeats=True),
    Action("tab_prev", "Previous tab", "Navigation", repeats=True),
    Action("new_tab", "New tab", "Navigation"),
    Action("close_tab", "Close tab", "Navigation"),
    Action("reopen_tab", "Reopen closed tab", "Navigation"),
    Action("refresh", "Refresh", "Navigation"),
    Action("page_up", "Page up", "Navigation", repeats=True),
    Action("page_down", "Page down", "Navigation", repeats=True),

    Action("copy", "Copy", "Editing"),
    Action("paste", "Paste", "Editing"),
    Action("cut", "Cut", "Editing"),
    Action("undo", "Undo", "Editing", repeats=True),
    Action("redo", "Redo", "Editing", repeats=True),
    Action("save", "Save", "Editing"),
    Action("find", "Find", "Editing"),
    Action("select_all", "Select all", "Editing"),
    Action("delete", "Delete", "Editing"),
    Action("enter", "Enter", "Editing"),
    Action("escape", "Escape", "Editing"),

    Action("zoom_in", "Zoom in", "Zoom and scroll", repeats=True),
    Action("zoom_out", "Zoom out", "Zoom and scroll", repeats=True),
    Action("zoom_reset", "Reset zoom", "Zoom and scroll"),
    Action("scroll_up", "Scroll up", "Zoom and scroll", repeats=True),
    Action("scroll_down", "Scroll down", "Zoom and scroll", repeats=True),
    Action("scroll_left", "Scroll left", "Zoom and scroll", repeats=True),
    Action("scroll_right", "Scroll right", "Zoom and scroll", repeats=True),
    Action("middle_click", "Middle click", "Zoom and scroll"),

    Action("keys", "Send a key combination...", "Custom", needs=NEEDS_KEYS, repeats=True),
    Action("launch", "Open a program or file...", "Custom", needs=NEEDS_PATH),
)

BY_ID: dict[str, Action] = {action.id: action for action in CATALOGUE}

GROUPS: tuple[str, ...] = tuple(dict.fromkeys(action.group for action in CATALOGUE))

# Actions the engine has to handle itself, because they depend on which button was
# pressed rather than on what the action is.
ENGINE_HANDLED = frozenset({"none", "passthrough"})

# ---------------------------------------------------------------------------
# Key combination text, e.g. "ctrl+shift+m"
# ---------------------------------------------------------------------------

MODIFIER_KEYS: dict[str, int] = {
    "ctrl": w.VK_CONTROL, "control": w.VK_CONTROL,
    "alt": w.VK_MENU,
    "shift": w.VK_SHIFT,
    "win": w.VK_LWIN, "windows": w.VK_LWIN, "super": w.VK_LWIN, "meta": w.VK_LWIN,
}

NAMED_KEYS: dict[str, int] = {
    "enter": w.VK_RETURN, "return": w.VK_RETURN,
    "tab": w.VK_TAB, "esc": w.VK_ESCAPE, "escape": w.VK_ESCAPE,
    "space": w.VK_SPACE, "spacebar": w.VK_SPACE,
    "backspace": w.VK_BACK, "back": w.VK_BACK,
    "delete": w.VK_DELETE, "del": w.VK_DELETE,
    "insert": w.VK_INSERT, "ins": w.VK_INSERT,
    "home": w.VK_HOME, "end": w.VK_END,
    "pageup": w.VK_PRIOR, "pgup": w.VK_PRIOR,
    "pagedown": w.VK_NEXT, "pgdn": w.VK_NEXT,
    "up": w.VK_UP, "down": w.VK_DOWN, "left": w.VK_LEFT, "right": w.VK_RIGHT,
    "printscreen": w.VK_SNAPSHOT, "prtsc": w.VK_SNAPSHOT,
    "pause": w.VK_PAUSE, "capslock": w.VK_CAPITAL, "menu": w.VK_APPS,
    "plus": w.VK_OEM_PLUS, "minus": w.VK_OEM_MINUS,
}
NAMED_KEYS.update({f"f{n}": w.VK_F1 + n - 1 for n in range(1, 25)})


def parse_combo(text: str) -> tuple[list[int], int] | None:
    """``"ctrl+shift+m"`` into the modifiers to hold and the key to strike.

    Returns None when the text does not describe a usable combination, so callers can
    tell the user rather than silently sending nothing.
    """
    parts = [part.strip().lower() for part in str(text).split("+") if part.strip()]
    if not parts:
        return None

    modifiers: list[int] = []
    for part in parts[:-1]:
        if part not in MODIFIER_KEYS:
            return None
        vk = MODIFIER_KEYS[part]
        if vk not in modifiers:
            modifiers.append(vk)

    key = parts[-1]
    if key in NAMED_KEYS:
        return modifiers, NAMED_KEYS[key]
    if key in MODIFIER_KEYS and len(parts) > 1:
        # "ctrl+alt" - a combination of modifiers only, which is legitimate.
        return modifiers, MODIFIER_KEYS[key]
    if len(key) == 1:
        # VkKeyScanW answers for the keyboard layout in use, so this stays correct on an
        # AZERTY layout where "a" and "q" are not where a US layout puts them.
        scan = w.user32.VkKeyScanW(key)
        if scan == -1:
            return None
        vk = scan & 0xFF
        if scan & 0x100 and w.VK_SHIFT not in modifiers:
            modifiers.append(w.VK_SHIFT)
        return modifiers, vk
    return None


def describe_combo(text: str) -> str:
    """The combination written back the way Windows writes it, for the settings window."""
    pretty = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
              "win": "Win", "windows": "Win", "super": "Win", "meta": "Win"}
    parts = [part.strip() for part in str(text).split("+") if part.strip()]
    return " + ".join(pretty.get(part.lower(), part.title() if len(part) > 1 else part.upper())
                      for part in parts)


# ---------------------------------------------------------------------------
# Running an action
# ---------------------------------------------------------------------------


def _tap(modifiers: list[int] | tuple[int, ...], key: int) -> None:
    events = [w.key_input(vk) for vk in modifiers]
    events.append(w.key_input(key))
    events.append(w.key_input(key, up=True))
    events.extend(w.key_input(vk, up=True) for vk in reversed(list(modifiers)))
    w.send_inputs(events)


def _wheel_with_control(notches: int) -> None:
    """Zoom the way a mouse zooms: Ctrl held while the wheel turns.

    Ctrl+plus works in browsers but not in much else; Ctrl+wheel works in browsers,
    Office, PDF readers, Explorer and most editors, which is the point.
    """
    w.send_inputs([
        w.key_input(w.VK_CONTROL),
        w.mouse_input(w.MOUSEEVENTF_WHEEL, data=notches * w.WHEEL_DELTA),
        w.key_input(w.VK_CONTROL, up=True),
    ])


def _launch(target: str) -> None:
    target = (target or "").strip()
    if not target:
        return
    file, params = target, None
    if not os.path.exists(target):
        # Only split when the whole string is not itself a path, so an unquoted
        # "C:\Program Files\..." still works.
        try:
            pieces = shlex.split(target, posix=False)
        except ValueError:
            pieces = [target]
        if len(pieces) > 1:
            file, params = pieces[0].strip('"'), " ".join(pieces[1:])
    result = w.shell32.ShellExecuteW(None, "open", file, params, None, 1)
    # ShellExecuteW returns a fake HINSTANCE; anything of 32 or less is an error code.
    if int(result or 0) <= 32:
        log.warning("Could not open %r (ShellExecute returned %s)", target, result)


def run(action_id: str, value: str = "") -> None:
    """Perform an action. Never raises: a bad binding must not take the app down."""
    try:
        if action_id in _COMBOS:
            keys = _COMBOS[action_id]
            _tap(keys[:-1], keys[-1])
        elif action_id == "zoom_in":
            _wheel_with_control(1)
        elif action_id == "zoom_out":
            _wheel_with_control(-1)
        elif action_id == "zoom_reset":
            _tap([w.VK_CONTROL], ord("0"))
        elif action_id == "scroll_up":
            w.send_inputs([w.mouse_input(w.MOUSEEVENTF_WHEEL, data=w.WHEEL_DELTA * 3)])
        elif action_id == "scroll_down":
            w.send_inputs([w.mouse_input(w.MOUSEEVENTF_WHEEL, data=-w.WHEEL_DELTA * 3)])
        elif action_id == "scroll_left":
            w.send_inputs([w.mouse_input(w.MOUSEEVENTF_HWHEEL, data=-w.WHEEL_DELTA)])
        elif action_id == "scroll_right":
            w.send_inputs([w.mouse_input(w.MOUSEEVENTF_HWHEEL, data=w.WHEEL_DELTA)])
        elif action_id == "middle_click":
            w.send_inputs([w.mouse_input(w.MOUSEEVENTF_MIDDLEDOWN),
                           w.mouse_input(w.MOUSEEVENTF_MIDDLEUP)])
        elif action_id == "keys":
            combo = parse_combo(value)
            if combo is None:
                log.warning("Cannot understand the key combination %r", value)
            else:
                _tap(*combo)
        elif action_id == "launch":
            _launch(value)
        elif action_id in ENGINE_HANDLED:
            pass
        else:
            log.warning("Unknown action %r", action_id)
    except Exception:
        log.exception("Action %r failed", action_id)
