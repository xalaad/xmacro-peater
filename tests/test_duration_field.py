"""Smart duration field: text parsing, display formatting, and the
widget's typed-input / popup behavior (offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from PySide6.QtGui import QValidator  # noqa: E402

from ui.widgets.duration_picker import (  # noqa: E402
    DurationPicker,
    DurationValidator,
    format_value,
    parse_duration,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("text,expected", [
    ("90", 90.0),
    ("2.5", 2.5),
    ("0", 0.0),
    ("1h 30m", 5400.0),
    ("2h", 7200.0),
    ("90m", 5400.0),
    ("45s", 45.0),
    ("1h30m5s", 5405.0),
    ("1 hour 30 minutes", 5400.0),
    ("2 hrs", 7200.0),
    ("1m30", 90.0),          # trailing bare number = seconds
    ("1:30:05", 5405.0),
    ("1:30", 90.0),
    ("2,5", 2.5),            # comma decimal
    ("  1H 30M  ", 5400.0),  # case/space insensitive
])
def test_parse_valid(text, expected):
    assert parse_duration(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [
    "", "abc", "1x", "h", "1:2:3:4", "1:xx", "12days",
])
def test_parse_invalid(text):
    assert parse_duration(text) is None


def test_parse_clamps_negative():
    assert parse_duration("-5") == 0.0


# ------------------------------------------------------------- formatting
@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (2.5, "2.5s"),
    (90, "1m 30s"),
    (5400, "1h 30m"),
    (5405, "1h 30m 5s"),
    (59.999, "1m"),  # rounding carry
])
def test_format_value(seconds, expected):
    assert format_value(seconds) == expected


# -------------------------------------------------------------- validator
def test_validator_blocks_garbage_keystrokes(app):
    v = DurationValidator()
    for bad in ("abc", "1x", "!", "1h30m?"):
        assert v.validate(bad, len(bad))[0] == QValidator.State.Invalid


def test_validator_accepts_and_flows(app):
    v = DurationValidator()
    assert v.validate("1h 30m", 6)[0] == QValidator.State.Acceptable
    assert v.validate("90", 2)[0] == QValidator.State.Acceptable
    assert v.validate("1h ", 3)[0] == QValidator.State.Acceptable
    # In-progress typing stays Intermediate, never rejected
    for partial in ("1:", ".", ""):
        assert v.validate(partial, len(partial))[0] \
            == QValidator.State.Intermediate


def test_validator_space_discipline(app):
    """No leading/double spaces, and no space after a trailing 's' —
    stray Space presses can't pile junk into the field."""
    v = DurationValidator()
    for bad in (" 3", "1h  30m", "3s ", "1m 30s ", " "):
        assert v.validate(bad, len(bad))[0] == QValidator.State.Invalid
    assert v.validate("1h ", 3)[0] == QValidator.State.Acceptable
    assert v.validate("30m ", 4)[0] == QValidator.State.Acceptable


# ----------------------------------------------------------------- widget
def test_set_value_updates_field_and_emits(app):
    p = DurationPicker()
    got = []
    p.valueChanged.connect(got.append)
    p.setValue(5400)
    assert p.value() == 5400
    assert p.field.text() == "1h 30m"
    assert got == [5400]


def test_typed_text_commits(app):
    p = DurationPicker()
    p.setValue(3)
    p.field.setText("2h 15m")
    p._commit_text()
    assert p.value() == pytest.approx(8100.0)
    assert p.field.text() == "2h 15m"


def test_invalid_text_reverts(app):
    p = DurationPicker()
    p.setValue(42)
    p.field.setText("nonsense")
    p._commit_text()
    assert p.value() == 42
    assert p.field.text() == "42s"


def test_value_clamped_to_24h(app):
    p = DurationPicker()
    p.setValue(999999)
    assert p.value() == 86400.0


def test_nudge_arrows(app):
    p = DurationPicker()
    p.setValue(5)
    p.field.nudged.emit(1.0)
    assert p.value() == 6
    p.field.nudged.emit(-1.0)
    assert p.value() == 5


def test_popup_live_applies(app):
    p = DurationPicker()
    p.setValue(0)
    pop = p._popup
    pop.h.setValue(1)
    pop.m.setValue(30)
    assert p.value() == pytest.approx(5400.0)
    assert p.field.text() == "1h 30m"
    assert "1h 30m" in pop.preview.text()
    assert "~" in pop.preview.text()  # clock preview for >= 1m


def test_commit_and_done_release_focus(app):
    p = DurationPicker()
    p.field.setText("90")
    p._commit_text()
    assert not p.field.hasFocus()
    p._popup._done()
    assert not p.field.hasFocus()


def test_blocked_signals_do_not_emit(app):
    p = DurationPicker()
    got = []
    p.valueChanged.connect(got.append)
    p.blockSignals(True)
    p.setValue(9)
    p.blockSignals(False)
    assert got == []
    assert p.value() == 9
