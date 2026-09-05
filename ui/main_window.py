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

import datetime
import logging
import time
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
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
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import RECORDINGS_DIR, SEQUENCES_DIR, AppConfig, save_config
from core.controllers.base import neutral_state
from core.controllers.factory import list_schemes
from core.events import MacroEvent
from core.hotkeys import (
    MODIFIERS,
    GlobalHotkeys,
    combo_label,
    combo_reps,
    parse_combo,
)
from core.playback.engine import PlaybackEngine
from core.playback.touch import touch_device_present
from core.playback.virtual_output import vigem_driver_installed
from core.recorder import MacroRecorder

from . import sounds
from .branding import FooterBar
from .countdown_overlay import RecordCountdown
from .bridge import (
    HotkeyBridge,
    PlaybackBridge,
    RecorderBridge,
    SequenceBridge,
)
from .live_monitor import LiveInputMonitor
from .overlay import MiniOverlay
from .scrolling import enable_smooth_scroll
from .settings_panel import HelpMark, SettingsDialog
from .tester_window import TesterWindow
from .theme import get_theme
from .titlebar import TitleBar
from .widgets.activity_log import ActivityLog, is_motion_event
from .widgets.controller_widget import ControllerWidget
from .widgets.duration_picker import DurationPicker, format_duration
from .widgets.keyboard_widget import KeyboardWidget
# kb_active_hkl / kb_layout_labels stay importable here: tests patch them
# on this module and ui.window.live_feed resolves them through it.
from .widgets.keyboard_widget import active_layout_hkl as kb_active_hkl
from .widgets.keyboard_widget import layout_labels as kb_layout_labels
from .widgets.mouse_widget import MouseWidget
from .widgets import recording_list as rl
from .widgets.status_pill import IDLE, RECORDING, StatusPill
from .widgets.stick_widget import StickWidget
from .widgets.trigger_bar import TriggerBar
from .window import (
    ChromeMixin,
    DeckMixin,
    DockingMixin,
    DockTab,
    LiveFeedMixin,
    PlaybackMixin,
    SchemesMixin,
)
from .window.deck import ROW_H
from .persist import app_settings

log = logging.getLogger(__name__)


def _non_modifier_reps(*specs: str) -> tuple[str, ...]:
    """pynput reps of every non-modifier hotkey key — excluded from
    recordings entirely (they're control keys, never macro content)."""
    reps: set[str] = set()
    for spec in specs:
        for name in parse_combo(spec):
            if name not in MODIFIERS:
                reps.add(f"char:{name}" if len(name) == 1 else f"key:{name}")
    return tuple(reps)


class MainWindow(DockingMixin, ChromeMixin, SchemesMixin, DeckMixin,
                 LiveFeedMixin, PlaybackMixin, QMainWindow):
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
        # and sequence estimates never re-parse unchanged files. Persisted
        # across launches so startup only parses NEW/CHANGED takes — a
        # big library used to freeze the first deck refresh.
        self._info_cache: dict[str, tuple] = {}
        self._dur_cache: dict[str, tuple] = {}
        self._load_deck_cache()
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
        if app_settings().value(
                "right_collapsed", True, type=bool):
            self._toggle_right_panel(force_collapsed=True)
        if app_settings().value(
                "docked", False, type=bool):
            self._enter_dock(app_settings().value("dock_side", None))
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
        # Space/Enter guard (see eventFilter). Installed on the WINDOW,
        # not the application: every clickable is NoFocus, so key events
        # only ever reach the deck list (own filter above), the exempt
        # text/spin fields, or fall through to the window — which is
        # exactly what this filter sees. An app-wide filter would drag
        # every mouse-move/paint/timer event through Python for nothing.
        self.installEventFilter(self)
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

    # ------------------------------------------------------------ side dock
    def _on_strip_clicked(self) -> None:
        if getattr(self, "_docked", False):
            self._toggle_drawer()
        else:
            self._toggle_right_panel()

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

    def is_playing(self) -> bool:
        return self._playback_active

    def is_simulating(self) -> bool:
        return self._simulating

    def is_temp_recording(self) -> bool:
        """True while a TEST MODE (temp) take is being recorded."""
        return (self.recorder is not None and self.recorder.is_recording
                and getattr(self, "_temp_rec", False))

    def describe_event(self, ev: MacroEvent) -> str:
        return self.activity.describe(ev)

    def refresh_recordings(self, select: str | None = None) -> None:
        self._refresh_recordings(select)

    # ------------------------------------------------------------- logging
    def _log(self, text: str, color_hex: str | None = None) -> None:
        self.activity.add_line(text,
                               QColor(color_hex) if color_hex else None)
        self.overlay.set_last_line(text)

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

    # ------------------------------------------------------------- lifecycle
    def closeEvent(self, event: QCloseEvent) -> None:
        self.removeEventFilter(self)
        self._tick_timer.stop()  # no ticks during teardown
        # Kill anything still scheduled: a pending playback launch or an
        # armed record countdown must not run injection code mid-teardown
        self._playback_active = False
        self._launch_gen = getattr(self, "_launch_gen", 0) + 1
        self._rec_arming = False
        self.rec_countdown.stop()
        self._save_deck_cache()
        settings = app_settings()
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
