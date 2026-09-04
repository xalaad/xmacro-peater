from pathlib import Path

from core.config import SCHEMES_DIR
from core.controllers.factory import list_schemes, scheme_available
from core.controllers.pygame_backend import Scheme

CANONICAL = {
    "A", "B", "X", "Y", "LEFT_SHOULDER", "RIGHT_SHOULDER", "BACK", "START",
    "LEFT_THUMB", "RIGHT_THUMB", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT",
    "DPAD_RIGHT",
}


def test_shipped_schemes_load():
    schemes = list_schemes(SCHEMES_DIR)
    assert {"xbox", "playstation", "generic"} <= set(schemes)


def test_scheme_button_names_are_canonical():
    for scheme in list_schemes(SCHEMES_DIR).values():
        for name in scheme.buttons.values():
            assert name in CANONICAL, f"{scheme.name}: bad button {name!r}"


def test_xbox_scheme_is_always_available():
    schemes = list_schemes(SCHEMES_DIR)
    ok, why = scheme_available(schemes["xbox"])
    assert ok, why


def test_bad_scheme_file_skipped(tmp_path: Path):
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "good.json").write_text('{"name": "ok", "backend": "xinput"}')
    schemes = list_schemes(tmp_path)
    assert set(schemes) == {"good"}


def test_scheme_defaults():
    s = Scheme({"name": "min"})
    assert s.invert_y is True
    assert s.buttons == {}
    assert s.backend == "pygame"
