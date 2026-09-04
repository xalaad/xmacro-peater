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
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
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
