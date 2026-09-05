"""Generate branded binary assets from the programmatic logo:

- assets/xmacro.ico              multi-size app icon (PNG-compressed ICO)
- installer/wizard_large*.bmp    Inno Setup side banner at 100/125/150/
                                 175/200% DPI (Inno 6 picks the closest,
                                 so text stays crisp instead of being
                                 stretched from the tiny 100% bitmap)
- installer/wizard_small*.bmp    Inno Setup header mark, same DPI set

Run after changing the logo or theme:  python tools/make_branding_assets.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPixmap

from ui.branding import make_logo
from ui.theme import get_theme

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def pixmap_png_bytes(pm: QPixmap) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    """Minimal ICO container with PNG-compressed entries (Vista+)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    blob = b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(png), offset)
        blob += png
        offset += len(png)
    path.write_bytes(header + entries + blob)


def main() -> None:
    app = QGuiApplication(sys.argv)
    theme = get_theme()

    # --- multi-size ICO
    images = [(s, pixmap_png_bytes(make_logo(theme, s, detailed=True)))
              for s in ICO_SIZES]
    ico_path = ROOT / "assets" / "xmacro.ico"
    write_ico(ico_path, images)
    print("wrote", ico_path)

    inst = ROOT / "installer"
    inst.mkdir(exist_ok=True)

    def banner(scale: float) -> QPixmap:
        """Side banner rendered NATIVELY at the given DPI scale — text is
        drawn at full size, never upscaled, so it stays sharp."""
        w, h = round(164 * scale), round(314 * scale)
        pm = QPixmap(w, h)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor(theme.bg))
        grad.setColorAt(1.0, QColor(theme.surface2))
        p.fillRect(pm.rect(), grad)
        accent = QLinearGradient(0, 0, w, 0)
        accent.setColorAt(0.0, QColor(theme.accent))
        accent.setColorAt(1.0, QColor(theme.accent2))
        p.fillRect(0, round(306 * scale), w, round(8 * scale), accent)
        logo_px = round(110 * scale)
        logo = make_logo(theme, logo_px, detailed=True)
        p.drawPixmap((w - logo_px) // 2, round(34 * scale), logo)

        def text(y, height, pt, color, family, s, bold=False):
            p.setPen(QColor(color))
            f = QFont(family, max(1, round(pt * scale)))
            if bold:
                f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(QRectF(6 * scale, y * scale, w - 12 * scale,
                              height * scale),
                       Qt.AlignmentFlag.AlignCenter
                       | Qt.TextFlag.TextWordWrap, s)

        text(160, 30, 13, theme.text, "Cascadia Mono",
             "XMacro-peater", bold=True)
        text(188, 20, 9, theme.accent, "Consolas", "by Xanonz")
        text(230, 60, 8, theme.text_dim, "Consolas",
             "record · replay\nkeyboard · mouse · controller")
        p.end()
        return pm

    def small_mark(scale: float) -> QPixmap:
        w, h = round(55 * scale), round(58 * scale)
        pm = QPixmap(w, h)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(pm.rect(), QColor(theme.bg))
        logo_px = round(48 * scale)
        logo = make_logo(theme, logo_px, detailed=True)
        p.drawPixmap((w - logo_px) // 2, round(5 * scale), logo)
        p.end()
        return pm

    # Inno Setup 6 multi-DPI sets: the 100% file keeps its classic name;
    # the rest are suffixed and listed comma-separated in the .iss
    scales = ((1.00, ""), (1.25, "_125"), (1.50, "_150"),
              (1.75, "_175"), (2.00, "_200"))
    for scale, suffix in scales:
        path = inst / f"wizard_large{suffix}.bmp"
        banner(scale).save(str(path), "BMP")
        print("wrote", path)
        path = inst / f"wizard_small{suffix}.bmp"
        small_mark(scale).save(str(path), "BMP")
        print("wrote", path)


if __name__ == "__main__":
    main()
