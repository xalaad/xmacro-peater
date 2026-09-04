"""Precision timing: drift-corrected tickers and hybrid sleep/busy-wait.

Naive `sleep(1/hz)` loops accumulate drift because each iteration's work and
sleep-overshoot push every subsequent tick later. DriftCorrectedTicker
schedules *absolute* tick times from a fixed origin instead, so error never
accumulates.

precise_wait_until() sleeps coarsely until ~2ms before the target, then
busy-waits on perf_counter for the final stretch — sub-millisecond accuracy
without burning a full core the whole time. Combine with TimerResolution
(winmm.timeBeginPeriod) so the coarse sleeps themselves wake within ~1ms.
"""
from __future__ import annotations

import ctypes
import sys
import time

BUSY_WAIT_WINDOW = 0.003  # seconds of busy-wait before the target


def boost_thread_priority() -> bool:
    """Raise the calling thread to TIME_CRITICAL scheduling priority.

    Playback and polling must hit sub-millisecond targets *while a game is
    hammering every core* — at normal priority the Windows scheduler will
    happily park us for a 3-15ms quantum behind the game's threads. Call
    this at the top of every timing-critical thread. No-op off Windows.
    """
    if sys.platform != "win32":
        return False
    THREAD_PRIORITY_TIME_CRITICAL = 15
    kernel32 = ctypes.windll.kernel32
    return bool(kernel32.SetThreadPriority(
        kernel32.GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL))


class TimerResolution:
    """Context manager that drops Windows' timer granularity to 1ms."""

    def __init__(self, period_ms: int = 1):
        self.period_ms = period_ms
        self._active = False

    def __enter__(self) -> "TimerResolution":
        if sys.platform == "win32":
            try:
                ctypes.WinDLL("winmm").timeBeginPeriod(self.period_ms)
                self._active = True
            except OSError:
                pass
        return self

    def __exit__(self, *exc) -> None:
        if self._active:
            ctypes.WinDLL("winmm").timeEndPeriod(self.period_ms)
            self._active = False


def precise_wait_until(target: float, should_abort=None) -> None:
    """Wait until time.perf_counter() >= target.

    should_abort: optional zero-arg callable checked during the wait; the
    wait returns early when it goes truthy.
    """
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return
        if should_abort is not None and should_abort():
            return
        if remaining > BUSY_WAIT_WINDOW:
            time.sleep(min(remaining - BUSY_WAIT_WINDOW, 0.05))
        else:
            # Final stretch: spin on perf_counter for sub-ms accuracy.
            while time.perf_counter() < target:
                if should_abort is not None and should_abort():
                    return
            return


class DriftCorrectedTicker:
    """Fixed-rate ticker with absolute scheduling and drift statistics.

    Usage:
        ticker = DriftCorrectedTicker(125)
        while running:
            ticker.wait_next()   # blocks until the next absolute tick time
            do_work()
    """

    def __init__(self, hz: float):
        if hz <= 0:
            raise ValueError("hz must be positive")
        self.period = 1.0 / hz
        self._origin = time.perf_counter()
        self._tick = 0
        self._drift_total = 0.0
        self._drift_max = 0.0
        self._samples = 0

    def wait_next(self, should_abort=None) -> float:
        """Block until the next tick. Returns this tick's absolute drift (s)."""
        self._tick += 1
        target = self._origin + self._tick * self.period
        precise_wait_until(target, should_abort)
        drift = abs(time.perf_counter() - target)
        self._drift_total += drift
        self._drift_max = max(self._drift_max, drift)
        self._samples += 1
        return drift

    @property
    def average_drift(self) -> float:
        return self._drift_total / self._samples if self._samples else 0.0

    @property
    def max_drift(self) -> float:
        return self._drift_max

    @property
    def ticks(self) -> int:
        return self._samples

    def stats_line(self) -> str:
        return (
            f"{self._samples} ticks @ {1.0 / self.period:.0f}Hz, "
            f"avg drift {self.average_drift * 1000:.3f}ms, "
            f"max drift {self.max_drift * 1000:.3f}ms"
        )
