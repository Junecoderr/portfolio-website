"""Run small TB snippets at build time so the pictures show real results.

Each example runs in a scratch folder laid out like the playground's pretend
disk (apps/data/... plus a few small files), with a seeded random number
generator. The value comes back as TB prints it inside a list (say([v])),
which `parse_literal` turns into Python values for drawing. Paths of the
build machine are replaced by neutral ones so nothing personal reaches the
pages.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import random
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_SANDBOX: Path | None = None
_CACHE: dict[str, "Result"] = {}

SAMPLE_FILES = {
    "notes.txt": "buy milk\ncall Sam\nship TB 3.0\n",
    "todo.txt": "write docs\n",
    "logs/app.log": "09:14 start\n09:15 ready\n",
    "logs/old/2025.log": "archived\n",
    "data/users.csv": "name,role\nAda,admin\nBo,user\n",
    "config.json": '{"port": 8080, "debug": true}\n',
    "config.toml": 'title = "TB"\n[server]\nport = 8080\n',
    "config.yml": "name: tb\nports:\n  - 80\n  - 443\n",
    "feed.xml": '<feed><item id="1">Hello</item><item id="2">Bye</item></feed>\n',
    "page.html": "<h1>{{title}}</h1>\n",
}


@dataclass
class Result:
    ok: bool
    text: str          # the value as TB writes it (or the error message)
    value: object = None
    printed: tuple = ()  # other lines the snippet printed


class Bytes:
    def __init__(self, data: bytes):
        self.data = data

    def __eq__(self, other):
        return isinstance(other, Bytes) and other.data == self.data

    def __hash__(self):
        return hash(self.data)


class Opaque:
    """A value TB prints in a form we don't parse (functions, ranges...)."""

    def __init__(self, text: str):
        self.text = text

    def __str__(self):
        return self.text

    def __eq__(self, other):
        return isinstance(other, Opaque) and other.text == self.text

    def __hash__(self):
        return hash(self.text)


def sandbox() -> Path:
    global _SANDBOX
    if _SANDBOX is None:
        base = Path(tempfile.mkdtemp(prefix="tbvis-"))
        shutil.copytree(ROOT / "apps" / "data", base / "apps" / "data")
        for rel, text in SAMPLE_FILES.items():
            p = base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        _SANDBOX = base
    return _SANDBOX


@contextlib.contextmanager
def _inside(folder: Path):
    old = os.getcwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(old)


def scrub(text: str) -> str:
    """Replace build-machine paths and the user name with neutral ones."""
    box = str(_SANDBOX) if _SANDBOX else None
    if box:
        real = os.path.realpath(box)
        for b in sorted({real, box}, key=len, reverse=True):
            text = text.replace(b, "/home/you/project")
    home = str(Path.home())
    text = text.replace(home, "/home/you")
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover
        user = ""
    if user and len(user) > 2:
        text = text.replace(user, "you")
    tmp = tempfile.gettempdir()
    text = text.replace(os.path.realpath(tmp), "/tmp").replace(tmp, "/tmp")
    return text


def run(libs, setup: str, expr: str) -> Result:
    """Evaluate expr (after setup, with the libraries imported) and give back its value."""
    imports = "".join(f"import {m}\n" for m in sorted(set(libs)) if m)
    key = f"{imports}\n{setup}\n{expr}"
    if key in _CACHE:
        return _CACHE[key]
    import tb  # imported late: gen_lib_docs puts the project root on sys.path

    source = (
        f"{imports}{setup}\n"
        f"try {{\n  let __v = {expr}\n  say(\"@@V@@\" + str([__v]))\n}} catch __e {{\n"
        f"  say(\"@@E@@\" + str(__e.message))\n}}\n"
    )
    lines: list[str] = []
    box = sandbox()
    try:
        with _inside(box):
            tb.run(source, output=lines.append, input_fn=lambda prompt="": "42", rng=random.Random(7))
    except BaseException as err:  # parse errors and the like
        if isinstance(err, KeyboardInterrupt):
            raise
        res = Result(False, scrub(str(err)).splitlines()[0][:200] if str(err) else type(err).__name__)
        _CACHE[key] = res
        return res
    printed = tuple(scrub(x) for x in lines if not x.startswith(("@@V@@", "@@E@@")))
    res = Result(False, "no value")
    for line in lines:
        if line.startswith("@@V@@"):
            text = scrub(line[5:])
            try:
                val = parse_literal(text)
                val = val[0] if isinstance(val, list) and val else val
            except ValueError:
                val = Opaque(text[1:-1])
            inner = text[1:-1] if text.startswith("[") and text.endswith("]") else text
            res = Result(True, inner, val, printed)
        elif line.startswith("@@E@@"):
            res = Result(False, scrub(line[5:]), None, printed)
    _CACHE[key] = res
    return res


# ------------------------------------------------------------ literal parser
class _P:
    def __init__(self, s: str):
        self.s = s
        self.i = 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def peek(self, n=1):
        return self.s[self.i:self.i + n]

    def value(self):
        self.ws()
        c = self.peek()
        if c == "[":
            return self.seq("[", "]")
        if c == "{":
            return self.mapping()
        if c == '"':
            return self.string()
        if self.peek(2) == 'b"':
            self.i += 1
            raw = self.string(raw=True)
            return Bytes(raw)
        if c == "<":
            j = self.s.find(">", self.i)
            if j < 0:
                raise ValueError("bad <")
            t = self.s[self.i:j + 1]
            self.i = j + 1
            return Opaque(t)
        return self.atom()

    def atom(self):
        j = self.i
        depth = 0
        while j < len(self.s):
            ch = self.s[j]
            if ch in "([":
                depth += 1
            elif ch in ")]" and depth:
                depth -= 1
            elif ch in ",]}" and depth == 0:
                break
            elif ch == ":" and depth == 0 and self._in_key:
                break  # a map key read as an atom stops at ':'
            j += 1
        tok = self.s[self.i:j].strip()
        self.i = j
        if tok == "nil":
            return None
        if tok == "true":
            return True
        if tok == "false":
            return False
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            pass
        if not tok:
            raise ValueError("empty")
        return Opaque(tok)

    _in_key = False

    def string(self, raw=False):
        assert self.s[self.i] == '"'
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                raise ValueError("unterminated")
            ch = self.s[self.i]
            if ch == '"':
                self.i += 1
                break
            if ch == "\\":
                nxt = self.s[self.i + 1]
                self.i += 2
                if nxt == "n":
                    out.append("\n")
                elif nxt == "t":
                    out.append("\t")
                elif nxt == "r":
                    out.append("\r")
                elif nxt == "0":
                    out.append("\0")
                elif nxt == "x":
                    out.append(chr(int(self.s[self.i:self.i + 2], 16)))
                    self.i += 2
                elif nxt == "u":
                    out.append(chr(int(self.s[self.i:self.i + 4], 16)))
                    self.i += 4
                else:
                    out.append(nxt)
                continue
            out.append(ch)
            self.i += 1
        text = "".join(out)
        if raw:
            return text.encode("latin-1", errors="replace")
        return text

    def seq(self, open_, close):
        self.i += 1
        items = []
        self.ws()
        if self.peek() == close:
            self.i += 1
            return items
        while True:
            items.append(self.value())
            self.ws()
            c = self.peek()
            if c == ",":
                self.i += 1
                continue
            if c == close:
                self.i += 1
                return items
            raise ValueError(f"expected , or {close}")

    def mapping(self):
        self.i += 1
        out = {}
        self.ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            self.ws()
            if self.peek() == '"':
                k = self.string()
            else:
                self._in_key = True
                try:
                    k = self.atom()
                finally:
                    self._in_key = False
                if isinstance(k, Opaque):
                    k = k.text
            self.ws()
            if self.peek() != ":":
                raise ValueError("expected :")
            self.i += 1
            out[k] = self.value()
            self.ws()
            c = self.peek()
            if c == ",":
                self.i += 1
                continue
            if c == "}":
                self.i += 1
                return out
            raise ValueError("expected , or }")


def parse_literal(text: str):
    p = _P(text)
    v = p.value()
    p.ws()
    if p.i != len(text):
        raise ValueError("trailing text")
    return v


def literal(v) -> str:
    """Write a Python value the way TB would (for static examples)."""
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'
    if isinstance(v, Bytes):
        return 'b"' + "".join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\' else f"\\x{b:02x}" for b in v.data) + '"'
    if isinstance(v, Opaque):
        return v.text
    if isinstance(v, list):
        return "[" + ", ".join(literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{literal(k) if isinstance(k, str) else k}: {literal(x)}" for k, x in v.items()) + "}"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return str(v)
