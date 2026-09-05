"""Analog stick visualizer: radial-gradient track, deadzone ring, and a
glowing dot that eases (~80ms OutCubic) to each new position instead of
snapping."""
from __future__ import annotations

from PySide6.QtCore import Property, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..animations import animate_property
from ..theme import Theme


class StickWidget(QWidget):
    def __init__(self, theme: Theme, label: str = "L", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.label = label
        self._pos = QPointF(0.0, 0.0)   # logical -1..1, Y up
        self._pressed = False           # stick click (L3/R3)
        self._deadzone = 0.08
        # Scales within sensible bounds: floors at 56px on small windows,
        # grows with the window, but caps at 190px so big screens don't
        # blow the scopes up (the circle paints from min(w, h)).
        self.setMinimumSize(56, 56)
        self.setMaximumSize(190, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(150, 150)

    def set_deadzone(self, dz: float) -> None:
        self._deadzone = dz
        self.update()

    def get_stick_pos(self) -> QPointF:
        return self._pos

    def set_stick_pos(self, pos: QPointF) -> None:
        self._pos = pos
        self.update()

    stick_pos = Property(QPointF, get_stick_pos, set_stick_pos)

    def set_target(self, x: float, y: float, pressed: bool = False) -> None:
        """Ease the dot toward (x, y) in -1..1 stick space."""
        if pressed != self._pressed:
            self._pressed = pressed
        target = QPointF(x, y)
        if (target - self._pos).manhattanLength() < 0.002:
            return
        animate_property(self, b"stick_pos", target, duration_ms=80)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_h = 15  # reserved strip so the label sits right under the rim
        avail_h = self.height() - label_h
        side = min(self.width(), avail_h) - 8
        cx, cy = self.width() / 2, avail_h / 2
        radius = side / 2

        # Track: subtle radial gradient bowl
        grad = QRadialGradient(cx, cy, radius)
        grad.setColorAt(0.0, QColor(self.theme.surface2))
        grad.setColorAt(0.85, QColor(self.theme.surface))
        grad.setColorAt(1.0, QColor(self.theme.border))
        p.setPen(QPen(QColor(self.theme.border), 1.5))
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Deadzone ring + crosshair
        p.setPen(QPen(QColor(self.theme.border), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), radius * self._deadzone, radius * self._deadzone)
        p.drawLine(int(cx - radius), int(cy), int(cx + radius), int(cy))
        p.drawLine(int(cx), int(cy - radius), int(cx), int(cy + radius))

        # Dot with soft glow (logical Y up -> screen Y down)
        dot_r = max(6.0, radius * 0.13)
        dx = cx + self._pos.x() * (radius - dot_r)
        dy = cy - self._pos.y() * (radius - dot_r)
        active = self._pos.manhattanLength() > 0.01 or self._pressed
        color = QColor(self.theme.accent2 if not self._pressed else self.theme.warning)

        glow = QRadialGradient(dx, dy, dot_r * 2.6)
        halo = QColor(color)
        halo.setAlphaF(0.35 if active else 0.15)
        glow.setColorAt(0.0, halo)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(dx, dy), dot_r * 2.6, dot_r * 2.6)

        core = QRadialGradient(dx - dot_r * 0.3, dy - dot_r * 0.3, dot_r * 2)
        core.setColorAt(0.0, color.lighter(140))
        core.setColorAt(1.0, color)
        p.setBrush(core)
        p.drawEllipse(QPointF(dx, dy), dot_r, dot_r)

        # Label: hugs the circle's bottom rim, not the widget's bottom edge
        p.setPen(QColor(self.theme.text_dim))
        f = p.font()
        f.setBold(True)
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(QRectF(0, cy + radius + 1, self.width(), label_h),
                   Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                   self.label)
