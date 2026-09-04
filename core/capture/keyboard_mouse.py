"""Keyboard + mouse capture via pynput listeners (event-driven, no polling).

Mouse movement is recorded as relative deltas so playback works with games
that capture the cursor for camera look.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable

try:
    from pynput import keyboard, mouse

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyboard = mouse = None
    PYNPUT_AVAILABLE = False


def key_repr(key) -> str:
    """Serialize a pynput key: 'char:a' for printables, 'key:f9' for specials."""
    try:
        if key.char is not None:
            return "char:" + key.char
    except AttributeError:
        pass
    return "key:" + str(key).split(".")[-1]


class KeyboardMouseCapture:
    """Feeds kb / mouse_move / mouse_btn / mouse_scroll events to `emit`.

    ignore_keys: key reprs to exclude from the recording (e.g. the record
    hotkey itself, so playback doesn't re-press F9).
    """

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        ignore_keys: Iterable[str] = (),
        capture_moves: bool = True,
        touch_mode: bool = False,
    ):
        if not PYNPUT_AVAILABLE:
            raise RuntimeError("pynput is not installed")
        self.emit = emit
        self.ignore_keys = set(ignore_keys)
        # False when RawMouseCapture supplies motion (it's more accurate);
        # clicks and scrolls always come from here.
        self.capture_moves = capture_moves
        # Touch mode: record ABSOLUTE tap/drag/swipe gestures ('touch'
        # events with x,y) instead of relative deltas — Windows promotes
        # touchscreen input to these same callbacks, and playback injects
        # them back as genuine touch.
        self.touch_mode = touch_mode
        self._kb_listener = None
        self._mouse_listener = None
        self._last_pos: tuple[int, int] | None = None
        self._touch_down = False
        self._last_touch_move = 0.0

    def start(self) -> None:
        self._last_pos = None
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
        )
        self._kb_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        if self._kb_listener is not None:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    # --- callbacks -------------------------------------------------------
    def _on_press(self, key) -> None:
        rep = key_repr(key)
        if rep not in self.ignore_keys:
            self.emit({"src": "kb", "action": "down", "key": rep})

    def _on_release(self, key) -> None:
        rep = key_repr(key)
        if rep not in self.ignore_keys:
            self.emit({"src": "kb", "action": "up", "key": rep})

    def _on_move(self, x: int, y: int) -> None:
        if self.touch_mode:
            # Drag/swipe path: absolute points while the contact is down,
            # coalesced to ~125Hz so fast swipes don't flood the file
            if self._touch_down:
                now = time.perf_counter()
                if now - self._last_touch_move >= 0.008:
                    self._last_touch_move = now
                    self.emit({"src": "touch", "action": "move",
                               "x": int(x), "y": int(y)})
            return
        if not self.capture_moves:
            return
        if self._last_pos is not None:
            dx, dy = x - self._last_pos[0], y - self._last_pos[1]
            if dx or dy:
                self.emit({"src": "mouse_move", "dx": dx, "dy": dy})
        self._last_pos = (x, y)

    def _on_click(self, x, y, button, pressed) -> None:
        name = str(button).split(".")[-1]
        if self.touch_mode and name == "left":
            self._touch_down = pressed
            self.emit({"src": "touch",
                       "action": "down" if pressed else "up",
                       "x": int(x), "y": int(y)})
            return
        self.emit({
            "src": "mouse_btn",
            "action": "down" if pressed else "up",
            "button": name,
        })

    def _on_scroll(self, x, y, dx, dy) -> None:
        self.emit({"src": "mouse_scroll", "dx": dx, "dy": dy})
