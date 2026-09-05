"""Mini overlay: a small translucent always-on-top HUD for use while a game
is running (windowed / borderless fullscreen — exclusive fullscreen bypasses
the desktop compositor, so no overlay can draw there).

Shows the current state, elapsed/run info, the last action line, hotkey
hints, and MDL2-icon controls: record, play/repeat, stop, expand back to
the full window. Frameless, draggable, doesn't steal focus from the game,
and remembers its position.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import (
    QPoint,
    QPointF,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

HEADER_ROLE = Qt.ItemDataRole.UserRole + 7


class _TargetDelegate(QStyledItemDelegate):
    """Paints the picker's section headers: a small dim title with a
    rule line continuing after the text to the right edge."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme

    def paint(self, p, option, index) -> None:
        if not index.data(HEADER_ROLE):
            super().paint(p, option, index)
            return
        p.save()
        f = QFont("Consolas")
        f.setPixelSize(9)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(f)
        rect = option.rect.adjusted(10, 0, -10, 0)
        text = index.data()
        p.setPen(QColor(self.theme.text_dim))
        p.drawText(rect, Qt.AlignmentFlag.AlignVCenter
                   | Qt.AlignmentFlag.AlignLeft, text)
        tw = QFontMetrics(f).horizontalAdvance(text)
        y = option.rect.center().y()
        p.setPen(QPen(QColor(self.theme.border), 1))
        p.drawLine(rect.left() + tw + 8, y, rect.right(), y)
        p.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(HEADER_ROLE):
            size.setHeight(20)
        return size


class TopmostCombo(QComboBox):
    """Combo whose dropdown stays ABOVE an always-on-top parent window.

    The popup is its own OS window without the topmost flag, so a topmost
    overlay would cover it — push the popup to HWND_TOPMOST on open."""

    def showPopup(self) -> None:
        super().showPopup()
        # Qt scrolls the popup to the current item (often the bottom of a
        # long list) — always open at the top instead. Qt's own scroll is
        # deferred too and its timing varies, so fire twice to win the
        # race deterministically.
        QTimer.singleShot(0, self.view().scrollToTop)
        QTimer.singleShot(50, self.view().scrollToTop)
        if sys.platform == "win32":
            popup = self.view().window()
            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                int(popup.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
            )

from .theme import Theme
from .widgets.duration_picker import format_duration
from .widgets.recording_list import device_badge
from .widgets.status_pill import IDLE, PLAYING, RECORDING
from .persist import app_settings


class StateDot(QWidget):
    """Small status circle that sits inline with the state label —
    layout-aligned, never hand-positioned."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._color = QColor(theme.text_dim)
        self.setFixedSize(14, 14)

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        halo = QColor(self._color)
        halo.setAlphaF(0.3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(center, 6.5, 6.5)
        p.setBrush(self._color)
        p.drawEllipse(center, 4.0, 4.0)


class MiniOverlay(QWidget):
    record_clicked = Signal()
    play_clicked = Signal()
    stop_clicked = Signal()
    expand_clicked = Signal()
    target_changed = Signal(str, str)  # kind ("rec"/"seq"), file name

    def __init__(self, theme: Theme, opacity: float = 0.92,
                 hints: str = "", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.bg_opacity = opacity
        self._state = IDLE

        self._drag_offset: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(330)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = StateDot(theme)
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.state_label = QLabel(IDLE)
        top.addWidget(self.state_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.info_label = QLabel("")
        top.addWidget(self.info_label, 1, Qt.AlignmentFlag.AlignVCenter)

        # Segoe MDL2 Assets: Record, Play, Stop, BackToWindow (unicode
        # escapes on purpose — literal PUA chars don't survive all editors)
        self.rec_btn = self._mini_btn("",
                                      "Start / stop recording (Ctrl+F9)")
        self.rec_btn.clicked.connect(self.record_clicked.emit)
        # ONE transport button: ▶ play flips to ■ stop while running
        self.play_btn = self._mini_btn(
            "", "Play the selection (Ctrl+F10) - becomes Stop "
            "(Ctrl+F11) while running")
        self.play_btn.clicked.connect(self._on_transport)
        self.expand_btn = self._mini_btn("\uE740", "Back to the full window")
        self.expand_btn.clicked.connect(self.expand_clicked.emit)
        for b in (self.rec_btn, self.play_btn, self.expand_btn):
            top.addWidget(b)
        root.addLayout(top)

        # What Play will run — every recording and sequence, pickable
        # without leaving the game
        self.target = TopmostCombo()
        self.target.setToolTip(
            "What ▶ Play runs — sequences on top, recordings below; "
            "synced with the main window's selection")
        self.target.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.target.setItemDelegate(_TargetDelegate(theme, self.target))
        self.target.currentIndexChanged.connect(self._on_target_changed)
        root.addWidget(self.target)

        # Repeat controls: one line — mode full-width for once/forever,
        # split half/half with the run count for "Repeat N times"
        loops = QHBoxLayout()
        loops.setSpacing(6)
        self.loop_mode = TopmostCombo()
        self.loop_mode.addItems(["Play once", "Repeat N times",
                                 "Loop forever"])
        self.loop_mode.setToolTip("How the next Play repeats — synced "
                                  "with the main window")
        self.loop_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.loop_count = QSpinBox()
        self.loop_count.setRange(2, 9999)
        self.loop_count.setValue(5)
        self.loop_count.setToolTip("Number of runs")
        self.loop_count.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.loop_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loops.addWidget(self.loop_mode, 1)
        loops.addWidget(self.loop_count, 1)
        root.addLayout(loops)
        self.loop_mode.currentIndexChanged.connect(self._sync_loop_row)
        self.loop_mode.currentIndexChanged.connect(
            self._refresh_target_tips)
        self.loop_count.valueChanged.connect(self._refresh_target_tips)
        self._sync_loop_row()

        self.last_line = QLabel("—")
        self.last_line.setFixedHeight(19)  # room for tall-script glyphs
        root.addWidget(self.last_line)

        self.hints_label = QLabel(hints)
        root.addWidget(self.hints_label)
        if not hints:
            self.hints_label.hide()

        self._apply_styles()
        self._restore_position()

        # Games (and Steam's own overlay) periodically re-claim topmost;
        # quietly re-assert ours so the HUD stays visible over borderless/
        # windowed games. NOACTIVATE keeps the game's focus untouched.
        # NOTE: exclusive-fullscreen games bypass the desktop compositor
        # entirely — no OS window can draw over those.
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(750)
        self._topmost_timer.timeout.connect(self._assert_topmost)

    def _mini_btn(self, glyph: str, tip: str) -> QPushButton:
        b = QPushButton(glyph)
        b.setObjectName("overlayBtn")
        b.setToolTip(tip)
        b.setFixedSize(30, 26)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _apply_styles(self) -> None:
        t = self.theme
        self.state_label.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {t.text};"
            "font-family: 'Cascadia Mono', Consolas, monospace;"
            "background: transparent;")
        self.info_label.setStyleSheet(
            f"font-size: 11px; color: {t.text_dim};"
            "font-family: Consolas, monospace; background: transparent;")
        self.last_line.setStyleSheet(
            f"color: {t.text_dim}; font-family: Consolas, monospace;"
            "font-size: 11px; background: transparent;")
        self.hints_label.setStyleSheet(
            f"color: {t.text_dim}; font-size: 10px;"
            "font-family: Consolas, monospace; background: transparent;")
        # Buttons are styled by the global QSS (#overlayBtn) — the same
        # proven path the title-bar MDL2 glyphs use.

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._assert_topmost()
        self._topmost_timer.start()

    def hideEvent(self, event) -> None:
        self._topmost_timer.stop()
        super().hideEvent(event)

    def _assert_topmost(self) -> None:
        # Never fight our own dropdown for the top of the z-order
        if QApplication.activePopupWidget() is not None:
            return
        if sys.platform == "win32" and self.isVisible():
            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            self.raise_()

    def _sync_loop_row(self, *_) -> None:
        self.loop_count.setVisible(self.loop_mode.currentIndex() == 1)

    # ------------------------------------------------------ target picker
    def set_targets(self, items: list[tuple],
                    current: tuple[str, str] | None,
                    loop_delay: float = 1.0) -> None:
        """items: (kind, file name, device kinds, duration seconds) for
        every recording and sequence. Grouped with sequences always on
        top under painted headers; each entry shows its device badge and
        run length, and its tooltip spells out the total completion time
        with the current repeat settings."""
        from PySide6.QtGui import QIcon
        self._loop_delay = loop_delay
        self._durations: dict[tuple[str, str], float | None] = {}
        self.target.blockSignals(True)
        self.target.clear()
        self.target.setIconSize(QSize(18, 18))
        groups = (("SEQUENCES",
                   [it for it in items if it[0] == "seq"]),
                  ("RECORDINGS",
                   [it for it in items if it[0] == "rec"]))
        for title, group in groups:
            if not group:
                continue
            self.target.addItem(title)
            hdr = self.target.count() - 1
            self.target.setItemData(hdr, True, HEADER_ROLE)
            self.target.model().item(hdr).setFlags(Qt.ItemFlag.NoItemFlags)
            for it in group:
                kind, name = it[0], it[1]
                kinds = it[2] if len(it) > 2 else (kind,)
                secs = it[3] if len(it) > 3 else None
                self._durations[(kind, name)] = secs
                label = name.removesuffix(".json")
                if secs:
                    label += f"  ·  {format_duration(secs)}"
                icon = QIcon(device_badge(tuple(kinds), self.theme, 18))
                self.target.addItem(icon, label, (kind, name))
        self._refresh_target_tips()
        idx = self._find_target(*current) if current is not None else -1
        if idx < 0:  # never leave a disabled header selected
            for i in range(self.target.count()):
                if self.target.itemData(i) is not None:
                    idx = i
                    break
        if idx >= 0:
            self.target.setCurrentIndex(idx)
        self.target.blockSignals(False)

    def _find_target(self, kind: str, name: str) -> int:
        # NOTE: QComboBox.findData can't match Python tuples (QVariant
        # comparison happens C++-side) — compare in Python instead
        for i in range(self.target.count()):
            if self.target.itemData(i) == (kind, name):
                return i
        return -1

    def set_current_target(self, kind: str, name: str) -> None:
        idx = self._find_target(kind, name)
        if idx >= 0 and idx != self.target.currentIndex():
            self.target.blockSignals(True)
            self.target.setCurrentIndex(idx)
            self.target.blockSignals(False)

    def _on_target_changed(self, _index: int) -> None:
        data = self.target.currentData()
        if data is not None:
            self.target_changed.emit(*data)

    def _refresh_target_tips(self) -> None:
        """Per-item tooltip: ▶ total time to complete with the CURRENT
        repeat settings — recomputed when the loop controls change."""
        mode = self.loop_mode.currentIndex()
        n = self.loop_count.value()
        delay = getattr(self, "_loop_delay", 1.0)
        for i in range(self.target.count()):
            data = self.target.itemData(i)
            if data is None:
                continue  # header row
            secs = getattr(self, "_durations", {}).get(tuple(data))
            unit = "pass" if data[0] == "seq" else "run"
            if not secs:
                tip = "Duration unknown"
            elif mode == 0:
                tip = f"▶ plays once — ≈ {format_duration(secs)}"
            elif mode == 1:
                total = n * secs + (n - 1) * delay
                tip = (f"▶ {n} {unit}s ≈ {format_duration(total)} to "
                       f"complete ({format_duration(secs)} per {unit})")
            else:
                tip = (f"▶ loops forever — ≈ {format_duration(secs)} "
                       f"per {unit}")
            self.target.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)

    # ------------------------------------------------------------------
    def set_state(self, state: str) -> None:
        self._state = state
        self.state_label.setText(state.upper())
        color = {RECORDING: self.theme.danger,
                 PLAYING: self.theme.success}.get(state, self.theme.text)
        self.state_label.setStyleSheet(
            f"font-weight: 700; font-size: 12px; color: {color};"
            "font-family: 'Cascadia Mono', Consolas, monospace;"
            "letter-spacing: 1px; background: transparent;")
        idle = state == IDLE
        self.loop_mode.setEnabled(idle)
        self.loop_count.setEnabled(idle)
        self.target.setEnabled(idle)
        self.dot.set_color(QColor({
            RECORDING: self.theme.danger,
            PLAYING: self.theme.success,
        }.get(state, self.theme.text_dim)))
        self.rec_btn.setEnabled(state != PLAYING)
        # Transport: play while idle, stop while playing
        self.play_btn.setEnabled(state != RECORDING)
        self.play_btn.setText(
            "" if state == PLAYING else "")
        # Record button flips to a stop glyph while recording
        self.rec_btn.setText("" if state == RECORDING else "")
        self.update()

    def _on_transport(self) -> None:
        if self._state == PLAYING:
            self.stop_clicked.emit()
        else:
            self.play_clicked.emit()

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)

    def set_last_line(self, text: str) -> None:
        metrics = self.last_line.fontMetrics()
        self.last_line.setText(
            metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                               self.width() - 34))

    def set_hints(self, text: str) -> None:
        self.hints_label.setText(text)
        self.hints_label.setVisible(bool(text))

    def set_bg_opacity(self, opacity: float) -> None:
        self.bg_opacity = opacity
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Panel language of the main window: subtle top sheen + a quiet
        # accent-tinted border, at the user's chosen opacity
        top = QColor(self.theme.surface2)
        top.setAlphaF(self.bg_opacity)
        bottom = QColor(self.theme.surface)
        bottom.setAlphaF(self.bg_opacity)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, top)
        grad.setColorAt(0.25, bottom)
        accent = QColor(self.theme.accent)
        accent.setAlphaF(min(1.0, self.bg_opacity * 0.55))
        p.setPen(QPen(accent, 1))
        p.setBrush(grad)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)

    # Dragging ----------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            app_settings().setValue(
                "overlay_pos", self.pos()
            )

    # Position ----------------------------------------------------------
    def _restore_position(self) -> None:
        pos = app_settings().value("overlay_pos")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if pos is not None and screen.contains(pos):
            self.move(pos)
        else:
            self.adjustSize()
            self.move(screen.right() - self.width() - 24,
                      screen.bottom() - 130)
