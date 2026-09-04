"""Main window: frameless with custom themed chrome (native edge-resize,
drag, and Aero snap via WM_NCHITTEST), fixed responsive layout (no
draggable splitters), footer branding, and a fullscreen Tester.

Threading model: capture/playback run on core's own threads; everything
crosses back to the UI thread through bridge signals. Visuals are driven by
one QTimer capped at cfg.ui_fps (default 60) — fully decoupled from the
125Hz recording pollers — and every visualizer repaints only when its state
actually changed.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import datetime
import logging
import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import RECORDINGS_DIR, AppConfig, save_config
from core.controllers.base import neutral_state
from core.controllers.factory import create_backend, list_schemes, scheme_available
from core.events import MacroEvent, MacroFile
from core.hotkeys import (
    MODIFIERS,
    GlobalHotkeys,
    combo_label,
    combo_reps,
    parse_combo,
)
from core.playback.engine import INFINITE, PlaybackEngine
from core.playback.touch import touch_device_present
from core.playback.virtual_output import (
    ensure_vgamepad,
    launch_vigem_installer,
    vigem_driver_installed,
)
from core.recorder import MacroRecorder

from . import sounds
from .branding import FooterBar
from .bridge import HotkeyBridge, PlaybackBridge, RecorderBridge
from .dialogs import alert, confirm
from .live_monitor import LiveInputMonitor
from .overlay import MiniOverlay
from .scrolling import enable_smooth_scroll
from .settings_panel import SettingsDialog
from .tester_window import TesterWindow
from .theme import get_theme
from .titlebar import TITLEBAR_HEIGHT, TitleBar
from .widgets.activity_log import ActivityLog, is_motion_event
from .widgets.controller_widget import ControllerWidget
from .widgets.keyboard_widget import KeyboardWidget
from .widgets.mouse_widget import MouseWidget
from .widgets.recording_list import RecordingRow
from .widgets.status_pill import IDLE, PLAYING, RECORDING, StatusPill
from .widgets.stick_widget import StickWidget
from .widgets.trigger_bar import TriggerBar

log = logging.getLogger(__name__)

TRIGGER_LOG_THRESHOLD = 0.30

if sys.platform == "win32":
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [("ptReserved", _POINT), ("ptMaxSize", _POINT),
                    ("ptMaxPosition", _POINT), ("ptMinTrackSize", _POINT),
                    ("ptMaxTrackSize", _POINT)]


def _compass(dx: float, dy: float) -> str:
    """Screen-space direction words: (+dx,+dy) is toward bottom-right."""
    octant = round(math.atan2(dy, dx) / (math.pi / 4)) % 8
    return ("right", "bottom-right", "bottom", "bottom-left",
            "left", "top-left", "top", "top-right")[octant]


def _non_modifier_reps(*specs: str) -> tuple[str, ...]:
    """pynput reps of every non-modifier hotkey key — excluded from
    recordings entirely (they're control keys, never macro content)."""
    reps: set[str] = set()
    for spec in specs:
        for name in parse_combo(spec):
            if name not in MODIFIERS:
                reps.add(f"char:{name}" if len(name) == 1 else f"key:{name}")
    return tuple(reps)


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.theme = get_theme()
        self.setWindowTitle(cfg.branding.app_name)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Window)
        # Floor chosen so every section stays fully visible; native resize
        # enforces it too via WM_GETMINMAXINFO's ptMinTrackSize below.
        self.setMinimumSize(1000, 640)

        sounds.enabled = cfg.sounds
        self._dot_icons: dict = {}
        self.schemes = list_schemes()
        self.backend = None
        self.recorder: MacroRecorder | None = None
        self.engine: PlaybackEngine | None = None
        self._playback_state = neutral_state()
        self._playback_active = False
        self._simulating = False
        self._connected = False
        self._conn_check_countdown = 0
        self._conn_text = ""
        self._run_info = ""
        self._prev_mouse_buttons: set[str] = set()
        self._prev_pad_buttons: set[str] = set()
        self._trigger_prev = {"lt": 0.0, "rt": 0.0}
        self._motion_acc = [0, 0]          # coalesced test-motion deltas
        self._motion_logged_at = 0.0
        self._overlay_hf_at = 0.0          # overlay motion-line coalescing
        self._stick_octant = {"left": None, "right": None}

        self.rec_bridge = RecorderBridge(self)
        self.rec_bridge.event_captured.connect(self._on_captured)
        self.pb_bridge = PlaybackBridge(self)
        self.pb_bridge.run_started.connect(self._on_run_started)
        self.pb_bridge.event_played.connect(self._on_played)
        self.pb_bridge.finished.connect(self._on_playback_finished)
        self.pb_bridge.timing.connect(self._on_playback_timing)
        self.hk_bridge = HotkeyBridge(self)
        self.hk_bridge.record_toggle.connect(self.toggle_record)
        self.hk_bridge.play_last.connect(self.start_playback)
        self.hk_bridge.abort_playback.connect(self.abort_playback)

        self._build_ui()
        self._build_overlay()
        self.tester_window = TesterWindow(self.theme, self)
        self._restore_geometry()
        # Sidebar-only is the default: compact actions + recordings, with
        # the test/activity section one arrow-click away
        if QSettings("MacroSuite", "InputMacroSuite").value(
                "right_collapsed", True, type=bool):
            self._toggle_right_panel(force_collapsed=True)
        self._select_scheme(cfg.controller_scheme)
        self._autodetect_controller()
        self._refresh_recordings()

        self.monitor = LiveInputMonitor()
        self.monitor.start()

        self.hotkeys: GlobalHotkeys | None = None
        try:
            self.hotkeys = GlobalHotkeys()
            self._bind_hotkeys()
            self.hotkeys.start()
        except RuntimeError as e:
            log.warning("Global hotkeys unavailable: %s", e)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(max(1000 // cfg.ui_fps, 7))

        if not vigem_driver_installed():
            self.activity.add_line(
                "ViGEmBus driver not installed — you'll be offered a "
                "one-time install when you first play a controller macro.",
                QColor(self.theme.warning),
            )
        self.activity.add_line(self._hotkey_hint_text(),
                               QColor(self.theme.text_dim))

    # ------------------------------------------------------------- UI build
    def _hotkey_hint_text(self) -> str:
        hk = self.cfg.hotkeys
        return (f"{combo_label(hk.record_toggle)} record · "
                f"{combo_label(hk.play_last)} play · "
                f"{combo_label(hk.abort_playback)} stop")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Custom chrome: [logo][title] then gear · mini · status, directly
        # beside the title (icon-only, MDL2 glyphs, primary-colored)
        self.titlebar = TitleBar(self.cfg.branding.app_name, self.theme)

        def title_icon(glyph: str, tip: str, handler) -> QPushButton:
            b = QPushButton(glyph)
            b.setObjectName("titleIconBtn")
            b.setFixedSize(30, 28)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(handler)
            return b

        self.settings_btn = title_icon("", "Settings",
                                       self._open_settings)
        self.mini_btn = title_icon(
            "", "Mini overlay — shrink to a small always-on-top "
            "HUD for use over a game", self._enter_mini)
        self.pill = StatusPill(self.theme)
        self.titlebar.add_widget(self.settings_btn, spacing=2)
        self.titlebar.add_widget(self.mini_btn, spacing=6)
        self.titlebar.add_widget(self.pill)
        root.addWidget(self.titlebar)

        # Content: sidebar | collapse strip | right section
        content = QWidget()
        content_lay = QHBoxLayout(content)
        content_lay.setContentsMargins(10, 8, 10, 6)
        content_lay.setSpacing(6)
        controls = self._build_controls_panel()
        controls.setFixedWidth(280)
        content_lay.addWidget(controls)

        # Full-height arrow strip: ◀ open (click to collapse the right
        # section), ▶ collapsed (sidebar-only; click to expand)
        self.collapse_btn = QPushButton("")
        self.collapse_btn.setObjectName("collapseStrip")
        self.collapse_btn.setFixedWidth(16)
        self.collapse_btn.setToolTip("Hide the test & activity section")
        self.collapse_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # NOTE: clicked(bool) would feed its checked-arg into
        # force_collapsed — always pass none so it truly toggles
        self.collapse_btn.clicked.connect(
            lambda _=False: self._toggle_right_panel())
        content_lay.addWidget(self.collapse_btn)

        self._right_panel = QWidget()
        right_lay = QVBoxLayout(self._right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)
        right_lay.addWidget(self._build_test_tabs(), 3)
        self.activity = ActivityLog(self.theme)
        self.activity.verbose.setChecked(self.cfg.log_motion)
        self.activity.verbose.toggled.connect(self._on_motion_toggled)
        enable_smooth_scroll(self.activity.list)
        right_lay.addWidget(self.activity, 1)
        content_lay.addWidget(self._right_panel, 1)
        root.addWidget(content, 1)

        self.footer = FooterBar(self.cfg, self.theme)
        root.addWidget(self.footer)

    def _build_controls_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        self.record_btn = QPushButton("●  Record")
        self.record_btn.setObjectName("primary")
        self.record_btn.clicked.connect(self.toggle_record)
        lay.addWidget(self.record_btn)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("primary")
        self.play_btn.clicked.connect(self.start_playback)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.abort_playback)
        row.addWidget(self.play_btn)
        row.addWidget(self.stop_btn)
        lay.addLayout(row)

        hint = QLabel(self._hotkey_hint_text())
        hint.setObjectName("statsLabel")
        hint.setWordWrap(True)
        self._hint_label = hint
        lay.addWidget(hint)

        # Touch mode toggle — shown when a touchscreen is detected (or the
        # mode is already on), so activation is obvious, never automatic:
        # games need the default relative-delta recording.
        self.touch_toggle = QCheckBox("Touch mode — record taps && swipes")
        self.touch_toggle.setToolTip(
            "ON: taps, drags and swipes record as absolute gestures and "
            "replay as genuine Windows touch — for touchscreen apps and "
            "UI automation.\nOFF (default): relative mouse recording — "
            "what games need for camera look.\nApplies to the next "
            "recording; also in Settings."
        )
        self.touch_toggle.setChecked(self.cfg.touch_mode)
        self.touch_toggle.toggled.connect(self._on_touch_toggled)
        self.touch_toggle.setVisible(
            touch_device_present() or self.cfg.touch_mode)
        lay.addWidget(self.touch_toggle)

        # --- Playback plan: inputs appear/disappear per selected mode ---
        loop_row = QHBoxLayout()
        loop_row.setSpacing(6)
        self.loop_mode = QComboBox()
        self.loop_mode.addItems(["Play once", "Repeat N times", "Loop forever"])
        self.loop_mode.setCurrentIndex(self.cfg.playback.loop_mode)
        self.loop_mode.currentIndexChanged.connect(self._update_playback_plan)
        self.loop_mode.currentIndexChanged.connect(self._save_loop_prefs)
        self.loop_count = QSpinBox()
        self.loop_count.setRange(2, 9999)
        self.loop_count.setValue(self.cfg.playback.loop_count)
        self.loop_count.valueChanged.connect(self._update_playback_plan)
        self.loop_count.valueChanged.connect(self._save_loop_prefs)
        loop_row.addWidget(self.loop_mode, 1)
        loop_row.addWidget(self.loop_count)
        lay.addLayout(loop_row)

        self._repeat_delay_row = QWidget()
        delay_row = QHBoxLayout(self._repeat_delay_row)
        delay_row.setContentsMargins(0, 0, 0, 0)
        delay_label = QLabel("Delay between repeats")
        delay_label.setObjectName("dim")
        self.loop_delay = QDoubleSpinBox()
        self.loop_delay.setRange(0.0, 3600.0)
        self.loop_delay.setDecimals(2)
        self.loop_delay.setSuffix(" s")
        self.loop_delay.setValue(self.cfg.playback.loop_delay)
        self.loop_delay.valueChanged.connect(self._on_delay_edited)
        delay_row.addWidget(delay_label, 1)
        delay_row.addWidget(self.loop_delay)
        lay.addWidget(self._repeat_delay_row)

        start_row_w = QWidget()
        start_row = QHBoxLayout(start_row_w)
        start_row.setContentsMargins(0, 0, 0, 0)
        start_label = QLabel("Start delay (1st run)")
        start_label.setObjectName("dim")
        start_label.setToolTip(
            "Grace period after Play so you can click into the game window")
        self.start_delay = QSpinBox()
        self.start_delay.setRange(0, 10)
        self.start_delay.setSuffix(" s")
        self.start_delay.setValue(self.cfg.playback.countdown_seconds)
        self.start_delay.valueChanged.connect(self._on_delay_edited)
        start_row.addWidget(start_label, 1)
        start_row.addWidget(self.start_delay)
        lay.addWidget(start_row_w)

        self.plan_label = QLabel("")
        self.plan_label.setObjectName("statsLabel")
        self.plan_label.setWordWrap(True)
        lay.addWidget(self.plan_label)
        self._update_playback_plan()

        rec_header = QHBoxLayout()
        rec_title = QLabel("RECORDINGS")
        rec_title.setObjectName("sectionTitle")
        refresh = QPushButton("")  # MDL2 Refresh
        refresh.setObjectName("rowBtn")
        refresh.setFixedSize(24, 24)
        refresh.setToolTip("Refresh the list")
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self._refresh_recordings)
        rec_header.addWidget(rec_title)
        rec_header.addWidget(refresh)
        rec_header.addStretch(1)
        lay.addLayout(rec_header)

        self.rec_list = QListWidget()
        self.rec_list.itemDoubleClicked.connect(lambda _: self.start_playback())
        self.rec_list.currentItemChanged.connect(self._update_recording_info)
        # Item widgets are sized to the item's sizeHint — keep hints synced
        # to the viewport so rows always span the full width.
        self.rec_list.viewport().installEventFilter(self)
        self.rec_list.setSpacing(2)
        enable_smooth_scroll(self.rec_list)
        lay.addWidget(self.rec_list, 1)

        self.rec_info = QLabel("")
        self.rec_info.setObjectName("statsLabel")
        self.rec_info.setWordWrap(True)
        lay.addWidget(self.rec_info)

        self.stats = QLabel("")
        self.stats.setObjectName("statsLabel")
        self.stats.setWordWrap(True)
        lay.addWidget(self.stats)
        return panel

    def _build_test_tabs(self) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("panel")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("INPUT TEST")
        title.setObjectName("sectionTitle")
        note = QLabel("live view — checks your devices before recording")
        note.setObjectName("dim")
        header.addWidget(title)
        header.addSpacing(8)
        header.addWidget(note)
        header.addStretch(1)
        tester_btn = QPushButton("\uE90F  TEST MODE")
        tester_btn.setObjectName("accentBtn")
        tester_btn.setToolTip(
            "Fullscreen live test: every device edge-to-edge, presets, "
            "temp recording and replay")
        tester_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tester_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tester_btn.clicked.connect(self._open_tester)
        header.addWidget(tester_btn)
        lay.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # --- Controller tab (scheme switcher + connection status live here)
        pad_tab = QWidget()
        pad_outer = QVBoxLayout(pad_tab)
        pad_outer.setContentsMargins(8, 6, 8, 8)
        pad_outer.setSpacing(6)

        scheme_row = QHBoxLayout()
        scheme_row.setSpacing(8)
        scheme_row.addWidget(QLabel("Scheme:"))
        self.scheme_combo = QComboBox()
        self.scheme_combo.setMinimumWidth(220)
        self._populate_schemes()
        self.scheme_combo.currentIndexChanged.connect(self._on_scheme_changed)
        scheme_row.addWidget(self.scheme_combo)
        self.device_label = QLabel("Device:")
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(170)
        self.device_combo.setToolTip(
            "More than one pad is connected — pick which physical device "
            "to read while testing"
        )
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        self.device_label.hide()
        self.device_combo.hide()
        scheme_row.addWidget(self.device_label)
        scheme_row.addWidget(self.device_combo)
        self.conn_label = QLabel("")
        self.conn_label.setObjectName("statsLabel")
        scheme_row.addWidget(self.conn_label, 1)
        pad_outer.addLayout(scheme_row)

        # LT | (controller over sticks) | RT — triggers flank on their
        # physical sides, analog scopes sit directly under the pad art
        pad_lay = QHBoxLayout()
        pad_lay.setSpacing(6)
        self.trigger_l = TriggerBar(self.theme, "LT")
        self.trigger_l.setFixedWidth(46)
        pad_lay.addWidget(self.trigger_l)
        center = QVBoxLayout()
        center.setSpacing(2)
        self.controller_w = ControllerWidget(self.theme)
        center.addWidget(self.controller_w, 4)
        # Scopes sit centered directly under the pad, between its grips
        sticks_row = QHBoxLayout()
        sticks_row.setSpacing(18)
        self.stick_l = StickWidget(self.theme, "LS")
        self.stick_r = StickWidget(self.theme, "RS")
        self.stick_l.set_deadzone(self.cfg.stick_deadzone)
        self.stick_r.set_deadzone(self.cfg.stick_deadzone)
        sticks_row.addStretch(1)
        sticks_row.addWidget(
            self.stick_l, 1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        sticks_row.addWidget(
            self.stick_r, 1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        sticks_row.addStretch(1)
        center.addLayout(sticks_row, 3)
        pad_lay.addLayout(center, 1)
        self.trigger_r = TriggerBar(self.theme, "RT")
        self.trigger_r.setFixedWidth(46)
        pad_lay.addWidget(self.trigger_r)
        pad_outer.addLayout(pad_lay, 1)
        self.tabs.addTab(pad_tab, "Controller")

        # --- Keyboard tab
        kb_tab = QWidget()
        kb_lay = QVBoxLayout(kb_tab)
        kb_lay.setContentsMargins(8, 8, 8, 8)
        self.keyboard_w = KeyboardWidget(self.theme)
        kb_lay.addWidget(self.keyboard_w)
        self.tabs.addTab(kb_tab, "Keyboard")

        # --- Mouse tab
        mouse_tab = QWidget()
        mouse_lay = QHBoxLayout(mouse_tab)
        mouse_lay.setContentsMargins(8, 8, 8, 8)
        self.mouse_w = MouseWidget(self.theme)
        self.mouse_w.setMaximumWidth(420)
        mouse_lay.addStretch(1)
        mouse_lay.addWidget(self.mouse_w, 2)
        mouse_lay.addStretch(1)
        self.tabs.addTab(mouse_tab, "Mouse")

        lay.addWidget(self.tabs, 1)
        return wrap

    def _build_overlay(self) -> None:
        hints = self._hotkey_hint_text() if self.cfg.overlay.show_hints else ""
        self.overlay = MiniOverlay(self.theme, self.cfg.overlay.opacity, hints)
        self.overlay.record_clicked.connect(self.toggle_record)
        self.overlay.play_clicked.connect(self.start_playback)
        self.overlay.stop_clicked.connect(self.abort_playback)
        self.overlay.expand_clicked.connect(self._exit_mini)

        # Two-way sync of the repeat controls with the main screen
        def mirror_combo(dst):
            def apply(index):
                if dst.currentIndex() != index:
                    dst.blockSignals(True)
                    dst.setCurrentIndex(index)
                    dst.blockSignals(False)
                    if dst is self.loop_mode:
                        self._update_playback_plan()
                    else:
                        self.overlay._sync_loop_row()
            return apply

        def mirror_spin(dst):
            def apply(value):
                if dst.value() != value:
                    dst.blockSignals(True)
                    dst.setValue(value)
                    dst.blockSignals(False)
                    if dst is self.loop_count:
                        self._update_playback_plan()
            return apply

        self.overlay.loop_mode.setCurrentIndex(self.loop_mode.currentIndex())
        self.overlay.loop_count.setValue(self.loop_count.value())
        self.overlay.loop_mode.currentIndexChanged.connect(
            mirror_combo(self.loop_mode))
        self.loop_mode.currentIndexChanged.connect(
            mirror_combo(self.overlay.loop_mode))
        self.overlay.loop_count.valueChanged.connect(
            mirror_spin(self.loop_count))
        self.loop_count.valueChanged.connect(
            mirror_spin(self.overlay.loop_count))

    def changeEvent(self, event) -> None:
        if event.type() == event.Type.WindowStateChange:
            self.titlebar.update_max_button(self.isMaximized())
        super().changeEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if (obj is self.rec_list.viewport()
                and event.type() == event.Type.Resize):
            self._sync_row_widths()
        return super().eventFilter(obj, event)

    def _sync_row_widths(self) -> None:
        width = self.rec_list.viewport().width() - 2 * self.rec_list.spacing() - 4
        for i in range(self.rec_list.count()):
            self.rec_list.item(i).setSizeHint(QSize(width, 30))

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
            # Exact fit so the arrow's right-side gap equals the window's
            # left margin: 10 + 280 sidebar + 6 spacing + 16 strip + 10
            if not self.isMaximized():
                self._expanded_width = self.width()
                self.setMinimumSize(322, 640)
                self.resize(322, self.height())
        else:
            self.collapse_btn.setText("\uE76B")   # chevron left: click to close
            self.collapse_btn.setToolTip("Hide the test & activity section")
            self.setMinimumSize(1000, 640)
            if not self.isMaximized():
                self.resize(max(getattr(self, "_expanded_width", 1120),
                                1000), self.height())
        QSettings("MacroSuite", "InputMacroSuite").setValue(
            "right_collapsed", collapsed)

    def _open_tester(self) -> None:
        self.tester_window.open()

    def _enter_mini(self) -> None:
        self.hide()
        self.overlay.show()
        self.overlay.set_last_line(
            "If hidden in-game: set the game to Borderless/Windowed")

    def _exit_mini(self) -> None:
        self.overlay.hide()
        self.showNormal()
        self.activateWindow()

    # ------------------------------------------------------------- host API
    # (used by the Tester panel — keeps it decoupled from window internals)
    def build_recorder(self, on_event) -> MacroRecorder:
        hk = self.cfg.hotkeys
        return MacroRecorder(
            backend=self.backend,
            poll_hz=self.cfg.poll_hz,
            stick_deadzone=self.cfg.stick_deadzone,
            trigger_deadzone=self.cfg.trigger_deadzone,
            ignore_keys=_non_modifier_reps(
                hk.record_toggle, hk.play_last, hk.abort_playback
            ),
            trim_keys=tuple(combo_reps(hk.record_toggle)),
            touch_mode=self.cfg.touch_mode,
            on_event=on_event,
        )

    def is_busy(self) -> bool:
        return self._playback_active or (
            self.recorder is not None and self.recorder.is_recording)

    def set_simulating(self, on: bool) -> None:
        self._simulating = on
        self._playback_state = neutral_state()

    def feed_visual_event(self, ev: MacroEvent) -> None:
        """Update the controller visual state from a macro event (used by
        real playback and by the Tester's dry-run simulation)."""
        s = self._playback_state
        d = ev.data
        if ev.src == "pad_btn":
            (s["buttons"].add if d["action"] == "down" else
             s["buttons"].discard)(d["button"])
        elif ev.src == "pad_axis":
            if d["stick"] == "left":
                s["lx"], s["ly"] = d["x"], d["y"]
            else:
                s["rx"], s["ry"] = d["x"], d["y"]
        elif ev.src == "pad_trigger":
            s["lt" if d["trigger"] == "left" else "rt"] = d["value"]

    def describe_event(self, ev: MacroEvent) -> str:
        return self.activity.describe(ev)

    def refresh_recordings(self, select: str | None = None) -> None:
        self._refresh_recordings(select)

    # ------------------------------------------------------------- logging
    def _log(self, text: str, color_hex: str | None = None) -> None:
        self.activity.add_line(text,
                               QColor(color_hex) if color_hex else None)
        self.overlay.set_last_line(text)

    # ------------------------------------------------------------- schemes
    def _populate_schemes(self) -> None:
        self.scheme_combo.blockSignals(True)
        self.scheme_combo.clear()
        for sid, scheme in self.schemes.items():
            self.scheme_combo.addItem(scheme.name, sid)
            idx = self.scheme_combo.count() - 1
            ok, why = scheme_available(scheme)
            if not ok:
                item = self.scheme_combo.model().item(idx)
                item.setEnabled(False)
                item.setToolTip(why)
        self.scheme_combo.blockSignals(False)

    def _select_scheme(self, scheme_id: str) -> None:
        idx = self.scheme_combo.findData(scheme_id)
        if idx < 0:
            idx = 0
        self.scheme_combo.setCurrentIndex(idx)
        self._on_scheme_changed(idx)

    def _on_scheme_changed(self, index: int) -> None:
        sid = self.scheme_combo.itemData(index)
        scheme = self.schemes.get(sid)
        if scheme is None:
            return
        if self.backend is not None:
            self.backend.close()
        self.backend = create_backend(scheme)
        self.controller_w.set_art(scheme.art, scheme.layout)
        if hasattr(self, "tester_window"):
            self.tester_window.set_scheme(scheme.art, scheme.layout)
        self.activity.set_pad_labels(scheme.labels)
        self.cfg.controller_scheme = sid
        save_config(self.cfg)
        self._conn_check_countdown = 0

    def _autodetect_controller(self) -> None:
        """On startup: if the configured scheme's pad isn't plugged in but
        another scheme's is, switch to the connected one automatically."""
        if self.backend is not None and self.backend.is_connected():
            return
        # Prefer XInput schemes: an Xbox pad is also visible to DirectInput,
        # but its native path is the better test target.
        ordered = sorted(self.schemes.items(),
                         key=lambda kv: kv[1].backend != "xinput")
        for sid, scheme in ordered:
            if sid == self.cfg.controller_scheme:
                continue
            ok, _ = scheme_available(scheme)
            if not ok:
                continue
            probe = create_backend(scheme)
            if probe is None:
                continue
            connected = probe.is_connected()
            probe.close()
            if connected:
                log.info("Auto-selected connected controller scheme: %s", sid)
                self._select_scheme(sid)
                return

    def _update_conn_label(self, connected: bool) -> None:
        if self.backend is None:
            text, color = "○ backend unavailable", self.theme.warning
        elif connected:
            count = self.backend.device_count()
            text = (f"● connected — {self.backend.device_info()}"
                    + (f" · {count} devices" if count > 1 else ""))
            color = self.theme.success
        else:
            text, color = "○ not connected", self.theme.text_dim
        if text != self._conn_text:
            self._conn_text = text
            self.conn_label.setText(text)
            self.conn_label.setStyleSheet(
                f"color: {color}; font-family: Consolas, monospace;"
                "font-size: 11px;")
            self.tester_window.set_conn_text(text, color)
        self._update_scheme_marks()
        self._update_device_combo()

    def _update_device_combo(self) -> None:
        """Offer a device picker whenever the current backend can see more
        than one pad, so you can switch which one you test/listen to."""
        devices = (self.backend.list_devices()
                   if self.backend is not None else [])
        if len(devices) < 2:
            if self.device_combo.isVisible():
                self.device_label.hide()
                self.device_combo.hide()
                self.device_combo.blockSignals(True)
                self.device_combo.clear()
                self.device_combo.blockSignals(False)
            return
        current = [(self.device_combo.itemData(i),
                    self.device_combo.itemText(i))
                   for i in range(self.device_combo.count())]
        wanted = [(idx, label) for idx, label in devices]
        if current != wanted:
            held = self.device_combo.currentData()
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for idx, label in wanted:
                self.device_combo.addItem(label, idx)
            pos = self.device_combo.findData(held)
            if pos >= 0:
                self.device_combo.setCurrentIndex(pos)
            self.device_combo.blockSignals(False)
        self.device_label.show()
        self.device_combo.show()

    def _on_device_changed(self, _index: int) -> None:
        from core.controllers.pygame_backend import PygameBackend
        from core.controllers.xinput_backend import XInputBackend
        idx = self.device_combo.currentData()
        scheme = self.schemes.get(self.cfg.controller_scheme)
        if idx is None or scheme is None:
            return
        try:
            if scheme.backend == "xinput":
                new_backend = XInputBackend(user_index=idx)
            else:
                new_backend = PygameBackend(scheme, joystick_index=idx)
        except RuntimeError as e:
            log.warning("Device switch failed: %s", e)
            return
        if self.backend is not None:
            self.backend.close()
        self.backend = new_backend
        self._conn_check_countdown = 0
        self._conn_text = ""
        self.activity.add_line(
            f"Now testing: {new_backend.device_info()}",
            QColor(self.theme.accent))

    def _device_counts(self) -> tuple[int, int]:
        """(xinput pads, non-XInput DirectInput pads).

        An Xbox pad is visible to BOTH APIs — subtracting the XInput count
        from pygame's total leaves the pads that genuinely belong to the
        PlayStation/generic schemes, so a lone Xbox pad doesn't light every
        scheme's dot green.
        """
        from core.controllers.pygame_backend import (
            PYGAME_AVAILABLE, PygameBackend, pygame)
        from core.controllers.xinput_backend import XInputBackend
        if not hasattr(self, "_xinput_probe"):
            try:
                self._xinput_probe = XInputBackend()
            except RuntimeError:
                self._xinput_probe = None
        xinput_n = (self._xinput_probe.device_count()
                    if self._xinput_probe is not None else 0)
        pygame_n = 0
        if PYGAME_AVAILABLE:
            if isinstance(self.backend, PygameBackend):
                pygame_n = self.backend.device_count()
            else:
                try:
                    if not pygame.joystick.get_init():
                        pygame.init()
                        pygame.joystick.init()
                    pygame_n = pygame.joystick.get_count()
                except Exception:
                    pygame_n = 0
        return xinput_n, max(0, pygame_n - xinput_n)

    def _dot_icon(self, on: bool) -> QIcon:
        key = (self.theme.name, on)
        if key not in self._dot_icons:
            pm = QPixmap(12, 12)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.theme.success if on else self.theme.border))
            p.drawEllipse(2, 2, 8, 8)
            p.end()
            self._dot_icons[key] = QIcon(pm)
        return self._dot_icons[key]

    def _update_scheme_marks(self) -> None:
        """Selector shows, per scheme, whether a pad of THAT type is
        connected (green dot) and how many — a single Xbox pad marks only
        the Xbox scheme, not every scheme that could technically read it."""
        xinput_n, other_n = self._device_counts()
        for i in range(self.scheme_combo.count()):
            sid = self.scheme_combo.itemData(i)
            scheme = self.schemes.get(sid)
            if scheme is None:
                continue
            ok, _ = scheme_available(scheme)
            n = (xinput_n if scheme.backend == "xinput" else other_n) if ok \
                else 0
            self.scheme_combo.setItemData(
                i, self._dot_icon(n > 0), Qt.ItemDataRole.DecorationRole)
            text = scheme.name + (f"  ·  {n} connected" if n else "")
            if self.scheme_combo.itemText(i) != text:
                self.scheme_combo.setItemText(i, text)

    # ------------------------------------------------------------- recording
    def toggle_record(self, temp: bool = False) -> None:
        """temp=True (TEST MODE): the take saves to one reusable
        test_take.json, overwritten by every new test recording."""
        if self._playback_active or self._simulating:
            return
        if self.recorder is None or not self.recorder.is_recording:
            self._temp_rec = bool(temp)
            self.recorder = self.build_recorder(self.rec_bridge.on_event)
            self.recorder.start()
            sounds.record_start()
            self._set_state(RECORDING)
            self.record_btn.setText("■  Stop Recording")
            self.play_btn.setEnabled(False)
            self._log("Recording started (TOUCH mode — gestures)…"
                      if self.cfg.touch_mode else "Recording started…",
                      self.theme.danger)
        else:
            macro = self.recorder.stop()
            drift = self.recorder.drift_stats()
            self.recorder = None
            sounds.record_stop()
            self._set_state(IDLE)
            self.record_btn.setText("●  Record")
            self.play_btn.setEnabled(True)
            if macro.events:
                if getattr(self, "_temp_rec", False):
                    path = RECORDINGS_DIR / "test_take.json"
                else:
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    path = RECORDINGS_DIR / f"rec_{ts}.json"
                macro.save(path)
                self._log(
                    f"Saved {len(macro.events)} events "
                    f"({macro.duration:.1f}s) → {path.name}",
                    self.theme.success,
                )
                self._refresh_recordings(select=path.name)
            else:
                self._log("Recording stopped — no events captured.",
                          self.theme.warning)
            if self.cfg.touch_mode:
                # Diagnostic: exactly what touch capture saw, so gesture
                # problems on real touchscreens can be reported precisely
                downs = sum(1 for e in macro.events if e.src == "touch"
                            and e.data["action"] == "down")
                moves = sum(1 for e in macro.events if e.src == "touch"
                            and e.data["action"] == "move")
                ups = sum(1 for e in macro.events if e.src == "touch"
                          and e.data["action"] == "up")
                self._log(
                    f"[touch] captured {downs} taps/contacts · "
                    f"{moves} path points ({ups} lifts)",
                    self.theme.accent2)
            for src, line in drift.items():
                self.stats.setText(f"Poll drift ({src}): {line}")

    def _overlay_event_line(self, ev: MacroEvent, prefix: str = "") -> None:
        """Overlay last-action line for record/playback streams. Motion
        (mouse moves, axes, trigger travel) shows too — coalesced, and
        honoring the same Activity 'Motion' toggle as the log."""
        if is_motion_event(ev):
            if not self.activity.motion_enabled:
                return
            now = time.monotonic()
            if now - self._overlay_hf_at < 0.25:
                return
            self._overlay_hf_at = now
        self.overlay.set_last_line(prefix + self.activity.describe(ev))

    def _on_captured(self, ev: MacroEvent) -> None:
        self.activity.add_event(ev)
        self._overlay_event_line(ev)

    # ------------------------------------------------------------- playback
    def _selected_recording(self) -> Path | None:
        item = self.rec_list.currentItem()
        if item is None and self.rec_list.count() > 0:
            item = self.rec_list.item(self.rec_list.count() - 1)
        if item is None:
            return None
        return RECORDINGS_DIR / item.data(Qt.ItemDataRole.UserRole)

    def start_playback(self) -> None:
        if (self._playback_active or self._simulating
                or (self.recorder and self.recorder.is_recording)):
            return
        path = self._selected_recording()
        if path is None or not path.exists():
            self._log("No recording to play — record one first.",
                      self.theme.warning)
            return
        try:
            macro = MacroFile.load(path)
        except (OSError, ValueError) as e:
            alert(self, "Can't load recording",
                  f"{path.name} could not be loaded:\n{e}")
            return
        if macro.has_pad_events:
            # Driver first: without ViGEmBus even importing vgamepad fails,
            # so the one-time driver offer must come before any pad check.
            if not getattr(self, "_vigem_ok", False):
                if vigem_driver_installed():
                    self._vigem_ok = True
                elif confirm(
                    self, "Driver needed",
                    "Controller playback needs the ViGEmBus driver (a "
                    "one-time install that creates the virtual Xbox 360 "
                    "pad).\n\nInstall it now?",
                    yes_text="Install", danger=False,
                ):
                    if launch_vigem_installer():
                        alert(self, "Installer started",
                              "Finish the ViGEmBus setup, then press "
                              "Play again.")
                    else:
                        alert(self, "Installer not found",
                              "Download ViGEmBus from:\n"
                              "github.com/ViGEm/ViGEmBus/releases")
                    return
                else:
                    return
            if not ensure_vgamepad():
                if getattr(sys, "frozen", False):
                    alert(self, "Controller support unavailable",
                          "The virtual-pad component failed to load even "
                          "though the driver is present.\n\nA restart of "
                          "the app (or reinstall via the Setup installer) "
                          "should fix it.")
                else:
                    alert(self, "vgamepad missing",
                          "This macro contains controller events, but the "
                          "vgamepad package isn't available.\n\n"
                          "Install with: pip install vgamepad")
                return

        mode = self.loop_mode.currentIndex()
        loop_count = {0: 1, 1: self.loop_count.value(), 2: INFINITE}[mode]

        self._playback_active = True
        self._playback_state = neutral_state()
        self._run_info = ""
        self._set_state(PLAYING)
        self.play_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        sounds.play_start()
        countdown = self.start_delay.value()
        self._log(
            f"Playing {path.name}"
            + (f" in {countdown}s — click into your game!" if countdown else "…"),
            self.theme.success,
        )

        def launch():
            if not self._playback_active:  # aborted during countdown
                return
            self.engine = PlaybackEngine(
                macro,
                loop_count=loop_count,
                loop_delay=self.loop_delay.value(),
                callbacks=self.pb_bridge.callbacks(),
            )
            self.engine.start()

        QTimer.singleShot(countdown * 1000, launch)

    def abort_playback(self) -> None:
        if self.engine is not None:
            self.engine.abort()
        elif self._playback_active:  # still in countdown
            self._playback_active = False
            self._on_playback_finished(True, "Aborted")

    def _on_run_started(self, run: int, total: int) -> None:
        self._run_info = f"run {run}" + (f"/{total}" if total else " · ∞")
        self.activity.add_line(f"Run {run}" + (f"/{total}" if total else " (∞)"),
                               QColor(self.theme.text_dim))

    def _on_played(self, ev: MacroEvent) -> None:
        self.activity.add_event(ev, prefix="▶ ")
        self._overlay_event_line(ev, prefix="▶ ")
        self.feed_visual_event(ev)

    def _on_playback_timing(self, avg: float, mx: float) -> None:
        self.stats.setText(
            f"Playback timing: avg {avg * 1000:.2f}ms, max {mx * 1000:.2f}ms "
            "off recorded timestamps"
        )

    def _on_playback_finished(self, aborted: bool, msg: str) -> None:
        (sounds.play_abort if aborted else sounds.play_done)()
        self._playback_active = False
        self.engine = None
        self._playback_state = neutral_state()
        self._run_info = ""
        self._set_state(IDLE)
        self.play_btn.setEnabled(True)
        self.record_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log(f"Playback: {msg}",
                  self.theme.warning if aborted else self.theme.success)

    def _set_state(self, state: str) -> None:
        self.pill.set_state(state)
        self.overlay.set_state(state)
        if state == IDLE:
            self.overlay.set_info("")

    # -------------------------------------------------------- playback plan
    def _update_playback_plan(self, *_) -> None:
        """Show only the inputs the selected loop mode uses, plus a plain
        sentence describing exactly what Play will do."""
        mode = self.loop_mode.currentIndex()
        self.loop_count.setVisible(mode == 1)
        self._repeat_delay_row.setVisible(mode in (1, 2))
        sd = self.start_delay.value() if hasattr(self, "start_delay") else \
            self.cfg.playback.countdown_seconds
        delay = self.loop_delay.value()
        start = f"starts after {sd}s" if sd else "starts instantly"
        if mode == 0:
            plan = f"▶ Plays once — {start}."
        elif mode == 1:
            plan = (f"▶ {self.loop_count.value()} runs, {delay:g}s between "
                    f"— {start}.")
        else:
            stop = combo_label(self.cfg.hotkeys.abort_playback)
            plan = (f"▶ Loops until {stop}, {delay:g}s between runs "
                    f"— {start}.")
        self.plan_label.setText(plan)

    def _save_loop_prefs(self, *_) -> None:
        self.cfg.playback.loop_mode = self.loop_mode.currentIndex()
        self.cfg.playback.loop_count = self.loop_count.value()
        save_config(self.cfg)

    def _on_motion_toggled(self, checked: bool) -> None:
        self.cfg.log_motion = checked
        save_config(self.cfg)

    def _on_touch_toggled(self, checked: bool) -> None:
        self.cfg.touch_mode = checked
        save_config(self.cfg)
        self._log(
            "Touch mode ON — next recording captures taps/drags/swipes "
            "as gestures." if checked else
            "Touch mode OFF — next recording uses relative mouse deltas.",
            self.theme.accent2 if checked else self.theme.text_dim)

    def _on_delay_edited(self, *_) -> None:
        """Both delays live-save to config so Settings and the main screen
        always agree."""
        self.cfg.playback.loop_delay = self.loop_delay.value()
        self.cfg.playback.countdown_seconds = self.start_delay.value()
        save_config(self.cfg)
        self._update_playback_plan()

    # ------------------------------------------------------------- recordings
    def _refresh_recordings(self, select: str | None = None) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.rec_list.clear()
        for path in sorted(RECORDINGS_DIR.glob("*.json")):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path.name)
            item.setSizeHint(QSize(10, 30))
            row = RecordingRow(path.name, self.theme)
            row.rename_requested.connect(self._rename_recording)
            row.delete_requested.connect(self._delete_recording)
            self.rec_list.addItem(item)
            self.rec_list.setItemWidget(item, row)
        self._sync_row_widths()
        if select:
            for i in range(self.rec_list.count()):
                if self.rec_list.item(i).data(
                        Qt.ItemDataRole.UserRole) == select:
                    self.rec_list.setCurrentRow(i)
                    break
        elif self.rec_list.count():
            self.rec_list.setCurrentRow(self.rec_list.count() - 1)
        self._update_recording_info()

    def _update_recording_info(self, *_) -> None:
        item = self.rec_list.currentItem()
        if item is None:
            self.rec_info.setText("")
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        try:
            macro = MacroFile.load(RECORDINGS_DIR / name)
        except (OSError, ValueError) as e:
            self.rec_info.setText(f"Unreadable: {e}")
            return
        counts = macro.counts_by_source()
        kb = counts.get("kb", 0)
        mouse = sum(v for k, v in counts.items() if k.startswith("mouse"))
        pad = sum(v for k, v in counts.items() if k.startswith("pad"))
        self.rec_info.setText(
            f"{macro.duration:.1f}s · {len(macro.events)} events — "
            f"kb {kb} · mouse {mouse} · pad {pad}"
        )

    def _rename_recording(self, old: str, new: str) -> None:
        for ch in '\\/:*?"<>|':
            new = new.replace(ch, "_")
        new = new.strip()
        if not new:
            self._refresh_recordings(select=old)
            return
        if not new.lower().endswith(".json"):
            new += ".json"
        src, dst = RECORDINGS_DIR / old, RECORDINGS_DIR / new
        if dst.exists():
            alert(self, "Name taken", f"{new} already exists.")
            self._refresh_recordings(select=old)
            return
        try:
            src.rename(dst)
        except OSError as e:
            alert(self, "Rename failed", str(e))
            self._refresh_recordings(select=old)
            return
        self._refresh_recordings(select=new)

    def _delete_recording(self, name: str) -> None:
        if confirm(self, "Delete recording", f"Delete {name}?",
                   yes_text="Delete"):
            (RECORDINGS_DIR / name).unlink(missing_ok=True)
            self._refresh_recordings()

    # ------------------------------------------------------------- settings
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        dlg.settings_changed.connect(self._apply_settings)
        dlg.scheme_imported.connect(self._reload_schemes)
        dlg.exec()

    def _apply_settings(self) -> None:
        self._tick_timer.start(max(1000 // self.cfg.ui_fps, 7))
        self.stick_l.set_deadzone(self.cfg.stick_deadzone)
        self.stick_r.set_deadzone(self.cfg.stick_deadzone)
        self.loop_delay.blockSignals(True)
        self.loop_delay.setValue(self.cfg.playback.loop_delay)
        self.loop_delay.blockSignals(False)
        self.start_delay.blockSignals(True)
        self.start_delay.setValue(self.cfg.playback.countdown_seconds)
        self.start_delay.blockSignals(False)
        self._update_playback_plan()
        self._hint_label.setText(self._hotkey_hint_text())
        self.overlay.set_bg_opacity(self.cfg.overlay.opacity)
        self.overlay.set_hints(
            self._hotkey_hint_text() if self.cfg.overlay.show_hints else "")
        sounds.enabled = self.cfg.sounds
        self.touch_toggle.blockSignals(True)
        self.touch_toggle.setChecked(self.cfg.touch_mode)
        self.touch_toggle.blockSignals(False)
        self.touch_toggle.setVisible(
            touch_device_present() or self.cfg.touch_mode)
        if self.hotkeys is not None:
            self._bind_hotkeys()

    def _reload_schemes(self) -> None:
        self.schemes = list_schemes()
        current = self.cfg.controller_scheme
        self._populate_schemes()
        self._select_scheme(current)

    def _bind_hotkeys(self) -> None:
        hk = self.cfg.hotkeys
        self.hotkeys.clear()
        self.hotkeys.bind(hk.record_toggle, self.hk_bridge.record_toggle.emit)
        self.hotkeys.bind(hk.play_last, self.hk_bridge.play_last.emit)
        self.hotkeys.bind(hk.abort_playback, self.hk_bridge.abort_playback.emit)

    # ------------------------------------------------------------- UI tick
    def _tick(self) -> None:
        snap = self.monitor.snapshot()

        # Overlay status line (cheap; overlay may be the only thing visible)
        if self.recorder is not None and self.recorder.is_recording:
            touch_tag = "touch · " if self.recorder.touch_mode else ""
            self.overlay.set_info(
                f"{touch_tag}{self.recorder.elapsed:.0f}s · "
                f"{self.recorder.event_count} events")
        elif self._playback_active and self._run_info:
            self.overlay.set_info(self._run_info)

        # Controller state — read even in mini mode so last-action works
        if self._playback_active or self._simulating:
            state = self._playback_state
            connected = True
        elif self.backend is not None:
            if self._conn_check_countdown <= 0:
                self._connected = self.backend.is_connected()
                self._update_conn_label(self._connected)
                self._conn_check_countdown = self.cfg.ui_fps  # ~1s
            self._conn_check_countdown -= 1
            connected = self._connected
            state = self.backend.read() if connected else neutral_state()
        else:
            state, connected = neutral_state(), False
            if self._conn_check_countdown <= 0:
                self._update_conn_label(False)
                self._conn_check_countdown = self.cfg.ui_fps
            self._conn_check_countdown -= 1

        self._track_last_action(snap, state)

        if self.tester_window.isVisible():
            self.tester_window.feed(snap, state, connected)

        if self.isHidden():
            return  # mini mode: skip all visualizer painting

        self.keyboard_w.frame(snap["keys"], snap["key_pulses"])
        self.mouse_w.frame(snap["mouse_buttons"], snap["move"], snap["scroll"])
        self.controller_w.frame(state, connected)
        self.stick_l.set_target(state["lx"], state["ly"],
                                "LEFT_THUMB" in state["buttons"])
        self.stick_r.set_target(state["rx"], state["ry"],
                                "RIGHT_THUMB" in state["buttons"])
        self.trigger_l.set_target(state["lt"])
        self.trigger_r.set_target(state["rt"])

        if self.recorder is not None and self.recorder.is_recording:
            self.stats.setText(
                f"Recording… {self.recorder.elapsed:.1f}s, "
                f"{self.recorder.event_count} events")

    def _track_last_action(self, snap: dict, state: dict) -> None:
        """Overlay 'last action' line + [test] activity entries while idle.
        Presses (keys, clicks, pad buttons, trigger pulls, scrolls) always
        log; continuous motion honors the Activity 'Motion' checkbox."""
        actions: list[tuple[str, str]] = []  # (text, color)
        for rep in snap["key_pulses"]:
            actions.append(
                ("Key " + rep.split(":", 1)[1].upper(), self.theme.kb))
        for btn in sorted(snap["mouse_buttons"] - self._prev_mouse_buttons):
            actions.append((f"Mouse {btn} click", self.theme.mouse))
        self._prev_mouse_buttons = set(snap["mouse_buttons"])
        for btn in sorted(state["buttons"] - self._prev_pad_buttons):
            name = self.activity.pad_labels.get(btn, btn)
            actions.append((f"Pad {name}", self.theme.pad))
        self._prev_pad_buttons = set(state["buttons"])

        # Analog trigger pulls are discrete acts — always logged
        for key, canon in (("lt", "L2"), ("rt", "R2")):
            v = state[key]
            if self._trigger_prev[key] < TRIGGER_LOG_THRESHOLD <= v:
                label = self.activity.pad_labels.get(canon, canon)
                actions.append((f"Pad {label} pull ({v:.2f})", self.theme.pad))
            self._trigger_prev[key] = v

        if snap["scroll"]:
            actions.append((
                f"Scroll {'up' if snap['scroll'] > 0 else 'down'}",
                self.theme.mouse))

        # Continuous motion: only when the Activity 'Motion' box is on
        if self.activity.motion_enabled:
            self._motion_acc[0] += snap["move"][0]
            self._motion_acc[1] += snap["move"][1]
            now = time.monotonic()
            adx, ady = self._motion_acc
            if (abs(adx) + abs(ady) > 24
                    and now - self._motion_logged_at > 0.45):
                px, py = snap["pos"]
                actions.append((
                    f"Mouse moved {_compass(adx, ady)} "
                    f"({adx:+d}, {ady:+d}) at ({px}, {py})",
                    self.theme.mouse))
                self._motion_acc = [0, 0]
                self._motion_logged_at = now
            for stick, kx, ky in (("left", "lx", "ly"), ("right", "rx", "ry")):
                x, y = state[kx], state[ky]
                mag = math.hypot(x, y)
                octant = _compass(x, -y) if mag > 0.25 else None
                if octant != self._stick_octant[stick] and octant is not None:
                    actions.append((
                        f"{stick.capitalize()} stick {octant} ({mag:.2f})",
                        self.theme.pad))
                self._stick_octant[stick] = octant
        else:
            self._motion_acc = [0, 0]

        if not actions:
            return
        busy = (self._playback_active or self._simulating
                or (self.recorder is not None and self.recorder.is_recording))
        if not busy:
            self.overlay.set_last_line(actions[-1][0])
            for text, color in actions:
                self.activity.add_line(f"[test] {text}", QColor(color))

    # ------------------------------------------------------------- lifecycle
    def _restore_geometry(self) -> None:
        settings = QSettings("MacroSuite", "InputMacroSuite")
        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1120, 700)

    def closeEvent(self, event: QCloseEvent) -> None:
        settings = QSettings("MacroSuite", "InputMacroSuite")
        settings.setValue("geometry", self.saveGeometry())
        if self.recorder is not None and self.recorder.is_recording:
            self.recorder.stop()
        if self.engine is not None:
            self.engine.abort()
            self.engine.join(timeout=1.0)
        self.tester_window.close()
        self.overlay.close()
        self.monitor.stop()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        if self.backend is not None:
            self.backend.close()
        super().closeEvent(event)
