"""Sequence builder: a themed dialog that composes recordings into an
ordered chain — per step: which recording, how many runs, and the wait
after each run. Live one-pass duration estimate; drag the grip to
reorder steps with the mouse.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config import RECORDINGS_DIR, SEQUENCES_DIR
from core.sequence import MAX_STEP_RUNS, Sequence, SequenceStep

from ..dialogs import FramelessDialog, alert
from ..theme import Theme
from .duration_picker import DurationPicker, format_duration

MAX_VISIBLE_STEPS = 6  # dialog grows up to this many rows, then scrolls


def recording_duration(path: Path) -> float:
    """Recording length without building the full event model."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return float(raw.get("duration", 0.0))
        if isinstance(raw, list) and raw:  # v1 bare list
            return float(raw[-1].get("t", 0.0))
    except (OSError, ValueError):
        pass
    return 0.0


class DragGrip(QLabel):
    """Mouse handle: drag a step row up/down to reorder it."""

    pressed = Signal(object)   # global QPoint
    moved = Signal(object)
    released = Signal()

    def __init__(self, parent=None):
        super().__init__("", parent)  # MDL2 grip lines
        self.setObjectName("dragGrip")
        self.setFixedSize(18, 24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.pressed.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.moved.emit(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.released.emit()


class StepRow(QWidget):
    """[grip] [recording combo] ×[runs] [wait picker] [remove]"""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, recordings: list[str], theme: Theme, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(6)
        self.setObjectName("stepRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.grip = DragGrip()
        lay.addWidget(self.grip)

        self.combo = QComboBox()
        for rec in recordings:  # show clean stems, keep real names in data
            self.combo.addItem(rec.removesuffix(".json"), rec)
        self.combo.currentIndexChanged.connect(
            lambda _: self.changed.emit())
        lay.addWidget(self.combo, 3)

        self.runs = QSpinBox()
        self.runs.setRange(1, MAX_STEP_RUNS)
        self.runs.setPrefix("× ")
        self.runs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.runs.setFixedWidth(64)
        self.runs.setToolTip("Runs of this step per pass")
        self.runs.valueChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(self.runs)

        self.wait = DurationPicker()
        self.wait.setToolTip(
            "Wait after each run of this step (before the next run or "
            "the next step)")
        self.wait.valueChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(self.wait, 2)

        remove = QPushButton("")  # MDL2 Cancel
        remove.setObjectName("rowBtn")
        remove.setFixedSize(24, 24)
        remove.setToolTip("Remove step")
        remove.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(
            lambda _=False: self.removed.emit(self))
        lay.addWidget(remove)

    def step(self) -> SequenceStep:
        return SequenceStep(
            recording=self.combo.currentData(),
            runs=self.runs.value(),
            wait=self.wait.value(),
        )

    def set_step(self, step: SequenceStep) -> None:
        idx = self.combo.findData(step.recording)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        self.runs.setValue(step.runs)
        self.wait.setValue(step.wait)


class SequenceBuilder(FramelessDialog):
    """Create or edit one sequence file. Emits saved(name) on success."""

    saved = Signal(str)

    def __init__(self, theme: Theme, parent=None,
                 existing: str | None = None):
        super().__init__("Sequence builder", parent)
        self.theme = theme
        self.existing = existing
        self.recordings = sorted(
            p.name for p in RECORDINGS_DIR.glob("*.json"))
        self._durations: dict[str, float] = {}
        self.setMinimumWidth(680)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("my_sequence")
        if existing:
            self.name_edit.setText(existing.removesuffix(".json"))
        name_row.addWidget(self.name_edit, 1)
        self.body.addLayout(name_row)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addSpacing(18 + 6)  # over the drag grips
        for text, stretch in (("RECORDING", 3), ("RUNS", 0),
                              ("WAIT AFTER EACH RUN", 2)):
            lbl = QLabel(text)
            lbl.setObjectName("dim")
            if text == "RUNS":
                lbl.setFixedWidth(64)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            elif text.startswith("WAIT"):
                lbl.setToolTip(
                    "This wait belongs to the step: it runs after each of "
                    "its runs, inside the pass. The LAST step's wait is "
                    "skipped — the main screen's Pass delay takes over "
                    "between passes, so the two never stack.")
            head.addWidget(lbl, stretch)
        head.addSpacing(24 + 6)  # over the remove button
        self.body.addLayout(head)

        # Steps live in a scroll area so long chains never outgrow the
        # screen — it expands with content up to MAX_VISIBLE_STEPS rows.
        steps_host = QWidget()
        self._steps_lay = QVBoxLayout(steps_host)
        self._steps_lay.setContentsMargins(0, 0, 0, 0)
        self._steps_lay.setSpacing(6)
        self._steps_lay.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(steps_host)
        self.body.addWidget(self._scroll, 1)

        add = QPushButton("  Add step")
        add.setObjectName("accentBtn")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add.clicked.connect(lambda: self._add_row(announce=True))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(add)
        self.record_btn = QPushButton("●  Record step")
        self.record_btn.setObjectName("recordStepBtn")
        self.record_btn.setToolTip(
            "Record a brand-new step right now: the builder hides, the "
            "countdown ring ticks down, recording starts — stop with "
            "Ctrl+F9 (or the main window's Record button) and the take "
            "is appended as a step.")
        self.record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.record_btn.clicked.connect(self._record_step)
        btn_row.addWidget(self.record_btn)
        btn_row.addStretch(1)
        self.body.addLayout(btn_row)

        self.summary = QLabel("")
        self.summary.setObjectName("statsLabel")
        self.summary.setWordWrap(True)
        self.body.addWidget(self.summary)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save sequence")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        self.body.addLayout(btns)

        if existing:
            try:
                seq = Sequence.load(SEQUENCES_DIR / existing)
                for step in seq.steps:
                    self._add_row(step)
            except (OSError, ValueError) as e:
                alert(self, "Can't load sequence", str(e))
        if not self._rows():
            self._add_row()
        self._update_summary()

    # ------------------------------------------------------------------
    def _rows(self) -> list[StepRow]:
        return [self._steps_lay.itemAt(i).widget()
                for i in range(self._steps_lay.count() - 1)]  # skip stretch

    def _add_row(self, step: SequenceStep | None = None,
                 announce: bool = False) -> None:
        if not self.recordings:
            if announce:
                alert(self, "No recordings",
                      "There are no recordings yet — use ● Record step "
                      "to capture your first one right here.")
            return
        row = StepRow(self.recordings, self.theme)
        row.changed.connect(self._update_summary)
        row.removed.connect(self._remove)
        row.grip.pressed.connect(lambda _g, r=row: self._begin_drag(r))
        row.grip.moved.connect(lambda g, r=row: self._drag_to(r, g))
        row.grip.released.connect(lambda r=row: self._end_drag(r))
        self._steps_lay.insertWidget(self._steps_lay.count() - 1, row)
        if step is not None:
            row.set_step(step)
        self._sync_height()
        self._update_summary()

    def _remove(self, row: StepRow) -> None:
        if len(self._rows()) <= 1:
            return  # a sequence needs at least one step
        self._steps_lay.removeWidget(row)
        row.deleteLater()
        self._sync_height()
        self._update_summary()

    # ------------------------------------------------------ drag reorder
    def _begin_drag(self, row: StepRow) -> None:
        row.setProperty("dragging", True)
        row.style().unpolish(row)
        row.style().polish(row)

    def _drag_to(self, row: StepRow, gpos) -> None:
        """Live reorder: the row follows the cursor across the list."""
        host = row.parentWidget()
        if host is None:
            return
        y = host.mapFromGlobal(gpos).y()
        rows = self._rows()
        target = len(rows) - 1
        for i, r in enumerate(rows):
            if y < r.geometry().center().y():
                target = i
                break
        self._move_row_to(row, target)

    def _move_row_to(self, row: StepRow, index: int) -> None:
        rows = self._rows()
        cur = rows.index(row)
        index = max(0, min(index, len(rows) - 1))
        if index != cur:
            self._steps_lay.removeWidget(row)
            self._steps_lay.insertWidget(index, row)

    def _end_drag(self, row: StepRow) -> None:
        row.setProperty("dragging", False)
        row.style().unpolish(row)
        row.style().polish(row)
        self._update_summary()

    def _sync_height(self) -> None:
        # Rows are ~38px (+6 spacing) — reserve exactly that, so the gap
        # between the last step and the buttons stays tight
        n = min(max(len(self._rows()), 1), MAX_VISIBLE_STEPS)
        self._scroll.setMinimumHeight(n * 44 + 2)

    def _duration(self, name: str) -> float:
        if name not in self._durations:
            self._durations[name] = recording_duration(RECORDINGS_DIR / name)
        return self._durations[name]

    def _update_summary(self, *_) -> None:
        rows = self._rows()
        if not rows:
            self.summary.setText(
                "No steps yet — ● Record step captures a new recording "
                "and adds it here.")
            return
        seq = Sequence(steps=[r.step() for r in rows])
        est = seq.pass_duration(
            {s.recording: self._duration(s.recording) for s in seq.steps})
        self.summary.setText(
            f"{len(rows)} step(s) · one pass ≈ "
            f"{format_duration(est)} — repeats & pass delay come from "
            "the main screen's loop controls")

    # ------------------------------------------------------- inline record
    def _record_step(self) -> None:
        """Hide the builder, record through the main window, and append
        the take as a new step when it lands."""
        host = self.parent()
        if not hasattr(host, "toggle_record"):
            return
        if host.is_busy():
            alert(self, "Busy",
                  "A recording or playback is already running.")
            return
        host.recording_finished.connect(self._on_step_recorded)
        self.hide()
        host.toggle_record()

    def _on_step_recorded(self, name: str) -> None:
        host = self.parent()
        host.recording_finished.disconnect(self._on_step_recorded)
        self.show()
        self.raise_()
        self.activateWindow()
        if not name or name == "test_take.json":
            return  # empty take — nothing to append
        if name not in self.recordings:
            self.recordings.append(name)
            self.recordings.sort()
            for row in self._rows():
                if row.combo.findData(name) < 0:
                    row.combo.addItem(name.removesuffix(".json"), name)
        self._add_row(SequenceStep(name))

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        for ch in '\\/:*?"<>|':
            name = name.replace(ch, "_")
        if not name:
            alert(self, "Name needed", "Give the sequence a name.")
            return
        if not name.lower().endswith(".json"):
            name += ".json"
        rows = self._rows()
        if not rows:
            alert(self, "Empty sequence", "Add at least one step.")
            return
        path = SEQUENCES_DIR / name
        if path.exists() and name != self.existing:
            alert(self, "Name taken", f"{name} already exists.")
            return
        Sequence(steps=[r.step() for r in rows]).save(path)
        if self.existing and name != self.existing:
            (SEQUENCES_DIR / self.existing).unlink(missing_ok=True)
        self.saved.emit(name)
        self.accept()
