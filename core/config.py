"""App configuration: pydantic schema + JSON load/save with safe defaults."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

APP_VERSION = "1.0"

if getattr(sys, "frozen", False):
    # PyInstaller: the exe usually lives in Program Files, which is
    # read-only for normal users — user data (config/logs/recordings)
    # goes to %LOCALAPPDATA%\XMacro-peater instead.
    APP_DIR = Path(os.environ.get("LOCALAPPDATA",
                                  str(Path.home()))) / "XMacro-peater"
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS",
                              Path(sys.executable).resolve().parent))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = APP_DIR
CONFIG_DIR = APP_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "app_config.json"
SCHEMES_DIR = CONFIG_DIR / "schemes"
RECORDINGS_DIR = APP_DIR / "recordings"
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
    loop_delay: float = Field(default=1.0, ge=0.0, le=3600.0)
    countdown_seconds: int = Field(default=3, ge=0, le=10)
    loop_mode: int = Field(default=0, ge=0, le=2)  # once / N times / forever
    loop_count: int = Field(default=5, ge=2, le=9999)


class AppConfig(BaseModel):
    poll_hz: int = Field(default=125, ge=10, le=1000)
    stick_deadzone: float = Field(default=0.08, ge=0.0, lt=1.0)
    trigger_deadzone: float = Field(default=0.02, ge=0.0, lt=1.0)
    controller_scheme: str = "xbox"
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    ui_fps: int = Field(default=60, ge=15, le=144)
    sounds: bool = True
    log_motion: bool = True  # Activity 'Motion' toggle, persisted
    # Touch mode: record absolute taps/drags/swipes and replay them as
    # genuine Windows touch input (instead of relative mouse deltas)
    touch_mode: bool = False


def load_config(path: str | Path = CONFIG_PATH) -> AppConfig:
    """Load config; fall back to defaults (and log) on missing/invalid file."""
    path = Path(path)
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, path)
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        log.warning("Config %s invalid (%s); using defaults", path, e)
        return AppConfig()


def save_config(cfg: AppConfig, path: str | Path = CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")


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
