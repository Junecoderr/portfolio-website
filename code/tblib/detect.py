"""detect: detection rules, threshold alerts, built-in hunts, Sigma rules and Sigma-to-SIEM queries."""

from __future__ import annotations

import base64 as _b64
import datetime as _dt
import fnmatch
import ipaddress
import os
import re
from collections import deque

from tblib._api import Module, check_type, fail, fail_os
from tblib.yaml import YamlError, parse_documents, parse_text as _parse_yaml

import tb as _tb

LEVELS = ["informational", "low", "medium", "high", "critical"]
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
ENCODE_MODS = {"utf16le", "wide", "utf16be", "utf16"}
BASE64_MODS = {"base64", "base64offset"}
MODIFIERS = {"contains", "startswith", "endswith", "all", "re", "cidr", "gt", "gte", "lt", "lte", "exists",
             "windash", "i", "m", "s", "cased", "fieldref", "expand"} | ENCODE_MODS | BASE64_MODS
MODIFIER_LIST = ("contains, startswith, endswith, all, re (with i, m, s), cased, cidr, gt, gte, lt, lte, exists, "
                 "windash, base64, base64offset, utf16, utf16le, utf16be, wide, fieldref or expand")
WINDASH = ["/", "–", "—", "―"]  # what Sigma's |windash swaps a - for
BACKENDS = ["splunk", "elastic", "kql"]
CORRELATION_TYPES = ["event_count", "value_count", "temporal", "temporal_ordered"]
# sshd writes "Invalid user" and a pam_unix line next to each "Failed password":
# count only the main line so one guess counts once.
COMPANION_ACTIONS = {"invalid_user", "auth_failure", "too_many_attempts"}
SAMPLE = 5


class RuleError(Exception):
    """A problem in a rule; turned into a friendly TB error by the caller."""


def read_yaml(text: str):
    """One YAML document (kept for older code; the yaml library does the work)."""
    return _parse_yaml(text)


# =============================================================================
# Matching values
# =============================================================================

def glob_body(pattern: str) -> str:
    """Sigma-style wildcards to a regex body: * any text, ? one character, \\* \\? \\\\ literal."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern) and pattern[i + 1] in "*?\\":
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return "".join(out)


def glob_regex(pattern: str, mode: str, cased=False) -> re.Pattern:
    body = glob_body(pattern)
    if mode in ("contains", "endswith"):
        body = ".*" + body
    if mode in ("contains", "startswith"):
        body = body + ".*"
    return re.compile(body, (0 if cased else re.IGNORECASE) | re.DOTALL)


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


def encode_value(text: str, encoding) -> bytes:
    if encoding in ("utf16le", "wide"):
        return text.encode("utf-16-le")
    if encoding == "utf16be":
        return text.encode("utf-16-be")
    if encoding == "utf16":
        return b"\xff\xfe" + text.encode("utf-16-le")
    return text.encode("utf-8")


def base64_offsets(data: bytes) -> list:
    """The three base64 forms a byte string can take depending on where it
    starts in the encoded stream (Sigma's |base64offset)."""
    starts = (0, 2, 3)
    ends = (None, -3, -2)
    out = []
    for i in range(3):
        encoded = _b64.b64encode(b" " * i + data).decode("ascii")
        out.append(encoded[starts[i]:ends[(len(data) + i) % 3]])
    return out


PLACEHOLDER = re.compile(r"%([A-Za-z0-9_.\-]+)%")


class ValueTest:
    """One field test: field, modifiers and the values to compare with."""

    def __init__(self, field, modifiers, values, where, placeholders=None):
        self.field = field
        self.mods = set(modifiers)
        for mod in modifiers:
            if mod not in MODIFIERS:
                raise RuleError(f"{where}: I don't know the modifier |{mod}. Use {MODIFIER_LIST}")
        self.all = "all" in self.mods
        self.cased = "cased" in self.mods
        self.where = where
        values = values if isinstance(values, list) else [values]
        if not values:
            raise RuleError(f"{where}: the list of values for {field} is empty")
        if "expand" in self.mods:
            values = self.expand(values, placeholders or {})
        if "windash" in self.mods:
            expanded = []
            for v in values:
                expanded.append(v)
                if isinstance(v, str) and "-" in v:
                    expanded.extend(v.replace("-", dash) for dash in WINDASH)
            values = expanded
        encodings = self.mods & ENCODE_MODS
        if len(encodings) > 1:
            raise RuleError(f"{where}: use only one of utf16, utf16le, utf16be or wide")
        self.encoding = next(iter(encodings), None)
        self.base64 = next((m for m in ("base64offset", "base64") if m in self.mods), None)
        if self.encoding and not self.base64:
            raise RuleError(f"{where}: |{self.encoding} needs |base64 or |base64offset after it")
        self.mode = next((m for m in ("contains", "startswith", "endswith") if m in self.mods), "exact")
        self.values = values
        self.tests = []
        for v in values:
            self.tests.extend(self.compile(v, where))

    def expand(self, values, placeholders):
        out = []
        for v in values:
            if not isinstance(v, str) or "%" not in v:
                out.append(v)
                continue
            names = PLACEHOLDER.findall(v)
            if not names:
                out.append(v)
                continue
            variants = [v]
            for name in names:
                if name not in placeholders:
                    known = ", ".join(sorted(placeholders)) or "none"
                    raise RuleError(f"{self.where}: the placeholder %{name}% has no value. Pass placeholders when "
                                    f"loading the rule: detect.sigma(path, {{\"placeholders\": {{\"{name}\": "
                                    f"[...]}}}}) (known: {known})")
                options = placeholders[name]
                options = options if isinstance(options, list) else [options]
                variants = [var.replace(f"%{name}%", _tb.to_text(opt)) for var in variants for opt in options]
            out.extend(variants)
        return out

    def compile(self, value, where):
        mods = self.mods
        if "exists" in mods:
            if not isinstance(value, bool):
                raise RuleError(f"{where}: |exists needs true or false")
            return [("exists", value)]
        if "fieldref" in mods:
            if not isinstance(value, str) or not value:
                raise RuleError(f"{where}: |fieldref needs the name of another field")
            return [("fieldref", value)]
        for op in ("gt", "gte", "lt", "lte"):
            if op in mods:
                number = number_of(value)
                if number is None:
                    raise RuleError(f"{where}: |{op} needs a number, not {_tb.to_text(value, True)}")
                return [(op, number)]
        if "cidr" in mods:
            try:
                return [("cidr", ipaddress.ip_network(str(value), strict=False))]
            except ValueError:
                raise RuleError(f"{where}: \"{value}\" isn't a network like 10.0.0.0/8") from None
        regex_mode = "re" in mods
        if isinstance(value, str) and value.startswith("re:") and not (mods & {"contains", "startswith", "endswith"}):
            regex_mode, value = True, value[3:]
        if regex_mode:
            flags = (re.IGNORECASE if "i" in mods else 0) | (re.MULTILINE if "m" in mods else 0) | (
                re.DOTALL if "s" in mods else 0)
            try:
                return [("re", re.compile(str(value), flags))]
            except re.error as err:
                raise RuleError(f"{where}: the pattern {value!r} doesn't work: {err}") from None
        if self.base64:
            data = encode_value(_tb.to_text(value), self.encoding)
            if self.base64 == "base64offset":
                forms = base64_offsets(data)
                mode = "contains" if self.mode == "exact" else self.mode
            else:
                forms = [_b64.b64encode(data).decode("ascii")]
                mode = self.mode
            return [("glob", glob_regex(form, mode, cased=True)) if mode != "exact" else ("text", form, True)
                    for form in forms]
        if value is None:
            return [("null", None)]
        if isinstance(value, bool):
            return [("bool", value)]
        if _tb.is_number(value) and self.mode == "exact":
            return [("number", value)]
        text = _tb.to_text(value)
        if self.mode == "exact" and "*" not in text and "?" not in text:
            return [("text", text if self.cased else text.casefold(), self.cased)]
        return [("glob", glob_regex(text, self.mode, self.cased))]

    def check(self, actual, test, record=None):
        kind, expected = test[0], test[1]
        if kind == "exists":
            return (actual is not None and actual != "") == expected
        if isinstance(actual, list):
            return any(self.check(item, test, record) for item in actual)
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
        if kind == "fieldref":
            other = get_field(record, expected)
            if other is None:
                return False
            other = other if isinstance(other, str) else _tb.to_text(other)
            a, b = (text, other) if self.cased else (text.casefold(), other.casefold())
            if self.mode == "contains":
                return b in a
            if self.mode == "startswith":
                return a.startswith(b)
            if self.mode == "endswith":
                return a.endswith(b)
            return a == b
        if kind == "text":
            return (text == expected) if test[2] else text.casefold() == expected
        if kind == "glob":
            return expected.fullmatch(text) is not None
        if kind == "re":
            return expected.search(text) is not None
        return False

    def matches(self, record):
        actual = get_field(record, self.field)
        if self.all:
            return all(self.check(actual, t, record) for t in self.tests)
        return any(self.check(actual, t, record) for t in self.tests)


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


def compile_where(where, label, placeholders=None) -> list:
    if not isinstance(where, dict):
        raise RuleError(f"{label} should be a map of field: value")
    tests = []
    for key, value in where.items():
        if not isinstance(key, str) or not key:
            raise RuleError(f"{label}: field names must be text")
        field, *mods = key.split("|")
        tests.append(ValueTest(field, [m.lower() for m in mods], value, f"{label}, field \"{key}\"", placeholders))
    return tests


class Keywords:
    """A Sigma keyword list: any of the words appears in the message or raw line."""

    def __init__(self, words, where):
        if not words:
            raise RuleError(f"{where}: the keyword list is empty")
        self.words = [_tb.to_text(w) for w in words]
        self.tests = [glob_regex(w, "contains") for w in self.words]

    def matches(self, record):
        texts = [t for t in (get_field(record, "message"), get_field(record, "raw"), get_field(record, "Message"))
                 if isinstance(t, str)]
        return any(t.fullmatch(text) for t in self.tests for text in texts)


class Selection:
    def __init__(self, name, value, rule_where="The rule", placeholders=None):
        where = f"{rule_where}, selection \"{name}\""
        self.name = name
        self.alternatives = []  # OR of (AND lists)
        if isinstance(value, dict):
            self.alternatives.append(compile_where(value, where, placeholders))
        elif isinstance(value, list):
            if not value:
                raise RuleError(f"{where} is empty")
            if all(isinstance(v, dict) for v in value):
                for v in value:
                    self.alternatives.append(compile_where(v, where, placeholders))
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


AGGREGATION = re.compile(
    r"^(count|min|max|avg|sum)\(\s*([\w.]*)\s*\)(?:\s+by\s+([\w.]+(?:\s*,\s*[\w.]+)*))?\s*(>=|>|==|=|<=|<)\s*(\d+(?:\.\d+)?)$",
    re.IGNORECASE)
NEAR = re.compile(r"^near\s+(.+)$", re.IGNORECASE)
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "=": lambda a, b: a == b, "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
       "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b, "eq": lambda a, b: a == b, "neq": lambda a, b: a != b}


def parse_aggregation(text, where):
    """'count() by ip > 5' -> {"fn", "field", "by", "op", "value"}; 'near x and not y' -> {"near": ...}."""
    text = text.strip()
    m = NEAR.match(text)
    if m:
        required, excluded = [], []
        for part in re.split(r"\s+and\s+", m.group(1).strip(), flags=re.IGNORECASE):
            part = part.strip()
            if part.lower().startswith("not "):
                excluded.append(part[4:].strip())
            elif part:
                required.append(part)
        if not required:
            raise RuleError(f"{where}: \"| near\" needs at least one selection that must be nearby")
        return {"near": {"required": required, "excluded": excluded}}
    m = AGGREGATION.match(text)
    if not m:
        raise RuleError(f"{where}: I can't read the aggregation \"| {text}\". Use \"| count() by field > N\", "
                        "\"| count(field) by other > N\" (distinct values), \"| min/max/avg/sum(field) by other > N\" "
                        "or \"| near other_selection\"")
    fn, field, by, op, number = m.groups()
    fn = fn.lower()
    if fn != "count" and not field:
        raise RuleError(f"{where}: {fn}() needs a field: {fn}(bytes)")
    value = float(number) if "." in number else int(number)
    by_fields = [b.strip() for b in by.split(",")] if by else None
    return {"fn": fn, "field": field or None, "by": by_fields, "op": "==" if op == "=" else op, "value": value}


def aggregation_text(agg) -> str:
    by = f" by {', '.join(agg['by'])}" if agg.get("by") else ""
    return f"{agg['fn']}({agg['field'] or ''}){by} {agg['op']} {_tb.to_text(agg['value'])}"


TIMEFRAME = re.compile(r"^(\d+)\s*([smhdwSMHDW])$")


def parse_timeframe(value, where):
    m = TIMEFRAME.match(str(value).strip())
    if not m:
        raise RuleError(f"{where}: timeframe \"{value}\" should look like 30s, 5m, 1h or 1d")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2).lower()]


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
        self.sub_rules = []
        where = f"The rule \"{self.name}\""
        placeholders = rule.get("placeholders") if isinstance(rule.get("placeholders"), dict) else None
        correlation = rule.get("correlation")
        if correlation is not None:
            if not isinstance(correlation, dict) or not isinstance(correlation.get("rules"), list):
                raise RuleError(f"{where}: correlation needs a map with type, rules, group_by and timespan")
            self.sub_rules = [CompiledRule(normalise_rule(r, "the correlation"), api, line) for r in correlation["rules"]]
        if "detection" in rule and rule["detection"] is not None:
            detection = rule["detection"]
            for key, value in detection.items():
                if key in ("condition", "timeframe"):
                    continue
                self.selections[key] = Selection(key, value, where, placeholders)
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
            near = rule.get("near")
            if near:
                for name in near.get("required", []) + near.get("excluded", []):
                    if name not in self.selections:
                        raise RuleError(f"{where}: \"| near {name}\" names a selection that doesn't exist")
        where_value = rule.get("where")
        if where_value is not None:
            if isinstance(where_value, dict):
                self.where = compile_where(where_value, f"{where}'s where", placeholders)
            elif _is_function(where_value):
                self.where_fn = where_value
            else:
                raise RuleError(f"{where}: where should be a map of field: value, or a function")
        if rule.get("unless") is not None:
            self.unless = compile_where(rule["unless"], f"{where}'s unless", placeholders)
        if self.condition is None and not self.where and self.where_fn is None and correlation is None:
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
            if not eval_condition(self.condition, self.checker(record)):
                return False
        if self.unless and all(t.matches(record) for t in self.unless):
            return False
        return True

    def checker(self, record):
        cache = {}

        def check(name):
            if name not in cache:
                cache[name] = self.selections[name].matches(record)
            return cache[name]
        return check

    def selection_matches(self, name, record):
        return self.selections[name].matches(record)


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
    fields = rule.get("fields")
    if fields is not None and not (isinstance(fields, list) and all(isinstance(f, str) for f in fields)):
        raise RuleError(f"The rule \"{rule['name']}\": fields should be a list of field names")
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
    if rule.get("tags"):
        alert["tags"] = list(rule["tags"])
    if rule.get("fields") and records:
        alert["fields"] = {f: get_field(records[0], f) for f in rule["fields"]}
    if extra:
        alert.update(extra)
    alert["records"] = list(records[:rule.get("sample", SAMPLE) if _tb.is_whole(rule.get("sample")) else SAMPLE])
    return alert


def group_key(record, by, lookup=None):
    fields = by if isinstance(by, list) else [by]
    values = [(lookup or get_field)(record, f) for f in fields]
    if any(v is None or v == "" for v in values):
        return None
    if len(values) == 1:
        return values[0] if not isinstance(values[0], (list, dict)) else _tb.to_text(values[0])
    return " / ".join(_tb.to_text(v) for v in values)


def grouped(matched, by, lookup=None):
    groups: dict = {}
    for record in matched:
        key = group_key(record, by, lookup) if by else "__all__"
        if key is None:
            continue
        groups.setdefault(key, []).append(record)
    return groups


def by_label(by):
    return by if isinstance(by, str) else (" / ".join(by) if by else None)


def threshold_alerts(rule, matched):
    if rule.get("aggregation") and (rule["aggregation"]["fn"] != "count" or rule["aggregation"]["field"]
                                    or rule["aggregation"]["op"] in ("<", "<=", "==")):
        return aggregate_alerts(rule, matched)
    by = rule.get("by")
    count = rule["count"]
    within = rule.get("within")
    groups = grouped(matched, by)
    by_field = by_label(by)
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
    return bursts(timed, within, lambda window: sum(weight(r) for r in window) >= count)


def bursts(timed, within, satisfied):
    """Generic sliding window: yield one burst each time the window of records
    within `within` seconds satisfies the test; the burst then keeps growing
    while records keep arriving within `within` seconds of the previous one."""
    window, current, last = deque(), None, None
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
        while window and time_of(window[0]) < ts - within:
            window.popleft()
        if satisfied(window):
            current, last = list(window), ts
            window.clear()
    if current is not None:
        yield current


def metric_of(agg, records):
    fn, field = agg["fn"], agg["field"]
    if fn == "count":
        if not field:
            return sum(weight(r) for r in records)
        seen = set()
        for r in records:
            v = get_field(r, field)
            if v is not None and v != "":
                seen.add(_tb.to_text(v))
        return len(seen)
    numbers = [n for n in (number_of(get_field(r, field)) for r in records) if n is not None]
    if not numbers:
        return None
    if fn == "min":
        return min(numbers)
    if fn == "max":
        return max(numbers)
    if fn == "sum":
        return _tb.tidy_number(sum(numbers))
    return _tb.tidy_number(sum(numbers) / len(numbers))


def aggregate_alerts(rule, matched):
    agg = rule["aggregation"]
    by = agg.get("by")
    within = rule.get("within")
    compare = OPS[agg["op"]]
    label = aggregation_text(agg)
    alerts = []

    def alert_for(key, records):
        value = metric_of(agg, records)
        if value is None or not compare(value, agg["value"]):
            return None
        extra = {"value": value, "aggregation": label}
        count = value if agg["fn"] == "count" else None
        return make_alert(rule, None if key == "__all__" else key, by_label(by), records, count=count, extra=extra)

    for key, records in grouped(matched, by).items():
        if within is None or agg["op"] in ("<", "<="):
            alert = alert_for(key, records)
            if alert:
                alerts.append(alert)
            continue
        timed = sorted((r for r in records if time_of(r) is not None), key=time_of)

        def satisfied(window):
            value = metric_of(agg, window)
            return value is not None and compare(value, agg["value"])

        for burst in bursts(timed, within, satisfied):
            alert = alert_for(key, burst)
            if alert:
                alerts.append(alert)
    return alerts


def near_alerts(compiled, records):
    """'selection | near other and not third': alert when a matching record has
    the required selections within the timeframe around it (and none excluded)."""
    rule = compiled.rule
    near = rule["near"]
    within = rule.get("within")
    timed = sorted((r for r in records if isinstance(r, dict) and time_of(r) is not None), key=time_of)
    anchors = [r for r in timed if compiled.matches(r)]
    alerts, used = [], set()
    for anchor in anchors:
        if id(anchor) in used:
            continue
        ts = time_of(anchor)
        nearby = [r for r in timed if within is None or abs(time_of(r) - ts) <= within]
        found = {name: [r for r in nearby if compiled.selection_matches(name, r)] for name in near["required"]}
        if not all(found.values()):
            continue
        if any(compiled.selection_matches(name, r) for name in near["excluded"] for r in nearby):
            continue
        involved = [anchor]
        for name in near["required"]:
            involved.extend(r for r in found[name] if r is not anchor)
        involved.sort(key=time_of)
        for r in involved:
            used.add(id(r))
        alerts.append(make_alert(rule, None, None, involved, extra={"near": list(near["required"])}))
    return alerts


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
        if rule.rule.get("correlation"):
            alerts.extend(correlation_alerts(rule, records))
            continue
        if rule.rule.get("near"):
            alerts.extend(near_alerts(rule, records))
            continue
        matched = [r for r in records if rule.matches(r)]
        if matched:
            alerts.extend(threshold_alerts(rule.rule, matched))
    return sort_alerts(alerts)


# =============================================================================
# Sigma correlation rules
# =============================================================================

def correlation_alerts(compiled, records):
    rule = compiled.rule
    corr = rule["correlation"]
    kind = corr["type"]
    within = corr.get("timespan")
    group_by = corr.get("group_by") or []
    aliases = corr.get("aliases") or {}
    condition = corr.get("condition") or {}
    names = [sub.name for sub in compiled.sub_rules]

    def field_for(rule_name, field):
        alias = aliases.get(field)
        if isinstance(alias, dict):
            return alias.get(rule_name, field)
        return field

    events = []  # (time, rule name, record)
    for r in records:
        if not isinstance(r, dict):
            continue
        for sub in compiled.sub_rules:
            if sub.matches(r):
                events.append((time_of(r), sub.name, r))
    groups: dict = {}
    for ts, name, r in events:
        if group_by:
            values = [get_field(r, field_for(name, f)) for f in group_by]
            if any(v is None or v == "" for v in values):
                continue
            key = values[0] if len(values) == 1 and not isinstance(values[0], (list, dict)) else \
                " / ".join(_tb.to_text(v) for v in values)
        else:
            key = "__all__"
        groups.setdefault(key, []).append((ts, name, r))
    op = OPS.get(condition.get("op", "gte"), OPS["gte"])
    threshold = condition.get("value", 1)
    value_field = condition.get("field")

    def measure(window):
        if kind == "event_count":
            return len(window)
        if kind == "value_count":
            seen = set()
            for _ts, name, r in window:
                v = get_field(r, field_for(name, value_field)) if value_field else None
                if v is not None and v != "":
                    seen.add(_tb.to_text(v))
            return len(seen)
        return len({name for _ts, name, _r in window})

    def satisfied(window):
        if kind in ("event_count", "value_count"):
            return op(measure(window), threshold)
        seen = [name for _ts, name, _r in window]
        if kind == "temporal":
            return all(n in seen for n in names)
        pos = 0  # temporal_ordered: the rules fire in the listed order
        for n in names:
            try:
                pos = seen.index(n, pos) + 1
            except ValueError:
                return False
        return True

    alerts = []
    for key, evs in groups.items():
        by_value = None if key == "__all__" else key
        timed = sorted((e for e in evs if e[0] is not None), key=lambda e: e[0])
        if within is None or (kind in ("event_count", "value_count") and condition.get("op") in ("lt", "lte")):
            windows = [evs] if satisfied(evs) else []
        else:
            windows = []
            window, current, last = deque(), None, None
            for ev in timed:
                ts = ev[0]
                if current is not None:
                    if ts - last <= within:
                        current.append(ev)
                        last = ts
                        continue
                    windows.append(current)
                    current = None
                window.append(ev)
                while window and window[0][0] < ts - within:
                    window.popleft()
                if satisfied(window):
                    current, last = list(window), ts
                    window.clear()
            if current is not None:
                windows.append(current)
        for window in windows:
            recs = [r for _ts, _n, r in window]
            fired = []
            for _ts, name, _r in window:
                if name not in fired:
                    fired.append(name)
            count = measure(window) if kind in ("event_count", "value_count") else len(window)
            alerts.append(make_alert(rule, by_value, by_label(group_by) if group_by else None, recs, count=count,
                                     extra={"rules": fired, "correlation": kind}))
    return alerts


# =============================================================================
# Sigma rules
# =============================================================================

# logsource -> which record formats from the logs library to look at, and
# where the same data lives in each SIEM (used by sigma_to_query).
LOGSOURCES = [
    {"product": "windows", "service": "security", "formats": ["windows"],
     "splunk": 'source="WinEventLog:Security"', "kql": "SecurityEvent", "elastic": "winlog.channel:Security"},
    {"product": "windows", "service": "sysmon", "formats": ["windows"],
     "splunk": 'source="WinEventLog:Microsoft-Windows-Sysmon/Operational"', "kql": "SysmonEvent",
     "elastic": "winlog.channel:Microsoft-Windows-Sysmon/Operational"},
    {"product": "windows", "category": "process_creation", "formats": ["windows"],
     "splunk": 'source="WinEventLog:Microsoft-Windows-Sysmon/Operational" EventCode=1', "kql": "DeviceProcessEvents",
     "elastic": "event.category:process AND event.type:start"},
    {"product": "windows", "service": "powershell", "formats": ["windows"],
     "splunk": 'source="WinEventLog:Microsoft-Windows-PowerShell/Operational"', "kql": "Event",
     "elastic": "winlog.channel:Microsoft-Windows-PowerShell/Operational"},
    {"product": "windows", "formats": ["windows"], "splunk": 'source="WinEventLog:*"', "kql": "SecurityEvent",
     "elastic": "agent.type:winlogbeat"},
    {"product": "linux", "service": "auth", "formats": ["auth", "syslog"], "splunk": 'sourcetype="linux_secure"',
     "kql": "Syslog", "elastic": "event.dataset:system.auth"},
    {"product": "linux", "service": "sshd", "formats": ["auth", "syslog"], "splunk": 'sourcetype="linux_secure"',
     "kql": "Syslog", "elastic": "event.dataset:system.auth AND process.name:sshd"},
    {"product": "linux", "service": "sudo", "formats": ["auth", "syslog"], "splunk": 'sourcetype="linux_secure"',
     "kql": "Syslog", "elastic": "event.dataset:system.auth AND process.name:sudo"},
    {"product": "linux", "service": "auditd", "formats": ["auth", "syslog"], "splunk": 'sourcetype="linux:audit"',
     "kql": "AuditLog", "elastic": "event.module:auditd"},
    {"product": "linux", "category": "process_creation", "formats": ["auth", "syslog", "json"],
     "splunk": 'sourcetype="linux:audit" type=EXECVE', "kql": "Syslog", "elastic": "event.category:process"},
    {"product": "linux", "formats": ["auth", "syslog", "text"], "splunk": 'sourcetype="syslog"', "kql": "Syslog",
     "elastic": "host.os.type:linux"},
    {"category": "webserver", "formats": ["apache", "nginx", "json", "csv"], "splunk": 'sourcetype="access_combined"',
     "kql": "W3CIISLog", "elastic": "event.category:web"},
    {"category": "proxy", "formats": ["apache", "nginx", "json", "csv"], "splunk": 'sourcetype="squid"',
     "kql": "CommonSecurityLog", "elastic": "event.category:web"},
    {"product": "apache", "formats": ["apache", "nginx", "json", "csv"], "splunk": 'sourcetype="access_combined"',
     "kql": "W3CIISLog", "elastic": "event.dataset:apache.access"},
    {"product": "nginx", "formats": ["apache", "nginx", "json", "csv"], "splunk": 'sourcetype="nginx:plus:access"',
     "kql": "W3CIISLog", "elastic": "event.dataset:nginx.access"},
    {"category": "firewall", "formats": ["syslog", "json", "csv"], "splunk": 'sourcetype="firewall"',
     "kql": "CommonSecurityLog", "elastic": "event.category:network"},
    {"category": "dns", "formats": ["syslog", "json", "csv"], "splunk": 'sourcetype="dns"', "kql": "DnsEvents",
     "elastic": "event.category:network AND network.protocol:dns"},
    {"product": "aws", "service": "cloudtrail", "formats": ["json"], "splunk": 'sourcetype="aws:cloudtrail"',
     "kql": "AWSCloudTrail", "elastic": "event.dataset:aws.cloudtrail"},
    {"product": "azure", "formats": ["json"], "splunk": 'sourcetype="azure:monitor:aad"', "kql": "AuditLogs",
     "elastic": "event.dataset:azure.auditlogs"},
    {"product": "m365", "formats": ["json"], "splunk": 'sourcetype="o365:management:activity"', "kql": "OfficeActivity",
     "elastic": "event.dataset:o365.audit"},
]


def logsource_entry(logsource):
    if not isinstance(logsource, dict):
        return None
    want = {k: str(logsource.get(k) or "").lower() for k in ("product", "service", "category")}
    best, best_score = None, 0
    for entry in LOGSOURCES:
        score = 0
        ok = True
        for k in ("product", "service", "category"):
            if k in entry:
                if want[k] == entry[k]:
                    score += 2 if k != "product" else 1
                else:
                    ok = False
                    break
        if ok and score > best_score:
            best, best_score = entry, score
    return best


def logsource_formats(logsource):
    entry = logsource_entry(logsource)
    return list(entry["formats"]) if entry else None


def sigma_rule(data, source, placeholders=None):
    where = f"The Sigma rule {source}"
    if not isinstance(data, dict):
        raise RuleError(f"{where} should be a map with title, detection and so on")
    if "correlation" in data:
        raise RuleError(f"{where} is a correlation rule; it needs the rules it refers to in the same file "
                        "(--- separated), loaded with detect.sigma() or detect.sigma_all()")
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
    count, by, within, aggregation, near = 1, None, None, None, None
    for text in conditions:
        if not isinstance(text, str):
            raise RuleError(f"{where}: the condition should be text")
        if "|" in text:
            if len(conditions) > 1:
                raise RuleError(f"{where}: aggregations (| count()) only work with a single condition")
            parsed = parse_aggregation(text.split("|", 1)[1], where)
            if "near" in parsed:
                near = parsed["near"]
            else:
                aggregation = parsed
                if parsed["fn"] == "count" and not parsed["field"] and parsed["op"] in (">", ">="):
                    count = parsed["value"] + 1 if parsed["op"] == ">" else parsed["value"]
                    if not _tb.is_whole(count):
                        raise RuleError(f"{where}: count() needs a whole number")
                    count = int(count)
                    by_fields = parsed["by"]
                    by = by_fields[0] if by_fields and len(by_fields) == 1 else by_fields
    timeframe = data.get("timeframe") or detection.get("timeframe")
    if timeframe is not None:
        within = parse_timeframe(timeframe, where)
    level = data.get("level") or "medium"
    if str(level).lower() not in LEVEL_RANK:
        raise RuleError(f"{where}: level should be one of {', '.join(LEVELS)}, not {_tb.to_text(level, True)}")
    fields = data.get("fields")
    if fields is not None and not (isinstance(fields, list) and all(isinstance(f, str) for f in fields)):
        raise RuleError(f"{where}: fields: should be a list of field names")
    rule = {
        "name": title, "level": str(level).lower(),
        "description": (_tb.to_text(data.get("description") or "")).strip() or None,
        "id": data.get("id"), "status": data.get("status"),
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "logsource": data.get("logsource") if isinstance(data.get("logsource"), dict) else {},
        "formats": logsource_formats(data.get("logsource")),
        "detection": detection, "condition": condition,
        "count": count, "within": within, "by": by, "source": "sigma",
        "fields": fields, "author": data.get("author"), "references": data.get("references"),
        "falsepositives": data.get("falsepositives"),
        "sigma_name": _tb.to_text(data["name"]) if data.get("name") is not None else None,
    }
    if aggregation:
        rule["aggregation"] = aggregation
    if near:
        rule["near"] = near
    if placeholders:
        rule["placeholders"] = placeholders
    CompiledRule(normalise_rule(rule))  # check everything now, so mistakes show at load time
    return rule


def correlation_rule(data, rules_by_name, source):
    where = f"The Sigma correlation rule {source}"
    title = _tb.to_text(data.get("title") or data.get("name") or "Untitled correlation")
    where = f"The Sigma correlation rule \"{title}\""
    corr = data.get("correlation")
    if not isinstance(corr, dict):
        raise RuleError(f"{where}: correlation: should be a map")
    kind = str(corr.get("type") or "").lower()
    if kind not in CORRELATION_TYPES:
        raise RuleError(f"{where}: correlation type should be one of {', '.join(CORRELATION_TYPES)}, "
                        f"not {_tb.to_text(corr.get('type'), True)}")
    refs = corr.get("rules")
    refs = refs if isinstance(refs, list) else ([refs] if refs is not None else [])
    if not refs:
        raise RuleError(f"{where}: correlation: needs rules: (the names of the rules it combines)")
    subs = []
    for ref in refs:
        ref_text = _tb.to_text(ref)
        found = rules_by_name.get(ref_text)
        if found is None:
            known = ", ".join(sorted(k for k in rules_by_name if not k.startswith("id:"))) or "none"
            raise RuleError(f"{where}: it refers to the rule \"{ref_text}\", but there's no rule with that name "
                            f"or id in the file (rules: {known})")
        subs.append(found)
    group_by = corr.get("group-by", corr.get("group_by"))
    group_by = [group_by] if isinstance(group_by, str) else (group_by or [])
    if not all(isinstance(g, str) for g in group_by):
        raise RuleError(f"{where}: group-by should be a list of field names")
    timespan = corr.get("timespan")
    within = parse_timeframe(timespan, where) if timespan is not None else None
    if within is None and kind in ("temporal", "temporal_ordered"):
        raise RuleError(f"{where}: a {kind} correlation needs a timespan: like 5m")
    condition = None
    raw_condition = corr.get("condition")
    if kind in ("event_count", "value_count"):
        if not isinstance(raw_condition, dict):
            raise RuleError(f"{where}: a {kind} correlation needs a condition: like {{gte: 10}}")
        ops = [k for k in raw_condition if k in ("gt", "gte", "lt", "lte", "eq", "neq")]
        if len(ops) != 1:
            raise RuleError(f"{where}: condition: needs exactly one of gt, gte, lt, lte, eq or neq")
        value = raw_condition[ops[0]]
        if not _tb.is_number(value):
            raise RuleError(f"{where}: the condition value should be a number")
        condition = {"op": ops[0], "value": value}
        if kind == "value_count":
            field = raw_condition.get("field")
            if not isinstance(field, str) or not field:
                raise RuleError(f"{where}: a value_count correlation needs condition: {{field: name, gte: N}}")
            condition["field"] = field
    aliases = corr.get("aliases") if isinstance(corr.get("aliases"), dict) else {}
    level = data.get("level") or max((s["level"] for s in subs), key=lambda lv: LEVEL_RANK[lv])
    if str(level).lower() not in LEVEL_RANK:
        raise RuleError(f"{where}: level should be one of {', '.join(LEVELS)}, not {_tb.to_text(level, True)}")
    rule = {
        "name": title, "level": str(level).lower(),
        "description": (_tb.to_text(data.get("description") or "")).strip() or None,
        "id": data.get("id"), "status": data.get("status"),
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "logsource": {}, "formats": None, "source": "sigma",
        "count": 1, "within": within, "by": group_by or None,
        "correlation": {"type": kind, "rules": subs, "rule_names": [s["name"] for s in subs],
                        "group_by": group_by, "timespan": within, "condition": condition, "aliases": aliases,
                        "generate": bool(corr.get("generate", False))},
    }
    CompiledRule(normalise_rule(rule))
    return rule


def sigma_rules(text, source, placeholders=None):
    """Every rule in a Sigma YAML text (one or several documents), as rule maps.
    Correlation rules embed the rules they refer to; a rule only used by a
    correlation (and not meant to alert by itself) is still returned."""
    docs = [d for d in parse_documents(text) if d is not None]
    if not docs:
        raise RuleError(f"The Sigma rule {source} is empty")
    plain, correlations = [], []
    for doc in docs:
        if isinstance(doc, dict) and "correlation" in doc:
            correlations.append(doc)
        else:
            plain.append(sigma_rule(doc, source, placeholders))
    by_name = {}
    for rule in plain:
        by_name[rule["name"]] = rule
        if rule.get("id"):
            by_name[_tb.to_text(rule["id"])] = rule
        if rule.get("sigma_name"):
            by_name[rule["sigma_name"]] = rule
    results = []
    hidden = set()
    for doc in correlations:
        rule = correlation_rule(doc, by_name, source)
        rule["hidden"] = False
        results.append(rule)
        if not rule["correlation"]["generate"]:
            hidden.update(id(s) for s in rule["correlation"]["rules"])
    for rule in plain:
        results.append(rule)
    for rule in plain:
        rule["hidden"] = id(rule) in hidden
    return results


# =============================================================================
# Sigma -> SIEM queries
# =============================================================================

# Sigma's generic field names -> where each SIEM keeps them (users can add more).
FIELD_MAPS = {
    "splunk": {
        "windows": {"EventID": "EventCode", "ComputerName": "host", "User": "user"},
        "linux": {"ip": "src", "host": "host"},
        "default": {},
    },
    "elastic": {
        "default": {
            "Image": "process.executable", "CommandLine": "process.command_line", "ParentImage": "process.parent.executable",
            "ParentCommandLine": "process.parent.command_line", "User": "user.name", "TargetUserName": "user.target.name",
            "SubjectUserName": "user.name", "EventID": "event.code", "IpAddress": "source.ip", "ComputerName": "host.name",
            "OriginalFileName": "process.pe.original_file_name", "Hashes": "process.hash.*", "DestinationIp": "destination.ip",
            "DestinationPort": "destination.port", "SourceIp": "source.ip", "SourcePort": "source.port",
            "TargetFilename": "file.path", "QueryName": "dns.question.name", "ip": "source.ip", "src_ip": "source.ip",
            "dst_ip": "destination.ip", "dst_port": "destination.port", "user": "user.name", "path": "url.path",
            "agent": "user_agent.original", "status": "http.response.status_code", "method": "http.request.method",
            "action": "event.action", "message": "message", "host": "host.name",
        },
    },
    "kql": {
        "SecurityEvent": {"Image": "NewProcessName", "ParentImage": "ParentProcessName", "User": "SubjectUserName",
                          "ComputerName": "Computer", "user": "TargetUserName", "ip": "IpAddress"},
        "DeviceProcessEvents": {"Image": "FolderPath", "CommandLine": "ProcessCommandLine", "ParentImage": "InitiatingProcessFolderPath",
                                "ParentCommandLine": "InitiatingProcessCommandLine", "User": "AccountName",
                                "ComputerName": "DeviceName", "OriginalFileName": "ProcessVersionInfoOriginalFileName",
                                "IntegrityLevel": "ProcessIntegrityLevel", "md5": "MD5", "sha256": "SHA256"},
        "SysmonEvent": {"ComputerName": "Computer"},
        "Syslog": {"message": "SyslogMessage", "host": "Computer", "process": "ProcessName"},
        "W3CIISLog": {"path": "csUriStem", "agent": "csUserAgent", "status": "scStatus", "method": "csMethod", "ip": "cIP",
                      "user": "csUserName"},
        "CommonSecurityLog": {"src_ip": "SourceIP", "dst_ip": "DestinationIP", "dst_port": "DestinationPort",
                              "src_port": "SourcePort", "action": "DeviceAction"},
        "default": {"ComputerName": "Computer"},
    },
}


class Backend:
    """Turns compiled selections and conditions into one SIEM's query language."""

    name = ""

    def __init__(self, rule, options):
        self.rule = rule
        self.options = options or {}
        self.entry = logsource_entry(rule.get("logsource"))
        self.table = self.options.get("table") or (self.entry[self.name] if self.entry and self.name in self.entry else None)
        self.fields = dict(FIELD_MAPS[self.name].get("default", {}))
        self.fields.update(FIELD_MAPS[self.name].get(self.table_key(), {}))
        extra = self.options.get("fields")
        if isinstance(extra, dict):
            self.fields.update({str(k): _tb.to_text(v) for k, v in extra.items()})
        self.post = []  # pipeline stages added after the search (splunk's | regex)

    def table_key(self):
        if self.name == "splunk":
            return str((self.rule.get("logsource") or {}).get("product") or "").lower()
        return self.table or ""

    def field(self, name):
        return self.fields.get(name, name)

    # -- building --------------------------------------------------------------------

    def build(self, compiled: CompiledRule) -> str:
        if compiled.rule.get("correlation"):
            raise RuleError(f"detect.sigma_to_query() can't convert correlation rules yet; convert the rules inside "
                            f"({', '.join(compiled.rule['correlation']['rule_names'])}) one by one")
        if compiled.rule.get("near"):
            raise RuleError("detect.sigma_to_query() can't convert \"| near\" conditions; they need the SIEM's own "
                            "correlation features")
        if compiled.condition is not None:
            body = self.condition(compiled.condition, compiled.selections, top=True)
        else:
            parts = [self.value_test(t, True) for t in compiled.where]
            body = self.join_and(parts)
            if compiled.unless:
                body = self.join_and([body, self.negate(self.join_and([self.value_test(t) for t in compiled.unless]))])
        return self.finish(body)

    def condition(self, node, selections, top=False):
        kind = node[0]
        if kind == "sel":
            return self.selection(selections[node[1]], top)
        if kind == "and":
            return self.join_and([self.condition(node[1], selections, top), self.condition(node[2], selections, top)])
        if kind == "or":
            return self.join_or([self.condition(node[1], selections), self.condition(node[2], selections)])
        if kind == "not":
            return self.negate(self.condition(node[1], selections))
        if kind == "any":
            return self.join_or([self.selection(selections[n]) for n in node[1]])
        if kind == "all":
            return self.join_and([self.selection(selections[n], top) for n in node[1]])
        raise RuleError("unknown condition")

    def selection(self, sel: Selection, top=False):
        alternatives = []
        for tests in sel.alternatives:
            alternatives.append(self.join_and([self.value_test(t, top and len(sel.alternatives) == 1) for t in tests]))
        return self.join_or(alternatives)

    def value_test(self, test, top=False):
        if isinstance(test, Keywords):
            return self.join_or([self.keyword(w) for w in test.words])
        parts = [self.one(test, t, top) for t in test.tests]
        return self.join_and(parts) if test.all else self.join_or(parts)

    def join_and(self, parts):
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return parts[0]
        return self.and_word.join(self.wrap(p) for p in parts)

    def join_or(self, parts):
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return parts[0]
        return self.or_word.join(self.wrap(p) for p in parts)

    def wrap(self, text):
        if (" AND " in text or " OR " in text or " and " in text or " or " in text) and not (
                text.startswith("(") and text.endswith(")") and balanced(text[1:-1])):
            return f"({text})"
        return text

    def one(self, vt, test, top):
        raise NotImplementedError

    def keyword(self, word):
        raise NotImplementedError

    def negate(self, text):
        raise NotImplementedError

    def finish(self, body):
        raise NotImplementedError

    def unsupported(self, what):
        raise RuleError(f"the {self.name} backend can't express {what}")


def strip_quoted(text):
    """The text with everything inside double quotes removed."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)


def balanced(text):
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def glob_text_of(test):
    """The original wildcard text of a glob test (regex -> Sigma wildcards)."""
    body = test[1].pattern
    if body.startswith(".*"):
        body = body[2:]
    if body.endswith(".*"):
        body = body[:-2]
    out, i = [], 0
    while i < len(body):
        if body.startswith(".*", i):
            out.append("*")
            i += 2
        elif body[i] == "." :
            out.append("?")
            i += 1
        elif body[i] == "\\" and i + 1 < len(body):
            out.append(body[i + 1])
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def wildcard_value(vt, test):
    """Text with Sigma wildcards for a text/glob test, using the modifier mode."""
    if test[0] == "text":
        text = test[1] if test[2] else vt_original(vt, test)
    else:
        text = glob_text_of(test)
    mode = vt.mode if not vt.base64 else ("contains" if vt.base64 == "base64offset" and vt.mode == "exact" else vt.mode)
    if test[0] == "glob" and test[1].pattern.startswith(".*") and mode == "exact":
        mode = "contains" if test[1].pattern.endswith(".*") else "endswith"
    elif test[0] == "glob" and test[1].pattern.endswith(".*") and mode == "exact":
        mode = "startswith"
    if mode == "contains":
        return f"*{text}*"
    if mode == "startswith":
        return f"{text}*"
    if mode == "endswith":
        return f"*{text}"
    return text


def vt_original(vt, test):
    """The value as written in the rule (the compiled text is casefolded)."""
    for v in vt.values:
        if isinstance(v, str) and v.casefold() == test[1]:
            return v
    return test[1]


class Splunk(Backend):
    name = "splunk"
    and_word = " "
    or_word = " OR "

    def join_or(self, parts):
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return parts[0]
        return " OR ".join(f"({p})" if " " in strip_quoted(p) and not (p.startswith("(") and p.endswith(")")
                                                                  and balanced(p[1:-1])) else p for p in parts)

    def esc(self, text):
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def one(self, vt, test, top):
        field = self.field(vt.field)
        kind = test[0]
        if kind in ("text", "glob"):
            return f'{field}="{self.esc(wildcard_value(vt, test))}"'
        if kind == "number":
            return f"{field}={_tb.to_text(test[1])}"
        if kind == "bool":
            return f'{field}="{"true" if test[1] else "false"}"'
        if kind == "null":
            return f"NOT {field}=*"
        if kind == "exists":
            return f"{field}=*" if test[1] else f"NOT {field}=*"
        if kind in ("gt", "gte", "lt", "lte"):
            return f"{field}{ {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[kind] }{_tb.to_text(test[1])}"
        if kind == "cidr":
            net = test[1]
            if isinstance(net, ipaddress.IPv4Network) and net.prefixlen in (8, 16, 24, 32):
                octets = str(net.network_address).split(".")[:net.prefixlen // 8]
                return f'{field}="{".".join(octets)}{".*" if net.prefixlen < 32 else ""}"'
            self.post.append(f'| where cidrmatch("{net}", {field})')
            return f"{field}=*"
        if kind == "re":
            if not top:
                raise RuleError("the splunk backend can only use |re in a selection that is ANDed at the top of the "
                                "condition (it becomes a \"| regex\" stage); move it or use the kql / elastic backend")
            self.post.append(f'| regex {field}="{self.esc(test[1].pattern)}"')
            return ""
        if kind == "fieldref":
            if not top:
                raise RuleError("the splunk backend can only use |fieldref at the top of the condition")
            self.post.append(f"| where {field}={self.field(test[1])}")
            return ""
        self.unsupported(kind)

    def keyword(self, word):
        return f'"{self.esc(word)}"'

    def negate(self, text):
        return f"NOT {self.wrap(text)}"

    def finish(self, body):
        parts = []
        index = self.options.get("index")
        if index:
            parts.append(f"index={index}")
        if self.table:
            parts.append(self.table)
        if body:
            parts.append(body)
        query = " ".join(parts) or "*"
        for stage in self.post:
            query += " " + stage
        agg = self.rule.get("aggregation")
        if agg:
            fn = {"count": "count", "min": "min", "max": "max", "avg": "avg", "sum": "sum"}[agg["fn"]]
            field = self.field(agg["field"]) if agg["field"] else None
            if agg["fn"] == "count" and field:
                expr, name = f"dc({field})", "distinct_count"
            elif field:
                expr, name = f"{fn}({field})", fn
            else:
                expr, name = "count", "count"
            groups = [self.field(b) for b in (agg["by"] or [])]
            within = self.rule.get("within")
            if within:
                query += f" | bin _time span={int(within)}s"
                groups.insert(0, "_time")
            by = f" by {', '.join(groups)}" if groups else ""
            stats = f"| stats {expr} as {name}{by}" if expr != name else f"| stats {expr}{by}"
            query += f" {stats} | where {name} {agg['op']} {_tb.to_text(agg['value'])}"
        return query


LUCENE_SPECIAL = set('+-=&|><!(){}[]^"~:\\/')


class Elastic(Backend):
    name = "elastic"
    and_word = " AND "
    or_word = " OR "

    def esc(self, text, wildcards=False):
        out = []
        for ch in text:
            if ch in LUCENE_SPECIAL or ch == " " or (ch in "*?" and not wildcards):
                out.append("\\" + ch)
            else:
                out.append(ch)
        return "".join(out)

    def one(self, vt, test, top):
        field = self.field(vt.field)
        kind = test[0]
        if kind in ("text", "glob"):
            value = wildcard_value(vt, test)
            if "*" in value or "?" in value:
                return f"{field}:{self.esc(value, wildcards=True)}"
            if " " in value or any(c in LUCENE_SPECIAL for c in value):
                return f'{field}:"{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            return f"{field}:{value}"
        if kind == "number":
            return f"{field}:{_tb.to_text(test[1])}"
        if kind == "bool":
            return f"{field}:{'true' if test[1] else 'false'}"
        if kind == "null":
            return f"NOT _exists_:{field}"
        if kind == "exists":
            return f"_exists_:{field}" if test[1] else f"NOT _exists_:{field}"
        if kind in ("gt", "gte", "lt", "lte"):
            return f"{field}:{ {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[kind] }{_tb.to_text(test[1])}"
        if kind == "cidr":
            return f'{field}:"{test[1]}"'
        if kind == "re":
            pattern = test[1].pattern.replace("/", "\\/")
            return f"{field}:/{pattern}/"
        if kind == "fieldref":
            self.unsupported("|fieldref (comparing two fields) in a Lucene query string; use the kql backend")
        self.unsupported(kind)

    def keyword(self, word):
        if "*" in word or "?" in word:
            return self.esc(word, wildcards=True)
        return f'"{word}"' if " " in word else word

    def negate(self, text):
        return f"NOT {self.wrap(text)}"

    def finish(self, body):
        if self.rule.get("aggregation"):
            raise RuleError("the elastic backend writes Lucene query strings, which can't count or group "
                            f"(\"| {aggregation_text(self.rule['aggregation'])}\"); use the kql or splunk backend, or run "
                            "the query and let detect.run() do the counting")
        if self.table and not self.options.get("no_logsource"):
            body = f"({self.table}) AND {self.wrap(body)}" if body else self.table
        return body or "*"


class Kql(Backend):
    name = "kql"
    and_word = " and "
    or_word = " or "

    def esc(self, text):
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def one(self, vt, test, top):
        field = self.field(vt.field)
        kind = test[0]
        if kind in ("text", "glob"):
            cased = vt.cased or (kind == "text" and test[2])
            value = wildcard_value(vt, test)
            inner = value.strip("*")
            if "*" in inner or "?" in inner or (value.startswith("*") and value.endswith("*") and len(value) < 3):
                flag = "" if cased else "(?i)"
                return f'{field} matches regex "{self.esc(flag + "^" + glob_body(value) + "$")}"'
            if value.startswith("*") and value.endswith("*"):
                return f'{field} {"contains_cs" if cased else "contains"} "{self.esc(inner)}"'
            if value.endswith("*"):
                return f'{field} {"startswith_cs" if cased else "startswith"} "{self.esc(inner)}"'
            if value.startswith("*"):
                return f'{field} {"endswith_cs" if cased else "endswith"} "{self.esc(inner)}"'
            return f'{field} {"==" if cased else "=~"} "{self.esc(value)}"'
        if kind == "number":
            return f"{field} == {_tb.to_text(test[1])}"
        if kind == "bool":
            return f"{field} == {'true' if test[1] else 'false'}"
        if kind == "null":
            return f"isempty({field})"
        if kind == "exists":
            return f"isnotempty({field})" if test[1] else f"isempty({field})"
        if kind in ("gt", "gte", "lt", "lte"):
            return f"{field} { {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[kind] } {_tb.to_text(test[1])}"
        if kind == "cidr":
            fn = "ipv6_is_in_range" if isinstance(test[1], ipaddress.IPv6Network) else "ipv4_is_in_range"
            return f'{fn}({field}, "{test[1]}")'
        if kind == "re":
            return f'{field} matches regex "{self.esc(test[1].pattern)}"'
        if kind == "fieldref":
            other = self.field(test[1])
            op = {"contains": "contains", "startswith": "startswith", "endswith": "endswith"}.get(vt.mode, "=~")
            if vt.cased:
                op = {"=~": "==", "contains": "contains_cs", "startswith": "startswith_cs", "endswith": "endswith_cs"}[op]
            return f"{field} {op} {other}"
        self.unsupported(kind)

    def keyword(self, word):
        if "*" in word.strip("*") or "?" in word:
            return f'* matches regex "{self.esc("(?i)" + glob_body(word.strip("*")))}"'
        return f'* contains "{self.esc(word.strip("*"))}"'

    def negate(self, text):
        return f"not({text})"

    def finish(self, body):
        lines = [self.table or "Event"]
        if body:
            lines.append(f"| where {body}")
        agg = self.rule.get("aggregation")
        if agg:
            field = self.field(agg["field"]) if agg["field"] else None
            if agg["fn"] == "count":
                expr, name = (f"dcount({field})", "distinct_count") if field else ("count()", "count_")
            else:
                expr, name = f"{agg['fn']}({field})", f"{agg['fn']}_"
            by = [self.field(b) for b in (agg["by"] or [])]
            within = self.rule.get("within")
            if within:
                by.append(f"bin(TimeGenerated, {int(within)}s)")
            lines.append(f"| summarize {name} = {expr}" + (f" by {', '.join(by)}" if by else ""))
            lines.append(f"| where {name} {agg['op']} {_tb.to_text(agg['value'])}")
        return "\n".join(lines)


BACKEND_CLASSES = {"splunk": Splunk, "elastic": Elastic, "kql": Kql}


def sigma_to_query(rule, backend, options=None, api=None, line=None) -> str:
    compiled = CompiledRule(normalise_rule(rule, "detect.sigma_to_query()"), api, line)
    if compiled.where_fn is not None:
        raise RuleError("detect.sigma_to_query() can't convert a rule whose where is a function")
    return BACKEND_CLASSES[backend](compiled.rule, options).build(compiled)


# =============================================================================
# The library
# =============================================================================

def load(api):
    m = Module(
        "detect",
        "Detection rules for logs: describe what suspicious looks like as a map (or load a Sigma rule), run it "
        "over records from the logs library, and get alerts back. Thresholds (\"5 failures within 60 seconds "
        "from one IP\"), Sigma aggregations and correlation rules, built-in hunts for brute force, port scans and "
        "rare values, and conversion of Sigma rules into Splunk, Elastic and Sentinel (KQL) queries are included.",
    )
    m.value("levels", list(LEVELS), doc="Alert levels from least to most serious: informational, low, medium, high, critical.")
    m.value("backends", list(BACKENDS), doc="Query languages sigma_to_query() can write: splunk, elastic (Lucene), kql (Sentinel).")
    m.value("modifiers", sorted(MODIFIERS), doc="Every Sigma field modifier the rules engine understands.")

    def rule_error(line, err):
        fail(line, str(err).rstrip(".") + ".")

    def records_arg(line, records, fn):
        check_type(line, records, "list", f"detect.{fn}", "records")
        return [r for r in records if isinstance(r, dict)]

    @m.fn("rule", 1, doc=(
        "rule(map) -> map\n"
        "Check a rule and fill in its defaults. A rule is a map that says what suspicious looks like.\n"
        "\n"
        "Fields:\n"
        "- name: what to call the alert.\n"
        "- level: how serious the alert is (default: \"medium\"). One of:\n"
        "  - \"informational\", \"low\", \"medium\", \"high\" or \"critical\"\n"
        "- where: a map of {field: value, ...}. Every field must match.\n"
        "  It can also be a function that gets a record and gives back true or false.\n"
        "- unless: a map of {field: value}. Records that match it are skipped, to remove false positives.\n"
        "- count: how many matches make an alert (default: 1).\n"
        "- within: the seconds (or text like \"5m\") the matches must fall in.\n"
        "- by: a field (or a list of fields) to count separately, like \"ip\".\n"
        "- fields: fields to copy into the alert.\n"
        "\n"
        "Values in where:\n"
        "- Exact text, in any case.\n"
        "- Text with * and ? wildcards.\n"
        "- \"re:pattern\" for a regular expression.\n"
        "- A number.\n"
        "- nil, meaning the field is missing.\n"
        "- A list: any of its values matches.\n"
        "\n"
        "Modifiers (add one to the field name):\n"
        "- \"path|contains\": the text appears anywhere.\n"
        "- \"agent|startswith\", \"file|endswith\": the start or end of the text.\n"
        "- \"bytes|gt\": a number bigger than the value.\n"
        "- \"ip|cidr\": an IP inside a range, like \"10.0.0.0/8\".\n"
        "- \"cmd|re\": a regular expression.\n"
        "- \"x|exists\": the field is there.\n"
        "- \"cmd|base64offset|contains\": the value hidden in Base64.\n"
        "- \"user|cased\": the case must match too.\n"
        "- \"a|fieldref\": equal to another field.\n"
        "\n"
        "Example:\n"
        "    let r = detect.rule({\n"
        "      \"name\": \"Root login\",\n"
        "      \"level\": \"high\",\n"
        "      \"where\": {\"user\": \"root\", \"cmd|contains\": \"passwd\"},\n"
        "    })\n"
        "    say(r.count)"))
    def rule(line, spec):
        try:
            result = normalise_rule(spec)
            CompiledRule(result, api, line)
        except RuleError as err:
            rule_error(line, err)
        return result

    @m.fn("run", 2, doc=(
        "run(rules, records) -> list\n"
        "Run one rule or a list of rules over log records. Gives back the alerts, most serious first.\n"
        "\n"
        "Fields of each alert:\n"
        "- \"rule\", \"level\": the rule's name and level.\n"
        "- \"by\": the value of the by field, like the IP.\n"
        "- \"count\": how many records matched.\n"
        "- \"first\", \"last\": timestamps of the first and last match.\n"
        "- \"first_when\", \"last_when\": the same times as text.\n"
        "- \"description\", \"tags\": copied from the rule.\n"
        "- \"records\": the first few matching records.\n"
        "- \"fields\": only when the rule lists fields.\n"
        "- \"value\", \"aggregation\": only for Sigma min/max/avg/sum/count(field) rules.\n"
        "- \"rules\": only for correlation rules.\n"
        "\n"
        "Good to know:\n"
        "- With count and within, each burst of activity gives one alert.\n"
        "- A record's \"repeat\" field (from \"message repeated 3 times\") counts as that many records.\n"
        "\n"
        "Example:\n"
        "    let r = detect.rule({\"name\": \"Root login\", \"where\": {\"user\": \"root\"}})\n"
        "    let alerts = detect.run(r, [{\"user\": \"root\"}, {\"user\": \"Tanisha\"}])\n"
        "    say(alerts[0].count)"))
    def run(line, rules, records):
        check_type(line, rules, "list|map", "detect.run", "rules")
        rules = [rules] if isinstance(rules, dict) else rules
        rules = [r for r in rules if not (isinstance(r, dict) and r.get("hidden") is True)]
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
        "Find password guessing: many failed logins from one IP in a short time.\n"
        "\n"
        "Options:\n"
        "- attempts: how many failed logins make an alert (default: 5).\n"
        "- within: the seconds those failures must fall in (default: 600).\n"
        "\n"
        "It works on auth.log (SSH, sudo) and Windows 4625/4771 events, or any record with success = false.\n"
        "\n"
        "Extra alert fields:\n"
        "- \"users\": the accounts that were tried.\n"
        "- \"success_after\": true if the same IP then logged in successfully.\n"
        "  That alert becomes critical, because the attacker got in."))
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
        "Find port scans in connection or firewall logs: one source IP touching many different "
        "destination ports.\n"
        "\n"
        "Options:\n"
        "- ports: how many different destination ports make an alert (default: 10).\n"
        "- within: the seconds those ports must fall in (default: 60).\n"
        "\n"
        "Fields it reads:\n"
        "- src_ip or ip: the source.\n"
        "- dst_port (or dest_port, DestinationPort, dpt): the destination port.\n"
        "UFW and iptables lines from the logs library have these fields.\n"
        "\n"
        "Extra alert fields:\n"
        "- \"ports\", \"targets\": the ports and destinations seen."))
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

    # -- Sigma -------------------------------------------------------------------------

    def sigma_options(line, options, fn):
        if options is None:
            return None
        check_type(line, options, "map", f"detect.{fn}", "options")
        unknown = [k for k in options if k != "placeholders"]
        if unknown:
            fail(line, f"detect.{fn}() doesn't know the option {_tb.to_text(unknown[0], True)}. Options: placeholders.")
        placeholders = options.get("placeholders")
        if placeholders is not None:
            check_type(line, placeholders, "map", f"detect.{fn}", "the placeholders option")
        return placeholders

    def sigma_text(line, source, fn):
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
        return text, label

    def load_sigma_all(line, source, fn, placeholders):
        text, label = sigma_text(line, source, fn)
        try:
            return sigma_rules(text, label, placeholders)
        except YamlError as err:
            at = f" (line {err.lineno})" if err.lineno else ""
            fail(line, f"I couldn't read the Sigma rule {label}: {err}{at}.")
        except RuleError as err:
            rule_error(line, err)

    def load_sigma(line, source, fn, placeholders=None):
        rules = load_sigma_all(line, source, fn, placeholders)
        visible = [r for r in rules if not r.get("hidden")]
        if len(visible) == 1:
            return visible[0]
        _text, label = sigma_text(line, source, fn)
        fail(line, f"The Sigma file {label} holds {len(visible)} rules; use detect.sigma_all() to load them all.")

    @m.fn("sigma", 1, 2, doc=(
        "sigma(text_or_path, options?) -> map\n"
        "Load a Sigma rule (the shared YAML format for detections) from a .yml file or from text. "
        "The result is a rule for detect.run().\n"
        "\n"
        "Supported Sigma fields:\n"
        "- title, id, level, description, tags, fields, timeframe.\n"
        "- logsource: limits the rule to matching record formats.\n"
        "- detection: named selections (maps, lists of maps, keyword lists).\n"
        "\n"
        "Modifiers (all of them):\n"
        "- contains, startswith, endswith, all.\n"
        "- re (with i, m and s), cased, cidr.\n"
        "- gt, gte, lt, lte, exists.\n"
        "- windash, base64, base64offset.\n"
        "- utf16, utf16le, utf16be, wide.\n"
        "- fieldref, expand.\n"
        "\n"
        "Conditions:\n"
        "- Wildcards * and ? in values.\n"
        "- and, or, not and brackets.\n"
        "- \"1 of selection*\" and \"all of them\".\n"
        "\n"
        "Aggregations:\n"
        "- \"| count() by field > N\"\n"
        "- \"| count(field) by other > N\"\n"
        "- \"| min/max/avg/sum(field) by other > N\"\n"
        "- \"| near other\"\n"
        "\n"
        "Sigma correlation rules work too: a file with the rules and a correlation: document, "
        "separated by --- lines.\n"
        "\n"
        "Options:\n"
        "- placeholders: a map {\"name\": [values]} that fills %name% in |expand values.\n"
        "\n"
        "Anything else gives a clear error."))
    def sigma(line, source, options=None):
        return load_sigma(line, source, "sigma", sigma_options(line, options, "sigma"))

    @m.fn("sigma_all", 1, 2, doc=(
        "sigma_all(text_or_path, options?) -> list\n"
        "Load every rule in a Sigma file with several documents (--- lines), including correlation rules. Rules a "
        "correlation uses only as building blocks (generate: false) have \"hidden\": true; detect.run() skips those."))
    def sigma_all(line, source, options=None):
        return load_sigma_all(line, source, "sigma_all", sigma_options(line, options, "sigma_all"))

    @m.fn("sigma_folder", 1, 2, doc=(
        "sigma_folder(path, options?) -> list\n"
        "Load every .yml / .yaml Sigma rule in a folder (and its sub-folders), sorted by file name."))
    def sigma_folder(line, folder, options=None):
        check_type(line, folder, "text", "detect.sigma_folder", "path")
        placeholders = sigma_options(line, options, "sigma_folder")
        folder = os.path.expanduser(folder)
        if not os.path.isdir(folder):
            fail(line, f"I couldn't find the folder '{folder}'.", path=folder)
        rules = []
        for here, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                if name.lower().endswith((".yml", ".yaml")):
                    rules.extend(load_sigma_all(line, os.path.join(here, name), "sigma_folder", placeholders))
        return rules

    @m.fn("sigma_to_query", 2, 3, doc=(
        "sigma_to_query(rule, backend, options?) -> text\n"
        "Write a rule (from detect.sigma() or detect.rule()) as a query for a SIEM.\n"
        "\n"
        "Backends:\n"
        "- \"splunk\": SPL.\n"
        "- \"elastic\": a Lucene query string for Kibana / Elasticsearch.\n"
        "- \"kql\": Microsoft Sentinel.\n"
        "\n"
        "The rule's logsource picks the index, source type or table. It also picks the field names, "
        "like EventID -> EventCode in Splunk or Image -> NewProcessName in Sentinel.\n"
        "\n"
        "Options:\n"
        "- fields: a map {sigma_name: siem_name} that adds field mappings.\n"
        "- table: the KQL table, Splunk source or Lucene filter.\n"
        "- index: the Splunk index.\n"
        "\n"
        "Aggregations become | stats (Splunk) or | summarize (KQL). "
        "The elastic backend can't count, so it refuses those rules with a hint."))
    def sigma_to_query_fn(line, rule, backend, options=None):
        check_type(line, rule, "map", "detect.sigma_to_query", "rule")
        check_type(line, backend, "text", "detect.sigma_to_query", "backend")
        name = backend.strip().lower()
        if name == "sentinel":
            name = "kql"
        if name in ("lucene", "elasticsearch", "kibana", "opensearch"):
            name = "elastic"
        if name not in BACKEND_CLASSES:
            fail(line, f"detect.sigma_to_query() doesn't know the backend '{backend}'. Use one of: {', '.join(BACKENDS)}.")
        if options is not None:
            check_type(line, options, "map", "detect.sigma_to_query", "options")
            unknown = [k for k in options if k not in ("fields", "table", "index", "no_logsource")]
            if unknown:
                fail(line, f"detect.sigma_to_query() doesn't know the option {_tb.to_text(unknown[0], True)}. "
                           "Options: fields, table, index.")
        try:
            return sigma_to_query(rule, name, options, api, line)
        except RuleError as err:
            rule_error(line, err)

    @m.fn("logsource", 1, doc=(
        "logsource(map) -> map\n"
        "What detect knows about a Sigma logsource ({\"product\", \"service\", \"category\"}): the record "
        "\"formats\" from the logs library it covers and where the data lives in each SIEM (\"splunk\", "
        "\"elastic\", \"kql\"), or nil for an unknown source."))
    def logsource_fn(line, source):
        check_type(line, source, "map", "detect.logsource", "map")
        entry = logsource_entry(source)
        if entry is None:
            return None
        return {"formats": list(entry["formats"]), "splunk": entry["splunk"], "elastic": entry["elastic"], "kql": entry["kql"]}

    @m.fn("parse_yaml", 1, doc=(
        "parse_yaml(text) -> value\n"
        "Read YAML into TB maps and lists (the same as yaml.parse(); kept for older programs)."))
    def parse_yaml(line, text):
        check_type(line, text, "text", "detect.parse_yaml", "text")
        try:
            return read_yaml(text)
        except YamlError as err:
            at = f" (line {err.lineno})" if err.lineno else ""
            fail(line, f"That YAML doesn't work: {err}{at}.")

    return m
