"""Draw the drag-to-Applications background for the Mac disk image.

Writes packaging/dmg-background.png and its @2x companion. Dev-only; run it after
changing the artwork: python scripts/make_dmg_background.py
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 640, 400
APP_ICON = (160, 190)  # Must match icon_locations in packaging/dmg_settings.py.
DROP_ICON = (480, 190)
BLUE, NAVY, MUTED, CREAM = "#2856e8", "#17326d", "#687590", "#fffbf1"


def font(size, weight=QFont.Weight.Normal):
    value = QFont("Outfit", size)
    value.setWeight(weight)
    return value


def text(p, center_y, message, color, size, weight=QFont.Weight.Normal):
    p.setFont(font(size, weight))
    p.setPen(QColor(color))
    p.drawText(
        QRectF(24, center_y - 18, WIDTH - 48, 36),
        Qt.AlignmentFlag.AlignCenter,
        message,
    )


def render(scale):
    image = QImage(WIDTH * scale, HEIGHT * scale, QImage.Format.Format_ARGB32)
    image.fill(QColor(CREAM))
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(scale, scale)

    # Soft discs behind the two icon slots, echoing the desk illustration.
    p.setPen(Qt.PenStyle.NoPen)
    for center, color in ((APP_ICON, "#fff4d0"), (DROP_ICON, "#e6edff")):
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(*center), 80, 80)
    # A dashed ring marks where the app is meant to land.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#9db5ff"), 2, Qt.PenStyle.DashLine))
    p.drawEllipse(QPointF(*DROP_ICON), 86, 86)

    text(p, 62, "Drag ClickAway to Applications", NAVY, 25, QFont.Weight.DemiBold)
    text(p, 96, "Then open it from your Applications folder.", MUTED, 14)

    start, end = APP_ICON[0] + 96, DROP_ICON[0] - 100
    middle = APP_ICON[1]
    p.setPen(
        QPen(
            QColor(BLUE),
            6,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
    )
    p.drawLine(QPointF(start, middle), QPointF(end - 10, middle))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BLUE))
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(end + 12, middle),
                QPointF(end - 12, middle - 13),
                QPointF(end - 12, middle + 13),
            ]
        )
    )

    text(
        p,
        352,
        "If macOS blocks the first launch, allow ClickAway in Privacy & Security.",
        MUTED,
        12,
    )
    p.end()
    return image


app = QApplication([])
QFontDatabase.addApplicationFont(str(ROOT / "clickaway" / "assets" / "Outfit.ttf"))
output = ROOT / "packaging"
output.mkdir(parents=True, exist_ok=True)
for scale, name in ((1, "dmg-background.png"), (2, "dmg-background@2x.png")):
    image = render(scale)
    image.save(str(output / name))
    print(f"Wrote {name}: {image.width()} x {image.height()}")
