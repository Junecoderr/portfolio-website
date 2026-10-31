"""The YARA condition language for tblib/scan.py: tokens, a compiler to closures, YARA's
"undefined" values, regex/base64 helpers and the pe, elf and math modules (backed by tblib.pe,
tblib.elf and tblib._magic). Semantics follow https://yara.readthedocs.io (4.x)."""

from __future__ import annotations

import base64
import bisect
import collections
import datetime as dt
import math
import operator
import re
import struct


class RuleSyntax(Exception):
    pass


class _Undefined:
    """YARA's undefined value: what a module field is when the file isn't that format, an
    out-of-range @a[9], uint32() past the end, division by zero. It is false, and any arithmetic or
    comparison involving it stays undefined."""

    def __repr__(self):
        return "undefined"


UNDEF = _Undefined()
MODULES = ("pe", "elf", "math")
UNSUPPORTED_MODULES = ("hash", "cuckoo", "magic", "dotnet", "time", "console", "string", "lnk", "dex", "macho",
                       "androguard", "vt")


def truth(v) -> bool:
    if v is UNDEF:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return bool(v)


# =============================================================================
# Strings: regex dialect, wide, base64
# =============================================================================

RE_SCAN_LIMIT = 4096   # YARA's YR_RE_SCAN_LIMIT: no regex match runs further than this
RE_MAX_RANGE = 32767   # YARA's RE_MAX_RANGE: the largest n in {n} or {n,m}


def yara_regex(pattern: str, wide: bool, name: str) -> str:
    """A YARA regex as a Python one: `$` is end-of-data only, and with wide=True every
    character also matches the 0x00 byte after it (UTF-16LE text)."""
    out, i, n = [], 0, len(pattern)

    def atom(text):
        out.append(f"(?:{text}\\x00)" if wide else text)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            nxt = pattern[i + 1:i + 2]
            if not nxt:
                raise RuleSyntax(f"string {name}: the regex ends with a lone backslash")
            if nxt == "x":
                atom(pattern[i:i + 4])
                i += 4
                continue
            if nxt in "bB" and wide:
                raise RuleSyntax(f"string {name}: \\{nxt} (word boundary) can't be used in a wide regex")
            if nxt in "bBAZz":
                out.append(pattern[i:i + 2])
            else:
                atom(pattern[i:i + 2])
            i += 2
        elif ch == "[":
            j = i + 1
            if pattern[j:j + 1] == "^":
                j += 1
            if pattern[j:j + 1] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            if j >= n:
                raise RuleSyntax(f"string {name}: a [character class] in the regex is never closed")
            atom(pattern[i:j + 1])
            i = j + 1
        elif ch == "$":
            out.append(r"\Z")
            i += 1
        elif ch in "*+":  # unbounded repeats stop at RE_SCAN_LIMIT, as YARA's matcher does
            out.append("{%d,%d}" % (0 if ch == "*" else 1, RE_SCAN_LIMIT))
            i += 1
        elif ch in "()|?^":
            out.append(ch)
            i += 1
        elif ch == "{":
            m = re.compile(r"\{(\d*)(,?)(\d*)\}").match(pattern, i)
            if m and m.group(0) != "{}" and m.group(0) != "{,}":
                lo, comma, hi = m.groups()
                if any(len(v) > 5 or int(v) > RE_MAX_RANGE for v in (lo, hi) if v):
                    raise RuleSyntax(f"string {name}: the repeat {m.group(0)} is too large (YARA allows up to "
                                     f"{{{RE_MAX_RANGE}}})")
                if comma and not hi:
                    hi = str(max(int(lo or 0), RE_SCAN_LIMIT))
                out.append("{%s%s%s}" % (lo, comma, hi))
                i = m.end()
            else:
                atom(r"\{")
                i += 1
        elif ch == ".":
            atom(".")
            i += 1
        else:
            for b in ch.encode("utf-8"):
                atom(re.escape(chr(b)) if b < 0x80 else f"\\x{b:02x}")
            i += 1
    return "".join(out)


try:
    from re import _parser as _sre  # Python 3.11+
except ImportError:  # pragma: no cover
    import sre_parse as _sre

_ALL = frozenset(range(256))
_DIGITS = frozenset(b"0123456789")
_WORDS = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACES = frozenset(b" \t\n\r\f\v")
_CATEGORIES = {"CATEGORY_DIGIT": _DIGITS, "CATEGORY_NOT_DIGIT": _ALL - _DIGITS, "CATEGORY_WORD": _WORDS,
               "CATEGORY_NOT_WORD": _ALL - _WORDS, "CATEGORY_SPACE": _SPACES, "CATEGORY_NOT_SPACE": _ALL - _SPACES}


def _char_set(op: str, av, nocase: bool):
    """The bytes one single-character regex node can match, or None if it isn't one."""
    if op == "LITERAL":
        out = {av}
    elif op == "NOT_LITERAL":
        out = set(_ALL - {av})
    elif op == "ANY":
        out = set(_ALL)
    elif op == "IN":
        out, negate = set(), False
        for item_op, item in av:
            item_op = str(item_op)
            if item_op == "NEGATE":
                negate = True
            elif item_op == "LITERAL":
                out.add(item)
            elif item_op == "RANGE":
                out.update(range(item[0], min(item[1], 255) + 1))
            elif item_op == "CATEGORY":
                out |= _CATEGORIES.get(str(item), _ALL)
            else:
                out = set(_ALL)
        if negate:
            out = set(_ALL - out)
    else:
        return None
    if nocase:
        out |= {c ^ 0x20 for c in out if chr(c).isascii() and chr(c).isalpha()}
    return out


def _atom_chars(nodes, nocase) -> set:
    """Every byte any single-character node inside nodes can match."""
    out = set()
    for op, av in nodes:
        op = str(op)
        chars = _char_set(op, av, nocase)
        if chars is not None:
            out |= chars
        else:
            for sub in _children(op, av):
                out |= _atom_chars(sub, nocase)
    return out


def _children(op: str, av) -> list:
    if op in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
        return [av[2]]
    if op == "SUBPATTERN":
        return [av[-1]]
    if op == "BRANCH":
        return list(av[1])
    if op in ("ASSERT", "ASSERT_NOT", "ATOMIC_GROUP"):
        return [av[-1]]
    return []


def _wide_open(nodes, nocase) -> set:
    """Bytes matched by repeats (at least RE_SCAN_LIMIT times, i.e. once unbounded) inside nodes."""
    out = set()
    for op, av in nodes:
        op = str(op)
        if op in ("MAX_REPEAT", "MIN_REPEAT") and av[1] >= RE_SCAN_LIMIT:
            out |= _atom_chars(av[2], nocase)
        for sub in _children(op, av):
            out |= _wide_open(sub, nocase)
    return out


def _anchored(nodes, chars, nocase) -> bool:
    """True if every way through nodes must match a character outside chars: then each repetition of
    the enclosing group ends at a definite place and the regex can't backtrack exponentially."""
    for op, av in nodes:
        op = str(op)
        own = _char_set(op, av, nocase)
        if own is not None and not own & chars:
            return True
        if op == "SUBPATTERN" and _anchored(av[-1], chars, nocase):
            return True
        if op == "BRANCH" and all(_anchored(b, chars, nocase) for b in av[1]):
            return True
    return False


def check_backtracking(pattern: bytes, flags: int, name: str):
    """Refuse regexes like (a+)+b, which Python's backtracking matcher (unlike YARA's) can take
    hours on: a repeated group whose inside repeats without limit, unless each repetition must
    end on a character the inner repeat can't match, as in (\\w+\\.)+."""
    try:
        tree = _sre.parse(pattern, flags)
    except Exception:  # re.compile reports real syntax errors
        return
    nocase = bool(flags & re.IGNORECASE)

    def walk(nodes):
        for op, av in nodes:
            op = str(op)
            if op in ("MAX_REPEAT", "MIN_REPEAT") and av[1] > 1:
                inner = _wide_open(av[2], nocase)
                if inner and not _anchored(av[2], inner, nocase):
                    raise RuleSyntax(
                        f"string {name}: the regex repeats a group that can itself repeat without limit, "
                        "like (a+)+ or (\\w+\\s*)*. Python's matcher can take hours on such a pattern, so "
                        "it is refused: make each repetition end on a character the inner part can't "
                        "match, like (\\w+\\.)+, or drop the outer repeat.")
            for sub in _children(op, av):
                walk(sub)
    walk(list(tree))


STANDARD_B64 = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def base64_variants(raw: bytes, alphabet: bytes | None = None) -> list:
    """The three base64 forms of raw (one per alignment), trimmed of the characters that depend on
    unknown neighbouring bytes, like YARA's base64 modifier."""
    out = []
    for shift in range(3):
        enc = base64.b64encode(b"\x00" * shift + raw)
        lead = (0, 2, 3)[shift]
        trail = (0, 3, 2)[(len(raw) + shift) % 3]
        piece = enc[lead:len(enc) - trail]
        if alphabet:
            piece = piece.translate(bytes.maketrans(STANDARD_B64, alphabet))
        if piece and piece not in out:
            out.append(piece)
    return out


def find_all(pattern, data: bytes, limit: int, memo: dict | None = None) -> list:
    """Every (offset, length) where pattern matches, overlapping ones included (YARA reports a
    match at each offset: "aa" in "aaaa" is 3 matches). memo: per-buffer scratch space that
    XorSearch and ChainSearch share during one scan (never kept on the compiled patterns).
    Regexes are compiled with bounded repeats (RE_SCAN_LIMIT), so each search stays short."""
    if isinstance(pattern, re.Pattern):
        search = pattern.search
    else:
        memo = {} if memo is None else memo

        def search(d, p):
            return pattern.search(d, p, memo)
    hits, pos = [], 0
    while len(hits) < limit:
        m = search(data, pos)
        if not m:
            break
        hits.append((m.start(), m.end() - m.start()))
        pos = m.start() + 1
    return hits


class _Hit:
    __slots__ = ("s", "e")

    def __init__(self, s, e):
        self.s, self.e = s, e

    def start(self):
        return self.s

    def end(self):
        return self.e


class XorSearch:
    """Finds an xor string fast: XOR-ing neighbouring bytes cancels the key, so one plain find()
    over data[i] ^ data[i+1] locates every candidate; the full pattern then checks it."""

    def __init__(self, pattern, base: bytes):
        self.pattern = pattern
        self.delta = bytes(a ^ b for a, b in zip(base[1:], base[:-1]))

    def search(self, data: bytes, pos: int = 0, memo: dict | None = None):
        if not self.delta:
            return self.pattern.search(data, pos)
        memo = {} if memo is None else memo
        diff = memo.get("xor-diff")  # one per buffer, shared by every xor string in the scan
        if diff is None:
            value = int.from_bytes(data, "big")
            diff = memo["xor-diff"] = (value ^ (value >> 8)).to_bytes(len(data), "big")
        while True:
            i = diff.find(self.delta, pos + 1)  # diff[i] = data[i - 1] ^ data[i]
            if i < 0:
                return None
            m = self.pattern.match(data, i - 1)
            if m:
                return m
            pos = i


class ChainSearch:
    """A hex string cut at its long or unbounded jumps ([n-], [-], [0-5000]): each piece is found
    on its own and the pieces are linked by distance, as YARA does, instead of one regex that
    would rescan to the end of the data from every candidate."""

    def __init__(self, parts):
        self.parts = [(re.compile(p, re.DOTALL), lo, hi) for p, lo, hi in parts]

    def complete(self, data: bytes):
        """[(start, end)] of every first-piece hit that some chain of later pieces completes, found
        from the last piece back: a hit is viable if any viable hit of the next piece starts within
        its jump window (so AA@0, BB@101, BB@302, CC@500 matches through the second BB). Each
        hit is looked at once, with a bisect into the next piece's viable starts."""
        found = [find_all(p, data, 1_000_000) for p, _, _ in self.parts]
        starts, ends = [s for s, _ in found[-1]], [s + n for s, n in found[-1]]
        for k in range(len(self.parts) - 2, -1, -1):
            _, lo, hi = self.parts[k + 1]
            new_starts, new_ends = [], []
            for s, n in found[k]:
                j = bisect.bisect_left(starts, s + n + lo)
                if j < len(starts) and (hi is None or starts[j] <= s + n + hi):
                    new_starts.append(s)
                    new_ends.append(ends[j])
            starts, ends = new_starts, new_ends
        return starts, ends

    def search(self, data: bytes, pos: int = 0, memo: dict | None = None):
        memo = {} if memo is None else memo
        key = ("chain", self)  # the memo lives for one buffer's scan, then goes with its results
        if key not in memo:
            memo[key] = self.complete(data)
        starts, ends = memo[key]
        k = bisect.bisect_left(starts, pos)
        return _Hit(starts[k], ends[k]) if k < len(starts) else None


# =============================================================================
# Conditions
# =============================================================================

_TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<num>0x[0-9A-Fa-f]+|0o[0-7]+|\d+(?:\.\d+)?)(?P<unit>KB|MB)?(?![A-Za-z0-9_])
  | (?P<text>"(?:[^"\\\n]|\\.)*")
  | (?P<regex>/(?:[^/\\\n]|\\.)+/[is]*)
  | (?P<op><<|>>|<=|>=|==|!=|\.\.|[<>()\[\],*+\-\\%&|^~:.])
  | (?P<str>[$#@!][A-Za-z0-9_]*\*?)
  | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
""", re.S | re.X)

RELATIONS = ("<", ">", "<=", ">=", "==", "!=")
STRING_OPS = ("contains", "icontains", "startswith", "istartswith", "endswith", "iendswith", "iequals", "matches")
INT_READERS = {f"{s}int{b}{e}": (b // 8, s == "", e == "be") for s in ("", "u") for b in (8, 16, 32) for e in ("", "be")}
KEYWORDS = {"and", "or", "not", "of", "them", "at", "in", "for", "any", "all", "none", "true", "false",
            "filesize", "entrypoint", "defined"} | set(STRING_OPS)


def text_literal(raw: str) -> str:
    out, i = [], 1
    while i < len(raw) - 1:
        ch = raw[i]
        if ch == "\\":
            nxt = raw[i + 1]
            if nxt == "x":
                out.append(chr(int(raw[i + 2:i + 4], 16)))
                i += 4
                continue
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _num(v):
    return int(v) if isinstance(v, bool) else v


INT64_MIN = -(1 << 63)


def int64(v: int) -> int:
    """Wrap to a signed 64-bit integer, as YARA's int64_t arithmetic does."""
    return ((v - INT64_MIN) & 0xFFFFFFFFFFFFFFFF) + INT64_MIN


def _arith(op):
    def run(a, b):
        a, b = _num(a), _num(b)
        if a is UNDEF or b is UNDEF or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return UNDEF
        if op in ("\\", "%") and b == 0:
            return UNDEF
        if isinstance(a, float) or isinstance(b, float):
            floats = {"\\": lambda: a / b, "%": lambda: math.fmod(a, b), "+": lambda: a + b,
                      "-": lambda: a - b, "*": lambda: a * b}
            return floats[op]() if op in floats else UNDEF  # bitwise operators need integers
        # integers: exact, C-style truncating \ and %, wrapped to int64 (INT64_MIN \ -1 is undefined)
        if op in ("\\", "%"):
            if a == INT64_MIN and b == -1:
                return UNDEF
            q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
            return int64(q if op == "\\" else a - b * q)
        if op in ("<<", ">>"):
            if b < 0:
                return UNDEF
            if b >= 64:
                return 0
            return int64(a << b) if op == "<<" else a >> b
        return int64({"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                      "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b}[op]())
    return run


def _relation(op):
    fn = {"<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
          "==": lambda a, b: a == b, "!=": lambda a, b: a != b}[op]

    def run(a, b):
        a, b = _num(a), _num(b)
        if a is UNDEF or b is UNDEF:
            return UNDEF
        try:
            return fn(a, b)
        except TypeError:
            return UNDEF
    return run


def _string_op(op):
    def run(a, b):
        if a is UNDEF or b is UNDEF:
            return UNDEF
        if op == "matches":
            return isinstance(a, str) and bool(b.search(a))
        if not isinstance(a, str) or not isinstance(b, str):
            return UNDEF
        if op.startswith("i"):
            a, b, base = a.lower(), b.lower(), op[1:]
        else:
            base = op
        return {"contains": lambda: b in a, "startswith": lambda: a.startswith(b), "endswith": lambda: a.endswith(b),
                "equals": lambda: a == b}[base]()
    return run


class Cond:
    """Compile a condition into a closure f(ctx) -> value. ctx (see Ctx) holds the string hits,
    the data, earlier rules' results, loop variables and lazily built modules."""

    def __init__(self, text: str, names: list, rule: str, known_rules=(), line: int | None = None):
        self.rule, self.names, self.known_rules, self.base_line = rule, names, set(known_rules), line
        self.text = text
        self.tokens = []  # (kind, value, position)
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m:
                if text.startswith("/*", pos):
                    self.fail("a /* comment in the condition is never closed", pos)
                self.fail(f"I don't understand '{text[pos:pos + 10]}' in the condition", pos)
            if m.group("num") is not None:
                n = m.group("num")
                if len(n) > 40:  # also keeps int() below Python's 4300-digit limit
                    self.fail(f"the number {n[:20]}... is too big; YARA numbers are 64-bit", pos)
                value = float(n) if "." in n else int(n[2:], 8) if n.startswith("0o") else \
                    int(n, 16) if n.startswith("0x") else int(n, 10)
                value *= {"KB": 1024, "MB": 1024 ** 2, None: 1}[m.group("unit")]
                if isinstance(value, int) and value > 0x7FFFFFFFFFFFFFFF:
                    self.fail(f"the number {n} is too big; YARA numbers are 64-bit", pos)
                self.tokens.append(("num", value, pos))
            elif m.group("text") is not None:
                try:
                    self.tokens.append(("text", text_literal(m.group("text")), pos))
                except ValueError:
                    self.fail(f"the text {m.group('text')} has a bad \\x escape", pos)
            elif m.group("regex") is not None:
                self.tokens.append(("regex", m.group("regex"), pos))
            elif m.group("op") is not None:
                self.tokens.append(("op", m.group("op"), pos))
            elif m.group("str") is not None:
                self.tokens.append(("str", m.group("str"), pos))
            elif m.group("word") is not None:
                self.tokens.append(("word", m.group("word"), pos))
            pos = m.end()
        self.i = 0
        self.scopes = []      # compile-time loop variables: [{name: schema}]
        self.anon_ok = 0      # inside "for ... of ... : ( $ )"
        if not self.tokens:
            self.fail("the condition is empty")
        self.fn = self.parse_or()
        if self.i < len(self.tokens):
            self.fail(f"unexpected '{self.tokens[self.i][1]}' in the condition", self.tokens[self.i][2])

    # -- helpers ----------------------------------------------------------------

    def fail(self, message, pos=None):
        if pos is None:
            pos = self.tokens[max(self.i - 1, 0)][2] if self.tokens else 0  # the token just read
        where = ""
        if self.base_line is not None:
            where = f" (line {self.base_line + self.text.count(chr(10), 0, pos)})"
        raise RuleSyntax(f"rule {self.rule}: {message}{where}")

    def peek(self, kind=None, value=None, ahead=0):
        if self.i + ahead >= len(self.tokens):
            return None
        k, v, _ = self.tokens[self.i + ahead]
        if (kind and k != kind) or (value is not None and v != value):
            return None
        return v

    def take(self):
        if self.i >= len(self.tokens):
            self.fail("the condition ends too early")
        tok = self.tokens[self.i]
        self.i += 1
        return tok[0], tok[1]

    def accept(self, kind, value):
        if self.peek(kind, value) is not None:
            self.i += 1
            return True
        return False

    def need(self, kind, value):
        if not self.accept(kind, value):
            found = self.tokens[self.i][1] if self.i < len(self.tokens) else "the end"
            where = self.tokens[self.i][2] if self.i < len(self.tokens) else len(self.text.rstrip())
            self.fail(f"expected '{value}' in the condition but found '{found}'", where)

    # -- boolean levels ---------------------------------------------------------------

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

            def negate(ctx, f=inner):
                v = f(ctx)
                return UNDEF if v is UNDEF else not truth(v)
            return negate
        if self.accept("word", "defined"):
            inner = self.parse_not()
            return lambda ctx, f=inner: f(ctx) is not UNDEF
        return self.parse_cmp()

    def parse_cmp(self):
        left = self.parse_bits(0)
        op = self.peek("op")
        if op in RELATIONS:
            self.take()
            right = self.parse_bits(0)
            return (lambda a, b, f: lambda ctx: f(a(ctx), b(ctx)))(left, right, _relation(op))
        word = self.peek("word")
        if word in STRING_OPS:
            self.take()
            if word == "matches":
                if self.peek("regex") is None:
                    self.fail("'matches' needs a /regex/ on its right")
                right = (lambda r: lambda ctx: r)(self.regex_value(self.take()[1]))
            else:
                right = self.parse_bits(0)
            return (lambda a, b, f: lambda ctx: f(a(ctx), b(ctx)))(left, right, _string_op(word))
        return left

    LEVELS = [("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "\\", "%")]

    def parse_bits(self, level):
        if level == len(self.LEVELS):
            return self.parse_unary()
        left = self.parse_bits(level + 1)
        while self.peek("op") in self.LEVELS[level]:
            # "50% of them": a % followed by 'of' belongs to the quantifier, not to modulo
            if self.peek("op") == "%" and self.peek("word", "of", 1) is not None:
                break
            op = self.take()[1]
            right = self.parse_bits(level + 1)
            left = (lambda a, b, f: lambda ctx: f(a(ctx), b(ctx)))(left, right, _arith(op))
        return left

    def parse_unary(self):
        if self.accept("op", "-"):
            inner = self.parse_unary()
            return lambda ctx, f=inner: UNDEF if (v := _num(f(ctx))) is UNDEF or isinstance(v, str) else -v
        if self.accept("op", "~"):
            inner = self.parse_unary()
            return lambda ctx, f=inner: UNDEF if (v := _num(f(ctx))) is UNDEF or not isinstance(v, int) else ~v
        fn, schema = self.parse_postfix()
        return fn

    # -- values -------------------------------------------------------------------

    def regex_value(self, raw):
        body, flags = raw[1:raw.rindex("/")], raw[raw.rindex("/") + 1:]
        try:
            return re.compile(yara_regex(body, False, "in the condition"),
                              (re.IGNORECASE if "i" in flags else 0) | (re.DOTALL if "s" in flags else 0))
        except re.error as err:
            self.fail(f"the regex {raw} is broken: {err}")

    def parse_postfix(self):
        fn, schema = self.parse_primary()
        while True:
            if self.peek("op", ".") is not None:
                self.take()
                if self.peek("word") is None:
                    self.fail("expected a field name after '.'")
                field = self.take()[1]
                fn, schema = self.member(fn, schema, field)
            elif self.peek("op", "[") is not None and schema is not None:
                self.take()
                index = self.parse_or()
                self.need("op", "]")
                fn, schema = self.index(fn, schema, index)
            elif self.peek("op", "(") is not None and isinstance(schema, tuple) and schema[0] == "func":
                self.take()
                args = []
                if not self.accept("op", ")"):
                    while True:
                        if self.peek("regex") is not None:
                            args.append((lambda r: lambda ctx: r)(self.regex_value(self.take()[1])))
                        else:
                            args.append(self.parse_or())
                        if self.accept("op", ")"):
                            break
                        self.need("op", ",")
                fn, schema = self.call(fn, schema, args)
            else:
                return fn, schema

    def member(self, fn, schema, field):
        if not isinstance(schema, dict):
            self.fail(f"'.{field}' can only follow a module or one of its structures")
        if field not in schema:
            path = schema.get("__path__", "it")
            fields = sorted(k for k in schema if not k.startswith("__") and not k.isupper())
            self.fail(f"{path}.{field} isn't supported by TB's scan. {path} has: {', '.join(fields)}")
        sub = schema[field]
        if isinstance(sub, dict):
            sub = dict(sub, __path__=f"{schema.get('__path__', '')}.{field}")

        def get(ctx, f=fn, k=field):
            base = f(ctx)
            if not isinstance(base, dict):
                return UNDEF
            return base.get(k, UNDEF)
        return get, sub

    def index(self, fn, schema, index):
        if not (isinstance(schema, tuple) and schema[0] in ("array", "dict")):
            self.fail("only module arrays (like pe.sections) can be indexed with [ ]")

        def get(ctx, f=fn, ix=index):
            base, k = f(ctx), _num(ix(ctx))
            if isinstance(base, list) and isinstance(k, int) and 0 <= k < len(base):
                return base[k]
            if isinstance(base, dict) and isinstance(k, str):
                return base.get(k, UNDEF)
            return UNDEF
        sub = schema[1]
        return get, (dict(sub, __path__=schema[2]) if isinstance(sub, dict) else sub)

    def call(self, fn, schema, args):
        _, arities, result, path = schema
        if len(args) not in arities:
            want = " or ".join(str(a) for a in sorted(arities))
            self.fail(f"{path}() takes {want} argument{'s' if want != '1' else ''}, not {len(args)}")

        def run(ctx, f=fn, a=args):
            func = f(ctx)
            if not callable(func):
                return UNDEF
            values = [x(ctx) for x in a]
            if any(v is UNDEF for v in values):
                return UNDEF
            try:
                out = func(*values)
            except (TypeError, ValueError, IndexError, OverflowError, ZeroDivisionError):
                return UNDEF
            return UNDEF if out is None else out
        return run, result

    def string_set(self):
        """After 'of': 'them' or ( $a, $b* ) -> list of string names."""
        if self.accept("word", "them"):
            if not self.names:
                self.fail("'of them' needs a strings: section")
            return list(self.names)
        self.need("op", "(")
        chosen = []
        while True:
            tok = self.peek("str")
            if tok is None or not tok.startswith("$"):
                if self.peek("word") is not None:
                    self.fail("sets of rules like 'any of (Rule1, Rule*)' aren't supported; "
                              "list the rules with 'or' instead")
                self.fail("'of (...)' needs string names like $a or $a*")
            self.take()
            if tok.endswith("*"):
                found = [n for n in self.names if n.startswith(tok[:-1])]
                if not found:
                    self.fail(f"no string starts with {tok[:-1]}")
                chosen.extend(n for n in found if n not in chosen)
            else:
                self.check_name(tok)
                chosen.append(tok)
            if not self.accept("op", ","):
                break
        self.need("op", ")")
        return chosen

    def check_name(self, name):
        if name not in self.names:
            self.fail(f"the condition uses {name} but no such string is defined")

    def range_after_in(self):
        self.need("op", "(")
        lo = self.parse_bits(0)
        self.need("op", "..")
        hi = self.parse_bits(0)
        self.need("op", ")")
        return lo, hi

    def quantifier(self, value=None):
        """any / all / none / N / N% -> f(ctx, total) giving the number needed (or a test)."""
        if value is None:
            kind, value = self.take()
        if value == "any":
            return lambda ctx, hits, total: hits >= 1
        if value == "all":
            return lambda ctx, hits, total: total > 0 and hits == total
        if value == "none":
            return lambda ctx, hits, total: hits == 0
        if callable(value):
            if self.accept("op", "%"):
                return lambda ctx, hits, total, f=value: total > 0 and hits * 100 >= _num(f(ctx)) * total
            return lambda ctx, hits, total, f=value: (n := _num(f(ctx))) is not UNDEF and hits >= n
        self.fail("expected any, all, none or a number before 'of'")

    def of_expr(self, quant):
        names = self.string_set()
        where = None
        if self.accept("word", "in"):
            lo, hi = self.range_after_in()
            where = ("in", lo, hi)
        elif self.accept("word", "at"):
            where = ("at", self.parse_bits(0), None)

        def run(ctx, q=quant, ns=names, w=where):
            if w is None:
                hits = sum(1 for s in ns if ctx.counts.get(s))
            elif w[0] == "in":
                lo, hi = _num(w[1](ctx)), _num(w[2](ctx))
                if lo is UNDEF or hi is UNDEF:
                    return UNDEF
                hits = sum(1 for s in ns if any(lo <= o <= hi for o, _ in ctx.counts.get(s, [])))
            else:
                at = _num(w[1](ctx))
                hits = sum(1 for s in ns if any(o == at for o, _ in ctx.counts.get(s, [])))
            return q(ctx, hits, len(ns))
        return run

    def parse_for(self):
        kind, value = self.take()
        if kind == "word" and value in ("any", "all", "none"):
            quant = self.quantifier(value)
        else:
            self.i -= 1
            quant = self.quantifier(self.parse_bits(0))
        if self.accept("word", "of"):
            names = self.string_set()
            self.need("op", ":")
            self.need("op", "(")
            self.anon_ok += 1
            body = self.parse_or()
            self.anon_ok -= 1
            self.need("op", ")")

            def run(ctx, q=quant, ns=names, b=body):
                saved, hits = ctx.anon, 0
                for s in ns:
                    ctx.anon = s
                    hits += truth(b(ctx))
                ctx.anon = saved
                return q(ctx, hits, len(ns))
            return run
        variables = []
        while True:
            if self.peek("word") is None:
                self.fail("expected a variable name after 'for any'")
            variables.append(self.take()[1])
            if not self.accept("op", ","):
                break
        self.need("word", "in")
        iterable, elem = self.iterable(len(variables))
        self.need("op", ":")
        self.need("op", "(")
        self.scopes.append(dict(zip(variables, elem)))
        body = self.parse_or()
        self.scopes.pop()
        self.need("op", ")")

        def run(ctx, q=quant, it=iterable, vs=variables, b=body):
            items = it(ctx)
            if items is UNDEF:
                return UNDEF
            saved = {v: ctx.vars.get(v, UNDEF) for v in vs}
            hits = 0
            for item in items:
                for v, x in zip(vs, item if len(vs) > 1 else (item,)):
                    ctx.vars[v] = x
                hits += truth(b(ctx))
            ctx.vars.update(saved)
            return q(ctx, hits, len(items))
        return run

    def iterable(self, nvars):
        """(lo..hi), (a, b, c) or a module array/dictionary -> (f(ctx) -> list, element schemas)."""
        if self.peek("op", "(") is not None:
            self.take()
            first = self.parse_bits(0)
            if self.accept("op", ".."):
                hi = self.parse_bits(0)
                self.need("op", ")")
                if nvars != 1:
                    self.fail("a range gives one value at a time; use one variable")

                def rng(ctx, a=first, b=hi):
                    lo, top = _num(a(ctx)), _num(b(ctx))
                    if not isinstance(lo, int) or not isinstance(top, int):
                        return UNDEF
                    return list(range(lo, top + 1)) if top - lo < 1_000_000 else UNDEF
                return rng, [None]
            items = [first]
            while self.accept("op", ","):
                items.append(self.parse_bits(0))
            self.need("op", ")")
            return (lambda ctx, xs=items: [x(ctx) for x in xs]), [None]
        fn, schema = self.parse_postfix()
        if isinstance(schema, tuple) and schema[0] == "array":
            if nvars != 1:
                self.fail("an array gives one value at a time; use one variable")
            elem = dict(schema[1], __path__=schema[2]) if isinstance(schema[1], dict) else schema[1]
            return (lambda ctx, f=fn: v if isinstance(v := f(ctx), list) else UNDEF), [elem]
        if isinstance(schema, tuple) and schema[0] == "dict":
            if nvars != 2:
                self.fail("a dictionary gives a key and a value; use two variables (for any k, v in ...)")
            return (lambda ctx, f=fn: list(v.items()) if isinstance(v := f(ctx), dict) else UNDEF), [None, schema[1]]
        self.fail("'for ... in' needs a range like (1..#a), a list like (1, 2, 3) or a module array")

    def string_ref(self, value):
        sigil, rest = value[0], value[1:]
        if rest.endswith("*"):
            self.fail(f"{value} can only be used inside 'of (...)'")
        anonymous = rest == ""
        if anonymous and not self.anon_ok:
            self.fail(f"a bare '{sigil}' only works inside 'for ... of ... : ( )'")
        name = "$" + rest
        if not anonymous:
            self.check_name(name)

        def hits_of(ctx, n=name, anon=anonymous):
            return ctx.counts.get(ctx.anon if anon else n, [])
        if sigil == "$":
            if self.accept("word", "at"):
                where = self.parse_bits(0)
                return lambda ctx, w=where: (lambda at: UNDEF if at is UNDEF else any(o == at for o, _ in hits_of(ctx)))(_num(w(ctx)))
            if self.accept("word", "in"):
                lo, hi = self.range_after_in()

                def within(ctx, a=lo, b=hi):
                    x, y = _num(a(ctx)), _num(b(ctx))
                    return UNDEF if x is UNDEF or y is UNDEF else any(x <= o <= y for o, _ in hits_of(ctx))
                return within
            return lambda ctx: bool(hits_of(ctx))
        if sigil == "#":
            if self.accept("word", "in"):
                lo, hi = self.range_after_in()

                def count_in(ctx, a=lo, b=hi):
                    x, y = _num(a(ctx)), _num(b(ctx))
                    return UNDEF if x is UNDEF or y is UNDEF else sum(1 for o, _ in hits_of(ctx) if x <= o <= y)
                return count_in
            return lambda ctx: len(hits_of(ctx))
        index = None
        if self.accept("op", "["):
            index = self.parse_or()
            self.need("op", "]")
        slot = 0 if sigil == "@" else 1

        def nth(ctx, ix=index, k=slot):
            hits = hits_of(ctx)
            i = 1 if ix is None else _num(ix(ctx))
            if not isinstance(i, int) or i < 1 or i > len(hits):
                return UNDEF
            return hits[i - 1][k]
        return nth

    def parse_primary(self):
        if self.accept("op", "("):
            inner = self.parse_or()
            self.need("op", ")")
            return inner, None
        kind, value = self.take()
        if kind == "num":
            if self.peek("word", "of") is not None or (self.peek("op", "%") is not None and self.peek("word", "of", 1)):
                quant = self.quantifier(lambda ctx, v=value: v)
                self.need("word", "of")
                return self.of_expr(quant), None
            return (lambda ctx, v=value: v), None
        if kind == "text":
            return (lambda ctx, v=value: v), None
        if kind == "regex":
            self.fail("a /regex/ can only follow 'matches' in a condition")
        if kind == "str":
            return self.string_ref(value), None
        if kind == "op":
            self.fail(f"unexpected '{value}' in the condition")
        # words
        if value in ("true", "false"):
            return (lambda ctx, v=value == "true": v), None
        if value == "filesize":
            return (lambda ctx: ctx.size), None
        if value == "entrypoint":
            return (lambda ctx: ctx.entrypoint()), None
        if value in ("any", "all", "none"):
            quant = self.quantifier(value)
            self.need("word", "of")
            return self.of_expr(quant), None
        if value == "for":
            return self.parse_for(), None
        if value in INT_READERS:
            size, signed, big = INT_READERS[value]
            self.need("op", "(")
            where = self.parse_or()
            self.need("op", ")")

            def read(ctx, w=where):
                off = _num(w(ctx))
                if not isinstance(off, int) or off < 0 or off + size > ctx.size:
                    return UNDEF
                return int.from_bytes(ctx.data[off:off + size], "big" if big else "little", signed=signed)
            return read, None
        for scope in reversed(self.scopes):
            if value in scope:
                return (lambda ctx, v=value: ctx.vars.get(v, UNDEF)), scope[value]
        if value in MODULES:
            return (lambda ctx, v=value: ctx.module(v)), dict(SCHEMAS[value], __path__=value)
        if value in UNSUPPORTED_MODULES:
            self.fail(f"the YARA module '{value}' isn't supported; TB's scan maps pe, elf and math")
        if value in self.known_rules:
            return (lambda ctx, v=value: ctx.rules.get(v, False)), None
        if value in KEYWORDS:
            self.fail(f"'{value}' is in the wrong place in the condition")
        self.fail(f"I don't know '{value}' in the condition (not a string, a rule defined above, or a module)")


# =============================================================================
# Modules: schemas (checked when a rule is compiled) and values (built per scan)
# =============================================================================

def func(path, arities, result="int"):
    return ("func", set(arities), result, path)


def array(elem, path):
    return ("array", elem, path)


PE_SECTION = {k: "int" for k in ("characteristics", "virtual_address", "virtual_size", "raw_data_offset",
                                 "raw_data_size", "pointer_to_relocations", "pointer_to_line_numbers",
                                 "number_of_relocations", "number_of_line_numbers")}
PE_SECTION["name"] = PE_SECTION["full_name"] = "str"
PE_CERT = {"issuer": "str", "subject": "str", "serial": "str", "not_before": "int", "not_after": "int",
           "version": "int"}
PE_SIGNATURE = dict(PE_CERT, verified="int", digest_alg="str", number_of_certificates="int",
                    certificates=array(PE_CERT, "pe.signatures[i].certificates"))
PE_RESOURCE = {"type": "int", "id": "int", "language": "int", "length": "int", "offset": "int",
               "type_string": "str", "name_string": "str"}
VERSION = {"major": "int", "minor": "int"}

PE_CONSTANTS = {
    "MACHINE_UNKNOWN": 0, "MACHINE_I386": 0x14C, "MACHINE_AMD64": 0x8664, "MACHINE_ARM": 0x1C0,
    "MACHINE_ARMNT": 0x1C4, "MACHINE_ARM64": 0xAA64, "MACHINE_IA64": 0x200, "MACHINE_THUMB": 0x1C2,
    "MACHINE_POWERPC": 0x1F0, "MACHINE_MIPS16": 0x266, "MACHINE_R4000": 0x166,
    "SUBSYSTEM_UNKNOWN": 0, "SUBSYSTEM_NATIVE": 1, "SUBSYSTEM_WINDOWS_GUI": 2, "SUBSYSTEM_WINDOWS_CUI": 3,
    "SUBSYSTEM_OS2_CUI": 5, "SUBSYSTEM_POSIX_CUI": 7, "SUBSYSTEM_NATIVE_WINDOWS": 8, "SUBSYSTEM_WINDOWS_CE_GUI": 9,
    "SUBSYSTEM_EFI_APPLICATION": 10, "SUBSYSTEM_EFI_BOOT_SERVICE_DRIVER": 11, "SUBSYSTEM_EFI_RUNTIME_DRIVER": 12,
    "SUBSYSTEM_EFI_ROM_IMAGE": 13, "SUBSYSTEM_XBOX": 14, "SUBSYSTEM_WINDOWS_BOOT_APPLICATION": 16,
    "RELOCS_STRIPPED": 0x1, "EXECUTABLE_IMAGE": 0x2, "LINE_NUMS_STRIPPED": 0x4, "LOCAL_SYMS_STRIPPED": 0x8,
    "AGGRESIVE_WS_TRIM": 0x10, "LARGE_ADDRESS_AWARE": 0x20, "BYTES_REVERSED_LO": 0x80, "MACHINE_32BIT": 0x100,
    "DEBUG_STRIPPED": 0x200, "REMOVABLE_RUN_FROM_SWAP": 0x400, "NET_RUN_FROM_SWAP": 0x800, "SYSTEM": 0x1000,
    "DLL": 0x2000, "UP_SYSTEM_ONLY": 0x4000, "BYTES_REVERSED_HI": 0x8000,
    "HIGH_ENTROPY_VA": 0x20, "DYNAMIC_BASE": 0x40, "FORCE_INTEGRITY": 0x80, "NX_COMPAT": 0x100,
    "NO_ISOLATION": 0x200, "NO_SEH": 0x400, "NO_BIND": 0x800, "APPCONTAINER": 0x1000, "WDM_DRIVER": 0x2000,
    "GUARD_CF": 0x4000, "TERMINAL_SERVER_AWARE": 0x8000,
    "SECTION_CNT_CODE": 0x20, "SECTION_CNT_INITIALIZED_DATA": 0x40, "SECTION_CNT_UNINITIALIZED_DATA": 0x80,
    "SECTION_MEM_DISCARDABLE": 0x02000000, "SECTION_MEM_NOT_CACHED": 0x04000000,
    "SECTION_MEM_NOT_PAGED": 0x08000000, "SECTION_MEM_SHARED": 0x10000000, "SECTION_MEM_EXECUTE": 0x20000000,
    "SECTION_MEM_READ": 0x40000000, "SECTION_MEM_WRITE": 0x80000000,
    "RESOURCE_TYPE_CURSOR": 1, "RESOURCE_TYPE_BITMAP": 2, "RESOURCE_TYPE_ICON": 3, "RESOURCE_TYPE_MENU": 4,
    "RESOURCE_TYPE_DIALOG": 5, "RESOURCE_TYPE_STRING": 6, "RESOURCE_TYPE_FONTDIR": 7, "RESOURCE_TYPE_FONT": 8,
    "RESOURCE_TYPE_ACCELERATOR": 9, "RESOURCE_TYPE_RCDATA": 10, "RESOURCE_TYPE_MESSAGETABLE": 11,
    "RESOURCE_TYPE_GROUP_CURSOR": 12, "RESOURCE_TYPE_GROUP_ICON": 14, "RESOURCE_TYPE_VERSION": 16,
    "RESOURCE_TYPE_DLGINCLUDE": 17, "RESOURCE_TYPE_PLUGPLAY": 19, "RESOURCE_TYPE_VXD": 20,
    "RESOURCE_TYPE_ANICURSOR": 21, "RESOURCE_TYPE_ANIICON": 22, "RESOURCE_TYPE_HTML": 23,
    "RESOURCE_TYPE_MANIFEST": 24,
}
for _i, _n in enumerate(["EXPORT", "IMPORT", "RESOURCE", "EXCEPTION", "SECURITY", "BASERELOC", "DEBUG",
                         "ARCHITECTURE", "GLOBALPTR", "TLS", "LOAD_CONFIG", "BOUND_IMPORT", "IAT", "DELAY_IMPORT",
                         "COM_DESCRIPTOR"]):
    PE_CONSTANTS[f"IMAGE_DIRECTORY_ENTRY_{_n}"] = _i

PE_SCHEMA = {k: "int" for k in (
    "is_pe", "machine", "number_of_sections", "timestamp", "pointer_to_symbol_table", "number_of_symbols",
    "size_of_optional_header", "characteristics", "entry_point", "entry_point_raw", "image_base", "subsystem",
    "dll_characteristics", "size_of_code", "size_of_initialized_data", "size_of_uninitialized_data",
    "base_of_code", "section_alignment", "file_alignment", "size_of_image", "size_of_headers", "checksum",
    "size_of_stack_reserve", "size_of_stack_commit", "size_of_heap_reserve", "size_of_heap_commit",
    "number_of_rva_and_sizes", "number_of_imports", "number_of_imported_functions", "number_of_exports",
    "number_of_resources", "number_of_signatures", "is_signed", "opthdr_magic")}
PE_SCHEMA.update({k: "int" for k in PE_CONSTANTS})
PE_SCHEMA.update({
    "linker_version": VERSION, "os_version": VERSION, "image_version": VERSION, "subsystem_version": VERSION,
    "dll_name": "str", "pdb_path": "str",
    "sections": array(PE_SECTION, "pe.sections[i]"),
    "data_directories": array({"virtual_address": "int", "size": "int"}, "pe.data_directories[i]"),
    "resources": array(PE_RESOURCE, "pe.resources[i]"),
    "signatures": array(PE_SIGNATURE, "pe.signatures[i]"),
    "overlay": {"offset": "int", "size": "int"},
    "rich_signature": {"offset": "int", "length": "int", "key": "int",
                       "version": func("pe.rich_signature.version", (1, 2)),
                       "toolid": func("pe.rich_signature.toolid", (1, 2))},
    "imphash": func("pe.imphash", (0,), "str"),
    "imports": func("pe.imports", (1, 2)),
    "exports": func("pe.exports", (1,)),
    "section_index": func("pe.section_index", (1,)),
    "is_dll": func("pe.is_dll", (0,)), "is_32bit": func("pe.is_32bit", (0,)), "is_64bit": func("pe.is_64bit", (0,)),
    "rva_to_offset": func("pe.rva_to_offset", (1,)),
    "language": func("pe.language", (1,)), "locale": func("pe.locale", (1,)),
})

ELF_CONSTANTS = {
    "ET_NONE": 0, "ET_REL": 1, "ET_EXEC": 2, "ET_DYN": 3, "ET_CORE": 4,
    "EM_NONE": 0, "EM_M32": 1, "EM_SPARC": 2, "EM_386": 3, "EM_68K": 4, "EM_88K": 5, "EM_860": 7, "EM_MIPS": 8,
    "EM_MIPS_RS3_LE": 10, "EM_PPC": 20, "EM_PPC64": 21, "EM_ARM": 40, "EM_X86_64": 62, "EM_AARCH64": 183,
    "SHT_NULL": 0, "SHT_PROGBITS": 1, "SHT_SYMTAB": 2, "SHT_STRTAB": 3, "SHT_RELA": 4, "SHT_HASH": 5,
    "SHT_DYNAMIC": 6, "SHT_NOTE": 7, "SHT_NOBITS": 8, "SHT_REL": 9, "SHT_SHLIB": 10, "SHT_DYNSYM": 11,
    "SHF_WRITE": 1, "SHF_ALLOC": 2, "SHF_EXECINSTR": 4,
    "PT_NULL": 0, "PT_LOAD": 1, "PT_DYNAMIC": 2, "PT_INTERP": 3, "PT_NOTE": 4, "PT_SHLIB": 5, "PT_PHDR": 6,
    "PT_TLS": 7, "PT_GNU_EH_FRAME": 0x6474E550, "PT_GNU_STACK": 0x6474E551, "PT_GNU_RELRO": 0x6474E552,
    "PF_X": 1, "PF_W": 2, "PF_R": 4,
    "DT_NULL": 0, "DT_NEEDED": 1, "DT_STRTAB": 5, "DT_SYMTAB": 6, "DT_SONAME": 14, "DT_RPATH": 15,
    "DT_RUNPATH": 29, "DT_FLAGS": 30, "DT_BIND_NOW": 24, "DT_TEXTREL": 22,
}
ELF_SCHEMA = {k: "int" for k in ("type", "machine", "entry_point", "number_of_sections", "number_of_segments",
                                 "sh_offset", "sh_entry_size", "ph_offset", "ph_entry_size",
                                 "dynamic_section_entries")}
ELF_SCHEMA.update({k: "int" for k in ELF_CONSTANTS})
ELF_SCHEMA.update({
    "sections": array({"type": "int", "flags": "int", "address": "int", "name": "str", "size": "int",
                       "offset": "int"}, "elf.sections[i]"),
    "segments": array({k: "int" for k in ("type", "flags", "offset", "virtual_address", "physical_address",
                                          "file_size", "memory_size", "alignment")}, "elf.segments[i]"),
    "dynamic": array({"type": "int", "val": "int"}, "elf.dynamic[i]"),
})

MATH_SCHEMA = {
    "entropy": func("math.entropy", (1, 2), "float"), "mean": func("math.mean", (1, 2), "float"),
    "deviation": func("math.deviation", (2, 3), "float"),
    "serial_correlation": func("math.serial_correlation", (1, 2), "float"),
    "monte_carlo_pi": func("math.monte_carlo_pi", (1, 2), "float"),
    "in_range": func("math.in_range", (3,)), "max": func("math.max", (2,)), "min": func("math.min", (2,)),
    "abs": func("math.abs", (1,)), "to_number": func("math.to_number", (1,)),
    "count": func("math.count", (1, 3)), "percentage": func("math.percentage", (1, 3), "float"),
    "mode": func("math.mode", (0, 2)),
}

SCHEMAS = {"pe": PE_SCHEMA, "elf": ELF_SCHEMA, "math": MATH_SCHEMA}


def _epoch(text):
    if not text:
        return UNDEF
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=dt.timezone.utc).timestamp())


def _name_match(pattern, text: str) -> bool:
    if isinstance(pattern, re.Pattern):
        return bool(pattern.search(text))
    return isinstance(pattern, str) and pattern.lower() == text.lower()


def build_pe(data: bytes) -> dict:
    from tblib import pe as pelib
    try:
        p = pelib.PE(data)
    except Exception:  # not a PE (or too damaged): every field is undefined, like YARA
        return {"is_pe": 0}
    try:
        entries = p.import_entries()
        exports = p.exports()["functions"]
        resources = p.resources(entropy=False)  # YARA has no resource entropy; skip the work
        signature = p.signature()
        rich = p.rich_header()
    except (struct.error, IndexError, ValueError):
        entries, exports, resources, signature, rich = [], [], [], None, None
    names = {v: k for k, v in pelib.RESOURCE_TYPES.items()}

    def resource(r):
        kind = names.get(r["type"])
        out = {"language": r["language"], "length": r["size"], "offset": r["offset"] if r["offset"] is not None else UNDEF}
        if kind is None:
            out["type_string"] = r["type"]
        else:
            out["type"] = kind
        if r["name"].isdigit():
            out["id"] = int(r["name"])
        else:
            out["name_string"] = r["name"]
        return out

    def cert(c):
        return {"issuer": c["issuer"], "subject": c["subject"], "serial": c["serial"], "version": 3,
                "not_before": _epoch(c["not_before"]), "not_after": _epoch(c["not_after"])}
    signatures = []
    if signature and not signature["problem"]:
        certs = [cert(c) for c in signature["certificates"]]
        signer = next((cert(c) for c in signature["certificates"] if c.get("is_signer")), {})
        signatures.append(dict(signer, verified=0, digest_alg=signature["digest_algorithm"] or UNDEF,
                               number_of_certificates=len(certs), certificates=certs))

    def imports(dll, fn=None):
        found = [(d, e) for d, e in entries if _name_match(dll, d)]
        if fn is None:
            return sum(len(e) for _, e in found)
        if isinstance(fn, int):
            return any(o == fn for _, e in found for _n, o in e)
        if isinstance(fn, re.Pattern):
            return sum(1 for _, e in found for n, _o in e if n and fn.search(n))
        return any(n and n.lower() == fn.lower() for _, e in found for n, _o in e)

    def exports_fn(what):
        if isinstance(what, int):
            return any(f["ordinal"] == what for f in exports)
        return any(_name_match(what, f["name"]) for f in exports)

    def section_index(what):
        for i, s in enumerate(p.sections):
            if isinstance(what, str) and s["name"] == what:
                return i
            if isinstance(what, int) and s["virtual_address"] <= what < s["virtual_address"] + max(s["virtual_size"], 1):
                return i
        return None

    def rich_count(first, second=None, by="product"):
        if not rich:
            return None
        total = 0
        for e in rich["entries"]:
            prod, build = (first, second) if by == "product" else (second, first)
            if (prod is None or e["product_id"] == prod) and (build is None or e["build"] == build):
                total += e["count"]
        return total
    rich_value = {"offset": rich["offset"], "length": 0, "key": int(rich["key"], 16),
                  "toolid": lambda a, b=None: rich_count(a, b, "product"),
                  "version": lambda a, b=None: rich_count(a, b, "build")} if rich else {}
    if rich:
        rich_value["length"] = data.find(b"Rich", rich["offset"]) - rich["offset"]
    entry_off = p.rva_to_offset(p.entry_point)
    overlay_start = p.end_of_sections()
    out = dict(PE_CONSTANTS)
    out.update({
        "is_pe": 1, "machine": p.machine, "number_of_sections": p.section_count, "timestamp": p.timestamp,
        "pointer_to_symbol_table": p.symtab_ptr, "number_of_symbols": p.symbol_count,
        "size_of_optional_header": p.opt_size, "characteristics": p.characteristics,
        "entry_point": entry_off if entry_off is not None else UNDEF, "entry_point_raw": p.entry_point,
        "image_base": p.image_base, "subsystem": p.subsystem, "dll_characteristics": p.dll_characteristics,
        "size_of_code": p.size_of_code, "size_of_initialized_data": p.size_of_init,
        "size_of_uninitialized_data": p.size_of_uninit, "base_of_code": p.base_of_code,
        "section_alignment": p.section_alignment, "file_alignment": p.file_alignment,
        "size_of_image": p.size_of_image, "size_of_headers": p.size_of_headers, "checksum": p.checksum,
        "size_of_stack_reserve": p.stack_reserve, "size_of_stack_commit": p.stack_commit,
        "size_of_heap_reserve": p.heap_reserve, "size_of_heap_commit": p.heap_commit,
        "number_of_rva_and_sizes": p.dir_count, "opthdr_magic": p.magic,
        "linker_version": {"major": p.linker_major, "minor": p.linker_minor},
        "os_version": {"major": p.os_major, "minor": p.os_minor},
        "image_version": {"major": p.image_major, "minor": p.image_minor},
        "subsystem_version": {"major": p.subsys_major, "minor": p.subsys_minor},
        "sections": [{"name": s["name"], "full_name": s["name"], "characteristics": s["characteristics"],
                      "virtual_address": s["virtual_address"], "virtual_size": s["virtual_size"],
                      "raw_data_offset": s["raw_offset"], "raw_data_size": s["raw_size"]} for s in p.sections],
        "data_directories": [{"virtual_address": p.directories.get(n, (0, 0))[0], "size": p.directories.get(n, (0, 0))[1]}
                             for n in pelib.DIRECTORIES[:min(p.dir_count, 16)]],
        "number_of_imports": len(entries), "number_of_imported_functions": sum(len(e) for _, e in entries),
        "number_of_exports": len(exports), "number_of_resources": len(resources),
        "resources": [resource(r) for r in resources],
        "number_of_signatures": len(signatures), "signatures": signatures,
        "is_signed": int(bool(p.directories.get("security", (0, 0))[1])),
        "overlay": {"offset": overlay_start, "size": max(0, len(data) - overlay_start)},
        "rich_signature": rich_value,
        "imphash": lambda: p.imphash(), "imports": imports, "exports": exports_fn, "section_index": section_index,
        "is_dll": lambda: bool(p.characteristics & 0x2000), "is_32bit": lambda: not p.plus, "is_64bit": lambda: p.plus,
        "rva_to_offset": p.rva_to_offset,
        "language": lambda lang: any(r["language"] == lang for r in resources),
        "locale": lambda lang: any(r["language"] == lang for r in resources),
    })
    dll_name = p.exports()["name"]
    if dll_name:
        out["dll_name"] = dll_name
    pdb = p.debug_pdb()
    if pdb:
        out["pdb_path"] = pdb
    return out


def build_elf(data: bytes) -> dict:
    from tblib import elf as elflib
    try:
        e = elflib.ELF(data)
        dynamic = e.dynamic()["entries"]
    except Exception:  # not ELF: everything is undefined
        return {}
    seg_types = {v: k for k, v in elflib.SEGMENT_TYPES.items()}
    dyn_tags = {v: k for k, v in elflib.DYN_TAGS.items()}

    def number(name, table):
        return table[name] if name in table else int(name, 16)
    entry = e.address_to_offset(e.entry)
    out = dict(ELF_CONSTANTS)
    out.update({
        "type": e.e_type, "machine": e.machine, "entry_point": entry if entry is not None else UNDEF,
        "number_of_sections": e.shnum, "number_of_segments": e.phnum, "sh_offset": e.shoff,
        "sh_entry_size": e.shentsize, "ph_offset": e.phoff, "ph_entry_size": e.phentsize,
        "sections": [{"type": s["type_id"], "name": s["name"], "address": s["address"], "size": s["size"],
                      "offset": s["offset"], "flags": sum(bit for bit, n in elflib.SECTION_FLAGS if n in s["flags"])}
                     for s in e.sections],
        "segments": [{"type": number(s["type"], seg_types), "offset": s["offset"], "virtual_address": s["address"],
                      "physical_address": s["address"], "file_size": s["file_size"], "memory_size": s["memory_size"],
                      "alignment": s["align"], "flags": sum(bit for bit, n in elflib.SEGMENT_FLAGS if n in s["flags"])}
                     for s in e.segments],
        "dynamic_section_entries": len(dynamic),
        "dynamic": [{"type": number(d["tag"], dyn_tags), "val": d["value"]} for d in dynamic],
    })
    return out


def build_math(data: bytes) -> dict:
    def block(args, n_extra=0):
        """(offset, size, *extra) or (string, *extra) -> bytes (None if out of range)."""
        if isinstance(args[0], str):
            return args[0].encode("latin-1", "replace"), args[1:]
        offset, size = args[0], args[1]
        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size < 0 or offset > len(data):
            return None, ()
        return data[offset:offset + size], args[2:]

    def histogram(chunk):
        return collections.Counter(chunk)  # counted in C

    def entropy(*args):
        chunk, _ = block(args)
        if not chunk:
            return None
        n = len(chunk)
        return -sum(c / n * math.log2(c / n) for c in histogram(chunk).values())

    def mean(*args):
        chunk, _ = block(args)
        return sum(chunk) / len(chunk) if chunk else None

    def deviation(*args):
        chunk, rest = block(args)
        if not chunk or not rest:
            return None
        return sum(c * abs(b - rest[0]) for b, c in histogram(chunk).items()) / len(chunk)

    def serial_correlation(*args):
        chunk, _ = block(args)
        if not chunk:
            return None
        # ent's serial correlation (what YARA's math module computes), with the products summed in C
        t1 = float(sum(map(operator.mul, chunk, chunk[1:])) + chunk[-1] * chunk[0])
        t2 = float(sum(chunk)) ** 2
        t3 = float(sum(c * b * b for b, c in histogram(chunk).items()))
        n = len(chunk)
        scc = n * t3 - t2
        return -100000.0 if scc == 0 else (n * t1 - t2) / scc

    def monte_carlo_pi(*args):
        chunk, _ = block(args)
        if not chunk or len(chunk) < 6:
            return None
        inside = total = 0
        radius = (256.0 ** 3 - 1) ** 2
        for i in range(0, len(chunk) - 5, 6):
            x = int.from_bytes(chunk[i:i + 3], "big")
            y = int.from_bytes(chunk[i + 3:i + 6], "big")
            total += 1
            inside += x * x + y * y <= radius
        estimate = 4.0 * inside / total
        return abs((estimate - math.pi) / math.pi)

    def count(byte, *rest):
        chunk, _ = block(rest) if rest else (data, ())
        return None if chunk is None else chunk.count(bytes([byte & 0xFF]))

    def percentage(byte, *rest):
        chunk, _ = block(rest) if rest else (data, ())
        return chunk.count(bytes([byte & 0xFF])) / len(chunk) if chunk else None

    def mode(*rest):
        chunk, _ = block(rest) if rest else (data, ())
        if not chunk:
            return None
        counts = histogram(chunk)
        return max(sorted(counts), key=lambda b: counts[b])  # the lowest byte wins a tie
    return {"entropy": entropy, "mean": mean, "deviation": deviation, "serial_correlation": serial_correlation,
            "monte_carlo_pi": monte_carlo_pi, "in_range": lambda t, lo, hi: lo <= t <= hi,
            "max": max, "min": min, "abs": abs, "to_number": lambda b: 1 if b else 0, "count": count,
            "percentage": percentage, "mode": mode}


BUILDERS = {"pe": build_pe, "elf": build_elf, "math": build_math}


class Ctx:
    """What a condition sees while one rule runs over one buffer."""

    __slots__ = ("counts", "size", "data", "rules", "vars", "anon", "modules")

    def __init__(self, counts, data, rules=None, modules=None):
        self.counts = counts
        self.size = len(data)
        self.data = data
        self.rules = rules if rules is not None else {}
        self.vars = {}
        self.anon = None
        self.modules = modules if modules is not None else {}

    def module(self, name):
        if name not in self.modules:
            try:
                self.modules[name] = BUILDERS[name](self.data)
            except Exception:  # a file too damaged to describe: its fields are undefined, as in YARA
                self.modules[name] = {}
        return self.modules[name]

    def entrypoint(self):
        """YARA's old `entrypoint`: the entry point's file offset in a PE or ELF file."""
        if self.data[:2] == b"MZ":
            return self.module("pe").get("entry_point", UNDEF)
        if self.data[:4] == b"\x7fELF":
            return self.module("elf").get("entry_point", UNDEF)
        return UNDEF
