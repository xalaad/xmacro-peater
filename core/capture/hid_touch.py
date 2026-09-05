"""Parse true contact coordinates out of raw HID digitizer reports.

Windows only moves the cursor for touch it PROMOTES to mouse input.
Pointer-native surfaces (Chrome, the taskbar, UWP) consume the pointer
directly, so GetCursorPos() there returns a STALE position — gestures
would record at wherever the cursor last happened to be. The report
itself always carries the real contact position, so parse it with the
HID parser (hid.dll HidP_*) and scale logical units to screen pixels.

Falls back cleanly: parse() returns None when anything is unavailable
and callers use the cursor position instead.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

HIDP_STATUS_SUCCESS = 0x00110000
HidP_Input = 0
GENERIC_DESKTOP_PAGE = 0x01
DIGITIZER_PAGE = 0x0D
USAGE_TIP_SWITCH = 0x42
USAGE_X, USAGE_Y = 0x30, 0x31
RIDI_PREPARSEDDATA = 0x20000005

USAGE = ctypes.c_ushort


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", USAGE), ("UsagePage", USAGE),
        ("InputReportByteLength", ctypes.c_ushort),
        ("OutputReportByteLength", ctypes.c_ushort),
        ("FeatureReportByteLength", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 17),
        ("NumberLinkCollectionNodes", ctypes.c_ushort),
        ("NumberInputButtonCaps", ctypes.c_ushort),
        ("NumberInputValueCaps", ctypes.c_ushort),
        ("NumberInputDataIndices", ctypes.c_ushort),
        ("NumberOutputButtonCaps", ctypes.c_ushort),
        ("NumberOutputValueCaps", ctypes.c_ushort),
        ("NumberOutputDataIndices", ctypes.c_ushort),
        ("NumberFeatureButtonCaps", ctypes.c_ushort),
        ("NumberFeatureValueCaps", ctypes.c_ushort),
        ("NumberFeatureDataIndices", ctypes.c_ushort),
    ]


class _RangeStruct(ctypes.Structure):
    _fields_ = [("UsageMin", USAGE), ("UsageMax", USAGE),
                ("StringMin", ctypes.c_ushort),
                ("StringMax", ctypes.c_ushort),
                ("DesignatorMin", ctypes.c_ushort),
                ("DesignatorMax", ctypes.c_ushort),
                ("DataIndexMin", ctypes.c_ushort),
                ("DataIndexMax", ctypes.c_ushort)]


class _NotRangeStruct(ctypes.Structure):
    _fields_ = [("Usage", USAGE), ("Reserved1", USAGE),
                ("StringIndex", ctypes.c_ushort),
                ("Reserved2", ctypes.c_ushort),
                ("DesignatorIndex", ctypes.c_ushort),
                ("Reserved3", ctypes.c_ushort),
                ("DataIndex", ctypes.c_ushort),
                ("Reserved4", ctypes.c_ushort)]


class _CapsUnion(ctypes.Union):
    _fields_ = [("Range", _RangeStruct), ("NotRange", _NotRangeStruct)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", USAGE), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", ctypes.c_ushort),
        ("LinkCollection", ctypes.c_ushort),
        ("LinkUsage", USAGE), ("LinkUsagePage", USAGE),
        ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte),
        ("BitSize", ctypes.c_ushort), ("ReportCount", ctypes.c_ushort),
        ("Reserved2", ctypes.c_ushort * 5),
        ("UnitsExp", ctypes.c_ulong), ("Units", ctypes.c_ulong),
        ("LogicalMin", ctypes.c_long), ("LogicalMax", ctypes.c_long),
        ("PhysicalMin", ctypes.c_long), ("PhysicalMax", ctypes.c_long),
        ("u", _CapsUnion),
    ]


if sys.platform == "win32":
    _hid = ctypes.windll.hid
    _user32 = ctypes.windll.user32
    _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p,
                                  ctypes.POINTER(HIDP_CAPS)]
    _hid.HidP_GetValueCaps.argtypes = [
        ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
        ctypes.POINTER(ctypes.c_ushort), ctypes.c_void_p]
    _hid.HidP_GetUsageValue.argtypes = [
        ctypes.c_int, USAGE, ctypes.c_ushort, USAGE,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
        ctypes.c_char_p, ctypes.c_ulong]
    _hid.HidP_GetUsages.argtypes = [
        ctypes.c_int, USAGE, ctypes.c_ushort,
        ctypes.POINTER(USAGE), ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong]
    _hid.HidP_MaxUsageListLength.argtypes = [
        ctypes.c_int, USAGE, ctypes.c_void_p]
    _hid.HidP_MaxUsageListLength.restype = ctypes.c_ulong
    _user32.GetRawInputDeviceInfoW.argtypes = [
        wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint)]


class DeviceMap:
    """Per-device X/Y parsing info, resolved once and cached."""

    def __init__(self, preparsed, buf, link: int, x_caps, y_caps,
                 tip_slots: int = 0):
        self.preparsed = preparsed
        self._buf = buf  # the preparsed buffer must outlive this map
        self.link = link
        # >0 when the device reports digitizer buttons, i.e. the tip
        # switch that tells us definitively whether the finger is down
        self.tip_slots = tip_slots
        self.x_min, self.x_max = x_caps.LogicalMin, x_caps.LogicalMax
        self.y_min, self.y_max = y_caps.LogicalMin, y_caps.LogicalMax

    @property
    def usable(self) -> bool:
        return self.x_max > self.x_min and self.y_max > self.y_min


_cache: dict[int, DeviceMap | None] = {}


def clear_cache() -> None:
    """Forget cached device maps. Windows reuses hDevice handle values
    after unplug/replug — the hub calls this on WM_INPUT_DEVICE_CHANGE
    so a NEW digitizer (dock, external touch display) never gets scaled
    with the OLD device's logical ranges."""
    _cache.clear()


def _build_map(hdevice) -> DeviceMap | None:
    size = ctypes.c_uint(0)
    _user32.GetRawInputDeviceInfoW(hdevice, RIDI_PREPARSEDDATA, None,
                                   ctypes.byref(size))
    if not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _user32.GetRawInputDeviceInfoW(hdevice, RIDI_PREPARSEDDATA, buf,
                                      ctypes.byref(size)) <= 0:
        return None
    preparsed = ctypes.cast(buf, ctypes.c_void_p)
    caps = HIDP_CAPS()
    if _hid.HidP_GetCaps(preparsed,
                         ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
        return None
    n = ctypes.c_ushort(caps.NumberInputValueCaps)
    if not n.value:
        return None
    arr = (HIDP_VALUE_CAPS * n.value)()
    if _hid.HidP_GetValueCaps(HidP_Input, arr, ctypes.byref(n),
                              preparsed) != HIDP_STATUS_SUCCESS:
        return None
    # Multi-touch reports one link collection per contact — use the
    # lowest, i.e. the primary contact (what a one-finger gesture is)
    per_link: dict[int, dict[int, object]] = {}
    for c in arr[:n.value]:
        if c.UsagePage != GENERIC_DESKTOP_PAGE or c.IsRange:
            continue
        usage = c.u.NotRange.Usage
        if usage in (USAGE_X, USAGE_Y):
            per_link.setdefault(c.LinkCollection, {})[usage] = c
    for link in sorted(per_link):
        pair = per_link[link]
        if USAGE_X in pair and USAGE_Y in pair:
            try:
                slots = int(_hid.HidP_MaxUsageListLength(
                    HidP_Input, DIGITIZER_PAGE, preparsed))
            except Exception:  # noqa: BLE001
                slots = 0
            dm = DeviceMap(preparsed, buf, link,
                           pair[USAGE_X], pair[USAGE_Y], slots)
            return dm if dm.usable else None
    return None


def _read_tip(dm, report: bytes) -> bool | None:
    """True/False when the device reports a tip switch, else None.

    A stationary finger stops generating reports on most digitizers, so
    timing alone splits a slow drag into several contacts. The tip
    switch is ground truth for down/up."""
    if not dm.tip_slots:
        return None
    try:
        n = ctypes.c_ulong(dm.tip_slots)
        lst = (USAGE * dm.tip_slots)()
        st = _hid.HidP_GetUsages(
            HidP_Input, DIGITIZER_PAGE, dm.link, lst, ctypes.byref(n),
            dm.preparsed, ctypes.c_char_p(report), len(report))
        if st != HIDP_STATUS_SUCCESS:
            return None
        return USAGE_TIP_SWITCH in lst[:n.value]
    except Exception:  # noqa: BLE001
        return None


def parse(hdevice, report: bytes) -> tuple[int, int, bool | None] | None:
    """(x, y, tip_down) in SCREEN pixels; tip_down None when unknown."""
    if sys.platform != "win32" or not report:
        return None
    key = int(hdevice) if hdevice else 0
    if key not in _cache:
        try:
            _cache[key] = _build_map(hdevice)
        except Exception as e:  # noqa: BLE001 — never break capture
            log.info("HID touch map unavailable: %s", e)
            _cache[key] = None
    dm = _cache[key]
    if dm is None:
        return None
    try:
        vx, vy = ctypes.c_ulong(0), ctypes.c_ulong(0)
        rep = ctypes.c_char_p(report)
        n = len(report)
        if _hid.HidP_GetUsageValue(
                HidP_Input, GENERIC_DESKTOP_PAGE, dm.link, USAGE_X,
                ctypes.byref(vx), dm.preparsed, rep,
                n) != HIDP_STATUS_SUCCESS:
            return None
        if _hid.HidP_GetUsageValue(
                HidP_Input, GENERIC_DESKTOP_PAGE, dm.link, USAGE_Y,
                ctypes.byref(vy), dm.preparsed, rep,
                n) != HIDP_STATUS_SUCCESS:
            return None
    except Exception:  # noqa: BLE001
        return None
    sw = _user32.GetSystemMetrics(78) or 1
    sh = _user32.GetSystemMetrics(79) or 1
    ox = _user32.GetSystemMetrics(76)
    oy = _user32.GetSystemMetrics(77)
    x = ox + (vx.value - dm.x_min) * sw / (dm.x_max - dm.x_min)
    y = oy + (vy.value - dm.y_min) * sh / (dm.y_max - dm.y_min)
    return int(round(x)), int(round(y)), _read_tip(dm, report)
