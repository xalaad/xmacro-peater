"""Controller button poller — dedicated thread, drift-corrected 125Hz.

Diffs the button set between ticks and emits pad_btn down/up events through
a callback. Records drift stats for the 'avg drift under 1ms' requirement.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from ..controllers.base import ControllerBackend
from ..timing import DriftCorrectedTicker, boost_thread_priority

log = logging.getLogger(__name__)


class ButtonPoller:
    def __init__(
        self,
        backend: ControllerBackend,
        emit: Callable[[dict[str, Any]], None],
        hz: int = 125,
    ):
        self.backend = backend
        self.emit = emit
        self.hz = hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticker: DriftCorrectedTicker | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ButtonPoller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.ticker is not None:
            log.info("ButtonPoller: %s", self.ticker.stats_line())

    def _run(self) -> None:
        boost_thread_priority()
        self.ticker = DriftCorrectedTicker(self.hz)
        prev: set[str] = set()
        aborting = self._stop.is_set
        while not self._stop.is_set():
            self.ticker.wait_next(should_abort=aborting)
            if self._stop.is_set():
                break
            current = self.backend.read()["buttons"]
            for name in sorted(current - prev):
                self.emit({"src": "pad_btn", "button": name, "action": "down"})
            for name in sorted(prev - current):
                self.emit({"src": "pad_btn", "button": name, "action": "up"})
            prev = current
