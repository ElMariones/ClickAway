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
VERSION = 2
MAX_TEXT = 64 * 1024
MAX_FRAME = 400 * 1024  # JSON may expand a Unicode character into escape sequences.


def encode(message):
    payload = json.dumps(
        message, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError("Message exceeds the size limit")
    return struct.pack("!I", len(payload)) + payload


def read_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        part = sock.recv(count - len(chunks))
        if not part:
            raise ConnectionError("Peer disconnected")
        chunks.extend(part)
    return bytes(chunks)


def receive(sock):
    length = struct.unpack("!I", read_exact(sock, 4))[0]
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
            return True
        except queue.Full:
            # Do not do socket shutdown or UI work on a native hook callback.
            threading.Thread(
                target=self.close,
                args=("Connection too slow; control returned to Windows",),
                daemon=True,
            ).start()
            return False

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
            while not self.closed.is_set():
                try:
                    message = self.outbox.get(timeout=0.5)
                except queue.Empty:
                    message = {"type": "ping"}
                self.sock.sendall(encode(message))
                # A stream of queued data must not defeat the liveness deadline.
                if time.monotonic() - self.last_received > 3:
                    raise TimeoutError("No response from the other computer")
        except Exception as exc:
            self.close(f"Connection ended: {exc}")
