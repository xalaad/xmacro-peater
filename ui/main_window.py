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

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtCore import QRectF
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import RECORDINGS_DIR, SEQUENCES_DIR, AppConfig, save_config
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
from core.sequence import Sequence, SequenceEngine

from . import sounds
from .branding import FooterBar
from .countdown_overlay import RecordCountdown
from .bridge import (
    HotkeyBridge,
    PlaybackBridge,
    RecorderBridge,
    SequenceBridge,
)
from .dialogs import alert, confirm
from .live_monitor import LiveInputMonitor
from .overlay import MiniOverlay
from .scrolling import enable_smooth_scroll
from .settings_panel import HelpMark, SettingsDialog
from .tester_window import TesterWindow
from .theme import get_theme
from .titlebar import TITLEBAR_HEIGHT, TitleBar
from .widgets.activity_log import ActivityLog, is_motion_event
from .widgets.controller_widget import ControllerWidget
from .widgets.duration_picker import DurationPicker, format_duration
from .widgets.keyboard_widget import KeyboardWidget
from .widgets.keyboard_widget import active_layout_hkl as kb_active_hkl
from .widgets.keyboard_widget import layout_labels as kb_layout_labels
from .widgets.mouse_widget import MouseWidget
from .widgets import recording_list as rl
from .widgets.recording_list import RecordingRow, SequenceRow
from .widgets.sequence_builder import (
    SequenceBuilder,
    recording_duration,
)
from .widgets.status_pill import IDLE, PLAYING, RECORDING, StatusPill
from .widgets.stick_widget import StickWidget
from .widgets.trigger_bar import TriggerBar

log = logging.getLogger(__name__)

TRIGGER_LOG_THRESHOLD = 0.30
DOCK_W = 318       # docked drawer width == sidebar-only width
DOCK_HANDLE_W = 16  # the collapse strip doubles as the drawer handle
ROW_H = 48         # deck card height (name + metadata line)

if sys.platform == "win32":
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [("ptReserved", _POINT), ("ptMaxSize", _POINT),
                    ("ptMaxPosition", _POINT), ("ptMinTrackSize", _POINT),
                    ("ptMaxTrackSize", _POINT)]


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
    # Fires when a recording stops: the saved file name, or "" if the
    # take was empty. The Sequence Builder uses it to record steps inline.
    recording_finished = Signal(str)

    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.theme = get_theme()
        self.setWindowTitle(cfg.branding.app_name)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Window)
        # Floor chosen so every section stays fully visible; native resize
        # enforces it too via WM_GETMINMAXINFO's ptMinTrackSize below.
        # DYNAMIC: capped by the actual screen, so small laptops never get
        # a window bigger than their display.
        self.setMinimumSize(*self._min_size(collapsed=False))

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
        self._pass_info = ""
        self._deck_mode = "rec"
        self._seq_pass_est = None
        self._rec_dur = None
        self._last_hkl = None
        self._kb_labels: dict[str, str] = {}
        self._layout_cache: dict[int, dict[str, str]] = {}
        self._docked = False
        self._dock_side = "right"
        self._drawer_open = True
        self._pre_dock_geo = None
        # (key, value) caches keyed by file mtime+size — list navigation
        # and sequence estimates never re-parse unchanged files
        self._info_cache: dict[str, tuple] = {}
        self._dur_cache: dict[str, tuple] = {}
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
        self.seq_bridge = SequenceBridge(self)
        self.seq_bridge.pass_started.connect(self._on_pass_started)
        self.seq_bridge.step_started.connect(self._on_step_started)
        self.seq_bridge.event_played.connect(self._on_played)
        self.seq_bridge.finished.connect(self._on_playback_finished)
        self.seq_bridge.timing.connect(self._on_playback_timing)
        self.hk_bridge = HotkeyBridge(self)
        self.hk_bridge.record_toggle.connect(self.toggle_record)
        self.hk_bridge.play_last.connect(self.start_playback)
        self.hk_bridge.abort_playback.connect(self.abort_playback)

        self._build_ui()
        self._build_overlay()
        self.dock_tab = DockTab(self.theme)
        self.dock_tab.clicked.connect(self._open_drawer)
        self._rec_arming = False
        self.rec_countdown = RecordCountdown(self.theme)
        self.rec_countdown.finished.connect(self._on_rec_countdown_done)
        self.rec_countdown.ticked.connect(lambda _s: sounds.tick())
        self.tester_window = TesterWindow(self.theme, self)
        self._restore_geometry()
        # Sidebar-only is the default: compact actions + recordings, with
        # the test/activity section one arrow-click away
        if QSettings("MacroSuite", "InputMacroSuite").value(
                "right_collapsed", True, type=bool):
            self._toggle_right_panel(force_collapsed=True)
        if QSettings("MacroSuite", "InputMacroSuite").value(
                "docked", False, type=bool):
            self._enter_dock(QSettings(
                "MacroSuite", "InputMacroSuite").value("dock_side", None))
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
            "\uE73F", "Mini overlay — shrink to a small always-on-top "
            "HUD for use over a game", self._enter_mini)
        self.dock_btn = title_icon(
            "", "Side drawer - glue the sidebar to the screen edge "
            "at full height, always on top; the arrow strip slides it "
            "away to a small edge tab. Pick the side from the menu.",
            self._show_dock_menu)
        self.pill = StatusPill(self.theme)
        self.titlebar.add_widget(self.settings_btn, spacing=2)
        self.titlebar.add_widget(self.dock_btn, spacing=2)
        self.titlebar.add_widget(self.mini_btn, spacing=6)
        self.titlebar.add_widget(self.pill)
        root.addWidget(self.titlebar)

        # Content: sidebar | collapse strip | right section
        content = QWidget()
        content_lay = QHBoxLayout(content)
        content_lay.setContentsMargins(10, 8, 10, 6)
        content_lay.setSpacing(6)
        self._content_lay = content_lay
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
        # force_collapsed — always pass none so it truly toggles.
        # Docked, the same strip becomes the drawer's slide handle.
        self.collapse_btn.clicked.connect(
            lambda _=False: self._on_strip_clicked())
        content_lay.addWidget(self.collapse_btn)

        self._right_panel = QWidget()
        right_lay = QVBoxLayout(self._right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)
        right_lay.addWidget(self._build_test_tabs(), 3)
        self.activity = ActivityLog(self.theme)
        self.activity.enabled_box.setChecked(self.cfg.log_enabled)
        self.activity.enabled_box.toggled.connect(self._on_log_toggled)
        self.activity.verbose.setChecked(self.cfg.log_motion)
        self.activity.verbose.setEnabled(self.cfg.log_enabled)
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
        # NoFocus on every clickable: Space/Enter must never re-trigger
        # the last thing clicked — they belong to the deck list only
        self.record_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.record_btn.clicked.connect(self.toggle_record)
        lay.addWidget(self.record_btn)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("primary")
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.clicked.connect(self.start_playback)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.abort_playback)
        row.addWidget(self.play_btn)
        row.addWidget(self.stop_btn)
        lay.addLayout(row)

        # Hotkeys live ON the buttons (dim, right edge) — no hint line
        self._btn_hotkeys: dict[QPushButton, QLabel] = {}
        for btn, dark in ((self.record_btn, True), (self.play_btn, True),
                          (self.stop_btn, False)):
            h = QHBoxLayout(btn)
            h.setContentsMargins(10, 0, 10, 6)
            h.addStretch(1)
            tag = QLabel("")
            tag.setObjectName("btnHotkeyDark" if dark else "btnHotkey")
            tag.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            h.addWidget(tag, 0, Qt.AlignmentFlag.AlignBottom)
            self._btn_hotkeys[btn] = tag
        self._sync_button_hotkeys()

        # Touch mode toggle — shown when a touchscreen is detected (or the
        # mode is already on), so activation is obvious, never automatic:
        # games need the default relative-delta recording.
        self.touch_toggle = QCheckBox("Touch mode — record taps && swipes")
        self.touch_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.touch_toggle.setToolTip(
            "ON: taps, drags and swipes record as absolute gestures and "
            "replay as genuine Windows touch — for touchscreen apps and "
            "UI automation.\nOFF (default): relative mouse recording — "
            "what games need for camera look.\nApplies to the next "
            "recording; also in Settings."
        )
        self.touch_toggle.setChecked(self.cfg.touch_mode)
        self.touch_toggle.toggled.connect(self._on_touch_toggled)
        lay.addWidget(self.touch_toggle)  # parent before setVisible —
        # visible=True on a parentless widget opens a top-level flash
        # Hidden entirely on machines without a digitizer — the mode
        # cannot do anything there
        self.touch_toggle.setVisible(touch_device_present())

        # --- Playback plan: inputs appear/disappear per selected mode ---
        loop_row = QHBoxLayout()
        loop_row.setSpacing(6)
        self.loop_mode = QComboBox()
        self.loop_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        # Always visible — the tip explains the whole timing model
        loop_row.addWidget(HelpMark(
            "<b>How the delays work</b> — every gap has exactly one "
            "owner; they never add up:"
            "<br>• <b>Start delay</b> — waits once, before the first run."
            "<br>• <b>Repeat delay</b> — the gap between runs of a "
            "recording. With a sequence selected it reads <b>Pass "
            "delay</b>: the gap between whole passes of the chain."
            "<br>• <b>Step wait</b> (Sequence Builder) — the gap after "
            "each run of that step, inside a pass. The final step's "
            "wait is skipped and the pass delay takes its place."
            "<br><br>Example — 2 passes of [A ×2 · wait 5s → B], pass "
            "delay 10s:<br><code>A ‥5s‥ A ‥5s‥ B ‥10s‥ A ‥5s‥ A ‥5s‥ "
            "B</code>"))
        lay.addLayout(loop_row)

        # Each delay = label line + full-width picker line, so the h/m/s
        # fields get the whole sidebar width split evenly when expanded
        self._repeat_delay_row = QWidget()
        delay_col = QVBoxLayout(self._repeat_delay_row)
        delay_col.setContentsMargins(0, 0, 0, 0)
        delay_col.setSpacing(3)
        delay_label = QLabel("Repeat delay")
        delay_label.setObjectName("dim")
        self._repeat_delay_label = delay_label
        delay_label.setToolTip(
            "The gap between runs (or between passes of a sequence) — "
            "type it (90, 1h 30m) or click for the clock panel. The (?) "
            "above explains the full timing model.")
        self.loop_delay = DurationPicker()
        self.loop_delay.setValue(self.cfg.playback.loop_delay)
        self.loop_delay.valueChanged.connect(self._on_delay_edited)
        delay_col.addWidget(delay_label)
        delay_col.addWidget(self.loop_delay)
        lay.addWidget(self._repeat_delay_row)

        start_row_w = QWidget()
        start_col = QVBoxLayout(start_row_w)
        start_col.setContentsMargins(0, 0, 0, 0)
        start_col.setSpacing(3)
        start_label = QLabel("Start delay")
        start_label.setObjectName("dim")
        start_label.setToolTip(
            "Grace period after Play — type 2h 30m (or use the clock "
            "panel) to SCHEDULE the run; the plan line shows the exact "
            "clock time")
        self.start_delay = DurationPicker()
        self.start_delay.setValue(self.cfg.playback.countdown_seconds)
        self.start_delay.valueChanged.connect(self._on_delay_edited)
        start_col.addWidget(start_label)
        start_col.addWidget(self.start_delay)
        lay.addWidget(start_row_w)

        self.plan_label = QLabel("")
        self.plan_label.setObjectName("statsLabel")
        self.plan_label.setWordWrap(True)
        lay.addWidget(self.plan_label)
        self._update_playback_plan()

        # Deck header: RECORDINGS | SEQUENCES tab pair + new/refresh
        rec_header = QHBoxLayout()
        rec_header.setSpacing(6)
        self._deck_mode = "rec"

        def deck_tab(text: str, mode: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("deckTab")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False: self._set_deck_mode(mode))
            return b

        self.deck_rec_tab = deck_tab("RECORDINGS", "rec")
        self.deck_rec_tab.setChecked(True)
        self.deck_seq_tab = deck_tab("SEQUENCES", "seq")
        self.deck_seq_tab.setToolTip(
            "Chains of recordings played back-to-back with per-step "
            "runs and waits - build once, repeat like any macro")
        self.new_seq_btn = QPushButton("\uE710")  # MDL2 Add
        self.new_seq_btn.setObjectName("rowBtn")
        self.new_seq_btn.setFixedSize(24, 24)
        self.new_seq_btn.setToolTip("New sequence")
        self.new_seq_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_seq_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_seq_btn.clicked.connect(lambda: self._open_builder(None))
        self.new_seq_btn.hide()
        refresh = QPushButton("\uE72C")  # MDL2 Refresh
        refresh.setObjectName("rowBtn")
        refresh.setFixedSize(24, 24)
        refresh.setToolTip("Refresh the list")
        refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self._refresh_deck)
        rec_header.addWidget(self.deck_rec_tab)
        rec_header.addWidget(self.deck_seq_tab)
        rec_header.addStretch(1)
        rec_header.addWidget(self.new_seq_btn)
        rec_header.addWidget(refresh)
        lay.addLayout(rec_header)

        self.rec_list = QListWidget()
        self.rec_list.setObjectName("recList")
        self.rec_list.itemDoubleClicked.connect(lambda _: self.start_playback())
        # Keyboard-only flow: arrows navigate, Enter/Space plays
        self.rec_list.itemActivated.connect(lambda _: self.start_playback())
        self.rec_list.currentItemChanged.connect(self._update_recording_info)
        # Item widgets are sized to the item's sizeHint — keep hints synced
        # to the viewport so rows always span the full width.
        self.rec_list.viewport().installEventFilter(self)
        self.rec_list.installEventFilter(self)
        # App-wide guard: Space/Enter act only on the deck list / text
        # fields inside this window (see eventFilter)
        QApplication.instance().installEventFilter(self)
        enable_smooth_scroll(self.rec_list)
        lay.addWidget(self.rec_list, 1)

        # ONE status line: selection info and run/drift stats share it
        # (full per-take details live on each card's tooltip)
        self.stats = QLabel("")
        self.stats.setObjectName("statsLabel")
        self.stats.setWordWrap(True)
        self.rec_info = self.stats
        lay.addWidget(self.stats)
        return panel

    def _sync_button_hotkeys(self) -> None:
        hk = self.cfg.hotkeys
        for btn, spec in ((self.record_btn, hk.record_toggle),
                          (self.play_btn, hk.play_last),
                          (self.stop_btn, hk.abort_playback)):
            self._btn_hotkeys[btn].setText(combo_label(spec))

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
        self.scheme_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scheme_combo.setMinimumWidth(220)
        self._populate_schemes()
        self.scheme_combo.currentIndexChanged.connect(self._on_scheme_changed)
        scheme_row.addWidget(self.scheme_combo)
        self.device_label = QLabel("Device:")
        self.device_combo = QComboBox()
        self.device_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

        # Controller goes LAST — keyboard/mouse are the common first check
        self.tabs.addTab(pad_tab, "Controller")

        lay.addWidget(self.tabs, 1)
        return wrap

    def _build_overlay(self) -> None:
        hints = self._hotkey_hint_text() if self.cfg.overlay.show_hints else ""
        self.overlay = MiniOverlay(self.theme, self.cfg.overlay.opacity, hints)
        self.overlay.record_clicked.connect(self.toggle_record)
        self.overlay.play_clicked.connect(self.start_playback)
        self.overlay.stop_clicked.connect(self.abort_playback)
        self.overlay.expand_clicked.connect(self._exit_mini)
        self.overlay.target_changed.connect(self._select_target)

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

    def eventFilter(self, obj, event) -> bool:
        if (obj is self.rec_list.viewport()
                and event.type() == event.Type.Resize):
            self._sync_row_widths()
        elif (obj is self.rec_list
                and event.type() == event.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                    Qt.Key.Key_Space)):
            self.start_playback()
            return True
        elif (event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                    Qt.Key.Key_Enter)
                and isinstance(obj, QWidget)
                and obj.window() is self
                and obj is not self.rec_list
                and not isinstance(obj, (QLineEdit, QAbstractSpinBox))
                and QApplication.activePopupWidget() is None):
            # Space/Enter belong to the deck list and text fields ONLY.
            # Swallow them anywhere else in the main window so buttons
            # and checkboxes can never fire from a stray keypress —
            # actions have their global hotkeys.
            return True
        return super().eventFilter(obj, event)

    def _sync_row_widths(self) -> None:
        width = self.rec_list.viewport().width() - 8
        for i in range(self.rec_list.count()):
            self.rec_list.item(i).setSizeHint(QSize(width, ROW_H))

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
        QSettings("MacroSuite", "InputMacroSuite").setValue(
            "right_collapsed", collapsed)

    # ------------------------------------------------------------ side dock
    def _on_strip_clicked(self) -> None:
        if getattr(self, "_docked", False):
            self._toggle_drawer()
        else:
            self._toggle_right_panel()

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
        settings = QSettings("MacroSuite", "InputMacroSuite")
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
        QSettings("MacroSuite", "InputMacroSuite").setValue("docked", False)

    def _slide_to(self, start: QPoint, end: QPoint,
                  on_done=None) -> None:
        """Slide the docked window between two poses. Animates POS only
        (same size both ends): no per-frame resize/relayout, and the
        explicit start pose means the first frame is never mid-flight."""
        # A rapid re-toggle must not leave two animations fighting over
        # pos — stop (and thereby delete) the in-flight one first
        prev = getattr(self, "_drawer_anim", None)
        if prev is not None:
            prev.stop()
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

    def _open_tester(self) -> None:
        self.tester_window.open()

    def _enter_mini(self) -> None:
        self._sync_overlay_targets()
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
        return (self._playback_active or self._rec_arming
                or (self.recorder is not None
                    and self.recorder.is_recording))

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
        test_take.json, overwritten by every new test recording.

        With cfg.record_countdown > 0, arming first shows the on-screen
        click-through ticking countdown; recording starts when it lands.
        Toggling again during the countdown cancels it."""
        if self._playback_active or self._simulating:
            return
        if getattr(self, "_rec_arming", False):
            self._cancel_record_countdown()
            return
        if self.recorder is None or not self.recorder.is_recording:
            countdown = float(self.cfg.record_countdown)
            if countdown > 0:
                self._rec_arming = True
                self._temp_rec_pending = bool(temp)
                self.record_btn.setText("✕  Cancel")
                self.play_btn.setEnabled(False)
                self.rec_countdown.start(countdown)
                self._log(
                    f"Recording starts in {format_duration(countdown)} — "
                    "get into position…", self.theme.warning)
                return
            self._start_recording(temp)
        else:
            self._stop_recording()

    def _on_rec_countdown_done(self) -> None:
        if getattr(self, "_rec_arming", False):
            self._rec_arming = False
            self._start_recording(getattr(self, "_temp_rec_pending", False))

    def _cancel_record_countdown(self) -> None:
        self._rec_arming = False
        self.rec_countdown.stop()
        self.record_btn.setText("●  Record")
        self.play_btn.setEnabled(True)
        self._log("Recording countdown cancelled.", self.theme.text_dim)
        # Waiters (e.g. the Sequence Builder's Record step) must resume
        self.recording_finished.emit("")

    def _start_recording(self, temp: bool = False) -> None:
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

    def _stop_recording(self) -> None:
        macro = self.recorder.stop()
        drift = self.recorder.drift_stats()
        self.recorder = None
        sounds.record_stop()
        self._set_state(IDLE)
        self.record_btn.setText("●  Record")
        self.play_btn.setEnabled(True)
        saved_name = ""
        if macro.events:
            if getattr(self, "_temp_rec", False):
                path = RECORDINGS_DIR / "test_take.json"
            else:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                path = RECORDINGS_DIR / f"rec_{ts}.json"
            macro.save(path)
            saved_name = path.name
            self._log(
                f"Saved {len(macro.events)} events "
                f"({macro.duration:.1f}s) → {path.name}",
                self.theme.success,
            )
            self._refresh_recordings(select=path.name)
        else:
            self._log("Recording stopped — no events captured.",
                      self.theme.warning)
        self.recording_finished.emit(saved_name)
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

    def _ensure_pad_ready(self) -> bool:
        """Driver first: without ViGEmBus even importing vgamepad fails,
        so the one-time driver offer must come before any pad check."""
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
                return False
            else:
                return False
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
            return False
        return True

    def _arm_playback(self, what: str) -> float:
        """Shared arm-up for both engines: state, buttons, sound, and the
        countdown/schedule log line. Returns the start delay."""
        # Generation token: each arm invalidates any earlier scheduled
        # launch (play -> stop -> play within the start delay must not
        # let the STALE singleShot start a second, orphaned engine)
        self._launch_gen = getattr(self, "_launch_gen", 0) + 1
        self._playback_active = True
        self._playback_state = neutral_state()
        self._run_info = ""
        self._pass_info = ""
        self._set_state(PLAYING)
        self.play_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        sounds.play_start()
        countdown = self.start_delay.value()
        self._sched_until = time.monotonic() + countdown
        self._log(
            f"Playing {what}"
            + (f" in {format_duration(countdown)}"
               + (" — click into your game!" if countdown < 60 else "")
               if countdown else "…"),
            self.theme.success,
        )
        return countdown

    def start_playback(self) -> None:
        if (self._playback_active or self._simulating or self._rec_arming
                or (self.recorder and self.recorder.is_recording)):
            return
        if self._deck_mode == "seq":
            self.start_sequence()
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
        if macro.has_pad_events and not self._ensure_pad_ready():
            return

        mode = self.loop_mode.currentIndex()
        loop_count = {0: 1, 1: self.loop_count.value(), 2: INFINITE}[mode]
        countdown = self._arm_playback(path.name)
        gen = self._launch_gen

        def launch():
            # aborted during countdown, or superseded by a newer arm
            if not self._playback_active or gen != self._launch_gen:
                return
            self.engine = PlaybackEngine(
                macro,
                loop_count=loop_count,
                loop_delay=self.loop_delay.value(),
                force_abs_mouse=self.cfg.playback.mouse_path_replay,
                callbacks=self.pb_bridge.callbacks(),
            )
            self.engine.start()

        QTimer.singleShot(int(countdown * 1000), launch)

    def start_sequence(self) -> None:
        if (self._playback_active or self._simulating or self._rec_arming
                or (self.recorder and self.recorder.is_recording)):
            return
        item = self.rec_list.currentItem()
        if item is None and self.rec_list.count() > 0:
            item = self.rec_list.item(self.rec_list.count() - 1)
        if item is None:
            self._log("No sequence to play — build one with +.",
                      self.theme.warning)
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        try:
            seq = Sequence.load(SEQUENCES_DIR / name)
            steps = seq.resolve(RECORDINGS_DIR)
        except (OSError, ValueError) as e:
            alert(self, "Can't play sequence", f"{name}:\n{e}")
            return
        if (any(m.has_pad_events for _, m in steps)
                and not self._ensure_pad_ready()):
            return

        mode = self.loop_mode.currentIndex()
        loop_count = {0: 1, 1: self.loop_count.value(), 2: INFINITE}[mode]
        countdown = self._arm_playback(
            f"sequence {name} ({len(steps)} steps)")
        gen = self._launch_gen

        def launch():
            # aborted during countdown, or superseded by a newer arm
            if not self._playback_active or gen != self._launch_gen:
                return
            self.engine = SequenceEngine(
                steps,
                loop_count=loop_count,
                loop_delay=self.loop_delay.value(),
                force_abs_mouse=self.cfg.playback.mouse_path_replay,
                callbacks=self.seq_bridge.callbacks(),
            )
            self.engine.start()

        QTimer.singleShot(int(countdown * 1000), launch)

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

    def _on_pass_started(self, pass_no: int, total: int) -> None:
        self._pass_info = (f"pass {pass_no}"
                           + (f"/{total}" if total else " · ∞"))
        self._run_info = self._pass_info
        self.activity.add_line(
            f"Pass {pass_no}" + (f"/{total}" if total else " (∞)"),
            QColor(self.theme.text_dim))

    def _on_step_started(self, index: int, count: int, name: str,
                         run: int, runs: int) -> None:
        run_txt = f" · run {run}/{runs}" if runs > 1 else ""
        self._run_info = (f"{self._pass_info} · "
                          f"step {index + 1}/{count}{run_txt}")
        self.activity.add_line(
            f"Step {index + 1}/{count}: {name}"
            + (f" (run {run}/{runs})" if runs > 1 else ""),
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
        if sd >= 60:  # scheduled: show the actual clock time too
            at = (datetime.datetime.now()
                  + datetime.timedelta(seconds=sd)).strftime("%H:%M")
            start = f"starts after {format_duration(sd)} (~{at})"
        elif sd:
            start = f"starts after {format_duration(sd)}"
        else:
            start = "starts instantly"
        between = format_duration(delay)
        if self._deck_mode == "seq":
            est = self._seq_pass_est
            one = (f"one pass (≈ {format_duration(est)})"
                   if est else "one pass")
            if mode == 0:
                plan = f"▶ Plays {one} — {start}."
            elif mode == 1:
                n = self.loop_count.value()
                if est:
                    total = n * est + (n - 1) * delay
                    plan = (f"▶ {n} passes ≈ {format_duration(total)} "
                            f"total, {between} between — {start}.")
                else:
                    plan = f"▶ {n} passes, {between} between — {start}."
            else:
                stop = combo_label(self.cfg.hotkeys.abort_playback)
                plan = (f"▶ Loops passes until {stop}, {between} "
                        f"between — {start}.")
        elif mode == 0:
            dur = self._rec_dur
            plan = (f"▶ Plays once ≈ {format_duration(dur)} — {start}."
                    if dur else f"▶ Plays once — {start}.")
        elif mode == 1:
            n = self.loop_count.value()
            dur = self._rec_dur
            if dur:
                total = n * dur + (n - 1) * delay
                plan = (f"▶ {n} runs ≈ {format_duration(total)} total, "
                        f"{between} between — {start}.")
            else:
                plan = f"▶ {n} runs, {between} between — {start}."
        else:
            stop = combo_label(self.cfg.hotkeys.abort_playback)
            dur = self._rec_dur
            per = f" (≈ {format_duration(dur)}/run)" if dur else ""
            plan = (f"▶ Loops until {stop}, {between} between{per} "
                    f"— {start}.")
        self.plan_label.setText(plan)

    def _save_loop_prefs(self, *_) -> None:
        self.cfg.playback.loop_mode = self.loop_mode.currentIndex()
        self.cfg.playback.loop_count = self.loop_count.value()
        save_config(self.cfg)

    def _on_motion_toggled(self, checked: bool) -> None:
        self.cfg.log_motion = checked
        save_config(self.cfg)

    def _on_log_toggled(self, checked: bool) -> None:
        self.cfg.log_enabled = checked
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
        self.cfg.playback.countdown_seconds = float(self.start_delay.value())
        save_config(self.cfg)
        self._update_playback_plan()

    # ---------------------------------------------------------------- deck
    def _set_deck_mode(self, mode: str) -> None:
        if mode == self._deck_mode:
            # Clicking the active tab: just re-assert the checked state
            # (the click toggled it off) — no rebuild, no flicker
            self.deck_rec_tab.setChecked(mode == "rec")
            self.deck_seq_tab.setChecked(mode == "seq")
            return
        self._deck_mode = mode
        self.deck_rec_tab.setChecked(mode == "rec")
        self.deck_seq_tab.setChecked(mode == "seq")
        self.new_seq_btn.setVisible(mode == "seq")
        # Same field, honest name: between runs vs between chain passes
        self._repeat_delay_label.setText(
            "Pass delay" if mode == "seq" else "Repeat delay")
        self._refresh_deck()
        self._update_playback_plan()

    def _refresh_deck(self, select: str | None = None) -> None:
        if self._deck_mode == "seq":
            self._refresh_sequences(select)
        else:
            self._refresh_recordings(select)

    def _select_deck_row(self, select: str | None) -> None:
        if select:
            for i in range(self.rec_list.count()):
                if self.rec_list.item(i).data(
                        Qt.ItemDataRole.UserRole) == select:
                    self.rec_list.setCurrentRow(i)
                    break
        elif self.rec_list.count():
            self.rec_list.setCurrentRow(self.rec_list.count() - 1)
        self._update_recording_info()
        overlay = getattr(self, "overlay", None)
        if overlay is not None and overlay.isVisible():
            item = self.rec_list.currentItem()
            if item is not None:
                overlay.set_current_target(
                    self._deck_mode, item.data(Qt.ItemDataRole.UserRole))

    def _sync_overlay_targets(self) -> None:
        items = []
        for p in sorted(RECORDINGS_DIR.glob("*.json")):
            details = self._recording_details(p.name)
            items.append(("rec", p.name, details[2], details[3] or None))
        for p in sorted(SEQUENCES_DIR.glob("*.json")):
            try:
                est, missing = self._sequence_estimate(Sequence.load(p))
            except (OSError, ValueError):
                est, missing = None, ["?"]
            items.append(("seq", p.name, ("seq",),
                          None if missing else est))
        cur = self.rec_list.currentItem()
        current = ((self._deck_mode, cur.data(Qt.ItemDataRole.UserRole))
                   if cur is not None else None)
        self.overlay.set_targets(items, current, self.loop_delay.value())

    def _select_target(self, kind: str, name: str) -> None:
        """Overlay picked what to play: mirror it into the deck so the
        normal Play path runs exactly that target."""
        if self._deck_mode != kind:
            self._deck_mode = kind
            self.deck_rec_tab.setChecked(kind == "rec")
            self.deck_seq_tab.setChecked(kind == "seq")
            self.new_seq_btn.setVisible(kind == "seq")
            self._repeat_delay_label.setText(
                "Pass delay" if kind == "seq" else "Repeat delay")
            self._refresh_deck(select=name)
            self._update_playback_plan()
        else:
            self._select_deck_row(name)

    def _refresh_recordings(self, select: str | None = None) -> None:
        if self._deck_mode != "rec":
            return  # a new take lands on disk; visible on next tab switch
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.rec_list.setUpdatesEnabled(False)
        try:
            self.rec_list.clear()
            for path in sorted(RECORDINGS_DIR.glob("*.json")):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, path.name)
                item.setSizeHint(QSize(10, ROW_H))
                info, meta, kinds, _dur = self._recording_details(path.name)
                row = RecordingRow(path.name, self.theme, meta, kinds)
                row.setToolTip(info)
                row.rename_requested.connect(self._rename_recording)
                row.delete_requested.connect(self._delete_recording)
                self.rec_list.addItem(item)
                self.rec_list.setItemWidget(item, row)
            self._sync_row_widths()
            self._select_deck_row(select)
        finally:
            self.rec_list.setUpdatesEnabled(True)

    def _refresh_sequences(self, select: str | None = None) -> None:
        if self._deck_mode != "seq":
            return
        SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)
        self.rec_list.setUpdatesEnabled(False)
        try:
            self.rec_list.clear()
            for path in sorted(SEQUENCES_DIR.glob("*.json")):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, path.name)
                item.setSizeHint(QSize(10, ROW_H))
                meta = self._sequence_meta(path)
                row = SequenceRow(path.name, self.theme, meta)
                row.setToolTip(meta)
                row.edit_requested.connect(self._open_builder)
                row.rename_requested.connect(self._rename_sequence)
                row.delete_requested.connect(self._delete_sequence)
                self.rec_list.addItem(item)
                self.rec_list.setItemWidget(item, row)
            self._sync_row_widths()
            self._select_deck_row(select)
        finally:
            self.rec_list.setUpdatesEnabled(True)

    def _sequence_meta(self, path: Path) -> str:
        try:
            seq = Sequence.load(path)
        except (OSError, ValueError):
            return "unreadable"
        est, missing = self._sequence_estimate(seq)
        if missing:
            return f"{len(seq.steps)} steps · ⚠ missing step"
        return f"{len(seq.steps)} steps · ≈ {format_duration(est)}"

    def _update_recording_info(self, *_) -> None:
        item = self.rec_list.currentItem()
        if item is None:
            self.rec_info.setText("")
            self._seq_pass_est = None
            self._update_playback_plan()
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if self._deck_mode == "seq":
            self._update_sequence_info(name)
            return
        details = self._recording_details(name)
        self.rec_info.setText(details[0])
        self._rec_dur = details[3] or None
        self._update_playback_plan()

    def _cache_key(self, path: Path) -> tuple | None:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _recording_details(self, name: str) -> tuple[str, str, str]:
        """(info line, card meta, badge glyph) — parsed once per file
        version. Arrow-key navigation must never re-read multi-MB takes."""
        path = RECORDINGS_DIR / name
        key = self._cache_key(path)
        cached = self._info_cache.get(name)
        if cached is not None and cached[0] == key and key is not None:
            return cached[1]
        details = self._load_macro_info(path)
        self._info_cache[name] = (key, details)
        return details

    def _recording_info_text(self, name: str) -> str:
        return self._recording_details(name)[0]

    def _load_macro_info(self, path: Path) -> tuple[str, str, tuple, float]:
        try:
            macro = MacroFile.load(path)
        except (OSError, ValueError) as e:
            return (f"Unreadable: {e}", "unreadable", ("broken",), 0.0)
        counts = macro.counts_by_source()
        kb = counts.get("kb", 0)
        touch = counts.get("touch", 0)
        mouse = sum(v for k, v in counts.items() if k.startswith("mouse"))
        pad = sum(v for k, v in counts.items() if k.startswith("pad"))
        # Zero-count devices are noise — list only what the take uses,
        # with full names
        detail = " · ".join(
            f"{label} {count}" for count, label in
            ((kb, "keyboard"), (mouse, "mouse"),
             (pad, "controller"), (touch, "touch")) if count)
        info = (f"{macro.duration:.1f}s · {len(macro.events)} events"
                + (f" — {detail}" if detail else ""))
        meta = (f"{format_duration(macro.duration)} · "
                f"{len(macro.events)} events")
        # Badge = every device the take uses, busiest first (top 3)
        ranked = sorted(((pad, "pad"), (touch, "touch"),
                         (mouse, "mouse"), (kb, "kb")), reverse=True)
        kinds = tuple(k for c, k in ranked if c > 0)[:3]
        return (info, meta, kinds or ("rec",), macro.duration)

    def _rename_recording(self, old: str, new: str) -> None:
        new = self._do_rename(RECORDINGS_DIR, old, new,
                              self._refresh_recordings)
        if new is not None:
            self._retarget_sequences(old, new)

    def _delete_recording(self, name: str) -> None:
        users = self._sequences_using(name)
        extra = (f"\n\nUsed by sequence(s): {', '.join(users)} — "
                 "they'll fail until you edit them." if users else "")
        if confirm(self, "Delete recording", f"Delete {name}?{extra}",
                   yes_text="Delete"):
            (RECORDINGS_DIR / name).unlink(missing_ok=True)
            self._refresh_recordings()

    # ------------------------------------------------------------ sequences
    def _update_sequence_info(self, name: str) -> None:
        try:
            seq = Sequence.load(SEQUENCES_DIR / name)
        except (OSError, ValueError) as e:
            self.rec_info.setText(f"Unreadable: {e}")
            return
        est, missing = self._sequence_estimate(seq)
        text = (f"{len(seq.steps)} step(s) · one pass ≈ "
                f"{format_duration(est)}")
        if missing:
            text += f" · ⚠ missing: {', '.join(missing)}"
            est = None  # estimate is a lie with steps missing
        self.rec_info.setText(text)
        self._seq_pass_est = est
        self._update_playback_plan()

    def _sequence_estimate(self, seq: Sequence) -> tuple[float, list[str]]:
        """(one-pass duration, missing recordings) — durations served
        from the mtime cache so estimates never re-parse takes."""
        durations: dict[str, float] = {}
        missing: list[str] = []
        for s in seq.steps:
            if s.recording in durations or s.recording in missing:
                continue
            path = RECORDINGS_DIR / s.recording
            key = self._cache_key(path)
            if key is None:
                missing.append(s.recording)
                continue
            cached = self._dur_cache.get(s.recording)
            if cached is not None and cached[0] == key:
                durations[s.recording] = cached[1]
            else:
                d = recording_duration(path)
                self._dur_cache[s.recording] = (key, d)
                durations[s.recording] = d
        return seq.pass_duration(durations), missing

    def _open_builder(self, existing: str | None = None) -> None:
        # Non-modal: the main window (and its Record button) stays usable
        # so the builder can record new steps inline.
        if getattr(self, "_builder", None) is not None:
            self._builder.raise_()
            self._builder.activateWindow()
            return
        dlg = SequenceBuilder(self.theme, self, existing=existing)

        def on_saved(name: str) -> None:
            if self._deck_mode != "seq":
                self._deck_mode = "seq"
                self.deck_rec_tab.setChecked(False)
                self.deck_seq_tab.setChecked(True)
                self.new_seq_btn.setVisible(True)
            self._refresh_sequences(select=name)
            self._log(f"Sequence saved → {name}", self.theme.success)

        def on_closed(_result: int) -> None:
            self._builder = None

        dlg.saved.connect(on_saved)
        dlg.finished.connect(on_closed)
        self._builder = dlg
        dlg.show()

    def _rename_sequence(self, old: str, new: str) -> None:
        self._do_rename(SEQUENCES_DIR, old, new, self._refresh_sequences)

    def _delete_sequence(self, name: str) -> None:
        if confirm(self, "Delete sequence",
                   f"Delete {name}?\n(Its recordings stay untouched.)",
                   yes_text="Delete"):
            (SEQUENCES_DIR / name).unlink(missing_ok=True)
            self._refresh_sequences()

    def _do_rename(self, folder: Path, old: str, new: str,
                   refresh) -> str | None:
        """Shared rename for both decks; returns the final name or None."""
        for ch in '\\/:*?"<>|':
            new = new.replace(ch, "_")
        new = new.strip()
        if not new:
            refresh(select=old)
            return None
        if not new.lower().endswith(".json"):
            new += ".json"
        src, dst = folder / old, folder / new
        if dst.exists():
            alert(self, "Name taken", f"{new} already exists.")
            refresh(select=old)
            return None
        try:
            src.rename(dst)
        except OSError as e:
            alert(self, "Rename failed", str(e))
            refresh(select=old)
            return None
        refresh(select=new)
        return new

    def _sequences_using(self, recording: str) -> list[str]:
        names = []
        for spath in SEQUENCES_DIR.glob("*.json"):
            try:
                seq = Sequence.load(spath)
            except (OSError, ValueError):
                continue
            if any(s.recording == recording for s in seq.steps):
                names.append(spath.name)
        return names

    def _retarget_sequences(self, old: str, new: str) -> None:
        """Renaming a recording silently updates every sequence that
        references it — chains never break from a rename."""
        for spath in SEQUENCES_DIR.glob("*.json"):
            try:
                seq = Sequence.load(spath)
            except (OSError, ValueError):
                continue
            hit = False
            for s in seq.steps:
                if s.recording == old:
                    s.recording = new
                    hit = True
            if hit:
                seq.save(spath)

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
        self._sync_button_hotkeys()
        self.overlay.set_bg_opacity(self.cfg.overlay.opacity)
        self.overlay.set_hints(
            self._hotkey_hint_text() if self.cfg.overlay.show_hints else "")
        sounds.enabled = self.cfg.sounds
        self.touch_toggle.blockSignals(True)
        self.touch_toggle.setChecked(self.cfg.touch_mode)
        self.touch_toggle.blockSignals(False)
        # Hidden entirely on machines without a digitizer — the mode
        # cannot do anything there
        self.touch_toggle.setVisible(touch_device_present())
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
        elif self._playback_active and self.engine is None:
            # Scheduled wait: live countdown until the run starts
            remaining = getattr(self, "_sched_until", 0) - time.monotonic()
            if remaining > 0:
                self.overlay.set_info(f"▶ in {format_duration(remaining)}")
                self.stats.setText(
                    f"Scheduled — starts in {format_duration(remaining)}")
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
                self._sync_keyboard_layout()
                self._conn_check_countdown = self.cfg.ui_fps  # ~1s
            self._conn_check_countdown -= 1
            connected = self._connected
            state = self.backend.read() if connected else neutral_state()
        else:
            state, connected = neutral_state(), False
            if self._conn_check_countdown <= 0:
                self._update_conn_label(False)
                self._sync_keyboard_layout()
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

    def _sync_keyboard_layout(self) -> None:
        """Follow the FOREGROUND window's keyboard layout. The ~1s poll
        costs exactly two syscalls (the layout handle); the full label
        map is computed via ToUnicodeEx only ONCE per distinct layout
        ever seen, then served from cache."""
        hkl = kb_active_hkl()
        if not hkl or hkl == self._last_hkl:
            return
        self._last_hkl = hkl
        labels = self._layout_cache.get(hkl)
        if labels is None:
            labels = kb_layout_labels(hkl)
            self._layout_cache[hkl] = labels
        if labels:
            self._kb_labels = labels
            self.keyboard_w.set_layout_labels(labels)
            self.tester_window.keyboard_w.set_layout_labels(labels)

    def _track_last_action(self, snap: dict, state: dict) -> None:
        """Overlay 'last action' line + [test] activity entries while idle.
        Presses (keys, clicks, pad buttons, trigger pulls, scrolls) always
        log; continuous motion honors the Activity 'Motion' checkbox."""
        actions: list[tuple[str, str]] = []  # (text, color)
        for rep in snap["key_pulses"]:
            # Name the key in the ACTIVE layout's language, not English
            name = (getattr(self, "_kb_labels", {}).get(rep)
                    or rep.split(":", 1)[1].upper())
            actions.append(("Key " + name, self.theme.kb))
        for btn in sorted(snap["mouse_buttons"] - self._prev_mouse_buttons):
            actions.append((f"Mouse {btn} click", self.theme.mouse))
        self._prev_mouse_buttons = set(snap["mouse_buttons"])
        # Real touchscreen taps (detected via the OS touch signature) —
        # named honestly instead of masquerading as left clicks
        for tx, ty in snap.get("touch_taps", ()):
            actions.append((f"Touch tap at ({tx}, {ty})",
                            self.theme.accent2))
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
        settings = QSettings("MacroSuite", "InputMacroSuite")
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

    def closeEvent(self, event: QCloseEvent) -> None:
        QApplication.instance().removeEventFilter(self)
        self._tick_timer.stop()  # no ticks during teardown
        # Kill anything still scheduled: a pending playback launch or an
        # armed record countdown must not run injection code mid-teardown
        self._playback_active = False
        self._launch_gen = getattr(self, "_launch_gen", 0) + 1
        self._rec_arming = False
        self.rec_countdown.stop()
        settings = QSettings("MacroSuite", "InputMacroSuite")
        # Docked: remember the pre-dock geometry, not the glued one, so
        # undocking after a restart lands the window somewhere sane
        if (getattr(self, "_docked", False)
                and getattr(self, "_pre_dock_geo", None) is not None):
            settings.setValue("geometry", self._pre_dock_geo)
        else:
            settings.setValue("geometry", self.saveGeometry())
        if self.recorder is not None and self.recorder.is_recording:
            self.recorder.stop()
        if self.engine is not None:
            self.engine.abort()
            self.engine.join(timeout=1.0)
        self.dock_tab.close()
        self.rec_countdown.close()
        self.tester_window.close()
        self.overlay.close()
        self.monitor.stop()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        if self.backend is not None:
            self.backend.close()
        super().closeEvent(event)
