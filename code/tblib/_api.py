"""Helpers for writing TB libraries in Python ("native modules").

A native module is a file tblib/<name>.py with a load(api) function that
returns a Module. TB calls load() once per running program, the first time
the program says `import <name>`.

    from tblib._api import MANY, Module, check_type, fail

    def load(api):
        m = Module("greet", "Friendly greetings.")      # shown by `tb doc`

        @m.fn("hello", 1, 2, doc="hello(name, punctuation?) -> text\\nSay hi to someone.")
        def hello(line, name, punctuation="!"):
            check_type(line, name, "text", "greet.hello", "name")
            return f"Hi {name}{punctuation}"

        @m.fn("each", 2, doc="each(list, fn)\\nCall fn(item) for every item.")
        def each(line, items, fn):
            check_type(line, items, "list", "greet.each", "list")
            check_type(line, fn, "function", "greet.each", "fn")
            for item in items:
                api.call(fn, [item], line)            # call back into TB

        m.value("VERSION", "1.0", doc="The library's version.")
        return m

Rules of the road
-----------------
* Every function receives `line` first (the TB line of the call), then the
  TB arguments. `@m.fn(name, min_args, max_args=None, doc=...)`:
  max_args None means "exactly min_args"; MANY means "any number".
* The doc text: first line is the signature (`read(path) -> text`), the
  rest is a plain-English description. `tb doc <name>` prints it and the
  website is generated from it, so write one for every function.
* Values crossing the boundary: nil=None, true/false=bool, numbers=int/float,
  text=str, list=list, map=dict (keys are str or numbers). Give back only
  those (convert tuples to lists, sets to sorted lists, bytes to text).
* Problems: `fail(line, "plain English message")`. It raises a TBError that
  TB programs can catch with try/catch, and it prints as
  "Oops! On line N: message" when uncaught. Never let a Python exception
  escape; `fail_os(line, err, path)` turns OSError into a friendly message.
* `api.call(fn, args, line)` calls a TB function (callbacks).
  `api.wrap(fn, line)` gives a plain Python callable (for tkinter etc.).
* `api.output(text)` prints through TB (tests and the playground capture it);
  `api.input(prompt)` reads a line like ask().
* `api.base_dir` is the running script's folder; `api.args` its arguments.
* `api.in_browser` is True in the web playground (Pyodide). Libraries that
  need a real computer call `api.need_desktop(line, "The gui library")`.
"""

from __future__ import annotations

import sys

import tb as _tb

Module = _tb.Module
TBError = _tb.TBError
MANY = _tb.MANY
to_text = _tb.to_text
type_name = _tb.type_name
tb_type = _tb.tb_type
is_number = _tb.is_number

__all__ = [
    "API", "MANY", "Module", "TBError", "check_type", "fail", "fail_os",
    "is_number", "tb_type", "to_text", "type_name",
]


def fail(line, message: str, **fields):
    """Stop with a friendly, catchable TB error. Extra keyword fields show up
    in the caught error map: fail(line, "Not found", status=404) gives
    err.status == 404 inside `catch err { }`."""
    raise TBError(message, line, fields or None)


def fail_os(line, err: OSError, path: str, action: str = "open"):
    """Turn an OSError about `path` into a friendly TB error."""
    name = str(path)
    if isinstance(err, FileNotFoundError):
        fail(line, f"I couldn't find '{name}'. Is the name spelled right?", path=name)
    if isinstance(err, IsADirectoryError):
        fail(line, f"'{name}' is a folder, not a file.", path=name)
    if isinstance(err, NotADirectoryError):
        fail(line, f"'{name}' is not a folder.", path=name)
    if isinstance(err, PermissionError):
        fail(line, f"I'm not allowed to {action} '{name}'.", path=name)
    if isinstance(err, FileExistsError):
        fail(line, f"'{name}' already exists.", path=name)
    reason = getattr(err, "strerror", None) or str(err)
    fail(line, f"I couldn't {action} '{name}': {reason}.", path=name)


_KIND_WORDS = {
    "text": "text", "number": "a number", "whole": "a whole number", "bool": "true or false",
    "list": "a list", "map": "a map", "function": "a function", "nil": "nil",
    "any": "a value",
}


def _matches(value, kind: str) -> bool:
    if kind == "any":
        return True
    if kind == "nil":
        return value is None
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "number":
        return is_number(value)
    if kind == "whole":
        return _tb.is_whole(value)
    if kind == "text":
        return isinstance(value, str)
    if kind == "list":
        return isinstance(value, list)
    if kind == "map":
        return isinstance(value, dict)
    if kind == "function":
        return isinstance(value, (_tb.Function, _tb.Builtin)) or (
            callable(value) and not isinstance(value, (Module, type))
        )
    raise ValueError(f"check_type: unknown kind {kind!r}")


def check_type(line, value, kind: str, fn_name: str, arg_name: str):
    """Check an argument and return it (whole numbers come back as int).

    kind: "text", "number", "whole", "bool", "list", "map", "function",
    "nil", "any", or several joined by "|", e.g. "text|nil" or "list|map".

        path = check_type(line, path, "text", "fs.read", "path")
        -> Oops! On line 3: fs.read() needs path to be text, not a number.
    """
    kinds = kind.split("|")
    for k in kinds:
        if _matches(value, k):
            return int(value) if k == "whole" else value
    wanted = " or ".join(_KIND_WORDS.get(k, k) for k in kinds)
    got = to_text(value, nested=True) if kind == "whole" and is_number(value) else _article(value)
    fail(line, f"{fn_name}() needs {arg_name} to be {wanted}, not {got}.")


def _article(value) -> str:
    name = type_name(value)
    return {"number": "a number", "list": "a list", "map": "a map", "function": "a function",
            "library": "a library"}.get(name, name)


class API:
    """What a native library's load(api) receives. One per running program."""

    def __init__(self, interpreter):
        self.interpreter = interpreter

    # -- calling TB code ------------------------------------------------------

    def call(self, fn, args, line=None):
        """Call a TB function (or built-in) with a list of arguments."""
        return self.interpreter.call(fn, list(args), line)

    def wrap(self, fn, line=None):
        """A plain Python callable that calls the TB function fn. TB errors
        inside it still raise TBError, so catch them where Python calls it."""
        def wrapper(*args):
            return _tb._guarded(lambda: self.call(fn, list(args), line))
        wrapper.__name__ = getattr(fn, "name", "callback")
        return wrapper

    # -- input and output -----------------------------------------------------

    def output(self, text) -> None:
        """Print a line through TB (respects the program's output, e.g. tests)."""
        self.interpreter.output(text if isinstance(text, str) else to_text(text))

    def input(self, prompt: str = "") -> str:
        """Read one line like ask(): "" at end of input, spaces trimmed."""
        try:
            answer = self.interpreter.input_fn(prompt)
        except EOFError:
            return ""
        return "" if answer is None else str(answer).strip()

    # -- the running program --------------------------------------------------

    @property
    def base_dir(self) -> str:
        """Folder of the running script (the current folder in the REPL)."""
        import os

        return self.interpreter.base_dir or os.getcwd()

    @property
    def args(self) -> list:
        """The program's command-line arguments (tb main.tb these args)."""
        return self.interpreter.argv

    @property
    def in_browser(self) -> bool:
        """True inside the web playground (Pyodide)."""
        return sys.platform == "emscripten"

    def need_desktop(self, line, what: str) -> None:
        """Fail in the browser: need_desktop(line, "The gui library")."""
        if self.in_browser:
            fail(line, f"{what} needs the TB app on your computer; "
                       "it can't work inside the browser playground.")

    def to_text(self, value) -> str:
        return to_text(value)

    def type_of(self, value) -> str:
        """What TB's type(x) says: "number", "text", "map", ..."""
        return tb_type(value)
