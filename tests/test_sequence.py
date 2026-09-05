"""Sequence model + engine tests with OS output stubbed (nothing is
injected into the system)."""
import time

import pytest

import core.sequence as seq_mod
from core.events import MacroEvent, MacroFile
from core.playback.engine import INFINITE
from core.sequence import (
    Sequence,
    SequenceCallbacks,
    SequenceEngine,
    SequenceStep,
)


class StubOutput:
    def __init__(self, need_gamepad):
        self.sent = []
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

    monkeypatch.setattr(seq_mod, "VirtualOutput", factory)
    monkeypatch.setattr(seq_mod, "get_cursor_pos", lambda: (500, 400))
    monkeypatch.setattr(
        seq_mod, "set_cursor_pos",
        lambda x, y: (cursor_calls.append((x, y))
                      if cursor_calls is not None else None))
    return created


def kb_macro(marker: str, n=3, spacing=0.002):
    events = [
        MacroEvent(i * spacing, "kb", {"action": "down",
                                       "key": f"char:{marker}"})
        for i in range(n)
    ]
    return MacroFile(events=events)


def mouse_macro():
    return MacroFile(events=[
        MacroEvent(0.001, "mouse_move", {"dx": 40, "dy": 10}),
        MacroEvent(0.003, "mouse_btn", {"action": "down", "button": "left"}),
        MacroEvent(0.004, "mouse_btn", {"action": "up", "button": "left"}),
    ])


def run_engine(steps, **kw):
    eng = SequenceEngine(steps, **kw)
    eng.start()
    eng.join(timeout=10)
    assert not eng.is_playing
    return eng


# ---------------------------------------------------------------- model
def test_sequence_roundtrip(tmp_path):
    seq = Sequence(steps=[
        SequenceStep("a.json", runs=3, wait=1.5),
        SequenceStep("b.json"),
    ])
    path = tmp_path / "combo.json"
    seq.save(path)
    loaded = Sequence.load(path)
    assert loaded.steps == seq.steps


def test_load_rejects_foreign_files(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"format": "macro-suite", "events": []}',
                    encoding="utf-8")
    with pytest.raises(ValueError):
        Sequence.load(path)


def test_resolve_names_every_problem(tmp_path):
    kb_macro("a").save(tmp_path / "good.json")
    seq = Sequence(steps=[
        SequenceStep("good.json"),
        SequenceStep("gone.json"),
        SequenceStep("also_gone.json"),
    ])
    with pytest.raises(ValueError) as e:
        seq.resolve(tmp_path)
    assert "step 2: gone.json is missing" in str(e.value)
    assert "step 3: also_gone.json is missing" in str(e.value)


def test_pass_duration_skips_trailing_wait():
    seq = Sequence(steps=[
        SequenceStep("a.json", runs=2, wait=1.0),  # 2*3 + 2*1
        SequenceStep("b.json", runs=1, wait=9.0),  # 5, trailing 9 skipped
    ])
    est = seq.pass_duration({"a.json": 3.0, "b.json": 5.0})
    assert est == pytest.approx(13.0)


# ---------------------------------------------------------------- engine
def test_chain_order_runs_and_passes(monkeypatch):
    outputs = stub_output(monkeypatch)
    done = {}
    cb = SequenceCallbacks(
        on_finished=lambda ab, msg: done.update(ab=ab, msg=msg))
    steps = [
        (SequenceStep("a.json", runs=2, wait=0.005), kb_macro("a", 2)),
        (SequenceStep("b.json", runs=1), kb_macro("b", 1)),
    ]
    run_engine(steps, loop_count=2, loop_delay=0.005, callbacks=cb)
    keys = [ev.data["key"][-1] for _, ev in outputs[0].sent]
    # per pass: a-run(2 events) ×2 then b-run(1 event); two passes
    assert keys == ["a", "a", "a", "a", "b"] * 2
    assert outputs[0].released == 6  # release_all after every run
    assert done["ab"] is False
    assert done["msg"] == "Finished 2 pass(es)"


def test_step_wait_is_scheduled_from_run_start(monkeypatch):
    """The wait after a run targets t0+duration+wait (drift-free), so the
    gap between step starts must match duration+wait, not exceed it by
    callback overhead."""
    outputs = stub_output(monkeypatch)
    starts = []
    cb = SequenceCallbacks(
        on_step_started=lambda i, n, name, r, rr:
            starts.append(time.perf_counter()))
    steps = [(SequenceStep("a.json", runs=3, wait=0.05),
              kb_macro("a", 2, spacing=0.01))]  # duration 0.01
    run_engine(steps, loop_count=1, callbacks=cb)
    assert len(starts) == 3
    for gap in (starts[1] - starts[0], starts[2] - starts[1]):
        assert gap == pytest.approx(0.06, abs=0.02)


def test_progress_callbacks_report_steps(monkeypatch):
    stub_output(monkeypatch)
    seen = []
    cb = SequenceCallbacks(
        on_step_started=lambda i, n, name, r, rr:
            seen.append((i, n, name, r, rr)))
    steps = [
        (SequenceStep("a.json", runs=2), kb_macro("a", 1)),
        (SequenceStep("b.json"), kb_macro("b", 1)),
    ]
    run_engine(steps, loop_count=1, callbacks=cb)
    assert seen == [
        (0, 2, "a.json", 1, 2),
        (0, 2, "a.json", 2, 2),
        (1, 2, "b.json", 1, 1),
    ]


def test_abort_stops_infinite_chain_fast(monkeypatch):
    stub_output(monkeypatch)
    done = {}
    cb = SequenceCallbacks(
        on_finished=lambda ab, msg: done.update(ab=ab, msg=msg))
    steps = [(SequenceStep("a.json", runs=1, wait=30.0),
              kb_macro("a", 2)),
             (SequenceStep("b.json"), kb_macro("b", 1))]
    eng = SequenceEngine(steps, loop_count=INFINITE, callbacks=cb)
    eng.start()
    time.sleep(0.15)  # engine is inside the 30s step wait
    t = time.perf_counter()
    eng.abort()
    eng.join(timeout=2)
    assert time.perf_counter() - t < 1.0
    assert done["ab"] is True


def test_cursor_anchor_shared_across_steps_and_passes(monkeypatch):
    """Every mouse-run after the first restores the chain-wide anchor so
    relative deltas can't compound across steps or passes."""
    calls = []
    stub_output(monkeypatch, cursor_calls=calls)
    steps = [
        (SequenceStep("m.json", runs=2, wait=0.005), mouse_macro()),
        (SequenceStep("k.json"), kb_macro("a", 1)),  # kb-only: no restore
    ]
    run_engine(steps, loop_count=2, loop_delay=0.005)
    # 4 mouse runs total; first captures, the other 3 restore
    assert calls == [(500, 400)] * 3


def test_kb_only_chain_never_touches_cursor(monkeypatch):
    calls = []
    stub_output(monkeypatch, cursor_calls=calls)
    steps = [(SequenceStep("a.json", runs=2), kb_macro("a", 2)),
             (SequenceStep("b.json"), kb_macro("b", 1))]
    run_engine(steps, loop_count=2, loop_delay=0.005)
    assert calls == []
