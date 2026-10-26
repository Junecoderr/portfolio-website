"""xml: read and write XML safely (no DOCTYPE, no entity expansion)."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _escape
from xml.sax.saxutils import quoteattr as _quoteattr

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_TEXT = 50_000_000     # bytes of XML text we agree to parse
MAX_DEPTH = 256           # nesting depth
DOCTYPE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
NODE_KEYS = ("tag", "attrs", "children", "text", "tail")


def parse_xml(text: str, line, where="xml.parse"):
    """An ElementTree root for text, rejecting DOCTYPE/ENTITY declarations
    (so no entity-expansion bombs or external entities) and giving friendly
    errors. Other tblib modules may reuse this."""
    if len(text) > MAX_TEXT:
        fail(line, f"{where}() got {len(text):,} characters of XML; it reads at most {MAX_TEXT:,}.")
    if DOCTYPE.search(text):
        fail(line, f"{where}() doesn't accept documents with a DOCTYPE or ENTITY declaration "
                   "(they can hide entity-expansion attacks). Remove the declaration first.", reason="doctype")
    parser = ET.XMLParser()
    try:
        root = ET.fromstring(text.lstrip("﻿"), parser=parser)
    except ET.ParseError as err:
        pos = getattr(err, "position", None)
        where_text = f" (line {pos[0]}, column {pos[1] + 1})" if pos else ""
        msg = str(err)
        msg = re.sub(r":\s*line \d+, column \d+$", "", msg)
        fail(line, f"That isn't well-formed XML{where_text}: {msg}.", reason="parse")
    return root


def load(api):
    m = Module(
        "xml",
        "Read and write XML. xml.parse(text) gives a node map {tag, attrs, children, text} with find(path) and "
        "find_all(path) using a small XPath subset (\"item\", \"a/b\", \".//name\", \"item[@id='3']\", \"item[2]\"). "
        "xml.stringify(node) writes a node (or a plain map with those keys) back out. Documents with a DOCTYPE "
        "are rejected, which protects against entity-expansion attacks.",
    )

    def method(obj, name, lo, hi, func, doc):
        obj[name] = _tb.Builtin(f"node.{name}", func, (lo, hi), doc)

    def tag_name(elem) -> str:
        tag = elem.tag if isinstance(elem.tag, str) else str(elem.tag)
        return tag

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag

    def to_node(elem, depth=0) -> dict:
        if depth > MAX_DEPTH:
            fail(None, f"The XML is nested more than {MAX_DEPTH} levels deep.")
        node = {
            "tag": tag_name(elem),
            "attrs": dict(elem.attrib),
            "children": [],
            "text": (elem.text or "").strip() if elem.text and elem.text.strip() else "",
        }
        if elem.tail and elem.tail.strip():
            node["tail"] = elem.tail.strip()
        node["children"] = [to_node(child, depth + 1) for child in elem]
        attach(node, elem)
        return node

    def attach(node, elem):
        def path_arg(line, path, fn):
            check_type(line, path, "text", f"node.{fn}", "path")
            if not path.strip():
                fail(line, f"node.{fn}() needs a path like \"item\" or \".//name\", but got empty text.")
            return path.strip()

        def run_find(line, path, fn, all_):
            try:
                found = elem.findall(path) if all_ else elem.find(path)
            except SyntaxError as err:
                fail(line, f"node.{fn}() can't use the path \"{path}\": {err}. Paths look like \"item\", "
                           "\"a/b\", \".//name\", \"item[@id='3']\" or \"item[2]\".", reason="path")
            except (KeyError, IndexError, ValueError, TypeError) as err:
                fail(line, f"node.{fn}() can't use the path \"{path}\": {err}.", reason="path")
            return found

        def find(line, path):
            path = path_arg(line, path, "find")
            found = run_find(line, path, "find", False)
            return None if found is None else to_node(found)

        def find_all(line, path):
            path = path_arg(line, path, "find_all")
            return [to_node(e) for e in run_find(line, path, "find_all", True)]

        def value(line, path, default=None):
            path = path_arg(line, path, "value")
            found = run_find(line, path, "value", False)
            if found is None:
                return default
            return (found.text or "").strip()

        def attr(line, name, default=None):
            check_type(line, name, "text", "node.attr", "name")
            return elem.attrib.get(name, default)

        def all_text(line):
            return "".join(elem.itertext()).strip()

        def name(line):
            return local(tag_name(elem))

        def to_text(line, options=None):
            return stringify(line, node, options)

        for mname, lo, hi, func, doc in [
            ("find", 1, 1, find, "find(path) -> node\nThe first matching node, or nil. Paths: \"item\" (a child), "
                                  "\"a/b\", \".//b\" (anywhere below), \"item[@id='3']\", \"item[2]\" (second one)."),
            ("find_all", 1, 1, find_all, "find_all(path) -> list\nEvery matching node (same paths as find)."),
            ("value", 1, 2, value, "value(path, default?) -> text\nThe text inside the first node matching path, or "
                                    "default (nil) when there is none: node.value(\"author/name\")."),
            ("attr", 1, 2, attr, "attr(name, default?) -> text\nAn attribute's value, or default (nil) if missing."),
            ("all_text", 0, 0, all_text, "all_text() -> text\nAll the text inside this node and its children."),
            ("name", 0, 0, name, "name() -> text\nThe tag without its namespace: \"{urn:x}item\" gives \"item\"."),
            ("to_text", 0, 1, to_text, "to_text(options?) -> text\nThis node as XML text (see xml.stringify)."),
        ]:
            method(node, mname, lo, hi, func, doc)

    def node_arg(line, value, fn, depth=0):
        """An ElementTree element from a node map (or a plain map with tag/attrs/children/text)."""
        if depth > MAX_DEPTH:
            fail(line, f"{fn}() got nodes nested more than {MAX_DEPTH} levels deep.")
        if isinstance(value, str):
            return value  # bare text inside children
        check_type(line, value, "map", fn, "node")
        tag = value.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            fail(line, f'{fn}() needs every node to have a "tag" (text), like {{"tag": "item", "text": "x"}}.')
        if not re.match(r"^(\{[^}]*\})?[A-Za-z_][\w.\-:]*$", tag):
            fail(line, f'{fn}() can\'t use "{tag}" as a tag name.')
        attrs = value.get("attrs") or {}
        check_type(line, attrs, "map", fn, '"attrs"')
        elem = ET.Element(tag, {str(k): _tb.to_text(v) for k, v in attrs.items() if v is not None})
        text = value.get("text")
        if text is not None:
            elem.text = text if isinstance(text, str) else _tb.to_text(text)
        tail = value.get("tail")
        if tail is not None:
            elem.tail = tail if isinstance(tail, str) else _tb.to_text(tail)
        children = value.get("children") or []
        check_type(line, children, "list", fn, '"children"')
        last = None
        for child in children:
            built = node_arg(line, child, fn, depth + 1)
            if isinstance(built, str):  # loose text between elements
                if last is None:
                    elem.text = (elem.text or "") + built
                else:
                    last.tail = (last.tail or "") + built
                continue
            elem.append(built)
            last = built
        for key in value:
            if isinstance(key, str) and key not in NODE_KEYS and not isinstance(value[key], _tb.Builtin):
                fail(line, f'{fn}() doesn\'t know the node key "{key}". Nodes have tag, attrs, children, text and tail.')
        return elem

    def options_arg(line, options, fn):
        if options is None:
            return {}
        check_type(line, options, "map", fn, "options")
        allowed = {"indent": "text|number|bool|nil", "declaration": "bool"}
        for key in options:
            if key not in allowed:
                fail(line, f'{fn}() doesn\'t know the option "{key}". Options: indent, declaration.')
        return options

    def indent_text(value):
        if value is None or value is False:
            return None
        if value is True:
            return "  "
        if _tb.is_number(value):
            return " " * int(value)
        return value

    def stringify(line, node, options=None, fn="xml.stringify"):
        o = options_arg(line, options, fn)
        elem = node_arg(line, node, fn)
        if isinstance(elem, str):
            fail(line, f"{fn}() needs a node map, not plain text.")
        indent = indent_text(o.get("indent", "  "))
        if indent is not None:
            ET.indent(elem, space=indent)
        text = ET.tostring(elem, encoding="unicode")
        if o.get("declaration"):
            text = '<?xml version="1.0" encoding="UTF-8"?>\n' + text
        return text

    @m.fn("parse", 1, doc=(
        "parse(text) -> node\n"
        "Read XML text into a node: {\"tag\", \"attrs\" (map), \"children\" (list of nodes), \"text\"} plus "
        "find(path), find_all(path), value(path), attr(name), all_text(). A DOCTYPE is refused."))
    def parse(line, text):
        check_type(line, text, "text", "xml.parse", "text")
        if not text.strip():
            fail(line, "xml.parse() got empty text; there is no XML to read.", reason="parse")
        return to_node(parse_xml(text, line))

    @m.fn("stringify", 1, 2, doc=(
        "stringify(node, options?) -> text\n"
        "Turn a node (from parse, or a plain map {\"tag\": \"item\", \"attrs\": {...}, \"children\": [...], "
        "\"text\": \"...\"}) into XML text. options: {\"indent\": \"  \" (default; false for one line), "
        "\"declaration\": true} to add the <?xml ...?> header. Text is escaped for you."))
    def stringify_(line, node, options=None):
        return stringify(line, node, options)

    @m.fn("read", 1, doc="read(path) -> node\nRead an XML file (see parse).")
    def read(line, path):
        check_type(line, path, "text", "xml.read", "path")
        if not path.strip():
            fail(line, "xml.read() needs a path, but got empty text.")
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        try:
            from tblib.fs import read_text
            text = read_text(path)
        except OSError as err:
            fail_os(line, err, path, "read")
        if not text.strip():
            fail(line, f"'{path}' is empty; there is no XML to read.", reason="parse", path=path)
        return to_node(parse_xml(text, line, "xml.read"))

    @m.fn("write", 2, 3, doc="write(path, node, options?)\nSave a node as an XML file (with the <?xml ?> header).")
    def write(line, path, node, options=None):
        check_type(line, path, "text", "xml.write", "path")
        if not path.strip():
            fail(line, "xml.write() needs a path, but got empty text.")
        o = dict(options_arg(line, options, "xml.write"))
        o.setdefault("declaration", True)
        text = stringify(line, node, o, "xml.write")
        try:
            with open(os.path.expanduser(path), "w", encoding="utf-8", newline="") as f:
                f.write(text + "\n")
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    @m.fn("node", 1, 3, doc=(
        "node(tag, attrs?, children?) -> map\n"
        "Make a node to build documents with: xml.node(\"item\", {\"id\": 1}, [\"Hello\"]). children may hold "
        "nodes and plain text."))
    def node_(line, tag, attrs=None, children=None):
        check_type(line, tag, "text", "xml.node", "tag")
        if attrs is not None:
            check_type(line, attrs, "map", "xml.node", "attrs")
        if children is not None:
            check_type(line, children, "list", "xml.node", "children")
        text = ""
        kids = []
        for child in children or []:
            if isinstance(child, dict):
                kids.append(child)
            elif not kids and isinstance(child, str):
                text += child
            else:
                kids.append(child if isinstance(child, str) else _tb.to_text(child))
        return {"tag": tag, "attrs": dict(attrs or {}), "children": kids, "text": text}

    @m.fn("escape", 1, doc="escape(text) -> text\nText made safe inside XML: < > & become &lt; &gt; &amp;.")
    def escape(line, value):
        check_type(line, value, "text", "xml.escape", "text")
        return _escape(value)

    @m.fn("quote_attr", 1, doc="quote_attr(text) -> text\nText quoted as an attribute value, like \"a &amp; b\" with its quotes.")
    def quote_attr(line, value):
        check_type(line, value, "text", "xml.quote_attr", "text")
        return _quoteattr(value)

    @m.fn("is_node", 1, doc="is_node(x) -> bool\nTrue if x looks like an XML node (a map with a text \"tag\").")
    def is_node(line, value):
        return isinstance(value, dict) and isinstance(value.get("tag"), str)

    return m
