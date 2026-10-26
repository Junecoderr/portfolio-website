"""json: read and write JSON."""

from __future__ import annotations

import json as _json
import math

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_DEPTH = 500


def to_plain(value, line, path="the value", depth=0):
    """Check that a TB value can become JSON (and copy it). Raises a friendly
    TBError naming the part that can't. Other tblib modules may reuse this."""
    if depth > MAX_DEPTH:
        fail(line, "That value is nested too deeply (or contains itself) to turn into JSON.", cls="ValueError")
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            fail(line, f"JSON can't hold {value} in {path}.", cls="ValueError")
        return value
    if isinstance(value, list):
        return [to_plain(v, line, path, depth + 1) for v in value]
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else _tb.to_text(k)): to_plain(v, line, path, depth + 1)
                for k, v in value.items()}
    fail(line, f"JSON can't hold a {_tb.type_name(value)} (found in {path}).", cls="TypeError")


def from_json(value):
    """JSON numbers like 2.0 become 2, so TB shows them the way they look."""
    if isinstance(value, float):
        return _tb.tidy_number(value)
    if isinstance(value, list):
        return [from_json(v) for v in value]
    if isinstance(value, dict):
        return {k: from_json(v) for k, v in value.items()}
    return value


def parse_text(text: str, line, where: str = "That text"):
    try:
        return from_json(_json.loads(text, parse_constant=_reject_constant))
    except _json.JSONDecodeError as err:
        fail(line, f"{where} isn't valid JSON: {err.msg} (line {err.lineno}, column {err.colno} of the JSON).",
             json_line=err.lineno, json_column=err.colno, cls="ParseError")
    except ValueError as err:
        fail(line, f"{where} isn't valid JSON: {err}.", cls="ParseError")
    except RecursionError:
        fail(line, f"{where} is nested too deeply to read.", cls="ParseError")


def _reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def dump(value, line, pretty=False) -> str:
    plain = to_plain(value, line)
    if pretty:
        return _json.dumps(plain, indent=2, ensure_ascii=False)
    return _json.dumps(plain, ensure_ascii=False, separators=(", ", ": "))


def load(api):
    m = Module("json", "Read and write JSON, the data format of the web and of most security tools.")

    @m.fn("parse", 1, doc="parse(text) -> value\nTurn JSON text into TB values (objects become maps, arrays become lists).")
    def parse(line, text):
        check_type(line, text, "text", "json.parse", "text")
        return parse_text(text, line)

    @m.fn("stringify", 1, 2, doc="stringify(value, pretty?) -> text\nTurn a TB value into JSON text. Pass true for pretty, indented output.")
    def stringify(line, value, pretty=False):
        return dump(value, line, _tb.truthy(pretty))

    @m.fn("read", 1, doc="read(path) -> value\nRead a .json file into TB values.")
    def read(line, path):
        check_type(line, path, "text", "json.read", "path")
        try:
            text = _tb.read_source(path)
        except OSError as err:
            fail_os(line, err, path, "read")
        return parse_text(text, line, f"The file '{path}'")

    @m.fn("write", 2, 3, doc="write(path, value, pretty?)\nSave a TB value as a .json file (pretty and indented unless pretty is false).")
    def write(line, path, value, pretty=True):
        check_type(line, path, "text", "json.write", "path")
        text = dump(value, line, _tb.truthy(pretty))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    return m
