"""Settings must have real, observable effects — not just config writes.
Each test changes a setting through the dialog and asserts the running
window actually behaves differently."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.main_window as mw_mod  # noqa: E402
import ui.settings_panel as sp_mod  # noqa: E402
from core.config import AppConfig  # noqa: E402
from core.hotkeys import parse_combo  # noqa: E402
from ui.settings_panel import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_config_writes(monkeypatch):
    monkeypatch.setattr(sp_mod, "save_config", lambda *a, **k: None)
    monkeypatch.setattr(mw_mod, "save_config", lambda *a, **k: None)


@pytest.fixture(scope="module")
def window(app):
    win = mw_mod.MainWindow(AppConfig())
    yield win
    win.close()


def apply(win, **changes):
    """Drive the real dialog so the real signal path runs."""
    dlg = SettingsDialog(win.cfg)
    dlg.settings_changed.connect(win._apply_settings)
    for attr, value in changes.items():
        control = getattr(dlg, attr)
        if hasattr(control, "setValue"):
            control.setValue(value)
        elif hasattr(control, "setChecked"):
            control.setChecked(value)
        elif hasattr(control, "setCurrentText"):
            control.setCurrentText(value)
        else:
            control.setText(value)
            dlg._apply()
    return dlg


def test_ui_fps_changes_tick_interval(window):
    apply(window, fps=30)
    assert window._tick_timer.interval() == 1000 // 30
    apply(window, fps=60)
    assert window._tick_timer.interval() == 1000 // 60


def test_poll_and_deadzones_reach_the_next_recorder(window):
    apply(window, poll=333, stick_dz=20, trig_dz=9)
    rec = window.build_recorder(lambda ev: None)
    assert rec.poll_hz == 333
    assert rec._axis_poller is None or \
        rec._axis_poller.stick_deadzone == pytest.approx(0.20)
    assert window.stick_l._deadzone == pytest.approx(0.20)


def test_hotkeys_rebind_live(window):
    if window.hotkeys is None:
        pytest.skip("pynput unavailable")
    apply(window, hk_record="shift+f2")
    assert parse_combo("shift+f2") in window.hotkeys._bindings
    assert parse_combo("ctrl+f9") not in window.hotkeys._bindings
    # the dim hotkey tag on the Record button follows
    assert window._btn_hotkeys[window.record_btn].text() == "Shift+F2"
    apply(window, hk_record="ctrl+f9")


def test_delays_sync_both_directions(window):
    apply(window, loop_delay=3.5, countdown=6)
    assert window.loop_delay.value() == pytest.approx(3.5)
    assert window.start_delay.value() == 6
    assert "after 6s" in window.plan_label.text()
    # and main-screen edits flow back into cfg
    window.start_delay.setValue(2)
    assert window.cfg.playback.countdown_seconds == 2


def test_overlay_opacity_applies(window):
    apply(window, ov_opacity=40)
    assert window.overlay.bg_opacity == pytest.approx(0.40)


def test_overlay_hints_toggle(window):
    apply(window, ov_hints=False)
    assert not window.overlay.hints_label.isVisibleTo(window.overlay)
    apply(window, ov_hints=True)
    assert window.overlay.hints_label.isVisibleTo(window.overlay)
