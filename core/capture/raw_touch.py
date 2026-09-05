"""System-wide touchscreen capture via Raw Input (HID digitizer,
usage page 0x0D / usage 0x04, RIDEV_INPUTSINK).

Touch-native apps (Chrome, modern UWP...) consume pointer input and
Windows never synthesizes mouse events for them — a mouse hook sees
NOTHING, so gestures over them could neither be monitored nor RECORDED.
The digitizer's raw reports still flow here for every app.

TWO hard-won constraints shape this module:

1. Windows allows exactly ONE window per process per device usage. Two
   independent registrations do not coexist: the later one silently
   takes over, and unregistering it kills touch input for the WHOLE
   process. So a single process-wide hub owns the window and the
   registration, and every consumer (live monitor, recorder) subscribes
   to it.

2. Contact positions must come from the REPORT, not GetCursorPos():
   Windows only moves the cursor for touch it promotes to mouse input,
   so over pointer-native surfaces the cursor is stale and gestures
   would record at the wrong place (see core.capture.hid_touch). The
   cursor is only a fallback when the report cannot be parsed.

HID report layouts are device-specific, so contact START/END come from
report BURSTS rather than parsing tip-switch flags: the first report
after a quiet gap starts a contact, the stream sustains it, quiet ends
it.

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

from . import hid_touch

log = logging.getLogger(__name__)

TAP_GAP = 0.35       # monitor: quiet seconds separating taps
GESTURE_GAP = 0.12   # recorder: quiet seconds that end a contact
MOVE_COALESCE = 0.008
STUCK_TIMEOUT = 2.0   # safety net if a lift report is ever lost

_RIDEV_DEVNOTIFY = 0x00002000
_WM_INPUT_DEVICE_CHANGE = 0x00FE


class _DigitizerHub:
    """Process-wide owner of the digitizer registration. Subscribers get
    (x, y) per report; the window lives while anyone is subscribed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: list[Callable[[int, int, object], None]] = []
        self._hwnd = None
        self._ready = threading.Event()
        self._failed = False
        self._thread: threading.Thread | None = None
        # Registered ONCE per process and never torn down: the window
        # class holds a pointer to the wndproc trampoline, so recreating
        # either on restart leaves the class pointing at a freed
        # callback -> access violation on the next report.
        self._wndproc_ref = None
        self._class_name = None
        self._hinstance = None

    # -------------------------------------------------------- subscriptions
    def subscribe(self, fn: Callable[[int, int, object], None]) -> bool:
        with self._lock:
            first = not self._subs
            self._subs.append(fn)
        if first and not self._start():
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)
            return False
        return not self._failed

    def unsubscribe(self, fn: Callable[[int, int, object], None]) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)
            empty = not self._subs
        if empty:
            self._stop()

    def _dispatch(self, x: int, y: int, tip: bool | None) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(x, y, tip)
            except Exception:  # noqa: BLE001 — one bad consumer must not
                log.exception("touch subscriber failed")  # break capture

    # ------------------------------------------------------------ lifecycle
    def _start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._thread is not None:
            if self._thread.is_alive():
                return not self._failed and self._hwnd is not None
            # Previous attempt died (failed registration, closed loop) —
            # a stale handle here must not block retries forever.
            self._thread = None
            self._hwnd = None
        self._failed = False
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._message_loop, name="RawTouchHub", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return not self._failed and self._hwnd is not None

    def _stop(self) -> None:
        if self._hwnd is not None:
            from . import raw_mouse as rm
            rm._user32.PostMessageW(self._hwnd, rm.WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._hwnd = None

    # -------------------------------------------------------- win32 plumbing
    def _message_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        from . import raw_mouse as rm
        try:
            if self._class_name is None:
                cls_name = "MacroSuiteRawTouchHub"
                self._wndproc_ref = rm.WNDPROC(self._wndproc)
                wc = rm.WNDCLASSW()
                wc.lpfnWndProc = self._wndproc_ref
                wc.lpszClassName = cls_name
                wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
                if not rm._user32.RegisterClassW(ctypes.byref(wc)):
                    raise ctypes.WinError()
                self._class_name = cls_name
                self._hinstance = wc.hInstance
                self._wc = wc  # keep the class struct alive too
            hwnd = rm._user32.CreateWindowExW(
                0, self._class_name, self._class_name, 0, 0, 0, 0, 0,
                wintypes.HWND(rm.HWND_MESSAGE), None, self._hinstance, None)
            if not hwnd:
                raise ctypes.WinError()
            # DEVNOTIFY: WM_INPUT_DEVICE_CHANGE on digitizer arrival/
            # removal, so stale per-handle HID maps get invalidated
            rid = rm.RAWINPUTDEVICE(
                0x0D, 0x04, rm.RIDEV_INPUTSINK | _RIDEV_DEVNOTIFY, hwnd)
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
        except OSError as e:
            log.info("Touch digitizer raw input unavailable: %s", e)
            self._failed = True
            self._ready.set()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        import ctypes
        from ctypes import wintypes

        from . import raw_mouse as rm
        if msg == rm.WM_INPUT:
            try:
                self._on_raw_input(lparam)
            except Exception:  # noqa: BLE001 — never kill the window
                log.exception("touch report handling failed")
            return 0
        if msg == _WM_INPUT_DEVICE_CHANGE:
            # A digitizer came or went; handle values get REUSED, so a
            # cached map could scale a new device with old ranges
            hid_touch.clear_cache()
            return 0
        if msg == rm.WM_DESTROY:
            rm._user32.PostQuitMessage(0)
            return 0
        return rm._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_raw_input(self, lparam) -> None:
        import ctypes
        from ctypes import wintypes

        from . import raw_mouse as rm
        size = wintypes.UINT(0)
        rm._user32.GetRawInputData(
            lparam, rm.RID_INPUT, None, ctypes.byref(size),
            ctypes.sizeof(rm.RAWINPUTHEADER))
        if size.value:
            buf = ctypes.create_string_buffer(size.value)
            got = rm._user32.GetRawInputData(
                lparam, rm.RID_INPUT, buf, ctypes.byref(size),
                ctypes.sizeof(rm.RAWINPUTHEADER))
            if got == size.value:
                header = rm.RAWINPUTHEADER.from_buffer_copy(
                    buf[:ctypes.sizeof(rm.RAWINPUTHEADER)])
                raw = buf.raw
                hid_off = ctypes.sizeof(rm.RAWINPUTHEADER)
                if len(raw) >= hid_off + 8:
                    size_hid = int.from_bytes(
                        raw[hid_off:hid_off + 4], "little")
                    count = int.from_bytes(
                        raw[hid_off + 4:hid_off + 8], "little")
                    start = hid_off + 8
                    if size_hid and count:
                        # Last report in the packet = newest position
                        off = start + size_hid * (count - 1)
                        report = raw[off:off + size_hid]
                        parsed = hid_touch.parse(header.hDevice, report)
                        if parsed is not None:
                            x, y, tip = parsed
                            self._dispatch(x, y, tip)
                            return
        # Fallback: the cursor (accurate only for promoted touch)
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        self._dispatch(pt.x, pt.y, None)


HUB = _DigitizerHub()


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
        self._prev_report = 0.0
        self._tip_known = False
        self._last_move_emit = 0.0
        self._contact = False
        self._last_pos = (0, 0)
        self._stop = threading.Event()
        self._gap_thread: threading.Thread | None = None
        self._subscribed = False

    # ---------------------------------------------------- pure state machine
    def note_report(self, now: float) -> bool:
        """True when a report starts a NEW contact after a quiet gap."""
        fresh = (now - self._last_report) > self.quiet_gap
        self._last_report = now
        self._prev_report = now
        return fresh

    def handle_report(self, now: float, x: int, y: int,
                      tip: bool | None = None) -> None:
        """One digitizer report at contact position (x, y).

        tip (the digitizer's finger-down flag) is authoritative when the
        device provides it: a finger resting still stops producing
        reports, so timing alone would split one drag into several
        contacts. Timing stays the fallback for devices without it."""
        self._last_report = now
        self._last_pos = (x, y)
        if tip is False:
            if self._contact:
                self._contact = False
                if self.on_up is not None:
                    self.on_up(x, y)
            return
        if tip is True:
            fresh = not self._contact
        else:
            fresh = (now - self._prev_report) > self.quiet_gap
        self._prev_report = now
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
        """Quiet long enough? The contact lifted. Skipped when the
        device reports a tip switch — that already told us."""
        if self._tip_known:
            # Tip switch is authoritative — but if the closing report is
            # ever lost (device unplugged, driver hiccup) a contact would
            # hang open forever, so keep a long safety net.
            if self._contact and (now - self._last_report) > STUCK_TIMEOUT:
                self._contact = False
                if self.on_up is not None:
                    self.on_up(*self._last_pos)
            return
        if self._contact and (now - self._last_report) > self.quiet_gap:
            self._contact = False
            if self.on_up is not None:
                self.on_up(*self._last_pos)

    # ------------------------------------------------------------- lifecycle
    def _on_report(self, x: int, y: int, tip: bool | None = None) -> None:
        if tip is not None:
            self._tip_known = True
        self.handle_report(time.monotonic(), x, y, tip)

    def start(self) -> bool:
        if self._subscribed:
            return True
        if not HUB.subscribe(self._on_report):
            return False
        self._subscribed = True
        self._stop.clear()
        if self.on_up is not None:
            self._gap_thread = threading.Thread(
                target=self._gap_loop, name="RawTouchGap", daemon=True)
            self._gap_thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._subscribed:
            HUB.unsubscribe(self._on_report)
            self._subscribed = False
        if self._gap_thread is not None:
            self._gap_thread.join(timeout=1.0)
            self._gap_thread = None
        # A live contact at stop: close it unconditionally so recordings
        # never end with a hanging touch-down (check_gap's timing rules
        # don't apply — the watcher is going away NOW)
        if self._contact:
            self._contact = False
            if self.on_up is not None:
                self.on_up(*self._last_pos)

    def _gap_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.03)
            self.check_gap(time.monotonic())
