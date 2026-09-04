"""Frameless themed dialogs: every popup in the app (Settings, About,
confirmations, alerts) uses our own chrome — no native title bars.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FramelessDialog(QDialog):
    """Base: custom title bar (title + MDL2 close), draggable, themed
    border. Subclasses add content to self.body (a QVBoxLayout)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("dialogFrame")
        outer.addWidget(frame)
        frame_lay = QVBoxLayout(frame)
        frame_lay.setContentsMargins(0, 0, 0, 0)
        frame_lay.setSpacing(0)

        self._bar = QWidget()
        self._bar.setObjectName("titleBar")
        self._bar.setFixedHeight(34)
        bar = QHBoxLayout(self._bar)
        bar.setContentsMargins(12, 0, 0, 0)
        self._title = QLabel(title.upper())
        self._title.setObjectName("sectionTitle")
        bar.addWidget(self._title)
        bar.addStretch(1)
        close = QPushButton("")  # MDL2 ChromeClose
        close.setObjectName("winClose")
        close.setFixedSize(40, 34)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.reject)
        bar.addWidget(close)
        frame_lay.addWidget(self._bar)

        body_widget = QWidget()
        self.body = QVBoxLayout(body_widget)
        self.body.setContentsMargins(16, 12, 16, 14)
        self.body.setSpacing(8)
        frame_lay.addWidget(body_widget, 1)

    # drag anywhere on the bar
    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and event.position().y() < 34):
            self._drag = (event.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None


class ConfirmDialog(FramelessDialog):
    def __init__(self, title: str, text: str, parent=None,
                 yes_text: str = "Yes", danger: bool = True):
        super().__init__(title, parent)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMinimumWidth(260)
        self.body.addWidget(label)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton("Cancel")
        no.clicked.connect(self.reject)
        yes = QPushButton(yes_text)
        yes.setObjectName("danger" if danger else "primary")
        yes.clicked.connect(self.accept)
        row.addWidget(no)
        row.addWidget(yes)
        self.body.addLayout(row)


def confirm(parent, title: str, text: str, yes_text: str = "Yes",
            danger: bool = True) -> bool:
    return ConfirmDialog(title, text, parent, yes_text,
                         danger).exec() == QDialog.DialogCode.Accepted


def alert(parent, title: str, text: str) -> None:
    dlg = FramelessDialog(title, parent)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setMinimumWidth(280)
    dlg.body.addWidget(label)
    ok = QPushButton("OK")
    ok.setObjectName("primary")
    ok.clicked.connect(dlg.accept)
    dlg.body.addWidget(ok)
    dlg.exec()
