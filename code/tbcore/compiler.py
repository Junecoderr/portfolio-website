"""The compiler: turns the syntax tree into bytecode for the VM (tbcore/vm.py).

The VM must behave exactly like the tree-walking interpreter (tbcore/interp.py),
so the compiler reproduces the tree-walker's scoping rules with static slots:

* Every block that the tree-walker runs in a fresh Env (a function body, an
  if/else branch, one loop iteration, a try/catch/finally body, a match arm)
  is a *block scope* here. Each name a block declares gets its own slot in the
  frame's `fast` list.
* A use of a name inside one function looks for a declaration made *earlier
  in the text* of an enclosing block of the same function (in a block the
  statements run in order, so "earlier in the text" is "already declared"),
  then in the enclosing functions, then in the globals.
* A variable that a nested function uses is kept in a Cell (the closure
  shares the cell). Cells of a block are made fresh each time the block is
  entered, like the tree-walker's fresh Env per loop iteration. A nested
  function may run before the outer `let` has: its cell is still UNSET, so
  the VM falls back to the next candidate outward (ending with the globals),
  just as the tree-walker's Env chain lookup does.
* The top level of a program or module stores its names in the globals Env
  (a dict), looked up dynamically, so the REPL, `eval()` and the debugger
  keep working with plain Envs.

Functions are compiled lazily (the first time they are called) from a
FnTemplate that knows how to find the function's free variables in the frame
that makes it. Each template can produce two Code variants: the fast one, and
a traced one with a LINE instruction before every statement (used when
`Interpreter.trace` is set, for the debugger and `tb test --coverage`).
"""

from __future__ import annotations

import weakref

from . import ast as A
from .errors import TBError

# ---------------------------------------------------------------------------
# Opcodes. The hot ones come first: the VM's dispatch tests them in order.
# ---------------------------------------------------------------------------

OPNAMES = [
    # 0-15: the hottest instructions (tested first by the VM loop)
    "LOAD_FAST", "LOAD_CONST", "STORE_FAST", "LOAD_GLOBAL", "JUMP_IF_FALSE", "JUMP", "CALL",
    "ADD", "SUB", "LT", "RETURN", "LOAD_METHOD", "CALL_METHOD", "GET_ATTR", "LOAD_DEREF", "EQ",
    # 16-39: common
    "FOR_ITER", "POP", "INDEX", "MUL", "GT", "LE", "GE", "NE", "SET_ATTR", "SET_INDEX",
    "STORE_DEREF", "JUMP_IF_TRUE", "LOOP", "LINE", "ENTER", "YIELD", "BUILD_LIST", "DUP",
    "FAST_ADD_CONST", "FAST_LT_JUMP", "FAST_CMP_CONST_JUMP", "INC_FAST", "LOAD_FAST2", "NOT",
    # 40-47: hot ones for top-level code (globals), constants and text
    "STORE_GLOBAL", "LOAD_GLOBAL_UPD", "GLOBAL_INC", "CONST_OP", "GLOBAL_CMP_CONST_JUMP", "MOD",
    "FORMAT_SIMPLE", "BUILD_STRING", "LOAD_ATTR_FAST", "FOR_ITER_STORE", "CONST_CMP_JUMP",
    # 51+: everything else (dispatched through a table)
    "DECLARE_GLOBAL", "CHECK_GLOBAL_ASSIGN", "LOAD_GLOBAL_KEY", "LOAD_GLOBAL_SOFT",
    "DECLARE_DEREF", "LOAD_FAST_CHK", "STORE_FAST_CHK", "CONST_ERROR", "NEW_CELLS", "CLEAR_SLOTS",
    "SWAP", "ROT3", "DIV", "FLOORDIV", "POW", "BINARY", "COMPARE_CHAIN", "NEG", "INV",
    "JUMP_IF_FALSE_KEEP", "JUMP_IF_TRUE_KEEP", "GET_ITER", "SETUP_TRY", "SETUP_FINALLY",
    "POP_BLOCK", "RERAISE", "CATCH_MATCH", "ERROR_VALUE", "THROW", "ASSERT_FAIL", "CALL_EX",
    "MAKE_FUNCTION", "MAKE_CLASS", "MAKE_ENUM", "DEFER", "EXPORT", "IMPORT", "FROM_IMPORT",
    "IMPORT_NAME", "LIST_APPEND", "LIST_EXTEND", "MAP_NEW", "MAP_KEY", "MAP_PUT", "MAP_SPREAD",
    "FORMAT_VALUE", "SLICE_PREP", "SLICE", "SET_ATTR_OP", "SET_INDEX_OP",
    "MAKE_RANGE", "IS_TYPE", "IS_TYPE_NAMED", "SUPER", "SUPER_ERROR", "AWAIT", "UNPACK_SEQ",
    "LIST_UNPACK", "MAP_SOURCE", "MAP_TAKE", "DEFAULT_CHECK", "MATCH_LITERAL", "MATCH_VALUE",
    "MATCH_RANGE", "MATCH_LIST", "MATCH_REST", "MATCH_MAP", "MATCH_MAP_KEY", "MATCH_CLASS",
    "MATCH_FIELD", "MATCH_FAIL", "TRUNC_STACK", "MARK_STACK", "GET_ITEM_RAW", "BUILD_KWARGS",
    "TRUTHY",
]
for _i, _name in enumerate(OPNAMES):
    globals()[_name] = _i
del _i, _name
N_HOT = 51          # opcodes below this are handled inline by the VM loop

# Binary operators with their own opcode (the rest use BINARY with the operator text).
BINARY_OPS = {"+": ADD, "-": SUB, "*": MUL, "<": LT, ">": GT, "<=": LE, ">=": GE,  # noqa: F821
              "==": EQ, "!=": NE, "/": DIV, "//": FLOORDIV, "%": MOD, "**": POW}  # noqa: F821


class _Marker:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


UNSET = _Marker("UNSET")      # a slot or cell that holds no value yet
MISSING = _Marker("MISSING")  # a parameter that takes its default
NOBASE = _Marker("NOBASE")    # a class without 'extends'


class Label:
    __slots__ = ("pos",)

    def __init__(self):
        self.pos = -1


class Code:
    """One compiled function body (or a program / module / eval top level)."""

    __slots__ = (
        "ops", "args", "lines", "stmt_lines", "nlocals", "name", "file", "kind", "traced",
        "varinfo", "fallbacks", "entry", "simple", "nparams", "is_method", "pos_params",
        "pos_param_nodes", "rest_slot", "kwrest_slot", "free_base", "nfree", "cell_params",
        "entry_cells", "top_names", "free_names",
    )

    def __init__(self):
        self.ops: list = []
        self.args: list = []
        self.lines: list = []        # the line of each instruction (for errors)
        self.stmt_lines: list = []   # the line of the statement each instruction belongs to
        self.nlocals = 0
        self.name = None
        self.file = None
        self.kind = "function"       # function | main | defer
        self.traced = False
        self.varinfo: list = []      # (name, slot, start_pc, end_pc, is_cell, depth)
        self.fallbacks: dict = {}    # pc -> tuple of fallback places for UNSET slots/cells
        self.entry = 0               # pc of the ENTER instruction
        self.simple = False          # plain positional params only: the VM's fast call path
        self.nparams = 0             # slots the caller fills (self, %class, params)
        self.is_method = False
        self.pos_params: list = []   # (name or None, has_default)
        self.pos_param_nodes: list = []
        self.rest_slot = -1
        self.kwrest_slot = -1
        self.free_base = 0
        self.nfree = 0
        self.cell_params: tuple = ()  # param slots that become cells
        self.entry_cells: tuple = ()  # other function-level slots that are cells
        self.top_names: list = []    # (name, slot, is_cell) declared at the function's top level
        self.free_names: list = []   # (name, slot) of free variables

    def dis(self) -> str:  # pragma: no cover - a debugging aid
        out = []
        for pc, (op, arg) in enumerate(zip(self.ops, self.args)):
            out.append(f"{pc:4} {self.lines[pc]:4} {OPNAMES[op]:<20} {arg!r}")
        return "\n".join(out)


class FnTemplate:
    """Everything needed to make a function value at run time: the syntax
    node, and where its free variables live in the frame that makes it."""

    __slots__ = ("name", "params", "body", "line", "is_generator", "is_async", "kind", "file",
                 "is_method", "free_sources", "outer_map", "doc", "_code", "_tcode", "node",
                 "const_refs", "__weakref__")

    def __init__(self, name, params, body, line, file, kind="function", is_generator=False,
                 is_async=False, is_method=False, node=None):
        self.name, self.params, self.body, self.line, self.file = name, params, body, line, file
        self.kind = kind
        self.is_generator, self.is_async, self.is_method = is_generator, is_async, is_method
        self.free_sources: tuple = ()   # slots of the making frame, one per free variable
        self.outer_map: dict = {}       # name -> list of free-variable indexes, innermost first
        self.const_refs: dict = {}      # name -> the outer Var it finds first (is it a const?)
        self.doc = ""
        self._code = None
        self._tcode = None
        self.node = node

    def code(self, traced: bool) -> Code:
        if traced:
            if self._tcode is None:
                self._tcode = FuncCompiler(self, traced=True).compile_function()
            return self._tcode
        if self._code is None:
            self._code = FuncCompiler(self, traced=False).compile_function()
        return self._code


# ---------------------------------------------------------------------------
# Static analysis helpers
# ---------------------------------------------------------------------------

_CHILD_CACHE: dict = {}


def _children(node):
    """The child values of a syntax node (Nodes, lists, tuples, Params, ...)."""
    if isinstance(node, (list, tuple)):
        return node
    d = getattr(node, "__dict__", None)
    if d is not None:
        return list(d.values())
    out = []
    for name in getattr(type(node), "__slots__", ()):
        out.append(getattr(node, name, None))
    return out


_LEAF_TYPES = (str, int, float, bool, bytes, type(None))
_WALKABLE = (A.Node, list, tuple, A.Param, A.Catch, A.MatchArm)
_FN_LIKE = (A.FnDecl, A.FnExpr, A.ClassDecl, A.Defer)
_REFS = weakref.WeakKeyDictionary()


def _walk_refs(node, out: set) -> None:
    if isinstance(node, A.Name):
        out.add(node.name)
        return
    if isinstance(node, A.Super):
        out.add("self")
        out.add("%class")
        return
    for child in _children(node):
        if isinstance(child, _WALKABLE):
            _walk_refs(child, out)


def refs(node) -> frozenset:
    """Every name a function-like node (or anything inside it) refers to."""
    try:
        return _REFS[node]
    except (KeyError, TypeError):
        pass
    out: set = set()
    _walk_refs(node, out)
    result = frozenset(out)
    try:
        _REFS[node] = result
    except TypeError:
        pass
    return result


def nested_fns(node, out: list) -> list:
    """Function-like nodes inside node, without looking inside them."""
    if isinstance(node, _FN_LIKE):
        out.append(node)
        if isinstance(node, A.ClassDecl):
            # the base and the static values run in the outer scope
            if node.base is not None:
                nested_fns(node.base, out)
            for _name, value, _ann in node.statics:
                if value is not None:
                    nested_fns(value, out)
        return out
    if isinstance(node, _WALKABLE):
        for child in _children(node):
            if isinstance(child, _WALKABLE):
                nested_fns(child, out)
    return out


def captured_by(nodes) -> set:
    names: set = set()
    for fn in nested_fns(nodes, []):
        if isinstance(fn, A.ClassDecl):
            for decl in fn.methods + fn.static_methods:
                names |= refs(decl)
            for _name, value, _ann in fn.fields:
                if value is not None:
                    names |= refs(value)
        else:
            names |= refs(fn)
    return names


def pattern_names(pattern) -> list:
    """Names a destructuring pattern (let / for / params) declares."""
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


def match_names(pattern, out: list, maybe: set, in_or: bool = False) -> list:
    """Names a match pattern binds; names bound inside an `or` go in maybe too."""
    kind = type(pattern)
    if kind is A.PName:
        if pattern.name != "_":
            out.append(pattern.name)
            if in_or:
                maybe.add(pattern.name)
    elif kind is A.POr:
        for option in pattern.options:
            match_names(option, out, maybe, True)
    elif kind is A.PList:
        for item in pattern.items:
            match_names(item, out, maybe, in_or)
        if pattern.rest is not None and pattern.rest != "_":
            out.append(pattern.rest)
            if in_or:
                maybe.add(pattern.rest)
    elif kind is A.PMap:
        for _key, sub in pattern.entries:
            match_names(sub, out, maybe, in_or)
    elif kind is A.PClass:
        if pattern.binding is not None:
            match_names(pattern.binding, out, maybe, in_or)
        for _name, sub in pattern.fields or ():
            match_names(sub, out, maybe, in_or)
    return out


def decl_names(stmts) -> list:
    """Names the statements of one block declare directly."""
    names = []
    for stmt in stmts:
        kind = type(stmt)
        if kind is A.Export:
            stmt = stmt.decl
            kind = type(stmt)
        if kind is A.Let:
            names.extend(pattern_names(stmt.target))
        elif kind in (A.FnDecl, A.ClassDecl, A.EnumDecl):
            names.append(stmt.name)
        elif kind is A.Import:
            names.append(stmt.alias)
        elif kind is A.FromImport:
            names.extend(alias for _name, alias in stmt.names)
    return names


def contains_type(node, kinds) -> bool:
    if isinstance(node, kinds):
        return True
    if isinstance(node, _FN_LIKE):
        return False
    if isinstance(node, _WALKABLE):
        for child in _children(node):
            if isinstance(child, _WALKABLE) and contains_type(child, kinds):
                return True
    return False


_SIMPLE_EXPR = (A.Literal, A.Name, A.Binary, A.Unary, A.Compare, A.Logical, A.ListLit, A.MapLit,
                A.FString, A.FHole, A.Conditional, A.RangeExpr, A.Spread, A.IsCheck)


def is_simple_expr(node) -> bool:
    """No calls, yields or blocks: evaluating it can't run other code first."""
    if isinstance(node, (list, tuple)):
        return all(is_simple_expr(x) for x in node)
    if isinstance(node, _LEAF_TYPES):
        return True
    if not isinstance(node, _SIMPLE_EXPR):
        return False
    for child in _children(node):
        if isinstance(child, _WALKABLE) and not is_simple_expr(child):
            return False
    return True


def _fold(op, a, b):
    """Constant folding for number literals, with the interpreter's own rules."""
    from .interp import Interpreter

    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        if isinstance(a, str) and isinstance(b, str) and op == "+" and len(a) + len(b) < 10_000:
            return (a + b,)
        return None
    if op not in ("+", "-", "*", "/", "//", "%"):
        return None
    try:
        result = Interpreter.binary(None, op, a, b, 0)
    except Exception:
        return None
    return (result,)


# ---------------------------------------------------------------------------
# Scopes during compilation
# ---------------------------------------------------------------------------


class Var:
    __slots__ = ("slot", "cell", "const", "maybe")

    def __init__(self, slot, cell, const=False, maybe=False):
        self.slot, self.cell, self.const, self.maybe = slot, cell, const, maybe


class BlockScope:
    __slots__ = ("is_global", "vars", "declared", "all", "start", "depth")

    def __init__(self, is_global=False, depth=0):
        self.is_global = is_global
        self.vars: dict = {}          # name -> Var (every name this block declares)
        self.declared: set = set()    # names declared so far (in the text)
        self.all: set = set()
        self.start = 0
        self.depth = depth


class Loop:
    __slots__ = ("break_label", "continue_label", "handlers", "mark_slot", "pops")

    def __init__(self, break_label, continue_label, handlers, mark_slot):
        self.break_label, self.continue_label = break_label, continue_label
        self.handlers = handlers      # len(self.handlers) when the loop started
        self.mark_slot = mark_slot


class FuncCompiler:
    """Compiles one function body (or a top level) into a Code object."""

    def __init__(self, template: FnTemplate | None, traced: bool, *, kind: str = "function",
                 file=None, name=None):
        self.tmpl = template
        self.traced = traced
        self.code = Code()
        self.code.traced = traced
        self.code.kind = kind if template is None else template.kind
        self.code.file = file if template is None else template.file
        self.code.name = name if template is None else template.name
        self.file = self.code.file
        self.ops = self.code.ops
        self.args = self.code.args
        self.lines = self.code.lines
        self.stmt_lines = self.code.stmt_lines
        self.nlocals = 0
        self.blocks: list[BlockScope] = []
        self.handlers: list = []      # ("try",) / ("finally", body) entries, innermost last
        self.loops: list[Loop] = []
        self.stmt_line = template.line if template is not None else 0
        self.outer_map: dict = template.outer_map if template is not None else {}
        self.free_slots: list = []
        self.in_expr_block = 0        # > 0 while compiling a block inside an expression (match)
        self.class_ctx = None

    # -- emitting -------------------------------------------------------------

    def emit(self, op, arg=None, line=0):
        self.ops.append(op)
        self.args.append(arg)
        self.lines.append(line)
        self.stmt_lines.append(self.stmt_line)
        return len(self.ops) - 1

    def mark(self, label: Label) -> None:
        label.pos = len(self.ops)

    def new_slot(self) -> int:
        slot = self.nlocals
        self.nlocals += 1
        return slot

    def assemble(self) -> Code:
        code = self.code
        args = code.args
        targets = set()
        for i, arg in enumerate(args):
            if type(arg) is Label:
                args[i] = arg.pos
                targets.add(arg.pos)
            elif type(arg) is tuple and any(type(a) is Label for a in arg):
                targets.update(a.pos for a in arg if type(a) is Label)
                args[i] = tuple(a.pos if type(a) is Label else a for a in arg)
        code.nlocals = max(self.nlocals, 1)
        self.peephole(targets)
        return code

    def peephole(self, targets) -> None:
        """Fuse common instruction runs into superinstructions. The fused
        instruction jumps over the rest of the run, which stays in place (so
        jump targets stay valid); runs that something jumps into are left alone."""
        ops, args = self.code.ops, self.code.args
        n = len(ops)

        def free(*positions):
            return all(p < n and p not in targets for p in positions)

        i = 0
        while i < n - 1:
            op = ops[i]
            if op == LOAD_GLOBAL_UPD and free(i + 1, i + 2, i + 3) and ops[i + 1] == LOAD_CONST \
                    and type(args[i + 1]) is int and ops[i + 2] == ADD and ops[i + 3] == STORE_GLOBAL \
                    and args[i + 3] == args[i]:  # noqa: F821
                ops[i] = GLOBAL_INC  # noqa: F821
                args[i] = (args[i], args[i + 1], i + 4)
                i += 4
                continue
            if op == LOAD_GLOBAL and free(i + 1, i + 2, i + 3) and ops[i + 1] == LOAD_CONST \
                    and type(args[i + 1]) is int and ops[i + 2] in (LT, LE, GT, GE, EQ, NE) \
                    and ops[i + 3] == JUMP_IF_FALSE:  # noqa: F821
                ops[i] = GLOBAL_CMP_CONST_JUMP  # noqa: F821
                args[i] = (args[i], args[i + 1], ops[i + 2], args[i + 3], i + 4)
                i += 4
                continue
            if op == FOR_ITER and free(i + 1) and ops[i + 1] == STORE_FAST:  # noqa: F821
                ops[i] = FOR_ITER_STORE  # noqa: F821
                args[i] = (args[i], args[i + 1], i + 2)
                i += 2
                continue
            if op == LOAD_CONST and free(i + 1, i + 2) and type(args[i]) is int \
                    and ops[i + 1] in (LT, LE, GT, GE, EQ, NE) and ops[i + 2] == JUMP_IF_FALSE:  # noqa: F821
                ops[i] = CONST_CMP_JUMP  # noqa: F821
                args[i] = (ops[i + 1], args[i], args[i + 2], i + 3)
                i += 3
                continue
            if op == LOAD_CONST and free(i + 1) and type(args[i]) is int \
                    and ops[i + 1] in (ADD, SUB, MUL, MOD, FLOORDIV, LT, LE, GT, GE, EQ, NE) \
                    and not (ops[i + 1] in (MOD, FLOORDIV) and args[i] == 0):  # noqa: F821
                ops[i] = CONST_OP  # noqa: F821
                args[i] = (ops[i + 1], args[i], i + 2)
                i += 2
                continue
            if op == LOAD_FAST and i + 3 < n and (i + 1) not in targets and (i + 2) not in targets \
                    and (i + 3) not in targets:  # noqa: F821
                # x op const then jump-if-false:  if x < 10 / while i < n
                op1, op2, op3 = ops[i + 1], ops[i + 2], ops[i + 3]
                if op1 == LOAD_CONST and op2 in (LT, LE, GT, GE, EQ, NE) and op3 == JUMP_IF_FALSE \
                        and type(args[i + 1]) is int:  # noqa: F821
                    ops[i] = FAST_CMP_CONST_JUMP  # noqa: F821
                    args[i] = (args[i], args[i + 1], op2, args[i + 3], i + 4)
                    i += 4
                    continue
                if op1 == LOAD_FAST and op2 == LT and op3 == JUMP_IF_FALSE:  # noqa: F821
                    ops[i] = FAST_LT_JUMP  # noqa: F821
                    args[i] = (args[i], args[i + 1], args[i + 3], i + 4)
                    i += 4
                    continue
            if op == LOAD_FAST and i + 3 < n and (i + 1) not in targets and (i + 2) not in targets \
                    and (i + 3) not in targets:  # noqa: F821
                # x = x + const  /  x += const
                op1, op2, op3 = ops[i + 1], ops[i + 2], ops[i + 3]
                if op1 == LOAD_CONST and op2 == ADD and op3 == STORE_FAST and args[i + 3] == args[i] \
                        and type(args[i + 1]) is int:  # noqa: F821
                    ops[i] = INC_FAST  # noqa: F821
                    args[i] = (args[i], args[i + 1], i + 4)
                    i += 4
                    continue
            if op == LOAD_FAST and (i + 1) not in targets and i + 2 < n and (i + 2) not in targets:  # noqa: F821
                op1, op2 = ops[i + 1], ops[i + 2]
                if op1 == LOAD_CONST and op2 == ADD and type(args[i + 1]) is int:  # noqa: F821
                    ops[i] = FAST_ADD_CONST  # noqa: F821
                    args[i] = (args[i], args[i + 1], i + 3)
                    i += 3
                    continue
            if op == LOAD_FAST and free(i + 1) and ops[i + 1] == GET_ATTR:  # noqa: F821
                ops[i] = LOAD_ATTR_FAST  # noqa: F821
                args[i] = (args[i], args[i + 1])
                i += 2
                continue
            if op == LOAD_FAST and (i + 1) not in targets and ops[i + 1] == LOAD_FAST:  # noqa: F821
                ops[i] = LOAD_FAST2  # noqa: F821
                args[i] = (args[i], args[i + 1])
                i += 2
                continue
            i += 1

    # -- scopes -----------------------------------------------------------------

    def push_block(self, names, nodes, *, is_global=False, preset: dict | None = None,
                   maybe: set | None = None, const_names: set | None = None) -> BlockScope:
        """Start a block scope declaring names (plus decl_names of its statements,
        given by the caller in names). nodes: what runs inside (to find captures)."""
        block = BlockScope(is_global, len(self.blocks))
        block.start = len(self.ops)
        self.blocks.append(block)
        if is_global:
            return block
        all_names = []
        for name in names:
            if name not in block.all:
                block.all.add(name)
                all_names.append(name)
        captured = captured_by(nodes) if all_names else set()
        new_cells = []
        for name in all_names:
            cell = name in captured
            if preset is not None and name in preset:
                slot = preset[name]
            else:
                slot = self.new_slot()
                if cell:
                    new_cells.append(slot)
            block.vars[name] = Var(slot, cell, False, bool(maybe) and name in maybe)
        if new_cells and preset is None:
            self.emit(NEW_CELLS, tuple(new_cells))  # noqa: F821
        if maybe:
            clear = tuple(block.vars[n].slot for n in maybe if n in block.vars and not block.vars[n].cell)
            if clear:
                self.emit(CLEAR_SLOTS, clear)  # noqa: F821
        return block

    def pop_block(self) -> None:
        block = self.blocks.pop()
        end = len(self.ops)
        for name, var in block.vars.items():
            self.code.varinfo.append((name, var.slot, block.start, end, var.cell, block.depth))

    def declare(self, name, const=False) -> None:
        block = self.blocks[-1]
        block.declared.add(name)
        if not block.is_global:
            var = block.vars.get(name)
            if var is not None:
                var.const = const

    def resolve(self, name):
        """Where a use of name (at this point of the text) finds it:
        ("fast", Var) / ("deref", Var) / ("free", [free slots]) / ("global", None)."""
        for block in reversed(self.blocks):
            if block.is_global:
                break
            if name in block.declared:
                var = block.vars[name]
                return ("deref" if var.cell else "fast"), var
        idxs = self.outer_map.get(name)
        if idxs:
            return "free", idxs
        return "global", None

    def fallback_after(self, name, var=None) -> tuple:
        """The places to try when the first place for name holds no value yet."""
        places = []
        seen_first = var is None
        for block in reversed(self.blocks):
            if block.is_global:
                break
            if name in block.declared:
                v = block.vars[name]
                if not seen_first:
                    if v is var:
                        seen_first = True
                    continue
                places.append(("deref" if v.cell else "fast", v.slot))
        for idx in self.outer_map.get(name, ()):
            places.append(("deref", self.code.free_base + idx))
        places.append(("global", name))
        return tuple(places)

    def free_fallback(self, name, idxs) -> tuple:
        places = [("deref", self.code.free_base + i) for i in idxs[1:]]
        places.append(("global", name))
        return tuple(places)

    # -- loading and storing names --------------------------------------------------

    def load_name(self, name, line, *, map_key=False, soft=False):
        where, info = self.resolve(name)
        if where == "fast":
            if info.maybe:
                pc = self.emit(LOAD_FAST_CHK, info.slot, line)  # noqa: F821
                self.code.fallbacks[pc] = self.fallback_after(name, info)
            else:
                self.emit(LOAD_FAST, info.slot, line)  # noqa: F821
        elif where == "deref":
            pc = self.emit(LOAD_DEREF, info.slot, line)  # noqa: F821
            self.code.fallbacks[pc] = self.fallback_after(name, info)
        elif where == "free":
            pc = self.emit(LOAD_DEREF, self.code.free_base + info[0], line)  # noqa: F821
            self.code.fallbacks[pc] = self.free_fallback(name, info)
        elif soft:
            self.emit(LOAD_GLOBAL_SOFT, name, line)  # noqa: F821
        elif map_key:
            self.emit(LOAD_GLOBAL_KEY, name, line)  # noqa: F821
        else:
            self.emit(LOAD_GLOBAL, name, line)  # noqa: F821

    def store_declare(self, name, line, const=False) -> None:
        """let / fn / class / import: the value on the stack goes into the
        innermost block (the tree-walker's env.declare)."""
        block = self.blocks[-1]
        if block.is_global:
            self.emit(DECLARE_GLOBAL, (name, const), line)  # noqa: F821
        else:
            var = block.vars.get(name)
            if var is None:  # pragma: no cover - decl_names missed it
                var = Var(self.new_slot(), False)
                block.vars[name] = var
                block.all.add(name)
            if var.cell:
                self.emit(DECLARE_DEREF, var.slot, line)  # noqa: F821
            else:
                self.emit(STORE_FAST, var.slot, line)  # noqa: F821
        self.declare(name, const)

    def is_const(self, name, where, info) -> bool:
        """Is the variable an assignment to name would change a constant?"""
        if where in ("fast", "deref"):
            return info.const
        if where == "free" and self.tmpl is not None:
            var = self.tmpl.const_refs.get(name)
            return var is not None and var.const
        return False

    def store_assign(self, name, line) -> None:
        """name = value (the name must exist; constants refuse)."""
        where, info = self.resolve(name)
        if self.is_const(name, where, info):
            self.emit(CONST_ERROR, name, line)  # noqa: F821
            return
        if where == "fast":
            if info.maybe:
                pc = self.emit(STORE_FAST_CHK, info.slot, line)  # noqa: F821
                self.code.fallbacks[pc] = self.fallback_after(name, info)
            else:
                self.emit(STORE_FAST, info.slot, line)  # noqa: F821
        elif where == "deref":
            pc = self.emit(STORE_DEREF, info.slot, line)  # noqa: F821
            self.code.fallbacks[pc] = self.fallback_after(name, info)
        elif where == "free":
            pc = self.emit(STORE_DEREF, self.code.free_base + info[0], line)  # noqa: F821
            self.code.fallbacks[pc] = self.free_fallback(name, info)
        else:
            self.emit(STORE_GLOBAL, name, line)  # noqa: F821

    # -- functions ----------------------------------------------------------------

    def make_template(self, name, params, body, line, kind="function", is_generator=False,
                      is_async=False, is_method=False, extra_refs=None, node=None) -> FnTemplate:
        tmpl = FnTemplate(name, params, body, line, self.file, kind, is_generator, is_async,
                          is_method, node)
        names = set(refs(node)) if node is not None else set()
        if node is None:
            out: set = set()
            _walk_refs(params, out)
            _walk_refs(body, out)
            names = out
        if extra_refs:
            names |= extra_refs
        sources: list = []
        index: dict = {}
        outer_map: dict = {}
        const_refs: dict = {}
        for ref in sorted(names):
            places = []
            first_var = None
            for block in reversed(self.blocks):
                if block.is_global:
                    break
                var = block.vars.get(ref)
                if var is not None:
                    if not var.cell:  # pragma: no cover - captured_by should have caught it
                        raise TBError(f"internal compiler error: '{ref}' is not a cell", line)
                    if first_var is None:
                        first_var = var
                    places.append(var.slot)
            for idx in self.outer_map.get(ref, ()):
                places.append(self.code.free_base + idx)
            if not places:
                continue
            if first_var is None and self.tmpl is not None:
                first_var = self.tmpl.const_refs.get(ref)
            if first_var is not None:
                const_refs[ref] = first_var
            idxs = []
            for slot in places:
                if slot not in index:
                    index[slot] = len(sources)
                    sources.append(slot)
                idxs.append(index[slot])
            outer_map[ref] = idxs
        tmpl.free_sources = tuple(sources)
        tmpl.outer_map = outer_map
        tmpl.const_refs = const_refs
        return tmpl

    def compile_function(self) -> Code:
        tmpl = self.tmpl
        code = self.code
        params = tmpl.params
        code.is_method = tmpl.is_method
        positional = [p for p in params if p.kind == "pos"]
        rest = next((p for p in params if p.kind == "rest"), None)
        kwrest = next((p for p in params if p.kind == "kwrest"), None)
        preset: dict = {}
        top_names: list = []
        if tmpl.is_method:
            preset["self"] = self.new_slot()
            preset["%class"] = self.new_slot()
            top_names += ["self", "%class"]
        pattern_slots = []
        code.pos_params = []
        code.pos_param_nodes = positional
        for p in positional:
            slot = self.new_slot()
            if p.name is not None:
                preset[p.name] = slot
                top_names.append(p.name)
            else:
                pattern_slots.append((p, slot))
                top_names.extend(pattern_names(p.target))
            code.pos_params.append((p.name, p.default is not None))
        if rest is not None:
            code.rest_slot = self.new_slot()
            preset[rest.name] = code.rest_slot
            top_names.append(rest.name)
        if kwrest is not None:
            code.kwrest_slot = self.new_slot()
            preset[kwrest.name] = code.kwrest_slot
            top_names.append(kwrest.name)
        code.nparams = self.nlocals
        code.free_base = self.nlocals
        nfree = len(tmpl.free_sources)
        code.nfree = nfree
        self.nlocals += nfree
        for name, idxs in tmpl.outer_map.items():
            code.free_names.append((name, code.free_base + idxs[0]))
        body = tmpl.body
        names = []
        for n in top_names + decl_names(body):
            if n not in names:
                names.append(n)
        captured = captured_by([params, body])
        cell_params = []
        entry_cells = []
        # every name of the function's top block: params keep their preset slots
        for name in names:
            if name in preset:
                if name in captured:
                    cell_params.append(preset[name])
            else:
                slot = self.new_slot()
                preset[name] = slot
                if name in captured:
                    entry_cells.append(slot)
        code.cell_params = tuple(cell_params)
        code.entry_cells = tuple(entry_cells)
        block = self.push_block(names, [params, body], preset=preset)
        for name in names:
            var = block.vars[name]
            code.top_names.append((name, var.slot, var.cell))
        # params are declared from the start (the tree-walker binds them first)
        for p in positional:
            if p.name is not None:
                block.declared.add(p.name)
        if tmpl.is_method:
            block.declared.update(("self", "%class"))
        if rest is not None:
            block.declared.add(rest.name)
        if kwrest is not None:
            block.declared.add(kwrest.name)
        # prologue: defaults and destructuring, in parameter order
        has_prologue = False
        pattern_slot_of = {id(p): slot for p, slot in pattern_slots}
        for p in positional:
            if p.default is None and p.name is not None:
                continue
            has_prologue = True
            if p.name is not None:
                var = block.vars[p.name]
                slot, is_cell = var.slot, var.cell
            else:
                slot, is_cell = pattern_slot_of[id(p)], False
            if p.default is not None:
                skip = Label()
                self.emit(DEFAULT_CHECK, (slot, is_cell, skip), p.line)  # noqa: F821
                self.expr(p.default)
                self.emit(DECLARE_DEREF if is_cell else STORE_FAST, slot, p.line)  # noqa: F821
                self.mark(skip)
            if p.name is None:
                self.emit(LOAD_FAST, slot, p.line)  # noqa: F821
                self.bind_target(p.target, -1)
        code.entry = self.emit(ENTER, None, tmpl.line)  # noqa: F821
        code.simple = (not has_prologue and rest is None and kwrest is None
                       and not tmpl.is_generator and not tmpl.is_async)
        self.statements(body)
        self.emit(LOAD_CONST, None, tmpl.line)  # noqa: F821
        self.emit(RETURN, None, tmpl.line)  # noqa: F821
        self.pop_block()
        return self.assemble()

    def compile_toplevel(self, body, *, value=False) -> Code:
        """A program, module or eval top level: names live in the globals Env."""
        self.code.kind = "main"
        self.push_block([], body, is_global=True)
        if value:
            self.block_value(body, new_scope=False)
            self.emit(RETURN, None, self.stmt_line)  # noqa: F821
        else:
            self.statements(body)
            self.emit(LOAD_CONST, None, self.stmt_line)  # noqa: F821
            self.emit(RETURN, None, self.stmt_line)  # noqa: F821
        self.pop_block()
        return self.assemble()

    def compile_expression(self, node) -> Code:
        self.code.kind = "main"
        self.push_block([], [node], is_global=True)
        self.stmt_line = getattr(node, "line", 0) or 0
        self.expr(node)
        self.emit(RETURN, None, self.stmt_line)  # noqa: F821
        self.pop_block()
        return self.assemble()

    # -- statements ----------------------------------------------------------------

    def statements(self, body) -> None:
        for stmt in body:
            self.stmt(stmt)

    def block(self, body, extra_names=(), extra_nodes=(), key_nodes=None) -> None:
        """Run body in a fresh block scope (the tree-walker's Env(env))."""
        names = list(extra_names) + decl_names(body)
        self.push_block(names, [list(extra_nodes), body])
        self.statements(body)
        self.pop_block()

    def block_value(self, body, new_scope=True) -> None:
        """Statements whose value is the last expression statement's (else nil)."""
        if new_scope:
            self.push_block(decl_names(body), body)
        if body and type(body[-1]) is A.ExprStmt:
            self.statements(body[:-1])
            last = body[-1]
            self.begin_stmt(last)
            self.expr(last.expr)
        else:
            self.statements(body)
            self.emit(LOAD_CONST, None, self.stmt_line)  # noqa: F821
        if new_scope:
            self.pop_block()

    def begin_stmt(self, node) -> None:
        self.stmt_line = node.line
        if self.traced:
            self.emit(LINE, node.line, node.line)  # noqa: F821

    def stmt(self, node) -> None:
        self.begin_stmt(node)
        handler = getattr(self, "s_" + type(node).__name__, None)
        if handler is None:  # pragma: no cover
            raise TBError("I don't know how to run this.", node.line)
        handler(node)

    def s_ExprStmt(self, node):
        self.expr(node.expr)
        self.emit(POP, None, node.line)  # noqa: F821

    def s_Let(self, node):
        self.expr(node.value)
        if node.name is not None:
            self.store_declare(node.name, node.line, node.is_const)
        else:
            self.bind_target(node.target, node.line, const=node.is_const)

    def s_Assign(self, node):
        target = node.target
        line = node.line
        kind = type(target)
        if kind is A.Name:
            name = target.name
            where, info = self.resolve(name)
            if self.is_const(name, where, info):
                self.emit(CONST_ERROR, name, line)  # noqa: F821
                return
            simple = is_simple_expr(node.value)
            if where == "global" and not (node.op is not None and simple) and (node.op is not None or not simple):
                self.emit(CHECK_GLOBAL_ASSIGN, name, line)  # noqa: F821
            if node.op is not None and simple:
                # nothing in the value can change the name first: load it first
                if where == "global":
                    self.emit(LOAD_GLOBAL_UPD, name, line)  # noqa: F821
                else:
                    self.load_name(name, line)
                self.expr(node.value)
                self.binary_op(node.op, line)
            elif node.op is not None:
                self.expr(node.value)
                self.load_name(name, line)
                self.emit(SWAP, None, line)  # noqa: F821
                self.binary_op(node.op, line)
            else:
                self.expr(node.value)
            self.store_assign(name, line)
            return
        if kind is A.Dot:
            self.expr(target.target)
            self.expr(node.value)
            if node.op is None:
                self.emit(SET_ATTR, target.name, line)  # noqa: F821
            else:
                self.emit(SET_ATTR_OP, (target.name, node.op), line)  # noqa: F821
            return
        self.expr(target.target)
        self.expr(target.index)
        self.expr(node.value)
        if node.op is None:
            self.emit(SET_INDEX, target.line, line)  # noqa: F821
        else:
            self.emit(SET_INDEX_OP, (node.op, target.line), line)  # noqa: F821

    def store_target(self, target, line) -> None:
        """Value on the stack into a Name / Dot / Index target (no operator)."""
        kind = type(target)
        if kind is A.Name:
            self.store_assign(target.name, line)
        elif kind is A.Dot:
            self.expr(target.target)
            self.emit(SWAP, None, line)  # noqa: F821
            self.emit(SET_ATTR, target.name, line)  # noqa: F821
        else:
            self.expr(target.target)
            self.expr(target.index)
            self.emit(ROT3, None, line)  # noqa: F821
            self.emit(SET_INDEX, target.line, line)  # noqa: F821

    def s_MultiAssign(self, node):
        self.expr(node.value)
        self.emit(UNPACK_SEQ, (len(node.targets), "put into"), node.line)  # noqa: F821
        for target in node.targets:
            self.store_target(target, node.line)

    def s_FnDecl(self, node):
        tmpl = self.make_template(node.name, node.params, node.body, node.line, "function",
                                  node.is_generator, getattr(node, "is_async", False), False,
                                  node=node)
        tmpl.doc = "\n".join(c.lstrip("#").strip() for c in node.comments) if node.comments else ""
        self.emit(MAKE_FUNCTION, tmpl, node.line)  # noqa: F821
        self.store_declare(node.name, node.line)

    def s_Return(self, node):
        if node.value is None:
            self.emit(LOAD_CONST, None, node.line)  # noqa: F821
        else:
            self.expr(node.value)
        self.unwind_handlers(0, node.line)
        self.emit(RETURN, None, node.line)  # noqa: F821

    def unwind_handlers(self, keep: int, line) -> None:
        """Leave try blocks down to len(handlers) == keep, running finally bodies."""
        saved = self.handlers
        for i in range(len(saved) - 1, keep - 1, -1):
            entry = saved[i]
            self.emit(POP_BLOCK, None, line)  # noqa: F821
            if entry[0] == "finally":
                self.handlers = saved[:i]
                self.block(entry[1])
        self.handlers = saved

    def s_If(self, node):
        end = Label()
        for cond, body in node.branches:
            nxt = Label()
            self.cond_jump(cond, nxt)
            self.block(body)
            self.emit(JUMP, end, node.line)  # noqa: F821
            self.mark(nxt)
        if node.otherwise is not None:
            self.block(node.otherwise)
        self.mark(end)

    def cond_jump(self, cond, false_label) -> None:
        """Jump to false_label when cond is not true."""
        self.expr(cond)
        self.emit(JUMP_IF_FALSE, false_label, getattr(cond, "line", 0))  # noqa: F821

    def loop_mark(self, body):
        if contains_type(body, A.Match):
            slot = self.new_slot()
            self.emit(MARK_STACK, slot, 0)  # noqa: F821
            return slot
        return None

    def s_While(self, node):
        top = Label()
        end = Label()
        mark = self.loop_mark(node.body)
        self.mark(top)
        self.cond_jump(node.cond, end)
        self.loops.append(Loop(end, top, len(self.handlers), mark))
        self.block(node.body)
        self.loops.pop()
        # An empty traced body has no LINE op, so use LOOP to still reach the trace hook
        # (cancel requests, the playground time limit, the debugger).
        self.emit(JUMP if self.traced and node.body else LOOP, top, node.line)  # noqa: F821
        self.mark(end)

    def s_For(self, node):
        targets = node.targets
        self.expr(node.iterable)
        self.emit(GET_ITER, len(targets) > 1, node.line)  # noqa: F821
        mark = self.loop_mark(node.body)
        top = Label()
        brk = Label()
        end = Label()
        self.mark(top)
        self.emit(FOR_ITER, end, node.line)  # noqa: F821
        names = []
        for t in targets:
            names.extend(pattern_names(t))
        names += decl_names(node.body)
        self.push_block(names, [node.body])
        if node.name is not None:
            self.store_declare(node.name, node.line)
        elif len(targets) == 1:
            self.bind_target(targets[0], node.line)
        else:
            self.emit(UNPACK_SEQ, (len(targets), "loop over"), node.line)  # noqa: F821
            for t in targets:
                self.bind_target(t, node.line)
        self.loops.append(Loop(brk, top, len(self.handlers), mark))
        self.statements(node.body)
        self.loops.pop()
        self.pop_block()
        # An empty traced body has no LINE op, so use LOOP to still reach the trace hook
        # (cancel requests, the playground time limit, the debugger).
        self.emit(JUMP if self.traced and node.body else LOOP, top, node.line)  # noqa: F821
        self.mark(brk)
        self.emit(POP, None, node.line)  # noqa: F821
        self.mark(end)

    def s_Break(self, node):
        loop = self.loops[-1]
        self.unwind_handlers(loop.handlers, node.line)
        if self.in_expr_block and loop.mark_slot is not None:
            self.emit(TRUNC_STACK, loop.mark_slot, node.line)  # noqa: F821
        self.emit(JUMP, loop.break_label, node.line)  # noqa: F821

    def s_Continue(self, node):
        loop = self.loops[-1]
        self.unwind_handlers(loop.handlers, node.line)
        if self.in_expr_block and loop.mark_slot is not None:
            self.emit(TRUNC_STACK, loop.mark_slot, node.line)  # noqa: F821
        self.emit(JUMP, loop.continue_label, node.line)  # noqa: F821

    def s_Import(self, node):
        self.emit(IMPORT, node, node.line)  # noqa: F821
        self.store_declare(node.alias, node.line)

    def s_FromImport(self, node):
        self.emit(FROM_IMPORT, node, node.line)  # noqa: F821
        for name, alias in node.names:
            self.emit(DUP, None, node.line)  # noqa: F821
            self.emit(IMPORT_NAME, name, node.line)  # noqa: F821
            self.store_declare(alias, node.line)
        self.emit(POP, None, node.line)  # noqa: F821

    def s_Try(self, node):
        fin_exc = Label()
        end = Label()
        if node.finally_body is not None:
            self.emit(SETUP_FINALLY, fin_exc, node.line)  # noqa: F821
            self.handlers.append(("finally", node.finally_body))
        if node.catches:
            catch_label = Label()
            after = Label()
            self.emit(SETUP_TRY, catch_label, node.line)  # noqa: F821
            self.handlers.append(("try",))
            self.block(node.body)
            self.emit(POP_BLOCK, None, node.line)  # noqa: F821
            self.handlers.pop()
            self.emit(JUMP, after, node.line)  # noqa: F821
            self.mark(catch_label)
            err_slot = self.new_slot()
            self.emit(STORE_FAST, err_slot, node.line)  # noqa: F821
            for catch in node.catches:
                nxt = Label()
                if catch.types:
                    matched = Label()
                    for t in catch.types:
                        self.expr(t)
                        self.emit(CATCH_MATCH, err_slot, t.line)  # noqa: F821
                        self.emit(JUMP_IF_TRUE, matched, t.line)  # noqa: F821
                    self.emit(JUMP, nxt, catch.line)  # noqa: F821
                    self.mark(matched)
                names = ([catch.name] if catch.name is not None else []) + decl_names(catch.body)
                self.push_block(names, [catch.body])
                if catch.name is not None:
                    self.emit(LOAD_FAST, err_slot, catch.line)  # noqa: F821
                    self.emit(ERROR_VALUE, None, catch.line)  # noqa: F821
                    self.store_declare(catch.name, catch.line)
                self.statements(catch.body)
                self.pop_block()
                self.emit(JUMP, after, catch.line)  # noqa: F821
                self.mark(nxt)
            self.emit(LOAD_FAST, err_slot, node.line)  # noqa: F821
            self.emit(RERAISE, None, node.line)  # noqa: F821
            self.mark(after)
        else:
            self.block(node.body)
        if node.finally_body is not None:
            self.emit(POP_BLOCK, None, node.line)  # noqa: F821
            self.handlers.pop()
            self.block(node.finally_body)
            self.emit(JUMP, end, node.line)  # noqa: F821
            self.mark(fin_exc)
            exc_slot = self.new_slot()
            self.emit(STORE_FAST, exc_slot, node.line)  # noqa: F821
            self.block(node.finally_body)
            self.emit(LOAD_FAST, exc_slot, node.line)  # noqa: F821
            self.emit(RERAISE, None, node.line)  # noqa: F821
            self.mark(end)

    def s_Throw(self, node):
        self.expr(node.value)
        self.emit(THROW, None, node.line)  # noqa: F821

    def s_ClassDecl(self, node):
        if node.base is not None:
            self.expr(node.base)
        else:
            self.emit(LOAD_CONST, NOBASE, node.line)  # noqa: F821
        for _name, value, _ann in node.statics:
            if value is None:
                self.emit(LOAD_CONST, None, node.line)  # noqa: F821
            else:
                self.expr(value)
        methods = []
        for decl in node.methods:
            tmpl = self.make_template(decl.name, decl.params, decl.body, decl.line, "function",
                                      decl.is_generator, getattr(decl, "is_async", False), True,
                                      node=decl)
            tmpl.doc = "\n".join(c.lstrip("#").strip() for c in decl.comments) if decl.comments else ""
            methods.append(tmpl)
        statics = []
        for decl in node.static_methods:
            tmpl = self.make_template(decl.name, decl.params, decl.body, decl.line, "function",
                                      decl.is_generator, getattr(decl, "is_async", False), True,
                                      node=decl)
            tmpl.doc = "\n".join(c.lstrip("#").strip() for c in decl.comments) if decl.comments else ""
            statics.append(tmpl)
        fields = []
        for name, value, _ann in node.fields:
            if value is None:
                fields.append((name, "const", None))
            elif type(value) is A.Literal:
                fields.append((name, "const", value.value))
            else:
                ret = A.Return(value, value.line)
                tmpl = self.make_template(f"{node.name}.{name}", [], [ret], value.line, "function",
                                          node=value)
                fields.append((name, "thunk", tmpl))
        doc = "\n".join(c.lstrip("#").strip() for c in node.comments) if node.comments else ""
        info = (node.name, [n for n, _v, _a in node.statics], methods, statics, fields,
                [(n, v) for n, v, _a in node.fields], doc)
        self.emit(MAKE_CLASS, info, node.line)  # noqa: F821
        self.store_declare(node.name, node.line)

    def s_EnumDecl(self, node):
        self.emit(MAKE_ENUM, (node.name, node.members), node.line)  # noqa: F821
        self.store_declare(node.name, node.line)

    def s_Export(self, node):
        self.stmt(node.decl)
        name = getattr(node.decl, "name", None)
        if name:
            self.emit(EXPORT, name, node.line)  # noqa: F821

    def stmt_body_only(self, node) -> None:
        """Compile a statement without a new LINE (export's own statement)."""
        self.stmt_line = node.line
        getattr(self, "s_" + type(node).__name__)(node)

    def s_Defer(self, node):
        tmpl = self.make_template("defer", [], node.body, node.line, "defer", node=node)
        self.emit(DEFER, tmpl, node.line)  # noqa: F821

    def s_Assert(self, node):
        ok = Label()
        self.expr(node.cond)
        self.emit(JUMP_IF_TRUE, ok, node.line)  # noqa: F821
        if node.message is not None:
            self.expr(node.message)
        self.emit(ASSERT_FAIL, node.message is not None, node.line)  # noqa: F821
        self.mark(ok)

    # -- destructuring ----------------------------------------------------------------

    def bind_target(self, pattern, line, const=False) -> None:
        """Declare the names of a let / for / parameter pattern from the value
        on the stack. line -1 means "the line of the call" (parameters)."""
        if isinstance(pattern, str):
            self.store_declare(pattern, line, const)
            return
        kind = type(pattern)
        if kind is A.NamePattern:
            self.store_declare(pattern.name, line, const)
        elif kind is A.ListPattern:
            self.emit(LIST_UNPACK, (len(pattern.items), pattern.rest is not None), line)  # noqa: F821
            for item in pattern.items:
                self.bind_target(item, line, const)
            if pattern.rest is not None:
                self.store_declare(pattern.rest, line, const)
        elif kind is A.MapPattern:
            self.emit(MAP_SOURCE, None, line)  # noqa: F821
            for key, sub, default in pattern.entries:
                self.emit(DUP, None, line)  # noqa: F821
                if default is not None:
                    use_default = Label()
                    have = Label()
                    self.emit(MAP_TAKE, (key, use_default), line)  # noqa: F821
                    self.emit(JUMP, have, line)  # noqa: F821
                    self.mark(use_default)
                    self.expr(default)
                    self.mark(have)
                else:
                    self.emit(MAP_TAKE, (key, None), line)  # noqa: F821
                if sub is None:
                    self.store_declare(key, line, const)
                else:
                    self.bind_target(sub, line, const)
            self.emit(POP, None, line)  # noqa: F821
        else:  # pragma: no cover
            raise TBError("I don't know how to unpack this.", line)

    # -- expressions --------------------------------------------------------------------

    def expr(self, node) -> None:
        handler = getattr(self, "e_" + type(node).__name__, None)
        if handler is None:  # pragma: no cover
            raise TBError("I don't know how to work this out.", node.line)
        handler(node)

    def e_Literal(self, node):
        self.emit(LOAD_CONST, node.value, node.line)  # noqa: F821

    def e_Name(self, node):
        self.load_name(node.name, node.line)

    def e_ListLit(self, node):
        if not any(type(item) is A.Spread for item in node.items):
            for item in node.items:
                self.expr(item)
            self.emit(BUILD_LIST, len(node.items), node.line)  # noqa: F821
            return
        self.emit(BUILD_LIST, 0, node.line)  # noqa: F821
        for item in node.items:
            if type(item) is A.Spread:
                self.expr(item.value)
                self.emit(LIST_EXTEND, None, item.line)  # noqa: F821
            else:
                self.expr(item)
                self.emit(LIST_APPEND, None, node.line)  # noqa: F821

    def e_MapLit(self, node):
        self.emit(MAP_NEW, None, node.line)  # noqa: F821
        for pair in node.pairs:
            if type(pair) is A.Spread:
                self.expr(pair.value)
                self.emit(MAP_SPREAD, None, pair.line)  # noqa: F821
                continue
            key_node, value_node = pair
            if type(key_node) is A.Name:
                self.load_name(key_node.name, key_node.line, map_key=True)
            else:
                self.expr(key_node)
            self.emit(MAP_KEY, None, key_node.line)  # noqa: F821
            self.expr(value_node)
            self.emit(MAP_PUT, None, node.line)  # noqa: F821

    def e_Unary(self, node):
        operand = node.operand
        if node.op == "-" and type(operand) is A.Literal and type(operand.value) in (int, float):
            self.emit(LOAD_CONST, -operand.value, node.line)  # noqa: F821
            return
        self.expr(operand)
        if node.op == "-":
            self.emit(NEG, None, node.line)  # noqa: F821
        elif node.op == "~":
            self.emit(INV, None, node.line)  # noqa: F821
        else:
            self.emit(NOT, None, node.line)  # noqa: F821

    def e_Logical(self, node):
        end = Label()
        self.expr(node.left)
        if node.op == "or":
            self.emit(JUMP_IF_TRUE_KEEP, end, node.line)  # noqa: F821
        else:
            self.emit(JUMP_IF_FALSE_KEEP, end, node.line)  # noqa: F821
        self.expr(node.right)
        self.mark(end)

    def binary_op(self, op, line) -> None:
        code = BINARY_OPS.get(op)
        if code is None:
            self.emit(BINARY, op, line)  # noqa: F821
        else:
            self.emit(code, op, line)

    def e_Binary(self, node):
        left, right = node.left, node.right
        if type(left) is A.Literal and type(right) is A.Literal:
            folded = _fold(node.op, left.value, right.value)
            if folded is not None:
                self.emit(LOAD_CONST, folded[0], node.line)  # noqa: F821
                return
        self.expr(left)
        self.expr(right)
        self.binary_op(node.op, node.line)

    def e_Compare(self, node):
        false = Label()
        end = Label()
        self.expr(node.operands[0])
        n = len(node.ops)
        tmp = self.new_slot()
        for i, (op, operand) in enumerate(zip(node.ops, node.operands[1:])):
            self.expr(operand)
            last = i == n - 1
            if not last:
                self.emit(DUP, None, node.line)  # noqa: F821
                self.emit(STORE_FAST, tmp, node.line)  # noqa: F821
            self.emit(COMPARE_CHAIN, op, node.line)  # noqa: F821
            if not last:
                self.emit(JUMP_IF_FALSE, false, node.line)  # noqa: F821
                self.emit(LOAD_FAST, tmp, node.line)  # noqa: F821
        self.emit(JUMP, end, node.line)  # noqa: F821
        self.mark(false)
        self.emit(LOAD_CONST, False, node.line)  # noqa: F821
        self.mark(end)

    def type_check(self, type_node, negated, line) -> None:
        """Value on the stack -> bool: value is type_node."""
        from .interp import BUILTIN_TYPE_CHECKS

        if type(type_node) is A.Name and type_node.name in BUILTIN_TYPE_CHECKS:
            self.load_name(type_node.name, type_node.line, soft=True)
            self.emit(IS_TYPE_NAMED, (type_node.name, negated), line)  # noqa: F821
        else:
            self.expr(type_node)
            self.emit(IS_TYPE, negated, line)  # noqa: F821

    def e_IsCheck(self, node):
        self.expr(node.value)
        self.type_check(node.type_node, node.negated, node.line)

    def e_Call(self, node):
        callee = node.callee
        plain = all(type(a) not in (A.Spread, A.KeywordArg) for a in node.args)
        if plain and type(callee) is A.Dot:
            self.expr(callee.target)
            self.emit(LOAD_METHOD, callee.name, callee.line)  # noqa: F821
            for a in node.args:
                self.expr(a)
            self.emit(CALL_METHOD, len(node.args), node.line)  # noqa: F821
            return
        self.expr(callee)
        if plain:
            for a in node.args:
                self.expr(a)
            self.emit(CALL, len(node.args), node.line)  # noqa: F821
            return
        self.emit(BUILD_LIST, 0, node.line)  # noqa: F821
        kw_names = []
        for a in node.args:
            kind = type(a)
            if kind is A.Spread:
                self.expr(a.value)
                self.emit(LIST_EXTEND, None, a.line)  # noqa: F821
            elif kind is A.KeywordArg:
                kw_names.append(a.name)
                self.expr(a.value)
                self.emit(SWAP, None, node.line)  # noqa: F821  (keep the list on top)
            else:
                self.expr(a)
                self.emit(LIST_APPEND, None, node.line)  # noqa: F821
        if kw_names:
            self.emit(BUILD_KWARGS, tuple(kw_names), node.line)  # noqa: F821
        self.emit(CALL_EX, bool(kw_names), node.line)  # noqa: F821

    def e_Index(self, node):
        self.expr(node.target)
        self.expr(node.index)
        self.emit(INDEX, None, node.line)  # noqa: F821

    def e_SliceExpr(self, node):
        self.expr(node.target)
        self.emit(SLICE_PREP, None, node.line)  # noqa: F821
        for part in (node.start, node.stop, node.step):
            if part is None:
                self.emit(LOAD_CONST, None, node.line)  # noqa: F821
            else:
                self.expr(part)
        self.emit(SLICE, None, node.line)  # noqa: F821

    def e_Dot(self, node):
        self.expr(node.target)
        self.emit(GET_ATTR, node.name, node.line)  # noqa: F821

    def e_Super(self, node):
        where, _info = self.resolve("%class")
        if where == "global":
            self.emit(SUPER_ERROR, None, node.line)  # noqa: F821
            return
        self.load_name("%class", node.line)
        self.load_name("self", node.line)
        self.emit(SUPER, node.name, node.line)  # noqa: F821

    def e_FString(self, node):
        count = 0
        for part in node.parts:
            if isinstance(part, str):
                self.emit(LOAD_CONST, part, node.line)  # noqa: F821
            else:
                self.expr(part.expr)
                if part.spec or part.conv:
                    self.emit(FORMAT_VALUE, (part.spec, part.conv), part.line)  # noqa: F821
                else:
                    self.emit(FORMAT_SIMPLE, None, part.line)  # noqa: F821
            count += 1
        self.emit(BUILD_STRING, count, node.line)  # noqa: F821

    def e_FnExpr(self, node):
        tmpl = self.make_template("anonymous", node.params, node.body, node.line, "function",
                                  node.is_generator, getattr(node, "is_async", False), False,
                                  node=node)
        self.emit(MAKE_FUNCTION, tmpl, node.line)  # noqa: F821

    def e_RangeExpr(self, node):
        self.expr(node.start)
        self.expr(node.stop)
        self.emit(MAKE_RANGE, node.inclusive, node.line)  # noqa: F821

    def e_Conditional(self, node):
        other = Label()
        end = Label()
        self.cond_jump(node.cond, other)
        self.expr(node.then)
        self.emit(JUMP, end, node.line)  # noqa: F821
        self.mark(other)
        self.expr(node.otherwise)
        self.mark(end)

    def e_Yield(self, node):
        if node.value is None:
            self.emit(LOAD_CONST, None, node.line)  # noqa: F821
        else:
            self.expr(node.value)
        self.emit(YIELD, None, node.line)  # noqa: F821

    def e_Await(self, node):
        self.expr(node.value)
        self.emit(AWAIT, None, node.line)  # noqa: F821

    # -- match ----------------------------------------------------------------------

    def e_Match(self, node):
        self.expr(node.subject)
        subject = self.new_slot()
        self.emit(STORE_FAST, subject, node.line)  # noqa: F821
        end = Label()
        self.in_expr_block += 1
        for arm in node.arms:
            fail = Label()
            maybe: set = set()
            names = match_names(arm.pattern, [], maybe)
            nodes = [arm.pattern, arm.guard, arm.body]
            if arm.is_block:
                names += decl_names(arm.body)
            self.push_block(names, nodes, maybe=maybe)
            self.match_pattern(arm.pattern, subject, fail)
            if arm.guard is not None:
                self.expr(arm.guard)
                self.emit(JUMP_IF_FALSE, fail, arm.line)  # noqa: F821
            if arm.is_block:
                self.block_value(arm.body, new_scope=False)
            else:
                self.expr(arm.body)
            self.pop_block()
            self.emit(JUMP, end, arm.line)  # noqa: F821
            self.mark(fail)
        self.in_expr_block -= 1
        self.emit(LOAD_FAST, subject, node.line)  # noqa: F821
        self.emit(MATCH_FAIL, None, node.line)  # noqa: F821
        self.mark(end)

    def bind_match_name(self, name, line) -> None:
        var = self.blocks[-1].vars[name]
        self.blocks[-1].declared.add(name)
        self.emit(DECLARE_DEREF if var.cell else STORE_FAST, var.slot, line)  # noqa: F821

    def match_pattern(self, pattern, subject: int, fail: Label) -> None:
        kind = type(pattern)
        line = pattern.line
        if kind is A.PName:
            if pattern.name != "_":
                self.emit(LOAD_FAST, subject, line)  # noqa: F821
                self.bind_match_name(pattern.name, line)
        elif kind is A.PLiteral:
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            self.emit(MATCH_LITERAL, (pattern.value, fail), line)  # noqa: F821
        elif kind is A.PValue:
            self.expr(pattern.expr)
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            self.emit(MATCH_VALUE, fail, line)  # noqa: F821
        elif kind is A.POr:
            ok = Label()
            for option in pattern.options[:-1]:
                nxt = Label()
                self.match_pattern(option, subject, nxt)
                self.emit(JUMP, ok, line)  # noqa: F821
                self.mark(nxt)
            self.match_pattern(pattern.options[-1], subject, fail)
            self.mark(ok)
        elif kind is A.PRange:
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            self.emit(MATCH_RANGE, (pattern.start, pattern.stop, pattern.inclusive, fail), line)  # noqa: F821
        elif kind is A.PList:
            n = len(pattern.items)
            seq = self.new_slot()
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            self.emit(MATCH_LIST, (n, pattern.rest is not None, fail), line)  # noqa: F821
            self.emit(STORE_FAST, seq, line)  # noqa: F821
            for i, sub in enumerate(pattern.items):
                item = self.new_slot()
                self.emit(LOAD_FAST, seq, line)  # noqa: F821
                self.emit(GET_ITEM_RAW, i, line)  # noqa: F821
                self.emit(STORE_FAST, item, line)  # noqa: F821
                self.match_pattern(sub, item, fail)
            if pattern.rest is not None and pattern.rest != "_":
                self.emit(LOAD_FAST, seq, line)  # noqa: F821
                self.emit(MATCH_REST, n, line)  # noqa: F821
                self.bind_match_name(pattern.rest, line)
        elif kind is A.PMap:
            source = self.new_slot()
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            self.emit(MATCH_MAP, fail, line)  # noqa: F821
            self.emit(STORE_FAST, source, line)  # noqa: F821
            for key, sub in pattern.entries:
                item = self.new_slot()
                self.emit(LOAD_FAST, source, line)  # noqa: F821
                self.emit(MATCH_MAP_KEY, (key, fail), line)  # noqa: F821
                self.emit(STORE_FAST, item, line)  # noqa: F821
                self.match_pattern(sub, item, fail)
        elif kind is A.PIs:
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            self.type_check(pattern.type_node, False, line)
            self.emit(JUMP_IF_FALSE, fail, line)  # noqa: F821
        elif kind is A.PClass:
            ok = Label()
            self.expr(pattern.type_node)
            self.emit(LOAD_FAST, subject, line)  # noqa: F821
            skip = ok if pattern.binding is None else None
            self.emit(MATCH_CLASS, (fail, skip), line)  # noqa: F821
            if pattern.binding is not None:
                self.match_pattern(pattern.binding, subject, fail)
            elif pattern.fields:
                for name, sub in pattern.fields:
                    item = self.new_slot()
                    self.emit(LOAD_FAST, subject, line)  # noqa: F821
                    self.emit(MATCH_FIELD, (name, fail), line)  # noqa: F821
                    self.emit(STORE_FAST, item, line)  # noqa: F821
                    self.match_pattern(sub, item, fail)
            self.mark(ok)
        else:  # pragma: no cover
            raise TBError("I don't understand this pattern.", line)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def compile_program(body, file=None, traced=False) -> Code:
    """A program or module's top level (names go into the globals Env)."""
    return FuncCompiler(None, traced, kind="main", file=file, name=None).compile_toplevel(body)


def compile_block_value(body, file=None, traced=False) -> Code:
    """Statements whose value is the last expression's (eval of statements)."""
    return FuncCompiler(None, traced, kind="main", file=file).compile_toplevel(body, value=True)


def compile_expression(node, file=None, traced=False) -> Code:
    """One expression, with every name looked up in the given Env (REPL, eval)."""
    return FuncCompiler(None, traced, kind="main", file=file).compile_expression(node)
