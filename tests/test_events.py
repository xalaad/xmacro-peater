import json

import pytest

from core.events import MacroEvent, MacroFile


def make_events():
    return [
        MacroEvent(0.0, "kb", {"action": "down", "key": "char:a"}),
        MacroEvent(0.1, "kb", {"action": "up", "key": "char:a"}),
        MacroEvent(0.2, "mouse_move", {"dx": 5, "dy": -3}),
        MacroEvent(0.3, "pad_btn", {"button": "A", "action": "down"}),
        MacroEvent(0.4, "pad_axis", {"stick": "left", "x": 0.5, "y": -0.5}),
        MacroEvent(0.5, "pad_trigger", {"trigger": "right", "value": 0.75}),
    ]


def test_round_trip(tmp_path):
    path = tmp_path / "macro.json"
    mf = MacroFile(events=make_events(), poll_hz=125)
    mf.save(path)
    loaded = MacroFile.load(path)
    assert len(loaded.events) == 6
    assert loaded.poll_hz == 125
    assert loaded.events[3].data["button"] == "A"
    assert loaded.duration == pytest.approx(0.5)
    assert loaded.has_pad_events


def test_v1_bare_list_loads(tmp_path):
    """Files from the old CLI tool (a bare JSON list) must still load."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps([
        {"t": 0.0, "src": "kb", "action": "down", "key": "key:space"},
        {"t": 1.0, "src": "pad_btn", "button": "B", "action": "down"},
    ]))
    mf = MacroFile.load(path)
    assert len(mf.events) == 2
    assert mf.has_pad_events


def test_events_sorted_on_load(tmp_path):
    path = tmp_path / "macro.json"
    mf = MacroFile(events=list(reversed(make_events())))
    mf.save(path)
    loaded = MacroFile.load(path)
    times = [e.t for e in loaded.events]
    assert times == sorted(times)


def test_unknown_source_rejected():
    with pytest.raises(ValueError):
        MacroEvent.from_dict({"t": 0, "src": "nonsense"})


def test_counts_by_source():
    mf = MacroFile(events=make_events())
    counts = mf.counts_by_source()
    assert counts["kb"] == 2
    assert counts["pad_btn"] == 1
