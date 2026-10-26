"""The interpreter: walks the syntax tree and runs the program."""

from __future__ import annotations

import difflib
import importlib
import importlib.util
import math
import os
import random
import re
import sys

from . import ast as A
from .errors import (
    MANY, MAX_DEPTH, MAX_RANGE, TOO_BIG, UNSET, BreakSignal, ContinueSignal, ReturnSignal,
    TBError, TBExit, check_size,
)
from .lexer import tokenize
from .parser import TYPE_NAMES, OTHER_LANGUAGE_NAMES, parse, parse_expression
from .runtime import (
    CARE_MESSAGES, DONE, ERROR_CLASS, ERROR_CLASSES, METHOD_HINTS, METHOD_KIND_LABEL,
    METHOD_KIND_PLURAL, METHODS, Bound, Builtin, EnumType, EnumValue, Env, Function, Instance,
    Module, NativeMethod, Partial, Range, TBClass, TBIterator, ThreadGenerator, _GenAbort,
    _extreme, _sum, arity_message, error_class, error_from_value, error_value, format_value,
    is_callable, is_number, is_whole, map_key, method_kind, missing_key, pow_guard, sort_values,
    tb_equal, tb_round, tb_type, text_to_number, tidy_number, to_text, truthy, type_name,
)

# ---------------------------------------------------------------------------
# Libraries (the standard library lives in tblib/ next to tb.py)
# ---------------------------------------------------------------------------


def _home_dir() -> str | None:
    """The folder that holds tb.py, tbcore/ and tblib/."""
    here = globals().get("__file__")
    if here:
        return os.path.dirname(os.path.dirname(os.path.abspath(here)))
    return None


def library_dirs() -> list[str]:
    """Folders that may hold the standard library (tblib)."""
    dirs = []
    bundle = getattr(sys, "_MEIPASS", None)  # inside the PyInstaller one-file app
    if bundle:
        dirs.append(os.path.join(bundle, "tblib"))
    home = _home_dir()
    if home:
        dirs.append(os.path.join(home, "tblib"))
    else:  # run from text (some browser setups): look next to us
        dirs.append(os.path.join(os.getcwd(), "tblib"))
    seen: list[str] = []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.append(d)
    return seen


def _dirs() -> list[str]:
    """library_dirs(), looked up through the tb module so tests can add folders."""
    finder = getattr(sys.modules.get("tb"), "library_dirs", None) or library_dirs
    return finder()


def _tblib_package():
    """Import (once) and return the tblib package that belongs to this TB.
    A different package called 'tblib' (there is one on PyPI) is ignored."""
    pkg = sys.modules.get("tblib")
    if pkg is not None and getattr(pkg, "TB_STDLIB", False):
        return pkg
    for folder in _dirs():
        init = os.path.join(folder, "__init__.py")
        if os.path.isfile(init):
            spec = importlib.util.spec_from_file_location(
                "tblib", init, submodule_search_locations=[folder]
            )
            pkg = importlib.util.module_from_spec(spec)
            sys.modules["tblib"] = pkg
            spec.loader.exec_module(pkg)
            return pkg
    return importlib.import_module("tblib")  # bundled copy (e.g. PyInstaller)


def _native_spec(name: str):
    try:
        _tblib_package()
        return importlib.util.find_spec(f"tblib.{name}")
    except Exception:
        return None


def _tb_library_path(name: str) -> str | None:
    for folder in _dirs():
        path = os.path.join(folder, name + ".tb")
        if os.path.isfile(path):
            return path
    return None


def list_libraries() -> list[str]:
    """Names of every standard library (what `import name` accepts)."""
    names: set[str] = set()
    for folder in _dirs():
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for entry in entries:
            stem, ext = os.path.splitext(entry)
            if ext in (".py", ".tb") and not stem.startswith("_") and stem.isidentifier():
                names.add(stem)
    try:
        import pkgutil

        for info in pkgutil.iter_modules(_tblib_package().__path__):
            if not info.name.startswith("_"):
                names.add(info.name)
    except Exception:
        pass
    return sorted(names)


FN_LINE = re.compile(r"(?:export\s+)?(?:async\s+)?fn\s+([^\W\d]\w*)\s*\(([^)]*)\)")
CLASS_LINE = re.compile(r"(?:export\s+)?class\s+([^\W\d]\w*)")


def tb_source_docs(source: str) -> tuple[str, dict]:
    """Docs for a library written in TB: the comment lines at the top of the
    file describe the library, and the comment lines right above a top-level
    `fn name(...)` (or class) describe that function."""
    lines = source.splitlines()
    head: list[str] = []
    for text in lines:
        stripped = text.strip()
        if stripped.startswith("#"):
            head.append(stripped.lstrip("#").strip())
        elif stripped:
            break
        elif head:
            break
    docs: dict = {}
    for number, text in enumerate(lines):
        match = FN_LINE.match(text) or CLASS_LINE.match(text)
        if not match:
            continue
        comment: list[str] = []
        j = number - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            comment.insert(0, lines[j].strip().lstrip("#").strip())
            j -= 1
        if j < 0 and comment == head:
            comment = []  # that comment is the library's own description
        if match.re is CLASS_LINE:
            docs[match.group(1)] = "\n".join([f"class {match.group(1)}"] + comment)
            continue
        params = ", ".join(p.strip() for p in match.group(2).split(",") if p.strip())
        docs[match.group(1)] = "\n".join([f"{match.group(1)}({params})"] + comment)
    return "\n".join(head).strip(), docs


def read_source(path: str) -> str:
    """Read a .tb file. Accepts UTF-8 (with or without BOM), UTF-16 (Notepad's
    "Unicode") and falls back to the Windows Western code page."""
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def find_project_root(start: str) -> str | None:
    """Walk up from start to the nearest folder holding tb.toml."""
    folder = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(folder, "tb.toml")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def package_entry(folder: str) -> str | None:
    """The file that `import name` runs for the package in folder, or None."""
    name = os.path.basename(folder.rstrip("/\\"))
    for candidate in ("lib.tb", f"{name}.tb"):
        path = os.path.join(folder, candidate)
        if os.path.isfile(path):
            return path
    toml_path = os.path.join(folder, "tb.toml")
    if os.path.isfile(toml_path):
        main = toml_main(toml_path)
        if main:
            path = os.path.join(folder, main)
            if os.path.isfile(path):
                return path
    return None


def toml_main(path: str) -> str | None:
    """[package] main = "..." from a tb.toml (tomllib when available)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    try:
        import tomllib  # Python 3.11+

        table = tomllib.loads(data.decode("utf-8", "replace"))
        main = (table.get("package") or {}).get("main")
        return main if isinstance(main, str) else None
    except Exception:
        pass
    section = None
    for raw in data.decode("utf-8", "replace").splitlines():
        text = raw.split("#", 1)[0].strip()
        if text.startswith("[") and text.endswith("]"):
            section = text[1:-1].strip()
        elif section == "package" and "=" in text:
            key, _, value = text.partition("=")
            if key.strip() == "main":
                return value.strip().strip('"').strip("'")
    return None


def find_package(name: str, start: str) -> str | None:
    """tb_packages/<name>/ found by walking up from start to the project root."""
    folder = os.path.abspath(start)
    names = [name]
    if "_" in name:
        names.append(name.replace("_", "-"))   # import tb_colors finds tb_packages/tb-colors
    while True:
        for candidate_name in names:
            candidate = os.path.join(folder, "tb_packages", candidate_name)
            if os.path.isdir(candidate):
                return candidate
        if os.path.isfile(os.path.join(folder, "tb.toml")):
            return None
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


# a + b on objects calls a.add(b) (or b.radd(a)); see Interpreter.operator_method.
OPERATOR_METHODS = {"+": "add", "-": "sub", "*": "mul", "/": "div", "//": "floor_div",
                    "%": "mod", "**": "pow"}
NO_METHOD = object()


class Frame:
    """One running function (or the main program): for error traces and the debugger."""

    __slots__ = ("fn", "file", "line", "env", "defers")

    def __init__(self, fn, file, line, env):
        self.fn, self.file, self.line, self.env = fn, file, line, env
        self.defers = None


BUILTIN_TYPE_CHECKS = {
    "number": is_number,
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, float),
    "text": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "nil": lambda v: v is None,
    "list": lambda v: isinstance(v, list),
    "map": lambda v: isinstance(v, dict),
    "bytes": lambda v: isinstance(v, bytes),
    "function": is_callable,
    "class": lambda v: isinstance(v, TBClass),
    "module": lambda v: isinstance(v, Module),
    "range": lambda v: isinstance(v, Range),
    "generator": lambda v: isinstance(v, TBIterator),
    "enum": lambda v: isinstance(v, (EnumType, EnumValue)),
    "any": lambda v: True,
}


class Interpreter:
    def __init__(self, output=None, input_fn=None, rng: random.Random | None = None,
                 *, base_dir: str | None = None, args: list | None = None):
        self.output = output or (lambda text: print(text, flush=True))
        self.input_fn = input_fn or input
        self.rng = rng or random.Random()
        self.builtins = Env()
        self.globals = Env(self.builtins)
        self.depth = 0
        self.base_dir = base_dir       # folder of the running script (None: current folder)
        self.script_path: str | None = None
        self.argv: list = list(args or [])  # what os.args gives
        self.current_file: str | None = None  # .tb file whose code is running
        self.modules: dict = {}        # import cache
        self.loading: list = []        # imports in progress (to spot circles)
        self.current_module: Module | None = None  # the .tb module being loaded (for export)
        self._api = None
        self.trace = None              # debugger hook: trace(event, line, file, env)
        self.root_frame = Frame(None, None, 0, self.globals)
        self.call_stack: list[Frame] = [self.root_frame]
        self.frame = self.root_frame
        self.active_gen = None         # the GenChannel of the generator body running now
        for name, fn, arity in self._builtins():
            self.builtins.vars[name] = Builtin(name, fn, arity, BUILTIN_DOCS.get(name, ""))
        self.builtins.vars["done"] = DONE
        for name, cls in ERROR_CLASSES.items():
            self.builtins.vars[name] = cls
        self._exec = {
            A.ExprStmt: self._exec_expr, A.Let: self._exec_let, A.Assign: self.assign,
            A.MultiAssign: self._exec_multi_assign, A.FnDecl: self._exec_fn,
            A.Return: self._exec_return, A.If: self._exec_if, A.While: self._exec_while,
            A.For: self._exec_for, A.Break: self._exec_break, A.Continue: self._exec_continue,
            A.Import: self._exec_import, A.FromImport: self._exec_from_import,
            A.Try: self._exec_try, A.Throw: self._exec_throw, A.ClassDecl: self._exec_class,
            A.EnumDecl: self._exec_enum, A.Export: self._exec_export, A.Defer: self._exec_defer,
            A.Assert: self._exec_assert,
        }
        self._eval = {
            A.Literal: self._eval_literal, A.Name: self._eval_name, A.ListLit: self._eval_list,
            A.Unary: self._eval_unary, A.Logical: self._eval_logical, A.Binary: self._eval_binary,
            A.Compare: self._eval_compare, A.IsCheck: self._eval_is, A.Call: self._eval_call,
            A.Index: self._eval_index, A.SliceExpr: self._eval_slice, A.Dot: self._eval_dot,
            A.Super: self._eval_super, A.MapLit: self._eval_map, A.FString: self._eval_fstring,
            A.FnExpr: self._eval_fnexpr, A.RangeExpr: self._eval_range,
            A.Conditional: self._eval_conditional, A.Match: self._eval_match,
            A.Yield: self._eval_yield, A.Await: self._eval_await,
        }

    # -- built-in functions -----------------------------------------------

    def _builtins(self):
        def say(line, *args):
            self.output(" ".join(to_text(a) for a in args))
            return None

        def length(line, value):
            if isinstance(value, (str, list, dict, bytes, Range)):
                return len(value)
            if isinstance(value, Instance):
                method, owner = value.cls.find_method("len")
                if method is not None:
                    return self.call(Bound(method, value, owner), [], line)
                return len(value.fields)
            raise TBError(f"len() needs text or a list, not {type_name(value)}.", line)

        def text(line, value):
            return to_text(value)

        def num(line, value):
            if is_number(value):
                return value
            if isinstance(value, str):
                return text_to_number(value, line)
            raise TBError(f"num() needs text or a number, not {type_name(value)}.", line)

        def rng(line, *args):
            for a in args:
                if not is_number(a) or (isinstance(a, float) and not a.is_integer()):
                    raise TBError("range() needs whole numbers.", line)
            ints = [int(a) for a in args]
            if len(ints) == 3 and ints[2] == 0:
                raise TBError("range() can't count in steps of 0.", line)
            numbers = range(*ints)
            try:
                count = len(numbers)
            except OverflowError:
                count = MAX_RANGE + 1
            if count > MAX_RANGE:
                raise TBError(
                    f"That range is too long. The most numbers range() can make is {MAX_RANGE:,}.",
                    line,
                )
            return list(numbers)

        def push(line, lst, value):
            if not isinstance(lst, list):
                raise TBError(f"push() needs a list first, not {type_name(lst)}.", line)
            lst.append(value)
            return lst

        def ask(line, prompt=""):
            try:
                answer = self.input_fn(to_text(prompt))
            except EOFError:
                return ""
            # Trim spaces and a stray Windows '\r', so " 2" counts as "2".
            return "" if answer is None else str(answer).strip()

        def care(line, name=None):
            message = self.rng.choice(CARE_MESSAGES)
            if name is not None:
                message = f"{to_text(name)}, {message[0].lower()}{message[1:]}"
            self.output("♥ " + message)
            return None

        def type_of(line, value):
            return tb_type(value)

        def keys(line, value):
            if isinstance(value, Instance):
                return list(value.fields.keys())
            if not isinstance(value, dict):
                raise TBError(f"keys() needs a map, not {type_name(value)}.", line)
            return list(value.keys())

        def whole(line, value):
            if isinstance(value, str):
                value = text_to_number(value, line)
            if not is_number(value):
                raise TBError(f"int() needs a number or text, not {type_name(value)}.", line)
            if isinstance(value, float) and not math.isfinite(value):
                raise TBError(TOO_BIG, line)
            return check_size(int(value), line)

        def to_float(line, value):
            if isinstance(value, str):
                value = text_to_number(value, line)
            if not is_number(value):
                raise TBError(f"float() needs a number or text, not {type_name(value)}.", line)
            return float(value)

        def fmt(line, value, spec):
            if not isinstance(spec, str):
                raise TBError(f"format() needs the spec to be text, not {type_name(spec)}.", line)
            return format_value(value, spec, line)

        def rounded(line, value, digits=0):
            return tb_round(value, digits, line)

        def pick(name, chooser):
            def fn(line, *args):
                values = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
                return _extreme(values, line, f"{name}()", chooser)
            return fn

        def total(line, values):
            if isinstance(values, (Range, TBIterator)):
                values = list(values)
            if not isinstance(values, list):
                raise TBError(f"sum() needs a list, not {type_name(values)}.", line)
            return _sum(values, line)

        def sort_list(line, values):
            if not isinstance(values, list):
                raise TBError(f"sorted() needs a list, not {type_name(values)}.", line)
            return sort_values(values, line, "sorted()", interp=self)

        def stop(line, code=0):
            if not is_whole(code):
                raise TBError("exit() needs a whole number, like exit(0) or exit(1).", line)
            raise TBExit(int(code))

        return [
            ("say", say, (0, MANY)),
            ("len", length, (1, 1)),
            ("str", text, (1, 1)),
            ("num", num, (1, 1)),
            ("range", rng, (1, 3)),
            ("push", push, (2, 2)),
            ("ask", ask, (0, 1)),
            ("care", care, (0, 1)),
            ("type", type_of, (1, 1)),
            ("keys", keys, (1, 1)),
            ("int", whole, (1, 1)),
            ("float", to_float, (1, 1)),
            ("format", fmt, (2, 2)),
            ("round", rounded, (1, 2)),
            ("min", pick("min", min), (1, MANY)),
            ("max", pick("max", max), (1, MANY)),
            ("sum", total, (1, 1)),
            ("sorted", sort_list, (1, 1)),
            ("exit", stop, (0, 1)),
        ]

    @property
    def api(self):
        """The helper object handed to native libraries (tblib._api.API)."""
        if self._api is None:
            _tblib_package()
            self._api = importlib.import_module("tblib._api").API(self)
        return self._api

    # -- debugger support ---------------------------------------------------

    def save_state(self) -> tuple:
        return (self.depth, self.current_file, self.call_stack, self.frame, self.active_gen)

    def restore_state(self, state: tuple) -> None:
        self.depth, self.current_file, self.call_stack, self.frame, self.active_gen = state

    def snapshot_frames(self) -> list[dict]:
        """The call stack for an error: innermost first, without the main program."""
        return [
            {"fn": f.fn, "line": f.line, "file": os.path.basename(f.file) if f.file else None}
            for f in reversed(self.call_stack) if f.fn is not None
        ]

    def frames(self) -> list[dict]:
        """For the debugger: every frame, outermost first: {fn, line, file, locals}.
        fn is "<main>" for the main program (or a module's top level); file is
        None for the main program unless it was run from a file."""
        result = []
        for f in self.call_stack:
            local_vars: dict = {}
            env = f.env
            while env is not None and env is not self.builtins:
                for k, v in env.vars.items():
                    local_vars.setdefault(k, v)
                if env is self.globals:
                    break
                env = env.parent
            result.append({
                "fn": f.fn or "<main>", "line": f.line,
                "file": f.file if f.file is not None or f is not self.root_frame else self.script_path,
                "locals": local_vars,
            })
        return result

    frames_info = frames  # older name

    def eval(self, source: str, env: Env | None = None):
        """Evaluate an expression (or run statements) in env; for the debugger and REPL."""
        env = env if env is not None else self.frame.env
        try:
            expr = parse_expression(source, 1, self.base_dir)
        except TBError:
            program = parse(source, self.base_dir)
            self.exec_block(program.body, env)
            return None
        return self.evaluate(expr, env)

    # -- running ----------------------------------------------------------

    def run(self, program: A.Program):
        try:
            self.exec_block(program.body, self.globals)
        except ReturnSignal:
            pass

    def exec_block(self, body, env: Env):
        for stmt in body:
            self.execute(stmt, env)

    def exec_block_value(self, body, env: Env):
        """Run a block and give back the value of its last expression (or nil)."""
        result = None
        for stmt in body:
            if type(stmt) is A.ExprStmt:
                self.frame.line = stmt.line
                if self.trace is not None:
                    self.trace("line", stmt.line, self.current_file or self.script_path, env)
                result = self.evaluate(stmt.expr, env)
            else:
                self.execute(stmt, env)
                result = None
        return result

    def execute(self, node: A.Node, env: Env):
        self.frame.line = node.line
        if self.trace is not None:
            self.trace("line", node.line, self.current_file or self.script_path, env)
        handler = self._exec.get(type(node))
        if handler is None:  # pragma: no cover - parser never produces anything else
            raise TBError("I don't know how to run this.", node.line)
        handler(node, env)

    def _exec_expr(self, node, env):
        self.evaluate(node.expr, env)

    def _exec_let(self, node, env):
        value = self.evaluate(node.value, env)
        if node.name is not None:
            env.declare(node.name, value, node.is_const)
        else:
            self.bind_pattern(node.target, value, env, node.line, node.is_const)

    def _exec_multi_assign(self, node, env):
        value = self.evaluate(node.value, env)
        values = self.unpack(value, len(node.targets), node.line, "put into")
        for target, item in zip(node.targets, values):
            self.store(target, item, env, node.line)

    def _exec_fn(self, node, env):
        fn = Function(node.name, node.params, node.body, env, self.current_file,
                      node.is_generator, node.is_async)
        fn.doc = "\n".join(c.lstrip("#").strip() for c in node.comments) if node.comments else ""
        env.declare(node.name, fn)

    def _exec_return(self, node, env):
        value = None if node.value is None else self.evaluate(node.value, env)
        raise ReturnSignal(value)

    def _exec_if(self, node, env):
        for cond, block in node.branches:
            if truthy(self.evaluate(cond, env)):
                self.exec_block(block, Env(env))
                return
        if node.otherwise is not None:
            self.exec_block(node.otherwise, Env(env))

    def _exec_while(self, node, env):
        while truthy(self.evaluate(node.cond, env)):
            try:
                self.exec_block(node.body, Env(env))
            except BreakSignal:
                break
            except ContinueSignal:
                continue

    def _exec_for(self, node, env):
        items = self.evaluate(node.iterable, env)
        targets = node.targets
        if len(targets) > 1 and isinstance(items, dict):
            iterator = iter([[k, v] for k, v in items.items()])
        else:
            iterator = self.iterate(items, node.line)
        name = node.name
        body = node.body
        for item in iterator:
            scope = Env(env)
            if name is not None:
                scope.vars[name] = item
            elif len(targets) == 1:
                self.bind_pattern(targets[0], item, scope, node.line)
            else:
                values = self.unpack(item, len(targets), node.line, "loop over")
                for target, value in zip(targets, values):
                    self.bind_pattern(target, value, scope, node.line)
            try:
                self.exec_block(body, scope)
            except BreakSignal:
                break
            except ContinueSignal:
                continue

    def _exec_break(self, node, env):
        raise BreakSignal()

    def _exec_continue(self, node, env):
        raise ContinueSignal()

    def _exec_import(self, node, env):
        env.declare(node.alias, self.import_node(node))

    def _exec_from_import(self, node, env):
        module = self.import_target(node.target, node.is_file, node.base_dir, node.line,
                                    getattr(node, "package_name", None))
        for name, alias in node.names:
            env.declare(alias, self.get_field(module, name, node.line))

    def _exec_try(self, node, env):
        try:
            self.exec_block(node.body, Env(env))
        except TBError as err:
            if err.stack is None:
                err.stack = self.snapshot_frames()
            for catch in node.catches:
                if catch.types is None or any(
                    self.error_matches(err, self.evaluate(t, env), t.line) for t in catch.types
                ):
                    scope = Env(env)
                    if catch.name is not None:
                        scope.vars[catch.name] = error_value(err)
                    self.exec_block(catch.body, scope)
                    break
            else:
                raise
        finally:
            if node.finally_body is not None:
                self.exec_block(node.finally_body, Env(env))

    def error_matches(self, err: TBError, cls, line) -> bool:
        if not isinstance(cls, TBClass) or not cls.is_error:
            raise TBError(
                f"'catch err: ...' needs an error class (like FileError), not {type_name(cls)}.", line
            )
        return error_class(err.cls).is_subclass_of(cls)

    def _exec_throw(self, node, env):
        raise self.make_throw(self.evaluate(node.value, env), node.line)

    def make_throw(self, value, line) -> TBError:
        if isinstance(value, Instance) and value.cls.is_error:
            return error_from_value(value, line)
        if isinstance(value, TBClass):
            if value.is_error:
                return TBError(
                    f"Call the class to make an error to throw: throw {value.name}(\"what went wrong\")", line
                )
            return TBError(f"I can only throw errors or text, not a class. Try: throw Error(\"message\")", line)
        if isinstance(value, dict):
            message = value.get("message", value)
            fields = {k: v for k, v in value.items() if k not in ("message", "line", "file")}
            where = value.get("line")
            return TBError(to_text(message), int(where) if is_whole(where) else line, fields)
        return TBError(to_text(value), line)

    def _exec_class(self, node, env):
        base = None
        if node.base is not None:
            base = self.evaluate(node.base, env)
            if not isinstance(base, TBClass):
                raise TBError(
                    f"'{node.name}' can only extend a class, not {type_name(base)}.", node.line
                )
        cls = TBClass(node.name, base, interp=self, file=self.current_file)
        cls.closure = env
        cls.field_defaults = [(name, value) for name, value, _ann in node.fields]
        cls.doc = "\n".join(c.lstrip("#").strip() for c in node.comments) if node.comments else ""
        for name, value, _ann in node.statics:
            cls.statics[name] = None if value is None else self.evaluate(value, env)
        for decl in node.methods:
            fn = Function(decl.name, decl.params, decl.body, env, self.current_file,
                          decl.is_generator, decl.is_async, owner=cls)
            fn.doc = "\n".join(c.lstrip("#").strip() for c in decl.comments) if decl.comments else ""
            cls.methods[decl.name] = fn
        for decl in node.static_methods:
            fn = Function(decl.name, decl.params, decl.body, env, self.current_file,
                          decl.is_generator, decl.is_async, owner=cls)
            fn.doc = "\n".join(c.lstrip("#").strip() for c in decl.comments) if decl.comments else ""
            cls.static_methods[decl.name] = fn
        env.declare(node.name, cls)

    def _exec_enum(self, node, env):
        env.declare(node.name, EnumType(node.name, node.members))

    def _exec_export(self, node, env):
        self.execute(node.decl, env)
        module = self.current_module
        if module is not None and module.exports is not None:
            decl = node.decl
            name = getattr(decl, "name", None)
            if name:
                module.exports.add(name)

    def _exec_defer(self, node, env):
        frame = self.frame
        if frame.defers is None:
            frame.defers = []
        frame.defers.append((node.body, env))

    def _exec_assert(self, node, env):
        if truthy(self.evaluate(node.cond, env)):
            return
        if node.message is not None:
            message = to_text(self.evaluate(node.message, env))
        else:
            message = "An assert check failed (the condition was not true)."
        raise TBError(message, node.line, cls=ERROR_CLASSES["AssertionError"])

    # -- storing values -------------------------------------------------------

    def assign(self, node: A.Assign, env: Env):
        target = node.target
        if type(target) is A.Name:
            owner = env.find(target.name)
            if owner is None:
                raise TBError(
                    f"'{target.name}' doesn't exist yet. Use 'let {target.name} = ...' first.",
                    node.line,
                )
            if owner.consts and target.name in owner.consts:
                raise TBError(
                    f"'{target.name}' is a constant (made with const), so it can't be changed.", node.line
                )
            value = self.evaluate(node.value, env)
            if node.op is not None:
                value = self.binary(node.op, owner.vars[target.name], value, node.line)
            owner.vars[target.name] = value
            return
        if type(target) is A.Dot:
            obj = self.evaluate(target.target, env)
            value = self.evaluate(node.value, env)
            self.set_field(obj, target.name, value, node.op, node.line)
            return
        container = self.evaluate(target.target, env)
        index = self.evaluate(target.index, env)
        value = self.evaluate(node.value, env)
        self.set_index(container, index, value, node.op, node.line, target.line)

    def store(self, target, value, env: Env, line) -> None:
        """Put value into a Name / Dot / Index target (no compound operator)."""
        if type(target) is A.Name:
            owner = env.find(target.name)
            if owner is None:
                raise TBError(
                    f"'{target.name}' doesn't exist yet. Use 'let {target.name} = ...' first.", line
                )
            if owner.consts and target.name in owner.consts:
                raise TBError(f"'{target.name}' is a constant (made with const), so it can't be changed.", line)
            owner.vars[target.name] = value
        elif type(target) is A.Dot:
            self.set_field(self.evaluate(target.target, env), target.name, value, None, line)
        else:
            container = self.evaluate(target.target, env)
            index = self.evaluate(target.index, env)
            self.set_index(container, index, value, None, line, target.line)

    def set_field(self, obj, name: str, value, op, line) -> None:
        if isinstance(obj, dict):
            if op is not None:
                if name not in obj:
                    raise missing_key(obj, name, line)
                value = self.binary(op, obj[name], value, line)
            obj[name] = value
            return
        if isinstance(obj, Instance):
            if op is not None:
                if name not in obj.fields:
                    raise self.missing_field(obj, name, line)
                value = self.binary(op, obj.fields[name], value, line)
            obj.fields[name] = value
            return
        if isinstance(obj, TBClass):
            owner = obj.find_static_owner(name)
            if op is not None:
                if owner is None:
                    raise TBError(
                        f"{obj.name} has no class field called '{name}'. "
                        f"Add it with: static let {name} = 0", line,
                    )
                value = self.binary(op, owner.statics[name], value, line)
            (owner or obj).statics[name] = value
            return
        if isinstance(obj, Module):
            raise TBError(f"You can't change {obj.label}'s '{name}'.", line)
        if isinstance(obj, EnumType):
            raise TBError(f"You can't change the enum {obj.name}.", line)
        raise TBError(f"Only maps have fields you can set, not {type_name(obj)}.", line)

    def set_index(self, container, index, value, op, line, index_line) -> None:
        if isinstance(container, dict):
            key = map_key(index, index_line)
            if op is not None:
                if key not in container:
                    raise missing_key(container, key, line)
                value = self.binary(op, container[key], value, line)
            container[key] = value
            return
        if isinstance(container, Instance):
            method, owner = container.cls.find_method("set")
            if method is None:
                raise TBError(
                    f"A {container.cls.name} can't be indexed with [ ]. Add a method: fn set(key, value) {{ ... }}",
                    line,
                )
            if op is not None:
                current = self.index_value(container, index, line)
                value = self.binary(op, current, value, line)
            self.call(Bound(method, container, owner), [index, value], line)
            return
        i = self.check_index(container, index, index_line)
        if isinstance(container, str):
            raise TBError("Text can't be changed one letter at a time.", line)
        if isinstance(container, bytes):
            raise TBError("Bytes can't be changed in place; build new bytes instead (see the binary library).", line)
        if op is not None:
            value = self.binary(op, container[i], value, line)
        container[i] = value

    def check_index(self, container, index, line) -> int:
        if not isinstance(container, (list, str, bytes)):
            raise TBError(f"Only lists and text can be indexed, not {type_name(container)}.", line)
        kind = "list" if isinstance(container, list) else "text" if isinstance(container, str) else "bytes"
        if not is_number(index) or (isinstance(index, float) and not index.is_integer()):
            what = {"list": "a list", "text": "text", "bytes": "bytes"}[kind]
            raise TBError(f"A position in {what} must be a whole number.", line)
        i = int(index)
        size = len(container)
        if i < -size or i >= size:
            if size == 0:
                raise TBError(
                    f"The {kind} is empty, so there is nothing at position {i}.", line
                )
            unit = {"list": "item", "text": "letter", "bytes": "byte"}[kind]
            raise TBError(
                f"Position {i} is outside the {kind}. It has {size} "
                f"{unit}{'' if size == 1 else 's'}, "
                f"numbered 0 to {size - 1}.",
                line,
            )
        return i

    # -- destructuring --------------------------------------------------------

    def unpack(self, value, count: int, line, verb: str) -> list:
        if isinstance(value, (Range, TBIterator)):
            value = list(value)
        if not isinstance(value, list):
            raise TBError(
                f"I need a list of {count} values to {verb} {count} names, but got {type_name(value)}.", line
            )
        if len(value) != count:
            raise TBError(
                f"I need exactly {count} values for {count} names, but the list has {len(value)}.", line
            )
        return value

    def bind_pattern(self, pattern, value, env: Env, line, const: bool = False) -> None:
        if isinstance(pattern, str):
            env.declare(pattern, value, const)
            return
        kind = type(pattern)
        if kind is A.NamePattern:
            env.declare(pattern.name, value, const)
        elif kind is A.ListPattern:
            if isinstance(value, (Range, TBIterator)):
                value = list(value)
            if not isinstance(value, list):
                raise TBError(
                    f"I can only unpack a list into [ ] names, not {type_name(value)}.", line
                )
            needed = len(pattern.items)
            if pattern.rest is None and len(value) != needed:
                raise TBError(
                    f"I can't unpack a list of {len(value)} item{'' if len(value) == 1 else 's'} "
                    f"into {needed} name{'' if needed == 1 else 's'}. Add '...rest' to collect the extra items.",
                    line,
                )
            if pattern.rest is not None and len(value) < needed:
                raise TBError(
                    f"I need at least {needed} items to unpack here, but the list has {len(value)}.", line
                )
            for sub, item in zip(pattern.items, value):
                self.bind_pattern(sub, item, env, line, const)
            if pattern.rest is not None:
                env.declare(pattern.rest, list(value[needed:]), const)
        elif kind is A.MapPattern:
            if isinstance(value, Instance):
                source = value.fields
            elif isinstance(value, dict):
                source = value
            else:
                raise TBError(
                    f"I can only unpack a map into {{ }} names, not {type_name(value)}.", line
                )
            for key, sub, default in pattern.entries:
                if key in source:
                    item = source[key]
                elif default is not None:
                    item = self.evaluate(default, env)
                else:
                    raise missing_key(source, key, line, " Give it a default: {" + f"{key} = ..." + "}")
                if sub is None:
                    env.declare(key, item, const)
                else:
                    self.bind_pattern(sub, item, env, line, const)
        else:  # pragma: no cover
            raise TBError("I don't know how to unpack this.", line)

    # -- evaluating -----------------------------------------------------------

    def unknown_name(self, name: str, env: Env, line) -> TBError:
        if name in OTHER_LANGUAGE_NAMES:
            hint = [OTHER_LANGUAGE_NAMES[name]]
        else:
            hint = difflib.get_close_matches(name, env.all_names(), n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        if not hint and name in list_libraries():
            extra = f" Did you forget 'import {name}' at the top?"
        if name == "self":
            extra = " 'self' only works inside a class's methods."
        return TBError(f"I don't know what '{name}' is.{extra}", line)

    def evaluate(self, node: A.Node, env: Env):
        handler = self._eval.get(type(node))
        if handler is None:  # pragma: no cover
            raise TBError("I don't know how to work this out.", node.line)
        return handler(node, env)

    def _eval_literal(self, node, env):
        return node.value

    def _eval_name(self, node, env):
        name = node.name
        while env is not None:
            if name in env.vars:
                return env.vars[name]
            env = env.parent
        raise self.unknown_name(node.name, self.frame.env, node.line)

    def _eval_list(self, node, env):
        result = []
        for item in node.items:
            if type(item) is A.Spread:
                result.extend(self.iterate(self.evaluate(item.value, env), item.line, "'...'"))
            else:
                result.append(self.evaluate(item, env))
        return result

    def _eval_unary(self, node, env):
        value = self.evaluate(node.operand, env)
        op = node.op
        if op == "-":
            if type(value) is Instance:
                method, owner = value.cls.find_method("negate")
                if method is not None:
                    return self.call(Bound(method, value, owner), [], node.line)
            if not is_number(value):
                raise TBError(f"I can only make numbers negative, not {type_name(value)}.", node.line,
                              cls=ERROR_CLASSES["TypeError"])
            return -value
        if op == "~":
            if not is_whole(value):
                raise TBError(f"'~' (flip the bits) needs a whole number, not {to_text(value, nested=True)}.", node.line)
            return ~int(value)
        return not truthy(value)

    def _eval_logical(self, node, env):
        left = self.evaluate(node.left, env)
        if node.op == "or":
            return left if truthy(left) else self.evaluate(node.right, env)
        return self.evaluate(node.right, env) if truthy(left) else left

    def _eval_binary(self, node, env):
        left = self.evaluate(node.left, env)
        right = self.evaluate(node.right, env)
        try:
            return self.binary(node.op, left, right, node.line)
        except OverflowError:
            raise TBError(TOO_BIG, node.line) from None

    def _eval_compare(self, node, env):
        left = self.evaluate(node.operands[0], env)
        for op, operand in zip(node.ops, node.operands[1:]):
            right = self.evaluate(operand, env)
            if not truthy(self.binary(op, left, right, node.line)):
                return False
            left = right
        return True

    def _eval_is(self, node, env):
        value = self.evaluate(node.value, env)
        result = self.is_type(value, node.type_node, env, node.line)
        return (not result) if node.negated else result

    def is_type(self, value, type_node, env: Env, line) -> bool:
        if type(type_node) is A.Name and type_node.name in BUILTIN_TYPE_CHECKS:
            owner = env.find(type_node.name)
            if owner is None or not isinstance(owner.vars[type_node.name], (TBClass, EnumType)):
                return BUILTIN_TYPE_CHECKS[type_node.name](value)
        target = self.evaluate(type_node, env)
        if isinstance(target, TBClass):
            if target.is_error and isinstance(value, Instance):
                return value.cls.is_subclass_of(target)
            return isinstance(value, Instance) and value.cls.is_subclass_of(target)
        if isinstance(target, EnumType):
            return isinstance(value, EnumValue) and value.enum is target
        raise TBError(
            f"'is' needs a class or a type name on its right (like number, text or Dog), not {type_name(target)}.",
            line,
        )

    def _eval_call(self, node, env):
        callee = self.evaluate(node.callee, env)
        args = []
        kwargs = None
        for arg in node.args:
            kind = type(arg)
            if kind is A.Spread:
                args.extend(self.iterate(self.evaluate(arg.value, env), arg.line, "'...'"))
            elif kind is A.KeywordArg:
                if kwargs is None:
                    kwargs = {}
                kwargs[arg.name] = self.evaluate(arg.value, env)
            else:
                args.append(self.evaluate(arg, env))
        return self.call(callee, args, node.line, kwargs)

    def _eval_index(self, node, env):
        container = self.evaluate(node.target, env)
        index = self.evaluate(node.index, env)
        return self.index_value(container, index, node.line)

    def index_value(self, container, index, line):
        if isinstance(container, dict):
            key = map_key(index, line)
            if key not in container:
                raise missing_key(container, key, line)
            return container[key]
        if isinstance(container, Instance):
            method, owner = container.cls.find_method("get")
            if method is not None:
                return self.call(Bound(method, container, owner), [index], line)
            if container.cls.is_error:
                key = map_key(index, line)
                if key not in container.fields:
                    raise missing_key(container.fields, key, line)
                return container.fields[key]
            raise TBError(
                f"A {container.cls.name} can't be indexed with [ ]. Add a method: fn get(key) {{ ... }}", line
            )
        if isinstance(container, Range):
            items = container.py_range()
            if not is_whole(index):
                raise TBError("A position in a range must be a whole number.", line)
            i = int(index)
            if i < -len(items) or i >= len(items):
                raise TBError(f"Position {i} is outside the range {to_text(container)}.", line)
            return items[i]
        return container[self.check_index(container, index, line)]

    def _eval_slice(self, node, env):
        container = self.evaluate(node.target, env)
        if isinstance(container, Range):
            container = list(container)
        if not isinstance(container, (list, str, bytes)):
            raise TBError(f"Only lists, text and bytes can be sliced with [a:b], not {type_name(container)}.", node.line)
        parts = []
        for part in (node.start, node.stop, node.step):
            if part is None:
                parts.append(None)
                continue
            value = self.evaluate(part, env)
            if value is None:
                parts.append(None)
            elif is_whole(value):
                parts.append(int(value))
            else:
                raise TBError(f"Slice positions must be whole numbers, not {to_text(value, nested=True)}.", node.line)
        if parts[2] == 0:
            raise TBError("A slice can't step by 0.", node.line)
        return container[parts[0]:parts[1]:parts[2]]

    def _eval_dot(self, node, env):
        return self.get_field(self.evaluate(node.target, env), node.name, node.line)

    def _eval_super(self, node, env):
        owner_env = env.find("%class")
        if owner_env is None:
            raise TBError("'super' only works inside a class's methods.", node.line)
        cls: TBClass = owner_env.vars["%class"]
        self_value = env.find("self").vars["self"]
        if cls.base is None:
            raise TBError(f"{cls.name} doesn't extend another class, so there is no 'super'.", node.line)
        if isinstance(self_value, TBClass):
            method, found = cls.base.find_static_method(node.name)
        else:
            method, found = cls.base.find_method(node.name)
        if method is None:
            raise TBError(
                f"{cls.base.name} (the parent of {cls.name}) has no method called '{node.name}'.", node.line
            )
        return Bound(method, self_value, found)

    def _eval_map(self, node, env):
        result = {}
        for pair in node.pairs:
            if type(pair) is A.Spread:
                extra = self.evaluate(pair.value, env)
                if isinstance(extra, Instance):
                    extra = extra.fields
                if not isinstance(extra, dict):
                    raise TBError(f"'...' inside a map needs another map, not {type_name(extra)}.", pair.line)
                result.update(extra)
                continue
            key_node, value_node = pair
            if type(key_node) is A.Name and env.find(key_node.name) is None:
                raise TBError(
                    f"I don't know what '{key_node.name}' is. Map keys that are words "
                    f'need quotes: {{"{key_node.name}": ...}}',
                    key_node.line,
                )
            key = map_key(self.evaluate(key_node, env), key_node.line)
            result[key] = self.evaluate(value_node, env)
        return result

    def _eval_fstring(self, node, env):
        out = []
        for part in node.parts:
            if isinstance(part, str):
                out.append(part)
                continue
            value = self.evaluate(part.expr, env)
            if part.conv == "r":
                text = to_text(value, nested=True)
                if part.spec:
                    text = format_value(text, part.spec, part.line)
            elif part.spec:
                text = format_value(value, part.spec, part.line)
            else:
                text = to_text(value)
            out.append(text)
        return "".join(out)

    def _eval_fnexpr(self, node, env):
        return Function("anonymous", node.params, node.body, env, self.current_file, node.is_generator)

    def _eval_range(self, node, env):
        start = self.evaluate(node.start, env)
        stop = self.evaluate(node.stop, env)
        for value in (start, stop):
            if not is_whole(value):
                raise TBError(
                    f"A range needs whole numbers on both sides (like 1..10), not {to_text(value, nested=True)}.",
                    node.line,
                )
        return Range(int(start), int(stop), node.inclusive)

    def _eval_conditional(self, node, env):
        if truthy(self.evaluate(node.cond, env)):
            return self.evaluate(node.then, env)
        return self.evaluate(node.otherwise, env)

    def _eval_yield(self, node, env):
        channel = self.active_gen
        if channel is None:
            raise TBError("'yield' only works inside a generator function.", node.line)
        value = None if node.value is None else self.evaluate(node.value, env)
        return channel.yield_(value)

    def _eval_await(self, node, env):
        value = self.evaluate(node.value, env)
        if isinstance(value, Instance):
            method, owner = value.cls.find_method("wait")
            if method is not None:
                return self.call(Bound(method, value, owner), [], node.line)
        wait = getattr(value, "wait", None)
        if callable(wait) and not isinstance(value, (dict, list, str, bytes)):
            try:
                return wait()
            except TBError:
                raise
            except Exception as err:
                raise TBError(f"Waiting failed: {err}", node.line) from None
        return value

    # -- match ---------------------------------------------------------------

    def _eval_match(self, node, env):
        subject = self.evaluate(node.subject, env)
        for arm in node.arms:
            scope = Env(env)
            if not self.match_pattern(arm.pattern, subject, scope):
                continue
            if arm.guard is not None and not truthy(self.evaluate(arm.guard, scope)):
                continue
            if arm.is_block:
                return self.exec_block_value(arm.body, scope)
            return self.evaluate(arm.body, scope)
        raise TBError(
            f"Nothing in this match fits {to_text(subject, nested=True)}. "
            "Add a '_ => ...' arm for everything else.",
            node.line,
        )

    def match_pattern(self, pattern, value, env: Env) -> bool:
        kind = type(pattern)
        if kind is A.PName:
            if pattern.name != "_":
                env.vars[pattern.name] = value
            return True
        if kind is A.PLiteral:
            return tb_equal(pattern.value, value)
        if kind is A.PValue:
            return tb_equal(self.evaluate(pattern.expr, env), value)
        if kind is A.POr:
            for option in pattern.options:
                if self.match_pattern(option, value, env):
                    return True
            return False
        if kind is A.PRange:
            if not is_number(value):
                return False
            lo, hi = pattern.start, pattern.stop
            return lo <= value <= hi if pattern.inclusive else lo <= value < hi
        if kind is A.PList:
            if isinstance(value, Range):
                value = list(value)
            if not isinstance(value, list):
                return False
            n = len(pattern.items)
            if pattern.rest is None:
                if len(value) != n:
                    return False
            elif len(value) < n:
                return False
            for sub, item in zip(pattern.items, value):
                if not self.match_pattern(sub, item, env):
                    return False
            if pattern.rest is not None and pattern.rest != "_":
                env.vars[pattern.rest] = list(value[n:])
            return True
        if kind is A.PMap:
            if isinstance(value, Instance):
                source = value.fields
            elif isinstance(value, dict):
                source = value
            else:
                return False
            for key, sub in pattern.entries:
                key = tidy_number(key) if is_number(key) else key
                if key not in source:
                    return False
                if not self.match_pattern(sub, source[key], env):
                    return False
            return True
        if kind is A.PIs:
            return self.is_type(value, pattern.type_node, env, pattern.line)
        if kind is A.PClass:
            target = self.evaluate(pattern.type_node, env)
            if isinstance(target, EnumType):
                if not (isinstance(value, EnumValue) and value.enum is target):
                    return False
                return pattern.binding is None or self.match_pattern(pattern.binding, value, env)
            if not isinstance(target, TBClass):
                raise TBError(
                    f"A pattern like {to_text(target, nested=True)}(...) needs a class, not {type_name(target)}.",
                    pattern.line,
                )
            if not (isinstance(value, Instance) and value.cls.is_subclass_of(target)):
                return False
            if pattern.binding is not None:
                return self.match_pattern(pattern.binding, value, env)
            if pattern.fields:
                for name, sub in pattern.fields:
                    if name not in value.fields:
                        return False
                    if not self.match_pattern(sub, value.fields[name], env):
                        return False
            return True
        raise TBError("I don't understand this pattern.", pattern.line)  # pragma: no cover

    # -- fields and methods -------------------------------------------------

    def missing_field(self, inst: Instance, name: str, line) -> TBError:
        names = list(inst.fields) + inst.cls.all_method_names()
        hint = difflib.get_close_matches(name, names, n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        fields = ", ".join(inst.fields) or "none yet"
        return TBError(
            f"This {inst.cls.name} has no field or method called '{name}'.{extra} Its fields: {fields}.", line
        )

    def get_field(self, obj, name: str, line):
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            if name in METHODS["map"]:
                return self.bound_method("map", obj, name)
            hint = difflib.get_close_matches(name, [k for k in obj if isinstance(k, str)], n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            raise missing_key(obj, name, line, extra)
        if isinstance(obj, Instance):
            fields = obj.fields
            if name in fields:
                return fields[name]
            method, owner = obj.cls.find_method(name)
            if method is not None:
                return Bound(method, obj, owner)
            if name == "fields":
                return Builtin("fields", lambda line: dict(obj.fields), (0, 0),
                               "fields() -> map\nA copy of the object's fields as a map.")
            if name == "class":
                return obj.cls
            if obj.cls.is_error and name in METHODS["map"]:
                return self.bound_method("map", fields, name)
            raise self.missing_field(obj, name, line)
        if isinstance(obj, Module):
            if name in obj.fields and obj.is_public(name):
                return obj.fields[name]
            names = obj.public_names()
            hint = difflib.get_close_matches(name, names, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            listing = f" It has: {', '.join(sorted(names))}." if names else ""
            label = obj.label[0].upper() + obj.label[1:] if obj.std else obj.label
            if obj.exports is not None and name in obj.fields and not name.startswith("_"):
                raise TBError(
                    f"{label} doesn't export '{name}'. Add 'export' in front of it, or use one of: "
                    f"{', '.join(sorted(names)) or 'nothing is exported yet'}.", line,
                )
            raise TBError(f"{label} has nothing called '{name}'.{extra}{listing}", line)
        if isinstance(obj, TBClass):
            owner = obj.find_static_owner(name)
            if owner is not None:
                return owner.statics[name]
            method, found = obj.find_static_method(name)
            if method is not None:
                return Bound(method, obj, found)
            if name == "name":
                return obj.name
            if name == "base":
                return obj.base
            method, found = obj.find_method(name)
            if method is not None:
                raise TBError(
                    f"'{name}' is a method of {obj.name} objects, not of the class itself. "
                    f"Call it on an object, like {obj.name}(...).{name}(), or declare it 'static fn {name}'.",
                    line,
                )
            names = obj.all_static_names()
            hint = difflib.get_close_matches(name, names, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            listing = f" Its class fields: {', '.join(names)}." if names else ""
            raise TBError(f"The class {obj.name} has nothing called '{name}'.{extra}{listing}", line)
        if isinstance(obj, EnumType):
            if name in obj.members:
                return obj.members[name]
            if name == "values":
                return Builtin("values", lambda line: obj.values(), (0, 0), "values() -> list\nEvery member, in order.")
            if name == "name":
                return obj.name
            hint = difflib.get_close_matches(name, list(obj.members), n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            raise TBError(
                f"The enum {obj.name} has no member called '{name}'.{extra} Its members: {', '.join(obj.members)}.",
                line,
            )
        if isinstance(obj, EnumValue):
            if name == "name":
                return obj.name
            if name == "index":
                return obj.index
            if name == "enum":
                return obj.enum
            raise TBError(f"{to_text(obj)} only has '.name' and '.index', not '.{name}'.", line)
        if isinstance(obj, (Function, Builtin, Bound, Partial)):
            if name == "name":
                return obj.name
            if name == "arity":
                return self.arity_of(obj)
        kind = method_kind(obj)
        if kind is None:
            if obj is None:
                raise TBError(
                    f"I can't use '.{name}' on nil. (Is the value missing, or not made yet?)", line
                )
            if obj is DONE:
                raise TBError("'done' is the end-of-generator marker; it has no fields.", line)
            raise TBError(f"I can't use '.{name}' on {type_name(obj)}.", line)
        if name in METHODS[kind]:
            return self.bound_method(kind, obj, name)
        names = sorted(METHODS[kind])
        if name in METHOD_HINTS and METHOD_HINTS[name] in METHODS[kind]:
            hint = [METHOD_HINTS[name]]
        else:
            hint = difflib.get_close_matches(name, names, n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        label = METHOD_KIND_LABEL[kind]
        plural = METHOD_KIND_PLURAL[kind]
        raise TBError(
            f"{label} doesn't have a method called '{name}'.{extra} "
            f"{plural} methods: {', '.join(names)}.",
            line,
        )

    def arity_of(self, fn) -> int:
        if isinstance(fn, Function):
            return fn.arity
        if isinstance(fn, Bound):
            inner = fn.fn
            return inner.arity if isinstance(inner, Function) else inner.arity[0]
        if isinstance(fn, Partial):
            return max(0, self.arity_of(fn.fn) - len(fn.args))
        if isinstance(fn, Builtin):
            return fn.arity[0]
        return 0

    def bound_method(self, kind: str, obj, name: str) -> Builtin:
        fn, lo, hi, doc = METHODS[kind][name]
        return Builtin(name, lambda line, *args: fn(self, line, obj, *args), (lo, hi), doc)

    # -- iteration -----------------------------------------------------------

    def iterate(self, value, line, what: str = "'for ... in'"):
        """A Python iterator over a TB value, for `for`, spreads and friends."""
        if isinstance(value, list):
            return iter(list(value))
        if isinstance(value, str):
            return iter(value)
        if isinstance(value, dict):
            return iter(list(value.keys()))
        if isinstance(value, (Range, bytes, TBIterator)):
            return iter(value)
        if isinstance(value, Instance):
            method, owner = value.cls.find_method("iter")
            if method is not None:
                result = self.call(Bound(method, value, owner), [], line)
                return self.iterator_from(result, line, value.cls.name)
            method, owner = value.cls.find_method("next")
            if method is not None:
                return self.iterator_from(value, line, value.cls.name)
            raise TBError(
                f"{what} can't loop over a {value.cls.name}. Give the class an iter() method that "
                "gives back a generator, or a next() method that gives back `done` at the end.", line,
            )
        raise TBError(f"{what} needs a list or text, not {type_name(value)}.", line)

    def iterator_from(self, result, line, owner_name: str):
        if isinstance(result, (TBIterator, Range, list, str, bytes, dict)):
            return self.iterate(result, line)
        if isinstance(result, Instance):
            method, owner = result.cls.find_method("next")
            if method is not None:
                bound = Bound(method, result, owner)

                def pull():
                    while True:
                        item = self.call(bound, [], line)
                        if item is DONE:
                            return
                        yield item
                return pull()
        raise TBError(
            f"{owner_name}.iter() must give back a generator or a list (or an object with next()), "
            f"not {type_name(result)}.", line,
        )

    def check_range_size(self, r: Range, line) -> None:
        if len(r) > MAX_RANGE:
            raise TBError(f"That range is too long. The most numbers it can make is {MAX_RANGE:,}.", line)

    # -- operators ------------------------------------------------------------

    def compare_values(self, a, b, line, where="comparing"):
        """-1, 0 or 1 for instances with compare()."""
        method, owner = a.cls.find_method("compare")
        if method is None:
            raise TBError(f"I can't compare {a.cls.name} objects; give the class a compare(other) method.", line)
        result = self.call(Bound(method, a, owner), [b], line)
        if not is_number(result):
            raise TBError(f"{a.cls.name}.compare() must give back a number (negative, 0 or positive), not {type_name(result)}.", line)
        return -1 if result < 0 else 1 if result > 0 else 0

    def operator_method(self, op, a, b, line):
        """Operator overloading: a.add(b) for a + b (or b.radd(a) when only the
        right side is an object). Gives back NO_METHOD when neither side has one."""
        name = OPERATOR_METHODS.get(op)
        if name is None:
            return NO_METHOD
        if isinstance(a, Instance):
            method, owner = a.cls.find_method(name)
            if method is not None:
                return self.call(Bound(method, a, owner), [b], line)
        if isinstance(b, Instance) and not isinstance(a, str):
            method, owner = b.cls.find_method("r" + name)
            if method is not None:
                return self.call(Bound(method, b, owner), [a], line)
        return NO_METHOD

    def binary(self, op, a, b, line):
        if (type(a) is Instance or type(b) is Instance) and op in OPERATOR_METHODS:
            result = self.operator_method(op, a, b, line)
            if result is not NO_METHOD:
                return result
        if op == "==":
            return tb_equal(a, b)
        if op == "!=":
            return not tb_equal(a, b)
        if op == "in":
            return self.contains(b, a, line)
        if op == "not in":
            return not self.contains(b, a, line)
        if op == "+":
            if is_number(a) and is_number(b):
                return check_size(a + b, line)
            if isinstance(a, str) or isinstance(b, str):
                return to_text(a) + to_text(b)
            if isinstance(a, list) and isinstance(b, list):
                return a + b
            if isinstance(a, bytes) and isinstance(b, bytes):
                return a + b
            raise TBError(f"I can't add {type_name(a)} and {type_name(b)}.", line, cls=ERROR_CLASSES["TypeError"])
        if op in ("<", "<=", ">", ">="):
            if isinstance(a, Instance) and isinstance(b, Instance):
                c = self.compare_values(a, b, line)
                return {"<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[op]
            if not ((is_number(a) and is_number(b)) or (isinstance(a, str) and isinstance(b, str))
                    or (isinstance(a, bytes) and isinstance(b, bytes))):
                raise TBError(f"I can't compare {type_name(a)} with {type_name(b)}.", line, cls=ERROR_CLASSES["TypeError"])
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        if op == "*" and (isinstance(a, str) or isinstance(b, str) or isinstance(a, list) or isinstance(b, list)):
            seq, times = (a, b) if isinstance(a, (str, list)) else (b, a)
            what = "Text" if isinstance(seq, str) else "A list"
            if not is_number(times) or times < 0 or (
                isinstance(times, float) and not times.is_integer()
            ):
                raise TBError(
                    f"{what} can only be repeated a whole number of times (0 or more), "
                    f"not {to_text(times, nested=True)}.",
                    line,
                )
            if len(seq) * int(times) > 100_000_000:
                raise TBError(f"That {'text' if isinstance(seq, str) else 'list'} would be too long for me to make.", line)
            return seq * int(times)
        if op in ("&", "|", "^", "<<", ">>"):
            return self.bitwise(op, a, b, line)
        if not (is_number(a) and is_number(b)):
            verb = {"-": "subtract", "*": "multiply", "/": "divide", "//": "divide",
                    "%": "find the remainder of", "**": "raise to a power"}[op]
            raise TBError(f"I can only {verb} numbers, not {type_name(a)} and {type_name(b)}.", line,
                          cls=ERROR_CLASSES["TypeError"])
        try:
            if op == "-":
                return check_size(a - b, line)
            if op == "*":
                return check_size(a * b, line)
            if op == "**":
                pow_guard(a, b, line)
                if b < 0 and a == 0:
                    raise TBError("You can't divide by zero (0 to a negative power).", line)
                result = a ** b
                if isinstance(result, complex):
                    raise TBError("I can't take a root of a negative number (the result isn't a real number).", line)
                if isinstance(result, float) and not math.isfinite(result):
                    raise TBError(TOO_BIG, line)
                return result
            if b == 0:
                raise TBError("You can't divide by zero.", line, cls=ERROR_CLASSES["ValueError"])
            if op == "/":
                return check_size(a / b, line)
            if op == "//":
                return check_size(a // b, line)
            return a % b
        except OverflowError:
            raise TBError(TOO_BIG, line) from None

    def bitwise(self, op, a, b, line):
        names = {"&": "'&' (bitwise and)", "|": "'|' (bitwise or)", "^": "'^' (bitwise xor)",
                 "<<": "'<<' (shift left)", ">>": "'>>' (shift right)"}
        if isinstance(a, bool) or isinstance(b, bool):
            word, both = ("and", "both") if op == "&" else ("or", "either")
            if op in ("&", "|"):
                raise TBError(f"To say '{both}' with true/false values, TB uses the word '{word}'.", line)
        if not (is_whole(a) and is_whole(b)):
            raise TBError(
                f"{names[op]} needs whole numbers, not {to_text(a, nested=True)} and {to_text(b, nested=True)}.", line
            )
        a, b = int(a), int(b)
        if op == "&":
            return a & b
        if op == "|":
            return a | b
        if op == "^":
            return a ^ b
        if b < 0:
            raise TBError(f"{names[op]} needs a shift of 0 or more, not {b}.", line)
        if op == "<<":
            if b > 10_000 or a.bit_length() + b > 10_000:
                raise TBError(TOO_BIG, line)
            return a << b
        return a >> b

    def contains(self, container, item, line) -> bool:
        if isinstance(container, list):
            return any(tb_equal(x, item) for x in container)
        if isinstance(container, str):
            if not isinstance(item, str):
                raise TBError(
                    f"To look inside text, the left side of 'in' must be text too, not {type_name(item)}.",
                    line,
                )
            return item in container
        if isinstance(container, dict):
            if isinstance(item, str) or is_number(item):
                return tidy_number(item) in container
            return False
        if isinstance(container, Range):
            return item in container
        if isinstance(container, bytes):
            if is_whole(item):
                return int(item) in container
            if isinstance(item, bytes):
                return item in container
            raise TBError(f"To look inside bytes, the left side of 'in' must be bytes or a byte value, not {type_name(item)}.", line)
        if isinstance(container, Instance):
            method, owner = container.cls.find_method("contains")
            if method is not None:
                return truthy(self.call(Bound(method, container, owner), [item], line))
            if container.cls.is_error:
                return (isinstance(item, str) or is_number(item)) and tidy_number(item) in container.fields
            raise TBError(f"'in' can't look inside a {container.cls.name}; give the class a contains(x) method.", line)
        if isinstance(container, EnumType):
            return isinstance(item, EnumValue) and item.enum is container
        if isinstance(container, TBIterator):
            return any(tb_equal(x, item) for x in container)
        raise TBError(f"'in' needs a list, text or a map on its right, not {type_name(container)}.", line)

    # -- calls ----------------------------------------------------------------

    def call(self, callee, args, line, kwargs: dict | None = None):
        if isinstance(callee, Builtin):
            if kwargs:
                raise TBError(
                    f"{callee.name}() doesn't take named inputs like {next(iter(kwargs))} = ...; "
                    "give its inputs in order.", line,
                )
            lo, hi = callee.arity
            if not lo <= len(args) <= hi:
                raise TBError(arity_message(callee.name, lo, hi, len(args)), line)
            try:
                return callee.fn(line, *args)
            except (TBError, ReturnSignal, BreakSignal, ContinueSignal, RecursionError, MemoryError,
                    _GenAbort):
                raise
            except Exception as err:  # a library bug: never show a Python traceback
                raise TBError(f"Something went wrong inside {callee.name}(): {err}", line) from None
        if isinstance(callee, Function):
            return self.call_function(callee, args, kwargs, line)
        if isinstance(callee, Bound):
            fn = callee.fn
            if isinstance(fn, NativeMethod):
                if kwargs:
                    raise TBError(f"{fn.name}() doesn't take named inputs; give its inputs in order.", line)
                lo, hi = fn.arity
                if not lo <= len(args) <= hi:
                    raise TBError(arity_message(f"{callee.owner.name}.{fn.name}", lo, hi, len(args)), line)
                return fn.fn(self, line, callee.self_value, *args)
            return self.call_function(fn, args, kwargs, line, callee.self_value, callee.owner)
        if isinstance(callee, Partial):
            merged = dict(callee.kwargs)
            if kwargs:
                merged.update(kwargs)
            return self.call(callee.fn, callee.args + list(args), line, merged or None)
        if isinstance(callee, TBClass):
            return self.construct(callee, args, kwargs, line)
        if isinstance(callee, Instance):
            method, owner = callee.cls.find_method("call")
            if method is not None:
                return self.call(Bound(method, callee, owner), args, line, kwargs)
            raise TBError(
                f"I can only call functions, not a {callee.cls.name} object. "
                "(Give the class a call(...) method to make its objects callable.)", line,
            )
        if callable(callee) and not isinstance(callee, (Module, EnumType, type)):
            try:
                return callee(*args, **(kwargs or {}))
            except (TBError, ReturnSignal, RecursionError, MemoryError, _GenAbort):
                raise
            except Exception as err:
                raise TBError(f"Something went wrong inside a function: {err}", line) from None
        article = {
            "number": "a number", "list": "a list", "map": "a map", "library": "a library",
            "enum": "an enum", "range": "a range", "generator": "a generator", "bytes": "bytes",
        }.get(type_name(callee), type_name(callee))
        if isinstance(callee, EnumValue):
            article = f"the enum value {to_text(callee)}"
        raise TBError(f"I can only call functions, not {article}.", line, cls=ERROR_CLASSES["TypeError"])

    def bind_params(self, fn: Function, args: list, kwargs: dict | None, line,
                    self_value=None, owner=None) -> Env:
        scope = Env(fn.closure)
        if self_value is not None or owner is not None:
            scope.vars["self"] = self_value
            scope.vars["%class"] = owner
        params = fn.params
        positional = [p for p in params if p.kind == "pos"]
        rest = next((p for p in params if p.kind == "rest"), None)
        kwrest = next((p for p in params if p.kind == "kwrest"), None)
        kwargs = dict(kwargs) if kwargs else {}
        n_pos = len(positional)
        label = fn.name if owner is None else f"{owner.name}.{fn.name}"
        if len(args) > n_pos and rest is None:
            raise TBError(arity_message(label, fn.required, n_pos, len(args)), line)
        for i, param in enumerate(positional):
            name = param.name
            if i < len(args):
                if name is not None and name in kwargs:
                    raise TBError(f"{label}() got '{name}' twice: once in order and once by name.", line)
                value = args[i]
            elif name is not None and name in kwargs:
                value = kwargs.pop(name)
            elif param.default is not None:
                value = self.evaluate(param.default, scope)
            else:
                if kwargs and not kwrest:
                    unknown = next(iter(kwargs))
                    if unknown not in {p.name for p in positional}:
                        raise self.unknown_keyword(label, unknown, positional, line)
                raise TBError(arity_message(label, fn.required, MANY if rest else n_pos, len(args)), line)
            if name is not None:
                scope.vars[name] = value
            else:
                self.bind_pattern(param.target, value, scope, line)
        if rest is not None:
            scope.vars[rest.name] = list(args[n_pos:])
        if kwargs:
            if kwrest is not None:
                scope.vars[kwrest.name] = kwargs
            else:
                raise self.unknown_keyword(label, next(iter(kwargs)), positional, line)
        elif kwrest is not None:
            scope.vars[kwrest.name] = {}
        return scope

    def unknown_keyword(self, label, name, positional, line) -> TBError:
        names = [p.name for p in positional if p.name]
        hint = difflib.get_close_matches(name, names, n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        listing = f" Its inputs are: {', '.join(names)}." if names else " It has no named inputs."
        return TBError(f"{label}() has no input called '{name}'.{extra}{listing}", line)

    def call_function(self, fn: Function, args, kwargs, line, self_value=None, owner=None):
        if self.depth >= MAX_DEPTH:
            who = "This function" if fn.name == "anonymous" else f"'{fn.name}'"
            raise TBError(
                f"{who} called itself too many times ({MAX_DEPTH} deep). "
                "Is there a way for it to stop?",
                line,
            )
        scope = self.bind_params(fn, args, kwargs, line, self_value, owner)
        if fn.is_generator:
            return TBIterator(ThreadGenerator(self, fn, scope), "generator")
        frame = Frame(fn.name, fn.file, line, scope)
        self.depth += 1
        outer_file, self.current_file = self.current_file, fn.file
        self.call_stack.append(frame)
        self.frame = frame
        trace = self.trace
        if trace is not None:
            trace("call", line, fn.file or self.script_path, scope)
        try:
            self.exec_block(fn.body, scope)
            result = None
        except ReturnSignal as signal:
            result = signal.value
        except TBError as err:
            if err.file is UNSET:
                err.file = fn.file  # the innermost function knows the file
            if err.stack is None:
                err.stack = self.snapshot_frames()
            if trace is not None:
                trace("exception", frame.line, fn.file or self.script_path, scope)
            raise
        finally:
            if frame.defers:
                self.run_defers(frame)
            self.call_stack.pop()
            self.frame = self.call_stack[-1]
            self.depth -= 1
            self.current_file = outer_file
        if trace is not None:
            trace("return", frame.line, fn.file or self.script_path, scope)
        return result

    def run_defers(self, frame: Frame) -> None:
        defers, frame.defers = frame.defers, None
        for body, env in reversed(defers):
            try:
                self.exec_block(body, Env(env))
            except ReturnSignal:
                pass

    def construct(self, cls: TBClass, args, kwargs, line):
        inst = Instance(cls)
        if cls.is_error:
            inst.fields.update({"message": "", "line": None, "file": None, "kind": cls.kind, "stack": []})
        for klass in cls.chain():
            for name, node in klass.field_defaults:
                inst.fields[name] = None if node is None else self.evaluate(node, klass.closure or self.globals)
        init, owner = cls.find_method("init")
        if init is None:
            if args or kwargs:
                raise TBError(
                    f"{cls.name} has no init method, so {cls.name}() takes no inputs (got {len(args) + len(kwargs or {})}). "
                    f"Add one: fn init(...) {{ self.field = ... }}", line,
                )
            return inst
        self.call(Bound(init, inst, owner), args, line, kwargs)
        return inst

    # -- generators ------------------------------------------------------------

    def generator_start_state(self, fn: Function, channel) -> tuple:
        frames = list(self.call_stack) + [Frame(fn.name, fn.file, 0, None)]
        return (self.depth + 1, fn.file, frames, frames[-1], channel)

    def run_generator_body(self, fn: Function, scope: Env, channel) -> None:
        """Runs on the generator's thread: wait to be resumed, run the body,
        hand each yield back, finish."""
        channel.to_gen.acquire()
        if channel.abort:
            channel.finished = True
            return
        try:
            self.frame.env = scope
            self.exec_block(fn.body, scope)
        except ReturnSignal:
            pass
        except _GenAbort:
            pass
        except TBError as err:
            if err.file is UNSET:
                err.file = fn.file
            if err.stack is None:
                err.stack = self.snapshot_frames()
            channel.error = err
        except BaseException as err:  # TBExit, RecursionError, ...: hand it to the consumer
            channel.error = err
        finally:
            channel.finished = True
            channel.to_main.release()

    # -- modules --------------------------------------------------------------

    def import_node(self, node: A.Import) -> Module:
        return self.import_target(node.target, node.is_file, node.base_dir, node.line,
                                  getattr(node, "package_name", None))

    def import_target(self, target: str, is_file: bool, base_dir, line, package_name=None) -> Module:
        if is_file:
            base = base_dir or self.base_dir or os.getcwd()
            path = os.path.abspath(os.path.join(base, target))
            if package_name and not os.path.isfile(path):
                # import "tb-colors": no such file next to us, so look in tb_packages/
                entry = self.find_package_entry(package_name, base_dir)
                if entry is not None:
                    key = "pkg:" + package_name
                    if key in self.modules:
                        return self.modules[key]
                    module = self.load_tb_file(entry, package_name, line, key=key,
                                               name=package_name.replace("-", "_"))
                    module.std = False
                    return module
            return self.load_tb_file(path, target, line)
        return self.load_library(target, line, base_dir)

    def load_library(self, name: str, line=None, base_dir: str | None = None) -> Module:
        """Import a standard library by name (cached per interpreter), or a
        package from tb_packages/."""
        key = "lib:" + name
        if key in self.modules:
            return self.modules[key]
        spec = None if name.startswith("_") or not name.isidentifier() else self.native_spec(name)
        if spec is not None:
            try:
                pymod = importlib.import_module(f"tblib.{name}")
                module = pymod.load(self.api)
            except TBError:
                raise
            except Exception as err:
                raise TBError(f"The {name} library couldn't start: {err}", line) from None
            if not isinstance(module, Module):
                raise TBError(f"The {name} library is broken (load() didn't give a Module).", line)
            self.modules[key] = module
            return module
        path = None if name.startswith("_") else _tb_library_path(name)
        if path is not None:
            return self.load_tb_file(path, name, line, key=key, name=name)
        package = self.find_package_entry(name, base_dir)
        if package is not None:
            module = self.load_tb_file(package, name, line, key="pkg:" + name, name=name)
            module.std = False
            return module
        raise self.unknown_library(name, line, base_dir)

    @staticmethod
    def native_spec(name: str):
        """tblib's module spec for name (looked up through the tb module so tests can patch it)."""
        tb_module = sys.modules.get("tb")
        finder = getattr(tb_module, "_native_spec", None) or _native_spec
        return finder(name)

    def find_package_entry(self, name: str, base_dir: str | None) -> str | None:
        if name.startswith("_"):
            return None
        start = base_dir or self.base_dir or os.getcwd()
        folder = find_package(name, start)
        if folder is None:
            return None
        return package_entry(folder)

    def unknown_library(self, name: str, line, base_dir: str | None = None) -> TBError:
        libraries = list_libraries()
        hint = difflib.get_close_matches(name, libraries, n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        start = base_dir or self.base_dir or os.getcwd()
        mine = os.path.join(start, name + ".tb")
        if os.path.isfile(mine):
            extra = f' To use your own file, put its name in quotes: import "{name}.tb"'
        elif find_package(name, start) is not None:
            extra = (f" There is a tb_packages/{name} folder, but it has no lib.tb, {name}.tb "
                     "or tb.toml with [package] main = \"...\".")
        else:
            extra += " (To add a package, run: tb add " + name + ")"
        listing = f" Libraries: {', '.join(libraries)}." if libraries else ""
        return TBError(f"There's no library called '{name}'.{extra}{listing}", line)

    def load_tb_file(self, path: str, shown: str, line=None, *, key=None, name=None) -> Module:
        """Run a .tb file once and give back its top-level names as a Module."""
        key = key or path
        if key in self.modules:
            return self.modules[key]
        if key in self.loading:
            start = self.loading.index(key)
            chain = [os.path.basename(k.split(":", 1)[1] + ".tb" if k.startswith(("lib:", "pkg:")) else k)
                     for k in self.loading[start:]] + [os.path.basename(path)]
            raise TBError(
                "These files import each other in a circle: " + " -> ".join(chain) + ". "
                "Move the shared part into another file that both can import.",
                line,
            )
        try:
            source = read_source(path)
        except FileNotFoundError:
            raise TBError(
                f"I couldn't find the file '{shown}' to import (I looked in {os.path.dirname(path)}).",
                line,
            ) from None
        except OSError as err:
            raise TBError(f"I couldn't open '{shown}' to import it: {err.strerror or err}.", line) from None
        folder = os.path.dirname(path)
        try:
            program = parse(source, folder)
        except TBError as err:
            if err.file is UNSET:
                err.file = path
            raise
        module = Module(name or os.path.splitext(os.path.basename(path))[0])
        module.doc, module.docs = tb_source_docs(source)
        module.path = path
        module.std = name is not None
        module.exports = set() if getattr(program, "uses_export", False) else None
        env = Env(self.builtins)
        module.fields = env.vars  # live: the module's own top-level names
        self.loading.append(key)
        outer_file, self.current_file = self.current_file, path
        outer_module, self.current_module = self.current_module, module
        frame = Frame(None, path, 0, env)
        self.call_stack.append(frame)
        outer_frame, self.frame = self.frame, frame
        try:
            self.exec_block(program.body, env)
        except TBError as err:
            if err.file is UNSET:
                err.file = path
            raise
        except ReturnSignal:  # pragma: no cover - the parser forbids top-level return
            pass
        finally:
            self.loading.pop()
            self.current_file = outer_file
            self.current_module = outer_module
            self.call_stack.pop()
            self.frame = outer_frame
        self.modules[key] = module
        return module


BUILTIN_DOCS = {
    "say": "say(...)\nShow values on the screen, with spaces between them.",
    "ask": "ask(prompt?) -> text\nShow prompt, wait for the person to type, and give back what they typed.",
    "len": "len(value) -> number\nHow many letters (text), items (list), keys (map), bytes, or numbers in a range.",
    "str": "str(value) -> text\nThe value as text.",
    "num": "num(text) -> number\nThe text as a number, like num(\"42\").",
    "range": "range(end) / range(start, end, step?) -> list\nA list of whole numbers from start up to (not including) end. (For a lazy range write start..end.)",
    "push": "push(list, value) -> list\nAdd value to the end of the list.",
    "care": "care(name?)\nShow a kind message.",
    "type": "type(value) -> text\nOne of: number, text, bool, nil, list, map, bytes, range, generator, function, module, class, enum, or a class name for objects.",
    "keys": "keys(map) -> list\nThe map's keys, in order.",
    "int": "int(value) -> number\nThe whole-number part (3.9 gives 3). Text is turned into a number first.",
    "float": "float(value) -> number\nThe value as a float (a number that may have a fraction). Text is turned into a number first.",
    "format": "format(value, spec) -> text\nThe value as text with a format spec: format(3.14159, \".2f\") is \"3.14\"; format(42, \"08\") is \"00000042\"; same specs as f\"{x:spec}\".",
    "round": "round(number, digits?) -> number\nRounded to digits after the dot (default 0). 2.5 rounds to 3.",
    "min": "min(list) / min(a, b, ...) -> value\nThe smallest value.",
    "max": "max(list) / max(a, b, ...) -> value\nThe biggest value.",
    "sum": "sum(list) -> number\nAll the numbers in the list (or range) added up.",
    "sorted": "sorted(list) -> list\nA new sorted list (all numbers or all text).",
    "exit": "exit(code?)\nStop the program now. 0 (the default) means all went well.",
}

BUILTIN_VALUE_DOCS = {
    "done": "done\nWhat a generator's next() gives back when it has nothing left (and what your own next() methods give back to stop a for loop).",
}
