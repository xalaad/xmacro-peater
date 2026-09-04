"""Controller backend interface.

Every backend normalizes hardware state to the same shape so the rest of the
app (pollers, UI, playback) never cares what kind of pad is plugged in:

    {
        "buttons": set[str],   # canonical names: A, B, X, Y, DPAD_UP, ...
        "lx", "ly", "rx", "ry": float in -1..1  (Y up = positive)
        "lt", "rt": float in 0..1
    }
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

BUTTON_NAMES = [
    "A", "B", "X", "Y",
    "LEFT_SHOULDER", "RIGHT_SHOULDER",
    "BACK", "START",
    "LEFT_THUMB", "RIGHT_THUMB",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
]


def neutral_state() -> dict[str, Any]:
    return {
        "buttons": set(),
        "lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0,
        "lt": 0.0, "rt": 0.0,
    }


class ControllerBackend(ABC):
    """A source of physical controller state. read() must be thread-safe."""

    name: str = "base"

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def read(self) -> dict[str, Any]:
        """Return the current normalized state (neutral_state() shape).

        Must return a neutral state, not raise, when the pad is unplugged.
        """

    def device_info(self) -> str:
        """Human description of the device being read (name/slot/id)."""
        return self.name

    def device_count(self) -> int:
        """How many compatible devices are currently detected."""
        return 1 if self.is_connected() else 0

    def list_devices(self) -> list[tuple[int, str]]:
        """Selectable devices as (index, label)."""
        return [(0, self.device_info())] if self.is_connected() else []

    def close(self) -> None:  # optional cleanup
        pass
