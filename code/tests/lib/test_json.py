import pytest

from conftest import run_tb, tb_error


def test_parse_values():
    src = 'import json\nlet d = json.parse("{\\"a\\": [1, 2.5, 3.0, null, true, \\"x\\"], \\"b\\": {\\"c\\": {}}}")\nsay(d, d.a[2], type(d.b))'
    assert run_tb(src) == ['{"a": [1, 2.5, 3, nil, true, "x"], "b": {"c": {}}} 3 map']


def test_stringify_compact_and_pretty():
    src = 'import json\nlet v = {"name": "Alex", "tags": ["a", "é"], "n": nil, "ok": true, 1: 2}\nsay(json.stringify(v))\nsay(json.stringify([1, {"a": 1}], true))'
    assert run_tb(src) == [
        '{"name": "Alex", "tags": ["a", "é"], "n": null, "ok": true, "1": 2}',
        '[\n  1,\n  {\n    "a": 1\n  }\n]',
    ]


def test_round_trip_through_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = 'import json\nlet v = {"alerts": [{"ip": "10.0.0.1", "count": 3}]}\njson.write("out.json", v)\nsay(json.read("out.json") == v)\njson.write("c.json", [1], false)\nsay(json.read("c.json"))'
    assert run_tb(src) == ["true", "[1]"]
    assert (tmp_path / "out.json").read_text().startswith('{\n  "alerts"')
    assert (tmp_path / "c.json").read_text() == "[1]\n"


@pytest.mark.parametrize("text,message", [
    ("{bad", "That text isn't valid JSON: Expecting property name enclosed in double quotes (line 1, column 2 of the JSON)."),
    ("", "isn't valid JSON: Expecting value"),
    ("[1,]", "isn't valid JSON"),
    ("NaN", "NaN is not allowed"),
    ("[1] x", "Extra data"),
])
def test_parse_errors(text, message):
    escaped = text.replace('"', '\\"')
    assert message in str(tb_error(f'import json\njson.parse("{escaped}")'))


def test_parse_error_map_has_json_position():
    assert run_tb('import json\ntry { json.parse("[1,\\n2,,]") } catch e { say(e.json_line, e.json_column, e.line) }') == ["2 3 2"]


def test_stringify_errors():
    assert "JSON can't hold a function" in str(tb_error("import json\njson.stringify({\"f\": say})"))
    assert "nested too deeply" in str(tb_error("import json\nlet a = []\npush(a, a)\njson.stringify(a)"))
    assert "JSON can't hold a library" in str(tb_error("import json\njson.stringify(json)"))


def test_read_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.json").write_text("{oops")
    assert "I couldn't find 'nope.json'" in str(tb_error('import json\njson.read("nope.json")'))
    assert "The file 'bad.json' isn't valid JSON" in str(tb_error('import json\njson.read("bad.json")'))
    assert "json.parse() needs text to be text, not a number." in str(tb_error("import json\njson.parse(1)"))
