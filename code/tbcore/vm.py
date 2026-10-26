"""The bytecode VM: runs the Code objects made by tbcore/compiler.py.

VMInterpreter is a subclass of the tree-walking Interpreter: it keeps the same
public surface (run, evaluate, eval, call, trace, frames(), load_library, the
`api` for native libraries, fork()/current() for threads) and reuses its
helpers for everything that isn't hot (operators on unusual values, fields,
imports, errors), so behaviour and error messages match the tree-walker.

Frames
------
A VMFrame has a `fast` list (one slot per local variable, parameter, free
variable cell and temporary), its own value `stack`, a program counter and a
`blocks` list of active try/finally handlers. TB-to-TB calls made by the CALL
instructions push a new frame inside the same Python loop (no Python
recursion); calls coming from Python (a library calling back, `say()` of an
object with text(), ...) start a nested loop with that frame as its base.

Generators are suspended frames: calling a generator function binds its
parameters and runs its prologue up to ENTER, then the frame is parked; each
next() resumes it until YIELD (or RETURN). `async fn` calls park the same way
and hand the frame to tbcore/tasks.py, which resumes it on a task thread.

The interpreter's per-thread state (depth, call_stack, trace, ...) is the
same as the tree-walker's, so fork()/current() work unchanged.
"""

from __future__ import annotations

import os
import threading

from . import compiler as C
from .compiler import MISSING, UNSET, compile_expression, compile_program
from .errors import (
    MANY, MAX_DEPTH, MAX_RANGE, TOO_BIG, BreakSignal, ContinueSignal, ReturnSignal, TBError, UNSET as FILE_UNSET,
)
from .interp import BUILTIN_TYPE_CHECKS, Interpreter, read_source, tb_source_docs
from .parser import parse, parse_expression
from .runtime import (
    DONE, ERROR_CLASSES, METHODS, Bound, Builtin, EnumType, EnumValue, Env, Function, Instance,
    Module, Range, TBClass, TBIterator, _GenAbort, arity_message, error_value, format_value,
    is_number, is_whole, map_key, missing_key, tb_equal, to_text, truthy, type_name,
)
from . import tasks

# The VM loop tests opcodes by number; make sure the numbering is what it expects.
_EXPECTED = ["LOAD_FAST", "LOAD_CONST", "STORE_FAST", "LOAD_GLOBAL", "JUMP_IF_FALSE", "JUMP", "CALL",
             "ADD", "SUB", "LT", "RETURN", "LOAD_METHOD", "CALL_METHOD", "GET_ATTR", "LOAD_DEREF", "EQ",
             "FOR_ITER", "POP", "INDEX", "MUL", "GT", "LE", "GE", "NE", "SET_ATTR", "SET_INDEX",
             "STORE_DEREF", "JUMP_IF_TRUE", "LOOP", "LINE", "ENTER", "YIELD", "BUILD_LIST", "DUP",
             "FAST_ADD_CONST", "FAST_LT_JUMP", "FAST_CMP_CONST_JUMP", "INC_FAST", "LOAD_FAST2", "NOT",
             "STORE_GLOBAL", "LOAD_GLOBAL_UPD", "GLOBAL_INC", "CONST_OP", "GLOBAL_CMP_CONST_JUMP", "MOD",
             "FORMAT_SIMPLE", "BUILD_STRING", "LOAD_ATTR_FAST", "FOR_ITER_STORE", "CONST_CMP_JUMP"]
assert C.OPNAMES[:C.N_HOT] == _EXPECTED, "tbcore/vm.py's dispatch is out of date with the opcodes"

BIG = 1 << 1100          # whole numbers must stay strictly between -BIG and BIG (MAX_INT_BITS)
NOSELF = C._Marker("NOSELF")
NOBASE = C.NOBASE

# frame kinds
FUNC, GEN, MAIN, BLOCK, ASYNC = 0, 1, 2, 3, 4


class Cell:
    """A variable shared between a function and the closures made inside it."""

    __slots__ = ("v",)

    def __init__(self, v=UNSET):
        self.v = v

    def __repr__(self):
        return f"<cell {self.v!r}>"


class VMFunction(Function):
    """A TB function made by the VM: its template (code), closure cells and globals."""

    __slots__ = ("tmpl", "cells", "genv", "gvars", "bvars", "tail")

    def __init__(self, tmpl, cells, genv, owner=None):
        Function.__init__(self, tmpl.name, tmpl.params, tmpl.body, genv, tmpl.file,
                          tmpl.is_generator, tmpl.is_async, owner)
        self.doc = tmpl.doc
        self.tmpl = tmpl
        self.cells = cells
        self.genv = genv
        self.gvars = genv.vars
        parent = genv.parent
        self.bvars = parent.vars if parent is not None and parent.parent is None else None
        self.tail = None

    def make_tail(self, code):
        """What follows the parameters in a fresh frame: the closure cells, then empty slots."""
        tail = list(self.cells) + [UNSET] * (code.nlocals - code.nparams - len(self.cells))
        if not code.traced:
            self.tail = tail
        return tail


class VMFrame:
    """One running function (or program / module / generator body).
    Rarely-set fields have class-level defaults, so making a frame is cheap."""

    blocks = None        # active try/finally handlers: (kind, target pc, stack depth)
    defers = None        # defer bodies to run at return
    call_line = 0
    kind = FUNC
    entered = False
    tline = 0            # the current line in traced code
    done = False
    running = False
    pc = 0

    def __init__(self, code, fast, func):
        self.code = code
        self.fast = fast
        self.stack = []
        self.back = None
        self.func = func
        self.genv = func.genv
        self.gvars = func.gvars
        self.bvars = func.bvars

    @property
    def fn(self):
        return self.func.name

    @property
    def file(self):
        return self.code.file

    @property
    def line(self) -> int:
        code = self.code
        if code.traced:
            return self.tline
        pc = self.pc
        lines = code.stmt_lines
        if not lines:
            return 0
        return lines[pc - 1 if pc > 0 else 0] if pc <= len(lines) else lines[-1]

    @line.setter
    def line(self, value):
        self.tline = value

    @property
    def env(self):
        return FrameEnv(self)

    def visible(self, top_only=False) -> dict:
        """The variables this frame can see right now (innermost wins)."""
        code = self.code
        fast = self.fast
        pc = self.pc - 1 if self.pc > 0 else 0
        out = {}
        for name, slot in code.free_names:
            v = fast[slot]
            if type(v) is Cell:
                v = v.v
                if v is not UNSET and v is not MISSING:
                    out[name] = v
        for name, slot, start, end, is_cell, depth in sorted(code.varinfo, key=lambda e: e[5]):
            if top_only and depth > 0:
                continue
            if not (start <= pc < end) and not (code.kind != "main" and depth == 0):
                continue
            v = fast[slot]
            if is_cell:
                if type(v) is not Cell:
                    continue
                v = v.v
            if v is UNSET or v is MISSING:
                continue
            out[name] = v
        return out

    def set_visible(self, name, value) -> bool:
        """Write a variable seen by visible() back into its slot (debugger eval)."""
        code = self.code
        fast = self.fast
        pc = self.pc - 1 if self.pc > 0 else 0
        best = None
        for entry in code.varinfo:
            ename, slot, start, end, is_cell, depth = entry
            if ename == name and (start <= pc < end or (code.kind != "main" and depth == 0)):
                if best is None or depth >= best[5]:
                    best = entry
        if best is not None:
            slot, is_cell = best[1], best[4]
            if is_cell:
                if type(fast[slot]) is Cell:
                    fast[slot].v = value
                    return True
                return False
            fast[slot] = value
            return True
        for fname, slot in code.free_names:
            if fname == name and type(fast[slot]) is Cell:
                fast[slot].v = value
                return True
        return False


class _MainFunc:
    """Stands in for the function of a program / module / eval frame."""

    __slots__ = ("name", "genv", "gvars", "bvars")

    def __init__(self, genv):
        self.name = None
        self.genv = genv
        self.gvars = genv.vars
        parent = genv.parent
        self.bvars = parent.vars if parent is not None and parent.parent is None else None


class FrameEnv(Env):
    """An Env view of a VM frame (what trace hooks, frames() and eval() get).
    Reading .vars gives a snapshot of the frame's visible variables."""

    __slots__ = ("frame", "_cache", "_top")

    def __init__(self, frame, top_only=False):
        self.frame = frame
        self.parent = frame.genv
        self.consts = None
        self._cache = None
        self._top = top_only

    @property
    def vars(self):
        if self._cache is None:
            self._cache = self.frame.visible(self._top)
        return self._cache

    @vars.setter
    def vars(self, value):  # pragma: no cover - Env.__init__ isn't called
        self._cache = value


class VMGenerator:
    """The Python-iterator side of a VM generator: resumes its parked frame."""

    __slots__ = ("interp", "frame", "__weakref__")

    def __init__(self, interp, frame):
        self.interp = interp
        self.frame = frame

    def __iter__(self):
        return self

    def __next__(self):
        frame = self.frame
        if frame.done:
            raise StopIteration
        return self.interp.current()._resume(frame)


class VMInterpreter(Interpreter):
    """The default engine: compiles to bytecode and runs it on a stack VM."""

    engine = "vm"

    # -- state the tree-walker keeps in attributes ------------------------------

    @property
    def frame(self):
        return self.call_stack[-1]

    @frame.setter
    def frame(self, value):
        pass

    @property
    def current_file(self):
        stack = self.__dict__.get("call_stack")
        return stack[-1].file if stack else None

    @current_file.setter
    def current_file(self, value):
        pass

    # -- running programs ----------------------------------------------------------

    def _main_frame(self, code, genv, file=None):
        frame = VMFrame(code, [UNSET] * code.nlocals, _MainFunc(genv))
        frame.kind = MAIN
        frame.entered = True
        return frame

    def run(self, program):
        self._owner = threading.get_ident()
        code = compile_program(program.body, None, traced=self.trace is not None)
        frame = self._main_frame(code, self.globals, None)
        stack = self.call_stack
        if len(stack) == 1:
            self.root_frame = frame
            stack[0] = frame
            self._run(frame)
        else:  # pragma: no cover - a program run from inside another
            stack.append(frame)
            try:
                self._run(frame)
            finally:
                stack.pop()

    def evaluate(self, node, env):
        """The value of one expression node, names looked up in env (REPL)."""
        code = compile_expression(node, self.current_file, traced=False)
        return self._run(self._main_frame(code, env, self.current_file))

    def eval(self, source, env=None):
        """Evaluate an expression (or run statements) in env; for the debugger and REPL."""
        env = env if env is not None else self.frame.env
        try:
            expr = parse_expression(source, 1, self.base_dir)
        except TBError:
            program = parse(source, self.base_dir)
            code = compile_program(program.body, self.current_file, traced=False)
            self._run(self._main_frame(code, env, self.current_file))
            if isinstance(env, FrameEnv) and env._cache is not None:
                for name, value in env._cache.items():
                    env.frame.set_visible(name, value)
            return None
        return self.evaluate(expr, env)

    def load_tb_file(self, path, shown, line=None, *, key=None, name=None):
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
            if err.file is FILE_UNSET:
                err.file = path
            raise
        module = Module(name or os.path.splitext(os.path.basename(path))[0])
        module.doc, module.docs = tb_source_docs(source)
        module.path = path
        module.std = name is not None
        module.exports = set() if getattr(program, "uses_export", False) else None
        env = Env(self.builtins)
        module.fields = env.vars
        code = compile_program(program.body, path, traced=self.trace is not None)
        frame = self._main_frame(code, env, path)
        self.loading.append(key)
        outer_module, self.current_module = self.current_module, module
        self.call_stack.append(frame)
        try:
            self._run(frame)
        except TBError as err:
            if err.file is FILE_UNSET:
                err.file = path
            raise
        finally:
            self.loading.pop()
            self.current_module = outer_module
            self.call_stack.pop()
        self.modules[key] = module
        return module

    # -- calls from Python -------------------------------------------------------------

    def call(self, callee, args, line, kwargs=None):
        t = type(callee)
        if t is VMFunction:
            return self._call_vm(callee, args, kwargs, line, None, None, False)
        if t is Bound and type(callee.fn) is VMFunction:
            return self._call_vm(callee.fn, args, kwargs, line, callee.self_value, callee.owner, True)
        return Interpreter.call(self, callee, args, line, kwargs)

    def _too_deep(self, fn, line):
        who = "This function" if fn.name == "anonymous" else f"'{fn.name}'"
        return TBError(
            f"{who} called itself too many times ({MAX_DEPTH} deep). Is there a way for it to stop?", line
        )

    def _frame_for(self, fn, args, kwargs, line, self_value, owner, bound):
        code = fn.tmpl.code(self.trace is not None)
        frame = VMFrame(code, self._bind(fn, code, args, kwargs, line, self_value, owner, bound), fn)
        frame.call_line = line
        frame.tline = line or 0
        if fn.is_generator:
            frame.kind = GEN
        elif fn.is_async:
            frame.kind = ASYNC
        return frame

    def _call_vm(self, fn, args, kwargs, line, self_value, owner, bound):
        if self.depth >= MAX_DEPTH:
            raise self._too_deep(fn, line)
        frame = self._frame_for(fn, args, kwargs, line, self_value, owner, bound)
        kind = frame.kind
        if kind == FUNC:
            return self._run(frame)
        self._run(frame)  # the prologue: defaults and destructuring, up to ENTER
        if kind == GEN:
            return TBIterator(VMGenerator(self, frame), "generator")
        task = tasks.spawn(self, lambda view: view._run_async(frame), fn.name)
        return task.obj

    def _run_async(self, frame):
        """On a task thread: run an async fn's body (parameters already bound)."""
        frame.kind = FUNC
        self.depth += 1
        self.call_stack.append(frame)
        if self.trace is not None:
            self.trace("call", frame.call_line, frame.file or self.script_path, FrameEnv(frame))
        return self._run(frame)

    def _resume(self, frame):
        """Run a parked generator frame to its next yield."""
        if frame.running:
            raise TBError("This generator is already running (it can't ask itself for its next value).",
                          frame.call_line)
        frame.running = True
        self.depth += 1
        self.call_stack.append(frame)
        try:
            value = self._run(frame)
        except BaseException:
            frame.done = True
            raise
        finally:
            frame.running = False
            self.call_stack.pop()
            self.depth -= 1
        if frame.done:
            raise StopIteration
        return value

    def _run_block_fn(self, fn):
        """Run a defer body or a field default: no call of its own (no frame
        on the call stack, no call/return events)."""
        code = fn.tmpl.code(self.trace is not None)
        frame = VMFrame(code, self._bind(fn, code, [], None, 0, None, None, False), fn)
        frame.kind = BLOCK
        return self._run(frame)

    def _run_defers(self, frame):
        defers, frame.defers = frame.defers, None
        for fn in reversed(defers):
            self._run_block_fn(fn)

    def _bind(self, fn, code, args, kwargs, line, self_value, owner, bound) -> list:
        """The new frame's slots: parameters (the tree-walker's bind_params,
        with the same checks and messages), closure cells, empty locals.
        Parameters that take their default hold MISSING; the prologue fills them."""
        tail = fn.tail
        if tail is None or code.traced:
            tail = fn.make_tail(code)
        n_pos = len(code.pos_params)
        if code.simple and not kwargs and len(args) == n_pos:
            if code.is_method:
                fast = [self_value, owner] if bound else [UNSET, UNSET]
                fast.extend(args)
                fast.extend(tail)
            else:
                fast = list(args) + tail
        else:
            fast = [UNSET] * code.nparams + tail
            base = 0
            if code.is_method:
                if bound:
                    fast[0] = self_value
                    fast[1] = owner
                base = 2
            label = fn.name if owner is None else f"{owner.name}.{fn.name}"
            positional = code.pos_param_nodes
            rest = code.rest_slot
            kwrest = code.kwrest_slot
            kwargs = dict(kwargs) if kwargs else {}
            if len(args) > n_pos and rest < 0:
                raise TBError(arity_message(label, fn.required, n_pos, len(args)), line)
            for i, (name, has_default) in enumerate(code.pos_params):
                if i < len(args):
                    if name is not None and name in kwargs:
                        raise TBError(f"{label}() got '{name}' twice: once in order and once by name.", line)
                    value = args[i]
                elif name is not None and name in kwargs:
                    value = kwargs.pop(name)
                elif has_default:
                    value = MISSING
                else:
                    if kwargs and kwrest < 0:
                        unknown = next(iter(kwargs))
                        if unknown not in {p.name for p in positional}:
                            raise self.unknown_keyword(label, unknown, positional, line)
                    raise TBError(arity_message(label, fn.required, MANY if rest >= 0 else n_pos, len(args)), line)
                fast[base + i] = value
            if rest >= 0:
                fast[rest] = list(args[n_pos:])
            if kwargs:
                if kwrest >= 0:
                    fast[kwrest] = kwargs
                else:
                    raise self.unknown_keyword(label, next(iter(kwargs)), positional, line)
            elif kwrest >= 0:
                fast[kwrest] = {}
        for i in code.cell_params:
            fast[i] = Cell(fast[i])
        for i in code.entry_cells:
            fast[i] = Cell(UNSET)
        return fast

    def construct(self, cls, args, kwargs, line):
        inst = Instance(cls)
        if cls.is_error:
            inst.fields.update({"message": "", "line": None, "file": None, "kind": cls.kind, "stack": []})
        fields = inst.fields
        for klass in cls.chain():
            vm_fields = getattr(klass, "vm_fields", None)
            if vm_fields is None:
                for name, node in klass.field_defaults:
                    fields[name] = None if node is None else self.evaluate(node, klass.closure or self.globals)
                continue
            for name, kind, value in vm_fields:
                fields[name] = value if kind == "const" else self._run_block_fn(value)
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

    # -- slow paths used by the loop ---------------------------------------------------------

    def _binary(self, op, a, b, line):
        try:
            return self.binary(op, a, b, line)
        except OverflowError:
            raise TBError(TOO_BIG, line) from None

    def _hint_env(self, frame):
        if isinstance(frame, VMFrame) and frame.kind != MAIN:
            return FrameEnv(frame, top_only=True)
        return frame.genv if isinstance(frame, VMFrame) else frame.env

    def _load_global(self, frame, name, line):
        env = frame.genv
        while env is not None:
            vars_ = env.vars
            if name in vars_:
                return vars_[name]
            env = env.parent
        raise self.unknown_name(name, self._hint_env(frame), line)

    def _store_global(self, frame, name, value, line):
        owner = frame.genv.find(name)
        if owner is None:
            raise TBError(f"'{name}' doesn't exist yet. Use 'let {name} = ...' first.", line)
        if owner.consts and name in owner.consts:
            raise TBError(f"'{name}' is a constant (made with const), so it can't be changed.", line)
        owner.vars[name] = value

    def _fallback_load(self, frame, pc, line):
        fast = frame.fast
        for kind, where in frame.code.fallbacks[pc]:
            if kind == "fast":
                v = fast[where]
            elif kind == "deref":
                c = fast[where]
                if type(c) is not Cell:
                    continue
                v = c.v
            else:
                return self._load_global(frame, where, line)
            if v is not UNSET and v is not MISSING:
                return v
        raise self.unknown_name("?", self._hint_env(frame), line)  # pragma: no cover

    def _fallback_store(self, frame, pc, value, line):
        fast = frame.fast
        for kind, where in frame.code.fallbacks[pc]:
            if kind == "fast":
                if fast[where] is not UNSET:
                    fast[where] = value
                    return
            elif kind == "deref":
                c = fast[where]
                if type(c) is Cell and c.v is not UNSET:
                    c.v = value
                    return
            else:
                self._store_global(frame, where, value, line)
                return

    def _handler_for(self, frame, err) -> int:
        blocks = frame.blocks
        while blocks:
            kind, target, depth = blocks.pop()
            if kind == 1 or isinstance(err, TBError):
                del frame.stack[depth:]
                if kind == 0 and err.stack is None:
                    err.stack = self.snapshot_frames()
                return target
        return -1

    def _leave_frame(self, frame, err):
        """A frame ends because of err; do what the tree-walker's call_function does."""
        kind = frame.kind
        if not frame.entered:
            frame.done = True
            return err
        if kind == FUNC:
            if isinstance(err, TBError):
                if err.file is FILE_UNSET:
                    err.file = frame.file
                if err.stack is None:
                    err.stack = self.snapshot_frames()
                trace = self.trace
                if trace is not None:
                    trace("exception", frame.line, frame.file or self.script_path, FrameEnv(frame))
            if frame.defers:
                try:
                    self._run_defers(frame)
                except BaseException as again:
                    err = again
            self.call_stack.pop()
            self.depth -= 1
        elif kind == GEN:
            frame.done = True
            if isinstance(err, TBError):
                if err.file is FILE_UNSET:
                    err.file = frame.file
                if err.stack is None:
                    err.stack = self.snapshot_frames()
        return err

    def _trace_tick(self, frame):
        """A loop back-edge in fast code while a trace hook is set (a cancel
        request, or a debugger attached mid-run)."""
        trace = self.trace
        if trace is not None:
            trace("line", frame.line, frame.file or self.script_path, FrameEnv(frame))

    def _call_builtin(self, fn, args, line):
        lo, hi = fn.arity
        if not lo <= len(args) <= hi:
            raise TBError(arity_message(fn.name, lo, hi, len(args)), line)
        try:
            return fn.fn(line, *args)
        except (TBError, ReturnSignal, BreakSignal, ContinueSignal, RecursionError, MemoryError, _GenAbort):
            raise
        except Exception as err:  # a library bug: never show a Python traceback
            raise TBError(f"Something went wrong inside {fn.name}(): {err}", line) from None

    def _call_method_builtin(self, entry, name, obj, args, line):
        fn, lo, hi, _doc = entry
        if not lo <= len(args) <= hi:
            raise TBError(arity_message(name, lo, hi, len(args)), line)
        try:
            return fn(self, line, obj, *args)
        except (TBError, ReturnSignal, BreakSignal, ContinueSignal, RecursionError, MemoryError, _GenAbort):
            raise
        except Exception as err:
            raise TBError(f"Something went wrong inside {name}(): {err}", line) from None

    def _load_method(self, obj, name, line, stack):
        """LOAD_METHOD for anything but the fast cases: pushes callee, self, owner."""
        t = type(obj)
        if t is Instance:
            fields = obj.fields
            if name in fields:
                stack[-1] = fields[name]
            else:
                method, owner = obj.cls.find_method(name)
                if type(method) is VMFunction:
                    stack[-1] = method
                    stack.append(obj)
                    stack.append(owner)
                    return
                stack[-1] = self.get_field(obj, name, line)
        else:
            kind = None
            if t is list:
                kind = "list"
            elif t is str:
                kind = "text"
            elif t is dict:
                kind = None if name in obj else "map"
            elif t is bytes:
                kind = "bytes"
            elif t is int or t is float:
                kind = "number"
            elif t is Range:
                kind = "range"
            elif t is TBIterator:
                kind = "generator"
            if kind is not None:
                entry = METHODS[kind].get(name)
                if entry is not None:
                    stack[-1] = entry
                    stack.append(obj)
                    stack.append(name)
                    return
            stack[-1] = self.get_field(obj, name, line)
        stack.append(NOSELF)
        stack.append(None)

    def _is_type_value(self, value, target, line) -> bool:
        if isinstance(target, TBClass):
            return isinstance(value, Instance) and value.cls.is_subclass_of(target)
        if isinstance(target, EnumType):
            return isinstance(value, EnumValue) and value.enum is target
        raise TBError(
            f"'is' needs a class or a type name on its right (like number, text or Dog), not {type_name(target)}.",
            line,
        )

    def _await(self, value, line):
        if isinstance(value, Instance):
            method, owner = value.cls.find_method("wait")
            if method is not None:
                return self.call(Bound(method, value, owner), [], line)
        wait = getattr(value, "wait", None)
        if callable(wait) and not isinstance(value, (dict, list, str, bytes)):
            try:
                return wait()
            except TBError:
                raise
            except Exception as err:
                raise TBError(f"Waiting failed: {err}", line) from None
        return value

    def _make_function(self, frame, tmpl, owner=None):
        fast = frame.fast
        return VMFunction(tmpl, tuple([fast[i] for i in tmpl.free_sources]), frame.genv, owner)

    def _list_unpack(self, value, needed, has_rest, line):
        if isinstance(value, (Range, TBIterator)):
            value = list(value)
        if not isinstance(value, list):
            raise TBError(f"I can only unpack a list into [ ] names, not {type_name(value)}.", line)
        if not has_rest and len(value) != needed:
            raise TBError(
                f"I can't unpack a list of {len(value)} item{'' if len(value) == 1 else 's'} "
                f"into {needed} name{'' if needed == 1 else 's'}. Add '...rest' to collect the extra items.",
                line,
            )
        if has_rest and len(value) < needed:
            raise TBError(f"I need at least {needed} items to unpack here, but the list has {len(value)}.", line)
        return value

    # -- the loop ----------------------------------------------------------------------------

    def _run(self, frame):
        """Run frame (and the TB calls it makes) until it returns or yields."""
        base = frame
        code = frame.code
        ops = code.ops
        opargs = code.args
        lines = code.lines
        fast = frame.fast
        stack = frame.stack
        gvars = frame.gvars
        pc = frame.pc
        call_stack = self.call_stack
        slow = _SLOW
        big = BIG
        nbig = -BIG
        while True:
            try:
                while True:
                    op = ops[pc]
                    arg = opargs[pc]
                    pc += 1
                    if op < 16:
                        if op < 8:
                            if op == 0:  # LOAD_FAST
                                stack.append(fast[arg])
                            elif op == 1:  # LOAD_CONST
                                stack.append(arg)
                            elif op == 2:  # STORE_FAST
                                fast[arg] = stack.pop()
                            elif op == 3:  # LOAD_GLOBAL
                                v = gvars.get(arg, UNSET)
                                if v is UNSET:
                                    bv = frame.bvars
                                    if bv is not None:
                                        v = bv.get(arg, UNSET)
                                    if v is UNSET:
                                        frame.pc = pc
                                        v = self._load_global(frame, arg, lines[pc - 1])
                                stack.append(v)
                            elif op == 4:  # JUMP_IF_FALSE
                                v = stack.pop()
                                if v is None or v is False:
                                    pc = arg
                            elif op == 5:  # JUMP
                                pc = arg
                            elif op == 6:  # CALL
                                if arg:
                                    cargs = stack[-arg:]
                                    del stack[-arg:]
                                else:
                                    cargs = []
                                fn = stack.pop()
                                t = type(fn)
                                if t is VMFunction:
                                    code2 = fn.tmpl._code
                                    if (code2 is not None and code2.simple and len(cargs) == code2.nparams
                                            and self.trace is None and not code2.is_method):
                                        if self.depth >= MAX_DEPTH:
                                            frame.pc = pc
                                            raise self._too_deep(fn, lines[pc - 1])
                                        tail = fn.tail
                                        if tail is None:
                                            tail = fn.make_tail(code2)
                                        cargs.extend(tail)
                                        if code2.cell_params:
                                            for i in code2.cell_params:
                                                cargs[i] = Cell(cargs[i])
                                        if code2.entry_cells:
                                            for i in code2.entry_cells:
                                                cargs[i] = Cell(UNSET)
                                        new = VMFrame(code2, cargs, fn)
                                        new.entered = True
                                        new.back = frame
                                        frame.pc = pc
                                        self.depth += 1
                                        call_stack.append(new)
                                        frame = new
                                        code = code2
                                        ops = code.ops
                                        opargs = code.args
                                        lines = code.lines
                                        fast = cargs
                                        stack = new.stack
                                        gvars = new.gvars
                                        pc = code.entry + 1
                                        continue
                                    if not fn.is_generator and not fn.is_async:
                                        line = lines[pc - 1]
                                        frame.pc = pc
                                        if self.depth >= MAX_DEPTH:
                                            raise self._too_deep(fn, line)
                                        new = self._frame_for(fn, cargs, None, line, None, None, False)
                                        new.back = frame
                                        frame = new
                                        code = new.code
                                        ops = code.ops
                                        opargs = code.args
                                        lines = code.lines
                                        fast = new.fast
                                        stack = new.stack
                                        gvars = new.gvars
                                        pc = 0
                                        continue
                                elif t is Builtin:
                                    frame.pc = pc
                                    lo, hi = fn.arity
                                    if not lo <= len(cargs) <= hi:
                                        raise TBError(arity_message(fn.name, lo, hi, len(cargs)), lines[pc - 1])
                                    try:
                                        stack.append(fn.fn(lines[pc - 1], *cargs))
                                    except _PASS_THROUGH:
                                        raise
                                    except Exception as err:
                                        raise TBError(f"Something went wrong inside {fn.name}(): {err}",
                                                      lines[pc - 1]) from None
                                    continue
                                elif t is Bound and type(fn.fn) is VMFunction and not fn.fn.is_generator \
                                        and not fn.fn.is_async:
                                    line = lines[pc - 1]
                                    frame.pc = pc
                                    if self.depth >= MAX_DEPTH:
                                        raise self._too_deep(fn.fn, line)
                                    new = self._frame_for(fn.fn, cargs, None, line, fn.self_value, fn.owner, True)
                                    new.back = frame
                                    frame = new
                                    code = new.code
                                    ops = code.ops
                                    opargs = code.args
                                    lines = code.lines
                                    fast = new.fast
                                    stack = new.stack
                                    gvars = new.gvars
                                    pc = 0
                                    continue
                                frame.pc = pc
                                stack.append(self.call(fn, cargs, lines[pc - 1]))
                            else:  # 7 ADD
                                b = stack.pop()
                                a = stack[-1]
                                ta = type(a)
                                if ta is int and type(b) is int:
                                    r = a + b
                                    if nbig < r < big:
                                        stack[-1] = r
                                        continue
                                elif ta is str and type(b) is str:
                                    stack[-1] = a + b
                                    continue
                                frame.pc = pc
                                stack[-1] = self._binary("+", a, b, lines[pc - 1])
                        else:
                            if op == 8:  # SUB
                                b = stack.pop()
                                a = stack[-1]
                                if type(a) is int and type(b) is int:
                                    r = a - b
                                    if nbig < r < big:
                                        stack[-1] = r
                                        continue
                                frame.pc = pc
                                stack[-1] = self._binary("-", a, b, lines[pc - 1])
                            elif op == 9:  # LT
                                b = stack.pop()
                                a = stack[-1]
                                ta = type(a)
                                if (ta is int or ta is float) and type(b) is ta:
                                    stack[-1] = a < b
                                else:
                                    frame.pc = pc
                                    stack[-1] = self._binary("<", a, b, lines[pc - 1])
                            elif op == 10:  # RETURN
                                value = stack.pop()
                                kind = frame.kind
                                if kind == 0:  # FUNC
                                    if frame.defers:
                                        frame.pc = pc
                                        self._run_defers(frame)
                                    call_stack.pop()
                                    self.depth -= 1
                                    if self.trace is not None:
                                        frame.pc = pc
                                        self.trace("return", frame.line, frame.file or self.script_path,
                                                   FrameEnv(frame))
                                elif kind == 1:  # GEN
                                    frame.done = True
                                if frame is base:
                                    frame.pc = pc
                                    return value
                                frame = frame.back
                                code = frame.code
                                ops = code.ops
                                opargs = code.args
                                lines = code.lines
                                fast = frame.fast
                                stack = frame.stack
                                gvars = frame.gvars
                                pc = frame.pc
                                stack.append(value)
                            elif op == 11:  # LOAD_METHOD
                                obj = stack[-1]
                                if type(obj) is Instance:
                                    flds = obj.fields
                                    if arg not in flds:
                                        method, owner = obj.cls.find_method(arg)
                                        if type(method) is VMFunction:
                                            stack[-1] = method
                                            stack.append(obj)
                                            stack.append(owner)
                                            continue
                                else:
                                    t = type(obj)
                                    if t is list:
                                        entry = _LIST_METHODS.get(arg)
                                    elif t is str:
                                        entry = _TEXT_METHODS.get(arg)
                                    elif t is dict and arg not in obj:
                                        entry = _MAP_METHODS.get(arg)
                                    else:
                                        entry = None
                                    if entry is not None:
                                        stack[-1] = entry
                                        stack.append(obj)
                                        stack.append(arg)
                                        continue
                                frame.pc = pc
                                self._load_method(obj, arg, lines[pc - 1], stack)
                            elif op == 12:  # CALL_METHOD
                                if arg:
                                    cargs = stack[-arg:]
                                    del stack[-arg:]
                                else:
                                    cargs = []
                                owner = stack.pop()
                                selfv = stack.pop()
                                fn = stack.pop()
                                if selfv is NOSELF:
                                    frame.pc = pc
                                    if type(fn) is VMFunction and not fn.is_generator and not fn.is_async:
                                        line = lines[pc - 1]
                                        if self.depth >= MAX_DEPTH:
                                            raise self._too_deep(fn, line)
                                        new = self._frame_for(fn, cargs, None, line, None, None, False)
                                    elif type(fn) is Builtin:
                                        stack.append(self._call_builtin(fn, cargs, lines[pc - 1]))
                                        continue
                                    else:
                                        stack.append(self.call(fn, cargs, lines[pc - 1]))
                                        continue
                                elif type(owner) is str:
                                    frame.pc = pc
                                    fnc, lo, hi, _doc = fn
                                    if not lo <= len(cargs) <= hi:
                                        raise TBError(arity_message(owner, lo, hi, len(cargs)), lines[pc - 1])
                                    try:
                                        stack.append(fnc(self, lines[pc - 1], selfv, *cargs))
                                    except _PASS_THROUGH:
                                        raise
                                    except Exception as err:
                                        raise TBError(f"Something went wrong inside {owner}(): {err}",
                                                      lines[pc - 1]) from None
                                    continue
                                else:
                                    code2 = fn.tmpl._code
                                    if (code2 is not None and code2.simple and len(cargs) + 2 == code2.nparams
                                            and self.trace is None):
                                        if self.depth >= MAX_DEPTH:
                                            frame.pc = pc
                                            raise self._too_deep(fn, lines[pc - 1])
                                        tail = fn.tail
                                        if tail is None:
                                            tail = fn.make_tail(code2)
                                        fast2 = [selfv, owner]
                                        fast2 += cargs
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
                                        frame.pc = pc
                                        self.depth += 1
                                        call_stack.append(new)
                                        frame = new
                                        code = code2
                                        ops = code.ops
                                        opargs = code.args
                                        lines = code.lines
                                        fast = fast2
                                        stack = new.stack
                                        gvars = new.gvars
                                        pc = code.entry + 1
                                        continue
                                    frame.pc = pc
                                    line = lines[pc - 1]
                                    if fn.is_generator or fn.is_async:
                                        stack.append(self._call_vm(fn, cargs, None, line, selfv, owner, True))
                                        continue
                                    if self.depth >= MAX_DEPTH:
                                        raise self._too_deep(fn, line)
                                    new = self._frame_for(fn, cargs, None, line, selfv, owner, True)
                                new.back = frame
                                frame = new
                                code = new.code
                                ops = code.ops
                                opargs = code.args
                                lines = code.lines
                                fast = new.fast
                                stack = new.stack
                                gvars = new.gvars
                                pc = 0
                            elif op == 13:  # GET_ATTR
                                obj = stack[-1]
                                t = type(obj)
                                if t is Instance:
                                    flds = obj.fields
                                    if arg in flds:
                                        stack[-1] = flds[arg]
                                        continue
                                elif t is dict and arg in obj:
                                    stack[-1] = obj[arg]
                                    continue
                                frame.pc = pc
                                stack[-1] = self.get_field(obj, arg, lines[pc - 1])
                            elif op == 14:  # LOAD_DEREF
                                v = fast[arg].v
                                if v is UNSET:
                                    frame.pc = pc
                                    v = self._fallback_load(frame, pc - 1, lines[pc - 1])
                                stack.append(v)
                            else:  # 15 EQ
                                b = stack.pop()
                                a = stack[-1]
                                ta = type(a)
                                if ta is type(b) and (ta is int or ta is str or ta is float):
                                    stack[-1] = a == b
                                else:
                                    frame.pc = pc
                                    stack[-1] = tb_equal(a, b)
                    elif op < 32:
                        if op < 24:
                            if op == 16:  # FOR_ITER
                                v = next(stack[-1], _END)
                                if v is _END:
                                    stack.pop()
                                    pc = arg
                                else:
                                    stack.append(v)
                            elif op == 17:  # POP
                                stack.pop()
                            elif op == 18:  # INDEX
                                i = stack.pop()
                                c = stack[-1]
                                tc = type(c)
                                if tc is list and type(i) is int and -len(c) <= i < len(c):
                                    stack[-1] = c[i]
                                elif tc is dict and type(i) is str and i in c:
                                    stack[-1] = c[i]
                                else:
                                    frame.pc = pc
                                    stack[-1] = self.index_value(c, i, lines[pc - 1])
                            elif op == 19:  # MUL
                                b = stack.pop()
                                a = stack[-1]
                                if type(a) is int and type(b) is int:
                                    r = a * b
                                    if nbig < r < big:
                                        stack[-1] = r
                                        continue
                                frame.pc = pc
                                stack[-1] = self._binary("*", a, b, lines[pc - 1])
                            elif op == 20 or op == 21 or op == 22:  # GT LE GE
                                b = stack.pop()
                                a = stack[-1]
                                ta = type(a)
                                if (ta is int or ta is float) and type(b) is ta:
                                    stack[-1] = a > b if op == 20 else a <= b if op == 21 else a >= b
                                else:
                                    frame.pc = pc
                                    stack[-1] = self._binary(arg, a, b, lines[pc - 1])
                            elif op == 23:  # NE
                                b = stack.pop()
                                a = stack[-1]
                                ta = type(a)
                                if ta is type(b) and (ta is int or ta is str or ta is float):
                                    stack[-1] = a != b
                                else:
                                    frame.pc = pc
                                    stack[-1] = not tb_equal(a, b)
                        else:
                            if op == 24:  # SET_ATTR
                                v = stack.pop()
                                obj = stack.pop()
                                if type(obj) is Instance:
                                    obj.fields[arg] = v
                                else:
                                    frame.pc = pc
                                    self.set_field(obj, arg, v, None, lines[pc - 1])
                            elif op == 25:  # SET_INDEX
                                v = stack.pop()
                                i = stack.pop()
                                c = stack.pop()
                                if type(c) is list and type(i) is int and -len(c) <= i < len(c):
                                    c[i] = v
                                elif type(c) is dict and type(i) is str:
                                    c[i] = v
                                else:
                                    frame.pc = pc
                                    self.set_index(c, i, v, None, lines[pc - 1], arg)
                            elif op == 26:  # STORE_DEREF
                                c = fast[arg]
                                if c.v is UNSET:
                                    frame.pc = pc
                                    self._fallback_store(frame, pc - 1, stack.pop(), lines[pc - 1])
                                else:
                                    c.v = stack.pop()
                            elif op == 27:  # JUMP_IF_TRUE
                                v = stack.pop()
                                if v is not None and v is not False:
                                    pc = arg
                            elif op == 28:  # LOOP (a back-edge in fast code)
                                pc = arg
                                if self.trace is not None:
                                    frame.pc = pc
                                    self._trace_tick(frame)
                            elif op == 29:  # LINE (traced code only)
                                frame.tline = arg
                                trace = self.trace
                                if trace is not None:
                                    frame.pc = pc
                                    trace("line", arg, frame.file or self.script_path, FrameEnv(frame))
                            elif op == 30:  # ENTER
                                kind = frame.kind
                                if kind == 0:  # FUNC
                                    frame.entered = True
                                    self.depth += 1
                                    call_stack.append(frame)
                                    if self.trace is not None:
                                        frame.pc = pc
                                        self.trace("call", frame.call_line, frame.file or self.script_path,
                                                   FrameEnv(frame))
                                elif kind == 1 or kind == 4:  # GEN / ASYNC: park here
                                    frame.entered = True
                                    frame.pc = pc
                                    return None
                            else:  # 31 YIELD
                                v = stack.pop()
                                stack.append(None)
                                frame.pc = pc
                                return v
                    elif op < 48:
                        if op < 40:
                            if op == 32:  # BUILD_LIST
                                if arg:
                                    v = stack[-arg:]
                                    del stack[-arg:]
                                    stack.append(v)
                                else:
                                    stack.append([])
                            elif op == 33:  # DUP
                                stack.append(stack[-1])
                            elif op == 34:  # FAST_ADD_CONST (x + c)
                                a = fast[arg[0]]
                                if type(a) is int:
                                    r = a + arg[1]
                                    if nbig < r < big:
                                        stack.append(r)
                                        pc = arg[2]
                                        continue
                                stack.append(a)
                            elif op == 35:  # FAST_LT_JUMP (if/while a < b)
                                a = fast[arg[0]]
                                b = fast[arg[1]]
                                if type(a) is int and type(b) is int:
                                    pc = arg[3] if a < b else arg[2]
                                else:
                                    stack.append(a)
                            elif op == 36:  # FAST_CMP_CONST_JUMP (if/while x < 10, ...)
                                a = fast[arg[0]]
                                if type(a) is int:
                                    b = arg[1]
                                    cmp = arg[2]
                                    if cmp == 9:
                                        ok = a < b
                                    elif cmp == 20:
                                        ok = a > b
                                    elif cmp == 21:
                                        ok = a <= b
                                    elif cmp == 22:
                                        ok = a >= b
                                    elif cmp == 15:
                                        ok = a == b
                                    else:
                                        ok = a != b
                                    pc = arg[4] if ok else arg[3]
                                else:
                                    stack.append(a)
                            elif op == 37:  # INC_FAST (x = x + c)
                                i = arg[0]
                                a = fast[i]
                                if type(a) is int:
                                    r = a + arg[1]
                                    if nbig < r < big:
                                        fast[i] = r
                                        pc = arg[2]
                                        continue
                                stack.append(a)
                            elif op == 38:  # LOAD_FAST2
                                stack.append(fast[arg[0]])
                                stack.append(fast[arg[1]])
                                pc += 1
                            else:  # 39 NOT
                                v = stack[-1]
                                stack[-1] = v is None or v is False
                        else:
                            if op == 43:  # CONST_OP (value op int-constant)
                                a = stack[-1]
                                if type(a) is int:
                                    kind, b, nxt = arg
                                    if kind == 7:
                                        r = a + b
                                    elif kind == 8:
                                        r = a - b
                                    elif kind == 9:
                                        r = a < b
                                    elif kind == 45:
                                        r = a % b
                                    elif kind == 15:
                                        r = a == b
                                    elif kind == 19:
                                        r = a * b
                                    elif kind == 20:
                                        r = a > b
                                    elif kind == 21:
                                        r = a <= b
                                    elif kind == 22:
                                        r = a >= b
                                    elif kind == 23:
                                        r = a != b
                                    else:  # FLOORDIV
                                        r = a // b
                                    if type(r) is bool or nbig < r < big:
                                        stack[-1] = r
                                        pc = nxt
                                        continue
                                stack.append(arg[1])
                            elif op == 40:  # STORE_GLOBAL
                                if arg in gvars and not frame.genv.consts:
                                    gvars[arg] = stack.pop()
                                else:
                                    frame.pc = pc
                                    self._store_global(frame, arg, stack.pop(), lines[pc - 1])
                            elif op == 41:  # LOAD_GLOBAL_UPD (x op= simple value)
                                v = gvars.get(arg, UNSET)
                                if v is UNSET or frame.genv.consts:
                                    frame.pc = pc
                                    _op_check_global_assign(self, frame, arg, stack, lines[pc - 1])
                                    v = self._load_global(frame, arg, lines[pc - 1])
                                stack.append(v)
                            elif op == 42:  # GLOBAL_INC (x += int-constant)
                                name = arg[0]
                                v = gvars.get(name, UNSET)
                                if type(v) is int and not frame.genv.consts:
                                    r = v + arg[1]
                                    if nbig < r < big:
                                        gvars[name] = r
                                        pc = arg[2]
                                        continue
                                frame.pc = pc
                                _op_check_global_assign(self, frame, name, stack, lines[pc - 1])
                                stack.append(self._load_global(frame, name, lines[pc - 1]))
                            elif op == 44:  # GLOBAL_CMP_CONST_JUMP (while i < 100 at the top level)
                                name = arg[0]
                                a = gvars.get(name, UNSET)
                                if type(a) is int:
                                    b = arg[1]
                                    cmp = arg[2]
                                    if cmp == 9:
                                        ok = a < b
                                    elif cmp == 20:
                                        ok = a > b
                                    elif cmp == 21:
                                        ok = a <= b
                                    elif cmp == 22:
                                        ok = a >= b
                                    elif cmp == 15:
                                        ok = a == b
                                    else:
                                        ok = a != b
                                    pc = arg[4] if ok else arg[3]
                                    continue
                                if a is UNSET:
                                    frame.pc = pc
                                    a = self._load_global(frame, name, lines[pc - 1])
                                stack.append(a)
                            elif op == 45:  # MOD
                                b = stack.pop()
                                a = stack[-1]
                                if type(a) is int and type(b) is int and b:
                                    stack[-1] = a % b
                                else:
                                    frame.pc = pc
                                    stack[-1] = self._binary("%", a, b, lines[pc - 1])
                            elif op == 46:  # FORMAT_SIMPLE (an f-text hole without a spec)
                                v = stack[-1]
                                t = type(v)
                                if t is not str:
                                    if t is int:
                                        stack[-1] = str(v)
                                    else:
                                        frame.pc = pc
                                        stack[-1] = to_text(v)
                            else:  # 47 BUILD_STRING
                                parts = stack[-arg:]
                                del stack[-arg:]
                                stack.append("".join(parts))
                    elif op < 51:
                        if op == 49:  # FOR_ITER_STORE (next item straight into the loop variable)
                            v = next(stack[-1], _END)
                            if v is _END:
                                stack.pop()
                                pc = arg[0]
                            else:
                                fast[arg[1]] = v
                                pc = arg[2]
                        elif op == 50:  # CONST_CMP_JUMP (value cmp int-constant, then jump if false)
                            a = stack[-1]
                            if type(a) is int:
                                stack.pop()
                                cmp = arg[0]
                                b = arg[1]
                                if cmp == 15:
                                    ok = a == b
                                elif cmp == 9:
                                    ok = a < b
                                elif cmp == 20:
                                    ok = a > b
                                elif cmp == 21:
                                    ok = a <= b
                                elif cmp == 22:
                                    ok = a >= b
                                else:
                                    ok = a != b
                                pc = arg[3] if ok else arg[2]
                            else:
                                stack.append(arg[1])
                        elif op == 48:  # LOAD_ATTR_FAST (x.name, x a local)
                            obj = fast[arg[0]]
                            if type(obj) is Instance:
                                flds = obj.fields
                                name = arg[1]
                                if name in flds:
                                    stack.append(flds[name])
                                    pc += 1
                                    continue
                            stack.append(obj)
                    else:
                        frame.pc = pc
                        r = slow[op](self, frame, arg, stack, lines[pc - 1])
                        if r is not None:
                            pc = r
            except BaseException as exc:
                if type(exc) is StopIteration:  # pragma: no cover - never let one escape a loop
                    exc = TBError("A generator stopped in an unexpected way.", None)
                frame.pc = pc
                err = exc
                while True:
                    target = self._handler_for(frame, err) if frame.blocks else -1
                    if target >= 0:
                        break
                    err = self._leave_frame(frame, err)
                    if frame is base:
                        raise err
                    frame = frame.back
                code = frame.code
                ops = code.ops
                opargs = code.args
                lines = code.lines
                fast = frame.fast
                stack = frame.stack
                gvars = frame.gvars
                pc = target
                stack.append(err)


_END = object()
_LIST_METHODS = METHODS["list"]
_TEXT_METHODS = METHODS["text"]
_MAP_METHODS = METHODS["map"]
_PASS_THROUGH = (TBError, ReturnSignal, BreakSignal, ContinueSignal, RecursionError, MemoryError, _GenAbort)


# ---------------------------------------------------------------------------
# The rarer instructions: slow[op](interp, frame, arg, stack, line) -> new pc or None
# ---------------------------------------------------------------------------

def _op_declare_global(it, frame, arg, stack, line):
    name, const = arg
    frame.genv.declare(name, stack.pop(), const)


def _op_check_global_assign(it, frame, arg, stack, line):
    owner = frame.genv.find(arg)
    if owner is None:
        raise TBError(f"'{arg}' doesn't exist yet. Use 'let {arg} = ...' first.", line)
    if owner.consts and arg in owner.consts:
        raise TBError(f"'{arg}' is a constant (made with const), so it can't be changed.", line)


def _op_load_global_key(it, frame, arg, stack, line):
    env = frame.genv
    while env is not None:
        if arg in env.vars:
            stack.append(env.vars[arg])
            return
        env = env.parent
    raise TBError(
        f"I don't know what '{arg}' is. Map keys that are words need quotes: {{\"{arg}\": ...}}", line
    )


def _op_load_global_soft(it, frame, arg, stack, line):
    env = frame.genv
    while env is not None:
        if arg in env.vars:
            stack.append(env.vars[arg])
            return
        env = env.parent
    stack.append(MISSING)


def _op_declare_deref(it, frame, arg, stack, line):
    frame.fast[arg].v = stack.pop()


def _op_load_fast_chk(it, frame, arg, stack, line):
    v = frame.fast[arg]
    if v is UNSET:
        v = it._fallback_load(frame, frame.pc - 1, line)
    stack.append(v)


def _op_store_fast_chk(it, frame, arg, stack, line):
    if frame.fast[arg] is UNSET:
        it._fallback_store(frame, frame.pc - 1, stack.pop(), line)
    else:
        frame.fast[arg] = stack.pop()


def _op_const_error(it, frame, arg, stack, line):
    raise TBError(f"'{arg}' is a constant (made with const), so it can't be changed.", line)


def _op_new_cells(it, frame, arg, stack, line):
    fast = frame.fast
    for i in arg:
        fast[i] = Cell(UNSET)


def _op_clear_slots(it, frame, arg, stack, line):
    fast = frame.fast
    for i in arg:
        fast[i] = UNSET


def _op_swap(it, frame, arg, stack, line):
    stack[-1], stack[-2] = stack[-2], stack[-1]


def _op_rot3(it, frame, arg, stack, line):
    stack.append(stack.pop(-3))


def _op_binary(it, frame, arg, stack, line):
    b = stack.pop()
    stack[-1] = it._binary(arg, stack[-1], b, line)


def _op_compare_chain(it, frame, arg, stack, line):
    b = stack.pop()
    stack[-1] = truthy(it._binary(arg, stack[-1], b, line))


def _op_neg(it, frame, arg, stack, line):
    value = stack[-1]
    if type(value) is Instance:
        method, owner = value.cls.find_method("negate")
        if method is not None:
            stack[-1] = it.call(Bound(method, value, owner), [], line)
            return
    if not is_number(value):
        raise TBError(f"I can only make numbers negative, not {type_name(value)}.", line,
                      cls=ERROR_CLASSES["TypeError"])
    stack[-1] = -value


def _op_inv(it, frame, arg, stack, line):
    value = stack[-1]
    if not is_whole(value):
        raise TBError(f"'~' (flip the bits) needs a whole number, not {to_text(value, nested=True)}.", line)
    stack[-1] = ~int(value)


def _op_jump_if_false_keep(it, frame, arg, stack, line):
    v = stack[-1]
    if v is None or v is False:
        return arg
    stack.pop()


def _op_jump_if_true_keep(it, frame, arg, stack, line):
    v = stack[-1]
    if v is not None and v is not False:
        return arg
    stack.pop()


def _op_get_iter(it, frame, arg, stack, line):
    items = stack[-1]
    if arg and isinstance(items, dict):
        stack[-1] = iter([[k, v] for k, v in items.items()])
    else:
        stack[-1] = it.iterate(items, line)


def _op_setup_try(it, frame, arg, stack, line):
    if frame.blocks is None:
        frame.blocks = []
    frame.blocks.append((0, arg, len(stack)))


def _op_setup_finally(it, frame, arg, stack, line):
    if frame.blocks is None:
        frame.blocks = []
    frame.blocks.append((1, arg, len(stack)))


def _op_pop_block(it, frame, arg, stack, line):
    frame.blocks.pop()


def _op_reraise(it, frame, arg, stack, line):
    raise stack.pop()


def _op_catch_match(it, frame, arg, stack, line):
    cls = stack.pop()
    stack.append(it.error_matches(frame.fast[arg], cls, line))


def _op_error_value(it, frame, arg, stack, line):
    stack[-1] = error_value(stack[-1])


def _op_throw(it, frame, arg, stack, line):
    raise it.make_throw(stack.pop(), line)


def _op_assert_fail(it, frame, arg, stack, line):
    if arg:
        message = to_text(stack.pop())
    else:
        message = "An assert check failed (the condition was not true)."
    raise TBError(message, line, cls=ERROR_CLASSES["AssertionError"])


def _op_call_ex(it, frame, arg, stack, line):
    kwargs = stack.pop() if arg else None
    args = stack.pop()
    fn = stack.pop()
    stack.append(it.call(fn, args, line, kwargs))


def _op_build_kwargs(it, frame, arg, stack, line):
    args = stack.pop()
    n = len(arg)
    values = stack[-n:]
    del stack[-n:]
    kwargs = {}
    for name, value in zip(arg, values):
        kwargs[name] = value
    stack.append(args)
    stack.append(kwargs)


def _op_make_function(it, frame, arg, stack, line):
    stack.append(it._make_function(frame, arg))


def _op_make_class(it, frame, arg, stack, line):
    name, static_names, methods, static_methods, fields, field_nodes, doc = arg
    n = len(static_names)
    values = stack[-n:] if n else []
    if n:
        del stack[-n:]
    base = stack.pop()
    if base is NOBASE:
        base = None
    elif not isinstance(base, TBClass):
        raise TBError(f"'{name}' can only extend a class, not {type_name(base)}.", line)
    cls = TBClass(name, base, interp=it, file=frame.file)
    cls.closure = frame.genv
    cls.field_defaults = field_nodes
    cls.doc = doc
    for sname, value in zip(static_names, values):
        cls.statics[sname] = value
    for tmpl in methods:
        cls.methods[tmpl.name] = it._make_function(frame, tmpl, cls)
    for tmpl in static_methods:
        cls.static_methods[tmpl.name] = it._make_function(frame, tmpl, cls)
    vm_fields = []
    for fname, kind, value in fields:
        if kind == "thunk":
            value = it._make_function(frame, value)
        vm_fields.append((fname, kind, value))
    cls.vm_fields = vm_fields
    stack.append(cls)


def _op_make_enum(it, frame, arg, stack, line):
    stack.append(EnumType(arg[0], arg[1]))


def _op_defer(it, frame, arg, stack, line):
    if frame.defers is None:
        frame.defers = []
    frame.defers.append(it._make_function(frame, arg))


def _op_export(it, frame, arg, stack, line):
    module = it.current_module
    if module is not None and module.exports is not None:
        module.exports.add(arg)


def _op_import(it, frame, arg, stack, line):
    stack.append(it.import_node(arg))


def _op_from_import(it, frame, arg, stack, line):
    stack.append(it.import_target(arg.target, arg.is_file, arg.base_dir, arg.line,
                                  getattr(arg, "package_name", None)))


def _op_import_name(it, frame, arg, stack, line):
    stack[-1] = it.get_field(stack[-1], arg, line)


def _op_list_append(it, frame, arg, stack, line):
    v = stack.pop()
    stack[-1].append(v)


def _op_list_extend(it, frame, arg, stack, line):
    v = stack.pop()
    stack[-1].extend(it.iterate(v, line, "'...'"))


def _op_map_new(it, frame, arg, stack, line):
    stack.append({})


def _op_map_key(it, frame, arg, stack, line):
    stack[-1] = map_key(stack[-1], line)


def _op_map_put(it, frame, arg, stack, line):
    v = stack.pop()
    k = stack.pop()
    stack[-1][k] = v


def _op_map_spread(it, frame, arg, stack, line):
    extra = stack.pop()
    if isinstance(extra, Instance):
        extra = extra.fields
    if not isinstance(extra, dict):
        raise TBError(f"'...' inside a map needs another map, not {type_name(extra)}.", line)
    stack[-1].update(extra)


def _op_format_value(it, frame, arg, stack, line):
    spec, conv = arg
    value = stack[-1]
    if conv == "r":
        text = to_text(value, nested=True)
        if spec:
            text = format_value(text, spec, line)
    elif spec:
        text = format_value(value, spec, line)
    else:
        text = value if type(value) is str else to_text(value)
    stack[-1] = text


def _op_slice_prep(it, frame, arg, stack, line):
    container = stack[-1]
    if isinstance(container, Range):
        container = list(container)
        stack[-1] = container
    if not isinstance(container, (list, str, bytes)):
        raise TBError(f"Only lists, text and bytes can be sliced with [a:b], not {type_name(container)}.", line)


def _op_slice(it, frame, arg, stack, line):
    step = stack.pop()
    stop = stack.pop()
    start = stack.pop()
    parts = []
    for value in (start, stop, step):
        if value is None:
            parts.append(None)
        elif is_whole(value):
            parts.append(int(value))
        else:
            raise TBError(f"Slice positions must be whole numbers, not {to_text(value, nested=True)}.", line)
    if parts[2] == 0:
        raise TBError("A slice can't step by 0.", line)
    stack[-1] = stack[-1][parts[0]:parts[1]:parts[2]]


def _op_set_attr_op(it, frame, arg, stack, line):
    v = stack.pop()
    obj = stack.pop()
    it.set_field(obj, arg[0], v, arg[1], line)


def _op_set_index_op(it, frame, arg, stack, line):
    v = stack.pop()
    i = stack.pop()
    c = stack.pop()
    it.set_index(c, i, v, arg[0], line, arg[1])


def _op_make_range(it, frame, arg, stack, line):
    stop = stack.pop()
    start = stack.pop()
    for value in (start, stop):
        if not is_whole(value):
            raise TBError(
                f"A range needs whole numbers on both sides (like 1..10), not {to_text(value, nested=True)}.",
                line,
            )
    stack.append(Range(int(start), int(stop), arg))


def _op_is_type(it, frame, arg, stack, line):
    target = stack.pop()
    value = stack.pop()
    result = it._is_type_value(value, target, line)
    stack.append((not result) if arg else result)


def _op_is_type_named(it, frame, arg, stack, line):
    name, negated = arg
    target = stack.pop()
    value = stack.pop()
    if target is MISSING or not isinstance(target, (TBClass, EnumType)):
        result = BUILTIN_TYPE_CHECKS[name](value)
    else:
        result = it._is_type_value(value, target, line)
    stack.append((not result) if negated else result)


def _op_super(it, frame, arg, stack, line):
    self_value = stack.pop()
    cls = stack.pop()
    if cls.base is None:
        raise TBError(f"{cls.name} doesn't extend another class, so there is no 'super'.", line)
    if isinstance(self_value, TBClass):
        method, found = cls.base.find_static_method(arg)
    else:
        method, found = cls.base.find_method(arg)
    if method is None:
        raise TBError(f"{cls.base.name} (the parent of {cls.name}) has no method called '{arg}'.", line)
    stack.append(Bound(method, self_value, found))


def _op_super_error(it, frame, arg, stack, line):
    raise TBError("'super' only works inside a class's methods.", line)


def _op_await(it, frame, arg, stack, line):
    stack[-1] = it._await(stack[-1], line)


def _line_of(frame, line):
    return frame.call_line if line == -1 else line


def _op_unpack_seq(it, frame, arg, stack, line):
    count, verb = arg
    values = it.unpack(stack.pop(), count, line, verb)
    stack.extend(reversed(values))


def _op_list_unpack(it, frame, arg, stack, line):
    needed, has_rest = arg
    value = it._list_unpack(stack.pop(), needed, has_rest, _line_of(frame, line))
    if has_rest:
        stack.append(list(value[needed:]))
    stack.extend(reversed(value[:needed]))


def _op_map_source(it, frame, arg, stack, line):
    value = stack[-1]
    if isinstance(value, Instance):
        stack[-1] = value.fields
    elif not isinstance(value, dict):
        raise TBError(f"I can only unpack a map into {{ }} names, not {type_name(value)}.", _line_of(frame, line))


def _op_map_take(it, frame, arg, stack, line):
    key, use_default = arg
    source = stack.pop()
    if key in source:
        stack.append(source[key])
        return None
    if use_default is not None:
        return use_default
    raise missing_key(source, key, _line_of(frame, line), " Give it a default: {" + f"{key} = ..." + "}")


def _op_default_check(it, frame, arg, stack, line):
    slot, is_cell, skip = arg
    v = frame.fast[slot]
    if is_cell:
        v = v.v
    if v is not MISSING:
        return skip


def _op_match_literal(it, frame, arg, stack, line):
    if not tb_equal(arg[0], stack.pop()):
        return arg[1]


def _op_match_value(it, frame, arg, stack, line):
    value = stack.pop()
    expected = stack.pop()
    if not tb_equal(expected, value):
        return arg


def _op_match_range(it, frame, arg, stack, line):
    lo, hi, inclusive, fail = arg
    value = stack.pop()
    if not is_number(value):
        return fail
    if not (lo <= value <= hi if inclusive else lo <= value < hi):
        return fail


def _op_match_list(it, frame, arg, stack, line):
    n, has_rest, fail = arg
    value = stack[-1]
    if isinstance(value, Range):
        value = list(value)
        stack[-1] = value
    if not isinstance(value, list) or (len(value) != n if not has_rest else len(value) < n):
        stack.pop()
        return fail


def _op_match_rest(it, frame, arg, stack, line):
    stack[-1] = list(stack[-1][arg:])


def _op_match_map(it, frame, arg, stack, line):
    value = stack[-1]
    if isinstance(value, Instance):
        stack[-1] = value.fields
    elif not isinstance(value, dict):
        stack.pop()
        return arg


def _op_match_map_key(it, frame, arg, stack, line):
    key, fail = arg
    source = stack.pop()
    key = (int(key) if isinstance(key, float) and key.is_integer() and abs(key) < 1e16 else key) \
        if is_number(key) else key
    if key not in source:
        return fail
    stack.append(source[key])


def _op_match_class(it, frame, arg, stack, line):
    fail, skip = arg
    value = stack.pop()
    target = stack.pop()
    if isinstance(target, EnumType):
        if not (isinstance(value, EnumValue) and value.enum is target):
            return fail
        return skip
    if not isinstance(target, TBClass):
        raise TBError(
            f"A pattern like {to_text(target, nested=True)}(...) needs a class, not {type_name(target)}.", line
        )
    if not (isinstance(value, Instance) and value.cls.is_subclass_of(target)):
        return fail


def _op_match_field(it, frame, arg, stack, line):
    name, fail = arg
    value = stack.pop()
    if name not in value.fields:
        return fail
    stack.append(value.fields[name])


def _op_match_fail(it, frame, arg, stack, line):
    subject = stack.pop()
    raise TBError(
        f"Nothing in this match fits {to_text(subject, nested=True)}. Add a '_ => ...' arm for everything else.",
        line,
    )


def _op_trunc_stack(it, frame, arg, stack, line):
    del stack[frame.fast[arg]:]


def _op_mark_stack(it, frame, arg, stack, line):
    frame.fast[arg] = len(stack)


def _op_get_item_raw(it, frame, arg, stack, line):
    stack[-1] = stack[-1][arg]


def _op_truthy(it, frame, arg, stack, line):
    stack[-1] = truthy(stack[-1])


def _op_missing(it, frame, arg, stack, line):  # pragma: no cover
    raise TBError("I don't know how to run this (a broken instruction).", line)


_SLOW = [_op_missing] * len(C.OPNAMES)
for _name in C.OPNAMES[C.N_HOT:]:
    _fn = globals().get("_op_" + _name.lower())
    if _fn is not None:
        _SLOW[getattr(C, _name)] = _fn
for _name in ("DIV", "FLOORDIV", "POW"):
    _SLOW[getattr(C, _name)] = _op_binary
del _name, _fn
_missing = [n for n in C.OPNAMES[C.N_HOT:] if _SLOW[getattr(C, n)] is _op_missing]
assert not _missing, f"VM instructions without a handler: {_missing}"
