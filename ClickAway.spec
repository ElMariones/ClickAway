# Build on the target OS: python -m PyInstaller --noconfirm ClickAway.spec
import sys
from PyInstaller.utils.hooks import copy_metadata

is_mac = sys.platform == "darwin"
metadata = []
for package in ("cryptography", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
    metadata += copy_metadata(package)

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[("clickaway/assets", "clickaway/assets"), ("THIRD_PARTY_NOTICES.md", "."), ("LICENSE", ".")] + metadata,
    hiddenimports=["Quartz", "AppKit", "ApplicationServices"] if is_mac else [],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="ClickAway",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, icon="clickaway/assets/logo.icns" if is_mac else "clickaway/assets/logo.ico",
    target_arch="arm64" if is_mac else None,
    codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ClickAway")
if is_mac:
    app = BUNDLE(
        coll, name="ClickAway.app", icon="clickaway/assets/logo.icns",
        bundle_identifier="io.github.elmariones.clickaway",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "NSLocalNetworkUsageDescription": "ClickAway connects directly to your Windows PC to share its mouse and text clipboard.",
            "NSAccessibilityUsageDescription": "ClickAway uses Accessibility to move and click the mouse on your Mac when you cross from Windows.",
            "LSMinimumSystemVersion": "14.0",
        },
    )
