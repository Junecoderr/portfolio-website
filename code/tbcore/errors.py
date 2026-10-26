"""Errors and limits shared by every part of TB."""

from __future__ import annotations

import math
import os
import re

# How many TB function calls may be "open" at once before TB says the program
# is probably calling itself forever. Deep enough for countdown(2000) and fib.
MAX_DEPTH = 3000

# Whole numbers bigger than about 1e330 are refused: Python would happily keep
# growing them until printing fails or the computer runs out of memory.
MAX_INT_BITS = 1100
# Results of ** may be bigger (big ints are unlimited in 3.0), up to this.
MAX_POW_BITS = 10_000
# The longest list range() will make.
MAX_RANGE = 10_000_000
TOO_BIG = "That number got too big for me to work with."

# Builtins whose maximum number of inputs is this (or more) take "any number".
MANY = 255


def check_size(value, line=None):
    """Return value, or raise a friendly TBError if the number is too big."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() > MAX_INT_BITS:
            raise TBError(TOO_BIG, line)
    elif isinstance(value, float) and not math.isfinite(value):
        raise TBError(TOO_BIG, line)
    return value


class _Unset:
    """Marks an error whose file is not known yet (see Interpreter.call)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


def snake_case(name: str) -> str:
    """ClassName -> class_name (the `kind` of an error)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class TBError(Exception):
    """An error in a TB program, with a friendly message and a line number.

    fields: extra values a TB program sees on the caught error
    (try { } catch err { err.code }), e.g. from throw {"message": .., "code": 7}.
    file: the .tb file the line belongs to; None for the main program.
    cls: the TB error class (Error, FileError, ...); None means Error.
    stack: list of {"fn", "line", "file"} frames, innermost first (filled in
    by the interpreter when the error passes through a function call).
    value: the TB Error instance a `catch` binds (made on demand).
    """

    def __init__(self, message: str, line: int | None = None, fields: dict | None = None,
                 cls=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.fields = dict(fields) if fields else {}
        self.file = UNSET
        self.cls = cls
        self.stack: list | None = None
        self.value = None

    @property
    def kind(self) -> str:
        if "kind" in self.fields:
            return self.fields["kind"]
        if self.cls is not None:
            return self.cls.kind
        return "error"

    def __str__(self) -> str:
        if self.line is not None:
            if isinstance(self.file, str):
                return f"Oops! On line {self.line} of {os.path.basename(self.file)}: {self.message}"
            return f"Oops! On line {self.line}: {self.message}"
        return f"Oops! {self.message}"

    def trace_text(self) -> str:
        """The short "in f() ← g()" line shown under an uncaught error ("" if
        the error happened outside any function)."""
        names = [f"{frame['fn']}()" for frame in (self.stack or []) if frame.get("fn")]
        return "  in " + " ← ".join(names) if names else ""

    def to_map(self) -> dict:
        """The map a TB 2.x 'catch' saw: message, line, kind, plus any extra fields."""
        result = {"message": self.message, "line": self.line, "kind": self.kind}
        result.update(self.fields)
        if isinstance(self.file, str):
            result.setdefault("file", os.path.basename(self.file))
        return result


class TBExit(SystemExit):
    """exit(code) was called. Not catchable by TB's try/catch."""


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass
