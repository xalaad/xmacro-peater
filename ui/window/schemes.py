"""Controller scheme & device management - extracted verbatim from
ui.main_window."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from core.controllers.factory import create_backend, scheme_available

from ui import main_window as _mw

log = logging.getLogger(__name__)


class SchemesMixin:
    """Scheme/device methods mixed into MainWindow (plain class,
    no Qt base): self.* attributes come from MainWindow.__init__."""

    # ------------------------------------------------------------- schemes
    def _populate_schemes(self) -> None:
        self.scheme_combo.blockSignals(True)
        self.scheme_combo.clear()
        for sid, scheme in self.schemes.items():
            self.scheme_combo.addItem(scheme.name, sid)
            idx = self.scheme_combo.count() - 1
            ok, why = scheme_available(scheme)
            if not ok:
                item = self.scheme_combo.model().item(idx)
                item.setEnabled(False)
                item.setToolTip(why)
        self.scheme_combo.blockSignals(False)

    def _select_scheme(self, scheme_id: str) -> None:
        idx = self.scheme_combo.findData(scheme_id)
        if idx < 0:
            idx = 0
        self.scheme_combo.setCurrentIndex(idx)
        self._on_scheme_changed(idx)

    def _on_scheme_changed(self, index: int) -> None:
        sid = self.scheme_combo.itemData(index)
        scheme = self.schemes.get(sid)
        if scheme is None:
            return
        if self.backend is not None:
            self.backend.close()
        self.backend = create_backend(scheme)
        self.controller_w.set_art(scheme.art, scheme.layout)
        if hasattr(self, "tester_window"):
            self.tester_window.set_scheme(scheme.art, scheme.layout)
        self.activity.set_pad_labels(scheme.labels)
        self.cfg.controller_scheme = sid
        _mw.save_config(self.cfg)
        self._conn_check_countdown = 0

    def _autodetect_controller(self) -> None:
        """On startup: if the configured scheme's pad isn't plugged in but
        another scheme's is, switch to the connected one automatically."""
        if self.backend is not None and self.backend.is_connected():
            return
        # Prefer XInput schemes: an Xbox pad is also visible to DirectInput,
        # but its native path is the better test target.
        ordered = sorted(self.schemes.items(),
                         key=lambda kv: kv[1].backend != "xinput")
        for sid, scheme in ordered:
            if sid == self.cfg.controller_scheme:
                continue
            ok, _ = scheme_available(scheme)
            if not ok:
                continue
            probe = create_backend(scheme)
            if probe is None:
                continue
            connected = probe.is_connected()
            probe.close()
            if connected:
                log.info("Auto-selected connected controller scheme: %s", sid)
                self._select_scheme(sid)
                return

    def _update_conn_label(self, connected: bool) -> None:
        if self.backend is None:
            text, color = "○ backend unavailable", self.theme.warning
        elif connected:
            count = self.backend.device_count()
            text = (f"● connected — {self.backend.device_info()}"
                    + (f" · {count} devices" if count > 1 else ""))
            color = self.theme.success
        else:
            text, color = "○ not connected", self.theme.text_dim
        changed = text != self._conn_text
        if changed:
            self._conn_text = text
            self.conn_label.setText(text)
            self.conn_label.setStyleSheet(
                f"color: {color}; font-family: Consolas, monospace;"
                "font-size: 11px;")
            self.tester_window.set_conn_text(text, color)
        # Scheme marks + device combo poll REAL hardware (XInput sweeps
        # all 4 slots; empty slots answer slowly right after device
        # changes). Refresh them on a state change, else only every 3rd
        # tick — a pad appearing on another scheme still shows within 3s
        # without paying the sweep every second.
        self._conn_tick = (getattr(self, "_conn_tick", -1) + 1) % 3
        if changed or self._conn_tick == 0:
            self._update_scheme_marks()
            self._update_device_combo()

    def _update_device_combo(self) -> None:
        """Offer a device picker whenever the current backend can see more
        than one pad, so you can switch which one you test/listen to."""
        devices = (self.backend.list_devices()
                   if self.backend is not None else [])
        if len(devices) < 2:
            if self.device_combo.isVisible():
                self.device_label.hide()
                self.device_combo.hide()
                self.device_combo.blockSignals(True)
                self.device_combo.clear()
                self.device_combo.blockSignals(False)
            return
        current = [(self.device_combo.itemData(i),
                    self.device_combo.itemText(i))
                   for i in range(self.device_combo.count())]
        wanted = [(idx, label) for idx, label in devices]
        if current != wanted:
            held = self.device_combo.currentData()
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for idx, label in wanted:
                self.device_combo.addItem(label, idx)
            pos = self.device_combo.findData(held)
            if pos >= 0:
                self.device_combo.setCurrentIndex(pos)
            self.device_combo.blockSignals(False)
        self.device_label.show()
        self.device_combo.show()

    def _on_device_changed(self, _index: int) -> None:
        from core.controllers.pygame_backend import PygameBackend
        from core.controllers.xinput_backend import XInputBackend
        idx = self.device_combo.currentData()
        scheme = self.schemes.get(self.cfg.controller_scheme)
        if idx is None or scheme is None:
            return
        try:
            if scheme.backend == "xinput":
                new_backend = XInputBackend(user_index=idx)
            else:
                new_backend = PygameBackend(scheme, joystick_index=idx)
        except RuntimeError as e:
            log.warning("Device switch failed: %s", e)
            return
        if self.backend is not None:
            self.backend.close()
        self.backend = new_backend
        self._conn_check_countdown = 0
        self._conn_text = ""
        self.activity.add_line(
            f"Now testing: {new_backend.device_info()}",
            QColor(self.theme.accent))

    def _device_counts(self) -> tuple[int, int]:
        """(xinput pads, non-XInput DirectInput pads).

        An Xbox pad is visible to BOTH APIs — subtracting the XInput count
        from pygame's total leaves the pads that genuinely belong to the
        PlayStation/generic schemes, so a lone Xbox pad doesn't light every
        scheme's dot green.
        """
        from core.controllers.pygame_backend import (
            PYGAME_AVAILABLE, PygameBackend, pygame)
        from core.controllers.xinput_backend import XInputBackend
        if not hasattr(self, "_xinput_probe"):
            try:
                self._xinput_probe = XInputBackend()
            except RuntimeError:
                self._xinput_probe = None
        xinput_n = (self._xinput_probe.device_count()
                    if self._xinput_probe is not None else 0)
        pygame_n = 0
        if PYGAME_AVAILABLE:
            if isinstance(self.backend, PygameBackend):
                pygame_n = self.backend.device_count()
            else:
                try:
                    if not pygame.joystick.get_init():
                        pygame.init()
                        pygame.joystick.init()
                    pygame_n = pygame.joystick.get_count()
                except Exception:
                    pygame_n = 0
        return xinput_n, max(0, pygame_n - xinput_n)

    def _dot_icon(self, on: bool) -> QIcon:
        key = (self.theme.name, on)
        if key not in self._dot_icons:
            pm = QPixmap(12, 12)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.theme.success if on else self.theme.border))
            p.drawEllipse(2, 2, 8, 8)
            p.end()
            self._dot_icons[key] = QIcon(pm)
        return self._dot_icons[key]

    def _update_scheme_marks(self) -> None:
        """Selector shows, per scheme, whether a pad of THAT type is
        connected (green dot) and how many — a single Xbox pad marks only
        the Xbox scheme, not every scheme that could technically read it."""
        xinput_n, other_n = self._device_counts()
        for i in range(self.scheme_combo.count()):
            sid = self.scheme_combo.itemData(i)
            scheme = self.schemes.get(sid)
            if scheme is None:
                continue
            ok, _ = scheme_available(scheme)
            n = (xinput_n if scheme.backend == "xinput" else other_n) if ok \
                else 0
            self.scheme_combo.setItemData(
                i, self._dot_icon(n > 0), Qt.ItemDataRole.DecorationRole)
            text = scheme.name + (f"  ·  {n} connected" if n else "")
            if self.scheme_combo.itemText(i) != text:
                self.scheme_combo.setItemText(i, text)
