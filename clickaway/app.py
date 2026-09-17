"""Qt desktop companions. Native capture and networking never run on the UI thread."""

import argparse
import errno
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
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import __version__, settings
from .audio import DEFAULT_LATENCY, LATENCIES
from .connection import Host, connect, local_addresses
from .geometry import Screen
from .pake import MAX_PASSWORD, MIN_PASSWORD, PairingError, normalize_password
from .protocol import MAX_TEXT, PORT
from .widgets import Button, Desk, Logo, STYLES, font

ASSETS = Path(__file__).parent / "assets"
CLIPBOARD_HINT = "Plain text up to 64 KB."
SOUND_HINT = (
    "Windows sound is sent to the Mac and plays alongside whatever the Mac is "
    "playing. Turn the Windows volume all the way down: the Mac still gets the "
    "sound at full strength."
)
MAC_SOUND_HINT = (
    "Windows sound plays through this Mac, mixed with the Mac's own sound. "
    "Volume and delay are set in the Windows app."
)


class Events(QObject):
    event = Signal(object, str, object)


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def card(name, title):
    widget = QFrame()
    widget.setObjectName(name)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(22, 16, 22, 18)
    layout.setSpacing(8)
    layout.addWidget(label(title, "cardTitle"))
    return widget, layout


def field(title, widget):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(label(title, "field"))
    layout.addWidget(widget)
    return box


class NetworkPicker(QComboBox):
    """Lists networks by name; each item's data is its IPv4 address."""

    def __init__(self, load):
        super().__init__()
        self.load = load
        self.setAccessibleName("Network to share on")
        self.refresh()

    def refresh(self):
        current = self.currentData()
        self.clear()
        for name, address in self.load():
            self.addItem(name, address)
        self.setCurrentIndex(max(0, self.findData(current)))

    def showPopup(self):
        # Wi-Fi and VPN connections change while the app is open.
        self.refresh()
        super().showPopup()


class App(QMainWindow):
    def __init__(self, preview=False, preview_mac=False):
        super().__init__()
        self.preview = preview or preview_mac
        self.is_windows = sys.platform == "win32" and not preview_mac
        self.generation = 0
        self.peer = self.host = self.controller = self.receiver = None
        self.capture = self.player = None
        self.sound_format = (48000, 2)
        self.mac_wants_sound = True
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
        volume = self.config.get("volume", 80)
        self.volume = volume if type(volume) is int and 0 <= volume <= 100 else 80
        latency = self.config.get("latency", DEFAULT_LATENCY)
        self.latency = (
            latency if latency in [value for _, value in LATENCIES] else DEFAULT_LATENCY
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
        self.setWindowTitle("ClickAway" + (" · Preview" if self.preview else ""))
        self.setWindowIcon(QIcon(str(ASSETS / "logo.svg")))
        self.resize(1000, 900 if self.is_windows else 940)
        self.setMinimumSize(860, 600)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("page")
        scroll.setWidget(page)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(Logo())
        header.addSpacing(6)
        header.addWidget(label("ClickAway", "brand"))
        header.addStretch()
        header.addWidget(label("WINDOWS" if self.is_windows else "MAC", "badge"))
        outer.addLayout(header)

        state = QFrame()
        state.setObjectName("statusCard")
        state_layout = QVBoxLayout(state)
        state_layout.setContentsMargins(18, 13, 18, 13)
        state_layout.setSpacing(3)
        self.status = label("", "status")
        self.detail = label("", "muted", True)
        state_layout.addWidget(self.status)
        state_layout.addWidget(self.detail)
        outer.addWidget(state)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        desk_card, desk_layout = card("deskCard", "Layout")
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
        self.display_picker = QComboBox()
        self.display_picker.addItems([s.name for s in self.displays])
        display_title = (
            "Windows display with the shared edge"
            if self.is_windows
            else "Mac display to control"
        )
        self.display_picker.setAccessibleName(display_title)
        self.display_picker.currentIndexChanged.connect(self._configure_controller)
        desk_layout.addWidget(field(display_title, self.display_picker))
        if not self.is_windows:
            desk_layout.addWidget(
                label("Layout and pointer speed are set in the Windows app.", "muted")
            )
        middle.addWidget(desk_card, 3)

        prefs, prefs_layout = card("settingsCard", "Options")
        self.clip_toggle = QCheckBox("Sync text clipboard")
        self.clip_toggle.setChecked(self.config.get("clipboard", True) is True)
        self.clip_toggle.toggled.connect(self._clipboard_changed)
        prefs_layout.addSpacing(4)
        prefs_layout.addWidget(self.clip_toggle)
        self.clip_status = label(CLIPBOARD_HINT, "muted", True)
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
        prefs_layout.addStretch()
        prefs_layout.addWidget(label("Return to Windows", "field"))
        shortcut = label("Ctrl + Alt + F12")
        shortcut.setStyleSheet("font-size: 19px; font-weight: 600; color: #2856e8;")
        prefs_layout.addWidget(shortcut)
        middle.addWidget(prefs, 2)
        outer.addLayout(middle)

        sound_card, sound_layout = card("soundCard", "Sound")
        sound_row = QHBoxLayout()
        sound_row.setSpacing(24)
        switch = QVBoxLayout()
        switch.setSpacing(6)
        self.sound_toggle = QCheckBox(
            "Send this PC's sound to the Mac"
            if self.is_windows
            else "Play the Windows sound on this Mac"
        )
        self.sound_toggle.setChecked(
            self.config.get("sound", not self.is_windows) is not False
        )
        self.sound_toggle.toggled.connect(self._sound_changed)
        switch.addWidget(self.sound_toggle)
        self.sound_status = label(
            SOUND_HINT if self.is_windows else MAC_SOUND_HINT, "muted", True
        )
        switch.addWidget(self.sound_status)
        switch.addStretch()
        sound_row.addLayout(switch, 3)

        knobs = QVBoxLayout()
        knobs.setSpacing(6)
        volume_row = QHBoxLayout()
        volume_row.addWidget(label("Volume on the Mac", "field"))
        volume_row.addStretch()
        self.volume_label = label(f"{self.volume}%", "badge")
        volume_row.addWidget(self.volume_label)
        knobs.addLayout(volume_row)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setSingleStep(5)
        self.volume_slider.setValue(self.volume)
        self.volume_slider.setAccessibleName("Sound volume on the Mac")
        self.volume_slider.valueChanged.connect(self._volume_changed)
        self.volume_slider.sliderReleased.connect(self._save)
        knobs.addWidget(self.volume_slider)
        self.latency_picker = QComboBox()
        for name, value in LATENCIES:
            self.latency_picker.addItem(name, value)
        self.latency_picker.setCurrentIndex(
            max(0, self.latency_picker.findData(self.latency))
        )
        self.latency_picker.setAccessibleName("Sound delay")
        self.latency_picker.currentIndexChanged.connect(self._latency_changed)
        knobs.addWidget(field("Sound delay", self.latency_picker))
        sound_row.addLayout(knobs, 2)
        sound_layout.addLayout(sound_row)
        if not self.is_windows:
            # The Mac can refuse the sound, but the Windows app sets how it plays.
            self.volume_slider.setEnabled(False)
            self.latency_picker.setEnabled(False)
        outer.addWidget(sound_card)

        pair, pair_layout = card("pairCard", "Connection")
        self.password_input = QLineEdit()
        self.password_input.setMaxLength(MAX_PASSWORD)
        self.password_input.setPlaceholderText(f"At least {MIN_PASSWORD} characters")
        self.password_input.setAccessibleName("Password")
        self.password_input.returnPressed.connect(self.toggle)
        row = QHBoxLayout()
        row.setSpacing(12)
        if self.is_windows:
            self.network_picker = NetworkPicker(self._networks)
            row.addWidget(field("Network", self.network_picker), 3)
            row.addWidget(field("Password", self.password_input), 2)
            self.action = Button("Start sharing", variant="blue")
            row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignBottom)
            pair_layout.addLayout(row)
            pair_layout.addWidget(
                label(
                    "Type the same password in the Mac app. If Windows Firewall asks, allow ClickAway.",
                    "muted",
                    True,
                )
            )
        else:
            row.addWidget(
                field("Password from the Windows app", self.password_input), 2
            )
            self.address_input = QLineEdit()
            self.address_input.setPlaceholderText("192.168.1.20")
            self.address_input.setAccessibleName("Windows IP address")
            self.address_input.returnPressed.connect(self.toggle)
            self.address_box = field("Windows IP address", self.address_input)
            self.address_box.setVisible(False)
            row.addWidget(self.address_box, 1)
            self.action = Button("Connect", variant="blue")
            row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignBottom)
            pair_layout.addLayout(row)
            permission_row = QHBoxLayout()
            self.permission = label(
                "Mouse control needs Accessibility permission.", "muted", True
            )
            permission_row.addWidget(self.permission, 1)
            self.permission_button = Button("Allow mouse control", variant="yellow")
            self.permission_button.clicked.connect(self.allow_access)
            permission_row.addWidget(self.permission_button)
            pair_layout.addLayout(permission_row)
            self.speed_slider.setEnabled(False)
            self.left.setEnabled(False)
            self.right.setEnabled(False)
        self.action.clicked.connect(self.toggle)
        outer.addWidget(pair)

        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(
            label(f"v{__version__}" + (" · preview" if self.preview else ""), "muted")
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
        self._show_idle()

    def _show_idle(self):
        if self.is_windows:
            self.status.setText("Not sharing")
            self.detail.setText("Choose a network and a password, then start sharing.")
        else:
            self.status.setText("Not connected")
            self.detail.setText("Type the password from the Windows app, then connect.")

    def _networks(self):
        if self.preview:
            return [("Wi-Fi: Home · 192.168.1.20", "192.168.1.20")]
        try:
            found = self.backend.networks()
        except OSError:
            found = []
        return found or [(address, address) for address in local_addresses()]

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
                        "sound": self.sound_toggle.isChecked(),
                        "volume": self.volume,
                        "latency": self.latency,
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

    def _set_inputs_enabled(self, enabled):
        self.password_input.setEnabled(enabled)
        if self.is_windows:
            self.network_picker.setEnabled(enabled)
        else:
            self.address_input.setEnabled(enabled)
            self.display_picker.setEnabled(enabled)

    def toggle(self):
        if self.preview:
            self.status.setText("Preview")
            self.detail.setText("Run ClickAway without --preview to connect.")
            return
        if self.active:
            self.stop()
            return
        self.generation += 1
        gen = self.generation
        try:
            password = normalize_password(self.password_input.text())
            if self.is_windows:
                address = str(
                    ipaddress.IPv4Address(self.network_picker.currentData() or "")
                )
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
                    password,
                    lambda peer, confirm: self._post(gen, "connected", peer, confirm),
                    lambda m: self._post(gen, "message", m),
                    lambda reason: self._post(gen, "disconnected", reason),
                    on_locked=lambda: self._post(gen, "locked"),
                    address=address,
                )
                self.host.start()
                self.status.setText("Waiting for the Mac")
                self.detail.setText(
                    f"Sharing on {address}. On the Mac, type the password and click Connect."
                )
                self.action.setText("Stop sharing")
            else:
                address = self.address_input.text().strip()
                if not self.address_box.isVisible():
                    address = ""
                elif address:
                    address = str(ipaddress.IPv4Address(address))
                if not self.backend.accessibility(prompt=True):
                    # accessibility(prompt=True) already triggered macOS's own consent
                    # alert; only open System Settings here, not a second AX prompt.
                    self._open_accessibility_settings()
                    self.status.setText("Mouse control is not allowed yet")
                    self.detail.setText(
                        "Turn on ClickAway in System Settings → Privacy & Security → Accessibility, then connect again."
                    )
                    return
                self.receiver = self.backend.MouseReceiver(self._selected_screen())
                receiver = self.receiver

                def incoming(m):
                    if m["type"] == "sound-data":
                        if self.player:
                            self.player.push(m["samples"])
                    elif m["type"] in ("clipboard", "layout", "sound"):
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
                        peer = connect(
                            password, receiver.screen, incoming, ended, address or None
                        )
                        if gen != self.generation:
                            peer.close()
                            return
                        self._post(gen, "connected", peer, None)
                    except LookupError as exc:
                        self._post(gen, "not_found", str(exc))
                    except PairingError:
                        self._post(
                            gen,
                            "failed",
                            "Wrong password. Type the same password as in the Windows app.",
                        )
                    except Exception as exc:
                        self._post(gen, "failed", str(exc))

                threading.Thread(
                    target=worker, daemon=True, name="clickaway-connect"
                ).start()
                self.status.setText("Connecting…")
                self.detail.setText(
                    f"Connecting to {address}."
                    if address
                    else "Looking for the Windows PC on this network."
                )
                self.action.setText("Cancel")
            self._set_inputs_enabled(False)
            self.active = True
        except OSError as exc:
            self.stop()
            self.status.setText("Could not start")
            in_use = (
                exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", 0) == 10048
            )
            self.detail.setText(
                f"Port {PORT} is already in use. Close other ClickAway windows and try again."
                if in_use
                else str(exc)
            )
        except Exception as exc:
            self.stop()
            self.status.setText("Could not start")
            self.detail.setText(str(exc))

    def allow_access(self):
        if not self.preview:
            self.backend.accessibility(prompt=True)
            self._open_accessibility_settings()

    @staticmethod
    def _open_accessibility_settings():
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
                "Mouse control is allowed."
                if granted
                else "Mouse control needs permission: System Settings → Privacy & Security → Accessibility."
            )
            self.permission_button.setVisible(not granted)
            if not granted and self.peer:
                self.peer.close("Mouse control permission was removed")
        try:
            fresh = self.backend.screens()
            if fresh != self.displays:
                self.stop()
                self.displays = fresh
                self.display_picker.clear()
                self.display_picker.addItems([s.name for s in fresh])
                self.status.setText("Displays changed")
                self.detail.setText("Choose a display and connect again.")
        except Exception:
            if self.active:
                self.stop()
                self.detail.setText(
                    "A display is unavailable. Connect again when it is back."
                )

    def _clipboard_changed(self):
        self.last_clip = self._read_clipboard()
        self.clip_status.setText(
            CLIPBOARD_HINT
            if self.clip_toggle.isChecked()
            else "Clipboard sync is off on this computer."
        )
        self._save()

    def _sound_settings(self, playing):
        rate, channels = self.sound_format
        return {
            "type": "sound",
            "playing": playing,
            "volume": self.volume / 100,
            "rate": rate,
            "channels": channels,
            "latency": self.latency,
        }

    def _sound_changed(self, enabled):
        if self.is_windows:
            self._start_sound() if enabled else self._stop_sound()
            if not enabled:
                self.sound_status.setText(SOUND_HINT)
        else:
            if not enabled:
                self._stop_sound()
                self.sound_status.setText("Windows sound is not played on this Mac.")
            else:
                self.sound_status.setText(MAC_SOUND_HINT)
            # Windows stops sending as soon as this Mac says it is not listening.
            if self.peer and not self.peer.closed.is_set():
                self.peer.send(self._sound_settings(enabled))
        self._save()

    def _volume_changed(self, value):
        self.volume = value
        self.volume_label.setText(f"{value}%")
        if self.player:
            self.player.set_volume(value / 100)
        self._send_sound_settings()

    def _latency_changed(self, *_):
        self.latency = self.latency_picker.currentData()
        self._send_sound_settings()
        self._save()

    def _send_sound_settings(self):
        if self.is_windows and self.peer and not self.peer.closed.is_set():
            self.peer.send(self._sound_settings(self.capture is not None))

    def _start_sound(self):
        """Windows: open loopback capture and tell the Mac how to play it."""
        if (
            self.preview
            or not self.is_windows
            or self.capture
            or not self.sound_toggle.isChecked()
            or not self.mac_wants_sound
            or not self.peer
            or self.peer.closed.is_set()
        ):
            return
        peer = self.peer
        try:
            from . import wasapi

            capture = wasapi.LoopbackCapture(
                peer.send_sound,
                on_format=lambda rate, channels: self._post(
                    None, "sound_format", rate, channels
                ),
                on_error=lambda text: self._post(None, "sound_error", text),
            )
            self.sound_format = capture.start()
            self.capture = capture
        except Exception as exc:
            self.sound_status.setText(str(exc))
            return
        peer.send(self._sound_settings(True))
        rate, channels = self.sound_format
        self.sound_status.setText(
            f"Sending Windows sound · {rate // 1000} kHz"
            + (" stereo" if channels == 2 else " mono")
        )

    def _stop_sound(self):
        if self.capture:
            self.capture.stop()
            self.capture = None
            if self.peer and not self.peer.closed.is_set():
                self.peer.send(self._sound_settings(False))
        if self.player:
            self.player.stop()

    def _play_sound(self, m):
        """Mac: follow the format, volume and delay the Windows app asked for."""
        self.volume_slider.setValue(round(m["volume"] * 100))
        self.latency_picker.setCurrentIndex(
            max(0, self.latency_picker.findData(m["latency"]))
        )
        if not m["playing"] or not self.sound_toggle.isChecked():
            if self.player:
                self.player.stop()
            if m["playing"] and self.peer and not self.peer.closed.is_set():
                self.peer.send(self._sound_settings(False))
            self.sound_status.setText(
                MAC_SOUND_HINT
                if self.sound_toggle.isChecked()
                else "Windows sound is not played on this Mac."
            )
            return
        try:
            if self.player is None:
                from .audio import SoundPlayer

                self.player = SoundPlayer(self, self.sound_status.setText)
            self.player.configure(m["rate"], m["channels"], m["latency"], m["volume"])
        except Exception as exc:
            self.sound_status.setText(str(exc))

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
                if len(text.encode("utf-8")) <= MAX_TEXT:
                    self.peer.send({"type": "clipboard", "text": text})
                    self.clip_status.setText("Sent copied text.")
                else:
                    self.clip_status.setText(
                        "Copied text is over 64 KB and was not sent."
                    )

    def _message(self, m):
        if m["type"] == "sound":
            if self.is_windows:
                # The Mac decides whether it wants to hear this PC at all.
                self.mac_wants_sound = m["playing"]
                if m["playing"]:
                    self._start_sound()
                else:
                    self._stop_sound()
                    self.sound_status.setText("The Mac is not playing this PC's sound.")
            else:
                self._play_sound(m)
        elif m["type"] == "layout" and not self.is_windows:
            self.side, self.speed = m["side"], m["speed"]
            self.speed_slider.setValue(round(self.speed * 100))
            self.speed_label.setText(f"{self.speed:.2f}×")
            self._draw_desk()
        elif m["type"] == "clipboard":
            if self.clip_toggle.isChecked():
                QApplication.clipboard().setText(m["text"])
                self.last_clip = m["text"]
                self.clip_status.setText("Received text from the other computer.")
        elif self.peer:
            self.peer.close("Unexpected message from the other computer")

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
                self.detail.setText(
                    "Move the pointer past the shared edge to use the Mac."
                )
            else:
                self.peer.start()
                self.action.setText("Disconnect")
                self.detail.setText("The Windows mouse can now control this Mac.")
            self.status.setText("Connected")
            self.desk.connected = True
            self.mac_wants_sound = True
            self._start_sound()
        elif event == "message":
            self._message(args[0])
        elif event == "control":
            if self.peer and not self.peer.closed.is_set():
                self.status.setText(
                    {
                        "mac": "Connected · pointer on the Mac",
                        "windows": "Connected · pointer on Windows",
                    }.get(args[0], args[0])
                )
        elif event == "sound_format":
            self.sound_format = args
            self._send_sound_settings()
        elif event == "sound_error":
            self.sound_status.setText(args[0])
        elif event == "locked":
            self.stop()
            self.status.setText("Sharing stopped")
            self.detail.setText(
                "Too many wrong passwords were tried. Start sharing again to allow new attempts."
            )
        elif event in ("disconnected", "failed", "not_found"):
            self.peer = None
            self._stop_sound()
            if self.controller:
                self.controller.detach()
            self.desk.connected = False
            if self.is_windows:
                self.status.setText(
                    "Waiting for the Mac" if self.active else "Disconnected"
                )
                self.detail.setText(
                    f"{args[0]}. The Mac can connect again with the same password."
                )
                return
            self.active = False
            self.action.setText("Connect")
            self._set_inputs_enabled(True)
            if event == "not_found":
                self.address_box.setVisible(True)
                self.status.setText("Windows PC not found")
                self.detail.setText(
                    f"{args[0]} Check that sharing is on, or type the IP address shown in the Windows app."
                )
                self.address_input.setFocus()
            else:
                self.status.setText(
                    "Could not connect" if event == "failed" else "Disconnected"
                )
                self.detail.setText(args[0])

    def stop(self):
        self.generation += 1
        self._stop_sound()
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
        self._show_idle()
        self.action.setText("Start sharing" if self.is_windows else "Connect")
        self._set_inputs_enabled(True)

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
