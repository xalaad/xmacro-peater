from core.events import MacroEvent
from core.hotkeys import (
    combo_label,
    combo_reps,
    parse_combo,
    trim_hotkey_artifacts,
)


def kb(t, action, key):
    return MacroEvent(t, "kb", {"action": action, "key": key})


def test_parse_combo():
    assert parse_combo("ctrl+f9") == frozenset({"ctrl", "f9"})
    assert parse_combo("Ctrl + F9") == frozenset({"ctrl", "f9"})
    assert parse_combo("esc") == frozenset({"esc"})
    assert parse_combo("shift_r+a") == frozenset({"shift", "a"})


def test_combo_reps_covers_modifier_variants():
    reps = combo_reps("ctrl+f9")
    assert "key:ctrl_l" in reps and "key:ctrl_r" in reps and "key:f9" in reps
    assert combo_reps("ctrl+r") >= {"char:r"}


def test_combo_label():
    assert combo_label("ctrl+f9") == "Ctrl+F9"
    assert combo_label("esc") == "Esc"


def test_trim_removes_start_combo_release_and_stop_combo_press():
    reps = combo_reps("ctrl+f9")
    events = [
        kb(0.01, "up", "key:ctrl_l"),     # releasing the start combo
        kb(0.02, "up", "key:f9"),
        kb(0.50, "down", "char:w"),       # actual macro content
        kb(0.60, "up", "char:w"),
        kb(1.00, "down", "key:ctrl_l"),   # pressing the stop combo
    ]
    out = trim_hotkey_artifacts(events, reps)
    assert [e.data["key"] for e in out] == ["char:w", "char:w"]


def test_trim_keeps_mid_recording_ctrl_use():
    reps = combo_reps("ctrl+f9")
    events = [
        kb(0.01, "up", "key:ctrl_l"),
        kb(0.50, "down", "key:ctrl_l"),   # ctrl used inside the macro
        kb(0.55, "down", "char:c"),
        kb(0.60, "up", "char:c"),
        kb(0.65, "up", "key:ctrl_l"),
        kb(0.90, "down", "char:x"),       # non-hotkey tail protects it
    ]
    out = trim_hotkey_artifacts(events, reps)
    keys = [e.data["key"] for e in out]
    assert keys == ["key:ctrl_l", "char:c", "char:c", "key:ctrl_l", "char:x"]


def test_trim_handles_all_hotkey_recording():
    reps = combo_reps("ctrl+f9")
    events = [kb(0.01, "up", "key:ctrl_l"), kb(0.02, "down", "key:ctrl_r")]
    assert trim_hotkey_artifacts(events, reps) == []
