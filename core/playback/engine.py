"""Playback engine — precise scheduling, loop modes, abortable.

Headless: progress is reported through plain callbacks (the UI layer wraps
these into Qt signals — core never imports Qt). Runs on its own thread.

Loop modes: play once, N times, or infinite until aborted.
Timing: hybrid sleep/busy-wait per event against absolute target times, with
winmm.timeBeginPeriod(1) active for the whole run (see core.timing).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..events import MacroFile
from ..timing import TimerResolution, boost_thread_priority, precise_wait_until
from .touch import adapt_events
from .virtual_output import VirtualOutput, get_cursor_pos, set_cursor_pos

log = logging.getLogger(__name__)

INFINITE = 0  # loop_count sentinel


@dataclass
class TimingStats:
    """Accumulates scheduling error across runs (shared by the single-macro
    engine and the sequence engine)."""
    total: float = 0.0
    mx: float = 0.0
    n: int = 0

    def add(self, err: float) -> None:
        self.total += err
        self.mx = max(self.mx, err)
        self.n += 1

    @property
    def avg(self) -> float:
        return self.total / self.n if self.n else 0.0


def play_events(
    events,
    output,
    should_abort: Callable[[], bool],
    on_event: Callable[[object], None] = lambda ev: None,
    on_progress: Callable[[int, int], None] = lambda done, total: None,
    stats: TimingStats | None = None,
    t0: float | None = None,
) -> bool:
    """Play one macro's events against absolute targets measured from t0
    (default: now). Returns True if aborted mid-run. This is THE inner
    loop — every replay path in the app (single macro, sequence step,
    tester) schedules through here so timing behavior is identical."""
    total = len(events)
    if t0 is None:
        t0 = time.perf_counter()
    for i, ev in enumerate(events):
        if should_abort():
            return True
        target = t0 + ev.t
        precise_wait_until(target, should_abort=should_abort)
        if should_abort():
            return True
        if stats is not None:
            stats.add(abs(time.perf_counter() - target))
        output.send(ev)
        on_event(ev)
        if i % 16 == 0 or i == total - 1:
            on_progress(i + 1, total)
    return False


@dataclass
class PlaybackCallbacks:
    """All optional; called from the playback thread."""
    on_run_started: Callable[[int, int], None] = lambda run, total: None
    on_progress: Callable[[int, int], None] = lambda done, total: None
    on_event: Callable[[object], None] = lambda ev: None
    on_finished: Callable[[bool, str], None] = lambda aborted, msg: None
    on_timing: Callable[[float, float], None] = lambda avg, mx: None
    on_debug: Callable[[str], None] = lambda line: None


def pointer_accel_enabled() -> bool:
    """Windows 'Enhance pointer precision'. When ON, raw-count replay of
    HUMAN motion cannot reproduce the original cursor path (the accel
    curve is velocity-dependent and coalesced counts present different
    velocities than the live packet stream did)."""
    if sys.platform != "win32":  # pragma: no cover
        return False
    import ctypes
    params = (ctypes.c_int * 3)()
    ctypes.windll.user32.SystemParametersInfoW(0x0003, 0, params, 0)
    return bool(params[2])


def replay_debug_summary(original, adapted, screen, force_abs) -> str:
    """One line describing exactly HOW this replay will run — surfaced
    in the activity log and app.log so misbehavior is diagnosable."""
    n_rel = sum(1 for e in adapted if e.src == "mouse_move")
    n_abs = sum(1 for e in adapted if e.src == "mouse_abs")
    n_touch = sum(1 for e in adapted if e.src == "touch")
    from .touch import virtual_screen_rect
    cur = virtual_screen_rect()
    return (f"mouse replay={'PATH(abs)' if n_abs and not n_rel else 'RAW(rel)' if n_rel and not n_abs else 'mixed' if n_abs else 'none'}"
            f" · moves rel={n_rel} abs={n_abs} touch={n_touch}"
            f" · exact-path setting={'ON' if force_abs else 'off'}"
            f" · recorded screen={screen or 'none'}"
            f" · current screen={cur or 'n/a'}"
            f" · adapted={'YES' if adapted is not original else 'no'}")


@dataclass
class PlaybackEngine:
    macro: MacroFile
    loop_count: int = 1  # INFINITE (0) = until aborted
    loop_delay: float = 1.0
    # Replay the exact recorded cursor path (absolute) instead of raw
    # relative counts — deterministic on touchpads/any pointer settings
    force_abs_mouse: bool = False
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

        # Adapt to the current screen (and, if requested, replay the
        # exact cursor path instead of raw counts)
        events = adapt_events(self.macro.events, self.macro.screen,
                              self.force_abs_mouse)
        dbg = replay_debug_summary(self.macro.events, events,
                                   self.macro.screen, self.force_abs_mouse)
        log.info("Playback debug: %s", dbg)
        cb.on_debug(dbg)
        if (any(e.src == "mouse_move" for e in events)
                and pointer_accel_enabled()):
            cb.on_debug(
                "note: Windows 'Enhance pointer precision' is ON — "
                "raw-count replay of hand motion will drift. For "
                "pixel-accurate clicks enable Settings > Playback > "
                "'Replay exact cursor path' (games still want raw).")
        stats = TimingStats()
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
                    aborted = play_events(
                        events, output, self._abort.is_set,
                        on_event=cb.on_event, on_progress=cb.on_progress,
                        stats=stats,
                    )
                    output.release_all()  # nothing sticks between runs
                    # Per-run cursor drift: where the cursor actually
                    # ended vs where the run started (relative replays
                    # drift when pointer settings shape counts unevenly)
                    if has_mouse:
                        end = get_cursor_pos()
                        cb.on_debug(
                            f"run {run}: cursor "
                            f"{anchor or 'unanchored'} -> {end}")
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
            cb.on_timing(stats.avg, stats.mx)
            aborted = aborted or self._abort.is_set()
            log.info(
                "Playback %s after %d run(s): avg timing error %.3fms, max %.3fms",
                "aborted" if aborted else "finished",
                completed, stats.avg * 1000, stats.mx * 1000,
            )
            msg = "Aborted" if aborted else f"Finished {completed} run(s)"
            cb.on_finished(aborted, msg)
