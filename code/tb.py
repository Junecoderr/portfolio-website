#!/usr/bin/env python3
"""TB - a small, friendly programming language.

Made with love for Tanisha Brahma (TB = her initials).

This single file is the whole language: a lexer, a parser that builds a tree,
and an interpreter that walks that tree. It needs only Python 3.10+ and its
standard library, so it also runs in the browser through Pyodide.

Usage:
    tb file.tb      run a TB program
    tb              start the interactive prompt (REPL)
    tb --version    print the version
    tb --help       print help
"""

from __future__ import annotations

import difflib
import math
import os
import random
import re
import sys

__version__ = "1.0.0"

# How many TB function calls may be "open" at once before TB says the program
# is probably calling itself forever. Deep enough for countdown(2000) and fib.
MAX_DEPTH = 3000

# Whole numbers bigger than about 1e330 are refused: Python would happily keep
# growing them until printing fails or the computer runs out of memory.
MAX_INT_BITS = 1100
# The longest list range() will make.
MAX_RANGE = 10_000_000
TOO_BIG = "That number got too big for me to work with."


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


class TBError(Exception):
    """An error in a TB program, with a friendly message and a line number."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line

    def __str__(self) -> str:
        if self.line is not None:
            return f"Oops! On line {self.line}: {self.message}"
        return f"Oops! {self.message}"


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

KEYWORDS = {
    "let", "fn", "return", "if", "else", "while", "for", "in",
    "true", "false", "nil", "and", "or", "not",
}

TWO_CHAR_OPS = {"==", "!=", "<=", ">="}
ONE_CHAR_OPS = set("+-*/%<>=!(){}[],;")


class Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind: str, value, line: int):
        self.kind = kind    # NUMBER, STRING, IDENT, KEYWORD, OP, EOF
        self.value = value
        self.line = line

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, line {self.line})"


ESCAPES = {"n": "\n", "t": "\t", '"': '"', "'": "'", "\\": "\\"}
DIGITS = "0123456789"

# Friendly hints for characters people often type by accident.
CHAR_HINTS = {
    "\u201c": "That's a curly quote. TB needs straight quotes: \" or '.",
    "\u201d": "That's a curly quote. TB needs straight quotes: \" or '.",
    "\u2018": "That's a curly quote. TB needs straight quotes: \" or '.",
    "\u2019": "That's a curly quote. TB needs straight quotes: \" or '.",
    "&": "To say 'both', TB uses the word 'and'.",
    "|": "To say 'either', TB uses the word 'or'.",
    ".": "Numbers need a digit before the dot (like 0.5), and TB has no '.' after names.",
    ":": "TB uses '{' and '}' around blocks instead of ':'.",
}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
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
                    raise TBError(
                        f"This text never ends. Did you forget a closing {quote}?",
                        start_line,
                    )
                ch = source[i]
                if ch == quote:
                    i += 1
                    break
                if ch == "\n":
                    raise TBError(
                        f"This text never ends. Did you forget a closing {quote}?",
                        start_line,
                    )
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
    def __init__(self, target, value, line):
        self.target, self.value, self.line = target, value, line


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


class ExprStmt(Node):
    def __init__(self, expr, line):
        self.expr, self.line = expr, line


class Literal(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class ListLit(Node):
    def __init__(self, items, line):
        self.items, self.line = items, line


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
}

# Built-ins from other languages, used for 'Did you mean' hints.
OTHER_LANGUAGE_NAMES = {
    "print": "say", "println": "say", "input": "ask", "length": "len",
    "append": "push", "null": "nil", "None": "nil", "undefined": "nil",
    "True": "true", "False": "false", "string": "str", "int": "num",
    "float": "num", "number": "num",
}


def describe(tok: Token) -> str:
    if tok.kind == "EOF":
        return "the end of the program"
    if tok.kind == "STRING":
        return f'the text "{tok.value}"'
    return f"'{tok.value}'"


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0
        self.fn_depth = 0  # how many functions we are inside (for 'return')

    # -- helpers ----------------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.pos]

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

    def statement(self) -> Node:
        tok = self.peek()
        if tok.kind == "KEYWORD":
            if tok.value == "let":
                return self.let_statement()
            if tok.value == "fn" and self.tokens[self.pos + 1].kind == "IDENT":
                return self.fn_declaration()
            if tok.value == "return":
                return self.return_statement()
            if tok.value == "if":
                return self.if_statement()
            if tok.value == "while":
                self.advance()
                cond = self.expression()
                return While(cond, self.block(), tok.line)
            if tok.value == "for":
                return self.for_statement()
            if tok.value == "else":
                raise TBError("This 'else' doesn't belong to any 'if'.", tok.line)
        if tok.kind == "IDENT" and tok.value in OTHER_LANGUAGE_WORDS:
            nxt = self.tokens[self.pos + 1]
            if nxt.kind in ("IDENT", "KEYWORD", "NUMBER", "STRING") and nxt.line == tok.line:
                raise TBError(
                    f"I don't know the word '{tok.value}'. {OTHER_LANGUAGE_WORDS[tok.value]}",
                    tok.line,
                )
        expr = self.expression()
        if self.check("OP", "="):
            eq = self.advance()
            if not isinstance(expr, (Name, Index)):
                raise TBError("I can only put a value into a name or a list slot.", eq.line)
            value = self.expression()
            return Assign(expr, value, eq.line)
        return ExprStmt(expr, tok.line)

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
        try:
            return self.block()
        finally:
            self.fn_depth -= 1

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
        return For(name.value, iterable, self.block(), tok.line)

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
        while self.peek().kind == "OP" and self.peek().value in ("<", "<=", ">", ">="):
            op = self.advance()
            left = Binary(op.value, left, self.term(), op.line)
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
            else:
                return expr

    def prev_line(self) -> int:
        return self.tokens[self.pos - 1].line if self.pos > 0 else 0

    def primary(self) -> Node:
        tok = self.peek()
        if tok.kind in ("NUMBER", "STRING"):
            self.advance()
            return Literal(tok.value, tok.line)
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
        raise TBError(f"I didn't expect {describe(tok)} here.", tok.line)


def parse(source: str) -> Program:
    return Parser(tokenize(source)).parse_program()


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
    def __init__(self, name, params, body, closure: Env):
        self.name, self.params, self.body, self.closure = name, params, body, closure


class Builtin:
    def __init__(self, name, fn, arity: tuple[int, int]):
        self.name, self.fn, self.arity = name, fn, arity


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


def type_name(value) -> str:
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
    if isinstance(value, (Function, Builtin)):
        return "function"
    return "value"


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
        return f'"{value}"' if nested else value
    if isinstance(value, list):
        if id(value) in _seen:
            return "[...]"  # a list that contains itself
        seen = _seen | {id(value)}
        return "[" + ", ".join(to_text(v, True, seen) for v in value) + "]"
    if isinstance(value, Function):
        return f"<fn {value.name}>"
    if isinstance(value, Builtin):
        return f"<built-in fn {value.name}>"
    return str(value)


def truthy(value) -> bool:
    return value is not None and value is not False


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def tidy_number(value):
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        return int(value)
    return value


# What num() accepts: 12, -3, +4, 3.5, .5, 2., 1e3, -2.5E-3 (plain ASCII only).
NUMBER_TEXT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)

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
# Interpreter
# ---------------------------------------------------------------------------


class Interpreter:
    def __init__(self, output=None, input_fn=None, rng: random.Random | None = None):
        self.output = output or (lambda text: print(text, flush=True))
        self.input_fn = input_fn or input
        self.rng = rng or random.Random()
        self.globals = Env()
        self.depth = 0
        for name, fn, arity in self._builtins():
            self.globals.vars[name] = Builtin(name, fn, arity)

    # -- built-in functions -----------------------------------------------

    def _builtins(self):
        def say(line, *args):
            self.output(" ".join(to_text(a) for a in args))
            return None

        def length(line, value):
            if isinstance(value, (str, list)):
                return len(value)
            raise TBError(f"len() needs text or a list, not {type_name(value)}.", line)

        def text(line, value):
            return to_text(value)

        def num(line, value):
            if is_number(value):
                return value
            if isinstance(value, str):
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

        return [
            ("say", say, (0, 255)),
            ("len", length, (1, 1)),
            ("str", text, (1, 1)),
            ("num", num, (1, 1)),
            ("range", rng, (1, 3)),
            ("push", push, (2, 2)),
            ("ask", ask, (0, 1)),
            ("care", care, (0, 1)),
        ]

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
            env.vars[node.name] = Function(node.name, node.params, node.body, env)
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
                self.exec_block(node.body, Env(env))
        elif kind is For:
            items = self.evaluate(node.iterable, env)
            if isinstance(items, str):
                items = list(items)
            elif not isinstance(items, list):
                raise TBError(
                    f"'for ... in' needs a list or text, not {type_name(items)}.", node.line
                )
            for item in list(items):
                scope = Env(env)
                scope.vars[node.name] = item
                self.exec_block(node.body, scope)
        else:  # pragma: no cover - parser never produces anything else
            raise TBError("I don't know how to run this.", node.line)

    def assign(self, node: Assign, env: Env):
        value = self.evaluate(node.value, env)
        target = node.target
        if isinstance(target, Name):
            owner = env.find(target.name)
            if owner is None:
                raise TBError(
                    f"'{target.name}' doesn't exist yet. Use 'let {target.name} = ...' first.",
                    node.line,
                )
            owner.vars[target.name] = value
            return
        lst = self.evaluate(target.target, env)
        index = self.evaluate(target.index, env)
        i = self.check_index(lst, index, target.line)
        if isinstance(lst, str):
            raise TBError("Text can't be changed one letter at a time.", node.line)
        lst[i] = value

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

    def evaluate(self, node: Node, env: Env):
        kind = type(node)
        if kind is Literal:
            return node.value
        if kind is Name:
            owner = env.find(node.name)
            if owner is None:
                if node.name in OTHER_LANGUAGE_NAMES:
                    hint = [OTHER_LANGUAGE_NAMES[node.name]]
                else:
                    hint = difflib.get_close_matches(node.name, env.all_names(), n=1)
                extra = f" Did you mean '{hint[0]}'?" if hint else ""
                raise TBError(f"I don't know what '{node.name}' is.{extra}", node.line)
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
                return self.binary(node, left, right)
            except OverflowError:
                raise TBError("That number got too big for me to work with.", node.line) from None
        if kind is Call:
            callee = self.evaluate(node.callee, env)
            args = [self.evaluate(a, env) for a in node.args]
            return self.call(callee, args, node.line)
        if kind is Index:
            container = self.evaluate(node.target, env)
            index = self.evaluate(node.index, env)
            return container[self.check_index(container, index, node.line)]
        if kind is FnExpr:
            return Function("anonymous", node.params, node.body, env)
        raise TBError("I don't know how to work this out.", node.line)  # pragma: no cover

    def binary(self, node: Binary, a, b):
        op, line = node.op, node.line
        if op == "==":
            return tb_equal(a, b)
        if op == "!=":
            return not tb_equal(a, b)
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
        if op == "-":
            return check_size(a - b, line)
        if op == "*":
            return check_size(a * b, line)
        if b == 0:
            raise TBError("You can't divide by zero.", line)
        if op == "/":
            return tidy_number(check_size(a / b, line))
        return a % b

    def call(self, callee, args, line):
        if isinstance(callee, Builtin):
            lo, hi = callee.arity
            if not lo <= len(args) <= hi:
                raise TBError(arity_message(callee.name, lo, hi, len(args)), line)
            return callee.fn(line, *args)
        if not isinstance(callee, Function):
            article = {"number": "a number", "list": "a list"}.get(type_name(callee), type_name(callee))
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
        try:
            self.exec_block(callee.body, scope)
        except ReturnSignal as signal:
            return signal.value
        finally:
            self.depth -= 1
        return None


def tb_equal(a, b, _comparing: frozenset = frozenset()) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if is_number(a) and is_number(b):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        if a is b:
            return True
        pair = (id(a), id(b))
        if pair in _comparing:
            return True  # lists that contain themselves: no difference found so far
        if len(a) != len(b):
            return False
        inner = _comparing | {pair}
        return all(tb_equal(x, y, inner) for x, y in zip(a, b))
    return a == b


def arity_message(name, lo, hi, got) -> str:
    if lo == hi:
        want = f"{lo} input" + ("" if lo == 1 else "s")
    elif hi >= 255:
        want = f"at least {lo} input" + ("" if lo == 1 else "s")
    else:
        want = f"{lo} to {hi} inputs"
    return f"{name}() needs {want}, but got {got}."


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
):
    """Run TB source code. Raises TBError on mistakes in the program.
    Pass a seeded random.Random as rng to make care() predictable."""
    interp = interpreter or Interpreter(output=output, input_fn=input_fn, rng=rng)
    _guarded(lambda: interp.run(parse(source)))
    return interp


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

HELP = f"""TB {__version__} - a small, friendly programming language.

Usage:
  tb FILE.tb       run a TB program
  tb               start the interactive prompt (type 'exit' to leave)
  tb --version     show the version
  tb --help        show this help

Example program (save as hello.tb, then run: tb hello.tb):
  let name = ask("What's your name? ")
  say("Hello, " + name + "!")
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
    # A PyInstaller one-file exe is two processes (a small launcher plus TB).
    alone = 2 if getattr(sys, "frozen", False) else 1
    return 0 < count <= alone


def pause_before_closing() -> None:
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def run_file(path: str) -> int:
    try:
        source = read_source(path)
    except FileNotFoundError:
        message = f"Oops! I couldn't find the file '{path}'. Is the name spelled right?"
        if os.path.isfile(path + ".txt"):
            message = (
                f"Oops! I couldn't find '{path}', but I found '{path}.txt'.\n"
                f"Did Notepad save it as '{path}.txt'? Rename it so it ends in .tb "
                "(in File Explorer, turn on View > File name extensions to see the .txt)."
            )
        print(message, file=sys.stderr)
        return 1
    except OSError as err:
        print(f"Oops! I couldn't open '{path}': {err.strerror or err}.", file=sys.stderr)
        return 1
    try:
        run(source)
    except TBError as err:
        print(err, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        ignore_ctrl_c()
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as err:  # never show a Python traceback
        print(crash_message(err), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # Never crash on a console that can't show characters like the heart.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if any(a in ("-h", "--help") for a in args):
        print(HELP)
        return 0
    if any(a in ("-V", "--version") for a in args):
        print(f"TB {__version__}")
        return 0
    if not args:
        return repl()
    if len(args) > 1:
        print("Oops! Please give me just one .tb file to run.", file=sys.stderr)
        code = 2
    else:
        code = run_file(args[0])
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
