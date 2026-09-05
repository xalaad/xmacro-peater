# Roadmap

Plans beyond the current release. Order within a section is rough
priority; nothing here is a promise with a date.

## Multi-platform (macOS / Linux)

The codebase is already structured for this: every OS-specific
capability is isolated behind a module with a clean fallback, and
`core/platform.py` maps each one (what it is, where it lives, what
happens without it). The engine core, sequences, timing math, config
and the whole Qt UI are platform-neutral today.

Target for a first port: **keyboard + mouse macros** (record, replay,
sequences, hotkeys). Controller *input* reading via pygame also works
cross-platform.

Work items, roughly in order:

1. **Window chrome fallback** — the frameless window uses Windows
   native hit-testing (`WM_NCHITTEST`) for resize/drag/snap. Other
   OSes need the system title bar instead (capability-gated in
   `ui/window/chrome.py`).
2. **`core/screen.py` ports** — virtual-desktop rect: CoreGraphics on
   macOS, X11/Wayland on Linux. Everything downstream already
   tolerates `None`.
3. **Sounds** — replace `winsound` beeps with a Qt sound fallback.
4. **Raw mouse** — Linux: `evdev` gives true hardware deltas (games
   fidelity preserved). macOS has no equivalent → pynput cursor
   deltas, with "Replay exact cursor path" as the default there.
5. **Timing re-benchmark** — no `timeBeginPeriod`/`TIME_CRITICAL`
   elsewhere; measure real scheduling error per platform and publish
   honest numbers.
6. **Packaging** — `.dmg` + notarization (macOS; also the
   Accessibility / Input Monitoring permission flow), AppImage or
   Flatpak (Linux).
7. **Linux virtual pad (stretch)** — `uinput` can emulate a gamepad;
   would restore controller *output* on Linux.

Not portable (OS reality, not code): touch capture/injection
(Windows digitizer APIs) and ViGEmBus virtual-pad output on macOS.
The capability flags hide these features where the OS can't do them.

## App features

- **Sequence editor upgrades** — per-step repeat-until conditions,
  step disable/solo toggles for testing chains.
- **Recording editor** — trim start/end, delete an event range,
  time-stretch a take without re-recording it.
- **Profiles** — named bundles of settings + hotkeys, switchable per
  game.
- **Scheduled runs** — start a recording/sequence at a clock time.

## Engineering

- **Code signing** — SignPath Foundation (free for OSS) or Azure
  Trusted Signing, wired into the release workflow, so installs stop
  showing "Unknown publisher".
- **Playback CPU trade dial** — the timing loop busy-waits the last
  3ms of every event for sub-ms accuracy; expose a battery-friendly
  mode (~1.5ms window) for laptops.
- **Startup metadata format** — persist per-take metadata at save time
  (v3 recording format) instead of the sidecar deck cache.
- **Shared win32 message-window plumbing** — `raw_mouse` and the
  digitizer hub each own a copy; extract one helper when the next
  capture feature lands.
