"""Always-on passive input monitor that drives the visualizer widgets.

Separate from the recording pipeline: pynput listeners accumulate keyboard/
mouse state into a lock-guarded snapshot; the main window's ~60fps QTimer
calls snapshot() on the UI thread and pushes the result to widgets. The
controller is read directly at UI frame rate (XInput reads are microseconds)
— the 125Hz recording pollers stay completely decoupled.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# Mouse events Windows synthesizes FROM TOUCH/PEN carry this signature
# in their dwExtraInfo. NOTE: inside a low-level hook the ONLY reliable
# source is the MSLLHOOKSTRUCT itself (pynput's win32_event_filter) —
# GetMessageExtraInfo() is queue state and reads 0 there.
_MI_SIG_MASK, _MI_SIG = 0xFFFFFF00, 0xFF515700
TOUCH_BURST_GAP = 0.35   # digitizer-quiet seconds that end a contact
TOUCH_CLICK_WINDOW = 0.4  # synthesized click within this of raw touch


def _click_is_touch() -> bool:  # legacy fallback, kept for tests
    if sys.platform != "win32":  # pragma: no cover
        return False
    import ctypes
    info = ctypes.windll.user32.GetMessageExtraInfo() & 0xFFFFFFFF
    return (info & _MI_SIG_MASK) == _MI_SIG


class RawTouchWatcher:
    """System-wide touchscreen detector via Raw Input (HID digitizer,
    usage page 0x0D / usage 0x04, RIDEV_INPUTSINK).

    Touch-native apps (Chrome, modern UWP...) consume pointer input and
    Windows never synthesizes mouse events for them — a mouse hook sees
    NOTHING. The digitizer's raw reports still flow here for every app.
    HID report layouts are device-specific, so instead of parsing them we
    detect report BURSTS: the first report after a quiet gap is a new
    contact, reported as a tap at the current cursor position (the
    primary contact drives the cursor)."""

    def __init__(self, on_tap):
        self.on_tap = on_tap  # on_tap(x, y) — called from the wnd thread
        self._last_report = 0.0
        self._hwnd = None
        self._ready = threading.Event()
        self._failed = False
        self._thread: threading.Thread | None = None
        self._wndproc_ref = None

    def note_report(self, now: float) -> bool:
        """Burst logic (pure, testable): True when a report starts a NEW
        contact after a quiet gap."""
        fresh = (now - self._last_report) > TOUCH_BURST_GAP
        self._last_report = now
        return fresh

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(
            target=self._message_loop, name="RawTouchWnd", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return not self._failed and self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd is not None:
            from core.capture import raw_mouse as rm
            rm._user32.PostMessageW(self._hwnd, rm.WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._hwnd = None

    def _message_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        from core.capture import raw_mouse as rm
        try:
            cls_name = "MacroSuiteRawTouch"
            self._wndproc_ref = rm.WNDPROC(self._wndproc)
            wc = rm.WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.lpszClassName = cls_name
            wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
            if not rm._user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError()
            hwnd = rm._user32.CreateWindowExW(
                0, cls_name, cls_name, 0, 0, 0, 0, 0,
                wintypes.HWND(rm.HWND_MESSAGE), None, wc.hInstance, None)
            if not hwnd:
                raise ctypes.WinError()
            rid = rm.RAWINPUTDEVICE(0x0D, 0x04, rm.RIDEV_INPUTSINK, hwnd)
            if not rm._user32.RegisterRawInputDevices(
                    ctypes.byref(rid), 1, ctypes.sizeof(rm.RAWINPUTDEVICE)):
                raise ctypes.WinError()
            self._hwnd = hwnd
            self._ready.set()

            msg = wintypes.MSG()
            while rm._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                rm._user32.TranslateMessage(ctypes.byref(msg))
                rm._user32.DispatchMessageW(ctypes.byref(msg))

            rid = rm.RAWINPUTDEVICE(0x0D, 0x04, rm.RIDEV_REMOVE, None)
            rm._user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(rm.RAWINPUTDEVICE))
            rm._user32.DestroyWindow(hwnd)
            rm._user32.UnregisterClassW(cls_name, wc.hInstance)
        except OSError as e:
            log.info("Touch digitizer raw input unavailable: %s", e)
            self._failed = True
            self._ready.set()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        import ctypes
        from ctypes import wintypes

        from core.capture import raw_mouse as rm
        if msg == rm.WM_INPUT:
            if self.note_report(time.monotonic()):
                pt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                self.on_tap(pt.x, pt.y)
            return 0
        if msg == rm.WM_DESTROY:
            rm._user32.PostQuitMessage(0)
            return 0
        return rm._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

try:
    from pynput import keyboard, mouse

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyboard = mouse = None
    PYNPUT_AVAILABLE = False

from core.capture.keyboard_mouse import key_repr

from .widgets.keyboard_widget import PHYSICAL_VK

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
    # Physical-position match by virtual key: an Arabic (or any other
    # layout's) character still lights the physical key that produced it
    if vk in PHYSICAL_VK and getattr(key, "char", None) is not None:
        return f"char:{PHYSICAL_VK[vk]}"
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
        self._touch_taps: list[tuple[int, int]] = []
        self._touch_active = False
        self._flagged_touch = False   # set per-message by the win32 filter
        self._touch_recent = 0.0      # last raw digitizer report time
        self._raw_touch_ok = False
        self._raw_touch: RawTouchWatcher | None = None
        self._last: tuple[int, int] | None = None
        self._kb_listener = None
        self._mouse_listener = None

    def start(self) -> None:
        if not PYNPUT_AVAILABLE or self._kb_listener is not None:
            return
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
        self._raw_touch = RawTouchWatcher(self._on_raw_touch)
        self._raw_touch_ok = self._raw_touch.start()

    def stop(self) -> None:
        for listener in (self._kb_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._kb_listener = self._mouse_listener = None
        if self._raw_touch is not None:
            self._raw_touch.stop()
            self._raw_touch = None
        self._raw_touch_ok = False

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
                "touch_taps": self._touch_taps,
            }
            self._key_pulses = []
            self._touch_taps = []
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

    def _win32_filter(self, msg, data) -> bool:
        """Runs inside pynput's low-level hook with the MSLLHOOKSTRUCT —
        the only place the touch/pen signature is actually readable."""
        if msg in (0x0201, 0x0202):  # WM_LBUTTONDOWN / WM_LBUTTONUP
            extra = getattr(data, "dwExtraInfo", 0) & 0xFFFFFFFF
            self._flagged_touch = (extra & _MI_SIG_MASK) == _MI_SIG
        return True  # never suppress anything

    def _on_raw_touch(self, x: int, y: int) -> None:
        """New digitizer contact (from the RawTouchWatcher thread)."""
        with self._lock:
            self._touch_taps.append((int(x), int(y)))
            self._touch_recent = time.monotonic()

    def _on_click(self, x, y, button, pressed) -> None:
        name = str(button).split(".")[-1]
        # Synthesized-from-touch LEFT clicks must not count as mouse:
        # flagged via the hook's dwExtraInfo, or arriving right after a
        # raw digitizer report. The raw watcher is the tap reporter when
        # it's running — here we only suppress the phantom click.
        if name == "left":
            raw_recent = (self._raw_touch_ok and
                          time.monotonic() - self._touch_recent
                          < TOUCH_CLICK_WINDOW)
            if (self._touch_active
                    or (pressed and (self._flagged_touch or raw_recent
                                     or _click_is_touch()))):
                with self._lock:
                    if pressed:
                        self._touch_active = True
                        if not raw_recent:  # no raw watcher: we report
                            self._touch_taps.append((int(x), int(y)))
                    else:
                        self._touch_active = False
                return
        with self._lock:
            (self._mouse_buttons.add if pressed else self._mouse_buttons.discard)(name)

    def _on_scroll(self, x, y, dx, dy) -> None:
        with self._lock:
            self._scroll += dy
