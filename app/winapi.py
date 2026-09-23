"""The Win32 calls this app needs, bound by hand with ctypes.

Everything here is standard library only. That is deliberate: the app has to run on a
locked-down work laptop where installing packages is awkward, so the price of writing
these bindings out is worth paying once.

Two things in here are easy to get wrong and expensive to debug, so they are called out:

* On 64-bit Windows every function that returns a handle or an ``LRESULT`` must have its
  ``restype`` set. Without it ctypes assumes ``int``, silently truncates the top 32 bits,
  and you get a window handle that looks plausible and refers to nothing.
* Callbacks handed to Windows must be kept alive by Python for as long as Windows holds
  them. A ``WINFUNCTYPE`` object that goes out of scope is garbage collected, and the
  next mouse click lands in freed memory.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------------------------------------------------------------------------
# Types that ctypes.wintypes does not define
# ---------------------------------------------------------------------------

LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM  # UINT_PTR: 4 bytes on x86, 8 on x64

# Marks input that this app synthesised, so our own hook can recognise it and let it
# straight through. Without it, remapping a button to a keystroke that is itself bound
# to something else loops until Windows drops the hook.
INJECTED_SIGNATURE = 0x4D584D33  # 'MXM3'

# ---------------------------------------------------------------------------
# Messages and constants
# ---------------------------------------------------------------------------

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_COMMAND = 0x0111
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
WM_USER = 0x0400
WM_APP = 0x8000

# Our own messages, sent to the hidden window that owns the hooks.
WM_TRAY_CALLBACK = WM_APP + 1
WM_APP_QUIT = WM_APP + 2
WM_APP_REFRESH_TRAY = WM_APP + 3
WM_APP_RAW_INPUT = WM_APP + 4
WM_APP_SHOW = WM_APP + 5

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
WHEEL_DELTA = 120

LLMHF_INJECTED = 0x00000001
LLKHF_INJECTED = 0x00000010

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

# Virtual key codes used by the action catalogue.
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22  # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_F1 = 0x70
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_OEM_PLUS = 0xBB
VK_OEM_MINUS = 0xBD

# Keys that live on the extended half of the keyboard. Sending them without
# KEYEVENTF_EXTENDEDKEY gives you the numeric keypad equivalent instead, which is how
# "Win + Right" ends up typing a 6 in some applications.
EXTENDED_KEYS = frozenset({
    VK_PRIOR, VK_NEXT, VK_END, VK_HOME, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_INSERT, VK_DELETE, VK_LWIN, VK_RWIN, VK_APPS, VK_SNAPSHOT,
    VK_MEDIA_NEXT_TRACK, VK_MEDIA_PREV_TRACK, VK_MEDIA_STOP, VK_MEDIA_PLAY_PAUSE,
    VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP,
})

# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10

MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_UNCHECKED = 0x0000
MF_SEPARATOR = 0x0800
MF_DISABLED = 0x0002
MF_GRAYED = 0x0001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.SHORT)]


class _RAWMOUSE_UNION(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", _RAWMOUSE_BUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_UNION),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWHID(ctypes.Structure):
    _fields_ = [
        ("dwSizeHid", wintypes.DWORD),
        ("dwCount", wintypes.DWORD),
        ("bRawData", ctypes.c_ubyte * 1),
    ]


class _RAWINPUT_UNION(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD), ("hid", RAWHID)]


class RAWINPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("header", RAWINPUTHEADER), ("u", _RAWINPUT_UNION)]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


class _RID_DEVICE_INFO_MOUSE(ctypes.Structure):
    _fields_ = [
        ("dwId", wintypes.DWORD),
        ("dwNumberOfButtons", wintypes.DWORD),
        ("dwSampleRate", wintypes.DWORD),
        ("fHasHorizontalWheel", wintypes.BOOL),
    ]


class _RID_DEVICE_INFO_KEYBOARD(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSubType", wintypes.DWORD),
        ("dwKeyboardMode", wintypes.DWORD),
        ("dwNumberOfFunctionKeys", wintypes.DWORD),
        ("dwNumberOfIndicators", wintypes.DWORD),
        ("dwNumberOfKeysTotal", wintypes.DWORD),
    ]


class _RID_DEVICE_INFO_HID(ctypes.Structure):
    _fields_ = [
        ("dwVendorId", wintypes.DWORD),
        ("dwProductId", wintypes.DWORD),
        ("dwVersionNumber", wintypes.DWORD),
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
    ]


class _RID_DEVICE_INFO_UNION(ctypes.Union):
    _fields_ = [("mouse", _RID_DEVICE_INFO_MOUSE),
                ("keyboard", _RID_DEVICE_INFO_KEYBOARD),
                ("hid", _RID_DEVICE_INFO_HID)]


class RID_DEVICE_INFO(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("cbSize", wintypes.DWORD), ("dwType", wintypes.DWORD),
                ("u", _RID_DEVICE_INFO_UNION)]


RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000B
RIDEV_INPUTSINK = 0x00000100

RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200

# ---------------------------------------------------------------------------
# Prototypes
# ---------------------------------------------------------------------------

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]

user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ULONG_PTR, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = wintypes.SHORT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT),
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
user32.GetRawInputDeviceList.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICELIST), ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputDeviceList.restype = wintypes.UINT

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.ExtractIconExW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int,
    ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON), wintypes.UINT,
]
shell32.ExtractIconExW.restype = wintypes.UINT
shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE

# Locking the workstation has its own API and cannot be done by sending Win+L. That
# hotkey is handled by winlogon on the secure attention path, deliberately out of reach
# of injected input so that nothing can imitate or interfere with the lock screen, so
# SendInput delivers the keystroke and nothing whatsoever happens.
user32.LockWorkStation.argtypes = []
user32.LockWorkStation.restype = wintypes.BOOL

ERROR_ALREADY_EXISTS = 183

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def high_word_signed(value: int) -> int:
    """The signed 16-bit high word of a DWORD, as wheel deltas and X buttons are packed."""
    return ctypes.c_short((value >> 16) & 0xFFFF).value


def send_inputs(items: list[INPUT]) -> int:
    """Hand a batch of synthesised events to Windows in one atomic call.

    One call matters: sending a modifier and its key separately lets a real keystroke
    from the user interleave between them, which is how a remapped Ctrl+C occasionally
    turns into a stuck Ctrl key.
    """
    if not items:
        return 0
    array = (INPUT * len(items))(*items)
    return user32.SendInput(len(items), array, ctypes.sizeof(INPUT))


def key_input(vk: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0,
                          dwExtraInfo=INJECTED_SIGNATURE)
    return event


def mouse_input(flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> INPUT:
    event = INPUT(type=INPUT_MOUSE)
    event.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF, dwFlags=flags,
                          time=0, dwExtraInfo=INJECTED_SIGNATURE)
    return event


def tray_icon() -> wintypes.HICON:
    """An icon for the notification area.

    Windows ships a perfectly good mouse icon in main.cpl, which saves carrying a binary
    .ico around in the repository. If that ever moves, the generic application icon is
    always there.
    """
    small = wintypes.HICON()
    large = wintypes.HICON()
    for source, index in ((r"C:\Windows\System32\main.cpl", 0),
                          (r"C:\Windows\System32\imageres.dll", 101)):
        if shell32.ExtractIconExW(source, index, ctypes.byref(large),
                                  ctypes.byref(small), 1) and small:
            return small
    return user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR))


# Moving a window while a button is held.
GA_ROOT = 2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
# Without this, SetWindowPos waits for the target window to handle the message. A window
# that is busy or hung would then block whichever thread called it - and that thread is
# the one carrying the mouse hook, which Windows removes if it stops responding. Every
# move made from inside the hook must be asynchronous.
SWP_ASYNCWINDOWPOS = 0x4000
SW_RESTORE = 9
SW_MAXIMIZE = 3

# WS_EX_TOPMOST and the pseudo-handles SetWindowPos uses to change it. ctypes wraps
# these negative values correctly into a full-width HWND, so no special casting is
# needed beyond passing them as HWND(-1) / HWND(-2).
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)

# Windows that must never be dragged: the desktop itself, and the taskbar.
UNDRAGGABLE_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
})

user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

# What Windows itself uses to tell a click from the start of a drag, in pixels. Usually
# 4, but it is whatever the user has set under Ease of Access, so asking rather than
# assuming keeps a grabbed window exactly as responsive to start dragging as everything
# else on the desktop already is.
SM_CXDRAG = 68
SM_CYDRAG = 69


def drag_arm_distance() -> int:
    return max(1, user32.GetSystemMetrics(SM_CXDRAG), user32.GetSystemMetrics(SM_CYDRAG))


def window_class(hwnd) -> str:
    name = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, name, 128)
    return name.value


def draggable_window_at(x: int, y: int):
    """The top-level window under a point, if it is one that may be moved.

    WindowFromPoint answers with whichever control is under the cursor - a button, a
    text area - so its top-level ancestor is what actually gets moved.
    """
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT)
    if not root or not user32.IsWindow(root):
        return None
    if user32.IsIconic(root):
        return None
    if window_class(root) in UNDRAGGABLE_CLASSES:
        return None
    return root


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def move_window(hwnd, x: int, y: int) -> None:
    user32.SetWindowPos(hwnd, None, x, y, 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)


def resize_window(hwnd, width: int, height: int) -> None:
    user32.SetWindowPos(hwnd, None, 0, 0, max(120, width), max(80, height),
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)


# Snapping a dragged window to the screen edge, the way Windows' own title-bar drag
# does. Windows shows a translucent preview as you approach the edge and only resizes
# the window on release; this settles for the simpler half of that - no preview, but
# the same result once you let go - which is most of the value for a fraction of the
# work a live preview overlay would take.
MONITOR_DEFAULTTONEAREST = 0x00000002
SNAP_EDGE_MARGIN = 24  # pixels from the edge that counts as "at the edge"


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL


def monitor_work_area(x: int, y: int) -> tuple[int, int, int, int] | None:
    """The usable area (screen minus the taskbar) of whichever monitor a point is on."""
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


def snap_zone_at(x: int, y: int, margin: int = SNAP_EDGE_MARGIN):
    """Which edge of its monitor a point is close to, if any.

    Returns ``(zone, work_area)`` - ``zone`` is ``"left"``, ``"right"`` or
    ``"maximize"`` - or ``None`` when the point is not near an edge. The top edge wins
    over the side edges, which is what lets a drag into a top corner still maximise
    rather than being read as a side snap that happens to also be near the top.
    """
    area = monitor_work_area(x, y)
    if area is None:
        return None
    left, top, right, bottom = area
    if y <= top + margin:
        return "maximize", area
    if x <= left + margin:
        return "left", area
    if x >= right - margin:
        return "right", area
    return None


def apply_snap(hwnd, zone: str, area: tuple[int, int, int, int]) -> None:
    """Put a window into a snapped layout, the same shapes Windows' own snap uses."""
    left, top, right, bottom = area
    if zone == "maximize":
        # A real maximise, not just a window sized to fill the screen: this is what
        # lets a later double-click on the title bar, or Win+Down, restore it properly.
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return
    half = (right - left) // 2
    if zone == "left":
        user32.SetWindowPos(hwnd, None, left, top, half, bottom - top,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
    elif zone == "right":
        user32.SetWindowPos(hwnd, None, left + half, top, right - left - half, bottom - top,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)


def unmaximise(hwnd) -> None:
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def is_topmost(hwnd) -> bool:
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def clear_topmost(hwnd) -> None:
    """Undo a Windows bug where snapping a window with an injected Win+Arrow can
    leave it flagged always-on-top, so it stays ahead of everything else you click on
    afterwards - including things you click on the taskbar.

    A real, physical Win+Arrow press does not do this; the shell's snap-layout code
    appears to race when all four key events are delivered by SendInput at once
    instead of arriving with the small, natural gaps a human hand produces. The flag
    is only ever cleared when it is actually set, so a window the user deliberately
    made always-on-top with something else is left alone.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return
    if is_topmost(hwnd):
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)


def claim_single_instance(name: str) -> bool:
    """Whether this process is the first one. The mutex lives until the process exits."""
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def device_name(handle) -> str:
    size = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
    if not size.value:
        return ""
    buffer = ctypes.create_unicode_buffer(size.value + 1)
    if user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer,
                                     ctypes.byref(size)) == 0xFFFFFFFF:
        return ""
    return buffer.value


def input_devices() -> list[dict]:
    """Every input device Windows knows about, with its top-level HID usage.

    The settings window uses this to show what the mouse actually presents to Windows,
    which is the first thing you want to know when a button appears to do nothing.
    """
    count = wintypes.UINT(0)
    size = ctypes.sizeof(RAWINPUTDEVICELIST)
    if user32.GetRawInputDeviceList(None, ctypes.byref(count), size) == 0xFFFFFFFF:
        return []
    if not count.value:
        return []
    entries = (RAWINPUTDEVICELIST * count.value)()
    written = user32.GetRawInputDeviceList(entries, ctypes.byref(count), size)
    if written == 0xFFFFFFFF:
        return []

    found = []
    for entry in entries[:written]:
        info = RID_DEVICE_INFO()
        info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
        info_size = wintypes.UINT(ctypes.sizeof(RID_DEVICE_INFO))
        if user32.GetRawInputDeviceInfoW(entry.hDevice, RIDI_DEVICEINFO,
                                         ctypes.byref(info),
                                         ctypes.byref(info_size)) == 0xFFFFFFFF:
            continue
        record = {
            "handle": entry.hDevice,
            # Windows occasionally reports a type outside the three documented ones,
            # for instance on some precision touchpads. Those are listed but not
            # interpreted, rather than being dropped or mislabelled.
            "type": {RIM_TYPEMOUSE: "mouse", RIM_TYPEKEYBOARD: "keyboard",
                     RIM_TYPEHID: "hid"}.get(entry.dwType, "other"),
            "name": device_name(entry.hDevice),
            "usage_page": None, "usage": None,
            "vendor": None, "product": None, "buttons": None,
        }
        if entry.dwType == RIM_TYPEHID:
            record.update(usage_page=info.hid.usUsagePage, usage=info.hid.usUsage,
                          vendor=info.hid.dwVendorId, product=info.hid.dwProductId)
        elif entry.dwType == RIM_TYPEMOUSE:
            record.update(usage_page=0x01, usage=0x02,
                          buttons=info.mouse.dwNumberOfButtons)
        elif entry.dwType == RIM_TYPEKEYBOARD:
            record.update(usage_page=0x01, usage=0x06)
        found.append(record)
    return found
