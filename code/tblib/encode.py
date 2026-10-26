"""encode: base64, hex, URL encoding, ROT13 and entropy."""

from __future__ import annotations

import base64 as _b64
import binascii
import codecs
import math
import re
import urllib.parse
from collections import Counter

from tblib._api import Module, check_type, fail

import tb as _tb

ENCODINGS = {"utf-8": "utf-8", "utf8": "utf-8", "utf-16": "utf-16-le", "utf-16le": "utf-16-le",
             "utf-16-le": "utf-16-le", "latin-1": "latin-1", "latin1": "latin-1", "ascii": "ascii"}


def looks_utf16le(data: bytes) -> bool:
    """PowerShell's -EncodedCommand is UTF-16LE: ASCII text with a zero byte
    after every character."""
    if len(data) < 4 or len(data) % 2:
        return False
    odd = data[1::2]
    return odd.count(0) >= 0.9 * len(odd)


def bytes_to_text(data: bytes, encoding=None) -> str:
    if encoding is None:
        if data.startswith(b"\xff\xfe"):
            return data[2:].decode("utf-16-le", "replace")
        if looks_utf16le(data):
            return data.decode("utf-16-le", "replace")
        return data.decode("utf-8", "replace")
    return data.decode(encoding, "replace")


def load(api):
    m = Module(
        "encode",
        "Encodings attackers (and the web) use: base64, hex, URL encoding and ROT13, "
        "plus entropy to spot random-looking text such as packed code or generated domains.",
    )

    def text_arg(line, value, fn, arg="text"):
        return check_type(line, value, "text", f"encode.{fn}", arg)

    def encoding_arg(line, encoding, fn):
        if encoding is None:
            return None
        check_type(line, encoding, "text", f"encode.{fn}", "encoding")
        found = ENCODINGS.get(encoding.lower())
        if found is None:
            fail(line, f"encode.{fn}() doesn't know the encoding \"{encoding}\". Try \"utf-8\", \"utf-16\" or \"latin-1\".", cls="ValueError")
        return found

    @m.fn("base64", 1, 2, doc="base64(text, encoding?) -> text\nThe text in base64. encoding is how the text becomes bytes first: "
                              "\"utf-8\" (default) or \"utf-16\" (what PowerShell's -EncodedCommand uses).")
    def base64(line, text, encoding=None):
        text_arg(line, text, "base64")
        enc = encoding_arg(line, encoding, "base64") or "utf-8"
        return _b64.b64encode(text.encode(enc, "surrogatepass")).decode("ascii")

    @m.fn("unbase64", 1, 2, doc="unbase64(text, encoding?) -> text\nDecode base64 (normal or URL-safe; missing = padding and spaces are fine). "
                                "Without encoding it guesses: UTF-16 for PowerShell -EncodedCommand payloads, otherwise UTF-8.")
    def unbase64(line, text, encoding=None):
        text_arg(line, text, "unbase64")
        enc = encoding_arg(line, encoding, "unbase64")
        cleaned = re.sub(r"\s+", "", text).replace("-", "+").replace("_", "/")
        if not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", cleaned):
            fail(line, "encode.unbase64() got text that isn't base64 (only A-Z, a-z, 0-9, +, / and = are allowed).", cls="ParseError")
        cleaned = cleaned.rstrip("=")
        if len(cleaned) % 4 == 1:
            fail(line, "encode.unbase64() got base64 with the wrong length; part of it may be missing.", cls="ParseError")
        try:
            data = _b64.b64decode(cleaned + "=" * (-len(cleaned) % 4))
        except (binascii.Error, ValueError) as err:
            fail(line, f"encode.unbase64() couldn't decode that: {err}.", cls="ParseError")
        return bytes_to_text(data, enc)

    @m.fn("hex", 1, doc="hex(text) -> text\nThe text's UTF-8 bytes as hex: hex(\"hi\") is \"6869\".")
    def hex_(line, text):
        return text_arg(line, text, "hex").encode("utf-8", "surrogatepass").hex()

    @m.fn("unhex", 1, doc="unhex(text) -> text\nTurn hex back into text. Spaces, colons, \\x and 0x prefixes are ignored: "
                          "unhex(\"68 69\") and unhex(\"\\\\x68\\\\x69\") are both \"hi\".")
    def unhex(line, text):
        text_arg(line, text, "unhex")
        cleaned = re.sub(r"0x|\\x|[\s:,-]", "", text, flags=re.IGNORECASE)
        if len(cleaned) % 2 or not re.fullmatch(r"[0-9A-Fa-f]*", cleaned):
            fail(line, "encode.unhex() got text that isn't hex (pairs of 0-9 and a-f).", cls="ParseError")
        return bytes_to_text(bytes.fromhex(cleaned))

    @m.fn("url", 1, doc="url(text) -> text\nURL-encode text so it is safe inside a link: url(\"a b&c\") is \"a%20b%26c\".")
    def url(line, text):
        return urllib.parse.quote(text_arg(line, text, "url"), safe="")

    @m.fn("unurl", 1, doc="unurl(text) -> text\nDecode URL encoding (%41 and + for space), like a web server does. "
                          "Run it twice on double-encoded attacks such as %252e%252e%252f.")
    def unurl(line, text):
        return urllib.parse.unquote_plus(text_arg(line, text, "unurl"))

    @m.fn("rot13", 1, doc="rot13(text) -> text\nShift every letter 13 places (doing it twice gives the text back). "
                          "Windows hides some registry values this way.")
    def rot13(line, text):
        return codecs.encode(text_arg(line, text, "rot13"), "rot13")

    @m.fn("entropy", 1, doc="entropy(text) -> number\nHow random the text looks, in bits per character (Shannon entropy): "
                            "0 for \"aaaa\", about 3-4 for English words, 4.5+ for random domains, keys and packed data.")
    def entropy(line, text):
        text_arg(line, text, "entropy")
        if not text:
            return 0
        total = len(text)
        bits = -sum((n / total) * math.log2(n / total) for n in Counter(text).values())
        return _tb.tidy_number(round(bits, 4)) if bits > 0 else 0

    return m
