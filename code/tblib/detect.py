"""detect: detection rules, threshold alerts, built-in hunts and a Sigma subset."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import ipaddress
import os
import re
from collections import deque

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

LEVELS = ["informational", "low", "medium", "high", "critical"]
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
MODIFIERS = {"contains", "startswith", "endswith", "all", "re", "cidr", "gt", "gte", "lt", "lte", "exists",
             "windash", "i", "m", "s"}
UNSUPPORTED_MODIFIERS = {"base64", "base64offset", "utf16", "utf16le", "utf16be", "wide", "expand", "fieldref",
                         "cased"}
# sshd writes "Invalid user" and a pam_unix line next to each "Failed password":
# count only the main line so one guess counts once.
COMPANION_ACTIONS = {"invalid_user", "auth_failure", "too_many_attempts"}
SAMPLE = 5


class RuleError(Exception):
    """A problem in a rule; turned into a friendly TB error by the caller."""


# =============================================================================
# A small YAML reader (the subset Sigma rules use)
# =============================================================================

class YamlError(Exception):
    def __init__(self, message, lineno=None):
        super().__init__(message)
        self.lineno = lineno


def strip_comment(text: str) -> str:
    """Remove a trailing # comment that is outside quotes."""
    quote = None
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
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def split_key(text: str, lineno: int):
    """'key: value' -> (key, value) or None when the line isn't a mapping entry."""
    if text[:1] in "'\"":
        quote = text[0]
        end = find_closing(text, 0)
        if end is None:
            raise YamlError("this quoted key never ends", lineno)
        rest = text[end + 1:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] in " \t"):
            return parse_scalar(text[:end + 1], lineno), rest[1:].strip()
        return None
    m = re.match(r"^([^\s'\"#\[\]{}][^:#]*?|[^\s'\"#\[\]{}]):(?:\s+(.*))?$", text)
    if not m:
        return None
    return m.group(1).strip(), (m.group(2) or "").strip()


def find_closing(text: str, start: int):
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


ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/", " ": " ", "a": "\a",
           "b": "\b", "e": "\x1b", "f": "\f", "v": "\v"}


def parse_scalar(text: str, lineno: int):
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*" or text.startswith("!"):
        what = {"&": "anchors (&name)", "*": "aliases (*name)", "!": "tags (!name)"}[text[0]]
        raise YamlError(f"YAML {what} aren't supported; write the value out in full, or quote it", lineno)
    if text[0] == "'":
        end = find_closing(text, 0)
        if end != len(text) - 1:
            raise YamlError("a single-quoted value has extra text after its closing quote", lineno)
        return text[1:-1].replace("''", "'")
    if text[0] == '"':
        end = find_closing(text, 0)
        if end != len(text) - 1:
            raise YamlError("a double-quoted value has extra text after its closing quote", lineno)
        body, out, i = text[1:-1], [], 0
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
                out.append(ESCAPES.get(nxt, "\\" + nxt))
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)
    if text[0] == "[":
        if not text.endswith("]"):
            raise YamlError("a [list] value doesn't end with ]", lineno)
        inner = text[1:-1].strip()
        return [parse_scalar(part, lineno) for part in split_flow(inner, lineno)] if inner else []
    if text[0] == "{":
        if not text.endswith("}"):
            raise YamlError("a {map} value doesn't end with }", lineno)
        inner = text[1:-1].strip()
        result = {}
        for part in split_flow(inner, lineno) if inner else []:
            pair = split_key(part, lineno)
            if pair is None:
                raise YamlError(f"\"{part}\" inside {{...}} should look like key: value", lineno)
            result[str(pair[0])] = parse_scalar(pair[1], lineno)
        return result
    if text in ("null", "Null", "NULL", "~"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if re.fullmatch(r"[-+]?[0-9]+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", text):
        return float(text)
    return text


def split_flow(text: str, lineno: int) -> list:
    parts, depth, quote, current = [], 0, None, []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            current.append(ch)
        elif ch in "[{":
            raise YamlError("lists or maps inside [...] or {...} aren't supported; use indented lines", lineno)
        elif ch == ",":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    if quote:
        raise YamlError("a quoted value inside [...] never ends", lineno)
    last = "".join(current).strip()
    if last:
        parts.append(last)
    return parts


class YamlReader:
    def __init__(self, text: str):
        self.raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines = []  # [indent, text, lineno]
        for n, raw_line in enumerate(self.raw, start=1):
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            if raw_line.startswith("%"):
                raise YamlError("YAML directives (%) aren't supported", n)
            leading = raw_line[:len(raw_line) - len(raw_line.lstrip())]
            if "\t" in leading:
                raise YamlError("indent with spaces, not tabs", n)
            self.lines.append([len(leading), strip_comment(raw_line.strip()), n])
        self.pos = 0

    def documents(self) -> list:
        docs, current = [], []
        for item in self.lines:
            if item[0] == 0 and item[1] in ("---", "..."):
                if current:
                    docs.append(current)
                current = []
                continue
            if item[0] == 0 and item[1].startswith("--- "):
                raise YamlError("put the first line of a document under its --- line", item[2])
            current.append(item)
        if current:
            docs.append(current)
        result = []
        for lines in docs:
            self.lines, self.pos = lines, 0
            value = self.block(lines[0][0])
            if self.pos < len(self.lines):
                bad = self.lines[self.pos]
                raise YamlError("this line is indented in a way that doesn't fit the lines above it", bad[2])
            result.append(value)
        return result

    def peek(self):
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def block(self, indent):
        line = self.peek()
        if line is None:
            return None
        if line[1] == "-" or line[1].startswith("- "):
            return self.sequence(line[0])
        return self.mapping(line[0])

    def sequence(self, indent):
        items = []
        while True:
            line = self.peek()
            if line is None or line[0] != indent or not (line[1] == "-" or line[1].startswith("- ")):
                break
            rest = line[1][1:]
            stripped = rest.lstrip()
            if not stripped:
                self.pos += 1
                nxt = self.peek()
                items.append(self.block(nxt[0]) if nxt is not None and nxt[0] > indent else None)
                continue
            offset = 1 + len(rest) - len(stripped)
            if stripped.startswith("- ") or stripped == "-" or (split_key(stripped, line[2]) is not None
                                                                and not stripped.startswith(("'", '"', "[", "{"))):
                # "- key: value" starts a map (or "- - x" a list) whose column is after the dash
                line[0], line[1] = indent + offset, stripped
                items.append(self.block(indent + offset))
                continue
            if stripped[:1] in "'\"" and split_key(stripped, line[2]) is not None:
                line[0], line[1] = indent + offset, stripped
                items.append(self.block(indent + offset))
                continue
            self.pos += 1
            items.append(self.scalar_value(stripped, indent, line[2]))
        return items

    def mapping(self, indent):
        result = {}
        while True:
            line = self.peek()
            if line is None or line[0] < indent:
                break
            if line[0] > indent:
                raise YamlError("this line is indented more than the ones above it", line[2])
            if line[1] == "-" or line[1].startswith("- "):
                break
            if line[1].startswith("? "):
                raise YamlError("complex keys (? key) aren't supported", line[2])
            pair = split_key(line[1], line[2])
            if pair is None:
                raise YamlError(f"I expected \"key: value\" here, but found \"{line[1]}\"", line[2])
            key, value = pair
            key = key if isinstance(key, str) else _tb.to_text(key)
            if key in result:
                raise YamlError(f"the key \"{key}\" appears twice", line[2])
            self.pos += 1
            if value == "":
                nxt = self.peek()
                if nxt is not None and (nxt[0] > indent or (nxt[0] == indent and (nxt[1] == "-" or nxt[1].startswith("- ")))):
                    result[key] = self.block(nxt[0])
                else:
                    result[key] = None
            elif re.fullmatch(r"[|>][-+]?[0-9]?", value):
                result[key] = self.block_scalar(value, indent, line[2])
            else:
                result[key] = self.scalar_value(value, indent, line[2])
        return result

    def scalar_value(self, value, indent, lineno):
        """A scalar that may continue on more-indented lines (plain or quoted)."""
        if value[:1] in "'\"" and find_closing(value, 0) is None:
            parts = [value]
            while True:
                nxt = self.peek()
                if nxt is None:
                    raise YamlError("a quoted value never ends", lineno)
                self.pos += 1
                parts.append(nxt[1])
                joined = " ".join(parts)
                if find_closing(joined, 0) is not None:
                    return parse_scalar(joined, lineno)
        parts = [value]
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] <= indent or value[:1] in "'\"[{":
                break
            if split_key(nxt[1], nxt[2]) is not None or nxt[1].startswith("- "):
                raise YamlError("this line is indented more than the ones above it", nxt[2])
            parts.append(nxt[1])
            self.pos += 1
        return parse_scalar(" ".join(parts), lineno)

    def block_scalar(self, style, indent, lineno):
        # block scalars need the raw lines (blank lines matter): find them by number
        body = []
        n = lineno  # raw index of the line after the key (lineno is 1-based)
        block_indent = None
        while n < len(self.raw):
            raw_line = self.raw[n]
            if raw_line.strip():
                width = len(raw_line) - len(raw_line.lstrip(" "))
                if width <= indent:
                    break
                if block_indent is None:
                    block_indent = width
                body.append(raw_line[min(block_indent, width):])
            else:
                body.append("")
            n += 1
        while self.pos < len(self.lines) and self.lines[self.pos][2] <= n:
            self.pos += 1
        while body and body[-1] == "":
            body.pop()
        if style.startswith(">"):
            text = ""
            for part in body:
                if part == "":
                    text += "\n"
                else:
                    text += ("" if not text or text.endswith("\n") else " ") + part
        else:
            text = "\n".join(body)
        if "-" in style:
            return text
        return text + "\n" if text else text


def read_yaml(text: str):
    docs = YamlReader(text).documents()
    if not docs:
        return None
    if len(docs) > 1:
        raise YamlError(f"this file has {len(docs)} YAML documents (--- lines); put one rule in each file")
    return docs[0]


# =============================================================================
# Matching values
# =============================================================================

def glob_regex(pattern: str, mode: str) -> re.Pattern:
    """Sigma-style wildcards: * any text, ? one character, \\* \\? \\\\ literal."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern) and pattern[i + 1] in "*?\\":
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    body = "".join(out)
    if mode in ("contains", "endswith"):
        body = ".*" + body
    if mode in ("contains", "startswith"):
        body = body + ".*"
    return re.compile(body, re.IGNORECASE | re.DOTALL)


def number_of(value):
    if _tb.is_number(value):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip()) if value.strip() else None
        except ValueError:
            try:
                return int(value.strip(), 0)
            except ValueError:
                return None
    return None


class ValueTest:
    """One field test: field, modifiers and the values to compare with."""

    def __init__(self, field, modifiers, values, where):
        self.field = field
        self.mods = set(modifiers)
        for mod in modifiers:
            if mod in UNSUPPORTED_MODIFIERS:
                raise RuleError(f"{where}: the |{mod} modifier isn't supported yet")
            if mod not in MODIFIERS:
                raise RuleError(f"{where}: I don't know the modifier |{mod}. "
                                "Use contains, startswith, endswith, re, all, cidr, gt, gte, lt, lte or exists")
        self.all = "all" in self.mods
        values = values if isinstance(values, list) else [values]
        if not values:
            raise RuleError(f"{where}: the list of values for {field} is empty")
        if "windash" in self.mods:
            expanded = []
            for v in values:
                expanded.append(v)
                if isinstance(v, str) and "-" in v:
                    expanded.extend([v.replace("-", "/"), v.replace("-", "–")])
            values = expanded
        self.tests = [self.compile(v, where) for v in values]

    def compile(self, value, where):
        mods = self.mods
        if "exists" in mods:
            if not isinstance(value, bool):
                raise RuleError(f"{where}: |exists needs true or false")
            return ("exists", value)
        for op in ("gt", "gte", "lt", "lte"):
            if op in mods:
                number = number_of(value)
                if number is None:
                    raise RuleError(f"{where}: |{op} needs a number, not {_tb.to_text(value, True)}")
                return (op, number)
        if "cidr" in mods:
            try:
                return ("cidr", ipaddress.ip_network(str(value), strict=False))
            except ValueError:
                raise RuleError(f"{where}: \"{value}\" isn't a network like 10.0.0.0/8") from None
        regex_mode = "re" in mods
        if isinstance(value, str) and value.startswith("re:") and not (mods & {"contains", "startswith", "endswith"}):
            regex_mode, value = True, value[3:]
        if regex_mode:
            flags = (re.IGNORECASE if "i" in mods else 0) | (re.MULTILINE if "m" in mods else 0) | (
                re.DOTALL if "s" in mods else 0)
            try:
                return ("re", re.compile(str(value), flags))
            except re.error as err:
                raise RuleError(f"{where}: the pattern {value!r} doesn't work: {err}") from None
        if value is None:
            return ("null", None)
        if isinstance(value, bool):
            return ("bool", value)
        mode = next((m for m in ("contains", "startswith", "endswith") if m in mods), "exact")
        if _tb.is_number(value) and mode == "exact":
            return ("number", value)
        text = _tb.to_text(value)
        if mode == "exact" and "*" not in text and "?" not in text:
            return ("text", text.casefold())
        return ("glob", glob_regex(text, mode))

    def check(self, actual, test):
        kind, expected = test
        if kind == "exists":
            return (actual is not None and actual != "") == expected
        if isinstance(actual, list):
            return any(self.check(item, test) for item in actual)
        if kind == "null":
            return actual is None or actual == ""
        if actual is None:
            return False
        if kind in ("gt", "gte", "lt", "lte"):
            number = number_of(actual)
            if number is None:
                return False
            return {"gt": number > expected, "gte": number >= expected,
                    "lt": number < expected, "lte": number <= expected}[kind]
        if kind == "cidr":
            try:
                return ipaddress.ip_address(str(actual).strip()) in expected
            except ValueError:
                return False
        if kind == "bool":
            return actual is expected if isinstance(actual, bool) else str(actual).lower() == str(expected).lower()
        if kind == "number":
            number = number_of(actual)
            return number is not None and not isinstance(actual, bool) and number == expected
        text = actual if isinstance(actual, str) else _tb.to_text(actual)
        if kind == "text":
            return text.casefold() == expected
        if kind == "glob":
            return expected.fullmatch(text) is not None
        if kind == "re":
            return expected.search(text) is not None
        return False

    def matches(self, record):
        actual = get_field(record, self.field)
        if self.all:
            return all(self.check(actual, t) for t in self.tests)
        return any(self.check(actual, t) for t in self.tests)


def get_field(record, name):
    if not isinstance(record, dict):
        return None
    if name in record:
        return record[name]
    low = name.lower()
    for key in record:
        if isinstance(key, str) and key.lower() == low:
            return record[key]
    if "." in name:
        current = record
        for part in name.split("."):
            current = get_field(current, part)
            if current is None:
                return None
        return current
    return None


def compile_where(where, label) -> list:
    if not isinstance(where, dict):
        raise RuleError(f"{label} should be a map of field: value")
    tests = []
    for key, value in where.items():
        if not isinstance(key, str) or not key:
            raise RuleError(f"{label}: field names must be text")
        field, *mods = key.split("|")
        tests.append(ValueTest(field, [m.lower() for m in mods], value, f"{label}, field \"{key}\""))
    return tests


class Keywords:
    """A Sigma keyword list: any of the words appears in the message or raw line."""

    def __init__(self, words, where):
        if not words:
            raise RuleError(f"{where}: the keyword list is empty")
        self.tests = [glob_regex(_tb.to_text(w), "contains") for w in words]

    def matches(self, record):
        texts = [t for t in (get_field(record, "message"), get_field(record, "raw"), get_field(record, "Message"))
                 if isinstance(t, str)]
        return any(t.fullmatch(text) for t in self.tests for text in texts)


class Selection:
    def __init__(self, name, value, rule_where="The rule"):
        where = f"{rule_where}, selection \"{name}\""
        self.alternatives = []  # OR of (AND lists)
        if isinstance(value, dict):
            self.alternatives.append(compile_where(value, where))
        elif isinstance(value, list):
            if not value:
                raise RuleError(f"{where} is empty")
            if all(isinstance(v, dict) for v in value):
                for v in value:
                    self.alternatives.append(compile_where(v, where))
            elif all(not isinstance(v, (dict, list)) for v in value):
                self.alternatives.append([Keywords(value, where)])
            else:
                raise RuleError(f"{where} mixes maps and plain values; use one or the other")
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            self.alternatives.append([Keywords([value], where)])
        else:
            raise RuleError(f"{where} should be a map of field: value, or a list")

    def matches(self, record):
        return any(all(t.matches(record) for t in tests) for tests in self.alternatives)


# =============================================================================
# Sigma conditions
# =============================================================================

TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z0-9_*.\-]+)")


def tokenize_condition(text, where):
    tokens, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise RuleError(f"{where}: I can't read \"{text[pos:].strip()}\" in the condition")
        tokens.append(m.group(1))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return tokens


class ConditionParser:
    def __init__(self, text, names, where):
        self.tokens = tokenize_condition(text, where)
        self.pos = 0
        self.names = names
        self.where = where
        self.text = text

    def parse(self):
        if not self.tokens:
            raise RuleError(f"{self.where}: the condition is empty")
        node = self.or_expr()
        if self.pos < len(self.tokens):
            raise RuleError(f"{self.where}: I didn't expect \"{self.tokens[self.pos]}\" in the condition \"{self.text}\"")
        return node

    def peek(self):
        return self.tokens[self.pos].lower() if self.pos < len(self.tokens) else None

    def take(self):
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def or_expr(self):
        node = self.and_expr()
        while self.peek() == "or":
            self.take()
            node = ("or", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.peek() == "and":
            self.take()
            node = ("and", node, self.not_expr())
        return node

    def not_expr(self):
        if self.peek() == "not":
            self.take()
            return ("not", self.not_expr())
        return self.atom()

    def atom(self):
        token = self.peek()
        if token is None:
            raise RuleError(f"{self.where}: the condition \"{self.text}\" ends too early")
        if token == "(":
            self.take()
            node = self.or_expr()
            if self.peek() != ")":
                raise RuleError(f"{self.where}: a ( in the condition is never closed")
            self.take()
            return node
        if token in ("1", "any", "all") and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].lower() == "of":
            quantifier = self.take().lower()
            self.take()
            if self.peek() is None:
                raise RuleError(f"{self.where}: \"{quantifier} of\" needs a name pattern or 'them'")
            pattern = self.take()
            names = [n for n in self.names if not n.startswith("_")] if pattern.lower() == "them" else \
                [n for n in self.names if fnmatch.fnmatchcase(n, pattern)]
            if not names:
                raise RuleError(f"{self.where}: no selection matches \"{pattern}\" in \"{quantifier} of {pattern}\"")
            return ("all" if quantifier == "all" else "any", names)
        if token in ("and", "or", ")", "of"):
            raise RuleError(f"{self.where}: I didn't expect \"{token}\" there in the condition \"{self.text}\"")
        name = self.take()
        if "*" in name:
            raise RuleError(f"{self.where}: wildcards like \"{name}\" only work after '1 of' or 'all of'")
        if name not in self.names:
            known = ", ".join(self.names) or "none"
            raise RuleError(f"{self.where}: the condition uses \"{name}\", but there's no selection with that name "
                            f"(selections: {known})")
        return ("sel", name)


def eval_condition(node, check):
    kind = node[0]
    if kind == "sel":
        return check(node[1])
    if kind == "and":
        return eval_condition(node[1], check) and eval_condition(node[2], check)
    if kind == "or":
        return eval_condition(node[1], check) or eval_condition(node[2], check)
    if kind == "not":
        return not eval_condition(node[1], check)
    if kind == "any":
        return any(check(n) for n in node[1])
    if kind == "all":
        return all(check(n) for n in node[1])
    return False


AGGREGATION = re.compile(r"^count\(\s*([\w.]*)\s*\)(?:\s+by\s+([\w.]+(?:\s*,\s*[\w.]+)*))?\s*(>=|>|==|=)\s*(\d+)$",
                         re.IGNORECASE)


def parse_aggregation(text, where):
    m = AGGREGATION.match(text.strip())
    if not m:
        raise RuleError(f"{where}: only \"| count() by field > N\" aggregations are supported, not \"| {text.strip()}\"")
    field, by, op, number = m.groups()
    if field:
        raise RuleError(f"{where}: count({field}) (counting distinct values) isn't supported; use count()")
    number = int(number)
    needed = number + 1 if op == ">" else number
    if op in ("==", "="):
        needed = number
    by_fields = [b.strip() for b in by.split(",")] if by else None
    return needed, by_fields


TIMEFRAME = re.compile(r"^(\d+)\s*([smhdSMHD])$")


def parse_timeframe(value, where):
    m = TIMEFRAME.match(str(value).strip())
    if not m:
        raise RuleError(f"{where}: timeframe \"{value}\" should look like 30s, 5m, 1h or 1d")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2).lower()]


class CompiledRule:
    """A rule map turned into something fast to test records with."""

    def __init__(self, rule, api=None, line=None):
        self.rule = rule
        self.api = api
        self.line = line
        self.name = rule["name"]
        self.where_fn = None
        self.where = []
        self.unless = []
        self.condition = None
        self.selections = {}
        where = f"The rule \"{self.name}\""
        if "detection" in rule and rule["detection"] is not None:
            detection = rule["detection"]
            for key, value in detection.items():
                if key in ("condition", "timeframe"):
                    continue
                self.selections[key] = Selection(key, value, where)
            conditions = rule.get("condition") or detection.get("condition")
            if conditions is None:
                raise RuleError(f"{where}: the detection needs a condition")
            conditions = conditions if isinstance(conditions, list) else [conditions]
            nodes = []
            for text in conditions:
                if not isinstance(text, str):
                    raise RuleError(f"{where}: the condition should be text")
                text = text.split("|", 1)[0]
                nodes.append(ConditionParser(text, list(self.selections), where).parse())
            self.condition = nodes[0]
            for node in nodes[1:]:
                self.condition = ("or", self.condition, node)
        where_value = rule.get("where")
        if where_value is not None:
            if isinstance(where_value, dict):
                self.where = compile_where(where_value, f"{where}'s where")
            elif _is_function(where_value):
                self.where_fn = where_value
            else:
                raise RuleError(f"{where}: where should be a map of field: value, or a function")
        if rule.get("unless") is not None:
            self.unless = compile_where(rule["unless"], f"{where}'s unless")
        if self.condition is None and not self.where and self.where_fn is None:
            raise RuleError(f"{where} needs a \"where\" map (or a Sigma detection)")
        formats = rule.get("formats")
        self.formats = {str(f).lower() for f in formats} if isinstance(formats, list) and formats else None

    def matches(self, record):
        if not isinstance(record, dict):
            return False
        if self.formats is not None:
            fmt = record.get("format")
            if isinstance(fmt, str) and fmt.lower() not in self.formats and not (
                    fmt == "nginx" and "apache" in self.formats):
                return False
        if self.where and not all(t.matches(record) for t in self.where):
            return False
        if self.where_fn is not None:
            if not _tb.truthy(self.api.call(self.where_fn, [record], self.line)):
                return False
        if self.condition is not None:
            cache = {}

            def check(name):
                if name not in cache:
                    cache[name] = self.selections[name].matches(record)
                return cache[name]

            if not eval_condition(self.condition, check):
                return False
        if self.unless and all(t.matches(record) for t in self.unless):
            return False
        return True


def _is_function(value):
    return isinstance(value, (_tb.Function, _tb.Builtin)) or (callable(value) and not isinstance(value, (_tb.Module, type)))


# =============================================================================
# Rules and alerts
# =============================================================================

def normalise_rule(spec, where="detect.rule()"):
    if not isinstance(spec, dict):
        raise RuleError(f"{where} needs a map like {{\"name\": ..., \"where\": {{...}}}}")
    rule = dict(spec)
    name = rule.get("name", rule.get("title"))
    if not isinstance(name, str) or not name.strip():
        raise RuleError(f"{where}: every rule needs a \"name\" (text)")
    rule["name"] = name.strip()
    level = rule.get("level", "medium")
    if not isinstance(level, str) or level.lower() not in LEVEL_RANK:
        raise RuleError(f"The rule \"{rule['name']}\": level should be one of {', '.join(LEVELS)}, "
                        f"not {_tb.to_text(level, True)}")
    rule["level"] = level.lower()
    count = rule.get("count", 1)
    if not _tb.is_whole(count) or count < 1:
        raise RuleError(f"The rule \"{rule['name']}\": count should be a whole number of 1 or more")
    rule["count"] = int(count)
    within = rule.get("within")
    if within is not None:
        if isinstance(within, str):
            within = parse_timeframe(within, f"The rule \"{rule['name']}\"")
        if not _tb.is_number(within) or within <= 0:
            raise RuleError(f"The rule \"{rule['name']}\": within should be a number of seconds (or text like \"5m\")")
    rule["within"] = within
    by = rule.get("by")
    if by is not None:
        if isinstance(by, str):
            pass
        elif isinstance(by, list) and by and all(isinstance(b, str) for b in by):
            pass
        else:
            raise RuleError(f"The rule \"{rule['name']}\": by should be a field name (or a list of them)")
    rule["by"] = by
    return rule


def weight(record):
    repeat = record.get("repeat") if isinstance(record, dict) else None
    return repeat if isinstance(repeat, int) and not isinstance(repeat, bool) and repeat > 0 else 1


def when_of(ts, record=None):
    if record is not None and isinstance(record.get("when"), str) and record.get("time") == ts:
        return record["when"]
    if ts is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return None


def time_of(record):
    ts = record.get("time") if isinstance(record, dict) else None
    return ts if _tb.is_number(ts) else None


def make_alert(rule, by_value, by_field, records, count=None, extra=None):
    times = [time_of(r) for r in records if time_of(r) is not None]
    first = min(times) if times else None
    last = max(times) if times else None
    first_record = next((r for r in records if time_of(r) == first), None)
    last_record = next((r for r in reversed(records) if time_of(r) == last), None)
    alert = {
        "rule": rule["name"], "level": rule["level"],
        "by": by_value, "by_field": by_field,
        "count": count if count is not None else sum(weight(r) for r in records),
        "first": first, "last": last,
        "first_when": when_of(first, first_record), "last_when": when_of(last, last_record),
        "description": rule.get("description"),
    }
    if extra:
        alert.update(extra)
    alert["records"] = list(records[:rule.get("sample", SAMPLE) if _tb.is_whole(rule.get("sample")) else SAMPLE])
    return alert


def group_key(record, by):
    fields = by if isinstance(by, list) else [by]
    values = [get_field(record, f) for f in fields]
    if any(v is None or v == "" for v in values):
        return None
    if len(values) == 1:
        return values[0] if not isinstance(values[0], (list, dict)) else _tb.to_text(values[0])
    return " / ".join(_tb.to_text(v) for v in values)


def threshold_alerts(rule, matched):
    by = rule.get("by")
    count = rule["count"]
    within = rule.get("within")
    groups: dict = {}
    for record in matched:
        key = group_key(record, by) if by else "__all__"
        if key is None:
            continue
        groups.setdefault(key, []).append(record)
    by_field = by if isinstance(by, str) else (" / ".join(by) if by else None)
    alerts = []
    for key, records in groups.items():
        value = None if key == "__all__" else key
        if within is None:
            if sum(weight(r) for r in records) >= count:
                alerts.append(make_alert(rule, value, by_field, records))
            continue
        timed = sorted((r for r in records if time_of(r) is not None), key=time_of)
        for episode in episodes(timed, count, within):
            alerts.append(make_alert(rule, value, by_field, episode))
    return alerts


def episodes(timed, count, within):
    """Bursts of at least count (weighted) records within `within` seconds of
    each other. A burst keeps growing while records keep coming within
    `within` seconds of the previous one, so one attack gives one alert."""
    window, total, current, last = deque(), 0, None, None
    for record in timed:
        ts = time_of(record)
        if current is not None:
            if ts - last <= within:
                current.append(record)
                last = ts
                continue
            yield current
            current = None
        window.append(record)
        total += weight(record)
        while window and time_of(window[0]) < ts - within:
            total -= weight(window.popleft())
        if total >= count:
            current, last = list(window), ts
            window.clear()
            total = 0
    if current is not None:
        yield current


def failed_logins(records):
    return [r for r in records if isinstance(r, dict) and r.get("success") is False
            and r.get("action") not in COMPANION_ACTIONS]


def sort_alerts(alerts):
    return sorted(alerts, key=lambda a: (-LEVEL_RANK.get(a.get("level"), 2),
                                         a["first"] if _tb.is_number(a.get("first")) else float("inf")))


def run_rules(rules, records, api, line):
    compiled = [CompiledRule(normalise_rule(r, "detect.run()"), api, line) for r in rules]
    alerts = []
    for rule in compiled:
        matched = [r for r in records if rule.matches(r)]
        if matched:
            alerts.extend(threshold_alerts(rule.rule, matched))
    return sort_alerts(alerts)


# =============================================================================
# Sigma
# =============================================================================

def logsource_formats(logsource):
    if not isinstance(logsource, dict):
        return None
    product = str(logsource.get("product") or "").lower()
    service = str(logsource.get("service") or "").lower()
    category = str(logsource.get("category") or "").lower()
    if product == "windows":
        return ["windows"]
    if category in ("webserver", "proxy") or product in ("apache", "nginx") or service in ("apache", "nginx"):
        return ["apache", "nginx", "json", "csv"]
    if product == "linux":
        if service in ("auth", "sshd", "sudo", "su", "auditd"):
            return ["auth", "syslog"]
        return ["auth", "syslog", "text"]
    if category == "firewall":
        return ["syslog", "json", "csv"]
    return None


def sigma_rule(data, source):
    where = f"The Sigma rule {source}"
    if not isinstance(data, dict):
        raise RuleError(f"{where} should be a map with title, detection and so on")
    detection = data.get("detection")
    if not isinstance(detection, dict):
        raise RuleError(f"{where} has no detection: section")
    title = data.get("title") or "Untitled Sigma rule"
    title = _tb.to_text(title)
    where = f"The Sigma rule \"{title}\""
    condition = detection.get("condition")
    if condition is None:
        raise RuleError(f"{where}: detection: needs a condition:")
    conditions = condition if isinstance(condition, list) else [condition]
    count, by, within = 1, None, None
    for text in conditions:
        if not isinstance(text, str):
            raise RuleError(f"{where}: the condition should be text")
        if "|" in text:
            if len(conditions) > 1:
                raise RuleError(f"{where}: aggregations (| count()) only work with a single condition")
            count, by_fields = parse_aggregation(text.split("|", 1)[1], where)
            by = by_fields[0] if by_fields and len(by_fields) == 1 else by_fields
    timeframe = data.get("timeframe") or detection.get("timeframe")
    if timeframe is not None:
        within = parse_timeframe(timeframe, where)
    level = data.get("level") or "medium"
    if str(level).lower() not in LEVEL_RANK:
        raise RuleError(f"{where}: level should be one of {', '.join(LEVELS)}, not {_tb.to_text(level, True)}")
    rule = {
        "name": title, "level": str(level).lower(),
        "description": (data.get("description") or "").strip() or None,
        "id": data.get("id"), "status": data.get("status"),
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "logsource": data.get("logsource") if isinstance(data.get("logsource"), dict) else {},
        "formats": logsource_formats(data.get("logsource")),
        "detection": detection, "condition": condition,
        "count": count, "within": within, "by": by, "source": "sigma",
    }
    CompiledRule(normalise_rule(rule))  # check everything now, so mistakes show at load time
    return rule


# =============================================================================
# The library
# =============================================================================

def load(api):
    m = Module(
        "detect",
        "Detection rules for logs: describe what suspicious looks like as a map (or load a Sigma rule), run it "
        "over records from the logs library, and get alerts back. Thresholds (\"5 failures within 60 seconds "
        "from one IP\") and built-in hunts for brute force, port scans and rare values are included.",
    )
    m.value("levels", list(LEVELS), doc="Alert levels from least to most serious: informational, low, medium, high, critical.")

    def rule_error(line, err):
        fail(line, str(err).rstrip(".") + ".")

    def records_arg(line, records, fn):
        check_type(line, records, "list", f"detect.{fn}", "records")
        return [r for r in records if isinstance(r, dict)]

    @m.fn("rule", 1, doc=(
        "rule(map) -> map\n"
        "Check a rule and fill in its defaults. A rule is a map:\n"
        "  \"name\": what to call the alert; \"level\": informational/low/medium (default)/high/critical;\n"
        "  \"where\": {field: value, ...} - every field must match. A value can be exact text (any case), text "
        "with * and ? wildcards, \"re:pattern\" for a regular expression, a number, nil (field missing), or a list "
        "(any of them). Add a modifier to the field name: \"path|contains\", \"agent|startswith\", "
        "\"file|endswith\", \"bytes|gt\", \"ip|cidr\" (\"10.0.0.0/8\"), \"cmd|re\", \"x|exists\". where can also "
        "be a function that gets a record and gives back true or false;\n"
        "  \"unless\": {field: value} - skip records that match this (to remove false positives);\n"
        "  \"count\": how many matches make an alert (default 1); \"within\": seconds (or \"5m\") they must fall in;\n"
        "  \"by\": a field (or list of fields) to count separately, like \"ip\"."))
    def rule(line, spec):
        try:
            result = normalise_rule(spec)
            CompiledRule(result, api, line)
        except RuleError as err:
            rule_error(line, err)
        return result

    @m.fn("run", 2, doc=(
        "run(rules, records) -> list\n"
        "Run one rule or a list of rules over log records and give back the alerts, most serious first. "
        "Each alert is a map: {\"rule\", \"level\", \"by\" (the value of the by field, like the IP), \"count\", "
        "\"first\", \"last\" (timestamps), \"first_when\", \"last_when\" (as text), \"description\", \"records\" "
        "(the first few matching records)}. With count and within, each burst of activity gives one alert. "
        "A record's \"repeat\" field (from \"message repeated 3 times\") counts as that many records."))
    def run(line, rules, records):
        check_type(line, rules, "list|map", "detect.run", "rules")
        rules = [rules] if isinstance(rules, dict) else rules
        records = records_arg(line, records, "run")
        try:
            return run_rules(rules, records, api, line)
        except RuleError as err:
            rule_error(line, err)

    @m.fn("match", 2, doc=(
        "match(rule, record) -> bool\n"
        "True if one record matches a rule's where (or Sigma detection), ignoring count and within. "
        "Handy for testing rules."))
    def match(line, spec, record):
        check_type(line, record, "map", "detect.match", "record")
        try:
            return CompiledRule(normalise_rule(spec, "detect.match()"), api, line).matches(record)
        except RuleError as err:
            rule_error(line, err)

    @m.fn("brute_force", 1, 3, doc=(
        "brute_force(records, attempts?, within?) -> list\n"
        "Find password guessing: at least attempts failed logins (default 5) from one IP within within seconds "
        "(default 600). Works on auth.log (SSH, sudo) and Windows 4625/4771 events, or any record with "
        "success = false. Each alert also has \"users\" (the accounts tried) and \"success_after\": true if the "
        "same IP then logged in successfully - that alert becomes critical, because the attacker got in."))
    def brute_force(line, records, attempts=5, within=600):
        records = records_arg(line, records, "brute_force")
        attempts = check_type(line, attempts, "whole", "detect.brute_force", "attempts")
        check_type(line, within, "number", "detect.brute_force", "within")
        if attempts < 1 or within <= 0:
            fail(line, "detect.brute_force() needs attempts of 1 or more and within above 0.")
        failures = failed_logins(records)
        rule_map = {"name": "Brute force", "level": "high", "count": attempts, "within": within,
                    "description": f"{attempts}+ failed logins within {_tb.to_text(within)} seconds from one source"}
        alerts = []
        by_ip = [r for r in failures if get_field(r, "ip")]
        by_user = [r for r in failures if not get_field(r, "ip") and get_field(r, "user")]
        for field, group in (("ip", by_ip), ("user", by_user)):
            rule_map["by"] = field
            for alert in threshold_alerts(normalise_rule(rule_map), group):
                key = alert["by"]
                burst = [r for r in group if get_field(r, field) == key and time_of(r) is not None
                         and alert["first"] <= time_of(r) <= alert["last"]]
                users = []
                for r in burst:
                    user = r.get("user")
                    if user and user not in users:
                        users.append(user)
                success = next((r for r in records if r.get("success") is True and get_field(r, field) == key
                                and time_of(r) is not None and time_of(r) >= alert["first"]), None)
                alert["users"] = users
                alert["success_after"] = success is not None
                alert["success_user"] = success.get("user") if success else None
                alert["success_when"] = when_of(time_of(success), success) if success else None
                if success is not None:
                    alert["level"] = "critical"
                    alert["description"] += f"; then {success.get('user') or 'someone'} logged in successfully"
                alert["records"] = alert.pop("records")
                alerts.append(alert)
        return sort_alerts(alerts)

    @m.fn("failed_logins", 1, doc=(
        "failed_logins(records) -> list\n"
        "Just the failed logins: records with success = false (SSH, sudo, su, Windows 4625/4771...). sshd's extra "
        "\"Invalid user\" and pam_unix lines are left out, so each guess counts once. brute_force() counts these."))
    def failed_logins_fn(line, records):
        return failed_logins(records_arg(line, records, "failed_logins"))

    @m.fn("port_scan", 1, 3, doc=(
        "port_scan(records, ports?, within?) -> list\n"
        "Find port scans in connection or firewall logs: one source IP touching at least ports different "
        "destination ports (default 10) within within seconds (default 60). Uses the fields src_ip / ip for the "
        "source and dst_port (or dest_port, DestinationPort, dpt) for the destination; UFW and iptables lines "
        "from the logs library have them. Alerts list the \"ports\" and \"targets\" seen."))
    def port_scan(line, records, ports=10, within=60):
        records = records_arg(line, records, "port_scan")
        ports = check_type(line, ports, "whole", "detect.port_scan", "ports")
        check_type(line, within, "number", "detect.port_scan", "within")
        if ports < 2 or within <= 0:
            fail(line, "detect.port_scan() needs ports of 2 or more and within above 0.")
        by_source: dict = {}
        for r in records:
            src = next((get_field(r, f) for f in ("src_ip", "ip", "source_ip", "SourceAddress", "src")
                        if get_field(r, f)), None)
            port = next((get_field(r, f) for f in ("dst_port", "dest_port", "DestinationPort", "dpt", "destination.port")
                         if get_field(r, f) is not None), None)
            if src is None or port is None or time_of(r) is None:
                continue
            by_source.setdefault(src, []).append((time_of(r), port, r))
        rule_map = normalise_rule({"name": "Port scan", "level": "medium",
                                   "description": f"{ports}+ different ports within {_tb.to_text(within)} seconds from one source"})
        alerts = []
        for src, events in by_source.items():
            events.sort(key=lambda e: e[0])
            window, seen, current, last = deque(), {}, None, None

            def finish(burst):
                distinct = []
                for _t, port, _r in burst:
                    if port not in distinct:
                        distinct.append(port)
                targets = []
                for _t, _p, r in burst:
                    dst = get_field(r, "dst_ip") or get_field(r, "dst")
                    if dst and dst not in targets:
                        targets.append(dst)
                alert = make_alert(rule_map, src, "ip", [r for _t, _p, r in burst], count=len(distinct),
                                   extra={"attempts": len(burst), "ports": sorted(distinct, key=lambda p: (str(type(p)), p))[:100],
                                          "targets": targets})
                alerts.append(alert)

            for event in events:
                ts, port, _r = event
                if current is not None:
                    if ts - last <= within:
                        current.append(event)
                        last = ts
                        continue
                    finish(current)
                    current = None
                window.append(event)
                seen[port] = seen.get(port, 0) + 1
                while window and window[0][0] < ts - within:
                    old = window.popleft()
                    seen[old[1]] -= 1
                    if not seen[old[1]]:
                        del seen[old[1]]
                if len(seen) >= ports:
                    current, last = list(window), ts
                    window.clear()
                    seen = {}
            if current is not None:
                finish(current)
        return sort_alerts(alerts)

    @m.fn("rare", 2, 3, doc=(
        "rare(records, field, max_count?) -> list\n"
        "Values of a field that show up at most max_count times (default 1), like a user agent or process seen "
        "only once: rare things are worth a look. Gives back low-level alerts, rarest first."))
    def rare(line, records, field, max_count=1):
        records = records_arg(line, records, "rare")
        check_type(line, field, "text", "detect.rare", "field")
        max_count = check_type(line, max_count, "whole", "detect.rare", "max_count")
        groups: dict = {}
        for r in records:
            value = get_field(r, field)
            if value is None or value == "" or isinstance(value, (list, dict)):
                continue
            groups.setdefault(value, []).append(r)
        rule_map = normalise_rule({"name": f"Rare {field}", "level": "low",
                                   "description": f"A {field} value seen {max_count} time(s) or fewer"})
        alerts = [make_alert(rule_map, value, field, group) for value, group in groups.items()
                  if sum(weight(r) for r in group) <= max_count]
        return sorted(alerts, key=lambda a: (a["count"], a["first"] if _tb.is_number(a["first"]) else float("inf")))

    def load_sigma(line, source, fn):
        check_type(line, source, "text", f"detect.{fn}", "text_or_path")
        label = "text"
        text = source
        candidate = os.path.expanduser(source.strip())
        if "\n" not in source and (candidate.lower().endswith((".yml", ".yaml")) or os.path.isfile(candidate)):
            if not os.path.isfile(candidate):
                fail(line, f"I couldn't find the Sigma rule '{source}'. Is the name spelled right?", path=source)
            try:
                from tblib.fs import read_text
                text = read_text(candidate)
            except OSError as err:
                fail_os(line, err, candidate, "read")
            label = f"'{os.path.basename(candidate)}'"
        try:
            data = read_yaml(text)
        except YamlError as err:
            at = f" (line {err.lineno})" if err.lineno else ""
            fail(line, f"I couldn't read the Sigma rule {label}: {err}{at}.")
        try:
            return sigma_rule(data, label)
        except RuleError as err:
            rule_error(line, err)

    @m.fn("sigma", 1, doc=(
        "sigma(text_or_path) -> map\n"
        "Load a Sigma rule (the shared YAML format for detections) from a .yml file or from text, as a rule for "
        "detect.run(). Supported: title, id, level, description, tags, logsource, timeframe, and detection with "
        "named selections (maps, lists of maps, keyword lists), the modifiers contains, startswith, endswith, all, "
        "re, cidr, gt/gte/lt/lte, exists and windash, wildcards * and ?, and conditions with and, or, not, "
        "brackets, \"1 of selection*\", \"all of them\" and \"| count() by field > N\". logsource limits the rule "
        "to matching record formats (product: windows -> Windows events). Anything else gives a clear error."))
    def sigma(line, source):
        return load_sigma(line, source, "sigma")

    @m.fn("sigma_folder", 1, doc=(
        "sigma_folder(path) -> list\n"
        "Load every .yml / .yaml Sigma rule in a folder (and its sub-folders), sorted by file name."))
    def sigma_folder(line, folder):
        check_type(line, folder, "text", "detect.sigma_folder", "path")
        folder = os.path.expanduser(folder)
        if not os.path.isdir(folder):
            fail(line, f"I couldn't find the folder '{folder}'.", path=folder)
        rules = []
        for here, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                if name.lower().endswith((".yml", ".yaml")):
                    rules.append(load_sigma(line, os.path.join(here, name), "sigma_folder"))
        return rules

    @m.fn("parse_yaml", 1, doc=(
        "parse_yaml(text) -> value\n"
        "Read the simple YAML that Sigma rules use (maps, lists, quoted and plain values, | and > text blocks) "
        "into TB maps and lists."))
    def parse_yaml(line, text):
        check_type(line, text, "text", "detect.parse_yaml", "text")
        try:
            return read_yaml(text)
        except YamlError as err:
            at = f" (line {err.lineno})" if err.lineno else ""
            fail(line, f"That YAML doesn't work: {err}{at}.")

    return m
