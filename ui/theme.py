"""Color tokens + QSS stylesheet builder, dark (default) and light themes.

Widgets that custom-paint pull colors from THEME tokens; everything
stock-Qt is styled by the generated QSS.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    text_dim: str
    accent: str          # primary accent (gradient start)
    accent2: str         # gradient end
    success: str
    danger: str
    warning: str
    kb: str              # activity color: keyboard
    mouse: str           # activity color: mouse
    pad: str             # activity color: controller


# Elegant dark: green primary, olive secondary, on warm graphite-green
# surfaces. Accent gradient runs emerald -> olive.
DARK = Theme(
    name="dark",
    bg="#0d100e",
    surface="#141a16",
    surface2="#1c241d",
    border="#2c3a2f",
    text="#e6ede7",
    text_dim="#8fa294",
    accent="#35c26e",
    accent2="#a8b23f",
    success="#3ddc84",
    danger="#ff5470",
    warning="#ffb454",
    kb="#4cc2ff",
    mouse="#57d998",
    pad="#b07cff",
)

def get_theme(_name: str | None = None) -> Theme:
    """Dark only — light mode was removed by design."""
    return DARK


MONO = "'Cascadia Mono', Consolas, monospace"
MDL2 = "'Segoe MDL2 Assets'"


def build_qss(t: Theme) -> str:
    try:
        from .qss_assets import ensure_assets
        assets = ensure_assets(t)
    except Exception:  # no QGuiApplication yet (headless tests)
        assets = {"check": "", "chevron": "", "chevron_accent": ""}
    return f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; }}

QMainWindow, QDialog, QWidget#testerWindow {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; font-size: 13px; }}

QLabel#appTitle {{
    font-family: {MONO};
    font-size: 15px; font-weight: 700; letter-spacing: 0.5px;
    color: {t.accent};
}}
QLabel#dim {{ color: {t.text_dim}; font-size: 11px; }}
QLabel#statsLabel {{
    color: {t.text_dim}; font-size: 11px; font-family: {MONO};
}}

QWidget#titleBar {{
    background: {t.surface};
    border-bottom: 1px solid {t.border};
}}

QFrame#dialogFrame {{
    background: {t.bg};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame#dialogFrame QWidget#titleBar {{
    background: transparent;
    border-bottom: 1px solid {t.border};
}}
QPushButton#winBtn, QPushButton#winClose {{
    background: transparent; border: none; border-radius: 0;
    color: {t.text_dim}; padding: 0;
    font-family: {MDL2}; font-size: 10px;
}}
QPushButton#winBtn:hover {{ background: {t.surface2}; color: {t.text}; }}
QPushButton#winClose:hover {{ background: {t.danger}; color: white; }}

QPushButton#rowBtn {{
    background: transparent; border: none; border-radius: 5px;
    color: {t.text_dim}; padding: 0;
    font-family: {MDL2}; font-size: 11px;
}}
QPushButton#rowBtn:hover {{ background: {t.surface2}; color: {t.accent}; }}

QPushButton#titleIconBtn {{
    background: transparent; border: none; border-radius: 7px;
    color: {t.accent}; padding: 0;
    font-family: {MDL2}; font-size: 13px;
}}
QPushButton#titleIconBtn:hover {{
    background: {t.surface2}; color: {t.accent2};
}}

QPushButton#accentBtn {{
    background: transparent; color: {t.accent};
    border: 1px solid {t.accent}; border-radius: 7px;
    padding: 4px 12px;
    font-family: {MONO}; font-size: 11px; font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#accentBtn:hover {{
    background: {t.accent}; color: {t.bg};
}}

QPushButton#collapseStrip {{
    background: {t.surface}; border: none; border-radius: 6px;
    color: {t.text_dim}; padding: 0;
    font-family: {MDL2}; font-size: 9px;
}}
QPushButton#collapseStrip:hover {{
    background: {t.surface2}; color: {t.accent};
}}

QPushButton#linkBtn {{
    background: {t.surface2}; color: {t.text_dim};
    border: 1px solid {t.border}; border-radius: 21px;
    font-family: {MDL2}; font-size: 16px; padding: 0;
}}
QPushButton#linkBtn:hover {{
    border-color: {t.accent}; color: {t.accent};
    background: {t.surface};
}}

QPushButton#overlayBtn {{
    background: {t.surface2}; color: {t.text};
    border: 1px solid {t.border}; border-radius: 6px;
    font-family: {MDL2}; font-size: 12px; padding: 0;
}}
QPushButton#overlayBtn:hover {{ border-color: {t.accent}; color: {t.accent}; }}
QPushButton#overlayBtn:disabled {{ color: {t.text_dim}; }}

QPushButton#sectionToggle {{
    background: transparent; border: none; padding: 2px 4px;
    color: {t.accent}; font-family: {MONO};
    font-size: 12px; font-weight: 700; letter-spacing: 1px;
    text-align: left;
}}
QPushButton#sectionToggle:hover {{ color: {t.accent2}; }}

QLabel#sectionTitle {{
    color: {t.accent}; font-family: {MONO};
    font-size: 12px; font-weight: 700; letter-spacing: 1px;
}}

QFrame#panel, QFrame#card {{
    background: {t.surface};
    border: none;
    border-radius: 10px;
}}

QPushButton {{
    background: {t.surface2};
    border: 1px solid {t.border};
    border-radius: 7px;
    padding: 5px 12px;
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.text_dim}; border-color: {t.border}; }}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t.accent}, stop:1 {t.accent2});
    border: none;
    color: white;
    padding: 8px 16px;
    font-size: 13px;
    border-radius: 8px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t.accent2}, stop:1 {t.accent});
}}
QPushButton#primary:disabled {{ background: {t.surface2}; color: {t.text_dim}; }}

QPushButton#danger {{
    background: {t.danger}; border: none; color: white; border-radius: 8px;
    padding: 8px 16px; font-size: 13px;
}}
QPushButton#danger:disabled {{ background: {t.surface2}; color: {t.text_dim}; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {t.surface2};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 5px 10px;
    selection-background-color: {t.accent};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:focus {{
    border-color: {t.accent};
}}

/* Number inputs: no stepper arrows — type, scroll, or use arrow keys */
QAbstractSpinBox {{ qproperty-buttonSymbols: NoButtons; }}
QSpinBox, QDoubleSpinBox {{
    padding: 5px 10px; min-width: 52px;
    font-family: {MONO}; font-size: 12px;
}}

QComboBox {{ padding-right: 24px; }}
QComboBox:on {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{assets['chevron']}"); }}
QComboBox::down-arrow:on {{ image: url("{assets['chevron_accent']}"); }}
QComboBox QAbstractItemView {{
    background: {t.surface2};
    border: 1px solid {t.accent};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: transparent;
}}
QComboBox QAbstractItemView::item {{
    border-radius: 6px; padding: 6px 10px; margin: 1px 2px;
    color: {t.text};
}}
QComboBox QAbstractItemView::item:selected,
QComboBox QAbstractItemView::item:hover {{
    background: {t.surface}; color: {t.accent};
}}

QListWidget {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    border-radius: 6px;
    padding: 5px 8px;
    margin: 1px 2px;
    color: {t.text};
}}
QListWidget::item:selected {{
    background: {t.surface2};
    border: 1px solid {t.accent};
    color: {t.text};
}}
QListWidget::item:hover {{ background: {t.surface2}; color: {t.text}; }}
QListWidget QLineEdit {{
    background: {t.surface2}; color: {t.text};
    border: 1px solid {t.accent}; border-radius: 6px;
    padding: 3px 6px;
    selection-background-color: {t.accent};
}}

QSlider::groove:horizontal {{
    height: 5px; background: {t.surface2}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t.accent}, stop:1 {t.accent2});
}}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}

QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {t.border}; }}
QTabWidget::tab-bar {{ left: 2px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {t.text_dim};
    padding: 7px 18px; font-weight: 600;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabBar::tab:hover:!selected {{ color: {t.text}; }}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.border}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; }}

QToolTip {{
    background: {t.surface2}; color: {t.text};
    border: 1px solid {t.accent}; border-radius: 6px; padding: 5px 8px;
}}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid {t.border}; background: {t.surface2};
}}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{
    background: {t.accent}; border-color: {t.accent};
    image: url("{assets['check']}");
}}

QGroupBox {{
    border: 1px solid {t.border}; border-radius: 10px;
    margin-top: 10px; padding-top: 8px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}

/* Kill every white-background leak in scroll areas and dialogs */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea {{ background: {t.surface}; }}
QListWidget, QListView {{ background: {t.surface}; }}
QMessageBox, QInputDialog {{ background: {t.bg}; }}

QProgressBar {{
    background: {t.surface2}; border: none; border-radius: 3px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {t.accent}, stop:1 {t.accent2});
    border-radius: 3px;
}}

QLabel#helpMark {{
    color: {t.accent}; font-weight: 700; font-family: {MONO};
}}
"""
