"""LIVE touch probe — tap with a real finger and see what each layer sees.

    python tools/touch_live_probe.py [seconds]

Tap during the countdown: the DESKTOP, the TASKBAR, a CHROME tab/window
bar, inside a page, one drag, and one real MOUSE click for comparison.

Layers shown:
  HUB  - one digitizer report: HID-parsed contact position, and what
         GetCursorPos said at the same instant (they differ exactly
         where Windows does not promote touch -> the old bug)
  REC  - what a touch-mode RECORDING captures (down/move/up)
  MON  - what the activity log shows (touch tap vs mouse click)

Passing looks like: every contact appears once in HUB and REC, MON
shows TOUCH and never MOUSE for a finger, and HID/cursor agree except
over pointer-native surfaces (where only HID is right).
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: F401  pins Per-Monitor-V2 DPI awareness

from core.capture.keyboard_mouse import KeyboardMouseCapture
from core.capture.raw_touch import HUB
from ui.live_monitor import LiveInputMonitor

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
t0 = time.monotonic()
log: list[tuple[str, str]] = []
stats = {"hub": 0, "rec_down": 0, "mon_tap": 0, "mon_mouse": 0,
         "cursor_mismatch": 0}


def note(kind: str, text: str) -> None:
    log.append((kind, text))
    print(f"  {time.monotonic() - t0:6.2f}s  {kind:4} {text}", flush=True)


def cursor() -> tuple[int, int]:
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


_last_hub = [0.0]


def on_hub(x: int, y: int, tip: bool | None = None) -> None:
    """Raw report straight off the hub (position already HID-parsed;
    tip is the digitizer finger-down flag when the device reports it)."""
    now = time.monotonic()
    if now - _last_hub[0] < 0.30:      # one line per contact, not per report
        return
    _last_hub[0] = now
    stats["hub"] += 1
    cx, cy = cursor()
    drift = abs(cx - x) + abs(cy - y)
    if drift > 8:
        stats["cursor_mismatch"] += 1
        note("HUB", f"contact ({x},{y}) tip={tip}  cursor was ({cx},{cy})"
                    f"  <-- cursor STALE by {drift}px (HID saved it)")
    else:
        note("HUB", f"contact ({x},{y}) tip={tip}  cursor agrees")


HUB.subscribe(on_hub)


def rec_note(e: dict) -> None:
    if e["src"] == "touch":
        if e["action"] == "down":
            stats["rec_down"] += 1
        if e["action"] != "move":      # moves are noisy; count only
            note("REC", f"touch {e['action']:4} ({e['x']},{e['y']})")
    elif e["src"].startswith("mouse"):
        note("REC", f"{e['src']} {e.get('action', '')} "
                    f"{ {k: v for k, v in e.items() if k not in ('src', 'action')} }")


rec = KeyboardMouseCapture(rec_note, touch_mode=True)
rec.start()
mon = LiveInputMonitor()
mon.start()

print(f"hub window: {HUB._hwnd is not None} | subscribers: {len(HUB._subs)}"
      f" | recorder watcher: {rec._raw_gestures is not None}")
print(f"\n=== TAP NOW for {DURATION:.0f}s ===")
print("desktop · TASKBAR · CHROME tab bar · inside a page · one drag · "
      "one mouse click\n")

try:
    while time.monotonic() - t0 < DURATION:
        snap = mon.snapshot()
        for x, y in snap["touch_taps"]:
            stats["mon_tap"] += 1
            note("MON", f"TOUCH tap ({x},{y})")
        for b in snap["mouse_buttons"]:
            stats["mon_mouse"] += 1
            note("MON", f"MOUSE {b}  <-- BUG if that was a finger")
        time.sleep(1 / 60)
except KeyboardInterrupt:
    pass
finally:
    mon.stop()
    rec.stop()
    HUB.unsubscribe(on_hub)

print("\n================ SUMMARY ================")
print(f"  contacts seen by HUB (digitizer): {stats['hub']}")
print(f"  contacts RECORDED (touch down)  : {stats['rec_down']}")
print(f"  taps in activity log            : {stats['mon_tap']}")
print(f"  finger logged as MOUSE (bug)    : {stats['mon_mouse']}")
print(f"  contacts where cursor was stale : {stats['cursor_mismatch']}"
      "   <- these are the pointer-native taps HID rescued")
ok = (stats["hub"] > 0 and stats["rec_down"] == stats["hub"]
      and stats["mon_tap"] == stats["hub"] and stats["mon_mouse"] == 0)
print("\nRESULT:", "PASS — every contact captured once at every layer"
      if ok else "MISMATCH — counts above show which layer misses")
