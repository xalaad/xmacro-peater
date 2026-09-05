"""Offscreen UI tests for the sequences deck: tab switching, builder
save, list rows, info line, and play routing."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.main_window as mw_mod  # noqa: E402
import ui.widgets.sequence_builder as sb_mod  # noqa: E402
from core.config import AppConfig  # noqa: E402
from core.events import MacroEvent, MacroFile  # noqa: E402
from core.sequence import Sequence, SequenceStep  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    rec = tmp_path_factory.mktemp("recordings")
    seq = tmp_path_factory.mktemp("sequences")
    MacroFile(events=[
        MacroEvent(0.0, "kb", {"action": "down", "key": "char:a"}),
        MacroEvent(2.0, "kb", {"action": "up", "key": "char:a"}),
    ]).save(rec / "alpha.json")
    MacroFile(events=[
        MacroEvent(0.0, "kb", {"action": "down", "key": "char:b"}),
        MacroEvent(3.0, "kb", {"action": "up", "key": "char:b"}),
    ]).save(rec / "beta.json")
    return rec, seq


@pytest.fixture(scope="module")
def window(app, dirs, tmp_path_factory):
    rec, seq = dirs
    mw_mod.RECORDINGS_DIR, mw_orig_r = rec, mw_mod.RECORDINGS_DIR
    mw_mod.SEQUENCES_DIR, mw_orig_s = seq, mw_mod.SEQUENCES_DIR
    sb_mod.RECORDINGS_DIR, sb_orig_r = rec, sb_mod.RECORDINGS_DIR
    sb_mod.SEQUENCES_DIR, sb_orig_s = seq, sb_mod.SEQUENCES_DIR
    mw_mod.save_config, orig_save = (lambda *a, **k: None), mw_mod.save_config
    win = mw_mod.MainWindow(AppConfig())
    yield win
    win.close()
    mw_mod.RECORDINGS_DIR, mw_mod.SEQUENCES_DIR = mw_orig_r, mw_orig_s
    sb_mod.RECORDINGS_DIR, sb_mod.SEQUENCES_DIR = sb_orig_r, sb_orig_s
    mw_mod.save_config = orig_save


def test_deck_starts_on_recordings(window):
    assert window._deck_mode == "rec"
    assert window.deck_rec_tab.isChecked()
    assert not window.deck_seq_tab.isChecked()
    assert window.rec_list.count() == 2  # alpha + beta


def test_switch_to_sequences_tab(window, dirs):
    _, seq_dir = dirs
    Sequence(steps=[
        SequenceStep("alpha.json", runs=2, wait=1.0),
        SequenceStep("beta.json"),
    ]).save(seq_dir / "combo.json")
    window._set_deck_mode("seq")
    assert window.deck_seq_tab.isChecked()
    assert not window.deck_rec_tab.isChecked()
    assert window.rec_list.count() == 1
    item = window.rec_list.item(0)
    assert item.data(0x0100) == "combo.json"  # Qt.UserRole


def test_sequence_info_line(window):
    window._set_deck_mode("seq")
    window.rec_list.setCurrentRow(0)
    window._update_recording_info()
    # alpha 2s ×2 + wait 1s ×2 + beta 3s (trailing wait none) = 9s
    assert "2 step(s)" in window.rec_info.text()
    assert "9s" in window.rec_info.text()


def test_info_flags_missing_recordings(window, dirs):
    _, seq_dir = dirs
    Sequence(steps=[SequenceStep("ghost.json")]).save(
        seq_dir / "broken.json")
    window._set_deck_mode("seq")
    window._refresh_sequences(select="broken.json")
    assert "missing: ghost.json" in window.rec_info.text()
    (seq_dir / "broken.json").unlink()
    window._refresh_sequences()


def test_seq_mode_relabels_delay_and_plan(window):
    window._set_deck_mode("seq")
    window._refresh_sequences(select="combo.json")
    assert window._repeat_delay_label.text() == "Pass delay"
    assert "pass" in window.plan_label.text()
    window.loop_mode.setCurrentIndex(1)  # N times
    window.loop_count.setValue(3)
    assert "3 passes" in window.plan_label.text()
    assert "total" in window.plan_label.text()  # estimate present
    window.loop_mode.setCurrentIndex(0)
    window._set_deck_mode("rec")
    assert window._repeat_delay_label.text() == "Repeat delay"
    assert "pass" not in window.plan_label.text()


def test_play_routes_by_deck_mode(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "start_sequence",
                        lambda: calls.append("seq"))
    window._set_deck_mode("seq")
    window.start_playback()
    assert calls == ["seq"]


def test_recordings_refresh_ignored_while_on_seq_tab(window):
    window._set_deck_mode("seq")
    before = window.rec_list.count()
    window._refresh_recordings()  # e.g. a recording just saved
    assert window.rec_list.count() == before  # list still shows sequences
    assert window._deck_mode == "seq"


def test_builder_saves_sequence(window, app, dirs):
    _, seq_dir = dirs
    dlg = sb_mod.SequenceBuilder(window.theme, window)
    assert len(dlg._rows()) == 1  # starts with one step
    dlg._add_row()
    rows = dlg._rows()
    # Combos display clean stems; the real .json names ride in item data
    rows[0].combo.setCurrentIndex(rows[0].combo.findData("alpha.json"))
    rows[0].runs.setValue(3)
    rows[1].combo.setCurrentIndex(rows[1].combo.findData("beta.json"))
    dlg.name_edit.setText("built")
    dlg._save()
    saved = Sequence.load(seq_dir / "built.json")
    assert [s.recording for s in saved.steps] == ["alpha.json", "beta.json"]
    assert saved.steps[0].runs == 3
    (seq_dir / "built.json").unlink()


def test_builder_summary_estimates_pass(window, app):
    dlg = sb_mod.SequenceBuilder(window.theme, window)
    row = dlg._rows()[0]
    row.combo.setCurrentIndex(row.combo.findData("alpha.json"))
    row.runs.setValue(2)
    row.wait.setValue(1.0)
    # 2 runs × 2s + one inter-run wait (trailing skipped) = 5s
    assert "5s" in dlg.summary.text()


def test_builder_records_step_inline(window, app, dirs, monkeypatch):
    """● Record step: builder hides, records via the host window, and
    appends the fresh take as a step when it lands."""
    rec_dir, _ = dirs
    MacroFile(events=[
        MacroEvent(0.0, "kb", {"action": "down", "key": "char:z"}),
    ]).save(rec_dir / "zzz.json")
    dlg = sb_mod.SequenceBuilder(window.theme, window)
    n_before = len(dlg._rows())
    monkeypatch.setattr(
        window, "toggle_record",
        lambda temp=False: window.recording_finished.emit("zzz.json"))
    dlg._record_step()
    rows = dlg._rows()
    assert len(rows) == n_before + 1
    assert rows[-1].combo.currentData() == "zzz.json"
    assert rows[-1].combo.currentText() == "zzz"  # stem shown, no .json
    assert rows[0].combo.findData("zzz.json") >= 0  # combos updated too
    assert dlg.isVisible()  # reshown after the take landed
    dlg.reject()
    (rec_dir / "zzz.json").unlink()


def test_empty_take_appends_nothing(window, app, monkeypatch):
    dlg = sb_mod.SequenceBuilder(window.theme, window)
    n_before = len(dlg._rows())
    monkeypatch.setattr(
        window, "toggle_record",
        lambda temp=False: window.recording_finished.emit(""))
    dlg._record_step()
    assert len(dlg._rows()) == n_before
    assert dlg.isVisible()
    dlg.reject()


def test_open_builder_is_nonmodal_and_single(window):
    window._open_builder(None)
    first = window._builder
    assert first is not None and not first.isModal()
    window._open_builder(None)  # second call focuses, doesn't duplicate
    assert window._builder is first
    first.reject()
    assert window._builder is None


def test_step_rows_reorder_with_drag_machinery(window, app):
    dlg = sb_mod.SequenceBuilder(window.theme, window)
    dlg._add_row()
    dlg._add_row()
    first = dlg._rows()[0]
    dlg._begin_drag(first)
    assert first.property("dragging") is True
    dlg._move_row_to(first, 2)
    assert dlg._rows()[2] is first
    dlg._end_drag(first)
    assert first.property("dragging") is False
    dlg._move_row_to(first, 0)
    assert dlg._rows()[0] is first
    dlg._move_row_to(first, 99)  # clamped to the end
    assert dlg._rows()[-1] is first
    dlg.reject()


def test_recording_info_is_cached(window, monkeypatch):
    window._set_deck_mode("rec")
    calls = []
    orig = window._load_macro_info
    monkeypatch.setattr(
        window, "_load_macro_info",
        lambda p: (calls.append(p), orig(p))[1])
    window._info_cache.clear()
    window.rec_list.setCurrentRow(0)
    window.rec_list.setCurrentRow(1)
    window.rec_list.setCurrentRow(0)  # unchanged file: served from cache
    assert len(calls) == 2


def test_overlay_target_picker_routes(window, dirs):
    _, seq_dir = dirs
    Sequence(steps=[SequenceStep("alpha.json")]).save(
        seq_dir / "pick_me.json")
    window._set_deck_mode("rec")
    window._sync_overlay_targets()
    ov = window.overlay
    assert ov.target.count() >= 3  # alpha + beta + pick_me sequence
    assert ov.target.itemText(0) == "SEQUENCES"  # header row on top
    idx = ov._find_target("seq", "pick_me.json")
    assert idx >= 0
    assert ov.target.itemText(idx).startswith("pick_me")
    assert "2s" in ov.target.itemText(idx)  # pass duration shown inline
    rec_idx = ov._find_target("rec", "alpha.json")
    assert rec_idx > idx  # recordings grouped below sequences
    ov.target.setCurrentIndex(idx)  # user picks it from the overlay
    assert window._deck_mode == "seq"
    item = window.rec_list.currentItem()
    assert item.data(0x0100) == "pick_me.json"
    window._set_deck_mode("rec")
    (seq_dir / "pick_me.json").unlink()


def test_rename_recording_retargets_sequences(window, dirs):
    rec_dir, seq_dir = dirs
    window._set_deck_mode("rec")
    window._rename_recording("beta.json", "gamma.json")
    assert (rec_dir / "gamma.json").exists()
    seq = Sequence.load(seq_dir / "combo.json")
    assert [s.recording for s in seq.steps] == ["alpha.json", "gamma.json"]
    # restore for other tests (module-scoped fixture)
    window._rename_recording("gamma.json", "beta.json")
    window._set_deck_mode("rec")
