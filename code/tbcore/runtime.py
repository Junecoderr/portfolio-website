"""Runtime values and the behaviour of built-in values.

Here live: Env (scopes), Function/Builtin/Module, classes and instances,
enums, ranges, generators, the `done` constant, how values are shown
(`to_text`) and compared (`tb_equal`), the methods of text/lists/maps/numbers/
bytes/ranges/generators/functions, format specs, and the Error class family.
"""

from __future__ import annotations

import functools
import math
import os
import re
import threading
import weakref

from .errors import (
    MANY, MAX_INT_BITS, MAX_POW_BITS, TOO_BIG, BreakSignal, ContinueSignal, ReturnSignal,
    TBError, check_size, snake_case,
)

# ---------------------------------------------------------------------------
# Scopes, functions, modules
# ---------------------------------------------------------------------------


class Env:
    __slots__ = ("vars", "parent", "consts")

    def __init__(self, parent: Env | None = None):
        self.vars: dict = {}
        self.parent = parent
        self.consts: set | None = None   # names made with const, if any

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

    def declare(self, name: str, value, const: bool = False) -> None:
        self.vars[name] = value
        if const:
            if self.consts is None:
                self.consts = set()
            self.consts.add(name)
        elif self.consts and name in self.consts:
            self.consts.discard(name)

    def is_const(self, name: str) -> bool:
        return bool(self.consts) and name in self.consts


class Function:
    __slots__ = ("name", "params", "body", "closure", "file", "is_generator", "is_async",
                 "owner", "doc", "bound_args")

    def __init__(self, name, params, body, closure: Env, file=None, is_generator=False,
                 is_async=False, owner=None):
        self.name, self.params, self.body, self.closure = name, params, body, closure
        self.file = file  # the .tb file it was written in (None: the main program)
        self.is_generator = is_generator
        self.is_async = is_async
        self.owner = owner       # the class a method belongs to (for super)
        self.doc = ""
        self.bound_args = None

    @property
    def arity(self) -> int:
        """How many plain (positional) inputs the function declares."""
        return sum(1 for p in self.params if p.kind == "pos")

    @property
    def required(self) -> int:
        return sum(1 for p in self.params if p.kind == "pos" and p.default is None)

    @property
    def has_rest(self) -> bool:
        return any(p.kind == "rest" for p in self.params)


_IDENT = re.compile(r"[A-Za-z_]\w*\Z")


def doc_params(doc: str | None):
    """The inputs a doc signature line declares, as (names, required, rest):
    `map(fn, items, workers?) -> list` gives (["fn", "items", "workers"], 2, False).
    `x?` and `x = default` are optional, `...` / `...args` is the rest.
    None when the first line isn't a signature we can read."""
    first = (doc or "").partition("\n")[0]
    start = first.find("(")
    if start <= 0 or not _IDENT.match(first[:start].strip()):
        return None
    depth, end = 0, -1
    for i in range(start, len(first)):
        depth += first[i] in "([{"
        depth -= first[i] in ")]}"
        if depth == 0:
            end = i
            break
    if end < 0:
        return None
    inside = first[start + 1:end].strip()
    names, required, rest, optional_seen = [], 0, False, False
    for piece in (inside.split(",") if inside else []):
        piece = piece.strip()
        if rest:
            return None  # nothing may follow the rest
        if piece.startswith("..."):
            rest = True
            if piece != "..." and not _IDENT.match(piece[3:]):
                return None
            continue
        optional = piece.endswith("?") or "=" in piece
        name = piece.partition("=")[0].strip().rstrip("?")
        if not _IDENT.match(name) or name in names or (optional_seen and not optional):
            return None
        optional_seen = optional_seen or optional
        required += not optional
        names.append(name)
    return names, required, rest


class Builtin:
    __slots__ = ("name", "fn", "arity", "doc", "_params")

    def __init__(self, name, fn, arity: tuple[int, int], doc: str = ""):
        self.name, self.fn, self.arity, self.doc = name, fn, arity, doc
        self._params = False

    @property
    def params(self):
        """Names of the inputs, read from the doc signature, so calls can give
        them by name (TB 3.1). None when the doc doesn't match the arity, or
        the function takes any number of inputs (`join(part, ...)`)."""
        if self._params is False:
            self._params = native_params(self.doc, self.arity)
        return self._params


def native_params(doc, arity):
    """Input names for a function written in Python, or None (see Builtin.params)."""
    parsed = doc_params(doc)
    ok = parsed is not None and not parsed[2] and (parsed[1], len(parsed[0])) == tuple(arity)
    return tuple(parsed[0]) if ok else None


class Partial:
    """fn.bind(a, b): a function with some inputs already filled in."""

    __slots__ = ("fn", "args", "kwargs", "name")

    def __init__(self, fn, args, kwargs=None):
        self.fn, self.args, self.kwargs = fn, list(args), dict(kwargs or {})
        self.name = getattr(fn, "name", "function")


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
        self.exports: set | None = None  # names the file exported (None: everything public)

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

    def is_public(self, name: str) -> bool:
        if name.startswith("_"):
            return False
        return self.exports is None or name in self.exports

    def public_names(self) -> list[str]:
        return [k for k in self.fields if self.is_public(k)]

    def __repr__(self) -> str:
        return f"<library {self.name}>"


# ---------------------------------------------------------------------------
# Classes, instances, enums
# ---------------------------------------------------------------------------


class NativeMethod:
    """A method written in Python: fn(interp, line, self_value, *args)."""

    __slots__ = ("name", "fn", "arity", "doc")

    def __init__(self, name, fn, arity: tuple[int, int], doc: str = ""):
        self.name, self.fn, self.arity, self.doc = name, fn, arity, doc


class TBClass:
    def __init__(self, name: str, base: TBClass | None = None, *, interp=None, file=None):
        self.name = name
        self.base = base
        self.field_defaults: list = []        # (name, value node or None), this class only
        self.methods: dict = {}               # name -> Function | NativeMethod
        self.static_methods: dict = {}        # name -> Function | NativeMethod
        self.statics: dict = {}               # name -> value (class fields), this class only
        self.closure: Env | None = None
        self.interp = interp
        self.file = file
        self.doc = ""
        self.is_error = base.is_error if base is not None else False

    @property
    def kind(self) -> str:
        return "error" if self.name == "Error" else snake_case(self.name)

    def find_method(self, name: str):
        cls = self
        while cls is not None:
            if name in cls.methods:
                return cls.methods[name], cls
            cls = cls.base
        return None, None

    def find_static_method(self, name: str):
        cls = self
        while cls is not None:
            if name in cls.static_methods:
                return cls.static_methods[name], cls
            cls = cls.base
        return None, None

    def find_static_owner(self, name: str):
        cls = self
        while cls is not None:
            if name in cls.statics:
                return cls
            cls = cls.base
        return None

    def is_subclass_of(self, other: TBClass) -> bool:
        cls = self
        while cls is not None:
            if cls is other:
                return True
            cls = cls.base
        return False

    def chain(self) -> list[TBClass]:
        """Base classes first, this class last."""
        out = []
        cls = self
        while cls is not None:
            out.append(cls)
            cls = cls.base
        return out[::-1]

    def all_field_names(self) -> list[str]:
        names: list[str] = []
        for cls in self.chain():
            for name, _value in cls.field_defaults:
                if name not in names:
                    names.append(name)
        return names

    def all_method_names(self) -> list[str]:
        names: list[str] = []
        for cls in self.chain():
            for name in cls.methods:
                if name not in names:
                    names.append(name)
        return names

    def all_static_names(self) -> list[str]:
        names: list[str] = []
        for cls in self.chain():
            for name in list(cls.statics) + list(cls.static_methods):
                if name not in names:
                    names.append(name)
        return names

    def __repr__(self) -> str:
        return f"<class {self.name}>"


class Instance:
    __slots__ = ("cls", "fields", "__weakref__")

    def __init__(self, cls: TBClass, fields: dict | None = None):
        self.cls = cls
        self.fields = fields if fields is not None else {}

    def __repr__(self) -> str:
        return f"<{self.cls.name} instance>"


class Bound:
    """A method attached to its instance (or a class, for static methods)."""

    __slots__ = ("fn", "self_value", "owner")

    def __init__(self, fn, self_value, owner: TBClass):
        self.fn, self.self_value, self.owner = fn, self_value, owner

    @property
    def name(self) -> str:
        return getattr(self.fn, "name", "method")


class EnumType:
    def __init__(self, name: str, members: list[str]):
        self.name = name
        self.members: dict = {}
        for index, member in enumerate(members):
            self.members[member] = EnumValue(self, member, index)

    def values(self) -> list:
        return list(self.members.values())

    def __repr__(self) -> str:
        return f"<enum {self.name}>"


class EnumValue:
    __slots__ = ("enum", "name", "index")

    def __init__(self, enum: EnumType, name: str, index: int):
        self.enum, self.name, self.index = enum, name, index


class Range:
    """a..b (exclusive) or a..=b (inclusive), with an optional step."""

    __slots__ = ("start", "stop", "step", "inclusive")

    def __init__(self, start: int, stop: int, inclusive: bool = False, step: int = 1):
        self.start, self.stop, self.inclusive, self.step = start, stop, inclusive, step

    def py_range(self) -> range:
        stop = self.stop + (1 if self.step > 0 else -1) if self.inclusive else self.stop
        return range(self.start, stop, self.step)

    def __iter__(self):
        return iter(self.py_range())

    def __len__(self) -> int:
        return len(self.py_range())

    def __contains__(self, value) -> bool:
        if not is_number(value):
            return False
        if isinstance(value, float):
            if not value.is_integer():
                return False
            value = int(value)
        return value in self.py_range()


class _Done:
    """The value a generator's next() gives back when there is nothing left."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "done"


DONE = _Done()


class TBIterator:
    """A generator / lazy sequence: wraps a Python iterator."""

    __slots__ = ("it", "label", "__weakref__")

    def __init__(self, it, label: str = "generator"):
        self.it = it
        self.label = label

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.it)


# ---------------------------------------------------------------------------
# Generators on a helper thread (the tree-walker's simple, correct approach)
# ---------------------------------------------------------------------------


class _GenAbort(BaseException):
    """Unwinds a generator body whose consumer went away."""


class GenChannel:
    """Hands control between the program and a generator body running on its
    own thread. Only one side runs at a time (the other waits on a semaphore)."""

    __slots__ = ("to_gen", "to_main", "value", "error", "finished", "abort", "started",
                 "thread", "state")

    def __init__(self):
        self.to_gen = threading.Semaphore(0)
        self.to_main = threading.Semaphore(0)
        self.value = None
        self.error = None
        self.finished = False
        self.abort = False
        self.started = False
        self.thread = None
        self.state = None

    def yield_(self, value):
        """Called on the generator thread by `yield`."""
        self.value = value
        self.to_main.release()
        self.to_gen.acquire()
        if self.abort:
            raise _GenAbort()
        return None

    def close(self):
        """Called when the consumer is garbage-collected: wake the thread so it unwinds."""
        if self.started and not self.finished:
            self.abort = True
            self.to_gen.release()


class ThreadGenerator:
    """The Python-iterator side of a TB generator function call."""

    __slots__ = ("interp", "fn", "scope", "channel", "state", "__weakref__")

    def __init__(self, interp, fn, scope):
        self.interp, self.fn, self.scope = interp, fn, scope
        self.channel = GenChannel()
        self.state = None
        weakref.finalize(self, self.channel.close)

    def __iter__(self):
        return self

    def __next__(self):
        channel = self.channel
        if channel.finished:
            raise StopIteration
        interp = self.interp
        if not channel.started:
            channel.started = True
            # TB 3.0 threads: the body runs on (and swaps state with) the view
            # of the thread that starts it, which may not be the one that made it.
            self.interp = interp = interp.current()
            self.state = interp.generator_start_state(self.fn, channel)
            thread = threading.Thread(
                target=interp.run_generator_body, args=(self.fn, self.scope, channel),
                name=f"tb-gen-{self.fn.name}", daemon=True,
            )
            channel.thread = thread
            thread.start()
        main_state = interp.save_state()
        interp.restore_state(self.state)
        channel.to_gen.release()
        channel.to_main.acquire()
        self.state = interp.save_state()
        interp.restore_state(main_state)
        if channel.error is not None:
            err, channel.error = channel.error, None
            raise err
        if channel.finished:
            raise StopIteration
        return channel.value


# ---------------------------------------------------------------------------
# Showing and comparing values
# ---------------------------------------------------------------------------


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
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, Module):
        return "library"
    if isinstance(value, Instance):
        return value.cls.name
    if isinstance(value, TBClass):
        return "class"
    if isinstance(value, EnumValue):
        return value.enum.name
    if isinstance(value, EnumType):
        return "enum"
    if isinstance(value, Range):
        return "range"
    if isinstance(value, TBIterator):
        return "generator"
    if value is DONE:
        return "done"
    if isinstance(value, (Function, Builtin, Bound, Partial, NativeMethod)) or callable(value):
        return "function"
    return "value"


def tb_type(value) -> str:
    """What TB's type(x) returns."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Module):
        return "module"
    return type_name(value)


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_whole(value) -> bool:
    return is_number(value) and (isinstance(value, int) or value.is_integer())


def is_callable(value) -> bool:
    return isinstance(value, (Function, Builtin, Bound, Partial)) or (
        callable(value) and not isinstance(value, (Module, TBClass, EnumType, type))
    )


def tidy_number(value):
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        return int(value)
    return value


def show_bytes(data: bytes) -> str:
    out = ['b"']
    for b in data:
        if b == 0x22:
            out.append('\\"')
        elif b == 0x5C:
            out.append("\\\\")
        elif b == 0x0A:
            out.append("\\n")
        elif b == 0x09:
            out.append("\\t")
        elif b == 0x0D:
            out.append("\\r")
        elif b == 0:
            out.append("\\0")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    out.append('"')
    return "".join(out)


def show_range(r: Range) -> str:
    text = f"{r.start}{'..=' if r.inclusive else '..'}{r.stop}"
    return f"({text}).step({r.step})" if r.step != 1 else text


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
    if isinstance(value, bytes):
        return show_bytes(value)
    if isinstance(value, Instance):
        return show_instance(value, nested, _seen)
    if isinstance(value, EnumValue):
        return f"{value.enum.name}.{value.name}"
    if isinstance(value, EnumType):
        return f"<enum {value.name}>"
    if isinstance(value, TBClass):
        return f"<class {value.name}>"
    if isinstance(value, Range):
        return show_range(value)
    if isinstance(value, TBIterator):
        return f"<{value.label}>"
    if value is DONE:
        return "done"
    if isinstance(value, Function):
        return f"<fn {value.name}>"
    if isinstance(value, Bound):
        return f"<fn {value.owner.name}.{value.name}>"
    if isinstance(value, Partial):
        return f"<fn {value.name}>"
    if isinstance(value, Builtin):
        return f"<built-in fn {value.name}>"
    if isinstance(value, Module):
        return f"<library {value.name}>"
    if callable(value):
        return f"<fn {getattr(value, '__name__', 'function')}>"
    return str(value)


def show_instance(inst: Instance, nested: bool, _seen: frozenset) -> str:
    method, _owner = inst.cls.find_method("text")
    if isinstance(method, NativeMethod):   # a Python-backed class (tblib native_class)
        result = method.fn(None, None, inst)
        return result if isinstance(result, str) else to_text(result)
    if method is not None and inst.cls.interp is not None:
        result = inst.cls.interp.call(Bound(method, inst, inst.cls), [], None)
        text = result if isinstance(result, str) else to_text(result)
        return text
    if id(inst) in _seen:
        return f"{inst.cls.name} {{...}}"
    seen = _seen | {id(inst)}
    body = ", ".join(f"{k}: {to_text(v, True, seen)}" for k, v in inst.fields.items())
    return f"{inst.cls.name} {{{body}}}"


def truthy(value) -> bool:
    return value is not None and value is not False


def tb_equal(a, b, _comparing: frozenset = frozenset()) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if is_number(a) and is_number(b):
        return a == b
    if (isinstance(b, Instance) and not isinstance(a, Instance)
            and isinstance(b.cls.find_method("equals")[0], NativeMethod)):
        a, b = b, a   # 0.3 == decimal: ask the (Python-backed) object
    if isinstance(a, Instance):
        if a is b:
            return True
        method, _owner = a.cls.find_method("equals")
        if isinstance(method, NativeMethod):
            return truthy(method.fn(None, None, a, b))
        if method is not None and a.cls.interp is not None:
            return truthy(a.cls.interp.call(Bound(method, a, a.cls), [b], None))
        return False
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
    if isinstance(a, Range):
        return a.py_range() == b.py_range() and a.inclusive == b.inclusive
    if isinstance(a, (EnumValue, TBClass, EnumType, TBIterator, Function, Builtin, Bound)):
        return a is b
    return a == b


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
    raise TBError(f'I can\'t turn "{value}" into a number.', line, cls=_cls("ValueError"))


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
    "One step at a time is still progress.",
    "Be gentle with yourself today.",
    "You are doing well. Keep going.",
    "Small steps still move you forward.",
    "You don't have to be perfect to be wonderful.",
    "Remember to eat something nice today.",
    "Step outside for a minute of fresh air.",
    "Well done for showing up today.",
    "Take a break, stretch, and look at the sky for a moment.",
]


# ---------------------------------------------------------------------------
# Format specs: f"{x:.2f}", format(value, spec), value.format(spec)
# ---------------------------------------------------------------------------

SPEC_RE = re.compile(
    r"^(?:(?P<fill>.)?(?P<align>[<>^=]))?(?P<sign>[+\- ])?(?P<alt>#)?(?P<zero>0)?"
    r"(?P<width>\d+)?(?P<group>[,_])?(?:\.(?P<prec>\d+))?(?P<type>[bcdeEfFgGosxX%])?$",
    re.DOTALL,
)
SPEC_HELP = (
    "Try .2f (2 decimals), 8 (width), >8 (right), <8 (left), ^8 (centre), "
    "08.3f (zero padded), , (thousands), x (hex), b (binary), .1% (percent) or .5 (shorten text)."
)


def format_value(value, spec: str, line=None) -> str:
    """Format a value with a Python-style spec subset; friendly errors."""
    if spec is None or spec == "":
        return to_text(value)
    m = SPEC_RE.match(spec)
    if not m:
        raise TBError(f"I don't understand the format '{spec}'. {SPEC_HELP}", line)
    kind = m.group("type")
    if is_number(value):
        value = tidy_number(value)
        if kind in ("b", "c", "d", "o", "x", "X"):
            if isinstance(value, float):
                raise TBError(
                    f"The format '{kind}' only works on whole numbers, but {to_text(value)} has a fraction. "
                    "Round it first, or use int().", line,
                )
        elif kind in ("e", "E", "f", "F", "g", "G", "%"):
            value = float(value)
        elif m.group("prec") is not None and isinstance(value, int):
            # {n:.2} on a whole number: show it with that many decimals
            value = float(value)
            spec = spec + "f"
        try:
            return format(value, spec)
        except (ValueError, OverflowError) as err:
            raise TBError(f"I can't use the format '{spec}' on {to_text(value)}: {err}.", line) from None
    text = value if isinstance(value, str) else to_text(value)
    if kind not in (None, "s"):
        raise TBError(
            f"The format '{kind}' needs a number, but this is {type_name(value)} ({to_text(value, nested=True)}).",
            line,
        )
    if m.group("sign") or m.group("alt") or m.group("group") or m.group("align") == "=":
        raise TBError(f"The format '{spec}' only makes sense for numbers, but this is {type_name(value)}.", line)
    if m.group("zero") and not m.group("width"):
        raise TBError(f"I don't understand the format '{spec}'. {SPEC_HELP}", line)
    try:
        return format(text, spec)
    except ValueError as err:
        raise TBError(f"I can't use the format '{spec}' on text: {err}.", line) from None


# ---------------------------------------------------------------------------
# Methods on built-in values: "hi".upper(), xs.push(1), m.keys(), 3.7.round()
# ---------------------------------------------------------------------------

# kind -> name -> (function(interp, line, value, *args), min args, max args, doc)
METHODS: dict[str, dict[str, tuple]] = {
    "text": {}, "list": {}, "map": {}, "number": {}, "bytes": {}, "range": {},
    "generator": {}, "function": {},
}

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
    "isdigit": "is_number", "push_back": "push", "keys": "keys",
    "decode": "to_text", "encode": "bytes", "collect": "to_list", "list": "to_list",
    "some": "any", "every": "all", "flatMap": "map", "apply": "call", "partial": "bind",
}


def method(kind: str, name: str, lo: int = 0, hi: int | None = None, doc: str = ""):
    def register(fn):
        METHODS[kind][name] = (fn, lo, lo if hi is None else hi, doc)
        return fn
    return register


def _cls(name: str):
    """A built-in error class by name (the table is filled in further down)."""
    return ERROR_CLASSES[name]


def _need_text(line, value, where):
    if not isinstance(value, str):
        raise TBError(f"{where} needs text, not {type_name(value)}.", line, cls=_cls("TypeError"))
    return value


def _need_whole(line, value, where):
    if not is_whole(value):
        raise TBError(f"{where} needs a whole number, not {to_text(value, nested=True)}.", line, cls=_cls("TypeError"))
    return int(value)


def _need_fn(line, value, where):
    if not is_callable(value):
        raise TBError(f"{where} needs a function, not {type_name(value)}.", line, cls=_cls("TypeError"))
    return value


def _need_bytes(line, value, where):
    if not isinstance(value, bytes):
        raise TBError(f"{where} needs bytes, not {type_name(value)}.", line, cls=_cls("TypeError"))
    return value


def _slice(line, seq, start, end, where):
    s = _need_whole(line, start, where)
    e = None if end is None else _need_whole(line, end, where)
    return seq[s:e]


def _compare_key(interp, line, where):
    """A sort key for instances with compare()."""
    def cmp(a, b):
        result = interp.compare_values(a, b, line, where)
        return result
    return functools.cmp_to_key(cmp)


def sort_values(values, line, where="sort()", interp=None):
    if all(is_number(v) for v in values) or all(isinstance(v, str) for v in values):
        return sorted(values)
    if values and all(isinstance(v, Instance) for v in values) and interp is not None:
        if all(v.cls.find_method("compare")[0] is not None for v in values):
            return sorted(values, key=_compare_key(interp, line, where))
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


@method("text", "bytes", 0, 1, doc="bytes(encoding?) -> bytes\nThe text as bytes (UTF-8 unless you name another encoding, like \"latin-1\" or \"utf-16\").")
def _t_bytes(it, line, s, encoding="utf-8"):
    encoding = _need_text(line, encoding, "bytes()")
    try:
        return s.encode(encoding)
    except LookupError:
        raise TBError(f"I don't know the text encoding '{encoding}'. Try \"utf-8\", \"latin-1\" or \"utf-16\".", line) from None
    except UnicodeEncodeError as err:
        raise TBError(f"This text can't be written as {encoding}: {err.reason}.", line) from None


@method("text", "format", 1, doc="format(spec) -> text\nThe text laid out with a format spec, like \"hi\".format(\">8\") (right-aligned in 8 spaces) or .3 (only the first 3 letters).")
def _t_format(it, line, s, spec):
    return format_value(s, _need_text(line, spec, "format()"), line)


@method("text", "count", 1, doc="count(part) -> number\nHow many times part appears in the text.")
def _t_count(it, line, s, part):
    part = _need_text(line, part, "count()")
    return s.count(part) if part else 0


@method("text", "pad_left", 1, 2, doc="pad_left(width, fill?) -> text\nThe text with fill (a space) added on the left until it is width long.")
def _t_pad_left(it, line, s, width, fill=" "):
    return s.rjust(_need_whole(line, width, "pad_left()"), _need_text(line, fill, "pad_left()")[:1] or " ")


@method("text", "pad_right", 1, 2, doc="pad_right(width, fill?) -> text\nThe text with fill (a space) added on the right until it is width long.")
def _t_pad_right(it, line, s, width, fill=" "):
    return s.ljust(_need_whole(line, width, "pad_right()"), _need_text(line, fill, "pad_right()")[:1] or " ")


@method("text", "reverse", doc="reverse() -> text\nThe letters in the opposite order.")
def _t_reverse(it, line, s):
    return s[::-1]


@method("text", "iter", doc="iter() -> generator\nA lazy sequence of the text's letters.")
def _t_iter(it, line, s):
    return TBIterator(iter(s))


# -- list -------------------------------------------------------------------

@method("list", "push", 1, MANY, doc="push(value, ...) -> list\nAdd value(s) to the end (changes the list) and give the list back.")
def _l_push(it, line, xs, *values):
    xs.extend(values)
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


@method("list", "remove", 1, doc="remove(value) -> bool\nTake out the first item equal to value (changes the list). True if one was found.")
def _l_remove(it, line, xs, value):
    for i, x in enumerate(xs):
        if tb_equal(x, value):
            del xs[i]
            return True
    return False


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


@method("list", "sort", doc="sort() -> list\nA new sorted list (all numbers, all text, or objects with compare()).")
def _l_sort(it, line, xs):
    return sort_values(xs, line, interp=it)


@method("list", "sort_by", 1, doc="sort_by(fn) -> list\nA new list sorted by what fn(item) gives back.")
def _l_sort_by(it, line, xs, fn):
    _need_fn(line, fn, "sort_by()")
    keys = [it.call(fn, [x], line) for x in xs]
    sort_values(keys, line, "sort_by()", interp=it)
    if keys and all(isinstance(k, Instance) for k in keys):
        order = sorted(range(len(xs)), key=lambda i: _compare_key(it, line, "sort_by()")(keys[i]))
    else:
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


@method("list", "any", 1, doc="any(fn) -> bool\nTrue if fn(item) is true for at least one item.")
def _l_any(it, line, xs, fn):
    _need_fn(line, fn, "any()")
    return any(truthy(it.call(fn, [x], line)) for x in list(xs))


@method("list", "all", 1, doc="all(fn) -> bool\nTrue if fn(item) is true for every item.")
def _l_all(it, line, xs, fn):
    _need_fn(line, fn, "all()")
    return all(truthy(it.call(fn, [x], line)) for x in list(xs))


@method("list", "find", 1, doc="find(fn) -> value\nThe first item where fn(item) is true, or nil.")
def _l_find(it, line, xs, fn):
    _need_fn(line, fn, "find()")
    for x in list(xs):
        if truthy(it.call(fn, [x], line)):
            return x
    return None


@method("list", "enumerate", doc="enumerate() -> list\nA list of [position, item] pairs: for i, x in xs.enumerate() { }.")
def _l_enumerate(it, line, xs):
    return [[i, x] for i, x in enumerate(xs)]


@method("list", "zip", 1, doc="zip(other) -> list\nA list of [item, other_item] pairs, as long as the shorter list.")
def _l_zip(it, line, xs, other):
    if not isinstance(other, list):
        raise TBError(f"zip() needs a list, not {type_name(other)}.", line)
    return [[a, b] for a, b in zip(xs, other)]


@method("list", "flat", doc="flat() -> list\nA new list with one level of inner lists spread out: [[1, 2], [3]] gives [1, 2, 3].")
def _l_flat(it, line, xs):
    out: list = []
    for x in xs:
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return out


@method("list", "iter", doc="iter() -> generator\nA lazy sequence over the items (see generators).")
def _l_iter(it, line, xs):
    return TBIterator(iter(xs))


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


@method("map", "each", 1, doc="each(fn) -> nil\nCall fn(key, value) for every pair.")
def _m_each(it, line, m, fn):
    _need_fn(line, fn, "each()")
    for k, v in list(m.items()):
        it.call(fn, [k, v], line)
    return None


@method("map", "map_values", 1, doc="map_values(fn) -> map\nA new map with the same keys and fn(value) for each value.")
def _m_map_values(it, line, m, fn):
    _need_fn(line, fn, "map_values()")
    return {k: it.call(fn, [v], line) for k, v in list(m.items())}


@method("map", "filter", 1, doc="filter(fn) -> map\nA new map with the pairs where fn(key, value) is true.")
def _m_filter(it, line, m, fn):
    _need_fn(line, fn, "filter()")
    return {k: v for k, v in list(m.items()) if truthy(it.call(fn, [k, v], line))}


@method("map", "iter", doc="iter() -> generator\nA lazy sequence of [key, value] pairs.")
def _m_iter(it, line, m):
    return TBIterator(iter([[k, v] for k, v in m.items()]))


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


@method("number", "is_int", doc="is_int() -> bool\nTrue if this is a whole number value (an int), like 3 or 6 // 2.")
def _n_is_int(it, line, x):
    return isinstance(x, int)


@method("number", "is_float", doc="is_float() -> bool\nTrue if this number has a fractional type (a float), like 2.5 or 6 / 3.")
def _n_is_float(it, line, x):
    return isinstance(x, float)


@method("number", "to_int", doc="to_int() -> number\nThe whole-number part as an int (3.9 gives 3, -3.9 gives -3).")
def _n_to_int(it, line, x):
    if isinstance(x, float) and not math.isfinite(x):
        raise TBError(TOO_BIG, line)
    return check_size(int(x), line)


@method("number", "to_float", doc="to_float() -> number\nThe number as a float (3 gives 3.0; it still shows as 3).")
def _n_to_float(it, line, x):
    return float(x)


@method("number", "format", 1, doc="format(spec) -> text\nThe number as text with a format spec: 3.14159.format(\".2f\") is \"3.14\", 1234567.format(\",\") is \"1,234,567\", 255.format(\"x\") is \"ff\".")
def _n_format(it, line, x, spec):
    return format_value(x, _need_text(line, spec, "format()"), line)


# -- bytes ------------------------------------------------------------------

@method("bytes", "len", doc="len() -> number\nHow many bytes there are.")
def _b_len(it, line, b):
    return len(b)


@method("bytes", "hex", doc="hex() -> text\nThe bytes as hex text, two characters per byte (\"4142\").")
def _b_hex(it, line, b):
    return b.hex()


@method("bytes", "slice", 1, 2, doc="slice(start, end?) -> bytes\nThe bytes from start up to (not including) end. Negative counts from the end.")
def _b_slice(it, line, b, start, end=None):
    return _slice(line, b, start, end, "slice()")


@method("bytes", "to_text", 0, 1, doc="to_text(encoding?) -> text\nDecode the bytes as text (UTF-8 unless you say otherwise). Bytes that aren't valid text become the replacement character.")
def _b_to_text(it, line, b, encoding="utf-8"):
    encoding = _need_text(line, encoding, "to_text()")
    try:
        return b.decode(encoding, errors="replace")
    except LookupError:
        raise TBError(f"I don't know the text encoding '{encoding}'. Try \"utf-8\", \"latin-1\" or \"utf-16\".", line) from None


@method("bytes", "at", 1, doc="at(i) -> number\nThe byte at position i (0 to 255). Negative counts from the end.")
def _b_at(it, line, b, index):
    return b[it.check_index(b, index, line)]


@method("bytes", "starts_with", 1, doc="starts_with(prefix) -> bool\nTrue if the bytes begin with prefix (bytes).")
def _b_starts(it, line, b, prefix):
    return b.startswith(_need_bytes(line, prefix, "starts_with()"))


@method("bytes", "ends_with", 1, doc="ends_with(suffix) -> bool\nTrue if the bytes end with suffix (bytes).")
def _b_ends(it, line, b, suffix):
    return b.endswith(_need_bytes(line, suffix, "ends_with()"))


@method("bytes", "contains", 1, doc="contains(part) -> bool\nTrue if part (bytes, or a byte value 0-255) appears inside.")
def _b_contains(it, line, b, part):
    if is_whole(part):
        return int(part) in b
    return _need_bytes(line, part, "contains()") in b


@method("bytes", "find", 1, doc="find(part) -> number\nWhere part (bytes) first appears, or -1.")
def _b_find(it, line, b, part):
    return b.find(_need_bytes(line, part, "find()"))


@method("bytes", "split", 1, doc="split(sep) -> list\nCut the bytes at every sep (bytes); a list of bytes.")
def _b_split(it, line, b, sep):
    sep = _need_bytes(line, sep, "split()")
    if not sep:
        return [bytes([x]) for x in b]
    return b.split(sep)


@method("bytes", "join", 1, doc="join(list) -> bytes\nJoin a list of bytes with these bytes between them.")
def _b_join(it, line, b, items):
    if not isinstance(items, list) or not all(isinstance(x, bytes) for x in items):
        raise TBError("join() needs a list of bytes.", line)
    return b.join(items)


@method("bytes", "reverse", doc="reverse() -> bytes\nThe bytes in the opposite order.")
def _b_reverse(it, line, b):
    return b[::-1]


@method("bytes", "to_list", doc="to_list() -> list\nA list of the byte values (0 to 255).")
def _b_to_list(it, line, b):
    return list(b)


@method("bytes", "copy", doc="copy() -> bytes\nThe same bytes (bytes never change, so this is the value itself).")
def _b_copy(it, line, b):
    return b


@method("bytes", "count", 1, doc="count(part) -> number\nHow many times part (bytes or a byte value) appears.")
def _b_count(it, line, b, part):
    if is_whole(part):
        return b.count(int(part))
    part = _need_bytes(line, part, "count()")
    return b.count(part) if part else 0


@method("bytes", "iter", doc="iter() -> generator\nA lazy sequence of the byte values.")
def _b_iter(it, line, b):
    return TBIterator(iter(b))


# -- range ------------------------------------------------------------------

@method("range", "step", 1, doc="step(n) -> range\nThe same range counting in steps of n: (0..10).step(2) is 0, 2, 4, 6, 8.")
def _r_step(it, line, r, n):
    n = _need_whole(line, n, "step()")
    if n == 0:
        raise TBError("A range can't count in steps of 0.", line)
    return Range(r.start, r.stop, r.inclusive, n)


@method("range", "to_list", doc="to_list() -> list\nThe numbers of the range as a list.")
def _r_to_list(it, line, r):
    it.check_range_size(r, line)
    return list(r)


@method("range", "len", doc="len() -> number\nHow many numbers the range has.")
def _r_len(it, line, r):
    return len(r)


@method("range", "contains", 1, doc="contains(x) -> bool\nTrue if x is one of the range's numbers.")
def _r_contains(it, line, r, x):
    return x in r


@method("range", "iter", doc="iter() -> generator\nA lazy sequence over the range.")
def _r_iter(it, line, r):
    return TBIterator(iter(r))


@method("range", "map", 1, doc="map(fn) -> list\nA list of fn(n) for every number in the range.")
def _r_map(it, line, r, fn):
    _need_fn(line, fn, "map()")
    it.check_range_size(r, line)
    return [it.call(fn, [n], line) for n in r]


@method("range", "filter", 1, doc="filter(fn) -> list\nThe numbers where fn(n) is true.")
def _r_filter(it, line, r, fn):
    _need_fn(line, fn, "filter()")
    it.check_range_size(r, line)
    return [n for n in r if truthy(it.call(fn, [n], line))]


@method("range", "each", 1, doc="each(fn) -> nil\nCall fn(n) for every number.")
def _r_each(it, line, r, fn):
    _need_fn(line, fn, "each()")
    for n in r:
        it.call(fn, [n], line)
    return None


@method("range", "sum", doc="sum() -> number\nAll the numbers added up.")
def _r_sum(it, line, r):
    return sum(r.py_range())


@method("range", "first", doc="first() -> value\nThe first number, or nil if the range is empty.")
def _r_first(it, line, r):
    rng = r.py_range()
    return rng[0] if len(rng) else None


@method("range", "last", doc="last() -> value\nThe last number, or nil if the range is empty.")
def _r_last(it, line, r):
    rng = r.py_range()
    return rng[-1] if len(rng) else None


@method("range", "reverse", doc="reverse() -> list\nThe numbers as a list, last first.")
def _r_reverse(it, line, r):
    it.check_range_size(r, line)
    return list(r)[::-1]


# -- generator --------------------------------------------------------------

@method("generator", "next", doc="next() -> value\nThe next value, or `done` when there are no more.")
def _g_next(it, line, g):
    try:
        return next(g)
    except StopIteration:
        return DONE


@method("generator", "to_list", doc="to_list() -> list\nAll the remaining values, as a list.")
def _g_to_list(it, line, g):
    return list(g)


@method("generator", "map", 1, doc="map(fn) -> generator\nA lazy sequence of fn(value) for each value.")
def _g_map(it, line, g, fn):
    _need_fn(line, fn, "map()")
    return TBIterator(it.call(fn, [x], line) for x in g)


@method("generator", "filter", 1, doc="filter(fn) -> generator\nA lazy sequence of the values where fn(value) is true.")
def _g_filter(it, line, g, fn):
    _need_fn(line, fn, "filter()")
    return TBIterator(x for x in g if truthy(it.call(fn, [x], line)))


@method("generator", "take", 1, doc="take(n) -> generator\nOnly the first n values.")
def _g_take(it, line, g, n):
    n = _need_whole(line, n, "take()")

    def taking():
        count = 0
        for x in g:
            if count >= n:
                return
            count += 1
            yield x
    return TBIterator(taking() if n > 0 else iter(()))


@method("generator", "skip", 1, doc="skip(n) -> generator\nEverything but the first n values.")
def _g_skip(it, line, g, n):
    n = _need_whole(line, n, "skip()")

    def skipping():
        for i, x in enumerate(g):
            if i >= n:
                yield x
    return TBIterator(skipping())


@method("generator", "zip", 1, doc="zip(other) -> generator\nPairs [value, other_value] until either side runs out.")
def _g_zip(it, line, g, other):
    other_it = it.iterate(other, line, "zip()")
    return TBIterator([a, b] for a, b in zip(g, other_it))


@method("generator", "enumerate", doc="enumerate() -> generator\nPairs [position, value]: for i, x in gen.enumerate() { }.")
def _g_enumerate(it, line, g):
    return TBIterator([i, x] for i, x in enumerate(g))


@method("generator", "each", 1, doc="each(fn) -> nil\nCall fn(value) for every value.")
def _g_each(it, line, g, fn):
    _need_fn(line, fn, "each()")
    for x in g:
        it.call(fn, [x], line)
    return None


@method("generator", "first", doc="first() -> value\nThe next value, or nil if there is none (like next() but nil instead of done).")
def _g_first(it, line, g):
    try:
        return next(g)
    except StopIteration:
        return None


@method("generator", "find", 1, doc="find(fn) -> value\nThe first value where fn(value) is true, or nil.")
def _g_find(it, line, g, fn):
    _need_fn(line, fn, "find()")
    for x in g:
        if truthy(it.call(fn, [x], line)):
            return x
    return None


@method("generator", "sum", doc="sum() -> number\nAll the remaining numbers added up.")
def _g_sum(it, line, g):
    return _sum(list(g), line)


@method("generator", "count", doc="count() -> number\nHow many values are left (uses them up).")
def _g_count(it, line, g):
    return sum(1 for _ in g)


@method("generator", "join", 0, 1, doc="join(sep?) -> text\nAll remaining values as one text, with sep between them.")
def _g_join(it, line, g, sep=""):
    return _need_text(line, sep, "join()").join(to_text(x) for x in g)


# -- function ---------------------------------------------------------------

@method("function", "call", 0, 1, doc="call(args?) -> value\nCall the function with a list of inputs: f.call([1, 2]) is f(1, 2).")
def _f_call(it, line, fn, args=None):
    if args is None:
        args = []
    if not isinstance(args, list):
        raise TBError(f"call() needs a list of inputs, not {type_name(args)}.", line)
    return it.call(fn, list(args), line)


@method("function", "bind", 0, MANY, doc="bind(...) -> function\nA new function with the first inputs filled in: add.bind(1)(2) is add(1, 2).")
def _f_bind(it, line, fn, *args):
    if isinstance(fn, Partial):
        return Partial(fn.fn, fn.args + list(args), fn.kwargs)
    return Partial(fn, args)


METHOD_KIND_LABEL = {
    "text": "Text", "list": "A list", "map": "A map", "number": "A number", "bytes": "Bytes",
    "range": "A range", "generator": "A generator", "function": "A function",
}
METHOD_KIND_PLURAL = {
    "text": "Text", "list": "List", "map": "Map", "number": "Number", "bytes": "Bytes",
    "range": "Range", "generator": "Generator", "function": "Function",
}


def method_kind(value) -> str | None:
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    if is_number(value):
        return "number"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, Range):
        return "range"
    if isinstance(value, TBIterator):
        return "generator"
    if isinstance(value, (Function, Builtin, Bound, Partial)):
        return "function"
    return None


# ---------------------------------------------------------------------------
# The Error class family (shared by every interpreter)
# ---------------------------------------------------------------------------

ERROR_FIELDS = ("message", "line", "file", "kind", "stack")


def _error_init(interp, line, self_value, message="", extra=None):
    """Error(message, extra_fields?)"""
    fields = self_value.fields
    fields["message"] = message if isinstance(message, str) else to_text(message)
    fields.setdefault("line", None)
    fields.setdefault("file", None)
    fields["kind"] = self_value.cls.kind
    fields.setdefault("stack", [])
    if extra is not None:
        if not isinstance(extra, dict):
            raise TBError(f"Error() takes the message and then a map of extra fields, not {type_name(extra)}.", line)
        for key, value in extra.items():
            if isinstance(key, str) and key not in ("message", "kind", "stack"):
                fields[key] = value
    return None


def make_error_class(name: str, base: TBClass | None, doc: str) -> TBClass:
    cls = TBClass(name, base)
    cls.is_error = True
    cls.doc = doc
    if base is None:
        cls.methods["init"] = NativeMethod("init", _error_init, (0, 2), "init(message, extra?)\nMake an error with a message and optional extra fields (a map).")
    return cls


ERROR_CLASS = make_error_class(
    "Error", None,
    "The base class of every error.\n"
    "\n"
    "Fields:\n"
    "- message, line, file, kind and stack\n"
    "- any extra fields you gave it\n"
    "\n"
    "Throw one with throw Error(\"msg\") or throw Error(\"msg\", {\"code\": 7}). "
    "Make your own with class MyError extends Error { }.",
)
ERROR_CLASSES: dict[str, TBClass] = {"Error": ERROR_CLASS}
for _name, _doc in (
    ("TypeError", "A value of the wrong type was used (text where a number was needed, ...)."),
    ("ValueError", "A value of the right type but a bad value (an empty name, a number out of range, ...)."),
    ("FileError", "A problem reading or writing a file or folder."),
    ("ParseError", "Text that couldn't be understood (bad JSON, CSV, YAML, a bad regex, ...)."),
    ("NetworkError", "A network problem (no connection, DNS failed, connection refused, ...)."),
    ("PermissionError", "Not allowed to do that (a protected file, a privileged port, ...)."),
    ("NotFoundError", "Something that doesn't exist (a missing file, key or record)."),
    ("TimeoutError", "Something took too long."),
    ("StopIteration", "A generator or iterator has nothing more to give."),
    ("AssertionError", "An `assert` check failed."),
):
    _base = ERROR_CLASSES["FileError"] if _name in ("NotFoundError", "PermissionError") else ERROR_CLASS
    ERROR_CLASSES[_name] = make_error_class(_name, _base, _doc)
# Keep NotFoundError/PermissionError catchable as FileError (their most common source), but
# they are also sensible for keys/network: `err is FileError` is true for them.
del _name, _doc, _base


def error_class(cls) -> TBClass:
    """Accept a TBClass or a class name; give back the TBClass (Error if None)."""
    if cls is None:
        return ERROR_CLASS
    if isinstance(cls, TBClass):
        return cls
    if isinstance(cls, str) and cls in ERROR_CLASSES:
        return ERROR_CLASSES[cls]
    raise ValueError(f"unknown error class {cls!r}")


def error_value(err: TBError) -> Instance:
    """The TB Error instance a `catch` binds for this TBError (made once)."""
    if err.value is not None:
        inst = err.value
        fields = inst.fields
        if fields.get("line") is None:
            fields["line"] = err.line
        if fields.get("file") is None and isinstance(err.file, str):
            fields["file"] = os.path.basename(err.file)
        if not fields.get("stack") and err.stack:
            fields["stack"] = [dict(f) for f in err.stack]
        return inst
    cls = error_class(err.cls)
    fields = {
        "message": err.message,
        "line": err.line,
        "file": os.path.basename(err.file) if isinstance(err.file, str) else None,
        "kind": err.kind,
        "stack": [dict(f) for f in (err.stack or [])],
    }
    for key, value in err.fields.items():
        if key == "kind":
            fields["kind"] = value
        elif key not in ("message", "line", "file", "stack"):
            fields[key] = value
    inst = Instance(cls, fields)
    err.value = inst
    return inst


def error_from_value(inst: Instance, line) -> TBError:
    """A TBError for `throw inst` where inst is an Error instance."""
    fields = inst.fields
    where = fields.get("line")
    err = TBError(
        to_text(fields.get("message", "")),
        int(where) if is_whole(where) else line,
        {k: v for k, v in fields.items() if k not in ERROR_FIELDS},
        inst.cls,
    )
    if fields.get("kind") not in (None, inst.cls.kind):
        err.fields["kind"] = fields["kind"]
    err.value = inst
    if fields.get("line") is None:
        fields["line"] = err.line
    return err


def arity_message(name, lo, hi, got) -> str:
    if lo == hi:
        want = f"{lo} input" + ("" if lo == 1 else "s")
    elif hi >= MANY:
        want = f"at least {lo} input" + ("" if lo == 1 else "s")
    else:
        want = f"{lo} to {hi} inputs"
    return f"{name}() needs {want}, but got {got}."


def pow_guard(a, b, line):
    """Refuse a ** b when the result would be absurdly big."""
    if isinstance(a, int) and isinstance(b, int) and b > 0 and abs(a) > 1:
        bits = (abs(a).bit_length()) * b
        if bits > MAX_POW_BITS:
            raise TBError(TOO_BIG, line)


