"""Versioned, bounded JSON messages over an authenticated TLS socket."""

import base64
import hashlib
import ipaddress
import json
import math
import queue
import re
import socket
import struct
import threading
import time

PORT = 49624
VERSION = 1
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


def validate(m):
    if not isinstance(m, dict) or not isinstance(m.get("type"), str):
        raise ValueError("Invalid message")
    kind = m["type"]
    if kind in ("hello", "ready"):
        if m.get("version") != VERSION:
            raise ValueError("Different ClickAway versions; update both apps")
        if kind == "hello":
            if not isinstance(m.get("token"), str) or not re.fullmatch(
                r"[a-f0-9]{64}", m["token"]
            ):
                raise ValueError("Invalid pairing key")
            for key in ("width", "height"):
                if (
                    type(m.get(key)) not in (int, float)
                    or not math.isfinite(m[key])
                    or not 100 <= m[key] <= 32768
                ):
                    raise ValueError("Invalid display dimensions")
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


def pairing_code(host, fingerprint, token, port=PORT):
    payload = json.dumps(
        {"host": host, "port": port, "fingerprint": fingerprint, "token": token},
        separators=(",", ":"),
    ).encode()
    return "CA1-" + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def parse_pairing(code):
    code = "".join(code.split())
    if not code.startswith("CA1-") or len(code) > 2048:
        raise ValueError("Paste the complete CA1- connection code from Windows")
    try:
        data = json.loads(
            base64.b64decode(
                code[4:] + "=" * (-len(code[4:]) % 4), altchars=b"-_", validate=True
            )
        )
        ipaddress.IPv4Address(data["host"])
        if type(data["port"]) is not int or not 1024 <= data["port"] <= 65535:
            raise ValueError()
        if not all(
            isinstance(data[k], str) and re.fullmatch(r"[a-f0-9]{64}", data[k])
            for k in ("fingerprint", "token")
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("That connection code is incomplete or invalid") from exc
    return data


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
