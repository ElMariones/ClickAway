"""Password pairing over TLS, and discovery of the sharing Windows PC on the LAN."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .pake import Exchange, normalize_password
from .protocol import PORT, VERSION, Peer, encode, fingerprint, receive

MAX_FAILURES = 10


def create_identity(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ClickAway")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = Path(directory) / "cert.pem", Path(directory) / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context, fingerprint(cert.public_bytes(serialization.Encoding.DER))


def local_addresses():
    addresses = []
    try:
        addresses = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(
                    socket.gethostname(), None, socket.AF_INET
                )
                if not item[4][0].startswith("127.")
            }
        )
    except OSError:
        pass
    return addresses or ["127.0.0.1"]


def _bind(kind, address, port):
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((address, port))
    except Exception:
        sock.close()
        raise
    sock.settimeout(0.5)
    return sock


class Host:
    """Windows side: answers discovery and accepts one Mac that knows the password."""

    def __init__(
        self,
        password,
        on_connect,
        on_message,
        on_close,
        on_locked=None,
        address="0.0.0.0",
        port=PORT,
        discovery_port=PORT,
        max_failures=MAX_FAILURES,
    ):
        self.password = normalize_password(password)
        self.on_connect, self.on_message = on_connect, on_message
        self.on_close, self.on_locked = on_close, on_locked
        self.max_failures, self.failures = max_failures, 0
        with tempfile.TemporaryDirectory(prefix="clickaway-") as directory:
            self.context, self.fingerprint = create_identity(directory)
        self.stop_event = threading.Event()
        self.peer = None
        self.discovery = None
        self.listener = _bind(socket.SOCK_STREAM, address, port)
        try:
            self.listener.listen(4)
            if discovery_port is not None:
                self.discovery = _bind(socket.SOCK_DGRAM, address, discovery_port)
        except Exception:
            self.listener.close()
            raise
        self.port = self.listener.getsockname()[1]
        self.discovery_port = (
            self.discovery.getsockname()[1] if self.discovery else None
        )

    def start(self):
        threading.Thread(
            target=self._accept, daemon=True, name="clickaway-host"
        ).start()
        if self.discovery:
            threading.Thread(
                target=self._answer, daemon=True, name="clickaway-discovery"
            ).start()

    def _answer(self):
        reply = json.dumps(
            {
                "clickaway": "here",
                "version": VERSION,
                "port": self.port,
                "name": socket.gethostname()[:64],
            }
        ).encode()
        while not self.stop_event.is_set():
            try:
                data, sender = self.discovery.recvfrom(512)
                probe = json.loads(data.decode("utf-8"))
                if isinstance(probe, dict) and probe.get("clickaway") == "discover":
                    self.discovery.sendto(reply, sender)
            except (OSError, ValueError):
                # Timeouts, junk datagrams and ICMP resets. stop() closes the socket.
                pass

    def _accept(self):
        while not self.stop_event.is_set():
            conn = None
            try:
                conn, _ = self.listener.accept()
                if self.peer and not self.peer.closed.is_set():
                    conn.close()
                    continue
                conn.settimeout(3)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn = self.context.wrap_socket(conn, server_side=True)
                confirm = self._pair(conn)
                if self.stop_event.is_set():
                    break
                conn.sendall(encode({"type": "ready", "version": VERSION}))
                peer = Peer(conn, self.on_message, self.on_close)
                self.peer = peer
                self.on_connect(peer, confirm)
                peer.start()
                conn = None  # Peer owns it now.
            except Exception:
                # Unpaired clients cannot control input, change UI state, or learn secrets.
                if self.failures >= self.max_failures and not self.stop_event.is_set():
                    self.stop()
                    if self.on_locked:
                        self.on_locked()
            finally:
                if conn is not None:
                    conn.close()

    def _pair(self, conn):
        hello = receive(conn)
        if hello["type"] != "hello":
            raise ValueError("Pairing rejected")
        exchange = Exchange(self.password, "windows")
        # Each started exchange lets the client test one password guess, so it counts
        # as a failure until the client proves it knows the password.
        self.failures += 1
        proof = exchange.finish(
            bytes.fromhex(hello["key"]), bytes.fromhex(self.fingerprint)
        )
        conn.sendall(
            encode(
                {
                    "type": "verify",
                    "version": VERSION,
                    "key": exchange.message.hex(),
                    "proof": proof.hex(),
                }
            )
        )
        confirm = receive(conn)
        if confirm["type"] != "confirm":
            raise ValueError("Pairing rejected")
        exchange.verify(bytes.fromhex(confirm["proof"]))
        self.failures -= 1
        return confirm

    def stop(self):
        self.stop_event.set()
        self.listener.close()
        if self.discovery:
            self.discovery.close()
        if self.peer:
            self.peer.close("Sharing stopped")


def discover(port=PORT, targets=("255.255.255.255",), timeout=2.0):
    """Broadcast for sharing Windows PCs and return their replies in arrival order."""
    probe = json.dumps({"clickaway": "discover", "version": VERSION}).encode()
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        deadline = time.monotonic() + timeout
        next_probe = 0.0
        while (now := time.monotonic()) < deadline:
            if now >= next_probe:
                for target in targets:
                    try:
                        sock.sendto(probe, (target, port))
                    except OSError:
                        pass
                next_probe = now + 0.4
            sock.settimeout(max(0.01, min(deadline, next_probe) - now))
            try:
                data, (address, _) = sock.recvfrom(512)
                reply = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):
                continue
            if (
                isinstance(reply, dict)
                and reply.get("clickaway") == "here"
                and type(reply.get("port")) is int
                and 1 <= reply["port"] <= 65535
                and address not in found
            ):
                found[address] = {
                    "address": address,
                    "port": reply["port"],
                    "version": reply.get("version"),
                    "name": str(reply.get("name", ""))[:64],
                }
                # Give a second computer a moment to answer, then stop waiting.
                deadline = min(deadline, time.monotonic() + 0.3)
    return list(found.values())


def connect(password, screen, on_message, on_close, address=None, port=PORT):
    """Mac side: find the sharing Windows PC, unless ``address`` is given, and pair."""
    password = normalize_password(password)
    if address:
        candidates = [(address, port)]
    else:
        found = discover(port)
        candidates = [
            (f["address"], f["port"]) for f in found if f["version"] == VERSION
        ]
        if found and not candidates:
            raise ValueError(
                "The Windows PC runs a different ClickAway version. Update both apps."
            )
        if not candidates:
            raise LookupError("No Windows PC is sharing on this network.")
    error = None
    for host, host_port in candidates:
        try:
            return _pair(host, host_port, password, screen, on_message, on_close)
        except (OSError, ValueError) as exc:  # PairingError is a ValueError.
            error = exc
    raise error


def _pair(host, port, password, screen, on_message, on_close):
    try:
        raw = socket.create_connection((host, port), timeout=3)
    except OSError as exc:
        raise ConnectionError(
            f"Could not reach {host}. Check that sharing is on in the Windows app."
        ) from exc
    conn = raw
    try:
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # No CA or hostname: the password exchange authenticates this certificate.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = context.wrap_socket(raw, server_hostname="ClickAway")
        binding = hashlib.sha256(conn.getpeercert(binary_form=True)).digest()
        exchange = Exchange(password, "mac")
        conn.sendall(
            encode({"type": "hello", "version": VERSION, "key": exchange.message.hex()})
        )
        verify = receive(conn)
        if verify["type"] != "verify":
            raise ValueError("Pairing was not accepted.")
        proof = exchange.finish(bytes.fromhex(verify["key"]), binding)
        exchange.verify(bytes.fromhex(verify["proof"]))
        conn.sendall(
            encode(
                {
                    "type": "confirm",
                    "proof": proof.hex(),
                    "width": screen.width,
                    "height": screen.height,
                }
            )
        )
        if receive(conn)["type"] != "ready":
            raise ValueError("Pairing was not accepted.")
        return Peer(conn, on_message, on_close)
    except OSError as exc:
        conn.close()
        raise ConnectionError(
            "The Windows app ended the connection. It may already be connected to another Mac."
        ) from exc
    except Exception:
        conn.close()
        raise
