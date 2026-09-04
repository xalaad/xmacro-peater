"""Always-on passive input monitor that drives the visualizer widgets.

Separate from the recording pipeline: pynput listeners accumulate keyboard/
mouse state into a lock-guarded snapshot; the main window's ~60fps QTimer
calls snapshot() on the UI thread and pushes the result to widgets. The
controller is read directly at UI frame rate (XInput reads are microseconds)
— the 125Hz recording pollers stay completely decoupled.
"""
from __future__ import annotations

import threading
from typing import Any

try:
    from pynput import keyboard, mouse

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyboard = mouse = None
    PYNPUT_AVAILABLE = False

from core.capture.keyboard_mouse import key_repr

# Windows virtual-key codes for the numpad — lets the on-screen keyboard
# light the physical numpad keys instead of the top number row. Display
# only: recordings keep the standard reps.
_NUMPAD_VK = {
    **{96 + i: f"key:kp_{i}" for i in range(10)},
    106: "key:kp_multiply", 107: "key:kp_add", 109: "key:kp_subtract",
    110: "key:kp_decimal", 111: "key:kp_divide",
}


def _viz_rep(key) -> str:
    vk = getattr(key, "vk", None)
    if vk in _NUMPAD_VK:
        return _NUMPAD_VK[vk]
    return key_repr(key)


class LiveInputMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        self._mouse_buttons: set[str] = set()
        self._move_dx = 0
        self._move_dy = 0
        self._scroll = 0
        self._key_pulses: list[str] = []  # keys pressed since last snapshot
        self._last: tuple[int, int] | None = None
        self._kb_listener = None
        self._mouse_listener = None

    def start(self) -> None:
        if not PYNPUT_AVAILABLE or self._kb_listener is not None:
            return
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
        )
        self._kb_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        for listener in (self._kb_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._kb_listener = self._mouse_listener = None

    def snapshot(self) -> dict[str, Any]:
        """Consume accumulated deltas; return current state. UI thread only."""
        with self._lock:
            snap = {
                "keys": set(self._keys),
                "key_pulses": self._key_pulses,
                "mouse_buttons": set(self._mouse_buttons),
                "move": (self._move_dx, self._move_dy),
                "scroll": self._scroll,
                "pos": self._last or (0, 0),
            }
            self._key_pulses = []
            self._move_dx = self._move_dy = 0
            self._scroll = 0
        return snap

    # --- listener callbacks (worker threads) ---------------------------
    def _on_press(self, key) -> None:
        rep = _viz_rep(key)
        with self._lock:
            if rep not in self._keys:
                self._key_pulses.append(rep)
            self._keys.add(rep)

    def _on_release(self, key) -> None:
        with self._lock:
            self._keys.discard(_viz_rep(key))

    def _on_move(self, x, y) -> None:
        with self._lock:
            if self._last is not None:
                self._move_dx += x - self._last[0]
                self._move_dy += y - self._last[1]
            self._last = (x, y)

    def _on_click(self, x, y, button, pressed) -> None:
        name = str(button).split(".")[-1]
        with self._lock:
            (self._mouse_buttons.add if pressed else self._mouse_buttons.discard)(name)

    def _on_scroll(self, x, y, dx, dy) -> None:
        with self._lock:
            self._scroll += dy
