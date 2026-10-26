"""yaml library: the YAML subset TB reads (block and flow collections, anchors, aliases, merge keys,
block scalars, multiple documents) and writes."""

import json

import pytest

from conftest import run_tb, tb_error
from tblib import yaml as tbyaml


def run(source):
    return run_tb("import yaml\n" + source)


def parse(text):
    return tbyaml.parse_text(text)


def test_scalars_and_collections():
    v = parse("a: 1\nb: 2.5\nc: true\nd: ~\ne: null\nf: 'it''s'\ng: \"tab\\there\"\nh: [1, two, {x: 3}]\ni:\n  - a\n  - b: c\n")
    assert v == {"a": 1, "b": 2.5, "c": True, "d": None, "e": None, "f": "it's", "g": "tab\there",
                 "h": [1, "two", {"x": 3}], "i": ["a", {"b": "c"}]}


def test_anchors_aliases_and_merge_keys():
    v = parse("base: &b\n  level: high\n  tags: [x]\nrule:\n  <<: *b\n  level: low\nboth:\n  - *b\n")
    assert v["rule"] == {"level": "low", "tags": ["x"]}
    assert v["both"] == [{"level": "high", "tags": ["x"]}]
    v["both"][0]["tags"].append("y")  # aliases are copies, not shared
    assert v["base"]["tags"] == ["x"]


def test_block_scalars_and_comments():
    v = parse("lit: |\n  one\n  two\nfold: >\n  a\n  b\nkeep: |+\n  x\n\nstrip: |-\n  y\nurl: http://x.example/#frag # comment\n")
    assert v["lit"] == "one\ntwo\n" and v["fold"] == "a b\n" and v["strip"] == "y"
    assert v["keep"].startswith("x\n") and v["url"] == "http://x.example/#frag"


def test_multi_document():
    assert tbyaml.parse_documents("a: 1\n---\nb: 2\n...\n") == [{"a": 1}, {"b": 2}]
    assert run('say(yaml.parse_all("---\\na: 1\\n---\\n- x\\n"))') == ['[{"a": 1}, ["x"]]']
    assert "2 YAML documents" in str(tb_error('import yaml\nyaml.parse("a: 1\\n---\\nb: 2")'))


def test_stringify_round_trip_and_quoting():
    src = ('let v = {"name": "Tanisha", "n": nil, "flag": true, "num": "123", "yes": "yes", "list": [1, {"a": "b: c"}],'
           ' "text": "line1\\nline2\\n", "empty": [], "m": {}}\nlet t = yaml.stringify(v)\nsay(yaml.parse(t) == v)\nsay(t)')
    out = run(src)
    assert out[0] == "true"
    text = "\n".join(out[1:])
    assert "name: Tanisha" in text and "num: '123'" in text and "a: 'b: c'" in text and "empty: []" in text


def test_read_and_write_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = run('yaml.write("c.yml", {"a": [1, 2], "b": {"c": "d"}})\nsay(yaml.read("c.yml"))\n'
              'say(yaml.read_all("c.yml"))')
    assert out == ['{"a": [1, 2], "b": {"c": "d"}}', '[{"a": [1, 2], "b": {"c": "d"}}]']
    assert "I couldn't find 'nope.yml'" in str(tb_error('import yaml\nyaml.read("nope.yml")'))


@pytest.mark.parametrize("text,message", [
    ("a: *nowhere", "alias *nowhere points to an anchor"),
    ("a: 1\na: 2", 'the key "a" appears twice'),
    ("a:\n\tb: 1", "spaces, not tabs"),
    ("a: [1, 2", "never closes"),
    ("a: 'open", "never ends"),
])
def test_errors_are_friendly(text, message):
    err = str(tb_error("import yaml\nyaml.parse(" + json.dumps(text) + ")"))
    assert "That YAML" in err or "yaml.parse" in err
    assert message in err


def test_detect_parse_yaml_still_works():
    assert run_tb('import detect\nsay(detect.parse_yaml("a: &x 1\\nb: *x"))') == ['{"a": 1, "b": 1}']
