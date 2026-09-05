"""Status icon: Idle / Recording / Playing as glyph shapes with a glowing,
breathing halo while active — compact enough to live in the title bar.

Idle      → hollow ring (dim)
Recording → filled dot (danger red), pulsing glow
Playing   → play triangle (success green), pulsing glow
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..animations import animate_color
from ..theme import Theme

IDLE, RECORDING, PLAYING = "Idle", "Recording", "Playing"


class StatusPill(QWidget):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._state = IDLE
        self._color = QColor(theme.text_dim)
        self._pulse = 0.0
        self.setFixedSize(34, 34)
        self.setToolTip(IDLE)

        self._pulse_anim = QPropertyAnimation(self, b"pulse", self)
        self._pulse_anim.setDuration(1100)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setKeyValueAt(0.5, 1.0)
        self._pulse_anim.setEndValue(0.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse_anim.setLoopCount(-1)

    def _state_color(self, state: str) -> QColor:
        return QColor({
            RECORDING: self.theme.danger,
            PLAYING: self.theme.success,
        }.get(state, self.theme.text_dim))

    def set_state(self, state: str, force: bool = False) -> None:
        if state == self._state and not force:
            return
        self._state = state
        self.setToolTip(state)
        animate_color(self, QColor(self._color), self._state_color(state),
                      self._set_color, duration_ms=300)
        if state in (RECORDING, PLAYING):
            self._pulse_anim.start()
        else:
            self._pulse_anim.stop()
            self._pulse = 0.0
        self.update()

    def _set_color(self, c: QColor) -> None:
        self._color = c
        self.update()

    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    pulse = Property(float, get_pulse, set_pulse)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)

        # Breathing halo while active
        if self._state in (RECORDING, PLAYING):
            halo = QColor(self._color)
            halo.setAlphaF(0.20 + 0.25 * self._pulse)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            r = 11.0 + 4.0 * self._pulse
            p.drawEllipse(center, r, r)

        if self._state == RECORDING:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._color)
            p.drawEllipse(center, 6.5, 6.5)
        elif self._state == PLAYING:
            path = QPainterPath()
            path.moveTo(center.x() - 4.5, center.y() - 6.5)
            path.lineTo(center.x() + 6.5, center.y())
            path.lineTo(center.x() - 4.5, center.y() + 6.5)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._color)
            p.drawPath(path)
        else:  # Idle: hollow ring
            p.setPen(QPen(self._color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center, 5.5, 5.5)
