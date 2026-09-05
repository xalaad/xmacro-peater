"""Activity log: color-tagged entries (blue keyboard, green mouse, purple
controller) in a clean card panel — fade-in on insert, auto-scroll, search
filter, verbosity toggle.

High-frequency streams (mouse_move, pad_axis, pad_trigger) are coalesced to
at most one entry per source per 250ms so the log stays readable while the
recording still captures everything. Button names honor the active
controller scheme (Cross/Circle/… on PlayStation pads).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from core.events import MacroEvent

from ..animations import fade_in
from ..theme import Theme

MAX_ENTRIES = 400
COALESCE_S = 0.25
HIGH_FREQ = {"mouse_move", "mouse_abs", "pad_axis", "pad_trigger"}


def is_motion_event(ev: MacroEvent) -> bool:
    """Continuous-motion events (gated behind the Motion toggle)."""
    return (ev.src in HIGH_FREQ
            or (ev.src == "touch" and ev.data.get("action") == "move"))


class ActivityLog(QFrame):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.theme = theme
        self.pad_labels: dict[str, str] = {}
        self._last_coalesce: dict[str, float] = {}
        self._trigger_state = {"left": 0.0, "right": 0.0}
        # +12, not +8: Arabic/other tall scripts fall back to Segoe UI
        # whose glyphs (and marks) need the extra room to render in full
        self._row_height = QFontMetrics(self.font()).height() + 12

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.collapse_btn = QPushButton("▾ ACTIVITY")
        self.collapse_btn.setObjectName("sectionToggle")
        self.collapse_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_btn.setToolTip("Show / hide the activity log")
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        self.enabled_box = QCheckBox("Log")
        self.enabled_box.setChecked(True)
        # NoFocus everywhere clickable: a stray Space/Enter must never
        # toggle whatever was clicked last
        self.enabled_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.enabled_box.setToolTip(
            "Master switch: OFF silences the activity log entirely — "
            "nothing is added while it's unchecked (recording/playback "
            "themselves are unaffected)."
        )
        self.enabled_box.toggled.connect(
            lambda on: self.verbose.setEnabled(on))
        self.verbose = QCheckBox("Motion")
        self.verbose.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verbose.setToolTip(
            "Log continuous motion too: mouse movement, stick axes, and "
            "analog trigger travel. Presses and releases are always logged."
        )
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(160)
        self.search.textChanged.connect(self._apply_filter)
        clear_btn = QPushButton("Clear")
        clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.collapse_btn)
        bar.addWidget(self.enabled_box)
        bar.addWidget(self.verbose)
        bar.addStretch(1)
        bar.addWidget(self.search)
        bar.addWidget(clear_btn)
        layout.addLayout(bar)

        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        layout.addWidget(self.list, 1)
        self._collapsed = False
        self.setMinimumHeight(120)

    @property
    def motion_enabled(self) -> bool:
        return self.verbose.isChecked() and self.enabled_box.isChecked()

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.list.setVisible(not self._collapsed)
        self.search.setVisible(not self._collapsed)
        self.collapse_btn.setText(
            "▸ ACTIVITY" if self._collapsed else "▾ ACTIVITY")
        # Collapsed height must still fit the header row's tallest child
        # (the Clear button ≈32px) plus the 8px margins — 40px clipped it
        self.setMinimumHeight(48 if self._collapsed else 120)
        self.setMaximumHeight(48 if self._collapsed else 16777215)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme

    def set_pad_labels(self, labels: dict[str, str]) -> None:
        self.pad_labels = dict(labels)

    def clear(self) -> None:
        self.list.clear()

    # ------------------------------------------------------------------
    def describe(self, ev: MacroEvent) -> str:
        d = ev.data
        if ev.src == "kb":
            key = d["key"].split(":", 1)[1]
            return f"Key {key.upper()} {d['action']}"
        if ev.src == "mouse_move":
            return f"Mouse move ({d['dx']:+d}, {d['dy']:+d})"
        if ev.src == "mouse_abs":
            return f"Mouse to ({d['x']}, {d['y']})"
        if ev.src == "mouse_btn":
            return f"Mouse {d['button']} {d['action']}"
        if ev.src == "mouse_scroll":
            return f"Scroll {'up' if d['dy'] > 0 else 'down'}"
        if ev.src == "touch":
            return f"Touch {d['action']} at ({d['x']}, {d['y']})"
        if ev.src == "pad_btn":
            name = self.pad_labels.get(d["button"], d["button"])
            return f"Pad {name} {d['action']}"
        if ev.src == "pad_trigger":
            side = "L2" if d["trigger"] == "left" else "R2"
            side = self.pad_labels.get(side, side)
            return f"Pad {side} {d['value']:.2f}"
        if ev.src == "pad_axis":
            return f"Pad {d['stick']} stick ({d['x']:+.2f}, {d['y']:+.2f})"
        return ev.src

    def _color_for(self, src: str) -> QColor:
        if src == "kb":
            return QColor(self.theme.kb)
        if src.startswith("mouse"):
            return QColor(self.theme.mouse)
        return QColor(self.theme.pad)

    def add_event(self, ev: MacroEvent, prefix: str = "") -> None:
        if is_motion_event(ev):
            # Trigger PRESS and RELEASE (crossing zero) are discrete acts
            # and always logged — only the analog travel in between is
            # motion, gated behind the Motion checkbox.
            discrete = False
            if ev.src == "pad_trigger":
                prev = self._trigger_state.get(ev.data["trigger"], 0.0)
                value = ev.data["value"]
                self._trigger_state[ev.data["trigger"]] = value
                discrete = (prev == 0.0) != (value == 0.0)
            if not discrete:
                if not self.verbose.isChecked():
                    return
                now = time.monotonic()
                if now - self._last_coalesce.get(ev.src, 0.0) < COALESCE_S:
                    return
                self._last_coalesce[ev.src] = now
        self.add_line(f"{prefix}[{ev.t:7.3f}s]  {self.describe(ev)}",
                      self._color_for(ev.src))

    def add_line(self, text: str, color: QColor | None = None) -> None:
        if not self.enabled_box.isChecked():
            return  # master 'Log' switch: silence everything
        item = QListWidgetItem()
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {color.name() if color else self.theme.text};"
            "font-family: Consolas, 'Segoe UI', monospace;"
            "font-size: 12px;"
            "background: transparent; padding: 0 4px;"
        )
        item.setSizeHint(QSize(10, self._row_height))
        self.list.addItem(item)
        self.list.setItemWidget(item, label)
        # Fade only when idle-ish; bulk inserts skip the animation cost.
        if self.list.count() < MAX_ENTRIES:
            fade_in(label)

        while self.list.count() > MAX_ENTRIES:
            self.list.takeItem(0)
        text_filter = self.search.text().strip().lower()
        if text_filter:
            item.setHidden(text_filter not in text.lower())
        self.list.scrollToBottom()

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            widget = self.list.itemWidget(item)
            visible = not text or (widget and text in widget.text().lower())
            item.setHidden(not visible)
