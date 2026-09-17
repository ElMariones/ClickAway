import queue
import socket
import threading
import time
import unittest

from clickaway.connection import Host, connect, discover
from clickaway.geometry import Screen
from clickaway.pake import PairingError
from clickaway.protocol import VERSION, Peer

PASSWORD = "desk lamp"


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.accepted = queue.Queue()
        self.host_messages = queue.Queue()
        self.mac_messages = queue.Queue()
        self.closed = queue.Queue()
        self.locked = threading.Event()
        self.host = Host(
            PASSWORD,
            lambda peer, confirm: self.accepted.put((peer, confirm)),
            self.host_messages.put,
            self.closed.put,
            on_locked=self.locked.set,
            address="127.0.0.1",
            port=0,
            discovery_port=0,
            max_failures=3,
        )
        self.host.start()
        self.addCleanup(self.host.stop)

    def client(self, password=PASSWORD):
        peer = connect(
            password,
            Screen(0, 0, 1512, 982),
            self.mac_messages.put,
            self.closed.put,
            address="127.0.0.1",
            port=self.host.port,
        )
        self.addCleanup(peer.close)
        peer.start()
        return peer

    def test_real_tls_connection_bidirectional_ordered_input_and_clipboard(self):
        mac = self.client()
        pc, confirm = self.accepted.get(timeout=3)
        self.assertEqual((confirm["width"], confirm["height"]), (1512, 982))
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

    def test_sound_reaches_the_mac_as_raw_samples(self):
        self.client()
        pc, _ = self.accepted.get(timeout=3)
        samples = bytes(range(256)) * 8
        self.assertTrue(pc.send_sound(samples))
        self.assertEqual(
            self.mac_messages.get(timeout=3),
            {"type": "sound-data", "samples": samples},
        )

    def test_wrong_password_is_rejected_and_listener_survives(self):
        with self.assertRaises(PairingError):
            self.client("desk lamb")
        self.assertTrue(self.accepted.empty())
        self.client()
        self.accepted.get(timeout=3)
        self.assertFalse(self.locked.is_set())

    def test_repeated_wrong_passwords_stop_sharing(self):
        for _ in range(3):
            with self.assertRaises((PairingError, ConnectionError)):
                self.client("not the password")
        self.assertTrue(self.locked.wait(3))
        with self.assertRaises(ConnectionError):
            self.client()
        self.assertTrue(self.accepted.empty())

    def test_short_password_cannot_start_sharing(self):
        with self.assertRaises(ValueError):
            Host("12345", None, None, None, address="127.0.0.1", port=0)

    def test_discovery_finds_the_sharing_computer(self):
        found = discover(
            port=self.host.discovery_port, targets=("127.0.0.1",), timeout=2
        )
        self.assertEqual(
            [(f["address"], f["port"], f["version"]) for f in found],
            [("127.0.0.1", self.host.port, VERSION)],
        )
        self.host.stop()
        self.assertEqual(
            discover(
                port=self.host.discovery_port, targets=("127.0.0.1",), timeout=0.5
            ),
            [],
        )

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
