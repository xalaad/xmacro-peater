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


def test_touch_toggle_syncs_config_and_recorder(window):
    win = window
    win.touch_toggle.setChecked(True)
    assert win.cfg.touch_mode is True
    rec = win.build_recorder(lambda ev: None)
    assert rec.touch_mode is True
    win.touch_toggle.setChecked(False)
    assert win.cfg.touch_mode is False


def test_space_enter_swallowed_off_the_deck_list(window, monkeypatch):
    """Space/Enter must only act on the deck list — anywhere else in the
    main window they are consumed before a button can fire."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    win = window
    space = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                      Qt.KeyboardModifier.NoModifier)
    # On a button / the window itself: swallowed
    assert win.eventFilter(win.record_btn, space) is True
    assert win.eventFilter(win, space) is True
    assert win.eventFilter(win.activity.enabled_box, space) is True
    # On the deck list: plays
    calls = []
    monkeypatch.setattr(win, "start_playback", lambda: calls.append(1))
    assert win.eventFilter(win.rec_list, space) is True
    assert calls == [1]
    # In a text field: passes through untouched
    assert win.eventFilter(win.start_delay.field, space) is False


def test_controller_tab_is_last(window):
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == ["Keyboard", "Mouse", "Controller"]


def test_settings_reset_to_defaults(app, monkeypatch):
    from core.config import AppConfig
    monkeypatch.setattr(sp_mod, "confirm", lambda *a, **k: True)
    cfg = AppConfig()
    cfg.poll_hz = 500
    cfg.sounds = False
    cfg.hotkeys.record_toggle = "alt+r"
    cfg.playback.loop_delay = 42.0
    dlg = SettingsDialog(cfg)
    dlg._reset_defaults()
    assert cfg.poll_hz == 125
    assert cfg.sounds is True
    assert cfg.hotkeys.record_toggle == "ctrl+f9"
    assert cfg.playback.loop_delay == 1.0
    # widgets follow the reset
    assert dlg.poll.value() == 125
    assert dlg.hk_record.text() == "ctrl+f9"
    assert dlg.loop_delay.value() == 1.0


# ------------------------------------------------------------- touch taps
def test_monitor_reports_touch_taps_not_left_clicks(monkeypatch):
    import ui.live_monitor as lm

    class FakeButton:
        def __str__(self):
            return "Button.left"

    mon = lm.LiveInputMonitor()
    monkeypatch.setattr(lm, "_click_is_touch", lambda: True)
    mon._on_click(120, 340, FakeButton(), True)   # touch tap down
    mon._on_click(120, 340, FakeButton(), False)  # lift
    snap = mon.snapshot()
    assert snap["touch_taps"] == [(120, 340)]
    assert "left" not in snap["mouse_buttons"]  # NOT a mouse click
    # A genuine mouse click still reports as one
    monkeypatch.setattr(lm, "_click_is_touch", lambda: False)
    mon._on_click(10, 10, FakeButton(), True)
    snap = mon.snapshot()
    assert "left" in snap["mouse_buttons"]
    assert snap["touch_taps"] == []


# ------------------------------------------------------------ log toggle
def test_log_master_toggle_silences_activity(window):
    win = window
    win.activity.enabled_box.setChecked(True)
    n = win.activity.list.count()
    win.activity.enabled_box.setChecked(False)
    win.activity.add_line("must not appear")
    assert win.activity.list.count() == n
    assert not win.activity.verbose.isEnabled()  # Motion greys out
    assert win.cfg.log_enabled is False
    win.activity.enabled_box.setChecked(True)
    win.activity.add_line("appears")
    assert win.activity.list.count() == n + 1
    assert win.cfg.log_enabled is True


# ------------------------------------------------------------- side dock
def test_dock_mode_lifecycle(window):
    win = window
    win._enter_dock()
    try:
        assert win._docked
        assert win._right_collapsed
        assert win.geometry() == win._dock_rect(True)
        # Handle sits on the inner edge: first in layout iff docked right
        strip_first = win._content_lay.indexOf(win.collapse_btn) == 0
        assert strip_first == (win._dock_side == "right")
        win._toggle_drawer()
        assert not win._drawer_open
        assert win._drawer_anim.endValue() == \
            win._dock_rect(False).topLeft()
        # Slide-out landing: window leaves, only the edge tab remains
        win._after_drawer_closed()
        assert win.isHidden()
        assert win.dock_tab.isVisible()
        win._open_drawer()  # tab click
        assert win._drawer_open
        assert not win.dock_tab.isVisible()
        assert not win.isHidden()
    finally:
        win._exit_dock()
    assert not win._docked
    from PySide6.QtCore import QSettings
    assert not QSettings("MacroSuite", "InputMacroSuite").value(
        "docked", False, type=bool)


def test_dock_side_is_explicit(window):
    win = window
    win._enter_dock("left")
    try:
        assert win._dock_side == "left"
        assert win.geometry() == win._dock_rect(True)
        win._enter_dock("right")  # re-dock across the screen
        assert win._dock_side == "right"
        assert win.geometry() == win._dock_rect(True)
    finally:
        win._exit_dock()


# ---------------------------------------------------------------- guards
def test_record_blocked_during_simulation(window):
    win = window
    win._simulating = True
    try:
        win.toggle_record()
        assert win.recorder is None or not win.recorder.is_recording
    finally:
        win._simulating = False
