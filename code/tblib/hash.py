"""hash: fingerprints (hashes) of text and files."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import os

from tblib._api import Module, check_type, fail, fail_os

CHUNK = 1024 * 1024  # files are hashed a megabyte at a time


def text_bytes(value) -> bytes:
    return value.encode("utf-8", "surrogatepass")


def hash_file(line, path: str, algorithms, fn: str) -> dict:
    """Hash a file in chunks with several algorithms at once (one read)."""
    check_type(line, path, "text", f"hash.{fn}", "path")
    if path == "":
        fail(line, f"hash.{fn}() needs a path, but got empty text.")
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        fail(line, f"'{path}' is a folder, not a file.", path=path)
    hashers = {name: hashlib.new(name) for name in algorithms}
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                for h in hashers.values():
                    h.update(chunk)
    except OSError as err:
        fail_os(line, err, path, "read")
    return {name: h.hexdigest() for name, h in hashers.items()}


def load(api):
    m = Module(
        "hash",
        "Hashes (fingerprints) of text and files: MD5, SHA-1, SHA-256 and SHA-512, "
        "HMAC signatures, and a safe way to compare secrets. Hashes come back as "
        "lowercase hex text, the way VirusTotal and threat reports show them.",
    )

    def text_hash(name):
        def run(line, text):
            check_type(line, text, "text", f"hash.{name}", "text")
            return hashlib.new(name, text_bytes(text)).hexdigest()
        return run

    m.fn("md5", 1, doc="md5(text) -> text\nThe MD5 hash of the text (UTF-8), as 32 hex characters. "
                       "MD5 is broken for security, but threat feeds still use it to name files.")(text_hash("md5"))
    m.fn("sha1", 1, doc="sha1(text) -> text\nThe SHA-1 hash of the text (UTF-8), as 40 hex characters.")(text_hash("sha1"))
    m.fn("sha256", 1, doc="sha256(text) -> text\nThe SHA-256 hash of the text (UTF-8), as 64 hex characters. "
                          "This is the one to use today.")(text_hash("sha256"))
    m.fn("sha512", 1, doc="sha512(text) -> text\nThe SHA-512 hash of the text (UTF-8), as 128 hex characters.")(text_hash("sha512"))

    def file_hash(name):
        def run(line, path):
            return hash_file(line, path, [name], f"file_{name}")[name]
        return run

    m.fn("file_md5", 1, doc="file_md5(path) -> text\nThe MD5 hash of a file's bytes. Works on big files too (read a piece at a time).")(file_hash("md5"))
    m.fn("file_sha1", 1, doc="file_sha1(path) -> text\nThe SHA-1 hash of a file's bytes.")(file_hash("sha1"))
    m.fn("file_sha256", 1, doc="file_sha256(path) -> text\nThe SHA-256 hash of a file's bytes: search for it on VirusTotal to see if the file is known malware.")(file_hash("sha256"))
    m.fn("file_sha512", 1, doc="file_sha512(path) -> text\nThe SHA-512 hash of a file's bytes.")(file_hash("sha512"))

    @m.fn("file", 1, doc="file(path) -> map\nEvery common hash of a file in one read: {\"md5\", \"sha1\", \"sha256\", \"size\"}.")
    def file_all(line, path):
        result = hash_file(line, path, ["md5", "sha1", "sha256"], "file")
        try:
            result["size"] = os.path.getsize(os.path.expanduser(path))
        except OSError as err:
            fail_os(line, err, path, "look at")
        return result

    @m.fn("hmac_sha256", 2, doc="hmac_sha256(key, text) -> text\nA signature of text made with a secret key (HMAC-SHA256), as hex. "
                                "Webhooks use this to prove a message really came from the sender.")
    def hmac_sha256(line, key, text):
        check_type(line, key, "text", "hash.hmac_sha256", "key")
        check_type(line, text, "text", "hash.hmac_sha256", "text")
        return _hmac.new(text_bytes(key), text_bytes(text), hashlib.sha256).hexdigest()

    @m.fn("compare", 2, doc="compare(a, b) -> bool\nTrue if two texts are the same, checked in constant time, so an attacker "
                            "can't learn a secret by timing the answer. Use it for passwords, tokens and signatures.")
    def compare(line, a, b):
        check_type(line, a, "text", "hash.compare", "a")
        check_type(line, b, "text", "hash.compare", "b")
        return _hmac.compare_digest(text_bytes(a), text_bytes(b))

    return m
