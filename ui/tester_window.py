"""Fullscreen live input test: every device visualized edge-to-edge —
keys as you press them, mouse motion and clicks, analog sticks and
triggers, controller buttons. Esc exits.

Also actionable: run built-in test presets (real injected mouse motion and
typing, plus visual pad sweeps) to see input flow without touching your
devices, or hit Record to capture a custom take while you test.

Fed each UI tick by the main window with the same snapshot/state the small
tabs use, so it costs nothing extra beyond painting bigger widgets.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.events import MacroEvent
from core.playback.virtual_output import send_relative_move

try:
    from pynput import keyboard as pk

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    pk = None
    PYNPUT_AVAILABLE = False

from .branding import make_logo
from .theme import Theme
from .widgets.controller_widget import ControllerWidget
from .widgets.keyboard_widget import KeyboardWidget
from .widgets.mouse_widget import MouseWidget
from .widgets.stick_widget import StickWidget
from .widgets.trigger_bar import TriggerBar

PAD_BUTTON_TOUR = [
    "A", "B", "X", "Y", "LEFT_SHOULDER", "RIGHT_SHOULDER",
    "DPAD_UP", "DPAD_RIGHT", "DPAD_DOWN", "DPAD_LEFT",
    "BACK", "START", "LEFT_THUMB", "RIGHT_THUMB",
]


class TesterWindow(QWidget):
    def __init__(self, theme: Theme, host, parent=None):
        super().__init__(None)
        self.theme = theme
        self.host = host
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Window)
        self.setObjectName("testerWindow")

        self._preset_gen = None
        self._preset_cleanup = None
        self._preset_timer = QTimer(self)
        self._preset_timer.setInterval(15)
        self._preset_timer.timeout.connect(self._preset_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("titleBar")
        header.setFixedHeight(38)
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 0, 0, 0)
        h.setSpacing(8)
        self._logo = QLabel()
        self._logo.setPixmap(make_logo(theme, 20, detailed=False))
        h.addWidget(self._logo)
        title = QLabel("TEST MODE")
        title.setObjectName("appTitle")
        h.addWidget(title)
        self.conn_label = QLabel("")
        self.conn_label.setObjectName("statsLabel")
        h.addWidget(self.conn_label)
        h.addSpacing(20)

        # Two labeled clusters split by a divider: injection presets on
        # one side, the temp take's record/replay on the other
        def cluster_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("popupColTitle")
            return lbl

        h.addWidget(cluster_label("PRESETS"))
        self.preset_combo = QComboBox()
        self.preset_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preset_combo.setMinimumWidth(190)
        presets = [
            ("Mouse circle (real input)", self._preset_mouse_circle),
            ("Mouse zigzag (real input)", self._preset_mouse_zigzag),
            ("Typing wave (real input)", self._preset_typing_wave),
            ("Pad stick sweep (visual)", self._preset_pad_sweep),
            ("Pad button tour (visual)", self._preset_pad_buttons),
        ]
        for name, factory in presets:
            self.preset_combo.addItem(name, factory)
        self.preset_combo.setToolTip(
            "Test presets: 'real input' presets inject actual mouse/key "
            "events so the whole pipeline reacts (and records, if you're "
            "recording); 'visual' presets animate the pad widgets directly."
        )
        h.addWidget(self.preset_combo)
        self.run_btn = QPushButton("▶  Run")
        self.run_btn.setObjectName("accentBtn")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.setToolTip("Run the selected preset")
        self.run_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.run_btn.clicked.connect(self._toggle_preset)
        h.addWidget(self.run_btn)

        h.addSpacing(10)
        sep = QFrame()
        sep.setObjectName("headSep")
        sep.setFixedSize(1, 20)
        h.addWidget(sep)
        h.addSpacing(10)

        h.addWidget(cluster_label("TEST TAKE"))
        self.rec_btn = QPushButton("●  Record")
        self.rec_btn.setObjectName("recordStepBtn")
        self.rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rec_btn.setToolTip(
            "Record a temp test take — saved as test_take.json and "
            "overwritten by the next test recording. Everything you (or a "
            "real-input preset) do is captured."
        )
        self.rec_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rec_btn.clicked.connect(self._toggle_record)
        h.addWidget(self.rec_btn)
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("accentBtn")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip(
            "Replay the selected recording (after a test recording, that's "
            "your fresh test take) — watch it happen live on every "
            "visualizer below"
        )
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.clicked.connect(self._toggle_play)
        h.addWidget(self.play_btn)

        h.addStretch(1)
        esc_hint = QLabel("Esc to exit")
        esc_hint.setObjectName("dim")
        h.addWidget(esc_hint)
        close = QPushButton("")  # MDL2 ChromeClose
        close.setObjectName("winClose")
        close.setFixedSize(44, 38)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.close)
        h.addWidget(close)
        root.addWidget(header)

        # --- top: LT | controller with sticks beneath | RT
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.trigger_l = TriggerBar(theme, "LT")
        self.trigger_l.setFixedWidth(64)
        top.addWidget(self.trigger_l)
        center = QVBoxLayout()
        center.setSpacing(0)
        self.controller_w = ControllerWidget(theme)
        center.addWidget(self.controller_w, 5)
        sticks = QHBoxLayout()
        sticks.setSpacing(24)
        self.stick_l = StickWidget(theme, "LS")
        self.stick_r = StickWidget(theme, "RS")
        sticks.addStretch(1)
        sticks.addWidget(
            self.stick_l, 1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        sticks.addWidget(
            self.stick_r, 1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        sticks.addStretch(1)
        center.addLayout(sticks, 4)
        top.addLayout(center, 1)
        self.trigger_r = TriggerBar(theme, "RT")
        self.trigger_r.setFixedWidth(64)
        top.addWidget(self.trigger_r)

        # --- bottom: keyboard | mouse (with breathing room between)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(22)
        self.keyboard_w = KeyboardWidget(theme)
        bottom.addWidget(self.keyboard_w, 7)
        self.mouse_w = MouseWidget(theme)
        bottom.addWidget(self.mouse_w, 3)

        content = QVBoxLayout()
        content.setContentsMargins(16, 12, 16, 16)
        content.setSpacing(12)
        content.addLayout(top, 9)
        content.addLayout(bottom, 11)
        root.addLayout(content, 1)

    # ------------------------------------------------------------- lifecycle
    def open(self) -> None:
        self.showFullScreen()
        self.activateWindow()

    def set_scheme(self, art: str, layout: str) -> None:
        self.controller_w.set_art(art, layout)

    def set_conn_text(self, text: str, color: str) -> None:
        self.conn_label.setText(text)
        self.conn_label.setStyleSheet(
            f"color: {color}; font-family: Consolas, monospace;"
            "font-size: 11px;")

    def feed(self, snap: dict, state: dict, connected: bool) -> None:
        """Push one UI frame of live input into every visualizer."""
        self.keyboard_w.frame(snap["keys"], snap["key_pulses"])
        self.mouse_w.frame(snap["mouse_buttons"], snap["move"],
                           snap["scroll"])
        self.controller_w.frame(state, connected)
        self.stick_l.set_target(state["lx"], state["ly"],
                                "LEFT_THUMB" in state["buttons"])
        self.stick_r.set_target(state["rx"], state["ry"],
                                "RIGHT_THUMB" in state["buttons"])
        self.trigger_l.set_target(state["lt"])
        self.trigger_r.set_target(state["rt"])
        recording = (self.host.recorder is not None
                     and self.host.recorder.is_recording)
        wanted = "■  Stop Rec" if recording else "●  Record"
        if self.rec_btn.text() != wanted:
            self.rec_btn.setText(wanted)
        playing = self.host._playback_active
        wanted = "■  Stop Play" if playing else "▶  Play"
        if self.play_btn.text() != wanted:
            self.play_btn.setText(wanted)
        self.play_btn.setEnabled(not recording)
        self.rec_btn.setEnabled(not playing)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._stop_preset()
        # A temp take in progress is finished (and saved to test_take.json)
        # rather than left recording invisibly.
        if (self.host.recorder is not None
                and self.host.recorder.is_recording
                and getattr(self.host, "_temp_rec", False)):
            self.host.toggle_record()
        super().closeEvent(event)

    def _toggle_record(self) -> None:
        # A visual preset owns the sim flag which blocks recording — stop
        # it first so Record always responds.
        if self.host._simulating:
            self._stop_preset()
        self.host.toggle_record(temp=True)

    def _toggle_play(self) -> None:
        if self.host._playback_active:
            self.host.abort_playback()
            return
        if self.host._simulating:
            self._stop_preset()
        self.host.start_playback()

    # ------------------------------------------------------------- presets
    def _toggle_preset(self) -> None:
        if self._preset_gen is not None:
            self._stop_preset()
            return
        factory = self.preset_combo.currentData()
        result = factory()
        if result is None:
            return
        self._preset_gen, self._preset_cleanup = result
        self.run_btn.setText("■ Stop")
        self._preset_timer.start()

    def _preset_tick(self) -> None:
        try:
            next(self._preset_gen)
        except (StopIteration, RuntimeError):
            self._stop_preset()

    def _stop_preset(self) -> None:
        self._preset_timer.stop()
        if self._preset_cleanup is not None:
            try:
                self._preset_cleanup()
            finally:
                self._preset_cleanup = None
        self._preset_gen = None
        self.run_btn.setText("▶ Run")

    # Real-input presets: inject genuine OS events, so the monitor, the
    # visualizers, and (if recording) the recorder all see them.
    def _preset_mouse_circle(self):
        def gen():
            steps, radius = 140, 110.0
            px, py = radius, 0.0
            for i in range(1, steps + 1):
                ang = 2 * math.pi * i / steps
                x, y = radius * math.cos(ang), radius * math.sin(ang)
                send_relative_move(round(x - px), round(y - py))
                px, py = x, y
                yield
        return gen(), None

    def _preset_mouse_zigzag(self):
        def gen():
            for dx, dy, ticks in ((6, 2, 25), (-6, 4, 25), (6, -4, 25),
                                  (-6, -2, 25)):
                for _ in range(ticks):
                    send_relative_move(dx, dy)
                    yield
        return gen(), None

    def _preset_typing_wave(self):
        if not PYNPUT_AVAILABLE:
            return None
        kb = pk.Controller()

        def gen():
            for ch in "qwertyuiop" + "poiuytrewq":
                kb.press(ch)
                yield
                kb.release(ch)
                yield
                yield
        return gen(), None

    # Visual presets: drive the pad widgets through the same path playback
    # uses — nothing is injected into Windows.
    def _preset_pad_sweep(self):
        host = self.host
        host.set_simulating(True)

        def gen():
            steps = 160
            for i in range(steps):
                ang = 2 * math.pi * i / steps
                host.feed_visual_event(MacroEvent(0, "pad_axis", {
                    "stick": "left",
                    "x": round(math.cos(ang), 3),
                    "y": round(math.sin(ang), 3)}))
                host.feed_visual_event(MacroEvent(0, "pad_axis", {
                    "stick": "right",
                    "x": round(math.cos(-ang), 3),
                    "y": round(math.sin(-ang), 3)}))
                tri = abs((i % 80) - 40) / 40.0
                host.feed_visual_event(MacroEvent(0, "pad_trigger", {
                    "trigger": "left", "value": round(tri, 3)}))
                host.feed_visual_event(MacroEvent(0, "pad_trigger", {
                    "trigger": "right", "value": round(1 - tri, 3)}))
                yield
        return gen(), lambda: host.set_simulating(False)

    def _preset_pad_buttons(self):
        host = self.host
        host.set_simulating(True)

        def gen():
            for name in PAD_BUTTON_TOUR:
                host.feed_visual_event(MacroEvent(0, "pad_btn", {
                    "button": name, "action": "down"}))
                for _ in range(8):
                    yield
                host.feed_visual_event(MacroEvent(0, "pad_btn", {
                    "button": name, "action": "up"}))
                for _ in range(3):
                    yield
        return gen(), lambda: host.set_simulating(False)
