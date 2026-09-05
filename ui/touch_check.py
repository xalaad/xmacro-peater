"""Self-diagnosing touch precision check (shared by the exe's
--touch-check flag and tools/touch_loopback.py).

Opens a fullscreen catch window, injects touch contacts at known
coordinates, captures them with the recorder's own hook, and reports the
offset per contact. Pixel-exact pipeline => offset=(0,0) everywhere.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel


def run_touch_check(app, on_done) -> None:
    """Async: calls on_done(report_text, ok) on the Qt event loop."""
    if sys.platform != "win32":
        on_done("Touch check is Windows-only.", False)
        return
    import ctypes

    from pynput import mouse

    from core.playback.touch import TouchInjector

    user32 = ctypes.windll.user32
    lines: list[str] = []
    ctx = user32.GetAwarenessFromDpiAwarenessContext(
        user32.GetThreadDpiAwarenessContext())
    lines.append(f"DPI awareness: {ctx} "
                 "(0=unaware, 1=system, 2=per-monitor <- good)")
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    lines.append(f"virtual screen: {w}x{h} at "
                 f"({user32.GetSystemMetrics(76)},"
                 f"{user32.GetSystemMetrics(77)})")
    try:
        dpi = user32.GetDpiForSystem()
        lines.append(f"system DPI: {dpi} ({dpi * 100 // 96}% scale)")
    except Exception:
        pass
    scr = app.primaryScreen()
    lines.append(f"Qt screen: {scr.size().width()}x"
                 f"{scr.size().height()} logical, "
                 f"devicePixelRatio {scr.devicePixelRatio()}")

    catch = QLabel("Touch precision check running…")
    catch.setAlignment(Qt.AlignmentFlag.AlignCenter)
    catch.setStyleSheet(
        "background:#0a0f0c; color:#3ddf7e; font-size:20px;")
    catch.setWindowFlags(Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
    catch.showFullScreen()

    clicks: list[tuple[int, int, bool]] = []
    listener = mouse.Listener(
        on_click=lambda x, y, b, p: clicks.append((int(x), int(y), p)))
    listener.start()

    targets = [(w // 2, h // 2), (w // 4, h // 4),
               (3 * w // 4, 3 * h // 4), (60, h - 60)]
    state: dict = {"i": 0, "inj": None}

    def step():
        try:
            if state["inj"] is None:
                state["inj"] = TouchInjector()
        except RuntimeError as e:
            listener.stop()
            catch.close()
            on_done("\n".join(lines)
                    + f"\n\nTouch injection unavailable: {e}", False)
            return
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
        lines.append("")
        lines.append(f"injected -> captured  "
                     f"({len(downs)}/{len(targets)} contacts seen)")
        ok = len(downs) == len(targets)
        used: set[int] = set()
        for tx, ty in targets:
            best = None
            for j, (cx, cy) in enumerate(downs):
                if j in used:
                    continue
                d = abs(cx - tx) + abs(cy - ty)
                if best is None or d < best[0]:
                    best = (d, j, cx, cy)
            if best is None:
                lines.append(f"  ({tx},{ty}) -> MISSED")
                ok = False
                continue
            _, j, cx, cy = best
            used.add(j)
            dx, dy = cx - tx, cy - ty
            flag = "OK" if (dx, dy) == (0, 0) else "OFF"
            ok = ok and flag == "OK"
            lines.append(f"  ({tx},{ty}) -> ({cx},{cy})   "
                         f"offset=({dx:+},{dy:+})  {flag}")
        lines.append("")
        lines.append("RESULT: pipeline is PIXEL-EXACT on this machine"
                     if ok else
                     "RESULT: PROBLEM DETECTED - report this output")
        on_done("\n".join(lines), ok)

    QTimer.singleShot(600, step)
