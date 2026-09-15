"""Password pairing with SPAKE2 over the 2048-bit MODP group from RFC 3526.

Both computers type the same short password. It never crosses the network, and a
recorded exchange gives nothing to guess passwords against offline: every connection
attempt can test exactly one guess. The Windows certificate digest is part of the
transcript, so a relay presenting its own certificate fails confirmation.

The message flow follows RFC 9382, using the prime-order subgroup of integers
modulo a safe prime instead of an elliptic curve.
"""

import hashlib
import hmac
import secrets
import unicodedata

MIN_PASSWORD = 6
MAX_PASSWORD = 64
KEY_BYTES = 256

# RFC 3526 section 3: 2^2048 - 2^1984 - 1 + 2^64 * (floor(2^1918 * pi) + 124476).
P = int(
    "".join(
        """
        FFFFFFFF FFFFFFFF C90FDAA2 2168C234 C4C6628B 80DC1CD1 29024E08 8A67CC74
        020BBEA6 3B139B22 514A0879 8E3404DD EF9519B3 CD3A431B 302B0A6D F25F1437
        4FE1356D 6D51C245 E485B576 625E7EC6 F44C42E9 A637ED6B 0BFF5CB6 F406B7ED
        EE386BFB 5A899FA5 AE9F2411 7C4B1FE6 49286651 ECE45B3D C2007CB8 A163BF05
        98DA4836 1C55D39A 69163FA8 FD24CF5F 83655D23 DCA3AD96 1C62F356 208552BB
        9ED52907 7096966D 670C354E 4ABC9804 F1746C08 CA18217C 32905E46 2E36CE3B
        E39E772C 180E8603 9B2783A2 EC07A28F B5C55DF0 6F4C52C9 DE2BCBF6 95581718
        3995497C EA956AE5 15D22618 98FA0510 15728E5A 8AACAA68 FFFFFFFF FFFFFFFF
        """.split()
    ),
    16,
)
Q = (P - 1) // 2  # Prime order of the subgroup of squares, where all elements live.
G = 2  # A square, because P = 7 mod 8.


class PairingError(ValueError):
    """The other computer typed a different password."""


def normalize_password(password):
    text = unicodedata.normalize("NFC", password.strip())
    if len(text) < MIN_PASSWORD:
        raise ValueError(f"Use a password with at least {MIN_PASSWORD} characters.")
    if len(text) > MAX_PASSWORD:
        raise ValueError(f"Use a password with at most {MAX_PASSWORD} characters.")
    return text


def _hash_to_group(label):
    # Squaring a hash output lands in the subgroup with a discrete log nobody knows.
    digest = hashlib.shake_256(b"ClickAway SPAKE2 element " + label).digest(
        KEY_BYTES + 32
    )
    return pow(int.from_bytes(digest, "big") % P, 2, P)


M = _hash_to_group(b"M")
N = _hash_to_group(b"N")


def _scalar(password):
    data = normalize_password(password).encode("utf-8")
    digest = hashlib.sha512(b"ClickAway SPAKE2 password\x00" + data).digest()
    return int.from_bytes(digest, "big") % Q


def _element(data):
    value = int.from_bytes(data, "big") if len(data) == KEY_BYTES else 0
    if not 1 < value < P - 1 or pow(value, Q, P) != 1:
        raise ValueError("Invalid key exchange")
    return value


def _frame(*parts):
    return b"".join(len(part).to_bytes(8, "big") + part for part in parts)


class Exchange:
    """One side of a single pairing attempt: role "mac" (A) or "windows" (B)."""

    def __init__(self, password, role):
        if role not in ("mac", "windows"):
            raise ValueError("Unknown pairing role")
        self.role = role
        self._w = _scalar(password)
        self._secret = secrets.randbelow(Q - 1) + 1
        mask = M if role == "mac" else N
        value = pow(G, self._secret, P) * pow(mask, self._w, P) % P
        self.message = value.to_bytes(KEY_BYTES, "big")
        self._proofs = None

    def finish(self, peer_message, binding):
        """Returns this side's proof. ``binding`` is the Windows certificate digest."""
        other = _element(peer_message)
        unmask = N if self.role == "mac" else M
        shared = pow(other * pow(unmask, -self._w, P) % P, self._secret, P)
        if shared == 1:
            raise PairingError("Wrong password")
        mac, windows = (
            (self.message, peer_message)
            if self.role == "mac"
            else (peer_message, self.message)
        )
        transcript = _frame(
            b"ClickAway SPAKE2 v2",
            binding,
            mac,
            windows,
            shared.to_bytes(KEY_BYTES, "big"),
            self._w.to_bytes(KEY_BYTES, "big"),
        )
        key = hashlib.sha256(transcript).digest()
        self._proofs = {
            role: hmac.new(key, b"confirm " + role.encode(), hashlib.sha256).digest()
            for role in ("mac", "windows")
        }
        return self._proofs[self.role]

    def verify(self, proof):
        other = "windows" if self.role == "mac" else "mac"
        if self._proofs is None or not hmac.compare_digest(proof, self._proofs[other]):
            raise PairingError("Wrong password")
