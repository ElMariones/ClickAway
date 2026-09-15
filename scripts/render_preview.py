"""Render the actual Qt widgets without connecting to either computer."""

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from clickaway.app import App, ASSETS
from clickaway.widgets import STYLES, font

app = QApplication([])
QFontDatabase.addApplicationFont(str(ASSETS / "Outfit.ttf"))
app.setFont(font(11))
app.setStyle("Fusion")
app.setStyleSheet(STYLES)
output = Path("docs/images")
output.mkdir(parents=True, exist_ok=True)
for mac in (False, True):
    window = App(preview=True, preview_mac=mac)
    window.resize(1000, 850 if mac else 800)
    window.show()
    app.processEvents()
    name = "mac" if mac else "windows"
    window.grab().save(str(output / f"{name}.png"))
    print(f"Rendered {name}: {window.width()} x {window.height()}")
    window.close()
