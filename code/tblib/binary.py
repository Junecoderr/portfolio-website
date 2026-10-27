"""binary: build, take apart and look inside bytes."""

from __future__ import annotations

import math
import re
import struct

from tblib._api import MANY, Module, check_type, fail_parse, fail_type, fail_value

import tb as _tb

# The struct format letters TB supports, and how many bytes each takes.
_SIZES = {"B": 1, "b": 1, "H": 2, "h": 2, "I": 4, "i": 4, "Q": 8, "q": 8,
          "f": 4, "d": 8, "s": 1, "x": 1}
_FORMAT_RE = re.compile(r"\s*(\d*)\s*([BbHhIiQqfdsx])")
_ORDERS = {"<": "<", ">": ">", "!": ">", "=": "<"}

_INT_RANGES = {
    "B": (0, 0xFF), "b": (-0x80, 0x7F), "H": (0, 0xFFFF), "h": (-0x8000, 0x7FFF),
    "I": (0, 0xFFFFFFFF), "i": (-0x80000000, 0x7FFFFFFF),
    "Q": (0, 0xFFFFFFFFFFFFFFFF), "q": (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
}
_KIND_WORDS = {
    "B": "an unsigned byte", "b": "a signed byte", "H": "an unsigned 16-bit number",
    "h": "a signed 16-bit number", "I": "an unsigned 32-bit number",
    "i": "a signed 32-bit number", "Q": "an unsigned 64-bit number",
    "q": "a signed 64-bit number",
}


def parse_format(line, fmt: str, fn: str):
    """'<HI4s' -> ('<', [(count, letter), ...]). Friendly errors for anything else."""
    check_type(line, fmt, "text", f"binary.{fn}", "format")
    order = "<"
    rest = fmt
    if rest[:1] in _ORDERS:
        order, rest = _ORDERS[rest[0]], rest[1:]
    items = []
    pos = 0
    while pos < len(rest):
        if rest[pos].isspace():
            pos += 1
            continue
        m = _FORMAT_RE.match(rest, pos)
        if not m:
            fail_value(
                line,
                f"binary.{fn}() doesn't understand '{rest[pos]}' in the format \"{fmt}\". "
                "Use < (little-endian) or > (big-endian) first, then letters: "
                "B b (8-bit), H h (16-bit), I i (32-bit), Q q (64-bit), f d (decimals), "
                "s (bytes, like 4s), x (skip a byte). Capital = unsigned.",
            )
        items.append((int(m.group(1)) if m.group(1) else 1, m.group(2)))
        pos = m.end()
    return order, items


def format_size(items) -> int:
    return sum(count * _SIZES[letter] for count, letter in items)


def to_bytes(line, value, fn: str, arg: str = "data") -> bytes:
    if isinstance(value, bytes):
        return value
    fail_type(line, f"binary.{fn}() needs {arg} to be bytes, not {_tb.type_name(value)}. "
                    "Turn text into bytes with text.bytes().")


def hexdump(data: bytes, width: int = 16) -> str:
    rows = []
    for start in range(0, len(data), width):
        chunk = data[start:start + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        text_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        rows.append(f"{start:08x}  {hex_part:<{width * 3 - 1}}  |{text_part}|")
    return "\n".join(rows)


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    total = len(data)
    result = -sum(c / total * math.log2(c / total) for c in counts if c)
    return round(result + 0.0, 4)


def load(api):
    m = Module(
        "binary",
        "Work with bytes: make them from hex or lists, pack and unpack numbers the way "
        "file formats and network protocols store them (like Python's struct), read "
        "little- and big-endian numbers at an offset, show a hex dump, measure entropy, "
        "and sniff what kind of file something is. Write bytes as b\"...\" in TB.",
    )

    @m.fn("from_hex", 1, doc="from_hex(text) -> bytes\nBytes from hex text: from_hex(\"4d5a90\") is b\"MZ\\x90\". Spaces and colons are ignored.")
    def from_hex(line, text):
        check_type(line, text, "text", "binary.from_hex", "text")
        clean = re.sub(r"[\s:]", "", text)
        if clean.lower().startswith("0x"):
            clean = clean[2:]
        try:
            return bytes.fromhex(clean)
        except ValueError:
            fail_parse(line, f"binary.from_hex() needs hex text (pairs of 0-9 and a-f), not \"{text[:40]}\".")

    @m.fn("to_hex", 1, 2, doc="to_hex(data, separator?) -> text\nThe bytes as hex text: to_hex(b\"MZ\") is \"4d5a\"; to_hex(b\"MZ\", \" \") is \"4d 5a\".")
    def to_hex(line, data, sep=""):
        data = to_bytes(line, data, "to_hex")
        check_type(line, sep, "text", "binary.to_hex", "separator")
        return data.hex(sep) if sep else data.hex()

    @m.fn("from_list", 1, doc="from_list(numbers) -> bytes\nBytes from a list of numbers 0 to 255: from_list([77, 90]) is b\"MZ\".")
    def from_list(line, items):
        check_type(line, items, "list", "binary.from_list", "numbers")
        for i, x in enumerate(items):
            if not (_tb.is_whole(x) and 0 <= x <= 255):
                fail_value(line, f"binary.from_list() needs whole numbers from 0 to 255, but item {i} is {_tb.to_text(x, nested=True)}.")
        return bytes(int(x) for x in items)

    @m.fn("from_text", 1, 2, doc="from_text(text, encoding?) -> bytes\nThe text as bytes (UTF-8 unless you name another encoding). Same as text.bytes().")
    def from_text(line, text, encoding="utf-8"):
        check_type(line, text, "text", "binary.from_text", "text")
        check_type(line, encoding, "text", "binary.from_text", "encoding")
        try:
            return text.encode(encoding)
        except LookupError:
            fail_value(line, f"binary.from_text() doesn't know the encoding '{encoding}'.")
        except UnicodeEncodeError:
            fail_value(line, f"Some of the text can't be written in {encoding}.")

    @m.fn("zeros", 1, doc="zeros(n) -> bytes\nn zero bytes.")
    def zeros(line, n):
        n = check_type(line, n, "whole", "binary.zeros", "n")
        if n < 0 or n > 100_000_000:
            fail_value(line, "binary.zeros() needs a size from 0 to 100,000,000.")
        return bytes(n)

    @m.fn("concat", 0, MANY, doc="concat(data, ...) -> bytes\nJoin bytes (or a list of bytes) into one.")
    def concat(line, *parts):
        if len(parts) == 1 and isinstance(parts[0], list):
            parts = tuple(parts[0])
        return b"".join(to_bytes(line, p, "concat", "each part") for p in parts)

    @m.fn("size", 1, doc="size(format) -> number\nHow many bytes pack(format, ...) makes: size(\"<HI\") is 6.")
    def size(line, fmt):
        _, items = parse_format(line, fmt, "size")
        return format_size(items)

    @m.fn("pack", 1, MANY, doc=(
        "pack(format, value, ...) -> bytes\n"
        "Numbers (and bytes) packed the way file formats store them.\n"
        "pack(\"<HI\", 1, 2) is 6 bytes, little-endian.\n"
        "\n"
        "Format letters:\n"
        "- <: little-endian (the default).\n"
        "- > or !: big-endian.\n"
        "- B b H h I i Q q: whole numbers. A capital letter means unsigned.\n"
        "  - B b: 8-bit\n"
        "  - H h: 16-bit\n"
        "  - I i: 32-bit\n"
        "  - Q q: 64-bit\n"
        "- f d: 32-bit and 64-bit decimals.\n"
        "- Ns: N bytes, like 4s.\n"
        "- x: a zero byte."))
    def pack(line, fmt, *values):
        order, items = parse_format(line, fmt, "pack")
        out = []
        vi = 0
        wanted = sum(0 if letter == "x" else (1 if letter == "s" else count) for count, letter in items)
        if len(values) != wanted:
            fail_value(line, f"binary.pack(\"{fmt}\") needs {wanted} value{'s' if wanted != 1 else ''}, but got {len(values)}.")
        for count, letter in items:
            if letter == "x":
                out.append(bytes(count))
                continue
            if letter == "s":
                value = values[vi]
                vi += 1
                if isinstance(value, str):
                    value = value.encode("utf-8")
                value = to_bytes(line, value, "pack", f"value {vi}")
                out.append(value[:count].ljust(count, b"\0"))
                continue
            for _ in range(count):
                value = values[vi]
                vi += 1
                if letter in "fd":
                    if not _tb.is_number(value):
                        fail_type(line, f"binary.pack() needs a number for '{letter}', not {_tb.type_name(value)}.")
                    out.append(struct.pack(order + letter, float(value)))
                    continue
                if not _tb.is_whole(value):
                    fail_type(line, f"binary.pack() needs a whole number for '{letter}', not {_tb.to_text(value, nested=True)}.")
                lo, hi = _INT_RANGES[letter]
                if not lo <= value <= hi:
                    fail_value(line, f"binary.pack(): {_tb.to_text(value)} doesn't fit in {_KIND_WORDS[letter]} ('{letter}' holds {lo} to {hi}).")
                out.append(struct.pack(order + letter, int(value)))
        return b"".join(out)

    @m.fn("unpack", 2, 3, doc="unpack(format, data, offset?) -> list\nRead numbers out of bytes, starting at offset (default 0): unpack(\"<HI\", data) gives [first, second]. Same format letters as pack().")
    def unpack(line, fmt, data, offset=0):
        order, items = parse_format(line, fmt, "unpack")
        data = to_bytes(line, data, "unpack")
        offset = check_type(line, offset, "whole", "binary.unpack", "offset")
        if offset < 0:
            offset += len(data)
        need = format_size(items)
        if offset < 0 or offset + need > len(data):
            fail_value(line, f"binary.unpack(\"{fmt}\") needs {need} bytes at offset {offset}, "
                             f"but the data is only {len(data)} bytes long.", reason="too_short")
        result = []
        pos = offset
        for count, letter in items:
            if letter == "x":
                pos += count
                continue
            if letter == "s":
                result.append(bytes(data[pos:pos + count]))
                pos += count
                continue
            for _ in range(count):
                (value,) = struct.unpack_from(order + letter, data, pos)
                pos += _SIZES[letter]
                result.append(_tb.tidy_number(value) if isinstance(value, float) else value)
        return result

    def reader(name, letter, order):
        size_ = _SIZES[letter]
        kind = "little-endian" if order == "<" else "big-endian"

        def read(line, data, offset=0):
            data = to_bytes(line, data, name)
            offset = check_type(line, offset, "whole", f"binary.{name}", "offset")
            if offset < 0 or offset + size_ > len(data):
                fail_value(line, f"binary.{name}() needs {size_} bytes at offset {offset}, "
                                 f"but the data is only {len(data)} bytes long.", reason="too_short")
            return struct.unpack_from(order + letter, data, offset)[0]

        bits = size_ * 8
        sign = "unsigned" if letter.isupper() else "signed"
        m.fn(name, 1, 2, doc=f"{name}(data, offset?) -> number\nThe {sign} {bits}-bit {kind} number at offset (default 0).")(read)

    for bits, letter in ((8, "B"), (16, "H"), (32, "I"), (64, "Q")):
        if bits == 8:
            reader("read_u8", "B", "<")
            reader("read_i8", "b", "<")
            continue
        for suffix, order in (("le", "<"), ("be", ">")):
            reader(f"read_u{bits}{suffix}", letter, order)
            reader(f"read_i{bits}{suffix}", letter.lower(), order)

    @m.fn("hexdump", 1, 2, doc="hexdump(data, width?) -> text\nA classic hex dump: offset, hex bytes and the printable characters, width bytes per row (default 16).")
    def hexdump_(line, data, width=16):
        data = to_bytes(line, data, "hexdump")
        width = check_type(line, width, "whole", "binary.hexdump", "width")
        if not 1 <= width <= 256:
            fail_value(line, "binary.hexdump() needs a width from 1 to 256.")
        return hexdump(data, width)

    @m.fn("entropy", 1, doc="entropy(data) -> number\nShannon entropy from 0 (all the same byte) to 8 (random). Packed or encrypted data is usually above 7.2.")
    def entropy_(line, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return _tb.tidy_number(entropy(to_bytes(line, data, "entropy")))

    @m.fn("xor", 2, doc="xor(data, key) -> bytes\nEach byte XORed with the key (bytes, repeated; or one number 0-255). XOR twice to get the original back.")
    def xor(line, data, key):
        data = to_bytes(line, data, "xor")
        if _tb.is_whole(key):
            if not 0 <= key <= 255:
                fail_value(line, "binary.xor() needs a key from 0 to 255 (or bytes).")
            key = bytes([int(key)])
        key = to_bytes(line, key, "xor", "key")
        if not key:
            fail_value(line, "binary.xor() needs a key that isn't empty.")
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

    @m.fn("file_type", 1, doc="file_type(path) -> map\nWhat kind of file this is, from its first bytes (not its name): {type, description, mime, extension, path, size}. Knows PE, ELF, Mach-O, PDF, ZIP, PNG, JPEG, GIF, gzip and more.")
    def file_type(line, path):
        from tblib import _magic

        return _magic.file_type(line, path)

    @m.fn("sniff", 1, doc="sniff(data) -> map\nLike file_type(), but for bytes you already have: {type, description, mime, extension}.")
    def sniff(line, data):
        from tblib import _magic

        return _magic.sniff(to_bytes(line, data, "sniff"))

    return m
