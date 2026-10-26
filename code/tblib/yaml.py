"""yaml: read and write YAML (the format Sigma rules, CI files and many configs use).

A stdlib-only YAML reader and writer. It reads the YAML people actually
write: block maps and lists, flow [lists] and {maps} (nested), plain and
quoted values, | and > text blocks (with - and + chomping and explicit
indents), comments, several documents in one file (---), anchors (&name),
aliases (*name), merge keys (<<: *base) and the core !!tags. It does not
read complex keys (? key), directives (%YAML) or custom !tags.

Other tblib modules use `parse_text`, `parse_documents`, `to_text_yaml` and
`YamlError` directly (detect loads Sigma rules through here).
"""

from __future__ import annotations

import base64 as _b64
import os
import re

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_ALIAS_DEPTH = 200


class YamlError(Exception):
    """A problem in the YAML text; the caller turns it into a friendly TB error."""

    def __init__(self, message, lineno=None):
        super().__init__(message)
        self.lineno = lineno


# =============================================================================
# Scalars
# =============================================================================

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/", " ": " ", "a": "\a",
           "b": "\b", "e": "\x1b", "f": "\f", "v": "\v", "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}
INT_RE = re.compile(r"[-+]?(?:0|[1-9][0-9_]*)")
HEX_RE = re.compile(r"0x[0-9a-fA-F_]+")
OCT_RE = re.compile(r"0o[0-7_]+")
FLOAT_RE = re.compile(r"[-+]?(?:[0-9][0-9_]*)?\.[0-9_]*(?:[eE][-+]?[0-9]+)?|[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+")
TIMESTAMP_RE = re.compile(r"\d{4}-\d\d?-\d\d?(?:[Tt ]\S.*)?")
NULLS = {"null", "Null", "NULL", "~", ""}
TRUES = {"true", "True", "TRUE"}
FALSES = {"false", "False", "FALSE"}
INFS = {".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF"}
NEG_INFS = {"-.inf", "-.Inf", "-.INF"}
NANS = {".nan", ".NaN", ".NAN"}


def find_closing(text: str, start: int):
    """Index of the quote that closes the one at text[start], or None."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == quote:
            if quote == "'" and text[i + 1:i + 2] == "'":
                i += 2
                continue
            return i
        if text[i] == "\\" and quote == '"':
            i += 2
            continue
        i += 1
    return None


def unquote_double(body: str, lineno) -> str:
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "x" and i + 3 < len(body):
                out.append(chr(int(body[i + 2:i + 4], 16)))
                i += 4
                continue
            if nxt == "u" and i + 5 < len(body):
                out.append(chr(int(body[i + 2:i + 6], 16)))
                i += 6
                continue
            if nxt == "U" and i + 9 < len(body):
                out.append(chr(int(body[i + 2:i + 10], 16)))
                i += 10
                continue
            if nxt == "\n":
                i += 2
                while i < len(body) and body[i] in " \t":
                    i += 1
                continue
            if nxt not in ESCAPES:
                raise YamlError(f"\\{nxt} isn't an escape I know inside \"...\"", lineno)
            out.append(ESCAPES[nxt])
            i += 2
            continue
        out.append(ch)
        i += 1
    return fold_quoted("".join(out))


def fold_quoted(text: str) -> str:
    """Line breaks inside a quoted scalar fold to spaces (blank lines stay as newlines)."""
    if "\n" not in text:
        return text
    lines = text.split("\n")
    out = lines[0].rstrip() if len(lines) > 1 else lines[0]
    blanks = 0
    for part in lines[1:]:
        stripped = part.strip()
        if stripped == "":
            blanks += 1
            continue
        out += ("\n" * blanks if blanks else " ") + stripped
        blanks = 0
    return out + ("\n" * blanks if blanks else "")


def plain_scalar(text: str):
    """The value of an unquoted scalar: null, bools, numbers, else text."""
    if text in NULLS:
        return None
    if text in TRUES:
        return True
    if text in FALSES:
        return False
    if INT_RE.fullmatch(text):
        return int(text.replace("_", ""))
    if HEX_RE.fullmatch(text):
        return int(text.replace("_", ""), 16)
    if OCT_RE.fullmatch(text):
        return int(text[2:].replace("_", ""), 8)
    if FLOAT_RE.fullmatch(text) and text not in (".", "+.", "-."):
        return _tb.tidy_number(float(text.replace("_", "")))
    if text in INFS:
        return float("inf")
    if text in NEG_INFS:
        return float("-inf")
    if text in NANS:
        return float("nan")
    return text


def apply_tag(tag: str, value, lineno):
    """The core !!tags: !!str, !!int, !!float, !!bool, !!null, !!seq, !!map, !!binary."""
    if tag in ("!!str", "!str"):
        return value if isinstance(value, str) else ("" if value is None else _tb.to_text(value))
    if tag == "!!int":
        try:
            return int(str(value).replace("_", ""), 0) if isinstance(value, str) else int(value)
        except (TypeError, ValueError):
            raise YamlError(f"!!int needs a whole number, not {_tb.to_text(value, True)}", lineno) from None
    if tag == "!!float":
        try:
            return float(str(value).replace("_", "")) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            raise YamlError(f"!!float needs a number, not {_tb.to_text(value, True)}", lineno) from None
    if tag == "!!bool":
        text = str(value).lower()
        if text in ("true", "yes", "on"):
            return True
        if text in ("false", "no", "off"):
            return False
        raise YamlError(f"!!bool needs true or false, not {_tb.to_text(value, True)}", lineno)
    if tag == "!!null":
        return None
    if tag == "!!binary":
        try:
            return _b64.b64decode(re.sub(r"\s+", "", str(value)))
        except Exception:
            raise YamlError("!!binary needs base64 text", lineno) from None
    if tag in ("!!seq", "!!map", "!!omap", "!!set", "!!timestamp"):
        return value
    raise YamlError(f"the tag {tag} isn't supported (only the core !!str, !!int, !!float, !!bool, !!null tags are)",
                    lineno)


# =============================================================================
# Flow collections: [a, b, {k: v}] possibly spanning lines
# =============================================================================

class FlowParser:
    def __init__(self, text: str, lineno, doc):
        self.text = text
        self.pos = 0
        self.lineno = lineno
        self.doc = doc

    def skip(self):
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch in " \t\n\r":
                self.pos += 1
            elif ch == "#" and (self.pos == 0 or self.text[self.pos - 1] in " \t\n"):
                while self.pos < len(self.text) and self.text[self.pos] != "\n":
                    self.pos += 1
            else:
                break

    def parse(self):
        self.skip()
        value = self.node()
        self.skip()
        if self.pos < len(self.text):
            raise YamlError(f"extra text after a flow value: \"{self.text[self.pos:].strip()}\"", self.lineno)
        return value

    def node(self):
        self.skip()
        anchor, tag = None, None
        while self.pos < len(self.text) and self.text[self.pos] in "&!":
            m = re.match(r"([&!]\S*)\s*", self.text[self.pos:])
            token = m.group(1)
            if token[0] == "&":
                anchor = token[1:]
            else:
                tag = token
            self.pos += m.end()
        if self.pos >= len(self.text):
            raise YamlError("a flow value ends too early", self.lineno)
        ch = self.text[self.pos]
        if ch == "[":
            value = self.sequence()
        elif ch == "{":
            value = self.mapping()
        elif ch == "*":
            m = re.match(r"\*([^\s,\]}]+)", self.text[self.pos:])
            if not m:
                raise YamlError("an alias (*name) needs a name", self.lineno)
            self.pos += m.end()
            value = self.doc.alias(m.group(1), self.lineno)
        elif ch in "'\"":
            end = find_closing(self.text, self.pos)
            if end is None:
                raise YamlError("a quoted value inside [...] or {...} never ends", self.lineno)
            body = self.text[self.pos + 1:end]
            value = body.replace("''", "'") if ch == "'" else unquote_double(body, self.lineno)
            if ch == "'":
                value = fold_quoted(value)
            self.pos = end + 1
        else:
            end = self.pos
            while end < len(self.text):
                c = self.text[end]
                if c in ",[]{}":
                    break
                if c == ":" and (end + 1 >= len(self.text) or self.text[end + 1] in " \t\n,]}"):
                    break
                if c == "#" and end > self.pos and self.text[end - 1] in " \t\n":
                    break
                end += 1
            raw = self.text[self.pos:end]
            self.pos = end
            value = plain_scalar(" ".join(raw.split()))
        if tag:
            value = apply_tag(tag, value, self.lineno)
        if anchor:
            self.doc.anchors[anchor] = value
        return value

    def sequence(self):
        self.pos += 1
        items = []
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise YamlError("a [list] value doesn't end with ]", self.lineno)
            if self.text[self.pos] == "]":
                self.pos += 1
                return items
            if self.text[self.pos] == ",":
                raise YamlError("a stray comma in a [list]", self.lineno)
            item = self.pair_or_node()
            items.append(item)
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1

    def pair_or_node(self):
        """In a flow list, "a: b" makes a one-entry map."""
        start = self.pos
        key = self.node()
        self.skip()
        if self.pos < len(self.text) and self.text[self.pos] == ":" and (
                self.pos + 1 >= len(self.text) or self.text[self.pos + 1] in " \t\n,]}"):
            self.pos += 1
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] in ",]}":
                return {self.key_text(key): None}
            return {self.key_text(key): self.node()}
        # a plain scalar that swallowed ":"? e.g. "http://x" is fine; "a:b" is the text a:b
        del start
        return key

    @staticmethod
    def key_text(key):
        return key if isinstance(key, str) else _tb.to_text(key)

    def mapping(self):
        self.pos += 1
        result = {}
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise YamlError("a {map} value doesn't end with }", self.lineno)
            if self.text[self.pos] == "}":
                self.pos += 1
                return result
            key = self.node()
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] == ":":
                self.pos += 1
                self.skip()
                if self.pos < len(self.text) and self.text[self.pos] in ",}":
                    value = None
                else:
                    value = self.node()
            else:
                value = None
            self.doc.put(result, self.key_text(key), value, self.lineno)
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1


# =============================================================================
# The block reader
# =============================================================================

def strip_comment(text: str) -> str:
    """Remove a trailing # comment that is outside quotes."""
    quote = None
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                quote = None
            elif ch == "\\" and quote == '"':
                i += 2
                continue
        elif ch in "'\"" and (i == 0 or text[i - 1] in " \t:-[{,"):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


KEY_RE = re.compile(r"^([^\s'\"#\[\]{}&*!>|%@`-][^:#]*?|[^\s'\"#\[\]{}&*!>|%@`-]|-[^\s][^:#]*?):(?:\s+(.*))?$")
KEY_WITH_NODE_RE = re.compile(r"^([&!][^\s]*\s+)+")


def split_key(text: str, lineno):
    """'key: value' -> (key, rest) or None when the line isn't a mapping entry."""
    if text[:1] in "'\"":
        end = find_closing(text, 0)
        if end is None:
            return None
        rest = text[end + 1:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] in " \t"):
            body = text[1:end]
            key = body.replace("''", "'") if text[0] == "'" else unquote_double(body, lineno)
            return key, rest[1:].strip()
        return None
    m = KEY_RE.match(text)
    if not m:
        return None
    return m.group(1).strip(), (m.group(2) or "").strip()


def is_list_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


BLOCK_SCALAR_RE = re.compile(r"^[|>](?:[-+]?[1-9]?|[1-9]?[-+]?)$")


class Document:
    """Shared state while reading one document: anchors and key checks."""

    def __init__(self):
        self.anchors = {}
        self.merged = {}  # id(mapping) -> keys that came from a <<: merge

    def alias(self, name, lineno):
        if name not in self.anchors:
            known = ", ".join(sorted(self.anchors)) or "none yet"
            raise YamlError(f"the alias *{name} points to an anchor that hasn't been defined (anchors so far: {known})",
                            lineno)
        return deep_copy(self.anchors[name])

    def put(self, mapping: dict, key, value, lineno):
        if key == "<<":
            sources = value if isinstance(value, list) else [value]
            for src in sources:
                if not isinstance(src, dict):
                    raise YamlError("a merge key (<<) needs a map or a list of maps (usually *aliases)", lineno)
                merged = self.merged.setdefault(id(mapping), set())
                for k, v in src.items():
                    if k not in mapping:
                        mapping[k] = deep_copy(v)
                        merged.add(k)
            return
        if key in mapping and key not in self.merged.get(id(mapping), ()):
            raise YamlError(f"the key \"{key}\" appears twice", lineno)
        self.merged.get(id(mapping), set()).discard(key)
        mapping[key] = value


def deep_copy(value):
    if isinstance(value, dict):
        return {k: deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [deep_copy(v) for v in value]
    return value


class YamlReader:
    def __init__(self, text: str):
        self.raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.all_lines = []  # [indent, text, lineno]
        for n, raw_line in enumerate(self.raw, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if raw_line.startswith("%"):
                raise YamlError("YAML directives (%) aren't supported", n)
            leading = raw_line[:len(raw_line) - len(raw_line.lstrip())]
            if "\t" in leading:
                raise YamlError("indent with spaces, not tabs", n)
            self.all_lines.append([len(leading), strip_comment(stripped), n])
        self.lines = self.all_lines
        self.pos = 0
        self.doc = Document()

    # -- documents --------------------------------------------------------------

    def documents(self) -> list:
        docs, current, starts = [], [], []
        seen_marker = False
        for item in self.all_lines:
            if item[0] == 0 and (item[1] in ("---", "...") or item[1].startswith("--- ")):
                if current or (seen_marker and item[1] != "..."):
                    docs.append(current)
                current = []
                seen_marker = True
                if item[1].startswith("--- "):
                    rest = item[1][4:].strip()
                    current.append([0, rest, item[2]])
                continue
            current.append(item)
        if current or not docs:
            docs.append(current)
        result = []
        for lines in docs:
            self.doc = Document()
            if not lines:
                result.append(None)
                continue
            self.lines, self.pos = lines, 0
            value = self.node_at(lines[0][0], lines[0][2])
            if self.pos < len(self.lines):
                bad = self.lines[self.pos]
                raise YamlError("this line is indented in a way that doesn't fit the lines above it", bad[2])
            result.append(value)
        return result

    def peek(self):
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    # -- nodes --------------------------------------------------------------------

    def node_at(self, indent, lineno):
        """The block node starting at the current line (a list, a map or a scalar)."""
        line = self.peek()
        if line is None:
            return None
        if is_list_item(line[1]):
            return self.sequence(line[0])
        if line[1].startswith("? "):
            raise YamlError("complex keys (? key) aren't supported", line[2])
        if split_key(line[1], line[2]) is not None or self.key_after_props(line[1]):
            return self.mapping(line[0])
        # a lone scalar document (or a scalar under a "- &anchor" style line)
        self.pos += 1
        return self.inline_value(line[1], indent - 1, line[2])

    def key_after_props(self, text):
        """"&anchor key: value" -- properties before a map's first key."""
        m = KEY_WITH_NODE_RE.match(text)
        return m is not None and split_key(text[m.end():], 0) is not None

    def take_props(self, text, lineno):
        """Strip leading &anchor / !tag words. Gives (anchor, tag, rest)."""
        anchor = tag = None
        while True:
            m = re.match(r"([&!]\S*)(?:\s+|$)", text)
            if not m:
                break
            token = m.group(1)
            if token[0] == "&":
                if len(token) == 1:
                    raise YamlError("an anchor (&name) needs a name", lineno)
                anchor = token[1:]
            else:
                tag = token
            text = text[m.end():]
        return anchor, tag, text

    def finish(self, value, anchor, tag, lineno):
        if tag:
            value = apply_tag(tag, value, lineno)
        if anchor:
            self.doc.anchors[anchor] = value
        return value

    def value_after(self, rest, indent, lineno):
        """What follows "key:" or "- ": nothing (a block below), a block scalar
        indicator, an alias, a flow collection, or a scalar."""
        anchor, tag, rest = self.take_props(rest, lineno)
        if rest == "":
            nxt = self.peek()
            if nxt is not None and (nxt[0] > indent or (nxt[0] == indent and is_list_item(nxt[1]))):
                if anchor and nxt[0] > indent or anchor:
                    # anchors on collections must be set after reading them
                    value = self.node_at(nxt[0], nxt[2])
                    return self.finish(value, anchor, tag, lineno)
                return self.finish(self.node_at(nxt[0], nxt[2]), None, tag, lineno)
            return self.finish(None, anchor, tag, lineno)
        if BLOCK_SCALAR_RE.match(rest):
            return self.finish(self.block_scalar(rest, indent, lineno), anchor, tag, lineno)
        return self.finish(self.inline_value(rest, indent, lineno), anchor, tag, lineno)

    def inline_value(self, text, indent, lineno):
        """A scalar, alias or flow collection that starts on this line (may continue below)."""
        if text.startswith("*"):
            name = text[1:].strip()
            if not name or " " in name:
                raise YamlError("an alias (*name) needs a name and nothing after it", lineno)
            return self.doc.alias(name, lineno)
        if text[:1] in "[{":
            joined = text
            while not flow_balanced(joined):
                nxt = self.peek()
                if nxt is None or nxt[0] <= indent:
                    raise YamlError(f"a {text[0]}...{']' if text[0] == '[' else '}'} value never closes", lineno)
                self.pos += 1
                joined += "\n" + nxt[1]
            return FlowParser(joined, lineno, self.doc).parse()
        if text[:1] in "'\"":
            if find_closing(text, 0) is None:
                parts = [text]
                while True:
                    nxt = self.peek()
                    if nxt is None:
                        raise YamlError("a quoted value never ends", lineno)
                    self.pos += 1
                    parts.append(self.raw[nxt[2] - 1].strip())
                    joined = "\n".join(parts)
                    if find_closing(joined, 0) is not None:
                        text = joined
                        break
            end = find_closing(text, 0)
            if end != len(text) - 1:
                after = text[end + 1:].strip()
                if after.startswith("#"):
                    pass
                else:
                    raise YamlError("a quoted value has extra text after its closing quote", lineno)
            body = text[1:end]
            if text[0] == "'":
                return fold_quoted(body.replace("''", "'"))
            return unquote_double(body, lineno)
        parts = [text]
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] <= indent:
                break
            if split_key(nxt[1], nxt[2]) is not None or is_list_item(nxt[1]):
                raise YamlError("this line is indented more than the ones above it", nxt[2])
            parts.append(nxt[1])
            self.pos += 1
        return plain_scalar(" ".join(parts))

    def sequence(self, indent):
        items = []
        while True:
            line = self.peek()
            if line is None or line[0] != indent or not is_list_item(line[1]):
                break
            rest = line[1][1:]
            stripped = rest.lstrip()
            offset = 1 + len(rest) - len(stripped)
            anchor, tag, after_props = self.take_props(stripped, line[2])
            if stripped and (is_list_item(after_props) or (split_key(after_props, line[2]) is not None
                                                            and not after_props.startswith(("[", "{")))):
                # "- key: value" starts a map (or "- - x" a list) whose column is after the dash
                extra = len(stripped) - len(after_props)
                line[0], line[1] = indent + offset + extra, after_props
                items.append(self.finish(self.node_at(indent + offset + extra, line[2]), anchor, tag, line[2]))
                continue
            self.pos += 1
            items.append(self.value_after(stripped, indent, line[2]))
        return items

    def mapping(self, indent):
        result = {}
        while True:
            line = self.peek()
            if line is None or line[0] < indent:
                break
            if line[0] > indent:
                raise YamlError("this line is indented more than the ones above it", line[2])
            if is_list_item(line[1]):
                break
            if line[1].startswith("? "):
                raise YamlError("complex keys (? key) aren't supported", line[2])
            text = line[1]
            map_anchor = map_tag = None
            if self.key_after_props(text):
                map_anchor, map_tag, text = self.take_props(text, line[2])
            pair = split_key(text, line[2])
            if pair is None:
                raise YamlError(f"I expected \"key: value\" here, but found \"{line[1]}\"", line[2])
            key, value = pair
            key = key if isinstance(key, str) else _tb.to_text(key)
            if key[:1] not in "'\"" and key != "<<":
                key_value = plain_scalar(key)
                key = key if isinstance(key_value, str) else _tb.to_text(key_value)
            self.pos += 1
            self.doc.put(result, key, self.value_after(value, indent, line[2]), line[2])
            if map_anchor or map_tag:
                self.finish(result, map_anchor, None, line[2])
        return result

    def block_scalar(self, style, indent, lineno):
        """| and > blocks; blank lines matter, so read the raw lines."""
        body = []
        n = lineno  # raw index of the line after the key (lineno is 1-based)
        explicit = re.search(r"[1-9]", style)
        block_indent = indent + int(explicit.group(0)) if explicit else None
        while n < len(self.raw):
            raw_line = self.raw[n]
            if raw_line.strip():
                width = len(raw_line) - len(raw_line.lstrip(" "))
                if width <= indent or (block_indent is not None and width < block_indent):
                    break
                if block_indent is None:
                    block_indent = width
                body.append(raw_line[block_indent:])
            else:
                body.append(raw_line[block_indent:] if block_indent is not None and len(raw_line) > block_indent else "")
            n += 1
        while self.pos < len(self.lines) and self.lines[self.pos][2] <= n:
            self.pos += 1
        trailing = 0
        while body and body[-1].strip() == "":
            body.pop()
            trailing += 1
        if style.startswith(">"):
            text = ""
            prev_more_indented = False
            for part in body:
                more = part[:1] in " \t"
                if part == "":
                    text += "\n"
                elif not text or text.endswith("\n") or more or prev_more_indented:
                    text += ("" if not text or text.endswith("\n") else "\n") + part
                else:
                    text += " " + part
                prev_more_indented = more
        else:
            text = "\n".join(body)
        if "-" in style:
            return text
        if "+" in style:
            return text + "\n" * (trailing + 1) if text or trailing else text
        return text + "\n" if text else text


def flow_balanced(text: str) -> bool:
    depth, quote = 0, None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                quote = None
            elif ch == "\\" and quote == '"':
                i += 1
        elif ch in "'\"" and (i == 0 or text[i - 1] in " \t[{,:\n"):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        i += 1
    return depth <= 0 and quote is None


# =============================================================================
# Public helpers for other modules
# =============================================================================

def parse_documents(text: str) -> list:
    """Every document in the text, as TB values (an empty document is nil)."""
    return YamlReader(text).documents()


def parse_text(text: str):
    """One document. Raises YamlError when the text holds several."""
    docs = parse_documents(text)
    if not docs:
        return None
    if len(docs) > 1:
        raise YamlError(f"this text has {len(docs)} YAML documents (--- lines); use yaml.parse_all() to read them all")
    return docs[0]


# =============================================================================
# Writing YAML
# =============================================================================

PLAIN_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ./\\()+,=@^$%;!?<>|~\-]*$")


def needs_quotes(text: str) -> bool:
    if text == "" or text != text.strip():
        return True
    if text[0] in "-?:,[]{}#&*!|>'\"%@`" or text.endswith(":"):
        return True
    if ": " in text or " #" in text or "\n" in text or "\t" in text:
        return True
    if plain_scalar(text) != text or text in ("yes", "no", "on", "off", "Yes", "No", "On", "Off", "y", "n"):
        return True
    if TIMESTAMP_RE.fullmatch(text):
        return True
    return not all(31 < ord(ch) != 127 for ch in text)


def quote(text: str) -> str:
    if "\n" in text or "\t" in text or any(ord(ch) < 32 for ch in text) or '"' in text and "'" not in text:
        if "\n" not in text and "\t" not in text and all(ord(ch) >= 32 for ch in text):
            return "'" + text.replace("'", "''") + "'"
        out = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        out = "".join(ch if ord(ch) >= 32 else f"\\x{ord(ch):02x}" for ch in out)
        return '"' + out + '"'
    return "'" + text.replace("'", "''") + "'"


def scalar_text(value, line) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return ".nan"
        if value in (float("inf"), float("-inf")):
            return ".inf" if value > 0 else "-.inf"
        return _tb.to_text(value)
    if isinstance(value, bytes):
        return "!!binary " + _b64.b64encode(value).decode("ascii")
    if isinstance(value, str):
        return quote(value) if needs_quotes(value) else value
    fail(line, f"YAML can't hold a {_tb.type_name(value)}.")


def to_text_yaml(value, line, indent=2) -> str:
    """TB value -> YAML text (block style, lists with "- ", multi-line text as |)."""
    out = []
    emit(value, out, 0, indent, line, False)
    return "\n".join(out) + "\n"


def emit(value, out, depth, indent, line, in_list):
    pad = " " * depth
    if isinstance(value, dict):
        if not value:
            out.append(pad + "{}")
            return
        first = True
        for k, v in value.items():
            key = k if isinstance(k, str) else _tb.to_text(k)
            key = quote(key) if needs_quotes(key) else key
            prefix = pad if not (in_list and first) else ""
            first = False
            if isinstance(v, (dict, list)) and v:
                out.append(f"{prefix}{key}:")
                emit(v, out, depth + (indent if isinstance(v, dict) else 0), indent, line, False)
            elif isinstance(v, str) and "\n" in v and v.strip("\n") == v.rstrip("\n") and all(
                    ord(ch) >= 32 or ch == "\n" for ch in v):
                keep = "" if v.endswith("\n") and not v.endswith("\n\n") else ("-" if not v.endswith("\n") else "+")
                out.append(f"{prefix}{key}: |{keep}")
                for part in v.rstrip("\n").split("\n") if keep != "+" else v.split("\n"):
                    out.append((" " * (depth + indent) + part) if part else "")
            elif isinstance(v, dict):
                out.append(f"{prefix}{key}: {{}}")
            elif isinstance(v, list):
                out.append(f"{prefix}{key}: []")
            else:
                out.append(f"{prefix}{key}: {scalar_text(v, line)}")
        return
    if isinstance(value, list):
        if not value:
            out.append(pad + "[]")
            return
        for item in value:
            if isinstance(item, dict) and item:
                start = len(out)
                emit(item, out, depth + 2, indent, line, True)
                out[start] = pad + "- " + out[start]
            elif isinstance(item, list) and item:
                start = len(out)
                emit(item, out, depth + 2, indent, line, True)
                out[start] = pad + "- " + out[start].lstrip()
            else:
                out.append(pad + "- " + scalar_text(item, line))
        return
    out.append(pad + scalar_text(value, line))


# =============================================================================
# The library
# =============================================================================

def yaml_fail(line, err: YamlError, what="That YAML"):
    at = f" (line {err.lineno})" if err.lineno else ""
    fail(line, f"{what} doesn't work: {err}{at}.", yaml_line=err.lineno)


def load(api):
    m = Module(
        "yaml",
        "Read and write YAML, the human-friendly data format Sigma rules, CI pipelines and many configs use. "
        "Reads maps, lists, [flow] values, quoted and plain text, | and > text blocks, comments, several "
        "documents (---), anchors (&name), aliases (*name), merge keys (<<: *base) and the core !!tags.",
    )

    @m.fn("parse", 1, doc=(
        "parse(text) -> value\n"
        "Turn YAML text into TB values: maps, lists, text, numbers, true/false and nil. The text must hold one "
        "document; use parse_all() for a file with several (--- lines)."))
    def parse(line, text):
        check_type(line, text, "text", "yaml.parse", "text")
        try:
            return parse_text(text)
        except YamlError as err:
            yaml_fail(line, err)
        except RecursionError:
            fail(line, "That YAML is nested too deeply to read.")

    @m.fn("parse_all", 1, doc=(
        "parse_all(text) -> list\n"
        "Every document in the YAML text (separated by --- lines), as a list of values."))
    def parse_all(line, text):
        check_type(line, text, "text", "yaml.parse_all", "text")
        try:
            return parse_documents(text)
        except YamlError as err:
            yaml_fail(line, err)
        except RecursionError:
            fail(line, "That YAML is nested too deeply to read.")

    @m.fn("stringify", 1, 2, doc=(
        "stringify(value, indent?) -> text\n"
        "Turn a TB value into YAML text (block style: one key per line, lists with \"- \", text with line "
        "breaks as a | block). indent is the number of spaces per level (default 2)."))
    def stringify(line, value, indent=2):
        indent = check_type(line, indent, "whole", "yaml.stringify", "indent")
        if not 1 <= indent <= 8:
            fail(line, "yaml.stringify() needs an indent from 1 to 8 spaces.")
        try:
            return to_text_yaml(value, line, indent)
        except RecursionError:
            fail(line, "That value is nested too deeply (or contains itself) to turn into YAML.")

    @m.fn("read", 1, doc="read(path) -> value\nRead a .yml / .yaml file with one document into TB values.")
    def read(line, path):
        text = read_file(line, path, "yaml.read")
        try:
            return parse_text(text)
        except YamlError as err:
            yaml_fail(line, err, f"The YAML in '{path}'")

    @m.fn("read_all", 1, doc="read_all(path) -> list\nRead every document (--- separated) in a YAML file.")
    def read_all(line, path):
        text = read_file(line, path, "yaml.read_all")
        try:
            return parse_documents(text)
        except YamlError as err:
            yaml_fail(line, err, f"The YAML in '{path}'")

    @m.fn("write", 2, 3, doc="write(path, value, indent?) -> nil\nWrite a TB value to a file as YAML.")
    def write(line, path, value, indent=2):
        check_type(line, path, "text", "yaml.write", "path")
        if not path:
            fail(line, "yaml.write() needs a path, but got empty text.")
        indent = check_type(line, indent, "whole", "yaml.write", "indent")
        text = to_text_yaml(value, line, max(1, min(8, indent)))
        full = os.path.expanduser(path)
        try:
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    def read_file(line, path, fn):
        check_type(line, path, "text", fn, "path")
        if not path:
            fail(line, f"{fn}() needs a path, but got empty text.")
        full = os.path.expanduser(path)
        if os.path.isdir(full):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        try:
            from tblib.fs import read_text
            return read_text(full)
        except OSError as err:
            fail_os(line, err, path, "read")

    return m
