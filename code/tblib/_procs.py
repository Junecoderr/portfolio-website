"""Worker processes for parallel.processes and shared queues (TB 3.2).

Threads don't speed up CPU-bound TB code (Python runs one thread at a time),
so parallel.processes runs the function in other processes, one per core.

How a function crosses: `Packer` turns the TB function into its syntax tree
plus the values of the names it uses (plain data, standard libraries, other
functions, shared queues). The child builds a fresh interpreter of the same
engine and remakes the function from that, so it works for both engines and
needs no copy of the program. Items and results must be plain values (nil,
true/false, numbers, text, bytes, lists, maps); anything else is refused with
a clear error before any process starts (or when a result comes back).

Processes use the "spawn" start method on every system: fork is unsafe once
threads exist and doesn't exist on Windows. Frozen builds (PyInstaller) call
multiprocessing.freeze_support() first thing (tb.py, packaging/tblaunch.py),
so a spawned copy of tb.exe becomes a worker instead of running a program.
"""

from __future__ import annotations

import sys
import threading

import tb as _tb
from tbcore import ast as A
from tbcore import tasks as _tasks
from tbcore.compiler import UNSET
from tblib._api import fail, fail_timeout, fail_type, make_object, method, native_class

PLAIN = (type(None), bool, int, float, str, bytes)
_LIMIT = 60000 if sys.version_info >= (3, 11) else 20000


class CantCross(Exception):
    """A value that can't be sent to (or back from) another process."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


def _describe(value) -> str:
    name = _tb.type_name(value)
    if isinstance(value, _tb.Instance):
        return f"a {name} object"
    if isinstance(value, _tb.Function):
        return "a function" if value.owner is None else "a method"
    return {"function": "a built-in function", "library": "a library file"}.get(name, f"a {name}")


def check_plain(value, seen=None):
    """value, if it is plain data all the way down; else CantCross."""
    if isinstance(value, PLAIN):
        return value
    if isinstance(value, (list, dict)):
        seen = seen if seen is not None else set()
        if id(value) in seen:
            raise CantCross("a list or map that contains itself")
        seen.add(id(value))
        if isinstance(value, list):
            for item in value:
                check_plain(item, seen)
        else:
            for key, item in value.items():
                check_plain(key, seen)
                check_plain(item, seen)
        seen.discard(id(value))
        return value
    raise CantCross(_describe(value))


# ---------------------------------------------------------------------------
# Packing a function (parent side)
# ---------------------------------------------------------------------------


def _names(node, out: set) -> None:
    """Every name a piece of syntax tree mentions."""
    if isinstance(node, (list, tuple)):
        for item in node:
            _names(item, out)
    elif isinstance(node, A.Name):
        out.add(node.name)
    elif type(node).__module__ == A.__name__:
        fields = getattr(node, "__dict__", None)
        values = fields.values() if fields is not None else [getattr(node, s, None) for s in type(node).__slots__]
        for value in values:
            if isinstance(value, (list, tuple)) or type(value).__module__ == A.__name__:
                _names(value, out)


def _lookup(fn, name):
    """(found, value) for a name fn can see, outside its own body. Built-ins
    are left out: every interpreter has them."""
    tmpl = getattr(fn, "tmpl", None)
    if tmpl is not None and name in tmpl.outer_map:
        cell = fn.cells[tmpl.outer_map[name][0]]
        value = getattr(cell, "v", cell)
        return value is not UNSET, value
    env = fn.closure.find(name) if fn.closure is not None else None
    if env is None or env.parent is None:
        return False, None
    return True, env.vars[name]


class Packer:
    def __init__(self):
        self.table: list = []
        self.index: dict = {}

    def value(self, value, name: str, owner: str):
        if isinstance(value, PLAIN):
            return value
        if isinstance(value, list):
            return [self.value(v, name, owner) for v in value]
        if isinstance(value, dict):
            return {check_plain(k): self.value(v, name, owner) for k, v in value.items()}
        if isinstance(value, _tb.Function) and value.owner is None and value.bound_args is None:
            return ("fn", self.function(value))
        if isinstance(value, _tb.Module) and value.std:
            return ("lib", value.name)
        if isinstance(value, _tb.Instance) and value.cls is SHARED_QUEUE:
            return ("queue", value.fields["_q"], value.fields["_closed"])
        raise CantCross(f"{_describe(value)} (the name '{name}' in {owner})")

    def function(self, fn) -> int:
        key = id(fn)
        if key in self.index:
            return self.index[key]
        at = self.index[key] = len(self.table)
        self.table.append(None)
        names: set = set()
        _names(fn.body, names)
        for param in fn.params:
            _names(param.default, names)
        names -= {p.target for p in fn.params if isinstance(p.target, str)}
        owner = "fn" if fn.name == "anonymous" else f"'{fn.name}'"
        free = {}
        for name in sorted(names):
            found, value = _lookup(fn, name)
            if found and not isinstance(value, _tb.Builtin):
                free[name] = self.value(value, name, owner)
        line = getattr(getattr(fn, "tmpl", None), "line", None) or (fn.body[0].line if fn.body else 0)
        self.table[at] = (fn.name, fn.params, fn.body, line, fn.file, fn.is_generator, fn.is_async, free)
        return at


# ---------------------------------------------------------------------------
# The worker (child side)
# ---------------------------------------------------------------------------

_STATE: dict = {}


def _child_init(engine, base_dir, script_path, argv, table, root) -> None:
    """Pool initializer: never raises (a failing initializer makes Pool
    restart workers forever), problems are reported by each call instead."""
    try:
        sys.setrecursionlimit(max(sys.getrecursionlimit(), _LIMIT))

        def output(text):
            print(text, flush=True)

        interp = _tb.ENGINES.get(engine, _tb.VMInterpreter)(output=output)
        interp.base_dir, interp.script_path, interp.argv = base_dir, script_path, list(argv)
        made, envs = [], []
        for name, params, body, line, file, is_gen, is_async, free in table:
            env = _tb.Env(interp.builtins)
            fn = interp.evaluate(A.FnExpr(params, body, line, is_gen, None, False, is_async), env)
            fn.name, fn.file = name, file
            made.append(fn)
            envs.append((env, free))

        def rebuild(value):
            if isinstance(value, list):
                return [rebuild(v) for v in value]
            if isinstance(value, dict):
                return {k: rebuild(v) for k, v in value.items()}
            if isinstance(value, tuple):
                if value[0] == "fn":
                    return made[value[1]]
                if value[0] == "lib":
                    return interp.load_library(value[1])
                return make_shared_queue(value[1], value[2])
            return value

        for env, free in envs:
            for key, value in free.items():
                env.vars[key] = rebuild(value)
        _STATE.update(interp=interp, fn=made[root], error=None)
    except BaseException as err:  # reported by _child_call
        _STATE.update(error=getattr(err, "message", None) or f"{type(err).__name__}: {err}")


def _child_call(job):
    at, item = job
    if _STATE.get("error"):
        return ("err", at, f"A worker process couldn't start: {_STATE['error']}", None, "Error")
    try:
        result = _STATE["interp"].call(_STATE["fn"], [item], None)
        return ("ok", at, check_plain(result))
    except _tb.TBError as err:
        return ("err", at, err.message, err.line, err.cls.name if err.cls is not None else "Error")
    except CantCross as err:
        return ("err", at, f"fn gave back {err.what}, which can't come back from another process. "
                           "Give back plain values: nil, true/false, numbers, text, bytes, lists and maps.",
                None, "TypeError")
    except _tb.TBExit:
        return ("err", at, "fn called exit() inside a worker process.", None, "Error")
    except RecursionError:
        return ("err", at, "fn went too deep (too many functions inside functions).", None, "Error")
    except BaseException as err:  # never a Python traceback
        return ("err", at, f"Something went wrong in a worker process: {err}", None, "Error")


# ---------------------------------------------------------------------------
# parallel.processes (parent side)
# ---------------------------------------------------------------------------

PLAIN_HINT = "Use plain values: nil, true/false, numbers, text, bytes, lists and maps."


def run(interp, fn, items, workers: int, line):
    """fn(item) for every item on `workers` processes; results in order."""
    packer = Packer()
    try:
        root = packer.function(fn)
    except CantCross as err:
        fail_type(line, f"parallel.processes() can't send {err.what} to another process. {PLAIN_HINT}")
    for i, item in enumerate(items):
        try:
            check_plain(item)
        except CantCross as err:
            fail_type(line, f"parallel.processes(): item {i + 1} is {err.what}, which can't cross to another "
                            f"process. {PLAIN_HINT}")
    if not items:
        return []
    import multiprocessing

    workers = max(1, min(workers, len(items)))
    args = (getattr(interp, "engine", "tree"), interp.base_dir, interp.script_path, list(interp.argv),
            packer.table, root)
    try:
        pool = multiprocessing.get_context("spawn").Pool(workers, _child_init, args)
    except (OSError, ImportError, ValueError) as err:
        fail(line, f"parallel.processes() couldn't start worker processes: {err}")
    try:
        finished = threading.Event()
        async_result = pool.map_async(_child_call, list(enumerate(items)),
                                      chunksize=max(1, len(items) // (workers * 4)),
                                      callback=lambda _: finished.set(), error_callback=lambda _: finished.set())
        _tasks.wait_event(finished)
        try:
            # The pool calls back just before it marks the result ready, so wait for that too.
            rows = async_result.get(timeout=30)
        except Exception as err:  # e.g. a value pickle refused
            fail(line, f"parallel.processes(): a worker process failed: {err}")
    finally:
        pool.terminate()
    results = [None] * len(items)
    for row in rows:
        if row[0] == "err":
            _, at, message, err_line, cls = row
            raise _tb.TBError(message, err_line or line, {"item": at + 1},
                              cls=_tb.ERROR_CLASSES.get(cls) or _tb.ERROR_CLASSES["Error"])
        results[row[1]] = row[2]
    return results


# ---------------------------------------------------------------------------
# Shared queues: the same methods as parallel.Queue, across processes
# ---------------------------------------------------------------------------

SHARED_QUEUE = native_class("SharedQueue", "A queue that tasks in other processes (parallel.processes) "
                                           "can use too. Only plain values can go in.")
_MANAGER = []
_lock = threading.Lock()


def _manager():
    with _lock:
        if not _MANAGER:
            import multiprocessing

            _MANAGER.append(multiprocessing.get_context("spawn").Manager())
        return _MANAGER[0]


def new_shared_queue(line, size):
    try:
        manager = _manager()
        return make_shared_queue(manager.Queue(size or 0), manager.Event())
    except (OSError, EOFError) as err:
        fail(line, f"parallel.shared_queue() couldn't start its helper process: {err}")


def make_shared_queue(proxy, closed):
    return make_object(SHARED_QUEUE, {"_q": proxy, "_closed": closed})


def _poll(line, what, timeout, attempt):
    """Call attempt() every 50 ms until it gives something (not None),
    staying cancellable and letting loop tasks run."""
    import time

    deadline = None if timeout is None else time.monotonic() + timeout
    with _tasks.blocking():
        while True:
            _tasks.check_cancelled()
            got = attempt()
            if got is not None:
                return got
            if deadline is not None and time.monotonic() >= deadline:
                fail_timeout(line, f"queue.{what}() waited {_tasks._seconds(timeout)} and gave up.")


def _timeout(line, timeout, where):
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        fail_type(line, f"{where}() needs timeout to be a number of seconds (0 or more).")
    return float(timeout)


@method(SHARED_QUEUE, "put", 1, 2, doc="put(value, timeout?)\nAdd value at the end (plain values only). If "
                                       "the queue has a size and is full, wait for room (up to timeout seconds).")
def _sq_put(line, me, value, timeout=None):
    import queue as _queue

    try:
        check_plain(value)
    except CantCross as err:
        fail_type(line, f"queue.put(): {err.what} can't go into a shared queue. {PLAIN_HINT}")
    timeout = _timeout(line, timeout, "queue.put")
    q, closed = me.fields["_q"], me.fields["_closed"]

    def attempt():
        if closed.is_set():
            fail(line, "This queue is closed, so nothing more can be put in it.")
        try:
            q.put(value, timeout=0.05)
            return True
        except _queue.Full:
            return None

    _poll(line, "put", timeout, attempt)
    return None


@method(SHARED_QUEUE, "get", 0, 1, doc="get(timeout?) -> value\nTake the value at the front, waiting for one "
                                       "(up to timeout seconds, then a TimeoutError). Gives done once the queue "
                                       "is closed and empty.")
def _sq_get(line, me, timeout=None):
    import queue as _queue

    timeout = _timeout(line, timeout, "queue.get")
    q, closed = me.fields["_q"], me.fields["_closed"]

    def attempt():
        was_closed = closed.is_set()
        try:
            return (q.get(timeout=0.05),)
        except _queue.Empty:
            return (_tb.DONE,) if was_closed else None

    return _poll(line, "get", timeout, attempt)[0]


@method(SHARED_QUEUE, "next", 0, doc="next() -> value\nLike get(): lets `for item in queue { }` take values "
                                     "until the queue is closed and empty.")
def _sq_next(line, me):
    return _sq_get(line, me)


@method(SHARED_QUEUE, "size", 0, doc="size() -> number\nHow many values are waiting.")
def _sq_size(line, me):
    return me.fields["_q"].qsize()


@method(SHARED_QUEUE, "close", 0, doc="close()\nNo more values will be put in. Waiting get()s and for loops "
                                      "finish once the queue is empty.")
def _sq_close(line, me):
    me.fields["_closed"].set()
    return None


@method(SHARED_QUEUE, "is_closed", 0, doc="is_closed() -> bool\nTrue after close().")
def _sq_is_closed(line, me):
    return me.fields["_closed"].is_set()


@method(SHARED_QUEUE, "text", 0, doc="text() -> text\nHow the queue shows in say().")
def _sq_text(line, me):
    closed = me.fields["_closed"].is_set()
    return f"<shared queue {me.fields['_q'].qsize()} waiting{', closed' if closed else ''}>"
