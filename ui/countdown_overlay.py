"""Record countdown: a click-through, always-on-top ring in the middle of
the active screen. Purely a heads-up — every click and keystroke passes
straight through to whatever is underneath; it never takes focus.

Animation: the outer ring depletes smoothly over the whole countdown,
the big number pops (scale + fade-in) at each new second, and a soft
"REC IN" caption sits underneath. Emits ticked(remaining) once per
second (the window plays the ticking sound off it) and finished() when
the countdown lands.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .theme import Theme

SIZE = 220
RING_W = 7


class RecordCountdown(QWidget):
    finished = Signal()
    ticked = Signal(int)  # remaining whole seconds, once per second

    def __init__(self, theme: Theme, parent=None):
        super().__init__(None,
                         Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(SIZE, SIZE)
        self._total = 1.0
        self._t0 = 0.0
        self._last_whole: int | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._frame)

    # ------------------------------------------------------------------
    def start(self, seconds: float) -> None:
        self._total = max(0.05, float(seconds))
        self._t0 = time.monotonic()
        self._last_whole = None
        # Center on the screen the cursor is on — that's where the user
        # is about to perform the actions being recorded.
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        geo = screen.geometry()
        self.move(geo.center().x() - SIZE // 2,
                  geo.center().y() - SIZE // 2)
        self.show()
        self.raise_()
        self._frame()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    # ------------------------------------------------------------------
    def _remaining(self) -> float:
        return self._total - (time.monotonic() - self._t0)

    def _frame(self) -> None:
        remaining = self._remaining()
        if remaining <= 0:
            self.stop()
            self.finished.emit()
            return
        whole = math.ceil(remaining)
        if whole != self._last_whole:
            self._last_whole = whole
            self.ticked.emit(whole)
        self.update()

    def paintEvent(self, event) -> None:
        remaining = max(0.0, self._remaining())
        whole = max(1, math.ceil(remaining))
        # 0 → just ticked, 1 → about to tick again
        second_age = 1.0 - (remaining - math.floor(remaining)) \
            if remaining % 1 else 0.0
        pop = max(0.0, 1.0 - second_age / 0.28)   # 280 ms pop window
        scale = 1.0 + 0.22 * (pop * pop)
        alpha = min(1.0, 0.35 + second_age * 4)   # quick fade-in per tick

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = SIZE / 2
        r = SIZE / 2 - RING_W - 4

        # Backdrop disc
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(13, 16, 14, 216))
        p.drawEllipse(QRectF(cx - r - RING_W / 2, cy - r - RING_W / 2,
                             (r + RING_W / 2) * 2, (r + RING_W / 2) * 2))

        # Track ring + depleting arc (full → empty over the countdown)
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        track = QPen(QColor(self.theme.surface2), RING_W)
        p.setPen(track)
        p.drawEllipse(rect)
        frac = remaining / self._total
        arc = QPen(QColor(self.theme.danger), RING_W)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc)
        p.drawArc(rect, 90 * 16, -int(360 * 16 * frac))

        # Big popping number
        num_color = QColor(self.theme.text)
        num_color.setAlphaF(alpha)
        font = QFont("Consolas")
        font.setBold(True)
        font.setPixelSize(int(84 * scale))
        p.setFont(font)
        p.setPen(num_color)
        p.drawText(self.rect().adjusted(0, -14, 0, -14),
                   Qt.AlignmentFlag.AlignCenter, str(whole))

        # Caption
        cap = QFont("Consolas")
        cap.setBold(True)
        cap.setPixelSize(13)
        cap.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        p.setFont(cap)
        p.setPen(QColor(self.theme.danger))
        p.drawText(self.rect().adjusted(0, SIZE // 2 - 26, 0, 0),
                   Qt.AlignmentFlag.AlignHCenter
                   | Qt.AlignmentFlag.AlignVCenter, "● REC IN")
