"""App configuration: pydantic schema + JSON load/save with safe defaults."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

# Single source of truth for the app version. The release workflow
# checks the pushed tag against this value, and the installer gets it
# via ISCC /DAppVersion — bump it here and tag v<this> to release.
APP_VERSION = "1.2.1"

if getattr(sys, "frozen", False):
    # PyInstaller: config/recordings/logs live NEXT TO THE EXE so users
    # can find, edit, and share them easily. The installer grants write
    # permission on these folders (Program Files is otherwise read-only);
    # the portable build sits in a user-writable folder anyway.
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = APP_DIR
CONFIG_DIR = APP_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "app_config.json"
SCHEMES_DIR = CONFIG_DIR / "schemes"
RECORDINGS_DIR = APP_DIR / "recordings"
SEQUENCES_DIR = APP_DIR / "sequences"
LOGS_DIR = APP_DIR / "logs"


class BrandingConfig(BaseModel):
    """Footer + About dialog identity. Empty links are hidden in the UI."""
    app_name: str = "XMacro-peater"
    author: str = "Xanonz"
    description: str = (
        "Record keyboard, mouse & controller input and replay it with "
        "sub-millisecond precision — output flows through a virtual "
        "Xbox 360 pad and real input events, so games can't tell the "
        "difference."
    )
    github: str = "https://github.com/xalaad"
    telegram: str = "https://t.me/Xanonz"
    email: str = "alaa.daour98@gmail.com"


class HotkeyConfig(BaseModel):
    record_toggle: str = "ctrl+f9"
    play_last: str = "ctrl+f10"
    # Two-key combo on purpose: plain Esc is a core in-game key (menus),
    # so a single-key abort would fire constantly during normal play.
    abort_playback: str = "ctrl+f11"


class OverlayConfig(BaseModel):
    opacity: float = Field(default=0.92, ge=0.3, le=1.0)
    show_hints: bool = True


class PlaybackConfig(BaseModel):
    # Both delays accept long schedules (up to 24h) via the h/m/s pickers
    loop_delay: float = Field(default=1.0, ge=0.0, le=86400.0)
    countdown_seconds: float = Field(default=3, ge=0, le=86400.0)
    loop_mode: int = Field(default=0, ge=0, le=2)  # once / N times / forever
    loop_count: int = Field(default=5, ge=2, le=9999)
    # Replay the exact recorded cursor path (absolute injection) instead
    # of raw relative counts. ON (default) = pixel-deterministic on ANY
    # pointer device/settings/screen — immune to Windows' velocity-
    # dependent pointer acceleration. OFF = game-grade raw counts for
    # in-game camera look (games read input before acceleration)
    mouse_path_replay: bool = True


class AppConfig(BaseModel):
    poll_hz: int = Field(default=125, ge=10, le=1000)
    # On-screen ticking countdown before a recording starts (settings-only)
    record_countdown: float = Field(default=3, ge=0, le=60)
    stick_deadzone: float = Field(default=0.08, ge=0.0, lt=1.0)
    trigger_deadzone: float = Field(default=0.02, ge=0.0, lt=1.0)
    controller_scheme: str = "xbox"
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    ui_fps: int = Field(default=60, ge=15, le=144)
    sounds: bool = True
    log_enabled: bool = True  # Activity 'Log' master toggle, persisted
    log_motion: bool = True   # Activity 'Motion' toggle, persisted
    # Touch mode: record absolute taps/drags/swipes and replay them as
    # genuine Windows touch input (instead of relative mouse deltas).
    # The effective default follows the HARDWARE — see load_config():
    # ON for touchscreens, and forced off on machines without one.
    touch_mode: bool = False


def has_touchscreen() -> bool:
    """True when this machine has a touch digitizer."""
    try:
        from .playback.touch import touch_device_present
        return touch_device_present()
    except Exception:  # noqa: BLE001 — never block startup
        return False


def load_config(path: str | Path = CONFIG_PATH) -> AppConfig:
    """Load config; fall back to defaults (and log) on missing/invalid file.

    Touch mode is hardware-driven: ON by default on a touchscreen, and
    always off without one (so a config copied from a touch device does
    not enable a mode this machine cannot use)."""
    path = Path(path)
    touch = has_touchscreen()
    if not path.exists():
        cfg = AppConfig()
        cfg.touch_mode = touch
        try:
            save_config(cfg, path)
        except OSError as e:
            # Read-only install dir (portable copy in a protected
            # location): run on defaults instead of dying at startup
            log.warning("Can't write first-run config %s: %s", path, e)
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = AppConfig.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        log.warning("Config %s invalid (%s); using defaults", path, e)
        cfg = AppConfig()
        cfg.touch_mode = touch
        return cfg
    if not touch and cfg.touch_mode:
        cfg.touch_mode = False
        try:
            save_config(cfg, path)
        except OSError as e:
            log.warning("Can't update config %s: %s", path, e)
    return cfg


def save_config(cfg: AppConfig, path: str | Path = CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")


def migrate_legacy_data() -> None:
    """ONE-time copy from the old %LOCALAPPDATA%\\XMacro-peater location
    (used by v1.0 builds) to the folders next to the exe.

    A marker file makes it truly one-time: without it, every launch
    re-copied anything the user had deleted — deleted recordings kept
    "coming back" from the legacy stash."""
    if not getattr(sys, "frozen", False):
        return
    marker = CONFIG_DIR / ".legacy-migrated"
    if marker.exists():
        return
    import shutil
    try:
        legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "XMacro-peater"
        # The install-side marker dies with every reinstall, so the stash
        # ALSO carries one: without it, each fresh install re-imported
        # the same old recordings ("the installer keeps shipping my
        # takes" — it didn't; this did).
        if (legacy / ".migrated").exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            marker.write_text("done", encoding="utf-8")
            return
        if legacy.exists() and legacy != APP_DIR:
            old_cfg = legacy / "config" / "app_config.json"
            if old_cfg.exists() and not CONFIG_PATH.exists():
                CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_cfg, CONFIG_PATH)
            old_rec = legacy / "recordings"
            if old_rec.exists():
                RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
                for f in old_rec.glob("*.json"):
                    dest = RECORDINGS_DIR / f.name
                    if not dest.exists():
                        shutil.copy2(f, dest)
            log.info("Migrated legacy data from %s", legacy)
            (legacy / ".migrated").write_text("done", encoding="utf-8")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text("done", encoding="utf-8")
    except OSError as e:
        log.warning("Legacy data migration failed: %s", e)


def setup_logging(level: int = logging.INFO) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
