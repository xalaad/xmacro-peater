"""Mouse visualizer: a circular stage filling the available space with a
realistic mouse — curved shell, split buttons with a seam, wheel slot, and
side buttons. Left press fills in the primary accent, right in the
secondary; motion pushes a glow around the stage in the movement direction.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from ..theme import Theme


class MouseWidget(QWidget):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._buttons: set[str] = set()
        self._motion = 0.0        # 0..1 smoothed motion energy
        self._motion_dir = 0.0    # radians
        self._scroll_flash = 0.0  # decays each frame
        self._scroll_dir = 0
        self.setMinimumSize(180, 180)

    def frame(self, buttons: set[str], move: tuple[int, int], scroll: int) -> None:
        dirty = buttons != self._buttons or bool(scroll)
        self._buttons = buttons
        dx, dy = move
        speed = min(math.hypot(dx, dy) / 60.0, 1.0)
        self._motion = max(speed, self._motion * 0.82)
        if self._motion > 0.01:
            dirty = True
        elif self._motion:
            self._motion = 0.0
            dirty = True
        if speed > 0.02:
            self._motion_dir = math.atan2(dy, dx)
        if scroll:
            self._scroll_flash = 1.0
            self._scroll_dir = 1 if scroll > 0 else -1
        elif self._scroll_flash > 0.01:
            self._scroll_flash *= 0.85
            dirty = True
        if dirty:
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 4  # circular stage radius
        primary = QColor(self.theme.accent)
        secondary = QColor(self.theme.accent2)

        # --- circular stage
        stage = QRadialGradient(cx, cy - R * 0.15, R * 1.25)
        stage.setColorAt(0.0, QColor(self.theme.surface2))
        stage.setColorAt(0.8, QColor(self.theme.surface))
        stage.setColorAt(1.0, QColor(self.theme.bg))
        p.setPen(QPen(QColor(self.theme.border), 1.2))
        p.setBrush(stage)
        p.drawEllipse(QPointF(cx, cy), R, R)

        stage_clip = QPainterPath()
        stage_clip.addEllipse(QPointF(cx, cy), R - 1, R - 1)
        p.setClipPath(stage_clip)

        # --- directional motion glow, hugging the stage rim
        if self._motion > 0.03:
            gx = cx + math.cos(self._motion_dir) * R * 0.55
            gy = cy + math.sin(self._motion_dir) * R * 0.55
            glow = QRadialGradient(gx, gy, R * 0.75)
            halo = QColor(primary)
            halo.setAlphaF(0.35 * self._motion)
            glow.setColorAt(0.0, halo)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(gx, gy), R * 0.75, R * 0.75)

        # --- mouse body: tall rounded shell
        mw = R * 0.78
        mh = R * 1.34
        bx, by = cx - mw / 2, cy - mh / 2
        body = QPainterPath()
        body.addRoundedRect(QRectF(bx, by, mw, mh), mw * 0.48, mw * 0.42)

        shell = QLinearGradient(bx, by, bx + mw, by + mh)
        shell.setColorAt(0.0, QColor(self.theme.surface2).lighter(130))
        shell.setColorAt(0.5, QColor(self.theme.surface2))
        shell.setColorAt(1.0, QColor(self.theme.bg))
        p.setPen(QPen(QColor(self.theme.border).lighter(120), 1.4))
        p.setBrush(shell)
        p.drawPath(body)

        # --- buttons zone: top 40%, pressed halves fill accent colors
        seam_y = by + mh * 0.40
        for name, left_side, color in (("left", True, primary),
                                       ("right", False, secondary)):
            if name not in self._buttons:
                continue
            clip = QPainterPath()
            clip.addRect(QRectF(bx if left_side else cx, by, mw / 2,
                                mh * 0.40))
            region = body.intersected(clip)
            fill = QColor(color)
            fill.setAlphaF(0.8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawPath(region)

        # button seams
        p.setPen(QPen(QColor(self.theme.bg), 1.6))
        p.drawLine(QPointF(cx, by + 2), QPointF(cx, seam_y))
        seam = QPainterPath()
        seam.moveTo(bx + 2, seam_y)
        seam.quadTo(cx, seam_y + mh * 0.05, bx + mw - 2, seam_y)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(seam)

        # --- wheel slot
        wheel_w, wheel_h = mw * 0.14, mh * 0.19
        wheel = QRectF(cx - wheel_w / 2, by + mh * 0.13, wheel_w, wheel_h)
        p.setPen(QPen(QColor(self.theme.bg), 2))
        p.setBrush(QColor(primary) if ("middle" in self._buttons
                                       or self._scroll_flash > 0.05)
                   else QColor(self.theme.border))
        p.drawRoundedRect(wheel, wheel_w / 2, wheel_w / 2)
        # wheel ridges
        p.setPen(QPen(QColor(self.theme.bg), 1))
        for i in range(1, 4):
            ry = wheel.top() + wheel_h * i / 4
            p.drawLine(QPointF(wheel.left() + 1.5, ry),
                       QPointF(wheel.right() - 1.5, ry))

        # --- side buttons (X1/X2) on the left flank
        for i, name in enumerate(("x2", "x1")):
            r = QRectF(bx - mw * 0.055, by + mh * (0.34 + 0.14 * i),
                       mw * 0.11, mh * 0.11)
            p.setPen(QPen(QColor(self.theme.border), 1))
            p.setBrush(QColor(secondary) if name in self._buttons
                       else QColor(self.theme.surface2))
            p.drawRoundedRect(r, 3, 3)

        # --- scroll direction flash
        if self._scroll_flash > 0.05:
            arrow = QColor(primary)
            arrow.setAlphaF(self._scroll_flash)
            p.setPen(QPen(arrow, 2))
            ay = wheel.top() - 8 if self._scroll_dir > 0 else wheel.bottom() + 8
            tip = -5 if self._scroll_dir > 0 else 5
            p.drawLine(QPointF(cx - 5, ay), QPointF(cx, ay + tip))
            p.drawLine(QPointF(cx + 5, ay), QPointF(cx, ay + tip))

        # top-left highlight for depth
        p.setPen(Qt.PenStyle.NoPen)
        hl = QColor("white")
        hl.setAlphaF(0.05)
        p.setBrush(hl)
        p.drawEllipse(QRectF(bx + mw * 0.1, by + mh * 0.04,
                             mw * 0.5, mh * 0.18))
