# PyInstaller spec — build a single-file exe:
#   pyinstaller xmacro_peater.spec
# Requires: pip install pyinstaller

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("assets/*.svg", "assets"),
        ("config/schemes/*.json", "config/schemes"),
        # vgamepad's ViGEmClient.dll + the bundled ViGEmBus installer MSI,
        # so the exe can offer the driver install on first controller use
        *collect_data_files("vgamepad"),
    ],
    hiddenimports=["pynput.keyboard._win32", "pynput.mouse._win32",
                   "vgamepad"],
    excludes=["tkinter", "pytest",
              # Qt modules the app never uses — Widgets-only UI
              "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtPdf", "PySide6.QtOpenGL",
              "PySide6.QtOpenGLWidgets"],
    noarchive=False,
)

# PyInstaller bundles whatever PySide6 ships, not what we import — the
# QML/Quick/Pdf/Network stacks, the 20MB software-OpenGL rasterizer and
# 96 translation files ride along for nothing. Pruning them cuts the
# app from ~136MB to ~85MB with zero feature loss. KEEP: Qt6Svg +
# qsvgicon (all icons are SVG), qico (window icon), qwindows
# (the platform), qoffscreen (--smoke), styles, MSVC runtimes.
_PRUNE = (
    "opengl32sw",                     # software GL: app never uses OpenGL
    "qt6quick", "qt6qml",             # QML stack (matches Qt6Qml* too)
    "qtquick", "qtqml",
    "qt6pdf", "imageformats\\qpdf",   # PDF rendering
    "qt6opengl",
    "qt6network", "qtnetwork",        # no networking in the app
    "networkinformation\\", "tls\\",
    "qt6virtualkeyboard", "qtvirtualkeyboardplugin",
    "platforms\\qdirect2d",           # alt platform; qwindows is the one
    "generic\\qtuiotouchplugin",      # TUIO-over-UDP touch, not Windows touch
    "translations\\",                 # English-only UI
    # image formats never loaded (icons are svg/ico)
    "imageformats\\qgif", "imageformats\\qicns", "imageformats\\qjpeg",
    "imageformats\\qtga", "imageformats\\qtiff", "imageformats\\qwbmp",
    "imageformats\\qwebp",
)


def _keep(entry):
    name = entry[0].lower().replace("/", "\\")
    return not any(p in name for p in _PRUNE)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)

# One-DIR on purpose: one-file bootloaders are the most heuristic-flagged
# binary format around (self-extracting stub), while a plain app folder
# passes AV/SmartScreen far cleaner and starts faster. The installer ships
# the folder; the portable zip contains it.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XMacro-peater",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/xmacro.ico",
    version="version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="XMacro-peater",
)
