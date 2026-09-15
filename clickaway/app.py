"""Qt desktop companions. Native capture and networking never run on the UI thread."""

import argparse
import ipaddress
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import __version__, settings
from .connection import Host, connect, local_addresses
from .geometry import Screen
from .protocol import MAX_TEXT, pairing_code, parse_pairing
from .widgets import Button, Desk, Logo, STYLES, font

ASSETS = Path(__file__).parent / "assets"


class Events(QObject):
    event = Signal(object, str, object)


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def card(name):
    widget = QFrame()
    widget.setObjectName(name)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(23, 17, 23, 17)
    layout.setSpacing(8)
    return widget, layout


class App(QMainWindow):
    def __init__(self, preview=False, preview_mac=False):
        super().__init__()
        self.preview = preview or preview_mac
        self.is_windows = sys.platform == "win32" and not preview_mac
        self.generation = 0
        self.peer = self.host = self.controller = self.receiver = None
        self.active = False
        self.last_clip = None
        self.backend = None
        self.events = Events(self)
        self.events.event.connect(self._event, Qt.ConnectionType.QueuedConnection)
        self.config = settings.load() if not self.preview else {}
        self.side = self.config.get("side", "left")
        if self.side not in ("left", "right"):
            self.side = "left"
        speed = self.config.get("speed", 1.0)
        self.speed = (
            speed if type(speed) in (int, float) and 0.25 <= speed <= 3 else 1.0
        )
        if self.preview:
            self.displays = [Screen(0, 0, 1920, 1080, "Display 1 · 1920 × 1080 (main)")]
        elif self.is_windows:
            from . import windows

            self.backend = windows
            self.displays = windows.screens()
        elif sys.platform == "darwin":
            from . import macos

            self.backend = macos
            self.displays = macos.screens()
        else:
            raise RuntimeError(
                "ClickAway supports Windows and macOS. Use --preview to see the design."
            )
        self._build()
        self.clip_timer = QTimer(self)
        self.clip_timer.timeout.connect(self._clipboard_tick)
        self.clip_timer.start(650)
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._health_tick)
        self.health_timer.start(1500)

    def _build(self):
        self.setWindowTitle("ClickAway" + (" · Design preview" if self.preview else ""))
        self.setWindowIcon(QIcon(str(ASSETS / "logo.svg")))
        self.resize(1060, 910 if not self.is_windows else 880)
        self.setMinimumSize(880, 650)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("page")
        scroll.setWidget(page)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(Logo())
        header.addSpacing(4)
        header.addWidget(label("ClickAway", "brand"))
        header.addStretch()
        header.addWidget(
            label(
                "WINDOWS / YOUR HOME BASE"
                if self.is_windows
                else "MAC / YOUR DESK COMPANION",
                "badge",
            )
        )
        outer.addLayout(header)

        intro = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label("Good neighbors. Great flow.", "title"))
        words.addWidget(
            label("One mouse, two happy computers. Make yourself at home.", "muted")
        )
        intro.addLayout(words)
        intro.addStretch()
        sticker = label("hello,\nother screen!", "badge")
        sticker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sticker.setStyleSheet(
            "background: #ffda69; color: #17326d; border-radius: 20px; padding: 12px 19px; font-size: 16px; font-weight: 600;"
        )
        intro.addWidget(sticker)
        outer.addLayout(intro)

        state, state_layout = card("statusCard")
        state_layout.setContentsMargins(18, 13, 18, 13)
        state_layout.setSpacing(3)
        self.status = label("Ready when you are", "status")
        self.detail = label(
            "Connect your computers, then move naturally between them.", "muted", True
        )
        state_layout.addWidget(self.status)
        state_layout.addWidget(self.detail)
        outer.addWidget(state)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        desk_card, desk_layout = card("deskCard")
        desk_layout.addWidget(label("01 / YOUR LITTLE DESK", "eyebrow"))
        desk_layout.addWidget(label("Side by side. Just like you.", "cardTitle"))
        self.desk = Desk()
        desk_layout.addWidget(self.desk)
        positions = QHBoxLayout()
        self.left = Button("←  Mac on the left", variant="blue")
        self.right = Button("Mac on the right  →", variant="white")
        self.left.clicked.connect(lambda: self._set_side("left"))
        self.right.clicked.connect(lambda: self._set_side("right"))
        positions.addWidget(self.left)
        positions.addWidget(self.right)
        desk_layout.addLayout(positions)
        desk_layout.addWidget(
            label(
                "Cross the shared edge to switch. Easy does it."
                if self.is_windows
                else "Your desk arrangement follows the Windows app.",
                "muted",
            )
        )
        self.display_picker = QComboBox()
        self.display_picker.addItems([s.name for s in self.displays])
        self.display_picker.setAccessibleName(
            "Windows screen to share" if self.is_windows else "Mac screen to control"
        )
        self.display_picker.currentIndexChanged.connect(self._configure_controller)
        desk_layout.addWidget(self.display_picker)
        middle.addWidget(desk_card, 3)

        prefs, prefs_layout = card("settingsCard")
        prefs_layout.addWidget(label("02 / THE LITTLE THINGS", "eyebrow"))
        prefs_layout.addWidget(label("Make it feel like you.", "cardTitle"))
        self.clip_toggle = QCheckBox("Shared text clipboard")
        self.clip_toggle.setChecked(self.config.get("clipboard", True) is True)
        self.clip_toggle.toggled.connect(self._clipboard_changed)
        prefs_layout.addSpacing(8)
        prefs_layout.addWidget(self.clip_toggle)
        self.clip_status = label(
            "Copy here. Paste there.\nText up to 64 KB, in both directions.",
            "muted",
            True,
        )
        prefs_layout.addWidget(self.clip_status)
        prefs_layout.addSpacing(10)
        speed_row = QHBoxLayout()
        speed_row.addWidget(label("Pointer speed"))
        speed_row.addStretch()
        self.speed_label = label(f"{self.speed:.2f}×", "badge")
        speed_row.addWidget(self.speed_label)
        prefs_layout.addLayout(speed_row)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(25, 300)
        self.speed_slider.setSingleStep(5)
        self.speed_slider.setValue(round(self.speed * 100))
        self.speed_slider.setAccessibleName("Pointer speed")
        self.speed_slider.valueChanged.connect(self._speed_changed)
        self.speed_slider.sliderReleased.connect(self._save)
        prefs_layout.addWidget(self.speed_slider)
        prefs_layout.addWidget(
            label(
                "Nice and slow                         A little quicker"
                if self.is_windows
                else "Adjust the speed in your Windows app.",
                "muted",
            )
        )
        prefs_layout.addStretch()
        prefs_layout.addWidget(label("YOUR WAY HOME", "eyebrow"))
        shortcut = label("Ctrl  +  Alt  +  F12")
        shortcut.setStyleSheet("font-size: 19px; font-weight: 600; color: #2856e8;")
        prefs_layout.addWidget(shortcut)
        prefs_layout.addWidget(
            label("Bring your mouse back to Windows, anytime.", "muted", True)
        )
        middle.addWidget(prefs, 2)
        outer.addLayout(middle)

        pair, pair_layout = card("pairCard")
        pair_layout.addWidget(label("03 / LET’S MAKE THE INTRODUCTIONS", "eyebrow"))
        if self.is_windows:
            pair_layout.addWidget(label("A tiny code. A connected desk.", "cardTitle"))
            pair_layout.addWidget(
                label(
                    "Choose your Wi-Fi address and start sharing. Paste your private code into the Mac app.",
                    "muted",
                    True,
                )
            )
            row = QHBoxLayout()
            self.address_picker = QComboBox()
            self.address_picker.setEditable(True)
            self.address_picker.addItems(
                local_addresses() if not self.preview else ["192.168.1.10"]
            )
            self.address_picker.setMinimumWidth(156)
            self.address_picker.setAccessibleName("Windows Wi-Fi IPv4 address")
            row.addWidget(self.address_picker)
            self.action = Button("Start sharing  ↗", variant="blue")
            self.action.clicked.connect(self.toggle)
            row.addWidget(self.action)
            self.copy_button = Button("Copy connection code", variant="yellow")
            self.copy_button.setEnabled(False)
            self.copy_button.clicked.connect(self.copy_code)
            row.addWidget(self.copy_button)
            row.addStretch()
            pair_layout.addLayout(row)
            pair_layout.addWidget(
                label(
                    "Just between your computers. Stopping sharing expires the code.",
                    "muted",
                )
            )
        else:
            pair_layout.addWidget(
                label("Your mouse has a new place to be.", "cardTitle")
            )
            self.code_input = QPlainTextEdit()
            self.code_input.setPlaceholderText(
                "Paste your CA1- connection code from Windows here…"
            )
            self.code_input.setAccessibleName("Private connection code from Windows")
            self.code_input.setFixedHeight(70)
            self.code_input.setTabChangesFocus(True)
            self.code_input.textChanged.connect(self._limit_code)
            pair_layout.addWidget(self.code_input)
            row = QHBoxLayout()
            self.action = Button("Connect to Windows  ↗", variant="blue")
            self.action.clicked.connect(self.toggle)
            row.addWidget(self.action)
            self.permission_button = Button("Allow mouse control", variant="yellow")
            self.permission_button.clicked.connect(self.allow_access)
            row.addWidget(self.permission_button)
            row.addStretch()
            pair_layout.addLayout(row)
            self.permission = label(
                "One quick permission: let ClickAway control your Mac’s mouse.", "muted"
            )
            pair_layout.addWidget(self.permission)
            self.speed_slider.setEnabled(False)
            self.left.setEnabled(False)
            self.right.setEnabled(False)
        outer.addWidget(pair)
        footer = QHBoxLayout()
        footer.addWidget(
            label("A direct, encrypted connection. A calmer desk.", "muted")
        )
        footer.addStretch()
        footer.addWidget(
            label(
                f"v{__version__}" + (" · DESIGN PREVIEW" if self.preview else ""),
                "muted",
            )
        )
        help_button = Button("Setup guide  ↗", variant="soft")
        help_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/ElMariones/ClickAway#setup")
            )
        )
        footer.addWidget(help_button)
        outer.addLayout(footer)
        self._draw_desk()

    def _limit_code(self):
        if len(self.code_input.toPlainText()) > 2048:
            self.code_input.setPlainText(self.code_input.toPlainText()[:2048])

    def _draw_desk(self):
        self.desk.set_side(self.side)
        self.left.variant = "blue" if self.side == "left" else "white"
        self.right.variant = "blue" if self.side == "right" else "white"
        self.left.update()
        self.right.update()

    def _set_side(self, side):
        self.side = side
        self._draw_desk()
        self._configure_controller()
        self._save()

    def _speed_changed(self, value):
        self.speed = value / 100
        self.speed_label.setText(f"{self.speed:.2f}×")
        self._configure_controller()

    def _save(self):
        if not self.preview:
            try:
                settings.save(
                    {
                        "side": self.side,
                        "speed": self.speed,
                        "clipboard": self.clip_toggle.isChecked(),
                    }
                )
            except OSError:
                self.detail.setText(
                    "Settings could not be saved. They will reset when the app closes."
                )

    def _selected_screen(self):
        return self.displays[max(0, self.display_picker.currentIndex())]

    def _configure_controller(self, *_):
        if self.controller and self.peer and not self.peer.closed.is_set():
            self.controller.configure(
                self.peer,
                self._selected_screen(),
                self.remote_screen,
                self.side,
                self.speed,
            )
            self.peer.send({"type": "layout", "side": self.side, "speed": self.speed})

    def _post(self, generation, event, *args):
        self.events.event.emit(generation, event, args)

    def toggle(self):
        if self.preview:
            self.status.setText("Design preview")
            self.detail.setText("Run ClickAway normally to connect your computers.")
            return
        if self.active:
            self.stop()
            return
        self.generation += 1
        gen = self.generation
        try:
            if self.is_windows:
                ipaddress.IPv4Address(self.address_picker.currentText().strip())
                if not self.controller:
                    self.controller = self.backend.MouseController(
                        lambda s: self._post(None, "control", s)
                    )
                    try:
                        self.controller.start()
                    except Exception:
                        self.controller = None
                        raise
                self.host = Host(
                    lambda peer, hello: self._post(gen, "connected", peer, hello),
                    lambda m: self._post(gen, "message", m),
                    lambda reason: self._post(gen, "disconnected", reason),
                    port=49624,
                )
                self.host.start()
                self.copy_button.setEnabled(True)
                self.status.setText("A little hello from your Mac?")
                self.detail.setText(
                    "Copy the connection code into the Mac app. Allow Windows Firewall on private networks if prompted."
                )
                self.action.setText("Stop sharing")
            else:
                if not self.backend.accessibility(prompt=True):
                    self.allow_access()
                    self.status.setText("Allow mouse control first")
                    self.detail.setText(
                        "Enable ClickAway in macOS Accessibility settings, then connect again."
                    )
                    return
                details = parse_pairing(self.code_input.toPlainText())
                self.receiver = self.backend.MouseReceiver(self._selected_screen())
                receiver = self.receiver

                def incoming(m):
                    if m["type"] in ("clipboard", "layout"):
                        self._post(gen, "message", m)
                    elif m["type"] in ("enter", "move", "button", "scroll", "leave"):
                        receiver.handle(m)
                        if m["type"] in ("enter", "leave"):
                            self._post(
                                gen,
                                "control",
                                "mac" if m["type"] == "enter" else "windows",
                            )
                    else:
                        raise ValueError("Unexpected message from Windows")

                def ended(reason):
                    try:
                        receiver.release()
                    finally:
                        self._post(gen, "disconnected", reason)

                def worker():
                    try:
                        peer = connect(details, receiver.screen, incoming, ended)
                        if gen != self.generation:
                            peer.close()
                            return
                        self._post(gen, "connected", peer, None)
                    except Exception as exc:
                        self._post(gen, "failed", str(exc))

                threading.Thread(
                    target=worker, daemon=True, name="clickaway-connect"
                ).start()
                self.display_picker.setEnabled(False)
                self.status.setText("Making a secure introduction…")
                self.detail.setText("Checking the Windows computer’s identity.")
                self.action.setText("Cancel connection")
            self.active = True
        except Exception as exc:
            self.stop()
            self.status.setText("Could not start")
            self.detail.setText(str(exc))

    def copy_code(self):
        if self.host:
            try:
                address = str(
                    ipaddress.IPv4Address(self.address_picker.currentText().strip())
                )
                code = pairing_code(
                    address, self.host.fingerprint, self.host.token, self.host.port
                )
                QApplication.clipboard().setText(code)
                self.last_clip = code
                self.copy_button.setText("Copied!  ✓")
                QTimer.singleShot(
                    2200, lambda: self.copy_button.setText("Copy connection code")
                )
                self.detail.setText(
                    "Code copied. Paste it into ClickAway on your Mac. It expires when sharing stops."
                )
            except ValueError:
                self.detail.setText(
                    "Enter your Windows computer’s Wi-Fi IPv4 address, such as 192.168.1.10."
                )

    def allow_access(self):
        if not self.preview:
            self.backend.accessibility(prompt=True)
            QDesktopServices.openUrl(
                QUrl(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
                )
            )

    def _health_tick(self):
        if self.preview:
            return
        if not self.is_windows:
            granted = self.backend.accessibility()
            self.permission.setText(
                "✓ Mouse control allowed. You’re right at home."
                if granted
                else "Enable ClickAway in System Settings → Privacy & Security → Accessibility."
            )
            if not granted and self.peer:
                self.peer.close("Mouse-control permission was removed")
        try:
            fresh = self.backend.screens()
            if fresh != self.displays:
                self.stop()
                self.displays = fresh
                self.display_picker.clear()
                self.display_picker.addItems([s.name for s in fresh])
                self.status.setText("Your screens changed")
                self.detail.setText(
                    "Choose your display and connect again to use the new layout."
                )
        except Exception:
            if self.active:
                self.stop()
                self.detail.setText(
                    "A display is unavailable. Reconnect when your desktop is ready."
                )

    def _clipboard_changed(self):
        self.last_clip = self._read_clipboard()
        self.clip_status.setText(
            "Copy here. Paste there.\nText up to 64 KB, in both directions."
            if self.clip_toggle.isChecked()
            else "Clipboard sync is paused on this computer."
        )
        self._save()

    @staticmethod
    def _read_clipboard():
        clipboard = QApplication.clipboard()
        return (
            clipboard.text()
            if clipboard.mimeData() and clipboard.mimeData().hasText()
            else None
        )

    def _clipboard_tick(self):
        if self.peer and not self.peer.closed.is_set() and self.clip_toggle.isChecked():
            text = self._read_clipboard()
            if text is not None and text != self.last_clip:
                self.last_clip = text
                if text.strip().startswith("CA1-"):
                    return
                if len(text.encode("utf-8")) <= MAX_TEXT:
                    self.peer.send({"type": "clipboard", "text": text})
                    self.clip_status.setText(
                        "✓ Copied text sent to your other computer."
                    )
                else:
                    self.clip_status.setText(
                        "This copy is over 64 KB; it stays on this computer."
                    )

    def _message(self, m):
        if m["type"] == "layout" and not self.is_windows:
            self.side, self.speed = m["side"], m["speed"]
            self.speed_slider.setValue(round(self.speed * 100))
            self.speed_label.setText(f"{self.speed:.2f}×")
            self._draw_desk()
        elif m["type"] == "clipboard":
            if self.clip_toggle.isChecked():
                QApplication.clipboard().setText(m["text"])
                self.last_clip = m["text"]
                self.clip_status.setText("✓ Text received. Ready when you paste.")
        elif self.peer:
            self.peer.close("Unexpected message from companion")

    @Slot(object, str, object)
    def _event(self, gen, event, args):
        if gen is not None and gen != self.generation:
            if event == "connected":
                args[0].close()
            return
        if event == "connected":
            self.peer = args[0]
            self.last_clip = self._read_clipboard()
            if self.is_windows:
                self.remote_screen = Screen(0, 0, args[1]["width"], args[1]["height"])
                self._configure_controller()
            else:
                self.peer.start()
                self.action.setText("Disconnect")
                self.code_input.clear()
            self.status.setText("Connected. Your mouse is on Windows.")
            self.detail.setText(
                "Move through the shared screen edge to visit your Mac. Copy text on either computer to sync it."
            )
            self.desk.connected = True
        elif event == "message":
            self._message(args[0])
        elif event == "control":
            if self.peer and not self.peer.closed.is_set():
                self.status.setText(
                    "Hello, Mac! Your mouse is here."
                    if args[0] == "mac"
                    else "Back home. Your mouse is on Windows."
                    if args[0] == "windows"
                    else args[0]
                )
        elif event in ("disconnected", "failed"):
            self.peer = None
            if self.controller:
                self.controller.detach()
            self.desk.connected = False
            self.status.setText(
                "Waiting for your Mac"
                if self.is_windows and self.active
                else "Disconnected"
            )
            self.detail.setText(
                args[0]
                + (
                    ". Reconnect on the Mac with the current code."
                    if self.is_windows
                    else ". Copy the code from Windows to reconnect."
                )
            )
            if not self.is_windows:
                self.active = False
                self.action.setText("Connect to Windows  ↗")
                self.display_picker.setEnabled(True)

    def stop(self):
        self.generation += 1
        if self.controller:
            self.controller.detach()
        if self.host:
            self.host.stop()
            self.host = None
        if self.peer:
            self.peer.close()
            self.peer = None
        if self.receiver:
            self.receiver.release()
        self.active = False
        self.desk.connected = False
        self.status.setText("Sharing is off. See you soon.")
        self.detail.setText("Your mouse and clipboard stay on this computer.")
        self.action.setText(
            "Start sharing  ↗" if self.is_windows else "Connect to Windows  ↗"
        )
        self.display_picker.setEnabled(True)
        if self.is_windows:
            self.copy_button.setEnabled(False)

    def closeEvent(self, event):
        self._save()
        self.stop()
        if self.controller:
            self.controller.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="ClickAway Windows and Mac companions")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview design without input capture or network access",
    )
    parser.add_argument(
        "--preview-mac", action="store_true", help="Preview the Mac setup screen"
    )
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ClickAway")
    app.setOrganizationName("ClickAway")
    QFontDatabase.addApplicationFont(str(ASSETS / "Outfit.ttf"))
    app.setFont(font(11))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLES)
    try:
        window = App(args.preview, args.preview_mac)
        window.show()
    except Exception as exc:
        QMessageBox.critical(None, "ClickAway could not start", str(exc))
        return
    app.exec()
