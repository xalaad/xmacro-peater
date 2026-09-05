"""Qt signal bridges: core/ reports through plain callbacks (it never
imports Qt); these QObjects turn those callbacks into signals, which Qt
automatically queues onto the UI thread when emitted from worker threads."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core.events import MacroEvent
from core.playback.engine import PlaybackCallbacks
from core.sequence import SequenceCallbacks


class RecorderBridge(QObject):
    event_captured = Signal(object)  # MacroEvent

    def on_event(self, ev: MacroEvent) -> None:  # called from capture threads
        self.event_captured.emit(ev)


class PlaybackBridge(QObject):
    run_started = Signal(int, int)
    progress = Signal(int, int)
    event_played = Signal(object)  # MacroEvent
    finished = Signal(bool, str)
    timing = Signal(float, float)
    debug = Signal(str)

    def callbacks(self) -> PlaybackCallbacks:
        return PlaybackCallbacks(
            on_run_started=self.run_started.emit,
            on_progress=self.progress.emit,
            on_event=self.event_played.emit,
            on_finished=self.finished.emit,
            on_timing=self.timing.emit,
            on_debug=self.debug.emit,
        )


class SequenceBridge(QObject):
    pass_started = Signal(int, int)
    step_started = Signal(int, int, str, int, int)
    event_played = Signal(object)  # MacroEvent
    finished = Signal(bool, str)
    timing = Signal(float, float)
    debug = Signal(str)

    def callbacks(self) -> SequenceCallbacks:
        return SequenceCallbacks(
            on_pass_started=self.pass_started.emit,
            on_step_started=self.step_started.emit,
            on_event=self.event_played.emit,
            on_finished=self.finished.emit,
            on_timing=self.timing.emit,
            on_debug=self.debug.emit,
        )


class HotkeyBridge(QObject):
    """Global hotkey presses arrive on the pynput listener thread; re-emit
    as signals so slots run on the UI thread."""
    record_toggle = Signal()
    play_last = Signal()
    abort_playback = Signal()
