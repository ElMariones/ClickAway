"""Render our SVG logo to platform icon formats. Dev-only: requires Pillow."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import Qt
from PIL import Image

root = Path(__file__).resolve().parents[1] / "clickaway" / "assets"
app = QApplication([])
renderer = QSvgRenderer(str(root / "logo.svg"))
image = QImage(1024, 1024, QImage.Format.Format_RGBA8888)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
renderer.render(painter)
painter.end()
bitmap = Image.frombytes("RGBA", (1024, 1024), image.bits().tobytes())
bitmap.save(
    root / "logo.ico",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
bitmap.save(root / "logo.icns")
