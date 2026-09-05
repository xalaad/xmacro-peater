"""Settings dialog — sectioned, every setting explained with a tip, and
every control applies to AppConfig and saves to app_config.json the moment
it changes (no OK/Apply dance).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.config import SCHEMES_DIR, AppConfig, save_config
from core.controllers.pygame_backend import Scheme
from core.hotkeys import parse_combo

from .dialogs import FramelessDialog, alert, confirm
from .scrolling import enable_smooth_scroll
from .widgets.duration_picker import DurationPicker


def _valid_combo(spec: str, fallback: str) -> str:
    try:
        parse_combo(spec)
        return spec.lower().replace(" ", "")
    except (ValueError, AttributeError):
        return fallback


def _rich(tip: str) -> str:
    """Rich-text tooltips word-wrap into a readable block instead of one
    endless line."""
    return f"<qt><p style='white-space:normal'>{tip}</p></qt>"


class HelpMark(QLabel):
    """(?) marker that shows its explanation instantly on hover — no
    tooltip delay."""

    def __init__(self, tip: str, parent=None):
        super().__init__("(?)", parent)
        self.setObjectName("helpMark")
        self._tip = _rich(tip)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)

    def enterEvent(self, event) -> None:
        QToolTip.showText(QCursor.pos(), self._tip, self)
        super().enterEvent(event)


class SettingsDialog(FramelessDialog):
    settings_changed = Signal()
    scheme_imported = Signal()

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__("Settings", parent)
        self.cfg = cfg
        self._ready = False  # suppress saves while building the UI
        self.setMinimumSize(470, 420)
        self.resize(490, 560)

        outer = self.body
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Content must always FIT the width — vertical scrolling only
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self._content = QVBoxLayout(content)
        self._content.setContentsMargins(0, 0, 8, 0)
        self._content.setSpacing(8)
        scroll.setWidget(content)
        enable_smooth_scroll(scroll)
        outer.addWidget(scroll, 1)

        self._build_recording_section()
        self._build_playback_section()
        self._build_hotkeys_section()
        self._build_overlay_section()
        self._build_interface_section()
        self._build_schemes_section()
        self._content.addStretch(1)

        self._ready = True

    # ------------------------------------------------------------- helpers
    def _section(self, title: str) -> QVBoxLayout:
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        header = QLabel(title.upper())
        header.setObjectName("sectionTitle")
        lay.addWidget(header)
        self._content.addWidget(box)
        return lay

    def _setting(self, section: QVBoxLayout, title: str,
                 control: QWidget, tip: str) -> None:
        """Compact row: title, (?) hover-help, control. The explanation
        lives in the tooltip — hover the (?) or the label."""
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(title)
        label.setToolTip(_rich(tip))
        control.setToolTip(_rich(tip))
        row.addWidget(label)
        row.addWidget(HelpMark(tip))
        row.addStretch(1)
        row.addWidget(control)
        section.addLayout(row)

    def _slider_setting(self, section: QVBoxLayout, title: str,
                        slider: QSlider, value_label: QLabel,
                        tip: str) -> None:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        slider.setFixedWidth(130)
        value_label.setFixedWidth(34)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(slider)
        h.addWidget(value_label)
        self._setting(section, title, wrap, tip)

    def _check_row(self, section: QVBoxLayout, checkbox: QCheckBox,
                   tip: str) -> None:
        """Checkbox row with the same (?) hover-help as every other
        setting — no row goes unexplained."""
        checkbox.setToolTip(_rich(tip))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(checkbox)
        row.addWidget(HelpMark(tip))
        row.addStretch(1)
        section.addLayout(row)

    # ------------------------------------------------------------- sections
    def _build_recording_section(self) -> None:
        s = self._section("Recording")
        cfg = self.cfg

        self.poll = QSpinBox()
        self.poll.setRange(10, 1000)
        self.poll.setSuffix(" Hz")
        self.poll.setValue(cfg.poll_hz)
        self.poll.valueChanged.connect(self._apply)
        self._setting(
            s, "Controller poll rate", self.poll,
            "How many times per second the controller is sampled while "
            "recording. 125 Hz matches a standard gamepad's own report rate "
            "— raising it rarely captures more, but costs CPU. Applies to "
            "the next recording you start.",
        )

        self.rec_countdown = QDoubleSpinBox()
        self.rec_countdown.setRange(0, 60)
        self.rec_countdown.setDecimals(1)
        self.rec_countdown.setSuffix(" s")
        self.rec_countdown.setValue(cfg.record_countdown)
        self.rec_countdown.valueChanged.connect(self._apply)
        self._setting(
            s, "Record countdown", self.rec_countdown,
            "Heads-up before a recording starts: a click-through ring "
            "with ticking seconds appears over the screen so you can get "
            "into position — input keeps flowing, nothing is blocked. "
            "Set 0 to start recording instantly.",
        )

        self.stick_dz = QSlider(Qt.Orientation.Horizontal)
        self.stick_dz.setRange(0, 40)
        self.stick_dz.setValue(int(cfg.stick_deadzone * 100))
        self.stick_dz_label = QLabel(f"{self.stick_dz.value()}%")
        self.stick_dz.valueChanged.connect(self._apply)
        self._slider_setting(
            s, "Stick deadzone", self.stick_dz, self.stick_dz_label,
            "Radial noise gate: stick motion inside this circle records as a "
            "clean zero, so a worn stick that drifts at rest doesn't flood "
            "the recording. Values are stored raw — this never rescales "
            "what's recorded. 8% suits most pads.",
        )

        self.trig_dz = QSlider(Qt.Orientation.Horizontal)
        self.trig_dz.setRange(0, 30)
        self.trig_dz.setValue(int(cfg.trigger_deadzone * 100))
        self.trig_dz_label = QLabel(f"{self.trig_dz.value()}%")
        self.trig_dz.valueChanged.connect(self._apply)
        self._slider_setting(
            s, "Trigger deadzone", self.trig_dz, self.trig_dz_label,
            "Same idea for the analog triggers: pressure below this level "
            "records as fully released.",
        )

        self.touch_mode = QCheckBox("Touch mode")
        self.touch_mode.setChecked(cfg.touch_mode)
        self.touch_mode.toggled.connect(self._apply)
        self._check_row(
            s, self.touch_mode,
            "Record taps, drags and swipes as absolute on-screen gestures "
            "and replay them as genuine Windows touch input. Best for "
            "touchscreen apps and UI automation. Leave OFF for games — "
            "they need the default relative mouse deltas for camera look.")

    def _build_playback_section(self) -> None:
        s = self._section("Playback")
        cfg = self.cfg

        self.countdown = DurationPicker()
        self.countdown.setValue(cfg.playback.countdown_seconds)
        self.countdown.valueChanged.connect(self._apply)
        self._setting(
            s, "Start delay (before first run)", self.countdown,
            "Grace period after you press Play, so you can click back into "
            "the game window before input starts. Type any duration (90, "
            "1h 30m) or use the clock panel to SCHEDULE a run — the main "
            "screen shows the exact clock time. Set 0 to start instantly.",
        )

        self.mouse_path = QCheckBox("Replay exact cursor path")
        self.mouse_path.setChecked(cfg.playback.mouse_path_replay)
        self.mouse_path.toggled.connect(self._apply)
        self._check_row(
            s, self.mouse_path,
            "ON (default): mouse motion replays as the exact recorded "
            "cursor positions (absolute) — bypasses pointer speed and "
            "acceleration entirely, so it is pixel-deterministic on ANY "
            "device and settings, including touchpads, and rescales "
            "across screen sizes. Windows' 'Enhance pointer precision' "
            "makes raw-count replay of hand motion drift BY DESIGN (its "
            "accel curve is velocity-dependent) — this mode is immune. "
            "Turn OFF for game camera-look macros: games read raw "
            "relative input before acceleration and need the raw "
            "counts, not cursor positions.")

        self.loop_delay = DurationPicker()
        self.loop_delay.setValue(cfg.playback.loop_delay)
        self.loop_delay.valueChanged.connect(self._apply)
        self._setting(
            s, "Delay between repeats", self.loop_delay,
            "Pause inserted after each run when repeating or looping "
            "forever — type any duration (90, 1h 30m) for long, "
            "time-based repeats. The main screen shows the same value; "
            "changing either updates both.",
        )

    def _build_hotkeys_section(self) -> None:
        s = self._section("Global hotkeys")
        cfg = self.cfg
        combo_tip_tail = (
            "Two-key combos (e.g. ctrl+f9, shift+f5, alt+r) are recommended "
            "— single keys clash with in-game controls. Hotkey presses are "
            "automatically excluded from recordings."
        )

        self.hk_record = QLineEdit(cfg.hotkeys.record_toggle)
        self.hk_record.setFixedWidth(110)
        self.hk_record.editingFinished.connect(self._apply)
        self._setting(
            s, "Start / stop recording", self.hk_record,
            "Toggles recording even while the game has focus. " + combo_tip_tail,
        )

        self.hk_play = QLineEdit(cfg.hotkeys.play_last)
        self.hk_play.setFixedWidth(110)
        self.hk_play.editingFinished.connect(self._apply)
        self._setting(
            s, "Play / repeat", self.hk_play,
            "Plays the recording selected in the list (the newest one by "
            "default) with the loop settings from the main screen.",
        )

        self.hk_abort = QLineEdit(cfg.hotkeys.abort_playback)
        self.hk_abort.setFixedWidth(110)
        self.hk_abort.editingFinished.connect(self._apply)
        self._setting(
            s, "Stop playback", self.hk_abort,
            "Aborts playback instantly and releases every held key and "
            "button so nothing stays stuck.",
        )

    def _build_overlay_section(self) -> None:
        s = self._section("Mini overlay")
        cfg = self.cfg

        self.ov_opacity = QSlider(Qt.Orientation.Horizontal)
        self.ov_opacity.setRange(30, 100)
        self.ov_opacity.setValue(int(cfg.overlay.opacity * 100))
        self.ov_opacity_label = QLabel(f"{self.ov_opacity.value()}%")
        self.ov_opacity.valueChanged.connect(self._apply)
        self._slider_setting(
            s, "Background opacity", self.ov_opacity, self.ov_opacity_label,
            "How solid the overlay card is over your game. Text and buttons "
            "always stay fully readable.",
        )

        self.ov_hints = QCheckBox("Show hotkey hints")
        self.ov_hints.setChecked(cfg.overlay.show_hints)
        self.ov_hints.toggled.connect(self._apply)
        self._check_row(
            s, self.ov_hints,
            "Shows the record/play/stop combos on the overlay. Note: the "
            "overlay needs the game in Borderless/Windowed mode — "
            "exclusive fullscreen bypasses the compositor, and no overlay "
            "(ours or Steam's) can draw over it.")

    def _build_interface_section(self) -> None:
        s = self._section("Interface")
        cfg = self.cfg

        self.fps = QSpinBox()
        self.fps.setRange(15, 144)
        self.fps.setSuffix(" fps")
        self.fps.setValue(cfg.ui_fps)
        self.fps.valueChanged.connect(self._apply)
        self._setting(
            s, "Visualizer refresh cap", self.fps,
            "Upper limit for Input Test animations. Completely independent "
            "from the recording poll rate — lowering this only smooths less, "
            "it never affects capture or playback accuracy.",
        )

        self.sounds = QCheckBox("Sound cues")
        self.sounds.setChecked(cfg.sounds)
        self.sounds.toggled.connect(self._apply)
        self._check_row(
            s, self.sounds,
            "Short distinct beeps for record start/stop, the countdown "
            "tick, playback start, finish, and abort — so you always "
            "know what the app did while the game has focus.")

    def _build_schemes_section(self) -> None:
        s = self._section("Controller schemes")
        import_btn = QPushButton("Import custom scheme…")
        import_btn.setToolTip(
            "A scheme is a JSON file mapping a pad's button/axis indices to "
            "standard names, plus its display labels and artwork. Import "
            "one here or drop it into config/schemes/ — see the README."
        )
        import_btn.clicked.connect(self._import_scheme)
        s.addWidget(import_btn)

        # Escape hatch: whatever got messed up, one click back to stock
        reset = QPushButton("Reset all settings to defaults")
        reset.setObjectName("dangerOutline")
        reset.setToolTip(
            "Restores every setting above to its default value. Your "
            "recordings, sequences, schemes and branding are untouched.")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset_defaults)
        self._content.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)

    def _reset_defaults(self) -> None:
        if not confirm(self, "Reset settings",
                       "Reset every setting to its default?\n\nRecordings, "
                       "sequences, schemes and branding stay untouched.",
                       yes_text="Reset"):
            return
        defaults = AppConfig()
        keep_branding = self.cfg.branding
        for name in AppConfig.model_fields:
            setattr(self.cfg, name, getattr(defaults, name))
        self.cfg.branding = keep_branding
        save_config(self.cfg)
        self._sync_widgets()
        self.settings_changed.emit()

    def _sync_widgets(self) -> None:
        """Push cfg values into every control (no re-save feedback)."""
        cfg = self.cfg
        self._ready = False
        try:
            self.poll.setValue(cfg.poll_hz)
            self.rec_countdown.setValue(cfg.record_countdown)
            self.stick_dz.setValue(int(cfg.stick_deadzone * 100))
            self.stick_dz_label.setText(f"{self.stick_dz.value()}%")
            self.trig_dz.setValue(int(cfg.trigger_deadzone * 100))
            self.trig_dz_label.setText(f"{self.trig_dz.value()}%")
            self.touch_mode.setChecked(cfg.touch_mode)
            self.countdown.setValue(cfg.playback.countdown_seconds)
            self.loop_delay.setValue(cfg.playback.loop_delay)
            self.mouse_path.setChecked(cfg.playback.mouse_path_replay)
            self.hk_record.setText(cfg.hotkeys.record_toggle)
            self.hk_play.setText(cfg.hotkeys.play_last)
            self.hk_abort.setText(cfg.hotkeys.abort_playback)
            self.ov_opacity.setValue(int(cfg.overlay.opacity * 100))
            self.ov_opacity_label.setText(f"{self.ov_opacity.value()}%")
            self.ov_hints.setChecked(cfg.overlay.show_hints)
            self.fps.setValue(cfg.ui_fps)
            self.sounds.setChecked(cfg.sounds)
        finally:
            self._ready = True

    # ------------------------------------------------------------- apply
    def _apply(self, *_):
        if not self._ready:
            return
        self.stick_dz_label.setText(f"{self.stick_dz.value()}%")
        self.trig_dz_label.setText(f"{self.trig_dz.value()}%")
        self.ov_opacity_label.setText(f"{self.ov_opacity.value()}%")
        c = self.cfg
        c.poll_hz = self.poll.value()
        c.record_countdown = float(self.rec_countdown.value())
        c.stick_deadzone = self.stick_dz.value() / 100.0
        c.trigger_deadzone = self.trig_dz.value() / 100.0
        c.hotkeys.record_toggle = _valid_combo(
            self.hk_record.text().strip(), "ctrl+f9")
        c.hotkeys.play_last = _valid_combo(
            self.hk_play.text().strip(), "ctrl+f10")
        c.hotkeys.abort_playback = _valid_combo(
            self.hk_abort.text().strip(), "ctrl+f11")
        c.playback.loop_delay = self.loop_delay.value()
        c.playback.countdown_seconds = float(self.countdown.value())
        c.playback.mouse_path_replay = self.mouse_path.isChecked()
        c.overlay.opacity = self.ov_opacity.value() / 100.0
        c.overlay.show_hints = self.ov_hints.isChecked()
        c.ui_fps = self.fps.value()
        c.sounds = self.sounds.isChecked()
        c.touch_mode = self.touch_mode.isChecked()
        save_config(c)
        self.settings_changed.emit()

    def _import_scheme(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import controller scheme", "", "Scheme JSON (*.json)"
        )
        if not path:
            return
        try:
            Scheme.load(path)  # validate before copying
        except Exception as e:
            alert(self, "Invalid scheme",
                  f"That file isn't a valid scheme:\n{e}")
            return
        SCHEMES_DIR.mkdir(parents=True, exist_ok=True)
        dest = SCHEMES_DIR / Path(path).name
        if dest.exists():
            if not confirm(self, "Overwrite scheme",
                           f"A scheme named {dest.name} already exists. "
                           "Replace it?", yes_text="Replace"):
                return
        shutil.copyfile(path, dest)
        self.scheme_imported.emit()
