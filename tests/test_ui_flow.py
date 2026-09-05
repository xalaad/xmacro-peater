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


# -------------------------------------------------------- keyboard layout
def test_viz_rep_maps_foreign_chars_to_physical_keys():
    """An Arabic (or any layout's) character must light the physical key
    that produced it — matched by virtual-key code, not the character."""
    import ui.live_monitor as lm

    class K:  # pynput KeyCode stand-in: Arabic shin on the physical A key
        vk = 0x41
        char = "ش"

    assert lm._viz_rep(K()) == "char:a"

    class K2:  # digit row under any layout
        vk = 0x31
        char = "1"

    assert lm._viz_rep(K2()) == "char:1"


def test_active_layout_labels_shape():
    from ui.widgets.keyboard_widget import active_layout_labels
    _hkl, labels = active_layout_labels()
    assert labels.get("char:a")  # every letter key has a cap
    assert all(k.startswith("char:") for k in labels)


def test_layout_labels_computed_once_per_hkl(window, monkeypatch):
    """The 1s poll must be two syscalls: full label maps compute once
    per distinct layout, then come from cache."""
    calls = []
    monkeypatch.setattr(
        mw_mod, "kb_layout_labels",
        lambda hkl: (calls.append(hkl), {"char:a": "X"})[1])
    monkeypatch.setattr(mw_mod, "kb_active_hkl", lambda: 0xABC)
    win = window
    win._last_hkl = None
    win._layout_cache.clear()
    win._sync_keyboard_layout()
    win._sync_keyboard_layout()          # same layout: cache hit
    win._last_hkl = None                 # re-activated layout
    win._sync_keyboard_layout()
    assert calls == [0xABC]              # computed exactly once


def test_any_layout_simulated_without_switching():
    """layout_labels works for ANY installed layout handle — simulate
    Arabic by loading its HKL directly (never activating it)."""
    import ctypes

    from ui.widgets.keyboard_widget import layout_labels
    hkl_ar = ctypes.windll.user32.LoadKeyboardLayoutW("00000401", 0)
    if not hkl_ar:
        pytest.skip("Arabic layout not installable on this machine")
    labels = layout_labels(hkl_ar & 0xFFFFFFFF)
    letter_caps = [labels.get(f"char:{c}") for c in "asdfjkl"]
    assert all(letter_caps)
    # The A-row must be ARABIC letters, not Latin fallbacks
    arabic = [c for c in letter_caps if "؀" <= c[0] <= "ۿ"]
    assert len(arabic) >= 5, f"expected Arabic caps, got {letter_caps}"


# ------------------------------------------------------------- touch taps
def test_win32_filters_survive_malformed_hook_data():
    """pynput invokes the filter for messages it can't convert, passing
    data with dwExtraInfo=None (or no data at all). A raised exception
    would PERMANENTLY kill the listener — the filters must shrug."""
    import ui.live_monitor as lm
    from core.capture.keyboard_mouse import KeyboardMouseCapture

    class NoneExtra:
        dwExtraInfo = None

    mon = lm.LiveInputMonitor()
    assert mon._win32_filter(0x0201, NoneExtra()) is True
    assert mon._win32_filter(0x020E, None) is True   # horizontal wheel
    assert mon._win32_filter(0x0200, object()) is True

    cap = KeyboardMouseCapture(lambda e: None, touch_mode=True)
    assert cap._win32_filter(0x0201, NoneExtra()) is True
    assert cap._win32_filter(0x020E, None) is True


def test_raw_touch_burst_coalescing():
    """Digitizer reports stream during a contact — only the first after a
    quiet gap counts as a new tap."""
    import ui.live_monitor as lm
    taps = []
    w = lm.RawTouchWatcher(on_tap=lambda x, y: taps.append((x, y)),
                           quiet_gap=0.35)
    w.handle_report(10.0, 1, 1)      # first contact -> tap
    w.handle_report(10.05, 2, 2)     # same contact streaming
    w.handle_report(10.30, 3, 3)
    w.handle_report(11.0, 4, 4)      # new contact after the gap -> tap
    assert taps == [(1, 1), (4, 4)]


def test_raw_touch_gesture_stream():
    """Recorder mode: reports become down/move/up with coalescing and a
    quiet-gap lift."""
    from core.capture.raw_touch import RawTouchWatcher
    got = []
    w = RawTouchWatcher(
        on_down=lambda x, y: got.append(("down", x, y)),
        on_move=lambda x, y: got.append(("move", x, y)),
        on_up=lambda x, y: got.append(("up", x, y)),
        quiet_gap=0.12)
    w.handle_report(1.000, 100, 100)   # contact begins
    w.handle_report(1.004, 105, 104)   # < 8ms: coalesced away
    w.handle_report(1.010, 110, 108)   # move
    w.handle_report(1.020, 120, 118)   # move
    w.check_gap(1.050)                 # still streaming: no lift
    w.check_gap(1.200)                 # quiet > gap: lift at last pos
    w.handle_report(2.000, 300, 300)   # NEW contact
    w.check_gap(2.200)
    assert got == [("down", 100, 100), ("move", 110, 108),
                   ("move", 120, 118), ("up", 120, 118),
                   ("down", 300, 300), ("up", 300, 300)]


def test_phantom_click_retracted_when_raw_tap_arrives_late(monkeypatch):
    """Race: the synthesized click can be recorded BEFORE the digitizer
    report is processed — the raw tap must retract it."""
    import ui.live_monitor as lm

    class FakeButton:
        def __str__(self):
            return "Button.left"

    mon = lm.LiveInputMonitor()
    mon._raw_touch_ok = True
    monkeypatch.setattr(lm, "_click_is_touch", lambda: False)
    mon._on_click(500, 400, FakeButton(), True)   # click wins the race
    assert "left" in mon._mouse_buttons           # briefly recorded...
    mon._on_raw_touch(500, 400)                   # digitizer catches up
    mon._on_click(500, 400, FakeButton(), False)  # lift routed to touch
    snap = mon.snapshot()
    assert snap["touch_taps"] == [(500, 400)]     # exactly one tap
    assert "left" not in snap["mouse_buttons"]    # click retracted


def test_flagged_click_reports_touch(monkeypatch):
    """A left click flagged by the hook's dwExtraInfo signature (the
    Surface path) must count as touch, not mouse."""
    import ui.live_monitor as lm

    class FakeButton:
        def __str__(self):
            return "Button.left"

    class Data:
        dwExtraInfo = 0xFF515780  # touch signature + touch bit

    mon = lm.LiveInputMonitor()
    mon._win32_filter(0x0201, Data())
    assert mon._flagged_touch is True
    mon._on_click(50, 60, FakeButton(), True)
    mon._win32_filter(0x0202, Data())
    mon._on_click(50, 60, FakeButton(), False)
    snap = mon.snapshot()
    assert snap["touch_taps"] == [(50, 60)]
    assert "left" not in snap["mouse_buttons"]


def test_raw_watcher_suppresses_phantom_click(monkeypatch):
    """With the digitizer watcher running, the synthesized click that
    follows a raw contact is suppressed and NOT double-reported."""
    import ui.live_monitor as lm

    class FakeButton:
        def __str__(self):
            return "Button.left"

    mon = lm.LiveInputMonitor()
    mon._raw_touch_ok = True
    mon._on_raw_touch(200, 300)              # digitizer saw the contact
    mon._on_click(200, 300, FakeButton(), True)   # phantom click follows
    mon._on_click(200, 300, FakeButton(), False)
    snap = mon.snapshot()
    assert snap["touch_taps"] == [(200, 300)]  # exactly once
    assert "left" not in snap["mouse_buttons"]


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


# ----------------------------------------------------- screen-fit windows
def test_saved_geometry_clamped_to_screen():
    """Dimensions saved on a big desktop must shrink & move on-screen
    when restored on a small laptop."""
    from PySide6.QtCore import QRect

    from ui.main_window import MainWindow
    laptop = QRect(0, 0, 1280, 720)
    big = QRect(400, 200, 1600, 900)      # saved on a 1920x1080 desktop
    fit = MainWindow._clamped_rect(big, laptop)
    assert fit.width() <= 1280 and fit.height() <= 720
    assert laptop.contains(fit)
    offscreen = QRect(1500, 800, 800, 600)  # was on a monitor to the right
    fit2 = MainWindow._clamped_rect(offscreen, laptop)
    assert laptop.contains(fit2)


def test_minimum_size_never_exceeds_screen(window):
    screen = window.screen()
    wa = screen.availableGeometry()
    for collapsed in (False, True):
        w, h = window._min_size(collapsed)
        assert w <= wa.width() and h <= wa.height()


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


def test_reset_restores_hardware_touch_default(app, monkeypatch):
    """Reset must set touch mode from the DEVICE, not the static
    default — otherwise a touchscreen user resets into a disabled mode."""
    import core.config as cfg_mod
    from core.config import AppConfig
    monkeypatch.setattr(sp_mod, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(cfg_mod, "has_touchscreen", lambda: True)
    cfg = AppConfig()
    cfg.touch_mode = False
    dlg = SettingsDialog(cfg)
    dlg._reset_defaults()
    assert cfg.touch_mode is True
