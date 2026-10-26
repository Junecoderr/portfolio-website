"""Gradual types and lint for TB: the engine behind `tb check` and the LSP.

    check_source(source, path) -> list[Diagnostic]

reads a program (without running it) and reports likely mistakes:

* parse errors (with the parser's friendly message);
* names that don't exist (with "Did you mean ..."), names used before the
  line that makes them, `self`/`super` outside a class;
* unused local variables and unused imports (warnings; names starting with
  `_` are never reported);
* calls with the wrong number of inputs, or unknown named inputs, for your
  own functions, methods and classes (defaults, `...rest` and `**opts`
  understood) and for built-ins and library functions;
* unknown methods on text, lists, numbers, bytes, ranges, generators and
  functions; unknown members of libraries, enums, classes and objects;
* assigning to a `const`, calling something that isn't a function,
  unreachable code after return/throw/break/continue, a `match` over an
  enum that misses members and has no `_` arm;
* type mismatches where the types are certain: annotations (`let x: number`,
  `fn f(a: text) -> list`, class fields), literals, operators that can never
  work (`5 - "a"`), library return kinds read from the doc signatures
  (`read(path) -> text`) and built-in method return kinds.

The checker is gradual: anything it can't be sure about is `any` and is
never reported. A program that would run fine is never given an error just
because inference ran out (warnings may still point at style problems).
A line with the comment `# tb-check: ignore` is never reported.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field

from . import ast as A
from .errors import MANY, TBError

__all__ = ["Diagnostic", "check_source", "check_file"]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass
class Diagnostic:
    line: int
    col: int
    severity: str            # "error" | "warning" | "info"
    code: str
    message: str
    end_line: int | None = None
    end_col: int | None = None
    hint: str | None = None
    file: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {
            "file": self.file, "line": self.line, "col": self.col,
            "end_line": self.end_line, "end_col": self.end_col,
            "severity": self.severity, "code": self.code, "message": self.message,
            "hint": self.hint,
        }

    def format(self, path: str | None = None) -> str:
        where = path or self.file or "<program>"
        text = f"{where}:{self.line}:{self.col}: {self.severity}: {self.message}"
        if self.hint:
            text += f"\n    hint: {self.hint}"
        return text


# ---------------------------------------------------------------------------
# Types (the checker's view of values). None means "any".
# ---------------------------------------------------------------------------

PRIMITIVES = ("number", "text", "bool", "nil", "list", "map", "bytes", "range", "generator",
              "function", "class", "enum", "module", "done")
METHOD_KINDS = ("text", "list", "number", "bytes", "range", "generator", "function")


class FnSig:
    """What we know about something callable."""

    def __init__(self, label, params=None, lo=0, hi=MANY, returns=None, native=False,
                 decl=None, is_generator=False, is_async=False):
        self.label = label
        self.params = params        # list[A.Param] for TB functions
        self.lo, self.hi = lo, hi   # for native functions
        self.returns = returns
        self.native = native
        self.decl = decl
        self.is_generator = is_generator
        self.is_async = is_async


class FnT:
    def __init__(self, sig: FnSig):
        self.sig = sig


class ClassT:
    def __init__(self, info):
        self.info = info


class InstT:
    def __init__(self, info, is_self=False):
        self.info, self.is_self = info, is_self


class EnumT:
    def __init__(self, info):
        self.info = info


class EnumValT:
    def __init__(self, info):
        self.info = info


class ModT:
    def __init__(self, info):
        self.info = info


def kind_of(t) -> str | None:
    if t is None:
        return None
    if isinstance(t, str):
        return t
    if isinstance(t, FnT):
        return "function"
    if isinstance(t, ClassT):
        return "class"
    if isinstance(t, InstT):
        return "instance"
    if isinstance(t, EnumT):
        return "enum"
    if isinstance(t, EnumValT):
        return "enumvalue"
    if isinstance(t, ModT):
        return "module"
    return None


def type_label(t) -> str:
    """How a type is named in messages (matches the runtime's type_name)."""
    k = kind_of(t)
    if k == "bool":
        return "true/false"
    if k == "instance":
        return f"a {t.info.name} object" if t.info is not None else "an object"
    if k == "enumvalue":
        return f"a {t.info.name} value"
    if k == "module":
        return "a library"
    if k in ("list", "map", "number", "range", "generator", "function", "class"):
        return f"a {k}"
    if k == "enum":
        return "an enum"
    return k or "a value"


def short_label(t) -> str:
    k = kind_of(t)
    if k == "bool":
        return "true/false"
    if k == "instance":
        return t.info.name if t.info is not None else "object"
    if k == "enumvalue":
        return t.info.name
    if k == "module":
        return "library"
    return k or "value"


def doc_returns(doc: str | None):
    """The return kind from a doc signature like `read(path) -> text`."""
    if not doc:
        return None
    first = doc.partition("\n")[0]
    if "->" not in first:
        return None
    ret = first.rsplit("->", 1)[1].strip().rstrip(".").lower()
    if ret == "true/false":
        return "bool"
    if " or " in ret or "|" in ret or "/" in ret or "?" in ret:
        return None
    word = ret.split(" ")[0] if ret else ""
    if word in ("int", "float"):
        return "number"
    if word in ("number", "text", "bool", "list", "map", "bytes", "nil", "range", "generator"):
        if word in ("list", "map") or " " not in ret or ret.startswith(("list of", "map of")):
            return word
    return None


# ---------------------------------------------------------------------------
# Classes, enums and modules
# ---------------------------------------------------------------------------


class ClassInfo:
    def __init__(self, name, node=None, file=None):
        self.name = name
        self.node = node
        self.file = file
        self.base: ClassInfo | None = None
        self.base_unknown = False
        self.fields: dict = {}          # name -> annotation text or None
        self.statics: dict = {}
        self.methods: dict = {}         # name -> FnDecl
        self.static_methods: dict = {}
        self.self_fields: set = set()   # self.X = ... inside its methods
        self.extra_names: set = set()   # anything.X = ... anywhere in its file
        self.children: list = []

    def chain(self):
        out, cls, seen = [], self, set()
        while cls is not None and id(cls) not in seen:
            seen.add(id(cls))
            out.append(cls)
            cls = cls.base
        return out

    def complete(self) -> bool:
        return not any(c.base_unknown for c in self.chain())

    def is_sub(self, other) -> bool:
        return any(c is other for c in self.chain())

    def find_method(self, name):
        for c in self.chain():
            if name in c.methods:
                return c.methods[name], c
        return None, None

    def find_static(self, name):
        for c in self.chain():
            if name in c.static_methods:
                return c.static_methods[name], c
        return None, None

    def descendants(self):
        out, todo = [], list(self.children)
        while todo:
            c = todo.pop()
            if c not in out:
                out.append(c)
                todo.extend(c.children)
        return out

    def instance_names(self, with_children=False) -> set:
        names = {"fields", "class"}
        classes = self.chain() + (self.descendants() if with_children else [])
        for c in classes:
            names.update(c.fields)
            names.update(c.methods)
            names.update(c.self_fields)
            names.update(c.extra_names)
        return names

    def field_annotation(self, name):
        for c in self.chain():
            if name in c.fields:
                return c.fields[name]
        return None

    def static_names(self) -> set:
        names = {"name", "base"}
        for c in self.chain():
            names.update(c.statics)
            names.update(c.static_methods)
            names.update(c.extra_names)
        return names


def build_class_info(node: A.ClassDecl, file) -> ClassInfo:
    info = ClassInfo(node.name, node, file)
    for name, _value, ann in node.fields:
        info.fields[name] = ann
    for name, _value, ann in node.statics:
        info.statics[name] = ann
    for decl in node.methods:
        info.methods[decl.name] = decl
    for decl in node.static_methods:
        info.static_methods[decl.name] = decl
    for decl in node.methods + node.static_methods:
        for sub in walk_all(decl.body):
            for target in assign_targets(sub):
                if isinstance(target, A.Dot) and isinstance(target.target, A.Name) \
                        and target.target.name == "self":
                    info.self_fields.add(target.name)
    return info


class EnumInfo:
    def __init__(self, name, members):
        self.name, self.members = name, list(members)


class ModInfo:
    """A library or a .tb file seen from outside."""

    def __init__(self, name, label, std=True, path=None):
        self.name = name
        self.label = label           # "the fs library" / "helpers.tb"
        self.std = std
        self.path = path
        self.members: dict = {}      # name -> type
        self.private: set = set()    # names that exist but aren't shared
        self.exports: set | None = None
        self.opaque = False          # unknown shape: allow anything

    def public(self) -> list:
        return sorted(n for n in self.members if n not in self.private)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

_AST_TYPES = tuple(
    obj for obj in vars(A).values() if isinstance(obj, type) and obj.__module__ == A.__name__
)


def _flat(value):
    if isinstance(value, _AST_TYPES):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flat(item)


def children(obj):
    """The AST objects directly inside obj (lists and tuples flattened)."""
    if not isinstance(obj, _AST_TYPES):
        return
    names = getattr(type(obj), "__slots__", None)
    values = [getattr(obj, n, None) for n in names] if names else list(vars(obj).values())
    for value in values:
        yield from _flat(value)


def walk_all(obj):
    """Every AST object inside obj (obj itself included), depth first."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, (list, tuple)):
            stack.extend(reversed(cur))
            continue
        if not isinstance(cur, _AST_TYPES):
            continue
        yield cur
        names = getattr(type(cur), "__slots__", None)
        values = [getattr(cur, n, None) for n in names] if names else list(vars(cur).values())
        for value in reversed(values):
            if isinstance(value, (_AST_TYPES, list, tuple)):
                stack.append(value)


def assign_targets(node):
    if isinstance(node, A.Assign):
        return [node.target]
    if isinstance(node, A.MultiAssign):
        return list(node.targets)
    return []


def pattern_names(pattern) -> list[str]:
    if isinstance(pattern, str):
        return [pattern]
    if isinstance(pattern, A.NamePattern):
        return [pattern.name]
    if isinstance(pattern, A.ListPattern):
        names = [n for item in pattern.items for n in pattern_names(item)]
        if pattern.rest:
            names.append(pattern.rest)
        return names
    if isinstance(pattern, A.MapPattern):
        names = []
        for key, sub, _default in pattern.entries:
            names.extend(pattern_names(sub) if sub is not None else [key])
        return names
    return []


def declared_in_block(stmts) -> dict:
    """name -> how many times it is declared directly in this block."""
    counts: dict = {}

    def add(name):
        counts[name] = counts.get(name, 0) + 1

    for st in stmts:
        if isinstance(st, A.Export):
            st = st.decl
        if isinstance(st, A.Let):
            for n in pattern_names(st.target):
                add(n)
        elif isinstance(st, (A.FnDecl, A.ClassDecl, A.EnumDecl)):
            add(st.name)
        elif isinstance(st, A.Import):
            add(st.alias)
        elif isinstance(st, A.FromImport):
            for _name, alias in st.names:
                add(alias)
    return counts


# ---------------------------------------------------------------------------
# Columns: the AST only has line numbers, so we find words on the line.
# Strings and comments are blanked out first (f-text holes are kept).
# ---------------------------------------------------------------------------


def mask_source(source: str) -> list[str]:
    n = len(source)
    chars = list(source)

    def blank(a, b):
        for k in range(a, min(b, n)):
            if chars[k] != "\n":
                chars[k] = " "

    def code(i, in_hole):
        depth = 0
        while i < n:
            c = source[i]
            if c == "#" and not in_hole:
                j = source.find("\n", i)
                j = n if j < 0 else j
                blank(i, j)
                i = j
                continue
            if c in "\"'":
                k = i
                while k > 0 and i - k < 2 and source[k - 1] in "fFrRbB":
                    k -= 1
                if k < i and k > 0 and (source[k - 1].isalnum() or source[k - 1] == "_"):
                    k = i
                prefix = source[k:i].lower()
                i = string(i, "f" in prefix, "r" in prefix)
                continue
            if in_hole:
                if c in "([{":
                    depth += 1
                elif c in ")]":
                    depth -= 1
                elif c == "}":
                    if depth == 0:
                        return i
                    depth -= 1
                elif c == ":" and depth == 0:
                    j = i
                    while j < n and source[j] not in "}\n":
                        j += 1
                    blank(i, j)
                    return j
                elif c == "!" and depth == 0 and source[i + 1:i + 2] == "r" \
                        and source[i + 2:i + 3] in ("}", ":"):
                    blank(i, i + 2)
                    i += 2
                    continue
            i += 1
        return i

    def string(i, is_f, raw):
        quote = source[i]
        triple = source.startswith(quote * 3, i)
        end = quote * 3 if triple else quote
        i += len(end)
        start = i
        while i < n:
            if source.startswith(end, i):
                blank(start, i)
                return i + len(end)
            c = source[i]
            if c == "\n" and not triple:
                blank(start, i)
                return i
            if c == "\\" and not raw:
                i += 2
                continue
            if is_f and c == "{":
                if source[i + 1:i + 2] == "{":
                    i += 2
                    continue
                blank(start, i + 1)
                i = code(i + 1, True) + 1
                start = i
                continue
            i += 1
        blank(start, n)
        return n

    code(0, False)
    return "".join(chars).split("\n")


# ---------------------------------------------------------------------------
# Built-ins and libraries (loaded once per process)
# ---------------------------------------------------------------------------

_BUILTINS: dict | None = None
_NATIVE: dict = {}
_FILES: dict = {}


def _interp():
    from .interp import Interpreter
    return Interpreter(output=lambda text: None, input_fn=lambda prompt: "")


_SHARED = {}


def shared_interp():
    if "it" not in _SHARED:
        _SHARED["it"] = _interp()
    return _SHARED["it"]


def value_type(value, label=""):
    from .runtime import (
        DONE, Builtin, EnumType, Function, Instance, Module, Partial, Range, TBClass, TBIterator,
    )
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, Builtin):
        lo, hi = value.arity
        return FnT(FnSig(value.name, lo=lo, hi=hi, returns=doc_returns(value.doc), native=True))
    if isinstance(value, Module):
        return ModT(native_modinfo(value))
    if isinstance(value, TBClass):
        return "class"
    if isinstance(value, EnumType):
        return EnumT(EnumInfo(value.name, list(value.members)))
    if isinstance(value, Range):
        return "range"
    if isinstance(value, TBIterator):
        return "generator"
    if value is DONE:
        return "done"
    if isinstance(value, Instance):
        return None
    if isinstance(value, (Function, Partial)) or callable(value):
        return "function"
    return None


def native_modinfo(module) -> ModInfo:
    info = ModInfo(module.name, f"the {module.name} library", std=True)
    for name, value in module.fields.items():
        if module.is_public(name):
            info.members[name] = value_type(value)
        else:
            info.members[name] = None
            info.private.add(name)
    return info


def builtins() -> dict:
    global _BUILTINS
    if _BUILTINS is None:
        from .interp import BUILTIN_DOCS
        table = {}
        for name, value in shared_interp().builtins.vars.items():
            t = value_type(value)
            if isinstance(t, FnT):
                t.sig.returns = doc_returns(BUILTIN_DOCS.get(name, "") or getattr(value, "doc", ""))
            table[name] = t
        _BUILTINS = table
    return _BUILTINS


def library_names() -> list[str]:
    try:
        from .interp import list_libraries
        return list_libraries()
    except Exception:  # pragma: no cover
        return []


def native_library(name: str):
    """ModInfo for a standard library written in Python, or None."""
    if name in _NATIVE:
        return _NATIVE[name]
    from .interp import Interpreter
    info = None
    if not name.startswith("_") and name.isidentifier() and Interpreter.native_spec(name) is not None:
        try:
            info = native_modinfo(shared_interp().load_library(name))
        except Exception:
            info = ModInfo(name, f"the {name} library")
            info.opaque = True
    _NATIVE[name] = info
    return info


def file_modinfo(path: str, name: str, std: bool, label: str, loading=()):
    """ModInfo for a .tb file, read without running it."""
    from .parser import parse
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, stamp, name)
    if key in _FILES:
        return _FILES[key]
    info = ModInfo(name, label, std=std, path=path)
    if path in loading:
        info.opaque = True
        return info
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
        program = parse(source, os.path.dirname(path))
    except (OSError, UnicodeDecodeError, TBError, RecursionError):
        info.opaque = True
        _FILES[key] = info
        return info
    if getattr(program, "uses_export", False):
        info.exports = set()
    dot_assigned = {t.name for node in walk_all(program.body) for t in assign_targets(node)
                    if isinstance(t, A.Dot)}
    classes: dict = {}
    for st in program.body:
        decl = st.decl if isinstance(st, A.Export) else st
        if isinstance(decl, A.ClassDecl):
            ci = build_class_info(decl, path)
            ci.extra_names = dot_assigned
            classes[decl.name] = ci
    for ci in classes.values():
        base = ci.node.base
        if base is None:
            continue
        if isinstance(base, A.Name) and base.name in classes:
            ci.base = classes[base.name]
            classes[base.name].children.append(ci)
        else:
            ci.base_unknown = True
    folder = os.path.dirname(path)
    for st in program.body:
        exported = isinstance(st, A.Export)
        decl = st.decl if exported else st
        names: list = []
        if isinstance(decl, A.Let):
            for n in pattern_names(decl.target):
                t = None
                if decl.name is not None and decl.annotation is None and isinstance(decl.value, A.Literal):
                    t = literal_type(decl.value.value)
                info.members[n] = t
                names.append(n)
        elif isinstance(decl, A.FnDecl):
            info.members[decl.name] = FnT(fn_sig(decl, decl.name))
            names.append(decl.name)
        elif isinstance(decl, A.ClassDecl):
            info.members[decl.name] = ClassT(classes[decl.name])
            names.append(decl.name)
        elif isinstance(decl, A.EnumDecl):
            info.members[decl.name] = EnumT(EnumInfo(decl.name, decl.members))
            names.append(decl.name)
        elif isinstance(decl, A.Import):
            sub = resolve_import(decl.target, decl.is_file, folder, getattr(decl, "package_name", None),
                                 tuple(loading) + (path,))
            info.members[decl.alias] = ModT(sub) if isinstance(sub, ModInfo) else None
        elif isinstance(decl, A.FromImport):
            for _n, alias in decl.names:
                info.members[alias] = None
        if exported and info.exports is not None:
            info.exports.update(names)
    for n in info.members:
        if n.startswith("_") or (info.exports is not None and n not in info.exports):
            info.private.add(n)
    _FILES[key] = info
    return info


def resolve_import(target, is_file, base_dir, package_name=None, loading=()):
    """ModInfo, or an error message (str) when the import can't work."""
    from .interp import _tb_library_path, find_package, package_entry
    base = base_dir or os.getcwd()
    if is_file:
        path = os.path.abspath(os.path.join(base, target))
        if package_name and not os.path.isfile(path):
            folder = find_package(package_name, base)
            entry = package_entry(folder) if folder else None
            if entry:
                return file_modinfo(entry, package_name.replace("-", "_"), False, package_name, loading) \
                    or ModInfo(package_name, package_name)
        if not os.path.isfile(path):
            return f"I couldn't find the file '{target}' to import (I looked in {os.path.dirname(path)})."
        return file_modinfo(path, os.path.splitext(os.path.basename(path))[0], False,
                            os.path.basename(path), loading) or ModInfo(target, target)
    info = native_library(target)
    if info is not None:
        return info
    if not target.startswith("_"):
        path = _tb_library_path(target)
        if path:
            return file_modinfo(path, target, True, f"the {target} library", loading) or ModInfo(target, target)
        folder = find_package(target, base)
        if folder is not None:
            entry = package_entry(folder)
            if entry:
                return file_modinfo(entry, target, False, target, loading) or ModInfo(target, target)
            info = ModInfo(target, target)
            info.opaque = True
            return info
    libraries = library_names()
    hint = difflib.get_close_matches(target, libraries, n=1)
    extra = f" Did you mean '{hint[0]}'?" if hint else ""
    if os.path.isfile(os.path.join(base, target + ".tb")):
        extra = f' To use your own file, put its name in quotes: import "{target}.tb"'
    return f"There's no library called '{target}'.{extra}"


def literal_type(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, bytes):
        return "bytes"
    return None


def fn_sig(decl, label) -> FnSig:
    return FnSig(label, params=list(decl.params), decl=decl,
                 is_generator=getattr(decl, "is_generator", False),
                 is_async=getattr(decl, "is_async", False))


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


class Symbol:
    __slots__ = ("name", "kind", "line", "type", "used", "scope", "annotation", "warn", "node",
                 "exported")

    def __init__(self, name, kind, line, t=None, scope=None, annotation=None, warn=False, node=None):
        self.name, self.kind, self.line, self.type = name, kind, line, t
        self.used = False
        self.scope = scope
        self.annotation = annotation   # parsed annotation (list) or None
        self.warn = warn               # report if never used
        self.node = node
        self.exported = False


class FuncCtx:
    def __init__(self, parent=None, cls=None, static=False, returns=None, label=None):
        self.parent = parent
        self.cls = cls          # ClassInfo when this is a method
        self.static = static
        self.returns = returns  # annotation text
        self.label = label
        self.pending: list = []

    def method_ctx(self):
        ctx = self
        while ctx is not None:
            if ctx.cls is not None:
                return ctx
            ctx = ctx.parent
        return None


class Scope:
    def __init__(self, parent, func, stmts=None, kind="block"):
        self.parent = parent
        self.func = func
        self.kind = kind
        self.symbols: dict = {}
        self.later = declared_in_block(stmts or [])


# ---------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------

TERMINATORS = (A.Return, A.Throw, A.Break, A.Continue)
IGNORE_MARK = "tb-check: ignore"


class Checker:
    def __init__(self, source: str, path: str | None):
        self.source = source
        self.path = path
        self.base_dir = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
        self.diags: list[Diagnostic] = []
        self.lines = mask_source(source)
        self.used_cols: dict = {}
        self.all_symbols: list[Symbol] = []
        self.class_infos: dict = {}       # id(ClassDecl) -> ClassInfo
        self.top_types: dict = {}         # top-level class/enum name -> ClassInfo/EnumInfo
        self.reassigned: set = set()
        self.dot_assigned: set = set()

    # -- reporting ------------------------------------------------------------

    def col_of(self, line, word=None, attr=False):
        text = self.lines[line - 1] if 1 <= line <= len(self.lines) else ""
        first = len(text) - len(text.lstrip()) + 1
        if not word:
            return first
        if attr:
            pattern = r"\.\s*(" + re.escape(word) + r")(?!\w)"
        else:
            pattern = r"(?<!\w)(" + re.escape(word) + r")(?!\w)"
        cands = []
        for m in re.finditer(pattern, text):
            s = m.start(1)
            if not attr and s > 0 and text[s - 1] == "." and text[max(0, s - 3):s] != "...":
                continue
            cands.append(s + 1)
        if not cands:
            return first
        used = self.used_cols.setdefault((line, word, attr), set())
        for c in cands:
            if c not in used:
                used.add(c)
                return c
        return cands[0]

    def col_of_op(self, line, op):
        text = self.lines[line - 1] if 1 <= line <= len(self.lines) else ""
        if op.isalpha() or " " in op:
            pattern = r"(?<!\w)" + re.escape(op).replace("\\ ", r"\s+") + r"(?!\w)"
        else:
            pattern = r"(?<![=<>!*/+\-%&|^.])" + re.escape(op) + r"(?![=<>*/.&|])"
        cands = [m.start() + 1 for m in re.finditer(pattern, text)]
        if not cands:
            return None
        used = self.used_cols.setdefault((line, op, "op"), set())
        for c in cands:
            if c not in used:
                used.add(c)
                return c
        return cands[0]

    def report(self, severity, code, message, line, word=None, attr=False, hint=None, col=None, op=None):
        line = line or 1
        if col is None and op is not None:
            col = self.col_of_op(line, op)
            if col is not None:
                diag = Diagnostic(line, col, severity, code, message, line, col + len(op), hint, self.path)
                if not any(d.line == line and d.col == col and d.message == message for d in self.diags):
                    self.diags.append(diag)
                return
        if col is None:
            col = self.col_of(line, word, attr)
        end_col = col + len(word) if word else None
        diag = Diagnostic(line, col, severity, code, message, line if word else None, end_col, hint,
                          self.path)
        if any(d.line == diag.line and d.col == diag.col and d.message == diag.message for d in self.diags):
            return
        self.diags.append(diag)

    def error(self, code, message, line, word=None, attr=False, hint=None, op=None):
        self.report("error", code, message, line, word, attr, hint, op=op)

    def warn(self, code, message, line, word=None, attr=False, hint=None):
        self.report("warning", code, message, line, word, attr, hint)

    # -- entry ------------------------------------------------------------------

    def run(self) -> list[Diagnostic]:
        from .parser import parse
        try:
            program = parse(self.source, self.base_dir)
        except TBError as err:
            self.parse_error(err)
            return self.diags
        except RecursionError:
            self.error("parse-error", "This program is nested too deeply for me to read.", 1)
            return self.diags
        self.prepass(program)
        ctx = FuncCtx()
        module = Scope(None, ctx, program.body, kind="module")
        self.module_scope = module
        self.uses_export = getattr(program, "uses_export", False)
        self.block(program.body, module)
        self.flush(ctx)
        self.report_unused()
        self.drop_ignored()
        self.diags.sort(key=lambda d: (d.line, d.col))
        return self.diags

    def drop_ignored(self) -> None:
        """A line ending in `# tb-check: ignore` is never reported (for code
        that breaks on purpose, like a test that expects an error)."""
        raw = self.source.split("\n")
        quiet = {i + 1 for i, text in enumerate(raw) if IGNORE_MARK in text}
        if quiet:
            self.diags = [d for d in self.diags if d.line not in quiet]

    def parse_error(self, err: TBError) -> None:
        message = err.message
        code = "parse-error"
        if "'return' only works inside a function" in message:
            code = "return-outside-fn"
        elif "only works inside a loop" in message:
            code = "break-outside-loop"
        elif "'yield' only works" in message or "'defer' only works" in message:
            code = "outside-fn"
        self.error(code, message, err.line or 1)

    def prepass(self, program) -> None:
        for node in walk_all(program.body):
            for target in assign_targets(node):
                if isinstance(target, A.Name):
                    self.reassigned.add(target.name)
                elif isinstance(target, A.Dot):
                    self.dot_assigned.add(target.name)
            if isinstance(node, A.ClassDecl):
                info = build_class_info(node, self.path)
                info.extra_names = self.dot_assigned
                self.class_infos[id(node)] = info
        for st in program.body:
            decl = st.decl if isinstance(st, A.Export) else st
            if isinstance(decl, A.ClassDecl):
                self.top_types[decl.name] = self.class_infos[id(decl)]
            elif isinstance(decl, A.EnumDecl):
                self.top_types[decl.name] = EnumInfo(decl.name, decl.members)

    def flush(self, ctx: FuncCtx) -> None:
        while ctx.pending:
            job = ctx.pending.pop(0)
            job()

    def report_unused(self) -> None:
        for sym in self.all_symbols:
            if sym.used or not sym.warn or sym.name.startswith("_") or sym.exported:
                continue
            if sym.kind == "import":
                self.warn("unused-import", f"'{sym.name}' is imported but never used.", sym.line, sym.name,
                          hint="Remove the import, or use it.")
            else:
                what = {"fn": "The function", "class": "The class", "enum": "The enum"}.get(sym.kind, "The variable")
                self.warn("unused-variable", f"{what} '{sym.name}' is never used.", sym.line, sym.name,
                          hint=f"Remove it, or name it '_{sym.name}' if that's on purpose.")

    # -- scopes -----------------------------------------------------------------

    def declare(self, scope: Scope, name, kind, line, t=None, annotation=None, node=None, warn=None):
        if warn is None:
            if kind == "import":
                warn = True
            elif kind in ("var", "const", "fn", "class", "enum"):
                warn = scope.kind != "module"
            else:
                warn = False
        sym = Symbol(name, kind, line, t, scope, annotation, warn, node)
        scope.symbols[name] = sym
        self.all_symbols.append(sym)
        return sym

    def lookup(self, name, scope):
        s = scope
        while s is not None:
            sym = s.symbols.get(name)
            if sym is not None:
                return sym
            s = s.parent
        return None

    def declared_later(self, name, scope):
        s = scope
        while s is not None and s.func is scope.func:
            if name in s.later:
                return True
            s = s.parent
        return False

    def visible_names(self, scope) -> list[str]:
        names = set(builtins())
        s = scope
        while s is not None:
            names.update(s.symbols)
            names.update(s.later)
            s = s.parent
        return sorted(names)

    def unknown_name(self, name, scope, line, as_key=False, assigning=False):
        if self.declared_later(name, scope):
            self.error("used-before-defined",
                       f"'{name}' is used before the line that makes it. Move the 'let' or 'fn' for '{name}' above this line.",
                       line, name)
            return
        if as_key:
            self.error("undefined-name",
                       f"I don't know what '{name}' is. Map keys that are words need quotes: {{\"{name}\": ...}}",
                       line, name)
            return
        if assigning:
            self.error("undefined-name", f"'{name}' doesn't exist yet. Use 'let {name} = ...' first.", line, name)
            return
        from .parser import OTHER_LANGUAGE_NAMES
        if name in OTHER_LANGUAGE_NAMES:
            hint = [OTHER_LANGUAGE_NAMES[name]]
        else:
            hint = difflib.get_close_matches(name, self.visible_names(scope), n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        if not hint and name in library_names():
            extra = f" Did you forget 'import {name}' at the top?"
        self.error("undefined-name", f"I don't know what '{name}' is.{extra}", line, name)

    def read_name(self, name, scope, line, as_key=False):
        """Type of a name being read (reports unknown names)."""
        if name == "self":
            return self.self_type(scope, line)
        sym = self.lookup(name, scope)
        if sym is not None:
            sym.used = True
            return sym.type
        table = builtins()
        if name in table:
            return table[name]
        self.unknown_name(name, scope, line, as_key=as_key)
        return None

    def self_type(self, scope, line):
        ctx = scope.func.method_ctx()
        if ctx is None:
            self.error("self-outside-class", "'self' only works inside a class's methods.", line, "self")
            return None
        if ctx.static:
            return ClassT(ctx.cls)
        return InstT(ctx.cls, is_self=True)

    # -- annotations ------------------------------------------------------------

    def parse_annotation(self, text, scope):
        """A list of accepted kinds, or None for 'any'."""
        if not text:
            return None
        parts = split_top(text, "|")
        out = []
        for part in parts:
            part = part.strip()
            while part.startswith("(") and part.endswith(")"):
                part = part[1:-1].strip()
            if "|" in part:
                sub = self.parse_annotation(part, scope)
                if sub is None:
                    return None
                out.extend(sub)
                continue
            if part.startswith("fn(") or part.startswith("fn ("):
                out.append("function")
                continue
            base = part.split("<", 1)[0].strip()
            if base in ("any", ""):
                return None
            if base in ("int", "float"):
                base = "number"
            if base in PRIMITIVES:
                out.append(base)
                continue
            target = None
            if "." not in base:
                sym = self.lookup(base, scope)
                if sym is not None:
                    target = sym.type
                elif base in self.top_types:
                    info = self.top_types[base]
                    target = ClassT(info) if isinstance(info, ClassInfo) else EnumT(info)
                elif isinstance(builtins().get(base), str) and builtins()[base] == "class":
                    out.append(("err", base))
                    continue
            else:
                head, _, member = base.partition(".")
                sym = self.lookup(head, scope)
                if sym is not None and isinstance(sym.type, ModT):
                    target = sym.type.info.members.get(member)
            if isinstance(target, ClassT):
                out.append(("inst", target.info))
            elif isinstance(target, EnumT):
                out.append(("enum", target.info))
            else:
                return None
        return out or None

    def compatible(self, t, ann) -> bool:
        if t is None or ann is None:
            return True
        k = kind_of(t)
        for a in ann:
            if isinstance(a, str):
                if a == k or (a == "function" and k == "class"):
                    return True
            elif a[0] == "inst":
                if k == "instance" and (t.info is None or not t.info.complete() or t.info.is_sub(a[1])):
                    return True
            elif a[0] == "enum":
                if k == "enumvalue" and (t.info is a[1] or t.info.name == a[1].name):
                    return True
            elif a[0] == "err":
                if k == "instance":
                    return True
        return False

    def ann_text(self, ann) -> str:
        names = []
        for a in ann:
            if isinstance(a, str):
                names.append("true/false" if a == "bool" else a)
            else:
                names.append(a[1].name if a[0] != "err" else a[1])
        return " or ".join(names)

    def check_type(self, t, ann, line, what, word=None, attr=False):
        if self.compatible(t, ann):
            return
        if kind_of(t) == "nil":
            # Often a placeholder filled in later; it runs fine, so only warn.
            self.warn("nil-annotation", f"{what} should be {self.ann_text(ann)}, but this is nil.",
                      line, word, attr, hint=f"Write '{self.ann_text(ann).replace(' or ', ' | ')} | nil' if it may be missing.")
            return
        self.error("type-mismatch", f"{what} should be {self.ann_text(ann)}, but this is {type_label(t)}.",
                   line, word, attr)

    # -- statements -------------------------------------------------------------

    def block(self, stmts, scope: Scope):
        dead_reported = False
        terminated = False
        for st in stmts:
            if terminated and not dead_reported:
                dead_reported = True
                self.warn("unreachable", "This code never runs: the line before it always leaves the block.",
                          st.line)
            self.stmt(st, scope)
            if isinstance(st, TERMINATORS):
                terminated = True

    def stmt(self, node, scope: Scope):
        method = getattr(self, "s_" + type(node).__name__, None)
        if method is None:
            self.generic(node, scope)
            return
        method(node, scope)

    def generic(self, node, scope):
        for child in children(node):
            if isinstance(child, A.Node) and not isinstance(child, (A.Pattern, A.MatchPattern)):
                self.expr(child, scope)

    def s_ExprStmt(self, node, scope):
        self.expr(node.expr, scope)

    def stable(self, name, scope) -> bool:
        return name not in self.reassigned and scope.later.get(name, 0) <= 1

    def s_Let(self, node, scope):
        t = self.expr(node.value, scope)
        ann = self.parse_annotation(node.annotation, scope) if node.annotation else None
        if ann is not None:
            self.check_type(t, ann, node.line, f"'{node.name}'", node.name)
        kind = "const" if node.is_const else "var"
        if node.name is not None:
            sym_t = None
            if ann is not None:
                sym_t = ann_to_type(ann)
            elif self.stable(node.name, scope):
                sym_t = t
            self.declare(scope, node.name, kind, node.line, sym_t, ann, node)
        else:
            self.bind_pattern(node.target, scope, kind, node.line, warn=False)

    def bind_pattern(self, pattern, scope, kind, line, warn=False):
        if isinstance(pattern, str):
            self.declare(scope, pattern, kind, line, warn=warn)
            return
        if isinstance(pattern, A.NamePattern):
            ann = self.parse_annotation(pattern.annotation, scope) if pattern.annotation else None
            self.declare(scope, pattern.name, kind, pattern.line or line, ann_to_type(ann) if ann else None,
                         ann, warn=warn)
        elif isinstance(pattern, A.ListPattern):
            for item in pattern.items:
                self.bind_pattern(item, scope, kind, line, warn)
            if pattern.rest:
                self.declare(scope, pattern.rest, kind, line, "list", warn=warn)
        elif isinstance(pattern, A.MapPattern):
            for key, sub, default in pattern.entries:
                if default is not None:
                    self.expr(default, scope)
                if sub is None:
                    self.declare(scope, key, kind, line, warn=warn)
                else:
                    self.bind_pattern(sub, scope, kind, line, warn)

    def s_Assign(self, node, scope):
        target = node.target
        t = self.expr(node.value, scope)
        if isinstance(target, A.Name):
            self.store_name(target, scope, node.line, t, compound=node.op is not None)
            if node.op is not None:
                sym = self.lookup(target.name, scope)
                cur = sym.type if sym else None
                self.binary_type(node.op, cur, t, node.line)
        elif isinstance(target, A.Dot):
            obj = self.expr(target.target, scope)
            self.store_field(obj, target, t, node.line, node.op)
        elif isinstance(target, A.Index):
            container = self.expr(target.target, scope)
            self.expr(target.index, scope)
            k = kind_of(container)
            if k == "text":
                self.error("type-mismatch", "Text can't be changed one letter at a time.", node.line)
            elif k == "bytes":
                self.error("type-mismatch", "Bytes can't be changed in place; build new bytes instead (see the binary library).",
                           node.line)
            elif k in ("number", "bool", "nil", "function", "range", "generator"):
                self.error("type-mismatch", f"Only lists and maps can be changed with [ ], not {type_label(container)}.",
                           node.line)
        else:
            self.expr(target, scope)

    def store_name(self, target, scope, line, t, compound=False):
        name = target.name
        if name == "self":
            return
        sym = self.lookup(name, scope)
        if sym is None:
            if name in builtins():
                return
            self.unknown_name(name, scope, target.line or line, assigning=True)
            return
        if compound:
            sym.used = True
        if sym.kind == "const":
            self.error("const-assign", f"'{name}' is a constant (made with const), so it can't be changed.",
                       target.line or line, name)
            return
        if sym.annotation is not None and not compound:
            self.check_type(t, sym.annotation, line, f"'{name}'", name)

    def store_field(self, obj, target, t, line, op):
        k = kind_of(obj)
        name = target.name
        if k == "module":
            self.error("type-mismatch", f"You can't change {obj.info.label}'s '{name}'.", target.line, name, True)
        elif k == "enum":
            self.error("type-mismatch", f"You can't change the enum {obj.info.name}.", target.line, name, True)
        elif k in ("text", "list", "number", "bool", "nil", "bytes", "range", "generator", "function"):
            self.error("type-mismatch", f"Only maps have fields you can set, not {short_label(obj)}.",
                       target.line, name, True)
        elif k == "instance" and obj.info is not None and op is None:
            ann_text = obj.info.field_annotation(name)
            if ann_text:
                ann = self.parse_annotation(ann_text, self.module_scope)
                if ann is not None:
                    self.check_type(t, ann, target.line, f"The field '{name}'", name, True)

    def s_MultiAssign(self, node, scope):
        self.expr(node.value, scope)
        for target in node.targets:
            if isinstance(target, A.Name):
                self.store_name(target, scope, node.line, None)
            elif isinstance(target, A.Dot):
                self.store_field(self.expr(target.target, scope), target, None, node.line, None)
            elif isinstance(target, A.Index):
                self.expr(target.target, scope)
                self.expr(target.index, scope)

    def s_FnDecl(self, node, scope):
        t = FnT(fn_sig(node, node.name)) if self.stable(node.name, scope) else None
        self.declare(scope, node.name, "fn", node.line, t, node=node)
        self.defer_function(node, scope, node.name)

    def defer_function(self, node, scope, label, cls=None, static=False):
        outer = scope.func
        ctx = FuncCtx(outer, cls, static, getattr(node, "returns", None), label)

        def job():
            fscope = Scope(scope, ctx, node.body, kind="function")
            for param in node.params:
                if param.default is not None:
                    self.expr(param.default, fscope)
                if param.kind == "rest":
                    self.declare(fscope, param.name, "param", param.line or node.line, "list")
                elif param.kind == "kwrest":
                    self.declare(fscope, param.name, "param", param.line or node.line, "map")
                elif isinstance(param.target, str):
                    ann = self.parse_annotation(param.annotation, fscope) if param.annotation else None
                    pt = ann_to_type(ann) if ann else None
                    if param.target in self.reassigned and ann is None:
                        pt = None
                    self.declare(fscope, param.target, "param", param.line or node.line, pt, ann)
                else:
                    self.bind_pattern(param.target, fscope, "param", param.line or node.line)
            self.block(node.body, fscope)
            self.flush(ctx)

        outer.pending.append(job)

    def s_Return(self, node, scope):
        t = "nil" if node.value is None else self.expr(node.value, scope)
        ctx = scope.func
        if ctx.returns:
            ann = self.parse_annotation(ctx.returns, scope)
            if ann is not None and not self.compatible(t, ann):
                self.error("type-mismatch",
                           f"{ctx.label or 'This function'}() should give back {self.ann_text(ann)}, but this is {type_label(t)}.",
                           node.line, "return")

    def s_If(self, node, scope):
        for cond, body in node.branches:
            self.expr(cond, scope)
            self.block(body, Scope(scope, scope.func, body))
        if node.otherwise is not None:
            self.block(node.otherwise, Scope(scope, scope.func, node.otherwise))

    def s_While(self, node, scope):
        self.expr(node.cond, scope)
        self.block(node.body, Scope(scope, scope.func, node.body))

    def s_For(self, node, scope):
        it = self.expr(node.iterable, scope)
        inner = Scope(scope, scope.func, node.body)
        elem = None
        k = kind_of(it)
        if k == "range" or (isinstance(node.iterable, A.Call) and isinstance(node.iterable.callee, A.Name)
                            and node.iterable.callee.name == "range" and self.lookup("range", scope) is None):
            elem = "number"
        elif k == "text":
            elem = "text"
        elif k == "bytes":
            elem = "number"
        elif k in ("number", "bool", "nil", "function") and len(node.targets) == 1:
            self.error("type-mismatch", f"'for ... in' can't loop over {type_label(it)}.", node.line)
        for target in node.targets:
            if isinstance(target, str):
                t = elem if (len(node.targets) == 1 and target not in self.reassigned) else None
                self.declare(inner, target, "loop", node.line, t)
            else:
                self.bind_pattern(target, inner, "loop", node.line)
        self.block(node.body, inner)

    def s_Break(self, node, scope):
        pass

    s_Continue = s_Break

    def s_Import(self, node, scope):
        result = resolve_import(node.target, node.is_file, node.base_dir or self.base_dir,
                                getattr(node, "package_name", None), (os.path.abspath(self.path),) if self.path else ())
        t = None
        if isinstance(result, str):
            self.error("unknown-library", result, node.line, None if node.is_file else node.target)
        elif result is not None:
            t = ModT(result)
        self.declare(scope, node.alias, "import", node.line, t, node=node)

    def s_FromImport(self, node, scope):
        result = resolve_import(node.target, node.is_file, node.base_dir or self.base_dir,
                                getattr(node, "package_name", None), (os.path.abspath(self.path),) if self.path else ())
        if isinstance(result, str):
            self.error("unknown-library", result, node.line, None if node.is_file else node.target)
            result = None
        for name, alias in node.names:
            t = None
            if result is not None:
                t = self.module_member(result, name, node.line, attr=False)
            self.declare(scope, alias, "import", node.line, t, node=node)

    def s_Try(self, node, scope):
        self.block(node.body, Scope(scope, scope.func, node.body))
        for catch in node.catches:
            if catch.types:
                for t in catch.types:
                    self.expr(t, scope)
            inner = Scope(scope, scope.func, catch.body)
            if catch.name is not None:
                self.declare(inner, catch.name, "catch", catch.line)
            self.block(catch.body, inner)
        if node.finally_body is not None:
            self.block(node.finally_body, Scope(scope, scope.func, node.finally_body))

    def s_Throw(self, node, scope):
        self.expr(node.value, scope)

    def s_ClassDecl(self, node, scope):
        info = self.class_infos.get(id(node)) or build_class_info(node, self.path)
        if node.base is not None:
            bt = self.expr(node.base, scope)
            if isinstance(bt, ClassT):
                info.base = bt.info
                if info not in bt.info.children:
                    bt.info.children.append(info)
            elif kind_of(bt) in ("number", "text", "bool", "nil", "list", "map", "function", "module",
                                 "enum", "instance"):
                self.error("type-mismatch", f"'{node.name}' can only extend a class, not {type_label(bt)}.",
                           node.line, getattr(node.base, "name", None))
                info.base_unknown = True
            else:
                info.base_unknown = True
        sym = self.declare(scope, node.name, "class", node.line,
                           ClassT(info) if self.stable(node.name, scope) else None, node=node)
        def check_fields(fields):
            for name, value, ann_text in fields:
                if value is not None:
                    t = self.expr(value, scope)
                    if ann_text:
                        ann = self.parse_annotation(ann_text, scope)
                        if ann is not None:
                            self.check_type(t, ann, value.line, f"The field '{name}'", name)

        check_fields(node.statics)
        # Instance field defaults are worked out each time an object is made.
        scope.func.pending.append(lambda: check_fields(node.fields))
        for decl in node.methods:
            self.defer_function(decl, scope, f"{node.name}.{decl.name}", cls=info)
        for decl in node.static_methods:
            self.defer_function(decl, scope, f"{node.name}.{decl.name}", cls=info, static=True)
        return sym

    def s_EnumDecl(self, node, scope):
        t = EnumT(EnumInfo(node.name, node.members)) if self.stable(node.name, scope) else None
        self.declare(scope, node.name, "enum", node.line, t, node=node)

    def s_Export(self, node, scope):
        self.stmt(node.decl, scope)
        name = getattr(node.decl, "name", None)
        if name and name in scope.symbols:
            scope.symbols[name].exported = True

    def s_Defer(self, node, scope):
        self.block(node.body, Scope(scope, scope.func, node.body))

    def s_Assert(self, node, scope):
        self.expr(node.cond, scope)
        if node.message is not None:
            self.expr(node.message, scope)

    # -- expressions ------------------------------------------------------------

    def expr(self, node, scope):
        if node is None:
            return None
        method = getattr(self, "e_" + type(node).__name__, None)
        if method is None:
            self.generic(node, scope)
            return None
        return method(node, scope)

    def e_Literal(self, node, scope):
        return literal_type(node.value)

    def e_Name(self, node, scope):
        return self.read_name(node.name, scope, node.line)

    def e_ListLit(self, node, scope):
        for item in node.items:
            self.expr(item, scope)
        return "list"

    def e_MapLit(self, node, scope):
        for pair in node.pairs:
            if isinstance(pair, A.Spread):
                t = self.expr(pair.value, scope)
                if kind_of(t) in ("number", "text", "bool", "nil", "list", "bytes"):
                    self.error("type-mismatch", f"'...' inside a map needs another map, not {short_label(t)}.",
                               pair.line)
                continue
            key, value = pair
            if isinstance(key, A.Name):
                self.read_name(key.name, scope, key.line, as_key=True)
            else:
                self.expr(key, scope)
            self.expr(value, scope)
        return "map"

    def e_Spread(self, node, scope):
        t = self.expr(node.value, scope)
        if kind_of(t) in ("number", "bool", "nil", "function"):
            self.error("type-mismatch", f"'...' needs a list (or something to loop over), not {type_label(t)}.",
                       node.line)
        return None

    def e_KeywordArg(self, node, scope):
        return self.expr(node.value, scope)

    def e_FString(self, node, scope):
        for part in node.parts:
            if isinstance(part, A.FHole):
                self.expr(part.expr, scope)
        return "text"

    def e_FHole(self, node, scope):
        self.expr(node.expr, scope)
        return "text"

    def e_Unary(self, node, scope):
        t = self.expr(node.operand, scope)
        k = kind_of(t)
        if node.op == "not":
            return "bool"
        if node.op == "-":
            if k is not None and k not in ("number", "instance"):
                self.error("type-mismatch", f"I can only make numbers negative, not {short_label(t)}.", node.line, op="-")
                return None
            return "number" if k == "number" else None
        if node.op == "~":
            if k is not None and k not in ("number",):
                self.error("type-mismatch", f"'~' (flip the bits) needs a whole number, not {short_label(t)}.", node.line, op="~")
            return "number"
        return None

    def e_Logical(self, node, scope):
        lt = self.expr(node.left, scope)
        rt = self.expr(node.right, scope)
        if isinstance(lt, str) and lt == rt:
            return lt
        return None

    def e_Binary(self, node, scope):
        lt = self.expr(node.left, scope)
        rt = self.expr(node.right, scope)
        return self.binary_type(node.op, lt, rt, node.line)

    def e_Compare(self, node, scope):
        types = [self.expr(op, scope) for op in node.operands]
        for i, op in enumerate(node.ops):
            self.binary_type(op, types[i], types[i + 1], node.line)
        return "bool"

    def binary_type(self, op, lt, rt, line):
        lk, rk = kind_of(lt), kind_of(rt)
        if op in ("==", "!="):
            return "bool"
        if op in ("in", "not in"):
            if rk in ("number", "bool", "nil", "function", "class", "module"):
                self.error("type-mismatch", f"'in' needs a list, text or a map on its right, not {short_label(rt)}.", line, op=op)
            elif rk == "text" and lk is not None and lk not in ("text", "instance"):
                self.error("type-mismatch",
                           f"To look inside text, the left side of 'in' must be text too, not {short_label(lt)}.", line, op=op)
            return "bool"
        if lk is None or rk is None or "instance" in (lk, rk):
            # unknown side (or an object with operator methods): anything goes
            return "bool" if op in ("<", "<=", ">", ">=") else None
        if op == "+":
            if lk == "number" and rk == "number":
                return "number"
            if lk == "text" or rk == "text":
                return "text"
            if lk == rk and lk in ("list", "bytes"):
                return lk
            self.error("type-mismatch", f"I can't add {short_label(lt)} and {short_label(rt)}.", line, op=op)
            return None
        if op in ("<", "<=", ">", ">="):
            if not (lk == rk and lk in ("number", "text", "bytes")):
                self.error("type-mismatch", f"I can't compare {short_label(lt)} with {short_label(rt)}.", line, op=op)
            return "bool"
        if op == "*" and (lk in ("text", "list") or rk in ("text", "list")):
            seq, times = (lk, rk) if lk in ("text", "list") else (rk, lk)
            if times != "number":
                what = "Text" if seq == "text" else "A list"
                self.error("type-mismatch", f"{what} can only be repeated a whole number of times, not {times}.", line, op=op)
                return None
            return seq
        if op in ("&", "|", "^", "<<", ">>"):
            if lk == "bool" and rk == "bool" and op in ("&", "|"):
                word, both = ("and", "both") if op == "&" else ("or", "either")
                self.error("type-mismatch", f"To say '{both}' with true/false values, TB uses the word '{word}'.", line, op=op)
            elif lk != "number" or rk != "number":
                self.error("type-mismatch", f"'{op}' needs whole numbers, not {short_label(lt)} and {short_label(rt)}.", line, op=op)
            return "number"
        if lk != "number" or rk != "number":
            verb = {"-": "subtract", "*": "multiply", "/": "divide", "//": "divide",
                    "%": "find the remainder of", "**": "raise to a power"}.get(op, "do that with")
            self.error("type-mismatch", f"I can only {verb} numbers, not {short_label(lt)} and {short_label(rt)}.", line, op=op)
            return None
        return "number"

    def e_IsCheck(self, node, scope):
        self.expr(node.value, scope)
        self.type_ref(node.type_node, scope)
        return "bool"

    def type_ref(self, type_node, scope):
        from .interp import BUILTIN_TYPE_CHECKS
        if isinstance(type_node, A.Name) and type_node.name in BUILTIN_TYPE_CHECKS:
            sym = self.lookup(type_node.name, scope)
            if sym is None:
                return None
            sym.used = True
            return sym.type
        return self.expr(type_node, scope)

    def e_Index(self, node, scope):
        t = self.expr(node.target, scope)
        it = self.expr(node.index, scope)
        k = kind_of(t)
        if k in ("number", "bool", "nil", "function", "class", "module", "enum"):
            self.error("type-mismatch", f"Only lists and text can be indexed, not {short_label(t)}.", node.line)
            return None
        if k in ("list", "text", "bytes") and kind_of(it) in ("text", "bool", "nil", "list", "map"):
            what = {"list": "a list", "text": "text", "bytes": "bytes"}[k]
            self.error("type-mismatch", f"A position in {what} must be a whole number, not {short_label(it)}.",
                       node.line)
        if k == "text":
            return "text"
        if k == "bytes":
            return "number"
        return None

    def e_SliceExpr(self, node, scope):
        t = self.expr(node.target, scope)
        for part in (node.start, node.stop, node.step):
            self.expr(part, scope)
        k = kind_of(t)
        return k if k in ("list", "text", "bytes") else None

    def e_RangeExpr(self, node, scope):
        for side in (node.start, node.stop):
            t = self.expr(side, scope)
            if kind_of(t) in ("text", "bool", "nil", "list", "map", "bytes"):
                self.error("type-mismatch", f"A range needs whole numbers on both sides (like 1..10), not {short_label(t)}.",
                           node.line)
        return "range"

    def e_Conditional(self, node, scope):
        self.expr(node.cond, scope)
        a = self.expr(node.then, scope)
        b = self.expr(node.otherwise, scope)
        return a if isinstance(a, str) and a == b else None

    def e_Yield(self, node, scope):
        self.expr(node.value, scope)
        return None

    def e_Await(self, node, scope):
        self.expr(node.value, scope)
        return None

    def e_FnExpr(self, node, scope):
        self.defer_function(node, scope, "anonymous")
        return FnT(fn_sig(node, "anonymous"))

    def e_Super(self, node, scope):
        ctx = scope.func.method_ctx()
        if ctx is None:
            self.error("self-outside-class", "'super' only works inside a class's methods.", node.line, "super")
            return None
        info = ctx.cls
        if info.node is not None and info.node.base is None:
            self.error("no-super", f"{info.name} doesn't extend another class, so there is no 'super'.",
                       node.line, "super")
            return None
        base = info.base
        if base is None or not base.complete():
            return None
        if ctx.static:
            decl, owner = base.find_static(node.name)
        else:
            decl, owner = base.find_method(node.name)
        if decl is None:
            self.error("unknown-member", f"{base.name} (the parent of {info.name}) has no method called '{node.name}'.",
                       node.line, node.name, True)
            return None
        return FnT(fn_sig(decl, f"{owner.name}.{decl.name}"))

    def e_Dot(self, node, scope):
        obj = self.expr(node.target, scope)
        return self.member(obj, node.name, node.line, node.target)

    def e_Match(self, node, scope):
        subject = self.expr(node.subject, scope)
        result = None
        first = True
        for arm in node.arms:
            inner = Scope(scope, scope.func, arm.body if arm.is_block else [])
            self.pattern(arm.pattern, inner)
            if arm.guard is not None:
                self.expr(arm.guard, inner)
            if arm.is_block:
                self.block(arm.body, inner)
                t = None
            else:
                t = self.expr(arm.body, inner)
            if first:
                result, first = t, False
            elif not (isinstance(t, str) and t == result):
                result = None
        self.check_exhaustive(node, subject, scope)
        return result

    def pattern(self, pattern, scope):
        if isinstance(pattern, A.PName):
            if pattern.name != "_":
                self.declare(scope, pattern.name, "match", pattern.line)
        elif isinstance(pattern, A.PValue):
            self.expr(pattern.expr, scope)
        elif isinstance(pattern, A.POr):
            for option in pattern.options:
                self.pattern(option, scope)
        elif isinstance(pattern, A.PList):
            for item in pattern.items:
                self.pattern(item, scope)
            if pattern.rest and pattern.rest != "_":
                self.declare(scope, pattern.rest, "match", pattern.line, "list")
        elif isinstance(pattern, A.PMap):
            for _key, sub in pattern.entries:
                self.pattern(sub, scope)
        elif isinstance(pattern, A.PIs):
            self.type_ref(pattern.type_node, scope)
        elif isinstance(pattern, A.PClass):
            t = self.expr(pattern.type_node, scope)
            if kind_of(t) in ("number", "text", "bool", "nil", "list", "map", "function", "module"):
                self.error("type-mismatch", f"A pattern like Name(...) needs a class, not {short_label(t)}.",
                           pattern.line)
            if pattern.binding is not None:
                self.pattern(pattern.binding, scope)
            for _name, sub in pattern.fields or []:
                self.pattern(sub, scope)

    def check_exhaustive(self, node, subject, scope):
        enum = subject.info if isinstance(subject, EnumValT) else None
        covered = set()
        for arm in node.arms:
            options = arm.pattern.options if isinstance(arm.pattern, A.POr) else [arm.pattern]
            for opt in options:
                if isinstance(opt, (A.PName, A.PIs)) and arm.guard is None:
                    return   # catch-all arm
                if isinstance(opt, A.PValue) and isinstance(opt.expr, A.Dot):
                    owner = self.quiet_type(opt.expr.target, scope)
                    if not isinstance(owner, EnumT):
                        return
                    if enum is None:
                        enum = owner.info
                    elif owner.info.name != enum.name:
                        return
                    if arm.guard is None:
                        covered.add(opt.expr.name)
                elif isinstance(opt, A.PClass) and arm.guard is None and opt.binding is None and not opt.fields:
                    owner = self.quiet_type(opt.type_node, scope)
                    if isinstance(owner, EnumT) and (enum is None or owner.info.name == enum.name):
                        return
                    return
                else:
                    return
        if enum is None:
            return
        missing = [m for m in enum.members if m not in covered]
        if missing:
            names = ", ".join(f"{enum.name}.{m}" for m in missing)
            self.warn("non-exhaustive-match",
                      f"This match over {enum.name} doesn't handle {names}, and has no '_ =>' arm.",
                      node.line, "match", hint="Add the missing members, or a '_ => ...' arm for everything else.")

    def quiet_type(self, node, scope):
        if isinstance(node, A.Name):
            sym = self.lookup(node.name, scope)
            return sym.type if sym is not None else None
        if isinstance(node, A.Dot):
            owner = self.quiet_type(node.target, scope)
            if isinstance(owner, ModT):
                return owner.info.members.get(node.name)
        return None

    # -- members ----------------------------------------------------------------

    def module_member(self, info: ModInfo, name, line, attr=True):
        if info.opaque:
            return None
        if name in info.members and name not in info.private:
            return info.members[name]
        public = info.public()
        if name in info.members:
            if name.startswith("_"):
                self.error("private-access",
                           f"'{name}' is private to {info.label} (names starting with '_' aren't shared).",
                           line, name, attr)
                return None
            if info.exports is not None:
                self.error("not-exported",
                           f"{info.label} doesn't export '{name}'. Add 'export' in front of it, or use one of: "
                           f"{', '.join(public) or 'nothing is exported yet'}.", line, name, attr)
                return None
        hint = difflib.get_close_matches(name, public, n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        label = info.label[0].upper() + info.label[1:] if info.std else info.label
        self.error("unknown-member", f"{label} has nothing called '{name}'.{extra}", line, name, attr)
        return None

    def member(self, obj, name, line, target_node=None):
        k = kind_of(obj)
        if k is None or k == "map":
            return None
        if k == "module":
            return self.module_member(obj.info, name, line)
        if k in METHOD_KINDS:
            if isinstance(obj, FnT) or k == "function":
                if name == "name":
                    return "text"
                if name == "arity":
                    return "number"
            from .runtime import METHOD_HINTS, METHOD_KIND_LABEL, METHOD_KIND_PLURAL, METHODS
            table = METHODS.get(k, {})
            if name in table:
                _fn, lo, hi, doc = table[name]
                return FnT(FnSig(name, lo=lo, hi=hi, returns=doc_returns(doc), native=True))
            names = sorted(table)
            if name in METHOD_HINTS and METHOD_HINTS[name] in table:
                hint = [METHOD_HINTS[name]]
            else:
                hint = difflib.get_close_matches(name, names, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            self.error("unknown-method",
                       f"{METHOD_KIND_LABEL[k]} doesn't have a method called '{name}'.{extra}",
                       line, name, True, hint=f"{METHOD_KIND_PLURAL[k]} methods: {', '.join(names)}.")
            return None
        if k == "bool":
            self.error("unknown-method", f"I can't use '.{name}' on true/false.", line, name, True)
            return None
        if k == "nil":
            self.error("unknown-method", f"I can't use '.{name}' on nil. (Is the value missing, or not made yet?)",
                       line, name, True)
            return None
        if k == "done":
            self.error("unknown-method", "'done' is the end-of-generator marker; it has no fields.", line, name, True)
            return None
        if k == "enum" and isinstance(obj, EnumT):
            info = obj.info
            if name in info.members:
                return EnumValT(info)
            if name == "values":
                return FnT(FnSig("values", lo=0, hi=0, returns="list", native=True))
            if name == "name":
                return "text"
            hint = difflib.get_close_matches(name, info.members, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            self.error("unknown-member",
                       f"The enum {info.name} has no member called '{name}'.{extra} Its members: {', '.join(info.members)}.",
                       line, name, True)
            return None
        if k == "enumvalue":
            if name in ("name",):
                return "text"
            if name == "index":
                return "number"
            if name == "enum":
                return EnumT(obj.info)
            self.error("unknown-member", f"{obj.info.name} values only have '.name' and '.index', not '.{name}'.",
                       line, name, True)
            return None
        if k == "class" and isinstance(obj, ClassT):
            info = obj.info
            if not info.complete():
                return None
            decl, owner = info.find_static(name)
            if decl is not None:
                return FnT(fn_sig(decl, f"{owner.name}.{decl.name}"))
            if name in info.static_names() or name in self.dot_assigned:
                if name == "name":
                    return "text"
                return None
            decl, _owner = info.find_method(name)
            if decl is not None:
                self.error("unknown-member",
                           f"'{name}' is a method of {info.name} objects, not of the class itself. "
                           f"Call it on an object, like {info.name}(...).{name}(), or declare it 'static fn {name}'.",
                           line, name, True)
                return None
            names = sorted(info.static_names() - {"name", "base"} - self.dot_assigned)
            hint = difflib.get_close_matches(name, names, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            self.error("unknown-member", f"The class {info.name} has nothing called '{name}'.{extra}",
                       line, name, True)
            return None
        if k == "instance" and isinstance(obj, InstT) and obj.info is not None:
            info = obj.info
            if name.startswith("_") and info.file != self.path and not obj.is_self:
                self.warn("private-access",
                          f"'{name}' is private to the {info.name} class (it comes from another file).",
                          line, name, True)
            if not info.complete():
                return None
            decl, owner = info.find_method(name)
            if decl is not None:
                if obj.is_self and any(name in c.methods for c in info.descendants()):
                    return None  # a subclass may override it with other inputs
                return FnT(fn_sig(decl, f"{owner.name}.{decl.name}"))
            if name in info.instance_names(with_children=obj.is_self) or name in self.dot_assigned:
                ann_text = info.field_annotation(name)
                if ann_text:
                    ann = self.parse_annotation(ann_text, self.module_scope)
                    return ann_to_type(ann) if ann else None
                return None
            fields = sorted(info.instance_names() - {"fields", "class"})
            hint = difflib.get_close_matches(name, fields, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            if obj.is_self:
                self.warn("unknown-member",
                          f"{info.name} has no field or method called '{name}'.{extra}",
                          line, name, True,
                          hint=f"If a subclass defines it, add a placeholder 'fn {name}() {{ }}' to {info.name}.")
            else:
                self.error("unknown-member", f"This {info.name} has no field or method called '{name}'.{extra}",
                           line, name, True)
            return None
        return None

    # -- calls ------------------------------------------------------------------

    def e_Call(self, node, scope):
        callee = node.callee
        if isinstance(callee, A.Dot):
            obj = self.expr(callee.target, scope)
            ct = self.member(obj, callee.name, callee.line, callee.target)
            word, attr = callee.name, True
        else:
            ct = self.expr(callee, scope)
            word = callee.name if isinstance(callee, A.Name) else None
            attr = False
        arg_types = []
        for arg in node.args:
            arg_types.append(self.expr(arg, scope))
        k = kind_of(ct)
        line = node.line
        if isinstance(ct, FnT):
            self.check_args(ct.sig, node, arg_types, word, attr)
            sig = ct.sig
            if sig.is_generator:
                return "generator"
            if sig.is_async:
                return None
            if sig.native:
                return sig.returns
            if sig.decl is not None and sig.decl.returns:
                ann = self.parse_annotation(sig.decl.returns, scope)
                return ann_to_type(ann) if ann else None
            return None
        if isinstance(ct, ClassT):
            info = ct.info
            if info.complete():
                decl, owner = info.find_method("init")
                if decl is None:
                    if node.args:
                        self.error("wrong-arity",
                                   f"{info.name} has no init method, so {info.name}() takes no inputs (got {len(node.args)}).",
                                   line, word, attr, hint="Add one: fn init(...) { self.field = ... }")
                else:
                    self.check_args(fn_sig(decl, f"{owner.name}.init"), node, arg_types, word, attr)
            return InstT(info)
        if isinstance(ct, InstT) and ct.info is not None and ct.info.complete():
            decl, owner = ct.info.find_method("call")
            if decl is None:
                self.error("not-callable",
                           f"I can only call functions, not a {ct.info.name} object. "
                           "(Give the class a call(...) method to make its objects callable.)", line, word, attr)
            else:
                self.check_args(fn_sig(decl, f"{owner.name}.call"), node, arg_types, word, attr)
            return None
        if k in ("number", "text", "bool", "nil", "list", "map", "bytes", "range", "generator", "module",
                 "enum", "enumvalue", "done"):
            self.error("not-callable", f"I can only call functions, not {type_label(ct)}.", line, word, attr)
            return None
        return None

    def check_args(self, sig: FnSig, node, arg_types, word, attr):
        line = node.line
        has_spread = any(isinstance(a, A.Spread) for a in node.args)
        pos = [(a, t) for a, t in zip(node.args, arg_types) if not isinstance(a, (A.Spread, A.KeywordArg))]
        kws = [(a, t) for a, t in zip(node.args, arg_types) if isinstance(a, A.KeywordArg)]
        from .runtime import arity_message
        if sig.native:
            if kws:
                self.error("unknown-keyword",
                           f"{sig.label}() doesn't take named inputs like {kws[0][0].name} = ...; give its inputs in order.",
                           line, word, attr)
                return
            if not has_spread and not sig.lo <= len(pos) <= sig.hi:
                self.error("wrong-arity", arity_message(sig.label, sig.lo, sig.hi, len(pos)), line, word, attr)
            return
        params = sig.params or []
        positional = [p for p in params if p.kind == "pos"]
        rest = any(p.kind == "rest" for p in params)
        kwrest = any(p.kind == "kwrest" for p in params)
        names = [p.name for p in positional]
        required = sum(1 for p in positional if p.default is None)
        n = len(pos)
        given = set()
        for a, t in kws:
            if a.name in names[:n] and not has_spread:
                self.error("wrong-arity", f"{sig.label}() got '{a.name}' twice: once in order and once by name.",
                           a.line, a.name)
                return
            if a.name not in names and not kwrest:
                hint = difflib.get_close_matches(a.name, [x for x in names if x], n=1)
                extra = f" Did you mean '{hint[0]}'?" if hint else ""
                listing = f" Its inputs are: {', '.join(x for x in names if x)}." if any(names) else " It has no named inputs."
                self.error("unknown-keyword", f"{sig.label}() has no input called '{a.name}'.{extra}{listing}",
                           a.line, a.name)
                return
            given.add(a.name)
        if not has_spread:
            if n > len(positional) and not rest:
                self.error("wrong-arity", arity_message(sig.label, required, len(positional), n), line, word, attr)
                return
            for p in positional[n:]:
                if p.default is None and p.name not in given:
                    self.error("wrong-arity", arity_message(sig.label, required, MANY if rest else len(positional), n),
                               line, word, attr)
                    return
        # annotated inputs
        fscope = self.module_scope
        for i, (a, t) in enumerate(pos):
            if has_spread:
                break
            if i < len(positional) and positional[i].annotation:
                ann = self.parse_annotation(positional[i].annotation, fscope)
                if ann is not None and not self.compatible(t, ann):
                    self.error("type-mismatch",
                               f"{sig.label}()'s input '{positional[i].name}' should be {self.ann_text(ann)}, "
                               f"but this is {type_label(t)}.", a.line or line, word, attr)
        for a, t in kws:
            p = next((p for p in positional if p.name == a.name), None)
            if p is not None and p.annotation:
                ann = self.parse_annotation(p.annotation, fscope)
                if ann is not None and not self.compatible(t, ann):
                    self.error("type-mismatch",
                               f"{sig.label}()'s input '{p.name}' should be {self.ann_text(ann)}, but this is {type_label(t)}.",
                               a.line or line, a.name)


def split_top(text: str, sep: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def ann_to_type(ann):
    """The type a variable has when its annotation is this (one kind only)."""
    if not ann or len(ann) != 1:
        return None
    a = ann[0]
    if isinstance(a, str):
        return a if a not in ("class", "enum", "module", "function") else None
    if a[0] == "inst":
        return InstT(a[1])
    if a[0] == "enum":
        return EnumValT(a[1])
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_source(source: str, path: str | None = None) -> list[Diagnostic]:
    """Check TB source text; path (if given) is used for relative imports
    and is put on every Diagnostic. Never raises for a bad program."""
    try:
        return Checker(source, path).run()
    except RecursionError:
        return [Diagnostic(1, 1, "error", "parse-error", "This program is nested too deeply for me to check.",
                           file=path)]


def check_file(path: str) -> list[Diagnostic]:
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError) as err:
        return [Diagnostic(1, 1, "error", "unreadable", f"I couldn't read this file: {err}", file=path)]
    return check_source(source, path)
