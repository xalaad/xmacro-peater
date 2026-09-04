"""Render the README showcase screenshots into docs/screenshots/.

Stages live-looking state (connected pad, pressed keys, activity lines)
so the images show the app working, then grabs each view offscreen.

Run after UI changes:  python tools/make_screenshots.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from core.config import load_config
from core.controllers.base import neutral_state
from ui.main_window import MainWindow
from ui.settings_panel import SettingsDialog
from ui.theme import build_qss, get_theme

OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

cfg = load_config()
theme = get_theme()
app.setStyleSheet(build_qss(theme))

w = MainWindow(cfg)
w._toggle_right_panel(force_collapsed=False)
w.resize(1180, 700)

# Stage a live-looking scene
pad = neutral_state()
pad["buttons"] = {"A", "LEFT_SHOULDER"}
pad.update(lx=0.55, ly=0.35, rt=0.7)
w.controller_w.frame(pad, True)
w.stick_l.set_stick_pos(type(w.stick_l.get_stick_pos())(0.55, 0.35))
w.trigger_r.set_value(0.7)
w._update_conn_label(True) if w.backend else None
w.conn_label.setText("● connected — XInput slot 0")
w.conn_label.setStyleSheet(
    f"color: {theme.success}; font-family: Consolas, monospace;"
    "font-size: 11px;")
for text, color in (
    ("Ctrl+F9 record · Ctrl+F10 play · Ctrl+F11 stop", theme.text_dim),
    ("[test] Pad A", theme.pad),
    ("[test] Left stick top-right (0.65)", theme.pad),
    ("[test] Mouse left click", theme.mouse),
    ("Saved 428 events (22.0s) → rec_farm_loop.json", theme.success),
):
    w.activity.add_line(text, QColor(color))
# Let the fade-in animations of the activity rows finish before grabbing
import time
for _ in range(25):
    app.processEvents()
    time.sleep(0.02)
w.grab().save(str(OUT / "app-full.png"))
print("app-full.png")

w._toggle_right_panel(force_collapsed=True)
app.processEvents()
w.grab().save(str(OUT / "compact-deck.png"))
print("compact-deck.png")
w._toggle_right_panel(force_collapsed=False)

dlg = SettingsDialog(cfg)
dlg.resize(450, 560)
app.processEvents()
dlg.grab().save(str(OUT / "settings.png"))
print("settings.png")

o = w.overlay
o.set_state("Playing")
o.set_info("run 2/5")
o.set_last_line("▶ Pad A down")
o.loop_mode.setCurrentIndex(1)
o.loop_count.setValue(5)
o.adjustSize()
app.processEvents()
o.grab().save(str(OUT / "overlay.png"))
print("overlay.png")
o.set_state("Idle")

tw = w.tester_window
tw.resize(1500, 850)
snap = {"keys": {"char:w", "char:a", "key:kp_8"}, "key_pulses": ["char:w"],
        "mouse_buttons": {"left"}, "move": (28, 14), "scroll": 0,
        "pos": (800, 500)}
tw.set_conn_text("● connected — XInput slot 0", theme.success)
tw.feed(snap, pad, True)
app.processEvents()
tw.grab().save(str(OUT / "test-mode.png"))
print("test-mode.png")

w.close()
print("done")
