"""Macro event model and macro file load/save.

A macro is a time-ordered list of flat event dicts. Event shapes:

    {"t": 0.123, "src": "kb",           "action": "down"|"up", "key": "char:a"|"key:f9"}
    {"t": ...,   "src": "mouse_move",   "dx": int, "dy": int}
    {"t": ...,   "src": "mouse_btn",    "action": "down"|"up", "button": "left"}
    {"t": ...,   "src": "mouse_scroll", "dx": int, "dy": int}
    {"t": ...,   "src": "pad_btn",      "action": "down"|"up", "button": "A"}
    {"t": ...,   "src": "pad_trigger",  "trigger": "left"|"right", "value": 0..1}
    {"t": ...,   "src": "pad_axis",     "stick": "left"|"right", "x": -1..1, "y": -1..1}

File format v2 wraps the list with metadata; v1 files (a bare JSON list, as
written by the old CLI tool) still load.
"""
from __future__ import annotations

import json
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_NAME = "macro-suite"
FORMAT_VERSION = 2

KB = "kb"
MOUSE_MOVE = "mouse_move"
MOUSE_BTN = "mouse_btn"
MOUSE_SCROLL = "mouse_scroll"
TOUCH = "touch"  # absolute taps/drags/swipes: action down|move|up + x,y
PAD_BTN = "pad_btn"
PAD_TRIGGER = "pad_trigger"
PAD_AXIS = "pad_axis"

PAD_SOURCES = frozenset({PAD_BTN, PAD_TRIGGER, PAD_AXIS})
ALL_SOURCES = (frozenset({KB, MOUSE_MOVE, MOUSE_BTN, MOUSE_SCROLL, TOUCH})
               | PAD_SOURCES)


@dataclass
class MacroEvent:
    t: float
    src: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"t": round(self.t, 5), "src": self.src, **self.data}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MacroEvent":
        d = dict(d)
        t = float(d.pop("t"))
        src = str(d.pop("src"))
        if src not in ALL_SOURCES:
            raise ValueError(f"Unknown event source: {src!r}")
        return cls(t=t, src=src, data=d)


@dataclass
class MacroFile:
    events: list[MacroEvent] = field(default_factory=list)
    poll_hz: int = 125
    created_utc: str = ""
    duration: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_utc:
            self.created_utc = datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            )
        if not self.duration and self.events:
            self.duration = self.events[-1].t

    @property
    def has_pad_events(self) -> bool:
        return any(e.src in PAD_SOURCES for e in self.events)

    def counts_by_source(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.src] = counts.get(e.src, 0) + 1
        return counts

    def save(self, path: str | Path) -> None:
        path = Path(path)
        payload = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "created_utc": self.created_utc,
            "poll_hz": self.poll_hz,
            "duration": round(self.duration, 5),
            "events": [e.to_dict() for e in self.events],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "MacroFile":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, list):  # v1: bare event list from the old CLI tool
            events = [MacroEvent.from_dict(d) for d in raw]
            return cls(events=events)
        if not isinstance(raw, dict) or raw.get("format") != FORMAT_NAME:
            raise ValueError(f"{path}: not a recognized macro file")
        events = [MacroEvent.from_dict(d) for d in raw.get("events", [])]
        events.sort(key=lambda e: e.t)
        return cls(
            events=events,
            poll_hz=int(raw.get("poll_hz", 125)),
            created_utc=str(raw.get("created_utc", "")),
            duration=float(raw.get("duration", 0.0)),
        )
