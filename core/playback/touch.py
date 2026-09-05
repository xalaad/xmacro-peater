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

    # use_last_error=True: without it ctypes.get_last_error() reads an
    # unpopulated stash and injection failures always log winerr 0
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _HAS_API = hasattr(_user32, "InjectTouchInput")
else:  # pragma: no cover
    _HAS_API = False


# Implementations live in the OS-neutral core.screen; re-imported here
# (not just re-exported) so existing imports AND test monkeypatching of
# this module's attributes keep working.
from ..screen import touch_device_present, virtual_screen_rect  # noqa: E402,F401


def adapt_touch_events(events, recorded: dict | None):
    """Screen adaptation for a take replayed on a DIFFERENT screen than
    it was recorded on. No-op (same list object) when screens match or
    the take predates screen metadata.

    - touch events: absolute x/y rescaled linearly to the current screen
    - mouse_move events that carry the absolute cursor path (px/py):
      converted to runtime 'mouse_abs' events at the rescaled path —
      raw relative counts are hardware mickeys shaped by THIS machine's
      pointer speed/acceleration and screen, so replaying them on a
      different setup walks a different path; the recorded cursor path,
      rescaled, is the truth. Same-screen replay keeps raw counts
      (bit-perfect for games). Old takes without px/py stay relative."""
    cur = virtual_screen_rect()
    if (not recorded or not cur or recorded == cur
            or not recorded.get("w") or not recorded.get("h")):
        return events
    from ..events import TOUCH, MacroEvent
    rx, ry = recorded.get("x", 0), recorded.get("y", 0)
    return _rescale(events, recorded, cur, rx, ry, TOUCH, MacroEvent)


def adapt_events(events, recorded: dict | None,
                 force_abs_mouse: bool = False):
    """Full playback adaptation. force_abs_mouse=True replays the exact
    recorded cursor path even on the SAME screen — absolute injection
    bypasses pointer speed/acceleration entirely, which makes replay
    deterministic on devices whose motion doesn't reproduce from raw
    counts (precision touchpads, exotic pointer settings)."""
    if not force_abs_mouse:
        return adapt_touch_events(events, recorded)
    cur = virtual_screen_rect()
    if cur is None:
        return adapt_touch_events(events, recorded)
    if not recorded or not recorded.get("w") or not recorded.get("h"):
        recorded = cur  # same-machine take: identity scaling
    from ..events import TOUCH, MacroEvent
    rx, ry = recorded.get("x", 0), recorded.get("y", 0)
    return _rescale(events, recorded, cur, rx, ry, TOUCH, MacroEvent)


def _rescale(events, recorded, cur, rx, ry, TOUCH, MacroEvent):
    sx = cur["w"] / recorded["w"]
    sy = cur["h"] / recorded["h"]

    def scale(x, y):
        return (round(cur["x"] + (x - rx) * sx),
                round(cur["y"] + (y - ry) * sy))

    out = []
    for ev in events:
        if ev.src == TOUCH:
            d = dict(ev.data)
            d["x"], d["y"] = scale(d["x"], d["y"])
            out.append(MacroEvent(ev.t, ev.src, d))
        elif ev.src == "mouse_move" and "px" in ev.data:
            x, y = scale(ev.data["px"], ev.data["py"])
            out.append(MacroEvent(ev.t, "mouse_abs", {"x": x, "y": y}))
        else:
            out.append(ev)
    return out


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
