"""Sequences: ordered chains of recordings played as one precise timeline.

A sequence step names a recording, how many times it runs per pass, and the
pause after each of its runs. The whole chain (a "pass") can then repeat
using the same loop modes as a single recording.

Timing model — drift-free by construction:
- Inside a run, events schedule against absolute targets from the run's t0
  (same inner loop as single-macro playback: engine.play_events).
- The pause AFTER a run is scheduled against `t0 + duration + wait`, not
  "now + wait", so callback/cleanup overhead never pushes the timeline —
  a 1-hour chain lands its last event exactly where arithmetic says.
- The trailing wait of the final step is skipped; the delay between passes
  is the regular loop delay, exactly like single-recording repeats.

Cursor fidelity matches the single-macro engine: the mouse anchor is
captured at the first mouse-run of the chain and restored before every
later mouse-run, so relative deltas never compound across steps or passes.

Headless like the rest of core/ — progress flows through plain callbacks.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .events import MacroFile
from .playback.engine import INFINITE, TimingStats, play_events
from .playback.touch import adapt_events
from .playback.virtual_output import (
    VirtualOutput,
    get_cursor_pos,
    set_cursor_pos,
)
from .timing import TimerResolution, boost_thread_priority, precise_wait_until

log = logging.getLogger(__name__)

FORMAT_NAME = "xmacro-sequence"
FORMAT_VERSION = 1
MAX_STEP_RUNS = 9999
MAX_STEP_WAIT = 86400.0


@dataclass
class SequenceStep:
    recording: str      # file name inside the recordings folder
    runs: int = 1       # times this step plays per pass
    wait: float = 0.0   # pause after each run (skipped at end of pass)

    def to_dict(self) -> dict:
        return {"recording": self.recording, "runs": self.runs,
                "wait": round(self.wait, 3)}

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceStep":
        return cls(
            recording=str(d["recording"]),
            runs=max(1, min(MAX_STEP_RUNS, int(d.get("runs", 1)))),
            wait=max(0.0, min(MAX_STEP_WAIT, float(d.get("wait", 0.0)))),
        )


@dataclass
class Sequence:
    steps: list[SequenceStep] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        payload = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "steps": [s.to_dict() for s in self.steps],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "Sequence":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("format") != FORMAT_NAME:
            raise ValueError(f"{path}: not a sequence file")
        return cls(steps=[SequenceStep.from_dict(d)
                          for d in raw.get("steps", [])])

    def resolve(self, recordings_dir: str | Path
                ) -> list[tuple[SequenceStep, MacroFile]]:
        """Load every step's recording up-front so a broken chain fails
        BEFORE any input is injected, naming exactly what's wrong."""
        recordings_dir = Path(recordings_dir)
        if not self.steps:
            raise ValueError("Sequence has no steps")
        resolved = []
        problems = []
        for i, step in enumerate(self.steps, start=1):
            path = recordings_dir / step.recording
            try:
                resolved.append((step, MacroFile.load(path)))
            except FileNotFoundError:
                problems.append(f"step {i}: {step.recording} is missing")
            except (OSError, ValueError) as e:
                problems.append(f"step {i}: {step.recording} unreadable ({e})")
        if problems:
            raise ValueError("\n".join(problems))
        return resolved

    def pass_duration(self, durations: dict[str, float]) -> float:
        """One pass's length given {recording name: duration}. Trailing
        wait of the last step excluded (loop delay covers pass gaps)."""
        total = 0.0
        for i, s in enumerate(self.steps):
            d = durations.get(s.recording, 0.0)
            total += s.runs * d + s.runs * s.wait
            if i == len(self.steps) - 1:
                total -= s.wait  # final run's wait is skipped
        return max(0.0, total)


@dataclass
class SequenceCallbacks:
    """All optional; called from the sequence thread."""
    on_pass_started: Callable[[int, int], None] = lambda n, total: None
    on_step_started: Callable[[int, int, str, int, int], None] = \
        lambda index, count, name, run, runs: None
    on_event: Callable[[object], None] = lambda ev: None
    on_progress: Callable[[int, int], None] = lambda done, total: None
    on_finished: Callable[[bool, str], None] = lambda aborted, msg: None
    on_timing: Callable[[float, float], None] = lambda avg, mx: None
    on_debug: Callable[[str], None] = lambda line: None


@dataclass
class SequenceEngine:
    """Plays a resolved sequence on its own thread. Same public surface as
    PlaybackEngine (start/abort/join/is_playing) so the UI can hold either
    engine in the same slot."""
    steps: list[tuple[SequenceStep, MacroFile]]
    loop_count: int = 1  # chain passes; INFINITE (0) = until aborted
    loop_delay: float = 1.0
    force_abs_mouse: bool = False
    callbacks: SequenceCallbacks = field(default_factory=SequenceCallbacks)

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
            target=self._run, name="SequenceEngine", daemon=True
        )
        self._thread.start()

    def abort(self) -> None:
        self._abort.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    def _run(self) -> None:
        boost_thread_priority()
        cb = self.callbacks
        need_pad = any(m.has_pad_events for _, m in self.steps)
        try:
            output = VirtualOutput(need_gamepad=need_pad)
        except RuntimeError as e:
            cb.on_finished(True, str(e))
            return

        stats = TimingStats()
        aborted = False
        passes_done = 0
        anchor: tuple[int, int] | None = None
        step_count = len(self.steps)
        last_index = step_count - 1
        mouse_flags = [any(e.src.startswith("mouse") for e in m.events)
                       for _, m in self.steps]
        # Per-step screen adaptation: each recording carries its own
        # recorded-screen rect
        step_events = [adapt_events(m.events, m.screen,
                                    self.force_abs_mouse)
                       for _, m in self.steps]
        from .playback.engine import replay_debug_summary
        for (step, m), adapted in zip(self.steps, step_events):
            dbg = replay_debug_summary(m.events, adapted, m.screen,
                                       self.force_abs_mouse)
            log.info("Sequence step %s debug: %s", step.recording, dbg)
            cb.on_debug(f"{step.recording}: {dbg}")
        try:
            with TimerResolution(1):
                pass_no = 0
                while not self._abort.is_set():
                    pass_no += 1
                    if (self.loop_count != INFINITE
                            and pass_no > self.loop_count):
                        break
                    cb.on_pass_started(pass_no, self.loop_count)

                    for si, (step, macro) in enumerate(self.steps):
                        for rep in range(1, step.runs + 1):
                            if self._abort.is_set():
                                aborted = True
                                break
                            if mouse_flags[si]:
                                if anchor is None:
                                    anchor = get_cursor_pos()
                                else:
                                    set_cursor_pos(*anchor)
                            cb.on_step_started(si, step_count,
                                               step.recording, rep, step.runs)
                            t0 = time.perf_counter()
                            aborted = play_events(
                                step_events[si], output, self._abort.is_set,
                                on_event=cb.on_event,
                                on_progress=cb.on_progress,
                                stats=stats, t0=t0,
                            )
                            output.release_all()
                            if aborted:
                                break
                            # Trailing wait of the pass is the loop delay's
                            # job — skip it here.
                            is_pass_end = (si == last_index
                                           and rep == step.runs)
                            if step.wait > 0 and not is_pass_end:
                                precise_wait_until(
                                    t0 + macro.duration + step.wait,
                                    should_abort=self._abort.is_set)
                        if aborted:
                            break
                    if aborted:
                        break
                    passes_done += 1
                    if (self.loop_count == INFINITE
                            or pass_no < self.loop_count):
                        end = time.perf_counter() + self.loop_delay
                        precise_wait_until(end,
                                           should_abort=self._abort.is_set)
        finally:
            output.close()
            cb.on_timing(stats.avg, stats.mx)
            aborted = aborted or self._abort.is_set()
            log.info(
                "Sequence %s after %d pass(es): avg timing error %.3fms, "
                "max %.3fms",
                "aborted" if aborted else "finished",
                passes_done, stats.avg * 1000, stats.mx * 1000,
            )
            msg = "Aborted" if aborted else f"Finished {passes_done} pass(es)"
            cb.on_finished(aborted, msg)
