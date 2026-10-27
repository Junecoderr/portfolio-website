"""`tb fmt`: the TB code formatter.

    tb fmt [PATHS...]      tidy every .tb file (folders are searched)
    tb fmt --check [PATHS] only report files that would change (exit 1 if any)
    tb fmt -               read a program on stdin, write the tidy version to stdout

The canonical style: 2-space indent, spaces around binary operators, one
statement per line, at most one blank line in a row, lines up to 100
characters (calls, lists and maps that don't fit are wrapped one item per
line, with a trailing comma), comments and blank lines kept where they were.

How it works: the program is parsed with tbcore's parser (a subclass that
also remembers which tokens each node came from), printed back from the tree
through a small Wadler-style layout engine, and then checked: the result must
parse to the same tree and keep every comment. Literals (numbers, text,
f-text, bytes) are printed exactly as typed. A statement holding a comment in
a place the printer doesn't know (say, between two operands) is kept as it
was written, re-indented; if even that isn't possible the file is left alone.
Formatting is idempotent: formatting a formatted file changes nothing.
"""

from __future__ import annotations

import os
import sys

from tbcore import ast as A
from tbcore.errors import TBError
from tbcore.lexer import (
    DIGITS, KEYWORDS, ONE_CHAR_OPS, THREE_CHAR_OPS, TWO_CHAR_OPS, _lex_fstring, _lex_number,
    _read_string_body, _string_prefix, tokenize,
)
from tbcore.parser import NAME_RE, Parser

__all__ = ["format_source", "main", "ast_signature", "FormatError", "find_tb_files", "WIDTH"]

WIDTH = 100
INDENT = 2
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
             ".pytest_cache", "tb_packages", "out"}


class FormatError(Exception):
    """The formatter couldn't produce a safe result (never for valid code; a bug guard)."""


# =============================================================================
# Raw token text and comment columns
# =============================================================================


def scan_raw(source: str) -> tuple[list[str], dict[int, int]]:
    """The exact source text of every token tbcore's lexer makes (EOF
    excluded), in the same order, and {token index: column of its trailing
    comment}."""
    spans, trailing_cols = scan_spans(source)
    return [source[a:b] for a, b in spans], trailing_cols


def scan_spans(source: str) -> tuple[list[tuple[int, int]], dict[int, int]]:
    """Like scan_raw, but the (start, end) character offsets of each token."""
    raws: list[tuple[int, int]] = []
    trailing_cols: dict[int, int] = {}
    i, n, line = 0, len(source), 1
    line_start = 0
    newline_since = True
    last_end_line = 0
    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            i += 1
            line_start = i
            newline_since = True
        elif c in " \t\r":
            i += 1
        elif c == "#":
            if raws and not newline_since and last_end_line == line:
                trailing_cols[len(raws) - 1] = i - line_start
            while i < n and source[i] != "\n":
                i += 1
        else:
            start, start_line = i, line
            if c in DIGITS:
                _, i = _lex_number(source, i, line)
            elif c in "fFrRbB" and _string_prefix(source, i):
                size = _string_prefix(source, i)
                prefix = source[i:i + size].lower()
                j = i + size
                quote = source[j]
                triple = source.startswith(quote * 3, j)
                if "f" in prefix:
                    _, i, line = _lex_fstring(source, j, line, triple, "r" in prefix)
                else:
                    _, i, line = _read_string_body(source, j + (3 if triple else 1), line, quote,
                                                   triple, "r" in prefix)
            elif c.isalpha() or c == "_":
                while i < n and (source[i].isalnum() or source[i] == "_"):
                    i += 1
            elif c in "\"'":
                triple = source.startswith(c * 3, i)
                _, i, line = _read_string_body(source, i + (3 if triple else 1), line, c, triple, False)
            elif source[i:i + 3] in THREE_CHAR_OPS:
                i += 3
            elif source[i:i + 2] in TWO_CHAR_OPS:
                i += 2
            elif c in ONE_CHAR_OPS:
                i += 1
            else:  # the real lexer has already raised a friendly error
                raise TBError(f"I don't understand the character {c!r}.", line)
            if line != start_line:
                line_start = source.rfind("\n", 0, i) + 1
            raws.append((start, i))
            newline_since = False
            last_end_line = line
    return raws, trailing_cols


# =============================================================================
# A parser that remembers token spans
# =============================================================================


class Block(list):
    """A block's statements, plus the token indexes of its '{' and '}'."""

    open_idx = -1
    close_idx = -1


class ArgList(list):
    open_idx = -1
    close_idx = -1


def _set(node, **attrs):
    for key, value in attrs.items():
        try:
            setattr(node, key, value)
        except AttributeError:  # __slots__ classes
            pass


class SpanParser(Parser):
    """tbcore's parser, noting the token range [start, end) of the nodes the
    formatter needs (statements, expressions, blocks, arguments, patterns)."""

    def __init__(self, tokens, base_dir=None):
        super().__init__(tokens, base_dir)
        self.class_members: list[list] = []

    def statement(self):
        start = self.pos
        node = super().statement()
        node._span = (start, self.pos)
        return node

    def expression(self):
        start = self.pos
        node = super().expression()
        if "_span" not in getattr(node, "__dict__", {}):
            _set(node, _span=(start, self.pos))
        return node

    def primary(self):
        start = self.pos
        tok = self.peek()
        node = super().primary()
        if tok.kind == "OP" and tok.value == "(" and not isinstance(node, A.FnExpr):
            if "_span" not in node.__dict__:
                node._span = (start + 1, self.pos - 1)
            node._parens = True
        elif "_span" not in node.__dict__:
            node._span = (start, self.pos)
        return node

    def block(self):
        start = self.pos
        body = super().block()
        result = Block(body)
        result.open_idx, result.close_idx = start, self.pos - 1
        return result

    def call_args(self, open_tok):
        start = self.pos - 1
        args = super().call_args(open_tok)
        result = ArgList(args)
        result.open_idx, result.close_idx = start, self.pos - 1
        return result

    def param_list(self, closer, what):
        start = self.pos - 1
        params = super().param_list(closer, what)
        result = ArgList(params)
        result.open_idx, result.close_idx = start, self.pos - 1
        return result

    def match_pattern_single(self):
        start = self.pos
        node = super().match_pattern_single()
        if "_span" not in node.__dict__:
            node._span = (start, self.pos)
        return node

    def match_pattern(self):
        start = self.pos
        node = super().match_pattern()
        if "_span" not in node.__dict__:
            node._span = (start, self.pos)
        return node

    def pattern(self):
        start = self.pos
        node = super().pattern()
        if "_span" not in node.__dict__:
            node._span = (start, self.pos)
        return node

    def export_statement(self):
        start = self.pos
        node = super().export_statement()
        node.decl._span = (start + 1, self.pos)
        return node

    def class_declaration(self):
        self.class_members.append([])
        try:
            node = super().class_declaration()
        finally:
            members = self.class_members.pop()
        node._members = members
        return node

    def check_member(self, tok, seen, what):
        super().check_member(tok, seen, what)
        if self.class_members:
            self.class_members[-1].append((what, tok, self.pos))


# =============================================================================
# The layout engine (Wadler / prettier style)
# =============================================================================


class Line:
    """A place that is a newline when its group breaks: soft → '' when flat,
    otherwise ' '. hard lines always break; blank adds an empty line."""

    __slots__ = ("soft", "hard", "blank")

    def __init__(self, soft=False, hard=False, blank=False):
        self.soft, self.hard, self.blank = soft, hard, blank


class Group:
    __slots__ = ("doc", "broken")

    def __init__(self, doc, broken=False, propagate=True):
        self.doc = doc
        # a forced line break breaks every group around it (unless propagate is off)
        self.broken = broken or (propagate and has_hard(doc))


class Indent:
    __slots__ = ("doc",)

    def __init__(self, doc):
        self.doc = doc


class IfBreak:
    __slots__ = ("brk", "flat")

    def __init__(self, brk, flat=""):
        self.brk, self.flat = brk, flat


class Comment:
    """A comment at the end of a line; tries to keep its old column."""

    __slots__ = ("text", "col")

    def __init__(self, text, col=None):
        self.text, self.col = text, col


class Verbatim:
    """Source lines kept as written (re-indented). keep: indexes of lines
    inside multi-line text, which are never touched."""

    __slots__ = ("lines", "keep")

    def __init__(self, lines, keep):
        self.lines, self.keep = lines, keep


class Choice:
    """Layouts to try in order (prettier's conditionalGroup): the first one
    whose first line fits, else the last one, broken."""

    __slots__ = ("variants", "hard")

    def __init__(self, variants, hard=False):
        self.variants = variants
        self.hard = hard   # breaks the groups around it (its last item is multi-line anyway)


SOFT = Line(soft=True)
LINE = Line()
HARD = Line(hard=True)
BLANK_LINE = Line(hard=True, blank=True)
FLAT, BREAK = 0, 1


def has_hard(doc) -> bool:
    """Does the doc certainly span several lines?"""
    stack = [doc]
    while stack:
        d = stack.pop()
        if isinstance(d, list):
            stack.extend(d)
        elif isinstance(d, Line):
            if d.hard:
                return True
        elif isinstance(d, Group):
            if d.broken:
                return True
        elif isinstance(d, Indent):
            stack.append(d.doc)
        elif isinstance(d, IfBreak):
            stack.append(d.flat)
        elif isinstance(d, (Comment, Verbatim)):
            return True
        elif isinstance(d, Choice):
            if d.hard:
                return True
    return False


def _fits(next_cmd, rest, width) -> bool:
    stack = [next_cmd]
    rest_idx = len(rest)
    while width >= 0:
        if not stack:
            if rest_idx == 0:
                return True
            rest_idx -= 1
            stack.append(rest[rest_idx])
            continue
        ind, mode, d = stack.pop()
        if isinstance(d, str):
            if "\n" in d:
                return width - len(d.split("\n", 1)[0]) >= 0
            width -= len(d)
        elif isinstance(d, list):
            for x in reversed(d):
                stack.append((ind, mode, x))
        elif isinstance(d, Indent):
            stack.append((ind, mode, d.doc))
        elif isinstance(d, Group):
            stack.append((ind, BREAK if d.broken else mode, d.doc))
        elif isinstance(d, IfBreak):
            stack.append((ind, mode, d.brk if mode == BREAK else d.flat))
        elif isinstance(d, Choice):
            stack.append((ind, mode, d.variants[-1] if mode == BREAK else d.variants[0]))
        elif isinstance(d, Line):
            if d.hard or mode == BREAK:
                return True
            if not d.soft:
                width -= 1
        elif isinstance(d, (Comment, Verbatim)):
            return True
    return False


def render(doc, width: int = WIDTH) -> str:
    out: list[str] = []
    col = 0
    at_start = True
    stack = [(0, BREAK, doc)]

    def emit(text: str, ind: int):
        nonlocal col, at_start
        if not text:
            return
        if at_start:
            out.append(" " * ind)
            col = ind
            at_start = False
        out.append(text)
        if "\n" in text:
            col = len(text.rsplit("\n", 1)[1])
        else:
            col += len(text)

    while stack:
        ind, mode, d = stack.pop()
        if isinstance(d, str):
            emit(d, ind)
        elif isinstance(d, list):
            for x in reversed(d):
                stack.append((ind, mode, x))
        elif isinstance(d, Indent):
            stack.append((ind + INDENT, mode, d.doc))
        elif isinstance(d, Group):
            if d.broken:
                stack.append((ind, BREAK, d.doc))
            elif mode == FLAT:
                stack.append((ind, FLAT, d.doc))
            else:
                flat = (ind, FLAT, d.doc)
                stack.append(flat if _fits(flat, stack, width - col) else (ind, BREAK, d.doc))
        elif isinstance(d, IfBreak):
            stack.append((ind, mode, d.brk if mode == BREAK else d.flat))
        elif isinstance(d, Choice):
            if mode == FLAT:
                stack.append((ind, FLAT, d.variants[0]))
            else:
                for variant in d.variants[:-1]:
                    cmd = (ind, FLAT, variant)
                    if _fits(cmd, stack, width - col):
                        stack.append(cmd)
                        break
                else:
                    stack.append((ind, BREAK, d.variants[-1]))
        elif isinstance(d, Line):
            if d.hard or mode == BREAK:
                out.append("\n\n" if d.blank else "\n")
                at_start = True
                col = ind
            elif not d.soft:
                emit(" ", ind)
        elif isinstance(d, Comment):
            if at_start:
                emit(d.text, ind)
            else:
                gap = 2
                if d.col is not None and d.col - col >= 2:
                    gap = d.col - col
                out.append(" " * gap + d.text)
                col += gap + len(d.text)
        elif isinstance(d, Verbatim):
            for k, text in enumerate(d.lines):
                if k:
                    out.append("\n")
                    at_start = True
                if k in d.keep:
                    out.append(text)
                    at_start = False
                elif text.strip():
                    emit(text, ind)
            col = len(out[-1]) if out else 0
    return "".join(out)


def join(sep, docs):
    result = []
    for k, d in enumerate(docs):
        if k:
            result.append(sep)
        result.append(d)
    return result


# =============================================================================
# The printer
# =============================================================================

# Binding strength of each expression (bigger binds tighter).
P_LOWEST, P_OR, P_AND, P_NOT, P_EQ, P_CMP, P_RANGE, P_BOR, P_BXOR, P_BAND, P_SHIFT, P_ADD, \
    P_MUL, P_UNARY, P_POW, P_POSTFIX, P_ATOM = range(17)

BINARY_PREC = {
    "==": P_EQ, "!=": P_EQ, "<": P_CMP, "<=": P_CMP, ">": P_CMP, ">=": P_CMP, "in": P_CMP,
    "not in": P_CMP, "|": P_BOR, "^": P_BXOR, "&": P_BAND, "<<": P_SHIFT, ">>": P_SHIFT,
    "+": P_ADD, "-": P_ADD, "*": P_MUL, "/": P_MUL, "%": P_MUL, "//": P_MUL, "**": P_POW,
}


class _NeedVerbatim(Exception):
    pass


def quote_text(value: str) -> str:
    """A TB text literal for value."""
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\0":
            out.append("\\0")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _is_name(text) -> bool:
    return isinstance(text, str) and bool(NAME_RE.fullmatch(text)) and text not in KEYWORDS


class Printer:
    def __init__(self, source: str, tokens, raw_by_id: dict, col_by_id: dict):
        self.source = source
        self.lines = source.split("\n")
        self.tokens = tokens
        self.raw_by_id = raw_by_id
        self.col_by_id = col_by_id
        self.claimed: set = set()
        # lines that sit inside multi-line text (never re-indented)
        self.string_lines: set[int] = set()
        for tok in tokens:
            if tok.kind in ("STRING", "FSTRING", "BYTES") and tok.end_line > tok.line:
                self.string_lines.update(range(tok.line + 1, tok.end_line + 1))

    # -- tokens and comments ---------------------------------------------------

    def raw(self, idx: int) -> str:
        tok = self.tokens[idx]
        return self.raw_by_id.get(id(tok), str(tok.value))

    def tok_is(self, idx: int, value: str) -> bool:
        if 0 <= idx < len(self.tokens):
            tok = self.tokens[idx]
            return tok.kind in ("OP", "KEYWORD", "IDENT") and tok.value == value
        return False

    def before(self, idx: int) -> list[str]:
        """Claim the comments above token idx: a list of comment texts and ""
        for blank lines (leading blank lines included)."""
        if idx < 0 or idx >= len(self.tokens) or (idx, "b") in self.claimed:
            return []
        self.claimed.add((idx, "b"))
        tok = self.tokens[idx]
        if not any(c for c in tok.comments_before):
            return []
        items = [""] if tok.blank_lines_before else []
        return items + list(tok.comments_before)

    def blank_before(self, idx: int) -> bool:
        tok = self.tokens[idx]
        if tok.comments_before and any(tok.comments_before):
            return bool(tok.blank_lines_before)
        return tok.blank_lines_before > 0

    def trailing(self, idx: int):
        """Claim the comment after token idx (a Comment doc or None)."""
        if idx < 0 or idx >= len(self.tokens) or (idx, "t") in self.claimed:
            return None
        tok = self.tokens[idx]
        if not tok.trailing_comment:
            return None
        self.claimed.add((idx, "t"))
        return Comment(tok.trailing_comment, self.col_by_id.get(id(tok)))

    def trailing_after(self, idx: int, *, separators=(";",)):
        """Trailing comments of token idx and of separator tokens right after it."""
        found = []
        c = self.trailing(idx)
        if c:
            found.append(c)
        k = idx + 1
        while k < len(self.tokens) and self.tokens[k].kind == "OP" and self.tokens[k].value in separators:
            c = self.trailing(k)
            if c:
                found.append(c)
            k += 1
        return found

    def unclaimed_in(self, start: int, end: int) -> bool:
        for k in range(start, min(end, len(self.tokens))):
            tok = self.tokens[k]
            if any(c for c in tok.comments_before) and (k, "b") not in self.claimed:
                return True
            if tok.trailing_comment and (k, "t") not in self.claimed:
                return True
        return False

    def claim_range(self, start: int, end: int) -> None:
        for k in range(start, min(end, len(self.tokens))):
            self.claimed.add((k, "b"))
            self.claimed.add((k, "t"))

    def after_separators(self, end: int, separators=(";",)) -> int:
        k = end
        while k < len(self.tokens) and self.tokens[k].kind == "OP" and self.tokens[k].value in separators:
            k += 1
        return k

    # -- statement lists ----------------------------------------------------------

    def comment_items(self, comments: list[str]) -> list:
        """Comment lines (and BLANK markers) for a statement list."""
        items: list = []
        for c in comments:
            items.append(BLANK if c == "" else c)
        return items

    def sequence(self, items: list):
        """Join statement docs, comment texts and BLANK markers with newlines:
        at most one blank line in a row, none at the start or the end."""
        result: list = []
        pending_blank = False
        for item in items:
            if item is BLANK:
                pending_blank = bool(result)
                continue
            if result:
                result.append(BLANK_LINE if pending_blank else HARD)
            pending_blank = False
            result.append(item)
        return result

    def statement_items(self, body: list, *, top: bool = False) -> list:
        items: list = []
        for k, stmt in enumerate(body):
            span = getattr(stmt, "_span", None)
            if span is None:  # pragma: no cover - SpanParser sets it on every statement
                raise _NeedVerbatim()
            start, end = span
            lead = self.before(start)
            if lead:
                items.extend(self.comment_items(lead))
            elif self.blank_before(start):
                items.append(BLANK)
            items.append(self.statement_doc(stmt))
        return items

    def statement_doc(self, stmt):
        start, end = stmt._span
        nxt = self.after_separators(end)
        try:
            doc = self.statement(stmt)
            trailing = self.trailing_after(end - 1)
            if self.unclaimed_in(start, end) or self.unclaimed_in(end, nxt):
                raise _NeedVerbatim()
        except _NeedVerbatim:
            return self.verbatim(start, end)
        return [doc, *trailing]

    def verbatim(self, start: int, end: int):
        """The statement's own source lines, if it has them to itself."""
        first, last = self.tokens[start], self.tokens[end - 1]
        nxt = self.after_separators(end)
        if start > 0 and self.tokens[start - 1].end_line >= first.line:
            raise _NeedVerbatim()
        if nxt < len(self.tokens) and self.tokens[nxt].kind != "EOF" and self.tokens[nxt].line <= last.end_line:
            raise _NeedVerbatim()
        self.claim_range(start + 1, nxt)
        self.claimed.add((start, "t"))
        lines = self.lines[first.line - 1:last.end_line]
        keep = {k for k in range(len(lines)) if first.line + k in self.string_lines}
        indents = [len(text) - len(text.lstrip(" \t")) for k, text in enumerate(lines)
                   if k not in keep and text.strip()]
        cut = min(indents) if indents else 0
        shaped = [text if k in keep else text[cut:].rstrip() for k, text in enumerate(lines)]
        # the last line may end inside text that keeps going: keep it whole
        return Verbatim(shaped, keep)

    def block(self, body, extra_comments=()):
        """{ statements } (always on several lines unless empty)."""
        open_idx = getattr(body, "open_idx", -1)
        close_idx = getattr(body, "close_idx", -1)
        head = list(extra_comments)
        c = self.trailing(open_idx)
        if c:
            head.append(c)
        items = self.statement_items(body)
        tail = self.before(close_idx)
        if tail:
            items.extend(self.comment_items(tail))
        if not any(item is not BLANK for item in items):
            if head:
                return Group(["{", *head, HARD, "}"], broken=True)
            return "{}"
        # a broken group, so the groups inside decide their layout again even
        # when the block sits in an expression printed flat (f(fn() { ... }))
        return Group(["{", *head, Indent([HARD, self.sequence(items)]), HARD, "}"], broken=True)

    # -- statements -------------------------------------------------------------------

    def statement(self, node):
        method = getattr(self, "s_" + type(node).__name__, None)
        if method is None:  # pragma: no cover
            raise _NeedVerbatim()
        return method(node)

    def s_ExprStmt(self, node):
        return self.expr(node.expr)

    def s_Let(self, node):
        word = "const" if node.is_const else "let"
        if node.name is not None:
            target = node.name
            if node.annotation:
                target += ": " + node.annotation
            return Group([word, " ", target, " =", self.rhs(node.value)])
        start = node._span[0] if hasattr(node, "_span") else -1
        pattern = node.target
        if (isinstance(pattern, A.ListPattern) and pattern.rest is None and start >= 0
                and not self.tok_is(start + 1, "[")):
            target = join(", ", [self.pattern(p) for p in pattern.items])
        else:
            target = self.pattern(pattern)
        return Group([word, " ", target, " =", self.rhs(node.value)])

    def rhs(self, value):
        """' value' after '=' (a long value is wrapped by its own groups)."""
        return [" ", self.expr(value)]

    def s_Assign(self, node):
        op = "=" if node.op is None else node.op + "="
        return Group([self.expr(node.target, P_POSTFIX), " ", op, self.rhs(node.value)])

    def s_MultiAssign(self, node):
        targets = join(", ", [self.expr(t, P_POSTFIX) for t in node.targets])
        value = node.value
        start = node._span[0] if hasattr(node, "_span") else -1
        eq = self.find_top(start, node._span[1], "=") if start >= 0 else -1
        if isinstance(value, A.ListLit) and eq >= 0 and not self.tok_is(eq + 1, "[") \
                and not getattr(value, "_parens", False):
            values = join(", ", [self.expr(v) for v in value.items])
            return Group([targets, " = ", values])
        return Group([targets, " =", self.rhs(value)])

    def find_top(self, start, end, value) -> int:
        depth = 0
        for k in range(start, end):
            tok = self.tokens[k]
            if tok.kind == "OP":
                if tok.value in "([{":
                    depth += 1
                elif tok.value in ")]}":
                    depth -= 1
                elif depth == 0 and tok.value == value:
                    return k
        return -1

    def s_FnDecl(self, node):
        head = ["async fn " if node.is_async else "fn ", node.name, self.params(node.params)]
        if node.returns:
            head.append(" -> " + node.returns)
        return [*head, " ", self.block(node.body)]

    def params(self, params):
        if not params:
            return "()"
        items = [(self.param(p), None) for p in params]
        return self.collection("(", ")", items, getattr(params, "open_idx", -1),
                               getattr(params, "close_idx", -1), comments=False)

    def param(self, p):
        if p.kind == "rest":
            return "..." + p.target
        if p.kind == "kwrest":
            return "**" + p.target
        doc = [p.target if isinstance(p.target, str) else self.pattern(p.target)]
        if p.annotation:
            doc.append(": " + p.annotation)
        if p.default is not None:
            doc += [" = ", self.expr(p.default)]
        return doc

    def s_Return(self, node):
        if node.value is None:
            return "return"
        start = node._span[0] if hasattr(node, "_span") else -1
        value = node.value
        if (isinstance(value, A.ListLit) and start >= 0 and not self.tok_is(start + 1, "[")
                and not getattr(value, "_parens", False)):
            return Group(["return ", join(", ", [self.expr(v) for v in value.items])])
        return ["return ", self.expr(value)]

    def s_If(self, node):
        doc: list = []
        for k, (cond, body) in enumerate(node.branches):
            doc.append("if " if k == 0 else " else if ")
            doc += [self.expr(cond), " "]
            extra = []
            if k > 0:
                prev = node.branches[k - 1][1]
                extra = self.closing_comments(prev)
            doc.append(self.block(body, extra))
        if node.otherwise is not None:
            extra = self.closing_comments(node.branches[-1][1])
            doc += [" else ", self.block(node.otherwise, extra)]
        return doc

    def closing_comments(self, block):
        """A comment after a '}' that is followed by else/catch/finally."""
        c = self.trailing(getattr(block, "close_idx", -1))
        return [c] if c else []

    def s_While(self, node):
        return ["while ", self.expr(node.cond), " ", self.block(node.body)]

    def s_For(self, node):
        targets = join(", ", [t if isinstance(t, str) else self.pattern(t) for t in node.targets])
        return ["for ", targets, " in ", self.expr(node.iterable), " ", self.block(node.body)]

    def s_Break(self, node):
        return "break"

    def s_Continue(self, node):
        return "continue"

    def plain_tokens(self, start, end) -> str:
        parts = [self.raw(k) for k in range(start, end)]
        text = " ".join(parts)
        return text.replace(" ,", ",")

    def s_Import(self, node):
        start, end = node._span
        return self.plain_tokens(start, end)

    def s_FromImport(self, node):
        start, end = node._span
        return self.plain_tokens(start, end)

    def s_Try(self, node):
        doc: list = ["try ", self.block(node.body)]
        prev = node.body
        for catch in node.catches:
            head = " catch "
            if catch.name:
                head += catch.name
                if catch.types:
                    head += ": " + " or ".join(self.type_ref(t) for t in catch.types)
                head += " "
            doc += [head, self.block(catch.body, self.closing_comments(prev))]
            prev = catch.body
        if node.finally_body is not None:
            doc += [" finally ", self.block(node.finally_body, self.closing_comments(prev))]
        return doc

    def type_ref(self, node) -> str:
        if isinstance(node, A.Name):
            return node.name
        if isinstance(node, A.Dot):
            return self.type_ref(node.target) + "." + node.name
        raise _NeedVerbatim()

    def s_Throw(self, node):
        return ["throw ", self.expr(node.value)]

    def s_Assert(self, node):
        doc = ["assert ", self.expr(node.cond)]
        if node.message is not None:
            doc += [", ", self.expr(node.message)]
        return doc

    def s_Defer(self, node):
        return ["defer ", self.block(node.body)]

    def s_Export(self, node):
        return ["export ", self.statement(node.decl)]

    def s_EnumDecl(self, node):
        start, end = node._span
        open_idx = start
        while open_idx < end and not self.tok_is(open_idx, "{"):
            open_idx += 1
        close_idx = end - 1
        items = []
        k = open_idx + 1
        for member in node.members:
            while k < close_idx and not (self.tokens[k].kind == "IDENT" and self.tokens[k].value == member):
                k += 1
            items.append((member, (k, k + 1)))
        return ["enum ", node.name, " ",
                self.collection("{", "}", items, open_idx, close_idx, pad=True, trailing_comma=True)]

    def s_ClassDecl(self, node):
        start, end = node._span
        head = "class " + node.name
        if node.base is not None:
            head += " extends " + self.type_ref(node.base)
        open_idx = start
        while open_idx < end and not self.tok_is(open_idx, "{"):
            open_idx += 1
        close_idx = end - 1
        members = self.class_member_order(node, open_idx, close_idx)
        head_comments = []
        c = self.trailing(open_idx)
        if c:
            head_comments.append(c)
        items: list = []
        for kind, is_static, obj, m_start, m_end in members:
            if m_start is not None:
                lead = self.before(m_start)
                if lead:
                    items.extend(self.comment_items(lead))
                elif self.blank_before(m_start):
                    items.append(BLANK)
            if kind == "field":
                name, value, annotation = obj
                word = "let"
                if m_start is not None:
                    k = m_start + (1 if is_static else 0)
                    if self.tok_is(k, "const"):
                        word = "const"
                doc = [("static " if is_static else ""), word, " ", name]
                if annotation:
                    doc.append(": " + annotation)
                if value is not None:
                    doc += [" =", self.rhs(value)]
                doc = Group(doc)
            else:
                doc = [("static " if is_static else ""), self.s_FnDecl(obj)]
            if m_end is not None:
                doc = [doc, *self.trailing_after(m_end - 1)]
            items.append(doc)
        tail = self.before(close_idx)
        if tail:
            items.extend(self.comment_items(tail))
        if not any(item is not BLANK for item in items):
            body = ["{", *head_comments, HARD, "}"] if head_comments else "{}"
        else:
            body = ["{", *head_comments, Indent([HARD, self.sequence(items)]), HARD, "}"]
        return [head, " ", body]

    def class_member_order(self, node, open_idx, close_idx):
        """(kind, is_static, member, start, end) in source order."""
        records = getattr(node, "_members", None) or []
        fields = {f[0]: f for f in node.fields}
        statics = {f[0]: f for f in node.statics}
        methods = {m.name: m for m in node.methods}
        static_methods = {m.name: m for m in node.static_methods}
        index_of = {id(tok): k for k, tok in enumerate(self.tokens)}
        result = []
        prev_end = open_idx + 1
        for what, tok, end in records:
            name_idx = index_of.get(id(tok))
            if name_idx is None:
                result = None
                break
            start = name_idx - 1
            if self.tok_is(start - 1, "async"):
                start -= 1
            is_static = self.tok_is(start - 1, "static")
            if is_static:
                start -= 1
            if what == "field":
                obj = (statics if is_static else fields).get(tok.value)
            else:
                obj = (static_methods if is_static else methods).get(tok.value)
            if obj is None:
                result = None
                break
            result.append(("field" if what == "field" else "method", is_static, obj, start, end))
            prev_end = end
        total = len(node.fields) + len(node.statics) + len(node.methods) + len(node.static_methods)
        if result is None or len(result) != total:
            result = ([("field", True, f, None, None) for f in node.statics]
                      + [("field", False, f, None, None) for f in node.fields]
                      + [("method", True, m, None, None) for m in node.static_methods]
                      + [("method", False, m, None, None) for m in node.methods])
        return result

    # -- collections ---------------------------------------------------------------

    def collection(self, open_text, close_text, items, open_idx, close_idx, *, pad=False,
                   trailing_comma=True, comments=True, keep_broken=False, hug_last=False,
                   hug_first=False):
        """open item, item, ... close: on one line if it fits, else one item per
        line with a trailing comma. items: (doc, (start, end) or None)."""
        edge = LINE if pad else SOFT
        if not items:
            tail = self.before(close_idx) if comments and close_idx >= 0 else []
            c = self.trailing(open_idx) if comments and open_idx >= 0 else None
            if not tail and not c:
                return open_text + close_text
            inner = [x for x in self.comment_items(tail) if x is not BLANK]
            body = [open_text]
            if c:
                body.append(c)
            if inner:
                body.append(Indent([HARD, join(HARD, inner)]))
            return Group([*body, HARD, close_text], broken=True)
        broken = keep_broken
        forced = False   # comments or a multi-line item before the last: always one per line
        parts: list = []
        opener_comment = self.trailing(open_idx) if comments and open_idx >= 0 else None
        if opener_comment:
            forced = True
        prev_end = open_idx + 1
        n = len(items)
        for k, (doc, span) in enumerate(items):
            lead: list = []
            trail: list = []
            if comments and span is not None:
                s, e = span
                for j in range(prev_end, s + 1):
                    lead += [x for x in self.before(j) if x]
                if span and self.blank_before(s) and k:
                    lead = [BLANK] + lead
                trail = self.trailing_after(e - 1, separators=(",",))
                prev_end = e + (1 if self.tok_is(e, ",") else 0)
            if any(x is not BLANK for x in lead) or trail:
                forced = True
            if k:
                parts.append(Line(blank=True) if (lead and lead[0] is BLANK) else LINE)
            for x in lead:
                if x is not BLANK:
                    parts += [x, HARD]
            parts.append(doc)
            if k < n - 1:
                parts.append(",")
            elif trailing_comma:
                parts.append(IfBreak(",", ""))
            parts += trail
            if k < n - 1 and has_hard(doc) and not (hug_first and k == 0):
                forced = True
        tail = []
        if comments and close_idx >= 0:
            for j in range(prev_end, close_idx + 1):
                tail += [x for x in self.before(j) if x]
        if tail:
            forced = True
            for x in tail:
                parts += [HARD, x]
        body = [open_text]
        if opener_comment:
            body.append(opener_comment)
        body += [Indent([edge, parts]), edge, close_text]
        if hug_first and not forced and not broken and not any(has_hard(d) for d, _s in items[1:]):
            # test.raises(fn() { ... }, "message"): the function first, the rest after it
            first = items[0][0]
            tail_docs: list = []
            for doc, _span in items[1:]:
                tail_docs += [", ", doc]
            return Choice([[open_text, first, *tail_docs, close_text],
                           [open_text, Group(first, broken=True), *tail_docs, close_text],
                           Group(body, broken=True)], hard=has_hard(first))
        if hug_last and not forced and not broken:
            # f(a, b, fn() { ... }) / f({...}): keep the head on the line and
            # let only the last item spread over several lines.
            head = [open_text]
            for doc, _span in items[:-1]:
                head += [doc, ", "]
            last = items[-1][0]
            return Choice([[*head, last, close_text],
                           [*head, Group(last, broken=True), close_text],
                           Group(body, broken=True)], hard=has_hard(last))
        return Group(body, broken=broken or forced)

    def span_of(self, node):
        span = getattr(node, "_span", None)
        if span is None:
            return None
        s, e = span
        if getattr(node, "_parens", False):
            s, e = s - 1, e + 1
        return (s, e)

    def item_span(self, node):
        """Token range of a list/call item (with '...' or 'name =' in front)."""
        if isinstance(node, A.Spread):
            inner = self.span_of(node.value)
            return (inner[0] - 1, inner[1]) if inner else None
        if isinstance(node, A.KeywordArg):
            inner = self.span_of(node.value)
            return (inner[0] - 2, inner[1]) if inner else None
        return self.span_of(node)

    def source_broken(self, node) -> bool:
        """Was a list/map written with a newline right after its opener?"""
        span = getattr(node, "_span", None)
        if span is None:
            return False
        s, e = span
        if e - s < 3:
            return False
        return self.tokens[s + 1].line > self.tokens[s].end_line

    # -- expressions -----------------------------------------------------------------

    def prec(self, node) -> int:
        t = type(node)
        if t is A.Logical:
            return P_OR if node.op == "or" else P_AND
        if t is A.Unary:
            # 'not' at the start of an operand always reads as the low 'not'
            return P_NOT if node.op == "not" else P_UNARY
        if t is A.Binary:
            return BINARY_PREC[node.op]
        if t in (A.Compare, A.IsCheck):
            return P_CMP
        if t is A.RangeExpr:
            return P_RANGE
        if t is A.Await:
            return P_UNARY
        if t in (A.Conditional, A.Yield):
            return P_LOWEST
        if t is A.FnExpr and node.is_lambda:
            return P_LOWEST
        if t in (A.Call, A.Index, A.SliceExpr, A.Dot, A.Super):
            return P_POSTFIX
        return P_ATOM

    def expr(self, node, min_prec: int = P_LOWEST):
        doc = getattr(self, "e_" + type(node).__name__)(node)
        if getattr(node, "_parens", False) or self.prec(node) < min_prec:
            span = getattr(node, "_span", None)
            if getattr(node, "_parens", False) and span is not None:
                # comments right inside the brackets are not supported
                pass
            return ["(", doc, ")"]
        return doc

    def e_Literal(self, node):
        span = getattr(node, "_span", None)
        if span is not None and span[1] - span[0] == 1:
            tok = self.tokens[span[0]]
            if tok.kind in ("NUMBER", "STRING", "BYTES") or (tok.kind == "KEYWORD" and tok.value in ("true", "false", "nil")):
                return self.raw(span[0])
        value = node.value
        if value is None:
            return "nil"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, str):
            return quote_text(value)
        if isinstance(value, bytes):  # pragma: no cover - bytes always have a token
            return "b" + quote_text(value.decode("latin-1"))
        return repr(value)

    def e_FString(self, node):
        span = getattr(node, "_span", None)
        if span is None or span[1] - span[0] != 1:  # pragma: no cover
            raise _NeedVerbatim()
        return self.raw(span[0])

    def e_Name(self, node):
        return node.name

    def e_Super(self, node):
        return "super." + node.name

    def e_Unary(self, node):
        if node.op == "not":
            return ["not ", self.expr(node.operand, P_NOT)]
        return [node.op, self.expr(node.operand, P_UNARY)]

    def e_Await(self, node):
        return ["await ", self.expr(node.value, P_UNARY)]

    def e_Binary(self, node):
        p = BINARY_PREC[node.op]
        if node.op == "**":
            return [self.expr(node.left, P_POSTFIX), " ** ", self.expr(node.right, P_UNARY)]
        if p == P_CMP:
            return [self.expr(node.left, P_RANGE), f" {node.op} ", self.expr(node.right, P_RANGE)]
        return self.chain(node, p)

    def e_Logical(self, node):
        return self.chain(node, P_OR if node.op == "or" else P_AND)

    def chain(self, node, p):
        """a + b - c / x and y and z: one group that, when too long, breaks
        after each operator (an operator never starts a line: inside a match
        arm that would end the arm)."""
        ops, operands = [], []
        cur = node
        while (type(cur) in (A.Binary, A.Logical) and self.prec(cur) == p
               and (cur is node or not getattr(cur, "_parens", False))):
            ops.append(cur.op)
            operands.append(cur.right)
            cur = cur.left
        rest: list = []
        for op, operand in zip(reversed(ops), reversed(operands)):
            rest += [" " + op, LINE, self.expr(operand, p + 1)]
        return Group([self.expr(cur, p), Indent(rest)], propagate=False)

    def e_Compare(self, node):
        doc = [self.expr(node.operands[0], P_RANGE)]
        for op, operand in zip(node.ops, node.operands[1:]):
            doc += [f" {op} ", self.expr(operand, P_RANGE)]
        return doc

    def e_IsCheck(self, node):
        return [self.expr(node.value, P_RANGE), " is not " if node.negated else " is ",
                self.type_ref(node.type_node)]

    def e_RangeExpr(self, node):
        return [self.expr(node.start, P_BOR), "..=" if node.inclusive else "..", self.expr(node.stop, P_BOR)]

    def e_Conditional(self, node):
        return Group(["if ", self.expr(node.cond), " then ", self.expr(node.then), " else ",
                      self.expr(node.otherwise)])

    def e_Yield(self, node):
        if node.value is None:
            return "yield"
        return ["yield ", self.expr(node.value)]

    def e_Spread(self, node):
        return ["...", self.expr(node.value)]

    def e_KeywordArg(self, node):
        return [node.name, " = ", self.expr(node.value)]

    def e_Dot(self, node):
        return [self.expr(node.target, P_POSTFIX), ".", node.name]

    def e_Index(self, node):
        return [self.expr(node.target, P_POSTFIX), "[", self.expr(node.index), "]"]

    def e_SliceExpr(self, node):
        doc = [self.expr(node.target, P_POSTFIX), "["]
        if node.start is not None:
            doc.append(self.expr(node.start))
        doc.append(":")
        if node.stop is not None:
            doc.append(self.expr(node.stop))
        if node.step is not None:
            doc += [":", self.expr(node.step)]
        doc.append("]")
        return doc

    def e_Call(self, node):
        args = node.args
        items = [(self.expr(a), self.item_span(a)) for a in args]
        hug = bool(args) and isinstance(args[-1], (A.FnExpr, A.MapLit, A.ListLit, A.Match))
        first_fn = (len(args) >= 2 and isinstance(args[0], A.FnExpr) and isinstance(args[0].body, Block)
                    and not any(isinstance(a, (A.FnExpr, A.MapLit, A.ListLit, A.Match)) for a in args[1:]))
        if first_fn:
            hug = False
        return [self.expr(node.callee, P_POSTFIX),
                self.collection("(", ")", items, getattr(args, "open_idx", -1),
                                getattr(args, "close_idx", -1), hug_last=hug, hug_first=first_fn)]

    def e_ListLit(self, node):
        span = getattr(node, "_span", None)
        items = [(self.expr(a), self.item_span(a)) for a in node.items]
        open_idx, close_idx = (span[0], span[1] - 1) if span else (-1, -1)
        return self.collection("[", "]", items, open_idx, close_idx,
                               keep_broken=self.source_broken(node))

    def e_MapLit(self, node):
        span = getattr(node, "_span", None)
        items = []
        for pair in node.pairs:
            if isinstance(pair, A.Spread):
                items.append((self.expr(pair), self.item_span(pair)))
                continue
            key, value = pair
            ks, vs = self.span_of(key), self.span_of(value)
            items.append(([self.expr(key), ": ", self.expr(value)],
                           (ks[0], vs[1]) if ks and vs else None))
        open_idx, close_idx = (span[0], span[1] - 1) if span else (-1, -1)
        return self.collection("{", "}", items, open_idx, close_idx,
                               keep_broken=self.source_broken(node))

    def map_needs_parens(self, node) -> bool:
        """Would '{' here be read as a block instead of this map?"""
        if not isinstance(node, A.MapLit) or getattr(node, "_parens", False):
            return False
        if not node.pairs or isinstance(node.pairs[0], A.Spread):
            return False
        key = node.pairs[0][0]
        if getattr(key, "_parens", False):
            return True
        if isinstance(key, A.Name) or isinstance(key, A.FString):
            return False
        if isinstance(key, A.Literal) and isinstance(key.value, (str, int, float)) \
                and not isinstance(key.value, bool):
            span = getattr(key, "_span", None)
            return not (span and span[1] - span[0] == 1)
        return True

    def braced_value(self, node):
        doc = self.expr(node)
        return ["(", doc, ")"] if self.map_needs_parens(node) else doc

    def e_FnExpr(self, node):
        if not node.is_lambda:
            head = ["async fn" if getattr(node, "is_async", False) else "fn", self.params(node.params)]
            if node.returns:
                head.append(" -> " + node.returns)
            return [*head, " ", self.block(node.body)]
        params = node.params
        if (len(params) == 1 and params[0].kind == "pos" and isinstance(params[0].target, str)
                and params[0].default is None and not params[0].annotation):
            head = params[0].target
        else:
            head = self.params(params) if params else "()"
        body = node.body
        if (len(body) == 1 and isinstance(body[0], A.Return) and body[0].value is not None
                and not isinstance(body, Block)):
            return [head, " => ", self.braced_value(body[0].value)]
        return [head, " => ", self.block(body)]

    def e_Match(self, node):
        span = getattr(node, "_span", None)
        open_idx = close_idx = -1
        if span is not None:
            close_idx = span[1] - 1
            depth = 0
            for k in range(span[0] + 1, close_idx):
                tok = self.tokens[k]
                if tok.kind == "OP" and tok.value in "([":
                    depth += 1
                elif tok.kind == "OP" and tok.value in ")]":
                    depth -= 1
                elif depth == 0 and tok.kind == "OP" and tok.value == "{":
                    open_idx = k
                    break
        head = ["match ", self.expr(node.subject, P_OR), " "]
        opener = self.trailing(open_idx) if open_idx >= 0 else None
        items: list = []
        for k, arm in enumerate(node.arms):
            pspan = getattr(arm.pattern, "_span", None)
            if pspan is not None:
                lead = self.before(pspan[0])
                if lead:
                    items.extend(self.comment_items(lead))
                elif self.blank_before(pspan[0]) and k:
                    items.append(BLANK)
            doc = [self.pattern(arm.pattern)]
            if arm.guard is not None:
                doc += [" if ", self.expr(arm.guard)]
            doc.append(" => ")
            if arm.is_block:
                doc.append(self.block(arm.body))
                end = getattr(arm.body, "close_idx", -1) + 1
            else:
                doc.append(self.braced_value(arm.body))
                bspan = self.span_of(arm.body)
                end = bspan[1] if bspan else 0
            if end > 0:
                doc += self.trailing_after(end - 1, separators=(",",))
            items.append(doc)
        tail = self.before(close_idx) if close_idx >= 0 else []
        if tail:
            items.extend(self.comment_items(tail))
        body = ["{", *([opener] if opener else []), Indent([HARD, self.sequence(items)]), HARD, "}"]
        return [*head, Group(body, broken=True)]

    # -- patterns ---------------------------------------------------------------------

    def pattern(self, node):
        t = type(node)
        if t is A.NamePattern:
            return node.name
        if t is A.ListPattern:
            items = [self.pattern(p) for p in node.items]
            if node.rest is not None:
                items.append("..." + node.rest)
            return ["[", join(", ", items), "]"]
        if t is A.MapPattern:
            items = []
            for key, sub, default in node.entries:
                if isinstance(sub, A.NamePattern) and sub.name == key and _is_name(key):
                    item = [key]
                else:
                    item = [key if _is_name(key) else quote_text(key), ": ", self.pattern(sub)]
                if default is not None:
                    item += [" = ", self.expr(default)]
                items.append(item)
            return ["{", join(", ", items), "}"]
        if t is A.PName:
            return node.name
        if t is A.PLiteral or t is A.PRange:
            span = getattr(node, "_span", None)
            if span is None:
                raise _NeedVerbatim()
            return "".join(self.raw(k) for k in range(*span))
        if t is A.PValue:
            return self.expr(node.expr)
        if t is A.POr:
            return join(" or ", [self.pattern(p) for p in node.options])
        if t is A.PList:
            items = [self.pattern(p) for p in node.items]
            if node.rest is not None:
                items.append("..." if node.rest == "_" else "..." + node.rest)
            return ["[", join(", ", items), "]"]
        if t is A.PMap:
            items = []
            for key, sub in node.entries:
                if isinstance(key, str):
                    if isinstance(sub, A.PName) and sub.name == key and _is_name(key):
                        items.append(key)
                        continue
                    key_doc = quote_text(key)
                else:
                    if isinstance(sub, A.PName) and sub.name == str(key):
                        items.append(repr(key))
                        continue
                    key_doc = repr(key)
                items.append([key_doc, ": ", self.pattern(sub)])
            return ["{", join(", ", items), "}"]
        if t is A.PClass:
            name = self.type_ref(node.type_node)
            if node.fields is not None:
                items = []
                for fname, sub in node.fields:
                    if isinstance(sub, A.PName) and sub.name == fname:
                        items.append(fname)
                    else:
                        items.append([fname, ": ", self.pattern(sub)])
                return [name, " {", join(", ", items), "}"]
            return [name, "(", self.pattern(node.binding) if node.binding is not None else "", ")"]
        if t is A.PIs:
            return "is " + self.type_ref(node.type_node)
        raise _NeedVerbatim()  # pragma: no cover

    # -- the program ----------------------------------------------------------------------

    def program(self, program) -> str:
        items = self.statement_items(program.body, top=True)
        eof = len(self.tokens) - 1
        tail = self.before(eof)
        if tail:
            items.extend(self.comment_items(tail))
        doc = self.sequence(items)
        text = render(doc)
        return text + "\n" if text else ""


BLANK = object()


# =============================================================================
# Public API
# =============================================================================


def _parse_for_format(source: str):
    tokens = tokenize(source)
    raws, trailing_cols = scan_raw(source)
    if len(raws) != len(tokens) - 1:  # pragma: no cover - a lexer change we don't know about
        raise FormatError("the formatter's view of the tokens doesn't match the lexer")
    raw_by_id = {id(tok): raw for tok, raw in zip(tokens, raws)}
    col_by_id = {id(tokens[k]): col for k, col in trailing_cols.items()}
    parser = SpanParser(tokens)
    program = parser.parse_program()
    program.uses_export = parser.saw_export
    return program, parser.tokens, raw_by_id, col_by_id


def format_source(source: str) -> str:
    """The canonical layout of a TB program. Raises TBError when the program
    doesn't parse, FormatError if the result could not be made safely."""
    source = source.replace("\r\n", "\n")
    if source.startswith("﻿"):
        source = source[1:]
    program, tokens, raw_by_id, col_by_id = _parse_for_format(source)
    printer = Printer(source, tokens, raw_by_id, col_by_id)
    try:
        text = printer.program(program)
    except _NeedVerbatim:
        return source if source.endswith("\n") or not source else source + "\n"
    if printer.unclaimed_in(0, len(tokens)):
        return source if source.endswith("\n") or not source else source + "\n"
    # safety: the same program, every comment kept
    try:
        again = A_signature(text)
    except TBError as err:
        raise FormatError(f"the formatted program doesn't parse: {err}") from None
    if again != ast_signature(program):
        raise FormatError("the formatted program means something different")
    if _comment_count(text) != _comment_count(source):
        raise FormatError("a comment went missing")
    return text


def _comment_count(source: str) -> int:
    tokens = tokenize(source)
    count = 0
    for tok in tokens:
        count += sum(1 for c in tok.comments_before if c)
        count += 1 if tok.trailing_comment else 0
    return count


def A_signature(source: str):
    from tbcore.parser import parse

    return ast_signature(parse(source))


_IGNORED = {"line", "comments", "blank_before", "trailing_comment", "end_line", "comments_after",
            "base_dir", "trailing_comments", "open_idx", "close_idx"}


def ast_signature(node):
    """A comparable form of a syntax tree: no positions, comments or layout."""
    if isinstance(node, (list, tuple)):
        return tuple(ast_signature(x) for x in node)
    if isinstance(node, (str, int, float, bool, bytes)) or node is None:
        return (type(node).__name__, node)
    fields = {}
    values = dict(getattr(node, "__dict__", {}))
    for cls in type(node).__mro__:
        for name in getattr(cls, "__slots__", ()):
            if hasattr(node, name):
                values[name] = getattr(node, name)
    for key, value in sorted(values.items()):
        if key in _IGNORED or key.startswith("_"):
            continue
        fields[key] = ast_signature(value)
    return (type(node).__name__, tuple(sorted(fields.items())))


# =============================================================================
# Command line
# =============================================================================


def find_tb_files(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for folder, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
                for name in sorted(files):
                    if name.endswith(".tb"):
                        found.append(os.path.join(folder, name))
        else:
            found.append(path)
    return found


USAGE = """\
tb fmt - tidy the layout of TB programs

  tb fmt [FILES or FOLDERS]   rewrite each .tb file in the canonical style
  tb fmt --check [...]        only list the files that would change (exit 1 if any)
  tb fmt -                    read a program on stdin, write it tidied to stdout

With no paths, formats every .tb file under the current folder."""


def main(args: list[str] | None = None) -> int:
    args = list(args or [])
    if any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return 0
    check = "--check" in args
    args = [a for a in args if a != "--check"]
    unknown = [a for a in args if a.startswith("--")]
    if unknown:
        print(f"Oops! tb fmt doesn't know the option '{unknown[0]}'. See: tb fmt --help", file=sys.stderr)
        return 2
    if args == ["-"]:
        source = sys.stdin.read()
        try:
            text = format_source(source)
        except TBError as err:
            print(err, file=sys.stderr)
            return 1
        except FormatError as err:
            print(f"Oops! tb fmt couldn't tidy this program safely ({err}).", file=sys.stderr)
            return 1
        if check:
            return 0 if text == source else 1
        sys.stdout.write(text)
        return 0
    files = find_tb_files(args or ["."])
    if not files:
        print("No .tb files found.")
        return 0
    changed, failed = [], 0
    for path in files:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                source = f.read()
        except OSError as err:
            print(f"Oops! I couldn't read '{path}': {err.strerror or err}.", file=sys.stderr)
            failed += 1
            continue
        # Keep the file's own line endings: a Windows file saved with CRLF
        # is tidy when only its line endings differ from the canonical text.
        crlf = "\r\n" in source
        if crlf:
            source = source.replace("\r\n", "\n")
        try:
            text = format_source(source)
        except TBError as err:
            print(f"{path}: {err}", file=sys.stderr)
            failed += 1
            continue
        except FormatError as err:
            print(f"{path}: tb fmt couldn't tidy this file safely ({err}); left it as it is.",
                  file=sys.stderr)
            failed += 1
            continue
        if text == source:
            continue
        changed.append(path)
        if check:
            print(f"would reformat {path}")
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace("\n", "\r\n") if crlf else text)
            print(f"formatted {path}")
    total = len(files)
    if check:
        if changed:
            print(f"{len(changed)} of {total} file{'s' if total != 1 else ''} would be reformatted.")
        else:
            print(f"All {total} file{'s' if total != 1 else ''} already tidy.")
        return 1 if changed or failed else 0
    print(f"{len(changed)} file{'s' if len(changed) != 1 else ''} reformatted, "
          f"{total - len(changed) - failed} already tidy" + (f", {failed} with errors." if failed else "."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
