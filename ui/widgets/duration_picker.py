"""Smart duration input: one typeable, MASKED field (only valid duration
text can be entered) with the clock button on the left. Clicking into the
field drops an h/m/s panel underneath — the field keeps keyboard focus
the whole time; the panel live-tracks what you type and its steppers
write back into the field. value() is always total seconds (float).
"""
from __future__ import annotations

import datetime
import re

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QValidator
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

MAX_SECONDS = 86400.0  # 24h — matches the config ceiling

# Longest alternative first — else 'h' would eat the 'h' of 'hour'
_TOKEN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(hours|hour|hrs|hr|h|minutes|minute|mins|min|m"
    r"|seconds|second|secs|sec|s)"
)
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0}
# Input mask: every character a valid duration can contain
_ALLOWED_CHARS = re.compile(r"[0-9hms:., ]*", re.IGNORECASE)


def parse_duration(text: str) -> float | None:
    """'90' → 90.0 · '1h 30m' → 5400.0 · '2h' → 7200.0 · '1:30:05' →
    5405.0 · '1m30' → 90.0 (trailing bare number = seconds). None if the
    text isn't a duration."""
    text = text.strip().lower().replace(",", ".")
    if not text:
        return None
    try:
        return max(0.0, float(text))  # bare number = seconds
    except ValueError:
        pass
    if ":" in text:
        parts = text.split(":")
        if not 2 <= len(parts) <= 3:
            return None
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        while len(nums) < 3:
            nums.insert(0, 0.0)
        return max(0.0, nums[0] * 3600 + nums[1] * 60 + nums[2])
    total = 0.0
    matched_any = False
    rest = text
    for m in _TOKEN_RE.finditer(text):
        total += float(m.group(1)) * _UNIT_SECONDS[m.group(2)[0]]
        matched_any = True
        rest = rest.replace(m.group(0), "", 1)
    rest = rest.strip()
    if rest:
        try:  # '1m30' — a trailing bare number counts as seconds
            total += float(rest)
        except ValueError:
            return None
    return max(0.0, total) if matched_any else None


def format_duration(seconds: float) -> str:
    """Humanize whole seconds: 5405 -> '1h 30m 5s'; 4 -> '4s'."""
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


def format_value(seconds: float) -> str:
    """Field text: like format_duration but keeps fractional seconds
    ('2.5s', '1m 30.25s')."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = round(seconds - h * 3600 - m * 60, 2)
    if s >= 60:  # rounding carry
        s -= 60
        m += 1
        if m >= 60:
            m -= 60
            h += 1
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s:g}s")
    return " ".join(parts)


class DurationValidator(QValidator):
    """Hard input mask: characters outside the duration grammar are
    rejected at the keystroke; partially-typed durations ('1:', '1h ')
    are Intermediate so typing flows naturally."""

    def validate(self, text: str, pos: int):
        if _ALLOWED_CHARS.fullmatch(text) is None:
            return QValidator.State.Invalid, text, pos
        if not text.strip():
            return QValidator.State.Intermediate, text, pos
        if parse_duration(text) is not None:
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Intermediate, text, pos


class _DurationField(QLineEdit):
    """Masked field: click opens the h/m/s panel (focus stays here);
    Up/Down keys and the wheel nudge the value."""

    nudged = Signal(float)  # delta seconds
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setValidator(DurationValidator(self))

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.clicked.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Up:
            self.nudged.emit(1.0)
            return
        if event.key() == Qt.Key.Key_Down:
            self.nudged.emit(-1.0)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            self.nudged.emit(1.0 if event.angleDelta().y() > 0 else -1.0)
            event.accept()
        else:
            event.ignore()


class DurationPopup(QWidget):
    """Panel under the field: h/m/s steppers + live preview. Opens
    WITHOUT taking focus (the field keeps it); the steppers write into
    the owner, and the owner's typing is mirrored back silently."""

    def __init__(self, owner: "DurationPicker"):
        super().__init__(owner,
                         Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._owner = owner
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("popupPanel")
        outer.addWidget(panel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.h = QSpinBox()
        self.h.setRange(0, 24)
        self.m = QSpinBox()
        self.m.setRange(0, 59)
        self.s = QDoubleSpinBox()
        self.s.setDecimals(2)
        self.s.setRange(0, 59.99)
        for col, (title, box) in enumerate(
                (("HOURS", self.h), ("MINUTES", self.m),
                 ("SECONDS", self.s))):
            lbl = QLabel(title)
            lbl.setObjectName("popupColTitle")
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.setMinimumWidth(72)
            box.valueChanged.connect(self._on_change)
            grid.addWidget(lbl, 0, col)
            grid.addWidget(box, 1, col)
        lay.addLayout(grid)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.preview = QLabel("")
        self.preview.setObjectName("statsLabel")
        bottom.addWidget(self.preview, 1)
        done = QPushButton("Done")
        done.setObjectName("accentBtn")
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        done.clicked.connect(self.hide)
        bottom.addWidget(done)
        lay.addLayout(bottom)

    def open_for(self, seconds: float, anchor: QWidget) -> None:
        self.sync_silent(seconds)
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        self.adjustSize()
        screen = QGuiApplication.screenAt(pos) \
            or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            pos.setX(min(pos.x(), geo.right() - self.width() - 4))
            if pos.y() + self.height() > geo.bottom():
                pos.setY(anchor.mapToGlobal(QPoint(0, 0)).y()
                         - self.height() - 4)
        self.move(pos)
        self.show()  # WA_ShowWithoutActivating: the field keeps focus

    def sync_silent(self, seconds: float) -> None:
        """Mirror a value into the steppers without writing back."""
        self._syncing = True
        try:
            seconds = min(max(0.0, seconds), MAX_SECONDS)
            self.h.setValue(int(seconds // 3600))
            self.m.setValue(int(seconds % 3600 // 60))
            self.s.setValue(round(seconds % 60, 2))
        finally:
            self._syncing = False
        self._update_preview()

    def total(self) -> float:
        return self.h.value() * 3600 + self.m.value() * 60 + self.s.value()

    def _on_change(self, *_) -> None:
        if self._syncing:
            return
        self._update_preview()
        # Live-apply: the field, plan line and config track the panel
        self._owner.setValue(self.total())

    def _update_preview(self) -> None:
        total = self.total()
        text = "= " + format_value(total)
        if total >= 60:
            at = (datetime.datetime.now()
                  + datetime.timedelta(seconds=total)).strftime("%H:%M")
            text += f" · lands ~{at}"
        self.preview.setText(text)


class DurationPicker(QWidget):
    """Public API (unchanged): value() / setValue(seconds) / valueChanged."""

    valueChanged = Signal(float)

    def __init__(self, decimals: int = 2, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._watched_window = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(6)

        # Clock on the LEFT, field fills the rest
        self.clock_btn = QPushButton("")  # MDL2 Recent (clock)
        self.clock_btn.setObjectName("rowBtn")
        self.clock_btn.setFixedSize(24, 24)
        self.clock_btn.setToolTip(
            "Open the hours / minutes / seconds panel")
        self.clock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clock_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clock_btn.clicked.connect(self._open_popup)
        lay.addWidget(self.clock_btn)

        self.field = _DurationField()
        self.field.setObjectName("durationField")
        self.field.setPlaceholderText("e.g. 90 or 1h 30m")
        self.field.setToolTip(
            "Type a duration: plain seconds (90), units (1h 30m, 45s, 2h) "
            "or clock form (1:30:05) — anything else is blocked.\nUp/Down "
            "keys and the wheel nudge by a second; clicking opens the "
            "h/m/s panel.")
        self.field.editingFinished.connect(self._commit_text)
        self.field.textEdited.connect(self._on_typed)
        self.field.clicked.connect(self._open_popup)
        self.field.nudged.connect(
            lambda d: self.setValue(self._value + d))
        lay.addWidget(self.field, 1)

        self._popup = DurationPopup(self)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self._sync_field()

    # ------------------------------------------------------------------
    def value(self) -> float:
        return self._value

    def setValue(self, seconds: float) -> None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        seconds = min(max(0.0, seconds), MAX_SECONDS)
        changed = abs(seconds - self._value) > 1e-9
        self._value = seconds
        self._sync_field()
        if self._popup.isVisible():
            self._popup.sync_silent(seconds)
        # QSpinBox semantics: programmatic setValue emits too (unless
        # signals are blocked), so connected slots always stay in sync
        if changed and not self.signalsBlocked():
            self.valueChanged.emit(self._value)

    # ------------------------------------------------------------------
    def _sync_field(self) -> None:
        if not self.field.hasFocus():
            self.field.setText(format_value(self._value))
        else:
            # Don't clobber the caret mid-typing unless the value really
            # differs from what's in the box (e.g. a panel stepper click)
            parsed = parse_duration(self.field.text())
            if parsed is None or abs(parsed - self._value) > 1e-9:
                self.field.setText(format_value(self._value))

    def _on_typed(self, text: str) -> None:
        """Mirror valid typing into the open panel, silently."""
        if self._popup.isVisible():
            parsed = parse_duration(text)
            if parsed is not None:
                self._popup.sync_silent(min(parsed, MAX_SECONDS))

    def _commit_text(self) -> None:
        parsed = parse_duration(self.field.text())
        if parsed is None:
            self._sync_field()  # revert to the last good value
        else:
            self.setValue(parsed)
            self._sync_field()
        self._popup.hide()

    def _open_popup(self) -> None:
        if self._popup.isVisible():
            return
        win = self.window()
        if win is not self._watched_window:
            if self._watched_window is not None:
                self._watched_window.removeEventFilter(self)
            win.installEventFilter(self)
            self._watched_window = win
        self._popup.open_for(self._value, self)

    def _on_focus_changed(self, _old, new) -> None:
        """Click-away: the panel follows the field's focus."""
        if not self._popup.isVisible():
            return
        w = new
        while w is not None:
            if w is self.field or w is self._popup:
                return
            w = w.parentWidget()
        self._popup.hide()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._watched_window and event.type() in (
                QEvent.Type.Move, QEvent.Type.Resize,
                QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            self._popup.hide()
        return super().eventFilter(obj, event)

    def hideEvent(self, event) -> None:
        self._popup.hide()
        super().hideEvent(event)
