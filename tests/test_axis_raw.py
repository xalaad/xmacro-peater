"""The axis poller must record raw values: deadzone gates noise to zero but
never rescales what gets recorded."""
import time

from core.capture.axis_poller import AxisPoller
from core.controllers.base import ControllerBackend, neutral_state


class FixedBackend(ControllerBackend):
    name = "fixed"

    def __init__(self, **overrides):
        self.overrides = overrides

    def is_connected(self):
        return True

    def read(self):
        s = neutral_state()
        s.update(self.overrides)
        return s


def collect(backend, ticks=8, hz=500, **kw):
    events = []
    poller = AxisPoller(backend, events.append, hz=hz, **kw)
    poller.start()
    time.sleep(ticks / hz + 0.05)
    poller.stop()
    return events


def test_raw_value_not_rescaled():
    events = collect(FixedBackend(lx=0.5, ly=-0.25), stick_deadzone=0.1)
    axes = [e for e in events if e["src"] == "pad_axis" and e["stick"] == "left"]
    assert axes and axes[0]["x"] == 0.5 and axes[0]["y"] == -0.25


def test_noise_inside_deadzone_records_zero():
    events = collect(FixedBackend(lx=0.05, ly=0.05), stick_deadzone=0.1)
    assert [e for e in events if e["src"] == "pad_axis"] == []


def test_trigger_raw_and_gated():
    events = collect(FixedBackend(lt=0.6, rt=0.01),
                     trigger_deadzone=0.02)
    trig = [e for e in events if e["src"] == "pad_trigger"]
    assert len(trig) == 1
    assert trig[0]["trigger"] == "left" and trig[0]["value"] == 0.6
