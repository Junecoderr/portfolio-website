"""xml library: safe parsing, finding, writing."""

import json

import pytest

import tb
from conftest import run_tb, tb_error

DOC = '''<library name="Town">
  <book id="1" year="2001"><title>Logs &amp; You</title><author>Tanisha</author></book>
  <book id="2"><title>Networks</title><author>Sam</author></book>
  <note>Open <b>late</b> on Fridays</note>
</library>'''

SETUP = 'import xml\nlet doc = xml.parse(' + json.dumps(DOC) + ')\n'


def test_parse_structure():
    src = '''import xml
let d = xml.parse("<a x='1'><b>hi</b><c/></a>")
say(d.tag, d.attrs, len(d.children), d.text)
say(d.children[0].tag, d.children[0].text, d.children[1].tag, d.children[1].children, d.children[1].text)
'''
    assert run_tb(src) == ['a {"x": "1"} 2 ', "b hi c [] "]


def test_find_and_values():
    src = SETUP + '''
say(doc.find("book").attrs.id, doc.find("book[@id='2']/title").text, doc.find("missing"))
say(doc.find_all("book").map(fn(b) { return b.value("author") }))
say(doc.find_all(".//title").map(fn(t) { return t.text }))
say(doc.value("book[2]/title"), doc.value("nope", "none"), doc.attr("name"), doc.attr("size", 0))
say(doc.find("note").all_text(), doc.find("note").text, doc.find("note").children[0].tail)
say(doc.find("book").find("title").text, doc.find("book").name())
'''
    assert run_tb(src) == [
        "1 Networks nil",
        '["Tanisha", "Sam"]',
        '["Logs & You", "Networks"]',
        "Networks none Town 0",
        "Open late on Fridays Open on Fridays",
        "Logs & You book",
    ]


def test_namespaces():
    src = '''import xml
let d = xml.parse("<r xmlns='urn:x'><item>1</item></r>")
say(d.tag, d.name(), d.children[0].name(), d.find("{urn:x}item").text)
'''
    assert run_tb(src) == ["{urn:x}r r item 1"]


def test_stringify_and_round_trip():
    src = '''import xml
let n = xml.node("items", {"count": 2}, [xml.node("item", {"id": 1}, ["a < b"]), xml.node("item", {}, ["two"])])
say(xml.stringify(n, {"indent": false}))
say(xml.stringify(n))
say(xml.stringify({"tag": "p", "text": "x", "children": [{"tag": "b", "text": "y", "tail": " z"}]}, {"indent": false}))
let d = xml.parse(xml.stringify(n))
say(d.find_all("item").map(fn(i) { return i.text }))
say(d.to_text({"indent": false}) == xml.stringify(n, {"indent": false}))
say(xml.stringify(n, {"indent": false, "declaration": true}).starts_with("<?xml version"))
say(xml.escape("a < b & c"), xml.quote_attr("say \\"hi\\""), xml.is_node(n), xml.is_node({}))
'''
    assert run_tb(src) == [
        '<items count="2"><item id="1">a &lt; b</item><item>two</item></items>',
        '<items count="2">\n  <item id="1">a &lt; b</item>\n  <item>two</item>\n</items>',
        "<p>x<b>y</b> z</p>",
        '["a < b", "two"]',
        "true",
        "true",
        'a &lt; b &amp; c \'say "hi"\' true false',
    ]


def test_read_write(tmp_path):
    path = str(tmp_path / "data.xml").replace("\\", "/")
    src = f'''import xml
xml.write("{path}", xml.node("root", {{}}, [xml.node("v", {{}}, ["1"])]))
let d = xml.read("{path}")
say(d.tag, d.value("v"))
'''
    assert run_tb(src) == ["root 1"]
    assert (tmp_path / "data.xml").read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>\n<root>')


def test_doctype_and_entities_are_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><lolz>&lol2;</lolz>'
    err = tb_error('import xml\nxml.parse(' + json.dumps(bomb) + ')')
    assert "DOCTYPE or ENTITY declaration" in str(err) and err.fields["reason"] == "doctype"
    err = tb_error('import xml\nxml.parse("<a>&custom;</a>")')
    assert "isn't well-formed XML" in str(err) and err.fields["reason"] == "parse"


@pytest.mark.parametrize("src,message", [
    ('xml.parse("<a><b></a>")', "isn't well-formed XML (line 1, column"),
    ('xml.parse("")', "got empty text"),
    ('xml.parse(5)', "xml.parse() needs text to be text"),
    ('xml.parse("<a/>").find("")', "needs a path like"),
    ('xml.parse("<a/>").find("[[[")', "can't use the path"),
    ('xml.stringify({"text": "x"})', 'needs every node to have a "tag"'),
    ('xml.stringify({"tag": "bad tag"})', 'can\'t use "bad tag" as a tag name'),
    ('xml.stringify({"tag": "a", "color": 1})', 'doesn\'t know the node key "color"'),
    ('xml.stringify({"tag": "a"}, {"pretty": true})', 'doesn\'t know the option "pretty"'),
    ('xml.read("nope.xml")', "I couldn't find 'nope.xml'"),
    ('xml.read(".")', "is a folder, not a file"),
])
def test_xml_errors(src, message):
    assert message in str(tb_error("import xml\n" + src))


def test_docs():
    info = tb.describe_library("xml")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
