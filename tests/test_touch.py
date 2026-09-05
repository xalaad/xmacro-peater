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
