"""Touch precision loop-back diagnostic.

Run this ON THE MACHINE where taps land off the mark:

    python tools/touch_loopback.py

It opens a fullscreen catch window (so every injected contact is
guaranteed to be seen), reports DPI awareness and display scale, injects
touch contacts at known coordinates, captures them with the same hook
the recorder uses, and prints the offset for each. A healthy pipeline
shows offset=(0,0) everywhere. Closes itself when done.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as app_main  # noqa: F401  (pins Per-Monitor-V2 on import)
import ctypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

user32 = ctypes.windll.user32

app = QApplication(sys.argv)

ctx = user32.GetAwarenessFromDpiAwarenessContext(
    user32.GetThreadDpiAwarenessContext())
print(f"DPI awareness: {ctx}  (0=unaware, 1=system, 2=per-monitor <- good)")
w = user32.GetSystemMetrics(78)
h = user32.GetSystemMetrics(79)
print(f"virtual screen: {w}x{h} at "
      f"({user32.GetSystemMetrics(76)},{user32.GetSystemMetrics(77)})")
try:
    dpi = user32.GetDpiForSystem()
    print(f"system DPI: {dpi} ({dpi * 100 // 96}% scale)")
except Exception:
    pass
scr = app.primaryScreen()
print(f"Qt screen: {scr.size().width()}x{scr.size().height()} logical, "
      f"devicePixelRatio {scr.devicePixelRatio()}")

from pynput import mouse

from core.playback.touch import TouchInjector

# Fullscreen catch window: every injected contact hits THIS window, so
# Windows always promotes it to a mouse click our hook can capture —
# no other app can swallow the tap as pointer input.
catch = QLabel("XMacro-peater touch loop-back running…\n"
               "(closes automatically)")
catch.setAlignment(Qt.AlignmentFlag.AlignCenter)
catch.setStyleSheet("background:#0a0f0c; color:#3ddf7e; font-size:20px;")
catch.setWindowFlags(Qt.WindowType.FramelessWindowHint
                     | Qt.WindowType.WindowStaysOnTopHint)
catch.showFullScreen()

clicks: list[tuple[int, int, bool]] = []
listener = mouse.Listener(
    on_click=lambda x, y, b, p: clicks.append((int(x), int(y), p)))
listener.start()

targets = [(w // 2, h // 2), (w // 4, h // 4),
           (3 * w // 4, 3 * h // 4), (60, h - 60)]
state = {"i": 0, "inj": None}


def step():
    if state["inj"] is None:
        state["inj"] = TouchInjector()
    i = state["i"]
    if i < len(targets):
        tx, ty = targets[i]
        state["inj"].down(tx, ty)
        QTimer.singleShot(60, lambda: state["inj"].up(tx, ty))
        state["i"] += 1
        QTimer.singleShot(300, step)
    else:
        QTimer.singleShot(400, finish)


def finish():
    listener.stop()
    catch.close()
    downs = [(x, y) for x, y, pressed in clicks if pressed]
    print(f"\ninjected -> captured  "
          f"({len(downs)}/{len(targets)} contacts seen)")
    ok = len(downs) == len(targets)
    used = set()
    for tx, ty in targets:
        best = None
        for j, (cx, cy) in enumerate(downs):
            if j in used:
                continue
            d = abs(cx - tx) + abs(cy - ty)
            if best is None or d < best[0]:
                best = (d, j, cx, cy)
        if best is None:
            print(f"  ({tx:5},{ty:5}) -> MISSED")
            ok = False
            continue
        _, j, cx, cy = best
        used.add(j)
        dx, dy = cx - tx, cy - ty
        flag = "OK" if (dx, dy) == (0, 0) else "OFF"
        ok = ok and flag == "OK"
        print(f"  ({tx:5},{ty:5}) -> ({cx:5},{cy:5})   "
              f"offset=({dx:+},{dy:+})  {flag}")
    print("\nRESULT:", "pipeline is PIXEL-EXACT on this machine" if ok
          else "PROBLEM DETECTED - send this whole output back")
    app.quit()


QTimer.singleShot(600, step)
QTimer.singleShot(15000, app.quit)  # safety
sys.exit(app.exec())
