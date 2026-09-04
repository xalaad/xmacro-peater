"""Generate branded binary assets from the programmatic logo:

- assets/xmacro.ico            multi-size app icon (PNG-compressed ICO)
- installer/wizard_large.bmp   Inno Setup side banner (164x314)
- installer/wizard_small.bmp   Inno Setup header mark (55x58)

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

    # --- wizard side banner 164x314
    pm = QPixmap(164, 314)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, 164, 314)
    grad.setColorAt(0.0, QColor(theme.bg))
    grad.setColorAt(1.0, QColor(theme.surface2))
    p.fillRect(pm.rect(), grad)
    accent = QLinearGradient(0, 0, 164, 0)
    accent.setColorAt(0.0, QColor(theme.accent))
    accent.setColorAt(1.0, QColor(theme.accent2))
    p.fillRect(0, 306, 164, 8, accent)
    logo = make_logo(theme, 110, detailed=True)
    p.drawPixmap((164 - 110) // 2, 34, logo)
    p.setPen(QColor(theme.text))
    p.setFont(QFont("Cascadia Mono", 13, QFont.Weight.Bold))
    p.drawText(QRectF(0, 160, 164, 30), Qt.AlignmentFlag.AlignCenter,
               "XMacro-peater")
    p.setPen(QColor(theme.accent))
    p.setFont(QFont("Consolas", 9))
    p.drawText(QRectF(0, 188, 164, 20), Qt.AlignmentFlag.AlignCenter,
               "by Xanonz")
    p.setPen(QColor(theme.text_dim))
    p.setFont(QFont("Consolas", 7))
    p.drawText(QRectF(6, 230, 152, 60),
               Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
               "record · replay\nkeyboard · mouse · controller")
    p.end()
    pm.save(str(inst / "wizard_large.bmp"), "BMP")
    print("wrote", inst / "wizard_large.bmp")

    # --- wizard small mark 55x58
    pm = QPixmap(55, 58)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(pm.rect(), QColor(theme.bg))
    logo = make_logo(theme, 48, detailed=True)
    p.drawPixmap(3, 5, logo)
    p.end()
    pm.save(str(inst / "wizard_small.bmp"), "BMP")
    print("wrote", inst / "wizard_small.bmp")


if __name__ == "__main__":
    main()
