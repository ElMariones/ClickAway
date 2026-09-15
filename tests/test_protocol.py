import socket
import struct
import threading
import unittest

from clickaway.protocol import (
    MAX_FRAME,
    MAX_TEXT,
    encode,
    pairing_code,
    parse_pairing,
    receive,
    validate,
)


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

    def test_connection_code_roundtrip_and_whitespace(self):
        code = pairing_code("192.168.1.10", "a" * 64, "b" * 64)
        parsed = parse_pairing("\n" + code[:50] + "\n" + code[50:] + " ")
        self.assertEqual(parsed["host"], "192.168.1.10")
        self.assertEqual(parsed["fingerprint"], "a" * 64)

    def test_invalid_code(self):
        for code in (
            "",
            "CA1-",
            "CA1-!!!!",
            "CA2-nope",
            "CA1-" + "A" * 3000,
            pairing_code("not-an-ip", "a" * 64, "b" * 64),
            pairing_code("127.0.0.1", "a", "b" * 64),
        ):
            with self.subTest(code=code[:30]), self.assertRaises(ValueError):
                parse_pairing(code)

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
