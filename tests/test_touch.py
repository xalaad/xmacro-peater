"""Touch mode: capture transforms taps/drags into absolute gesture events,
files round-trip them, and playback routes them to the touch injector
(with an absolute-mouse fallback). No real injection happens in tests."""
import time

import core.playback.touch as touch_mod
import core.playback.virtual_output as vo
from core.capture.keyboard_mouse import KeyboardMouseCapture
from core.events import MacroEvent, MacroFile
from core.playback.touch import adapt_touch_events


def test_macro_file_roundtrips_screen_rect(tmp_path):
    m = MacroFile(events=[MacroEvent(0.0, "touch",
                                     {"action": "down", "x": 10, "y": 20})],
                  screen={"x": 0, "y": 0, "w": 1920, "h": 1080})
    p = tmp_path / "t.json"
    m.save(p)
    loaded = MacroFile.load(p)
    assert loaded.screen == {"x": 0, "y": 0, "w": 1920, "h": 1080}


def test_touch_coords_rescale_to_current_screen(monkeypatch):
    """A gesture recorded at 1920x1080 must land on the same RELATIVE
    spots on a 1280x720 laptop."""
    monkeypatch.setattr(touch_mod, "virtual_screen_rect",
                        lambda: {"x": 0, "y": 0, "w": 1280, "h": 720})
    events = [
        MacroEvent(0.0, "touch", {"action": "down", "x": 960, "y": 540}),
        MacroEvent(0.1, "touch", {"action": "up", "x": 1920, "y": 1080}),
        MacroEvent(0.2, "kb", {"action": "down", "key": "char:a"}),
    ]
    out = adapt_touch_events(
        events, {"x": 0, "y": 0, "w": 1920, "h": 1080})
    assert (out[0].data["x"], out[0].data["y"]) == (640, 360)  # center
    assert (out[1].data["x"], out[1].data["y"]) == (1280, 720)
    assert out[2] is events[2]          # non-touch events untouched
    assert events[0].data["x"] == 960   # originals never mutated


def test_touch_coords_untouched_on_same_screen(monkeypatch):
    rect = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    monkeypatch.setattr(touch_mod, "virtual_screen_rect", lambda: rect)
    events = [MacroEvent(0.0, "touch", {"action": "down",
                                        "x": 5, "y": 6})]
    assert adapt_touch_events(events, dict(rect)) is events


def test_legacy_takes_without_screen_pass_through():
    events = [MacroEvent(0.0, "touch", {"action": "down",
                                        "x": 5, "y": 6})]
    assert adapt_touch_events(events, None) is events


def test_cross_screen_mouse_path_replays_absolute(monkeypatch):
    """On a different screen, raw counts are meaningless (pointer speed/
    accel/resolution differ) — moves carrying the cursor path convert to
    rescaled absolute events; old takes without a path stay relative."""
    monkeypatch.setattr(touch_mod, "virtual_screen_rect",
                        lambda: {"x": 0, "y": 0, "w": 1280, "h": 720})
    events = [
        MacroEvent(0.0, "mouse_move",
                   {"dx": 7, "dy": 3, "px": 960, "py": 540}),
        MacroEvent(0.1, "mouse_move", {"dx": 5, "dy": 5}),  # legacy
        MacroEvent(0.2, "mouse_btn", {"action": "down", "button": "left"}),
    ]
    out = adapt_touch_events(
        events, {"x": 0, "y": 0, "w": 1920, "h": 1080})
    assert out[0].src == "mouse_abs"
    assert (out[0].data["x"], out[0].data["y"]) == (640, 360)
    assert out[1] is events[1]   # no recorded path: left as raw counts
    assert out[2] is events[2]


def test_exact_path_mode_converts_even_on_same_screen(monkeypatch):
    """'Replay exact cursor path' ON: moves become absolute at the
    recorded positions even when the screen matches — the fix for
    touchpads and pointer settings where relative counts drift."""
    from core.playback.touch import adapt_events
    rect = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    monkeypatch.setattr(touch_mod, "virtual_screen_rect", lambda: rect)
    events = [MacroEvent(0.0, "mouse_move",
                         {"dx": 7, "dy": 3, "px": 800, "py": 600})]
    out = adapt_events(events, dict(rect), force_abs_mouse=True)
    assert out[0].src == "mouse_abs"
    assert (out[0].data["x"], out[0].data["y"]) == (800, 600)
    # legacy take without screen metadata: identity scaling still works
    out2 = adapt_events(events, None, force_abs_mouse=True)
    assert (out2[0].data["x"], out2[0].data["y"]) == (800, 600)
    # OFF on the same screen: untouched raw counts
    assert adapt_events(events, dict(rect), force_abs_mouse=False) \
        is events


def test_raw_capture_rides_cursor_path(monkeypatch):
    from core.capture.raw_mouse import RawMouseCapture
    if not RawMouseCapture.available():
        return
    got = []
    cap = RawMouseCapture(got.append)
    monkeypatch.setattr(cap, "_cursor_pos", lambda: (321, 654))
    cap._emit_move(4, -2)
    assert got == [{"src": "mouse_move", "dx": 4, "dy": -2,
                    "px": 321, "py": 654}]


class FakeButton:
    def __init__(self, name):
        self._n = name

    def __str__(self):
        return f"Button.{self._n}"


def test_touch_capture_transform():
    events = []
    cap = KeyboardMouseCapture(events.append, touch_mode=True)
    cap._on_click(100, 200, FakeButton("left"), True)     # finger down
    cap._on_move(120, 220)                                # swipe...
    time.sleep(0.01)
    cap._on_move(150, 260)
    cap._on_click(150, 260, FakeButton("left"), False)    # lift
    cap._on_click(300, 300, FakeButton("right"), True)    # non-touch button
    cap._on_move(400, 400)                                # no contact: ignored

    kinds = [(e["src"], e.get("action")) for e in events]
    assert kinds[0] == ("touch", "down")
    assert ("touch", "move") in kinds
    assert ("touch", "up") in kinds
    assert kinds[-1] == ("mouse_btn", "down")  # right stays a mouse event
    down = events[0]
    assert down["x"] == 100 and down["y"] == 200


def test_touch_events_roundtrip(tmp_path):
    macro = MacroFile(events=[
        MacroEvent(0.0, "touch", {"action": "down", "x": 10, "y": 20}),
        MacroEvent(0.1, "touch", {"action": "move", "x": 30, "y": 40}),
        MacroEvent(0.2, "touch", {"action": "up", "x": 30, "y": 40}),
    ])
    path = tmp_path / "touch.json"
    macro.save(path)
    loaded = MacroFile.load(path)
    assert [e.data["action"] for e in loaded.events] == ["down", "move", "up"]
    assert loaded.events[1].data["x"] == 30


class FakeInjector:
    calls = []

    @staticmethod
    def available():
        return True

    def __init__(self):
        FakeInjector.calls = []

    def down(self, x, y):
        FakeInjector.calls.append(("down", x, y))

    def move(self, x, y):
        FakeInjector.calls.append(("move", x, y))

    def up(self, x, y):
        FakeInjector.calls.append(("up", x, y))

    def release(self):
        FakeInjector.calls.append(("release",))


def test_playback_routes_touch_to_injector(monkeypatch):
    monkeypatch.setattr(vo, "TouchInjector", FakeInjector)
    out = vo.VirtualOutput(need_gamepad=False)
    for action, x, y in (("down", 5, 6), ("move", 7, 8), ("up", 7, 8)):
        out.send(MacroEvent(0, "touch", {"action": action, "x": x, "y": y}))
    assert FakeInjector.calls == [("down", 5, 6), ("move", 7, 8),
                                  ("up", 7, 8)]
    out.release_all()
    assert FakeInjector.calls[-1] == ("release",)


def test_playback_touch_fallback_absolute_mouse(monkeypatch):
    class NoInjector:
        @staticmethod
        def available():
            return False

    moved = []
    monkeypatch.setattr(vo, "TouchInjector", NoInjector)
    monkeypatch.setattr(vo, "set_cursor_pos", lambda x, y: moved.append((x, y)))
    out = vo.VirtualOutput(need_gamepad=False)
    out.send(MacroEvent(0, "touch", {"action": "down", "x": 50, "y": 60}))
    out.send(MacroEvent(0, "touch", {"action": "up", "x": 50, "y": 60}))
    assert moved == [(50, 60), (50, 60)]
    assert not out._held_mouse  # released again


# --------------------------------------------------- digitizer state machine
def test_tip_switch_keeps_a_paused_drag_as_one_contact():
    """A finger resting mid-drag stops producing reports; with a tip
    switch that must NOT split the gesture (the old timing-only rule
    did, which is why slow drags recorded as several contacts)."""
    from core.capture.raw_touch import RawTouchWatcher
    got = []
    w = RawTouchWatcher(
        on_down=lambda x, y: got.append(("down", x, y)),
        on_move=lambda x, y: got.append(("move", x, y)),
        on_up=lambda x, y: got.append(("up", x, y)),
        quiet_gap=0.12)
    w._tip_known = True
    w.handle_report(1.00, 100, 100, tip=True)
    w.handle_report(1.02, 110, 110, tip=True)
    w.handle_report(3.00, 200, 200, tip=True)   # 2s pause mid-drag
    w.check_gap(3.50)                            # must not lift
    w.handle_report(3.60, 210, 210, tip=False)   # real lift
    assert got == [("down", 100, 100), ("move", 110, 110),
                   ("move", 200, 200), ("up", 210, 210)]


def test_tip_switch_separates_consecutive_taps():
    from core.capture.raw_touch import RawTouchWatcher
    got = []
    w = RawTouchWatcher(
        on_down=lambda x, y: got.append(("down", x, y)),
        on_up=lambda x, y: got.append(("up", x, y)), quiet_gap=0.12)
    w._tip_known = True
    w.handle_report(1.0, 10, 10, tip=True)
    w.handle_report(1.1, 10, 10, tip=False)
    w.handle_report(1.15, 50, 50, tip=True)   # second tap right after
    w.handle_report(1.25, 50, 50, tip=False)
    assert got == [("down", 10, 10), ("up", 10, 10),
                   ("down", 50, 50), ("up", 50, 50)]


def test_timing_fallback_when_device_has_no_tip_switch():
    from core.capture.raw_touch import RawTouchWatcher
    got = []
    w = RawTouchWatcher(
        on_down=lambda x, y: got.append(("down", x, y)),
        on_up=lambda x, y: got.append(("up", x, y)), quiet_gap=0.12)
    w.handle_report(1.0, 7, 7)      # tip unknown -> burst logic
    w.handle_report(1.05, 8, 8)
    w.check_gap(1.30)
    assert got == [("down", 7, 7), ("up", 8, 8)]


def test_real_surface_report_pattern_is_one_gesture():
    """Replays the exact pattern captured from a Surface digitizer
    (tools/hid_dump.py): a burst of tip-down reports including a
    stationary pause, closed by a single tip-up report."""
    from core.capture.raw_touch import RawTouchWatcher
    downs, moves, ups = [], [], []
    w = RawTouchWatcher(
        on_down=lambda x, y: downs.append((x, y)),
        on_move=lambda x, y: moves.append((x, y)),
        on_up=lambda x, y: ups.append((x, y)),
        quiet_gap=0.12)
    w._tip_known = True
    t = 5.20
    w.handle_report(t, 1638, 995, tip=True)          # contact starts
    for i in range(40):                              # drag
        t += 0.016
        w.handle_report(t, 1638 + i * 4, 995 - i * 9, tip=True)
    for _ in range(6):                               # finger holds still
        t += 0.016
        w.handle_report(t, 1798, 635, tip=True)
    t += 0.016
    w.handle_report(t, 1800, 630, tip=False)         # lift
    assert len(downs) == 1 and len(ups) == 1
    assert downs[0] == (1638, 995) and ups[0] == (1800, 630)
    assert len(moves) > 20        # the path was recorded
    w.check_gap(t + 5.0)          # no phantom second lift
    assert len(ups) == 1


def test_stuck_contact_safety_net():
    """If a lift report is ever lost, the contact must not hang open."""
    from core.capture.raw_touch import STUCK_TIMEOUT, RawTouchWatcher
    ups = []
    w = RawTouchWatcher(on_down=lambda x, y: None,
                        on_up=lambda x, y: ups.append((x, y)),
                        quiet_gap=0.12)
    w._tip_known = True
    w.handle_report(1.0, 300, 400, tip=True)
    w.check_gap(1.0 + STUCK_TIMEOUT / 2)
    assert ups == []                       # still held: no premature lift
    w.check_gap(1.0 + STUCK_TIMEOUT + 0.1)
    assert ups == [(300, 400)]             # rescued
