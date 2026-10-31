"""The parser: tokens -> syntax tree, with friendly messages for mistakes."""

from __future__ import annotations

import os
import re

from .ast import (
    Assert, Assign, Await, Binary, Break, Call, Catch, ClassDecl, Compare, Conditional,
    Continue, Defer, Dot, EnumDecl, Export, ExprStmt, FHole, FString, FnDecl, FnExpr, For,
    FromImport, If, Import, Index, IsCheck, KeywordArg, Let, ListLit, ListPattern, Literal,
    Logical, MapLit, MapPattern, Match, MatchArm, MultiAssign, Name, NamePattern, Node, PClass,
    PIs, PList, PLiteral, PMap, PName, POr, PRange, PValue, Param, Program, RangeExpr, Return,
    SliceExpr, Spread, Super, Throw, Try, Unary, While, Yield,
)
from .errors import TBError
from .lexer import ASSIGN_OPS, KEYWORD_ALTERNATIVES, KEYWORDS, NEW_KEYWORDS, Token, tokenize

# Words from other languages, and what TB says instead.
OTHER_LANGUAGE_WORDS = {
    "def": "TB makes functions with 'fn', like: fn greet(name) { ... }",
    "function": "TB makes functions with 'fn', like: fn greet(name) { ... }",
    "func": "TB makes functions with 'fn', like: fn greet(name) { ... }",
    "var": "TB makes new names with 'let', like: let x = 5",
    "elif": "TB writes this as 'else if'.",
    "raise": "TB throws errors with 'throw', like: throw \"it broke\"",
    "except": "TB catches errors with: try { ... } catch err { ... }",
    "switch": "TB uses 'match', like: match x { 1 => \"one\"  _ => \"other\" }",
    "lambda": "TB writes small functions as: x => x * 2",
    "struct": "TB uses 'class', like: class Point { fn init(x, y) { self.x = x; self.y = y } }",
    "interface": "TB has no interfaces; use a class.",
}

# Built-ins from other languages, used for 'Did you mean' hints.
OTHER_LANGUAGE_NAMES = {
    "print": "say", "println": "say", "input": "ask", "length": "len",
    "append": "push", "null": "nil", "None": "nil", "undefined": "nil",
    "True": "true", "False": "false", "string": "str", "number": "num",
    "Exception": "Error", "raise": "throw",
}

NAME_RE = re.compile(r"[^\W\d]\w*")

# Names that `x is name` understands without a class.
TYPE_NAMES = {"number", "int", "float", "text", "bool", "nil", "list", "map", "bytes",
              "function", "class", "module", "range", "generator", "enum", "any"}


def describe(tok: Token) -> str:
    if tok.kind == "EOF":
        return "the end of the program"
    if tok.kind == "STRING":
        return f'the text "{tok.value}"'
    if tok.kind == "FSTRING":
        return "an f-text"
    if tok.kind == "BYTES":
        return "some bytes"
    return f"'{tok.value}'"


def keyword_as_name(tok: Token) -> TBError:
    word = tok.value
    other = KEYWORD_ALTERNATIVES.get(word, f"my_{word}")
    if word in NEW_KEYWORDS:
        return TBError(f"'{word}' is a keyword in TB 3.0; call it '{other}' instead.", tok.line)
    return TBError(f"'{word}' is a keyword in TB and can't be used as a name; call it '{other}' instead.", tok.line)


class Parser:
    def __init__(self, tokens: list[Token], base_dir: str | None = None):
        self.tokens = tokens
        self.pos = 0
        self.fn_stack: list[dict] = []   # one entry per function we are inside
        self.loop_depth = 0  # how many loops we are inside (for 'break')
        self.class_depth = 0
        self.base_dir = base_dir  # folder that 'import "file.tb"' is relative to
        self.no_lambda = 0        # >0 while parsing match guards (x => would be an arm)
        self.arm_depth = 0        # >0 while parsing a match arm's expression
        self.saw_export = False

    @property
    def fn_depth(self) -> int:
        return len(self.fn_stack)

    # -- helpers ----------------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def peek_next(self) -> Token:
        return self.tokens[min(self.pos + 1, len(self.tokens) - 1)]

    def peek_at(self, offset: int) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def check(self, kind: str, value=None) -> bool:
        tok = self.peek()
        return tok.kind == kind and (value is None or tok.value == value)

    def check_op(self, value: str) -> bool:
        tok = self.peek()
        return tok.kind == "OP" and tok.value == value

    def match(self, kind: str, value=None) -> bool:
        if self.check(kind, value):
            self.advance()
            return True
        return False

    def expect(self, kind: str, value=None, what: str | None = None) -> Token:
        if self.check(kind, value):
            return self.advance()
        tok = self.peek()
        if kind == "IDENT" and tok.kind == "KEYWORD":
            raise keyword_as_name(tok)
        wanted = what or (f"'{value}'" if value is not None else kind.lower())
        raise TBError(f"I expected {wanted} here, but found {describe(tok)}.", tok.line)

    def expect_name(self, what: str) -> Token:
        return self.expect("IDENT", what=what)

    def close(self, value: str, opener: Token, what: str) -> Token:
        """Expect a closing bracket; if the program ends first, point at the opener."""
        if self.check("EOF"):
            raise TBError(
                f"This '{opener.value}' was never closed. Did you forget a '{value}'?",
                opener.line,
            )
        return self.expect("OP", value, what)

    def prev_line(self) -> int:
        return self.tokens[self.pos - 1].end_line if self.pos > 0 else 0

    def same_line(self, tok: Token) -> bool:
        return tok.line == self.prev_line()

    def continues(self, tok: Token) -> bool:
        """May this operator token continue the current expression? Inside a
        match arm, an operator on a new line starts the next arm instead."""
        return not self.arm_depth or tok.line == self.prev_line()

    # -- statements -------------------------------------------------------

    def parse_program(self) -> Program:
        body = []
        while not self.check("EOF"):
            if self.match("OP", ";"):
                continue
            body.append(self.statement())
        program = Program(body)
        program.trailing_comments = list(self.peek().comments_before)
        return program

    def block(self) -> list[Node]:
        if self.check_op("="):
            raise TBError(
                "I expected '{' to start a block here, but found '='. "
                "To check if two things are equal, use '=='.",
                self.peek().line,
            )
        if self.check_op(":"):
            raise TBError(
                "I expected '{' to start a block here, but found ':'. "
                "TB uses '{' and '}' around blocks instead of ':'.",
                self.peek().line,
            )
        open_tok = self.expect("OP", "{", "'{' to start a block")
        body = []
        while not self.check_op("}"):
            if self.check("EOF"):
                raise TBError(
                    "This block was never closed. Did you forget a '}'?", open_tok.line
                )
            if self.match("OP", ";"):
                continue
            body.append(self.statement())
        close_tok = self.advance()
        if body and close_tok.comments_before:
            body[-1].comments_after = list(close_tok.comments_before)
        return body

    def loop_body(self) -> list[Node]:
        self.loop_depth += 1
        try:
            return self.block()
        finally:
            self.loop_depth -= 1

    def statement(self) -> Node:
        start = self.peek()
        node = self._statement()
        node.comments = start.comments_before
        node.blank_before = start.blank_lines_before
        last = self.tokens[self.pos - 1] if self.pos > 0 else start
        node.end_line = last.end_line
        node.trailing_comment = last.trailing_comment
        return node

    def _statement(self) -> Node:
        tok = self.peek()
        if tok.kind == "KEYWORD":
            word = tok.value
            if word == "let":
                return self.let_statement()
            if word == "const":
                return self.let_statement(is_const=True)
            if word == "fn":
                nxt = self.peek_next()
                if nxt.kind == "IDENT":
                    return self.fn_declaration()
                if nxt.kind == "KEYWORD" and nxt.value not in ("self", "super"):
                    raise keyword_as_name(nxt)
            if word == "async" and self.peek_next().kind == "KEYWORD" and self.peek_next().value == "fn" \
                    and self.peek_at(2).kind == "IDENT":
                async_tok = self.advance()
                decl = self.fn_declaration()
                decl.is_async = True
                self.check_async(decl.is_generator, async_tok)
                return decl
            if word == "return":
                return self.return_statement()
            if word == "if":
                return self.if_statement()
            if word == "while":
                self.advance()
                cond = self.expression()
                return While(cond, self.loop_body(), tok.line)
            if word == "for":
                return self.for_statement()
            if word == "else":
                raise TBError("This 'else' doesn't belong to any 'if'.", tok.line)
            if word == "class":
                return self.class_declaration()
            if word == "enum":
                return self.enum_declaration()
            if word == "export":
                return self.export_statement()
            if word == "from":
                return self.from_import()
            if word == "static":
                raise TBError("'static' only works inside a class, like: static fn make() { ... }", tok.line)
            if word == "extends":
                raise TBError("'extends' belongs after a class name, like: class Dog extends Animal { ... }", tok.line)
            if word == "finally":
                raise TBError("This 'finally' doesn't belong to any 'try'.", tok.line)
            if word == "then":
                raise TBError("'then' belongs in a choice, like: if ready then 1 else 0", tok.line)
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
        nxt = self.peek()
        if nxt.kind == "OP" and nxt.value == "," and isinstance(expr, (Name, Index, Dot)):
            # a, b = b, a
            targets = [expr]
            while self.match("OP", ","):
                target = self.expression()
                if not isinstance(target, (Name, Index, Dot)):
                    raise TBError("I can only put a value into a name or a list slot (or a map key).", target.line)
                targets.append(target)
            eq = self.expect("OP", "=", "'=' after the names (like: a, b = b, a)")
            values = [self.expression()]
            while self.match("OP", ","):
                values.append(self.expression())
            for target in targets:
                self.check_assignable(target, eq)
            value = values[0] if len(values) == 1 else ListLit(values, eq.line)
            return MultiAssign(targets, value, eq.line)
        if nxt.kind == "OP" and nxt.value in ASSIGN_OPS:
            eq = self.advance()
            if not isinstance(expr, (Name, Index, Dot)):
                raise TBError(
                    "I can only put a value into a name or a list slot (or a map key).", eq.line
                )
            self.check_assignable(expr, eq)
            value = self.expression()
            return Assign(expr, value, eq.line, None if eq.value == "=" else eq.value[:-1])
        return ExprStmt(expr, tok.line)

    def check_assignable(self, target, eq: Token) -> None:
        if isinstance(target, Name) and target.name == "self":
            raise TBError("You can't change what 'self' is; change its fields instead, like: self.name = ...", eq.line)
        if isinstance(target, Dot) and isinstance(target.target, Super):
            raise TBError("You can't assign to super; set the field on self instead.", eq.line)

    def special_statement(self, tok: Token) -> Node | None:
        """import / try / throw / break / continue / defer / assert, which are
        only special at the start of a statement (so they still work as names)."""
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
        if word == "defer" and nxt.kind == "OP" and nxt.value == "{":
            self.advance()
            if self.fn_depth == 0:
                raise TBError("'defer' only works inside a function (fn). It runs its block when the function finishes.", tok.line)
            return Defer(self.block(), tok.line)
        starts_value = same_line and not (
            nxt.kind == "OP" and nxt.value in ASSIGN_OPS | {".", "[", "}", ";", ")", ",", "]", "=>"}
        )
        if word == "throw" and starts_value:
            self.advance()
            return Throw(self.expression(), tok.line)
        if word == "assert" and starts_value:
            self.advance()
            cond = self.expression()
            message = None
            if self.match("OP", ","):
                message = self.expression()
            return Assert(cond, message, tok.line)
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
                and self.tokens[self.pos + 2].value in ("{", ":"))
        ):
            raise TBError("This 'catch' doesn't belong to any 'try'.", tok.line)
        return None

    def import_statement(self) -> Import:
        tok = self.advance()
        target = self.advance()
        if self.check_op("."):
            raise TBError(
                f"Import the whole library with 'import {target.value}', "
                f"then use its parts like {target.value}.name. "
                f"(Or pick parts: from {target.value} import name)",
                tok.line,
            )
        alias = None
        if self.check("IDENT", "as"):
            self.advance()
            alias = self.expect_name("a name after 'as'").value
        if target.kind == "STRING":
            path = target.value
            if not path.strip():
                raise TBError('I need a file name to import, like: import "helpers.tb"', tok.line)
            package_name = None
            if not os.path.splitext(path)[1]:
                if "/" not in path and "\\" not in path:
                    package_name = path   # import "tb-colors" may be a package
                path += ".tb"
            if alias is None:
                stem = os.path.splitext(os.path.basename(path))[0]
                if package_name is not None:
                    stem = stem.replace("-", "_")
                if not NAME_RE.fullmatch(stem) or stem in KEYWORDS:
                    raise TBError(
                        f"I can't make a name from '{target.value}'. Give it one with: "
                        f'import "{target.value}" as helpers',
                        tok.line,
                    )
                alias = stem
            node = Import(path, alias, True, self.base_dir, tok.line)
            node.package_name = package_name
            return node
        return Import(target.value, alias or target.value, False, self.base_dir, tok.line)

    def from_import(self) -> FromImport:
        tok = self.advance()
        target = self.peek()
        if target.kind not in ("IDENT", "STRING"):
            raise TBError('After \'from\' write a library or file, like: from fs import read, write', tok.line)
        self.advance()
        if not self.check("IDENT", "import"):
            raise TBError(
                f"After 'from {target.value}' I expected 'import', like: from {target.value} import name",
                tok.line,
            )
        self.advance()
        names = []
        while True:
            name = self.expect_name("a name to import")
            alias = name.value
            if self.check("IDENT", "as"):
                self.advance()
                alias = self.expect_name("a name after 'as'").value
            names.append((name.value, alias))
            if not self.match("OP", ","):
                break
        is_file = target.kind == "STRING"
        path = target.value
        package_name = None
        if is_file and not os.path.splitext(path)[1]:
            if "/" not in path and "\\" not in path:
                package_name = path
            path += ".tb"
        node = FromImport(path, is_file, names, self.base_dir, tok.line)
        node.package_name = package_name
        return node

    def try_statement(self) -> Try:
        tok = self.advance()
        body = self.block()
        catches = []
        finally_body = None
        while self.check("IDENT", "catch"):
            catch_tok = self.advance()
            name = None
            types = None
            if self.check_op("("):
                raise TBError(
                    "Write the error's name without brackets, like: catch err { ... }", catch_tok.line
                )
            if self.check("IDENT"):
                name = self.advance().value
                if self.match("OP", ":"):
                    types = [self.type_reference()]
                    while self.match("KEYWORD", "or"):
                        types.append(self.type_reference())
            handler = self.block()
            catches.append(Catch(name, types, handler, catch_tok.line))
        if self.match("KEYWORD", "finally"):
            finally_body = self.block()
        if not catches and finally_body is None:
            raise TBError(
                "A 'try' block needs a 'catch' after it, like: try { ... } catch err { ... }",
                tok.line,
            )
        return Try(body, catches, finally_body, tok.line)

    def type_reference(self) -> Node:
        """A class name, possibly dotted (Error, my_lib.NotReady)."""
        tok = self.peek()
        if tok.kind == "KEYWORD" and tok.value in ("nil", "class"):
            self.advance()
            return Name(tok.value, tok.line)
        name = self.expect_name("a type name")
        node: Node = Name(name.value, name.line)
        while self.check_op("."):
            self.advance()
            part = self.expect_name("a name after '.'")
            node = Dot(node, part.value, part.line)
        return node

    def let_statement(self, is_const=False) -> Let:
        tok = self.advance()
        word = tok.value
        if self.check("OP", "[") or self.check("OP", "{"):
            target = self.pattern()
            self.expect("OP", "=", f"'=' after the pattern")
            return Let(target, self.expression(), tok.line, is_const)
        name = self.expect_name(f"a name after '{word}'")
        annotation = None
        if self.check_op(","):
            start = self.pos - 1
            names = [NamePattern(name.value, name.line)]
            while self.match("OP", ","):
                names.append(self.pattern())
            lhs = "".join(str(t.value) + (" " if t.value == "," else "") for t in self.tokens[start:self.pos])
            self.expect("OP", "=", f"'=' after the names")
            start_value = self.pos
            value = self.expression()
            if self.check_op(","):
                # let a, b = 0, 1: several values need to be one list
                parts = [self._short_text(start_value, self.pos)]
                while self.match("OP", ","):
                    start_value = self.pos
                    self.expression()
                    parts.append(self._short_text(start_value, self.pos))
                raise TBError(f"To give several names their values at once, put the values in a list: "
                              f"{word} {lhs} = [{', '.join(parts)}]", tok.line)
            return Let(ListPattern(names, None, tok.line), value, tok.line, is_const)
        if self.match("OP", ":"):
            annotation = self.type_annotation()
        if self.check_op("=="):
            raise TBError(f"Use one '=' to give '{name.value}' a value: {word} {name.value} = ...", self.peek().line)
        if is_const and not self.check_op("="):
            raise TBError(f"A const needs a value: const {name.value} = ...", tok.line)
        self.expect("OP", "=", f"'=' after '{word} {name.value}'")
        return Let(name.value, self.expression(), tok.line, is_const, annotation)

    def _short_text(self, start: int, end: int) -> str:
        """The source of tokens[start:end] for a hint when it is one simple
        token (tokens keep no columns), else a placeholder."""
        if end - start == 1:
            t = self.tokens[start]
            if t.kind in ("NUMBER", "IDENT") or (t.kind == "KEYWORD" and t.value in ("true", "false", "nil")):
                return str(t.value)
            if t.kind == "STRING" and not any(c in t.value for c in '"\\\n{'):
                return f'"{t.value}"'
        return "<value>"

    # -- patterns (destructuring) -------------------------------------------

    def pattern(self):
        tok = self.peek()
        if tok.kind == "OP" and tok.value == "[":
            self.advance()
            items = []
            rest = None
            while not self.check_op("]"):
                if self.check_op("..."):
                    self.advance()
                    rest = self.expect_name("a name after '...'").value
                    self.match("OP", ",")
                    if not self.check_op("]"):
                        raise TBError("'...rest' must be the last thing in the pattern.", tok.line)
                    break
                items.append(self.pattern())
                if not self.match("OP", ","):
                    break
            self.close("]", tok, "']' to close the pattern")
            return ListPattern(items, rest, tok.line)
        if tok.kind == "OP" and tok.value == "{":
            self.advance()
            entries = []
            while not self.check_op("}"):
                key_tok = self.peek()
                if key_tok.kind == "STRING":
                    self.advance()
                    key = key_tok.value
                    if not self.check_op(":"):
                        raise TBError(f'After the key "{key}" write \':\' and a name, like {{"{key}": value}}.', key_tok.line)
                else:
                    key = self.expect_name("a key name").value
                sub = NamePattern(key, key_tok.line) if key_tok.kind != "STRING" else None
                if self.match("OP", ":"):
                    sub = self.pattern()
                default = None
                if self.match("OP", "="):
                    default = self.expression()
                entries.append((key, sub, default))
                if not self.match("OP", ","):
                    break
            self.close("}", tok, "'}' to close the pattern")
            return MapPattern(entries, tok.line)
        name = self.expect_name("a name")
        return NamePattern(name.value, name.line)

    # -- types (parsed, ignored at run time) ----------------------------------

    def type_annotation(self) -> str:
        parts = [self.type_single()]
        while self.check_op("|"):
            self.advance()
            parts.append(self.type_single())
        return " | ".join(parts)

    def type_single(self) -> str:
        tok = self.peek()
        if tok.kind == "OP" and tok.value == "(":
            self.advance()
            inner = self.type_annotation()
            self.expect("OP", ")", "')' to close the type")
            return f"({inner})"
        if tok.kind == "KEYWORD" and tok.value == "fn":
            self.advance()
            self.expect("OP", "(", "'(' after 'fn' in a type")
            args = []
            while not self.check_op(")"):
                args.append(self.type_annotation())
                if not self.match("OP", ","):
                    break
            self.expect("OP", ")", "')' to close the type")
            text = f"fn({', '.join(args)})"
            if self.match("OP", "->"):
                text += " -> " + self.type_annotation()
            return text
        if tok.kind == "KEYWORD" and tok.value in ("nil", "class"):
            self.advance()
            return tok.value
        if tok.kind != "IDENT":
            raise TBError(f"I expected a type name here (like number, text or list<number>), but found {describe(tok)}.", tok.line)
        self.advance()
        text = tok.value
        while self.check_op(".") and self.peek_next().kind == "IDENT":
            self.advance()
            text += "." + self.advance().value
        if self.check_op("<"):
            self.advance()
            args = [self.type_annotation()]
            while self.match("OP", ","):
                args.append(self.type_annotation())
            if self.check_op(">>"):
                # list<list<number>> lexes '>>' as one token: split it
                tok2 = self.peek()
                tok2.value = ">"
                self.tokens.insert(self.pos, Token("OP", ">", tok2.line))
            self.expect("OP", ">", "'>' to close the type")
            text += f"<{', '.join(args)}>"
        return text

    # -- functions ---------------------------------------------------------

    def params(self) -> list[Param]:
        self.expect("OP", "(", "'(' before the inputs")
        return self.param_list(")", "')' after the inputs")

    def param_list(self, closer: str, what: str) -> list[Param]:
        params: list[Param] = []
        names: list[str] = []
        seen_rest = False
        seen_kwrest = False
        if not self.check_op(closer):
            while True:
                tok = self.peek()
                if seen_kwrest:
                    raise TBError("'**opts' must be the last input.", tok.line)
                if tok.kind == "OP" and tok.value == "...":
                    self.advance()
                    name = self.expect_name("a name after '...'")
                    if seen_rest:
                        raise TBError("A function can only have one '...rest' input.", name.line)
                    seen_rest = True
                    self.note_param_name(name, names)
                    params.append(Param(name.value, None, "rest", None, name.line))
                elif tok.kind == "OP" and tok.value == "**":
                    self.advance()
                    name = self.expect_name("a name after '**'")
                    seen_kwrest = True
                    self.note_param_name(name, names)
                    params.append(Param(name.value, None, "kwrest", None, name.line))
                else:
                    if tok.kind == "OP" and tok.value in ("[", "{"):
                        target = self.pattern()
                        for n in self.pattern_names(target):
                            self.note_param_name(Token("IDENT", n, tok.line), names)
                    else:
                        name = self.expect_name("an input name")
                        self.note_param_name(name, names)
                        target = name.value
                    annotation = None
                    if self.match("OP", ":"):
                        annotation = self.type_annotation()
                    default = None
                    if self.match("OP", "="):
                        default = self.expression()
                    if seen_rest and default is None:
                        raise TBError("Inputs after '...rest' must have a default value, or come before it.", tok.line)
                    params.append(Param(target, default, "pos", annotation, tok.line))
                if not self.match("OP", ","):
                    break
                if self.check_op(closer):
                    break
        self.expect("OP", closer, what)
        return params

    def pattern_names(self, pattern) -> list[str]:
        if isinstance(pattern, NamePattern):
            return [pattern.name]
        if isinstance(pattern, ListPattern):
            names = [n for item in pattern.items for n in self.pattern_names(item)]
            if pattern.rest:
                names.append(pattern.rest)
            return names
        if isinstance(pattern, MapPattern):
            names = []
            for key, sub, _default in pattern.entries:
                names.extend(self.pattern_names(sub) if sub is not None else [key])
            return names
        return []

    def note_param_name(self, tok: Token, names: list[str]) -> None:
        if tok.value in names:
            raise TBError(
                f"The input name '{tok.value}' is used twice. "
                "Each input needs its own name.",
                tok.line,
            )
        names.append(tok.value)

    def fn_declaration(self) -> FnDecl:
        tok = self.advance()
        name = self.advance().value
        params = self.params()
        returns = None
        if self.match("OP", "->"):
            returns = self.type_annotation()
        body, is_generator = self.function_body()
        return FnDecl(name, params, body, tok.line, is_generator, returns)

    @staticmethod
    def check_async(is_generator: bool, tok: Token) -> None:
        if is_generator:
            raise TBError("An async fn can't use yield. Make it a normal generator fn, or give back a "
                          "list from the async fn.", tok.line)

    def function_body(self) -> tuple[list[Node], bool]:
        self.fn_stack.append({"yield": False})
        loops, self.loop_depth = self.loop_depth, 0
        arms, self.arm_depth = self.arm_depth, 0
        try:
            body = self.block()
            return body, self.fn_stack[-1]["yield"]
        finally:
            self.fn_stack.pop()
            self.loop_depth = loops
            self.arm_depth = arms

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
        value = self.expression()
        if self.check_op(","):
            values = [value]
            while self.match("OP", ","):
                values.append(self.expression())
            value = ListLit(values, tok.line)
        return Return(value, tok.line)

    def if_statement(self) -> Node:
        tok = self.advance()
        cond = self.expression()
        if self.check("KEYWORD", "then"):
            # if cond then a else b, used as a statement
            expr = self.conditional_rest(cond, tok)
            return ExprStmt(expr, tok.line)
        branches = [(cond, self.block())]
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
        targets = []
        while True:
            if self.check("OP", "[") or self.check("OP", "{"):
                targets.append(self.pattern())
            else:
                name = self.expect_name("a name after 'for'")
                targets.append(name.value)
            if not self.match("OP", ","):
                break
        self.expect("KEYWORD", "in", "'in' (like: for x in list)")
        iterable = self.expression()
        return For(targets, iterable, self.loop_body(), tok.line)

    # -- classes and enums --------------------------------------------------

    def class_declaration(self) -> ClassDecl:
        tok = self.advance()
        name = self.expect_name("a name after 'class'")
        base = None
        if self.match("KEYWORD", "extends"):
            base = self.type_reference()
        if self.check_op("<"):
            raise TBError("TB classes don't take type parameters like <T>. Just write: class " + name.value + " { ... }", tok.line)
        open_tok = self.expect("OP", "{", "'{' to start the class body")
        fields, statics, methods, static_methods = [], [], [], []
        seen: dict[str, str] = {}
        self.class_depth += 1
        try:
            while not self.check_op("}"):
                if self.check("EOF"):
                    raise TBError("This class was never closed. Did you forget a '}'?", open_tok.line)
                if self.match("OP", ";"):
                    continue
                member_tok = self.peek()
                is_static = self.match("KEYWORD", "static")
                async_tok = self.peek() if self.check("KEYWORD", "async") else None
                if async_tok is not None:  # async fn method() { }  (TB 3.0)
                    self.advance()
                    if not self.check("KEYWORD", "fn"):
                        raise TBError(f"'async' goes right before 'fn', like: async fn load() {{ }}", async_tok.line)
                if self.check("KEYWORD", "let") or self.check("KEYWORD", "const"):
                    self.advance()
                    fname = self.expect_name("a field name")
                    annotation = None
                    if self.match("OP", ":"):
                        annotation = self.type_annotation()
                    value = None
                    if self.match("OP", "="):
                        value = self.expression()
                    self.check_member(fname, seen, "field")
                    (statics if is_static else fields).append((fname.value, value, annotation))
                elif self.check("KEYWORD", "fn"):
                    fn_tok = self.advance()
                    mname = self.peek()
                    if mname.kind == "KEYWORD" and mname.value not in ("self", "super"):
                        raise keyword_as_name(mname)
                    if mname.kind != "IDENT":
                        raise TBError(f"I expected a method name after 'fn', but found {describe(mname)}.", mname.line)
                    self.advance()
                    params = self.params()
                    returns = None
                    if self.match("OP", "->"):
                        returns = self.type_annotation()
                    body, is_generator = self.function_body()
                    self.check_member(mname, seen, "method")
                    decl = FnDecl(mname.value, params, body, fn_tok.line, is_generator, returns,
                                  is_async=async_tok is not None)
                    if async_tok is not None:
                        self.check_async(is_generator, async_tok)
                    decl.comments = member_tok.comments_before
                    decl.blank_before = member_tok.blank_lines_before
                    (static_methods if is_static else methods).append(decl)
                else:
                    found = self.peek()
                    raise TBError(
                        f"Inside a class I expected 'let', 'fn', 'static let' or 'static fn', but found {describe(found)}.",
                        found.line,
                    )
        finally:
            self.class_depth -= 1
        self.advance()
        return ClassDecl(name.value, base, fields, statics, methods, static_methods, tok.line)

    def check_member(self, tok: Token, seen: dict, what: str) -> None:
        if tok.value in seen:
            raise TBError(f"The class already has a {seen[tok.value]} called '{tok.value}'.", tok.line)
        seen[tok.value] = what

    def enum_declaration(self) -> EnumDecl:
        tok = self.advance()
        name = self.expect_name("a name after 'enum'")
        open_tok = self.expect("OP", "{", "'{' after the enum name")
        members = []
        while not self.check_op("}"):
            if self.check("EOF"):
                raise TBError("This enum was never closed. Did you forget a '}'?", open_tok.line)
            member = self.expect_name("a member name")
            if member.value in members:
                raise TBError(f"The enum already has a member called '{member.value}'.", member.line)
            members.append(member.value)
            if not self.match("OP", ","):
                break
        self.close("}", open_tok, "'}' to close the enum")
        if not members:
            raise TBError(f"The enum '{name.value}' needs at least one member, like: enum {name.value} {{ low, high }}", tok.line)
        return EnumDecl(name.value, members, tok.line)

    def export_statement(self) -> Export:
        tok = self.advance()
        nxt = self.peek()
        allowed = ("fn", "let", "const", "class", "enum", "async")
        if nxt.kind != "KEYWORD" or nxt.value not in allowed:
            raise TBError(
                "After 'export' write what to share: export fn ..., export let ..., "
                "export const ..., export class ... or export enum ...",
                tok.line,
            )
        if self.fn_depth or self.class_depth:
            raise TBError("'export' only works at the top of a file.", tok.line)
        decl = self._statement()
        if isinstance(decl, Let) and decl.name is None:
            raise TBError("'export let' needs a single name, like: export let VERSION = 1", tok.line)
        self.saw_export = True
        return Export(decl, tok.line)

    # -- expressions (lowest to highest precedence) -----------------------

    def expression(self) -> Node:
        tok = self.peek()
        if tok.kind == "KEYWORD":
            if tok.value == "yield":
                return self.yield_expr()
            if tok.value == "if":
                self.advance()
                cond = self.expression()
                return self.conditional_rest(cond, tok)
        return self.or_expr()

    def conditional_rest(self, cond: Node, tok: Token) -> Conditional:
        if not self.match("KEYWORD", "then"):
            found = self.peek()
            raise TBError(
                f"I expected 'then' after the condition (like: if ready then 1 else 0), but found {describe(found)}.",
                found.line,
            )
        then = self.expression()
        if not self.match("KEYWORD", "else"):
            found = self.peek()
            raise TBError(
                "An 'if ... then ...' choice needs an 'else' part too, like: if ready then 1 else 0.",
                found.line if found.kind != "EOF" else tok.line,
            )
        otherwise = self.expression()
        return Conditional(cond, then, otherwise, tok.line)

    def yield_expr(self) -> Yield:
        tok = self.advance()
        if self.fn_depth == 0:
            raise TBError("'yield' only works inside a function (fn). It hands out one value at a time.", tok.line)
        self.fn_stack[-1]["yield"] = True
        nxt = self.peek()
        if nxt.line != tok.line or nxt.kind == "EOF" or (nxt.kind == "OP" and nxt.value in ("}", ";", ")", ",", "]")):
            return Yield(None, tok.line)
        return Yield(self.expression(), tok.line)

    def or_expr(self) -> Node:
        left = self.and_expr()
        while self.check("KEYWORD", "or") and self.continues(self.peek()):
            op = self.advance()
            left = Logical("or", left, self.and_expr(), op.line)
        return left

    def and_expr(self) -> Node:
        left = self.not_expr()
        while self.check("KEYWORD", "and") and self.continues(self.peek()):
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
        while self.peek().kind == "OP" and self.peek().value in ("==", "!=") and self.continues(self.peek()):
            op = self.advance()
            left = Binary(op.value, left, self.comparison(), op.line)
        return left

    def comparison(self) -> Node:
        left = self.range_expr()
        operands = [left]
        ops: list[str] = []
        first_line = None
        while True:
            tok = self.peek()
            if not self.continues(tok):
                break
            if tok.kind == "OP" and tok.value in ("<", "<=", ">", ">="):
                self.advance()
                ops.append(tok.value)
                operands.append(self.range_expr())
            elif tok.kind == "KEYWORD" and tok.value == "in":
                self.advance()
                ops.append("in")
                operands.append(self.range_expr())
            elif (tok.kind == "KEYWORD" and tok.value == "not"
                  and self.peek_next().kind == "KEYWORD" and self.peek_next().value == "in"):
                self.advance()
                self.advance()
                ops.append("not in")
                operands.append(self.range_expr())
            elif tok.kind == "KEYWORD" and tok.value == "is":
                self.advance()
                negated = self.match("KEYWORD", "not")
                type_node = self.type_reference()
                current = self.fold_comparison(operands, ops)
                operands = [IsCheck(current, type_node, negated, tok.line)]
                ops = []
                continue
            else:
                break
            if first_line is None:
                first_line = tok.line
        return self.fold_comparison(operands, ops)

    def fold_comparison(self, operands, ops) -> Node:
        if not ops:
            return operands[0]
        if len(ops) == 1:
            line = operands[1].line or operands[0].line
            return Binary(ops[0], operands[0], operands[1], line)
        return Compare(operands, ops, operands[1].line or operands[0].line)

    def range_expr(self) -> Node:
        left = self.bit_or()
        tok = self.peek()
        if tok.kind == "OP" and tok.value in ("..", "..=") and self.continues(tok):
            self.advance()
            right = self.bit_or()
            return RangeExpr(left, right, tok.value == "..=", tok.line)
        return left

    def bit_or(self) -> Node:
        left = self.bit_xor()
        while self.check_op("|") and self.continues(self.peek()):
            op = self.advance()
            left = Binary("|", left, self.bit_xor(), op.line)
        return left

    def bit_xor(self) -> Node:
        left = self.bit_and()
        while self.check_op("^") and self.continues(self.peek()):
            op = self.advance()
            left = Binary("^", left, self.bit_and(), op.line)
        return left

    def bit_and(self) -> Node:
        left = self.shift()
        while self.check_op("&") and self.continues(self.peek()):
            op = self.advance()
            left = Binary("&", left, self.shift(), op.line)
        return left

    def shift(self) -> Node:
        left = self.term()
        while self.peek().kind == "OP" and self.peek().value in ("<<", ">>") and self.continues(self.peek()):
            op = self.advance()
            left = Binary(op.value, left, self.term(), op.line)
        return left

    def term(self) -> Node:
        left = self.factor()
        while self.peek().kind == "OP" and self.peek().value in ("+", "-") and self.continues(self.peek()):
            op = self.advance()
            left = Binary(op.value, left, self.factor(), op.line)
        return left

    def factor(self) -> Node:
        left = self.unary()
        while self.peek().kind == "OP" and self.peek().value in ("*", "/", "%", "//") and self.continues(self.peek()):
            op = self.advance()
            left = Binary(op.value, left, self.unary(), op.line)
        return left

    def unary(self) -> Node:
        tok = self.peek()
        if tok.kind == "OP" and tok.value in ("-", "!", "~"):
            self.advance()
            op = {"-": "-", "!": "not", "~": "~"}[tok.value]
            return Unary(op, self.unary(), tok.line)
        if tok.kind == "KEYWORD" and tok.value == "not":
            # 'not' only lands here inside an operand, e.g. '1 == not x'.
            self.advance()
            return Unary("not", self.unary(), tok.line)
        if tok.kind == "KEYWORD" and tok.value == "await":
            self.advance()
            return Await(self.unary(), tok.line)
        if tok.kind == "OP" and tok.value == "...":
            raise TBError("'...' (spread) only works inside a list, a map or a call, like: [...a, ...b] or f(...args).", tok.line)
        return self.power()

    def power(self) -> Node:
        base = self.postfix()
        tok = self.peek()
        if tok.kind == "OP" and tok.value == "**" and self.continues(tok):
            self.advance()
            exponent = self.unary()   # right-associative: 2 ** 3 ** 2 = 2 ** 9
            return Binary("**", base, exponent, tok.line)
        return base

    def postfix(self) -> Node:
        expr = self.primary()
        while True:
            tok = self.peek()
            # A '(' or '[' on a new line starts a new statement, not a call/index.
            if tok.kind == "OP" and tok.value == "(" and tok.line == self.prev_line():
                self.advance()
                args = self.call_args(tok)
                expr = Call(expr, args, tok.line)
            elif tok.kind == "OP" and tok.value == "[" and tok.line == self.prev_line():
                self.advance()
                expr = self.index_or_slice(expr, tok)
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

    def call_args(self, open_tok: Token) -> list[Node]:
        args: list[Node] = []
        seen_keyword = False
        if not self.check_op(")"):
            while True:
                tok = self.peek()
                if tok.kind == "OP" and tok.value == "...":
                    self.advance()
                    args.append(Spread(self.expression(), tok.line))
                elif tok.kind == "IDENT" and self.peek_next().kind == "OP" and self.peek_next().value == "=":
                    self.advance()
                    self.advance()
                    args.append(KeywordArg(tok.value, self.expression(), tok.line))
                    seen_keyword = True
                else:
                    if tok.kind == "KEYWORD" and tok.value in NEW_KEYWORDS and self.peek_next().kind == "OP" and self.peek_next().value == "=":
                        raise keyword_as_name(tok)
                    value = self.expression()
                    if seen_keyword:
                        raise TBError("Named inputs (like b = 3) must come after the plain ones.", tok.line)
                    args.append(value)
                if not self.match("OP", ",") or self.check_op(")"):
                    break
        self.close(")", open_tok, "')' to finish the call")
        return args

    def index_or_slice(self, target: Node, open_tok: Token) -> Node:
        start = stop = step = None
        if not self.check_op(":"):
            start = self.expression()
        if self.match("OP", ":"):
            if not self.check_op(":") and not self.check_op("]"):
                stop = self.expression()
            if self.match("OP", ":"):
                if not self.check_op("]"):
                    step = self.expression()
            self.close("]", open_tok, "']' after the slice")
            return SliceExpr(target, start, stop, step, open_tok.line)
        self.close("]", open_tok, "']' after the index")
        return Index(target, start, open_tok.line)

    def looks_like_lambda(self) -> bool:
        """At '(' : is this '(params) =>' ?"""
        depth = 0
        i = self.pos
        while i < len(self.tokens):
            tok = self.tokens[i]
            if tok.kind == "OP" and tok.value in ("(", "[", "{"):
                depth += 1
            elif tok.kind == "OP" and tok.value in (")", "]", "}"):
                depth -= 1
                if depth == 0:
                    nxt = self.tokens[min(i + 1, len(self.tokens) - 1)]
                    return nxt.kind == "OP" and nxt.value == "=>"
            elif tok.kind == "EOF":
                return False
            i += 1
        return False

    def lambda_body(self, params: list[Param], tok: Token) -> FnExpr:
        self.expect("OP", "=>", "'=>'")
        if self.check_op("{") and not self.map_follows():
            body, is_generator = self.function_body()
            return FnExpr(params, body, tok.line, is_generator, None, True)
        self.fn_stack.append({"yield": False})
        loops, self.loop_depth = self.loop_depth, 0
        no_lambda, self.no_lambda = self.no_lambda, 0
        try:
            value = self.expression()
            if self.fn_stack[-1]["yield"]:
                raise TBError("A one-line '=>' function can't use yield; give it a block body: x => { yield x }", tok.line)
        finally:
            self.fn_stack.pop()
            self.loop_depth = loops
            self.no_lambda = no_lambda
        ret = Return(value, value.line)
        return FnExpr(params, [ret], tok.line, False, None, True)

    def map_follows(self) -> bool:
        """At '{': does this look like a map literal ({} or {key: ...}) rather than a block?"""
        nxt = self.peek_next()
        if nxt.kind == "OP" and nxt.value == "}":
            return True
        if nxt.kind == "OP" and nxt.value == "...":
            return True
        after = self.peek_at(2)
        return nxt.kind in ("STRING", "NUMBER", "IDENT", "FSTRING") and after.kind == "OP" and after.value == ":"

    def primary(self) -> Node:
        tok = self.peek()
        if tok.kind in ("NUMBER", "STRING", "BYTES"):
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
                returns = None
                if self.match("OP", "->"):
                    returns = self.type_annotation()
                body, is_generator = self.function_body()
                return FnExpr(params, body, tok.line, is_generator, returns)
            if tok.value == "async":  # async fn(x) { ... } as a value (TB 3.0)
                self.advance()
                if not self.check("KEYWORD", "fn"):
                    raise TBError("'async' goes right before 'fn', like: async fn load() { }", tok.line)
                node = self.primary()
                node.is_async = True
                self.check_async(node.is_generator, tok)
                return node
            if tok.value == "self":
                self.advance()
                return Name("self", tok.line)
            if tok.value == "super":
                self.advance()
                if not self.check_op("."):
                    raise TBError("Use super with a method name, like: super.init(name) or super.speak()", tok.line)
                self.advance()
                name = self.peek()
                if name.kind not in ("IDENT", "KEYWORD"):
                    raise TBError(f"I expected a method name after 'super.', but found {describe(name)}.", tok.line)
                self.advance()
                return Super(name.value, tok.line)
            if tok.value == "match":
                return self.match_expr()
            if tok.value in ("if", "yield"):
                return self.expression()
            raise keyword_as_name(tok)
        if tok.kind == "IDENT":
            nxt = self.peek_next()
            if nxt.kind == "OP" and nxt.value == "=>" and not self.no_lambda:
                self.advance()
                return self.lambda_body([Param(tok.value, None, "pos", None, tok.line)], tok)
            self.advance()
            return Name(tok.value, tok.line)
        if tok.kind == "OP" and tok.value == "(":
            if not self.no_lambda and self.looks_like_lambda():
                self.advance()
                params = self.param_list(")", "')' after the inputs")
                return self.lambda_body(params, tok)
            self.advance()
            expr = self.expression()
            self.close(")", tok, "')' to close the '('")
            return expr
        if tok.kind == "OP" and tok.value == "[":
            self.advance()
            items = []
            if not self.check_op("]"):
                while True:
                    if self.check_op("..."):
                        spread_tok = self.advance()
                        items.append(Spread(self.expression(), spread_tok.line))
                    else:
                        items.append(self.expression())
                    # A trailing comma before ']' is fine.
                    if not self.match("OP", ",") or self.check_op("]"):
                        break
            self.close("]", tok, "']' to close the list")
            return ListLit(items, tok.line)
        if tok.kind == "OP" and tok.value == "{":
            return self.map_literal()
        if tok.kind == "OP" and tok.value == "." and self.peek_next().kind == "NUMBER":
            raise TBError("Numbers need a digit before the dot, like 0.5.", tok.line)
        if tok.kind == "OP" and tok.value == "=>":
            raise TBError("A '=>' function needs its inputs first, like: x => x * 2 or (a, b) => a + b.", tok.line)
        raise TBError(f"I didn't expect {describe(tok)} here.", tok.line)

    def map_literal(self) -> MapLit:
        tok = self.advance()
        pairs: list = []
        while not self.check_op("}"):
            if self.check("EOF"):
                break
            if self.check_op("..."):
                spread_tok = self.advance()
                pairs.append(Spread(self.expression(), spread_tok.line))
            else:
                key = self.expression()
                if not self.check_op(":"):
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
            source, line, spec, conv = part
            sub = Parser(tokenize(source, line), self.base_dir)
            sub.fn_stack = self.fn_stack
            expr = sub.expression()
            if not sub.check("EOF"):
                raise TBError(
                    f"I couldn't understand {{{source.strip()}}} in this f-text.", line
                )
            parts.append(FHole(expr, spec, conv, line))
        return FString(parts, tok.line)

    # -- match ---------------------------------------------------------------

    def match_expr(self) -> Match:
        tok = self.advance()
        self.no_lambda += 1
        try:
            subject = self.or_expr()
        finally:
            self.no_lambda -= 1
        open_tok = self.expect("OP", "{", "'{' after the value to match")
        arms: list[MatchArm] = []
        while not self.check_op("}"):
            if self.check("EOF"):
                raise TBError("This 'match' was never closed. Did you forget a '}'?", open_tok.line)
            if self.match("OP", ","):
                continue
            arm_tok = self.peek()
            self.no_lambda += 1
            try:
                pattern = self.match_pattern()
                guard = None
                if self.match("KEYWORD", "if"):
                    guard = self.expression()
            finally:
                self.no_lambda -= 1
            if not self.check_op("=>"):
                found = self.peek()
                if found.kind == "OP" and found.value == ":":
                    raise TBError("Match arms use '=>' between the pattern and the result, like: 1 => \"one\"", found.line)
                raise TBError(f"I expected '=>' after this match pattern, but found {describe(found)}.", found.line)
            self.advance()
            if self.check_op("{") and not self.map_follows():
                body = self.block()
                arms.append(MatchArm(pattern, guard, body, True, arm_tok.line))
            else:
                self.arm_depth += 1
                try:
                    body = self.expression()
                finally:
                    self.arm_depth -= 1
                arms.append(MatchArm(pattern, guard, body, False, arm_tok.line))
        self.advance()
        if not arms:
            raise TBError("This 'match' has no arms. Add at least one, like: _ => \"other\"", tok.line)
        return Match(subject, arms, tok.line)

    def match_pattern(self):
        first = self.match_pattern_single()
        if self.check("KEYWORD", "or"):
            options = [first]
            while self.match("KEYWORD", "or"):
                options.append(self.match_pattern_single())
            return POr(options, first.line)
        return first

    def pattern_literal(self):
        """A literal value usable in a pattern, or None if the next token isn't one."""
        tok = self.peek()
        if tok.kind in ("NUMBER", "STRING", "BYTES"):
            self.advance()
            return PLiteral(tok.value, tok.line)
        if tok.kind == "OP" and tok.value == "-" and self.peek_next().kind == "NUMBER":
            self.advance()
            num = self.advance()
            return PLiteral(-num.value, tok.line)
        if tok.kind == "KEYWORD" and tok.value in ("true", "false", "nil"):
            self.advance()
            return PLiteral({"true": True, "false": False, "nil": None}[tok.value], tok.line)
        return None

    def match_pattern_single(self):
        tok = self.peek()
        lit = self.pattern_literal()
        if lit is not None:
            if self.peek().kind == "OP" and self.peek().value in ("..", "..="):
                op = self.advance()
                end = self.pattern_literal()
                if end is None or not isinstance(end.value, (int, float)) or not isinstance(lit.value, (int, float)):
                    raise TBError("A range pattern needs numbers on both sides, like 1..=5.", op.line)
                return PRange(lit.value, end.value, op.value == "..=", tok.line)
            return lit
        if tok.kind == "OP" and tok.value == "[":
            self.advance()
            items = []
            rest = None
            while not self.check_op("]"):
                if self.check_op("..."):
                    self.advance()
                    rest = "_"
                    if self.check("IDENT"):
                        rest = self.advance().value
                    self.match("OP", ",")
                    if not self.check_op("]"):
                        raise TBError("'...rest' must be the last thing in a list pattern.", tok.line)
                    break
                items.append(self.match_pattern())
                if not self.match("OP", ","):
                    break
            self.close("]", tok, "']' to close the pattern")
            return PList(items, rest, tok.line)
        if tok.kind == "OP" and tok.value == "{":
            self.advance()
            entries = []
            while not self.check_op("}"):
                key_tok = self.peek()
                if key_tok.kind in ("STRING", "NUMBER"):
                    self.advance()
                    key = key_tok.value
                else:
                    key = self.expect_name("a key").value
                if self.match("OP", ":"):
                    sub = self.match_pattern()
                else:
                    sub = PName(key if isinstance(key, str) else str(key), key_tok.line)
                entries.append((key, sub))
                if not self.match("OP", ","):
                    break
            self.close("}", tok, "'}' to close the pattern")
            return PMap(entries, tok.line)
        if tok.kind == "OP" and tok.value == "(":
            self.advance()
            inner = self.match_pattern()
            self.close(")", tok, "')' to close the pattern")
            return inner
        if tok.kind == "KEYWORD" and tok.value == "is":
            self.advance()
            return PIs(self.type_reference(), tok.line)
        if tok.kind == "IDENT":
            self.advance()
            if tok.value == "_":
                return PName("_", tok.line)
            node: Node = Name(tok.value, tok.line)
            dotted = False
            while self.check_op("."):
                self.advance()
                part = self.expect_name("a name after '.'")
                node = Dot(node, part.value, part.line)
                dotted = True
            if self.check_op("(") and self.same_line(self.peek()):
                open_tok = self.advance()
                binding = None
                if not self.check_op(")"):
                    binding = self.match_pattern()
                self.close(")", open_tok, "')' to close the class pattern")
                return PClass(node, binding, None, tok.line)
            if self.check_op("{") and self.same_line(self.peek()):
                open_tok = self.advance()
                fields = []
                while not self.check_op("}"):
                    fname = self.expect_name("a field name")
                    if self.match("OP", ":"):
                        sub = self.match_pattern()
                    else:
                        sub = PName(fname.value, fname.line)
                    fields.append((fname.value, sub))
                    if not self.match("OP", ","):
                        break
                self.close("}", open_tok, "'}' to close the class pattern")
                return PClass(node, None, fields, tok.line)
            if dotted:
                return PValue(node, tok.line)
            return PName(tok.value, tok.line)
        if tok.kind == "KEYWORD":
            raise keyword_as_name(tok)
        raise TBError(f"I didn't expect {describe(tok)} in a match pattern.", tok.line)


def parse(source: str, base_dir: str | None = None) -> Program:
    """Parse TB source into a Program (keeps comments on statements)."""
    parser = Parser(tokenize(source), base_dir)
    program = parser.parse_program()
    program.uses_export = parser.saw_export
    return program


def parse_expression(source: str, line: int = 1, base_dir: str | None = None) -> Node:
    """Parse one expression (used by Interpreter.eval and the debugger)."""
    parser = Parser(tokenize(source, line), base_dir)
    parser.fn_stack.append({"yield": False})
    expr = parser.expression()
    if not parser.check("EOF"):
        tok = parser.peek()
        raise TBError(f"I didn't expect {describe(tok)} here.", tok.line)
    return expr
