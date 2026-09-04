"""Global hotkeys with two-key combo support ("ctrl+f9") via pynput — they
work while a game has focus.

A binding is a set of canonical key names that must all be held; it fires on
the press of its final key, and only on an exact match (holding extra keys
does not trigger a smaller binding, so "esc" won't fire during "ctrl+esc").

Callbacks run on the listener thread; keep them tiny (emit a signal).
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable

try:
    from pynput import keyboard

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyboard = None
    PYNPUT_AVAILABLE = False

log = logging.getLogger(__name__)

# Left/right variants fold onto one canonical modifier name
_MODIFIER_FOLD = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win",
}
MODIFIERS = frozenset(_MODIFIER_FOLD.values())

# Canonical modifier -> every pynput rep it may arrive as (for recording
# exclusion / trimming)
MODIFIER_REPS = {
    "ctrl": {"key:ctrl", "key:ctrl_l", "key:ctrl_r"},
    "shift": {"key:shift", "key:shift_l", "key:shift_r"},
    "alt": {"key:alt", "key:alt_l", "key:alt_r", "key:alt_gr"},
    "win": {"key:cmd", "key:cmd_l", "key:cmd_r"},
}


def parse_combo(spec: str) -> frozenset[str]:
    """'ctrl+f9' -> frozenset({'ctrl', 'f9'}); single keys work too."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey spec: {spec!r}")
    canonical = set()
    for part in parts:
        canonical.add(_MODIFIER_FOLD.get(part, part))
    return frozenset(canonical)


def combo_reps(spec: str) -> set[str]:
    """All pynput key reprs a combo's keys can arrive as — used to keep
    hotkey presses out of recordings."""
    reps: set[str] = set()
    for name in parse_combo(spec):
        if name in MODIFIER_REPS:
            reps |= MODIFIER_REPS[name]
        elif len(name) == 1:
            reps.add(f"char:{name}")
        else:
            reps.add(f"key:{name}")
    return reps


def combo_label(spec: str) -> str:
    """Human display: 'ctrl+f9' -> 'Ctrl+F9'."""
    return "+".join(
        p.strip().capitalize() if len(p.strip()) > 1 else p.strip().upper()
        for p in spec.split("+") if p.strip()
    )


def _canonical_key_name(key) -> str | None:
    """Canonical name for a pynput key event ('ctrl', 'f9', 'a', ...)."""
    try:
        if getattr(key, "char", None) is not None:
            ch = key.char.lower()
            # Ctrl+letter arrives as a control character (\x01..\x1a)
            if ch and 1 <= ord(ch) <= 26:
                ch = chr(ord(ch) + ord("a") - 1)
            return ch
    except Exception:
        return None
    name = str(key).split(".")[-1].lower()
    return _MODIFIER_FOLD.get(name, name)


class GlobalHotkeys:
    def __init__(self):
        if not PYNPUT_AVAILABLE:
            raise RuntimeError("pynput is not installed")
        self._bindings: dict[frozenset[str], Callable[[], None]] = {}
        self._held: set[str] = set()
        self._listener = None

    def bind(self, spec: str, callback: Callable[[], None]) -> None:
        self._bindings[parse_combo(spec)] = callback

    def clear(self) -> None:
        self._bindings.clear()

    def start(self) -> None:
        if self._listener is None:
            self._listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    # ------------------------------------------------------------------
    def _on_press(self, key) -> None:
        name = _canonical_key_name(key)
        if name is None:
            return
        self._held.add(name)
        cb = self._bindings.get(frozenset(self._held))
        if cb is not None:
            try:
                cb()
            except Exception:
                log.exception("Hotkey callback failed for %s", self._held)

    def _on_release(self, key) -> None:
        name = _canonical_key_name(key)
        if name is not None:
            self._held.discard(name)


def trim_hotkey_artifacts(events: list, hotkey_reps: Iterable[str]) -> list:
    """Strip the start/stop combo's keystrokes from a recording's edges.

    Leading contiguous key-ups of hotkey keys (releasing the start combo)
    and trailing contiguous key-downs/ups of hotkey keys (pressing the stop
    combo) are removed. Mid-recording use of the same keys is kept.
    Works on MacroEvent objects.
    """
    reps = set(hotkey_reps)

    def is_hotkey_kb(ev) -> bool:
        return ev.src == "kb" and ev.data.get("key") in reps

    start = 0
    while (start < len(events) and is_hotkey_kb(events[start])
           and events[start].data.get("action") == "up"):
        start += 1
    end = len(events)
    while end > start and is_hotkey_kb(events[end - 1]):
        end -= 1
    return events[start:end]
