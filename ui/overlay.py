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

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class TopmostCombo(QComboBox):
    """Combo whose dropdown stays ABOVE an always-on-top parent window.

    The popup is its own OS window without the topmost flag, so a topmost
    overlay would cover it — push the popup to HWND_TOPMOST on open."""

    def showPopup(self) -> None:
        super().showPopup()
        if sys.platform == "win32":
            popup = self.view().window()
            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                int(popup.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
            )

from .theme import Theme

IDLE, RECORDING, PLAYING = "Idle", "Recording", "Playing"


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
        self.play_btn = self._mini_btn("", "Play the selected "
                                                 "recording (Ctrl+F10)")
        self.play_btn.clicked.connect(self.play_clicked.emit)
        self.stop_btn = self._mini_btn("", "Stop playback (Ctrl+F11)")
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        self.expand_btn = self._mini_btn("\uE740", "Back to the full window")
        self.expand_btn.clicked.connect(self.expand_clicked.emit)
        for b in (self.rec_btn, self.play_btn, self.stop_btn,
                  self.expand_btn):
            top.addWidget(b)
        root.addLayout(top)

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
        self._sync_loop_row()

        self.last_line = QLabel("—")
        self.last_line.setFixedHeight(16)
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

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._apply_styles()
        self.set_state(self._state)
        self.update()

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

    # ------------------------------------------------------------------
    def set_state(self, state: str) -> None:
        self._state = state
        self.state_label.setText(state)
        idle = state == IDLE
        self.loop_mode.setEnabled(idle)
        self.loop_count.setEnabled(idle)
        self.dot.set_color(QColor({
            RECORDING: self.theme.danger,
            PLAYING: self.theme.success,
        }.get(state, self.theme.text_dim)))
        self.stop_btn.setEnabled(state == PLAYING)
        self.rec_btn.setEnabled(state != PLAYING)
        self.play_btn.setEnabled(state == IDLE)
        # Record button flips to a stop glyph while recording
        self.rec_btn.setText("" if state == RECORDING else "")
        self.update()

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
        bg = QColor(self.theme.surface)
        bg.setAlphaF(self.bg_opacity)
        border = QColor(self.theme.border)
        border.setAlphaF(min(1.0, self.bg_opacity + 0.05))
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)

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
            QSettings("MacroSuite", "InputMacroSuite").setValue(
                "overlay_pos", self.pos()
            )

    # Position ----------------------------------------------------------
    def _restore_position(self) -> None:
        pos = QSettings("MacroSuite", "InputMacroSuite").value("overlay_pos")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if pos is not None and screen.contains(pos):
            self.move(pos)
        else:
            self.adjustSize()
            self.move(screen.right() - self.width() - 24,
                      screen.bottom() - 130)
