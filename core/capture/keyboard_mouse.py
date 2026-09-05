"""Keyboard + mouse capture via pynput listeners (event-driven, no polling).

Mouse movement is recorded as relative deltas so playback works with games
that capture the cursor for camera look.

Touch mode records absolute gestures — and coexists with a REAL mouse:
each hook message carries Windows' touch/pen signature in dwExtraInfo, so
touch-synthesized events become gestures while genuine mouse events keep
recording as normal clicks and relative motion.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable, Iterable

try:
    from pynput import keyboard, mouse

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyboard = mouse = None
    PYNPUT_AVAILABLE = False

# Mouse messages Windows synthesizes from touch/pen carry this signature
# in the hook struct's dwExtraInfo (readable ONLY there, not via
# GetMessageExtraInfo inside low-level hooks)
_MI_SIG_MASK, _MI_SIG = 0xFFFFFF00, 0xFF515700
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN, _WM_LBUTTONUP = 0x0201, 0x0202


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
        # Per-message: was the CURRENT mouse event synthesized from touch?
        # None = unknown (no filter available) -> legacy all-touch behavior
        self._evt_is_touch: bool | None = None

    def start(self) -> None:
        self._last_pos = None
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        kwargs = {}
        if sys.platform == "win32":
            kwargs["win32_event_filter"] = self._win32_filter
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click,
            on_scroll=self._on_scroll, **kwargs
        )
        self._kb_listener.start()
        self._mouse_listener.start()
        # Touch mode: gestures come straight from the DIGITIZER, so taps
        # over pointer-native apps (Chrome, taskbar, UWP) record too —
        # those never synthesize mouse events for the hook to see. The
        # hook then only contributes the real mouse (unflagged) events.
        self._raw_gestures = None
        if self.touch_mode:
            from .raw_touch import GESTURE_GAP, RawTouchWatcher
            w = RawTouchWatcher(
                on_down=lambda x, y: self.emit(
                    {"src": "touch", "action": "down", "x": x, "y": y}),
                on_move=lambda x, y: self.emit(
                    {"src": "touch", "action": "move", "x": x, "y": y}),
                on_up=lambda x, y: self.emit(
                    {"src": "touch", "action": "up", "x": x, "y": y}),
                quiet_gap=GESTURE_GAP,
            )
            if w.start():
                self._raw_gestures = w

    def _win32_filter(self, msg, data) -> bool:
        # MUST be bulletproof: pynput calls this even for messages it
        # can't convert, with partial data — and an exception here kills
        # the listener permanently, silently losing all further events
        try:
            if msg in (_WM_MOUSEMOVE, _WM_LBUTTONDOWN, _WM_LBUTTONUP):
                extra = getattr(data, "dwExtraInfo", None)
                if extra is not None:
                    self._evt_is_touch = ((int(extra) & 0xFFFFFFFF
                                           & _MI_SIG_MASK) == _MI_SIG)
        except Exception:  # noqa: BLE001 — never let the hook die
            pass
        return True  # never suppress

    def stop(self) -> None:
        if self._kb_listener is not None:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if getattr(self, "_raw_gestures", None) is not None:
            self._raw_gestures.stop()
            self._raw_gestures = None

    # --- callbacks -------------------------------------------------------
    def _on_press(self, key) -> None:
        rep = key_repr(key)
        if rep not in self.ignore_keys:
            self.emit({"src": "kb", "action": "down", "key": rep})

    def _on_release(self, key) -> None:
        rep = key_repr(key)
        if rep not in self.ignore_keys:
            self.emit({"src": "kb", "action": "up", "key": rep})

    def _event_is_touch(self) -> bool:
        # Unknown (no win32 filter) -> legacy behavior: in touch mode
        # everything left-button counts as touch
        return self._evt_is_touch if self._evt_is_touch is not None else True

    def _on_move(self, x: int, y: int) -> None:
        if self.touch_mode and self._event_is_touch():
            # With the digitizer watcher running IT records the gesture
            # (taps over pointer-native apps never even reach here);
            # this promoted path only serves as the fallback
            if (getattr(self, "_raw_gestures", None) is None
                    and self._touch_down):
                now = time.perf_counter()
                if now - self._last_touch_move >= 0.008:
                    self._last_touch_move = now
                    self.emit({"src": "touch", "action": "move",
                               "x": int(x), "y": int(y)})
            # A finger warped the cursor: relative deltas must restart
            # fresh or the next real mouse move records a giant jump
            self._last_pos = None
            return
        if not self.capture_moves:
            return
        if self._last_pos is not None:
            dx, dy = x - self._last_pos[0], y - self._last_pos[1]
            if dx or dy:
                self.emit({"src": "mouse_move", "dx": dx, "dy": dy,
                           "px": int(x), "py": int(y)})
        self._last_pos = (x, y)

    def _on_click(self, x, y, button, pressed) -> None:
        name = str(button).split(".")[-1]
        if (self.touch_mode and name == "left"
                and (self._event_is_touch() or self._touch_down)):
            self._touch_down = pressed
            self._last_pos = None
            if getattr(self, "_raw_gestures", None) is None:
                # Fallback only: with the digitizer watcher running, the
                # gesture is already being recorded from the hardware
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
        # Precision touchpads / touch scrolling emit a flood of no-op
        # notches (dx=dy=0) between real ones - never record those
        if not dx and not dy:
            return
        self.emit({"src": "mouse_scroll", "dx": dx, "dy": dy})
