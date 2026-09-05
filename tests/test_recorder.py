"""End-to-end recorder test with a scripted fake controller backend
(no real hardware, no pynput listeners)."""
import time

from core.controllers.base import ControllerBackend, neutral_state
from core.capture.keyboard_mouse import KeyboardMouseCapture
from core.recorder import MacroRecorder


class FakeLeft:
    def __str__(self):
        return "Button.left"


def test_touch_mode_coexists_with_real_mouse():
    """Touch mode must NOT hijack the mouse: signature-flagged events
    become gestures, unflagged (real mouse) events keep recording as
    normal clicks and relative motion."""
    got = []
    cap = KeyboardMouseCapture(got.append, touch_mode=True)
    # A finger: flagged touch -> gesture down/move/up
    cap._evt_is_touch = True
    cap._on_click(100, 100, FakeLeft(), True)
    cap._on_move(120, 130)
    cap._on_click(120, 130, FakeLeft(), False)
    # A real mouse right after: unflagged -> mouse events
    cap._evt_is_touch = False
    cap._on_move(300, 300)          # first move: only seeds last_pos
    cap._on_move(310, 305)
    cap._on_click(310, 305, FakeLeft(), True)
    cap._on_click(310, 305, FakeLeft(), False)
    srcs = [e["src"] for e in got]
    assert srcs == ["touch", "touch", "touch",
                    "mouse_move", "mouse_btn", "mouse_btn"]
    assert got[0]["action"] == "down" and got[2]["action"] == "up"
    # Relative deltas restarted AFTER the touch warp — no giant jump
    assert (got[3]["dx"], got[3]["dy"]) == (10, 5)
    assert got[4] == {"src": "mouse_btn", "action": "down",
                      "button": "left"}


def test_digitizer_watcher_owns_gestures_when_running():
    """With the raw digitizer watcher active, promoted touch clicks/moves
    must NOT double-emit — the watcher is the single gesture source."""
    got = []
    cap = KeyboardMouseCapture(got.append, touch_mode=True)
    cap._raw_gestures = object()  # pretend the watcher registered
    cap._evt_is_touch = True
    cap._on_click(10, 10, FakeLeft(), True)
    cap._on_move(20, 20)
    cap._on_click(20, 20, FakeLeft(), False)
    assert got == []  # nothing from the promoted path
    # Real mouse still records normally alongside
    cap._evt_is_touch = False
    cap._on_move(100, 100)
    cap._on_move(110, 105)
    assert [e["src"] for e in got] == ["mouse_move"]


def test_touch_mode_legacy_without_filter():
    """No win32 filter info (flag unknown): everything left-button still
    counts as touch — the old behavior stays for exotic setups."""
    got = []
    cap = KeyboardMouseCapture(got.append, touch_mode=True)
    cap._on_click(10, 10, FakeLeft(), True)
    cap._on_click(10, 10, FakeLeft(), False)
    assert [e["src"] for e in got] == ["touch", "touch"]


class ScriptedBackend(ControllerBackend):
    """Steps through a list of states, one per read() call (pollers each
    call read(), so states advance quickly at 125Hz)."""
    name = "scripted"

    def __init__(self, states):
        self.states = states
        self.i = 0

    def is_connected(self):
        return True

    def read(self):
        state = self.states[min(self.i, len(self.states) - 1)]
        self.i += 1
        out = neutral_state()
        out.update({k: (set(v) if k == "buttons" else v) for k, v in state.items()})
        return out


def test_recorder_captures_button_and_axis():
    press = {"buttons": {"A"}, "lx": 0.9, "ly": 0.0}
    states = [neutral_state()] * 3 + [press] * 6 + [neutral_state()] * 6
    rec = MacroRecorder(
        backend=ScriptedBackend(states),
        poll_hz=250,
        stick_deadzone=0.1,
        capture_keyboard_mouse=False,
    )
    rec.start()
    time.sleep(0.35)
    macro = rec.stop()

    srcs = [(e.src, e.data) for e in macro.events]
    downs = [d for s, d in srcs if s == "pad_btn" and d["action"] == "down"]
    ups = [d for s, d in srcs if s == "pad_btn" and d["action"] == "up"]
    axes = [d for s, d in srcs if s == "pad_axis"]
    assert downs and downs[0]["button"] == "A"
    assert ups and ups[0]["button"] == "A"
    assert axes, "stick deflection should emit pad_axis"
    # values are recorded RAW (deadzone is only a noise gate), so playback
    # reproduces exactly what the stick reported
    assert axes[0]["x"] == 0.9
    # timestamps monotonic
    times = [e.t for e in macro.events]
    assert times == sorted(times)


def test_recorder_stop_idempotent_and_restartable():
    rec = MacroRecorder(
        backend=ScriptedBackend([neutral_state()]),
        poll_hz=250,
        capture_keyboard_mouse=False,
    )
    assert rec.stop().events == []  # stop before start is a no-op
    rec.start()
    time.sleep(0.05)
    first = rec.stop()
    rec.start()
    time.sleep(0.05)
    second = rec.stop()
    assert first.events == [] and second.events == []  # neutral pad = no events
