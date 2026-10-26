#!/usr/bin/env python3
"""TB - a small, friendly programming language.

Made with love for Tanisha Brahma (TB = her initials).

This single file is the core of the language: a lexer, a parser that builds a
tree, an interpreter that walks that tree, the module loader and the command
line. The standard library lives next to it, in the tblib/ folder. It needs
only Python 3.10+ and its standard library, so it also runs in the browser
through Pyodide.

Usage:
    tb file.tb [args...]   run a TB program (args go to os.args)
    tb                     start the interactive prompt (REPL)
    tb doc [library]       show the standard library
    tb test [folder]       run every *_test.tb file
    tb --version           print the version
    tb --help              print help
"""

from __future__ import annotations

import sys

# When this file runs as a script (`python tb.py`, or the one-file app), make
# `import tb` (used by tblib) find this very module instead of loading a second
# copy whose TBError would be a different class.
if __name__ == "__main__":
    sys.modules.setdefault("tb", sys.modules[__name__])

import difflib  # noqa: E402
import importlib  # noqa: E402
import importlib.util  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402

__version__ = "2.0.0"

# How many TB function calls may be "open" at once before TB says the program
# is probably calling itself forever. Deep enough for countdown(2000) and fib.
MAX_DEPTH = 3000

# Whole numbers bigger than about 1e330 are refused: Python would happily keep
# growing them until printing fails or the computer runs out of memory.
MAX_INT_BITS = 1100
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

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class _Unset:
    """Marks an error whose file is not known yet (see Interpreter.call)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


class TBError(Exception):
    """An error in a TB program, with a friendly message and a line number.

    fields: extra values a TB program sees in the caught error map
    (try { } catch err { err.code }), e.g. from throw {"message": .., "code": 7}.
    file: the .tb file the line belongs to; None for the main program.
    """

    def __init__(self, message: str, line: int | None = None, fields: dict | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.fields = dict(fields) if fields else {}
        self.file = UNSET

    def __str__(self) -> str:
        if self.line is not None:
            if isinstance(self.file, str):
                return f"Oops! On line {self.line} of {os.path.basename(self.file)}: {self.message}"
            return f"Oops! On line {self.line}: {self.message}"
        return f"Oops! {self.message}"

    def to_map(self) -> dict:
        """The map a TB 'catch' sees: message, line, kind, plus any extra fields."""
        result = {"message": self.message, "line": self.line, "kind": "error"}
        result.update(self.fields)
        if isinstance(self.file, str):
            result.setdefault("file", os.path.basename(self.file))
        return result


class TBExit(SystemExit):
    """exit(code) was called. Not catchable by TB's try/catch."""


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

KEYWORDS = {
    "let", "fn", "return", "if", "else", "while", "for", "in",
    "true", "false", "nil", "and", "or", "not",
}
# import, as, try, catch, throw, break and continue are not reserved words:
# they only mean something special where they start a statement, so 1.0
# programs that use them as names keep working.

ASSIGN_OPS = {"=", "+=", "-=", "*=", "/="}
TWO_CHAR_OPS = {"==", "!=", "<=", ">=", "+=", "-=", "*=", "/="}
ONE_CHAR_OPS = set("+-*/%<>=!(){}[],;.:")


class Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind: str, value, line: int):
        self.kind = kind    # NUMBER, STRING, FSTRING, IDENT, KEYWORD, OP, EOF
        self.value = value
        self.line = line

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, line {self.line})"


ESCAPES = {"n": "\n", "t": "\t", '"': '"', "'": "'", "\\": "\\"}
DIGITS = "0123456789"

# Friendly hints for characters people often type by accident.
CHAR_HINTS = {
    "“": "That's a curly quote. TB needs straight quotes: \" or '.",
    "”": "That's a curly quote. TB needs straight quotes: \" or '.",
    "‘": "That's a curly quote. TB needs straight quotes: \" or '.",
    "’": "That's a curly quote. TB needs straight quotes: \" or '.",
    "&": "To say 'both', TB uses the word 'and'.",
    "|": "To say 'either', TB uses the word 'or'.",
}


def _never_ends(quote, line):
    return TBError(f"This text never ends. Did you forget a closing {quote}?", line)


def _lex_fstring(source: str, i: int, line: int):
    """Read f"..." starting at the quote. Returns (parts, new_i): parts is a
    list of plain text pieces and (expression source, line) pairs."""
    n = len(source)
    quote = source[i]
    i += 1
    parts: list = []
    buf: list[str] = []
    while True:
        if i >= n or source[i] == "\n":
            raise _never_ends(quote, line)
        ch = source[i]
        if ch == quote:
            i += 1
            break
        if ch == "\\" and i + 1 < n and source[i + 1] != "\n":
            nxt = source[i + 1]
            buf.append(ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == "{":
            if source[i + 1:i + 2] == "{":
                buf.append("{")
                i += 2
                continue
            i += 1
            start = i
            depth = 0
            while True:
                if i >= n or source[i] == "\n":
                    raise TBError(
                        "This '{' inside the f-text was never closed. Did you forget a '}'?", line
                    )
                c = source[i]
                if c in "\"'":
                    j = i + 1
                    while j < n and source[j] != c and source[j] != "\n":
                        j += 2 if source[j] == "\\" else 1
                    if j >= n or source[j] != c:
                        raise TBError(
                            "This '{' inside the f-text was never closed. Did you forget a '}'?", line
                        )
                    i = j + 1
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        break
                    depth -= 1
                i += 1
            expr = source[start:i]
            i += 1
            if not expr.strip():
                raise TBError(
                    "There's an empty {} in this f-text. Put a name inside, like {name}, "
                    "or write {{ and }} to show real braces.",
                    line,
                )
            parts.append("".join(buf))
            buf = []
            parts.append((expr, line))
            continue
        if ch == "}":
            if source[i + 1:i + 2] == "}":
                buf.append("}")
                i += 2
                continue
            raise TBError("To show a '}' in f-text, write it twice: }}", line)
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p != ""], i


def tokenize(source: str, line: int = 1) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            while i < n and source[i] != "\n":
                i += 1
        elif c in DIGITS:
            start = i
            while i < n and source[i] in DIGITS:
                i += 1
            if i + 1 < n and source[i] == "." and source[i + 1] in DIGITS:
                i += 1
                while i < n and source[i] in DIGITS:
                    i += 1
                tokens.append(Token("NUMBER", check_size(float(source[start:i]), line), line))
            elif i - start > 400:
                raise TBError(TOO_BIG, line)
            else:
                tokens.append(Token("NUMBER", check_size(int(source[start:i]), line), line))
        elif c == "f" and i + 1 < n and source[i + 1] in "\"'":
            # f"..." (identifiers are read whole, so this 'f' starts a token)
            parts, i = _lex_fstring(source, i + 1, line)
            tokens.append(Token("FSTRING", parts, line))
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            word = source[start:i]
            tokens.append(Token("KEYWORD" if word in KEYWORDS else "IDENT", word, line))
        elif c in "\"'":
            quote = c
            start_line = line
            i += 1
            chars = []
            while True:
                if i >= n:
                    raise _never_ends(quote, start_line)
                ch = source[i]
                if ch == quote:
                    i += 1
                    break
                if ch == "\n":
                    raise _never_ends(quote, start_line)
                if ch == "\\" and i + 1 < n and source[i + 1] != "\n":
                    nxt = source[i + 1]
                    chars.append(ESCAPES.get(nxt, "\\" + nxt))
                    i += 2
                    continue
                chars.append(ch)
                i += 1
            tokens.append(Token("STRING", "".join(chars), start_line))
        elif source[i:i + 2] in TWO_CHAR_OPS:
            tokens.append(Token("OP", source[i:i + 2], line))
            i += 2
        elif c in ONE_CHAR_OPS:
            tokens.append(Token("OP", c, line))
            i += 1
        else:
            hint = CHAR_HINTS.get(c, "")
            raise TBError(f"I don't understand the character {c!r}. {hint}".rstrip(), line)
    tokens.append(Token("EOF", None, line))
    return tokens


# ---------------------------------------------------------------------------
# Syntax tree
# ---------------------------------------------------------------------------


class Node:
    line: int = 0


class Program(Node):
    def __init__(self, body):
        self.body = body


class Let(Node):
    def __init__(self, name, value, line):
        self.name, self.value, self.line = name, value, line


class Assign(Node):
    def __init__(self, target, value, line, op=None):
        # op is None for '=', or '+', '-', '*', '/' for '+=' and friends.
        self.target, self.value, self.line, self.op = target, value, line, op


class FnDecl(Node):
    def __init__(self, name, params, body, line):
        self.name, self.params, self.body, self.line = name, params, body, line


class Return(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class If(Node):
    def __init__(self, branches, otherwise, line):
        # branches: list of (condition, block); otherwise: block or None
        self.branches, self.otherwise, self.line = branches, otherwise, line


class While(Node):
    def __init__(self, cond, body, line):
        self.cond, self.body, self.line = cond, body, line


class For(Node):
    def __init__(self, name, iterable, body, line):
        self.name, self.iterable, self.body, self.line = name, iterable, body, line


class Break(Node):
    def __init__(self, line):
        self.line = line


class Continue(Node):
    def __init__(self, line):
        self.line = line


class Import(Node):
    def __init__(self, target, alias, is_file, base_dir, line):
        # target: library name, or a file path (is_file); alias: the bound name.
        self.target, self.alias, self.is_file = target, alias, is_file
        self.base_dir, self.line = base_dir, line


class Try(Node):
    def __init__(self, body, name, handler, line):
        self.body, self.name, self.handler, self.line = body, name, handler, line


class Throw(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class ExprStmt(Node):
    def __init__(self, expr, line):
        self.expr, self.line = expr, line


class Literal(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class ListLit(Node):
    def __init__(self, items, line):
        self.items, self.line = items, line


class MapLit(Node):
    def __init__(self, pairs, line):
        self.pairs, self.line = pairs, line


class FString(Node):
    def __init__(self, parts, line):
        # parts: plain text pieces and expression nodes, in order
        self.parts, self.line = parts, line


class Name(Node):
    def __init__(self, name, line):
        self.name, self.line = name, line


class Unary(Node):
    def __init__(self, op, operand, line):
        self.op, self.operand, self.line = op, operand, line


class Binary(Node):
    def __init__(self, op, left, right, line):
        self.op, self.left, self.right, self.line = op, left, right, line


class Logical(Node):
    def __init__(self, op, left, right, line):
        self.op, self.left, self.right, self.line = op, left, right, line


class Call(Node):
    def __init__(self, callee, args, line):
        self.callee, self.args, self.line = callee, args, line


class Index(Node):
    def __init__(self, target, index, line):
        self.target, self.index, self.line = target, index, line


class Dot(Node):
    def __init__(self, target, name, line):
        self.target, self.name, self.line = target, name, line


class FnExpr(Node):
    def __init__(self, params, body, line):
        self.params, self.body, self.line = params, body, line


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


# Words from other languages, and what TB says instead.
OTHER_LANGUAGE_WORDS = {
    "def": "TB makes functions with 'fn', like: fn greet(name) { ... }",
    "function": "TB makes functions with 'fn', like: fn greet(name) { ... }",
    "func": "TB makes functions with 'fn', like: fn greet(name) { ... }",
    "var": "TB makes new names with 'let', like: let x = 5",
    "const": "TB makes new names with 'let', like: let x = 5",
    "elif": "TB writes this as 'else if'.",
    "raise": "TB throws errors with 'throw', like: throw \"it broke\"",
    "except": "TB catches errors with: try { ... } catch err { ... }",
    "from": "TB imports a whole library, like: import fs  (then use fs.read(...))",
}

# Built-ins from other languages, used for 'Did you mean' hints.
OTHER_LANGUAGE_NAMES = {
    "print": "say", "println": "say", "input": "ask", "length": "len",
    "append": "push", "null": "nil", "None": "nil", "undefined": "nil",
    "True": "true", "False": "false", "string": "str", "int": "num",
    "float": "num", "number": "num",
}

NAME_RE = re.compile(r"[^\W\d]\w*")


def describe(tok: Token) -> str:
    if tok.kind == "EOF":
        return "the end of the program"
    if tok.kind == "STRING":
        return f'the text "{tok.value}"'
    if tok.kind == "FSTRING":
        return "an f-text"
    return f"'{tok.value}'"


class Parser:
    def __init__(self, tokens: list[Token], base_dir: str | None = None):
        self.tokens = tokens
        self.pos = 0
        self.fn_depth = 0    # how many functions we are inside (for 'return')
        self.loop_depth = 0  # how many loops we are inside (for 'break')
        self.base_dir = base_dir  # folder that 'import "file.tb"' is relative to

    # -- helpers ----------------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def peek_next(self) -> Token:
        return self.tokens[min(self.pos + 1, len(self.tokens) - 1)]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def check(self, kind: str, value=None) -> bool:
        tok = self.peek()
        return tok.kind == kind and (value is None or tok.value == value)

    def match(self, kind: str, value=None) -> bool:
        if self.check(kind, value):
            self.advance()
            return True
        return False

    def expect(self, kind: str, value=None, what: str | None = None) -> Token:
        if self.check(kind, value):
            return self.advance()
        tok = self.peek()
        wanted = what or (f"'{value}'" if value is not None else kind.lower())
        raise TBError(f"I expected {wanted} here, but found {describe(tok)}.", tok.line)

    def close(self, value: str, opener: Token, what: str) -> Token:
        """Expect a closing bracket; if the program ends first, point at the opener."""
        if self.check("EOF"):
            raise TBError(
                f"This '{opener.value}' was never closed. Did you forget a '{value}'?",
                opener.line,
            )
        return self.expect("OP", value, what)

    # -- statements -------------------------------------------------------

    def parse_program(self) -> Program:
        body = []
        while not self.check("EOF"):
            if self.match("OP", ";"):
                continue
            body.append(self.statement())
        return Program(body)

    def block(self) -> list[Node]:
        if self.check("OP", "="):
            raise TBError(
                "I expected '{' to start a block here, but found '='. "
                "To check if two things are equal, use '=='.",
                self.peek().line,
            )
        if self.check("OP", ":"):
            raise TBError(
                "I expected '{' to start a block here, but found ':'. "
                "TB uses '{' and '}' around blocks instead of ':'.",
                self.peek().line,
            )
        open_tok = self.expect("OP", "{", "'{' to start a block")
        body = []
        while not self.check("OP", "}"):
            if self.check("EOF"):
                raise TBError(
                    "This block was never closed. Did you forget a '}'?", open_tok.line
                )
            if self.match("OP", ";"):
                continue
            body.append(self.statement())
        self.advance()
        return body

    def loop_body(self) -> list[Node]:
        self.loop_depth += 1
        try:
            return self.block()
        finally:
            self.loop_depth -= 1

    def statement(self) -> Node:
        tok = self.peek()
        if tok.kind == "KEYWORD":
            if tok.value == "let":
                return self.let_statement()
            if tok.value == "fn" and self.peek_next().kind == "IDENT":
                return self.fn_declaration()
            if tok.value == "return":
                return self.return_statement()
            if tok.value == "if":
                return self.if_statement()
            if tok.value == "while":
                self.advance()
                cond = self.expression()
                return While(cond, self.loop_body(), tok.line)
            if tok.value == "for":
                return self.for_statement()
            if tok.value == "else":
                raise TBError("This 'else' doesn't belong to any 'if'.", tok.line)
        if tok.kind == "IDENT":
            special = self.special_statement(tok)
            if special is not None:
                return special
        if tok.kind == "IDENT" and tok.value in OTHER_LANGUAGE_WORDS:
            nxt = self.peek_next()
            if nxt.kind in ("IDENT", "KEYWORD", "NUMBER", "STRING") and nxt.line == tok.line:
                raise TBError(
                    f"I don't know the word '{tok.value}'. {OTHER_LANGUAGE_WORDS[tok.value]}",
                    tok.line,
                )
        expr = self.expression()
        if self.peek().kind == "OP" and self.peek().value in ASSIGN_OPS:
            eq = self.advance()
            if not isinstance(expr, (Name, Index, Dot)):
                raise TBError(
                    "I can only put a value into a name or a list slot (or a map key).", eq.line
                )
            value = self.expression()
            return Assign(expr, value, eq.line, None if eq.value == "=" else eq.value[0])
        return ExprStmt(expr, tok.line)

    def special_statement(self, tok: Token) -> Node | None:
        """import / try / throw / break / continue, which are only special at
        the start of a statement (so they still work as ordinary names)."""
        word = tok.value
        nxt = self.peek_next()
        same_line = nxt.line == tok.line and nxt.kind != "EOF"
        ends_here = (not same_line) or (nxt.kind == "OP" and nxt.value in ("}", ";"))
        if word == "import" and same_line and nxt.kind in ("IDENT", "STRING"):
            return self.import_statement()
        if word == "import" and not same_line:
            raise TBError(
                'What should I import? Write a library name, like: import fs  '
                '(or your own file: import "helpers.tb")',
                tok.line,
            )
        if word == "throw" and not same_line:
            raise TBError('What should I throw? Like: throw "Something went wrong"', tok.line)
        if word == "try" and nxt.kind == "OP" and nxt.value == "{":
            return self.try_statement()
        if word == "throw" and same_line and not (
            nxt.kind == "OP" and nxt.value in ASSIGN_OPS | {".", "[", "}", ";", ")", ",", "]"}
        ):
            self.advance()
            return Throw(self.expression(), tok.line)
        if word in ("break", "continue") and ends_here:
            self.advance()
            if self.loop_depth == 0:
                raise TBError(
                    f"'{word}' only works inside a loop (while or for).", tok.line
                )
            return Break(tok.line) if word == "break" else Continue(tok.line)
        if word == "catch" and (
            (nxt.kind == "OP" and nxt.value == "{")
            or (nxt.kind == "IDENT" and same_line and self.tokens[self.pos + 2].kind == "OP"
                and self.tokens[self.pos + 2].value == "{")
        ):
            raise TBError("This 'catch' doesn't belong to any 'try'.", tok.line)
        return None

    def import_statement(self) -> Import:
        tok = self.advance()
        target = self.advance()
        if self.check("OP", "."):
            raise TBError(
                f"Import the whole library with 'import {target.value}', "
                f"then use its parts like {target.value}.name.",
                tok.line,
            )
        alias = None
        if self.check("IDENT", "as"):
            self.advance()
            alias = self.expect("IDENT", what="a name after 'as'").value
        if target.kind == "STRING":
            path = target.value
            if not path.strip():
                raise TBError('I need a file name to import, like: import "helpers.tb"', tok.line)
            if not os.path.splitext(path)[1]:
                path += ".tb"
            if alias is None:
                stem = os.path.splitext(os.path.basename(path))[0]
                if not NAME_RE.fullmatch(stem) or stem in KEYWORDS:
                    raise TBError(
                        f"I can't make a name from '{target.value}'. Give it one with: "
                        f'import "{target.value}" as helpers',
                        tok.line,
                    )
                alias = stem
            return Import(path, alias, True, self.base_dir, tok.line)
        return Import(target.value, alias or target.value, False, self.base_dir, tok.line)

    def try_statement(self) -> Try:
        tok = self.advance()
        body = self.block()
        if not self.check("IDENT", "catch"):
            raise TBError(
                "A 'try' block needs a 'catch' after it, like: try { ... } catch err { ... }",
                tok.line,
            )
        catch_tok = self.advance()
        name = None
        if self.check("OP", "("):
            raise TBError(
                "Write the error's name without brackets, like: catch err { ... }", catch_tok.line
            )
        if self.check("IDENT"):
            name = self.advance().value
        handler = self.block()
        return Try(body, name, handler, tok.line)

    def let_statement(self) -> Let:
        tok = self.advance()
        name = self.expect("IDENT", what="a name after 'let'")
        self.expect("OP", "=", f"'=' after 'let {name.value}'")
        return Let(name.value, self.expression(), tok.line)

    def params(self) -> list[str]:
        self.expect("OP", "(", "'(' before the inputs")
        names = []
        if not self.check("OP", ")"):
            while True:
                tok = self.expect("IDENT", what="an input name")
                if tok.value in names:
                    raise TBError(
                        f"The input name '{tok.value}' is used twice. "
                        "Each input needs its own name.",
                        tok.line,
                    )
                names.append(tok.value)
                if not self.match("OP", ","):
                    break
        self.expect("OP", ")", "')' after the inputs")
        return names

    def fn_declaration(self) -> FnDecl:
        tok = self.advance()
        name = self.advance().value
        params = self.params()
        return FnDecl(name, params, self.function_body(), tok.line)

    def function_body(self) -> list[Node]:
        self.fn_depth += 1
        loops, self.loop_depth = self.loop_depth, 0
        try:
            return self.block()
        finally:
            self.fn_depth -= 1
            self.loop_depth = loops

    def return_statement(self) -> Return:
        tok = self.advance()
        if self.fn_depth == 0:
            raise TBError(
                "'return' only works inside a function (fn). "
                "It hands a value back to whoever called the function.",
                tok.line,
            )
        nxt = self.peek()
        if nxt.line != tok.line or nxt.kind == "EOF" or (
            nxt.kind == "OP" and nxt.value in ("}", ";")
        ):
            return Return(None, tok.line)
        return Return(self.expression(), tok.line)

    def if_statement(self) -> If:
        tok = self.advance()
        branches = [(self.expression(), self.block())]
        otherwise = None
        while self.match("KEYWORD", "else"):
            if self.match("KEYWORD", "if"):
                branches.append((self.expression(), self.block()))
            else:
                otherwise = self.block()
                break
        return If(branches, otherwise, tok.line)

    def for_statement(self) -> For:
        tok = self.advance()
        name = self.expect("IDENT", what="a name after 'for'")
        self.expect("KEYWORD", "in", "'in' (like: for x in list)")
        iterable = self.expression()
        return For(name.value, iterable, self.loop_body(), tok.line)

    # -- expressions (lowest to highest precedence) -----------------------

    def expression(self) -> Node:
        return self.or_expr()

    def or_expr(self) -> Node:
        left = self.and_expr()
        while self.check("KEYWORD", "or"):
            op = self.advance()
            left = Logical("or", left, self.and_expr(), op.line)
        return left

    def and_expr(self) -> Node:
        left = self.not_expr()
        while self.check("KEYWORD", "and"):
            op = self.advance()
            left = Logical("and", left, self.not_expr(), op.line)
        return left

    def not_expr(self) -> Node:
        # 'not' reads like English, so 'not x == 5' means 'not (x == 5)'.
        if self.check("KEYWORD", "not"):
            tok = self.advance()
            return Unary("not", self.not_expr(), tok.line)
        return self.equality()

    def equality(self) -> Node:
        left = self.comparison()
        while self.peek().kind == "OP" and self.peek().value in ("==", "!="):
            op = self.advance()
            left = Binary(op.value, left, self.comparison(), op.line)
        return left

    def comparison(self) -> Node:
        left = self.term()
        while True:
            tok = self.peek()
            if tok.kind == "OP" and tok.value in ("<", "<=", ">", ">="):
                self.advance()
                left = Binary(tok.value, left, self.term(), tok.line)
            elif tok.kind == "KEYWORD" and tok.value == "in":
                self.advance()
                left = Binary("in", left, self.term(), tok.line)
            elif (tok.kind == "KEYWORD" and tok.value == "not"
                  and self.peek_next().kind == "KEYWORD" and self.peek_next().value == "in"):
                self.advance()
                self.advance()
                left = Binary("not in", left, self.term(), tok.line)
            else:
                return left

    def term(self) -> Node:
        left = self.factor()
        while self.peek().kind == "OP" and self.peek().value in ("+", "-"):
            op = self.advance()
            left = Binary(op.value, left, self.factor(), op.line)
        return left

    def factor(self) -> Node:
        left = self.unary()
        while self.peek().kind == "OP" and self.peek().value in ("*", "/", "%"):
            op = self.advance()
            left = Binary(op.value, left, self.unary(), op.line)
        return left

    def unary(self) -> Node:
        tok = self.peek()
        if (tok.kind == "OP" and tok.value in ("-", "!")) or (
            tok.kind == "KEYWORD" and tok.value == "not"
        ):
            # 'not' only lands here inside an operand, e.g. '1 == not x'.
            self.advance()
            op = "-" if tok.value == "-" else "not"
            return Unary(op, self.unary(), tok.line)
        return self.postfix()

    def postfix(self) -> Node:
        expr = self.primary()
        while True:
            tok = self.peek()
            # A '(' or '[' on a new line starts a new statement, not a call/index.
            if tok.kind == "OP" and tok.value == "(" and tok.line == self.prev_line():
                self.advance()
                args = []
                if not self.check("OP", ")"):
                    while True:
                        args.append(self.expression())
                        if not self.match("OP", ",") or self.check("OP", ")"):
                            break
                self.close(")", tok, "')' to finish the call")
                expr = Call(expr, args, tok.line)
            elif tok.kind == "OP" and tok.value == "[" and tok.line == self.prev_line():
                self.advance()
                index = self.expression()
                self.close("]", tok, "']' after the index")
                expr = Index(expr, index, tok.line)
            elif tok.kind == "OP" and tok.value == ".":
                # A '.' may start a new line: it can only continue the expression.
                self.advance()
                name = self.peek()
                if name.kind not in ("IDENT", "KEYWORD"):
                    raise TBError(
                        f"I expected a name after '.', but found {describe(name)}.", tok.line
                    )
                self.advance()
                expr = Dot(expr, name.value, name.line)
            else:
                return expr

    def prev_line(self) -> int:
        return self.tokens[self.pos - 1].line if self.pos > 0 else 0

    def primary(self) -> Node:
        tok = self.peek()
        if tok.kind in ("NUMBER", "STRING"):
            self.advance()
            return Literal(tok.value, tok.line)
        if tok.kind == "FSTRING":
            self.advance()
            return self.fstring(tok)
        if tok.kind == "KEYWORD":
            if tok.value == "true":
                self.advance()
                return Literal(True, tok.line)
            if tok.value == "false":
                self.advance()
                return Literal(False, tok.line)
            if tok.value == "nil":
                self.advance()
                return Literal(None, tok.line)
            if tok.value == "fn":
                self.advance()
                params = self.params()
                return FnExpr(params, self.function_body(), tok.line)
        if tok.kind == "IDENT":
            self.advance()
            return Name(tok.value, tok.line)
        if tok.kind == "OP" and tok.value == "(":
            self.advance()
            expr = self.expression()
            self.close(")", tok, "')' to close the '('")
            return expr
        if tok.kind == "OP" and tok.value == "[":
            self.advance()
            items = []
            if not self.check("OP", "]"):
                while True:
                    items.append(self.expression())
                    # A trailing comma before ']' is fine.
                    if not self.match("OP", ",") or self.check("OP", "]"):
                        break
            self.close("]", tok, "']' to close the list")
            return ListLit(items, tok.line)
        if tok.kind == "OP" and tok.value == "{":
            return self.map_literal()
        if tok.kind == "OP" and tok.value == "." and self.peek_next().kind == "NUMBER":
            raise TBError("Numbers need a digit before the dot, like 0.5.", tok.line)
        raise TBError(f"I didn't expect {describe(tok)} here.", tok.line)

    def map_literal(self) -> MapLit:
        tok = self.advance()
        pairs = []
        while not self.check("OP", "}"):
            if self.check("EOF"):
                break
            key = self.expression()
            if not self.check("OP", ":"):
                found = self.peek()
                raise TBError(
                    "I expected ':' between a key and its value in this map "
                    f'(like {{"name": "Tanisha"}}), but found {describe(found)}.',
                    found.line,
                )
            self.advance()
            pairs.append((key, self.expression()))
            if not self.match("OP", ","):
                break
        self.close("}", tok, "'}' to close the map")
        return MapLit(pairs, tok.line)

    def fstring(self, tok: Token) -> FString:
        parts: list = []
        for part in tok.value:
            if isinstance(part, str):
                parts.append(part)
                continue
            source, line = part
            sub = Parser(tokenize(source, line), self.base_dir)
            expr = sub.expression()
            if not sub.check("EOF"):
                raise TBError(
                    f"I couldn't understand {{{source.strip()}}} in this f-text.", line
                )
            parts.append(expr)
        return FString(parts, tok.line)


def parse(source: str, base_dir: str | None = None) -> Program:
    return Parser(tokenize(source), base_dir).parse_program()


# ---------------------------------------------------------------------------
# Runtime values
# ---------------------------------------------------------------------------


class Env:
    def __init__(self, parent: Env | None = None):
        self.vars: dict = {}
        self.parent = parent

    def find(self, name: str) -> Env | None:
        env = self
        while env is not None:
            if name in env.vars:
                return env
            env = env.parent
        return None

    def all_names(self) -> set[str]:
        names: set[str] = set()
        env = self
        while env is not None:
            names.update(env.vars)
            env = env.parent
        return names


class Function:
    def __init__(self, name, params, body, closure: Env, file=None):
        self.name, self.params, self.body, self.closure = name, params, body, closure
        self.file = file  # the .tb file it was written in (None: the main program)


class Builtin:
    def __init__(self, name, fn, arity: tuple[int, int], doc: str = ""):
        self.name, self.fn, self.arity, self.doc = name, fn, arity, doc


class Module:
    """A library: `import fs` gives a Module whose fields are its functions
    and values. Native modules (tblib/<name>.py) build one in load(api):

        m = Module("greet", "One line about the library.")

        @m.fn("hello", 1, 1, doc="hello(name) -> text\\nSay hi to someone.")
        def hello(line, name):
            return "Hi " + name

        m.value("VERSION", "1.0", doc="The library's version.")
    """

    def __init__(self, name: str, doc: str = ""):
        self.name = name
        self.doc = doc
        self.fields: dict = {}  # name -> value (functions are Builtin objects)
        self.docs: dict = {}    # name -> doc text
        self.path: str | None = None  # the .tb file, for modules written in TB
        self.std = True  # False for your own files: import "helpers.tb"

    @property
    def label(self) -> str:
        if not self.std and self.path:
            return os.path.basename(self.path)
        return f"the {self.name} library"

    def fn(self, name: str, min_args: int = 0, max_args: int | None = None, doc: str = ""):
        """Decorator: register a function. max_args None means "same as
        min_args"; use MANY for any number. The function gets (line, *args)."""
        hi = min_args if max_args is None else max_args

        def register(func):
            self.fields[name] = Builtin(f"{self.name}.{name}", func, (min_args, hi), doc)
            self.docs[name] = doc
            return func

        return register

    def value(self, name: str, value, doc: str = ""):
        """Add a plain value, like math.pi."""
        self.fields[name] = value
        self.docs[name] = doc
        return value

    def public_names(self) -> list[str]:
        return [k for k in self.fields if not k.startswith("_")]

    def __repr__(self) -> str:
        return f"<library {self.name}>"


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


def type_name(value) -> str:
    """The name of a value's type, as used in error messages."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    if isinstance(value, Module):
        return "library"
    if isinstance(value, (Function, Builtin)) or callable(value):
        return "function"
    return "value"


def tb_type(value) -> str:
    """What TB's type(x) returns."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Module):
        return "module"
    return type_name(value)


def to_text(value, nested: bool = False, _seen: frozenset = frozenset()) -> str:
    """How TB shows a value to a person."""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if not nested:
            return value
        # Inside a list or map, show text the way you'd type it, so quotes
        # inside it can't be confused with the end of the text.
        shown = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return f'"{shown}"'
    if isinstance(value, list):
        if id(value) in _seen:
            return "[...]"  # a list that contains itself
        seen = _seen | {id(value)}
        return "[" + ", ".join(to_text(v, True, seen) for v in value) + "]"
    if isinstance(value, dict):
        if id(value) in _seen:
            return "{...}"
        seen = _seen | {id(value)}
        return "{" + ", ".join(
            f"{to_text(k, True, seen)}: {to_text(v, True, seen)}" for k, v in value.items()
        ) + "}"
    if isinstance(value, Function):
        return f"<fn {value.name}>"
    if isinstance(value, Builtin):
        return f"<built-in fn {value.name}>"
    if isinstance(value, Module):
        return f"<library {value.name}>"
    if callable(value):
        return f"<fn {getattr(value, '__name__', 'function')}>"
    return str(value)


def truthy(value) -> bool:
    return value is not None and value is not False


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_whole(value) -> bool:
    return is_number(value) and (isinstance(value, int) or value.is_integer())


def tidy_number(value):
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        return int(value)
    return value


# What num() accepts: 12, -3, +4, 3.5, .5, 2., 1e3, -2.5E-3 (plain ASCII only).
NUMBER_TEXT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)


def text_to_number(value: str, line=None):
    """Turn text into a number like num() does, or raise a friendly TBError."""
    cleaned = value.strip()
    if NUMBER_TEXT.fullmatch(cleaned):
        if len(cleaned) > 400:
            raise TBError(TOO_BIG, line)
        if not any(c in cleaned for c in ".eE"):
            return check_size(int(cleaned), line)
        result = float(cleaned)
        if math.isfinite(result):
            return tidy_number(result)
    raise TBError(f'I can\'t turn "{value}" into a number.', line)


def looks_like_number(value: str) -> bool:
    try:
        text_to_number(value)
        return True
    except TBError:
        return False


def tb_round(value, digits=0, line=None):
    """Round half away from zero (2.5 -> 3), like people do on paper."""
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    if not is_number(value):
        raise TBError(f"round() needs a number, not {type_name(value)}.", line)
    if not is_whole(digits) or not -15 <= digits <= 15:
        raise TBError("round() needs a whole number of digits from -15 to 15.", line)
    digits = int(digits)
    if isinstance(value, int) and digits >= 0:
        return value
    try:
        exact = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
        rounded = exact.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise TBError(TOO_BIG, line) from None
    if digits <= 0:
        return check_size(int(rounded), line)
    return tidy_number(float(rounded))


def map_key(key, line):
    """Check (and tidy) a map key: text or a number."""
    if isinstance(key, str):
        return key
    if is_number(key):
        return tidy_number(key)
    raise TBError(f"Map keys must be text or numbers, not {type_name(key)}.", line)


def missing_key(container: dict, key, line, extra: str = "") -> TBError:
    names = [to_text(k) for k in container]
    if not names:
        has = "The map is empty."
    elif len(names) > 12:
        has = "It has: " + ", ".join(names[:12]) + f", and {len(names) - 12} more."
    else:
        has = "It has: " + ", ".join(names) + "."
    return TBError(f"There's no key {to_text(key, nested=True)} in this map. {has}{extra}", line)


CARE_MESSAGES = [
    "You are doing better than you think.",
    "Drink some water and take a deep breath.",
    "It's okay to rest. Rest is part of the work.",
    "You make the world softer just by being in it.",
    "Be gentle with yourself today.",
    "Whatever happens today, you are loved.",
    "Small steps still move you forward.",
    "You don't have to be perfect to be wonderful.",
    "Remember to eat something nice today.",
    "Your smile is someone's favourite thing.",
    "Proud of you, always.",
    "Take a break, stretch, and look at the sky for a moment.",
]


# ---------------------------------------------------------------------------
# Methods on built-in values: "hi".upper(), xs.push(1), m.keys(), 3.7.round()
# ---------------------------------------------------------------------------

# kind -> name -> (function(interp, line, value, *args), min args, max args, doc)
METHODS: dict[str, dict[str, tuple]] = {"text": {}, "list": {}, "map": {}, "number": {}}

# Method names from other languages -> the TB name.
METHOD_HINTS = {
    "append": "push", "add": "push", "length": "len", "size": "len", "count": "len",
    "strip": "trim", "trimmed": "trim", "toUpperCase": "upper", "toLowerCase": "lower",
    "includes": "contains", "has": "contains", "startswith": "starts_with",
    "startsWith": "starts_with", "endswith": "ends_with", "endsWith": "ends_with",
    "indexOf": "index_of", "index": "index_of", "forEach": "each", "for_each": "each",
    "sorted": "sort", "reversed": "reverse", "remove": "remove_at", "substring": "slice",
    "substr": "slice", "toString": "to_text", "to_string": "to_text", "str": "to_text",
    "isEmpty": "is_empty", "empty": "is_empty", "concat": "merge", "update": "merge",
    "contains_key": "has", "containsKey": "has", "delete": "remove", "splitlines": "lines",
    "format": "to_text", "isdigit": "is_number", "push_back": "push", "keys": "keys",
}


def method(kind: str, name: str, lo: int = 0, hi: int | None = None, doc: str = ""):
    def register(fn):
        METHODS[kind][name] = (fn, lo, lo if hi is None else hi, doc)
        return fn
    return register


def _need_text(line, value, where):
    if not isinstance(value, str):
        raise TBError(f"{where} needs text, not {type_name(value)}.", line)
    return value


def _need_whole(line, value, where):
    if not is_whole(value):
        raise TBError(f"{where} needs a whole number, not {to_text(value, nested=True)}.", line)
    return int(value)


def _need_fn(line, value, where):
    if not isinstance(value, (Function, Builtin)) and not callable(value):
        raise TBError(f"{where} needs a function, not {type_name(value)}.", line)
    return value


def _slice(line, seq, start, end, where):
    s = _need_whole(line, start, where)
    e = None if end is None else _need_whole(line, end, where)
    return seq[s:e]


def sort_values(values, line, where="sort()"):
    if all(is_number(v) for v in values) or all(isinstance(v, str) for v in values):
        return sorted(values)
    raise TBError(f"{where} can only sort a list of all numbers or all text.", line)


def _extreme(values, line, where, pick):
    if not values:
        raise TBError(f"{where} needs at least one value, but the list is empty.", line)
    if not (all(is_number(v) for v in values) or all(isinstance(v, str) for v in values)):
        raise TBError(f"{where} needs all numbers or all text.", line)
    return pick(values)


def _sum(values, line, where="sum()"):
    total = 0
    for v in values:
        if not is_number(v):
            raise TBError(f"{where} can only add up numbers, not {type_name(v)}.", line)
        total = check_size(total + v, line)
    return tidy_number(total) if isinstance(total, float) else total


# -- text -------------------------------------------------------------------

@method("text", "upper", doc="upper() -> text\nThe text in CAPITAL letters.")
def _t_upper(it, line, s):
    return s.upper()


@method("text", "lower", doc="lower() -> text\nThe text in small letters.")
def _t_lower(it, line, s):
    return s.lower()


@method("text", "trim", doc="trim() -> text\nThe text without spaces, tabs or newlines at the start and end.")
def _t_trim(it, line, s):
    return s.strip()


@method("text", "split", 0, 1, doc="split(sep?) -> list\nCut the text at every sep. With no sep, cut at spaces.")
def _t_split(it, line, s, sep=None):
    if sep is None:
        return s.split()
    sep = _need_text(line, sep, "split()")
    return list(s) if sep == "" else s.split(sep)


@method("text", "lines", doc="lines() -> list\nThe text cut into its lines.")
def _t_lines(it, line, s):
    return s.splitlines()


@method("text", "contains", 1, doc="contains(part) -> bool\nTrue if part appears in the text.")
def _t_contains(it, line, s, part):
    return _need_text(line, part, "contains()") in s


@method("text", "starts_with", 1, doc="starts_with(part) -> bool\nTrue if the text begins with part.")
def _t_starts(it, line, s, part):
    return s.startswith(_need_text(line, part, "starts_with()"))


@method("text", "ends_with", 1, doc="ends_with(part) -> bool\nTrue if the text ends with part.")
def _t_ends(it, line, s, part):
    return s.endswith(_need_text(line, part, "ends_with()"))


@method("text", "replace", 2, doc="replace(old, new) -> text\nThe text with every old swapped for new.")
def _t_replace(it, line, s, old, new):
    return s.replace(_need_text(line, old, "replace()"), to_text(new))


@method("text", "find", 1, doc="find(part) -> number\nWhere part first appears (0 is the start), or -1.")
def _t_find(it, line, s, part):
    return s.find(_need_text(line, part, "find()"))


@method("text", "slice", 1, 2, doc="slice(start, end?) -> text\nThe letters from start up to (not including) end. Negative counts from the end.")
def _t_slice(it, line, s, start, end=None):
    return _slice(line, s, start, end, "slice()")


@method("text", "repeat", 1, doc="repeat(n) -> text\nThe text n times in a row.")
def _t_repeat(it, line, s, n):
    return it.binary("*", s, n, line)


@method("text", "is_number", doc="is_number() -> bool\nTrue if num() could turn this text into a number.")
def _t_is_number(it, line, s):
    return looks_like_number(s)


@method("text", "to_number", doc="to_number() -> number\nThe text as a number (like num()).")
def _t_to_number(it, line, s):
    return text_to_number(s, line)


@method("text", "chars", doc="chars() -> list\nA list of the text's letters.")
def _t_chars(it, line, s):
    return list(s)


@method("text", "len", doc="len() -> number\nHow many letters the text has.")
def _t_len(it, line, s):
    return len(s)


# -- list -------------------------------------------------------------------

@method("list", "push", 1, doc="push(value) -> list\nAdd value to the end (changes the list) and give the list back.")
def _l_push(it, line, xs, value):
    xs.append(value)
    return xs


@method("list", "pop", doc="pop() -> value\nTake the last item off the list (changes it) and give it back.")
def _l_pop(it, line, xs):
    if not xs:
        raise TBError("pop() can't take from an empty list.", line)
    return xs.pop()


@method("list", "insert", 2, doc="insert(position, value) -> list\nPut value at position, moving the rest along (changes the list).")
def _l_insert(it, line, xs, index, value):
    i = _need_whole(line, index, "insert()")
    if not -len(xs) <= i <= len(xs):
        raise TBError(
            f"insert() can't put something at position {i}; the list has {len(xs)} "
            f"item{'' if len(xs) == 1 else 's'}.",
            line,
        )
    xs.insert(i, value)
    return xs


@method("list", "remove_at", 1, doc="remove_at(position) -> value\nTake out the item at position (changes the list) and give it back.")
def _l_remove_at(it, line, xs, index):
    return xs.pop(it.check_index(xs, index, line))


@method("list", "contains", 1, doc="contains(value) -> bool\nTrue if the list has value in it.")
def _l_contains(it, line, xs, value):
    return any(tb_equal(x, value) for x in xs)


@method("list", "index_of", 1, doc="index_of(value) -> number\nThe position of value in the list, or -1.")
def _l_index_of(it, line, xs, value):
    for i, x in enumerate(xs):
        if tb_equal(x, value):
            return i
    return -1


@method("list", "join", 0, 1, doc="join(sep?) -> text\nAll the items as one text, with sep between them.")
def _l_join(it, line, xs, sep=""):
    return _need_text(line, sep, "join()").join(to_text(x) for x in xs)


@method("list", "sort", doc="sort() -> list\nA new sorted list (all numbers or all text).")
def _l_sort(it, line, xs):
    return sort_values(xs, line)


@method("list", "sort_by", 1, doc="sort_by(fn) -> list\nA new list sorted by what fn(item) gives back.")
def _l_sort_by(it, line, xs, fn):
    _need_fn(line, fn, "sort_by()")
    keys = [it.call(fn, [x], line) for x in xs]
    sort_values(keys, line, "sort_by()")
    order = sorted(range(len(xs)), key=lambda i: keys[i])
    return [xs[i] for i in order]


@method("list", "reverse", doc="reverse() -> list\nA new list in the opposite order.")
def _l_reverse(it, line, xs):
    return xs[::-1]


@method("list", "map", 1, doc="map(fn) -> list\nA new list of fn(item) for every item.")
def _l_map(it, line, xs, fn):
    _need_fn(line, fn, "map()")
    return [it.call(fn, [x], line) for x in list(xs)]


@method("list", "filter", 1, doc="filter(fn) -> list\nA new list of the items where fn(item) is true.")
def _l_filter(it, line, xs, fn):
    _need_fn(line, fn, "filter()")
    return [x for x in list(xs) if truthy(it.call(fn, [x], line))]


@method("list", "reduce", 2, doc="reduce(fn, start) -> value\nCombine the items: fn(fn(start, first), second) and so on.")
def _l_reduce(it, line, xs, fn, start):
    _need_fn(line, fn, "reduce()")
    acc = start
    for x in list(xs):
        acc = it.call(fn, [acc, x], line)
    return acc


@method("list", "each", 1, doc="each(fn) -> nil\nCall fn(item) for every item.")
def _l_each(it, line, xs, fn):
    _need_fn(line, fn, "each()")
    for x in list(xs):
        it.call(fn, [x], line)
    return None


@method("list", "slice", 1, 2, doc="slice(start, end?) -> list\nA new list of the items from start up to (not including) end.")
def _l_slice(it, line, xs, start, end=None):
    return _slice(line, xs, start, end, "slice()")


@method("list", "first", doc="first() -> value\nThe first item, or nil if the list is empty.")
def _l_first(it, line, xs):
    return xs[0] if xs else None


@method("list", "last", doc="last() -> value\nThe last item, or nil if the list is empty.")
def _l_last(it, line, xs):
    return xs[-1] if xs else None


@method("list", "is_empty", doc="is_empty() -> bool\nTrue if the list has no items.")
def _l_is_empty(it, line, xs):
    return not xs


@method("list", "copy", doc="copy() -> list\nA new list with the same items.")
def _l_copy(it, line, xs):
    return list(xs)


@method("list", "len", doc="len() -> number\nHow many items the list has.")
def _l_len(it, line, xs):
    return len(xs)


@method("list", "sum", doc="sum() -> number\nAll the numbers added up.")
def _l_sum(it, line, xs):
    return _sum(xs, line)


@method("list", "min", doc="min() -> value\nThe smallest item.")
def _l_min(it, line, xs):
    return _extreme(xs, line, "min()", min)


@method("list", "max", doc="max() -> value\nThe biggest item.")
def _l_max(it, line, xs):
    return _extreme(xs, line, "max()", max)


@method("list", "unique", doc="unique() -> list\nA new list without repeats (first ones kept).")
def _l_unique(it, line, xs):
    result: list = []
    for x in xs:
        if not any(tb_equal(x, y) for y in result):
            result.append(x)
    return result


# -- map --------------------------------------------------------------------

@method("map", "keys", doc="keys() -> list\nThe map's keys, in order.")
def _m_keys(it, line, m):
    return list(m.keys())


@method("map", "values", doc="values() -> list\nThe map's values, in order.")
def _m_values(it, line, m):
    return list(m.values())


@method("map", "items", doc="items() -> list\nA list of [key, value] pairs.")
def _m_items(it, line, m):
    return [[k, v] for k, v in m.items()]


@method("map", "has", 1, doc="has(key) -> bool\nTrue if the map has this key.")
def _m_has(it, line, m, key):
    return map_key(key, line) in m


@method("map", "get", 1, 2, doc="get(key, default?) -> value\nThe value for key, or default (nil) if it's missing.")
def _m_get(it, line, m, key, default=None):
    return m.get(map_key(key, line), default)


@method("map", "remove", 1, doc="remove(key) -> value\nTake key out of the map (changes it); gives back its value, or nil.")
def _m_remove(it, line, m, key):
    return m.pop(map_key(key, line), None)


@method("map", "set", 2, doc="set(key, value) -> map\nPut value under key (changes the map) and give the map back.")
def _m_set(it, line, m, key, value):
    m[map_key(key, line)] = value
    return m


@method("map", "merge", 1, doc="merge(other) -> map\nA new map with both maps' keys (other wins).")
def _m_merge(it, line, m, other):
    if not isinstance(other, dict):
        raise TBError(f"merge() needs a map, not {type_name(other)}.", line)
    return {**m, **other}


@method("map", "copy", doc="copy() -> map\nA new map with the same keys and values.")
def _m_copy(it, line, m):
    return dict(m)


@method("map", "len", doc="len() -> number\nHow many keys the map has.")
def _m_len(it, line, m):
    return len(m)


@method("map", "is_empty", doc="is_empty() -> bool\nTrue if the map has no keys.")
def _m_is_empty(it, line, m):
    return not m


# -- number -----------------------------------------------------------------

@method("number", "round", 0, 1, doc="round(digits?) -> number\nRounded to digits after the dot (default 0). 2.5 rounds to 3.")
def _n_round(it, line, x, digits=0):
    return tb_round(x, digits, line)


@method("number", "floor", doc="floor() -> number\nThe whole number at or below it.")
def _n_floor(it, line, x):
    return check_size(math.floor(x), line)


@method("number", "ceil", doc="ceil() -> number\nThe whole number at or above it.")
def _n_ceil(it, line, x):
    return check_size(math.ceil(x), line)


@method("number", "abs", doc="abs() -> number\nThe number without its minus sign.")
def _n_abs(it, line, x):
    return abs(x)


@method("number", "to_text", doc="to_text() -> text\nThe number as text (like str()).")
def _n_to_text(it, line, x):
    return to_text(x)


METHOD_KIND_LABEL = {"text": "Text", "list": "A list", "map": "A map", "number": "A number"}


def method_kind(value) -> str | None:
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    if is_number(value):
        return "number"
    return None


# ---------------------------------------------------------------------------
# Libraries (the standard library lives in tblib/ next to this file)
# ---------------------------------------------------------------------------


def library_dirs() -> list[str]:
    """Folders that may hold the standard library (tblib)."""
    dirs = []
    bundle = getattr(sys, "_MEIPASS", None)  # inside the PyInstaller one-file app
    if bundle:
        dirs.append(os.path.join(bundle, "tblib"))
    here = globals().get("__file__")
    if here:
        dirs.append(os.path.join(os.path.dirname(os.path.abspath(here)), "tblib"))
    else:  # tb.py was run from text (some browser setups): look next to us
        dirs.append(os.path.join(os.getcwd(), "tblib"))
    seen: list[str] = []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.append(d)
    return seen


def _tblib_package():
    """Import (once) and return the tblib package that belongs to this TB.
    A different package called 'tblib' (there is one on PyPI) is ignored."""
    pkg = sys.modules.get("tblib")
    if pkg is not None and getattr(pkg, "TB_STDLIB", False):
        return pkg
    for folder in library_dirs():
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
    for folder in library_dirs():
        path = os.path.join(folder, name + ".tb")
        if os.path.isfile(path):
            return path
    return None


def list_libraries() -> list[str]:
    """Names of every standard library (what `import name` accepts)."""
    names: set[str] = set()
    for folder in library_dirs():
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


def tb_source_docs(source: str) -> tuple[str, dict]:
    """Docs for a library written in TB: the comment lines at the top of the
    file describe the library, and the comment lines right above a top-level
    `fn name(...)` describe that function."""
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
        match = re.match(r"fn\s+([^\W\d]\w*)\s*\(([^)]*)\)", text)
        if not match:
            continue
        comment: list[str] = []
        j = number - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            comment.insert(0, lines[j].strip().lstrip("#").strip())
            j -= 1
        if j < 0 and comment == head:
            comment = []  # that comment is the library's own description
        params = ", ".join(p.strip() for p in match.group(2).split(",") if p.strip())
        docs[match.group(1)] = "\n".join([f"{match.group(1)}({params})"] + comment)
    return "\n".join(head).strip(), docs


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


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
        self._api = None
        for name, fn, arity in self._builtins():
            self.builtins.vars[name] = Builtin(name, fn, arity, BUILTIN_DOCS.get(name, ""))

    # -- built-in functions -----------------------------------------------

    def _builtins(self):
        def say(line, *args):
            self.output(" ".join(to_text(a) for a in args))
            return None

        def length(line, value):
            if isinstance(value, (str, list, dict)):
                return len(value)
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
            if not isinstance(value, dict):
                raise TBError(f"keys() needs a map, not {type_name(value)}.", line)
            return list(value.keys())

        def whole(line, value):
            if isinstance(value, str):
                value = text_to_number(value, line)
            if not is_number(value):
                raise TBError(f"int() needs a number or text, not {type_name(value)}.", line)
            return check_size(int(value), line)

        def rounded(line, value, digits=0):
            return tb_round(value, digits, line)

        def pick(name, chooser):
            def fn(line, *args):
                values = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
                return _extreme(values, line, f"{name}()", chooser)
            return fn

        def total(line, values):
            if not isinstance(values, list):
                raise TBError(f"sum() needs a list, not {type_name(values)}.", line)
            return _sum(values, line)

        def sort_list(line, values):
            if not isinstance(values, list):
                raise TBError(f"sorted() needs a list, not {type_name(values)}.", line)
            return sort_values(values, line, "sorted()")

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

    # -- running ----------------------------------------------------------

    def run(self, program: Program):
        try:
            self.exec_block(program.body, self.globals)
        except ReturnSignal:
            pass

    def exec_block(self, body, env: Env):
        for stmt in body:
            self.execute(stmt, env)

    def execute(self, node: Node, env: Env):
        kind = type(node)
        if kind is ExprStmt:
            self.evaluate(node.expr, env)
        elif kind is Let:
            env.vars[node.name] = self.evaluate(node.value, env)
        elif kind is Assign:
            self.assign(node, env)
        elif kind is FnDecl:
            env.vars[node.name] = Function(node.name, node.params, node.body, env, self.current_file)
        elif kind is Return:
            value = None if node.value is None else self.evaluate(node.value, env)
            raise ReturnSignal(value)
        elif kind is If:
            for cond, block in node.branches:
                if truthy(self.evaluate(cond, env)):
                    self.exec_block(block, Env(env))
                    return
            if node.otherwise is not None:
                self.exec_block(node.otherwise, Env(env))
        elif kind is While:
            while truthy(self.evaluate(node.cond, env)):
                try:
                    self.exec_block(node.body, Env(env))
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        elif kind is For:
            items = self.evaluate(node.iterable, env)
            if isinstance(items, str):
                items = list(items)
            elif isinstance(items, dict):
                items = list(items.keys())
            elif not isinstance(items, list):
                raise TBError(
                    f"'for ... in' needs a list or text, not {type_name(items)}.", node.line
                )
            for item in list(items):
                scope = Env(env)
                scope.vars[node.name] = item
                try:
                    self.exec_block(node.body, scope)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        elif kind is Break:
            raise BreakSignal()
        elif kind is Continue:
            raise ContinueSignal()
        elif kind is Import:
            env.vars[node.alias] = self.import_node(node)
        elif kind is Try:
            try:
                self.exec_block(node.body, Env(env))
            except TBError as err:
                scope = Env(env)
                if node.name is not None:
                    scope.vars[node.name] = err.to_map()
                self.exec_block(node.handler, scope)
        elif kind is Throw:
            raise self.make_throw(self.evaluate(node.value, env), node.line)
        else:  # pragma: no cover - parser never produces anything else
            raise TBError("I don't know how to run this.", node.line)

    def make_throw(self, value, line) -> TBError:
        if isinstance(value, dict):
            message = value.get("message", value)
            fields = {k: v for k, v in value.items() if k not in ("message", "line", "file")}
            where = value.get("line")
            return TBError(to_text(message), int(where) if is_whole(where) else line, fields)
        return TBError(to_text(value), line)

    def assign(self, node: Assign, env: Env):
        target = node.target
        if isinstance(target, Name):
            owner = env.find(target.name)
            if owner is None:
                raise TBError(
                    f"'{target.name}' doesn't exist yet. Use 'let {target.name} = ...' first.",
                    node.line,
                )
            value = self.evaluate(node.value, env)
            if node.op is not None:
                value = self.binary(node.op, owner.vars[target.name], value, node.line)
            owner.vars[target.name] = value
            return
        if isinstance(target, Dot):
            obj = self.evaluate(target.target, env)
            if isinstance(obj, Module):
                raise TBError(f"You can't change {obj.label}'s '{target.name}'.", node.line)
            if not isinstance(obj, dict):
                raise TBError(
                    f"Only maps have fields you can set, not {type_name(obj)}.", node.line
                )
            value = self.evaluate(node.value, env)
            if node.op is not None:
                if target.name not in obj:
                    raise missing_key(obj, target.name, node.line)
                value = self.binary(node.op, obj[target.name], value, node.line)
            obj[target.name] = value
            return
        container = self.evaluate(target.target, env)
        index = self.evaluate(target.index, env)
        if isinstance(container, dict):
            key = map_key(index, target.line)
            value = self.evaluate(node.value, env)
            if node.op is not None:
                if key not in container:
                    raise missing_key(container, key, node.line)
                value = self.binary(node.op, container[key], value, node.line)
            container[key] = value
            return
        i = self.check_index(container, index, target.line)
        if isinstance(container, str):
            raise TBError("Text can't be changed one letter at a time.", node.line)
        value = self.evaluate(node.value, env)
        if node.op is not None:
            value = self.binary(node.op, container[i], value, node.line)
        container[i] = value

    def check_index(self, container, index, line) -> int:
        if not isinstance(container, (list, str)):
            raise TBError(f"Only lists and text can be indexed, not {type_name(container)}.", line)
        kind = "list" if isinstance(container, list) else "text"
        if not is_number(index) or (isinstance(index, float) and not index.is_integer()):
            raise TBError(f"A position in {'a list' if kind == 'list' else 'text'} must be a whole number.", line)
        i = int(index)
        size = len(container)
        if i < -size or i >= size:
            if size == 0:
                raise TBError(
                    f"The {kind} is empty, so there is nothing at position {i}.", line
                )
            raise TBError(
                f"Position {i} is outside the {kind}. It has {size} "
                f"{'item' if kind == 'list' else 'letter'}{'' if size == 1 else 's'}, "
                f"numbered 0 to {size - 1}.",
                line,
            )
        return i

    def unknown_name(self, name: str, env: Env, line) -> TBError:
        if name in OTHER_LANGUAGE_NAMES:
            hint = [OTHER_LANGUAGE_NAMES[name]]
        else:
            hint = difflib.get_close_matches(name, env.all_names(), n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        if not hint and name in list_libraries():
            extra = f" Did you forget 'import {name}' at the top?"
        return TBError(f"I don't know what '{name}' is.{extra}", line)

    def evaluate(self, node: Node, env: Env):
        kind = type(node)
        if kind is Literal:
            return node.value
        if kind is Name:
            owner = env.find(node.name)
            if owner is None:
                raise self.unknown_name(node.name, env, node.line)
            return owner.vars[node.name]
        if kind is ListLit:
            return [self.evaluate(item, env) for item in node.items]
        if kind is Unary:
            value = self.evaluate(node.operand, env)
            if node.op == "-":
                if not is_number(value):
                    raise TBError(f"I can only make numbers negative, not {type_name(value)}.", node.line)
                return -value
            return not truthy(value)
        if kind is Logical:
            left = self.evaluate(node.left, env)
            if node.op == "or":
                return left if truthy(left) else self.evaluate(node.right, env)
            return self.evaluate(node.right, env) if truthy(left) else left
        if kind is Binary:
            left = self.evaluate(node.left, env)
            right = self.evaluate(node.right, env)
            try:
                return self.binary(node.op, left, right, node.line)
            except OverflowError:
                raise TBError("That number got too big for me to work with.", node.line) from None
        if kind is Call:
            callee = self.evaluate(node.callee, env)
            args = [self.evaluate(a, env) for a in node.args]
            return self.call(callee, args, node.line)
        if kind is Index:
            container = self.evaluate(node.target, env)
            index = self.evaluate(node.index, env)
            if isinstance(container, dict):
                key = map_key(index, node.line)
                if key not in container:
                    raise missing_key(container, key, node.line)
                return container[key]
            return container[self.check_index(container, index, node.line)]
        if kind is Dot:
            return self.get_field(self.evaluate(node.target, env), node.name, node.line)
        if kind is MapLit:
            result = {}
            for key_node, value_node in node.pairs:
                if isinstance(key_node, Name) and env.find(key_node.name) is None:
                    raise TBError(
                        f"I don't know what '{key_node.name}' is. Map keys that are words "
                        f'need quotes: {{"{key_node.name}": ...}}',
                        key_node.line,
                    )
                key = map_key(self.evaluate(key_node, env), key_node.line)
                result[key] = self.evaluate(value_node, env)
            return result
        if kind is FString:
            return "".join(
                part if isinstance(part, str) else to_text(self.evaluate(part, env))
                for part in node.parts
            )
        if kind is FnExpr:
            return Function("anonymous", node.params, node.body, env, self.current_file)
        raise TBError("I don't know how to work this out.", node.line)  # pragma: no cover

    # -- fields and methods -------------------------------------------------

    def get_field(self, obj, name: str, line):
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            if name in METHODS["map"]:
                return self.bound_method("map", obj, name)
            hint = difflib.get_close_matches(name, [k for k in obj if isinstance(k, str)], n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            raise missing_key(obj, name, line, extra)
        if isinstance(obj, Module):
            if name in obj.fields and not name.startswith("_"):
                return obj.fields[name]
            names = obj.public_names()
            hint = difflib.get_close_matches(name, names, n=1)
            extra = f" Did you mean '{hint[0]}'?" if hint else ""
            listing = f" It has: {', '.join(sorted(names))}." if names else ""
            label = obj.label[0].upper() + obj.label[1:] if obj.std else obj.label
            raise TBError(f"{label} has nothing called '{name}'.{extra}{listing}", line)
        kind = method_kind(obj)
        if kind is None:
            if obj is None:
                raise TBError(
                    f"I can't use '.{name}' on nil. (Is the value missing, or not made yet?)", line
                )
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
        plural = {"text": "Text", "list": "List", "map": "Map", "number": "Number"}[kind]
        raise TBError(
            f"{label} doesn't have a method called '{name}'.{extra} "
            f"{plural} methods: {', '.join(names)}.",
            line,
        )

    def bound_method(self, kind: str, obj, name: str) -> Builtin:
        fn, lo, hi, doc = METHODS[kind][name]
        return Builtin(name, lambda line, *args: fn(self, line, obj, *args), (lo, hi), doc)

    # -- operators ------------------------------------------------------------

    def binary(self, op, a, b, line):
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
            raise TBError(f"I can't add {type_name(a)} and {type_name(b)}.", line)
        if op in ("<", "<=", ">", ">="):
            if not ((is_number(a) and is_number(b)) or (isinstance(a, str) and isinstance(b, str))):
                raise TBError(f"I can't compare {type_name(a)} with {type_name(b)}.", line)
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        if op == "*" and (isinstance(a, str) or isinstance(b, str)):
            text, times = (a, b) if isinstance(a, str) else (b, a)
            if not is_number(times) or times < 0 or (
                isinstance(times, float) and not times.is_integer()
            ):
                raise TBError(
                    "Text can only be repeated a whole number of times (0 or more), "
                    f"not {to_text(times, nested=True)}.",
                    line,
                )
            if len(text) * int(times) > 100_000_000:
                raise TBError("That text would be too long for me to make.", line)
            return text * int(times)
        if not (is_number(a) and is_number(b)):
            verb = {"-": "subtract", "*": "multiply", "/": "divide", "%": "find the remainder of"}[op]
            raise TBError(f"I can only {verb} numbers, not {type_name(a)} and {type_name(b)}.", line)
        try:
            if op == "-":
                return check_size(a - b, line)
            if op == "*":
                return check_size(a * b, line)
            if b == 0:
                raise TBError("You can't divide by zero.", line)
            if op == "/":
                return tidy_number(check_size(a / b, line))
            return a % b
        except OverflowError:
            raise TBError(TOO_BIG, line) from None

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
        raise TBError(f"'in' needs a list, text or a map on its right, not {type_name(container)}.", line)

    # -- calls ----------------------------------------------------------------

    def call(self, callee, args, line):
        if isinstance(callee, Builtin):
            lo, hi = callee.arity
            if not lo <= len(args) <= hi:
                raise TBError(arity_message(callee.name, lo, hi, len(args)), line)
            try:
                return callee.fn(line, *args)
            except (TBError, ReturnSignal, BreakSignal, ContinueSignal, RecursionError, MemoryError):
                raise
            except Exception as err:  # a library bug: never show a Python traceback
                raise TBError(f"Something went wrong inside {callee.name}(): {err}", line) from None
        if not isinstance(callee, Function):
            if callable(callee) and not isinstance(callee, (Module, type)):
                try:
                    return callee(*args)
                except (TBError, ReturnSignal, RecursionError, MemoryError):
                    raise
                except Exception as err:
                    raise TBError(f"Something went wrong inside a function: {err}", line) from None
            article = {
                "number": "a number", "list": "a list", "map": "a map", "library": "a library",
            }.get(type_name(callee), type_name(callee))
            raise TBError(f"I can only call functions, not {article}.", line)
        if len(args) != len(callee.params):
            n = len(callee.params)
            raise TBError(arity_message(callee.name, n, n, len(args)), line)
        scope = Env(callee.closure)
        for name, value in zip(callee.params, args):
            scope.vars[name] = value
        if self.depth >= MAX_DEPTH:
            who = "This function" if callee.name == "anonymous" else f"'{callee.name}'"
            raise TBError(
                f"{who} called itself too many times ({MAX_DEPTH} deep). "
                "Is there a way for it to stop?",
                line,
            )
        self.depth += 1
        outer_file, self.current_file = self.current_file, callee.file
        try:
            self.exec_block(callee.body, scope)
        except ReturnSignal as signal:
            return signal.value
        except TBError as err:
            if err.file is UNSET:
                err.file = callee.file  # the innermost function knows the file
            raise
        finally:
            self.depth -= 1
            self.current_file = outer_file
        return None

    # -- modules --------------------------------------------------------------

    def import_node(self, node: Import) -> Module:
        if node.is_file:
            base = node.base_dir or self.base_dir or os.getcwd()
            path = os.path.abspath(os.path.join(base, node.target))
            return self.load_tb_file(path, node.target, node.line)
        return self.load_library(node.target, node.line)

    def load_library(self, name: str, line=None) -> Module:
        """Import a standard library by name (cached per interpreter)."""
        key = "lib:" + name
        if key in self.modules:
            return self.modules[key]
        spec = None if name.startswith("_") or not name.isidentifier() else _native_spec(name)
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
        raise self.unknown_library(name, line)

    def unknown_library(self, name: str, line) -> TBError:
        libraries = list_libraries()
        hint = difflib.get_close_matches(name, libraries, n=1)
        extra = f" Did you mean '{hint[0]}'?" if hint else ""
        mine = os.path.join(self.base_dir or os.getcwd(), name + ".tb")
        if os.path.isfile(mine):
            extra = f' To use your own file, put its name in quotes: import "{name}.tb"'
        listing = f" Libraries: {', '.join(libraries)}." if libraries else ""
        return TBError(f"There's no library called '{name}'.{extra}{listing}", line)

    def load_tb_file(self, path: str, shown: str, line=None, *, key=None, name=None) -> Module:
        """Run a .tb file once and give back its top-level names as a Module."""
        key = key or path
        if key in self.modules:
            return self.modules[key]
        if key in self.loading:
            start = self.loading.index(key)
            chain = [os.path.basename(k[4:] + ".tb" if k.startswith("lib:") else k)
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
        env = Env(self.builtins)
        module.fields = env.vars  # live: the module's own top-level names
        self.loading.append(key)
        outer_file, self.current_file = self.current_file, path
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
        self.modules[key] = module
        return module


def tb_equal(a, b, _comparing: frozenset = frozenset()) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if is_number(a) and is_number(b):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, (list, dict)):
        if a is b:
            return True
        pair = (id(a), id(b))
        if pair in _comparing:
            return True  # containers that contain themselves: no difference found so far
        if len(a) != len(b):
            return False
        inner = _comparing | {pair}
        if isinstance(a, dict):
            if a.keys() != b.keys():
                return False
            return all(tb_equal(a[k], b[k], inner) for k in a)
        return all(tb_equal(x, y, inner) for x, y in zip(a, b))
    return a == b


def arity_message(name, lo, hi, got) -> str:
    if lo == hi:
        want = f"{lo} input" + ("" if lo == 1 else "s")
    elif hi >= MANY:
        want = f"at least {lo} input" + ("" if lo == 1 else "s")
    else:
        want = f"{lo} to {hi} inputs"
    return f"{name}() needs {want}, but got {got}."


BUILTIN_DOCS = {
    "say": "say(...)\nShow values on the screen, with spaces between them.",
    "ask": "ask(prompt?) -> text\nShow prompt, wait for the person to type, and give back what they typed.",
    "len": "len(value) -> number\nHow many letters (text), items (list) or keys (map).",
    "str": "str(value) -> text\nThe value as text.",
    "num": "num(text) -> number\nThe text as a number, like num(\"42\").",
    "range": "range(end) / range(start, end, step?) -> list\nA list of whole numbers from start up to (not including) end.",
    "push": "push(list, value) -> list\nAdd value to the end of the list.",
    "care": "care(name?)\nShow a kind message.",
    "type": "type(value) -> text\nOne of: number, text, bool, nil, list, map, function, module.",
    "keys": "keys(map) -> list\nThe map's keys, in order.",
    "int": "int(value) -> number\nThe whole-number part (3.9 gives 3). Text is turned into a number first.",
    "round": "round(number, digits?) -> number\nRounded to digits after the dot (default 0). 2.5 rounds to 3.",
    "min": "min(list) / min(a, b, ...) -> value\nThe smallest value.",
    "max": "max(list) / max(a, b, ...) -> value\nThe biggest value.",
    "sum": "sum(list) -> number\nAll the numbers in the list added up.",
    "sorted": "sorted(list) -> list\nA new sorted list (all numbers or all text).",
    "exit": "exit(code?)\nStop the program now. 0 (the default) means all went well.",
}


# ---------------------------------------------------------------------------
# Public API (also used by the browser playground through Pyodide)
# ---------------------------------------------------------------------------


# Python needs room for TB's own recursion. From Python 3.11 on, Python calls
# don't use the C stack, so a high limit is safe; 3.10 keeps a modest one.
_PYTHON_RECURSION_LIMIT = 60000 if sys.version_info >= (3, 11) else 20000


def _guarded(work):
    """Run work() with enough Python recursion room, turning a Python
    RecursionError into a friendly TBError."""
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, _PYTHON_RECURSION_LIMIT))
    try:
        return work()
    except RecursionError:
        raise TBError(
            "The program went too deep (too many functions inside functions, "
            "or a list inside itself)."
        ) from None
    finally:
        sys.setrecursionlimit(old_limit)


def run(
    source: str,
    output=None,
    input_fn=None,
    interpreter: Interpreter | None = None,
    rng: random.Random | None = None,
    *,
    path: str | None = None,
    args: list | None = None,
):
    """Run TB source code. Raises TBError on mistakes in the program.
    Pass a seeded random.Random as rng to make care() predictable.
    path: the file the source came from (imports are relative to it).
    args: the program's command-line arguments (os.args)."""
    interp = interpreter or Interpreter(output=output, input_fn=input_fn, rng=rng)
    if path is not None:
        interp.script_path = os.path.abspath(path)
        interp.base_dir = os.path.dirname(interp.script_path)
        # The main file counts as "being imported", so a file that imports
        # it back is reported as a circle instead of running it twice.
        interp.loading = [interp.script_path]
    if args is not None:
        interp.argv = list(args)
    _guarded(lambda: interp.run(parse(source, interp.base_dir)))
    return interp


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

HELP = f"""TB {__version__} - a small, friendly programming language.

Usage:
  tb FILE.tb [args...]   run a TB program (it sees the args as os.args)
  tb                     start the interactive prompt (type 'exit' to leave)
  tb doc                 list the standard libraries
  tb doc NAME            show what a library can do (try: tb doc fs)
  tb doc builtins        show the built-in functions and methods
  tb test [FOLDER]       run every *_test.tb file in FOLDER (default: here)
  tb --version           show the version
  tb --help              show this help

Example program (save as hello.tb, then run: tb hello.tb):
  let name = ask("What's your name? ")
  say(f"Hello, {{name}}!")
  care(name)
"""


def repl() -> int:
    print(f"TB {__version__} - type some code, or 'exit' to leave.")
    interp = Interpreter()
    buffer: list[str] = []
    while True:
        try:
            line = input("... " if buffer else "tb> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not buffer and line.strip() in ("exit", "quit", "exit()", "quit()"):
            return 0
        buffer.append(line)
        source = "\n".join(buffer)
        if open_brackets(source) > 0:
            continue
        buffer = []
        try:
            _guarded(lambda: _repl_run(interp, source))
        except TBError as err:
            print(err)
        except TBExit as stop:
            return stop.code if isinstance(stop.code, int) else 0
        except KeyboardInterrupt:
            previous = ignore_ctrl_c()
            print("Stopped.")
            restore_ctrl_c(previous)
        except Exception as err:  # never show a Python traceback
            print(crash_message(err))


def ignore_ctrl_c():
    """Ignore further Ctrl+C presses. The PyInstaller one-file exe forwards a
    second Ctrl+C that would otherwise land inside our handler and show a
    traceback. Returns the old handler (or None if this isn't possible, e.g.
    in Pyodide or outside the main thread)."""
    try:
        import signal

        return signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        return None


def restore_ctrl_c(previous, settle: float = 0.2) -> None:
    """Wait briefly for a forwarded Ctrl+C to arrive (and be ignored), then
    let Ctrl+C work again."""
    if previous is None:
        return
    try:
        import signal
        import time

        time.sleep(settle)
        signal.signal(signal.SIGINT, previous)
    except Exception:
        pass


def _repl_run(interp: Interpreter, source: str) -> None:
    program = parse(source)
    # Show the value of a lone expression, like a calculator.
    if len(program.body) == 1 and isinstance(program.body[0], ExprStmt):
        value = interp.evaluate(program.body[0].expr, interp.globals)
        if value is not None:
            print(to_text(value, nested=True))
    else:
        interp.run(program)


def open_brackets(source: str) -> int:
    """How many brackets are still open (used by the REPL to wait for more lines)."""
    try:
        tokens = tokenize(source)
    except TBError:
        return 0
    depth = 0
    for tok in tokens:
        if tok.kind == "OP" and tok.value in "({[":
            depth += 1
        elif tok.kind == "OP" and tok.value in ")}]":
            depth -= 1
    return depth


def crash_message(err: BaseException) -> str:
    if isinstance(err, MemoryError):
        return "Oops! The program needed more memory than this computer has."
    return (
        "Oops! Something went wrong inside TB itself. This is not your fault.\n"
        f"(If it keeps happening, share this: {type(err).__name__}: {err})"
    )


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


# -- Windows: keep the window open -----------------------------------------


def owns_console_window() -> bool:
    """True on Windows when TB has its own console window, meaning it was
    started by double-clicking tb.exe or dropping a .tb file on it. Then the
    window would vanish the moment TB finishes, so we wait for Enter first.
    Safe everywhere: any problem means False."""
    if sys.platform != "win32":
        return False
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        import ctypes

        processes = (ctypes.c_uint32 * 8)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(processes, 8)
    except Exception:
        return False
    # A PyInstaller one-file exe is two processes (a small launcher plus TB);
    # the one-folder build TB ships (tb.exe next to _internal) is one.
    alone = 2 if _is_onefile() else 1
    return 0 < count <= alone


def _is_onefile() -> bool:
    """True inside a PyInstaller one-file exe, which unpacks itself to a temp
    folder (sys._MEIPASS) instead of running from next to the executable."""
    if not getattr(sys, "frozen", False):
        return False
    bundle = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", "")))
    home = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    return not bundle.startswith(home)


class _WindowedErrors:
    """tbw.exe (used by Start-menu shortcuts) has no console, so anything TB
    would print as an error is collected and shown in one dialog at exit."""

    def __init__(self):
        self.parts: list[str] = []

    def write(self, text):
        self.parts.append(str(text))
        return len(text)

    def flush(self):
        pass

    def show(self):
        text = "".join(self.parts).strip()
        if not text:
            return
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("TB", text[-4000:])
            root.destroy()
        except Exception:
            pass


def pause_before_closing() -> None:
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def _read_program(path: str):
    """Read a .tb file for running; on failure print why and return None."""
    try:
        return read_source(path)
    except FileNotFoundError:
        message = f"Oops! I couldn't find the file '{path}'. Is the name spelled right?"
        if os.path.isfile(path + ".txt"):
            message = (
                f"Oops! I couldn't find '{path}', but I found '{path}.txt'.\n"
                f"Did Notepad save it as '{path}.txt'? Rename it so it ends in .tb "
                "(in File Explorer, turn on View > File name extensions to see the .txt)."
            )
        print(message, file=sys.stderr)
    except OSError as err:
        print(f"Oops! I couldn't open '{path}': {err.strerror or err}.", file=sys.stderr)
    return None


def checks_summary(state: dict) -> str:
    passed, failed = state.get("passed", 0), state.get("failed", 0)
    checks = passed + failed
    if failed:
        return f"{failed} of {checks} check{'' if checks == 1 else 's'} failed."
    return f"All {checks} check{'' if checks == 1 else 's'} passed."


def run_file(path: str, args: list | None = None) -> int:
    source = _read_program(path)
    if source is None:
        return 1
    try:
        interp = run(source, path=path, args=list(args or []))
    except TBError as err:
        print(err, file=sys.stderr)
        return 1
    except TBExit as stop:
        return stop.code if isinstance(stop.code, int) else 0
    except KeyboardInterrupt:
        ignore_ctrl_c()
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as err:  # never show a Python traceback
        print(crash_message(err), file=sys.stderr)
        return 1
    state = getattr(interp, "test_state", None)
    if isinstance(state, dict) and state.get("passed", 0) + state.get("failed", 0):
        print(checks_summary(state))
        return 1 if state.get("failed") else 0
    return 0


# -- tb doc -------------------------------------------------------------------


def describe_library(name: str) -> dict:
    """Everything `tb doc NAME` shows, as data (the website uses this too):
    {"name", "doc", "functions": [{"name", "signature", "doc"}],
     "values": [{"name", "value", "doc"}]}. Raises TBError if unknown."""
    interp = Interpreter(output=lambda text: None, input_fn=lambda prompt: "")
    module = interp.load_library(name)
    functions, values = [], []
    for field in module.public_names():
        value = module.fields[field]
        doc = module.docs.get(field) or getattr(value, "doc", "") or ""
        if isinstance(value, (Builtin, Function)) or callable(value):
            first, _, rest = doc.partition("\n")
            if not first.startswith(field + "("):
                first, rest = f"{field}(...)", doc
            functions.append({"name": field, "signature": first.strip(), "doc": rest.strip()})
        elif not isinstance(value, Module):
            values.append({"name": field, "value": to_text(value, nested=True), "doc": doc.strip()})
    return {"name": module.name, "doc": module.doc.strip(), "functions": functions, "values": values}


def describe_builtins() -> dict:
    """The built-in functions and the methods of text, lists, maps and numbers."""
    return {
        "functions": [
            {"name": n, "signature": d.partition("\n")[0], "doc": d.partition("\n")[2]}
            for n, d in BUILTIN_DOCS.items()
        ],
        "methods": {
            kind: [
                {"name": n, "signature": spec[3].partition("\n")[0], "doc": spec[3].partition("\n")[2]}
                for n, spec in sorted(table.items())
            ]
            for kind, table in METHODS.items()
        },
    }


METHOD_EXAMPLES = {"text": '"hi".upper()', "list": "xs.push(1)", "map": "m.keys()", "number": "3.7.round()"}


def _print_entry(signature: str, doc: str) -> None:
    print("  " + signature)
    for text in doc.splitlines():
        print("      " + text if text.strip() else "")


def first_sentence(text: str) -> str:
    text = text.strip().partition("\n")[0]
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text


def shown_path(path: str) -> str:
    try:
        rel = os.path.relpath(path)
    except ValueError:  # Windows: path is on another drive
        return path
    return path if rel.startswith("..") else rel


def doc_command(args: list) -> int:
    if not args:
        names = list_libraries()
        print(f"TB {__version__} standard library. Use one with: import NAME")
        print()
        width = max((len(n) for n in names), default=4)
        for name in names:
            try:
                about = first_sentence(describe_library(name)["doc"])
            except TBError as err:
                about = f"(can't load: {err.message})"
            print(f"  {name.ljust(width)}  {about}")
        print()
        print("See one library with: tb doc NAME   (built-in functions: tb doc builtins)")
        return 0
    name = args[0]
    if name in ("builtins", "builtin", "methods"):
        info = describe_builtins()
        print("Built-in functions (no import needed):")
        print()
        for entry in info["functions"]:
            _print_entry(entry["signature"], entry["doc"])
        for kind, entries in info["methods"].items():
            print()
            print(f"{kind.capitalize()} methods (like {METHOD_EXAMPLES[kind]}):")
            print()
            for entry in entries:
                _print_entry(entry["signature"], entry["doc"])
        return 0
    try:
        info = describe_library(name)
    except TBError as err:
        print(err, file=sys.stderr)
        return 1
    print(f"{info['name']} - {info['doc']}" if info["doc"] else info["name"])
    print(f"Use it with: import {info['name']}")
    if info["values"]:
        print()
        for entry in info["values"]:
            _print_entry(f"{info['name']}.{entry['name']} = {entry['value']}", entry["doc"])
    if info["functions"]:
        print()
        for entry in info["functions"]:
            _print_entry(f"{info['name']}.{entry['signature']}", entry["doc"])
    return 0


# -- tb test ------------------------------------------------------------------

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".pytest_cache"}


def find_test_files(target: str) -> list[str]:
    if os.path.isfile(target):
        return [target]
    found = []
    for folder, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith("_test.tb"):
                found.append(os.path.join(folder, name))
    return found


def run_tests(args: list) -> int:
    target = args[0] if args else "."
    if not os.path.exists(target):
        print(f"Oops! I couldn't find '{target}'.", file=sys.stderr)
        return 1
    files = find_test_files(target)
    if not files:
        print(f"No test files found in '{target}'. Test files end in _test.tb, like math_test.tb.")
        return 1
    totals = {"passed": 0, "failed": 0}
    bad_files = 0
    for path in files:
        print(shown_path(path))
        source = _read_program(path)
        interp = Interpreter()
        interp.test_state = {"passed": 0, "failed": 0}
        crashed = source is None
        if source is not None:
            try:
                run(source, interpreter=interp, path=path)
            except TBError as err:
                print(f"  {err}")
                crashed = True
            except TBExit as stop:
                crashed = stop.code not in (0, None)
                if crashed:
                    print(f"  The test file stopped with exit({stop.code}).")
            except KeyboardInterrupt:
                print("\nStopped.", file=sys.stderr)
                return 130
            except Exception as err:  # never show a Python traceback
                print("  " + crash_message(err))
                crashed = True
        state = interp.test_state
        totals["passed"] += state["passed"]
        totals["failed"] += state["failed"]
        if crashed or state["failed"]:
            bad_files += 1
        if crashed:
            print("  The file stopped early because of the error above.")
        print(f"  {checks_summary(state)}" if state["passed"] + state["failed"] else "  (no checks)")
    print()
    count = len(files)
    files_text = f"{count} test file{'' if count == 1 else 's'}"
    if bad_files:
        print(f"FAILED: {totals['failed']} failed, {totals['passed']} passed, "
              f"in {bad_files} of {files_text}.")
        return 1
    print(f"OK: {totals['passed']} checks passed in {files_text}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # A windowed build (tbw.exe) has no console at all.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        import atexit

        sys.stderr = _WindowedErrors()
        atexit.register(sys.stderr.show)
    # `tb prog.tb | head -1` should just stop quietly when the reader leaves.
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, OSError):
        pass  # Windows has no SIGPIPE; not the main thread
    # Never crash on a console that can't show characters like the heart.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if not args:
        return repl()
    first = args[0]
    if first in ("-h", "--help"):
        print(HELP)
        return 0
    if first in ("-V", "--version"):
        print(f"TB {__version__}")
        return 0
    if first == "doc" and not os.path.isfile(first):
        return doc_command(args[1:])
    if first == "test" and not os.path.isfile(first):
        return run_tests(args[1:])
    if first.startswith("-") and len(first) > 1:
        print(f"Oops! I don't know the option '{first}'. See: tb --help", file=sys.stderr)
        return 2
    program_args = args[1:]
    if program_args and program_args[0] == "--":
        program_args = program_args[1:]
        code = run_file(first, program_args)
    elif any(a.lower().endswith(".tb") for a in program_args):
        # 1.0 refused several files; keep that unless the user asks with '--'.
        print(
            "Oops! Please give me just one .tb file to run. "
            "(To hand other file names to your program, put -- first: tb main.tb -- other.tb)",
            file=sys.stderr,
        )
        code = 2
    else:
        code = run_file(first, program_args)
    if owns_console_window():
        sys.stdout.flush()
        pause_before_closing()
    return code


if __name__ == "__main__":
    try:
        exit_code = main()
    except KeyboardInterrupt:  # a stray Ctrl+C: leave quietly
        exit_code = 130
    sys.exit(exit_code)
