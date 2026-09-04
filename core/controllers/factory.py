"""Scheme discovery + backend construction.

A scheme JSON in config/schemes/ fully describes a controller type: which
backend reads it, the button/axis index mapping (for pygame), and which SVG
art the UI shows. Adding a controller = dropping in a JSON file.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import BUNDLE_DIR, SCHEMES_DIR
from .base import ControllerBackend
from .pygame_backend import PYGAME_AVAILABLE, PygameBackend, Scheme
from .xinput_backend import XInputBackend

log = logging.getLogger(__name__)


def list_schemes(schemes_dir: Path = SCHEMES_DIR) -> dict[str, Scheme]:
    """Map of scheme id (filename stem) -> Scheme, sorted by id."""
    schemes: dict[str, Scheme] = {}
    if not schemes_dir.exists():
        # Frozen build first run: fall back to the schemes bundled in the exe
        bundled = BUNDLE_DIR / "config" / "schemes"
        if bundled.exists():
            schemes_dir = bundled
    if schemes_dir.exists():
        for path in sorted(schemes_dir.glob("*.json")):
            try:
                schemes[path.stem] = Scheme.load(path)
            except (OSError, ValueError) as e:
                log.warning("Skipping bad scheme %s: %s", path, e)
    return schemes


def scheme_available(scheme: Scheme) -> tuple[bool, str]:
    """(usable, reason-if-not) — lets the UI gray out instead of crash."""
    if scheme.backend == "pygame" and not PYGAME_AVAILABLE:
        return False, "Requires pygame (pip install pygame)"
    return True, ""


def create_backend(scheme: Scheme) -> ControllerBackend | None:
    """Build the backend for a scheme, or None if unavailable/unplugged."""
    try:
        if scheme.backend == "xinput":
            return XInputBackend()
        if scheme.backend == "pygame":
            if not PYGAME_AVAILABLE:
                log.warning("Scheme %s needs pygame, which is missing", scheme.name)
                return None
            return PygameBackend(scheme)
        log.warning("Unknown backend %r in scheme %s", scheme.backend, scheme.name)
    except RuntimeError as e:
        log.warning("Backend for %s unavailable: %s", scheme.name, e)
    return None
