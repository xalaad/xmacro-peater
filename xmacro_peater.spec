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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XMacro-peater",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/xmacro.ico",
)
