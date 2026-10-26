"""toml: read and write TOML config files."""

from __future__ import annotations

import datetime as _dt
import os
import re

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_TEXT = 50_000_000

try:  # Python 3.11+
    import tomllib as _tomllib
except ImportError:  # pragma: no cover - exercised on 3.10 and by the fallback tests
    _tomllib = None


class TomlProblem(Exception):
    def __init__(self, message, line=None):
        super().__init__(message)
        self.message, self.line = message, line


# =============================================================================
# Fallback parser (3.10 has no tomllib). Covers the TOML that config files
# use: tables, arrays of tables, dotted keys, strings (all four kinds),
# numbers, booleans, dates, arrays and inline tables.
# =============================================================================

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
NUMBER = re.compile(
    r"[+-]?(?:0x[0-9A-Fa-f_]+|0o[0-7_]+|0b[01_]+|inf|nan|"
    r"(?:\d[\d_]*)(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?)")
DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?)?|\d{2}:\d{2}:\d{2}(?:\.\d+)?")
ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.root: dict = {}
        self.current = self.root
        self.explicit: set = set()      # table paths defined with [a.b]
        self.array_tables: set = set()  # paths defined with [[a.b]]

    # -- helpers ----------------------------------------------------------------

    def line_no(self) -> int:
        return self.text.count("\n", 0, self.i) + 1

    def error(self, message):
        raise TomlProblem(message, self.line_no())

    def peek(self, n=1) -> str:
        return self.text[self.i:self.i + n]

    def skip_ws(self):
        while self.peek() in (" ", "\t") and self.peek():
            self.i += 1

    def skip_ws_and_newlines(self):
        while True:
            self.skip_ws()
            c = self.peek()
            if c == "#":
                self.skip_comment()
            elif c in ("\n", "\r") and c:
                self.i += 1
            else:
                return

    def skip_comment(self):
        while self.peek() and self.peek() not in ("\n", "\r"):
            self.i += 1

    def end_of_line(self):
        self.skip_ws()
        if self.peek() == "#":
            self.skip_comment()
        c = self.peek()
        if c == "" or c == "\n":
            self.i += 1 if c else 0
            return
        if self.peek(2) == "\r\n":
            self.i += 2
            return
        self.error(f"I expected the end of the line here, but found {c!r}")

    # -- document ---------------------------------------------------------------

    def parse(self) -> dict:
        while True:
            self.skip_ws_and_newlines()
            if not self.peek():
                return self.root
            if self.peek(2) == "[[":
                self.i += 2
                path = self.key_path("]]")
                self.open_array_table(path)
            elif self.peek() == "[":
                self.i += 1
                path = self.key_path("]")
                self.open_table(path)
            else:
                path = self.key_path("=")
                self.skip_ws()
                value = self.value()
                self.assign(self.current, path, value)
            self.end_of_line()

    def key_path(self, closer: str) -> list:
        parts = []
        while True:
            self.skip_ws()
            c = self.peek()
            if c == '"':
                parts.append(self.basic_string())
            elif c == "'":
                parts.append(self.literal_string())
            else:
                m = BARE_KEY.match(self.text, self.i)
                if not m:
                    self.error("I expected a key name here")
                parts.append(m.group(0))
                self.i = m.end()
            self.skip_ws()
            if self.peek() == ".":
                self.i += 1
                continue
            if closer == "]]" and self.peek(2) == "]]":
                self.i += 2
                return parts
            if closer != "]]" and self.peek() == closer:
                self.i += 1
                return parts
            self.error(f"I expected '{closer}' after the key" if closer != "=" else "I expected '=' after the key")

    def descend(self, table: dict, path: list, for_table=False) -> dict:
        for part in path:
            node = table.get(part)
            if node is None:
                node = table[part] = {}
            elif isinstance(node, list) and node and isinstance(node[-1], dict):
                node = node[-1]
            elif not isinstance(node, dict):
                self.error(f"'{part}' is already a value, so it can't hold more keys")
            table = node
        return table

    def open_table(self, path: list):
        key = tuple(path)
        if key in self.explicit or key in self.array_tables:
            self.error(f"The table [{'.'.join(path)}] is defined twice")
        table = self.descend(self.root, path[:-1], True)
        node = table.get(path[-1])
        if node is None:
            node = table[path[-1]] = {}
        elif not isinstance(node, dict):
            self.error(f"'{path[-1]}' is already a value, so it can't be a table")
        self.explicit.add(key)
        self.current = node

    def open_array_table(self, path: list):
        key = tuple(path)
        if key in self.explicit:
            self.error(f"[{'.'.join(path)}] is a table; it can't also be an array of tables")
        table = self.descend(self.root, path[:-1], True)
        node = table.get(path[-1])
        if node is None:
            node = table[path[-1]] = []
        elif not isinstance(node, list) or (node and not isinstance(node[-1], dict)):
            self.error(f"'{path[-1]}' is already a value, so it can't be an array of tables")
        fresh: dict = {}
        node.append(fresh)
        self.array_tables.add(key)
        self.current = fresh

    def assign(self, table: dict, path: list, value):
        for part in path[:-1]:
            node = table.get(part)
            if node is None:
                node = table[part] = {}
            elif not isinstance(node, dict):
                self.error(f"'{part}' is already a value, so it can't hold more keys")
            table = node
        if path[-1] in table:
            self.error(f"The key '{path[-1]}' is set twice")
        table[path[-1]] = value

    # -- values -----------------------------------------------------------------

    def value(self):
        c = self.peek()
        if c == '"':
            if self.peek(3) == '"""':
                return self.multiline_basic()
            return self.basic_string()
        if c == "'":
            if self.peek(3) == "'''":
                return self.multiline_literal()
            return self.literal_string()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        if self.text.startswith("true", self.i):
            self.i += 4
            return True
        if self.text.startswith("false", self.i):
            self.i += 5
            return False
        m = DATE_TIME.match(self.text, self.i)
        if m and (len(m.group(0)) > 10 or self.text[self.i + 4:self.i + 5] == "-"):
            self.i = m.end()
            return m.group(0).replace("t", "T").replace("z", "Z")
        m = NUMBER.match(self.text, self.i)
        if m and m.group(0) not in ("+", "-"):
            self.i = m.end()
            return self.number(m.group(0))
        if not c:
            self.error("I expected a value here, but the text ended")
        self.error(f"I expected a value here, but found {c!r}")

    def number(self, raw: str):
        text = raw.replace("_", "")
        low = text.lower().lstrip("+-")
        sign = -1 if text.startswith("-") else 1
        try:
            if low in ("inf", "nan"):
                return float(text) if low == "inf" else float("nan")
            if low.startswith("0x"):
                return sign * int(low[2:], 16)
            if low.startswith("0o"):
                return sign * int(low[2:], 8)
            if low.startswith("0b"):
                return sign * int(low[2:], 2)
            if any(ch in low for ch in ".e"):
                return float(text)
            return int(text)
        except ValueError:
            self.error(f"I couldn't read the number {raw}")

    def basic_string(self) -> str:
        self.i += 1
        out = []
        while True:
            c = self.peek()
            if not c or c == "\n":
                self.error("A string is missing its closing quote")
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\":
                out.append(self.escape())
                continue
            out.append(c)
            self.i += 1

    def escape(self) -> str:
        self.i += 1
        c = self.peek()
        if c in ESCAPES:
            self.i += 1
            return ESCAPES[c]
        if c in ("u", "U"):
            size = 4 if c == "u" else 8
            digits = self.text[self.i + 1:self.i + 1 + size]
            if len(digits) != size or not all(d in "0123456789abcdefABCDEF" for d in digits):
                self.error("A \\u escape needs hex digits")
            self.i += 1 + size
            try:
                return chr(int(digits, 16))
            except (ValueError, OverflowError):
                self.error("That \\u escape isn't a valid character")
        self.error(f"I don't know the escape \\{c}")

    def literal_string(self) -> str:
        self.i += 1
        end = self.text.find("'", self.i)
        nl = self.text.find("\n", self.i)
        if end == -1 or (nl != -1 and nl < end):
            self.error("A string is missing its closing quote")
        s = self.text[self.i:end]
        self.i = end + 1
        return s

    def multiline_basic(self) -> str:
        self.i += 3
        if self.peek() == "\n":
            self.i += 1
        elif self.peek(2) == "\r\n":
            self.i += 2
        out = []
        while True:
            if not self.peek():
                self.error("A multi-line string is missing its closing quotes")
            if self.text.startswith('"""', self.i):
                extra = 0
                while self.text.startswith('"', self.i + 3 + extra) and extra < 2:
                    extra += 1
                out.append('"' * extra)
                self.i += 3 + extra
                return "".join(out)
            c = self.peek()
            if c == "\\":
                nxt = self.text[self.i + 1:self.i + 2]
                if nxt in ("\n", "\r", " ", "\t"):
                    j = self.i + 1
                    while j < len(self.text) and self.text[j] in " \t\r\n":
                        j += 1
                    if "\n" in self.text[self.i:j]:
                        self.i = j
                        continue
                out.append(self.escape())
                continue
            out.append(c)
            self.i += 1

    def multiline_literal(self) -> str:
        self.i += 3
        if self.peek() == "\n":
            self.i += 1
        elif self.peek(2) == "\r\n":
            self.i += 2
        end = self.text.find("'''", self.i)
        if end == -1:
            self.error("A multi-line string is missing its closing quotes")
        extra = 0
        while self.text.startswith("'", end + 3 + extra) and extra < 2:
            extra += 1
        s = self.text[self.i:end] + "'" * extra
        self.i = end + 3 + extra
        return s

    def array(self) -> list:
        self.i += 1
        items = []
        while True:
            self.skip_ws_and_newlines()
            if self.peek() == "]":
                self.i += 1
                return items
            if not self.peek():
                self.error("An array is missing its closing ']'")
            items.append(self.value())
            self.skip_ws_and_newlines()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                self.error("I expected ',' or ']' in the array")

    def inline_table(self) -> dict:
        self.i += 1
        table: dict = {}
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return table
        while True:
            self.skip_ws()
            path = self.key_path("=")
            self.skip_ws()
            value = self.value()
            self.assign(table, path, value)
            self.skip_ws()
            if self.peek() == ",":
                self.i += 1
                continue
            if self.peek() == "}":
                self.i += 1
                return table
            self.error("I expected ',' or '}' in the inline table")


def fallback_parse(text: str) -> dict:
    """Parse TOML without tomllib. Raises TomlProblem."""
    return _Parser(text).parse()


# =============================================================================
# Shared
# =============================================================================


def to_tb(value):
    """tomllib/fallback output as TB values (dates become ISO text)."""
    if isinstance(value, dict):
        return {str(k): to_tb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_tb(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, float):
        return _tb.tidy_number(value) if value == value and value not in (float("inf"), float("-inf")) else value
    return value


def parse_toml(text: str, line, where="toml.parse") -> dict:
    """A TB map from TOML text, with friendly errors."""
    if len(text) > MAX_TEXT:
        fail(line, f"{where}() got {len(text):,} characters; it reads at most {MAX_TEXT:,}.")
    text = text.lstrip("﻿")
    if _tomllib is not None:
        try:
            return to_tb(_tomllib.loads(text))
        except _tomllib.TOMLDecodeError as err:
            msg = str(err)
            m = re.search(r"\(at line (\d+), column (\d+)\)", msg)
            where_text = f" (line {m.group(1)}, column {m.group(2)})" if m else ""
            msg = re.sub(r"\s*\(at line \d+, column \d+\)", "", msg).rstrip(".")
            fail(line, f"That isn't valid TOML{where_text}: {msg}.", reason="parse")
        except (ValueError, RecursionError) as err:
            fail(line, f"That isn't valid TOML: {err}.", reason="parse")
    try:
        return to_tb(fallback_parse(text))
    except TomlProblem as err:
        where_text = f" (line {err.line})" if err.line else ""
        fail(line, f"That isn't valid TOML{where_text}: {err.message}.", reason="parse")
    except RecursionError:
        fail(line, "That TOML is nested too deeply.", reason="parse")


# -- writing -------------------------------------------------------------------


def _key(name: str) -> str:
    return name if BARE_KEY.fullmatch(name) else _quote(name)


def _quote(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _scalar(value, line, path) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "inf" if value > 0 else "-inf"
        return _tb.to_text(value) if not value.is_integer() else f"{value:.1f}"
    if isinstance(value, str):
        return _quote(value)
    if value is None:
        fail(line, f"toml.stringify() can't write nil (at {path}); TOML has no nil. Leave the key out instead.")
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v, line, f"{path}[{i}]") for i, v in enumerate(value)) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_key(str(k))} = {_scalar(v, line, f'{path}.{k}')}" for k, v in value.items()) + "}"
    fail(line, f"toml.stringify() can't write {_tb.type_name(value)} values (at {path}).")


def _is_table_array(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def stringify_toml(data: dict, line) -> str:
    out: list[str] = []

    def write_table(table: dict, prefix: list):
        plain = [(k, v) for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)]
        subs = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        arrays = [(k, v) for k, v in table.items() if _is_table_array(v)]
        for k, v in plain:
            out.append(f"{_key(str(k))} = {_scalar(v, line, '.'.join(prefix + [str(k)]) or str(k))}")
        for k, v in subs:
            path = prefix + [str(k)]
            if out and out[-1] != "":
                out.append("")
            out.append("[" + ".".join(_key(p) for p in path) + "]")
            write_table(v, path)
        for k, items in arrays:
            path = prefix + [str(k)]
            for item in items:
                if out and out[-1] != "":
                    out.append("")
                out.append("[[" + ".".join(_key(p) for p in path) + "]]")
                write_table(item, path)

    write_table(data, [])
    return "\n".join(out).strip("\n") + "\n" if out else ""


def load(api):
    m = Module(
        "toml",
        "Read and write TOML, the config-file format used by tb.toml and pyproject.toml: toml.parse(text) "
        "gives a map (tables become maps, dates become ISO text), toml.stringify(map) writes one back, "
        "toml.read/write work on files.",
    )

    @m.fn("parse", 1, doc=(
        "parse(text) -> map\n"
        "Read TOML text: tables [a.b] become nested maps, [[items]] become lists of maps, dates and times "
        "become ISO text like \"2026-10-26T09:00:00\"."))
    def parse(line, text):
        check_type(line, text, "text", "toml.parse", "text")
        return parse_toml(text, line)

    @m.fn("stringify", 1, doc=(
        "stringify(map) -> text\n"
        "Write a map as TOML: plain values first, then [tables] for nested maps and [[tables]] for lists "
        "of maps. nil can't be written (TOML has no nil), so leave such keys out."))
    def stringify(line, data):
        check_type(line, data, "map", "toml.stringify", "map")
        return stringify_toml(data, line)

    @m.fn("read", 1, doc="read(path) -> map\nRead a TOML file (see parse).")
    def read(line, path):
        check_type(line, path, "text", "toml.read", "path")
        if not path.strip():
            fail(line, "toml.read() needs a path, but got empty text.")
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        try:
            from tblib.fs import read_text
            text = read_text(path)
        except OSError as err:
            fail_os(line, err, path, "read")
        return parse_toml(text, line, "toml.read")

    @m.fn("write", 2, doc="write(path, map)\nSave a map as a TOML file (see stringify).")
    def write(line, path, data):
        check_type(line, path, "text", "toml.write", "path")
        check_type(line, data, "map", "toml.write", "map")
        if not path.strip():
            fail(line, "toml.write() needs a path, but got empty text.")
        text = stringify_toml(data, line)
        try:
            with open(os.path.expanduser(path), "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    m.value("native", _tomllib is not None,
            doc="True when Python's own tomllib does the parsing (3.11+); false means TB's small built-in parser.")
    return m
