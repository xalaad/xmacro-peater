"""Tiny runtime-generated images referenced by the stylesheet (Qt QSS can
only load images from files): the checkbox checkmark and the combo-box
chevron, tinted per theme. Written once per theme into config/ui_cache/.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

from core.config import CONFIG_DIR

from .theme import Theme

CACHE_DIR = CONFIG_DIR / "ui_cache"


def _save(pm: QPixmap, name: str) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    pm.save(str(path), "PNG")
    return path.as_posix()


def ensure_assets(t: Theme) -> dict[str, str]:
    """Returns {'check': path, 'chevron': path, 'chevron_accent': path}."""
    # White checkmark (drawn over the accent-filled indicator)
    pm = QPixmap(12, 12)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("white"), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(2.2, 6.4), QPointF(5.0, 9.2),
                              QPointF(9.8, 3.0)]))
    p.end()
    check = _save(pm, "check.png")

    def chevron(color: str, name: str) -> str:
        pm = QPixmap(10, 6)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(1.2, 1.2), QPointF(5.0, 4.8),
                                  QPointF(8.8, 1.2)]))
        p.end()
        return _save(pm, name)

    return {
        "check": check,
        "chevron": chevron(t.text_dim, f"chevron_{t.name}.png"),
        "chevron_accent": chevron(t.accent, f"chevron_accent_{t.name}.png"),
    }
