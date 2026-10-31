"""scan: YARA-like pattern rules over files and bytes, plus strings extraction."""

from __future__ import annotations

import os
import re

from tblib import _magic
from tblib._api import MANY, Module, check_type, fail, fail_os
from tblib._yara import (MODULES, RE_SCAN_LIMIT, ChainSearch, Cond, Ctx, RuleSyntax, XorSearch, base64_variants,
                         check_backtracking, find_all, truth, yara_regex)

# shortcut: YARA allows 1,000,000 matches per string; each match here costs a Python-level search
# call and a stored tuple, so dense data with many strings would be slow. Raise with a counting-only
# pass (no offsets kept past 1,000) if #a > 1000 conditions turn up in real rules.
MAX_MATCHES_PER_STRING = 1000
MAX_FILE = 256 * 1024 * 1024
WORD = rb"[A-Za-z0-9]"  # YARA's fullword: delimited by non-alphanumeric characters
WIDE_WORD = rb"[A-Za-z0-9]\x00"

# =============================================================================
# Rule text parser (a YARA subset)
# =============================================================================

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
                    digits = text[self.pos + 2:self.pos + 4]
                    if not re.fullmatch(r"[0-9A-Fa-f]{2}", digits):
                        raise RuleSyntax(f"'\\x{digits}' needs two hex digits, like \\x41 (line {self.line()})")
                    out.append(chr(int(digits, 16)))
                    self.pos += 4
                    continue
                if nxt not in TEXT_ESCAPES:
                    raise RuleSyntax(f"'\\{nxt}' isn't an escape YARA knows (use \\\\ for a backslash; the others "
                                     f"are \\\" \\n \\r \\t \\xNN) (line {self.line()})")
                out.append(TEXT_ESCAPES[nxt])
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


MODIFIERS = {"nocase", "wide", "ascii", "fullword", "private", "xor", "base64", "base64wide"}
TEXT_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def parse_rules(text: str, known=()) -> list:
    """Parse rule text into a list of rule maps (raises RuleSyntax). known: names of rules
    defined earlier (a condition may use them)."""
    s = Scanner(text)
    rules = []
    known = list(known)
    while not s.at_end():
        if s.try_word("import"):
            line = s.line()
            module = s.quoted()
            if module not in MODULES:
                raise RuleSyntax(f'import "{module}" (line {line}): TB\'s scan supports the YARA modules '
                                 '"pe", "elf" and "math"')
            continue
        if s.try_word("include"):
            line = s.line()
            raise RuleSyntax(f'include "{s.quoted()}" (line {line}) isn\'t supported: load each file with '
                             "scan.load() and join the lists")
        start_line = s.line()
        private = is_global = False
        while True:
            if s.try_word("private"):
                private = True
            elif s.try_word("global"):
                is_global = True
            else:
                break
        if not s.try_word("rule"):
            got = s.text[s.pos:s.pos + 20].split("\n")[0]
            raise RuleSyntax(f"expected 'rule' but found '{got}' (line {s.line()})")
        name = s.word()
        if name in known:
            raise RuleSyntax(f"rule {name} is defined twice (line {start_line})")
        tags = []
        if s.peek() == ":":
            s.expect(":")
            while s.peek() != "{":
                tags.append(s.word())
        s.expect("{")
        rule = {"name": name, "tags": tags, "meta": {}, "strings": [], "condition": "", "private": private,
                "global": is_global}
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
                    rule["strings"].append(_parse_string(s, rule["strings"]))
            elif section == "condition":
                s.skip()
                rule["_line"] = s.line()
                start = s.pos
                s.pos = _condition_end(s)
                rule["condition"] = s.text[start:s.pos]
            else:
                raise RuleSyntax(f"unknown section '{section}' in rule {name} (line {s.line()}); "
                                 "I know meta, strings and condition")
        if not rule["condition"].strip():
            raise RuleSyntax(f"rule {name} has no condition (line {start_line})")
        rules.append(normalize_rule(rule, known))
        known.append(name)
    return rules


def _condition_end(s: Scanner) -> int:
    """Where a condition stops: the rule's closing }, skipping "text", /regex/ and comments."""
    text, pos, depth = s.text, s.pos, 0
    while pos < len(text):
        ch = text[pos]
        if ch == '"':
            m = re.compile(r'"(?:[^"\\\n]|\\.)*"').match(text, pos)
            if not m:
                raise RuleSyntax(f"a \"text\" in the condition is never closed (line {text.count(chr(10), 0, pos) + 1})")
            pos = m.end()
            continue
        if text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = len(text) if end < 0 else end
            continue
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end < 0:
                raise RuleSyntax("a /* comment is never closed")
            pos = end + 2
            continue
        if ch == "/":
            m = re.compile(r"/(?:[^/\\\n]|\\.)+/[is]*").match(text, pos)
            if m:
                pos = m.end()
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "}" and depth <= 0:
            return pos
        pos += 1
    raise RuleSyntax("a rule's { ... } is never closed")


def _section_ahead(s: Scanner) -> bool:
    return bool(re.compile(r"(meta|strings|condition)\s*:").match(s.text, s.pos))


def _parse_string(s: Scanner, existing: list) -> dict:
    line = s.line()
    s.expect("$")
    m = re.compile(r"[A-Za-z0-9_]*").match(s.text, s.pos)
    s.pos = m.end()
    name = "$" + m.group(0)
    entry = {"name": name, "_line": line}
    if name == "$":  # anonymous: only reachable through them, $* and for-of
        taken = {e["name"] for e in existing}
        n = len(existing) + 1
        while f"$anonymous{n}" in taken:
            n += 1
        entry["name"], entry["anonymous"] = f"$anonymous{n}", True
    s.expect("=")
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
        raise RuleSyntax(f"string {name} needs a \"text\", {{ hex }} or /regex/ value (line {line})")
    seen = set()
    while True:
        s.skip()
        m = re.compile(r"[a-z0-9]+(?![A-Za-z0-9_])").match(s.text, s.pos)
        if not m or m.group(0) not in MODIFIERS:
            break
        word = m.group(0)
        if word in seen:
            raise RuleSyntax(f"string {name} has the '{word}' modifier twice (line {s.line()})")
        seen.add(word)
        s.pos = m.end()
        value = True
        if word == "xor":
            value = [0, 255]
            if s.text.startswith("(", s.pos):
                m = re.compile(r"\(\s*(0x[0-9A-Fa-f]+|\d+)\s*(?:-\s*(0x[0-9A-Fa-f]+|\d+)\s*)?\)").match(s.text, s.pos)
                if not m:
                    raise RuleSyntax(f"string {name}: xor takes a key or a range, like xor(0x20) or "
                                     f"xor(0x01-0xff) (line {s.line()})")
                lo = int(m.group(1), 0)
                hi = int(m.group(2), 0) if m.group(2) else lo
                s.pos = m.end()
                value = [lo, hi]
        elif word in ("base64", "base64wide") and s.text.startswith("(", s.pos):
            s.pos += 1
            value = s.quoted()
            s.expect(")")
        entry[word] = value
    return entry


# =============================================================================
# Rule maps (the TB-friendly form) and compilation
# =============================================================================

def normalize_rule(rule, known=()) -> dict:
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
            for flag in ("nocase", "wide", "ascii", "fullword", "dotall", "private", "anonymous", "xor", "base64",
                         "base64wide"):
                if value.get(flag):
                    entry[flag] = value[flag]
        else:
            raise RuleSyntax(f"rule {name}: string {key} must be text or a map")
        if entry["kind"] not in ("text", "hex", "regex") or not isinstance(entry["value"], str):
            raise RuleSyntax(f"rule {name}: string {key} has a bad definition")
        entry["name"] = key
        try:
            compile_string(entry)  # validate now, so errors point at the string
        except RuleSyntax as err:
            line = value.get("_line") if isinstance(value, dict) else None
            raise RuleSyntax(f"rule {name}: {err}" + (f" (line {line})" if line else ""))
        out_strings.append(entry)
    raw_condition = rule.get("condition") or ("any of them" if out_strings else "true")
    if not isinstance(raw_condition, str):
        raise RuleSyntax(f"rule {name}: the condition must be text")
    meta = rule.get("meta") or {}
    if not isinstance(meta, dict):
        raise RuleSyntax(f"rule {name}: meta must be a map")
    tags = rule.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split()
    result = {"name": name, "tags": [str(t) for t in tags], "meta": dict(meta), "strings": out_strings,
              "condition": " ".join(raw_condition.split())}
    for flag in ("private", "global"):
        if rule.get(flag):
            result[flag] = True
    # Compile from the raw text first so a mistake is reported with its line number.
    Cond(raw_condition, [e["name"] for e in out_strings], name, known, rule.get("_line"))
    compile_rule(result, known)
    return result


_HEX_TOKEN = re.compile(r"\s*(\[\s*(\d*)\s*(-?)\s*(\d*)\s*\]|\(|\)|\||\?\?|[0-9A-Fa-f?]{2}|~[0-9A-Fa-f]{2})")


LONG_JUMP = 200  # jumps wider than this (or unbounded) split a hex string into chained pieces


def hex_to_regex(body: str, name: str) -> list:
    """A hex string -> [(regex bytes, min gap, max gap or None)]: one piece, or several when
    long jumps at the top level separate them (the gaps say how far apart they may be)."""
    pos = 0
    out = []
    pieces = []
    depth = 0
    body = re.sub(r"//[^\n]*|/\*.*?\*/", " ", body, flags=re.S).strip()
    if not body:
        raise RuleSyntax(f"string {name}: the hex string is empty")
    while pos < len(body):
        m = _HEX_TOKEN.match(body, pos)
        if not m:
            raise RuleSyntax(f"string {name}: I don't understand '{body[pos:pos + 8].strip()}' in the hex string")
        pos = m.end()
        tok = m.group(1)
        if tok.startswith("["):
            lo, dash, hi = m.group(2), m.group(3), m.group(4)
            if not dash and not lo:
                raise RuleSyntax(f"string {name}: a jump needs a number like [4] or [2-8]")
            if len(lo) > 12 or len(hi) > 12:
                raise RuleSyntax(f"string {name}: the jump {tok} is far too large")
            lo_n = int(lo) if lo else 0
            if not dash:
                hi = lo
            if hi and int(hi) < lo_n:
                raise RuleSyntax(f"string {name}: the jump [{lo}-{hi}] is backwards")
            hi_n = int(hi) if hi else None  # [n-] and [-] have no upper limit in YARA
            if depth == 0 and (hi_n is None or hi_n - lo_n > LONG_JUMP or lo_n > RE_SCAN_LIMIT):
                if not out:
                    raise RuleSyntax(f"string {name}: a hex string can't start with a jump")
                pieces.append(("".join(out).encode("latin-1"), lo_n, hi_n))
                out = []
                continue
            if not dash:
                out.append(".{%d}" % lo_n)
            else:  # inside ( | ) groups an open-ended jump still stops at RE_SCAN_LIMIT
                out.append(".{%d,%d}?" % (lo_n, max(lo_n, RE_SCAN_LIMIT) if hi_n is None else hi_n))
        elif tok == "(":
            depth += 1
            out.append("(?:")
        elif tok == ")":
            depth -= 1
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
    if not out:
        raise RuleSyntax(f"string {name}: a hex string can't end with a jump")
    # each piece carries the gap before the next one
    gaps = [(0, 0)] + [(lo, hi) for _, lo, hi in pieces]
    regexes = [p for p, _, _ in pieces] + ["".join(out).encode("latin-1")]
    return [(r, lo, hi) for r, (lo, hi) in zip(regexes, gaps)]


def _check_modifiers(entry: dict):
    """The modifier combinations YARA itself rejects."""
    kind, name = entry["kind"], entry["name"]
    used = {k for k in ("nocase", "wide", "ascii", "fullword", "xor", "base64", "base64wide") if entry.get(k)}
    if kind == "hex" and used:
        raise RuleSyntax(f"string {name}: hex strings only take 'private', not {', '.join(sorted(used))}")
    if kind == "regex" and used & {"xor", "base64", "base64wide"}:
        raise RuleSyntax(f"string {name}: {', '.join(sorted(used & {'xor', 'base64', 'base64wide'}))} only "
                         "works on \"text\" strings, not regexes")
    if "xor" in used and "nocase" in used:
        raise RuleSyntax(f"string {name}: YARA doesn't allow 'xor' together with 'nocase'")
    b64 = used & {"base64", "base64wide"}
    if b64 and used & {"nocase", "xor", "fullword"}:
        raise RuleSyntax(f"string {name}: {' and '.join(sorted(b64))} can't be combined with "
                         f"{', '.join(sorted(used & {'nocase', 'xor', 'fullword'}))}")
    xor = entry.get("xor")
    if xor:
        if xor is True:
            entry["xor"] = xor = [0, 255]
        if isinstance(xor, int) and not isinstance(xor, bool):
            entry["xor"] = xor = [xor, xor]
        if not (isinstance(xor, list) and len(xor) == 2 and all(isinstance(k, int) for k in xor)
                and 0 <= xor[0] <= xor[1] <= 255):
            raise RuleSyntax(f"string {name}: the xor keys must be a range between 0 and 255, low to high")
    for word in b64:
        alphabet = entry[word]
        if alphabet is not True and (not isinstance(alphabet, str) or len(alphabet.encode("latin-1", "replace")) != 64):
            raise RuleSyntax(f"string {name}: a {word} alphabet must be exactly 64 characters")


def compile_string(entry: dict) -> list:
    """One string definition -> list of compiled byte regexes."""
    kind, value, name = entry["kind"], entry["value"], entry["name"]
    _check_modifiers(entry)
    flags = re.IGNORECASE if entry.get("nocase") else 0
    patterns = []
    try:
        if kind == "text":
            if value == "":
                raise RuleSyntax(f"string {name} is empty")
            raw = value.encode("utf-8", "surrogatepass")
            groups = []  # (variants, wide?)
            for word, wide in (("base64", False), ("base64wide", True)):
                if entry.get(word):
                    alphabet = None if entry[word] is True else entry[word].encode("latin-1")
                    found = base64_variants(raw, alphabet)
                    groups.append(([_widen(v) for v in found] if wide else found, wide))
            if not groups:
                if entry.get("ascii") or not entry.get("wide"):
                    groups.append(([raw], False))
                if entry.get("wide"):
                    groups.append(([_widen(raw)], True))
            xor = entry.get("xor")
            for variants, wide in groups:
                base = variants[0]
                if xor:
                    variants = [bytes(b ^ k for b in v) for v in variants for k in range(xor[0], xor[1] + 1)]
                unique = list(dict.fromkeys(variants))
                pat = b"|".join(re.escape(v) for v in unique)
                if entry.get("fullword"):
                    edge = WIDE_WORD if wide else WORD
                    pat = b"(?<!" + edge + b")(?:" + pat + b")(?!" + edge + b")"
                compiled = re.compile(pat, flags | re.DOTALL)
                patterns.append(XorSearch(compiled, base) if xor else compiled)
        elif kind == "hex":
            pieces = hex_to_regex(value, name)
            if len(pieces) == 1:
                patterns.append(re.compile(pieces[0][0], re.DOTALL))
            else:
                patterns.append(ChainSearch(pieces))
        else:
            forms = []
            if entry.get("ascii") or not entry.get("wide"):
                forms.append((yara_regex(value, False, name), False))
            if entry.get("wide"):
                forms.append((yara_regex(value, True, name), True))
            for text, wide in forms:
                pat = text.encode("utf-8", "surrogatepass")
                if entry.get("fullword"):
                    edge = WIDE_WORD if wide else WORD
                    pat = b"(?<!" + edge + b")(?:" + pat + b")(?!" + edge + b")"
                # YARA's . matches any byte but \n, unless the regex ends in /s
                rflags = flags | (re.DOTALL if entry.get("dotall") else 0)
                check_backtracking(pat, rflags, name)
                patterns.append(re.compile(pat, rflags))
    except re.error as err:
        raise RuleSyntax(f"string {name} has a broken pattern: {err}")
    except (OverflowError, RecursionError) as err:  # e.g. {0,99999999999} inside a hex ( | ) group
        raise RuleSyntax(f"string {name} has a pattern too large to compile ({err})")
    return patterns


def _widen(raw: bytes) -> bytes:
    return b"".join(bytes([b]) + b"\x00" for b in raw)


_COMPILED: dict = {}


def compile_rule(rule: dict, known=()):
    key = (rule["name"], repr(rule["strings"]), rule["condition"], tuple(known))
    cached = _COMPILED.get(key)
    if cached:
        return cached
    strings = [(entry, compile_string(entry)) for entry in rule["strings"]]
    cond = Cond(rule["condition"], [e["name"] for e in rule["strings"]], rule["name"], known)
    if len(_COMPILED) > 500:
        _COMPILED.clear()
    _COMPILED[key] = (strings, cond)
    return _COMPILED[key]


def match_rule(rule: dict, data: bytes, results=None, modules=None, known=()):
    """Run one rule over bytes. Returns a match map or None. results: earlier rules' outcomes
    (for conditions that name them); modules: pe/elf/math built once per buffer."""
    strings, cond = compile_rule(rule, known)
    # per-buffer search scratch (xor diff, chained hex pieces): lives with this scan's modules map,
    # never on the cached compiled rule, so nothing of the data outlives the scan
    memo = modules.setdefault("_search", {}) if modules is not None else {}
    counts = {}
    for entry, patterns in strings:
        hits = {}
        for pat in patterns:
            for offset, length in find_all(pat, data, MAX_MATCHES_PER_STRING, memo):
                hits[offset] = max(length, hits.get(offset, 0))
        counts[entry["name"]] = sorted(hits.items())[:MAX_MATCHES_PER_STRING]
    if not truth(cond.fn(Ctx(counts, data, results, modules))):
        return None
    found = []
    for entry, _ in strings:
        if entry.get("private"):
            continue
        for offset, length in counts[entry["name"]][:50]:
            piece = data[offset:offset + length]
            found.append({"name": "$" if entry.get("anonymous") else entry["name"], "offset": offset,
                          "length": length, "data": _show(piece)})
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
    """Every rule over data, in order (a condition may use earlier rules). Private rules count for
    other rules but aren't reported; if any global rule fails, nothing matches."""
    results, modules, out = {}, {}, []
    known = []
    for rule in rules:
        m = match_rule(rule, data, results, modules, tuple(known))
        known.append(rule["name"])
        results[rule["name"]] = m is not None
        if m is None and rule.get("global"):
            return []
        if m and not rule.get("private"):
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

def parse_rules_or_fail(line, text, fn, known=()):
    try:
        rules = parse_rules(text, known)
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
        known = [r["name"] for r in out]
        if isinstance(rule, str):
            out.extend(parse_rules_or_fail(line, rule, fn, known))
            continue
        try:
            out.append(normalize_rule(rule, known))
        except RuleSyntax as err:
            fail(line, f"{fn}() couldn't use that rule: {err}.")
    return out


def load(api):
    m = Module(
        "scan",
        "Look for patterns in files and bytes with YARA rules: text (with nocase, wide, fullword, xor and "
        "base64), hex byte patterns with ?? wildcards and [n-m] jumps, and /regular expressions/, combined with "
        "a condition ($a and ($b or $c), 2 of them, #a > 3, @a[1] < 100, uint16(0) == 0x5A4D, for any i in "
        "(1..#a) : (...)). The pe, elf and math modules work too (pe.imphash(), pe.sections, math.entropy). "
        "Rules can be YARA text or plain TB maps; YARA features TB doesn't have give a precise error. Also "
        "pulls readable strings (ASCII and UTF-16) out of binary data. Limits: each string keeps its first "
        "1,000 matches, so #a counts at most 1,000 (YARA goes to 1,000,000); a regex match stops after "
        "4,096 bytes, as in YARA; regexes like (a+)+ that could backtrack for hours are refused.",
    )
    m.value("example", 'rule SuspiciousDownloader {\n  meta:\n    author = "soc"\n    severity = "high"\n'
                       '  strings:\n    $a = "DownloadString" nocase\n    $b = { 4D 5A ?? 00 }\n'
                       '    $c = /https?:\\/\\/[0-9.]+\\//\n  condition:\n    $a and ($b or $c)\n}',
            doc="A rule in the text form scan understands, to copy from.")

    @m.fn("rule", 1, doc=(
        "rule(text) -> map\n"
        "Parse one YARA-style rule into a rule map. Fails with a plain explanation if the rule is broken.\n"
        "For several rules use scan.rules().\n"
        "\n"
        "Fields:\n"
        "- \"name\", \"tags\", \"meta\"\n"
        "- \"strings\": a list of {\"name\", \"kind\", \"value\", flags} maps.\n"
        "- \"condition\""))
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

    @m.fn("bytes", 2, doc=(
        "bytes(data, rules) -> list\n"
        "Run rules over bytes (or text). Gives back one map per rule that matched.\n"
        "An empty list means no match.\n"
        "\n"
        "Fields of each match:\n"
        "- \"rule\", \"tags\", \"meta\", \"count\"\n"
        "- \"strings\": a list of {\"name\", \"offset\", \"length\", \"data\"} maps."))
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

    @m.fn("folder", 2, 3, doc=(
        "folder(dir, rules, options?) -> list\n"
        "Scan every file in a folder and its sub-folders. Gives back the matches from every file,\n"
        "each with \"path\". Files it can't read are skipped.\n"
        "\n"
        "Options:\n"
        "- recursive: false scans only the folder itself, not its sub-folders.\n"
        "- max_size: skip files bigger than this many bytes (default: 32 MB)."))
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

    @m.fn("strings", 1, 3, doc=(
        "strings(data, min?, limit?) -> list\n"
        "Readable strings in bytes, text or a file path, sorted by offset.\n"
        "It finds ASCII runs and UTF-16 (Windows wide) runs.\n"
        "\n"
        "Arguments:\n"
        "- min: the fewest characters a string can have (default: 4).\n"
        "- limit: the most strings to give back.\n"
        "\n"
        "Fields of each string:\n"
        "- \"text\", \"offset\", \"encoding\""))
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

    @m.fn("file_type", 1, doc=(
        "file_type(path) -> map\n"
        "What a file really is, from its first bytes (not its name). Same as binary.file_type().\n"
        "\n"
        "Fields:\n"
        "- \"type\": like pe, elf, macho, pdf, zip, docx, png, pcap, evtx, script, text or unknown.\n"
        "- \"description\", \"mime\", \"extension\", \"executable\", \"size\""))
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
