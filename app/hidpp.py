"""Reaching the gesture button through Logitech's own channel.

Windows has no standard place to put a sixth mouse button. The MX Master's report
descriptor declares sixteen, but the mouse driver and RAWINPUT both stop at five, so the
gesture button under your thumb is simply not delivered to anything - not to a low-level
hook, not to raw input, not to any application.

Logitech's own software does not read it as a mouse button either. It talks to the mouse
over HID++, a request/response protocol carried on a separate vendor-defined HID
collection that the system does not claim exclusively, and asks the device to stop
handling a button itself and report it as a notification instead. That is "diversion",
and it is what this module does:

    find the vendor collection -> ping it -> look up feature 0x1B04 ->
    divert control 0x00C3 -> read notifications

Two things are worth knowing about diversion. It is *temporary* on this control: the
mouse forgets when it reconnects, so it has to be re-applied, and it is cheap and
idempotent to do so. And while a button is diverted it does nothing on its own - which
for the gesture button costs nothing, because on Windows it does nothing anyway.

Everything here is read/write to one local HID device node. Nothing is installed, no
driver is loaded, and no elevation is needed.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

LOGITECH = 0x046D

# HID++ transport.
HIDPP_LONG = 0x11
HIDPP_LONG_SIZE = 20
HIDPP_SHORT = 0x10
HIDPP_SHORT_SIZE = 7
# An arbitrary non-zero software id. The device echoes it back, which is how a reply to
# our request is told apart from an unsolicited notification, whose software id is zero.
SW_ID = 0x0A
ROOT_FEATURE_INDEX = 0x00

FEATURE_REPROG_CONTROLS_V4 = 0x1B04
FEATURE_DEVICE_NAME = 0x0005
# Logitech's control id for the gesture button. 0x00C3 is the physical one under the
# thumb; 0x00D7 is the virtual gesture control, which is not a button at all.
CID_GESTURE = 0x00C3

# setCidReporting flags, confirmed against the device:
#   bit 0 is the divert setting, bit 1 says that setting is meaningful.
# Writing 0x01 or 0x00 - the setting without its change bit - is accepted and then
# quietly ignored, so both bits always have to be sent together.
DIVERT = 0x03
UNDIVERT = 0x02
# getCidReporting reports the current state in the same bit 0.
DIVERTED = 0x01

# Devices connected through a receiver answer on their own index; a device paired
# directly over Bluetooth answers on 0xFF.
DEVICE_INDEXES = (0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06)

RECONNECT_SECONDS = 4.0
MAX_RECONNECT_SECONDS = 30.0
# Diversion is lost when the mouse sleeps and comes back. Re-applying it costs one
# 20-byte write; doing it occasionally is far cheaper than missing every press until
# the app is restarted.
REDIVERT_SECONDS = 240.0

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
HIDP_STATUS_SUCCESS = 0x00110000


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(wintypes.ULONG))]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

hid.HidD_GetHidGuid.restype = None
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.restype = ctypes.c_ubyte
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.restype = ctypes.c_ubyte
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = ctypes.c_ubyte
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_SetNumInputBuffers.restype = ctypes.c_ubyte
hid.HidD_SetNumInputBuffers.argtypes = [wintypes.HANDLE, wintypes.ULONG]
hid.HidD_GetProductString.restype = ctypes.c_ubyte
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]


# ---------------------------------------------------------------------------
# Finding and opening the vendor collection
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    path: str
    vendor: int
    product: int
    usage_page: int
    usage: int
    report_length: int
    name: str


def find_vendor_collections(vendor: int = LOGITECH) -> list[Candidate]:
    """Every Logitech HID++ collection currently attached.

    Each is opened with no access rights at all just to read its attributes, which is
    allowed even for collections Windows owns exclusively.
    """
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    enumerator = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if enumerator == INVALID_HANDLE_VALUE:
        log.warning("Could not enumerate HID devices: %s", ctypes.get_last_error())
        return []

    found: list[Candidate] = []
    try:
        index = 0
        while True:
            interface = SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    enumerator, None, ctypes.byref(guid), index, ctypes.byref(interface)):
                break
            index += 1

            needed = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                enumerator, ctypes.byref(interface), None, 0, ctypes.byref(needed), None)
            if not needed.value:
                continue
            buffer = ctypes.create_string_buffer(needed.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W's declared cbSize is 8 on 64-bit and 6
            # on 32-bit, even though the path itself always starts at offset 4.
            declared = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buffer, ctypes.byref(wintypes.DWORD(declared)), 4)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    enumerator, ctypes.byref(interface), buffer, needed.value,
                    ctypes.byref(needed), None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buffer) + 4)

            candidate = _describe(path, vendor)
            if candidate is not None:
                found.append(candidate)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(enumerator)
    return found


def _describe(path: str, vendor: int) -> Candidate | None:
    handle = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  None, OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        return None
    try:
        attributes = HIDD_ATTRIBUTES()
        attributes.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if not hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
            return None
        if attributes.VendorID != vendor:
            return None

        preparsed = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            return None
        try:
            caps = HIDP_CAPS()
            if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                return None
        finally:
            hid.HidD_FreePreparsedData(preparsed)

        # A HID++ collection is vendor-defined and carries 20-byte reports both ways.
        if caps.UsagePage < 0xFF00:
            return None
        if caps.InputReportByteLength != HIDPP_LONG_SIZE:
            return None
        if caps.OutputReportByteLength != HIDPP_LONG_SIZE:
            return None

        name = ctypes.create_unicode_buffer(128)
        hid.HidD_GetProductString(handle, name, ctypes.sizeof(name))
        return Candidate(path=path, vendor=attributes.VendorID,
                         product=attributes.ProductID, usage_page=caps.UsagePage,
                         usage=caps.Usage, report_length=HIDPP_LONG_SIZE,
                         name=name.value or "Logitech device")
    finally:
        kernel32.CloseHandle(handle)


class Channel:
    """One open HID++ collection, with reads and writes that cannot hang forever."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.handle = kernel32.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if self.handle == INVALID_HANDLE_VALUE:
            raise OSError(f"cannot open {path}: error {ctypes.get_last_error()}")
        self._read_event = kernel32.CreateEventW(None, True, False, None)
        self._write_event = kernel32.CreateEventW(None, True, False, None)
        # Notifications arrive whenever the mouse feels like it, so give Windows room to
        # hold a few while we are busy rather than dropping them.
        hid.HidD_SetNumInputBuffers(self.handle, 64)

    def close(self) -> None:
        if self.handle and self.handle != INVALID_HANDLE_VALUE:
            kernel32.CancelIoEx(self.handle, None)
            kernel32.CloseHandle(self.handle)
        self.handle = None
        for event in ("_read_event", "_write_event"):
            value = getattr(self, event, None)
            if value:
                kernel32.CloseHandle(value)
                setattr(self, event, None)

    def write(self, payload: bytes) -> None:
        data = bytes(payload).ljust(HIDPP_LONG_SIZE, b"\x00")[:HIDPP_LONG_SIZE]
        buffer = ctypes.create_string_buffer(data, HIDPP_LONG_SIZE)
        overlapped = OVERLAPPED()
        overlapped.hEvent = self._write_event
        kernel32.ResetEvent(self._write_event)
        written = wintypes.DWORD(0)
        if kernel32.WriteFile(self.handle, buffer, HIDPP_LONG_SIZE,
                              ctypes.byref(written), ctypes.byref(overlapped)):
            return
        code = ctypes.get_last_error()
        if code != ERROR_IO_PENDING:
            raise OSError(f"write failed: error {code}")
        if kernel32.WaitForSingleObject(self._write_event, 2000) != WAIT_OBJECT_0:
            kernel32.CancelIoEx(self.handle, ctypes.byref(overlapped))
            raise OSError("write timed out")
        if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(overlapped),
                                            ctypes.byref(written), False):
            raise OSError(f"write failed: error {ctypes.get_last_error()}")

    def read(self, timeout_ms: int) -> bytes | None:
        """One report, or None if nothing arrived in time."""
        buffer = ctypes.create_string_buffer(HIDPP_LONG_SIZE)
        overlapped = OVERLAPPED()
        overlapped.hEvent = self._read_event
        kernel32.ResetEvent(self._read_event)
        read = wintypes.DWORD(0)
        if not kernel32.ReadFile(self.handle, buffer, HIDPP_LONG_SIZE,
                                 ctypes.byref(read), ctypes.byref(overlapped)):
            code = ctypes.get_last_error()
            if code != ERROR_IO_PENDING:
                raise OSError(f"read failed: error {code}")
            if kernel32.WaitForSingleObject(self._read_event, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.handle, ctypes.byref(overlapped))
                kernel32.WaitForSingleObject(self._read_event, 200)
                return None
            if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(overlapped),
                                                ctypes.byref(read), False):
                raise OSError(f"read failed: error {ctypes.get_last_error()}")
        return buffer.raw[:read.value]


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    pass


def _frame(device_index: int, feature_index: int, function: int, params: bytes) -> bytes:
    return bytes([HIDPP_LONG, device_index, feature_index,
                  ((function & 0x0F) << 4) | SW_ID]) + bytes(params)


def _request(channel: Channel, device_index: int, feature_index: int, function: int,
             params: bytes = b"", timeout_ms: int = 900, on_other=None) -> bytes:
    """Send one request and wait for its own answer.

    Frames that arrive meanwhile - notably a button notification, which the mouse sends
    whenever it likes - are handed to ``on_other`` rather than dropped. Without that, a
    request made while listening would silently eat a press.
    """
    wanted = ((function & 0x0F) << 4) | SW_ID
    channel.write(_frame(device_index, feature_index, function, params))

    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProtocolError("the mouse did not answer")
        reply = channel.read(max(1, int(remaining * 1000)))
        if not reply or len(reply) < 6:
            continue
        if reply[1] == device_index:
            # 0xFF in the feature slot marks an error reply, not feature index 255.
            if reply[2] == 0xFF and reply[3] == feature_index and reply[4] == wanted:
                raise ProtocolError(
                    f"the mouse refused the request (error 0x{reply[5]:02X})")
            if reply[2] == feature_index and reply[3] == wanted:
                return reply[4:]
        if on_other is not None:
            on_other(reply)


def _ping(channel: Channel, device_index: int) -> tuple[int, int]:
    marker = 0xAA
    answer = _request(channel, device_index, ROOT_FEATURE_INDEX, 0x01,
                      bytes([0, 0, marker]), timeout_ms=600)
    if answer[2] != marker:
        raise ProtocolError("the mouse answered a ping that was not ours")
    return answer[0], answer[1]


def _feature_index(channel: Channel, device_index: int, feature: int) -> int:
    answer = _request(channel, device_index, ROOT_FEATURE_INDEX, 0x00,
                      bytes([(feature >> 8) & 0xFF, feature & 0xFF, 0]))
    return answer[0]


def _cid_flags(channel: Channel, device_index: int, reprog_index: int, cid: int,
               on_other=None) -> int:
    """getCidReporting: what the mouse currently believes about this control."""
    answer = _request(channel, device_index, reprog_index, 0x02,
                      bytes([(cid >> 8) & 0xFF, cid & 0xFF]), on_other=on_other)
    return answer[2]


def device_name(channel: Channel, device_index: int) -> str:
    """The mouse's own name for itself, read 16 characters at a time.

    Purely cosmetic - it is what the settings window shows - so any failure here just
    means a duller message, never a failure to work.
    """
    try:
        index = _feature_index(channel, device_index, FEATURE_DEVICE_NAME)
        if not index:
            return ""
        length = _request(channel, device_index, index, 0x00)[0]
        name = b""
        while len(name) < length and len(name) < 64:
            chunk = _request(channel, device_index, index, 0x01, bytes([len(name)]))
            if not chunk:
                break
            name += chunk[:min(16, length - len(name))]
        return name.decode("utf-8", "replace").strip("\x00 ").strip()
    except (ProtocolError, OSError, IndexError):
        return ""


def set_divert(channel: Channel, device_index: int, reprog_index: int, cid: int,
               divert: bool, on_other=None) -> bool:
    """Turn diversion on or off, and confirm the mouse really did it.

    Asking and hoping is not good enough here. If diversion silently fails the button
    simply never reports anything, which looks exactly like a mouse that cannot do it at
    all - so the setting is always read back.
    """
    _request(channel, device_index, reprog_index, 0x03,
             bytes([(cid >> 8) & 0xFF, cid & 0xFF,
                    DIVERT if divert else UNDIVERT, 0, 0]),
             on_other=on_other)
    flags = _cid_flags(channel, device_index, reprog_index, cid, on_other=on_other)
    return bool(flags & DIVERTED) is divert


def diverted_controls(frame: bytes, reprog_index: int) -> set[int] | None:
    """The controls held down, from a diverted-button notification.

    The device reports the whole set every time, as up to four big-endian control ids
    packed into the payload, with empty slots zeroed. A release is therefore a frame
    with that control missing, not an event of its own.
    """
    if len(frame) < 12 or frame[0] != HIDPP_LONG:
        return None
    if frame[2] != reprog_index:
        return None
    # A notification has function 0 and software id 0; a reply to us would not.
    if frame[3] != 0x00:
        return None
    held = set()
    for offset in range(4, 12, 2):
        cid = (frame[offset] << 8) | frame[offset + 1]
        if cid:
            held.add(cid)
    return held


# ---------------------------------------------------------------------------
# The source the rest of the app sees
# ---------------------------------------------------------------------------


@dataclass
class Status:
    state: str  # "off" | "searching" | "active" | "failed"
    detail: str

    @property
    def working(self) -> bool:
        return self.state == "active"


class GestureButton(threading.Thread):
    """Watches the gesture button and reports it as an ordinary press and release."""

    def __init__(self, on_button, on_status=None) -> None:
        super().__init__(name="gesture-button", daemon=True)
        self._on_button = on_button
        self._on_status = on_status
        self._stop = threading.Event()
        self._channel: Channel | None = None
        self._pressed = False
        self._reprog_index = 0
        self.status = Status("searching", "Looking for the mouse")

    # -- lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def _set_status(self, state: str, detail: str) -> None:
        if (state, detail) == (self.status.state, self.status.detail):
            return
        self.status = Status(state, detail)
        log.info("Gesture button: %s (%s)", state, detail)
        if self._on_status is not None:
            try:
                self._on_status(self.status)
            except Exception:
                log.exception("Gesture status callback failed")

    def run(self) -> None:
        delay = RECONNECT_SECONDS
        while not self._stop.is_set():
            connected = False
            try:
                connected = self._session()
            except Exception as error:
                self._set_status("searching", str(error))
            finally:
                self._close()
            # Back off when there is nothing to talk to. A machine with no Logitech
            # mouse attached should not enumerate every HID device every few seconds
            # for as long as it is switched on.
            delay = (RECONNECT_SECONDS if connected
                     else min(delay * 1.6, MAX_RECONNECT_SECONDS))
            if not self._stop.is_set():
                self._stop.wait(delay)
        self._set_status("off", "Stopped")

    def _close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None

    # -- one connected session --------------------------------------------

    def _session(self) -> bool:
        """One attempt at finding the mouse and listening to it.

        Returns whether we actually got as far as talking to something, which decides
        how soon it is worth trying again.
        """
        candidates = find_vendor_collections()
        if not candidates:
            self._set_status("searching", "No Logitech mouse is connected")
            return False

        for candidate in candidates:
            if self._stop.is_set():
                return True
            try:
                if self._talk_to(candidate):
                    return True
            except OSError as error:
                log.debug("%s did not work out: %s", candidate.path, error)
        self._set_status("failed", "The mouse does not offer a divertable gesture button")
        return False

    def _talk_to(self, candidate: Candidate) -> bool:
        channel = Channel(candidate.path)
        self._channel = channel

        index = None
        for device_index in DEVICE_INDEXES:
            try:
                major, minor = _ping(channel, device_index)
            except (ProtocolError, OSError):
                continue
            index = device_index
            log.info("HID++ %d.%d on %s (device index 0x%02X)",
                     major, minor, candidate.name, device_index)
            break
        if index is None:
            self._close()
            return False

        friendly = device_name(channel, index) or candidate.name
        try:
            reprog = _feature_index(channel, index, FEATURE_REPROG_CONTROLS_V4)
        except (ProtocolError, OSError) as error:
            log.debug("No reprogrammable controls feature: %s", error)
            self._close()
            return False
        if not reprog:
            # Index zero means "this device does not have that feature".
            self._close()
            return False

        if not set_divert(channel, index, reprog, CID_GESTURE, True):
            self._set_status("failed",
                             "This mouse will not hand over its gesture button")
            self._close()
            return True  # the right device, but it said no; do not try the next one

        self._set_status("active", f"Connected to {friendly}")
        self._listen(channel, index, reprog)
        return True

    def _listen(self, channel: Channel, device_index: int, reprog_index: int) -> None:
        self._pressed = False
        self._reprog_index = reprog_index
        last_divert = time.monotonic()

        while not self._stop.is_set():
            frame = channel.read(500)  # short, so that stopping stays quick
            if frame:
                self._handle(frame)

            if time.monotonic() - last_divert > REDIVERT_SECONDS:
                last_divert = time.monotonic()
                # Cheap insurance: the mouse forgets diversion when it sleeps and
                # reconnects, and nothing tells us that it has.
                if not set_divert(channel, device_index, reprog_index, CID_GESTURE,
                                  True, on_other=self._handle):
                    raise OSError("the mouse stopped honouring diversion")

        if self._pressed:
            # Never leave the engine believing the button is still held down.
            self._pressed = False
            self._report(False)
        try:
            set_divert(channel, device_index, reprog_index, CID_GESTURE, False,
                       on_other=None)
        except (ProtocolError, OSError):
            log.debug("Could not hand the gesture button back; it returns on reconnect")

    def _handle(self, frame: bytes) -> None:
        """One frame from the mouse, which may or may not be a button notification."""
        held = diverted_controls(frame, self._reprog_index)
        if held is None:
            return
        now = CID_GESTURE in held
        if now != self._pressed:
            self._pressed = now
            self._report(now)

    def _report(self, pressed: bool) -> None:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        try:
            self._on_button(pressed, (point.x, point.y))
        except Exception:
            log.exception("Gesture button handler failed")
