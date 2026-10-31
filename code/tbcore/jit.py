"""The VM's fast tier: hot bytecode translated into Python functions.

The VM loop (tbcore/vm.py) pays for every instruction: fetch the opcode, walk
the dispatch tests, push and pop a Python list. When a Code object gets hot
(called often, or a loop in it turns often) `compile_code` translates it into
one Python function `f(interp, frame)` that runs the same instructions with
the dispatch gone:

* Basic blocks become straight-line Python. A jump sets `blk` and the
  function finds the block with a short chain of tests (blocks in chunks of
  eight, forward jumps fall through, backward jumps restart the chain).
* The value stack is virtual inside a block: each push is a Python local, and
  the real `frame.stack` list is only written when something needs it (a
  block boundary, a rarer instruction run through the VM's own handler, a
  yield). Constants are folded into the code, so `i + 1` or `n < 2` test one
  type, not two.
* The VM's superinstructions are undone (each keeps its original instruction
  in place), because the translation already specialises the plain sequence.
* Method calls on instances use an inline cache per call site (class ->
  method), filled on the first lookup and never invalidated: it relies on
  class methods never changing after creation (see runtime.TBClass).

Everything else is the VM's: rarer instructions call the same `_SLOW`
handlers, slow paths call the same interpreter methods with the same line
numbers, `frame.pc` is set before anything that can fail or look at the call
stack, and an exception goes through the same handler search and
`_leave_frame`, so errors, tracebacks and `frames()` read exactly as before.
TB-to-TB calls run the callee's function (or the VM loop, while it is cold)
as a Python call. Traced code (the debugger, coverage) is never translated:
it keeps its LINE instructions in the VM loop.

When code gets hot is decided in vm.py (JIT_HOT, JIT_HOT_LOOPS; `TB_JIT=off`
turns the tier off, `TB_JIT=always` translates every Code on its first run).
"""

from __future__ import annotations

import re

from . import compiler as C
from .errors import MAX_DEPTH, TBError
from .runtime import Bound, Builtin, Instance, METHODS, TBIterator, arity_message, tb_equal, to_text
from . import vm as V

MAX_OPS = 4000   # bigger bodies stay in the VM loop

_END = V._END
_PASS_THROUGH = V._PASS_THROUGH
VMFunction = V.VMFunction
VMFrame = V.VMFrame
Cell = V.Cell
NOSELF = V.NOSELF
UNSET = C.UNSET

# superinstruction -> (original instruction, its argument): the original run stays in place
_UNFUSE = {
    C.GLOBAL_INC: lambda a: (C.LOAD_GLOBAL_UPD, a[0]),
    C.GLOBAL_CMP_CONST_JUMP: lambda a: (C.LOAD_GLOBAL, a[0]),
    C.FOR_ITER_STORE: lambda a: (C.FOR_ITER, a[0]),
    C.CONST_CMP_JUMP: lambda a: (C.LOAD_CONST, a[1]),
    C.CONST_OP: lambda a: (C.LOAD_CONST, a[1]),
    C.FAST_CMP_CONST_JUMP: lambda a: (C.LOAD_FAST, a[0]),
    C.FAST_LT_JUMP: lambda a: (C.LOAD_FAST, a[0]),
    C.INC_FAST: lambda a: (C.LOAD_FAST, a[0]),
    C.FAST_ADD_CONST: lambda a: (C.LOAD_FAST, a[0]),
    C.LOAD_ATTR_FAST: lambda a: (C.LOAD_FAST, a[0]),
    C.LOAD_FAST2: lambda a: (C.LOAD_FAST, a[0]),
}


def compile_code(code):
    """Translate code into its fast function (stored in code.fast), or mark
    it as not translatable. Returns the function or None."""
    fn = None
    if not code.traced and len(code.ops) <= MAX_OPS:
        try:
            fn = _Translator(code).build()
        except _NoJit:
            fn = None
        except Exception:  # a translator bug must not stop the program: the VM loop runs it
            if V._JIT_MODE == "always":
                raise  # (but the test suite should hear about it)
            fn = None
    code.fast = fn
    if fn is None:
        code.hot = -(1 << 60)   # never try again
    elif code.simple and not code.cell_params and not code.entry_cells:
        if code.is_method:
            code.fastmeth = code.nparams
        else:
            code.fastcall = code.nparams
    return fn


class _NoJit(Exception):
    pass


# ---------------------------------------------------------------------------
# Run-time helpers the generated code calls
# ---------------------------------------------------------------------------

def _unwind(it, frame, exc):
    """An exception inside a translated frame: the VM loop's except block for one frame."""
    if type(exc) is StopIteration:  # pragma: no cover - never let one escape a loop
        exc = TBError("A generator stopped in an unexpected way.", None)
    if frame.blocks:
        target = it._handler_for(frame, exc)
        if target >= 0:
            frame.stack.append(exc)
            return target
    raise it._leave_frame(frame, exc)


def _enter_simple(it, frame, fn, code2, fast2):
    """Finish a simple call's frame (parameters already in fast2) and run it."""
    if it.depth >= MAX_DEPTH:
        raise it._too_deep(fn, frame.code.lines[frame.pc - 1])
    tail = fn.tail
    if tail is None:
        tail = fn.make_tail(code2)
    fast2 += tail
    if code2.cell_params:
        for i in code2.cell_params:
            fast2[i] = Cell(fast2[i])
    if code2.entry_cells:
        for i in code2.entry_cells:
            fast2[i] = Cell(UNSET)
    new = VMFrame(code2, fast2, fn)
    new.entered = True
    new.back = frame
    new.pc = code2.entry + 1
    it.depth += 1
    it.call_stack.append(new)
    f = code2.fast
    if f is not None:
        return f(it, new)
    return it._run(new)


def _call(it, frame, fn, cargs, line):
    """CALL: the VM loop's call instruction, as a function."""
    t = type(fn)
    if t is VMFunction:
        code2 = fn.tmpl._code
        if (code2 is not None and code2.simple and len(cargs) == code2.nparams
                and it.trace is None and not code2.is_method):
            return _enter_simple(it, frame, fn, code2, cargs)
        if not fn.is_generator and not fn.is_async:
            if it.depth >= MAX_DEPTH:
                raise it._too_deep(fn, line)
            new = it._frame_for(fn, cargs, None, line, None, None, False)
            new.back = frame
            return it._run(new)
    elif t is Builtin:
        lo, hi = fn.arity
        if not lo <= len(cargs) <= hi:
            raise TBError(arity_message(fn.name, lo, hi, len(cargs)), line)
        try:
            return fn.fn(line, *cargs)
        except _PASS_THROUGH:
            raise
        except Exception as err:
            raise TBError(f"Something went wrong inside {fn.name}(): {err}", line) from None
    elif t is Bound and type(fn.fn) is VMFunction and not fn.fn.is_generator and not fn.fn.is_async:
        if it.depth >= MAX_DEPTH:
            raise it._too_deep(fn.fn, line)
        new = it._frame_for(fn.fn, cargs, None, line, fn.self_value, fn.owner, True)
        new.back = frame
        return it._run(new)
    return it.call(fn, cargs, line)


_LIST_METHODS = METHODS["list"]
_TEXT_METHODS = METHODS["text"]
_MAP_METHODS = METHODS["map"]


def _load_method(it, obj, name, line, cache):
    """LOAD_METHOD (the cache missed): (callee, self, owner) like the VM pushes them."""
    t = type(obj)
    if t is Instance:
        if name not in obj.fields:
            method, owner = obj.cls.find_method(name)
            if type(method) is VMFunction:
                # shortcut: classes don't change their methods after they are made; if that ever
                # changes, clear the caches there.
                cache[0] = (obj.cls, method, owner)  # one store: other threads see all or nothing
                return method, obj, owner
    else:
        if t is list:
            entry = _LIST_METHODS.get(name)
        elif t is str:
            entry = _TEXT_METHODS.get(name)
        elif t is dict and name not in obj:
            entry = _MAP_METHODS.get(name)
        else:
            entry = None
        if entry is not None:
            return entry, obj, name
    out = [obj]
    it._load_method(obj, name, line, out)
    return out[0], out[1], out[2]


def _call_method(it, frame, fn, selfv, owner, cargs, line):
    """CALL_METHOD: the VM loop's method-call instruction, as a function."""
    if selfv is NOSELF:
        if type(fn) is VMFunction and not fn.is_generator and not fn.is_async:
            if it.depth >= MAX_DEPTH:
                raise it._too_deep(fn, line)
            new = it._frame_for(fn, cargs, None, line, None, None, False)
            new.back = frame
            return it._run(new)
        if type(fn) is Builtin:
            return it._call_builtin(fn, cargs, line)
        return it.call(fn, cargs, line)
    if type(owner) is str:
        fnc, lo, hi, _doc = fn
        if not lo <= len(cargs) <= hi:
            raise TBError(arity_message(owner, lo, hi, len(cargs)), line)
        try:
            return fnc(it, line, selfv, *cargs)
        except _PASS_THROUGH:
            raise
        except Exception as err:
            raise TBError(f"Something went wrong inside {owner}(): {err}", line) from None
    code2 = fn.tmpl._code
    if code2 is not None and code2.simple and len(cargs) + 2 == code2.nparams and it.trace is None:
        fast2 = [selfv, owner]
        fast2 += cargs
        return _enter_simple(it, frame, fn, code2, fast2)
    if fn.is_generator or fn.is_async:
        return it._call_vm(fn, cargs, None, line, selfv, owner, True)
    if it.depth >= MAX_DEPTH:
        raise it._too_deep(fn, line)
    new = it._frame_for(fn, cargs, None, line, selfv, owner, True)
    new.back = frame
    return it._run(new)


def _wrong(name, err, line):
    return TBError(f"Something went wrong inside {name}(): {err}", line)


def _bad_jump(blk):  # pragma: no cover - a translator bug
    raise TBError(f"I lost my place in the program (a jump to {blk}).", None)


_NAMESPACE = {
    "UNSET": UNSET, "Instance": Instance, "BIG": V.BIG, "NBIG": -V.BIG,
    "_END": _END, "tb_equal": tb_equal, "to_text": to_text, "FrameEnv": V.FrameEnv,
    "_unwind": _unwind, "_call": _call, "_call_method": _call_method, "_load_method": _load_method,
    "_bad_jump": _bad_jump, "_check_global_assign": V._op_check_global_assign,
    "_wrong": _wrong, "_PASS_THROUGH": _PASS_THROUGH, "NOSELF": NOSELF,
    "VMFunction": VMFunction, "Builtin": Builtin, "VMFrame": VMFrame, "_new": object.__new__,
    "MAX_DEPTH": MAX_DEPTH, "TBIterator": TBIterator,
}

# ---------------------------------------------------------------------------
# The translator
# ---------------------------------------------------------------------------

_PROLOGUE = ("fast = frame.fast", "stack = frame.stack", "gvars = frame.gvars", "bvars = frame.bvars",
             "genv = frame.genv", "call_stack = it.call_stack")
_COMPARE = {C.LT: "<", C.GT: ">", C.LE: "<=", C.GE: ">="}
_ARITH = {C.ADD: "+", C.SUB: "-", C.MUL: "*"}


class _Translator:
    def __init__(self, code):
        self.code = code
        self.ops = code.ops
        self.args = code.args
        self.lines = code.lines
        self.out = []         # lines of Python source
        self.pcs = []         # the instruction each line belongs to (for tb profile)
        self.cur = 0
        self.ind = 0
        self.vs = []          # the virtual stack: Python expressions above the real stack
        self.ntmp = 0
        self.ns = dict(_NAMESPACE)
        self.nconst = 0
        self.methods = {}     # LOAD_METHOD temp -> (self temp, owner temp, native entries it may hold)

    # -- emitting --------------------------------------------------------------

    def w(self, text):
        self.out.append("    " * self.ind + text)
        self.pcs.append(self.cur)

    def tmp(self):
        self.ntmp += 1
        return f"t{self.ntmp}"

    def const(self, value):
        t = type(value)
        if value is None or t is bool or (t is int and -(1 << 62) < value < (1 << 62)):
            return repr(value)
        if t is str and len(value) < 120 and value.isprintable():
            return repr(value)
        self.nconst += 1
        name = f"K{self.nconst}"
        self.ns[name] = value
        return name

    def const_obj(self, value):
        self.nconst += 1
        name = f"K{self.nconst}"
        self.ns[name] = value
        return name

    def push(self, expr):
        self.vs.append(expr)

    def push_new(self, rhs):
        t = self.tmp()
        self.w(f"{t} = {rhs}")
        self.vs.append(t)
        return t

    def pop(self):
        if self.vs:
            return self.vs.pop()
        t = self.tmp()
        self.w(f"{t} = stack.pop()")
        return t

    def popn(self, n):
        items = [self.pop() for _ in range(n)]
        items.reverse()
        return items

    def flush(self):
        vs = self.vs
        if len(vs) == 1:
            self.w(f"stack.append({vs[0]})")
        elif vs:
            self.w(f"stack.extend(({', '.join(vs)},))")
        self.vs = []

    def is_int_const(self, expr):
        return expr[:1].isdigit() or (expr[:1] == "-" and expr[1:2].isdigit())

    def atom(self, expr):
        """expr, safe before `.name` (a number literal needs brackets)."""
        return f"({expr})" if expr[:1].isdigit() or expr[:1] == "-" else expr

    def var(self, expr):
        """expr as a variable (`1 is False` would make Python warn about the literal)."""
        if expr.isidentifier() and expr not in ("True", "False", "None"):
            return expr
        t = self.tmp()
        self.w(f"{t} = {expr}")
        return t

    def is_str_const(self, expr):
        return expr[:1] in ("'", '"')

    # -- the function ----------------------------------------------------------------

    def instr(self, i):
        op, arg = self.ops[i], self.args[i]
        un = _UNFUSE.get(op)
        if un is not None:
            op, arg = un(arg)
        return op, arg

    def build(self):
        code = self.code
        ops = self.ops
        n = len(ops)
        starts = {0} | {t for t in getattr(code, "targets", ()) if 0 <= t < n}
        for i in range(n):
            op = self.instr(i)[0]
            if op == C.LINE:
                raise _NoJit()
            if op in (C.ENTER, C.YIELD, C.JUMP_IF_FALSE, C.JUMP_IF_TRUE) and i + 1 < n:
                starts.add(i + 1)
        starts = sorted(starts)
        name = "".join(ch if ch.isalnum() else "_" for ch in (code.name or "main"))[:40]
        self.w(f"def tb_{name}(it, frame):")
        self.ind = 1
        prologue = len(self.out)
        self.w("blk = frame.pc")
        self.w("while True:")
        self.ind = 2
        self.w("try:")
        self.ind = 3
        self.w("while True:")
        chunks = [starts[k:k + 8] for k in range(0, len(starts), 8)]
        for ci, chunk in enumerate(chunks):
            self.ind = 4
            if ci + 1 < len(chunks):
                self.w(f"if blk < {chunks[ci + 1][0]}:")
                self.ind = 5
            else:
                self.w("if True:")
                self.ind = 5
            base = self.ind
            for start in chunk:
                k = starts.index(start)
                end = starts[k + 1] if k + 1 < len(starts) else n
                self.ind = base
                self.w(f"if blk == {start}:")
                self.ind = base + 1
                self.block(start, end)
        self.ind = 4
        self.w("_bad_jump(blk)")
        self.ind = 2
        self.w("except BaseException as exc:")
        self.w("    blk = _unwind(it, frame, exc)")
        body = "\n".join(self.out[prologue:])
        used = ["    " + line for line in _PROLOGUE if re.search(r"\b" + line.split()[0] + r"\b", body)]
        self.out[prologue:prologue] = used
        self.pcs[prologue:prologue] = [0] * len(used)
        source = "\n".join(self.out) + "\n"
        where = f"<tb fast code {code.name or 'main'} {code.file or ''}>"
        ns = self.ns
        exec(compile(source, where, "exec"), ns)  # noqa: S102 - our own generated code
        fn = ns[f"tb_{name}"]
        fn.source = source
        fn.pcs = self.pcs   # Python line n runs instruction fn.pcs[n - 1] (tb profile reads it)
        return fn

    def block(self, start, end):
        self.vs = []
        i = start
        while i < end:
            if self.instr_code(i):
                return  # control left the block; what follows up to `end` can't run
            i += 1
        self.flush()
        self.w(f"blk = {end}")

    def goto(self, target, here):
        """An unconditional jump at the end of a block (the virtual stack is flushed)."""
        self.w(f"blk = {target}")
        if target <= here:
            self.w("continue")

    # -- one instruction; returns True when control never falls through ------------------

    def instr_code(self, i):
        self.cur = i
        op, arg = self.instr(i)
        line = self.lines[i]
        pc = i + 1
        w = self.w
        O = C
        if op == O.LOAD_FAST:
            self.push_new(f"fast[{arg}]")
        elif op == O.LOAD_CONST:
            self.push(self.const(arg))
        elif op == O.STORE_FAST:
            w(f"fast[{arg}] = {self.pop()}")
        elif op == O.POP:
            if self.vs:
                self.vs.pop()
            else:
                w("stack.pop()")
        elif op == O.DUP:
            v = self.pop()
            self.push(v)
            self.push(v)
        elif op == O.LOAD_GLOBAL:
            name = self.const(arg)
            t = self.push_new(f"gvars.get({name}, UNSET)")
            w(f"if {t} is UNSET:")
            w(f"    if bvars is not None: {t} = bvars.get({name}, UNSET)")
            w(f"    if {t} is UNSET:")
            w(f"        frame.pc = {pc}; {t} = it._load_global(frame, {name}, {line})")
        elif op == O.LOAD_GLOBAL_UPD:
            name = self.const(arg)
            t = self.push_new(f"gvars.get({name}, UNSET)")
            w(f"if {t} is UNSET or genv.consts:")
            w(f"    frame.pc = {pc}; _check_global_assign(it, frame, {name}, None, {line})")
            w(f"    {t} = it._load_global(frame, {name}, {line})")
        elif op == O.STORE_GLOBAL:
            v = self.pop()
            name = self.const(arg)
            w(f"if {name} in gvars and not genv.consts: gvars[{name}] = {v}")
            w(f"else: frame.pc = {pc}; it._store_global(frame, {name}, {v}, {line})")
        elif op == O.LOAD_DEREF:
            t = self.push_new(f"fast[{arg}].v")
            w(f"if {t} is UNSET: frame.pc = {pc}; {t} = it._fallback_load(frame, {i}, {line})")
        elif op == O.STORE_DEREF:
            v = self.pop()
            c = self.tmp()
            w(f"{c} = fast[{arg}]")
            w(f"if {c}.v is UNSET: frame.pc = {pc}; it._fallback_store(frame, {i}, {v}, {line})")
            w(f"else: {c}.v = {v}")
        elif op in _ARITH:
            self.arith(_ARITH[op], pc, line)
        elif op in _COMPARE:
            b, a = self.pop(), self.pop()
            t = self.tmp()
            sym = _COMPARE[op]
            if self.is_int_const(b):
                w(f"if type({a}) is int: {t} = {a} {sym} {b}")
            else:
                w(f"if (type({a}) is int or type({a}) is float) and type({b}) is type({a}): {t} = {a} {sym} {b}")
            w(f"else: frame.pc = {pc}; {t} = it._binary({sym!r}, {a}, {b}, {line})")
            self.push(t)
        elif op == O.EQ or op == O.NE:
            b, a = self.pop(), self.pop()
            t = self.tmp()
            sym, neg = ("==", "") if op == O.EQ else ("!=", "not ")
            if self.is_int_const(b):
                w(f"if type({a}) is int: {t} = {a} {sym} {b}")
            elif self.is_str_const(b):
                w(f"if type({a}) is str: {t} = {a} {sym} {b}")
            else:
                w(f"if type({a}) is type({b}) and (type({a}) is int or type({a}) is str or type({a}) is float):"
                  f" {t} = {a} {sym} {b}")
            w(f"else: frame.pc = {pc}; {t} = {neg}tb_equal({a}, {b})")
            self.push(t)
        elif op == O.MOD:
            b, a = self.pop(), self.pop()
            t = self.tmp()
            if self.is_int_const(b) and b not in ("0", "-0"):
                w(f"if type({a}) is int: {t} = {a} % {b}")
            else:
                w(f"if type({a}) is int and type({b}) is int and {b}: {t} = {a} % {b}")
            w(f"else: frame.pc = {pc}; {t} = it._binary('%', {a}, {b}, {line})")
            self.push(t)
        elif op == O.NOT:
            v = self.var(self.pop())
            self.push_new(f"{v} is None or {v} is False")
        elif op == O.JUMP:
            self.flush()
            self.goto(arg, i)
            return True
        elif op == O.LOOP:
            self.flush()
            w(f"if it.trace is not None: frame.pc = {arg}; it._trace_tick(frame)")
            self.goto(arg, i)
            return True
        elif op == O.JUMP_IF_FALSE or op == O.JUMP_IF_TRUE:
            v = self.var(self.pop())
            self.flush()
            test = f"{v} is False or {v} is None" if op == O.JUMP_IF_FALSE else f"{v} is not False and {v} is not None"
            if arg > i:
                w(f"blk = {arg} if {test} else {pc}")
            else:
                w(f"if {test}:")
                w(f"    blk = {arg}")
                w("    continue")
                w(f"blk = {pc}")
            return True
        elif op == O.FOR_ITER:
            self.flush()
            t = self.tmp()
            w(f"frame.pc = {pc}; {t} = stack[-1]")
            w(f"{t} = next({t}.it if type({t}) is TBIterator else {t}, _END)  # (TBIterator only forwards)")
            w(f"if {t} is _END:")
            w("    stack.pop()")
            w(f"    blk = {arg}")
            w("    continue")
            self.push(t)
        elif op == O.CALL:
            cargs = self.popn(arg)
            fn = self.atom(self.pop())
            al = ", ".join(cargs)
            t = self.tmp()
            n = len(cargs)
            w(f"frame.pc = {pc}")
            w(f"if (type({fn}) is VMFunction and {fn}.tail is not None and {fn}.tmpl._code.fastcall == {n}"
              f" and it.trace is None and it.depth < MAX_DEPTH):")
            self.ind += 1
            self.inline_call(fn, f"[{al}{', ' if al else ''}*{fn}.tail]", t)
            self.ind -= 1
            w(f"elif type({fn}) is Builtin and {fn}.arity[0] <= {n} <= {fn}.arity[1]:")
            w(f"    try: {t} = {fn}.fn({line}{', ' if al else ''}{al})")
            w("    except _PASS_THROUGH: raise")
            w(f"    except Exception as err: raise _wrong({fn}.name, err, {line}) from None")
            w(f"else: {t} = _call(it, frame, {fn}, [{al}], {line})")
            self.push(t)
        elif op == O.LOAD_METHOD:
            obj = self.atom(self.pop())
            m, s, o = self.tmp(), self.tmp(), self.tmp()
            self.nconst += 1
            cache = f"IC{self.nconst}"
            self.ns[cache] = [(None, None, None)]
            name = self.const(arg)
            natives = []
            w(f"ic = {cache}[0]")
            w(f"if type({obj}) is Instance and {obj}.cls is ic[0] and {name} not in {obj}.fields:")
            w(f"    {m} = ic[1]; {s} = {obj}; {o} = ic[2]")
            for pytype, table, extra in (("list", _LIST_METHODS, ""), ("dict", _MAP_METHODS, f" and {name} not in {obj}"),
                                         ("str", _TEXT_METHODS, "")):
                entry = table.get(arg)
                if entry is not None:
                    ename = self.const_obj(entry)
                    natives.append((ename, entry))
                    w(f"elif type({obj}) is {pytype}{extra}: {m} = {ename}; {s} = {obj}; {o} = {name}")
            w(f"else: frame.pc = {pc}; {m}, {s}, {o} = _load_method(it, {obj}, {name}, {line}, {cache})")
            self.methods[m] = (s, o, natives)
            self.push(m)
            self.push(s)
            self.push(o)
        elif op == O.CALL_METHOD:
            cargs = self.popn(arg)
            o, s, m = self.pop(), self.pop(), self.pop()
            al = ", ".join(cargs)
            n = len(cargs)
            t = self.tmp()
            w(f"frame.pc = {pc}")
            rec = self.methods.get(m)
            if rec is not None and rec[0] == s and rec[1] == o:
                kw = "if"
                for ename, (_fn, lo, hi, _doc) in rec[2]:
                    if lo <= n <= hi:
                        w(f"{kw} {m} is {ename}:")
                        w(f"    try: {t} = {ename}[0](it, {line}, {s}{', ' if al else ''}{al})")
                        w("    except _PASS_THROUGH: raise")
                        w(f"    except Exception as err: raise _wrong({o}, err, {line}) from None")
                        kw = "elif"
                w(f"{kw} ({s} is not NOSELF and type({o}) is not str and {m}.tail is not None"
                  f" and {m}.tmpl._code.fastmeth == {n + 2} and it.trace is None and it.depth < MAX_DEPTH):")
                self.ind += 1
                self.inline_call(m, f"[{s}, {o}, {al}{', ' if al else ''}*{m}.tail]", t)
                self.ind -= 1
                w(f"else: {t} = _call_method(it, frame, {m}, {s}, {o}, [{al}], {line})")
            else:
                w(f"{t} = _call_method(it, frame, {m}, {s}, {o}, [{al}], {line})")
            self.push(t)
        elif op == O.GET_ATTR:
            obj = self.atom(self.pop())
            name = self.const(arg)
            t = self.tmp()
            w(f"if type({obj}) is Instance and {name} in {obj}.fields: {t} = {obj}.fields[{name}]")
            w(f"elif type({obj}) is dict and {name} in {obj}: {t} = {obj}[{name}]")
            w(f"else: frame.pc = {pc}; {t} = it.get_field({obj}, {name}, {line})")
            self.push(t)
        elif op == O.SET_ATTR:
            v = self.pop()
            obj = self.atom(self.pop())
            name = self.const(arg)
            w(f"if type({obj}) is Instance: {obj}.fields[{name}] = {v}")
            w(f"else: frame.pc = {pc}; it.set_field({obj}, {name}, {v}, None, {line})")
        elif op == O.INDEX:
            i_, c = self.pop(), self.atom(self.pop())
            t = self.tmp()
            w(f"if type({c}) is list and type({i_}) is int and -len({c}) <= {i_} < len({c}): {t} = {c}[{i_}]")
            w(f"elif type({c}) is dict and type({i_}) is str and {i_} in {c}: {t} = {c}[{i_}]")
            w(f"else: frame.pc = {pc}; {t} = it.index_value({c}, {i_}, {line})")
            self.push(t)
        elif op == O.SET_INDEX:
            v, i_, c = self.pop(), self.pop(), self.atom(self.pop())
            w(f"if type({c}) is list and type({i_}) is int and -len({c}) <= {i_} < len({c}): {c}[{i_}] = {v}")
            w(f"elif type({c}) is dict and type({i_}) is str: {c}[{i_}] = {v}")
            w(f"else: frame.pc = {pc}; it.set_index({c}, {i_}, {v}, None, {line}, {self.const(arg)})")
        elif op == O.BUILD_LIST:
            self.push_new(f"[{', '.join(self.popn(arg))}]")
        elif op == O.FORMAT_SIMPLE:
            v = self.pop()
            if self.is_str_const(v):
                self.push(v)
            else:
                t = self.tmp()
                w(f"if type({v}) is str: {t} = {v}")
                w(f"elif type({v}) is int: {t} = str({v})")
                w(f"else: frame.pc = {pc}; {t} = to_text({v})")
                self.push(t)
        elif op == O.BUILD_STRING:
            parts = self.popn(arg)
            if 0 < len(parts) <= 4:
                self.push_new(" + ".join(parts))
            else:
                self.push_new(f"''.join(({', '.join(parts)},))")
        elif op == O.RETURN:
            v = self.pop()
            self.flush()
            w("kind = frame.kind")
            w("if kind == 0:")
            w(f"    if frame.defers: frame.pc = {pc}; it._run_defers(frame)")
            w("    call_stack.pop()")
            w("    it.depth -= 1")
            w("    if it.trace is not None:")
            w(f"        frame.pc = {pc}")
            w("        it.trace('return', frame.line, frame.file or it.script_path, FrameEnv(frame))")
            w("elif kind == 1:")
            w("    frame.done = True")
            w(f"frame.pc = {pc}")
            w(f"return {v}")
            return True
        elif op == O.ENTER:
            self.flush()
            w("kind = frame.kind")
            w("if kind == 0:")
            w("    frame.entered = True")
            w("    it.depth += 1")
            w("    call_stack.append(frame)")
            w("    if it.trace is not None:")
            w(f"        frame.pc = {pc}")
            w("        it.trace('call', frame.call_line, frame.file or it.script_path, FrameEnv(frame))")
            w("elif kind == 1 or kind == 4:")
            w("    frame.entered = True")
            w(f"    frame.pc = {pc}")
            w("    return None")
        elif op == O.YIELD:
            v = self.pop()
            self.flush()
            w("stack.append(None)")
            w(f"frame.pc = {pc}")
            w(f"return {v}")
            return True
        elif op < C.N_HOT:  # pragma: no cover - a hot instruction this file doesn't know
            raise _NoJit()
        else:
            self.slow(op, arg, i, pc, line)
        return False

    def inline_call(self, fn, fast_list, t):
        """A call of a translated simple function: the VM's fast call path, written out
        (the frame gets exactly the fields VMFrame() and the VM loop give it)."""
        w = self.w
        w(f"c2 = {fn}.tmpl._code")
        w("new = _new(VMFrame)")
        w(f"new.code = c2; new.fast = {fast_list}; new.stack = []; new.back = frame; new.func = {fn}")
        w(f"new.genv = {fn}.genv; new.gvars = {fn}.gvars; new.bvars = {fn}.bvars")
        w("new.entered = True; new.pc = c2.entry + 1")
        w("it.depth += 1")
        w("call_stack.append(new)")
        w(f"{t} = c2.fast(it, new)")

    def arith(self, sym, pc, line):
        b, a = self.pop(), self.pop()
        t = self.tmp()
        w = self.w
        slow = f"frame.pc = {pc}; {t} = it._binary({sym!r}, {a}, {b}, {line})"
        if self.is_int_const(b):
            w(f"if type({a}) is int:")
            w(f"    {t} = {a} {sym} {b}")
            w(f"    if not NBIG < {t} < BIG: {slow}")
        else:
            w(f"if type({a}) is int and type({b}) is int:")
            w(f"    {t} = {a} {sym} {b}")
            w(f"    if not NBIG < {t} < BIG: {slow}")
            if sym == "+":
                w(f"elif type({a}) is str and type({b}) is str: {t} = {a} + {b}")
        w(f"else: {slow}")
        self.push(t)

    def slow(self, op, arg, i, pc, line):
        """A rarer instruction: the VM's own handler on the real stack."""
        self.flush()
        handler = V._SLOW[op]
        hname = f"S_{C.OPNAMES[op]}"
        self.ns[hname] = handler
        argx = self.const(arg)
        self.w(f"frame.pc = {pc}")
        if op in _NO_JUMP:
            self.w(f"{hname}(it, frame, {argx}, stack, {line})")
        else:
            r = self.tmp()
            self.w(f"{r} = {hname}(it, frame, {argx}, stack, {line})")
            self.w(f"if {r} is not None:")
            self.w(f"    blk = {r}")
            self.w("    continue")


# rare instructions known never to jump (their handler returns None)
_NO_JUMP = {getattr(C, n) for n in (
    "DECLARE_GLOBAL", "CHECK_GLOBAL_ASSIGN", "LOAD_GLOBAL_KEY", "LOAD_GLOBAL_SOFT", "DECLARE_DEREF",
    "LOAD_FAST_CHK", "STORE_FAST_CHK", "CONST_ERROR", "NEW_CELLS", "CLEAR_SLOTS", "SWAP", "ROT3",
    "DIV", "FLOORDIV", "POW", "BINARY", "COMPARE_CHAIN", "NEG", "INV", "GET_ITER", "SETUP_TRY",
    "SETUP_FINALLY", "POP_BLOCK", "RERAISE", "CATCH_MATCH", "ERROR_VALUE", "THROW", "ASSERT_FAIL",
    "CALL_EX", "MAKE_FUNCTION", "MAKE_CLASS", "MAKE_ENUM", "DEFER", "EXPORT", "IMPORT", "FROM_IMPORT",
    "IMPORT_NAME", "LIST_APPEND", "LIST_EXTEND", "MAP_NEW", "MAP_KEY", "MAP_PUT", "MAP_SPREAD",
    "FORMAT_VALUE", "SLICE_PREP", "SLICE", "SET_ATTR_OP", "SET_INDEX_OP", "MAKE_RANGE", "IS_TYPE",
    "IS_TYPE_NAMED", "SUPER", "SUPER_ERROR", "AWAIT", "UNPACK_SEQ", "LIST_UNPACK", "MAP_SOURCE",
    "MATCH_REST", "MATCH_FAIL", "TRUNC_STACK", "MARK_STACK", "GET_ITEM_RAW", "BUILD_KWARGS", "TRUTHY",
)}
