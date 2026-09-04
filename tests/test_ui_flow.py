"""Offscreen UI tests: every setting applies and the playback section
adapts to the selected mode."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.main_window as mw_mod  # noqa: E402
import ui.settings_panel as sp_mod  # noqa: E402
from core.config import AppConfig  # noqa: E402
from ui.settings_panel import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_config_writes(monkeypatch):
    """UI applies settings live — keep tests from clobbering the real
    app_config.json."""
    monkeypatch.setattr(sp_mod, "save_config", lambda *a, **k: None)
    monkeypatch.setattr(mw_mod, "save_config", lambda *a, **k: None)


@pytest.fixture(scope="module")
def window(app):
    win = mw_mod.MainWindow(AppConfig())
    yield win
    win.close()


# ---------------------------------------------------------------- settings
def test_every_setting_applies(app):
    cfg = AppConfig()
    dlg = SettingsDialog(cfg)

    dlg.poll.setValue(250)
    assert cfg.poll_hz == 250
    dlg.stick_dz.setValue(12)
    assert cfg.stick_deadzone == pytest.approx(0.12)
    assert dlg.stick_dz_label.text() == "12%"
    dlg.trig_dz.setValue(5)
    assert cfg.trigger_deadzone == pytest.approx(0.05)
    dlg.countdown.setValue(7)
    assert cfg.playback.countdown_seconds == 7
    dlg.loop_delay.setValue(2.5)
    assert cfg.playback.loop_delay == pytest.approx(2.5)
    dlg.ov_opacity.setValue(55)
    assert cfg.overlay.opacity == pytest.approx(0.55)
    dlg.ov_hints.setChecked(False)
    assert cfg.overlay.show_hints is False
    dlg.fps.setValue(30)
    assert cfg.ui_fps == 30

    dlg.hk_record.setText("shift+f5")
    dlg._apply()
    assert cfg.hotkeys.record_toggle == "shift+f5"
    dlg.hk_play.setText("alt+p")
    dlg._apply()
    assert cfg.hotkeys.play_last == "alt+p"


def test_invalid_hotkey_falls_back(app):
    cfg = AppConfig()
    dlg = SettingsDialog(cfg)
    dlg.hk_record.setText("   ")
    dlg._apply()
    assert cfg.hotkeys.record_toggle == "ctrl+f9"


# ---------------------------------------------------------- playback plan
def test_playback_section_adapts_to_mode(window):
    win = window
    win.loop_mode.setCurrentIndex(0)  # Play once
    assert not win.loop_count.isVisible() if win.isVisible() else True
    assert "once" in win.plan_label.text().lower()

    win.loop_mode.setCurrentIndex(1)  # Repeat N
    win.loop_count.setValue(8)
    assert "8 runs" in win.plan_label.text()
    assert win._repeat_delay_row.isVisibleTo(win)
    assert win.loop_count.isVisibleTo(win)

    win.loop_mode.setCurrentIndex(2)  # Forever
    assert "loops until" in win.plan_label.text().lower()
    assert not win.loop_count.isVisibleTo(win)
    assert win._repeat_delay_row.isVisibleTo(win)

    win.start_delay.setValue(0)
    assert "instantly" in win.plan_label.text()
    win.start_delay.setValue(3)
    assert "after 3s" in win.plan_label.text()


def test_delay_edits_sync_to_config(window):
    win = window
    win.loop_delay.setValue(4.25)
    assert win.cfg.playback.loop_delay == pytest.approx(4.25)
    win.start_delay.setValue(5)
    assert win.cfg.playback.countdown_seconds == 5


def test_overlay_loop_controls_sync(window):
    """Overlay repeat controls mirror the main screen both ways; the run
    count shows only for Repeat N times."""
    win = window
    win.loop_mode.setCurrentIndex(2)  # forever -> full-width mode line
    assert win.overlay.loop_mode.currentIndex() == 2
    assert not win.overlay.loop_count.isVisibleTo(win.overlay)

    win.overlay.loop_mode.setCurrentIndex(1)  # N times -> half/half
    assert win.loop_mode.currentIndex() == 1
    assert win.overlay.loop_count.isVisibleTo(win.overlay)

    win.overlay.loop_count.setValue(12)
    assert win.loop_count.value() == 12
    assert "12 runs" in win.plan_label.text()
    win.loop_count.setValue(7)
    assert win.overlay.loop_count.value() == 7
    win.loop_mode.setCurrentIndex(0)


# ---------------------------------------------------------------- guards
def test_record_blocked_during_simulation(window):
    win = window
    win._simulating = True
    try:
        win.toggle_record()
        assert win.recorder is None or not win.recorder.is_recording
    finally:
        win._simulating = False
