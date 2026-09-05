<div align="center">

# 🎮 XMacro-peater

**Record keyboard, mouse & controller input — replay it with sub-millisecond precision.**

Output flows through a virtual Xbox 360 pad and genuine relative input events, so games can't tell the difference.

[![CI](https://github.com/xalaad/xmacro-peater/actions/workflows/ci.yml/badge.svg)](https://github.com/xalaad/xmacro-peater/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/xalaad/xmacro-peater?color=35c26e)](https://github.com/xalaad/xmacro-peater/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-a8b23f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-35c26e.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-1c241d.svg)](#)

*by [Xanonz](https://github.com/xalaad)*

**Windows macro recorder · input replayer · keyboard mouse controller gamepad macro tool · auto clicker alternative · Xbox / PlayStation controller macro · touch gesture recorder · game input automation**

<br>

![XMacro-peater — main window with live input test](docs/screenshots/app-full.png)

<table>
  <tr>
    <td align="center"><img src="docs/screenshots/compact-deck.png" width="220" alt="Compact sidebar deck"></td>
    <td align="center"><img src="docs/screenshots/settings.png" width="300" alt="Settings dialog"></td>
    <td align="center"><img src="docs/screenshots/overlay.png" width="330" alt="In-game mini overlay"></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Compact deck</b> — sidebar-only mode, one arrow-click away</sub></td>
    <td align="center"><sub><b>Settings</b> — everything applies &amp; saves live</sub></td>
    <td align="center"><sub><b>Mini overlay</b> — control it from inside your game</sub></td>
  </tr>
</table>

![Sequence builder — chain recordings into one precise timeline](docs/screenshots/sequence-builder.png)
<sub><b>Sequence builder</b> — chain recordings with per-step runs &amp; waits, live pass estimate, drift-free execution.</sub>

<br><br>

![TEST MODE — fullscreen live input dashboard](docs/screenshots/test-mode.png)
<sub><b>TEST MODE</b> — every device live, edge to edge: full mechanical keyboard with numpad, controller with per-button glow, analog scopes, and a circular mouse stage.</sub>

</div>

---

## 📚 Navigation

- [✨ Features](#-features)
- [📦 Install](#-install)
- [🚀 Quick start](#-quick-start)
- [🔗 Sequences](#-sequences)
- [⚙️ Settings explained](#%EF%B8%8F-settings-explained)
- [⌨️ Default hotkeys](#%EF%B8%8F-default-hotkeys)
- [🎯 Measured precision](#-measured-precision)
- [🎮 Custom controller schemes](#-custom-controller-schemes)
- [🏗 Architecture](#-architecture)
- [🔨 Building a release locally](#-building-a-release-locally)
- [🤝 Contributing](#-contributing)
- [⚠️ Fair use](#%EF%B8%8F-fair-use)
- [📄 License](#-license)

---

## ✨ Features

- **Record everything at once** — keyboard, mouse, and controller
  (buttons, sticks, analog triggers) at a drift-corrected 125 Hz.
- **True hardware mouse capture** via **Windows Raw Input**: real deltas,
  immune to games recentering or clamping the cursor. Analog values are
  recorded **raw** (deadzone is only a noise gate — never rescaled).
- **Precise replay** — hybrid sleep/busy-wait scheduling with
  TIME_CRITICAL threads lands events within ~1 ms of the recorded
  timestamps even under full game load. Mouse motion replays as genuine
  relative `SendInput`; controller output goes through a **virtual
  Xbox 360 pad** (ViGEmBus).
- **Loop-perfect repeats** — the cursor is anchored at run 1 and restored
  before every subsequent run, so moves trace the same path and clicks hit
  the same pixels in every cycle.
- **Sequences** — chain recordings into ordered playlists: per step, how
  many runs and the wait after each run, then repeat the whole chain with
  the normal loop modes. The chain plays as **one precise timeline**
  (waits are scheduled from the run's start, so overhead never drifts it),
  and renaming a recording auto-updates every sequence that uses it.
- **Any pad in, Xbox pad out** — Xbox via raw XInput; PlayStation and
  generic pads via JSON-defined schemes (adding a controller = adding a
  JSON file). Live per-scheme connection dots and a device switcher when
  several pads are plugged in.
- **Touch mode** — record taps, drags & swipes as absolute on-screen
  gestures and replay them as **genuine Windows touch input**
  (`InjectTouchInput`), perfect for touchscreen apps and UI automation;
  falls back to absolute mouse where touch injection isn't available.
- **Global two-key hotkeys** that work while a game has focus —
  `Ctrl+F9` record, `Ctrl+F10` play, `Ctrl+F11` stop (rebindable; hotkey
  presses are stripped from recordings automatically).
- **Three ways to keep it around** — the full window; the **mini
  overlay** (a translucent always-on-top HUD over your game with state,
  last action, repeat controls and record/play/stop); or the **side
  drawer**: pick Dock left / Dock right from the dock menu and the
  sidebar glues to that screen edge at full height, always on top. The
  arrow strip slides it away entirely — only a small half-capsule tab
  stays at the edge; click it to slide the drawer back. The mode and
  side survive restarts.
- **TEST MODE** — a fullscreen live dashboard: full mechanical keyboard
  (with numpad), realistic mouse on a circular stage, controller with
  glowing buttons, stick scopes and trigger bars. Run injection presets,
  record temp takes, and replay them on the spot.
- **Sound cues** — distinct beep motifs for record/play/stop so you always
  know what happened while the game has focus.
- **Time-based repeats & scheduling** — delays are smart duration fields
  with a hard input mask (only valid time text can even be typed): enter
  `90`, `1h 30m` or `1:30:05`, nudge with arrow keys / wheel, or click
  the field — an h/m/s panel drops underneath (the field keeps focus and
  mirrors your typing) with a live "lands ~16:42" preview. Schedule a run
  for later or space repeats hours apart.
- **Record countdown** — a click-through ticking ring floats over the
  screen before recording starts (3 s, tunable in Settings), so you can
  get into position; it blocks nothing and never steals focus.
- **Custom dark UI** — frameless themed window, green/olive terminal
  aesthetic, everything resizable with a sane minimum.

## 📦 Install

**Option A — installer (recommended):** download
`XMacro-peater-Setup-*.exe` from
[Releases](https://github.com/xalaad/xmacro-peater/releases) and run it.
It installs the app with Start Menu/desktop shortcuts **and silently sets
up the ViGEmBus virtual-controller driver** if it's missing — everything
needed, one click.

> **Requirements:** Windows 10/11, 64-bit — that's it. No GPU needed,
> ~136 MB on disk, ~160 MB RAM while running, idle CPU ≈ 0%.

**Option B — portable:** grab the `-portable.zip`, extract, run
`XMacro-peater.exe`. Controller output needs the ViGEmBus driver — the
app detects that on first controller playback and offers its bundled
installer (keyboard/mouse macros never need it).

**Option B — from source** (Python 3.11+):

```powershell
git clone https://github.com/xalaad/xmacro-peater.git
cd xmacro-peater
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

> `vgamepad` prompts to install the **ViGEmBus** driver on first install —
> accept it; that's what creates the virtual controller. `pygame` is
> optional: without it the app still fully works for Xbox pads and
> keyboard/mouse; PlayStation/generic options gray out with a tooltip.

## 🚀 Quick start

1. Pick your controller **scheme** on the Controller tab (a green dot
   marks the scheme whose pad is actually connected).
2. Hit **● Record** (or `Ctrl+F9`), play, hit `Ctrl+F9` again — the take
   lands in `recordings/` with duration and per-device event counts.
3. Choose the repeat mode (once / N times / forever + delays — the plan
   line spells out exactly what Play will do) and hit **▶ Play**
   (or `Ctrl+F10`). `Ctrl+F11` aborts and releases every held key/button.
4. Click **Mini** for the in-game overlay, **Dock** to glue the sidebar
   to the screen edge as a slide-away drawer, or **Tester** for the
   fullscreen live input dashboard.
5. Got several recordings that belong together? Switch the deck to
   **SEQUENCES** and hit **+** to chain them — see below.

## 🔗 Sequences

Sequences turn recordings into building blocks: record small macros once,
then compose them into ordered chains instead of re-recording long
sessions.

Switch the sidebar deck to **SEQUENCES** and press **+** to open the
builder. Each step is one line: **which recording**, **how many runs**,
and **the wait after each run**. Drag the grip to reorder steps with the
mouse, watch the live "one pass ≈ …" estimate, save — the sequence appears as a card and plays
exactly like a recording: select it and hit **▶ Play** (or `Ctrl+F10`),
including from the mini overlay.

Missing a step? **● Record step** captures it without leaving the
builder: the dialog steps aside, the countdown ring ticks down, and when
you stop (`Ctrl+F9`) the fresh take is appended as the next step.

How a chain executes:

- One **pass** = every step in order, honoring its runs and waits. The
  **loop controls on the main screen** (once / N times / forever + repeat
  delay) then repeat the whole pass — the last step's trailing wait is
  skipped so the pass delay is the only gap between passes.
- **Perfect time management**: inside a run, events replay through the
  same sub-millisecond scheduler as single recordings; each wait is
  scheduled against the *run's start time* (`t0 + duration + wait`), so
  callback and cleanup overhead never accumulates — an hour-long chain
  lands its last event where the arithmetic says.
- **Loop-perfect across steps**: the cursor anchor is captured at the
  chain's first mouse run and restored before every later one, so
  relative mouse moves can't drift between steps or passes.
- The activity log and overlay narrate progress live:
  `Pass 2/5 · Step 1/3: farm_run.json (run 2/3)`.
- Sequences are tiny JSON files in `sequences/` next to your recordings —
  easy to share. A broken chain (deleted recording) refuses to start and
  names the exact step; **renaming** a recording auto-updates every
  sequence that references it. Mixed device types are fine — chain a
  keyboard macro into a controller macro into a touch gesture.

## ⚙️ Settings explained

Every setting applies and saves the moment you change it. Hover any
**(?)** in the app for the same explanations.

### Recording

| Setting | What it does | Example |
|---|---|---|
| **Record countdown** (default 3 s) | Heads-up before recording starts: a **click-through** ticking ring appears over the screen so you can get into position — input keeps flowing, nothing is blocked or focused. Press Record again to cancel. | Set 0 to record instantly; set 5 s when you need time to alt-tab into a game. |
| **Controller poll rate** (default 125 Hz) | How often the gamepad is sampled while recording. 125 Hz matches a standard pad's own USB report rate. | Leave at 125 Hz; raising to 250 Hz rarely captures more detail but doubles CPU use of the pollers. |
| **Stick deadzone** (default 8%) | A *radial* noise gate: stick positions inside this circle record as a clean 0 so a worn, drifting stick doesn't flood the file. Values are always stored **raw** — this never rescales what's recorded. | Your left stick drifts slightly at rest and the log fills with tiny axis events → raise to 12–15%. |
| **Trigger deadzone** (default 2%) | Same gate for the analog triggers: pressure below the level records as fully released. | A hair-trigger pad registers 1–2% pull constantly → keep at 2–5%. |
| **Touch mode** (default OFF) | Records taps, drags & swipes as absolute on-screen gestures and replays them as **genuine Windows touch**. OFF records relative mouse deltas instead. | ON: automate a touchscreen kiosk app (tap button → drag slider). OFF: record camera-look in a shooter — games need relative deltas. |

### Playback

| Setting | What it does | Example |
|---|---|---|
| **Start delay** (default 3 s) | Grace period after pressing Play. Type any duration (`90`, `1h 30m`, `1:30:05`) or open the clock panel; long delays *schedule* the run — the plan line shows the exact clock time and a countdown ticks on the overlay. | Leave 0 to replay into the focused app instantly; type `2h 30m` to schedule a run for later tonight. |
| **Delay between repeats** (default 1 s) | Pause after each run when repeating N times or looping forever — same smart field, so time-based repeats are just `1h`. Synced with the main-screen field. | 30 s respawn wait → `30`; run the loop once every hour → `1h`. |

### Global hotkeys

| Setting | What it does | Example |
|---|---|---|
| **Start/stop recording** (`ctrl+f9`) | Toggles recording even while a game has focus. Hotkey presses are stripped from recordings automatically. | Rebind to `shift+f2` if your game uses Ctrl+F9. |
| **Play / repeat** (`ctrl+f10`) | Plays the recording selected in the list with the current loop settings. | — |
| **Stop playback** (`ctrl+f11`) | Aborts instantly and releases every held key/button so nothing sticks. | — |

Two-key combos on purpose: single keys like `Esc` collide with in-game
menus.

### Mini overlay

| Setting | What it does | Example |
|---|---|---|
| **Background opacity** (default 92%) | How solid the overlay card is over your game; text stays fully readable. | Set 50% for a barely-there HUD during streams. |
| **Show hotkey hints** (ON) | Shows the combo reminders on the overlay. | Turn off once you know them, to save a line. |

> The overlay needs the game in **Borderless/Windowed** mode — exclusive
> fullscreen bypasses the compositor and no overlay (ours or Steam's) can
> draw over it.

### Interface

| Setting | What it does | Example |
|---|---|---|
| **Visualizer refresh cap** (60 fps) | Upper limit for Input Test animations only — completely independent from recording/playback accuracy. | Drop to 30 fps on a weak laptop; capture stays 125 Hz regardless. |
| **Sound cues** (ON) | Distinct beeps for record start/stop, playback start/finish/abort, so you know what happened while the game has focus. | Rising two-tone = recording armed; low buzz = aborted. |

## ⌨️ Default hotkeys

| Combo | Action |
|---|---|
| `Ctrl+F9` | Start / stop recording |
| `Ctrl+F10` | Play the selected recording |
| `Ctrl+F11` | Stop playback (releases everything) |

All rebindable in Settings; two-key combos on purpose so they never clash
with in-game keys, and they are never written into recordings.

## 🎯 Measured precision

Validated by the bundled end-to-end suite (`tools/precision_test.py`),
which injects a scripted sequence over an absorb window, records it,
replays it, and measures what a real window receives:

| Metric | Result |
|---|---|
| Mouse motion fidelity (capture & replay) | **100.0 %** pixel-exact |
| Clicks / keys delivered | **100 %** at every stage |
| Engine scheduling error | avg **0.00 ms**, max 0.04 ms |
| Repeated cycles (3-run loop, +150 px/run drift macro) | **0 px** start-position spread |

## 🎮 Custom controller schemes

Drop a JSON file into `config/schemes/` (or *Settings → Import custom
scheme*):

```json
{
  "name": "My Pad",
  "backend": "pygame",
  "art": "controller_generic.svg",
  "layout": "generic",
  "labels": { "A": "Cross", "B": "Circle" },
  "buttons": { "0": "A", "1": "B", "2": "X", "3": "Y" },
  "axes": { "lx": 0, "ly": 1, "rx": 2, "ry": 3 },
  "triggers": { "lt": 4, "rt": 5 },
  "invert_y": true,
  "hat_dpad": true
}
```

`buttons` maps pygame indices to canonical names (`A/B/X/Y`,
`LEFT_SHOULDER`, `DPAD_*`, …), `labels` sets display names, `layout`
picks the visualizer geometry (`xbox` / `ps` / `generic`).

## 🏗 Architecture

```
core/   pure Python, no Qt — events, radial deadzone, drift-corrected
        pollers, Raw Input capture, playback engine, controller backends
ui/     PySide6 — frameless chrome, visualizers, overlay, TEST MODE;
        talks to core only through Qt signal bridges
tools/  precision_test.py — the end-to-end accuracy harness
```

Capture and playback run on TIME_CRITICAL background threads; the UI
repaints on its own 60 fps timer, fully decoupled from the 125 Hz
sampling, and every visualizer repaints only when its state changes.

## 🔨 Building a release locally

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller xmacro_peater.spec   # -> dist/XMacro-peater.exe
```

Tagged pushes (`v*`) trigger the [release workflow](.github/workflows/release.yml),
which tests, builds, and publishes a zipped exe to GitHub Releases
automatically.

## 🤝 Contributing

Ideas, edits, and fixes are welcome:

- **Pull requests** — open one against `main`; CI runs the core test
  suite automatically. Every PR gets reviewed.
- **Email** — send ideas or patches to
  [alaa.daour98@gmail.com](mailto:alaa.daour98@gmail.com); they'll be
  checked and credited.
- **Telegram** — [@Xanonz](https://t.me/Xanonz).

Please run `pytest tests` before submitting, and keep `core/` free of Qt
imports.

## ⚠️ Fair use

Built for single-player automation, accessibility, testing, and games
that allow macros. Some online/competitive games treat synthetic input as
a bannable offense — check the rules before using it there.

## 📄 License

[MIT](LICENSE) © 2026 Xanonz
