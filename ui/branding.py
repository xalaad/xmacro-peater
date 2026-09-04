"""App identity: programmatic logo (controller + record dot), About dialog,
and the footer bar. Name/author/links come from AppConfig.branding.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtCore import QSize
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

# Official brand marks (Simple Icons paths) + an envelope, rendered from
# inline SVG in light/white so they read cleanly on the dark buttons.
_GITHUB_PATH = (
    "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6"
    ".113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61"
    "-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729"
    ".084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 "
    "3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93"
    " 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005"
    "-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 "
    "2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 "
    "1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 "
    "2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092"
    " 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
)
_TELEGRAM_PATH = (
    "M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 "
    "12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465."
    "14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 "
    "6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9"
    "-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91"
    ".177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174"
    "-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49"
    "-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297"
    "-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 "
    "3.332-1.386 4.025-1.627 4.476-1.635z"
)
_MAIL_PATH = (
    "M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6"
    "c0-1.1-.9-2-2-2zm0 4-8 5-8-5V6l8 5 8-5v2z"
)


def _brand_icon(path_d: str, size: int = 22,
                color: str = "#f4f6f4") -> QIcon:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path fill="{color}" d="{path_d}"/></svg>')
    renderer = QSvgRenderer(svg.encode("utf-8"))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    return QIcon(pm)

from core.config import APP_VERSION, AppConfig

from .dialogs import FramelessDialog
from .theme import Theme


def make_logo(theme: Theme, size: int = 128, detailed: bool = True) -> QPixmap:
    """Controller silhouette with a record dot at its bottom-right.

    detailed=True: gradient body + glow (About dialog / splash).
    detailed=False: flat minimal mark (title bar / window icon).
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 128.0

    body = QRectF(10 * s, 34 * s, 100 * s, 52 * s)
    if detailed:
        grad = QLinearGradient(body.topLeft(), body.bottomRight())
        grad.setColorAt(0.0, QColor(theme.accent))
        grad.setColorAt(1.0, QColor(theme.accent2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.accent))
    # body with grips
    p.drawRoundedRect(body, 26 * s, 26 * s)
    p.drawEllipse(QRectF(6 * s, 40 * s, 42 * s, 52 * s))
    p.drawEllipse(QRectF(72 * s, 40 * s, 42 * s, 52 * s))

    # face details carved in bg-transparent "holes"
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.drawEllipse(QRectF(28 * s, 50 * s, 17 * s, 17 * s))   # left stick
    p.drawEllipse(QRectF(80 * s, 48 * s, 9 * s, 9 * s))     # Y
    p.drawEllipse(QRectF(90 * s, 58 * s, 9 * s, 9 * s))     # B
    p.drawEllipse(QRectF(70 * s, 58 * s, 9 * s, 9 * s))     # X
    p.drawEllipse(QRectF(80 * s, 68 * s, 9 * s, 9 * s))     # A
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    # record badge, bottom-right
    cx, cy, r = 98 * s, 96 * s, 22 * s
    if detailed:
        glow = QRadialGradient(cx, cy, r * 1.6)
        halo = QColor(theme.danger)
        halo.setAlphaF(0.45)
        glow.setColorAt(0.0, halo)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(glow)
        p.drawEllipse(QRectF(cx - r * 1.6, cy - r * 1.6, r * 3.2, r * 3.2))
    p.setBrush(QColor(theme.bg))
    p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
    p.setBrush(QColor(theme.danger))
    rr = r * 0.62
    p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))
    p.end()
    return pm


def make_icon(theme: Theme) -> QIcon:
    return QIcon(make_logo(theme, 256, detailed=True))


class AboutDialog(FramelessDialog):
    def __init__(self, cfg: AppConfig, theme: Theme, parent=None):
        super().__init__("About", parent)
        b = cfg.branding
        self.setFixedWidth(360)

        lay = self.body
        lay.setContentsMargins(26, 18, 26, 20)
        lay.setSpacing(6)

        logo = QLabel()
        logo.setPixmap(make_logo(theme, 96, detailed=True))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(logo)

        name = QLabel(b.app_name)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name.setStyleSheet(
            "font-family: 'Cascadia Mono', Consolas, monospace;"
            "font-size: 18px; font-weight: 700;"
        )
        lay.addWidget(name)

        version = QLabel(f"v{APP_VERSION}  ·  by {b.author}")
        version.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        version.setStyleSheet(
            f"color: {theme.accent}; font-family: Consolas, monospace;"
            "font-size: 12px; font-weight: 600;"
        )
        lay.addWidget(version)

        lay.addSpacing(8)
        desc = QLabel(b.description)
        desc.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color: {theme.text_dim}; font-size: 12px; line-height: 150%;")
        lay.addWidget(desc)
        lay.addSpacing(12)

        # Social icon links (real brand marks, light), centered at the bottom
        links = [
            (_GITHUB_PATH, "GitHub", b.github),
            (_TELEGRAM_PATH, "Telegram", b.telegram),
            (_MAIL_PATH, "Email", f"mailto:{b.email}" if b.email else ""),
        ]
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addStretch(1)
        for path_d, label, url in links:
            if not url:
                continue
            btn = QPushButton()
            btn.setObjectName("linkBtn")
            btn.setFixedSize(42, 42)
            btn.setIcon(_brand_icon(path_d))
            btn.setIconSize(QSize(22, 22))
            btn.setToolTip(f"{label} - {url}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)


class FooterBar(QWidget):
    """Slim bottom bar: clickable branding left, version right."""

    def __init__(self, cfg: AppConfig, theme: Theme, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self.setFixedHeight(24)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 2, 12, 2)

        mono = "font-family: Consolas, monospace; font-size: 11px;"
        self.brand = QLabel(f"© {cfg.branding.author}")
        self.brand.setStyleSheet(
            f"{mono} color: {theme.text_dim};")
        self.brand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brand.setToolTip("About this app")
        lay.addWidget(self.brand)
        lay.addStretch(1)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet(f"{mono} color: {theme.text_dim};")
        lay.addWidget(ver)

    def mousePressEvent(self, event) -> None:
        AboutDialog(self.cfg, self.theme, self.window()).exec()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        mono = "font-family: Consolas, monospace; font-size: 11px;"
        for w in self.findChildren(QLabel):
            w.setStyleSheet(f"{mono} color: {theme.text_dim};")
