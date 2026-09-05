"""Analog trigger bar: vertical gradient fill that eases to each new value."""
from __future__ import annotations

from PySide6.QtCore import Property, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..animations import animate_property
from ..theme import Theme


class TriggerBar(QWidget):
    def __init__(self, theme: Theme, label: str = "LT", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.label = label
        self._value = 0.0
        self.setMinimumSize(28, 60)

    def get_value(self) -> float:
        return self._value

    def set_value(self, v: float) -> None:
        self._value = max(0.0, min(1.0, v))
        self.update()

    value = Property(float, get_value, set_value)

    def set_target(self, v: float) -> None:
        if abs(v - self._value) < 0.004:
            return
        animate_property(self, b"value", v, duration_ms=60)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar = self.rect().adjusted(int(w * 0.28), 4, -int(w * 0.28), -18)

        p.setPen(QPen(QColor(self.theme.border), 1))
        p.setBrush(QColor(self.theme.surface2))
        p.drawRoundedRect(bar, 5, 5)

        if self._value > 0.005:
            fill_h = int(bar.height() * self._value)
            fill = bar.adjusted(1, bar.height() - fill_h + 1, -1, -1)
            grad = QLinearGradient(0, bar.bottom(), 0, bar.top())
            grad.setColorAt(0.0, QColor(self.theme.accent))
            grad.setColorAt(1.0, QColor(self.theme.accent2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(fill, 4, 4)

        p.setPen(QColor(self.theme.text_dim))
        f = p.font()
        f.setBold(True)
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(self.rect().adjusted(0, 0, 0, -2),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                   self.label)
