"""Audio cues so you know what the app did while a game has focus.

Distinct short beep motifs per action, played via winsound on a worker
thread (Beep blocks). Toggleable in Settings (cfg.sounds).
"""
from __future__ import annotations

import sys
import threading

if sys.platform == "win32":
    import winsound

    def _play(pattern: list[tuple[int, int]]) -> None:
        def run():
            for freq, dur in pattern:
                try:
                    winsound.Beep(freq, dur)
                except RuntimeError:
                    return
        threading.Thread(target=run, daemon=True).start()
else:  # pragma: no cover
    def _play(pattern) -> None:
        pass


enabled = True


def _cue(pattern: list[tuple[int, int]]) -> None:
    if enabled:
        _play(pattern)


def record_start() -> None:
    _cue([(880, 110), (1320, 110)])       # rising: armed


def tick() -> None:
    _cue([(1180, 35)])                    # short click: countdown second


def record_stop() -> None:
    _cue([(1320, 110), (880, 110)])       # falling: saved


def play_start() -> None:
    _cue([(660, 90), (990, 90), (1320, 130)])   # fanfare up: running


def play_done() -> None:
    _cue([(990, 90), (660, 150)])         # settle down: finished


def play_abort() -> None:
    _cue([(440, 200)])                    # low buzz: aborted
