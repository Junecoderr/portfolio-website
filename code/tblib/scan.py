"""scan: YARA-like pattern rules over files and bytes, plus strings extraction."""

from __future__ import annotations

import os
import re

from tblib import _magic
from tblib._api import MANY, Module, check_type, fail, fail_os

MAX_MATCHES_PER_STRING = 1000
MAX_FILE = 256 * 1024 * 1024
WORD = rb"[A-Za-z0-9_]"

# =============================================================================
# Rule text parser (a YARA subset)
# =============================================================================

MODIFIERS = {"nocase", "wide", "ascii", "fullword", "private"}
UNSUPPORTED_MODIFIERS = {"xor", "base64", "base64wide"}


class RuleSyntax(Exception):
    pass


class Scanner:
    """A tiny cursor over rule text."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def skip(self):
        text, n = self.text, len(self.text)
        while self.pos < n:
            ch = text[self.pos]
            if ch.isspace():
                self.pos += 1
            elif text.startswith("//", self.pos):
                end = text.find("\n", self.pos)
                self.pos = n if end < 0 else end
            elif text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                if end < 0:
                    raise RuleSyntax("a /* comment is never closed")
                self.pos = end + 2
            else:
                break

    def peek(self, k=1) -> str:
        self.skip()
        return self.text[self.pos:self.pos + k]

    def at_end(self) -> bool:
        self.skip()
        return self.pos >= len(self.text)

    def line(self) -> int:
        return self.text.count("\n", 0, self.pos) + 1

    def expect(self, token: str):
        self.skip()
        if not self.text.startswith(token, self.pos):
            got = self.text[self.pos:self.pos + 12].split("\n")[0] or "the end"
            raise RuleSyntax(f"expected '{token}' but found '{got}' (line {self.line()})")
        self.pos += len(token)

    def word(self) -> str:
        self.skip()
        m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(self.text, self.pos)
        if not m:
            got = self.text[self.pos:self.pos + 12].split("\n")[0] or "the end"
            raise RuleSyntax(f"expected a name but found '{got}' (line {self.line()})")
        self.pos = m.end()
        return m.group(0)

    def try_word(self, w: str) -> bool:
        self.skip()
        if re.compile(re.escape(w) + r"(?![A-Za-z0-9_])").match(self.text, self.pos):
            self.pos += len(w)
            return True
        return False

    def quoted(self) -> str:
        self.expect('"')
        out = []
        text, n = self.text, len(self.text)
        while True:
            if self.pos >= n:
                raise RuleSyntax("a \"string\" is never closed")
            ch = text[self.pos]
            if ch == '"':
                self.pos += 1
                return "".join(out)
            if ch == "\\":
                nxt = text[self.pos + 1:self.pos + 2]
                if nxt == "x":
                    out.append(chr(int(text[self.pos + 2:self.pos + 4], 16)))
                    self.pos += 4
                    continue
                out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt))
                self.pos += 2
                continue
            out.append(ch)
            self.pos += 1

    def hex_body(self) -> str:
        self.expect("{")
        end = self.text.find("}", self.pos)
        if end < 0:
            raise RuleSyntax("a { hex string } is never closed")
        body = self.text[self.pos:end]
        self.pos = end + 1
        return body.strip()

    def regex_body(self):
        self.expect("/")
        out = []
        text, n = self.text, len(self.text)
        while True:
            if self.pos >= n:
                raise RuleSyntax("a /regex/ is never closed")
            ch = text[self.pos]
            if ch == "\\":
                out.append(text[self.pos:self.pos + 2])
                self.pos += 2
                continue
            if ch == "/":
                self.pos += 1
                break
            out.append(ch)
            self.pos += 1
        flags = ""
        while self.pos < n and text[self.pos] in "is":
            flags += text[self.pos]
            self.pos += 1
        return "".join(out), flags


def parse_rules(text: str) -> list:
    """Parse rule text into a list of rule maps (raises RuleSyntax)."""
    s = Scanner(text)
    rules = []
    while not s.at_end():
        while s.try_word("import"):
            s.quoted()
        while s.try_word("include"):
            s.quoted()
        if s.at_end():
            break
        private = s.try_word("private")
        s.try_word("global")
        if not s.try_word("rule"):
            got = s.text[s.pos:s.pos + 20].split("\n")[0]
            raise RuleSyntax(f"expected 'rule' but found '{got}' (line {s.line()})")
        name = s.word()
        tags = []
        if s.peek() == ":":
            s.expect(":")
            while s.peek() != "{":
                tags.append(s.word())
        s.expect("{")
        rule = {"name": name, "tags": tags, "meta": {}, "strings": [], "condition": "", "private": private}
        while True:
            if s.peek() == "}":
                s.expect("}")
                break
            section = s.word()
            s.expect(":")
            if section == "meta":
                while s.peek() not in ("}", "") and not _section_ahead(s):
                    key = s.word()
                    s.expect("=")
                    if s.peek() == '"':
                        rule["meta"][key] = s.quoted()
                    elif s.try_word("true"):
                        rule["meta"][key] = True
                    elif s.try_word("false"):
                        rule["meta"][key] = False
                    else:
                        m = re.compile(r"-?(0x[0-9A-Fa-f]+|\d+)").match(s.text, s.pos)
                        if not m:
                            raise RuleSyntax(f"meta '{key}' needs a \"text\", number or true/false (line {s.line()})")
                        s.pos = m.end()
                        rule["meta"][key] = int(m.group(0), 0)
            elif section == "strings":
                while s.peek() == "$":
                    rule["strings"].append(_parse_string(s))
            elif section == "condition":
                start = s.pos
                depth = 0
                while s.pos < len(s.text):
                    ch = s.text[s.pos]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    elif ch == "}" and depth <= 0:
                        break
                    s.pos += 1
                rule["condition"] = " ".join(s.text[start:s.pos].split())
            else:
                raise RuleSyntax(f"unknown section '{section}' in rule {name} (line {s.line()}); "
                                 "I know meta, strings and condition")
        if not rule["condition"]:
            raise RuleSyntax(f"rule {name} has no condition")
        rules.append(normalize_rule(rule))
    return rules


def _section_ahead(s: Scanner) -> bool:
    return bool(re.compile(r"(meta|strings|condition)\s*:").match(s.text, s.pos))


def _parse_string(s: Scanner) -> dict:
    s.expect("$")
    m = re.compile(r"[A-Za-z0-9_]*").match(s.text, s.pos)
    s.pos = m.end()
    name = "$" + m.group(0)
    s.expect("=")
    entry = {"name": name}
    ch = s.peek()
    if ch == '"':
        entry["kind"] = "text"
        entry["value"] = s.quoted()
    elif ch == "{":
        entry["kind"] = "hex"
        entry["value"] = s.hex_body()
    elif ch == "/":
        entry["kind"] = "regex"
        entry["value"], flags = s.regex_body()
        if "i" in flags:
            entry["nocase"] = True
        if "s" in flags:
            entry["dotall"] = True
    else:
        raise RuleSyntax(f"string {name} needs a \"text\", {{ hex }} or /regex/ value (line {s.line()})")
    while True:
        s.skip()
        m = re.compile(r"[a-z0-9]+").match(s.text, s.pos)
        if not m or m.group(0) not in MODIFIERS | UNSUPPORTED_MODIFIERS:
            break
        word = m.group(0)
        s.pos = m.end()
        if word in UNSUPPORTED_MODIFIERS:
            raise RuleSyntax(f"the '{word}' modifier on {name} isn't supported (TB's scan knows nocase, wide, ascii, fullword)")
        entry[word] = True
    return entry


# =============================================================================
# Rule maps (the TB-friendly form) and compilation
# =============================================================================

def normalize_rule(rule) -> dict:
    """Accept the text-parser output or a TB map; give back the canonical rule map."""
    if not isinstance(rule, dict):
        raise RuleSyntax("a rule must be a map or rule text")
    name = rule.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise RuleSyntax("every rule needs a \"name\" (letters, digits and _ only)")
    strings = rule.get("strings") or []
    out_strings = []
    if isinstance(strings, dict):
        items = []
        for key, value in strings.items():
            key = str(key)
            items.append((key if key.startswith("$") else "$" + key, value))
    elif isinstance(strings, list):
        items = []
        for entry in strings:
            if not isinstance(entry, dict) or "name" not in entry:
                raise RuleSyntax(f"rule {name}: each string needs a name")
            key = str(entry["name"])
            items.append((key if key.startswith("$") else "$" + key, entry))
    else:
        raise RuleSyntax(f"rule {name}: \"strings\" must be a map or list")
    seen = set()
    for key, value in items:
        if key in seen:
            raise RuleSyntax(f"rule {name}: the string {key} is defined twice")
        seen.add(key)
        if isinstance(value, str):
            entry = {"kind": "text", "value": value}
        elif isinstance(value, dict):
            entry = {}
            if "kind" in value and "value" in value:
                entry = {"kind": value["kind"], "value": value["value"]}
            elif "hex" in value:
                entry = {"kind": "hex", "value": value["hex"]}
            elif "regex" in value:
                entry = {"kind": "regex", "value": value["regex"]}
            elif "text" in value:
                entry = {"kind": "text", "value": value["text"]}
            else:
                raise RuleSyntax(f"rule {name}: string {key} needs \"text\", \"hex\" or \"regex\"")
            for flag in ("nocase", "wide", "ascii", "fullword", "dotall", "private"):
                if value.get(flag):
                    entry[flag] = True
        else:
            raise RuleSyntax(f"rule {name}: string {key} must be text or a map")
        if entry["kind"] not in ("text", "hex", "regex") or not isinstance(entry["value"], str):
            raise RuleSyntax(f"rule {name}: string {key} has a bad definition")
        entry["name"] = key
        out_strings.append(entry)
    condition = rule.get("condition") or ("any of them" if out_strings else "true")
    if not isinstance(condition, str):
        raise RuleSyntax(f"rule {name}: the condition must be text")
    meta = rule.get("meta") or {}
    if not isinstance(meta, dict):
        raise RuleSyntax(f"rule {name}: meta must be a map")
    tags = rule.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split()
    result = {"name": name, "tags": [str(t) for t in tags], "meta": dict(meta), "strings": out_strings,
              "condition": " ".join(condition.split())}
    if rule.get("private"):
        result["private"] = True
    compile_rule(result)  # validate now, so errors point at the rule
    return result


_HEX_TOKEN = re.compile(r"\s*(\[\s*(\d*)\s*(-?)\s*(\d*)\s*\]|\(|\)|\||\?\?|[0-9A-Fa-f?]{2}|~[0-9A-Fa-f]{2})")


def hex_to_regex(body: str, name: str) -> bytes:
    pos = 0
    out = []
    body = body.strip()
    if not body:
        raise RuleSyntax(f"string {name}: the hex string is empty")
    while pos < len(body):
        m = _HEX_TOKEN.match(body, pos)
        if not m:
            raise RuleSyntax(f"string {name}: I don't understand '{body[pos:pos + 8]}' in the hex string")
        pos = m.end()
        tok = m.group(1)
        if tok.startswith("["):
            lo, dash, hi = m.group(2), m.group(3), m.group(4)
            if not dash:
                if not lo:
                    raise RuleSyntax(f"string {name}: a jump needs a number like [4] or [2-8]")
                out.append(".{%d}" % int(lo))
            else:
                lo_n = int(lo) if lo else 0
                hi_n = int(hi) if hi else 64
                if hi_n < lo_n:
                    raise RuleSyntax(f"string {name}: the jump [{lo}-{hi}] is backwards")
                out.append(".{%d,%d}" % (lo_n, hi_n))
        elif tok == "(":
            out.append("(?:")
        elif tok == ")":
            out.append(")")
        elif tok == "|":
            out.append("|")
        elif tok == "??":
            out.append(".")
        elif tok.startswith("~"):
            out.append("[^\\x%s]" % tok[1:])
        elif "?" in tok:
            hi, lo = tok[0], tok[1]
            if hi == "?":
                values = [int(lo, 16) + 16 * k for k in range(16)]
            else:
                values = [int(hi, 16) * 16 + k for k in range(16)]
            out.append("[" + "".join("\\x%02x" % v for v in values) + "]")
        else:
            out.append("\\x" + tok.lower())
    return "".join(out).encode("latin-1")


def compile_string(entry: dict) -> list:
    """One string definition -> list of compiled byte regexes (ascii and/or wide)."""
    kind, value, name = entry["kind"], entry["value"], entry["name"]
    flags = re.DOTALL
    if entry.get("nocase"):
        flags |= re.IGNORECASE
    patterns = []
    try:
        if kind == "text":
            if value == "":
                raise RuleSyntax(f"string {name} is empty")
            raw = value.encode("utf-8", "surrogatepass")
            variants = []
            if entry.get("ascii") or not entry.get("wide"):
                variants.append(re.escape(raw))
            if entry.get("wide"):
                variants.append(b"".join(re.escape(bytes([b])) + b"\\x00" for b in raw))
            for pat in variants:
                if entry.get("fullword"):
                    pat = b"(?<!" + WORD + b")" + pat + b"(?!" + WORD + b")"
                patterns.append(re.compile(pat, flags))
        elif kind == "hex":
            patterns.append(re.compile(hex_to_regex(value, name), re.DOTALL))
        else:
            pat = value.encode("utf-8", "surrogatepass")
            if entry.get("fullword"):
                pat = b"(?<!" + WORD + b")(?:" + pat + b")(?!" + WORD + b")"
            patterns.append(re.compile(pat, flags))
            if entry.get("wide"):
                raise RuleSyntax(f"string {name}: 'wide' isn't supported on regex strings")
    except re.error as err:
        raise RuleSyntax(f"string {name} has a broken pattern: {err}")
    return patterns


# -- conditions ---------------------------------------------------------------

_COND_TOKEN = re.compile(
    r"\s*(?:(?P<num>0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)(?P<unit>KB|MB|GB)?"
    r"|(?P<op><=|>=|==|!=|<|>|\(|\)|\.\.|\[|\]|,|\*)"
    r"|(?P<str>[$#@!][A-Za-z0-9_]*\*?)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*))"
)


class Cond:
    """Compile a condition into a closure f(ctx) where ctx has .counts (name -> [(offset, length)]),
    .size, .data."""

    def __init__(self, text: str, names: list, rule: str):
        self.rule = rule
        self.names = names
        self.tokens = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            if text[pos:].isspace():
                break
            m = _COND_TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise RuleSyntax(f"rule {rule}: I don't understand '{text[pos:pos + 10]}' in the condition")
            pos = m.end()
            if m.group("num") is not None:
                n = m.group("num")
                value = int(n, 0) if not ("." in n) else float(n)
                unit = m.group("unit")
                value *= {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, None: 1}[unit]
                self.tokens.append(("num", value))
            elif m.group("str") is not None:
                self.tokens.append(("str", m.group("str")))
            elif m.group("op") is not None:
                self.tokens.append(("op", m.group("op")))
            else:
                self.tokens.append(("word", m.group("word")))
        self.i = 0
        self.fn = self.parse_or()
        if self.i < len(self.tokens):
            raise RuleSyntax(f"rule {rule}: unexpected '{self.tokens[self.i][1]}' in the condition")

    def peek(self, kind=None, value=None):
        if self.i >= len(self.tokens):
            return None
        k, v = self.tokens[self.i]
        if kind and k != kind:
            return None
        if value is not None and v != value:
            return None
        return v

    def take(self):
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def accept(self, kind, value):
        if self.peek(kind, value) is not None:
            self.i += 1
            return True
        return False

    def need(self, kind, value):
        if not self.accept(kind, value):
            found = self.tokens[self.i][1] if self.i < len(self.tokens) else "the end"
            raise RuleSyntax(f"rule {self.rule}: expected '{value}' in the condition but found '{found}'")

    def parse_or(self):
        left = self.parse_and()
        while self.accept("word", "or"):
            right = self.parse_and()
            left = (lambda a, b: lambda ctx: truth(a(ctx)) or truth(b(ctx)))(left, right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.accept("word", "and"):
            right = self.parse_not()
            left = (lambda a, b: lambda ctx: truth(a(ctx)) and truth(b(ctx)))(left, right)
        return left

    def parse_not(self):
        if self.accept("word", "not"):
            inner = self.parse_not()
            return lambda ctx: not truth(inner(ctx))
        return self.parse_cmp()

    def parse_cmp(self):
        left = self.parse_primary()
        op = self.peek("op")
        if op in ("<", ">", "<=", ">=", "==", "!="):
            self.take()
            right = self.parse_primary()
            ops = {"<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b,
                   ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}[op]
            return (lambda a, b, f: lambda ctx: f(number(a(ctx)), number(b(ctx))))(left, right, ops)
        return left

    def string_set(self):
        """After 'of': 'them' or '( $a, $b* )' -> list of string names."""
        if self.accept("word", "them"):
            if not self.names:
                raise RuleSyntax(f"rule {self.rule}: 'of them' needs a strings: section")
            return list(self.names)
        self.need("op", "(")
        chosen = []
        while True:
            tok = self.peek("str")
            if tok is None or not tok.startswith("$"):
                raise RuleSyntax(f"rule {self.rule}: 'of (...)' needs string names like $a or $a*")
            self.take()
            if tok.endswith("*"):
                prefix = tok[:-1]
                found = [n for n in self.names if n.startswith(prefix)]
                if not found:
                    raise RuleSyntax(f"rule {self.rule}: no string starts with {prefix}")
                chosen.extend(found)
            else:
                self.check_name(tok)
                chosen.append(tok)
            if not self.accept("op", ","):
                break
        self.need("op", ")")
        return chosen

    def check_name(self, name):
        if name not in self.names:
            raise RuleSyntax(f"rule {self.rule}: the condition uses {name} but no such string is defined")

    def parse_primary(self):
        if self.accept("op", "("):
            inner = self.parse_or()
            self.need("op", ")")
            return inner
        tok = self.peek()
        if tok is None:
            raise RuleSyntax(f"rule {self.rule}: the condition ends too early")
        kind, value = self.take()
        if kind == "num":
            if self.accept("word", "of"):
                names = self.string_set()
                return (lambda n, ns: lambda ctx: sum(1 for s in ns if ctx.counts.get(s)) >= n)(value, names)
            return lambda ctx, v=value: v
        if kind == "str":
            if value == "#" or value == "$" or value == "@":
                raise RuleSyntax(f"rule {self.rule}: a string name is missing after '{value}'")
            sigil, name = value[0], "$" + value[1:]
            if value.endswith("*"):
                raise RuleSyntax(f"rule {self.rule}: {value} can only be used inside 'of (...)'")
            self.check_name(name)
            if sigil == "$":
                if self.accept("word", "at"):
                    where = self.parse_primary()
                    return (lambda n, w: lambda ctx: any(o == number(w(ctx)) for o, _ in ctx.counts.get(n, [])))(name, where)
                if self.accept("word", "in"):
                    self.need("op", "(")
                    lo = self.parse_primary()
                    self.need("op", "..")
                    hi = self.parse_primary()
                    self.need("op", ")")
                    return (lambda n, a, b: lambda ctx: any(
                        number(a(ctx)) <= o <= number(b(ctx)) for o, _ in ctx.counts.get(n, [])))(name, lo, hi)
                return lambda ctx, n=name: bool(ctx.counts.get(n))
            if sigil == "#":
                if self.accept("word", "in"):
                    self.need("op", "(")
                    lo = self.parse_primary()
                    self.need("op", "..")
                    hi = self.parse_primary()
                    self.need("op", ")")
                    return (lambda n, a, b: lambda ctx: sum(
                        1 for o, _ in ctx.counts.get(n, []) if number(a(ctx)) <= o <= number(b(ctx))))(name, lo, hi)
                return lambda ctx, n=name: len(ctx.counts.get(n, []))
            if sigil == "@":
                index = None
                if self.accept("op", "["):
                    index = self.parse_primary()
                    self.need("op", "]")

                def offset(ctx, n=name, ix=index):
                    hits = ctx.counts.get(n, [])
                    k = 1 if ix is None else number(ix(ctx))
                    if k < 1 or k > len(hits):
                        return -1
                    return hits[k - 1][0]
                return offset
            if sigil == "!":
                index = None
                if self.accept("op", "["):
                    index = self.parse_primary()
                    self.need("op", "]")

                def length(ctx, n=name, ix=index):
                    hits = ctx.counts.get(n, [])
                    k = 1 if ix is None else number(ix(ctx))
                    if k < 1 or k > len(hits):
                        return -1
                    return hits[k - 1][1]
                return length
        if kind == "word":
            if value == "true":
                return lambda ctx: True
            if value == "false":
                return lambda ctx: False
            if value == "filesize":
                return lambda ctx: ctx.size
            if value in ("any", "all", "none"):
                self.need("word", "of")
                names = self.string_set()
                if value == "any":
                    return lambda ctx, ns=names: any(ctx.counts.get(s) for s in ns)
                if value == "none":
                    return lambda ctx, ns=names: not any(ctx.counts.get(s) for s in ns)
                return lambda ctx, ns=names: all(ctx.counts.get(s) for s in ns)
            if value in ("uint8", "uint16", "uint32", "int8", "int16", "int32", "uint16be", "uint32be",
                         "int16be", "int32be"):
                self.need("op", "(")
                where = self.parse_or()
                self.need("op", ")")
                bits = int(re.search(r"\d+", value).group(0))
                signed = value.startswith("int")
                big = value.endswith("be")

                def read(ctx, w=where, n=bits // 8, s=signed, b=big):
                    off = number(w(ctx))
                    chunk = ctx.data[off:off + n]
                    if off < 0 or len(chunk) < n:
                        return -1
                    return int.from_bytes(chunk, "big" if b else "little", signed=s)
                return read
            if value == "entrypoint":
                raise RuleSyntax(f"rule {self.rule}: 'entrypoint' isn't supported; use the pe or elf library "
                                 "and uint32() instead")
            if value in ("for", "them", "of", "at", "in", "and", "or", "not"):
                raise RuleSyntax(f"rule {self.rule}: '{value}' is in the wrong place in the condition")
            if value.startswith("pe") or value.startswith("math") or value.startswith("hash"):
                raise RuleSyntax(f"rule {self.rule}: YARA modules like '{value}' aren't supported")
            raise RuleSyntax(f"rule {self.rule}: I don't know '{value}' in the condition")
        raise RuleSyntax(f"rule {self.rule}: unexpected '{value}' in the condition")


def truth(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    return bool(v)


def number(v):
    if isinstance(v, bool):
        return 1 if v else 0
    return v


class Ctx:
    __slots__ = ("counts", "size", "data")

    def __init__(self, counts, data):
        self.counts = counts
        self.size = len(data)
        self.data = data


_COMPILED: dict = {}


def compile_rule(rule: dict):
    key = (rule["name"], repr(rule["strings"]), rule["condition"])
    cached = _COMPILED.get(key)
    if cached:
        return cached
    strings = [(entry, compile_string(entry)) for entry in rule["strings"]]
    cond = Cond(rule["condition"], [e["name"] for e in rule["strings"]], rule["name"])
    if len(_COMPILED) > 500:
        _COMPILED.clear()
    _COMPILED[key] = (strings, cond)
    return _COMPILED[key]


def match_rule(rule: dict, data: bytes):
    """Run one rule over bytes. Returns a match map or None."""
    strings, cond = compile_rule(rule)
    counts = {}
    for entry, patterns in strings:
        hits = []
        for pat in patterns:
            for m in pat.finditer(data):
                hits.append((m.start(), m.end() - m.start()))
                if len(hits) >= MAX_MATCHES_PER_STRING:
                    break
        hits.sort()
        counts[entry["name"]] = hits
    if not truth(cond.fn(Ctx(counts, data))):
        return None
    found = []
    for entry, _ in strings:
        if entry.get("private"):
            continue
        for offset, length in counts[entry["name"]][:50]:
            piece = data[offset:offset + length]
            found.append({"name": entry["name"], "offset": offset, "length": length,
                          "data": _show(piece)})
    found.sort(key=lambda h: (h["offset"], h["name"]))
    return {"rule": rule["name"], "tags": list(rule["tags"]), "meta": dict(rule["meta"]), "strings": found,
            "count": sum(len(v) for v in counts.values())}


def _show(piece: bytes) -> str:
    """Matched bytes as readable text: plain if printable, else hex."""
    if piece and all(0x20 <= b < 0x7F for b in piece):
        return piece.decode("ascii")
    if len(piece) >= 2 and len(piece) % 2 == 0 and all(piece[i] == 0 for i in range(1, len(piece), 2)) \
            and all(0x20 <= piece[i] < 0x7F for i in range(0, len(piece), 2)):
        return piece.decode("utf-16-le")
    return piece.hex()


def scan_bytes(rules: list, data: bytes) -> list:
    out = []
    for rule in rules:
        if rule.get("private"):
            continue
        m = match_rule(rule, data)
        if m:
            out.append(m)
    return out


# =============================================================================
# Strings extraction
# =============================================================================

def extract_strings(data: bytes, minimum: int = 4, limit: int | None = None) -> list:
    """Printable ASCII runs and UTF-16LE runs of at least `minimum` characters."""
    out = []
    ascii_re = re.compile(rb"[\x20-\x7e]{%d,}" % minimum)
    wide_re = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % minimum)
    for m in ascii_re.finditer(data):
        out.append({"text": m.group(0).decode("ascii"), "offset": m.start(), "encoding": "ascii"})
    for m in wide_re.finditer(data):
        out.append({"text": m.group(0).decode("utf-16-le"), "offset": m.start(), "encoding": "utf-16"})
    out.sort(key=lambda s: s["offset"])
    if limit is not None:
        out = out[:limit]
    return out


# =============================================================================
# The module
# =============================================================================

def parse_rules_or_fail(line, text, fn):
    try:
        rules = parse_rules(text)
    except RuleSyntax as err:
        fail(line, f"{fn}() couldn't read that rule: {err}.")
    if not rules:
        fail(line, f"{fn}() found no rules in that text. A rule looks like: "
                   'rule Name { strings: $a = "text" condition: $a }')
    return rules


def rules_from(line, rules, fn) -> list:
    """Whatever TB passed as rules -> list of canonical rule maps."""
    if isinstance(rules, str):
        return parse_rules_or_fail(line, rules, fn)
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list) or not rules:
        fail(line, f"{fn}() needs rules: rule text, a rule map or a list of them.")
    out = []
    for rule in rules:
        if isinstance(rule, str):
            out.extend(parse_rules_or_fail(line, rule, fn))
            continue
        try:
            out.append(normalize_rule(rule))
        except RuleSyntax as err:
            fail(line, f"{fn}() couldn't use that rule: {err}.")
    return out


def load(api):
    m = Module(
        "scan",
        "Look for patterns in files and bytes with YARA-like rules: text (with nocase, wide and fullword), "
        "hex byte patterns with ?? wildcards and [n-m] jumps, and /regular expressions/, combined with a "
        "condition ($a and ($b or $c), 2 of them, #a > 3, filesize < 1MB, uint16(0) == 0x5A4D). Rules can "
        "be YARA-style text or plain TB maps. Also pulls readable strings (ASCII and UTF-16) out of binary data.",
    )
    m.value("example", 'rule SuspiciousDownloader {\n  meta:\n    author = "soc"\n    severity = "high"\n'
                       '  strings:\n    $a = "DownloadString" nocase\n    $b = { 4D 5A ?? 00 }\n'
                       '    $c = /https?:\\/\\/[0-9.]+\\//\n  condition:\n    $a and ($b or $c)\n}',
            doc="A rule in the text form scan understands, to copy from.")

    @m.fn("rule", 1, doc="rule(text) -> map\nParse one YARA-style rule into a rule map: {\"name\", \"tags\", "
                         "\"meta\", \"strings\" (list of {\"name\", \"kind\", \"value\", flags}), \"condition\"}. "
                         "Fails with a plain explanation if the rule is broken. For several rules use scan.rules().")
    def rule(line, text):
        check_type(line, text, "text", "scan.rule", "text")
        rules = parse_rules_or_fail(line, text, "scan.rule")
        if len(rules) > 1:
            fail(line, f"scan.rule() got {len(rules)} rules; use scan.rules() to parse several at once.")
        return rules[0]

    @m.fn("rules", 1, doc="rules(text) -> list\nParse rule text holding one or more rules into a list of rule maps.")
    def rules(line, text):
        check_type(line, text, "text", "scan.rules", "text")
        return parse_rules_or_fail(line, text, "scan.rules")

    @m.fn("load", 1, doc="load(path) -> list\nRead a .yar/.yara rules file and parse every rule in it.")
    def load_rules(line, path):
        check_type(line, path, "text", "scan.load", "path")
        try:
            with open(os.path.expanduser(path), "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError as err:
            fail_os(line, err, path, "read")
        return parse_rules_or_fail(line, text, "scan.load")

    @m.fn("check", 1, doc="check(rules) -> list\nValidate rules (text, a map or a list) without scanning anything; "
                          "gives back the canonical rule maps or fails with what is wrong.")
    def check(line, rules):
        return rules_from(line, rules, "scan.check")

    @m.fn("bytes", 2, doc="bytes(data, rules) -> list\nRun rules over bytes (or text). Gives back one map per rule "
                          "that matched: {\"rule\", \"tags\", \"meta\", \"count\", \"strings\": [{\"name\", "
                          "\"offset\", \"length\", \"data\"}]}. Empty list means no match.")
    def scan_data(line, data, rules):
        raw = _magic.to_bytes(line, data, "scan.bytes")
        return scan_bytes(rules_from(line, rules, "scan.bytes"), raw)

    @m.fn("file", 2, doc="file(path, rules) -> list\nRun rules over a file's bytes. Same result as scan.bytes(), "
                         "with \"path\" added to every match.")
    def scan_file(line, path, rules):
        rule_list = rules_from(line, rules, "scan.file")
        raw = _magic.read_file(line, path, "scan.file", MAX_FILE)
        found = scan_bytes(rule_list, raw)
        for match in found:
            match["path"] = path
        return found

    @m.fn("folder", 2, 3, doc="folder(dir, rules, options?) -> list\nScan every file in a folder (and its "
                              "sub-folders unless options {\"recursive\": false}); skips files bigger than "
                              "options \"max_size\" (default 32 MB) and ones it can't read. Gives back the "
                              "matches from every file, each with \"path\".")
    def scan_folder(line, folder, rules, options=None):
        check_type(line, folder, "text", "scan.folder", "dir")
        rule_list = rules_from(line, rules, "scan.folder")
        opts = options or {}
        check_type(line, opts, "map", "scan.folder", "options")
        recursive = opts.get("recursive", True) is not False
        max_size = opts.get("max_size", 32 * 1024 * 1024)
        max_size = check_type(line, max_size, "whole", "scan.folder", "max_size")
        full = os.path.expanduser(folder)
        if not os.path.isdir(full):
            if os.path.isfile(full):
                fail(line, f"'{folder}' is a file, not a folder; use scan.file().", path=folder)
            fail(line, f"I couldn't find a folder called '{folder}'.", path=folder)
        found = []
        skipped = 0
        for root, dirs, files in os.walk(full):
            dirs.sort()
            if not recursive:
                dirs[:] = []
            for name in sorted(files):
                path = os.path.join(root, name)
                try:
                    if os.path.islink(path) or os.path.getsize(path) > max_size:
                        skipped += 1
                        continue
                    with open(path, "rb") as f:
                        raw = f.read()
                except OSError:
                    skipped += 1
                    continue
                for match in scan_bytes(rule_list, raw):
                    match["path"] = path
                    found.append(match)
        return found

    @m.fn("strings", 1, 3, doc="strings(data, min?, limit?) -> list\nReadable strings in bytes, text or a file path: "
                               "ASCII runs and UTF-16 (Windows wide) runs of at least min characters (default 4), "
                               "as {\"text\", \"offset\", \"encoding\"} maps sorted by offset. limit caps the count.")
    def strings(line, data, minimum=4, limit=None):
        minimum = check_type(line, minimum, "whole", "scan.strings", "min")
        if minimum < 1:
            fail(line, "scan.strings() needs min to be at least 1.")
        if limit is not None:
            limit = check_type(line, limit, "whole", "scan.strings", "limit")
        if isinstance(data, str) and "\n" not in data and os.path.isfile(os.path.expanduser(data)):
            raw = _magic.read_file(line, data, "scan.strings", MAX_FILE)
        else:
            raw = _magic.to_bytes(line, data, "scan.strings")
        return extract_strings(raw, minimum, limit)

    @m.fn("file_type", 1, doc="file_type(path) -> map\nWhat a file really is, from its first bytes (not its name): "
                              "{\"type\" (pe, elf, macho, pdf, zip, docx, png, pcap, evtx, script, text, unknown...), "
                              "\"description\", \"mime\", \"extension\", \"executable\", \"size\"}. Same as "
                              "binary.file_type().")
    def file_type(line, path):
        return _magic.file_type(line, path)

    @m.fn("entropy", 1, doc="entropy(data) -> number\nShannon entropy of bytes, text or a file path: 0 (all the same "
                            "byte) to 8 (random). Above about 7.2 usually means compressed or encrypted.")
    def entropy(line, data):
        if isinstance(data, str) and "\n" not in data and os.path.isfile(os.path.expanduser(data)):
            return _magic.entropy(_magic.read_file(line, data, "scan.entropy", MAX_FILE))
        return _magic.entropy(_magic.to_bytes(line, data, "scan.entropy"))

    @m.fn("interesting", 1, 2, doc="interesting(strings, limit?) -> map\nSort extracted strings (from scan.strings) "
                                   "into what analysts look for: {\"urls\", \"ips\", \"emails\", \"paths\", "
                                   "\"registry\", \"dlls\", \"commands\"} (lists of unique text).")
    def interesting(line, items, limit=50):
        check_type(line, items, "list", "scan.interesting", "strings")
        limit = check_type(line, limit, "whole", "scan.interesting", "limit")
        texts = []
        for item in items:
            if isinstance(item, dict):
                texts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                texts.append(item)
        buckets = {"urls": [], "ips": [], "emails": [], "paths": [], "registry": [], "dlls": [], "commands": []}
        seen = set()

        def add(bucket, value):
            key = (bucket, value.lower())
            if key not in seen and len(buckets[bucket]) < limit:
                seen.add(key)
                buckets[bucket].append(value)

        for text in texts:
            for url in re.findall(r"(?i)\b(?:https?|ftp|hxxps?)://[^\s\"'<>]+", text):
                add("urls", url)
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
                if all(int(p) < 256 for p in ip.split(".")):
                    add("ips", ip)
            for mail in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
                add("emails", mail)
            for path in re.findall(r"(?i)\b[A-Z]:\\[^\s\"'<>|*?]+|(?<![\w.])/(?:usr|etc|tmp|var|home|bin|opt|dev|proc)/[^\s\"'<>|]*", text):
                add("paths", path)
            for key in re.findall(r"(?i)\b(?:HKEY_[A-Z_]+|HKLM|HKCU|HKCR|HKU)\\[^\s\"'<>|]*", text):
                add("registry", key)
            for dll in re.findall(r"(?i)\b[A-Z0-9_.-]+\.(?:dll|sys|ocx|drv)\b", text):
                add("dlls", dll)
            if re.search(r"(?i)\b(?:powershell|cmd\.exe|/c |wscript|cscript|rundll32|regsvr32|mshta|certutil|"
                         r"bitsadmin|schtasks|net user|net localgroup|whoami|curl |wget |chmod \+x|/bin/sh|bash -c|"
                         r"-encodedcommand|-enc |IEX|Invoke-Expression|DownloadString|vssadmin|bcdedit|wevtutil)\b", text):
                add("commands", text)
        return buckets

    return m
