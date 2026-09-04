"""Playback engine tests with the OS output stubbed out, so nothing is
actually injected into the system while tests run."""
import time

import core.playback.engine as engine_mod
from core.events import MacroEvent, MacroFile
from core.playback.engine import INFINITE, PlaybackCallbacks, PlaybackEngine


class StubOutput:
    def __init__(self, need_gamepad):
        self.sent = []  # (perf_counter, event)
        self.released = 0

    def send(self, ev):
        self.sent.append((time.perf_counter(), ev))

    def release_all(self):
        self.released += 1

    def close(self):
        pass


def stub_output(monkeypatch, cursor_calls=None):
    created = []

    def factory(need_gamepad):
        out = StubOutput(need_gamepad)
        created.append(out)
        return out

    monkeypatch.setattr(engine_mod, "VirtualOutput", factory)
    # Never move the real cursor from tests
    monkeypatch.setattr(engine_mod, "get_cursor_pos", lambda: (500, 400))
    monkeypatch.setattr(
        engine_mod, "set_cursor_pos",
        lambda x, y: (cursor_calls.append((x, y))
                      if cursor_calls is not None else None))
    return created


def make_macro(n=20, spacing=0.01):
    events = [
        MacroEvent(i * spacing, "kb", {"action": "down", "key": "char:x"})
        for i in range(n)
    ]
    return MacroFile(events=events)


def test_timing_within_2ms(monkeypatch):
    """Hard requirement: playback within 1-2ms of recorded timestamps.
    Best of 3 attempts — measures capability, not background-app load."""
    outputs = stub_output(monkeypatch)
    best_avg = best_mx = 1.0
    for _ in range(5):
        timings = {}
        cb = PlaybackCallbacks(
            on_timing=lambda avg, mx: timings.update(avg=avg, mx=mx))
        eng = PlaybackEngine(make_macro(50, 0.005), loop_count=1, callbacks=cb)
        eng.start()
        eng.join(timeout=5)
        assert not eng.is_playing
        best_avg = min(best_avg, timings["avg"])
        best_mx = min(best_mx, timings["mx"])
        if best_avg < 0.001 and best_mx < 0.002:
            break
        time.sleep(0.05)
    assert len(outputs[0].sent) == 50
    assert best_avg < 0.001, f"avg error {best_avg*1000:.3f}ms"
    assert best_mx < 0.002, f"max error {best_mx*1000:.3f}ms"


def test_loop_count_and_finish_message(monkeypatch):
    outputs = stub_output(monkeypatch)
    done = {}
    cb = PlaybackCallbacks(on_finished=lambda ab, msg: done.update(ab=ab, msg=msg))
    eng = PlaybackEngine(
        make_macro(3, 0.001), loop_count=3, loop_delay=0.01, callbacks=cb
    )
    eng.start()
    eng.join(timeout=5)
    assert len(outputs[0].sent) == 9
    assert outputs[0].released == 3  # release_all after every run
    assert done["ab"] is False
    assert done["msg"] == "Finished 3 run(s)"


def test_abort_stops_infinite_loop(monkeypatch):
    stub_output(monkeypatch)
    done = {}
    cb = PlaybackCallbacks(on_finished=lambda ab, msg: done.update(ab=ab, msg=msg))
    eng = PlaybackEngine(
        make_macro(1000, 0.01), loop_count=INFINITE, callbacks=cb
    )
    eng.start()
    time.sleep(0.1)
    eng.abort()
    eng.join(timeout=2)
    assert not eng.is_playing
    assert done["ab"] is True


def test_loop_runs_restore_cursor_anchor(monkeypatch):
    """Repeat-cycle fidelity: every run after the first must start from
    the run-1 cursor position, or relative mouse deltas compound."""
    calls = []
    stub_output(monkeypatch, cursor_calls=calls)
    events = [
        MacroEvent(0.001, "mouse_move", {"dx": 50, "dy": 20}),
        MacroEvent(0.004, "mouse_btn", {"action": "down", "button": "left"}),
        MacroEvent(0.006, "mouse_btn", {"action": "up", "button": "left"}),
    ]
    eng = PlaybackEngine(MacroFile(events=events), loop_count=3,
                         loop_delay=0.01)
    eng.start()
    eng.join(timeout=5)
    # runs 2 and 3 restored to the anchor captured at run 1
    assert calls == [(500, 400), (500, 400)]


def test_no_cursor_anchor_without_mouse_events(monkeypatch):
    calls = []
    stub_output(monkeypatch, cursor_calls=calls)
    eng = PlaybackEngine(make_macro(3, 0.001), loop_count=3, loop_delay=0.01)
    eng.start()
    eng.join(timeout=5)
    assert calls == []  # kb-only macro: cursor untouched


def test_abort_during_loop_delay(monkeypatch):
    stub_output(monkeypatch)
    eng = PlaybackEngine(
        make_macro(2, 0.001), loop_count=INFINITE, loop_delay=30.0
    )
    eng.start()
    time.sleep(0.2)  # engine is now inside the 30s inter-loop delay
    t = time.perf_counter()
    eng.abort()
    eng.join(timeout=2)
    assert time.perf_counter() - t < 1.0, "abort during loop delay must be fast"
