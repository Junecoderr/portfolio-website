"""toml library: tomllib on 3.11+, the fallback parser, and writing."""

import json

import pytest

import tb
import tblib.toml as tomllib_
from conftest import run_tb, tb_error

SAMPLE = '''# app config
title = "TB app"
debug = true
port = 8_080
ratio = 0.25
when = 2026-10-26T09:00:00Z
day = 2026-10-26
tags = ["a", "b",
  "c"]  # trailing comment
empty = []
nested = [[1, 2], [3]]
inline = { x = 1, y = "two", z.w = true }
"quoted key" = 'literal \\n'
dotted.key.here = "deep"
multi = """
line one
line two\\
   joined"""
lit = \'\'\'
keep \\n as is\'\'\'
hex = 0xFF
neg = -3
big = 1e3
esc = "tab\\tquote\\"u\\u00e9"

[server]
host = "127.0.0.1"
ports = [80, 443]

[server.tls]
enabled = false

[[users]]
name = "Tanisha"
roles = ["admin"]

[[users]]
name = "Sam"

[users.prefs]
theme = "dark"
'''

EXPECTED = {
    "title": "TB app", "debug": True, "port": 8080, "ratio": 0.25,
    "when": "2026-10-26T09:00:00+00:00", "day": "2026-10-26",
    "tags": ["a", "b", "c"], "empty": [], "nested": [[1, 2], [3]],
    "inline": {"x": 1, "y": "two", "z": {"w": True}},
    "quoted key": "literal \\n", "dotted": {"key": {"here": "deep"}},
    "multi": "line one\nline twojoined",
    "lit": "keep \\n as is",
    "hex": 255, "neg": -3, "big": 1000, "esc": 'tab\tquote"ué',
    "server": {"host": "127.0.0.1", "ports": [80, 443], "tls": {"enabled": False}},
    "users": [{"name": "Tanisha", "roles": ["admin"]}, {"name": "Sam", "prefs": {"theme": "dark"}}],
}


def test_parse_with_whatever_parser_python_has():
    assert tomllib_.parse_toml(SAMPLE, 1) == EXPECTED


def test_fallback_parser_matches():
    out = tomllib_.to_tb(tomllib_.fallback_parse(SAMPLE))
    # tomllib gives "+00:00" for Z; the fallback keeps the text as written.
    out["when"] = out["when"].replace("Z", "+00:00")
    assert out == EXPECTED


def test_fallback_is_used_without_tomllib(monkeypatch):
    monkeypatch.setattr(tomllib_, "_tomllib", None)
    src = 'import toml\nlet c = toml.parse("a = 1\\n[t]\\nb = [1, 2]\\n")\nsay(c.a, c.t.b, toml.native)'
    assert run_tb(src) == ["1 [1, 2] false"]


@pytest.mark.parametrize("text,message", [
    ("a = ", "I expected a value here"),
    ("a = 1\na = 2", "set twice"),
    ("[t]\n[t]", "defined twice"),
    ('a = "open', "missing its closing quote"),
    ("a = [1, 2", "I expected ',' or ']'"),
    ("a = {x = 1", "I expected ',' or '}'"),
    ("a = 1 b = 2", "I expected the end of the line"),
    ("= 1", "I expected a key name"),
    ('a = "\\q"', "I don't know the escape"),
    ("x = 1\n[x]\n", "already a value"),
])
def test_fallback_errors(text, message):
    with pytest.raises(tomllib_.TomlProblem) as info:
        tomllib_.fallback_parse(text)
    assert message in info.value.message and info.value.line >= 1


def test_tb_parse_and_errors():
    src = 'import toml\nlet c = toml.parse(' + json.dumps(SAMPLE) + ')\nsay(c.title, c.port, c.server.tls.enabled, c.users[1].prefs.theme, c.inline.z.w, c.day)'
    assert run_tb(src) == ["TB app 8080 false dark true 2026-10-26"]
    err = tb_error('import toml\ntoml.parse("a = \\n")')
    assert "That isn't valid TOML (line 1" in str(err) and err.fields["reason"] == "parse"
    assert "toml.parse() needs text to be text" in str(tb_error("import toml\ntoml.parse(1)"))


def test_stringify_round_trip():
    src = '''import toml
let data = {"title": "x \\"y\\"", "n": 3, "f": 1.5, "ok": true, "list": [1, "two", [3]], "odd key": "v",
  "server": {"host": "h", "tls": {"on": false}}, "users": [{"name": "Tanisha"}, {"name": "Sam", "tags": []}],
  "inline_list_of_mixed": [{"a": 1}, 2]}
let text = toml.stringify(data)
say(text)
say(toml.parse(text) == data)
'''
    out = run_tb(src)
    assert out[-1] == "true"
    lines = out[0].split("\n")
    assert lines[0] == 'title = "x \\"y\\""'
    assert "[server.tls]" in lines and "[[users]]" in lines and '"odd key" = "v"' in lines
    assert 'inline_list_of_mixed = [{a = 1}, 2]' in lines


def test_stringify_rejects_nil_and_functions():
    assert "can't write nil (at a)" in str(tb_error('import toml\ntoml.stringify({"a": nil})'))
    assert "can't write function values (at t.f)" in str(tb_error('import toml\ntoml.stringify({"t": {"f": fn() {}}})'))
    assert "needs map to be a map" in str(tb_error('import toml\ntoml.stringify([1])'))


def test_read_write(tmp_path):
    path = str(tmp_path / "c.toml").replace("\\", "/")
    src = f'''import toml
toml.write("{path}", {{"name": "demo", "db": {{"path": "data.db"}}}})
let c = toml.read("{path}")
say(c.name, c.db.path)
'''
    assert run_tb(src) == ["demo data.db"]
    assert (tmp_path / "c.toml").read_text() == 'name = "demo"\n\n[db]\npath = "data.db"\n'
    assert "I couldn't find 'nope.toml'" in str(tb_error('import toml\ntoml.read("nope.toml")'))
    assert "is a folder" in str(tb_error('import toml\ntoml.read(".")'))


def test_docs():
    info = tb.describe_library("toml")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"]) and all(v["doc"] for v in info["values"])
