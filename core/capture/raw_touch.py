"""System-wide touchscreen capture via Raw Input (HID digitizer,
usage page 0x0D / usage 0x04, RIDEV_INPUTSINK).

Touch-native apps (Chrome, modern UWP...) consume pointer input and
Windows never synthesizes mouse events for them — a mouse hook sees
NOTHING, so gestures over them could neither be monitored nor RECORDED.
The digitizer's raw reports still flow here for every app. HID report
layouts are device-specific, so instead of parsing them we track report
BURSTS: the first report after a quiet gap starts a contact, the stream
sustains it, and quiet ends it. Positions come from the cursor, which
Windows moves with the primary contact regardless of promotion.

Two consumption styles:
- on_tap(x, y): one callback per contact start (live monitor).
- on_down/on_move/on_up(x, y): full gesture stream (touch-mode
  recording) — moves coalesced to ~125Hz, the up fired by a small
  gap-watch thread shortly after the last report.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

TAP_GAP = 0.35       # monitor: quiet seconds separating taps
GESTURE_GAP = 0.12   # recorder: quiet seconds that end a contact
MOVE_COALESCE = 0.008


class RawTouchWatcher:
    def __init__(self,
                 on_tap: Callable[[int, int], None] | None = None,
                 on_down: Callable[[int, int], None] | None = None,
                 on_move: Callable[[int, int], None] | None = None,
                 on_up: Callable[[int, int], None] | None = None,
                 quiet_gap: float = TAP_GAP):
        self.on_tap = on_tap
        self.on_down = on_down
        self.on_move = on_move
        self.on_up = on_up
        self.quiet_gap = quiet_gap
        self._last_report = 0.0
        self._last_move_emit = 0.0
        self._contact = False
        self._last_pos = (0, 0)
        self._hwnd = None
        self._ready = threading.Event()
        self._failed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gap_thread: threading.Thread | None = None
        self._wndproc_ref = None

    # ---------------------------------------------------- pure state machine
    def note_report(self, now: float) -> bool:
        """True when a report starts a NEW contact after a quiet gap."""
        fresh = (now - self._last_report) > self.quiet_gap
        self._last_report = now
        return fresh

    def handle_report(self, now: float, x: int, y: int) -> None:
        """One digitizer report at cursor position (x, y)."""
        fresh = self.note_report(now)
        self._last_pos = (x, y)
        if fresh:
            self._contact = True
            self._last_move_emit = now
            if self.on_tap is not None:
                self.on_tap(x, y)
            if self.on_down is not None:
                self.on_down(x, y)
        elif (self.on_move is not None and self._contact
                and now - self._last_move_emit >= MOVE_COALESCE):
            self._last_move_emit = now
            self.on_move(x, y)

    def check_gap(self, now: float) -> None:
        """Quiet long enough? The contact lifted."""
        if self._contact and (now - self._last_report) > self.quiet_gap:
            self._contact = False
            if self.on_up is not None:
                self.on_up(*self._last_pos)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._message_loop, name="RawTouchWnd", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        ok = not self._failed and self._hwnd is not None
        if ok and self.on_up is not None:
            self._gap_thread = threading.Thread(
                target=self._gap_loop, name="RawTouchGap", daemon=True)
            self._gap_thread.start()
        return ok

    def stop(self) -> None:
        self._stop.set()
        if self._hwnd is not None:
            from . import raw_mouse as rm
            rm._user32.PostMessageW(self._hwnd, rm.WM_CLOSE, 0, 0)
        for t in (self._thread, self._gap_thread):
            if t is not None:
                t.join(timeout=1.0)
        self._thread = self._gap_thread = None
        self._hwnd = None
        # A live contact at stop: close it so recordings never end with
        # a hanging touch-down
        self.check_gap(self._last_report + self.quiet_gap + 1.0)

    def _gap_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.03)
            self.check_gap(time.monotonic())

    # ---------------------------------------------------------- win32 plumbing
    def _message_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        from . import raw_mouse as rm
        try:
            cls_name = f"MacroSuiteRawTouch{id(self):x}"
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
                    ctypes.byref(rid), 1,
                    ctypes.sizeof(rm.RAWINPUTDEVICE)):
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

        from . import raw_mouse as rm
        if msg == rm.WM_INPUT:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            self.handle_report(time.monotonic(), pt.x, pt.y)
            return 0
        if msg == rm.WM_DESTROY:
            rm._user32.PostQuitMessage(0)
            return 0
        return rm._user32.DefWindowProcW(hwnd, msg, wparam, lparam)
