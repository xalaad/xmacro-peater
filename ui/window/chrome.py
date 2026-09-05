"""Window chrome & geometry (native hit-testing, panel collapse, min size,
geometry restore) - extracted verbatim from ui.main_window."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

from ..persist import app_settings
from ..titlebar import TITLEBAR_HEIGHT
from .docking import DOCK_W

if sys.platform == "win32":
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [("ptReserved", _POINT), ("ptMaxSize", _POINT),
                    ("ptMaxPosition", _POINT), ("ptMinTrackSize", _POINT),
                    ("ptMaxTrackSize", _POINT)]


class ChromeMixin:
    """Window-chrome methods mixed into MainWindow (plain class,
    no Qt base): self.* attributes come from MainWindow.__init__."""

    # -------------------------------------------------------- window chrome
    def nativeEvent(self, event_type, message):
        """Answer WM_NCHITTEST so Windows gives the frameless window native
        edge-resizing, title-bar dragging, and Aero snap; WM_GETMINMAXINFO
        keeps maximize inside the taskbar's work area."""
        if sys.platform != "win32" or event_type != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message == 0x0084:  # WM_NCHITTEST
            # Use the coordinates from the message itself — for TOUCH the
            # cursor hasn't moved yet at hit-test time, so QCursor.pos()
            # is stale and taps get misrouted (buttons wouldn't respond).
            sx = ctypes.c_short(msg.lParam & 0xFFFF).value
            sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            pt = ctypes.wintypes.POINT(sx, sy)
            ctypes.windll.user32.ScreenToClient(int(self.winId()),
                                                ctypes.byref(pt))
            dpr = self.devicePixelRatioF() or 1.0
            pos = QPoint(int(pt.x / dpr), int(pt.y / dpr))
            w, h = self.width(), self.height()
            m = 6
            # Docked: geometry is managed — no edge-resize, no drag
            if getattr(self, "_docked", False):
                return super().nativeEvent(event_type, message)
            if not self.isMaximized():
                top, bottom = pos.y() < m, pos.y() > h - m
                left, right = pos.x() < m, pos.x() > w - m
                if top and left:
                    return True, 13
                if top and right:
                    return True, 14
                if bottom and left:
                    return True, 16
                if bottom and right:
                    return True, 17
                if left:
                    return True, 10
                if right:
                    return True, 11
                if top:
                    return True, 12
                if bottom:
                    return True, 15
            if 0 <= pos.y() < TITLEBAR_HEIGHT:
                child = self.childAt(pos)
                draggable = (None, self.titlebar, self.titlebar.title,
                             self.titlebar.logo, self.pill)
                if child in draggable:
                    return True, 2  # HTCAPTION
        elif msg.message == 0x0024:  # WM_GETMINMAXINFO
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                ag, g = screen.availableGeometry(), screen.geometry()
                dpr = screen.devicePixelRatio()
                mmi = _MINMAXINFO.from_address(msg.lParam)
                mmi.ptMaxPosition.x = int((ag.x() - g.x()) * dpr)
                mmi.ptMaxPosition.y = int((ag.y() - g.y()) * dpr)
                mmi.ptMaxSize.x = int(ag.width() * dpr)
                mmi.ptMaxSize.y = int(ag.height() * dpr)
                # Hard floor for native edge-resizing: without this,
                # Windows lets the frameless window shrink below Qt's
                # minimum and the layout clips.
                mmi.ptMinTrackSize.x = int(self.minimumWidth() * dpr)
                mmi.ptMinTrackSize.y = int(self.minimumHeight() * dpr)
                return True, 0
        return super().nativeEvent(event_type, message)

    def _toggle_right_panel(self, force_collapsed: bool | None = None) -> None:
        """Sidebar-only mode: hide the whole test/activity section, keep
        actions + recordings; the title text hides too (logo stays)."""
        collapsed = (not getattr(self, "_right_collapsed", False)
                     if force_collapsed is None else force_collapsed)
        self._right_collapsed = collapsed
        self._right_panel.setVisible(not collapsed)
        self.titlebar.set_compact(collapsed)
        if collapsed:
            self.collapse_btn.setText("\uE76C")   # chevron right: click to open
            self.collapse_btn.setToolTip("Show the test & activity section")
            # Symmetric arrow: right margin drops to the layout spacing
            # (6px) so the gap on each side of the arrow is identical.
            # Exact fit: 10 + 280 sidebar + 6 + 16 strip + 6 = 318
            self._content_lay.setContentsMargins(10, 8, 6, 6)
            if not self.isMaximized():
                self._expanded_width = self.width()
                self.setMinimumSize(*self._min_size(collapsed=True))
                self.resize(DOCK_W, self.height())
        else:
            self.collapse_btn.setText("\uE76B")   # chevron left: click to close
            self.collapse_btn.setToolTip("Hide the test & activity section")
            self._content_lay.setContentsMargins(10, 8, 10, 6)
            min_w, min_h = self._min_size(collapsed=False)
            self.setMinimumSize(min_w, min_h)
            if not self.isMaximized():
                wa = (self.screen()
                      or QGuiApplication.primaryScreen()).availableGeometry()
                self.resize(min(max(getattr(self, "_expanded_width", 1120),
                                    min_w), wa.width()), self.height())
        app_settings().setValue("right_collapsed", collapsed)

    def _min_size(self, collapsed: bool) -> tuple[int, int]:
        """Preferred floors (1000x640 / 318x640), capped by the CURRENT
        screen so a small laptop is never forced past its display."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        wa = screen.availableGeometry()
        w = DOCK_W if collapsed else min(1000, wa.width() - 8)
        return (min(w, wa.width() - 8), min(640, wa.height() - 8))

    @staticmethod
    def _clamped_rect(geo: QRect, wa: QRect) -> QRect:
        """Fit a (possibly stale, saved-on-another-screen) geometry into
        the given work area: shrink oversize, pull fully on-screen."""
        w = min(geo.width(), wa.width())
        h = min(geo.height(), wa.height())
        x = max(wa.left(), min(geo.x(), wa.right() - w + 1))
        y = max(wa.top(), min(geo.y(), wa.bottom() - h + 1))
        return QRect(x, y, w, h)

    def _restore_geometry(self) -> None:
        settings = app_settings()
        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1120, 700)
        # Saved dimensions come from whatever screen the app last ran on
        # — validate against THIS one (smaller laptop, changed scaling…)
        screen = (QGuiApplication.screenAt(self.frameGeometry().center())
                  or self.screen() or QGuiApplication.primaryScreen())
        self.setGeometry(self._clamped_rect(
            self.geometry(), screen.availableGeometry()))
