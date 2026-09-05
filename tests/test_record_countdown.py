"""Record countdown: config plumbing, arming/cancel flow, and the
click-through overlay's timer."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.main_window as mw_mod  # noqa: E402
from core.config import AppConfig  # noqa: E402
from ui.countdown_overlay import RecordCountdown  # noqa: E402
from ui.theme import get_theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(app):
    orig = mw_mod.save_config
    mw_mod.save_config = lambda *a, **k: None
    win = mw_mod.MainWindow(AppConfig())
    yield win
    win.close()
    mw_mod.save_config = orig


@pytest.fixture(autouse=True)
def reset(window, monkeypatch):
    monkeypatch.setattr(window.rec_countdown, "start", lambda s: None)
    yield
    window._rec_arming = False
    window.cfg.record_countdown = 3
    window.record_btn.setText("●  Record")
    window.play_btn.setEnabled(True)


def test_toggle_arms_then_cancels(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_start_recording",
                        lambda temp=False: calls.append(temp))
    finished = []
    window.recording_finished.connect(finished.append)
    try:
        window.toggle_record()
        assert window._rec_arming
        assert "Cancel" in window.record_btn.text()
        assert window.is_busy()
        window.toggle_record()  # second press cancels
        assert not window._rec_arming
        assert calls == []
        assert finished == [""]  # waiters (builder) resume
    finally:
        window.recording_finished.disconnect(finished.append)


def test_countdown_finish_starts_recording(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_start_recording",
                        lambda temp=False: calls.append(temp))
    window.toggle_record(temp=True)
    assert window._rec_arming
    window._on_rec_countdown_done()
    assert calls == [True]
    assert not window._rec_arming


def test_zero_countdown_records_instantly(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_start_recording",
                        lambda temp=False: calls.append(temp))
    window.cfg.record_countdown = 0
    window.toggle_record()
    assert calls == [False]
    assert not window._rec_arming


def test_arming_blocks_playback(window, monkeypatch):
    monkeypatch.setattr(window, "_start_recording", lambda temp=False: None)
    window.toggle_record()
    assert window._rec_arming
    window.start_playback()
    assert not window._playback_active


def test_overlay_ticks_and_finishes(app):
    cd = RecordCountdown(get_theme())
    done = []
    ticks = []
    cd.finished.connect(lambda: done.append(1))
    cd.ticked.connect(ticks.append)
    cd.start(0.15)
    t0 = time.monotonic()
    while not done and time.monotonic() - t0 < 3:
        app.processEvents()
        time.sleep(0.01)
    assert done == [1]
    assert ticks and ticks[0] == 1
    assert not cd.isVisible()
    cd.close()


def test_settings_expose_record_countdown(app):
    from ui.settings_panel import SettingsDialog
    import ui.settings_panel as sp_mod
    orig = sp_mod.save_config
    sp_mod.save_config = lambda *a, **k: None
    try:
        cfg = AppConfig()
        dlg = SettingsDialog(cfg)
        dlg.rec_countdown.setValue(7.5)
        assert cfg.record_countdown == pytest.approx(7.5)
    finally:
        sp_mod.save_config = orig
