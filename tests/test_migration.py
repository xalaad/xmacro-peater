"""Legacy-data migration must run exactly once: deleted recordings must
never 'come back' from the old %LOCALAPPDATA% stash."""
import sys

import pytest

import core.config as cfg_mod
from core.config import migrate_legacy_data


@pytest.fixture
def frozen_env(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    legacy = tmp_path / "localappdata" / "XMacro-peater"
    (legacy / "recordings").mkdir(parents=True)
    (legacy / "recordings" / "old_take.json").write_text("{}")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(cfg_mod, "APP_DIR", app_dir)
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", app_dir / "config")
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH",
                        app_dir / "config" / "app_config.json")
    monkeypatch.setattr(cfg_mod, "RECORDINGS_DIR", app_dir / "recordings")
    return app_dir


def test_migration_runs_once_deleted_files_stay_deleted(frozen_env):
    app_dir = frozen_env
    migrate_legacy_data()
    copied = app_dir / "recordings" / "old_take.json"
    assert copied.exists()          # first run: legacy data migrated
    assert (app_dir / "config" / ".legacy-migrated").exists()

    copied.unlink()                 # the user deletes the recording
    migrate_legacy_data()           # app restarts
    assert not copied.exists()      # it must NOT come back


def test_marker_written_even_without_legacy_data(frozen_env, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(frozen_env / "nope"))
    migrate_legacy_data()
    assert (frozen_env / "config" / ".legacy-migrated").exists()


def test_migration_noop_when_not_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path / "config")
    migrate_legacy_data()
    assert not (tmp_path / "config").exists()


def test_reinstall_does_not_remigrate(frozen_env, tmp_path, monkeypatch):
    """Regression: the marker next to the exe dies with every reinstall,
    so the legacy stash carries its OWN marker - a fresh install must
    not re-import recordings the user long since deleted."""
    app_dir = frozen_env
    migrate_legacy_data()
    assert (tmp_path / "localappdata" / "XMacro-peater"
            / ".migrated").exists()

    # Simulate a REINSTALL: fresh app dir, no install-side marker
    app2 = tmp_path / "app2"
    monkeypatch.setattr(cfg_mod, "APP_DIR", app2)
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", app2 / "config")
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH",
                        app2 / "config" / "app_config.json")
    monkeypatch.setattr(cfg_mod, "RECORDINGS_DIR", app2 / "recordings")
    migrate_legacy_data()
    assert not (app2 / "recordings" / "old_take.json").exists()
    assert (app2 / "config" / ".legacy-migrated").exists()
