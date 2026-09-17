"""Versioned, bounded JSON messages over an authenticated TLS socket."""

import hashlib
import json
import math
import queue
import re
import socket
import struct
import threading
import time

from .pake import KEY_BYTES

PORT = 49624
VERSION = 3
MAX_TEXT = 64 * 1024
MAX_FRAME = 400 * 1024  # JSON may expand a Unicode character into escape sequences.
SOUND_FRAME = 0x80000000  # This bit of the length word marks raw samples, not JSON.
MAX_SOUND = 32 * 1024
SOUND_QUEUE = 25  # Half a second of sound; older chunks are dropped, never queued.
MIN_LATENCY, MAX_LATENCY = 40, 500  # How long the Mac may hold sound before playing.


def encode(message):
    payload = json.dumps(
        message, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError("Message exceeds the size limit")
    return struct.pack("!I", len(payload)) + payload


def encode_sound(chunk):
    """Sound skips JSON: it is a wall of samples, sent and played in order."""
    if not 0 < len(chunk) <= MAX_SOUND:
        raise ValueError("Sound chunk exceeds the size limit")
    return struct.pack("!I", len(chunk) | SOUND_FRAME) + chunk


def read_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        part = sock.recv(count - len(chunks))
        if not part:
            raise ConnectionError("Peer disconnected")
        chunks.extend(part)
    return bytes(chunks)


def receive(sock):
    header = struct.unpack("!I", read_exact(sock, 4))[0]
    length = header & ~SOUND_FRAME
    if header & SOUND_FRAME:
        if not 0 < length <= MAX_SOUND:
            raise ValueError("Invalid sound frame length")
        return {"type": "sound-data", "samples": read_exact(sock, length)}
    if not 0 < length <= MAX_FRAME:
        raise ValueError("Invalid frame length")
    message = json.loads(read_exact(sock, length).decode("utf-8"))
    validate(message)
    return message


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(f"[a-f0-9]{{{length}}}", value)


def _dimensions(m):
    for key in ("width", "height"):
        if (
            type(m.get(key)) not in (int, float)
            or not math.isfinite(m[key])
            or not 100 <= m[key] <= 32768
        ):
            raise ValueError("Invalid display dimensions")


def validate(m):
    if not isinstance(m, dict) or not isinstance(m.get("type"), str):
        raise ValueError("Invalid message")
    kind = m["type"]
    if kind in ("hello", "verify", "ready"):
        if m.get("version") != VERSION:
            raise ValueError("Different ClickAway versions; update both apps")
        if kind != "ready" and not _hex(m.get("key"), 2 * KEY_BYTES):
            raise ValueError("Invalid key exchange")
        if kind == "verify" and not _hex(m.get("proof"), 64):
            raise ValueError("Invalid password proof")
    elif kind == "confirm":
        if not _hex(m.get("proof"), 64):
            raise ValueError("Invalid password proof")
        _dimensions(m)
    elif kind in ("enter", "move"):
        for key in ("x", "y"):
            if (
                type(m.get(key)) not in (int, float)
                or not math.isfinite(m[key])
                or not 0 <= m[key] <= 32768
            ):
                raise ValueError("Invalid pointer coordinates")
    elif kind == "button":
        if (
            m.get("button") not in ("left", "right", "middle", "back", "forward")
            or type(m.get("down")) is not bool
        ):
            raise ValueError("Invalid mouse button")
    elif kind == "scroll":
        for key in ("dx", "dy"):
            if type(m.get(key)) is not int or abs(m[key]) > 32768:
                raise ValueError("Invalid scroll delta")
    elif kind == "clipboard":
        if (
            not isinstance(m.get("text"), str)
            or len(m["text"].encode("utf-8")) > MAX_TEXT
        ):
            raise ValueError("Clipboard text exceeds 64 KiB")
    elif kind == "sound":
        if (
            type(m.get("playing")) is not bool
            or type(m.get("volume")) not in (int, float)
            or not 0 <= m["volume"] <= 1
            or type(m.get("rate")) is not int
            or not 8000 <= m["rate"] <= 192000
            or m.get("channels") not in (1, 2)
            or type(m.get("latency")) is not int
            or not MIN_LATENCY <= m["latency"] <= MAX_LATENCY
        ):
            raise ValueError("Invalid sound settings")
    elif kind == "layout":
        if (
            m.get("side") not in ("left", "right")
            or type(m.get("speed")) not in (int, float)
            or not 0.25 <= m["speed"] <= 3
        ):
            raise ValueError("Invalid screen layout")
    elif kind not in ("ping", "leave"):
        raise ValueError("Unknown message type")


def fingerprint(cert_der):
    return hashlib.sha256(cert_der).hexdigest()


class Peer:
    """One reader and one writer. Hooks only enqueue; they never wait on I/O."""

    def __init__(self, sock, on_message, on_close):
        self.sock = sock
        self.on_message, self.on_close = on_message, on_close
        self.outbox = queue.Queue(maxsize=1024)
        self.sound = queue.Queue(maxsize=SOUND_QUEUE)
        self.waiting = threading.Event()
        self.closed = threading.Event()
        self._close_lock = threading.Lock()
        self.last_received = time.monotonic()
        self.sock.settimeout(3.0)

    def start(self):
        threading.Thread(
            target=self._reader, daemon=True, name="clickaway-read"
        ).start()
        threading.Thread(
            target=self._writer, daemon=True, name="clickaway-write"
        ).start()

    def send(self, message):
        if self.closed.is_set():
            return False
        try:
            self.outbox.put_nowait(message)
            self.waiting.set()
            return True
        except queue.Full:
            # Do not do socket shutdown or UI work on a native hook callback.
            threading.Thread(
                target=self.close,
                args=("Connection too slow; control returned to Windows",),
                daemon=True,
            ).start()
            return False

    def send_sound(self, chunk):
        """Sound that cannot be sent now is stale: drop the oldest, keep the newest."""
        if self.closed.is_set():
            return False
        try:
            self.sound.put_nowait(chunk)
        except queue.Full:
            try:
                self.sound.get_nowait()
                self.sound.put_nowait(chunk)
            except (queue.Empty, queue.Full):
                return False
        self.waiting.set()
        return True

    def close(self, reason="Disconnected"):
        with self._close_lock:
            if self.closed.is_set():
                return
            self.closed.set()
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
        self.on_close(reason)

    def _reader(self):
        try:
            while not self.closed.is_set():
                message = receive(self.sock)
                self.last_received = time.monotonic()
                if message["type"] != "ping":
                    self.on_message(message)
        except Exception as exc:
            self.close(f"Connection ended: {exc}")

    def _writer(self):
        try:
            sent = time.monotonic()
            while not self.closed.is_set():
                frame = self._next_frame()
                if frame is None:
                    idle = 0.5 - (time.monotonic() - sent)
                    if idle > 0:
                        # Anything queued wakes this immediately; otherwise, heartbeat.
                        self.waiting.wait(idle)
                        self.waiting.clear()
                        continue
                    frame = encode({"type": "ping"})
                self.sock.sendall(frame)
                sent = time.monotonic()
                # A stream of queued data must not defeat the liveness deadline.
                if time.monotonic() - self.last_received > 3:
                    raise TimeoutError("No response from the other computer")
        except Exception as exc:
            self.close(f"Connection ended: {exc}")

    def _next_frame(self):
        """The mouse never waits behind sound: control messages always go first."""
        try:
            return encode(self.outbox.get_nowait())
        except queue.Empty:
            pass
        try:
            return encode_sound(self.sound.get_nowait())
        except queue.Empty:
            return None
