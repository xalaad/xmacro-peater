"""Controller stick/trigger poller — dedicated thread, drift-corrected 125Hz.

Records RAW analog values for exact reproduction: the deadzone is used only
as a noise gate (motion inside it records as a clean 0, so idle-stick jitter
doesn't flood the file), never to rescale — the value the game receives on
playback is bit-for-bit what the stick reported when you moved it.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Any, Callable

from ..controllers.base import ControllerBackend
from ..timing import DriftCorrectedTicker, boost_thread_priority

log = logging.getLogger(__name__)

CHANGE_EPSILON = 0.004  # ~1 LSB of a 8-bit trigger; below this = "unchanged"


class AxisPoller:
    def __init__(
        self,
        backend: ControllerBackend,
        emit: Callable[[dict[str, Any]], None],
        hz: int = 125,
        stick_deadzone: float = 0.08,
        trigger_deadzone: float = 0.02,
    ):
        self.backend = backend
        self.emit = emit
        self.hz = hz
        self.stick_deadzone = stick_deadzone
        self.trigger_deadzone = trigger_deadzone
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticker: DriftCorrectedTicker | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="AxisPoller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.ticker is not None:
            log.info("AxisPoller: %s", self.ticker.stats_line())

    def _changed(self, a: float, b: float) -> bool:
        return abs(a - b) > CHANGE_EPSILON

    def _run(self) -> None:
        boost_thread_priority()
        self.ticker = DriftCorrectedTicker(self.hz)
        prev = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0, "lt": 0.0, "rt": 0.0}
        aborting = self._stop.is_set
        while not self._stop.is_set():
            self.ticker.wait_next(should_abort=aborting)
            if self._stop.is_set():
                break
            raw = self.backend.read()

            for stick, kx, ky in (("left", "lx", "ly"), ("right", "rx", "ry")):
                x, y = raw[kx], raw[ky]
                # Noise gate only: inside the radial deadzone = clean zero.
                if math.hypot(x, y) <= self.stick_deadzone:
                    x = y = 0.0
                if self._changed(x, prev[kx]) or self._changed(y, prev[ky]):
                    self.emit({
                        "src": "pad_axis", "stick": stick,
                        "x": round(x, 5), "y": round(y, 5),
                    })
                    prev[kx], prev[ky] = x, y

            for trig, key in (("left", "lt"), ("right", "rt")):
                v = raw[key] if raw[key] > self.trigger_deadzone else 0.0
                if self._changed(v, prev[key]):
                    self.emit({
                        "src": "pad_trigger", "trigger": trig, "value": round(v, 5),
                    })
                    prev[key] = v
