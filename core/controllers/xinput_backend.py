"""Xbox / XInput-compatible controller backend via ctypes — no extra deps."""
from __future__ import annotations

import ctypes
import sys
from typing import Any

from .base import ControllerBackend, neutral_state

BUTTON_BITS = {
    "DPAD_UP": 0x0001,
    "DPAD_DOWN": 0x0002,
    "DPAD_LEFT": 0x0004,
    "DPAD_RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "LEFT_THUMB": 0x0040,
    "RIGHT_THUMB": 0x0080,
    "LEFT_SHOULDER": 0x0100,
    "RIGHT_SHOULDER": 0x0200,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}

AXIS_MAX = 32767.0
TRIGGER_MAX = 255.0


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _XINPUT_GAMEPAD)]


class XInputBackend(ControllerBackend):
    name = "xinput"

    def __init__(self, user_index: int = 0):
        if sys.platform != "win32":
            raise RuntimeError("XInput is only available on Windows")
        self.user_index = user_index
        self._dll = None
        for dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self._dll = ctypes.WinDLL(dll_name)
                break
            except OSError:
                continue
        if self._dll is None:
            raise RuntimeError("No XInput DLL found")

    def is_connected(self) -> bool:
        state = _XINPUT_STATE()
        return self._dll.XInputGetState(self.user_index, ctypes.byref(state)) == 0

    def device_info(self) -> str:
        return f"XInput slot {self.user_index}"

    def device_count(self) -> int:
        return len(self.list_devices())

    def list_devices(self) -> list[tuple[int, str]]:
        """Connected XInput slots as (index, label)."""
        state = _XINPUT_STATE()
        return [
            (i, f"XInput slot {i}") for i in range(4)
            if self._dll.XInputGetState(i, ctypes.byref(state)) == 0
        ]

    def read(self) -> dict[str, Any]:
        state = _XINPUT_STATE()
        if self._dll.XInputGetState(self.user_index, ctypes.byref(state)) != 0:
            return neutral_state()
        pad = state.Gamepad
        buttons = {name for name, bit in BUTTON_BITS.items() if pad.wButtons & bit}
        return {
            "buttons": buttons,
            "lx": max(pad.sThumbLX / AXIS_MAX, -1.0),
            "ly": max(pad.sThumbLY / AXIS_MAX, -1.0),
            "rx": max(pad.sThumbRX / AXIS_MAX, -1.0),
            "ry": max(pad.sThumbRY / AXIS_MAX, -1.0),
            "lt": pad.bLeftTrigger / TRIGGER_MAX,
            "rt": pad.bRightTrigger / TRIGGER_MAX,
        }
