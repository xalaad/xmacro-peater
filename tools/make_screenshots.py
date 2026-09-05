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

# Sequence builder, staged with a plausible chain
from core.config import RECORDINGS_DIR, SEQUENCES_DIR
from core.sequence import Sequence, SequenceStep
from ui.widgets.sequence_builder import SequenceBuilder

recs = sorted(p.name for p in RECORDINGS_DIR.glob("rec_*.json"))
if len(recs) >= 3:
    SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)
    staged = SEQUENCES_DIR / "_showcase.json"
    Sequence(steps=[
        SequenceStep(recs[0], runs=3, wait=2.0),
        SequenceStep(recs[1], runs=1, wait=0.0),
        SequenceStep(recs[2], runs=2, wait=90.0),
    ]).save(staged)
    dlg2 = SequenceBuilder(theme, w, existing="_showcase.json")
    dlg2.name_edit.setText("farm_cycle")
    app.processEvents()
    dlg2.grab().save(str(OUT / "sequence-builder.png"))
    print("sequence-builder.png")
    dlg2.reject()
    staged.unlink(missing_ok=True)
else:
    print("sequence-builder.png SKIPPED (needs 3+ recordings)")

dlg = SettingsDialog(cfg)
dlg.resize(450, 560)
app.processEvents()
dlg.grab().save(str(OUT / "settings.png"))
print("settings.png")

o = w.overlay
w._sync_overlay_targets()
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

# --- v1.1 feature shots ---------------------------------------------
from PySide6.QtCore import QPoint, QRect

from core.config import SEQUENCES_DIR

# Stage two showcase sequences (only if the names are free; cleaned up)
made = []
recs2 = sorted(p.name for p in RECORDINGS_DIR.glob("rec_*.json"))
if len(recs2) >= 2:
    for name, steps in (
        ("farm_cycle.json", [SequenceStep(recs2[0], runs=3, wait=2.0),
                             SequenceStep(recs2[-1], runs=1)]),
        ("daily_login.json", [SequenceStep(recs2[-1], runs=1, wait=5.0),
                              SequenceStep(recs2[0], runs=10, wait=1.0)]),
    ):
        pth = SEQUENCES_DIR / name
        if not pth.exists():
            Sequence(steps=steps).save(pth)
            made.append(pth)

# Sequences deck (compact sidebar, SEQUENCES tab active)
w._toggle_right_panel(force_collapsed=True)
w._set_deck_mode("seq")
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
w.grab().save(str(OUT / "sequences-deck.png"))
print("sequences-deck.png")
w._set_deck_mode("rec")

# Smart duration field with its h/m/s panel open (screen-region grab —
# the panel is its own window, so a widget grab can't see both)
w.show()
w.move(60, 60)
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
w.start_delay.setValue(9000)
w.start_delay._open_popup()
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
field_tl = w.start_delay.mapToGlobal(QPoint(0, -26))
pop = w.start_delay._popup.frameGeometry()
region = QRect(field_tl, pop.bottomRight()).adjusted(-10, -6, 12, 10)
app.primaryScreen().grabWindow(
    0, region.x(), region.y(), region.width(), region.height()
).save(str(OUT / "duration-panel.png"))
print("duration-panel.png")
w.start_delay._popup.hide()
w.start_delay.setValue(3)
w.hide()
w._toggle_right_panel(force_collapsed=False)

# Overlay target picker, open and grouped (auto-scrolls to top)
w._sync_overlay_targets()
o.show()
o.target.showPopup()
for _ in range(25):
    app.processEvents()
    time.sleep(0.02)
o.target.view().scrollToTop()
for _ in range(10):
    app.processEvents()
    time.sleep(0.02)
o.target.view().window().grab().save(str(OUT / "overlay-picker.png"))
print("overlay-picker.png")
o.target.hidePopup()
o.hide()

# Record-countdown ring, mid-tick
cd = w.rec_countdown
cd.start(3.0)
time.sleep(0.55)
app.processEvents()
cd.grab().save(str(OUT / "countdown.png"))
print("countdown.png")
cd.stop()

# The side drawer's half-capsule edge tab
w.dock_tab._side = "right"
w.dock_tab.grab().save(str(OUT / "drawer-tab.png"))
print("drawer-tab.png")

for pth in made:
    pth.unlink(missing_ok=True)

w.close()
print("done")
