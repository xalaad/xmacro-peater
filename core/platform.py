"""Platform capability registry — the single map of what works where.

The app is built Windows-first but structured to port: every OS-specific
feature is isolated behind a module that degrades cleanly (returns
False/None or falls back), and this registry names each capability, how
to probe it, and what the fallback is. A port fills in capabilities one
by one — nothing else needs restructuring, and Windows behavior (and
its timing precision) is untouched because probes only *read* the same
availability flags the modules already expose.

Capability -> where it lives -> non-Windows fallback:

- raw_mouse       core.capture.raw_mouse    pynput cursor deltas
- raw_touch       core.capture.raw_touch/hid_touch   no touch capture
- touch_inject    core.playback.touch       absolute-mouse emulation
- virtual_pad     vgamepad (ViGEmBus)       none (Windows driver only);
                                            pygame INPUT reading is
                                            already cross-platform
- timer_boost     core.timing               plain sleep scheduling
                                            (perf_counter/busy-wait in
                                            core.timing work everywhere)
- global_hotkeys  core.hotkeys (pynput)     pynput works on macOS with
                                            accessibility permission
- layout_labels   ui.widgets.keyboard_widget (ToUnicodeEx)  static caps
- screen_rect     core.screen               port per OS (see its note)
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def capabilities() -> dict[str, bool]:
    """Live probe of every optional platform capability. Import-light:
    safe to call on any OS, never raises."""
    from .capture.raw_mouse import RawMouseCapture
    from .playback.touch import TouchInjector
    from .screen import touch_device_present, virtual_screen_rect

    try:
        # find_spec, NOT import: importing vgamepad connects to the
        # ViGEmBus driver — a probe must stay passive
        import importlib.util
        virtual_pad = (IS_WINDOWS
                       and importlib.util.find_spec("vgamepad") is not None)
    except Exception:  # noqa: BLE001 — a probe must never raise
        virtual_pad = False
    try:
        from pynput import keyboard  # noqa: F401
        hotkeys = True
    except Exception:  # noqa: BLE001
        hotkeys = False
    return {
        "raw_mouse": RawMouseCapture.available(),
        "raw_touch": IS_WINDOWS and touch_device_present(),
        "touch_inject": TouchInjector.available(),
        "virtual_pad": virtual_pad,
        "timer_boost": IS_WINDOWS,
        "global_hotkeys": hotkeys,
        "layout_labels": IS_WINDOWS,
        "screen_rect": virtual_screen_rect() is not None,
    }


def capability_summary() -> str:
    """One log line: 'raw_mouse ✓  raw_touch ✗  ...'."""
    caps = capabilities()
    return "  ".join(
        f"{name} {'OK' if ok else '--'}" for name, ok in caps.items())
