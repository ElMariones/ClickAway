import queue
import socket
import threading
import time
import unittest

from clickaway.connection import Host, connect
from clickaway.geometry import Screen
from clickaway.protocol import Peer


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.accepted = queue.Queue()
        self.host_messages = queue.Queue()
        self.mac_messages = queue.Queue()
        self.closed = queue.Queue()
        self.host = Host(
            lambda peer, hello: self.accepted.put(peer),
            self.host_messages.put,
            self.closed.put,
            port=0,
        )
        self.host.start()
        self.addCleanup(self.host.stop)
        self.details = {
            "host": "127.0.0.1",
            "port": self.host.port,
            "fingerprint": self.host.fingerprint,
            "token": self.host.token,
        }

    def client(self, details=None):
        peer = connect(
            details or self.details,
            Screen(0, 0, 1512, 982),
            self.mac_messages.put,
            self.closed.put,
        )
        self.addCleanup(peer.close)
        peer.start()
        return peer

    def test_real_tls_connection_bidirectional_ordered_input_and_clipboard(self):
        mac = self.client()
        pc = self.accepted.get(timeout=3)
        events = [{"type": "enter", "x": 1509, "y": 420}]
        events += [{"type": "move", "x": 1400 - i, "y": 420} for i in range(200)]
        events += [
            {"type": "button", "button": "left", "down": True},
            {"type": "button", "button": "left", "down": False},
            {"type": "leave"},
        ]
        for event in events:
            self.assertTrue(pc.send(event))
        for event in events:
            self.assertEqual(self.mac_messages.get(timeout=3), event)
        clipboard = {"type": "clipboard", "text": "From Mac → Windows 👋"}
        mac.send(clipboard)
        self.assertEqual(self.host_messages.get(timeout=3), clipboard)
        self.assertIn(pc.sock.version(), ("TLSv1.2", "TLSv1.3"))

    def test_wrong_certificate_pin_rejected_before_authentication(self):
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.client({**self.details, "fingerprint": "0" * 64})
        self.assertTrue(self.accepted.empty())
        self.client()  # A failed pairing does not kill the listener.
        self.accepted.get(timeout=3)

    def test_wrong_pairing_secret_rejected(self):
        with self.assertRaises((ConnectionError, OSError)):
            self.client({**self.details, "token": "0" * 64})
        self.assertTrue(self.accepted.empty())

    def test_disconnect_notifies_and_allows_reconnect(self):
        first = self.client()
        self.accepted.get(timeout=3)
        first.close()
        deadline = time.monotonic() + 3
        while not self.host.peer.closed.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.host.peer.closed.is_set())
        self.client()
        self.accepted.get(timeout=3)

    def test_unresponsive_peer_triggers_timeout(self):
        first, second = socket.socketpair()
        self.addCleanup(second.close)
        closed = queue.Queue()
        peer = Peer(first, lambda m: None, closed.put)
        self.addCleanup(peer.close)
        peer.start()
        reason = closed.get(timeout=5)
        self.assertTrue(peer.closed.is_set())
        self.assertIn("Connection ended", reason)

    def test_queue_overflow_fails_closed(self):
        first, second = socket.socketpair()
        self.addCleanup(second.close)
        done = threading.Event()
        peer = Peer(first, lambda m: None, lambda r: done.set())
        self.addCleanup(peer.close)
        for _ in range(1024):
            self.assertTrue(peer.send({"type": "ping"}))
        self.assertFalse(peer.send({"type": "ping"}))
        self.assertTrue(done.wait(2))


if __name__ == "__main__":
    unittest.main()
