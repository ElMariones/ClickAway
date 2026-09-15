"""Ephemeral pairing: certificate pinning plus a 256-bit per-session secret."""

from datetime import datetime, timedelta, timezone
import hmac
from pathlib import Path
import secrets
import socket
import ssl
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .protocol import PORT, VERSION, Peer, encode, fingerprint, receive


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


class Host:
    def __init__(self, on_connect, on_message, on_close, port=PORT):
        self.on_connect, self.on_message = on_connect, on_message
        self.on_close = on_close
        self.token = secrets.token_hex(32)
        with tempfile.TemporaryDirectory(prefix="clickaway-") as directory:
            self.context, self.fingerprint = create_identity(directory)
        self.stop_event = threading.Event()
        self.peer = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.listener.setsockopt(
                    socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
                )
            self.listener.bind(("0.0.0.0", port))
            self.listener.listen(4)
            self.listener.settimeout(0.5)
        except Exception:
            self.listener.close()
            raise
        self.port = self.listener.getsockname()[1]

    def start(self):
        threading.Thread(
            target=self._accept, daemon=True, name="clickaway-host"
        ).start()

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
                hello = receive(conn)
                if hello["type"] != "hello" or not hmac.compare_digest(
                    hello["token"], self.token
                ):
                    raise ValueError("Pairing rejected")
                if self.stop_event.is_set():
                    conn.close()
                    break
                conn.sendall(encode({"type": "ready", "version": VERSION}))
                peer = Peer(conn, self.on_message, self.on_close)
                self.peer = peer
                self.on_connect(peer, hello)
                peer.start()
                conn = None  # Peer owns it now.
            except socket.timeout:
                pass
            except Exception:
                # Unpaired clients cannot control input, change UI state, or leak secrets.
                pass
            finally:
                if conn is not None:
                    conn.close()

    def stop(self):
        self.stop_event.set()
        self.listener.close()
        if self.peer:
            self.peer.close("Sharing stopped")


def connect(details, screen, on_message, on_close):
    raw = socket.create_connection((details["host"], details["port"]), timeout=3)
    conn = raw
    try:
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Trust comes from the exact SHA-256 certificate pin in the pairing code.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = context.wrap_socket(raw, server_hostname="ClickAway")
        if not hmac.compare_digest(
            fingerprint(conn.getpeercert(binary_form=True)), details["fingerprint"]
        ):
            raise ValueError("Computer identity changed. Copy a new code from Windows.")
        conn.sendall(
            encode(
                {
                    "type": "hello",
                    "version": VERSION,
                    "token": details["token"],
                    "width": screen.width,
                    "height": screen.height,
                }
            )
        )
        if receive(conn)["type"] != "ready":
            raise ValueError("Pairing was not accepted")
        return Peer(conn, on_message, on_close)
    except Exception:
        conn.close()
        raise
