import secrets
import unittest

from clickaway import pake
from clickaway.pake import Exchange, PairingError, normalize_password

BINDING = bytes(32)


def probable_prime(n, rounds=12):
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def pi_times_power_of_two(exponent, guard=128):
    scale = 1 << (exponent + guard)

    def atan_inverse(n):
        total, term, k, sign = 0, scale // n, 1, 1
        while term:
            total += sign * (term // k)
            term //= n * n
            k, sign = k + 2, -sign
        return total

    return (16 * atan_inverse(5) - 4 * atan_inverse(239)) >> guard


class PakeTests(unittest.TestCase):
    def test_group_is_rfc3526_group_14(self):
        expected = (
            (1 << 2048)
            - (1 << 1984)
            - 1
            + (1 << 64) * (pi_times_power_of_two(1918) + 124476)
        )
        self.assertEqual(pake.P, expected)
        self.assertTrue(probable_prime(pake.P))
        self.assertTrue(probable_prime(pake.Q))
        for element in (pake.G, pake.M, pake.N):
            self.assertEqual(pow(element, pake.Q, pake.P), 1)
        self.assertNotEqual(pake.M, pake.N)

    def pair(self, mac_password, windows_password, mac_binding=BINDING):
        mac = Exchange(mac_password, "mac")
        windows = Exchange(windows_password, "windows")
        windows_proof = windows.finish(mac.message, BINDING)
        mac_proof = mac.finish(windows.message, mac_binding)
        return mac, windows, mac_proof, windows_proof

    def test_same_password_confirms_both_sides(self):
        mac, windows, mac_proof, windows_proof = self.pair(
            "banana split", "  banana split "
        )
        mac.verify(windows_proof)
        windows.verify(mac_proof)

    def test_wrong_password_fails_on_both_sides(self):
        mac, windows, mac_proof, windows_proof = self.pair(
            "banana split", "Banana split"
        )
        with self.assertRaises(PairingError):
            mac.verify(windows_proof)
        with self.assertRaises(PairingError):
            windows.verify(mac_proof)

    def test_relay_with_its_own_certificate_fails(self):
        mac, windows, mac_proof, windows_proof = self.pair(
            "banana split", "banana split", mac_binding=b"\x01" * 32
        )
        with self.assertRaises(PairingError):
            mac.verify(windows_proof)
        with self.assertRaises(PairingError):
            windows.verify(mac_proof)

    def test_every_attempt_uses_fresh_values(self):
        first = Exchange("banana split", "mac").message
        self.assertNotEqual(first, Exchange("banana split", "mac").message)

    def test_invalid_group_elements_are_rejected(self):
        exchange = Exchange("banana split", "windows")
        outside = next(v for v in range(3, 100) if pow(v, pake.Q, pake.P) != 1)
        for value in (0, 1, pake.P - 1, pake.P, outside):
            with self.subTest(value=str(value)[:12]), self.assertRaises(ValueError):
                exchange.finish(value.to_bytes(pake.KEY_BYTES, "big"), BINDING)
        with self.assertRaises(ValueError):
            exchange.finish(b"\x02", BINDING)
        with self.assertRaises(PairingError):
            exchange.verify(bytes(32))

    def test_password_rules(self):
        self.assertEqual(normalize_password(" Café 12 "), "Café 12")
        for password in ("", "12345", "     x     ", "x" * 65):
            with self.subTest(password=password), self.assertRaises(ValueError):
                normalize_password(password)


if __name__ == "__main__":
    unittest.main()
