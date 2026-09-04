"""Radial (magnitude-based) deadzone filtering for thumbsticks.

Per-axis deadzones make diagonals easier to trigger than cardinals because
each axis is filtered independently; a radial deadzone compares the stick's
overall displacement magnitude against the threshold, then rescales the
remaining range so output ramps smoothly from 0 at the deadzone edge to 1 at
full deflection.
"""
from __future__ import annotations

import math


def apply_radial_deadzone(x: float, y: float, deadzone: float) -> tuple[float, float]:
    """Filter a stick position (each axis -1..1) through a radial deadzone.

    deadzone is a fraction of full deflection (0..1). Returns the filtered
    (x, y), rescaled so magnitude runs 0..1 across the live zone.
    """
    if not 0.0 <= deadzone < 1.0:
        raise ValueError(f"deadzone must be in [0, 1), got {deadzone}")
    magnitude = math.hypot(x, y)
    if magnitude <= deadzone:
        return 0.0, 0.0
    # Rescale: deadzone edge -> 0, full deflection -> 1
    clamped = min(magnitude, 1.0)
    scaled = (clamped - deadzone) / (1.0 - deadzone)
    factor = scaled / magnitude
    return x * factor, y * factor


def apply_trigger_deadzone(value: float, deadzone: float) -> float:
    """Same idea for a single 0..1 trigger axis."""
    if not 0.0 <= deadzone < 1.0:
        raise ValueError(f"deadzone must be in [0, 1), got {deadzone}")
    if value <= deadzone:
        return 0.0
    return min((value - deadzone) / (1.0 - deadzone), 1.0)
