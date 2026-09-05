"""Global hotkeys with two-key combo support ("ctrl+f9") via pynput — they
work while a game has focus.

A binding is a set of canonical key names that must all be held; it fires on
the press of its final key, and only on an exact match (holding extra keys
does not trigger a smaller binding, so "esc" won't fire during "ctrl+esc").

Callbacks run on the listener thread; keep them tiny (emit a signal).
"""
from __future__ import annotations

import logging
import sys
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


# Canonical key name -> Windows virtual-key code(s), used to verify that
# a key we BELIEVE is held really still is (key-ups get lost across UAC
# prompts, Win+L, listener restarts — a phantom entry in _held would
# otherwise block every hotkey, including abort, until it's re-pressed).
_VERIFY_VKS: dict[str, tuple[int, ...]] = {
    "ctrl": (0x11,), "shift": (0x10,), "alt": (0x12,),
    "win": (0x5B, 0x5C),
    "esc": (0x1B,), "space": (0x20,), "tab": (0x09,), "enter": (0x0D,),
    **{f"f{i}": (0x6F + i,) for i in range(1, 25)},
    **{c: (ord(c.upper()),) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{d: (ord(d),) for d in "0123456789"},
}


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
        if cb is None and len(self._held) > 1:
            # No match — maybe a phantom key is polluting the set. Ask
            # the OS which of them are REALLY down and retry once.
            self._prune_stale_held()
            cb = self._bindings.get(frozenset(self._held))
        if cb is not None:
            try:
                cb()
            except Exception:
                log.exception("Hotkey callback failed for %s", self._held)

    def _prune_stale_held(self) -> None:
        """Drop held keys the OS says are up (missed key-up events)."""
        if sys.platform != "win32":  # pragma: no cover
            return
        import ctypes
        u32 = ctypes.windll.user32
        for name in list(self._held):
            vks = _VERIFY_VKS.get(name)
            if vks is None:
                continue  # unmapped key: can't verify, keep it
            if not any(u32.GetAsyncKeyState(vk) & 0x8000 for vk in vks):
                log.info("Dropping phantom held key %r", name)
                self._held.discard(name)

    def _on_release(self, key) -> None:
        name = _canonical_key_name(key)
        if name is not None:
            self._held.discard(name)


# Hotkey keystrokes at a recording's edges land within this many seconds
# of the first/last event; anything older is macro content.
_EDGE_WINDOW = 1.5


def trim_hotkey_artifacts(events: list, hotkey_reps: Iterable[str]) -> list:
    """Strip the start/stop combo's keystrokes from a recording's edges.

    Leading key-ups of hotkey keys (releasing the start combo) and
    trailing key-downs/ups of hotkey keys (pressing the stop combo) are
    removed. Mouse/pad/touch events interleave with the combo keystrokes
    at 125Hz, so the scan skips over NON-KEYBOARD events instead of
    requiring contiguity — otherwise a single mouse delta between the
    Ctrl-down and the stop would leave the combo press in the take, and
    replaying it would toggle recording back on. The scan stays inside a
    short window at each edge and stops at the first non-hotkey KEYBOARD
    event, so mid-recording use of the same keys is kept.
    """
    reps = set(hotkey_reps)

    def is_hotkey_kb(ev) -> bool:
        return ev.src == "kb" and ev.data.get("key") in reps

    if not events:
        return events
    drop: set[int] = set()

    t0 = events[0].t
    for i, ev in enumerate(events):
        if ev.t - t0 > _EDGE_WINDOW:
            break
        if is_hotkey_kb(ev):
            if ev.data.get("action") != "up":
                break  # a fresh press = real use of the key
            drop.add(i)
        elif ev.src == "kb":
            break  # real typing begins

    t_end = events[-1].t
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if t_end - ev.t > _EDGE_WINDOW:
            break
        if is_hotkey_kb(ev):
            drop.add(i)
        elif ev.src == "kb":
            break

    if not drop:
        return events
    return [ev for i, ev in enumerate(events) if i not in drop]
