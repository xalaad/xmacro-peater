"""One QSettings identity for the whole app.

Every persisted UI value (geometry, dock state, overlay position) goes
through this factory - a renamed org/app string in ONE inline call would
silently orphan all stored state.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings


def app_settings() -> QSettings:
    return QSettings("MacroSuite", "InputMacroSuite")
