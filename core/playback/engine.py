"""Playback engine — precise scheduling, loop modes, abortable.

Headless: progress is reported through plain callbacks (the UI layer wraps
these into Qt signals — core never imports Qt). Runs on its own thread.

Loop modes: play once, N times, or infinite until aborted.
Timing: hybrid sleep/busy-wait per event against absolute target times, with
winmm.timeBeginPeriod(1) active for the whole run (see core.timing).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..events import MacroFile
from ..timing import TimerResolution, boost_thread_priority, precise_wait_until
from .virtual_output import VirtualOutput, get_cursor_pos, set_cursor_pos

log = logging.getLogger(__name__)

INFINITE = 0  # loop_count sentinel


@dataclass
class PlaybackCallbacks:
    """All optional; called from the playback thread."""
    on_run_started: Callable[[int, int], None] = lambda run, total: None
    on_progress: Callable[[int, int], None] = lambda done, total: None
    on_event: Callable[[object], None] = lambda ev: None
    on_finished: Callable[[bool, str], None] = lambda aborted, msg: None
    on_timing: Callable[[float, float], None] = lambda avg, mx: None


@dataclass
class PlaybackEngine:
    macro: MacroFile
    loop_count: int = 1  # INFINITE (0) = until aborted
    loop_delay: float = 1.0
    callbacks: PlaybackCallbacks = field(default_factory=PlaybackCallbacks)

    def __post_init__(self):
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_playing:
            return
        self._abort.clear()
        self._thread = threading.Thread(
            target=self._run, name="PlaybackEngine", daemon=True
        )
        self._thread.start()

    def abort(self) -> None:
        self._abort.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    def _run(self) -> None:
        boost_thread_priority()  # stay sub-ms even under full game load
        cb = self.callbacks
        aborted = False
        try:
            output = VirtualOutput(need_gamepad=self.macro.has_pad_events)
        except RuntimeError as e:
            cb.on_finished(True, str(e))
            return

        events = self.macro.events
        total = len(events)
        err_total = 0.0
        err_max = 0.0
        err_n = 0
        run = 0
        completed = 0
        # Repeat-cycle fidelity: relative mouse replay compounds across
        # runs (run 2 starts wherever run 1 left the cursor, so any net
        # displacement drifts further every cycle). Anchor the cursor at
        # run 1 and restore it before every later run so each cycle
        # starts from the identical position — moves land the same, and
        # clicks hit the same spots.
        has_mouse = any(e.src.startswith("mouse") for e in events)
        anchor: tuple[int, int] | None = None
        try:
            with TimerResolution(1):
                while not self._abort.is_set():
                    run += 1
                    if self.loop_count != INFINITE and run > self.loop_count:
                        break
                    if has_mouse:
                        if anchor is None:
                            anchor = get_cursor_pos()
                        else:
                            set_cursor_pos(*anchor)
                    cb.on_run_started(run, self.loop_count)
                    t0 = time.perf_counter()

                    for i, ev in enumerate(events):
                        if self._abort.is_set():
                            aborted = True
                            break
                        target = t0 + ev.t
                        precise_wait_until(target, should_abort=self._abort.is_set)
                        if self._abort.is_set():
                            aborted = True
                            break
                        err = abs(time.perf_counter() - target)
                        err_total += err
                        err_max = max(err_max, err)
                        err_n += 1
                        output.send(ev)
                        cb.on_event(ev)
                        if i % 16 == 0 or i == total - 1:
                            cb.on_progress(i + 1, total)

                    output.release_all()  # nothing sticks between runs
                    if aborted:
                        break
                    completed += 1
                    if self.loop_count == INFINITE or run < self.loop_count:
                        end = time.perf_counter() + self.loop_delay
                        precise_wait_until(end, should_abort=self._abort.is_set)
                        if self._abort.is_set():
                            break
        finally:
            output.close()
            avg = err_total / err_n if err_n else 0.0
            cb.on_timing(avg, err_max)
            aborted = aborted or self._abort.is_set()
            log.info(
                "Playback %s after %d run(s): avg timing error %.3fms, max %.3fms",
                "aborted" if aborted else "finished",
                completed, avg * 1000, err_max * 1000,
            )
            msg = "Aborted" if aborted else f"Finished {completed} run(s)"
            cb.on_finished(aborted, msg)
