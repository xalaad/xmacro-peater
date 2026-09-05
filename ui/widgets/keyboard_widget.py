"""On-screen full mechanical keyboard: main block + nav cluster + arrows +
numpad, laid out in real key units and scaled to whatever space is
available. Keys pulse in the primary accent color on press.

Numpad detection isn't possible from hooks, so the full 100% layout is
always shown (per spec); numpad keys light independently thanks to the
vk-based reps the live monitor supplies (key:kp_*).

Drawn as a single paintEvent over precomputed unit-rects — cheaper than a
hundred child widgets, repaints only when state changes.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import Theme

# Physical key map: layout-INDEPENDENT virtual-key codes -> the US char
# rep each drawn key uses. Lets any keyboard layout (Arabic, Cyrillic,
# AZERTY, ...) light the right physical key, and drives the live
# key-cap relabeling below.
PHYSICAL_VK: dict[int, str] = {
    **{vk: chr(vk + 32) for vk in range(0x41, 0x5B)},   # A-Z
    **{vk: chr(vk) for vk in range(0x30, 0x3A)},        # 0-9
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}


def layout_labels(hkl: int) -> dict[str, str]:
    """{char-rep: key cap} for ANY installed layout handle, via
    ToUnicodeEx — the real character each physical key produces (Arabic,
    Cyrillic, Greek, ...). MapVirtualKeyEx(VK_TO_CHAR) is NOT used: it
    returns Latin capitals for letter keys on many layouts.

    Flag bit 2 (Win10 1607+) keeps kernel dead-key state untouched; a
    dead-key result (-1) still yields its own character in the buffer."""
    if sys.platform != "win32":  # pragma: no cover
        return {}
    import ctypes
    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(8)
    state = (ctypes.c_ubyte * 256)()
    labels: dict[str, str] = {}
    for vk, ch in PHYSICAL_VK.items():
        sc = user32.MapVirtualKeyExW(vk, 0, hkl)  # MAPVK_VK_TO_VSC
        n = user32.ToUnicodeEx(vk, sc, state, buf, 8, 1 << 2, hkl)
        if n == -1:  # dead key: its standalone character is in the buffer
            n = 1
        if n > 0:
            text = buf.value[:n]
            if text and ord(text[0]) >= 32:
                labels[f"char:{ch}"] = text.upper() or text
    return labels


def active_layout_hkl() -> int:
    """Layout handle of the FOREGROUND window — two syscalls, no more.
    The per-second poll checks only this; label maps are computed once
    per distinct layout and cached."""
    if sys.platform != "win32":  # pragma: no cover
        return 0
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    hkl = user32.GetKeyboardLayout(tid) & 0xFFFFFFFF
    if not hkl:  # foreign-process edge case: use our own thread's layout
        hkl = user32.GetKeyboardLayout(0) & 0xFFFFFFFF
    return hkl


def active_layout_labels() -> tuple[int, dict[str, str]]:
    """(active layout handle, labels) for the foreground window."""
    hkl = active_layout_hkl()
    return hkl, layout_labels(hkl) if hkl else {}

# (label, rep, x, y, w, h) in key units. Blocks:
#   main 0..15 · nav 15.5..18.5 · numpad 19..23  — 6.25 rows tall
U = 1.0
KEYS: list[tuple[str, str, float, float, float, float]] = []


def _k(label, rep, x, y, w=1.0, h=1.0):
    KEYS.append((label, rep, x, y, w, h))


# --- function row
_k("Esc", "key:esc", 0, 0)
for i, f in enumerate(range(1, 5)):
    _k(f"F{f}", f"key:f{f}", 2 + i, 0)
for i, f in enumerate(range(5, 9)):
    _k(f"F{f}", f"key:f{f}", 6.5 + i, 0)
for i, f in enumerate(range(9, 13)):
    _k(f"F{f}", f"key:f{f}", 11 + i, 0)
# --- number row
row = [("`", "char:`"), ("1", "char:1"), ("2", "char:2"), ("3", "char:3"),
       ("4", "char:4"), ("5", "char:5"), ("6", "char:6"), ("7", "char:7"),
       ("8", "char:8"), ("9", "char:9"), ("0", "char:0"), ("-", "char:-"),
       ("=", "char:=")]
for i, (lbl, rep) in enumerate(row):
    _k(lbl, rep, i, 1.25)
_k("Bksp", "key:backspace", 13, 1.25, 2)
# --- QWERTY row
_k("Tab", "key:tab", 0, 2.25, 1.5)
for i, ch in enumerate("qwertyuiop"):
    _k(ch.upper(), f"char:{ch}", 1.5 + i, 2.25)
_k("[", "char:[", 11.5, 2.25)
_k("]", "char:]", 12.5, 2.25)
_k("\\", "char:\\", 13.5, 2.25, 1.5)
# --- home row
_k("Caps", "key:caps_lock", 0, 3.25, 1.75)
for i, ch in enumerate("asdfghjkl"):
    _k(ch.upper(), f"char:{ch}", 1.75 + i, 3.25)
_k(";", "char:;", 10.75, 3.25)
_k("'", "char:'", 11.75, 3.25)
_k("Enter", "key:enter", 12.75, 3.25, 2.25)
# --- shift row
_k("Shift", "key:shift", 0, 4.25, 2.25)
for i, ch in enumerate("zxcvbnm"):
    _k(ch.upper(), f"char:{ch}", 2.25 + i, 4.25)
_k(",", "char:,", 9.25, 4.25)
_k(".", "char:.", 10.25, 4.25)
_k("/", "char:/", 11.25, 4.25)
_k("Shift", "key:shift_r", 12.25, 4.25, 2.75)
# --- bottom row
_k("Ctrl", "key:ctrl_l", 0, 5.25, 1.25)
_k("Win", "key:cmd", 1.25, 5.25, 1.25)
_k("Alt", "key:alt_l", 2.5, 5.25, 1.25)
_k("Space", "key:space", 3.75, 5.25, 6.25)
_k("Alt", "key:alt_gr", 10, 5.25, 1.25)
_k("Fn", "key:fn", 11.25, 5.25, 1.25)
_k("Menu", "key:menu", 12.5, 5.25, 1.25)
_k("Ctrl", "key:ctrl_r", 13.75, 5.25, 1.25)
# --- nav cluster
NAVX = 15.5
_k("Prt", "key:print_screen", NAVX, 0)
_k("Scr", "key:scroll_lock", NAVX + 1, 0)
_k("Pse", "key:pause", NAVX + 2, 0)
_k("Ins", "key:insert", NAVX, 1.25)
_k("Hm", "key:home", NAVX + 1, 1.25)
_k("PgU", "key:page_up", NAVX + 2, 1.25)
_k("Del", "key:delete", NAVX, 2.25)
_k("End", "key:end", NAVX + 1, 2.25)
_k("PgD", "key:page_down", NAVX + 2, 2.25)
_k("▲", "key:up", NAVX + 1, 4.25)
_k("◄", "key:left", NAVX, 5.25)
_k("▼", "key:down", NAVX + 1, 5.25)
_k("►", "key:right", NAVX + 2, 5.25)
# --- numpad
NPX = 19.0
_k("Num", "key:num_lock", NPX, 1.25)
_k("/", "key:kp_divide", NPX + 1, 1.25)
_k("*", "key:kp_multiply", NPX + 2, 1.25)
_k("-", "key:kp_subtract", NPX + 3, 1.25)
_k("7", "key:kp_7", NPX, 2.25)
_k("8", "key:kp_8", NPX + 1, 2.25)
_k("9", "key:kp_9", NPX + 2, 2.25)
_k("+", "key:kp_add", NPX + 3, 2.25, 1, 2)
_k("4", "key:kp_4", NPX, 3.25)
_k("5", "key:kp_5", NPX + 1, 3.25)
_k("6", "key:kp_6", NPX + 2, 3.25)
_k("1", "key:kp_1", NPX, 4.25)
_k("2", "key:kp_2", NPX + 1, 4.25)
_k("3", "key:kp_3", NPX + 2, 4.25)
_k("Ent", "key:kp_enter", NPX + 3, 4.25, 1, 2)
_k("0", "key:kp_0", NPX, 5.25, 2)
_k(".", "key:kp_decimal", NPX + 2, 5.25)

TOTAL_W = 23.0
TOTAL_H = 6.25

# Shifted symbols arrive from pynput as their shifted char — fold them back
# onto the physical key so visualization still lights up.
SHIFT_FOLD = {
    "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[",
    "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
}


def fold_rep(rep: str) -> str:
    """Normalize a pynput key repr onto our physical-key reps."""
    if rep.startswith("char:"):
        ch = rep[5:]
        ch = SHIFT_FOLD.get(ch, ch.lower())
        return "char:" + ch
    return rep


class KeyboardWidget(QWidget):
    DECAY = 0.10  # pulse intensity lost per frame (~6 frames of glow)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._held: set[str] = set()
        self._pulse: dict[str, float] = {}     # rep -> 0..1 fading intensity
        self._labels: dict[str, str] = {}      # rep -> active-layout cap
        self.setMinimumSize(560, 170)

    def set_layout_labels(self, labels: dict[str, str]) -> None:
        """Relabel the char key caps for the active keyboard layout."""
        if labels != self._labels:
            self._labels = dict(labels)
            self.update()

    def frame(self, held: set[str], pulses: list[str]) -> None:
        """Advance one UI frame; repaints only when something changed."""
        new_held = {fold_rep(r) for r in held}
        dirty = new_held != self._held or bool(pulses) or bool(self._pulse)
        self._held = new_held
        for rep in pulses:
            rep = fold_rep(rep)
            self._pulse[rep] = 1.0
        for rep in list(self._pulse):
            self._pulse[rep] -= self.DECAY
            if self._pulse[rep] <= 0:
                del self._pulse[rep]
        if dirty:
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        gap = 3.0
        unit = min(w / TOTAL_W, h / TOTAL_H)
        ox = (w - TOTAL_W * unit) / 2
        oy = (h - TOTAL_H * unit) / 2

        accent = QColor(self.theme.accent)
        font = QFont("Consolas")
        border = QColor(self.theme.border)

        for label, rep, kx, ky, kw, kh in KEYS:
            rect = QRectF(ox + kx * unit + gap / 2, oy + ky * unit + gap / 2,
                          kw * unit - gap, kh * unit - gap)

            held = rep in self._held
            pulse = self._pulse.get(rep, 0.0)
            intensity = 1.0 if held else pulse

            bg = self._blend(self.theme.surface2, accent.name(),
                             intensity * 0.9)

            # Press pulse: keys sink slightly at full intensity
            if intensity > 0:
                shrink = 1.2 * intensity
                rect = rect.adjusted(shrink, shrink, -shrink, -shrink)

            p.setPen(QPen(accent if intensity > 0.3 else border, 1))
            p.setBrush(bg)
            p.drawRoundedRect(rect, 4, 4)

            p.setPen(QColor("white") if intensity > 0.4
                     else QColor(self.theme.text_dim))
            cap = self._labels.get(rep, label)
            # Non-Latin caps (Arabic, Cyrillic, ...): Consolas has no
            # glyphs — use Segoe UI, a touch larger, and never clip so
            # ascenders/marks render in full
            if any(ord(c) > 0x2FF for c in cap):
                f2 = QFont("Segoe UI")
                f2.setPointSizeF(max(7.0, min(11.5, unit * 0.44)))
                p.setFont(f2)
            else:
                font.setPointSizeF(max(6.0, min(9.5, unit * 0.34)))
                p.setFont(font)
            p.drawText(rect,
                       int(Qt.AlignmentFlag.AlignCenter)
                       | int(Qt.TextFlag.TextDontClip), cap)

    def _blend(self, base_hex: str, over_hex: str, alpha: float) -> QColor:
        base, over = QColor(base_hex), QColor(over_hex)
        a = max(0.0, min(1.0, alpha))
        return QColor(
            int(base.red() * (1 - a) + over.red() * a),
            int(base.green() * (1 - a) + over.green() * a),
            int(base.blue() * (1 - a) + over.blue() * a),
        )
