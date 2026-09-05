"""Playback orchestration (arming, engines, progress callbacks) -
extracted verbatim from ui.main_window."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor

from core.controllers.base import neutral_state
from core.events import MacroEvent, MacroFile
from core.playback.engine import INFINITE, PlaybackEngine
from core.playback.virtual_output import (
    ensure_vgamepad,
    launch_vigem_installer,
    vigem_driver_installed,
)
from core.sequence import Sequence, SequenceEngine

from ui import main_window as _mw

from .. import sounds
from ..dialogs import alert, confirm
from ..widgets.duration_picker import format_duration
from ..widgets.status_pill import IDLE, PLAYING


class PlaybackMixin:
    """Playback methods mixed into MainWindow (plain class, no
    Qt base): self.* attributes come from MainWindow.__init__."""

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
            seq = Sequence.load(_mw.SEQUENCES_DIR / name)
            steps = seq.resolve(_mw.RECORDINGS_DIR)
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
