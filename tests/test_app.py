import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from clickaway.app import App, NetworkPicker
from clickaway.connection import Host, connect
from clickaway.geometry import Screen


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

    def test_oversized_text_is_never_synced(self):
        self.read.return_value = "é" * 65536
        self.window._clipboard_tick()
        self.peer.send.assert_not_called()

    def test_network_picker_keeps_the_selected_address_when_refreshed(self):
        networks = [
            ("Wi-Fi: Home · 192.168.1.20", "192.168.1.20"),
            ("Ethernet · 10.0.0.5", "10.0.0.5"),
        ]
        picker = NetworkPicker(lambda: list(networks))
        picker.setCurrentIndex(1)
        networks.reverse()
        picker.refresh()
        self.assertEqual(picker.currentData(), "10.0.0.5")
        self.assertEqual(picker.currentText(), "Ethernet · 10.0.0.5")

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

    def mac(self, direction="to-mac", **peer_state):
        """A Mac app that has just heard from Windows, with a stub peer attached."""
        window = App(preview_mac=True)
        self.addCleanup(window.close)
        window.peer = self.peer
        state = {
            "type": "sound",
            "direction": direction,
            "sending": False,
            "listening": True,
            "volume": 0.25,
            "rate": 48000,
            "channels": 2,
            "latency": 250,
        }
        state.update(peer_state)
        window._message(state)
        return window

    def test_the_mac_follows_the_direction_volume_and_delay_windows_chose(self):
        mac = self.mac()
        self.assertEqual(mac.direction, "to-mac")
        self.assertEqual(mac.volume_slider.value(), 25)
        self.assertEqual(mac.volume_label.text(), "25%")
        self.assertEqual(mac.latency_picker.currentData(), 250)

    def test_a_mac_that_refuses_sound_tells_windows_it_is_not_listening(self):
        mac = self.mac()
        self.peer.send.reset_mock()
        mac.sound_toggle.setChecked(False)
        self.assertIsNone(mac.player)
        self.peer.send.assert_called()
        self.assertFalse(self.peer.send.call_args[0][0]["listening"])
        self.assertIn("off on this Mac", mac.sound_status.text())

    def test_the_mac_reports_whether_it_would_play_or_send(self):
        listening = self.mac("to-mac")._sound_state()
        self.assertTrue(listening["listening"])
        self.assertFalse(listening["sending"])
        sending = self.mac("to-pc")._sound_state()
        self.assertFalse(sending["listening"])
        self.assertEqual(sending["direction"], "to-pc")

    def test_streamed_sound_plays_instead_of_dropping_the_connection(self):
        """A sound frame is not an unknown message: treating it as one hung up.

        Windows routed every frame to the message handler, which closed the link
        on anything it did not recognise, so the Mac's first chunk of sound ended
        the session as soon as it arrived.
        """
        self.window.player = Mock()
        with patch.object(self.window, "_post") as post:
            self.window._incoming(
                self.window.generation, {"type": "sound-data", "samples": b"\x01\x02"}
            )
        self.peer.close.assert_not_called()
        self.window.player.push.assert_called_once_with(b"\x01\x02")
        # Sound must not queue behind the window's own work on the Qt thread.
        post.assert_not_called()

    def test_sound_arriving_with_no_player_open_is_dropped_quietly(self):
        self.window.player = None
        self.window._incoming(
            self.window.generation, {"type": "sound-data", "samples": b"\x01\x02"}
        )
        self.peer.close.assert_not_called()

    def test_other_messages_still_reach_the_qt_thread(self):
        with patch.object(self.window, "_post") as post:
            self.window._incoming(7, {"type": "clipboard", "text": "Copied"})
        post.assert_called_once_with(
            7, "message", {"type": "clipboard", "text": "Copied"}
        )

    def test_a_sound_frame_on_the_qt_thread_never_closes_the_connection(self):
        """Defence in depth: whichever route it takes, sound is not a hang-up."""
        self.window.player = Mock()
        self.window._message({"type": "sound-data", "samples": b"\x03\x04"})
        self.peer.close.assert_not_called()
        self.window.player.push.assert_called_once_with(b"\x03\x04")

    def test_a_burst_of_mac_sound_does_not_end_a_real_connection(self):
        """The whole Mac → PC path over a real socket, which used to hang up.

        The receiving computer used to read the first chunk of sound as a stray
        message and close the link, so this connects for real, streams sound the
        way the Mac does, and checks that the session is still standing.
        """
        window = self.window
        window.player = Mock()
        closed, accepted = queue.Queue(), queue.Queue()
        host = Host(
            "desk lamp",
            lambda peer, confirm: accepted.put(peer),
            lambda m: window._incoming(window.generation, m),
            closed.put,
            address="127.0.0.1",
            port=0,
            discovery_port=0,
        )
        host.start()
        self.addCleanup(host.stop)
        mac = connect(
            "desk lamp",
            Screen(0, 0, 1512, 982),
            lambda m: None,
            closed.put,
            address="127.0.0.1",
            port=host.port,
        )
        self.addCleanup(mac.close)
        mac.start()
        pc = accepted.get(timeout=5)
        chunks = [bytes([n % 256]) * 3840 for n in range(10)]
        for chunk in chunks:
            self.assertTrue(mac.send_sound(chunk))
        deadline = time.monotonic() + 5
        while (
            window.player.push.call_count < len(chunks)
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(
            [call.args[0] for call in window.player.push.call_args_list], chunks
        )
        self.assertFalse(pc.closed.is_set())
        self.assertFalse(mac.closed.is_set())
        self.assertTrue(closed.empty(), f"connection dropped: {list(closed.queue)}")

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
