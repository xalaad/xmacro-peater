"""PlayStation / generic controller backend via pygame.

The button/axis index mapping is loaded from a scheme JSON file
(config/schemes/*.json), so supporting a new controller means writing a JSON
file, not code. Scheme format:

    {
        "name": "PlayStation (DualShock/DualSense)",
        "art": "controller_ps.svg",
        "buttons": {"0": "A", "1": "B", ...},        # pygame index -> canonical
        "axes": {"lx": 0, "ly": 1, "rx": 2, "ry": 3},
        "triggers": {"lt": 4, "rt": 5},              # -1..1 axes -> 0..1
        "invert_y": true,                            # pygame Y-down -> our Y-up
        "hat_dpad": true                             # hat 0 drives the dpad
    }

pygame is optional; import failures are surfaced via PYGAME_AVAILABLE so the
UI can gray out these controller types instead of crashing.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .base import ControllerBackend, neutral_state

try:
    import pygame

    PYGAME_AVAILABLE = True
except ImportError:
    pygame = None
    PYGAME_AVAILABLE = False


class Scheme:
    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.path = path
        self.name: str = data.get("name", "Unnamed scheme")
        self.backend: str = data.get("backend", "pygame")
        self.art: str = data.get("art", "controller_generic.svg")
        self.buttons: dict[int, str] = {
            int(k): v for k, v in data.get("buttons", {}).items()
        }
        self.axes: dict[str, int] = data.get("axes", {})
        self.triggers: dict[str, int] = data.get("triggers", {})
        self.invert_y: bool = data.get("invert_y", True)
        self.hat_dpad: bool = data.get("hat_dpad", True)
        # Display names for canonical buttons (e.g. A -> Cross on PS pads)
        self.labels: dict[str, str] = data.get("labels", {})
        # Which overlay geometry the visualizer uses: xbox | ps | generic
        art = self.art
        self.layout: str = data.get(
            "layout",
            "xbox" if "xbox" in art else "ps" if "ps" in art else "generic",
        )

    @classmethod
    def load(cls, path: str | Path) -> "Scheme":
        path = Path(path)
        return cls(json.loads(path.read_text(encoding="utf-8")), path)


class PygameBackend(ControllerBackend):
    name = "pygame"

    def __init__(self, scheme: Scheme, joystick_index: int = 0):
        if not PYGAME_AVAILABLE:
            raise RuntimeError("pygame is not installed")
        self.scheme = scheme
        self._lock = threading.Lock()
        pygame.init()
        pygame.joystick.init()
        self._joy = None
        if pygame.joystick.get_count() > joystick_index:
            self._joy = pygame.joystick.Joystick(joystick_index)
            self._joy.init()

    def is_connected(self) -> bool:
        with self._lock:
            if self._joy is None:
                pygame.joystick.quit()
                pygame.joystick.init()
                if pygame.joystick.get_count() > 0:
                    self._joy = pygame.joystick.Joystick(0)
                    self._joy.init()
            return self._joy is not None

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self._joy is None:
                return neutral_state()
            try:
                pygame.event.pump()
                s = self.scheme
                state = neutral_state()

                for idx, name in s.buttons.items():
                    if idx < self._joy.get_numbuttons() and self._joy.get_button(idx):
                        state["buttons"].add(name)

                if s.hat_dpad and self._joy.get_numhats() > 0:
                    hx, hy = self._joy.get_hat(0)
                    if hy > 0:
                        state["buttons"].add("DPAD_UP")
                    elif hy < 0:
                        state["buttons"].add("DPAD_DOWN")
                    if hx < 0:
                        state["buttons"].add("DPAD_LEFT")
                    elif hx > 0:
                        state["buttons"].add("DPAD_RIGHT")

                y_sign = -1.0 if s.invert_y else 1.0
                num_axes = self._joy.get_numaxes()
                for key in ("lx", "ly", "rx", "ry"):
                    idx = s.axes.get(key, -1)
                    if 0 <= idx < num_axes:
                        v = self._joy.get_axis(idx)
                        state[key] = v * y_sign if key in ("ly", "ry") else v

                for key in ("lt", "rt"):
                    idx = s.triggers.get(key, -1)
                    if 0 <= idx < num_axes:
                        # pygame trigger axes rest at -1 and reach +1 pressed
                        state[key] = (self._joy.get_axis(idx) + 1.0) / 2.0

                return state
            except pygame.error:
                self._joy = None
                return neutral_state()

    def device_info(self) -> str:
        with self._lock:
            if self._joy is None:
                return "no device"
            try:
                return f"{self._joy.get_name()} (id {self._joy.get_instance_id()})"
            except pygame.error:
                return "device error"

    def device_count(self) -> int:
        with self._lock:
            try:
                return pygame.joystick.get_count()
            except pygame.error:
                return 0

    def list_devices(self) -> list[tuple[int, str]]:
        """All pygame joysticks as (index, name)."""
        with self._lock:
            devices = []
            try:
                for i in range(pygame.joystick.get_count()):
                    try:
                        devices.append((i, pygame.joystick.Joystick(i).get_name()))
                    except pygame.error:
                        devices.append((i, f"device {i}"))
            except pygame.error:
                pass
            return devices

    def close(self) -> None:
        with self._lock:
            if self._joy is not None:
                self._joy.quit()
                self._joy = None
