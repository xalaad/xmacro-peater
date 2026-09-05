"""Genuine Windows touch injection (Win8+): taps, drags, and swipes are
replayed as real touch contacts via InjectTouchInput, so touch-aware apps
receive proper pointer input — not simulated mouse clicks.

Falls back gracefully: available() is False on systems without the API,
and VirtualOutput then replays touch events with absolute mouse input.
"""
from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

PT_TOUCH = 2
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000
TOUCH_FEEDBACK_DEFAULT = 1
TOUCH_MASK_CONTACTAREA = 0x00000001

if sys.platform == "win32":
    from ctypes import wintypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _POINTER_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerType", ctypes.c_uint32),
            ("pointerId", ctypes.c_uint32),
            ("frameId", ctypes.c_uint32),
            ("pointerFlags", ctypes.c_uint32),
            ("sourceDevice", wintypes.HANDLE),
            ("hwndTarget", wintypes.HWND),
            ("ptPixelLocation", _POINT),
            ("ptHimetricLocation", _POINT),
            ("ptPixelLocationRaw", _POINT),
            ("ptHimetricLocationRaw", _POINT),
            ("dwTime", wintypes.DWORD),
            ("historyCount", ctypes.c_uint32),
            ("InputData", ctypes.c_int32),
            ("dwKeyStates", wintypes.DWORD),
            ("PerformanceCount", ctypes.c_uint64),
            ("ButtonChangeType", ctypes.c_int32),
        ]

    class _POINTER_TOUCH_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerInfo", _POINTER_INFO),
            ("touchFlags", ctypes.c_uint32),
            ("touchMask", ctypes.c_uint32),
            ("rcContact", wintypes.RECT),
            ("rcContactRaw", wintypes.RECT),
            ("orientation", ctypes.c_uint32),
            ("pressure", ctypes.c_uint32),
        ]

    _user32 = ctypes.windll.user32
    _HAS_API = hasattr(_user32, "InjectTouchInput")
else:  # pragma: no cover
    _HAS_API = False


def virtual_screen_rect() -> dict | None:
    """The virtual desktop's bounding rect {x, y, w, h} — recorded into
    every take so absolute touch coordinates can be rescaled when the
    take replays on a different screen size."""
    if sys.platform != "win32":  # pragma: no cover
        return None
    gm = ctypes.windll.user32.GetSystemMetrics
    return {"x": gm(76), "y": gm(77), "w": gm(78), "h": gm(79)}


def adapt_touch_events(events, recorded: dict | None):
    """Rescale absolute touch positions from the RECORDING machine's
    virtual screen to the CURRENT one, so gestures land on the same
    relative spots regardless of resolution/scaling. No-op when screens
    match or the take predates screen metadata."""
    cur = virtual_screen_rect()
    if (not recorded or not cur or recorded == cur
            or not recorded.get("w") or not recorded.get("h")):
        return events
    from ..events import TOUCH, MacroEvent
    sx = cur["w"] / recorded["w"]
    sy = cur["h"] / recorded["h"]
    out = []
    for ev in events:
        if ev.src == TOUCH:
            d = dict(ev.data)
            d["x"] = round(cur["x"] + (d["x"] - recorded.get("x", 0)) * sx)
            d["y"] = round(cur["y"] + (d["y"] - recorded.get("y", 0)) * sy)
            out.append(MacroEvent(ev.t, ev.src, d))
        else:
            out.append(ev)
    return out


def touch_device_present() -> bool:
    """True when the machine has a touch digitizer (touchscreen)."""
    if sys.platform != "win32":
        return False
    SM_MAXIMUMTOUCHES = 95
    return ctypes.windll.user32.GetSystemMetrics(SM_MAXIMUMTOUCHES) > 0


class TouchInjector:
    """Single-contact touch injection. One instance per playback run."""

    @staticmethod
    def available() -> bool:
        return _HAS_API

    def __init__(self):
        if not _HAS_API:
            raise RuntimeError("Touch injection unavailable on this system")
        if not _user32.InitializeTouchInjection(1, TOUCH_FEEDBACK_DEFAULT):
            raise RuntimeError("InitializeTouchInjection failed")
        self._down = False
        self._info = _POINTER_TOUCH_INFO()
        pi = self._info.pointerInfo
        pi.pointerType = PT_TOUCH
        pi.pointerId = 0
        self._info.touchMask = TOUCH_MASK_CONTACTAREA
        self._info.pressure = 32000

    def _inject(self, x: int, y: int, flags: int) -> None:
        pi = self._info.pointerInfo
        pi.pointerFlags = flags
        pi.ptPixelLocation.x = int(x)
        pi.ptPixelLocation.y = int(y)
        self._info.rcContact.left = int(x) - 2
        self._info.rcContact.top = int(y) - 2
        self._info.rcContact.right = int(x) + 2
        self._info.rcContact.bottom = int(y) + 2
        if not _user32.InjectTouchInput(1, ctypes.byref(self._info)):
            err = ctypes.get_last_error()
            log.warning("InjectTouchInput failed (winerr %s)", err)

    def down(self, x: int, y: int) -> None:
        self._down = True
        self._inject(x, y, POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE
                     | POINTER_FLAG_INCONTACT)

    def move(self, x: int, y: int) -> None:
        if self._down:
            self._inject(x, y, POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE
                         | POINTER_FLAG_INCONTACT)

    def up(self, x: int, y: int) -> None:
        if self._down:
            self._down = False
            self._inject(x, y, POINTER_FLAG_UP)

    def release(self) -> None:
        """Lift a still-active contact (abort safety)."""
        if self._down:
            self.up(self._info.pointerInfo.ptPixelLocation.x,
                    self._info.pointerInfo.ptPixelLocation.y)
