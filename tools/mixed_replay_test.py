"""Does a recording that contains TOUCH still move the MOUSE on replay?

    python tools/mixed_replay_test.py

Fully automatic (no finger needed): builds a macro that interleaves
touch contacts and mouse moves, replays it through the real engine, and
samples the cursor to see which stages actually moved it.

Stages: mouse -> touch tap -> mouse -> touch drag -> mouse
A pass means the cursor lands on every mouse target even though touch
was injected in between.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: F401  pins Per-Monitor-V2

from core.events import MacroEvent, MacroFile
from core.playback.engine import PlaybackEngine
from core.playback.touch import virtual_screen_rect

u = ctypes.windll.user32


def cursor() -> tuple[int, int]:
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


scr = virtual_screen_rect()
W, H = scr["w"], scr["h"]
start = cursor()

# Targets well apart so a miss is obvious
M1 = (int(W * 0.20), int(H * 0.25))
T1 = (int(W * 0.50), int(H * 0.50))
M2 = (int(W * 0.80), int(H * 0.25))
T2 = (int(W * 0.50), int(H * 0.75))
M3 = (int(W * 0.35), int(H * 0.60))

events = [
    MacroEvent(0.10, "mouse_abs", {"x": M1[0], "y": M1[1]}),
    MacroEvent(0.40, "touch", {"action": "down", "x": T1[0], "y": T1[1]}),
    MacroEvent(0.50, "touch", {"action": "up", "x": T1[0], "y": T1[1]}),
    MacroEvent(0.80, "mouse_abs", {"x": M2[0], "y": M2[1]}),
    MacroEvent(1.10, "touch", {"action": "down", "x": T2[0], "y": T2[1]}),
    MacroEvent(1.20, "touch", {"action": "move",
                               "x": T2[0] + 40, "y": T2[1] + 40}),
    MacroEvent(1.30, "touch", {"action": "up",
                               "x": T2[0] + 40, "y": T2[1] + 40}),
    MacroEvent(1.60, "mouse_abs", {"x": M3[0], "y": M3[1]}),
]
macro = MacroFile(events=events, screen=scr)

samples: list[tuple[float, tuple[int, int], str]] = []
t0 = time.perf_counter()


def on_event(ev) -> None:
    samples.append((time.perf_counter() - t0, cursor(),
                    f"{ev.src}:{ev.data.get('action', '')}"))


print(f"screen {W}x{H}   cursor starts at {start}")
print("targets: M1", M1, " T1", T1, " M2", M2, " T2", T2, " M3", M3)
print("\nreplaying mixed touch+mouse macro...\n")

from core.playback.engine import PlaybackCallbacks  # noqa: E402

eng = PlaybackEngine(macro, loop_count=1,
                     callbacks=PlaybackCallbacks(on_event=on_event))
eng.start()
eng.join(timeout=15)
time.sleep(0.2)

for t, pos, what in samples:
    print(f"  {t:5.2f}s after {what:16} cursor={pos}")

# Which mouse targets were actually reached?
reached = []
for name, target in (("M1", M1), ("M2", M2), ("M3", M3)):
    hit = any(abs(p[0] - target[0]) <= 2 and abs(p[1] - target[1]) <= 2
              for _t, p, _w in samples)
    reached.append((name, target, hit))
    print(f"  {name} {target}: {'REACHED' if hit else 'NOT reached'}")

final = cursor()
print(f"\nfinal cursor {final} (M3 was {M3})")
ok = all(h for _n, _t, h in reached)
print("RESULT:", "PASS - mouse moves fine alongside touch"
      if ok else "FAIL - touch injection is blocking mouse movement")
u.SetCursorPos(*start)
