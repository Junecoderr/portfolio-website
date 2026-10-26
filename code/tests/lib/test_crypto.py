"""crypto library: AES (NIST vectors), KDFs, TOTP (RFC 6238), JWT, passwords,
RSA verify and X.509 parsing. Certificates and keys are built in the test
with a tiny DER encoder, so no OpenSSL or key files are needed."""

import base64
import hashlib
import json
import random
import time

import pytest

from conftest import run_tb, tb_error
from tblib import crypto


def run(source):
    return run_tb("import crypto\n" + source)


# -- a tiny DER encoder and RSA key generator for the tests ------------------------

def der(tag, body):
    if len(body) < 0x80:
        return bytes([tag, len(body)]) + body
    length = len(body).to_bytes((len(body).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(length)]) + length + body


def der_int(n):
    body = n.to_bytes((n.bit_length() + 8) // 8, "big")
    return der(0x02, body)


def der_oid(text):
    parts = [int(p) for p in text.split(".")]
    out = [40 * parts[0] + parts[1]]
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.insert(0, 0x80 | (p & 0x7F))
            p >>= 7
        out.extend(chunk)
    return der(0x06, bytes(out))


def seq(*items):
    return der(0x30, b"".join(items))


def utf8(text):
    return der(0x0C, text.encode())


def name(**attrs):
    oids = {"CN": "2.5.4.3", "O": "2.5.4.10", "C": "2.5.4.6", "OU": "2.5.4.11"}
    return seq(*[der(0x31, seq(der_oid(oids[k]), utf8(v))) for k, v in attrs.items()])


def utc(text):
    return der(0x17, text.encode())


def pem(label, data):
    body = base64.b64encode(data).decode()
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n"


def is_probable_prime(n, rounds=16):
    if n < 4:
        return n in (2, 3)
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    rng = random.Random(7)
    for _ in range(rounds):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def make_prime(bits, rng):
    while True:
        n = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if is_probable_prime(n):
            return n


@pytest.fixture(scope="module")
def rsa_key():
    rng = random.Random(2024)
    p, q = make_prime(512, rng), make_prime(512, rng)
    n, e = p * q, 65537
    d = pow(e, -1, (p - 1) * (q - 1))
    return {"n": n, "e": e, "d": d}


def spki(key):
    rsa_pub = seq(der_int(key["n"]), der_int(key["e"]))
    return seq(seq(der_oid("1.2.840.113549.1.1.1"), der(0x05, b"")), der(0x03, b"\0" + rsa_pub))


def rsa_sign(key, data, algo="sha256"):
    digest = getattr(hashlib, algo)(data).digest()
    t = crypto.DIGEST_INFO[algo] + digest
    k = (key["n"].bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


def general_name(tag, text):
    return der(0x80 | tag, text.encode())


def make_cert(key, cn="www.example.com", ca=False, self_signed=True, not_after="300101000000Z", sans=None):
    sans = sans if sans is not None else ["www.example.com", "example.com"]
    san_ext = seq(der_oid("2.5.29.17"), der(0x04, seq(*[general_name(2, s) for s in sans] + [der(0x87, bytes([192, 0, 2, 1]))])))
    bc_body = seq(der(0x01, b"\xff")) if ca else seq()
    bc_ext = seq(der_oid("2.5.29.19"), der(0x01, b"\xff"), der(0x04, bc_body))
    ku_ext = seq(der_oid("2.5.29.15"), der(0x01, b"\xff"), der(0x04, der(0x03, bytes([0x05, 0xA0]))))  # digitalSignature, keyEncipherment
    eku_ext = seq(der_oid("2.5.29.37"), der(0x04, seq(der_oid("1.3.6.1.5.5.7.3.1"), der_oid("1.3.6.1.5.5.7.3.2"))))
    issuer = name(CN=cn, O="Example Org", C="NL") if self_signed else name(CN="Example Root CA", O="Example Org", C="NL")
    tbs = seq(
        der(0xA0, der_int(2)),
        der_int(0x1A2B3C),
        seq(der_oid("1.2.840.113549.1.1.11"), der(0x05, b"")),
        issuer,
        seq(utc("240101000000Z"), utc(not_after)),
        name(CN=cn, O="Example Org", C="NL"),
        spki(key),
        der(0xA3, seq(san_ext, bc_ext, ku_ext, eku_ext)),
    )
    signature = rsa_sign(key, tbs)
    return seq(tbs, seq(der_oid("1.2.840.113549.1.1.11"), der(0x05, b"")), der(0x03, b"\0" + signature))


# -- AES ------------------------------------------------------------------------------

def test_aes_block_vectors():
    rk = crypto.expand_key(bytes(range(16)))
    assert crypto.encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff")).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"
    rk = crypto.expand_key(bytes(range(24)))
    assert crypto.encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff")).hex() == "dda97ca4864cdfe06eaf70a0ec0d7191"
    rk = crypto.expand_key(bytes(range(32)))
    ct = crypto.encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff"))
    assert ct.hex() == "8ea2b7ca516745bfeafc49904b496089"
    assert crypto.decrypt_block(rk, ct).hex() == "00112233445566778899aabbccddeeff"


def test_gcm_nist_vectors():
    key = bytes.fromhex("feffe9928665731c6d6a8f9467308308")
    iv = bytes.fromhex("cafebabefacedbaddecaf888")
    plain = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a721c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39")
    aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
    out = crypto.gcm(key, iv, plain, aad, True)
    assert out[-16:].hex() == "5bc94fbc3221a5db94fae95ae7121a47"
    assert out[:-16].hex().startswith("42831ec2217774244b7221b784d0d49c")
    assert crypto.gcm(key, iv, out[:-16], aad, False, out[-16:]) == plain
    assert crypto.gcm(bytes(16), bytes(12), b"", b"", True).hex() == "58e2fccefa7e3061367f1d57a4e7455a"


def test_aes_gcm_round_trip_and_tamper():
    src = '''let key = crypto.random_bytes(32)
let secret = crypto.aes_gcm_encrypt(key, "the plan: Tanisha meets at 9", "ticket-42")
say(len(key), secret != "the plan: Tanisha meets at 9")
say(crypto.aes_gcm_decrypt(key, secret, "ticket-42"))
try { crypto.aes_gcm_decrypt(key, secret, "ticket-43") } catch e { say(e.message) }
try { crypto.aes_gcm_decrypt(crypto.random_bytes(32), secret, "ticket-42") } catch e { say("wrong key caught") }
let other = crypto.aes_gcm_encrypt(key, "the plan: Tanisha meets at 9", "ticket-42")
say(other != secret)'''
    out = run(src)
    assert out[0] == "64 true"
    assert out[1] == "the plan: Tanisha meets at 9"
    assert "wrong key, wrong extra data (aad), or the data was changed" in out[2]
    assert out[3] == "wrong key caught" and out[4] == "true"


def test_aes_cbc_round_trip_and_fixed_iv():
    key = "000102030405060708090a0b0c0d0e0f"
    src = f'''let c = crypto.aes_cbc_encrypt("{key}", "hello", "00000000000000000000000000000000")
say(c)
say(crypto.aes_cbc_decrypt("{key}", c))
try {{ crypto.aes_cbc_decrypt("{key}", crypto.aes_cbc_encrypt(crypto.random_bytes(16), "x")) }} catch e {{ say(e.message) }}'''
    out = run(src)
    raw = base64.b64decode(out[0])
    assert raw[:16] == bytes(16) and len(raw) == 32
    assert out[1] == "hello"
    assert "bad padding" in out[2]


def test_aes_key_errors():
    assert "key of 16, 24 or 32 bytes as hex text" in str(tb_error('import crypto\ncrypto.aes_gcm_encrypt("short", "x")'))
    assert "too short to be AES-GCM output" in str(tb_error('import crypto\ncrypto.aes_gcm_decrypt(crypto.random_bytes(32), "QUJD")'))
    assert "iv should be 16 bytes" in str(tb_error('import crypto\ncrypto.aes_cbc_encrypt(crypto.random_bytes(16), "x", "abcd")'))


# -- KDFs, HMAC, random -------------------------------------------------------------------

def test_pbkdf2_scrypt_hmac():
    out = run('say(crypto.pbkdf2("password", "salt", 1, 20, "sha1"))\nsay(crypto.pbkdf2("password", "salt", 4096, 20, "sha1"))')
    assert out == ["0c60c80f961f0e71f3a9b524af6012062fe037a6", "4b007901b765489abead49d926f721d065a429c1"]
    out = run('say(crypto.scrypt("password", "NaCl", 1024, 8, 16, 64))')
    assert out[0].startswith("fdbabe1c9d3472007856e7190d01e9fe7c6ad7cbc8237830e77376634b373162")
    out = run('say(crypto.hmac("key", "The quick brown fox jumps over the lazy dog"))')
    assert out == ["f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"]
    assert run('say(crypto.hmac("key", "data", "sha1"))') == ["104152c5bfdca07bc633eebd46199f0255c9f49d"]
    assert "doesn't know the hash" in str(tb_error('import crypto\ncrypto.hmac("k", "d", "sha3")'))


def test_random_bytes_and_token():
    out = run('let a = crypto.random_bytes(16)\nlet b = crypto.random_bytes(16)\nsay(len(a), a != b, len(crypto.random_token()) >= 43)')
    assert out == ["32 true true"]
    assert "needs n from 1 to" in str(tb_error("import crypto\ncrypto.random_bytes(0)"))


# -- TOTP -----------------------------------------------------------------------------------

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_totp_rfc6238_vectors():
    src = f'''let s = "{RFC_SECRET}"
let o = {{"digits": 8}}
say(crypto.totp(s, 59, o), crypto.totp(s, 1111111109, o), crypto.totp(s, 1234567890, o), crypto.totp(s, 20000000000, o))
say(crypto.totp(s, 59))
say(crypto.verify_totp(s, "94287082", 59, {{"digits": 8}}), crypto.verify_totp(s, "94287082", 59 + 30, {{"digits": 8}}), crypto.verify_totp(s, "94287082", 59 + 61, {{"digits": 8}}))
say(crypto.verify_totp(s, 94287082, 59, {{"digits": 8, "window": 0}}), crypto.verify_totp(s, "00000000", 59, {{"digits": 8}}))
say(crypto.hotp(s, 0), crypto.hotp(s, 1), crypto.hotp(s, 9))'''
    out = run(src)
    assert out[0] == "94287082 07081804 89005924 65353130"
    assert out[1] == "94287082"[-6:]
    assert out[2] == "true true false"
    assert out[3] == "true false"
    assert out[4] == "755224 287082 520489"


def test_totp_sha256_and_secret():
    secret = base64.b32encode(b"12345678901234567890123456789012").decode()
    assert run(f'say(crypto.totp("{secret}", 59, {{"digits": 8, "hash": "sha256"}}))') == ["46119246"]
    out = run("let s = crypto.totp_secret()\nsay(len(s), len(crypto.totp(s)))")
    assert out == ["32 6"]
    assert "base32 secret" in str(tb_error('import crypto\ncrypto.totp("not base32 at all!")'))


# -- JWT --------------------------------------------------------------------------------------

def test_jwt_sign_verify_decode():
    now = int(time.time())
    src = f'''let token = crypto.jwt_sign({{"sub": "tanisha", "role": "analyst", "exp": {now + 600}}}, "secret-key")
say(len(token.split(".")))
let payload = crypto.jwt_verify(token, "secret-key")
say(payload.sub, payload.role)
let peek = crypto.jwt_decode(token)
say(peek.alg, peek.verified, peek.expired, peek.header.typ)
try {{ crypto.jwt_verify(token, "other-key") }} catch e {{ say(e.reason, e.message) }}
try {{ crypto.jwt_verify(token + "x", "secret-key") }} catch e {{ say(e.reason) }}
let old = crypto.jwt_sign({{"sub": "x", "exp": {now - 3600}}}, "k", "HS512")
try {{ crypto.jwt_verify(old, "k") }} catch e {{ say(e.reason) }}
say(crypto.jwt_verify(old, "k", {{"now": {now - 3700}}}).sub)
let soon = crypto.jwt_sign({{"sub": "x", "nbf": {now + 3600}}}, "k", "HS384")
try {{ crypto.jwt_verify(soon, "k") }} catch e {{ say(e.reason) }}
try {{ crypto.jwt_verify(token, "secret-key", {{"algs": ["RS256"]}}) }} catch e {{ say(e.reason) }}'''
    out = run(src)
    assert out[0] == "3"
    assert out[1] == "tanisha analyst"
    assert out[2] == "HS256 false false JWT"
    assert out[3].startswith("signature ") and "doesn't match" in out[3]
    assert out[4:] == ["signature", "expired", "x", "not_yet", "alg"]


def test_jwt_rejects_alg_none_and_pem_confusion(rsa_key):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps({"sub": "x"}).encode()).decode().rstrip("=")
    err = tb_error(f'import crypto\ncrypto.jwt_verify("{header}.{body}.", "k")')
    assert "unsigned (alg none)" in str(err)
    public = pem("PUBLIC KEY", spki(rsa_key)).replace("\n", "\\n")
    token = run('say(crypto.jwt_sign({"a": 1}, "k"))')[0]
    err = tb_error(f'import crypto\ncrypto.jwt_verify("{token}", "{public}")')
    assert "you gave a PEM key" in str(err)
    assert "three parts" in str(tb_error('import crypto\ncrypto.jwt_decode("abc")'))


def test_jwt_rs256_verify(rsa_key):
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(b'{"sub":"svc","iss":"test"}').decode().rstrip("=")
    signing = f"{header}.{body}".encode()
    sig = base64.urlsafe_b64encode(rsa_sign(rsa_key, signing)).decode().rstrip("=")
    token = f"{header}.{body}.{sig}"
    public = pem("PUBLIC KEY", spki(rsa_key)).replace("\n", "\\n")
    cert = pem("CERTIFICATE", make_cert(rsa_key)).replace("\n", "\\n")
    out = run(f'say(crypto.jwt_verify("{token}", "{public}").sub)\nsay(crypto.jwt_verify("{token}", "{cert}").iss)')
    assert out == ["svc", "test"]
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert "signature doesn't match" in str(tb_error(f'import crypto\ncrypto.jwt_verify("{bad}", "{public}")'))


# -- passwords -----------------------------------------------------------------------------------

def test_password_hash_and_verify():
    src = '''let h = crypto.password_hash("correct horse")
say(h.starts_with("$scrypt$n=16384,r=8,p=1$"), crypto.password_verify("correct horse", h), crypto.password_verify("wrong", h))
say(crypto.password_hash("correct horse") != h)'''
    assert run(src) == ["true true false", "true"]
    assert "isn't one made by crypto.password_hash()" in str(tb_error('import crypto\ncrypto.password_verify("x", "md5:abc")'))


# -- RSA and X.509 -------------------------------------------------------------------------------

def test_rsa_verify_and_public_key(rsa_key):
    data = b"attached: invoice.pdf"
    sig = base64.b64encode(rsa_sign(rsa_key, data)).decode()
    sig_sha1 = rsa_sign(rsa_key, data, "sha1").hex()
    public = pem("PUBLIC KEY", spki(rsa_key)).replace("\n", "\\n")
    pkcs1 = pem("RSA PUBLIC KEY", seq(der_int(rsa_key["n"]), der_int(rsa_key["e"]))).replace("\n", "\\n")
    src = f'''let pub = "{public}"
say(crypto.rsa_verify("attached: invoice.pdf", "{sig}", pub))
say(crypto.rsa_verify("attached: invoice.pdf", "{sig}", "{pkcs1}"))
say(crypto.rsa_verify("attached: invoice.pdf", "{sig_sha1}", pub, "sha1"))
say(crypto.rsa_verify("attached: invoice.pdf", "{sig_sha1}", pub))
say(crypto.rsa_verify("attached: invoice.PDF", "{sig}", pub))
let k = crypto.public_key(pub)
say(k.kind, k.bits, k.e, len(k.n))'''
    out = run(src)
    assert out[:5] == ["true", "true", "true", "false", "false"]
    assert out[5] == "rsa 1024 65537 256"
    assert "has no PUBLIC KEY or RSA PUBLIC KEY or CERTIFICATE block" in str(
        tb_error('import crypto\ncrypto.rsa_verify("x", "AAAA", "-----BEGIN PRIVATE KEY-----\\nAAAA\\n-----END PRIVATE KEY-----")'))


def test_x509_parse(rsa_key):
    cert_pem = pem("CERTIFICATE", make_cert(rsa_key)).replace("\n", "\\n")
    src = f'''let c = crypto.x509_parse("{cert_pem}", 1735689600)
say(c.common_name, c.subject.O, c.subject.C, c.issuer.CN, c.self_signed, c.is_ca)
say(c.dns_names, c.sans.ip)
say(c.not_before, c.not_after, c.valid_now, c.expired, c.days_left > 1800)
say(c.key_usage, c.ext_key_usage, c.signature_algorithm, c.public_key.kind, c.public_key.bits, c.serial, c.version)
say(len(c.fingerprint_sha256), c.extensions)
say(c.subject.text)'''
    out = run(src)
    assert out[0] == "www.example.com Example Org NL www.example.com true false"
    assert out[1] == '["www.example.com", "example.com"] ["192.0.2.1"]'
    assert out[2] == "2024-01-01 00:00:00 2030-01-01 00:00:00 true false true"
    assert out[3] == '["digitalSignature", "keyEncipherment"] ["serverAuth", "clientAuth"] sha256WithRSAEncryption rsa 1024 1a2b3c 3'
    assert out[4] == '95 ["basicConstraints", "extKeyUsage", "keyUsage", "subjectAltName"]'
    assert out[5] == "CN=www.example.com, O=Example Org, C=NL"
    der_bytes = make_cert(rsa_key)
    assert crypto.x509_parse_bytes(der_bytes)["fingerprint_sha256"] == crypto.colon_hex(hashlib.sha256(der_bytes).hexdigest())


def test_x509_expired_ca_and_chain(rsa_key):
    expired = make_cert(rsa_key, cn="old.example.com", not_after="250101000000Z", self_signed=False)
    ca = make_cert(rsa_key, cn="Example Root CA", ca=True, sans=[])
    chain = (pem("CERTIFICATE", expired) + pem("CERTIFICATE", ca)).replace("\n", "\\n")
    src = f'''let certs = crypto.x509_parse_all("{chain}")
say(len(certs))
let old = certs[0]
say(old.common_name, old.issuer.CN, old.self_signed, old.expired, old.days_left < 0)
say(certs[1].is_ca, certs[1].dns_names)
say(crypto.x509_parse(certs[1].pem).common_name)'''
    out = run(src)
    assert out[0] == "2"
    assert out[1] == "old.example.com Example Root CA false true true"
    assert out[2] == 'true []'
    assert out[3] == "Example Root CA"
    b64 = base64.b64encode(ca).decode()
    assert run(f'say(crypto.x509_parse("{b64}").common_name)') == ["Example Root CA"]
    assert "isn't an X.509 certificate" in str(tb_error('import crypto\ncrypto.x509_parse("3003020101")'))
    assert "has no CERTIFICATE block" in str(tb_error('import crypto\ncrypto.x509_parse("-----BEGIN PUBLIC KEY-----\\nAAAA\\n-----END PUBLIC KEY-----")'))


def test_hex_base64_helpers():
    assert run('say(crypto.hex_to_base64("6869"), crypto.base64_to_hex("aGk="))') == ["aGk= 6869"]
    assert "should be hex text" in str(tb_error('import crypto\ncrypto.hex_to_base64("zz")'))


def test_crypto_errors_are_catchable():
    assert run('try { crypto.aes_gcm_decrypt("00", "x") } catch e { say("caught", e.kind) }') == ["caught error"]
