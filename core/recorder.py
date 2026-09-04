"""MacroRecorder — glues keyboard/mouse capture and controller pollers into
one start()/stop()/get_events() facade. Headless: no Qt anywhere.

An optional on_event callback fires for every captured event (timestamped),
which the UI uses for the live activity log.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .capture.axis_poller import AxisPoller
from .capture.button_poller import ButtonPoller
from .capture.keyboard_mouse import KeyboardMouseCapture, PYNPUT_AVAILABLE
from .capture.raw_mouse import RawMouseCapture
from .controllers.base import ControllerBackend
from .events import MacroEvent, MacroFile
from .hotkeys import trim_hotkey_artifacts
from .timing import TimerResolution

log = logging.getLogger(__name__)


class MacroRecorder:
    def __init__(
        self,
        backend: ControllerBackend | None = None,
        poll_hz: int = 125,
        stick_deadzone: float = 0.08,
        trigger_deadzone: float = 0.02,
        capture_keyboard_mouse: bool = True,
        ignore_keys: tuple[str, ...] = ("key:f9",),
        trim_keys: tuple[str, ...] = (),
        touch_mode: bool = False,
        on_event: Callable[[MacroEvent], None] | None = None,
    ):
        self.poll_hz = poll_hz
        self.trim_keys = trim_keys
        self.touch_mode = touch_mode
        self.on_event = on_event
        self._events: list[MacroEvent] = []
        self._lock = threading.Lock()
        self._start_time = 0.0
        self._recording = False
        self._timer_res = TimerResolution(1)

        # Raw Input gives true hardware mouse deltas (immune to cursor
        # recentering/clamping in games); pynput deltas are the fallback.
        # Touch mode records absolute gesture paths instead — Raw Input
        # doesn't see touch, so it stays off there.
        self._raw_mouse: RawMouseCapture | None = None
        if (capture_keyboard_mouse and not touch_mode
                and RawMouseCapture.available()):
            self._raw_mouse = RawMouseCapture(self._add, hz=poll_hz)

        self._kbm: KeyboardMouseCapture | None = None
        if capture_keyboard_mouse and PYNPUT_AVAILABLE:
            self._kbm = KeyboardMouseCapture(
                self._add, ignore_keys=ignore_keys,
                capture_moves=self._raw_mouse is None,
                touch_mode=touch_mode,
            )

        self._button_poller: ButtonPoller | None = None
        self._axis_poller: AxisPoller | None = None
        if backend is not None:
            self._button_poller = ButtonPoller(backend, self._add, hz=poll_hz)
            self._axis_poller = AxisPoller(
                backend, self._add, hz=poll_hz,
                stick_deadzone=stick_deadzone,
                trigger_deadzone=trigger_deadzone,
            )

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._start_time if self._recording else 0.0

    def _add(self, data: dict[str, Any]) -> None:
        if not self._recording:
            return
        d = dict(data)
        src = d.pop("src")
        ev = MacroEvent(t=time.perf_counter() - self._start_time, src=src, data=d)
        with self._lock:
            self._events.append(ev)
        if self.on_event is not None:
            self.on_event(ev)

    def start(self) -> None:
        if self._recording:
            return
        with self._lock:
            self._events.clear()
        self._timer_res.__enter__()
        if self._raw_mouse is not None and not self._raw_mouse.start():
            self._raw_mouse = None  # registration failed -> pynput deltas
            if self._kbm is not None:
                self._kbm.capture_moves = True
        self._start_time = time.perf_counter()
        self._recording = True
        if self._kbm is not None:
            self._kbm.start()
        if self._button_poller is not None:
            self._button_poller.start()
        if self._axis_poller is not None:
            self._axis_poller.start()
        log.info("Recording started (poll %dHz)", self.poll_hz)

    def stop(self) -> MacroFile:
        if not self._recording:
            return MacroFile(events=[], poll_hz=self.poll_hz)
        self._recording = False
        if self._raw_mouse is not None:
            self._raw_mouse.stop()
        if self._kbm is not None:
            self._kbm.stop()
        if self._button_poller is not None:
            self._button_poller.stop()
        if self._axis_poller is not None:
            self._axis_poller.stop()
        self._timer_res.__exit__(None, None, None)
        with self._lock:
            events = sorted(self._events, key=lambda e: e.t)
            self._events = []
        if self.trim_keys:
            events = trim_hotkey_artifacts(events, self.trim_keys)
        log.info("Recording stopped: %d events", len(events))
        return MacroFile(events=events, poll_hz=self.poll_hz)

    def get_events(self) -> list[MacroEvent]:
        with self._lock:
            return list(self._events)

    def drift_stats(self) -> dict[str, str]:
        stats = {}
        for name, poller in (
            ("buttons", self._button_poller), ("axes", self._axis_poller)
        ):
            if poller is not None and poller.ticker is not None:
                stats[name] = poller.ticker.stats_line()
        return stats
