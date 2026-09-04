"""End-to-end recorder test with a scripted fake controller backend
(no real hardware, no pynput listeners)."""
import time

from core.controllers.base import ControllerBackend, neutral_state
from core.recorder import MacroRecorder


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
