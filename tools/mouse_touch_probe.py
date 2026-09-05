"""Why is the mouse (not) recorded while touch mode is on?

    python tools/mouse_touch_probe.py [seconds]

Do all of these while it runs:
  1. MOVE THE MOUSE, then LEFT-CLICK with it
  2. TAP the screen with a finger
  3. DRAG with a finger
  4. CLICK the mouse again

For every hook event it prints the raw dwExtraInfo Windows attached and
the verdict (TOUCH vs MOUSE), then every event the recorder actually
emitted. If real mouse input carries the touch signature, this is where
it shows up.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: F401  pins Per-Monitor-V2

from core.capture.keyboard_mouse import KeyboardMouseCapture

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
t0 = time.monotonic()
seen = {"mouse_move": 0, "mouse_btn": 0, "touch": 0, "mouse_scroll": 0}
_last_move_log = [0.0]

MSG = {0x0200: "MOUSEMOVE", 0x0201: "LBUTTONDOWN", 0x0202: "LBUTTONUP",
       0x0204: "RBUTTONDOWN", 0x0205: "RBUTTONUP"}


def note(kind: str, text: str) -> None:
    print(f"  {time.monotonic() - t0:6.2f}s  {kind:5} {text}", flush=True)


class Probe(KeyboardMouseCapture):
    def _win32_filter(self, msg, data):
        extra = getattr(data, "dwExtraInfo", None)
        out = super()._win32_filter(msg, data)
        if msg in MSG:
            quiet_move = msg == 0x0200
            now = time.monotonic()
            if quiet_move and now - _last_move_log[0] < 0.7:
                return out
            if quiet_move:
                _last_move_log[0] = now
            ev = self._evt_is_touch
            verdict = ("TOUCH (suppressed from mouse)" if ev is True
                       else "MOUSE (recorded)" if ev is False
                       else "UNKNOWN -> treated as mouse")
            raw = "None" if extra is None else hex(int(extra) & 0xFFFFFFFF)
            note("HOOK", f"{MSG[msg]:12} dwExtraInfo={raw:12} {verdict}")
        return out


def emitted(e: dict) -> None:
    src = e["src"]
    seen[src] = seen.get(src, 0) + 1
    if src == "mouse_move":
        if seen[src] % 40 == 1:
            note("EMIT", f"mouse_move  ({e['dx']:+},{e['dy']:+}) "
                         f"at ({e.get('px')},{e.get('py')})   "
                         f"[{seen[src]} so far]")
    elif src == "touch":
        if e["action"] != "move":
            note("EMIT", f"touch {e['action']:4} ({e['x']},{e['y']})")
    else:
        note("EMIT", f"{src} {e.get('action', '')} "
                     f"{ {k: v for k, v in e.items() if k not in ('src', 'action')} }")


cap = Probe(emitted, touch_mode=True)
cap.start()
print(f"touch_mode: {cap.touch_mode} | digitizer watcher: "
      f"{cap._raw_gestures is not None} | capture_moves: {cap.capture_moves}")
print(f"\n=== {DURATION:.0f}s: move+click the MOUSE, tap+drag with a FINGER ===\n")
try:
    time.sleep(DURATION)
except KeyboardInterrupt:
    pass
cap.stop()

print("\n================ WHAT GOT RECORDED ================")
for k, v in seen.items():
    print(f"  {k:14} {v}")
ok_mouse = seen.get("mouse_move", 0) > 0 or seen.get("mouse_btn", 0) > 0
ok_touch = seen.get("touch", 0) > 0
print(f"\n  mouse captured: {ok_mouse}   touch captured: {ok_touch}")
print("RESULT:", "PASS — both in one recording" if (ok_mouse and ok_touch)
      else "PROBLEM — see the HOOK verdicts above")
