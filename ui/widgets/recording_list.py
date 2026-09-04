"""Recording list rows: marquee name label (loops when too long) with
always-visible inline rename/delete buttons pinned to the right edge.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ..theme import Theme

MARQUEE_GAP = 40      # px gap between the looping copies
MARQUEE_STEP = 1      # px per tick
MARQUEE_TICK_MS = 40
MARQUEE_PAUSE_MS = 1200


class MarqueeLabel(QWidget):
    """Single-line label that loops horizontally when the text overflows.
    Transparent to mouse events so clicks select the list row."""

    def __init__(self, text: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._text = text
        self._offset = 0.0
        self._paused_until = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumWidth(40)
        self.setFixedHeight(22)
        font = self.font()
        font.setFamily("Consolas")
        font.setPixelSize(12)
        self.setFont(font)
        self._timer = QTimer(self)
        self._timer.setInterval(MARQUEE_TICK_MS)
        self._timer.timeout.connect(self._advance)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._offset = 0.0
        self._sync_timer()
        self.update()

    def _text_width(self) -> int:
        return self.fontMetrics().horizontalAdvance(self._text)

    def _overflows(self) -> bool:
        # Small hysteresis so a borderline fit stays static instead of
        # scrolling for the sake of a few clipped pixels.
        return self._text_width() > self.width() + 6

    def _sync_timer(self) -> None:
        if self._overflows() and self.isVisible():
            if not self._timer.isActive():
                self._pause_ticks = MARQUEE_PAUSE_MS // MARQUEE_TICK_MS
                self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0.0

    def _advance(self) -> None:
        if self._pause_ticks > 0:
            self._pause_ticks -= 1
            return
        self._offset += MARQUEE_STEP
        loop_span = self._text_width() + MARQUEE_GAP
        if self._offset >= loop_span:
            self._offset = 0.0
            self._pause_ticks = MARQUEE_PAUSE_MS // MARQUEE_TICK_MS
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_timer()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setClipRect(self.rect())
        p.setPen(QColor(self.theme.text))
        y = int((self.height() + self.fontMetrics().ascent()
                 - self.fontMetrics().descent()) / 2)
        if self._overflows():
            x = int(-self._offset)
            span = self._text_width() + MARQUEE_GAP
            p.drawText(x, y, self._text)
            p.drawText(x + span, y, self._text)
        else:
            # Fits: center horizontally for a balanced card look
            p.drawText((self.width() - self._text_width()) // 2, y,
                       self._text)


class RecordingRow(QWidget):
    """Row widget: marquee name + inline rename/delete, edit-in-place."""

    rename_requested = Signal(str, str)  # old, new
    delete_requested = Signal(str)

    def __init__(self, name: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.name = name
        self.theme = theme

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(6)

        self.label = MarqueeLabel(name, theme)
        lay.addWidget(self.label, 1)

        self.editor = QLineEdit()
        self.editor.hide()
        self.editor.editingFinished.connect(self._commit_edit)
        lay.insertWidget(1, self.editor, 1)

        # Segoe MDL2 Assets glyphs: Edit pen and Delete
        for glyph, tip, handler in (
            ("", "Rename", self.start_edit),
            ("", "Delete", lambda: self.delete_requested.emit(self.name)),
        ):
            btn = QPushButton(glyph)
            btn.setObjectName("rowBtn")
            btn.setFixedSize(24, 24)
            btn.setToolTip(tip)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            lay.addWidget(btn)

    def start_edit(self) -> None:
        self.editor.setText(self.name)
        self.label.hide()
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()

    def _commit_edit(self) -> None:
        if not self.editor.isVisible():
            return
        new = self.editor.text().strip()
        self.editor.hide()
        self.label.show()
        if new and new != self.name:
            self.rename_requested.emit(self.name, new)
