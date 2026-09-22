"""Where the bindings live, and what a binding looks like.

The file is plain JSON under %APPDATA% so it can be read, edited, backed up or deleted
by hand. Nothing here reaches the network, and nothing is stored anywhere else.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "mx-master-tweaker"
CONFIG_VERSION = 1


def config_dir() -> Path:
    root = Path(os.environ.get("APPDATA") or Path.home())
    return root / APP_NAME


def config_path() -> Path:
    return config_dir() / "settings.json"


def logs_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    folder = root / APP_NAME / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------------------
# What can be bound
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Button:
    id: str
    label: str
    hint: str


# Only buttons that are safe to take over. Left and right click are deliberately absent:
# a mistake there leaves you with a mouse you cannot use to fix the mistake.
BUTTONS: tuple[Button, ...] = (
    Button("gesture", "Gesture button",
           "The big flat button under your thumb, where your thumb rests"),
    Button("x1", "Back button", "The lower of the two small thumb buttons"),
    Button("x2", "Forward button", "The upper of the two small thumb buttons"),
    Button("middle", "Wheel click", "Pressing the main scroll wheel down"),
)

BUTTON_BY_ID: dict[str, Button] = {button.id: button for button in BUTTONS}


@dataclass(frozen=True)
class Slot:
    id: str
    label: str
    hint: str


# The ways a button can be used. This is the Logitech model: a quick press is one thing,
# holding the button and then moving or scrolling is another.
SLOTS: tuple[Slot, ...] = (
    Slot("tap", "Press", "A quick press and release"),
    Slot("wheel_up", "Hold + scroll up", "Hold the button and roll the wheel forward"),
    Slot("wheel_down", "Hold + scroll down", "Hold the button and roll the wheel back"),
    Slot("up", "Hold + move up", "Hold the button and push the mouse away from you"),
    Slot("down", "Hold + move down", "Hold the button and pull the mouse towards you"),
    Slot("left", "Hold + move left", "Hold the button and move the mouse left"),
    Slot("right", "Hold + move right", "Hold the button and move the mouse right"),
)

SLOT_BY_ID: dict[str, Slot] = {slot.id: slot for slot in SLOTS}

# The thumb wheel is not a button, so it gets its own two directions.
WHEEL_ID = "thumbwheel"
WHEEL_SLOTS: tuple[Slot, ...] = (
    Slot("left", "Tilt / roll left", "Rolling the thumb wheel towards you"),
    Slot("right", "Tilt / roll right", "Rolling the thumb wheel away from you"),
)


def defaults() -> dict:
    """A configuration that is useful the moment it is installed.

    The thumb wheel is bound to the volume because that works on every MX Master over
    every connection, with no driver and no diversion. The gesture button bindings are
    there too; they simply never fire if this machine cannot see that button, which
    costs nothing and means they are already right when it can.
    """
    return {
        "version": CONFIG_VERSION,
        "enabled": True,
        "settings": {
            # How far the mouse has to travel, in pixels, before a held button counts as
            # a direction rather than a press.
            "move_threshold": 30,
            # A hold longer than this is never treated as a press, even without movement.
            "tap_milliseconds": 700,
            # Some wheels report several small steps per physical notch. Raising this
            # makes one notch do less.
            "wheel_notch": 120,
            "invert_thumbwheel": False,
            "show_notifications": True,
            # The gesture button is only reachable by asking the mouse itself to report
            # it, over Logitech's own protocol. Turn this off to leave the mouse's
            # configuration completely untouched.
            "use_gesture_button": True,
        },
        "bindings": {
            "thumbwheel": {
                "left": {"action": "volume_down"},
                "right": {"action": "volume_up"},
            },
            "gesture": {
                "tap": {"action": "task_view"},
                "wheel_up": {"action": "volume_up"},
                "wheel_down": {"action": "volume_down"},
                "up": {"action": "maximise_window"},
                "down": {"action": "minimise_window"},
                "left": {"action": "desktop_prev"},
                "right": {"action": "desktop_next"},
            },
        },
    }


@dataclass
class Config:
    enabled: bool = True
    settings: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)

    def binding(self, source: str, slot: str) -> dict | None:
        """The binding for one trigger, or None when that trigger is left alone."""
        entry = self.bindings.get(source, {}).get(slot)
        if not isinstance(entry, dict) or not entry.get("action"):
            return None
        return entry

    def is_bound(self, source: str) -> bool:
        """Whether anything at all is bound to a button.

        This decides whether the button is intercepted. A button nobody has configured
        is never touched, so it keeps working exactly as Windows intends.
        """
        return any(self.binding(source, slot.id) for slot in SLOTS)

    def setting(self, name: str, fallback):
        value = self.settings.get(name, fallback)
        return fallback if value is None else value

    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "enabled": self.enabled,
            "settings": self.settings,
            "bindings": self.bindings,
        }


def _clean(raw: dict) -> dict:
    """Drop anything unrecognised, so a hand-edited file cannot break the engine."""
    known_sources = {button.id for button in BUTTONS} | {WHEEL_ID}
    known_slots = {slot.id for slot in SLOTS} | {slot.id for slot in WHEEL_SLOTS}

    bindings: dict[str, dict] = {}
    for source, slots in (raw.get("bindings") or {}).items():
        if source not in known_sources or not isinstance(slots, dict):
            continue
        kept = {}
        for slot, entry in slots.items():
            if slot not in known_slots:
                continue
            if isinstance(entry, str):  # tolerate the shorthand {"tap": "copy"}
                entry = {"action": entry}
            if isinstance(entry, dict) and entry.get("action"):
                kept[slot] = {"action": str(entry["action"]),
                              "value": str(entry.get("value", ""))}
        if kept:
            bindings[source] = kept

    base = defaults()["settings"]
    settings = {**base, **{k: v for k, v in (raw.get("settings") or {}).items() if k in base}}
    return {"enabled": bool(raw.get("enabled", True)),
            "settings": settings, "bindings": bindings}


def default_config() -> Config:
    """A fresh Config holding the shipped defaults, ready to be saved or applied."""
    return Config(**_clean(defaults()))


def load() -> Config:
    path = config_path()
    if not path.exists():
        config = default_config()
        save(config)
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # A corrupt file must not stop the mouse from working. Keep the broken copy
        # for inspection and carry on with the defaults.
        log.warning("Could not read %s (%s); falling back to the defaults", path, error)
        try:
            path.replace(path.with_suffix(".broken.json"))
        except OSError:
            pass
        return default_config()
    return Config(**_clean(raw if isinstance(raw, dict) else {}))


def save(config: Config) -> None:
    """Write the settings out without ever leaving a half-written file behind."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body + "\n")
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
