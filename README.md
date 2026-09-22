# MX Master Tweaker

Personalise the buttons on a Logitech MX Master, on Windows, without Logitech Options+.

Hold the gesture button and roll the wheel to change the volume. Tilt the thumb wheel to
change the volume. Push the mouse left or right with the thumb button down to move
between virtual desktops. Bind any button to mute Teams, or to a key combination of your
own, or to a program.

It runs from the notification area, starts when you sign in, and is configured from a
settings window. It is about 2,500 lines of Python using nothing but the standard
library, so there is nothing to install beyond Python itself.

The gesture button - the wide one under your thumb - is reached the same way Logitech's
own software reaches it, by asking the mouse to report it over HID++. Everything else
goes through an ordinary Windows mouse hook.

**Nothing leaves this machine.** There is no account, no background service, no
telemetry and no network code of any kind - not even a disabled one. The app talks to
the Windows input APIs and to a JSON file in your own `%APPDATA%`, and to nothing else.

---

## Install

You need Python 3.10 or newer with tkinter, which is what the standard Windows installer
from [python.org](https://www.python.org/downloads/windows/) gives you. No administrator
rights are needed at any point.

```powershell
.\setup.ps1 -AutoStart
```

That checks Python, runs the self-test, puts a shortcut on your desktop and registers the
app to start when you sign in. Leave off `-AutoStart` if you would rather decide later -
there is a tick box for it in the settings window.

Then start it:

```powershell
.\run.ps1 -Settings
```

A **mouse icon appears in the notification area** (bottom-right of the taskbar). Click it
to open the settings, right-click it for a short menu.

On Windows 11 new tray icons are usually hidden: click the **^** arrow next to the clock
to see them, and drag the mouse icon onto the taskbar to keep it visible.

The app has no window of its own when it is running - it is only the tray icon and the
settings window. Running `.\run.ps1` again, or double-clicking the desktop shortcut,
brings the settings window back rather than starting a second copy.

## What you get out of the box

| Trigger | What it does |
| --- | --- |
| Thumb wheel, either direction | Volume down / volume up |
| Gesture button, quick press | Task view |
| Gesture button + scroll | Volume down / volume up |
| Gesture button + push left / right | Previous / next virtual desktop |
| Gesture button + push up / down | Maximise / minimise the window |

Everything else - the two small thumb buttons, the wheel click, left and right click - is
left completely alone until you bind something to it.

## How bindings work

Every button understands seven different things:

- **Press** - a quick press and release.
- **Hold + scroll up / down** - hold the button and roll the main wheel. This repeats for
  as long as you keep scrolling, which is what makes it the right home for the volume.
- **Hold + move up / down / left / right** - hold the button, move the whole mouse, and
  let go. The action happens when you let go.

Two rules are worth knowing:

- **A button with nothing bound to it is never intercepted.** It keeps doing exactly what
  Windows already made it do.
- **A button with anything bound to it is intercepted completely.** If you bind
  "hold + scroll" on the Back button and still want a quick press to go back, set
  **Press** to **Pass through**, which replays the original button.

If you start a gesture and change your mind, hold the button still for longer than the
press time (700 ms by default) and let go. Nothing happens.

### Actions available

Volume and media keys, Teams call controls, virtual desktops and window management,
browser navigation and tabs, copy/paste/undo, zoom, scrolling - plus two open-ended ones:

- **Send a key combination...** - press **Record...** and press the keys you want. This is
  how you reach anything the list does not already cover.
- **Open a program or file...** - anything you can double-click.

## The gesture button

The gesture button is the wide flat one your thumb rests on, and it takes more than a
mouse hook to reach.

The MX Master's report descriptor declares sixteen buttons. Windows' mouse driver and
its raw input API both stop at five, so the gesture button is never delivered to
anything - not to this app, not to any application, not even as an unknown button. It is
simply not there.

Logitech's own software does not read it as a mouse button either. It talks to the mouse
over **HID++**, a request/response protocol carried on a separate vendor-defined HID
collection that Windows does not claim exclusively, and asks the device to stop handling
that button itself and report it as a notification instead. This app does the same
thing: find the vendor collection, look up feature `0x1B04`, divert control `0x00C3`,
and listen.

All of that is local. It is a read and a write to one HID device node on this machine,
needs no elevation, installs nothing, and loads no driver.

Two consequences worth knowing:

- **The mouse forgets when it reconnects.** Diversion on this control is temporary by
  design, so the app re-applies it periodically and after every reconnection.
- **While diverted, the button does nothing on its own.** On Windows that costs nothing,
  because it did nothing to begin with. Turning the switch off in the settings, or
  quitting the app, hands it straight back.

The switch and a live status line are at the bottom of the gesture button's page. If
that line does not say *Working*, the bindings on that page will do nothing - and the
thumb wheel and the two small thumb buttons are unaffected either way.

## Finding out what your mouse sends

The **Detect** tab shows every button, wheel and raw HID report the machine receives.
Turn on **Deep listening** to include raw reports from every device, including the
vendor collections. It is the quickest way to work out why a binding is not firing.

## Starting with Windows

Either tick **Start when I sign in to Windows** in the settings window, or:

```powershell
.\install-startup.ps1              # register it
.\install-startup.ps1 -Remove      # undo
```

This registers a scheduled task that runs as you, in your own session, ten seconds after
sign-in, and restarts the app if it ever stops. A logon session is required: a mouse hook
can only see input on a real, signed-in desktop.

## Troubleshooting

**A binding does nothing.** Check the tray menu says *Bindings active*. For the gesture
button, check the status line on its page says *Working*. For anything else, open
**Detect** and press the button: if it does not appear there, Windows is not delivering
it to this app at all.

**The gesture button stopped working.** The mouse forgets diversion when it sleeps. The
app re-applies it within a few minutes and immediately on reconnection; the log records
every change of status.

**Everything stopped working after a while.** Windows removes a low-level hook that takes
too long to answer, and some remote-desktop and screen-sharing tools install hooks of
their own that interfere. Quit from the tray menu and start the app again.

**A keystroke action goes to the wrong window.** Actions are sent to whatever has focus at
the moment the button is released, which for a gesture is where the mouse ended up, not
where it started.

**The settings window will not come up.** Run `.\run.ps1 -Console -Verbose` to start it in
the window you are in, with the log on screen; any error will be visible there rather
than swallowed. `pythonw.exe` has no console, so a failure to start is otherwise silent.

**The log** is at `%LOCALAPPDATA%\mx-master-tweaker\logs\app.log`. Run
`.\run.ps1 -Console -Verbose` to watch it live, which also records every binding as it
fires.

**The settings file** is at `%APPDATA%\mx-master-tweaker\settings.json`. Delete it to
start again from the defaults; the app writes a fresh one.

## Uninstall

```powershell
.\install-startup.ps1 -Remove
Remove-Item "$env:APPDATA\mx-master-tweaker" -Recurse
Remove-Item "$env:LOCALAPPDATA\mx-master-tweaker" -Recurse
```

Then delete the desktop shortcut and this folder. Nothing is written anywhere else, and
no registry keys, services or drivers are ever created.

## How it is put together

| File | What it does |
| --- | --- |
| `app/winapi.py` | The Win32 calls, bound by hand with ctypes |
| `app/host.py` | The hidden window, the mouse hook and the tray icon |
| `app/hidpp.py` | Talking to the mouse itself, to reach the gesture button |
| `app/engine.py` | Turning presses, gestures and scrolls into actions |
| `app/actions.py` | Everything a binding can do |
| `app/config.py` | The settings file and the binding model |
| `app/ui.py` | The settings window |
| `app/main.py` | Wiring, threading and startup |

Three threads: Tk owns the main thread because it is not thread safe; the hook needs a
thread that never blocks and does nothing but pump messages; the actions run on a third,
so that nothing slow ever happens inside the hook. A fourth appears when the gesture
button is in use, sitting on a blocking read from the mouse. They only talk through
queues and posted window messages.

Run the tests with:

```powershell
python -m unittest discover -s tests
```

## Scope

Written for a Logitech MX Master 3. The wheel click, the two side buttons and the thumb
wheel work on any mouse that has them, because they go through the ordinary Windows
mouse hook. The gesture button is the Logitech-specific part: it needs a device that
speaks HID++ and is willing to divert control `0x00C3`, which covers the MX Master
family and most recent Logitech mice, over Bluetooth or a Unifying/Bolt receiver.

Bindings are global - there are no per-application profiles.
