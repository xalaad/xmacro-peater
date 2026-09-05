"""Docking / drawer behavior - extracted verbatim from ui.main_window."""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtCore import QRectF
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
)
from PySide6.QtWidgets import QMenu, QWidget

from ..persist import app_settings

DOCK_W = 318       # docked drawer width == sidebar-only width
DOCK_HANDLE_W = 16  # the collapse strip doubles as the drawer handle


class DockTab(QWidget):
    """Floating half-capsule at the screen edge — the only thing left on
    screen when the docked drawer is slid away. Click to bring it back."""

    clicked = Signal()
    W, H = 18, 64

    def __init__(self, theme, parent=None):
        super().__init__(None,
                         Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.theme = theme
        self._side = "right"
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open the drawer")

    def show_at(self, side: str, screen) -> None:
        self._side = side
        wa = screen.availableGeometry()
        x = wa.right() - self.W + 1 if side == "right" else wa.left()
        self.move(x, wa.center().y() - self.H // 2)
        self.show()
        self.raise_()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Half-capsule: a rounded rect whose other half hangs offscreen,
        # so only the curved side shows, bulging toward the screen center
        full = QRectF(0, 0, self.W * 2, self.H)
        if self._side == "left":
            full.moveLeft(-self.W)
        path = QPainterPath()
        path.addRoundedRect(full, 16, 16)
        grad = QLinearGradient(0, 0, 0, self.H)
        grad.setColorAt(0, QColor(self.theme.accent))
        grad.setColorAt(1, QColor(self.theme.accent2))
        p.fillPath(path, grad)
        p.setPen(QColor(self.theme.bg))
        f = QFont("Segoe MDL2 Assets")
        f.setPixelSize(10)
        p.setFont(f)
        ch = "" if self._side == "right" else ""
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, ch)


class DockingMixin:
    """Dock/drawer methods mixed into MainWindow (plain class,
    no Qt base): self.* attributes come from MainWindow.__init__."""

    def _set_topmost(self, on: bool) -> None:
        if sys.platform != "win32":
            return
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        ctypes.windll.user32.SetWindowPos(
            int(self.winId()), HWND_TOPMOST if on else HWND_NOTOPMOST,
            0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    def _place_strip(self, first: bool) -> None:
        """Docked right, the handle must sit on the window's INNER edge
        (facing the screen) so it stays clickable when the drawer is
        slid away; everywhere else it lives between sidebar and panel."""
        self._content_lay.removeWidget(self.collapse_btn)
        self._content_lay.insertWidget(0 if first else 1,
                                       self.collapse_btn)
        if first:
            self._content_lay.setContentsMargins(6, 8, 10, 6)
        else:
            self._content_lay.setContentsMargins(10, 8, 6, 6)

    def _update_dock_strip(self) -> None:
        """Arrow points where a click will slide the drawer."""
        toward_edge = "" if self._dock_side == "right" else ""
        toward_screen = "" if self._dock_side == "right" else ""
        self.collapse_btn.setText(
            toward_edge if self._drawer_open else toward_screen)
        self.collapse_btn.setToolTip(
            "Slide the drawer away — only this handle stays on screen"
            if self._drawer_open else "Slide the drawer back out")

    def _dock_rect(self, open_: bool) -> QRect:
        screen = self.screen() or QGuiApplication.primaryScreen()
        wa = screen.availableGeometry()
        if self._dock_side == "right":
            x = (wa.right() - DOCK_W + 1) if open_ else \
                (wa.right() - DOCK_HANDLE_W + 1)
        else:
            x = wa.left() if open_ else wa.left() + DOCK_HANDLE_W - DOCK_W
        return QRect(x, wa.top(), DOCK_W, wa.height())

    def _show_dock_menu(self) -> None:
        """Pick the dock side explicitly — no nearest-edge guessing."""
        menu = QMenu(self)
        if self._docked:
            undock = menu.addAction("Undock — back to a window")
            undock.triggered.connect(self._exit_dock)
            menu.addSeparator()
        for side, label in (("left", "Dock left"),
                            ("right", "Dock right")):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._docked and self._dock_side == side)
            act.triggered.connect(
                lambda _=False, s=side: self._enter_dock(s))
        menu.exec(self.dock_btn.mapToGlobal(
            QPoint(0, self.dock_btn.height() + 4)))

    def _toggle_dock(self) -> None:  # kept for programmatic use
        if self._docked:
            self._exit_dock()
        else:
            self._enter_dock()

    def _enter_dock(self, side: str | None = None) -> None:
        """Dock (or re-dock to the other side). side=None keeps the
        stored/last side, defaulting to whichever edge is nearest."""
        if self.isHidden() and not self._docked:
            self._exit_mini()
        screen = self.screen() or QGuiApplication.primaryScreen()
        wa = screen.availableGeometry()
        if side not in ("left", "right"):
            side = ("left" if self.frameGeometry().center().x()
                    < wa.center().x() else "right")
        if not self._docked:
            self._pre_dock_geo = self.saveGeometry()
        self._dock_side = side
        self._docked = True
        self._drawer_open = True
        self.dock_tab.hide()
        if not getattr(self, "_right_collapsed", False):
            self._toggle_right_panel(force_collapsed=True)
        self._place_strip(side == "right")
        self.setGeometry(self._dock_rect(open_=True))
        self.show()
        self._set_topmost(True)
        self._update_dock_strip()
        settings = app_settings()
        settings.setValue("docked", True)
        settings.setValue("dock_side", side)

    def _exit_dock(self) -> None:
        self._docked = False
        self.dock_tab.hide()
        self._set_topmost(False)
        self._place_strip(False)
        self.show()
        if getattr(self, "_pre_dock_geo", None) is not None:
            self.restoreGeometry(self._pre_dock_geo)
        # Re-assert sidebar-only chrome (glyphs, margins, min size)
        self._toggle_right_panel(force_collapsed=True)
        app_settings().setValue("docked", False)

    def _slide_to(self, start: QPoint, end: QPoint,
                  on_done=None) -> None:
        """Slide the docked window between two poses. Animates POS only
        (same size both ends): no per-frame resize/relayout, and the
        explicit start pose means the first frame is never mid-flight."""
        # A rapid re-toggle must not leave two animations fighting over
        # pos — stop (and thereby delete) the in-flight one first.
        # DeleteWhenStopped means a FINISHED animation's C++ half is
        # already gone while this handle survives — stop() then raises
        # RuntimeError, which is exactly the "nothing to stop" case.
        prev = getattr(self, "_drawer_anim", None)
        if prev is not None:
            try:
                prev.stop()
            except RuntimeError:
                pass
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(260)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.setStartValue(start)
        anim.setEndValue(end)
        if on_done is not None:
            anim.finished.connect(on_done)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._drawer_anim = anim  # keep alive

    def _toggle_drawer(self) -> None:
        if self._drawer_open:
            self._drawer_open = False
            self._slide_to(self.pos(),
                           self._dock_rect(False).topLeft(),
                           self._after_drawer_closed)
        else:
            self._open_drawer()
        self._update_dock_strip()

    def _after_drawer_closed(self) -> None:
        """Slide-out finished: the window leaves the screen entirely and
        only the half-capsule tab stays at the edge."""
        if self._docked and not self._drawer_open:
            self.hide()
            screen = self.screen() or QGuiApplication.primaryScreen()
            self.dock_tab.show_at(self._dock_side, screen)

    def _open_drawer(self) -> None:
        if not self._docked:
            return
        self.dock_tab.hide()
        self._drawer_open = True
        closed = self._dock_rect(False)
        self.setGeometry(closed)
        self.show()
        self.raise_()
        # Let the window actually map & paint at the closed pose first —
        # starting the slide in the same event burst skips frames and
        # makes the drawer pop in mid-flight
        QTimer.singleShot(0, lambda: self._slide_to(
            closed.topLeft(), self._dock_rect(True).topLeft()))
        self._update_dock_strip()
