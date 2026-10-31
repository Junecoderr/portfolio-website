"""Ed25519 signature verification (RFC 8032, section 5.1.7) in pure Python.

`tb add` uses it to check the TB Package Index's signature (tbtools/pkg.py).
Verification only: it handles public data, so it doesn't need to be
constant-time. Follows the RFC's reference code (section 6), with the strict
checks: S must be below the group order and both points must decode.
"""

from __future__ import annotations

import hashlib

__all__ = ["verify"]

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493  # the group order
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(a, b):
    """Add two points in extended coordinates (X, Y, Z, T)."""
    x = (a[1] - a[0]) * (b[1] - b[0]) % P
    y = (a[1] + a[0]) * (b[1] + b[0]) % P
    c = 2 * a[3] * b[3] * D % P
    d = 2 * a[2] * b[2] % P
    e, f, g, h = y - x, d - c, d + c, y + x
    return e * f % P, g * h % P, f * g % P, e * h % P


def _mul(s: int, point):
    result = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _recover_x(y: int, sign: int):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    return P - x if (x & 1) != sign else x


def _decode(data: bytes):
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when `signature` (64 bytes) is `public_key`'s (32 bytes) Ed25519
    signature of `message`. Never raises for bad input; returns False."""
    if not isinstance(public_key, (bytes, bytearray)) or not isinstance(signature, (bytes, bytearray)):
        return False
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decode(bytes(public_key))
    r = _decode(bytes(signature[:32]))
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    h = int.from_bytes(hashlib.sha512(bytes(signature[:32]) + bytes(public_key) + bytes(message)).digest(), "little") % L
    return _equal(_mul(s, G), _add(r, _mul(h, a)))
