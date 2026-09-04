"""Controller visualizer: SVG art base with animated overlays.

Each art file has its own overlay layout (button positions, stick centers,
trigger rects) in the SVG's 400x240 coordinate space, so Xbox keeps its
asymmetric sticks while PlayStation/generic pads get the symmetric-stick,
dpad-top-left layout that matches their hardware.

Repaints only when the visual state actually changed — at rest this widget
costs nothing even though the UI timer ticks at 60fps.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from core.config import BUNDLE_DIR

from ..theme import Theme

ASSETS_DIR = BUNDLE_DIR / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"

VIEW_W, VIEW_H = 400.0, 240.0
PULSE_DECAY = 0.08

_TRIGGERS = {"lt": QRectF(86, 8, 40, 20), "rt": QRectF(274, 8, 40, 20)}
# Shoulder pills are drawn slanted in the art; the fill overlay matches.
_TRIGGER_ANGLES = {"lt": -9.0, "rt": 9.0}

_XBOX = {
    "buttons": {
        "A": (285, 124, 13), "B": (311, 100, 13),
        "X": (259, 100, 13), "Y": (285, 76, 13),
        "BACK": (176, 104, 9), "START": (224, 104, 9),
        "LEFT_SHOULDER": (112, 42, 15), "RIGHT_SHOULDER": (288, 42, 15),
        "DPAD_UP": (150, 136, 9), "DPAD_DOWN": (150, 164, 9),
        "DPAD_LEFT": (136, 150, 9), "DPAD_RIGHT": (164, 150, 9),
        "LEFT_THUMB": (115, 100, 22), "RIGHT_THUMB": (250, 150, 22),
    },
    "sticks": {"left": (115.0, 100.0), "right": (250.0, 150.0)},
    "travel": 10.0,
    "triggers": _TRIGGERS,
}

_PS = {
    "buttons": {
        "A": (285, 125, 13), "B": (310, 100, 13),
        "X": (260, 100, 13), "Y": (285, 75, 13),
        "BACK": (153, 72, 8), "START": (247, 72, 8),
        "LEFT_SHOULDER": (112, 42, 15), "RIGHT_SHOULDER": (288, 42, 15),
        "DPAD_UP": (115, 81, 11), "DPAD_DOWN": (115, 119, 11),
        "DPAD_LEFT": (95, 100, 11), "DPAD_RIGHT": (135, 100, 11),
        "LEFT_THUMB": (162, 152, 19), "RIGHT_THUMB": (238, 152, 19),
    },
    "sticks": {"left": (162.0, 152.0), "right": (238.0, 152.0)},
    "travel": 9.0,
    "triggers": _TRIGGERS,
}

_GENERIC = {
    **_PS,
    "buttons": {
        **_PS["buttons"],
        "BACK": (183, 74, 8), "START": (217, 74, 8),
        "DPAD_UP": (115, 88, 9), "DPAD_DOWN": (115, 112, 9),
        "DPAD_LEFT": (103, 100, 9), "DPAD_RIGHT": (127, 100, 9),
    },
}

LAYOUTS = {"xbox": _XBOX, "ps": _PS, "generic": _GENERIC}


class ControllerWidget(QWidget):
    def __init__(self, theme: Theme, art: str = "controller_xbox.svg",
                 layout: str = "xbox", parent=None):
        super().__init__(parent)
        self.theme = theme
        self._renderer: QSvgRenderer | None = None
        self._layout = LAYOUTS["xbox"]
        self._pressed: set[str] = set()
        self._pulse: dict[str, float] = {}  # release afterglow per button
        self._state = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0,
                       "lt": 0.0, "rt": 0.0}
        self._connected = False
        self.setMinimumSize(320, 200)
        self.set_art(art, layout)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    def set_art(self, art_filename: str, layout: str = "") -> None:
        path = ASSETS_DIR / art_filename
        if not path.exists():
            path = ASSETS_DIR / "controller_generic.svg"
        self._renderer = QSvgRenderer(str(path), self)
        if not layout:
            stem = path.stem
            layout = ("xbox" if "xbox" in stem
                      else "ps" if "ps" in stem else "generic")
        self._layout = LAYOUTS.get(layout, _GENERIC)
        self.update()

    def frame(self, state: dict, connected: bool) -> None:
        """Advance one UI frame; repaint only if something visible changed."""
        buttons: set[str] = state["buttons"]
        dirty = connected != self._connected or buttons != self._pressed
        for name in self._pressed - buttons:  # just released -> afterglow
            self._pulse[name] = 0.8
        self._pressed = set(buttons)
        self._connected = connected
        if self._pulse:
            dirty = True
            for name in list(self._pulse):
                self._pulse[name] -= PULSE_DECAY
                if self._pulse[name] <= 0:
                    del self._pulse[name]
        for k in self._state:
            v = state[k]
            if abs(v - self._state[k]) > 0.004:
                self._state[k] = v
                dirty = True
        if dirty:
            self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / VIEW_W, self.height() / VIEW_H)
        ox = (self.width() - VIEW_W * scale) / 2
        oy = (self.height() - VIEW_H * scale) / 2
        target = QRectF(ox, oy, VIEW_W * scale, VIEW_H * scale)

        if self._renderer is not None and self._renderer.isValid():
            p.setOpacity(1.0 if self._connected else 0.4)
            self._renderer.render(p, target)
            p.setOpacity(1.0)

        def to_widget(x: float, y: float) -> QPointF:
            return QPointF(ox + x * scale, oy + y * scale)

        accent = QColor(self.theme.accent)
        layout = self._layout

        # Button glows: full for held, fading for just-released
        glows = {name: 1.0 for name in self._pressed}
        for name, v in self._pulse.items():
            glows.setdefault(name, v)
        p.setPen(Qt.PenStyle.NoPen)
        for name, intensity in glows.items():
            hit = layout["buttons"].get(name)
            if hit is None:
                continue
            cx, cy, r = hit
            center = to_widget(cx, cy)
            radius = r * scale * (1.2 + 0.3 * intensity)
            grad = QRadialGradient(center, radius)
            c0 = QColor(accent)
            c0.setAlphaF(0.8 * intensity)
            c1 = QColor(accent)
            c1.setAlphaF(0.0)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(1.0, c1)
            p.setBrush(grad)
            p.drawEllipse(center, radius, radius)

        # Stick caps: bright dot showing live deflection
        travel = layout["travel"]
        for stick, (sx, sy) in layout["sticks"].items():
            x = self._state["lx" if stick == "left" else "rx"]
            y = self._state["ly" if stick == "left" else "ry"]
            center = to_widget(sx + x * travel, sy - y * travel)
            active = abs(x) > 0.01 or abs(y) > 0.01
            color = QColor(self.theme.accent2)
            color.setAlphaF(0.9 if active else 0.30)
            p.setBrush(color)
            r = 4.5 * scale
            p.drawEllipse(center, r, r)

        # Trigger fill overlays (rotated to match the slanted pills)
        for key, rect in layout["triggers"].items():
            v = self._state[key]
            if v < 0.01:
                continue
            filled = QRectF(
                ox + rect.x() * scale,
                oy + (rect.y() + rect.height() * (1 - v)) * scale,
                rect.width() * scale,
                rect.height() * v * scale,
            )
            c = QColor(self.theme.accent2)
            c.setAlphaF(0.6)
            p.setBrush(c)
            angle = _TRIGGER_ANGLES.get(key, 0.0)
            p.save()
            pivot = to_widget(rect.center().x(), rect.center().y())
            p.translate(pivot)
            p.rotate(angle)
            p.translate(-pivot)
            p.drawRoundedRect(filled, 4 * scale, 4 * scale)
            p.restore()

        if not self._connected:
            p.setPen(QColor(self.theme.text_dim))
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No controller detected")
