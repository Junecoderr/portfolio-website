"""crypto: encryption, key derivation, one-time codes, JWTs, password hashing and certificates.

Everything here is the Python standard library plus pure Python: AES is
written out in Python (fine for configs, tokens and small files - about
0.3 to 1 MB/s - not for gigabytes), RSA signature checks use pow() with a
DER/PEM public-key parser, and X.509 certificates are read with the same
DER parser. Nothing needs OpenSSL beyond what Python ships.

Other tblib modules can reuse `x509_parse_bytes`, `parse_pem`, `der_parse`
and `rsa_public_key` (tls.certificate uses them).
"""

from __future__ import annotations

import base64 as _b64
import binascii
import datetime as _dt
import hashlib
import hmac as _hmac
import json as _json
import os
import re
import struct
import time as _time

from tblib._api import Module, check_type, fail

import tb as _tb

HASHES = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha384": hashlib.sha384,
          "sha512": hashlib.sha512}
JWT_HS = {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}
JWT_RS = {"RS256": "sha256", "RS384": "sha384", "RS512": "sha512"}
SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1
MAX_AES_BYTES = 50_000_000


class CryptoError(Exception):
    """A problem with keys, tokens or encoded data; turned into a friendly TB error."""


# =============================================================================
# Bytes in and out (TB has text; keys and ciphertext travel as hex / base64)
# =============================================================================

def to_bytes(value, encoding="utf-8") -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return str(value).encode(encoding, "surrogatepass")


def from_hex(text: str, what: str) -> bytes:
    cleaned = re.sub(r"[\s:]", "", text)
    if len(cleaned) % 2 or not re.fullmatch(r"[0-9A-Fa-f]*", cleaned):
        raise CryptoError(f"{what} should be hex text (pairs of 0-9 and a-f)")
    return bytes.fromhex(cleaned)


def from_base64(text: str, what: str) -> bytes:
    cleaned = re.sub(r"\s+", "", text).replace("-", "+").replace("_", "/")
    if not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", cleaned):
        raise CryptoError(f"{what} should be base64 text")
    cleaned = cleaned.rstrip("=")
    if len(cleaned) % 4 == 1:
        raise CryptoError(f"{what} is base64 with the wrong length; part of it may be missing")
    try:
        return _b64.b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except (binascii.Error, ValueError):
        raise CryptoError(f"{what} isn't valid base64") from None


def b64url(data: bytes) -> str:
    return _b64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64url(text: str, what: str) -> bytes:
    return from_base64(text, what)


def key_bytes(value, what: str, sizes=(16, 24, 32)) -> bytes:
    """An AES key: hex text of 32/48/64 characters (or bytes of 16/24/32)."""
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    elif isinstance(value, str):
        stripped = re.sub(r"[\s:]", "", value)
        if len(stripped) in (s * 2 for s in sizes) and re.fullmatch(r"[0-9A-Fa-f]+", stripped):
            data = bytes.fromhex(stripped)
        elif len(value.encode("utf-8")) in sizes:
            data = value.encode("utf-8")
        else:
            raise CryptoError(f"{what} should be a key of 16, 24 or 32 bytes as hex text (32, 48 or 64 hex "
                              f"characters; crypto.random_bytes(32) makes one), not {len(value)} characters of text")
    else:
        raise CryptoError(f"{what} should be hex text")
    if len(data) not in sizes:
        raise CryptoError(f"{what} should be 16, 24 or 32 bytes long, not {len(data)}")
    return data


def hash_name(value, fn) -> str:
    name = str(value).lower().replace("-", "").replace("_", "")
    if name not in HASHES:
        raise CryptoError(f"{fn}() doesn't know the hash \"{value}\". Use one of: {', '.join(HASHES)}")
    return name


# =============================================================================
# AES (FIPS 197) in pure Python
# =============================================================================

def _build_tables():
    sbox = [0] * 256
    p = q = 1
    while True:  # generate the S-box from the multiplicative inverse
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (q << 1) ^ (q << 2) ^ (q << 3) ^ (q << 4)
        x = (x ^ (x >> 8) ^ 0x63) & 0xFF
        sbox[p] = x
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i

    def mul(a, b):
        r = 0
        while b:
            if b & 1:
                r ^= a
            a = ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1
            b >>= 1
        return r
    tables = {n: [mul(i, n) for i in range(256)] for n in (2, 3, 9, 11, 13, 14)}
    return sbox, inv, tables


SBOX, INV_SBOX, MUL = _build_tables()
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def expand_key(key: bytes) -> list:
    nk = len(key) // 4
    rounds = nk + 6
    words = [list(key[i:i + 4]) for i in range(0, len(key), 4)]
    for i in range(nk, 4 * (rounds + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(rounds + 1)]


def encrypt_block(round_keys: list, block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    m2, m3 = MUL[2], MUL[3]
    for rk in round_keys[1:-1]:
        s = [SBOX[b] for b in s]
        s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3], s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
        out = []
        for c in range(0, 16, 4):
            a0, a1, a2, a3 = s[c], s[c + 1], s[c + 2], s[c + 3]
            out += [m2[a0] ^ m3[a1] ^ a2 ^ a3, a0 ^ m2[a1] ^ m3[a2] ^ a3,
                    a0 ^ a1 ^ m2[a2] ^ m3[a3], m3[a0] ^ a1 ^ a2 ^ m2[a3]]
        s = [b ^ k for b, k in zip(out, rk)]
    s = [SBOX[b] for b in s]
    s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3], s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
    return bytes(b ^ k for b, k in zip(s, round_keys[-1]))


def decrypt_block(round_keys: list, block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[-1])]
    m9, m11, m13, m14 = MUL[9], MUL[11], MUL[13], MUL[14]
    for rk in reversed(round_keys[1:-1]):
        s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11], s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
        s = [INV_SBOX[b] for b in s]
        s = [b ^ k for b, k in zip(s, rk)]
        out = []
        for c in range(0, 16, 4):
            a0, a1, a2, a3 = s[c], s[c + 1], s[c + 2], s[c + 3]
            out += [m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3], m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3],
                    m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3], m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]]
        s = out
    s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11], s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
    s = [INV_SBOX[b] for b in s]
    return bytes(b ^ k for b, k in zip(s, round_keys[0]))


def pkcs7_pad(data: bytes) -> bytes:
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise CryptoError("the data isn't a whole number of blocks")
    n = data[-1]
    if not 1 <= n <= 16 or data[-n:] != bytes([n]) * n:
        raise CryptoError("wrong key, or the data was changed (bad padding)")
    return data[:-n]


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    rk = expand_key(key)
    out, prev = [], iv
    for i in range(0, len(plaintext), 16):
        block = bytes(a ^ b for a, b in zip(plaintext[i:i + 16], prev))
        prev = encrypt_block(rk, block)
        out.append(prev)
    return b"".join(out)


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    rk = expand_key(key)
    out, prev = [], iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        out.append(bytes(a ^ b for a, b in zip(decrypt_block(rk, block), prev)))
        prev = block
    return b"".join(out)


def aes_ctr(rk: list, counter_block: bytes, data: bytes) -> bytes:
    out = []
    counter = int.from_bytes(counter_block[12:], "big")
    prefix = counter_block[:12]
    for i in range(0, len(data), 16):
        counter = (counter + 1) & 0xFFFFFFFF
        stream = encrypt_block(rk, prefix + counter.to_bytes(4, "big"))
        chunk = data[i:i + 16]
        out.append(bytes(a ^ b for a, b in zip(chunk, stream)))
    return b"".join(out)


GCM_R = 0xE1 << 120
# what falls off the bottom when multiplying by x^4, folded back in at the top
GCM_REDUCE = [v << 112 for v in (0x0000, 0x1C20, 0x3840, 0x2460, 0x7080, 0x6CA0, 0x48C0, 0x54E0, 0xE100, 0xFD20,
                                 0xD940, 0xC560, 0x9180, 0x8DA0, 0xA9C0, 0xB5E0)]


def gf_mul(x: int, y: int) -> int:
    """x * y in GF(2^128) with GCM's bit order (bit 0 = the integer's top bit)."""
    z, v = 0, y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = (v >> 1) ^ (GCM_R if v & 1 else 0)
    return z


def ghash_table(h: int) -> list:
    """table[n] = (n as the top 4 bits) * H, so GHASH can go a nibble at a time."""
    return [gf_mul(n << 124, h) for n in range(16)]


def gf_mul_table(x: int, table: list) -> int:
    """x * H using the table (Horner's rule over x's nibbles, highest degree first)."""
    z = 0
    for i in range(32):
        if i:
            z = (z >> 4) ^ GCM_REDUCE[z & 0xF]
        z ^= table[(x >> (4 * i)) & 0xF]
    return z


def ghash(h: int, data: bytes) -> int:
    table = ghash_table(h)
    y = 0
    for i in range(0, len(data), 16):
        block = data[i:i + 16].ljust(16, b"\0")
        y = gf_mul_table(y ^ int.from_bytes(block, "big"), table)
    return y


def gcm(key: bytes, nonce: bytes, data: bytes, aad: bytes, encrypt: bool, tag=None):
    rk = expand_key(key)
    h = int.from_bytes(encrypt_block(rk, b"\0" * 16), "big")
    if len(nonce) == 12:
        j0 = nonce + b"\0\0\0\1"
    else:
        padded = nonce + b"\0" * (-len(nonce) % 16) + b"\0" * 8 + (len(nonce) * 8).to_bytes(8, "big")
        j0 = ghash(h, padded).to_bytes(16, "big")
    if encrypt:
        ciphertext = aes_ctr(rk, j0, data)
    else:
        ciphertext = data
    padded = aad + b"\0" * (-len(aad) % 16) + ciphertext + b"\0" * (-len(ciphertext) % 16)
    lengths = (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big")
    s = ghash(h, padded + lengths).to_bytes(16, "big")
    computed = bytes(a ^ b for a, b in zip(encrypt_block(rk, j0), s))
    if encrypt:
        return ciphertext + computed
    if tag is None or not _hmac.compare_digest(computed, tag):
        raise CryptoError("wrong key, wrong extra data (aad), or the data was changed (the tag doesn't match)")
    return aes_ctr(rk, j0, ciphertext)


# =============================================================================
# DER / PEM / X.509
# =============================================================================

OIDS = {
    "2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "ST", "2.5.4.10": "O", "2.5.4.11": "OU",
    "2.5.4.5": "serialNumber", "2.5.4.9": "street", "2.5.4.17": "postalCode", "2.5.4.4": "SN", "2.5.4.42": "GN",
    "2.5.4.12": "title", "2.5.4.46": "dnQualifier", "2.5.4.65": "pseudonym", "0.9.2342.19200300.100.1.25": "DC",
    "0.9.2342.19200300.100.1.1": "UID", "1.2.840.113549.1.9.1": "emailAddress",
    "1.3.6.1.4.1.311.60.2.1.3": "jurisdictionC", "1.3.6.1.4.1.311.60.2.1.2": "jurisdictionST",
    "2.5.4.15": "businessCategory",
    "1.2.840.113549.1.1.1": "rsaEncryption", "1.2.840.113549.1.1.5": "sha1WithRSAEncryption",
    "1.2.840.113549.1.1.11": "sha256WithRSAEncryption", "1.2.840.113549.1.1.12": "sha384WithRSAEncryption",
    "1.2.840.113549.1.1.13": "sha512WithRSAEncryption", "1.2.840.113549.1.1.4": "md5WithRSAEncryption",
    "1.2.840.113549.1.1.10": "RSASSA-PSS", "1.2.840.10045.2.1": "ecPublicKey",
    "1.2.840.10045.4.3.2": "ecdsa-with-SHA256", "1.2.840.10045.4.3.3": "ecdsa-with-SHA384",
    "1.2.840.10045.4.3.4": "ecdsa-with-SHA512", "1.2.840.10045.4.1": "ecdsa-with-SHA1",
    "1.3.101.112": "Ed25519", "1.3.101.113": "Ed448",
    "1.2.840.10045.3.1.7": "P-256", "1.3.132.0.34": "P-384", "1.3.132.0.35": "P-521", "1.3.132.0.10": "secp256k1",
    "2.5.29.17": "subjectAltName", "2.5.29.19": "basicConstraints", "2.5.29.15": "keyUsage",
    "2.5.29.37": "extKeyUsage", "2.5.29.14": "subjectKeyIdentifier", "2.5.29.35": "authorityKeyIdentifier",
    "2.5.29.31": "cRLDistributionPoints", "2.5.29.32": "certificatePolicies", "1.3.6.1.5.5.7.1.1": "authorityInfoAccess",
    "1.3.6.1.4.1.11129.2.4.2": "signedCertificateTimestamps", "2.5.29.18": "issuerAltName",
    "1.3.6.1.5.5.7.3.1": "serverAuth", "1.3.6.1.5.5.7.3.2": "clientAuth", "1.3.6.1.5.5.7.3.3": "codeSigning",
    "1.3.6.1.5.5.7.3.4": "emailProtection", "1.3.6.1.5.5.7.3.8": "timeStamping", "1.3.6.1.5.5.7.3.9": "OCSPSigning",
}
KEY_USAGE_BITS = ["digitalSignature", "nonRepudiation", "keyEncipherment", "dataEncipherment", "keyAgreement",
                  "keyCertSign", "cRLSign", "encipherOnly", "decipherOnly"]
DIGEST_INFO = {
    "sha1": bytes.fromhex("3021300906052b0e03021a05000414"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}


def der_read(data: bytes, pos: int = 0):
    """One TLV at pos -> (tag, value bytes, end position)."""
    if pos >= len(data):
        raise CryptoError("the DER data ends too early")
    tag = data[pos]
    pos += 1
    if tag & 0x1F == 0x1F:  # multi-byte tags are not used in certificates
        raise CryptoError("unexpected long-form DER tag")
    if pos >= len(data):
        raise CryptoError("the DER data ends too early")
    length = data[pos]
    pos += 1
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or pos + n > len(data):
            raise CryptoError("a DER length is malformed")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    if pos + length > len(data):
        raise CryptoError("a DER length runs past the end of the data")
    return tag, data[pos:pos + length], pos + length


def der_parse(data: bytes):
    """Parse DER into nested Python values: lists for SEQUENCE/SET, ints for
    INTEGER, text for strings/OIDs, dicts {"tag", "value"} for other tags."""
    tag, value, end = der_read(data, 0)
    if end != len(data):
        raise CryptoError("extra bytes after the DER value")
    return der_value(tag, value)


def der_children(value: bytes) -> list:
    items, pos = [], 0
    while pos < len(value):
        tag, inner, pos = der_read(value, pos)
        items.append((tag, inner))
    return items


def der_value(tag, value):
    if tag in (0x30, 0x31):
        return [der_value(t, v) for t, v in der_children(value)]
    if tag == 0x02:
        return int.from_bytes(value, "big", signed=True)
    if tag == 0x06:
        return decode_oid(value)
    if tag in (0x0C, 0x13, 0x16, 0x1A, 0x14):
        return value.decode("utf-8", "replace")
    if tag == 0x1E:
        return value.decode("utf-16-be", "replace")
    if tag == 0x05:
        return None
    if tag == 0x01:
        return value != b"\0"
    if tag in (0x17, 0x18):
        return parse_time(tag, value)
    return {"tag": tag, "value": value}


def decode_oid(value: bytes) -> str:
    if not value:
        raise CryptoError("an empty OID")
    parts = [value[0] // 40, value[0] % 40]
    if value[0] >= 80:
        parts = [2, value[0] - 80]
    n = 0
    for b in value[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(n)
            n = 0
    return ".".join(str(p) for p in parts)


def parse_time(tag, value: bytes):
    text = value.decode("ascii", "replace")
    try:
        if tag == 0x17:  # UTCTime YYMMDDHHMMSSZ
            year = int(text[:2])
            year += 1900 if year >= 50 else 2000
            dt = _dt.datetime.strptime(text[2:], "%m%d%H%M%SZ").replace(year=year, tzinfo=_dt.timezone.utc)
        else:
            body = text.rstrip("Z").split(".")[0]
            dt = _dt.datetime.strptime(body[:14], "%Y%m%d%H%M%S").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        raise CryptoError(f"the time \"{text}\" in the certificate is malformed") from None
    return dt


def parse_pem(text: str, kinds=None):
    """Every PEM block in text -> list of (label, der bytes)."""
    blocks = []
    for m in re.finditer(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", text, re.S):
        label = m.group(1)
        if kinds and label not in kinds:
            continue
        try:
            blocks.append((label, _b64.b64decode(re.sub(r"\s+", "", m.group(2)))))
        except (binascii.Error, ValueError):
            raise CryptoError(f"the {label} block isn't valid base64") from None
    return blocks


def der_input(value, what: str, labels=None) -> bytes:
    """PEM text, base64 DER text, hex DER text or bytes -> DER bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    text = str(value).strip()
    if "-----BEGIN" in text:
        blocks = parse_pem(text, labels)
        if not blocks:
            wanted = " or ".join(labels) if labels else "PEM"
            raise CryptoError(f"{what} has no {wanted} block")
        return blocks[0][1]
    if re.fullmatch(r"[0-9A-Fa-f\s:]+", text) and len(re.sub(r"[\s:]", "", text)) % 2 == 0:
        return from_hex(text, what)
    return from_base64(text, what)


def name_of(seq) -> dict:
    """An X.501 Name (list of sets of [oid, value]) -> {"CN": ..., "O": ..., "text": "CN=..., O=..."}."""
    result = {}
    parts = []
    for rdn in seq if isinstance(seq, list) else []:
        for attr in rdn if isinstance(rdn, list) else []:
            if isinstance(attr, list) and len(attr) == 2:
                key = OIDS.get(attr[0], attr[0])
                val = attr[1] if isinstance(attr[1], str) else _tb.to_text(attr[1])
                if key in result:
                    result[key] = f"{result[key]}, {val}" if isinstance(result[key], str) else val
                else:
                    result[key] = val
                parts.append(f"{key}={val}")
    result["text"] = ", ".join(parts)
    return result


def rsa_public_key(der: bytes) -> dict:
    """SubjectPublicKeyInfo or RSAPublicKey DER -> {"kind": "rsa", "n", "e", "bits"} (or EC / Ed info)."""
    value = der_parse(der)
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], int):
        n, e = value
        return {"kind": "rsa", "n": n, "e": e, "bits": n.bit_length()}
    if not (isinstance(value, list) and len(value) == 2 and isinstance(value[0], list)):
        raise CryptoError("this isn't a public key I recognise (expected a PEM PUBLIC KEY or RSA PUBLIC KEY)")
    algorithm, key_bits = value
    oid = algorithm[0] if algorithm else None
    name = OIDS.get(oid, oid)
    bits = key_bits["value"] if isinstance(key_bits, dict) else b""
    if bits[:1] == b"\0":
        bits = bits[1:]
    if name == "rsaEncryption":
        inner = der_parse(bits)
        if not (isinstance(inner, list) and len(inner) == 2):
            raise CryptoError("the RSA key inside the certificate is malformed")
        n, e = inner
        return {"kind": "rsa", "n": n, "e": e, "bits": n.bit_length()}
    if name == "ecPublicKey":
        curve = OIDS.get(algorithm[1], algorithm[1]) if len(algorithm) > 1 else None
        size = {"P-256": 256, "P-384": 384, "P-521": 521, "secp256k1": 256}.get(curve)
        return {"kind": "ec", "curve": curve, "bits": size, "point": bits.hex()}
    if name in ("Ed25519", "Ed448"):
        return {"kind": name.lower(), "bits": 256 if name == "Ed25519" else 456, "point": bits.hex()}
    return {"kind": name or "unknown", "bits": None}


def general_names(value: bytes) -> dict:
    """A GeneralNames SEQUENCE -> {"dns": [...], "ip": [...], "email": [...], "uri": [...], "other": n}."""
    result = {"dns": [], "ip": [], "email": [], "uri": [], "other": 0}
    for tag, inner in der_children(value):
        kind = tag & 0x1F
        if kind == 2:
            result["dns"].append(inner.decode("ascii", "replace"))
        elif kind == 7:
            import ipaddress
            try:
                result["ip"].append(str(ipaddress.ip_address(inner)))
            except ValueError:
                result["ip"].append(inner.hex())
        elif kind == 1:
            result["email"].append(inner.decode("ascii", "replace"))
        elif kind == 6:
            result["uri"].append(inner.decode("ascii", "replace"))
        else:
            result["other"] += 1
    return result


def x509_parse_bytes(der: bytes, now=None) -> dict:
    """A certificate's DER bytes -> a map of its interesting fields."""
    cert = der_parse(der)
    if not (isinstance(cert, list) and len(cert) == 3 and isinstance(cert[0], list)):
        raise CryptoError("this isn't an X.509 certificate")
    tbs = cert[0]
    children = der_children(der_read(der, 0)[1])  # [tbs, sigalg, sig] raw
    tbs_children = der_children(children[0][1])
    pos = 0
    version = 1
    if tbs_children and tbs_children[0][0] == 0xA0:
        version = der_parse(tbs_children[0][1]) + 1
        pos = 1
    serial = tbs[pos]
    sig_alg = tbs[pos + 1]
    issuer = name_of(tbs[pos + 2])
    validity = tbs[pos + 3]
    subject = name_of(tbs[pos + 4])
    spki_raw = tbs_children[pos + 5][1]
    spki_der = bytes([0x30]) + der_length(len(spki_raw)) + spki_raw
    try:
        public_key = rsa_public_key(spki_der)
    except CryptoError:
        public_key = {"kind": "unknown", "bits": None}
    not_before, not_after = validity[0], validity[1]
    extensions, sans, ca, path_len, key_usage, ext_key_usage, ski, aki = {}, None, None, None, [], [], None, None
    for tag, inner in tbs_children[pos + 6:]:
        if tag != 0xA3:
            continue
        for ext in [der_value(t, v) for t, v in der_children(der_read(inner, 0)[1])]:
            if not isinstance(ext, list) or len(ext) < 2:
                continue
            oid = ext[0]
            critical = ext[1] is True
            raw = ext[-1]["value"] if isinstance(ext[-1], dict) else b""
            name = OIDS.get(oid, oid)
            extensions[name] = {"critical": critical}
            try:
                if name == "subjectAltName":
                    sans = general_names(der_read(raw, 0)[1])
                elif name == "basicConstraints":
                    parts = der_parse(raw)
                    ca = bool(parts and parts[0] is True)
                    path_len = parts[1] if len(parts) > 1 and isinstance(parts[1], int) else None
                elif name == "keyUsage":
                    bits = der_read(raw, 0)[1]
                    if len(bits) >= 2:
                        value = int.from_bytes(bits[1:].ljust(2, b"\0")[:2], "big")
                        key_usage = [KEY_USAGE_BITS[i] for i in range(9) if value & (0x8000 >> i)]
                elif name == "extKeyUsage":
                    ext_key_usage = [OIDS.get(o, o) for o in der_parse(raw)]
                elif name == "subjectKeyIdentifier":
                    ski = der_read(raw, 0)[1].hex()
                elif name == "authorityKeyIdentifier":
                    for t, v in der_children(der_read(raw, 0)[1]):
                        if t == 0x80:
                            aki = v.hex()
            except CryptoError:
                pass
    now = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    if isinstance(now, (int, float)):
        now = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    days_left = (not_after - now).total_seconds() / 86400
    sig_name = OIDS.get(sig_alg[0], sig_alg[0]) if isinstance(sig_alg, list) and sig_alg else None
    result = {
        "version": version,
        "serial": format(serial, "x") if serial >= 0 else str(serial),
        "subject": subject, "issuer": issuer,
        "common_name": subject.get("CN"),
        "not_before": not_before.strftime("%Y-%m-%d %H:%M:%S"), "not_after": not_after.strftime("%Y-%m-%d %H:%M:%S"),
        "not_before_ts": int(not_before.timestamp()), "not_after_ts": int(not_after.timestamp()),
        "valid_now": not_before <= now <= not_after,
        "expired": now > not_after,
        "days_left": int(days_left) if days_left >= 0 else -int(-days_left),
        "sans": (sans or {"dns": [], "ip": [], "email": [], "uri": [], "other": 0}),
        "dns_names": list(sans["dns"]) if sans else [],
        "is_ca": bool(ca), "path_length": path_len,
        "key_usage": key_usage, "ext_key_usage": ext_key_usage,
        "subject_key_id": ski, "authority_key_id": aki,
        "self_signed": subject.get("text") == issuer.get("text"),
        "signature_algorithm": sig_name,
        "public_key": {k: (format(v, "x") if k == "n" else v) for k, v in public_key.items()},
        "extensions": sorted(extensions),
        "fingerprint_sha256": colon_hex(hashlib.sha256(der).hexdigest()),
        "fingerprint_sha1": colon_hex(hashlib.sha1(der).hexdigest()),
        "fingerprint_md5": colon_hex(hashlib.md5(der).hexdigest()),
        "pem": "-----BEGIN CERTIFICATE-----\n" + "\n".join(
            _b64.b64encode(der).decode("ascii")[i:i + 64] for i in range(0, len(_b64.b64encode(der)), 64))
               + "\n-----END CERTIFICATE-----\n",
    }
    return result


def der_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def colon_hex(hexdigest: str) -> str:
    return ":".join(hexdigest[i:i + 2].upper() for i in range(0, len(hexdigest), 2))


def public_key_from(value, what: str) -> dict:
    """A PEM public key, RSA public key or certificate -> key map (with int n, e)."""
    if isinstance(value, dict):
        return value
    der = der_input(value, what, ("PUBLIC KEY", "RSA PUBLIC KEY", "CERTIFICATE"))
    text = value if isinstance(value, str) else ""
    if "BEGIN CERTIFICATE" in text or (not text.strip().startswith("-----") and looks_like_certificate(der)):
        cert = der_parse(der)
        tbs_children = der_children(der_children(der_read(der, 0)[1])[0][1])
        pos = 1 if tbs_children and tbs_children[0][0] == 0xA0 else 0
        spki_raw = tbs_children[pos + 5][1]
        del cert
        return rsa_public_key(bytes([0x30]) + der_length(len(spki_raw)) + spki_raw)
    return rsa_public_key(der)


def looks_like_certificate(der: bytes) -> bool:
    try:
        value = der_parse(der)
    except CryptoError:
        return False
    return isinstance(value, list) and len(value) == 3 and isinstance(value[0], list) and len(value[0]) >= 6


def rsa_verify_pkcs1(key: dict, data: bytes, signature: bytes, algo: str) -> bool:
    if key.get("kind") != "rsa":
        raise CryptoError(f"RSA checks need an RSA public key, but this is {key.get('kind') or 'unknown'}")
    n, e = key["n"], key["e"]
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    digest = HASHES[algo](data).digest()
    t = DIGEST_INFO[algo] + digest
    if len(t) + 11 > k:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return _hmac.compare_digest(em, expected)


# =============================================================================
# One-time codes
# =============================================================================

def base32_secret(text: str, what: str) -> bytes:
    cleaned = re.sub(r"[\s-]", "", text).upper().rstrip("=")
    if not re.fullmatch(r"[A-Z2-7]*", cleaned) or not cleaned:
        raise CryptoError(f"{what} should be a base32 secret (A-Z and 2-7), like the one in the QR code")
    try:
        return _b64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (binascii.Error, ValueError):
        raise CryptoError(f"{what} isn't valid base32") from None


def hotp(secret: bytes, counter: int, digits: int, algo: str) -> str:
    mac = _hmac.new(secret, struct.pack(">Q", counter), HASHES[algo]).digest()
    offset = mac[-1] & 0x0F
    code = (int.from_bytes(mac[offset:offset + 4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# =============================================================================
# The library
# =============================================================================

def load(api):
    m = Module(
        "crypto",
        "Encryption and the building blocks around it: AES-GCM and AES-CBC (pure Python, so fine for secrets and "
        "small files, slow for gigabytes), random keys, PBKDF2 and scrypt key derivation, HMAC, TOTP one-time codes, "
        "JWTs (sign HS256/384/512, verify HS* and RS*), scrypt password hashing, RSA signature checks and X.509 "
        "certificate parsing. Keys and random bytes are hex text; encrypted data is base64 text.",
    )

    def wrap(line, fn_name, work):
        try:
            return work()
        except CryptoError as err:
            fail(line, f"{fn_name}(): {str(err).rstrip('.')}.")

    def text_or_bytes(line, value, fn, arg):
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        return to_bytes(check_type(line, value, "text", fn, arg))

    def result_text(data: bytes, encoding: str):
        if encoding == "hex":
            return data.hex()
        if encoding == "base64":
            return _b64.b64encode(data).decode("ascii")
        if encoding == "bytes":
            return data
        return data.decode("utf-8", "replace")

    # -- random and keys ---------------------------------------------------------------

    @m.fn("random_bytes", 1, doc=(
        "random_bytes(n) -> text\n"
        "n cryptographically random bytes as hex text (2n characters): random_bytes(32) makes an AES-256 key, "
        "random_bytes(16) a salt or token."))
    def random_bytes(line, n):
        n = check_type(line, n, "whole", "crypto.random_bytes", "n")
        if not 1 <= n <= 1_000_000:
            fail(line, "crypto.random_bytes() needs n from 1 to 1,000,000.")
        return os.urandom(n).hex()

    @m.fn("random_token", 0, 1, doc=(
        "random_token(bytes?) -> text\n"
        "A random URL-safe token (base64 without padding) of bytes random bytes (default 32), for session ids, "
        "reset links and API keys."))
    def random_token(line, n=32):
        n = check_type(line, n, "whole", "crypto.random_token", "bytes")
        if not 1 <= n <= 1024:
            fail(line, "crypto.random_token() needs bytes from 1 to 1024.")
        return b64url(os.urandom(n))

    # -- AES ---------------------------------------------------------------------------

    def aes_args(line, key, data, fn, max_bytes=MAX_AES_BYTES):
        key_b = wrap(line, fn, lambda: key_bytes(key, "key"))
        payload = text_or_bytes(line, data, fn, "text")
        if len(payload) > max_bytes:
            fail(line, f"{fn}() works on data up to {max_bytes:,} bytes (pure-Python AES is slow on big files).")
        return key_b, payload

    def aad_arg(line, aad, fn):
        return b"" if aad is None else text_or_bytes(line, aad, fn, "aad")

    @m.fn("aes_gcm_encrypt", 2, 3, doc=(
        "aes_gcm_encrypt(key, text, aad?) -> text\n"
        "Encrypt text with AES-GCM. It is authenticated: any tampering is detected when decrypting.\n"
        "\n"
        "Arguments:\n"
        "- key: hex text of 32, 48 or 64 characters. Make one with crypto.random_bytes(32).\n"
        "- text: the text to encrypt.\n"
        "- aad: optional extra text, like a record id. It is not encrypted,\n"
        "  but it must match when decrypting.\n"
        "\n"
        "Returns:\n"
        "- base64 text holding the nonce, the ciphertext and the tag.\n"
        "- A fresh random 12-byte nonce is used every time.\n"
        "\n"
        "Pure Python, so it runs at about 0.5 MB/s.\n"
        "\n"
        "Example:\n"
        "    let key = crypto.random_bytes(32)\n"
        "    let secret = crypto.aes_gcm_encrypt(key, \"meet at noon\")\n"
        "    say(crypto.aes_gcm_decrypt(key, secret))"))
    def aes_gcm_encrypt_fn(line, key, text, aad=None):
        key_b, data = aes_args(line, key, text, "crypto.aes_gcm_encrypt")
        nonce = os.urandom(12)
        out = gcm(key_b, nonce, data, aad_arg(line, aad, "crypto.aes_gcm_encrypt"), True)
        return _b64.b64encode(nonce + out).decode("ascii")

    @m.fn("aes_gcm_decrypt", 2, 3, doc=(
        "aes_gcm_decrypt(key, data, aad?) -> text\n"
        "Decrypt what aes_gcm_encrypt() made (base64 text). A wrong key, wrong aad or changed data gives a "
        "catchable error instead of garbage."))
    def aes_gcm_decrypt_fn(line, key, data, aad=None):
        fn = "crypto.aes_gcm_decrypt"
        key_b = wrap(line, fn, lambda: key_bytes(key, "key"))
        check_type(line, data, "text", fn, "data")
        raw = wrap(line, fn, lambda: from_base64(data, "data"))
        if len(raw) < 12 + 16:
            fail(line, f"{fn}(): the data is too short to be AES-GCM output (nonce + ciphertext + tag).")
        nonce, body, tag = raw[:12], raw[12:-16], raw[-16:]
        plain = wrap(line, fn, lambda: gcm(key_b, nonce, body, aad_arg(line, aad, fn), False, tag))
        return plain.decode("utf-8", "replace")

    @m.fn("aes_cbc_encrypt", 2, 3, doc=(
        "aes_cbc_encrypt(key, text, iv?) -> text\n"
        "Encrypt text with AES-CBC and PKCS#7 padding. Use it to talk to older systems.\n"
        "Prefer aes_gcm_encrypt(), which also detects tampering.\n"
        "\n"
        "Arguments:\n"
        "- key: hex text of 32, 48 or 64 characters.\n"
        "- text: the text to encrypt.\n"
        "- iv: optional hex text of 32 characters (default: random).\n"
        "\n"
        "Returns base64 text holding the iv and then the ciphertext."))
    def aes_cbc_encrypt_fn(line, key, text, iv=None):
        fn = "crypto.aes_cbc_encrypt"
        key_b, data = aes_args(line, key, text, fn)
        if iv is None:
            iv_b = os.urandom(16)
        else:
            check_type(line, iv, "text", fn, "iv")
            iv_b = wrap(line, fn, lambda: from_hex(iv, "iv"))
            if len(iv_b) != 16:
                fail(line, f"{fn}(): iv should be 16 bytes (32 hex characters), not {len(iv_b)} bytes.")
        return _b64.b64encode(iv_b + aes_cbc_encrypt(key_b, iv_b, pkcs7_pad(data))).decode("ascii")

    @m.fn("aes_cbc_decrypt", 2, doc=(
        "aes_cbc_decrypt(key, data) -> text\n"
        "Decrypt what aes_cbc_encrypt() made (base64 text of iv + ciphertext). Bad padding (wrong key or changed "
        "data) gives a catchable error."))
    def aes_cbc_decrypt_fn(line, key, data):
        fn = "crypto.aes_cbc_decrypt"
        key_b = wrap(line, fn, lambda: key_bytes(key, "key"))
        check_type(line, data, "text", fn, "data")
        raw = wrap(line, fn, lambda: from_base64(data, "data"))
        if len(raw) < 32 or len(raw) % 16:
            fail(line, f"{fn}(): the data should be iv + whole 16-byte blocks of ciphertext.")
        plain = wrap(line, fn, lambda: pkcs7_unpad(aes_cbc_decrypt(key_b, raw[:16], raw[16:])))
        return plain.decode("utf-8", "replace")

    # -- key derivation and MACs --------------------------------------------------------

    @m.fn("pbkdf2", 3, 5, doc=(
        "pbkdf2(password, salt, iterations, length?, hash?) -> text\n"
        "Turn a password into a key with PBKDF2-HMAC (default sha256, length 32 bytes), as hex text. salt is text "
        "or hex from random_bytes(16); use 600000+ iterations for password storage."))
    def pbkdf2(line, password, salt, iterations, length=32, algo="sha256"):
        fn = "crypto.pbkdf2"
        pw = text_or_bytes(line, password, fn, "password")
        salt_b = text_or_bytes(line, salt, fn, "salt")
        iterations = check_type(line, iterations, "whole", fn, "iterations")
        length = check_type(line, length, "whole", fn, "length")
        if not 1 <= iterations <= 100_000_000:
            fail(line, f"{fn}() needs iterations from 1 to 100,000,000.")
        if not 1 <= length <= 1024:
            fail(line, f"{fn}() needs length from 1 to 1024 bytes.")
        name = wrap(line, fn, lambda: hash_name(check_type(line, algo, "text", fn, "hash"), fn))
        return hashlib.pbkdf2_hmac(name, pw, salt_b, iterations, length).hex()

    @m.fn("scrypt", 2, 6, doc=(
        "scrypt(password, salt, n?, r?, p?, length?) -> text\n"
        "Turn a password into a key with scrypt (memory-hard, so GPUs can't guess fast), as hex text. Defaults: "
        "n = 16384, r = 8, p = 1, length = 32 bytes."))
    def scrypt(line, password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, length=32):
        fn = "crypto.scrypt"
        pw = text_or_bytes(line, password, fn, "password")
        salt_b = text_or_bytes(line, salt, fn, "salt")
        n = check_type(line, n, "whole", fn, "n")
        r = check_type(line, r, "whole", fn, "r")
        p = check_type(line, p, "whole", fn, "p")
        length = check_type(line, length, "whole", fn, "length")
        if n < 2 or n & (n - 1) or n > 2 ** 22:
            fail(line, f"{fn}() needs n to be a power of 2 from 2 to 4194304.")
        if not 1 <= r <= 64 or not 1 <= p <= 16 or not 1 <= length <= 1024:
            fail(line, f"{fn}() needs r from 1 to 64, p from 1 to 16 and length from 1 to 1024.")
        try:
            return hashlib.scrypt(pw, salt=salt_b, n=n, r=r, p=p, dklen=length, maxmem=256 * 1024 * 1024).hex()
        except (ValueError, MemoryError) as err:
            fail(line, f"{fn}() couldn't run with those settings: {err}.")

    @m.fn("hmac", 2, 3, doc=(
        "hmac(key, text, hash?) -> text\n"
        "A keyed signature (HMAC) of text, as hex. Compare it with hash.compare(),\n"
        "so timing can't leak the answer.\n"
        "\n"
        "Options:\n"
        "- hash: the hash to use (default: sha256). One of:\n"
        "  - sha256, sha1, sha384, sha512 or md5"))
    def hmac_fn(line, key, text, algo="sha256"):
        fn = "crypto.hmac"
        name = wrap(line, fn, lambda: hash_name(check_type(line, algo, "text", fn, "hash"), fn))
        return _hmac.new(text_or_bytes(line, key, fn, "key"), text_or_bytes(line, text, fn, "text"), HASHES[name]).hexdigest()

    # -- one-time codes -------------------------------------------------------------------

    def totp_options(line, options, fn):
        digits, period, algo = 6, 30, "sha1"
        if options is not None:
            check_type(line, options, "map", fn, "options")
            for key in options:
                if key not in ("digits", "period", "hash"):
                    fail(line, f"{fn}() doesn't know the option {_tb.to_text(key, True)}. Options: digits, period, hash.")
            digits = check_type(line, options.get("digits", 6), "whole", fn, "the digits option")
            period = check_type(line, options.get("period", 30), "whole", fn, "the period option")
            algo = wrap(line, fn, lambda: hash_name(check_type(line, options.get("hash", "sha1"), "text", fn, "the hash option"), fn))
            if not 6 <= digits <= 10:
                fail(line, f"{fn}() needs digits from 6 to 10.")
            if not 1 <= period <= 3600:
                fail(line, f"{fn}() needs period from 1 to 3600 seconds.")
        return digits, period, algo

    def when_arg(line, when, fn):
        if when is None:
            return _time.time()
        check_type(line, when, "number", fn, "time")
        return when

    @m.fn("totp", 1, 3, doc=(
        "totp(secret, time?, options?) -> text\n"
        "The 6-digit one-time code an authenticator app shows right now.\n"
        "secret is the base32 text behind the QR code. time is an optional timestamp to use instead of now.\n"
        "\n"
        "Options:\n"
        "- digits: how long the code is (default: 6).\n"
        "- period: seconds each code lasts (default: 30).\n"
        "- hash: the hash to use (default: \"sha1\")."))
    def totp(line, secret, when=None, options=None):
        fn = "crypto.totp"
        check_type(line, secret, "text", fn, "secret")
        key = wrap(line, fn, lambda: base32_secret(secret, "secret"))
        digits, period, algo = totp_options(line, options, fn)
        return hotp(key, int(when_arg(line, when, fn) // period), digits, algo)

    @m.fn("verify_totp", 2, 4, doc=(
        "verify_totp(secret, code, time?, options?) -> bool\n"
        "True if code is the current one-time code for the secret.\n"
        "The code just before or after also counts, because clocks drift. It is compared in constant time.\n"
        "\n"
        "Options:\n"
        "- digits, period, hash: as for totp().\n"
        "- window: how many steps either side to accept (default: 1)."))
    def verify_totp(line, secret, code, when=None, options=None):
        fn = "crypto.verify_totp"
        check_type(line, secret, "text", fn, "secret")
        key = wrap(line, fn, lambda: base32_secret(secret, "secret"))
        code = re.sub(r"\s", "", _tb.to_text(check_type(line, code, "text|number", fn, "code")))
        window = 1
        if options is not None:
            check_type(line, options, "map", fn, "options")
            window = check_type(line, options.get("window", 1), "whole", fn, "the window option")
            options = {k: v for k, v in options.items() if k != "window"}
            if not 0 <= window <= 10:
                fail(line, f"{fn}() needs window from 0 to 10.")
        digits, period, algo = totp_options(line, options, fn)
        step = int(when_arg(line, when, fn) // period)
        ok = False
        for delta in range(-window, window + 1):
            ok |= _hmac.compare_digest(hotp(key, step + delta, digits, algo), code)
        return ok

    @m.fn("hotp", 2, 3, doc=(
        "hotp(secret, counter, digits?) -> text\n"
        "The counter-based one-time code (HOTP, RFC 4226) for a base32 secret."))
    def hotp_fn(line, secret, counter, digits=6):
        fn = "crypto.hotp"
        check_type(line, secret, "text", fn, "secret")
        key = wrap(line, fn, lambda: base32_secret(secret, "secret"))
        counter = check_type(line, counter, "whole", fn, "counter")
        digits = check_type(line, digits, "whole", fn, "digits")
        if counter < 0 or not 6 <= digits <= 10:
            fail(line, f"{fn}() needs counter of 0 or more and digits from 6 to 10.")
        return hotp(key, counter, digits, "sha1")

    @m.fn("totp_secret", 0, 1, doc=(
        "totp_secret(bytes?) -> text\n"
        "A new random base32 secret (default 20 bytes = 32 characters) to put in an authenticator app."))
    def totp_secret(line, n=20):
        n = check_type(line, n, "whole", "crypto.totp_secret", "bytes")
        if not 10 <= n <= 64:
            fail(line, "crypto.totp_secret() needs bytes from 10 to 64.")
        return _b64.b32encode(os.urandom(n)).decode("ascii").rstrip("=")

    # -- JWT ---------------------------------------------------------------------------------

    def jwt_parts(line, token, fn):
        check_type(line, token, "text", fn, "token")
        parts = token.strip().split(".")
        if len(parts) != 3:
            fail(line, f"{fn}(): a JWT has three parts separated by dots (header.payload.signature); this has "
                       f"{len(parts)}.", reason="format")
        try:
            header = _json.loads(unb64url(parts[0], "the header").decode("utf-8"))
            payload = _json.loads(unb64url(parts[1], "the payload").decode("utf-8"))
            signature = unb64url(parts[2], "the signature") if parts[2] else b""
        except (CryptoError, ValueError, UnicodeDecodeError) as err:
            fail(line, f"{fn}(): the token isn't a readable JWT ({str(err).rstrip('.')}).", reason="format")
        if not isinstance(header, dict) or not isinstance(payload, dict):
            fail(line, f"{fn}(): the token's header and payload should be JSON objects.", reason="format")
        from tblib.json import from_json
        return from_json(header), from_json(payload), signature, f"{parts[0]}.{parts[1]}".encode("ascii")

    @m.fn("jwt_sign", 2, 3, doc=(
        "jwt_sign(payload, key, alg?) -> text\n"
        "Make a signed JSON Web Token from a payload map with a shared secret: alg is HS256 (default), HS384 or "
        "HS512. Add \"exp\" (a timestamp) to the payload yourself to make it expire. RS* signing needs a private key "
        "and isn't included; verifying RS* tokens is (jwt_verify)."))
    def jwt_sign(line, payload, key, alg="HS256"):
        fn = "crypto.jwt_sign"
        check_type(line, payload, "map", fn, "payload")
        check_type(line, alg, "text", fn, "alg")
        alg = alg.upper()
        if alg not in JWT_HS:
            fail(line, f"{fn}() can sign with HS256, HS384 or HS512, not {alg}.")
        from tblib.json import to_plain
        header = _json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":"))
        body = _json.dumps(to_plain(payload, line), separators=(",", ":"), ensure_ascii=False)
        signing = b64url(header.encode()) + "." + b64url(body.encode("utf-8"))
        sig = _hmac.new(text_or_bytes(line, key, fn, "key"), signing.encode("ascii"), HASHES[JWT_HS[alg]]).digest()
        return signing + "." + b64url(sig)

    @m.fn("jwt_verify", 2, 3, doc=(
        "jwt_verify(token, key, options?) -> map\n"
        "Check a JWT's signature and times, and give back its payload.\n"
        "\n"
        "The key:\n"
        "- For HS256, HS384 or HS512: the shared secret.\n"
        "- For RS256, RS384 or RS512: a PEM public key or certificate (checked with pure-Python RSA).\n"
        "\n"
        "Options:\n"
        "- leeway: seconds of clock difference to allow (default: 60).\n"
        "- now: a timestamp to use as the current time.\n"
        "- algs: a list like [\"HS256\"] to allow only some algorithms.\n"
        "\n"
        "Errors:\n"
        "Each one is catchable, and err.reason says which:\n"
        "- \"signature\": the signature is bad.\n"
        "- \"alg\": the token says \"alg\": \"none\" (or an algorithm you did not allow).\n"
        "- \"expired\": the token has expired (its exp time).\n"
        "- \"not_yet\": the token is not valid yet (its nbf time)."))
    def jwt_verify(line, token, key, options=None):
        fn = "crypto.jwt_verify"
        header, payload, signature, signing = jwt_parts(line, token, fn)
        leeway, now, allowed = 60, _time.time(), None
        if options is not None:
            check_type(line, options, "map", fn, "options")
            for k in options:
                if k not in ("leeway", "now", "algs"):
                    fail(line, f"{fn}() doesn't know the option {_tb.to_text(k, True)}. Options: leeway, now, algs.")
            leeway = check_type(line, options.get("leeway", 60), "number", fn, "the leeway option")
            now = check_type(line, options.get("now", now), "number", fn, "the now option")
            if options.get("algs") is not None:
                allowed = [str(a).upper() for a in check_type(line, options["algs"], "list", fn, "the algs option")]
        alg = str(header.get("alg") or "").upper()
        if alg in ("", "NONE"):
            fail(line, f"{fn}(): the token is unsigned (alg none), so it can't be trusted.", reason="alg")
        if allowed is not None and alg not in allowed:
            fail(line, f"{fn}(): the token uses {alg}, which isn't in the allowed list {allowed}.", reason="alg")
        if alg in JWT_HS:
            if not isinstance(key, (str, bytes, bytearray)):
                fail(line, f"{fn}() needs key to be the shared secret (text) for {alg}.")
            if isinstance(key, str) and "-----BEGIN" in key:
                fail(line, f"{fn}(): the token says {alg} (a shared secret) but you gave a PEM key. Refusing, "
                           "because mixing them up is a known attack.", reason="alg")
            expected = _hmac.new(to_bytes(key), signing, HASHES[JWT_HS[alg]]).digest()
            ok = _hmac.compare_digest(expected, signature)
        elif alg in JWT_RS:
            pub = wrap(line, fn, lambda: public_key_from(key, "key"))
            ok = wrap(line, fn, lambda: rsa_verify_pkcs1(pub, signing, signature, JWT_RS[alg]))
        else:
            fail(line, f"{fn}() can check HS256/384/512 and RS256/384/512 tokens, not {alg}.", reason="alg")
        if not ok:
            fail(line, f"{fn}(): the signature doesn't match, so the token was changed or signed with another key.",
                 reason="signature")
        exp, nbf = payload.get("exp"), payload.get("nbf")
        if _tb.is_number(exp) and now > exp + leeway:
            fail(line, f"{fn}(): the token expired at {_dt.datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S')}.",
                 reason="expired", expired_at=exp)
        if _tb.is_number(nbf) and now < nbf - leeway:
            fail(line, f"{fn}(): the token isn't valid until {_dt.datetime.fromtimestamp(nbf).strftime('%Y-%m-%d %H:%M:%S')}.",
                 reason="not_yet")
        return payload

    @m.fn("jwt_decode", 1, doc=(
        "jwt_decode(token) -> map\n"
        "Read a JWT WITHOUT checking its signature. Good for a look inside.\n"
        "Never trust the payload without jwt_verify().\n"
        "\n"
        "Returns a map with:\n"
        "- \"header\": the token's header map.\n"
        "- \"payload\": the token's payload map.\n"
        "- \"alg\": the algorithm the header names.\n"
        "- \"expired\": true if its exp time has passed.\n"
        "- \"verified\": always false."))
    def jwt_decode(line, token):
        header, payload, _sig, _signing = jwt_parts(line, token, "crypto.jwt_decode")
        exp = payload.get("exp")
        return {"header": header, "payload": payload, "alg": header.get("alg"),
                "expired": bool(_tb.is_number(exp) and _time.time() > exp), "verified": False}

    # -- passwords ---------------------------------------------------------------------------

    @m.fn("password_hash", 1, doc=(
        "password_hash(password) -> text\n"
        "Hash a password for storing with scrypt and a random salt. The answer looks like "
        "$scrypt$n=16384,r=8,p=1$<salt>$<hash> and password_verify() checks it. Never store passwords any other way."))
    def password_hash(line, password):
        pw = text_or_bytes(line, password, "crypto.password_hash", "password")
        salt = os.urandom(16)
        digest = hashlib.scrypt(pw, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
        return f"$scrypt$n={SCRYPT_N},r={SCRYPT_R},p={SCRYPT_P}${b64url(salt)}${b64url(digest)}"

    @m.fn("password_verify", 2, doc=(
        "password_verify(password, hash) -> bool\n"
        "True if password matches a hash from password_hash() (constant-time compare)."))
    def password_verify(line, password, hashed):
        fn = "crypto.password_verify"
        pw = text_or_bytes(line, password, fn, "password")
        check_type(line, hashed, "text", fn, "hash")
        m_ = re.fullmatch(r"\$scrypt\$n=(\d+),r=(\d+),p=(\d+)\$([A-Za-z0-9_\-]+)\$([A-Za-z0-9_\-]+)", hashed.strip())
        if not m_:
            fail(line, f"{fn}(): the hash isn't one made by crypto.password_hash() ($scrypt$...).")
        n, r, p = int(m_.group(1)), int(m_.group(2)), int(m_.group(3))
        try:
            salt = unb64url(m_.group(4), "the salt")
            want = unb64url(m_.group(5), "the hash")
            got = hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=len(want), maxmem=256 * 1024 * 1024)
        except (CryptoError, ValueError) as err:
            fail(line, f"{fn}(): the hash is damaged ({str(err).rstrip('.')}).")
        return _hmac.compare_digest(got, want)

    # -- RSA and certificates --------------------------------------------------------------

    @m.fn("rsa_verify", 3, 4, doc=(
        "rsa_verify(text, signature, public_key, hash?) -> bool\n"
        "True if signature is a valid RSA PKCS#1 v1.5 signature of text.\n"
        "Pure Python (pow()), no OpenSSL.\n"
        "\n"
        "Arguments:\n"
        "- signature: base64 or hex text.\n"
        "- public_key: a PEM public key. One of:\n"
        "  - a PUBLIC KEY block (-----BEGIN PUBLIC KEY-----)\n"
        "  - an RSA PUBLIC KEY block\n"
        "  - a CERTIFICATE\n"
        "- hash: the hash used (default: sha256). One of:\n"
        "  - sha256, sha1, sha384 or sha512"))
    def rsa_verify(line, text, signature, public_key, algo="sha256"):
        fn = "crypto.rsa_verify"
        data = text_or_bytes(line, text, fn, "text")
        check_type(line, signature, "text", fn, "signature")
        name = wrap(line, fn, lambda: hash_name(check_type(line, algo, "text", fn, "hash"), fn))
        if name not in DIGEST_INFO:
            fail(line, f"{fn}() can use sha1, sha256, sha384 or sha512, not {algo}.")
        sig_text = signature.strip()
        sig = wrap(line, fn, lambda: from_hex(sig_text, "signature") if re.fullmatch(r"[0-9A-Fa-f\s:]+", sig_text)
                   and len(re.sub(r"[\s:]", "", sig_text)) % 2 == 0 else from_base64(sig_text, "signature"))
        pub = wrap(line, fn, lambda: public_key_from(public_key, "public_key"))
        return wrap(line, fn, lambda: rsa_verify_pkcs1(pub, data, sig, name))

    @m.fn("public_key", 1, doc=(
        "public_key(pem) -> map\n"
        "Read a PEM public key, or the key inside a certificate.\n"
        "\n"
        "Returns a map with:\n"
        "- \"kind\": \"rsa\", \"ec\" or \"ed25519\".\n"
        "- \"bits\": the key size.\n"
        "- \"n\" (hex) and \"e\": for RSA keys.\n"
        "- \"curve\": for EC keys."))
    def public_key_fn(line, pem):
        fn = "crypto.public_key"
        if not isinstance(pem, (bytes, bytearray)):
            check_type(line, pem, "text", fn, "pem")
        key = wrap(line, fn, lambda: public_key_from(pem, "pem"))
        return {k: (format(v, "x") if k == "n" else v) for k, v in key.items()}

    @m.fn("x509_parse", 1, 2, doc=(
        "x509_parse(pem_or_der, now?) -> map\n"
        "Read an X.509 certificate. Give it PEM text, or DER as base64 or hex text.\n"
        "now is an optional timestamp to judge validity against.\n"
        "\n"
        "Returns a map with:\n"
        "- \"subject\", \"issuer\": maps with CN, O, C... and \"text\".\n"
        "- \"common_name\": the subject's CN.\n"
        "- \"dns_names\": the host names the certificate covers.\n"
        "- \"sans\": all alternative names (dns, ip, email, uri).\n"
        "- \"not_before\", \"not_after\": when it starts and stops being valid.\n"
        "- \"valid_now\", \"expired\", \"days_left\": its validity now (or at now).\n"
        "- \"is_ca\", \"self_signed\": what kind of certificate it is.\n"
        "- \"key_usage\", \"ext_key_usage\": what the key may be used for.\n"
        "- \"signature_algorithm\": how it was signed.\n"
        "- \"public_key\": a map with kind and bits.\n"
        "- \"serial\": the serial number.\n"
        "- \"fingerprint_sha256\", \"fingerprint_sha1\", \"fingerprint_md5\": colon hex.\n"
        "- \"extensions\": the names of its extensions, sorted.\n"
        "- \"pem\": the certificate as PEM text."))
    def x509_parse(line, pem, now=None):
        fn = "crypto.x509_parse"
        if not isinstance(pem, (bytes, bytearray)):
            check_type(line, pem, "text", fn, "pem_or_der")
        if now is not None:
            check_type(line, now, "number", fn, "now")
        der = wrap(line, fn, lambda: der_input(pem, "pem_or_der", ("CERTIFICATE",)))
        return wrap(line, fn, lambda: x509_parse_bytes(der, now))

    @m.fn("x509_parse_all", 1, doc=(
        "x509_parse_all(pem) -> list\n"
        "Every certificate in PEM text (a chain file), each parsed like x509_parse()."))
    def x509_parse_all(line, pem):
        fn = "crypto.x509_parse_all"
        check_type(line, pem, "text", fn, "pem")
        blocks = wrap(line, fn, lambda: parse_pem(pem, ("CERTIFICATE",)))
        return [wrap(line, fn, lambda d=der: x509_parse_bytes(d)) for _label, der in blocks]

    @m.fn("hex_to_base64", 1, doc="hex_to_base64(hex) -> text\nRe-encode hex text (a key or hash) as base64.")
    def hex_to_base64(line, text):
        check_type(line, text, "text", "crypto.hex_to_base64", "hex")
        return _b64.b64encode(wrap(line, "crypto.hex_to_base64", lambda: from_hex(text, "hex"))).decode("ascii")

    @m.fn("base64_to_hex", 1, doc="base64_to_hex(base64) -> text\nRe-encode base64 text as hex.")
    def base64_to_hex(line, text):
        check_type(line, text, "text", "crypto.base64_to_hex", "base64")
        return wrap(line, "crypto.base64_to_hex", lambda: from_base64(text, "base64")).hex()

    return m
