"""ClickAway's blue-and-butter desktop visual system."""

from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
    QPropertyAnimation,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton, QWidget

BLUE = "#2856e8"
NAVY = "#17326d"
BUTTER = "#ffda69"
CREAM = "#fffbf1"
MUTED = "#687590"


def font(size, weight=QFont.Weight.Normal):
    value = QFont("Outfit", size)
    value.setWeight(weight)
    return value


class Button(QPushButton):
    """Keyboard-accessible button with a smooth hover fill and pressed offset."""

    def __init__(self, text, parent=None, variant="blue"):
        super().__init__(text, parent)
        self.variant = variant
        self._hover = 0.0
        self.animation = QPropertyAnimation(self, b"hover", self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setFont(font(11, QFont.Weight.DemiBold))
        self.setStyleSheet("padding: 0 18px; border: none; background: transparent;")

    def get_hover(self):
        return self._hover

    def set_hover(self, value):
        self._hover = value
        self.update()

    hover = Property(float, get_hover, set_hover)

    def enterEvent(self, event):
        self._animate(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate(0)
        super().leaveEvent(event)

    def _animate(self, target):
        self.animation.stop()
        self.animation.setStartValue(self._hover)
        self.animation.setEndValue(target)
        self.animation.start()

    def paintEvent(self, event):
        colors = {
            "blue": (BLUE, "#1740c5", "#ffffff"),
            "yellow": (BUTTER, "#ffc940", NAVY),
            "soft": ("#e9efff", "#d8e3ff", BLUE),
            "white": ("#ffffff", "#f2f5ff", NAVY),
        }
        start, end, text = colors[self.variant]
        c1, c2 = QColor(start), QColor(end)
        t = self._hover if self.isEnabled() else 0
        fill = QColor.fromRgbF(
            c1.redF() * (1 - t) + c2.redF() * t,
            c1.greenF() * (1 - t) + c2.greenF() * t,
            c1.blueF() * (1 - t) + c2.blueF() * t,
        )
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.43)
        rect = QRectF(1, 2 if self.isDown() else 0, self.width() - 2, self.height() - 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 13, 13)
        if self.hasFocus():
            p.setPen(QPen(QColor(NAVY), 2, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 10, 10)
        p.setFont(self.font())
        p.setPen(QColor(text))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class Logo(QWidget):
    """Draws assets/logo.svg, the same artwork used for the app icons."""

    def __init__(self, parent=None, size=44):
        super().__init__(parent)
        self.renderer = QSvgRenderer(
            str(Path(__file__).parent / "assets" / "logo.svg"), self
        )
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.renderer.render(p, QRectF(self.rect()))


class Desk(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._position = 0.0
        self.connected = False
        self.phase = 0.0
        self.setMinimumHeight(190)
        self.setAccessibleName("Desk layout: Mac on the left of Windows")
        self.animation = QPropertyAnimation(self, b"position", self)
        self.animation.setDuration(420)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)

    def _tick(self):
        if self.connected and self.isVisible():
            self.phase = (self.phase + 0.02) % 1
            self.update()

    def get_position(self):
        return self._position

    def set_position(self, value):
        self._position = value
        self.update()

    position = Property(float, get_position, set_position)

    def set_side(self, side):
        self.setAccessibleName(f"Desk layout: Mac on the {side} of Windows")
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(0.0 if side == "left" else 1.0)
        self.animation.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Scale uniformly and center, so the computers keep their proportions.
        scale = min(self.width() / 480, self.height() / 198)
        p.translate((self.width() - 480 * scale) / 2, (self.height() - 198 * scale) / 2)
        p.scale(scale, scale)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#fff1b9"))
        p.drawEllipse(QRectF(60, 17, 136, 136))
        p.setBrush(QColor("#d9e6ff"))
        p.drawEllipse(QRectF(289, 11, 148, 148))
        p.setBrush(QColor("#c5d5fa"))
        p.drawRoundedRect(QRectF(26, 155, 425, 5), 2, 2)
        p.setPen(QPen(QColor("#86a4f6"), 2, Qt.PenStyle.DashLine))
        path = QPainterPath(QPointF(198, 100))
        path.cubicTo(226, 66, 253, 137, 279, 100)
        p.drawPath(path)
        if self.connected:
            point = path.pointAtPercent(self.phase)
            p.setBrush(QColor(BLUE))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(point, 5, 5)
        self._computer(p, 123 + 226 * self._position, True)
        self._computer(p, 349 - 226 * self._position, False)
        p.setPen(QPen(QColor(BLUE), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(230, 43, 250, 43)
        p.drawLine(230, 43, 234, 39)
        p.drawLine(230, 43, 234, 47)
        p.drawLine(250, 43, 246, 39)
        p.drawLine(250, 43, 246, 47)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d2a228"))
        for cx, cy in ((44, 37), (446, 125)):
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx, cy - 7),
                        QPointF(cx + 2, cy - 2),
                        QPointF(cx + 7, cy),
                        QPointF(cx + 2, cy + 2),
                        QPointF(cx, cy + 7),
                        QPointF(cx - 2, cy + 2),
                        QPointF(cx - 7, cy),
                        QPointF(cx - 2, cy - 2),
                    ]
                )
            )

    def _computer(self, p, cx, mac):
        top, w, h = (57, 142, 87) if mac else (37, 155, 103)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(NAVY))
        p.drawRoundedRect(QRectF(cx - w / 2, top, w, h), 9, 9)
        p.setBrush(QColor(BUTTER if mac else "#6693ff"))
        p.drawRoundedRect(QRectF(cx - w / 2 + 6, top + 6, w - 12, h - 12), 5, 5)
        # A friendly face makes the machines feel like desk companions.
        p.setPen(
            QPen(
                QColor(NAVY if mac else "#ffffff"),
                3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        eye_y = top + h / 2 - 3
        for eye_x in (cx - 14, cx + 14):
            p.drawLine(QPointF(eye_x, eye_y - 3), QPointF(eye_x, eye_y + 2))
        smile = QPainterPath(QPointF(cx - 7, eye_y + 11))
        smile.quadTo(cx, eye_y + 18, cx + 7, eye_y + 11)
        p.drawPath(smile)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(NAVY))
        if mac:
            p.drawRoundedRect(QRectF(cx - 81, top + h + 3, 162, 8), 4, 4)
        else:
            p.drawRect(QRectF(cx - 5, top + h, 10, 13))
            p.drawRoundedRect(QRectF(cx - 28, top + h + 11, 56, 5), 2, 2)
        p.setFont(font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(NAVY))
        p.drawText(
            QRectF(cx - 85, 170, 170, 22),
            Qt.AlignmentFlag.AlignCenter,
            "Mac" if mac else "Windows PC",
        )


STYLES = """
QMainWindow, QWidget#page { background: #fffbf1; }
QWidget { color: #17326d; font-family: 'Outfit'; font-size: 14px; }
QLabel { background: transparent; }
QLabel#muted { color: #687590; }
QLabel#field { color: #687590; font-size: 12px; font-weight: 600; }
QLabel#brand { font-size: 28px; font-weight: 700; color: #17326d; }
QLabel#cardTitle { font-size: 20px; font-weight: 600; }
QLabel#badge { background: #e9efff; color: #2856e8; border-radius: 15px; padding: 8px 14px; font-weight: 600; font-size: 11px; }
QFrame#deskCard { background: #edf3ff; border: 1px solid #d8e3fb; border-radius: 23px; }
QFrame#settingsCard { background: #fff3cc; border: 1px solid #f4e4ae; border-radius: 23px; }
QFrame#pairCard { background: white; border: 1px solid #e7e8ed; border-radius: 23px; }
QFrame#statusCard { background: #eaf0ff; border-radius: 16px; }
QLabel#status { font-weight: 600; font-size: 15px; color: #2856e8; }
QLineEdit, QPlainTextEdit, QComboBox { background: #f6f8fe; border: 1px solid #d6e0f6; border-radius: 10px; padding: 10px; color: #17326d; selection-background-color: #2856e8; selection-color: white; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 2px solid #7298ff; padding: 9px; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled { color: #8390a5; background: #f1f3f7; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView { background: white; color: #17326d; selection-background-color: #e9efff; selection-color: #2856e8; border: 1px solid #d6e0f6; }
QCheckBox { spacing: 10px; font-size: 15px; }
QCheckBox::indicator { width: 21px; height: 21px; border: 2px solid #a7b6d9; border-radius: 7px; background: white; }
QCheckBox::indicator:checked { background: #2856e8; border: 5px solid #2856e8; image: none; }
QCheckBox::indicator:focus { border-color: #17326d; }
QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #e0d4ad; }
QSlider::sub-page:horizontal { background: #2856e8; border-radius: 3px; }
QSlider::handle:horizontal { background: #2856e8; width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; border: 3px solid white; }
QSlider:disabled { opacity: 0.5; }
QScrollArea { border: none; background: #fffbf1; }
QScrollBar:vertical { background: #fffbf1; width: 10px; }
QScrollBar::handle:vertical { background: #cdd9f5; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #17326d; color: white; border: none; padding: 8px; }
"""
