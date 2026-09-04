import math

import pytest

from core.deadzone import apply_radial_deadzone, apply_trigger_deadzone

DZ = 0.2


def test_inside_deadzone_is_zero():
    assert apply_radial_deadzone(0.1, 0.1, DZ) == (0.0, 0.0)
    assert apply_radial_deadzone(0.0, 0.0, DZ) == (0.0, 0.0)
    assert apply_radial_deadzone(-0.19, 0.0, DZ) == (0.0, 0.0)


def test_full_deflection_stays_full():
    x, y = apply_radial_deadzone(1.0, 0.0, DZ)
    assert math.isclose(x, 1.0) and y == 0.0
    x, y = apply_radial_deadzone(0.0, -1.0, DZ)
    assert x == 0.0 and math.isclose(y, -1.0)


def test_radial_not_per_axis():
    """A diagonal at the same magnitude as a cardinal must filter the same.

    This is the whole point of a radial deadzone: per-axis filtering lets a
    diagonal at (0.15, 0.15) — magnitude 0.212 — through a 0.2 deadzone
    while blocking a cardinal (0.21, 0), making diagonals easier to hit.
    """
    mag = 0.5
    cx, cy = apply_radial_deadzone(mag, 0.0, DZ)
    d = mag / math.sqrt(2)
    dx, dy = apply_radial_deadzone(d, d, DZ)
    assert math.isclose(math.hypot(cx, cy), math.hypot(dx, dy), rel_tol=1e-9)


def test_direction_preserved():
    x, y = apply_radial_deadzone(0.6, 0.3, DZ)
    assert math.isclose(y / x, 0.5, rel_tol=1e-9)


def test_ramp_is_continuous_at_edge():
    """Just past the deadzone edge, output magnitude should be near zero
    (smooth ramp), not a jump to the raw magnitude."""
    x, y = apply_radial_deadzone(DZ + 0.001, 0.0, DZ)
    assert 0.0 < math.hypot(x, y) < 0.01


def test_overdeflection_clamped():
    x, y = apply_radial_deadzone(1.0, 1.0, DZ)  # magnitude sqrt(2) from hardware noise
    assert math.hypot(x, y) <= 1.0 + 1e-9


def test_invalid_deadzone_raises():
    with pytest.raises(ValueError):
        apply_radial_deadzone(0.5, 0.5, 1.0)
    with pytest.raises(ValueError):
        apply_radial_deadzone(0.5, 0.5, -0.1)


def test_trigger_deadzone():
    assert apply_trigger_deadzone(0.0, 0.05) == 0.0
    assert apply_trigger_deadzone(0.05, 0.05) == 0.0
    assert math.isclose(apply_trigger_deadzone(1.0, 0.05), 1.0)
    v = apply_trigger_deadzone(0.06, 0.05)
    assert 0.0 < v < 0.02
