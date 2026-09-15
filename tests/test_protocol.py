import socket
import struct
import threading
import unittest

from clickaway.protocol import MAX_FRAME, MAX_TEXT, VERSION, encode, receive, validate


class ProtocolTests(unittest.TestCase):
    def test_unicode_clipboard_round_trip_with_fragmented_tcp_packets(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        second.settimeout(2)
        msg = {"type": "clipboard", "text": "Hello\nEspaña 👋\t日本語\u0000"}
        frame = encode(msg)

        def send():
            for start in range(0, len(frame), 3):
                first.sendall(frame[start : start + 3])

        thread = threading.Thread(target=send)
        thread.start()
        self.assertEqual(receive(second), msg)
        thread.join()

    def test_pairing_messages_are_strict(self):
        key, proof = "ab" * 256, "cd" * 32
        validate({"type": "hello", "version": VERSION, "key": key})
        validate({"type": "verify", "version": VERSION, "key": key, "proof": proof})
        validate({"type": "confirm", "proof": proof, "width": 1512, "height": 982})
        validate({"type": "ready", "version": VERSION})
        for message in (
            {"type": "hello", "version": 1, "token": "b" * 64},
            {"type": "hello", "version": VERSION, "key": key[:-2]},
            {"type": "hello", "version": VERSION, "key": key.upper()},
            {"type": "hello", "version": VERSION, "key": key + "\n"},
            {"type": "verify", "version": VERSION, "key": key, "proof": "00"},
            {"type": "confirm", "proof": proof, "width": 50, "height": 982},
            {"type": "confirm", "width": 1512, "height": 982},
        ):
            with self.subTest(message=str(message)[:60]), self.assertRaises(ValueError):
                validate(message)

    def test_frame_size_rejected_before_body_read(self):
        for length in (0, MAX_FRAME + 1, 0xFFFFFFFF):
            first, second = socket.socketpair()
            try:
                first.sendall(struct.pack("!I", length))
                with self.assertRaises(ValueError):
                    receive(second)
            finally:
                first.close()
                second.close()

    def test_invalid_events_cannot_reach_native_input(self):
        invalid = [
            [],
            {},
            {"type": "shell"},
            {"type": "move", "x": float("nan"), "y": 0},
            {"type": "move", "x": True, "y": 1},
            {"type": "move", "x": -1, "y": 1},
            {"type": "button", "button": "left", "down": "false"},
            {"type": "scroll", "dx": 0.1, "dy": 0},
            {"type": "clipboard", "text": "é" * (MAX_TEXT // 2 + 1)},
            {"type": "layout", "side": "up", "speed": 1},
            {"type": "ready", "version": 999},
        ]
        for message in invalid:
            with self.subTest(message=str(message)[:80]), self.assertRaises(ValueError):
                validate(message)

    def test_closed_midframe_is_detected(self):
        first, second = socket.socketpair()
        self.addCleanup(second.close)
        first.sendall(struct.pack("!I", 20) + b"{")
        first.close()
        with self.assertRaises(ConnectionError):
            receive(second)

    def test_clipboard_limit_counts_utf8_bytes(self):
        validate({"type": "clipboard", "text": "é" * (MAX_TEXT // 2)})
        frame = encode({"type": "clipboard", "text": "\x01" * MAX_TEXT})
        self.assertLessEqual(len(frame) - 4, MAX_FRAME)


if __name__ == "__main__":
    unittest.main()
