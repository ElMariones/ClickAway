"""Playing the Windows sound stream on the Mac, and the pure helpers it needs."""

import array
from collections import deque
import sys
import threading

from PySide6.QtCore import QObject, QTimer

SAMPLE_BYTES = 2  # Signed 16-bit samples travel over the wire, little-endian.
CHUNK_MS = 20
LATENCIES = (
    ("Low · 60 ms", 60),
    ("Balanced · 120 ms", 120),
    ("Safe · 250 ms", 250),
)
DEFAULT_LATENCY = 120


def byte_count(rate, channels, milliseconds):
    """Whole audio frames worth of bytes for a length of sound."""
    frame = channels * SAMPLE_BYTES
    return max(frame, round(rate * milliseconds / 1000) * frame)


def to_int16(data, channels, floating):
    """One captured packet as interleaved 16-bit samples, keeping at most 2 channels."""
    if floating:
        samples = array.array("f")
        samples.frombytes(data[: len(data) - len(data) % 4])
        if channels > 2:
            samples = _front_pair(samples, channels)
        output = array.array(
            "h",
            (round(32767 * (v if -1 < v < 1 else 1 if v > 0 else -1)) for v in samples),
        )
    else:
        output = array.array("h")
        output.frombytes(data[: len(data) - len(data) % 2])
        if channels > 2:
            output = _front_pair(output, channels)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def planes_to_int16(data, channels, frames):
    """Channel planes laid end to end, as macOS delivers them, interleaved into samples.

    Each plane holds every sample of one channel as a 32-bit float, so the left
    channel is followed by the right one rather than alternating with it.
    """
    wanted = min(channels, 2)
    if frames <= 0 or len(data) < frames * channels * 4:
        return b""
    scaled = array.array("h")
    scaled.frombytes(to_int16(data[: frames * channels * 4], 1, True))
    if wanted == 1:
        return scaled[:frames].tobytes()
    output = array.array("h", bytes(4 * frames))
    output[0::2] = scaled[:frames]
    output[1::2] = scaled[frames : 2 * frames]
    return output.tobytes()


def _front_pair(samples, channels):
    """Surround sound reaches the Mac as its front left and right channels."""
    kept = array.array(samples.typecode)
    for start in range(0, len(samples) - channels + 1, channels):
        kept.extend(samples[start : start + 2])
    return kept


class Chunker:
    """Collects captured sound and hands it on in equal pieces."""

    def __init__(self, size, deliver):
        self.size, self.deliver = size, deliver
        self.pending = bytearray()

    def add(self, data):
        self.pending += data
        while len(self.pending) >= self.size:
            self.deliver(bytes(self.pending[: self.size]))
            del self.pending[: self.size]


class JitterBuffer:
    """Collects sound until ``target`` bytes wait, so the network can run late."""

    def __init__(self, target, limit):
        self.target, self.limit = target, limit
        self.chunks = deque()
        self.size = 0
        self.playing = False
        self.dropped = 0
        self.lock = threading.Lock()

    def push(self, chunk):
        with self.lock:
            self.chunks.append(chunk)
            self.size += len(chunk)
            # Sound is only worth hearing on time: throw away what fell behind.
            while self.size > self.limit and len(self.chunks) > 1:
                stale = self.chunks.popleft()
                self.size -= len(stale)
                self.dropped += len(stale)
            if self.size >= self.target:
                self.playing = True

    def take(self, count):
        """Up to ``count`` bytes in order, or nothing while the buffer refills."""
        with self.lock:
            if not self.playing:
                return b""
            output = bytearray()
            while self.chunks and len(output) < count:
                room = count - len(output)
                chunk = self.chunks[0]
                if len(chunk) <= room:
                    output += self.chunks.popleft()
                    self.size -= len(chunk)
                else:
                    output += chunk[:room]
                    self.chunks[0] = chunk[room:]
                    self.size -= room
            if not self.chunks:
                self.playing = (
                    False  # Refill before playing again, rather than crackle.
                )
            return bytes(output)

    def clear(self):
        with self.lock:
            self.chunks.clear()
            self.size = 0
            self.playing = False


class SoundPlayer(QObject):
    """Plays the streamed Windows sound through the Mac's speakers.

    ``configure``, ``set_volume`` and ``stop`` belong to the Qt thread, which owns
    the speakers. ``push`` is safe to call from the network thread: it only fills
    a buffer that the Qt thread hands to the speakers a little later.
    """

    def __init__(self, parent=None, on_status=None):
        super().__init__(parent)
        self.on_status = on_status or (lambda text: None)
        self.sink = self.stream = None
        self.buffer = None
        self.settings = None  # (rate, channels, latency)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._pump)

    def configure(self, rate, channels, latency, volume):
        """Open the speakers for this format, or keep the open ones if unchanged."""
        if self.settings != (rate, channels, latency):
            self.stop()
            self._open(rate, channels, latency)
            self.settings = (rate, channels, latency)
        self.set_volume(volume)

    def _open(self, rate, channels, latency):
        # Qt's sound module is loaded here and nowhere else, so a computer that
        # cannot play sound still gets its mouse, clipboard and everything else.
        from PySide6.QtMultimedia import (
            QAudioFormat,
            QAudioSink,
            QMediaDevices,
        )

        device = QMediaDevices.defaultAudioOutput()
        if device is None or device.isNull():
            raise RuntimeError("This Mac has no sound output device.")
        wanted = QAudioFormat()
        wanted.setSampleRate(rate)
        wanted.setChannelCount(channels)
        wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(wanted):
            raise RuntimeError(
                f"{device.description()} cannot play {rate} Hz sound from Windows."
            )
        self.sink = QAudioSink(device, wanted, self)
        # Twice the chosen delay, so a late pump cannot starve the speakers.
        self.sink.setBufferSize(byte_count(rate, channels, latency * 2))
        self.buffer = JitterBuffer(
            byte_count(rate, channels, latency),
            byte_count(rate, channels, max(4 * latency, 600)),
        )
        self.stream = self.sink.start()
        if self.stream is None:
            self.sink = None
            raise RuntimeError(f"{device.description()} could not be opened.")
        self.timer.start(max(5, CHUNK_MS // 2))
        self.on_status(
            f"Playing Windows sound · {rate // 1000} kHz"
            + (" stereo" if channels == 2 else " mono")
        )

    def push(self, chunk):
        if self.buffer is not None:
            self.buffer.push(chunk)

    def set_volume(self, volume):
        if self.sink is not None:
            from PySide6.QtMultimedia import QAudio

            self.sink.setVolume(
                QAudio.convertVolume(
                    volume,
                    QAudio.VolumeScale.LogarithmicVolumeScale,
                    QAudio.VolumeScale.LinearVolumeScale,
                )
            )

    def _pump(self):
        if self.stream is None:
            return
        try:
            free = self.sink.bytesFree()
            if free > 0:
                data = self.buffer.take(free)
                if data:
                    self.stream.write(data)
        except (RuntimeError, OSError) as exc:
            self.stop()
            self.on_status(f"Sound stopped: {exc}")

    def stop(self):
        self.timer.stop()
        self.settings = None
        self.stream = None
        if self.buffer is not None:
            self.buffer.clear()
        if self.sink is not None:
            sink, self.sink = self.sink, None
            sink.stop()
            sink.deleteLater()
