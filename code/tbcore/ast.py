"""The syntax tree.

Statement nodes also carry `comments` (the comment lines above them),
`blank_before` (blank lines above) and `trailing_comment` (a comment on the
same line), so `tb fmt` can print a program back with its comments.
Type annotations are kept as text (`annotation`, `returns`) and ignored at
run time.
"""

from __future__ import annotations


class Node:
    line: int = 0
    comments: list = []
    blank_before: int = 0
    trailing_comment: str | None = None
    end_line: int = 0


# -- statements --------------------------------------------------------------


class Program(Node):
    def __init__(self, body):
        self.body = body


class Let(Node):
    """let name = value / let [a, b] = xs / const NAME = value.
    target: a name (str) or a Pattern. name: the str when it's a plain name."""

    def __init__(self, target, value, line, is_const=False, annotation=None):
        self.target, self.value, self.line = target, value, line
        self.name = target if isinstance(target, str) else None
        self.is_const = is_const
        self.annotation = annotation


class Assign(Node):
    def __init__(self, target, value, line, op=None):
        # op is None for '=', or the operator text ('+', '**', '<<', ...) for '+=' etc.
        self.target, self.value, self.line, self.op = target, value, line, op


class MultiAssign(Node):
    """a, b = b, a  (value is one expression that must give a list)."""

    def __init__(self, targets, value, line):
        self.targets, self.value, self.line = targets, value, line


class Param:
    """One function input. kind: "pos" (normal), "rest" (...rest), "kwrest" (**opts)."""

    __slots__ = ("target", "default", "kind", "annotation", "line")

    def __init__(self, target, default=None, kind="pos", annotation=None, line=0):
        self.target, self.default, self.kind, self.annotation, self.line = target, default, kind, annotation, line

    @property
    def name(self) -> str | None:
        return self.target if isinstance(self.target, str) else None


class FnDecl(Node):
    def __init__(self, name, params, body, line, is_generator=False, returns=None, is_async=False):
        self.name, self.params, self.body, self.line = name, params, body, line
        self.is_generator, self.returns, self.is_async = is_generator, returns, is_async


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
    """for x in xs / for k, v in m / for [a, b] in pairs.
    targets: list of names (str) or Patterns; name: the str for one plain name."""

    def __init__(self, targets, iterable, body, line):
        if isinstance(targets, str):
            targets = [targets]
        self.targets, self.iterable, self.body, self.line = targets, iterable, body, line
        self.name = targets[0] if len(targets) == 1 and isinstance(targets[0], str) else None


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


class FromImport(Node):
    """from fs import read, write as w / from "helpers.tb" import a.
    names: list of (name, alias)."""

    def __init__(self, target, is_file, names, base_dir, line):
        self.target, self.is_file, self.names = target, is_file, names
        self.base_dir, self.line = base_dir, line


class Catch:
    """One catch clause: name (or None), types (list of expression nodes or
    None for "anything"), body."""

    __slots__ = ("name", "types", "body", "line")

    def __init__(self, name, types, body, line):
        self.name, self.types, self.body, self.line = name, types, body, line


class Try(Node):
    def __init__(self, body, catches, finally_body, line):
        self.body, self.catches, self.finally_body, self.line = body, catches, finally_body, line

    # 2.x shape, for anything that still reads it
    @property
    def name(self):
        return self.catches[0].name if self.catches else None

    @property
    def handler(self):
        return self.catches[0].body if self.catches else None


class Throw(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class ExprStmt(Node):
    def __init__(self, expr, line):
        self.expr, self.line = expr, line


class ClassDecl(Node):
    """class Name extends Base { let field = v; static let n = 0; fn m() {} }
    fields/statics: list of (name, value node or None, annotation);
    methods/static_methods: list of FnDecl."""

    def __init__(self, name, base, fields, statics, methods, static_methods, line):
        self.name, self.base, self.line = name, base, line
        self.fields, self.statics = fields, statics
        self.methods, self.static_methods = methods, static_methods


class EnumDecl(Node):
    def __init__(self, name, members, line):
        self.name, self.members, self.line = name, members, line


class Export(Node):
    """export fn/let/const/class/enum ..."""

    def __init__(self, decl, line):
        self.decl, self.line = decl, line


class Defer(Node):
    def __init__(self, body, line):
        self.body, self.line = body, line


class Assert(Node):
    def __init__(self, cond, message, line):
        self.cond, self.message, self.line = cond, message, line


# -- expressions -------------------------------------------------------------


class Literal(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class ListLit(Node):
    def __init__(self, items, line):
        self.items, self.line = items, line   # items may include Spread nodes


class MapLit(Node):
    def __init__(self, pairs, line):
        # pairs: (key node, value node) tuples, or Spread nodes
        self.pairs, self.line = pairs, line


class Spread(Node):
    """...expr inside a list, map or call."""

    def __init__(self, value, line):
        self.value, self.line = value, line


class KeywordArg(Node):
    """name = value inside a call."""

    def __init__(self, name, value, line):
        self.name, self.value, self.line = name, value, line


class FString(Node):
    def __init__(self, parts, line):
        # parts: plain text pieces and FHole nodes, in order
        self.parts, self.line = parts, line


class FHole(Node):
    """One {expr:spec!r} hole in an f-text."""

    def __init__(self, expr, spec, conv, line):
        self.expr, self.spec, self.conv, self.line = expr, spec, conv, line


class Name(Node):
    def __init__(self, name, line):
        self.name, self.line = name, line


class Unary(Node):
    def __init__(self, op, operand, line):
        self.op, self.operand, self.line = op, operand, line   # '-', 'not', '~'


class Binary(Node):
    def __init__(self, op, left, right, line):
        self.op, self.left, self.right, self.line = op, left, right, line


class Compare(Node):
    """A chained comparison: 0 < x <= 10 (operands has one more item than ops)."""

    def __init__(self, operands, ops, line):
        self.operands, self.ops, self.line = operands, ops, line


class IsCheck(Node):
    """value is Type / value is not Type. type_node is a Name or Dot."""

    def __init__(self, value, type_node, negated, line):
        self.value, self.type_node, self.negated, self.line = value, type_node, negated, line


class Logical(Node):
    def __init__(self, op, left, right, line):
        self.op, self.left, self.right, self.line = op, left, right, line


class Call(Node):
    def __init__(self, callee, args, line):
        self.callee, self.args, self.line = callee, args, line  # args: nodes, Spread, KeywordArg


class Index(Node):
    def __init__(self, target, index, line):
        self.target, self.index, self.line = target, index, line


class SliceExpr(Node):
    def __init__(self, target, start, stop, step, line):
        self.target, self.start, self.stop, self.step, self.line = target, start, stop, step, line


class Dot(Node):
    def __init__(self, target, name, line):
        self.target, self.name, self.line = target, name, line


class Super(Node):
    """super.name (used as super.name(...))."""

    def __init__(self, name, line):
        self.name, self.line = name, line


class FnExpr(Node):
    def __init__(self, params, body, line, is_generator=False, returns=None, is_lambda=False,
                 is_async=False):
        self.params, self.body, self.line = params, body, line
        self.is_generator, self.returns, self.is_lambda = is_generator, returns, is_lambda
        self.is_async = is_async


class RangeExpr(Node):
    def __init__(self, start, stop, inclusive, line):
        self.start, self.stop, self.inclusive, self.line = start, stop, inclusive, line


class Conditional(Node):
    """if cond then a else b"""

    def __init__(self, cond, then, otherwise, line):
        self.cond, self.then, self.otherwise, self.line = cond, then, otherwise, line


class Yield(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class Await(Node):
    def __init__(self, value, line):
        self.value, self.line = value, line


class MatchArm:
    __slots__ = ("pattern", "guard", "body", "is_block", "line")

    def __init__(self, pattern, guard, body, is_block, line):
        self.pattern, self.guard, self.body, self.is_block, self.line = pattern, guard, body, is_block, line


class Match(Node):
    def __init__(self, subject, arms, line):
        self.subject, self.arms, self.line = subject, arms, line


# -- destructuring patterns (let / for / params) -----------------------------


class Pattern(Node):
    pass


class NamePattern(Pattern):
    def __init__(self, name, line, annotation=None):
        self.name, self.line, self.annotation = name, line, annotation


class ListPattern(Pattern):
    """[a, b, ...rest]: items are Patterns; rest is a name or None."""

    def __init__(self, items, rest, line):
        self.items, self.rest, self.line = items, rest, line


class MapPattern(Pattern):
    """{name, ip: address, port = 22}: entries are (key, Pattern, default node or None)."""

    def __init__(self, entries, line):
        self.entries, self.line = entries, line


# -- match patterns ---------------------------------------------------------


class MatchPattern(Node):
    pass


class PLiteral(MatchPattern):
    def __init__(self, value, line):
        self.value, self.line = value, line


class PValue(MatchPattern):
    """An expression whose value must equal the subject (Level.low, a constant)."""

    def __init__(self, expr, line):
        self.expr, self.line = expr, line


class PName(MatchPattern):
    """A name that binds the subject ('_' matches anything without binding)."""

    def __init__(self, name, line):
        self.name, self.line = name, line


class POr(MatchPattern):
    def __init__(self, options, line):
        self.options, self.line = options, line


class PList(MatchPattern):
    def __init__(self, items, rest, line):
        self.items, self.rest, self.line = items, rest, line


class PMap(MatchPattern):
    def __init__(self, entries, line):
        self.entries, self.line = entries, line   # (key value, MatchPattern)


class PRange(MatchPattern):
    def __init__(self, start, stop, inclusive, line):
        self.start, self.stop, self.inclusive, self.line = start, stop, inclusive, line


class PClass(MatchPattern):
    """Name(binding) or Name {field: pattern}. binding: MatchPattern or None;
    fields: list of (name, MatchPattern) or None."""

    def __init__(self, type_node, binding, fields, line):
        self.type_node, self.binding, self.fields, self.line = type_node, binding, fields, line


class PIs(MatchPattern):
    def __init__(self, type_node, line):
        self.type_node, self.line = type_node, line
