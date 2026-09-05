"""Screen and input-hardware metrics — OS-neutral surface.

Capture (recorder) and playback (touch/mouse adaptation) both need the
virtual desktop rect; keeping it here means capture no longer depends on
the playback package and gives ports ONE place to implement.

Porting note (macOS/Linux): implement the two functions below for the
platform — everything that consumes them already tolerates None/False
(recordings simply carry no screen metadata and replay unscaled, and
touch features stay hidden). On macOS, CGDisplayBounds over all displays
yields the virtual rect; there is no public touch-digitizer query, so
touch_device_present() stays False.
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes


def virtual_screen_rect() -> dict | None:
    """The virtual desktop's bounding rect {x, y, w, h} — recorded into
    every take so absolute touch coordinates can be rescaled when the
    take replays on a different screen size. None when unknown."""
    if sys.platform != "win32":  # pragma: no cover — porting point
        return None
    gm = ctypes.windll.user32.GetSystemMetrics
    return {"x": gm(76), "y": gm(77), "w": gm(78), "h": gm(79)}


def touch_device_present() -> bool:
    """True when the machine has a touch digitizer (touchscreen)."""
    if sys.platform != "win32":  # pragma: no cover — porting point
        return False
    SM_MAXIMUMTOUCHES = 95
    return ctypes.windll.user32.GetSystemMetrics(SM_MAXIMUMTOUCHES) > 0
