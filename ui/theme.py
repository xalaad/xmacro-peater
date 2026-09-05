"""Color tokens + QSS stylesheet builder (dark only — by design).

Widgets that custom-paint pull colors from THEME tokens; everything
stock-Qt is styled by the generated QSS. `_mix` blends tokens so hover /
depth tints always derive from the palette instead of hard-coded hexes.
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


# Elegant dark: emerald primary, olive secondary, on deep graphite-green
# surfaces. Accent gradient runs emerald -> olive.
DARK = Theme(
    name="dark",
    bg="#0a0f0c",
    surface="#111813",
    surface2="#1a231c",
    border="#2b3a2e",
    text="#e9f2ea",
    text_dim="#8ba390",
    accent="#3ddf7e",
    accent2="#b8c34a",
    success="#3ddc84",
    danger="#ff4d6d",
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


def _mix(a: str, b: str, f: float) -> str:
    """Blend hex color a toward b by fraction f (0..1)."""
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{round(x + (y - x) * f):02x}" for x, y in zip(av, bv))


def build_qss(t: Theme) -> str:
    try:
        from .qss_assets import ensure_assets
        assets = ensure_assets(t)
    except Exception:  # no QGuiApplication yet (headless tests)
        assets = {"check": "", "chevron": "", "chevron_accent": ""}

    # Derived tints — every shade comes from the palette
    well = _mix(t.bg, "#000000", 0.25)          # input wells sit deepest
    panel_hi = _mix(t.surface, "#ffffff", 0.02)  # panel top sheen
    lift = _mix(t.surface2, "#ffffff", 0.05)     # hover lift
    hair = _mix(t.border, t.bg, 0.45)            # hairline edges
    accent_dim = _mix(t.accent, t.bg, 0.45)      # quiet accent lines
    accent_hot = _mix(t.accent, "#ffffff", 0.18)  # hover accent
    danger_hot = _mix(t.danger, "#ffffff", 0.15)

    return f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; }}

QMainWindow, QDialog, QWidget#testerWindow {{ background: {t.bg}; }}
QMainWindow {{ border: 1px solid {hair}; }}
QWidget {{
    color: {t.text}; font-size: 13px;
    selection-background-color: {t.accent}; selection-color: {t.bg};
}}

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
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi}, stop:1 {t.surface});
    border-bottom: 1px solid {_mix(t.border, t.accent, 0.16)};
}}

QFrame#dialogFrame {{
    background: {t.bg};
    border: 1px solid {_mix(t.border, t.accent, 0.22)};
    border-radius: 14px;
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
    background: transparent; border: none; border-radius: 6px;
    color: {t.text_dim}; padding: 0;
    font-family: {MDL2}; font-size: 11px;
}}
QPushButton#rowBtn:hover {{ background: {lift}; color: {t.accent}; }}
QPushButton#rowBtn:pressed {{ background: {t.border}; }}

QPushButton#titleIconBtn {{
    background: transparent; border: none; border-radius: 8px;
    color: {t.accent}; padding: 0;
    font-family: {MDL2}; font-size: 13px;
}}
QPushButton#titleIconBtn:hover {{
    background: {t.surface2}; color: {accent_hot};
}}

QPushButton#accentBtn {{
    background: transparent; color: {t.accent};
    border: 1px solid {accent_dim}; border-radius: 8px;
    padding: 4px 13px;
    font-family: {MONO}; font-size: 11px; font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#accentBtn:hover {{
    background: {t.accent}; border-color: {t.accent}; color: {t.bg};
}}
QPushButton#accentBtn:pressed {{ background: {accent_dim}; }}

QPushButton#recordStepBtn, QPushButton#dangerOutline {{
    background: transparent; color: {t.danger};
    border: 1px solid {_mix(t.danger, t.bg, 0.45)}; border-radius: 8px;
    padding: 4px 13px;
    font-family: {MONO}; font-size: 11px; font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#recordStepBtn:hover, QPushButton#dangerOutline:hover {{
    background: {t.danger}; border-color: {t.danger}; color: white;
}}

QPushButton#collapseStrip {{
    background: transparent; border: none; border-radius: 6px;
    color: {t.text_dim}; padding: 0;
    font-family: {MDL2}; font-size: 10px;
}}
QPushButton#collapseStrip:hover {{ background: {t.surface}; color: {t.accent}; }}

QPushButton#linkBtn {{
    background: {t.surface2}; color: {t.text_dim};
    border: 1px solid {hair}; border-radius: 21px;
    font-family: {MDL2}; font-size: 16px; padding: 0;
}}
QPushButton#linkBtn:hover {{
    border-color: {t.accent}; color: {t.accent};
    background: {t.surface};
}}

QPushButton#overlayBtn {{
    background: {t.surface2}; color: {t.text};
    border: 1px solid {hair}; border-radius: 7px;
    font-family: {MDL2}; font-size: 12px; padding: 0;
}}
QPushButton#overlayBtn:hover {{
    border-color: {t.accent}; color: {t.accent}; background: {lift};
}}
QPushButton#overlayBtn:disabled {{ color: {t.text_dim}; }}

QPushButton#sectionToggle {{
    background: transparent; border: none; padding: 2px 4px;
    color: {t.accent}; font-family: {MONO};
    font-size: 12px; font-weight: 700; letter-spacing: 1px;
    text-align: left;
}}
QPushButton#sectionToggle:hover {{ color: {accent_hot}; }}

QLabel#sectionTitle {{
    color: {t.accent}; font-family: {MONO};
    font-size: 12px; font-weight: 700; letter-spacing: 1.2px;
}}

QPushButton#deckTab {{
    background: transparent; border: none;
    border-bottom: 2px solid transparent; border-radius: 0;
    color: {t.text_dim}; padding: 3px 2px 5px 2px;
    font-family: {MONO}; font-size: 12px; font-weight: 700;
    letter-spacing: 1.2px;
}}
QPushButton#deckTab:hover {{
    color: {t.text}; border-bottom: 2px solid {t.border};
}}
QPushButton#deckTab:checked {{
    color: {t.accent}; border-bottom: 2px solid {t.accent};
}}

QFrame#panel, QFrame#card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi}, stop:1 {t.surface});
    border: 1px solid {hair};
    border-radius: 14px;
}}

QPushButton {{
    background: {t.surface2};
    border: 1px solid {_mix(t.border, "#ffffff", 0.05)};
    border-radius: 9px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {t.accent}; background: {lift}; }}
QPushButton:pressed {{ background: {well}; border-color: {accent_dim}; }}
QPushButton:focus {{ border-color: {t.accent}; outline: none; }}
QPushButton:disabled {{ color: {t.text_dim}; border-color: transparent; }}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t.accent}, stop:1 {t.accent2});
    border: none;
    color: {t.bg};
    padding: 8px 14px;
    font-size: 13px; font-weight: 700; letter-spacing: 0.3px;
    border-radius: 9px;
    text-align: left;  /* dim hotkey tag docks at the right edge */
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {accent_hot}, stop:1 {_mix(t.accent2, "#ffffff", 0.15)});
}}
QPushButton#primary:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_mix(t.accent, t.bg, 0.2)}, stop:1 {_mix(t.accent2, t.bg, 0.2)});
}}
QPushButton#primary:disabled {{ background: {t.surface2}; color: {t.text_dim}; }}

QPushButton#danger {{
    background: {t.danger}; border: none; color: white; border-radius: 9px;
    padding: 8px 14px; font-size: 13px; font-weight: 700;
    letter-spacing: 0.3px;
    text-align: left;
}}
QPushButton#danger:hover {{ background: {danger_hot}; }}
QPushButton#danger:pressed {{ background: {_mix(t.danger, t.bg, 0.25)}; }}
QPushButton#danger:disabled {{ background: {t.surface2}; color: {t.text_dim}; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {well};
    border: 1px solid {_mix(t.border, "#ffffff", 0.04)};
    border-radius: 9px;
    padding: 6px 10px;
    selection-background-color: {t.accent};
    selection-color: {t.bg};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {t.text_dim};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {t.accent};
    background: {t.surface2};
}}

QLineEdit#durationField {{
    font-family: {MONO}; font-size: 12px;
}}
QLineEdit[invalid="true"] {{
    border-color: {t.danger}; color: {t.danger};
}}

/* Anchored popup panels (duration picker) */
QFrame#popupPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi}, stop:1 {t.surface});
    border: 1px solid {accent_dim};
    border-radius: 13px;
}}
QLabel#popupColTitle {{
    color: {t.text_dim}; font-family: {MONO};
    font-size: 10px; font-weight: 700; letter-spacing: 1.2px;
}}
QFrame#headSep {{ background: {t.border}; border: none; }}

/* Sequence-builder step rows: always mini-cards; the dragged one lifts */
QLabel#dragGrip {{
    color: {t.text_dim}; font-family: {MDL2}; font-size: 11px;
}}
QLabel#dragGrip:hover {{ color: {t.accent}; }}
QWidget#stepRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
}}
QWidget#stepRow[dragging="true"] {{
    background: {lift};
    border: 1px solid {t.accent};
    border-radius: 10px;
}}

/* Deck cards: icon chip + name + metadata line */
QLabel#cardChip {{
    background: {_mix(t.accent, t.bg, 0.86)};
    color: {t.accent};
    border: 1px solid {_mix(t.accent, t.bg, 0.68)};
    border-radius: 8px;
    font-family: {MDL2}; font-size: 13px;
}}
QLabel#cardMeta {{
    color: {t.text_dim}; font-family: {MONO}; font-size: 10px;
}}

/* Number inputs: no stepper arrows — type, scroll, or use arrow keys */
QAbstractSpinBox {{ qproperty-buttonSymbols: NoButtons; }}
QSpinBox, QDoubleSpinBox {{
    padding: 6px 10px; min-width: 52px;
    font-family: {MONO}; font-size: 12px;
}}

QComboBox {{ padding-right: 24px; }}
QComboBox:on {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{assets['chevron']}"); }}
QComboBox::down-arrow:on {{ image: url("{assets['chevron_accent']}"); }}
QComboBox QAbstractItemView {{
    background: {t.surface2};
    border: 1px solid {accent_dim};
    border-radius: 10px;
    padding: 5px;
    outline: none;
    selection-background-color: transparent;
}}
QComboBox QAbstractItemView::item {{
    border-radius: 7px; padding: 7px 10px; margin: 1px 2px;
    color: {t.text};
}}
QComboBox QAbstractItemView::item:selected,
QComboBox QAbstractItemView::item:hover {{
    background: {t.surface}; color: {t.accent};
}}

QListWidget {{
    background: {t.surface};
    border: 1px solid {hair};
    border-radius: 12px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    border-radius: 7px;
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
    border: 1px solid {t.accent}; border-radius: 7px;
    padding: 3px 6px;
    selection-background-color: {t.accent};
}}

/* Recordings / sequences deck: card rows — spaced, hover lift, accent
   bar on the selected card, same look with or without keyboard focus */
QListWidget#recList {{
    background: transparent; border: none; padding: 2px; outline: 0;
}}
QListWidget#recList::item {{
    background: {t.surface2};
    border: 1px solid {_mix(t.border, t.bg, 0.55)};
    border-radius: 10px;
    margin: 3px 2px;
    padding: 0;
    color: {t.text};
    outline: 0;
}}
QListWidget#recList::item:hover {{
    background: {lift};
    border-color: {t.border};
}}
QListWidget#recList::item:selected,
QListWidget#recList::item:selected:!active {{
    background: {_mix(t.surface2, t.accent, 0.06)};
    border: 1px solid {t.accent};
    border-left: 3px solid {t.accent};
    color: {t.text};
}}
QListWidget#recList::item:selected:hover {{
    background: {lift};
    border-color: {accent_hot};
    border-left: 3px solid {accent_hot};
}}

QSlider::groove:horizontal {{
    height: 5px; background: {well}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t.accent}, stop:1 {t.accent2});
}}
QSlider::handle:horizontal:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {accent_hot}, stop:1 {t.accent2});
}}
QSlider::sub-page:horizontal {{ background: {accent_dim}; border-radius: 2px; }}

QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {hair}; }}
QTabWidget::tab-bar {{ left: 2px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {t.text_dim};
    padding: 8px 22px; font-weight: 600;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabBar::tab:hover:!selected {{
    color: {t.text}; border-bottom: 2px solid {t.border};
}}

QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.border}; border-radius: 3px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {t.border}; border-radius: 3px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.accent}; }}

QToolTip {{
    background: {_mix(t.surface2, t.bg, 0.3)}; color: {t.text};
    border: 1px solid {accent_dim}; border-radius: 8px; padding: 6px 10px;
}}

QMenu {{
    background: {t.surface2}; color: {t.text};
    border: 1px solid {accent_dim}; border-radius: 10px; padding: 5px;
}}
QMenu::item {{
    background: transparent; color: {t.text};
    border-radius: 6px; padding: 6px 22px 6px 12px;
}}
QMenu::item:selected {{ background: {t.accent}; color: {t.bg}; }}
QMenu::item:disabled {{ color: {t.text_dim}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}

/* Dim hotkey tags living on the action buttons */
QLabel#btnHotkey {{
    background: transparent; border: none;
    color: rgba(255, 255, 255, 150);
    font-family: {MONO}; font-size: 9px;
}}
QLabel#btnHotkeyDark {{
    background: transparent; border: none;
    color: rgba(0, 10, 5, 130);
    font-family: {MONO}; font-size: 9px;
}}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border-radius: 5px;
    border: 1px solid {t.border}; background: {well};
}}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{
    background: {t.accent}; border-color: {t.accent};
    image: url("{assets['check']}");
}}

QGroupBox {{
    border: 1px solid {hair}; border-radius: 12px;
    margin-top: 10px; padding-top: 8px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}

/* Kill every white-background leak in scroll areas and dialogs.
   NOTE: equal-specificity type selectors resolve by ORDER in Qt — the
   plain-QScrollArea transparency must come AFTER the QAbstractScrollArea
   surface rule, or scroll hosts (settings, sequence steps) paint a
   visible slab behind their content. */
QAbstractScrollArea {{ background: {t.surface}; }}
QListWidget, QListView {{ background: {t.surface}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QMessageBox, QInputDialog {{ background: {t.bg}; }}

QProgressBar {{
    background: {well}; border: none; border-radius: 3px;
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
