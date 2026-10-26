import hashlib

import pytest

from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_text_hashes_match_known_values():
    src = 'import hash\nsay(hash.md5("hello"))\nsay(hash.sha1("hello"))\nsay(hash.sha256("hello"))\nsay(len(hash.sha512("")))'
    assert run_tb(src) == [
        "5d41402abc4b2a76b9719d911017c592",
        "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d",
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        "128",
    ]


def test_text_is_hashed_as_utf8():
    assert run_tb('import hash\nsay(hash.md5("café"))') == [hashlib.md5("café".encode()).hexdigest()]


def test_file_hashes_match_text_hashes_and_hashlib(tmp_path):
    data = bytes(range(256)) * 5000  # bigger than one chunk, binary
    (tmp_path / "bin.dat").write_bytes(data)
    (tmp_path / "t.txt").write_bytes(b"hello")
    src = '''import hash
say(hash.file_md5("t.txt") == hash.md5("hello"), hash.file_sha1("t.txt") == hash.sha1("hello"))
say(hash.file_sha256("bin.dat"))
say(hash.file_sha512("t.txt") == hash.sha512("hello"))
let all = hash.file("bin.dat")
say(all.md5, all.sha1, all.size)'''
    out = run_tb(src)
    assert out[0] == "true true"
    assert out[1] == hashlib.sha256(data).hexdigest()
    assert out[2] == "true"
    assert out[3] == f"{hashlib.md5(data).hexdigest()} {hashlib.sha1(data).hexdigest()} {len(data)}"


def test_hmac_and_compare():
    src = '''import hash
say(hash.hmac_sha256("key", "The quick brown fox jumps over the lazy dog"))
say(hash.compare("secret", "secret"), hash.compare("secret", "Secret"), hash.compare("", ""), hash.compare("a", "ab"))'''
    assert run_tb(src) == [
        "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
        "true false true false",
    ]


@pytest.mark.parametrize("src,message", [
    ("hash.md5(5)", "hash.md5() needs text to be text, not a number."),
    ('hash.file_sha256("missing.bin")', "I couldn't find 'missing.bin'"),
    ('hash.file_md5("")', "hash.file_md5() needs a path, but got empty text."),
    ('hash.file(".")', "is a folder, not a file."),
    ('hash.hmac_sha256(nil, "x")', "hash.hmac_sha256() needs key to be text, not nil."),
    ('hash.compare("a", 1)', "hash.compare() needs b to be text"),
])
def test_hash_errors(src, message):
    assert message in str(tb_error(f"import hash\n{src}"))
