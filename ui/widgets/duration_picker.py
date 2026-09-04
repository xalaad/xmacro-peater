"""Duration picker: one seconds field by default, expandable to
hours / minutes / seconds for long schedules — same themed spin styling
as everything else. value() is always total seconds (float).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QWidget,
)

MAX_COLLAPSED_S = 35999  # generous single-field ceiling (~10h)


class DurationPicker(QWidget):
    valueChanged = Signal(float)

    def __init__(self, decimals: int = 2, parent=None):
        super().__init__(parent)
        self._updating = False

        lay = QHBoxLayout(self)
        # 1px side padding so the outer spin borders never clip against
        # the container edge
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(6)

        # No fixed widths: the three fields share the row EQUALLY when
        # expanded, and the seconds field spans it alone when collapsed.
        self.h = QSpinBox()
        self.h.setRange(0, 99)
        self.h.setSuffix(" h")
        self.m = QSpinBox()
        self.m.setRange(0, 59)
        self.m.setSuffix(" m")
        self.s = QDoubleSpinBox()
        self.s.setDecimals(decimals)
        self.s.setRange(0, MAX_COLLAPSED_S)
        self.s.setSuffix(" s")
        for box in (self.h, self.m, self.s):
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.setMinimumWidth(0)

        # MDL2 'Recent' clock glyph toggles the h/m fields
        self.expand_btn = QPushButton("")
        self.expand_btn.setObjectName("rowBtn")
        self.expand_btn.setFixedSize(22, 22)
        self.expand_btn.setToolTip(
            "Collapse to a single seconds field / expand back to "
            "hours-minutes-seconds")
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.expand_btn.clicked.connect(self.toggle_expanded)

        lay.addWidget(self.expand_btn)
        lay.addWidget(self.h, 1)
        lay.addWidget(self.m, 1)
        lay.addWidget(self.s, 1)

        # Full h/m/s visible by default — no hidden affordance to discover
        self._expanded = False
        self.set_expanded(True)

        for box in (self.h, self.m, self.s):
            box.valueChanged.connect(self._on_change)

    # ------------------------------------------------------------------
    def value(self) -> float:
        if self._expanded:
            return self.h.value() * 3600 + self.m.value() * 60 + self.s.value()
        return self.s.value()

    def setValue(self, seconds: float) -> None:
        old = self.value()
        self._updating = True
        try:
            seconds = max(0.0, float(seconds))
            if self._expanded or seconds > MAX_COLLAPSED_S:
                if not self._expanded:
                    self.set_expanded(True)
                self.h.setValue(int(seconds // 3600))
                self.m.setValue(int(seconds % 3600 // 60))
                self.s.setValue(round(seconds % 60, 2))
            else:
                self.s.setValue(round(seconds, 2))
        finally:
            self._updating = False
        # QSpinBox semantics: programmatic setValue emits too (unless
        # signals are blocked), so connected slots always stay in sync
        if abs(self.value() - old) > 1e-9 and not self.signalsBlocked():
            self.valueChanged.emit(self.value())

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        total = self.value() if hasattr(self, "_expanded") else 0.0
        self._expanded = expanded
        self._updating = True
        try:
            self.h.setVisible(expanded)
            self.m.setVisible(expanded)
            if expanded:
                self.s.setRange(0, 59.99)
                self.h.setValue(int(total // 3600))
                self.m.setValue(int(total % 3600 // 60))
                self.s.setValue(round(total % 60, 2))
            else:
                self.s.setRange(0, MAX_COLLAPSED_S)
                self.s.setValue(round(total, 2))
        finally:
            self._updating = False

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_change(self, *_) -> None:
        if not self._updating and not self.signalsBlocked():
            self.valueChanged.emit(self.value())


def format_duration(seconds: float) -> str:
    """Humanize: 5405 -> '1h 30m 5s'; 4 -> '4s'."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)
