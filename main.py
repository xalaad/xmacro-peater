"""Input Macro Suite — entry point.

Run:  python main.py
Smoke test (offscreen, auto-quits): python main.py --smoke
"""
from __future__ import annotations

import logging
import sys
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from core.config import load_config, setup_logging
from ui.branding import make_icon, make_logo
from ui.main_window import MainWindow
from ui.theme import build_qss, get_theme

log = logging.getLogger("main")


def make_splash(theme, app_name: str) -> QSplashScreen:
    pm = QPixmap(460, 260)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, 460, 260)
    grad.setColorAt(0.0, QColor(theme.bg))
    grad.setColorAt(1.0, QColor(theme.surface2))
    p.fillRect(pm.rect(), grad)
    accent = QLinearGradient(0, 0, 460, 0)
    accent.setColorAt(0.0, QColor(theme.accent))
    accent.setColorAt(1.0, QColor(theme.accent2))
    p.fillRect(0, 252, 460, 8, accent)
    logo = make_logo(theme, 72, detailed=True)
    p.drawPixmap((460 - 72) // 2, 34, logo)
    p.setPen(QColor(theme.text))
    f = QFont("Cascadia Mono", 18, QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(pm.rect().adjusted(0, 60, 0, -40),
               Qt.AlignmentFlag.AlignCenter, app_name)
    p.setPen(QColor(theme.text_dim))
    p.setFont(QFont("Consolas", 10))
    p.drawText(pm.rect().adjusted(0, 130, 0, 0),
               Qt.AlignmentFlag.AlignCenter,
               "record · replay · keyboard · mouse · controller")
    p.end()
    return QSplashScreen(pm)


def install_excepthook(app: QApplication) -> None:
    """Never show the user a raw traceback — log it, dialog a summary."""

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.critical("Unhandled exception:\n%s", text)
        box = QMessageBox(
            QMessageBox.Icon.Critical, "Input Macro Suite — Error",
            f"Something went wrong:\n\n{exc}\n\n"
            "Details were written to logs/app.log.",
        )
        box.exec()

    sys.excepthook = hook


def main() -> int:
    smoke = "--smoke" in sys.argv
    if smoke:
        sys.argv = [a for a in sys.argv if a != "--smoke"]
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    setup_logging()
    cfg = load_config()
    theme = get_theme()

    app = QApplication(sys.argv)
    app.setApplicationName(cfg.branding.app_name)
    app.setWindowIcon(make_icon(theme))
    app.setStyleSheet(build_qss(theme))
    install_excepthook(app)

    splash = None
    if not smoke:
        splash = make_splash(theme, cfg.branding.app_name)
        splash.show()
        app.processEvents()

    window = MainWindow(cfg)

    def show():
        window.show()
        if splash is not None:
            splash.finish(window)

    QTimer.singleShot(450 if not smoke else 0, show)

    if smoke:
        QTimer.singleShot(2500, app.quit)
        rc = app.exec()
        print("SMOKE OK" if rc == 0 else f"SMOKE FAIL rc={rc}")
        return rc

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
