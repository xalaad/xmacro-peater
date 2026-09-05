"""Live visualizer feed: the UI tick, keyboard-layout sync and last-action
tracking - extracted verbatim from ui.main_window."""
from __future__ import annotations

import math
import time

from PySide6.QtGui import QColor

from core.controllers.base import neutral_state
from core.events import MacroEvent

from ui import main_window as _mw

from ..widgets.duration_picker import format_duration

TRIGGER_LOG_THRESHOLD = 0.30


def _compass(dx: float, dy: float) -> str:
    """Screen-space direction words: (+dx,+dy) is toward bottom-right."""
    octant = round(math.atan2(dy, dx) / (math.pi / 4)) % 8
    return ("right", "bottom-right", "bottom", "bottom-left",
            "left", "top-left", "top", "top-right")[octant]


class LiveFeedMixin:
    """Live-feed methods mixed into MainWindow (plain class, no
    Qt base): self.* attributes come from MainWindow.__init__."""

    def feed_visual_event(self, ev: MacroEvent) -> None:
        """Update the controller visual state from a macro event (used by
        real playback and by the Tester's dry-run simulation)."""
        s = self._playback_state
        d = ev.data
        if ev.src == "pad_btn":
            (s["buttons"].add if d["action"] == "down" else
             s["buttons"].discard)(d["button"])
        elif ev.src == "pad_axis":
            if d["stick"] == "left":
                s["lx"], s["ly"] = d["x"], d["y"]
            else:
                s["rx"], s["ry"] = d["x"], d["y"]
        elif ev.src == "pad_trigger":
            s["lt" if d["trigger"] == "left" else "rt"] = d["value"]

    # ------------------------------------------------------------- UI tick
    def _tick(self) -> None:
        snap = self.monitor.snapshot()

        # Overlay status line (cheap; overlay may be the only thing visible)
        if self.recorder is not None and self.recorder.is_recording:
            touch_tag = "touch · " if self.recorder.touch_mode else ""
            self.overlay.set_info(
                f"{touch_tag}{self.recorder.elapsed:.0f}s · "
                f"{self.recorder.event_count} events")
        elif self._playback_active and self.engine is None:
            # Scheduled wait: live countdown until the run starts
            remaining = getattr(self, "_sched_until", 0) - time.monotonic()
            if remaining > 0:
                self.overlay.set_info(f"▶ in {format_duration(remaining)}")
                self.stats.setText(
                    f"Scheduled — starts in {format_duration(remaining)}")
        elif self._playback_active and self._run_info:
            self.overlay.set_info(self._run_info)

        # Controller state — read even in mini mode so last-action works
        if self._playback_active or self._simulating:
            state = self._playback_state
            connected = True
        elif self.backend is not None:
            if self._conn_check_countdown <= 0:
                self._connected = self.backend.is_connected()
                self._update_conn_label(self._connected)
                self._sync_keyboard_layout()
                self._conn_check_countdown = self.cfg.ui_fps  # ~1s
            self._conn_check_countdown -= 1
            connected = self._connected
            state = self.backend.read() if connected else neutral_state()
        else:
            state, connected = neutral_state(), False
            if self._conn_check_countdown <= 0:
                self._update_conn_label(False)
                self._sync_keyboard_layout()
                self._conn_check_countdown = self.cfg.ui_fps
            self._conn_check_countdown -= 1

        self._track_last_action(snap, state)

        if self.tester_window.isVisible():
            self.tester_window.feed(snap, state, connected)

        if self.isHidden():
            return  # mini mode: skip all visualizer painting

        self.keyboard_w.frame(snap["keys"], snap["key_pulses"])
        self.mouse_w.frame(snap["mouse_buttons"], snap["move"], snap["scroll"])
        self.controller_w.frame(state, connected)
        self.stick_l.set_target(state["lx"], state["ly"],
                                "LEFT_THUMB" in state["buttons"])
        self.stick_r.set_target(state["rx"], state["ry"],
                                "RIGHT_THUMB" in state["buttons"])
        self.trigger_l.set_target(state["lt"])
        self.trigger_r.set_target(state["rt"])

        if self.recorder is not None and self.recorder.is_recording:
            self.stats.setText(
                f"Recording… {self.recorder.elapsed:.1f}s, "
                f"{self.recorder.event_count} events")

    def _sync_keyboard_layout(self) -> None:
        """Follow the FOREGROUND window's keyboard layout. The ~1s poll
        costs exactly two syscalls (the layout handle); the full label
        map is computed via ToUnicodeEx only ONCE per distinct layout
        ever seen, then served from cache."""
        hkl = _mw.kb_active_hkl()
        if not hkl or hkl == self._last_hkl:
            return
        self._last_hkl = hkl
        labels = self._layout_cache.get(hkl)
        if labels is None:
            labels = _mw.kb_layout_labels(hkl)
            self._layout_cache[hkl] = labels
        if labels:
            self._kb_labels = labels
            self.keyboard_w.set_layout_labels(labels)
            self.tester_window.keyboard_w.set_layout_labels(labels)

    def _track_last_action(self, snap: dict, state: dict) -> None:
        """Overlay 'last action' line + [test] activity entries while idle.
        Presses (keys, clicks, pad buttons, trigger pulls, scrolls) always
        log; continuous motion honors the Activity 'Motion' checkbox."""
        actions: list[tuple[str, str]] = []  # (text, color)
        for rep in snap["key_pulses"]:
            # Name the key in the ACTIVE layout's language, not English
            name = (getattr(self, "_kb_labels", {}).get(rep)
                    or rep.split(":", 1)[1].upper())
            actions.append(("Key " + name, self.theme.kb))
        for btn in sorted(snap["mouse_buttons"] - self._prev_mouse_buttons):
            actions.append((f"Mouse {btn} click", self.theme.mouse))
        self._prev_mouse_buttons = set(snap["mouse_buttons"])
        # Real touchscreen taps (detected via the OS touch signature) —
        # named honestly instead of masquerading as left clicks
        for tx, ty in snap.get("touch_taps", ()):
            actions.append((f"Touch tap at ({tx}, {ty})",
                            self.theme.accent2))
        for btn in sorted(state["buttons"] - self._prev_pad_buttons):
            name = self.activity.pad_labels.get(btn, btn)
            actions.append((f"Pad {name}", self.theme.pad))
        self._prev_pad_buttons = set(state["buttons"])

        # Analog trigger pulls are discrete acts — always logged
        for key, canon in (("lt", "L2"), ("rt", "R2")):
            v = state[key]
            if self._trigger_prev[key] < TRIGGER_LOG_THRESHOLD <= v:
                label = self.activity.pad_labels.get(canon, canon)
                actions.append((f"Pad {label} pull ({v:.2f})", self.theme.pad))
            self._trigger_prev[key] = v

        if snap["scroll"]:
            actions.append((
                f"Scroll {'up' if snap['scroll'] > 0 else 'down'}",
                self.theme.mouse))

        # Continuous motion: only when the Activity 'Motion' box is on
        if self.activity.motion_enabled:
            self._motion_acc[0] += snap["move"][0]
            self._motion_acc[1] += snap["move"][1]
            now = time.monotonic()
            adx, ady = self._motion_acc
            if (abs(adx) + abs(ady) > 24
                    and now - self._motion_logged_at > 0.45):
                px, py = snap["pos"]
                actions.append((
                    f"Mouse moved {_compass(adx, ady)} "
                    f"({adx:+d}, {ady:+d}) at ({px}, {py})",
                    self.theme.mouse))
                self._motion_acc = [0, 0]
                self._motion_logged_at = now
            for stick, kx, ky in (("left", "lx", "ly"), ("right", "rx", "ry")):
                x, y = state[kx], state[ky]
                mag = math.hypot(x, y)
                octant = _compass(x, -y) if mag > 0.25 else None
                if octant != self._stick_octant[stick] and octant is not None:
                    actions.append((
                        f"{stick.capitalize()} stick {octant} ({mag:.2f})",
                        self.theme.pad))
                self._stick_octant[stick] = octant
        else:
            self._motion_acc = [0, 0]

        if not actions:
            return
        busy = (self._playback_active or self._simulating
                or (self.recorder is not None and self.recorder.is_recording))
        if not busy:
            self.overlay.set_last_line(actions[-1][0])
            for text, color in actions:
                self.activity.add_line(f"[test] {text}", QColor(color))
