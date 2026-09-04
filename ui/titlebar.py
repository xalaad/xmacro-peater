"""Custom title bar for the frameless main window: logo, app name, status
pill, action buttons, and themed minimize/maximize/close controls.

The window stays fully native-feeling: MainWindow.nativeEvent answers
WM_NCHITTEST so Windows itself handles edge-resizing, dragging, and Aero
snap — we only draw the chrome.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .branding import make_logo
from .theme import Theme

TITLEBAR_HEIGHT = 40


class TitleBar(QWidget):
    def __init__(self, app_name: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedHeight(TITLEBAR_HEIGHT)
        self.setObjectName("titleBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.setSpacing(10)

        self.logo = QLabel()
        self.logo.setPixmap(
            make_logo(theme, 22, detailed=False).scaled(
                22, 22, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(self.logo)

        self.title = QLabel(app_name)
        self.title.setObjectName("appTitle")
        lay.addWidget(self.title)

        # Callers insert widgets (pill, action buttons) via add_widget()
        self._insert_at = lay.count()
        lay.addStretch(1)

        # Segoe MDL2 Assets: the same glyphs native Windows chrome uses
        self._win_buttons: list[QPushButton] = []
        for glyph, tip, handler, danger in (
            ("", "Minimize", self._minimize, False),
            ("", "Maximize", self._toggle_max, False),
            ("", "Close", self._close, True),
        ):
            btn = QPushButton(glyph)
            btn.setObjectName("winClose" if danger else "winBtn")
            btn.setFixedSize(44, TITLEBAR_HEIGHT)
            btn.setToolTip(tip)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(handler)
            lay.addWidget(btn)
            self._win_buttons.append(btn)
        self.max_btn = self._win_buttons[1]

    def update_max_button(self, maximized: bool) -> None:
        self.max_btn.setText("" if maximized else "")
        self.max_btn.setToolTip("Restore" if maximized else "Maximize")

    def add_widget(self, widget: QWidget, spacing: int = 0) -> None:
        lay = self.layout()
        lay.insertWidget(self._insert_at, widget)
        self._insert_at += 1
        if spacing:
            lay.insertSpacing(self._insert_at, spacing)
            self._insert_at += 1

    def add_stretch_widget(self, widget: QWidget) -> None:
        """Add after the stretch (right-aligned, before window buttons)."""
        lay = self.layout()
        lay.insertWidget(lay.count() - len(self._win_buttons), widget)

    # ------------------------------------------------------------------
    def _minimize(self) -> None:
        self.window().showMinimized()

    def _toggle_max(self) -> None:
        win = self.window()
        if win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()

    def _close(self) -> None:
        self.window().close()

    def mouseDoubleClickEvent(self, event) -> None:
        self._toggle_max()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.logo.setPixmap(
            make_logo(theme, 22, detailed=False).scaled(
                22, 22, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
