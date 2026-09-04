"""Animated status pill: Idle / Recording / Playing with a glowing dot.

Color cross-fades between states; while Recording or Playing the glow
breathes with a looping pulse animation.
"""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPen
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
        self.setFixedSize(132, 34)

        self._pulse_anim = QPropertyAnimation(self, b"pulse", self)
        self._pulse_anim.setDuration(1100)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setKeyValueAt(0.5, 1.0)
        self._pulse_anim.setEndValue(0.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse_anim.setLoopCount(-1)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.set_state(self._state, force=True)

    def _state_color(self, state: str) -> QColor:
        return QColor({
            RECORDING: self.theme.danger,
            PLAYING: self.theme.success,
        }.get(state, self.theme.text_dim))

    def set_state(self, state: str, force: bool = False) -> None:
        if state == self._state and not force:
            return
        self._state = state
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
        rect = self.rect().adjusted(1, 1, -1, -1)

        bg = QColor(self.theme.surface2)
        p.setPen(QPen(QColor(self.theme.border), 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        # Glowing dot: pulse widens a translucent halo, perfectly
        # concentric with the dot (float-precision centers, no int snap)
        center = QPointF(20.0, self.height() / 2.0)
        halo = QColor(self._color)
        halo.setAlphaF(0.25 + 0.3 * self._pulse)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(center, 6.5 + 2.5 * self._pulse, 6.5 + 2.5 * self._pulse)
        p.setBrush(self._color)
        p.drawEllipse(center, 4.0, 4.0)

        p.setPen(QColor(self.theme.text))
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect.adjusted(34, 0, -8, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._state)
