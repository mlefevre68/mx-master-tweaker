"""The settings window.

Tkinter, because it is in the standard library and therefore already on the machine.
The layout is deliberately master/detail - pick a button on the left, see everything it
can do on the right - rather than one long scrolling list of thirty dropdowns.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import actions, config as cfg, winapi as w
from .engine import Observation

log = logging.getLogger(__name__)

LEAVE_ALONE = "(leave this alone)"

HELP_TEXT = """\
How the buttons work

Press means a quick press and release. Hold + move means holding the button down, \
moving the whole mouse in a direction, and letting go - the action happens when you \
let go. Hold + scroll means holding the button down and rolling the main wheel, and \
it repeats for as long as you keep scrolling, which is what makes it good for volume. \
Hold + drag is different again: instead of judging one movement, it grabs whatever \
window is under the cursor and carries it with the mouse for as long as the button \
stays down - useful for moving or resizing a window without hunting for its title bar.

A button bound to Hold + drag has no directions left: by the time you let go, the \
movement has already been used to carry the window, so there is nothing left to judge \
a direction from. Press and Hold + scroll still work normally on the same button.

A button with nothing bound to it is never touched, and keeps doing whatever Windows \
already made it do. As soon as you bind anything to a button, the whole button belongs \
to this app - so if you still want a quick press to do its original job, set Press to \
"Pass through".

"Pass through" is offered for the Back and Forward buttons and the wheel click, because \
Windows has an event of its own for each of those to hand back. It is not offered for \
the gesture button, which Windows never receives at all - see below.

Holding a button still for longer than the press time and then releasing does nothing. \
That is the way out of a gesture you did not mean to start.

About the gesture button

The gesture button is the wide flat one under your thumb. Windows has no standard slot \
for a sixth mouse button, so it is never delivered as one - not to this app, and not to \
anything else. Instead the mouse is asked, over Logitech's own protocol, to stop \
handling that button itself and report it separately. That is what the switch at the \
bottom of its page does.

The status line under that switch says whether it is actually working. If it says \
anything other than Working, the bindings on this page will do nothing, and the thumb \
wheel and the two small thumb buttons are the ones to use instead.

The mouse forgets this arrangement whenever it sleeps and reconnects, so the app quietly \
re-applies it. Turning the switch off, or quitting the app, hands the button straight \
back.

Where things are kept

Settings:  {config}
Log:       {log}

Nothing is sent anywhere. There is no account, no service and no network access of any \
kind; the app only ever talks to Windows on this machine.
"""


def record_combo(parent: tk.Misc, initial: str = "") -> str | None:
    """Ask the user to press a key combination, and give it back as text.

    Modifier state is tracked by watching keys go down and up rather than by reading
    the event's state mask, which is reported inconsistently for Alt and the Windows
    key across Tk builds.
    """
    names = {
        "Control_L": "ctrl", "Control_R": "ctrl",
        "Shift_L": "shift", "Shift_R": "shift",
        "Alt_L": "alt", "Alt_R": "alt",
        "Win_L": "win", "Win_R": "win", "Super_L": "win", "Super_R": "win",
        "Return": "enter", "Escape": "esc", "BackSpace": "backspace", "Tab": "tab",
        "space": "space", "Delete": "delete", "Insert": "insert",
        "Prior": "pageup", "Next": "pagedown", "Home": "home", "End": "end",
        "Left": "left", "Right": "right", "Up": "up", "Down": "down",
        "Print": "printscreen",
    }
    names.update({f"F{n}": f"f{n}" for n in range(1, 25)})
    modifier_names = {"ctrl", "shift", "alt", "win"}

    dialog = tk.Toplevel(parent)
    dialog.title("Press a key combination")
    dialog.resizable(False, False)
    dialog.transient(parent)
    result: dict[str, str | None] = {"value": None}
    held: list[str] = []

    ttk.Label(dialog, text="Press the combination you want, for example Ctrl + Shift + M.",
              padding=(16, 14, 16, 4)).pack()
    shown = tk.StringVar(value=actions.describe_combo(initial) if initial else "...")
    ttk.Label(dialog, textvariable=shown, font=("Segoe UI", 14, "bold"),
              padding=(16, 4, 16, 10)).pack()
    buttons = ttk.Frame(dialog, padding=(16, 0, 16, 14))
    buttons.pack(fill="x")

    def finish(value: str | None) -> None:
        result["value"] = value
        dialog.destroy()

    ttk.Button(buttons, text="Cancel", command=lambda: finish(None)).pack(side="right")

    def on_press(event: tk.Event) -> str:
        name = names.get(event.keysym)
        if name is None:
            name = event.keysym.lower() if len(event.keysym) == 1 else None
        if name is None:
            return "break"
        if name in modifier_names:
            if name not in held:
                held.append(name)
            shown.set(actions.describe_combo("+".join(held + ["..."])))
            return "break"
        combo = "+".join(held + [name])
        shown.set(actions.describe_combo(combo))
        dialog.after(180, lambda: finish(combo))
        return "break"

    def on_release(event: tk.Event) -> str:
        name = names.get(event.keysym)
        if name in held:
            held.remove(name)
        return "break"

    dialog.bind("<KeyPress>", on_press)
    dialog.bind("<KeyRelease>", on_release)
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    dialog.update_idletasks()
    dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 80, parent.winfo_rooty() + 90))
    dialog.grab_set()
    dialog.focus_force()
    parent.wait_window(dialog)
    return result["value"]


class SlotRow:
    """One trigger and the action bound to it."""

    def __init__(self, parent: ttk.Frame, row: int, source: str, slot: cfg.Slot,
                 window: "SettingsWindow") -> None:
        self.source = source
        self.slot = slot
        self.window = window

        ttk.Label(parent, text=slot.label, width=18).grid(
            row=row, column=0, sticky="w", pady=3)

        self.choices = window.choices_for(source, slot.id)
        self.action = tk.StringVar(value=LEAVE_ALONE)
        self.combo = ttk.Combobox(parent, textvariable=self.action, state="readonly",
                                  values=list(self.choices), width=38)
        self.combo.grid(row=row, column=1, sticky="ew", pady=3, padx=(0, 6))
        self.combo.bind("<<ComboboxSelected>>", self._on_action_change)

        self.value = tk.StringVar()
        self.entry = ttk.Entry(parent, textvariable=self.value, width=22)
        self.entry.grid(row=row, column=2, sticky="ew", pady=3, padx=(0, 6))
        self.value.trace_add("write", lambda *_: window.touch())

        self.extra = ttk.Button(parent, text="...", width=9, command=self._on_extra)
        self.extra.grid(row=row, column=3, sticky="w", pady=3)

        ttk.Label(parent, text=slot.hint, foreground="#666").grid(
            row=row, column=4, sticky="w", padx=(10, 0))

    # -- state -------------------------------------------------------------

    def load(self, binding: dict | None) -> None:
        if binding is None:
            self.action.set(LEAVE_ALONE)
            self.value.set("")
        else:
            self.action.set(self.window.label_for(binding["action"]))
            self.value.set(binding.get("value", ""))
        self._sync_value_widgets()

    def dump(self) -> dict | None:
        action_id = self.choices.get(self.action.get())
        if action_id is None:
            return None
        entry = {"action": action_id}
        if actions.BY_ID[action_id].needs:
            entry["value"] = self.value.get().strip()
        return entry

    # -- reacting ----------------------------------------------------------

    def _needs(self) -> str | None:
        action_id = self.choices.get(self.action.get())
        return actions.BY_ID[action_id].needs if action_id else None

    def _sync_value_widgets(self) -> None:
        needs = self._needs()
        self.entry.configure(state="normal" if needs else "disabled")
        if needs == actions.NEEDS_KEYS:
            self.extra.configure(state="normal", text="Record...")
        elif needs == actions.NEEDS_PATH:
            self.extra.configure(state="normal", text="Browse...")
        else:
            self.extra.configure(state="disabled", text="...")
            self.value.set("")

    def _on_action_change(self, _event=None) -> None:
        self._sync_value_widgets()
        self.window.touch()
        self.window.warn_about_conflicts()

    def _on_extra(self) -> None:
        needs = self._needs()
        if needs == actions.NEEDS_KEYS:
            combo = record_combo(self.window.window, self.value.get())
            if combo:
                self.value.set(combo)
        elif needs == actions.NEEDS_PATH:
            chosen = filedialog.askopenfilename(
                parent=self.window.window, title="Choose a program or file",
                filetypes=[("Programs", "*.exe;*.lnk;*.bat;*.cmd"), ("All files", "*.*")])
            if chosen:
                self.value.set(chosen)


class SettingsWindow:
    """The whole settings window. Created once and then shown and hidden."""

    def __init__(self, app) -> None:
        self.app = app
        self.dirty = False
        self.rows: dict[tuple[str, str], SlotRow] = {}
        self.panels: dict[str, ttk.Frame] = {}
        self.conflict_notes: dict[str, ttk.Label] = {}
        self.events: queue.Queue = queue.Queue(maxsize=400)
        self._observing = False
        self._build()
        self.load()

    # -- action lists ------------------------------------------------------

    def choices_for(self, source: str, slot_id: str) -> dict[str, str]:
        """Display text to action id, in catalogue order, for one trigger.

        What is offered depends on the button as well as the trigger: passing a button
        "through" is only meaningful where Windows has an event of its own to replay,
        and a drag is a mode that only the drag trigger can run.
        """
        items = {LEAVE_ALONE: None}
        for action in actions.CATALOGUE:
            if slot_id == "drag":
                # Anything that happens once is meaningless here: this trigger fires
                # continuously for as long as the button is held.
                if not action.drag:
                    continue
            elif action.drag:
                continue
            elif action.id == "passthrough":
                # Passing a gesture or a scroll through has no meaning - there is no
                # original event to replay - and nor does it for a button Windows
                # never receives in the first place.
                if slot_id != "tap" or not cfg.passes_through(source):
                    continue
            items[f"{action.group}:  {action.label}"] = action.id
        return items

    def label_for(self, action_id: str) -> str:
        action = actions.BY_ID.get(action_id)
        if action is None:
            return LEAVE_ALONE
        return f"{action.group}:  {action.label}"

    # -- building ----------------------------------------------------------

    def _build(self) -> None:
        self.window = tk.Toplevel(self.app.root)
        self.window.title("MX Master Tweaker")
        self.window.geometry("1000x620")
        self.window.minsize(880, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)

        style = ttk.Style(self.window)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        self._build_header()
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self._build_buttons_tab(notebook)
        self._build_wheel_tab(notebook)
        self._build_detect_tab(notebook)
        self._build_help_tab(notebook)
        self._build_footer()

    def _build_header(self) -> None:
        header = ttk.Frame(self.window, padding=(14, 12, 14, 8))
        header.pack(fill="x")

        self.enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(header, text="Bindings active", variable=self.enabled,
                        command=self._on_enabled).pack(side="left")

        self.at_startup = tk.BooleanVar(value=False)
        ttk.Checkbutton(header, text="Start when I sign in to Windows",
                        variable=self.at_startup,
                        command=self._on_startup).pack(side="left", padx=(20, 0))

        self.status = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.status, foreground="#0a7").pack(side="right")

    def _build_buttons_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="  Buttons  ")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        chooser = ttk.Frame(tab)
        chooser.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        ttk.Label(chooser, text="Button", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.button_list = tk.Listbox(chooser, width=24, height=8, exportselection=False,
                                      activestyle="none")
        self.button_list.pack(fill="y", expand=True, pady=(4, 0))
        self.button_list.bind("<<ListboxSelect>>", self._on_button_selected)

        self.detail = ttk.Frame(tab)
        self.detail.grid(row=0, column=1, sticky="nsew")

        for button in cfg.BUTTONS:
            self.panels[button.id] = self._build_panel(
                self.detail, button.id, button.label, button.hint, cfg.SLOTS)
        self._build_gesture_extras(self.panels["gesture"], len(cfg.SLOTS) + 2)
        self._refresh_button_list()
        self.button_list.selection_set(0)
        self._on_button_selected()

    def _build_gesture_extras(self, panel: ttk.Frame, row: int) -> None:
        """The gesture button needs a switch and an honest status of its own.

        It is the one button whose bindings can fail to work for reasons that have
        nothing to do with what you bound to it, so the window says plainly whether it
        is reaching the mouse.
        """
        ttk.Separator(panel, orient="horizontal").grid(
            row=row, column=0, columnspan=5, sticky="ew", pady=(16, 10))

        self.use_gesture = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            panel, variable=self.use_gesture, command=self.touch,
            text="Reach the gesture button through Logitech's own protocol"
        ).grid(row=row + 1, column=0, columnspan=5, sticky="w")

        ttk.Label(panel, foreground="#666", wraplength=620, justify="left", text=(
            "Windows has no slot for a sixth mouse button, so the mouse has to be asked "
            "to report this one. Turn it off to leave the mouse's own configuration "
            "completely alone; the other buttons are unaffected either way.\n\n"
            "This is also why there is no \"Pass through\" for this button: Windows "
            "never receives it on its own, so there is no normal behaviour to give "
            "back. Leave a trigger unset to ignore it.")
        ).grid(row=row + 2, column=0, columnspan=5, sticky="w", pady=(2, 8))

        self.gesture_status = tk.StringVar(value="")
        self.gesture_label = ttk.Label(panel, textvariable=self.gesture_status,
                                       font=("Segoe UI", 9, "bold"))
        self.gesture_label.grid(row=row + 3, column=0, columnspan=5, sticky="w")

    def _build_panel(self, parent: tk.Misc, source: str, title: str, hint: str,
                     slots: tuple[cfg.Slot, ...]) -> ttk.Frame:
        panel = ttk.Frame(parent)
        panel.columnconfigure(4, weight=1)
        ttk.Label(panel, text=title, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(panel, text=hint, foreground="#666").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(0, 10))
        for index, slot in enumerate(slots):
            self.rows[(source, slot.id)] = SlotRow(panel, index + 2, source, slot, self)
        if source in cfg.BUTTON_BY_ID:
            note = ttk.Label(panel, foreground="#b60", wraplength=620, justify="left")
            note.grid(row=len(slots) + 2, column=0, columnspan=5,
                      sticky="w", pady=(8, 0))
            self.conflict_notes[source] = note
        return panel

    def warn_about_conflicts(self) -> None:
        """Say so when a drag has taken over the movement the directions need.

        A drag consumes the mouse as it moves, so by the time the button is released
        there is no single direction left to judge. Binding both looks reasonable and
        leaves the directions dead, which is exactly the kind of silent nothing this
        app should not be handing out.
        """
        for source, note in self.conflict_notes.items():
            row = self.rows.get((source, "drag"))
            dragging = row is not None and row.dump() is not None
            directions = [cfg.SLOT_BY_ID[slot].label for slot in cfg.DIRECTION_SLOTS
                          if self.rows[(source, slot)].dump() is not None]
            if dragging and directions:
                note.configure(text=(
                    "While this button is dragging, it is using the mouse movement as "
                    "it happens, so there is no direction left to judge when you let "
                    "go: " + ", ".join(directions) + " will not fire. Press and "
                    "hold + scroll still work."))
            else:
                note.configure(text="")

    def _build_wheel_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="  Thumb wheel  ")

        panel = self._build_panel(tab, cfg.WHEEL_ID, "Thumb wheel",
                                  "The small wheel beside the thumb buttons",
                                  cfg.WHEEL_SLOTS)
        panel.pack(fill="x", anchor="w")

        self.invert = tk.BooleanVar(value=False)
        ttk.Checkbutton(tab, text="Swap the two directions",
                        variable=self.invert,
                        command=self.touch).pack(anchor="w", pady=(14, 0))

        tuning = ttk.LabelFrame(tab, text="Feel", padding=12)
        tuning.pack(fill="x", pady=(18, 0))
        self.move_threshold = tk.IntVar(value=30)
        self.tap_milliseconds = tk.IntVar(value=700)
        self.wheel_notch = tk.IntVar(value=120)
        for row, (label, variable, low, high, hint) in enumerate((
                ("Movement needed for a direction", self.move_threshold, 5, 300,
                 "pixels - lower reacts sooner, higher is harder to trigger by accident"),
                ("Longest press", self.tap_milliseconds, 150, 3000,
                 "milliseconds - hold longer than this and nothing happens"),
                ("Scroll step", self.wheel_notch, 20, 480,
                 "raise this if one notch does too much"))):
            ttk.Label(tuning, text=label, width=32).grid(row=row, column=0, sticky="w", pady=3)
            spin = ttk.Spinbox(tuning, from_=low, to=high, textvariable=variable, width=8,
                               command=self.touch)
            spin.grid(row=row, column=1, sticky="w", pady=3)
            spin.bind("<KeyRelease>", lambda *_: self.touch())
            ttk.Label(tuning, text=hint, foreground="#666").grid(
                row=row, column=2, sticky="w", padx=(12, 0))

    def _build_detect_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="  Detect  ")

        ttk.Label(tab, text="Press a button on the mouse and see what Windows receives.",
                  font=("Segoe UI", 10)).pack(anchor="w")
        ttk.Label(tab, foreground="#666", text=(
            "Buttons and wheels appear here as soon as the window is open. Turn on deep "
            "listening as well to see raw reports from every device, which is how you "
            "find out whether the gesture button sends anything at all.")
        ).pack(anchor="w", pady=(2, 10))

        controls = ttk.Frame(tab)
        controls.pack(fill="x")
        self.deep_listen = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Deep listening (all devices, raw HID)",
                        variable=self.deep_listen,
                        command=self._on_deep_listen).pack(side="left")
        ttk.Button(controls, text="List devices",
                   command=self._list_devices).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Clear", command=self._clear_log).pack(side="left", padx=(8, 0))

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(frame, height=16, wrap="none", font=("Consolas", 9),
                           state="disabled", background="#1e1e1e", foreground="#d4d4d4")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_help_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="  How it works  ")
        text = tk.Text(tab, wrap="word", relief="flat", font=("Segoe UI", 10),
                       padx=10, pady=10)
        text.insert("1.0", HELP_TEXT.format(config=cfg.config_path(), log=cfg.logs_dir()))
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

    def _build_footer(self) -> None:
        footer = ttk.Frame(self.window, padding=(14, 4, 14, 12))
        footer.pack(fill="x")
        ttk.Button(footer, text="Restore defaults",
                   command=self._restore_defaults).pack(side="left")
        ttk.Button(footer, text="Close", command=self.hide).pack(side="right")
        self.apply_button = ttk.Button(footer, text="Apply", command=self.apply)
        self.apply_button.pack(side="right", padx=(0, 8))
        self.apply_button.state(["disabled"])

    # -- loading and saving ------------------------------------------------

    def load(self) -> None:
        config = self.app.config
        self.enabled.set(config.enabled)
        self.invert.set(bool(config.setting("invert_thumbwheel", False)))
        self.use_gesture.set(bool(config.setting("use_gesture_button", True)))
        self.move_threshold.set(int(config.setting("move_threshold", 30)))
        self.tap_milliseconds.set(int(config.setting("tap_milliseconds", 700)))
        self.wheel_notch.set(int(config.setting("wheel_notch", 120)))
        for (source, slot), row in self.rows.items():
            row.load(config.binding(source, slot))
        self.at_startup.set(self.app.starts_at_logon())
        self._refresh_button_list()
        self.warn_about_conflicts()
        self._set_dirty(False)

    def apply(self) -> None:
        bindings: dict[str, dict] = {}
        for (source, slot), row in self.rows.items():
            entry = row.dump()
            if entry is None:
                continue
            if actions.BY_ID[entry["action"]].needs and not entry.get("value"):
                where = cfg.BUTTON_BY_ID[source].label if source in cfg.BUTTON_BY_ID \
                    else "Thumb wheel"
                slot_label = cfg.SLOT_BY_ID[slot].label if slot in cfg.SLOT_BY_ID else slot
                messagebox.showwarning(
                    "Not finished",
                    f"{where} - {slot_label}: choose what to send before applying.",
                    parent=self.window)
                return
            bindings.setdefault(source, {})[slot] = entry

        self.app.config.bindings = bindings
        self.app.config.enabled = bool(self.enabled.get())
        self.app.config.settings.update({
            "invert_thumbwheel": bool(self.invert.get()),
            "use_gesture_button": bool(self.use_gesture.get()),
            "move_threshold": int(self.move_threshold.get()),
            "tap_milliseconds": int(self.tap_milliseconds.get()),
            "wheel_notch": int(self.wheel_notch.get()),
        })
        self.app.save()
        self._refresh_button_list()
        self._set_dirty(False)
        self._flash("Saved")

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno("Restore defaults",
                                   "Replace every binding with the original set?",
                                   parent=self.window):
            return
        fresh = cfg.default_config()
        self.app.config.bindings = fresh.bindings
        self.app.config.settings = fresh.settings
        self.app.config.enabled = fresh.enabled
        self.app.save()
        self.load()
        self._flash("Defaults restored")

    # -- small reactions ---------------------------------------------------

    def touch(self) -> None:
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.apply_button.state(["!disabled"] if dirty else ["disabled"])
        if dirty:
            self.status.set("Unsaved changes")

    def _flash(self, message: str) -> None:
        self.status.set(message)
        self.window.after(2500, lambda: self.status.set("") if not self.dirty else None)

    def _on_enabled(self) -> None:
        # The master switch takes effect at once; waiting for Apply would be surprising
        # when what you want is to turn the thing off right now.
        self.app.set_enabled(bool(self.enabled.get()))
        self._flash("Bindings on" if self.enabled.get() else "Bindings off")

    def _on_startup(self) -> None:
        wanted = bool(self.at_startup.get())
        if self.app.set_startup(wanted):
            self._flash("Will start at sign-in" if wanted else "Will not start at sign-in")
        else:
            self.at_startup.set(self.app.starts_at_logon())
            messagebox.showerror(
                "Could not change startup",
                "Windows would not let the sign-in task be changed. "
                f"The log has the details:\n\n{cfg.logs_dir()}", parent=self.window)

    def _on_button_selected(self, _event=None) -> None:
        selection = self.button_list.curselection()
        index = selection[0] if selection else 0
        for panel in self.panels.values():
            panel.pack_forget()
        chosen = cfg.BUTTONS[index]
        self.panels[chosen.id].pack(in_=self.detail, fill="both", expand=True)

    def _refresh_button_list(self) -> None:
        selection = self.button_list.curselection()
        self.button_list.delete(0, "end")
        for button in cfg.BUTTONS:
            bound = sum(1 for slot in cfg.SLOTS
                        if self.rows[(button.id, slot.id)].dump() is not None)
            suffix = f"   ({bound})" if bound else ""
            self.button_list.insert("end", f"{button.label}{suffix}")
        if selection:
            self.button_list.selection_set(selection[0])

    # -- the detector ------------------------------------------------------

    def _on_deep_listen(self) -> None:
        self.app.host.set_detecting(bool(self.deep_listen.get()))
        self._append("--- deep listening " +
                     ("on" if self.deep_listen.get() else "off") + " ---")

    def _list_devices(self) -> None:
        self._append("--- input devices ---")
        for device in w.input_devices():
            page = device.get("usage_page")
            usage = device.get("usage")
            vendor = device.get("vendor")
            where = f"page 0x{page:04X} usage 0x{usage:02X}" if page else "?"
            who = f" vid 0x{vendor:04X} pid 0x{device['product']:04X}" if vendor else ""
            buttons = f" buttons {device['buttons']}" if device.get("buttons") else ""
            self._append(f"{device['type']:<9}{where}{who}{buttons}")
            self._append(f"          {device['name']}")

    def observe(self, event) -> None:
        """Called from the hook thread. Must not touch Tk from here."""
        try:
            self.events.put_nowait(event)
        except queue.Full:
            pass

    def observe_raw(self, described: tuple[str, str]) -> None:
        kind, detail = described
        self.observe(Observation(kind, detail))

    def _drain(self) -> None:
        drained = 0
        while drained < 60:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._append(f"{event.kind:<9} {event.detail}")
            drained += 1
        # Stop the timer when the window is closed: an idle poll that never ends is a
        # small cost, but it is a cost paid forever.
        if self._observing and self.window.winfo_exists():
            self.window.after(120, self._drain)

    def _append(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        # Keep the panel from growing without limit over a long session.
        if int(self.log.index("end-1c").split(".")[0]) > 600:
            self.log.delete("1.0", "200.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- the gesture button's status --------------------------------------

    def _poll_gesture(self) -> None:
        status = self.app.gesture_status()
        wording = {
            "active": ("Working - the gesture button is reaching this app", "#0a7"),
            "searching": ("Looking for the mouse", "#b60"),
            "failed": ("Not available", "#c00"),
            "off": ("Turned off", "#666"),
        }.get(status.state, ("Unknown", "#666"))
        self.gesture_status.set(f"{wording[0]}  ({status.detail})")
        self.gesture_label.configure(foreground=wording[1])
        if self._observing and self.window.winfo_exists():
            self.window.after(1000, self._poll_gesture)

    # -- showing and hiding ------------------------------------------------

    def show(self) -> None:
        self.load()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        if not self._observing:
            self.app.engine.add_observer(self.observe)
            self._observing = True
            self.window.after(120, self._drain)
            self.window.after(50, self._poll_gesture)

    def hide(self) -> None:
        if self.dirty and messagebox.askyesno(
                "Unsaved changes", "Apply your changes before closing?",
                parent=self.window):
            self.apply()
        if self._observing:
            self.app.engine.remove_observer(self.observe)
            self._observing = False
        if self.deep_listen.get():
            self.deep_listen.set(False)
            self.app.host.set_detecting(False)
        self.window.withdraw()
