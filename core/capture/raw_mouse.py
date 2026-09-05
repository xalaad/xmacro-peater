"""Raw Input mouse capture (WM_INPUT) — true hardware deltas.

pynput's low-level hook reports *cursor positions*, which games break in
two ways: they re-center the cursor every frame (turning real motion into
zero/garbage deltas) and the cursor clamps at screen edges (losing motion).
Raw Input delivers the mouse's actual relative counts regardless of where
the cursor is or who owns it — that's what "record it like I moved it"
needs.

A hidden message-only window on a worker thread receives WM_INPUT and
accumulates dx/dy; a flusher thread emits one coalesced mouse_move event
per poll tick (a 1000Hz gaming mouse would otherwise flood the recording
with events games can't distinguish anyway — the summed per-tick deltas
replay identically).

Falls back cleanly: available() is False off-Windows or if registration
fails, and the recorder then uses pynput's position deltas as before.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Any, Callable

from ..timing import DriftCorrectedTicker, boost_thread_priority

log = logging.getLogger(__name__)

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.windll.user32

    WM_INPUT = 0x00FF
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    HWND_MESSAGE = -3
    RIDEV_INPUTSINK = 0x00000100
    RIDEV_REMOVE = 0x00000001
    RID_INPUT = 0x10000003
    RIM_TYPEMOUSE = 0
    MOUSE_MOVE_ABSOLUTE = 0x0001

    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", wintypes.WPARAM),
        ]

    class RAWMOUSE(ctypes.Structure):
        _fields_ = [
            ("usFlags", wintypes.USHORT),
            ("_pad", wintypes.USHORT),
            ("usButtonFlags", wintypes.USHORT),
            ("usButtonData", wintypes.USHORT),
            ("ulRawButtons", wintypes.ULONG),
            ("lLastX", wintypes.LONG),
            ("lLastY", wintypes.LONG),
            ("ulExtraInformation", wintypes.ULONG),
        ]

    class RAWINPUT(ctypes.Structure):
        _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]

    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    # Precise prototypes matter on 64-bit: without them ctypes passes
    # handles as 32-bit ints, truncating HWND_MESSAGE (-3) and returned
    # HWNDs into invalid handles.
    _user32.DefWindowProcW.restype = LRESULT
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.GetRawInputData.restype = wintypes.UINT
    _user32.GetRawInputData.argtypes = [
        wintypes.LPARAM, wintypes.UINT, ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT), wintypes.UINT,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.RegisterRawInputDevices.restype = wintypes.BOOL
    _user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT
    ]
    _user32.GetMessageW.argtypes = [
        ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT
    ]
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    ctypes.windll.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class RawMouseCapture:
    _instance_counter = 0

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def __init__(self, emit: Callable[[dict[str, Any]], None], hz: int = 125):
        if not self.available():
            raise RuntimeError("Raw Input is Windows-only")
        self.emit = emit
        self.hz = hz
        self._lock = threading.Lock()
        self._acc_dx = 0
        self._acc_dy = 0
        self._hwnd = None
        self._ready = threading.Event()
        self._failed = False
        self._stop = threading.Event()
        self._msg_thread: threading.Thread | None = None
        self._flush_thread: threading.Thread | None = None
        self._wndproc_ref = None  # keep the callback alive for the window

    def start(self) -> bool:
        """Start capture; returns False (after cleanup) if registration
        failed, so callers can fall back to pynput deltas."""
        self._stop.clear()
        self._msg_thread = threading.Thread(
            target=self._message_loop, name="RawMouseWnd", daemon=True
        )
        self._msg_thread.start()
        self._ready.wait(timeout=2.0)
        if self._failed or self._hwnd is None:
            self.stop()
            return False
        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="RawMouseFlush", daemon=True
        )
        self._flush_thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._hwnd is not None:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._msg_thread is not None:
            self._msg_thread.join(timeout=1.0)
            self._msg_thread = None
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=1.0)
            self._flush_thread = None
        self._hwnd = None
        self._ready.clear()

    # ------------------------------------------------------------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = wintypes.UINT(0)
            _user32.GetRawInputData(
                lparam, RID_INPUT, None, ctypes.byref(size),
                ctypes.sizeof(RAWINPUTHEADER),
            )
            if size.value:
                buf = ctypes.create_string_buffer(size.value)
                got = _user32.GetRawInputData(
                    lparam, RID_INPUT, buf, ctypes.byref(size),
                    ctypes.sizeof(RAWINPUTHEADER),
                )
                if got == size.value:
                    ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
                    if (ri.header.dwType == RIM_TYPEMOUSE
                            and not ri.mouse.usFlags & MOUSE_MOVE_ABSOLUTE):
                        dx, dy = ri.mouse.lLastX, ri.mouse.lLastY
                        if dx or dy:
                            with self._lock:
                                self._acc_dx += dx
                                self._acc_dy += dy
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _message_loop(self) -> None:
        try:
            RawMouseCapture._instance_counter += 1
            cls_name = f"MacroSuiteRawInput{RawMouseCapture._instance_counter}"
            self._wndproc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.lpszClassName = cls_name
            wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
            if not _user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError()
            hwnd = _user32.CreateWindowExW(
                0, cls_name, cls_name, 0, 0, 0, 0, 0,
                wintypes.HWND(HWND_MESSAGE), None, wc.hInstance, None,
            )
            if not hwnd:
                raise ctypes.WinError()
            rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
            if not _user32.RegisterRawInputDevices(
                    ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                raise ctypes.WinError()
            self._hwnd = hwnd
            self._ready.set()

            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

            rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_REMOVE, None)
            _user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
            _user32.DestroyWindow(hwnd)
            _user32.UnregisterClassW(cls_name, wc.hInstance)
        except OSError as e:
            log.warning("Raw Input unavailable, falling back to pynput: %s", e)
            self._failed = True
            self._ready.set()

    def _cursor_pos(self) -> tuple[int, int]:
        pt = ctypes.wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _flush_loop(self) -> None:
        boost_thread_priority()
        ticker = DriftCorrectedTicker(self.hz)
        aborting = self._stop.is_set
        while not self._stop.is_set():
            ticker.wait_next(should_abort=aborting)
            with self._lock:
                dx, dy = self._acc_dx, self._acc_dy
                self._acc_dx = self._acc_dy = 0
            if dx or dy:
                self._emit_move(dx, dy)

    def _emit_move(self, dx: int, dy: int) -> None:
        # px/py: the absolute cursor path riding along with the raw
        # counts — playback uses it (rescaled) when the take runs on a
        # different screen than it was recorded on
        px, py = self._cursor_pos()
        self.emit({"src": "mouse_move", "dx": dx, "dy": dy,
                   "px": px, "py": py})
