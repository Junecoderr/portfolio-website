"""Tasks: TB code running on its own thread (TB 3.0 concurrency).

Used by `async fn` (interp.Interpreter.call_function) and by the `parallel`
library (tblib/parallel.py). The bytecode VM mirrors the same contract.

The model
---------
* A task runs on a Python thread with its own *interpreter view*
  (`Interpreter.fork()`): its own call depth, call stack, current frame,
  current file, active generator and trace hook. Globals, builtins, the
  module cache, output, input and the native-library `api` are shared.
  Dict and list operations on shared globals are atomic under the GIL;
  anything bigger needs a `parallel.Lock`.
* The thread registers its view in `interp._local.interp`, so native
  libraries calling back into TB (`api.call`) run on the right view.
* `wait()` gives back the task's result, or re-raises the very TBError that
  stopped it (same class, message, line, file and stack). `exit()` inside a
  task re-raises TBExit in the waiter.
* `cancel()` is cooperative: it sets a flag and installs a one-shot trace
  hook on the task's view(s); the task stops before its next statement by
  raising TaskCancelled (a BaseException, so TB `catch` can't swallow it, but
  `finally` and `defer` still run). `parallel.sleep`, `Queue.get` and
  `wait()` inside the task wake up early when it is cancelled. Waiting on a
  cancelled task raises "The task was cancelled." (err.cancelled is true).
* In the browser (Pyodide: no threads) a task runs to completion at once,
  so `async fn` and `await` still work, just not in parallel.

The event loop (parallel.loop, TB 3.2)
--------------------------------------
* `run_loop` makes the calling thread a loop thread: an asyncio event loop
  there owns the sockets of servers started inside it (one thread for any
  number of connections), and TB code inside the loop runs one piece at a
  time. Tasks started inside it (`async fn`, parallel.run, map workers) are
  coroutines: they take turns holding the loop's baton and hand it on
  whenever they wait (await, sleep, queues, locks, socket reads, http
  requests: every `wait_event` / `blocking()`), so loop code needs no locks.
  Server handlers run on the loop thread while holding the baton.
* asyncio rather than raw `selectors`: it gives the same non-blocking sockets
  plus IOCP on Windows (no 512-socket select() cap) and thread-safe wakeups.
  TB code is called from its protocol callbacks, never as Python coroutines.
* Python has no stack-switching coroutines in its standard library, so a
  coroutine that is waiting parks its own OS thread; connections, server
  handlers and the loop itself cost no extra threads.
* The loop ends when its main function and every task started inside it
  have finished; servers started with start() inside it stop then.
"""

from __future__ import annotations

import collections
import contextlib
import sys
import threading
import time

from .errors import TBError, TBExit
from .runtime import ERROR_CLASSES, Instance, NativeMethod, TBClass

THREADS_OK = sys.platform != "emscripten"

# Set by tblib/gui.py while windows exist: poster(fn, args, line) -> bool runs
# the TB callback fn(*args) on the window thread and returns True, or returns
# False when there is no window loop (the callback then runs on the task's
# own thread). Used by Task.on_done.
MAIN_POSTER = None

_local = threading.local()      # .task: the Task this thread works for
_STACK_SET = False
_PYTHON_RECURSION_LIMIT = 60000 if sys.version_info >= (3, 11) else 20000
_SLICE = 0.05                   # how often blocked waits look for cancel()


class TaskCancelled(BaseException):
    """Raised inside a task's thread after cancel(); not catchable from TB."""


def current_task():
    """The Task the calling thread is working for, or None (main program)."""
    return getattr(_local, "task", None)


def check_cancelled() -> None:
    task = current_task()
    if task is not None and task.cancel_requested:
        for view in task.views[:1]:  # this thread's view: its cancel hook has now fired too,
            mine = getattr(view._local, "interp", None)  # so finally/defer blocks can run
            if mine is not None and getattr(mine.trace, "tb_cancel", False):
                mine.trace = None
        raise TaskCancelled()


# ---------------------------------------------------------------------------
# The event loop
# ---------------------------------------------------------------------------


class Baton:
    """Who may run TB code inside a loop: one holder at a time, handed on in
    first-come order (a plain Lock could starve a waiter)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._held = False
        self._waiters: collections.deque = collections.deque()

    def acquire(self) -> None:
        with self._lock:
            if not self._held:
                self._held = True
                return
            ready = threading.Event()
            self._waiters.append(ready)
        ready.wait()                  # release() handed it straight to us

    def release(self) -> None:
        with self._lock:
            if self._waiters:
                self._waiters.popleft().set()
            else:
                self._held = False


class Loop:
    """One parallel.loop(): the asyncio loop on its thread, the baton, and the
    tasks running inside it."""

    def __init__(self):
        import asyncio

        self.aio = asyncio.new_event_loop()
        self.baton = Baton()
        self.thread = threading.current_thread()
        self.tasks: set = set()           # unfinished tasks started inside the loop
        self.closers: list = []           # run when the loop ends (servers)
        self.root = None
        self.parked = 0                   # > 0 while the loop thread itself waits (a handler in wait())

    def wake(self) -> None:
        """From any thread: let the loop see whether it is finished."""
        try:
            self.aio.call_soon_threadsafe(self._check)
        except RuntimeError:              # the loop has already closed
            pass

    def _check(self) -> None:
        if self.root is not None and self.root.done() and not self.tasks:
            self.aio.stop()

    def on_loop_thread(self) -> bool:
        return threading.current_thread() is self.thread

    def call(self, coro, fallback=None):
        """Run an asyncio coroutine on the loop from a task and wait for it
        (other tasks run meanwhile). While the loop thread is itself parked
        (a request handler waiting for this very task), the coroutine could
        never run: then fallback() does the work instead (if given)."""
        import asyncio

        if fallback is not None and self.parked:
            coro.close()
            return fallback()
        future = asyncio.run_coroutine_threadsafe(coro, self.aio)
        done = threading.Event()
        future.add_done_callback(lambda f: done.set())
        stuck = False
        try:
            with blocking():
                while not _wait_event(done, _SLICE):
                    if fallback is not None and self.parked:
                        stuck = True
                        break
        except BaseException:
            future.cancel()
            raise
        if stuck:
            future.cancel()
            return fallback()
        return future.result()


def current_loop():
    """The Loop the calling thread runs inside, or None (thread mode)."""
    return getattr(_local, "loop", None)


def _hold(loop) -> None:
    loop.baton.acquire()
    _local.holding = True


def _drop(loop) -> None:
    _local.holding = False
    loop.baton.release()


@contextlib.contextmanager
def blocking():
    """Wrap anything that may block (socket I/O, waits): inside a loop, the
    other tasks run meanwhile. Nothing happens outside a loop."""
    loop = current_loop()
    if loop is None or not getattr(_local, "holding", False):
        yield
        return
    mine = loop.on_loop_thread()
    if mine:
        loop.parked += 1
    _drop(loop)
    try:
        yield
    finally:
        _hold(loop)
        if mine:
            loop.parked -= 1


def releases(fn):
    """Decorator for library functions that only do I/O (no TB callbacks):
    they run inside blocking()."""
    import functools

    @functools.wraps(fn)
    def run(*args, **kwargs):
        with blocking():
            return fn(*args, **kwargs)
    return run


@contextlib.contextmanager
def turn(loop):
    """Hold loop's baton while a library runs a TB callback (server handlers
    on the loop thread, or a listener thread started inside the loop)."""
    if getattr(_local, "holding", False):
        yield
        return
    previous = getattr(_local, "loop", None)
    _local.loop = loop
    _hold(loop)
    try:
        yield
    finally:
        _drop(loop)
        _local.loop = previous


def inherit_loop(target):
    """target, made to run its TB callbacks inside the caller's loop (if any)
    when started on a new thread."""
    loop = current_loop()
    if loop is None:
        return target

    def run(*args):
        _local.loop = loop
        return target(*args)
    return run


def wait_event(event: threading.Event, timeout=None) -> bool:
    """event.wait(timeout) that wakes up when the calling task is cancelled
    (raising TaskCancelled) and stays responsive to Ctrl+C. Inside a loop,
    other tasks run while this one waits."""
    with blocking():
        return _wait_event(event, timeout)


def _wait_event(event, timeout):
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    while True:
        check_cancelled()
        step = _SLICE
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                return event.is_set()
            step = min(step, left)
        if event.wait(step):
            return True


def sleep(seconds: float) -> None:
    """time.sleep that a cancel() of the calling task interrupts."""
    wait_event(threading.Event(), seconds)


def _cancel_hook(view):
    def hook(event, line, file, env):
        view.trace = None           # one shot: finally/defer bodies still run
        raise TaskCancelled()
    hook.tb_cancel = True
    return hook


def _prepare_threads() -> None:
    """Room for deep TB recursion on task threads (once per process)."""
    global _STACK_SET
    if _STACK_SET:
        return
    _STACK_SET = True
    try:
        if threading.stack_size() < 32 * 1024 * 1024:
            threading.stack_size(32 * 1024 * 1024)
    except (ValueError, RuntimeError):  # pragma: no cover - platform limits
        pass


def _report(err) -> None:
    text = str(err)
    trace = err.trace_text() if hasattr(err, "trace_text") else ""
    print(text + ("\n" + trace if trace else ""), file=sys.stderr, flush=True)


class Task:
    """The Python side of a TB Task. `obj` is the TB object programs see."""

    def __init__(self, name: str, interp):
        self.name = name
        self.interp = interp            # the interpreter that started it
        self.views: list = []           # interpreter views working for this task
        self.value = None
        self.error: BaseException | None = None
        self.status = "running"         # running | done | failed | cancelled
        self.cancel_requested = False
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list = []
        self.obj = Instance(TASK_CLASS, {"name": name, "_task": self})

    # -- finishing -------------------------------------------------------------

    def finish(self, value=None, error=None, status=None) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.value, self.error = value, error
            self.status = status or ("failed" if error is not None else "done")
            callbacks, self._callbacks = self._callbacks, []
            self._event.set()
        for cb in callbacks:
            cb(self)

    def add_done_callback(self, cb) -> None:
        """cb(task) once the task has finished (now, if it already has)."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(cb)
                return
        cb(self)

    # -- what TB sees ----------------------------------------------------------

    def done(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout=None, line=None):
        if not wait_event(self._event, timeout):
            raise TBError(f"The task '{self.name}' didn't finish within {_seconds(timeout)}.", line,
                          {"task": self.name}, cls=ERROR_CLASSES["TimeoutError"])
        err = self.error
        if err is not None:
            if isinstance(err, TBError) and err.line is None:
                err.line = line
            raise err
        return self.value

    def cancel(self) -> bool:
        """Ask the task to stop. False if it has already finished."""
        if self._event.is_set():
            return False
        self.cancel_requested = True
        for view in list(self.views):
            view.trace = _cancel_hook(view)
        return True


def _seconds(timeout) -> str:
    t = float(timeout)
    shown = int(t) if t.is_integer() else round(t, 3)
    return f"{shown} second" + ("" if shown == 1 else "s")


def _run(task: Task, view, work) -> None:
    """Thread body: run work(view) for task, record how it ended."""
    previous_task = getattr(_local, "task", None)
    previous_view = getattr(view._local, "interp", None)
    _local.task = task
    view._local.interp = view
    task.views.append(view)
    if sys.getrecursionlimit() < _PYTHON_RECURSION_LIMIT:
        sys.setrecursionlimit(_PYTHON_RECURSION_LIMIT)
    try:
        try:
            value = work(view)
        except TaskCancelled:
            task.finish(error=TBError("The task was cancelled.", None, {"cancelled": True, "task": task.name}),
                        status="cancelled")
        except TBError as err:
            task.finish(error=err)
        except TBExit as stop:
            task.finish(error=stop)
        except RecursionError:
            task.finish(error=TBError("The task went too deep (too many functions inside functions).", None))
        except BaseException as err:  # never let a Python traceback out of a task
            task.finish(error=TBError(f"Something went wrong inside the task: {err}", None))
        else:
            task.finish(value=value)
    finally:
        task.views.remove(view)
        _local.task = previous_task
        view._local.interp = previous_view


def spawn(interp, work, name: str = "task") -> Task:
    """Start work(view) on its own thread; view is a fork of interp's
    current view. Returns the Task (its TB object is task.obj)."""
    root = interp.current()
    task = Task(name, root)
    parent = current_task()
    if parent is not None and parent.cancel_requested:
        raise TaskCancelled()
    view = root.fork()
    if not THREADS_OK:  # the browser: no threads, so run it now
        _run(task, view, work)
        return task
    _prepare_threads()
    loop = current_loop()
    target, args = _run, (task, view, work)
    if loop is not None:  # a coroutine: counted by the loop, runs only while holding its baton
        loop.tasks.add(task)
        target, args = _run_in_loop, (loop, task, view, work)
    thread = threading.Thread(target=target, args=args, name=f"tb-task-{name}", daemon=True)
    thread.start()
    return task


def _run_in_loop(loop, task, view, work) -> None:
    _local.loop = loop
    _hold(loop)
    try:
        _run(task, view, work)
    finally:
        _drop(loop)
        loop.tasks.discard(task)
        loop.wake()


def run_loop(interp, work, name: str = "loop", line=None):
    """parallel.loop(): run work(view) as the main task of a new event loop
    on the calling thread; return its result once it and every task started
    inside the loop have finished. Inside a loop already, just run it."""
    if current_loop() is not None:
        return work(interp.current())
    loop = Loop()
    _local.loop = loop
    try:
        loop.root = spawn(interp, work, name)
        loop.wake()
        try:
            loop.aio.run_forever()
        except BaseException:  # Ctrl+C: stop every task, then leave
            for task in list(loop.tasks):
                task.cancel()
            raise
    finally:
        _local.loop = None
        for close in reversed(loop.closers):
            try:
                close()
            except Exception:
                pass
        try:
            loop.aio.run_until_complete(loop.aio.shutdown_asyncgens())
        except Exception:
            pass
        loop.aio.close()
    return loop.root.wait(None, line)


def start_async(interp, fn, scope, line) -> Instance:
    """Calling an `async fn`: run its body (params already bound in scope)
    as a task and give back the Task object."""
    task = spawn(interp, lambda view: view.call_function(fn, None, None, line, scope=scope), fn.name)
    return task.obj


def run_pool(interp, items, call_one, workers: int, line):
    """Call call_one(view, item) for every item on up to `workers` threads
    (one interpreter view per thread). Results in order; the first error (by
    position) is raised after the others stop. Used by parallel.map/each."""
    n = len(items)
    results = [None] * n
    if n == 0:
        return results
    root = interp.current()
    parent = current_task()
    workers = max(1, min(workers, n))
    if not THREADS_OK or workers == 1:
        for i, item in enumerate(items):
            check_cancelled()
            results[i] = call_one(root, item)
        return results
    _prepare_threads()
    lock = threading.Lock()
    state = {"next": 0, "error": None, "error_at": n, "stop": False}
    finished = threading.Event()
    running = [workers]
    loop = current_loop()

    def worker():
        if loop is not None:  # a coroutine of the caller's loop
            _local.loop = loop
            _hold(loop)
        view = root.fork()
        _local.task = parent
        view._local.interp = view
        if parent is not None:
            parent.views.append(view)
            if parent.cancel_requested:
                view.trace = _cancel_hook(view)
        try:
            while True:
                with lock:
                    i = state["next"]
                    if state["stop"] or i >= n:
                        break
                    state["next"] = i + 1
                try:
                    results[i] = call_one(view, items[i])
                except BaseException as err:  # TBError, TBExit, TaskCancelled, ...
                    if isinstance(err, RecursionError):
                        err = TBError("The function went too deep (too many functions inside functions).", line)
                    with lock:
                        state["stop"] = True
                        if i < state["error_at"]:
                            state["error"], state["error_at"] = err, i
                    break
        finally:
            if parent is not None:
                parent.views.remove(view)
            with lock:
                running[0] -= 1
                if running[0] == 0:
                    finished.set()
            if loop is not None:
                _drop(loop)

    if sys.getrecursionlimit() < _PYTHON_RECURSION_LIMIT:
        sys.setrecursionlimit(_PYTHON_RECURSION_LIMIT)
    for k in range(workers):
        threading.Thread(target=worker, name=f"tb-pool-{k}", daemon=True).start()
    try:
        wait_event(finished)
    except BaseException:
        state["stop"] = True
        raise
    if state["error"] is not None:
        raise state["error"]
    return results


# ---------------------------------------------------------------------------
# The TB Task class
# ---------------------------------------------------------------------------

TASK_CLASS = TBClass("Task")
TASK_CLASS.doc = ("Work running at the same time as the rest of the program, from parallel.run() "
                  "or calling an async fn. await it (or call wait()) for its result.")


def _task_of(me) -> Task:
    return me.fields["_task"]


def _timeout_arg(line, timeout, where):
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise TBError(f"{where}() needs timeout to be a number of seconds (0 or more).", line,
                      cls=ERROR_CLASSES["TypeError"])
    return timeout


def _m_wait(interp, line, me, timeout=None):
    return _task_of(me).wait(_timeout_arg(line, timeout, "task.wait"), line)


def _deliver(task: Task, interp, fn, args, line) -> None:
    """Run a TB callback for a finished task: on the window thread if a gui
    is running, else right here."""
    poster = MAIN_POSTER
    if poster is not None:
        try:
            if poster(fn, args, line):
                return
        except Exception:  # pragma: no cover - a closing window
            pass
    try:
        interp.current().call(fn, list(args), line)
    except TBError as err:
        _report(err)
    except TaskCancelled:
        pass


def _m_on_done(interp, line, me, fn, on_error=None):
    from .runtime import is_callable
    for value, label in ((fn, "fn"), (on_error, "on_error")):
        if value is not None and not is_callable(value):
            raise TBError(f"task.on_done() needs {label} to be a function.", line, cls=ERROR_CLASSES["TypeError"])
    task = _task_of(me)
    root = task.interp

    def finished(t: Task):
        if t.error is None:
            if fn is not None:
                _deliver(t, root, fn, [t.value], line)
        elif on_error is not None:
            err = t.error
            message = getattr(err, "message", None) or str(err)
            _deliver(t, root, on_error, [message], line)
        elif isinstance(t.error, TBError):
            _report(t.error)

    task.add_done_callback(finished)
    return me


def _m_text(interp, line, me):
    task = _task_of(me)
    return f"<task {task.name} {task.status}>"


for _name, _fn, _arity, _doc in (
    ("wait", _m_wait, (0, 1), "wait(timeout?) -> value\nWait until the task has finished and give back its "
     "result. If the task stopped with an error, the same error happens here. timeout is in seconds: "
     "waiting longer gives a TimeoutError (the task keeps running)."),
    ("done", lambda i, l, me: _task_of(me).done(), (0, 0), "done() -> bool\nTrue once the task has finished "
     "(well or with an error)."),
    ("cancel", lambda i, l, me: _task_of(me).cancel(), (0, 0), "cancel() -> bool\nAsk the task to stop. It "
     "stops before its next step (finally and defer blocks still run); parallel.sleep and waits wake up. "
     "False if it had already finished."),
    ("cancelled", lambda i, l, me: _task_of(me).status == "cancelled" or _task_of(me).cancel_requested,
     (0, 0), "cancelled() -> bool\nTrue if cancel() was called before the task finished."),
    ("status", lambda i, l, me: _task_of(me).status, (0, 0), "status() -> text\n\"running\", \"done\", "
     "\"failed\" or \"cancelled\"."),
    ("on_done", _m_on_done, (1, 2), "on_done(fn, on_error?) -> task\nCall fn(result) when the task finishes "
     "(or on_error(message) if it fails). With a window open, they run on the window thread, so they may "
     "change widgets. Without on_error, a failure is printed."),
    ("text", _m_text, (0, 0), "text() -> text\nHow the task shows in say()."),
):
    TASK_CLASS.methods[_name] = NativeMethod(_name, _fn, _arity, _doc)
