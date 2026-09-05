"""Virtual output senders: vgamepad (virtual Xbox 360) + pynput (kb/mouse).

Whatever kind of physical pad was recorded, playback always goes out as a
virtual Xbox 360 controller — that's what ViGEmBus emulates and what games
universally accept.

Both libraries are feature-detected so callers can degrade gracefully.
"""
from __future__ import annotations

import ctypes
import importlib.util
import logging
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..events import MacroEvent
from .touch import TouchInjector

log = logging.getLogger(__name__)

# --- Relative mouse motion via SendInput -----------------------------------
# pynput's move() sets an absolute cursor position (current pos + delta),
# which games that use relative/raw mouse input for camera look either
# ignore or mangle. MOUSEEVENTF_MOVE without the ABSOLUTE flag injects a
# genuine relative motion event — the same thing physical mice produce.
if sys.platform == "win32":
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_ABSOLUTE = 0x8000
    _MOUSEEVENTF_VIRTUALDESK = 0x4000

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("mi", _MOUSEINPUT)]

    _SendInput = ctypes.windll.user32.SendInput

    def send_relative_move(dx: int, dy: int) -> None:
        inp = _INPUT(type=0, mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0,
                                            dwFlags=_MOUSEEVENTF_MOVE,
                                            time=0, dwExtraInfo=0))
        _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    RELATIVE_MOVE_AVAILABLE = True

    def send_absolute_move(x: int, y: int) -> None:
        """Cursor to an exact virtual-desktop pixel — bypasses pointer
        speed/acceleration entirely (used for cross-screen replay)."""
        gm = ctypes.windll.user32.GetSystemMetrics
        vx, vy, vw, vh = gm(76), gm(77), gm(78), gm(79)
        nx = round((x - vx) * 65535 / max(1, vw - 1))
        ny = round((y - vy) * 65535 / max(1, vh - 1))
        inp = _INPUT(type=0, mi=_MOUSEINPUT(
            dx=nx, dy=ny, mouseData=0,
            dwFlags=(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE
                     | _MOUSEEVENTF_VIRTUALDESK),
            time=0, dwExtraInfo=0))
        _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def get_cursor_pos() -> tuple[int, int]:
        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def set_cursor_pos(x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))

else:  # pragma: no cover
    RELATIVE_MOVE_AVAILABLE = False

    def send_relative_move(dx: int, dy: int) -> None:
        raise NotImplementedError

    def send_absolute_move(x: int, y: int) -> None:
        raise NotImplementedError

    def get_cursor_pos() -> tuple[int, int]:
        return 0, 0

    def set_cursor_pos(x: int, y: int) -> None:
        pass

try:
    from pynput import keyboard, mouse

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyboard = mouse = None
    PYNPUT_AVAILABLE = False

try:
    import vgamepad as vg

    VGAMEPAD_AVAILABLE = True
except Exception:  # ImportError, or driver missing — vgamepad CONNECTS to
    vg = None      # ViGEmBus at import time, so no driver = failed import
    VGAMEPAD_AVAILABLE = False


def ensure_vgamepad() -> bool:
    """Retry the vgamepad import — succeeds once the ViGEmBus driver has
    been installed (the first-run offer), without restarting the app."""
    global vg, VGAMEPAD_AVAILABLE
    if vg is not None:
        return True
    try:
        import vgamepad as _vg
        vg = _vg
        VGAMEPAD_AVAILABLE = True
        return True
    except Exception:
        return False

AXIS_MAX = 32767
TRIGGER_MAX = 255


# --- ViGEmBus driver: detected and offered at first controller use ----------
# The python package (and its bundled MSI) ships inside the exe; the DRIVER
# is a per-machine runtime install the UI offers only when a pad macro is
# actually played — keyboard/mouse users never see it.

def vigem_driver_installed() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.check_output(
            ["reg", "query",
             r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
             "/s"],
            text=True, stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).lower()
        return "nefarius virtual gamepad emulation bus driver" in out
    except Exception:
        return False


def vigem_msi_path() -> Path | None:
    """The installer MSI bundled inside the vgamepad package, if present."""
    spec = importlib.util.find_spec("vgamepad")
    if spec is None or not spec.origin:
        return None
    arch = "x64" if platform.machine().endswith("64") else "x86"
    msi = (Path(spec.origin).parent / "win" / "vigem" / "install" / arch
           / f"ViGEmBusSetup_{arch}.msi")
    return msi if msi.exists() else None


def launch_vigem_installer() -> bool:
    """Open the bundled ViGEmBus installer (interactive, non-blocking)."""
    msi = vigem_msi_path()
    if msi is None:
        return False
    subprocess.Popen(["msiexec", "/i", str(msi)])
    return True

VG_BUTTON_NAMES = {
    "DPAD_UP": "XUSB_GAMEPAD_DPAD_UP",
    "DPAD_DOWN": "XUSB_GAMEPAD_DPAD_DOWN",
    "DPAD_LEFT": "XUSB_GAMEPAD_DPAD_LEFT",
    "DPAD_RIGHT": "XUSB_GAMEPAD_DPAD_RIGHT",
    "START": "XUSB_GAMEPAD_START",
    "BACK": "XUSB_GAMEPAD_BACK",
    "LEFT_THUMB": "XUSB_GAMEPAD_LEFT_THUMB",
    "RIGHT_THUMB": "XUSB_GAMEPAD_RIGHT_THUMB",
    "LEFT_SHOULDER": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "RIGHT_SHOULDER": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "A": "XUSB_GAMEPAD_A",
    "B": "XUSB_GAMEPAD_B",
    "X": "XUSB_GAMEPAD_X",
    "Y": "XUSB_GAMEPAD_Y",
}


def key_from_repr(rep: str):
    kind, val = rep.split(":", 1)
    if kind == "char":
        return keyboard.KeyCode.from_char(val)
    return getattr(keyboard.Key, val)


class VirtualOutput:
    """Dispatches MacroEvents to the OS, tracking held keys/buttons so
    release_all() can guarantee nothing sticks after a run or an abort."""

    def __init__(self, need_gamepad: bool):
        if not PYNPUT_AVAILABLE:
            raise RuntimeError("pynput is not installed")
        self._kb = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._pad = None
        if need_gamepad:
            if not ensure_vgamepad():
                raise RuntimeError(
                    "This macro contains controller events but vgamepad/ViGEmBus "
                    "is not available. Install it with: pip install vgamepad"
                )
            self._pad = vg.VX360Gamepad()
        self._held_keys: set = set()
        self._held_mouse: set = set()
        self._touch: TouchInjector | None = None
        self._touch_failed = False

    def send(self, ev: MacroEvent) -> None:
        d = ev.data
        if ev.src == "kb":
            k = key_from_repr(d["key"])
            if d["action"] == "down":
                self._kb.press(k)
                self._held_keys.add(k)
            else:
                self._kb.release(k)
                self._held_keys.discard(k)
        elif ev.src == "mouse_move":
            if RELATIVE_MOVE_AVAILABLE:
                send_relative_move(d["dx"], d["dy"])
            else:
                self._mouse.move(d["dx"], d["dy"])
        elif ev.src == "mouse_abs":
            # Runtime-only event from cross-screen adaptation: replay the
            # recorded CURSOR PATH (rescaled) instead of raw counts
            if RELATIVE_MOVE_AVAILABLE:
                send_absolute_move(d["x"], d["y"])
            else:
                self._mouse.position = (d["x"], d["y"])
        elif ev.src == "mouse_btn":
            btn = getattr(mouse.Button, d["button"])
            if d["action"] == "down":
                self._mouse.press(btn)
                self._held_mouse.add(btn)
            else:
                self._mouse.release(btn)
                self._held_mouse.discard(btn)
        elif ev.src == "mouse_scroll":
            self._mouse.scroll(d["dx"], d["dy"])
        elif ev.src == "touch":
            self._send_touch(d)
        elif self._pad is not None:
            self._send_pad(ev.src, d)

    def _send_touch(self, d: dict[str, Any]) -> None:
        """Replay a recorded gesture as genuine touch; fall back to
        absolute mouse if the injection API is unavailable."""
        x, y, action = d["x"], d["y"], d["action"]
        if self._touch is None and not self._touch_failed:
            if TouchInjector.available():
                try:
                    self._touch = TouchInjector()
                except RuntimeError as e:
                    log.warning("Touch injection unavailable: %s", e)
                    self._touch_failed = True
            else:
                self._touch_failed = True
        if self._touch is not None:
            getattr(self._touch, action)(x, y)
            return
        # Fallback: absolute mouse emulation of the gesture
        set_cursor_pos(x, y)
        if action == "down":
            self._mouse.press(mouse.Button.left)
            self._held_mouse.add(mouse.Button.left)
        elif action == "up":
            self._mouse.release(mouse.Button.left)
            self._held_mouse.discard(mouse.Button.left)

    def _send_pad(self, src: str, d: dict[str, Any]) -> None:
        if src == "pad_btn":
            btn = getattr(vg.XUSB_BUTTON, VG_BUTTON_NAMES[d["button"]])
            if d["action"] == "down":
                self._pad.press_button(button=btn)
            else:
                self._pad.release_button(button=btn)
        elif src == "pad_trigger":
            value = min(round(d["value"] * TRIGGER_MAX), TRIGGER_MAX)
            if d["trigger"] == "left":
                self._pad.left_trigger(value=value)
            else:
                self._pad.right_trigger(value=value)
        elif src == "pad_axis":
            x = max(-32768, min(round(d["x"] * AXIS_MAX), AXIS_MAX))
            y = max(-32768, min(round(d["y"] * AXIS_MAX), AXIS_MAX))
            if d["stick"] == "left":
                self._pad.left_joystick(x_value=x, y_value=y)
            else:
                self._pad.right_joystick(x_value=x, y_value=y)
        self._pad.update()

    def release_all(self) -> None:
        """Release every held key/button and neutralize the virtual pad."""
        for k in list(self._held_keys):
            try:
                self._kb.release(k)
            except Exception:
                pass
        self._held_keys.clear()
        for b in list(self._held_mouse):
            try:
                self._mouse.release(b)
            except Exception:
                pass
        self._held_mouse.clear()
        if self._touch is not None:
            self._touch.release()
        if self._pad is not None:
            self._pad.reset()
            self._pad.update()

    def close(self) -> None:
        self.release_all()
        self._pad = None
