"""Deck controller (recordings/sequences list, caches, rename/delete,
overlay targets) - extracted verbatim from ui.main_window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QListWidgetItem

from core.events import MacroFile
from core.sequence import Sequence

from ui import main_window as _mw

from ..dialogs import alert, confirm
from ..widgets.duration_picker import format_duration
from ..widgets.recording_list import RecordingRow, SequenceRow
from ..widgets.sequence_builder import (
    SequenceBuilder,
    recording_duration,
)

ROW_H = 48         # deck card height (name + metadata line)


class DeckMixin:
    """Deck methods mixed into MainWindow (plain class, no Qt
    base): self.* attributes come from MainWindow.__init__."""

    # ------------------------------------------------------ cache persistence
    def _deck_cache_path(self) -> Path:
        from core.config import CONFIG_DIR
        return CONFIG_DIR / "deck_cache.json"

    def _load_deck_cache(self) -> None:
        """Prime the mtime+size caches from the previous session so the
        first deck refresh only parses new/changed takes. Entries whose
        key no longer matches the file are ignored by the normal cache
        checks, so stale data can never be shown."""
        import json
        try:
            raw = json.loads(
                self._deck_cache_path().read_text(encoding="utf-8"))
            for name, (key, d) in raw.get("info", {}).items():
                self._info_cache[name] = (
                    tuple(key), (d[0], d[1], tuple(d[2]), d[3]))
            for name, (key, dur) in raw.get("dur", {}).items():
                self._dur_cache[name] = (tuple(key), dur)
        except (OSError, ValueError, TypeError, IndexError, KeyError):
            pass  # cold start: caches simply warm up by parsing

    def _save_deck_cache(self) -> None:
        import json
        try:
            path = self._deck_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "info": self._info_cache, "dur": self._dur_cache,
            }), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            pass  # cache is an optimization, never worth an error

    # ---------------------------------------------------------------- deck
    def _apply_deck_mode(self, mode: str) -> None:
        """The ONE place that mirrors deck mode into the chrome (tabs,
        new-sequence button, repeat/pass label) — every mode switch goes
        through here so the three can never drift apart."""
        self._deck_mode = mode
        self.deck_rec_tab.setChecked(mode == "rec")
        self.deck_seq_tab.setChecked(mode == "seq")
        self.new_seq_btn.setVisible(mode == "seq")
        # Same field, honest name: between runs vs between chain passes
        self._repeat_delay_label.setText(
            "Pass delay" if mode == "seq" else "Repeat delay")

    def _set_deck_mode(self, mode: str) -> None:
        if mode == self._deck_mode:
            # Clicking the active tab: just re-assert the checked state
            # (the click toggled it off) — no rebuild, no flicker
            self.deck_rec_tab.setChecked(mode == "rec")
            self.deck_seq_tab.setChecked(mode == "seq")
            return
        self._apply_deck_mode(mode)
        self._refresh_deck()
        self._update_playback_plan()

    def _refresh_deck(self, select: str | None = None) -> None:
        if self._deck_mode == "seq":
            self._refresh_sequences(select)
        else:
            self._refresh_recordings(select)

    def _select_deck_row(self, select: str | None) -> None:
        if select:
            for i in range(self.rec_list.count()):
                if self.rec_list.item(i).data(
                        Qt.ItemDataRole.UserRole) == select:
                    self.rec_list.setCurrentRow(i)
                    break
        elif self.rec_list.count():
            self.rec_list.setCurrentRow(self.rec_list.count() - 1)
        self._update_recording_info()
        overlay = getattr(self, "overlay", None)
        if overlay is not None and overlay.isVisible():
            item = self.rec_list.currentItem()
            if item is not None:
                overlay.set_current_target(
                    self._deck_mode, item.data(Qt.ItemDataRole.UserRole))

    def _sync_overlay_targets(self) -> None:
        items = []
        for p in sorted(_mw.RECORDINGS_DIR.glob("*.json")):
            details = self._recording_details(p.name)
            items.append(("rec", p.name, details[2], details[3] or None))
        for p in sorted(_mw.SEQUENCES_DIR.glob("*.json")):
            try:
                est, missing = self._sequence_estimate(Sequence.load(p))
            except (OSError, ValueError):
                est, missing = None, ["?"]
            items.append(("seq", p.name, ("seq",),
                          None if missing else est))
        cur = self.rec_list.currentItem()
        current = ((self._deck_mode, cur.data(Qt.ItemDataRole.UserRole))
                   if cur is not None else None)
        self.overlay.set_targets(items, current, self.loop_delay.value())

    def _select_target(self, kind: str, name: str) -> None:
        """Overlay picked what to play: mirror it into the deck so the
        normal Play path runs exactly that target."""
        if self._deck_mode != kind:
            self._apply_deck_mode(kind)
            self._refresh_deck(select=name)
            self._update_playback_plan()
        else:
            self._select_deck_row(name)

    def _refresh_recordings(self, select: str | None = None) -> None:
        if self._deck_mode != "rec":
            return  # a new take lands on disk; visible on next tab switch
        _mw.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.rec_list.setUpdatesEnabled(False)
        try:
            self.rec_list.clear()
            for path in sorted(_mw.RECORDINGS_DIR.glob("*.json")):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, path.name)
                item.setSizeHint(QSize(10, ROW_H))
                info, meta, kinds, _dur = self._recording_details(path.name)
                row = RecordingRow(path.name, self.theme, meta, kinds)
                row.setToolTip(info)
                row.rename_requested.connect(self._rename_recording)
                row.delete_requested.connect(self._delete_recording)
                self.rec_list.addItem(item)
                self.rec_list.setItemWidget(item, row)
            self._sync_row_widths()
            self._select_deck_row(select)
        finally:
            self.rec_list.setUpdatesEnabled(True)

    def _refresh_sequences(self, select: str | None = None) -> None:
        if self._deck_mode != "seq":
            return
        _mw.SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)
        self.rec_list.setUpdatesEnabled(False)
        try:
            self.rec_list.clear()
            for path in sorted(_mw.SEQUENCES_DIR.glob("*.json")):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, path.name)
                item.setSizeHint(QSize(10, ROW_H))
                meta = self._sequence_meta(path)
                row = SequenceRow(path.name, self.theme, meta)
                row.setToolTip(meta)
                row.edit_requested.connect(self._open_builder)
                row.rename_requested.connect(self._rename_sequence)
                row.delete_requested.connect(self._delete_sequence)
                self.rec_list.addItem(item)
                self.rec_list.setItemWidget(item, row)
            self._sync_row_widths()
            self._select_deck_row(select)
        finally:
            self.rec_list.setUpdatesEnabled(True)

    def _sequence_meta(self, path: Path) -> str:
        try:
            seq = Sequence.load(path)
        except (OSError, ValueError):
            return "unreadable"
        est, missing = self._sequence_estimate(seq)
        if missing:
            return f"{len(seq.steps)} steps · ⚠ missing step"
        return f"{len(seq.steps)} steps · ≈ {format_duration(est)}"

    def _update_recording_info(self, *_) -> None:
        item = self.rec_list.currentItem()
        if item is None:
            self.rec_info.setText("")
            self._seq_pass_est = None
            self._update_playback_plan()
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if self._deck_mode == "seq":
            self._update_sequence_info(name)
            return
        details = self._recording_details(name)
        self.rec_info.setText(details[0])
        self._rec_dur = details[3] or None
        self._update_playback_plan()

    def _cache_key(self, path: Path) -> tuple | None:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _recording_details(self, name: str) -> tuple[str, str, str]:
        """(info line, card meta, badge glyph) — parsed once per file
        version. Arrow-key navigation must never re-read multi-MB takes."""
        path = _mw.RECORDINGS_DIR / name
        key = self._cache_key(path)
        cached = self._info_cache.get(name)
        if cached is not None and cached[0] == key and key is not None:
            return cached[1]
        details = self._load_macro_info(path)
        self._info_cache[name] = (key, details)
        return details

    def _recording_info_text(self, name: str) -> str:
        return self._recording_details(name)[0]

    def _load_macro_info(self, path: Path) -> tuple[str, str, tuple, float]:
        try:
            macro = MacroFile.load(path)
        except (OSError, ValueError) as e:
            return (f"Unreadable: {e}", "unreadable", ("broken",), 0.0)
        counts = macro.counts_by_source()
        kb = counts.get("kb", 0)
        touch = counts.get("touch", 0)
        mouse = sum(v for k, v in counts.items() if k.startswith("mouse"))
        pad = sum(v for k, v in counts.items() if k.startswith("pad"))
        # Zero-count devices are noise — list only what the take uses,
        # with full names
        detail = " · ".join(
            f"{label} {count}" for count, label in
            ((kb, "keyboard"), (mouse, "mouse"),
             (pad, "controller"), (touch, "touch")) if count)
        info = (f"{macro.duration:.1f}s · {len(macro.events)} events"
                + (f" — {detail}" if detail else ""))
        meta = (f"{format_duration(macro.duration)} · "
                f"{len(macro.events)} events")
        # Badge = every device the take uses, busiest first (top 3)
        ranked = sorted(((pad, "pad"), (touch, "touch"),
                         (mouse, "mouse"), (kb, "kb")), reverse=True)
        kinds = tuple(k for c, k in ranked if c > 0)[:3]
        return (info, meta, kinds or ("rec",), macro.duration)

    def _rename_recording(self, old: str, new: str) -> None:
        new = self._do_rename(_mw.RECORDINGS_DIR, old, new,
                              self._refresh_recordings)
        if new is not None:
            self._retarget_sequences(old, new)

    def _delete_recording(self, name: str) -> None:
        users = self._sequences_using(name)
        extra = (f"\n\nUsed by sequence(s): {', '.join(users)} — "
                 "they'll fail until you edit them." if users else "")
        if confirm(self, "Delete recording", f"Delete {name}?{extra}",
                   yes_text="Delete"):
            (_mw.RECORDINGS_DIR / name).unlink(missing_ok=True)
            self._refresh_recordings()

    # ------------------------------------------------------------ sequences
    def _update_sequence_info(self, name: str) -> None:
        try:
            seq = Sequence.load(_mw.SEQUENCES_DIR / name)
        except (OSError, ValueError) as e:
            self.rec_info.setText(f"Unreadable: {e}")
            return
        est, missing = self._sequence_estimate(seq)
        text = (f"{len(seq.steps)} step(s) · one pass ≈ "
                f"{format_duration(est)}")
        if missing:
            text += f" · ⚠ missing: {', '.join(missing)}"
            est = None  # estimate is a lie with steps missing
        self.rec_info.setText(text)
        self._seq_pass_est = est
        self._update_playback_plan()

    def _sequence_estimate(self, seq: Sequence) -> tuple[float, list[str]]:
        """(one-pass duration, missing recordings) — durations served
        from the mtime cache so estimates never re-parse takes."""
        durations: dict[str, float] = {}
        missing: list[str] = []
        for s in seq.steps:
            if s.recording in durations or s.recording in missing:
                continue
            path = _mw.RECORDINGS_DIR / s.recording
            key = self._cache_key(path)
            if key is None:
                missing.append(s.recording)
                continue
            cached = self._dur_cache.get(s.recording)
            if cached is not None and cached[0] == key:
                durations[s.recording] = cached[1]
            else:
                d = recording_duration(path)
                self._dur_cache[s.recording] = (key, d)
                durations[s.recording] = d
        return seq.pass_duration(durations), missing

    def _open_builder(self, existing: str | None = None) -> None:
        # Non-modal: the main window (and its Record button) stays usable
        # so the builder can record new steps inline.
        if getattr(self, "_builder", None) is not None:
            self._builder.raise_()
            self._builder.activateWindow()
            return
        dlg = SequenceBuilder(self.theme, self, existing=existing)

        def on_saved(name: str) -> None:
            if self._deck_mode != "seq":
                self._apply_deck_mode("seq")
            self._refresh_sequences(select=name)
            self._log(f"Sequence saved → {name}", self.theme.success)

        def on_closed(_result: int) -> None:
            self._builder = None

        dlg.saved.connect(on_saved)
        dlg.finished.connect(on_closed)
        self._builder = dlg
        dlg.show()

    def _rename_sequence(self, old: str, new: str) -> None:
        self._do_rename(_mw.SEQUENCES_DIR, old, new, self._refresh_sequences)

    def _delete_sequence(self, name: str) -> None:
        if confirm(self, "Delete sequence",
                   f"Delete {name}?\n(Its recordings stay untouched.)",
                   yes_text="Delete"):
            (_mw.SEQUENCES_DIR / name).unlink(missing_ok=True)
            self._refresh_sequences()

    def _do_rename(self, folder: Path, old: str, new: str,
                   refresh) -> str | None:
        """Shared rename for both decks; returns the final name or None."""
        for ch in '\\/:*?"<>|':
            new = new.replace(ch, "_")
        new = new.strip()
        if not new:
            refresh(select=old)
            return None
        if not new.lower().endswith(".json"):
            new += ".json"
        src, dst = folder / old, folder / new
        if dst.exists():
            alert(self, "Name taken", f"{new} already exists.")
            refresh(select=old)
            return None
        try:
            src.rename(dst)
        except OSError as e:
            alert(self, "Rename failed", str(e))
            refresh(select=old)
            return None
        refresh(select=new)
        return new

    def _sequences_using(self, recording: str) -> list[str]:
        names = []
        for spath in _mw.SEQUENCES_DIR.glob("*.json"):
            try:
                seq = Sequence.load(spath)
            except (OSError, ValueError):
                continue
            if any(s.recording == recording for s in seq.steps):
                names.append(spath.name)
        return names

    def _retarget_sequences(self, old: str, new: str) -> None:
        """Renaming a recording silently updates every sequence that
        references it — chains never break from a rename."""
        for spath in _mw.SEQUENCES_DIR.glob("*.json"):
            try:
                seq = Sequence.load(spath)
            except (OSError, ValueError):
                continue
            hit = False
            for s in seq.steps:
                if s.recording == old:
                    s.recording = new
                    hit = True
            if hit:
                seq.save(spath)
