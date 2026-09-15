import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from clickaway.app import App


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = App(preview=True)
        self.addCleanup(self.window.close)
        self.peer = Mock()
        self.peer.closed = threading.Event()
        self.window.peer = self.peer
        self.clip_patch = patch.object(self.window, "_read_clipboard")
        self.read = self.clip_patch.start()
        self.addCleanup(self.clip_patch.stop)

    def test_pairing_codes_and_oversized_text_are_never_synced(self):
        for text in ("CA1-secret", "é" * 65536):
            self.read.return_value = text
            self.window._clipboard_tick()
        self.peer.send.assert_not_called()

    def test_clipboard_is_sent_once_and_only_while_enabled(self):
        self.read.return_value = "A new copy"
        self.window._clipboard_tick()
        self.window._clipboard_tick()
        self.peer.send.assert_called_once_with(
            {"type": "clipboard", "text": "A new copy"}
        )
        self.window.clip_toggle.setChecked(False)
        self.read.return_value = "Private copy"
        self.window._clipboard_tick()
        self.assertEqual(self.peer.send.call_count, 1)

    def test_incoming_clipboard_is_not_echoed_and_disable_blocks_receive(self):
        with patch.object(QApplication, "clipboard") as clipboard:
            self.window._message({"type": "clipboard", "text": "Remote copy"})
            clipboard.return_value.setText.assert_called_once_with("Remote copy")
            self.read.return_value = "Remote copy"
            self.window._clipboard_tick()
            self.peer.send.assert_not_called()
            self.window.clip_toggle.setChecked(False)
            self.window._message({"type": "clipboard", "text": "Should be ignored"})
            clipboard.return_value.setText.assert_called_once()

    def test_late_connect_after_cancel_is_closed(self):
        late_peer = Mock()
        self.window.generation = 4
        self.window._event(3, "connected", (late_peer, None))
        late_peer.close.assert_called_once()
        self.assertIs(self.window.peer, self.peer)

    def test_preview_never_starts_native_capture(self):
        self.window.toggle()
        self.assertFalse(self.window.active)
        self.assertIsNone(self.window.host)
        self.assertIsNone(self.window.controller)


if __name__ == "__main__":
    unittest.main()
