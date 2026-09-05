"""Deck cards for recordings & sequences: a device icon chip, the name
(marquee when too long), a dim metadata line (duration · events / steps),
and inline rename/delete pinned right. Edit-in-place renaming.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import Theme

MARQUEE_GAP = 40      # px gap between the looping copies
MARQUEE_STEP = 1      # px per tick
MARQUEE_TICK_MS = 40
MARQUEE_PAUSE_MS = 1200

# MDL2 badges: what kind of input dominates the take
GLYPH_KB = ""
GLYPH_MOUSE = ""
GLYPH_PAD = ""
GLYPH_TOUCH = ""
GLYPH_REC = ""
GLYPH_SEQ = ""
GLYPH_BROKEN = ""


GLYPHS = {"kb": GLYPH_KB, "mouse": GLYPH_MOUSE, "pad": GLYPH_PAD,
          "touch": GLYPH_TOUCH, "rec": GLYPH_REC, "seq": GLYPH_SEQ,
          "broken": GLYPH_BROKEN}


def device_badge(kinds: tuple[str, ...], theme: Theme,
                 size: int = 22) -> QPixmap:
    """Composite device badge: one icon centered; two icons split by a
    diagonal; three icons in the sectors of a Y divider."""
    kinds = tuple(kinds) or ("rec",)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = QColor(theme.accent)
    divider = QColor(theme.accent)
    divider.setAlphaF(0.45)

    def glyph(kind: str, rect: QRectF, px: int) -> None:
        f = QFont("Segoe MDL2 Assets")
        f.setPixelSize(max(7, px))
        p.setFont(f)
        p.setPen(ink)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                   GLYPHS.get(kind, GLYPH_REC))

    s = float(size)
    if len(kinds) == 1:
        glyph(kinds[0], QRectF(0, 0, s, s), int(s * 0.60))
    elif len(kinds) == 2:
        # Diagonal split: first icon top-left, second bottom-right
        p.setPen(QPen(divider, 1.1))
        p.drawLine(QPointF(s * 0.18, s * 0.82),
                   QPointF(s * 0.82, s * 0.18))
        px = int(s * 0.42)
        glyph(kinds[0], QRectF(-s * 0.04, -s * 0.02, s * 0.58, s * 0.58),
              px)
        glyph(kinds[1], QRectF(s * 0.46, s * 0.44, s * 0.58, s * 0.58),
              px)
    else:
        # Y divider: two arms up, stem down. The icons live in the EMPTY
        # sectors between the lines: top-center, bottom-left, bottom-right
        c = QPointF(s / 2, s * 0.52)
        p.setPen(QPen(divider, 1.1))
        p.drawLine(c, QPointF(s * 0.10, s * 0.10))
        p.drawLine(c, QPointF(s * 0.90, s * 0.10))
        p.drawLine(c, QPointF(s / 2, s * 0.97))
        px = int(s * 0.34)
        glyph(kinds[0], QRectF(s * 0.25, -s * 0.02, s * 0.5, s * 0.42), px)
        glyph(kinds[1], QRectF(-s * 0.03, s * 0.52, s * 0.46, s * 0.46), px)
        glyph(kinds[2], QRectF(s * 0.57, s * 0.52, s * 0.46, s * 0.46), px)
    p.end()
    return pm


class MarqueeLabel(QWidget):
    """Single-line label that loops horizontally when the text overflows.
    Transparent to mouse events so clicks select the list row."""

    def __init__(self, text: str, theme: Theme, parent=None,
                 left_align: bool = False):
        super().__init__(parent)
        self.theme = theme
        self._text = text
        self._left = left_align
        self._offset = 0.0
        self._paused_until = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumWidth(40)
        self.setFixedHeight(20)
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
        elif self._left:
            p.drawText(0, y, self._text)
        else:
            p.drawText((self.width() - self._text_width()) // 2, y,
                       self._text)


class RecordingRow(QWidget):
    """Deck card: [icon chip] name + meta line, inline rename/delete."""

    rename_requested = Signal(str, str)  # old, new
    delete_requested = Signal(str)

    def __init__(self, name: str, theme: Theme, meta: str = "",
                 kinds: tuple[str, ...] = ("rec",), parent=None):
        super().__init__(parent)
        self.name = name  # full file name (with .json) — signals use this
        self.theme = theme
        self._display = name.removesuffix(".json")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(9)

        self.chip = QLabel()
        self.chip.setObjectName("cardChip")
        self.chip.setFixedSize(28, 28)
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chip.setPixmap(device_badge(kinds, theme, 22))
        lay.addWidget(self.chip)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._col = col
        self.label = MarqueeLabel(self._display, theme, left_align=True)
        col.addWidget(self.label)
        # The rename editor is created lazily on first use — building a
        # QLineEdit per card was ~40% of the deck-refresh cost
        self.editor: QLineEdit | None = None
        self.meta = QLabel(meta)
        self.meta.setObjectName("cardMeta")
        col.addWidget(self.meta)
        lay.addLayout(col, 1)
        # Only NOW is meta parented (widgets in an orphan QVBoxLayout stay
        # parentless until the layout joins a widget) — setVisible(True)
        # any earlier shows the label as its own flashing top-level window
        self.meta.setVisible(bool(meta))

        # Segoe MDL2 Assets glyphs: Edit pen and Delete
        for glyph_btn, tip, handler in (
            ("", "Rename", self.start_edit),
            ("", "Delete",
             lambda: self.delete_requested.emit(self.name)),
        ):
            btn = QPushButton(glyph_btn)
            btn.setObjectName("rowBtn")
            btn.setFixedSize(24, 24)
            btn.setToolTip(tip)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            lay.addWidget(btn)

    def start_edit(self) -> None:
        if self.editor is None:
            self.editor = QLineEdit(self)
            self.editor.editingFinished.connect(self._commit_edit)
            self._col.insertWidget(1, self.editor)
        # Edit the clean stem; .json is re-appended under the hood
        self.editor.setText(self._display)
        self.label.hide()
        self.meta.hide()
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()

    def _commit_edit(self) -> None:
        if self.editor is None or not self.editor.isVisible():
            return
        new = self.editor.text().strip()
        self.editor.hide()
        self.label.show()
        self.meta.setVisible(bool(self.meta.text()))
        if new and new != self._display:
            self.rename_requested.emit(self.name, new)


class SequenceRow(RecordingRow):
    """Sequence card: chain chip + a leading 'edit steps' button that
    opens the Sequence Builder."""

    edit_requested = Signal(str)

    def __init__(self, name: str, theme: Theme, meta: str = "",
                 parent=None):
        super().__init__(name, theme, meta, kinds=("seq",), parent=parent)
        btn = QPushButton("")  # MDL2 BulletedList
        btn.setObjectName("rowBtn")
        btn.setFixedSize(24, 24)
        btn.setToolTip("Edit steps")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.edit_requested.emit(self.name))
        # After [chip][name column]: steps-edit first, then rename/delete
        self.layout().insertWidget(2, btn)
