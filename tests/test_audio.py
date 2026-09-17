import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import array
import queue
import socket
import struct
import threading
import unittest

from clickaway.audio import (
    DEFAULT_LATENCY,
    LATENCIES,
    Chunker,
    JitterBuffer,
    byte_count,
    planes_to_int16,
    to_int16,
)
from clickaway.protocol import (
    DIRECTIONS,
    MAX_LATENCY,
    MIN_LATENCY,
    MAX_SOUND,
    SOUND_FRAME,
    SOUND_QUEUE,
    Peer,
    encode_sound,
    receive,
    validate,
)


def floats(*values):
    return struct.pack(f"<{len(values)}f", *values)


class ConversionTests(unittest.TestCase):
    def test_floats_become_samples_and_loud_sound_cannot_wrap_around(self):
        converted = array.array("h")
        converted.frombytes(to_int16(floats(0.0, 0.5, -0.5, 4.0, -4.0, 1.0), 1, True))
        self.assertEqual(list(converted), [0, 16384, -16384, 32767, -32767, 32767])

    def test_surround_sound_arrives_as_its_front_pair(self):
        converted = array.array("h")
        packet = floats(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.0, 0.0)
        converted.frombytes(to_int16(packet, 6, True))
        self.assertEqual(list(converted), [3277, 6553, 22937, 26214])

    def test_channel_planes_are_interleaved_the_way_macos_sends_them(self):
        packet = floats(0.5, -0.5, 1.0, 0.0)  # Left plane, then right plane.
        converted = array.array("h")
        converted.frombytes(planes_to_int16(packet, 2, 2))
        self.assertEqual(list(converted), [16384, 32767, -16384, 0])

    def test_a_short_plane_packet_is_dropped_rather_than_misread(self):
        self.assertEqual(planes_to_int16(floats(0.5, -0.5), 2, 4), b"")
        self.assertEqual(planes_to_int16(b"", 2, 0), b"")

    def test_sixteen_bit_packets_pass_through_untouched(self):
        samples = array.array("h", [0, -1, 32767, -32768]).tobytes()
        self.assertEqual(to_int16(samples, 2, False), samples)

    def test_a_partial_sample_at_the_end_is_ignored(self):
        self.assertEqual(to_int16(floats(1.0) + b"\x01\x02\x03", 1, True), b"\xff\x7f")

    def test_byte_count_is_whole_audio_frames(self):
        self.assertEqual(byte_count(48000, 2, 120), 48000 * 2 * 2 * 120 // 1000)
        self.assertEqual(byte_count(48000, 2, 0), 4)


class ChunkerTests(unittest.TestCase):
    def test_sound_is_handed_on_in_equal_pieces_with_the_rest_kept(self):
        pieces = []
        chunker = Chunker(4, pieces.append)
        chunker.add(b"abcde")
        chunker.add(b"fgh")
        self.assertEqual(pieces, [b"abcd", b"efgh"])
        self.assertEqual(bytes(chunker.pending), b"")
        chunker.add(b"ij")
        self.assertEqual(pieces, [b"abcd", b"efgh"])
        self.assertEqual(bytes(chunker.pending), b"ij")


class JitterBufferTests(unittest.TestCase):
    def test_nothing_plays_until_the_buffer_has_filled(self):
        buffer = JitterBuffer(target=8, limit=64)
        buffer.push(b"1234")
        self.assertEqual(buffer.take(4), b"")
        buffer.push(b"5678")
        self.assertEqual(buffer.take(6), b"123456")
        self.assertEqual(buffer.take(6), b"78")

    def test_an_empty_buffer_refills_before_playing_again(self):
        buffer = JitterBuffer(target=4, limit=64)
        buffer.push(b"abcd")
        self.assertEqual(buffer.take(64), b"abcd")
        buffer.push(b"ef")
        self.assertEqual(buffer.take(64), b"")

    def test_sound_that_fell_behind_is_dropped_rather_than_delayed(self):
        buffer = JitterBuffer(target=4, limit=10)
        for chunk in (b"1111", b"2222", b"3333", b"4444"):
            buffer.push(chunk)
        self.assertLessEqual(buffer.size, 10)
        self.assertEqual(buffer.take(64), b"33334444")
        self.assertEqual(buffer.dropped, 8)


class SoundFrameTests(unittest.TestCase):
    def test_samples_survive_a_fragmented_connection(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        second.settimeout(2)
        samples = bytes(range(256)) * 4
        frame = encode_sound(samples)

        def send():
            for start in range(0, len(frame), 7):
                first.sendall(frame[start : start + 7])

        thread = threading.Thread(target=send)
        thread.start()
        self.assertEqual(receive(second), {"type": "sound-data", "samples": samples})
        thread.join()

    def test_sound_frames_are_bounded(self):
        with self.assertRaises(ValueError):
            encode_sound(b"")
        with self.assertRaises(ValueError):
            encode_sound(bytes(MAX_SOUND + 1))
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.sendall(struct.pack("!I", (MAX_SOUND + 1) | SOUND_FRAME))
        with self.assertRaises(ValueError):
            receive(second)

    def test_sound_settings_are_checked_before_they_reach_the_speakers(self):
        good = {
            "type": "sound",
            "direction": "to-pc",
            "sending": True,
            "listening": False,
            "volume": 0.8,
            "rate": 48000,
            "channels": 2,
            "latency": 120,
        }
        validate(good)
        for change in (
            {"direction": "sideways"},
            {"direction": None},
            {"sending": "yes"},
            {"listening": 1},
            {"volume": 1.5},
            {"volume": True},
            {"rate": 4000},
            {"rate": 48000.0},
            {"channels": 6},
            {"latency": MAX_LATENCY + 1},
            {"latency": 0},
        ):
            message = dict(good, **change)
            with self.subTest(change=str(change)), self.assertRaises(ValueError):
                validate(message)

    def test_both_sound_directions_are_understood(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                validate(
                    {
                        "type": "sound",
                        "direction": direction,
                        "sending": False,
                        "listening": True,
                        "volume": 0,
                        "rate": 44100,
                        "channels": 1,
                        "latency": MIN_LATENCY,
                    }
                )

    def test_every_offered_delay_is_one_the_other_computer_accepts(self):
        for name, value in LATENCIES:
            with self.subTest(delay=name):
                self.assertTrue(MIN_LATENCY <= value <= MAX_LATENCY)
        self.assertIn(DEFAULT_LATENCY, [value for _, value in LATENCIES])


class PeerSoundTests(unittest.TestCase):
    def peer(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        second.settimeout(3)
        closed = queue.Queue()
        peer = Peer(first, lambda m: None, closed.put)
        self.addCleanup(peer.close)
        return peer, second, closed

    def test_the_mouse_never_waits_behind_queued_sound(self):
        peer, other, _ = self.peer()
        for index in range(SOUND_QUEUE):
            peer.send_sound(bytes([index]) * 64)
        peer.send({"type": "leave"})
        peer.start()
        self.assertEqual(receive(other), {"type": "leave"})
        self.assertEqual(receive(other)["type"], "sound-data")

    def test_a_full_sound_queue_drops_the_oldest_and_keeps_the_connection(self):
        peer, _, closed = self.peer()
        for index in range(SOUND_QUEUE + 3):
            self.assertTrue(peer.send_sound(bytes([index]) * 64))
        self.assertEqual(peer.sound.qsize(), SOUND_QUEUE)
        self.assertEqual(peer.sound.get_nowait(), bytes([3]) * 64)
        self.assertFalse(peer.closed.is_set())
        self.assertTrue(closed.empty())


if __name__ == "__main__":
    unittest.main()
