"""parallel: do several things at the same time (tasks, worker pools, queues, timers).

The tasks themselves live in tbcore/tasks.py (shared with `async fn`); this
file is the library surface. Each task runs TB code on its own thread with
its own call stack; globals are shared, so guard shared counters and lists
that several tasks change with a Lock.
"""

from __future__ import annotations

import collections
import os
import threading
import time

import tb as _tb
from tbcore import tasks as _tasks
from tblib._api import (
    MANY, Module, check_type, fail, fail_timeout, fail_type, fail_value, make_object, method, native_class,
)

DOC = ("Do several things at the same time: run functions as tasks, map a function over a list "
       "with many workers, pass work through queues, and run timers. Great for waiting on many "
       "network checks or files at once. Tasks share global variables: use a Lock when several "
       "change the same thing. In a window app, change widgets from on_done (or gui.on_main), "
       "never wait() inside a button click.")


def _name_of(fn) -> str:
    name = getattr(fn, "name", None)
    return name if isinstance(name, str) and name and name != "anonymous" else "task"


def _seconds_arg(line, value, where, what="timeout"):
    if value is None:
        return None
    value = check_type(line, value, "number", where, what)
    if value < 0:
        fail_value(line, f"{where}() needs {what} to be 0 or more seconds.")
    return float(value)


def _ms_arg(line, value, where):
    value = check_type(line, value, "number", where, "ms")
    if value < 0:
        fail_value(line, f"{where}() needs ms to be 0 or more (milliseconds).")
    return value / 1000.0


def _workers_arg(line, value, where):
    if value is None:
        return 8
    value = check_type(line, value, "whole", where, "workers")
    if value < 1:
        fail_value(line, f"{where}() needs at least 1 worker.")
    return min(value, 256)


def _is_task(value) -> bool:
    return isinstance(value, _tb.Instance) and value.cls is _tasks.TASK_CLASS


def load(api):
    m = Module("parallel", DOC)
    interp = api.interpreter

    def need(line):
        api.need_desktop(line, "The parallel library")

    def items_of(line, items, where):
        try:
            return list(interp.current().iterate(items, line, f"{where}()"))
        except _tb.TBError:
            check_type(line, items, "list", where, "items")
            raise

    # -- tasks -------------------------------------------------------------------

    @m.fn("run", 1, MANY, doc="run(fn, ...args) -> Task\nStart fn(args...) at the same time as the rest of "
                             "the program and give back its Task right away. task.wait() (or await task) "
                             "gives its result; task.done(), task.cancel() and task.on_done(fn) are there too.")
    def run(line, fn, *args):
        need(line)
        check_type(line, fn, "function", "parallel.run", "fn")
        task = _tasks.spawn(interp, lambda view: view.call(fn, list(args), line), _name_of(fn))
        return task.obj

    @m.fn("map", 2, 3, doc="map(fn, items, workers?) -> list\nfn(item) for every item, several at once "
                          "(workers at a time, default 8), giving the results in the same order as items. "
                          "If any call fails, map stops and that error happens here.")
    def map_(line, fn, items, workers=None):
        need(line)
        check_type(line, fn, "function", "parallel.map", "fn")
        workers = _workers_arg(line, workers, "parallel.map")
        values = items_of(line, items, "parallel.map")
        return _tasks.run_pool(interp, values, lambda view, item: view.call(fn, [item], line), workers, line)

    @m.fn("each", 2, 3, doc="each(fn, items, workers?)\nCall fn(item) for every item, several at once "
                           "(workers at a time, default 8), and wait until all are done.")
    def each(line, fn, items, workers=None):
        need(line)
        check_type(line, fn, "function", "parallel.each", "fn")
        workers = _workers_arg(line, workers, "parallel.each")
        values = items_of(line, items, "parallel.each")
        _tasks.run_pool(interp, values, lambda view, item: view.call(fn, [item], line), workers, line)
        return None

    @m.fn("all", 1, 2, doc="all(tasks, timeout?) -> list\nWait for every task in the list and give back "
                          "their results in order (plain values are passed through). If one failed, its "
                          "error happens here. timeout is in seconds, for all of them together.")
    def all_(line, tasks, timeout=None):
        check_type(line, tasks, "list", "parallel.all", "tasks")
        timeout = _seconds_arg(line, timeout, "parallel.all")
        deadline = None if timeout is None else time.monotonic() + timeout
        results = []
        for value in tasks:
            if _is_task(value):
                left = None if deadline is None else max(0.0, deadline - time.monotonic())
                results.append(value.fields["_task"].wait(left, line))
            else:
                results.append(value)
        return results

    @m.fn("race", 1, 2, doc="race(tasks, timeout?) -> value\nWait for whichever task finishes first and "
                           "give back its result (or its error). The others keep running: cancel() them "
                           "if you don't need them.")
    def race(line, tasks, timeout=None):
        check_type(line, tasks, "list", "parallel.race", "tasks")
        timeout = _seconds_arg(line, timeout, "parallel.race")
        if not tasks:
            fail_value(line, "parallel.race() needs at least one task.")
        for value in tasks:
            if not _is_task(value):
                return value
        first = []
        ready = threading.Event()

        def finished(task):
            first.append(task)
            ready.set()

        for value in tasks:
            value.fields["_task"].add_done_callback(finished)
        if not _tasks.wait_event(ready, timeout):
            fail_timeout(line, f"None of the tasks finished within {_tasks._seconds(timeout)}.")
        return first[0].wait(0, line)

    @m.fn("sleep", 1, doc="sleep(seconds)\nPause for a while (fractions like 0.25 work). Inside a task, "
                         "cancel() wakes it up and stops the task.")
    def sleep(line, seconds):
        need(line)
        seconds = _seconds_arg(line, seconds, "parallel.sleep", "seconds")
        _tasks.sleep(seconds)
        return None

    @m.fn("cpu_count", 0, doc="cpu_count() -> number\nHow many processor cores this computer has.")
    def cpu_count(line):
        return os.cpu_count() or 1

    # -- the event loop, groups and time limits (TB 3.2) ---------------------------

    @m.fn("loop", 1, MANY, doc=(
        "loop(fn, ...args) -> value\n"
        "Run fn(args...) on an event loop and give back its result once fn and every task and timer it "
        "started have finished. Servers it started with start() stop then.\n"
        "\n"
        "Inside the loop, tasks (async fn, parallel.run, map) take turns instead of running at the same "
        "moment: one runs until it waits (await, sleep, a queue, a network request), then the next. So "
        "loop code needs no locks. Servers started inside a loop handle every connection on one thread, "
        "so thousands of connections are fine.\n"
        "\n"
        "Example:\n"
        "    async fn twice(x) {\n"
        "      parallel.sleep(0.1)\n"
        "      return x * 2\n"
        "    }\n"
        "    say(parallel.loop(fn() { return parallel.all([twice(1), twice(2)]) }))"))
    def loop(line, fn, *args):
        need(line)
        check_type(line, fn, "function", "parallel.loop", "fn")
        return _tasks.run_loop(interp, lambda view: view.call(fn, list(args), line), _name_of(fn), line)

    Group = native_class("Group", "Tasks that belong together, from parallel.group(fn).")

    class _G:
        def __init__(self):
            self.tasks: list = []
            self.failed: list = []
            self.changed = threading.Event()
            self.open = True

        def finished(self, task):
            if task.status == "failed":
                self.failed.append(task)
            self.changed.set()

        def cancel(self):
            for task in self.tasks:
                task.cancel()

        def join(self):  # wait for every task, even while being cancelled ourselves
            with _tasks.blocking():
                for task in self.tasks:
                    task._event.wait()

    @method(Group, "run", 1, MANY, doc="run(fn, ...args) -> Task\nStart fn(args...) as part of the group and "
                                       "give back its Task.")
    def g_run(line, me, fn, *args):
        g = me.fields["_g"]
        check_type(line, fn, "function", "group.run", "fn")
        if not g.open:
            fail(line, "This group has finished. Start tasks inside the fn given to parallel.group().")
        task = _tasks.spawn(interp, lambda view: view.call(fn, list(args), line), _name_of(fn))
        g.tasks.append(task)
        task.add_done_callback(g.finished)
        return task.obj

    @method(Group, "cancel", 0, doc="cancel()\nCancel every task of the group that is still running. Their "
                                    "results are nil.")
    def g_cancel(line, me):
        me.fields["_g"].cancel()
        return None

    @method(Group, "text", 0, doc="text() -> text\nHow the group shows in say().")
    def g_text(line, me):
        g = me.fields["_g"]
        return f"<group {sum(1 for t in g.tasks if not t.done())} running of {len(g.tasks)}>"

    @m.fn("group", 1, doc=(
        "group(fn) -> list\n"
        "Call fn(group), where group.run(f, ...args) starts tasks that belong to it, then wait for all of "
        "them and give back their results in the order they were started.\n"
        "\n"
        "If one task fails, the others are cancelled and that error happens here. If fn itself fails, or "
        "the task waiting here is cancelled, the group's tasks are cancelled too, so none are left "
        "running. group.cancel() stops the rest early (their results are nil).\n"
        "\n"
        "Example:\n"
        "    let pages = parallel.group(fn(g) {\n"
        "      for url in urls { g.run(http.get, url) }\n"
        "    })"))
    def group(line, fn):
        need(line)
        check_type(line, fn, "function", "parallel.group", "fn")
        g = _G()
        try:
            interp.current().call(fn, [make_object(Group, {"_g": g})], line)
            while True:
                g.changed.clear()
                if g.failed or all(t.done() for t in g.tasks):
                    break
                _tasks.wait_event(g.changed)
        except BaseException:
            g.open = False
            g.cancel()
            g.join()
            raise
        g.open = False
        if g.failed:
            g.cancel()
            g.join()
            return g.failed[0].wait(0, line)
        return [t.value if t.status == "done" else None for t in g.tasks]

    @m.fn("timeout", 2, doc=(
        "timeout(seconds, fn) -> value\n"
        "Call fn() and give back its result, but cancel it if it takes longer than seconds; that gives a "
        "TimeoutError with err.cancelled true. Tasks fn started in a parallel.group are cancelled with it.\n"
        "\n"
        "Cancelling is cooperative: fn stops before its next step, or when it next waits."))
    def timeout_(line, seconds, fn):
        need(line)
        seconds = _seconds_arg(line, seconds, "parallel.timeout", "seconds")
        check_type(line, fn, "function", "parallel.timeout", "fn")
        task = _tasks.spawn(interp, lambda view: view.call(fn, [], line), _name_of(fn))
        try:
            finished = _tasks.wait_event(task._event, seconds)
        except BaseException:
            task.cancel()
            raise
        if not finished:
            task.cancel()
            with _tasks.blocking():
                task._event.wait()
            if task.status == "cancelled":
                fail_timeout(line, f"parallel.timeout(): fn didn't finish within {_tasks._seconds(seconds)}, "
                                   "so it was cancelled.", cancelled=True)
        return task.wait(0, line)

    # -- processes (TB 3.2) ---------------------------------------------------------

    @m.fn("processes", 2, 3, doc=(
        "processes(fn, items, workers?) -> list\n"
        "fn(item) for every item, in other processes (workers at a time, default: one per processor core), "
        "giving the results in the same order as items.\n"
        "\n"
        "Unlike map, this makes CPU-heavy work faster: each process has a core to itself. Starting the "
        "processes takes a moment, so use it for work that takes seconds, not milliseconds.\n"
        "\n"
        "fn is copied into each process with the values it uses, so changes it makes to variables aren't "
        "seen here. Items, results and the values fn uses must be plain: nil, true/false, numbers, text, "
        "bytes, lists, maps, other functions, libraries or shared queues; anything else is a TypeError.\n"
        "\n"
        "Example:\n"
        "    fn slow_square(n) { return n * n }\n"
        "    say(parallel.processes(slow_square, [1, 2, 3, 4], 2))"))
    def processes(line, fn, items, workers=None):
        need(line)
        if not isinstance(fn, _tb.Function) or fn.owner is not None:
            fail_type(line, "parallel.processes() needs fn to be a function written in TB (not a method or a "
                            "built-in function).")
        workers = (os.cpu_count() or 1) if workers is None else _workers_arg(line, workers, "parallel.processes")
        from tblib import _procs
        return _procs.run(interp, fn, items_of(line, items, "parallel.processes"), workers, line)

    @m.fn("shared_queue", 0, 1, doc=(
        "shared_queue(size?) -> SharedQueue\n"
        "A queue that functions running in parallel.processes can use too: put(value, timeout?), "
        "get(timeout?), size(), close(), and `for item in q { }`, like parallel.Queue. Only plain values "
        "can go in. With a size, put() waits while the queue is full."))
    def shared_queue(line, size=None):
        need(line)
        if size is not None:
            size = check_type(line, size, "whole", "parallel.shared_queue", "size")
            if size < 1:
                fail_value(line, "parallel.shared_queue() needs a size of at least 1 (or no size).")
        from tblib import _procs
        return _procs.new_shared_queue(line, size)

    # -- Queue -------------------------------------------------------------------

    Queue = native_class("Queue", "A first-in, first-out line of values that tasks can share safely.")

    class _Q:
        def __init__(self, size):
            self.items = collections.deque()
            self.size = size
            self.closed = False
            self.cond = threading.Condition()

        def wait_until(self, ready, timeout, line, what):
            deadline = None if timeout is None else time.monotonic() + timeout
            while not ready():
                _tasks.check_cancelled()
                step = 0.05
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        fail_timeout(line, f"queue.{what}() waited {_tasks._seconds(timeout)} and gave up.")
                    step = min(step, left)
                if _tasks.current_loop() is None:
                    self.cond.wait(step)
                    continue
                # Inside a loop: never wait for the baton while holding cond.
                self.cond.release()
                try:
                    with _tasks.blocking(), self.cond:
                        if not ready():
                            self.cond.wait(step)
                finally:
                    self.cond.acquire()

    def q_of(me):
        return me.fields["_q"]

    @method(Queue, "put", 1, 2, doc="put(value, timeout?)\nAdd value at the end. If the queue has a size "
                                    "and is full, wait for room (up to timeout seconds).")
    def q_put(line, me, value, timeout=None):
        q = q_of(me)
        timeout = _seconds_arg(line, timeout, "queue.put")
        with q.cond:
            if q.closed:
                fail(line, "This queue is closed, so nothing more can be put in it.")
            q.wait_until(lambda: q.closed or not q.size or len(q.items) < q.size, timeout, line, "put")
            if q.closed:
                fail(line, "This queue is closed, so nothing more can be put in it.")
            q.items.append(value)
            q.cond.notify_all()
        return None

    @method(Queue, "get", 0, 1, doc="get(timeout?) -> value\nTake the value at the front, waiting for one "
                                    "if the queue is empty (up to timeout seconds, then a TimeoutError). "
                                    "Gives done once the queue is closed and empty.")
    def q_get(line, me, timeout=None):
        q = q_of(me)
        timeout = _seconds_arg(line, timeout, "queue.get")
        with q.cond:
            q.wait_until(lambda: q.items or q.closed, timeout, line, "get")
            if not q.items:
                return _tb.DONE
            value = q.items.popleft()
            q.cond.notify_all()
            return value

    @method(Queue, "next", 0, doc="next() -> value\nLike get(): lets `for item in queue { }` take values "
                                  "until the queue is closed and empty.")
    def q_next(line, me):
        return q_get(line, me)

    @method(Queue, "size", 0, doc="size() -> number\nHow many values are waiting.")
    def q_size(line, me):
        return len(q_of(me).items)

    @method(Queue, "close", 0, doc="close()\nNo more values will be put in. Waiting get()s and for loops "
                                   "finish once the queue is empty.")
    def q_close(line, me):
        q = q_of(me)
        with q.cond:
            q.closed = True
            q.cond.notify_all()
        return None

    @method(Queue, "is_closed", 0, doc="is_closed() -> bool\nTrue after close().")
    def q_is_closed(line, me):
        return q_of(me).closed

    @method(Queue, "text", 0, doc="text() -> text\nHow the queue shows in say().")
    def q_text(line, me):
        q = q_of(me)
        return f"<queue {len(q.items)} waiting{', closed' if q.closed else ''}>"

    @m.fn("Queue", 0, 1, doc="Queue(size?) -> Queue\nA new queue for passing values between tasks: "
                             "q.put(value), q.get(timeout?), q.size(), q.close(), and `for item in q { }`. "
                             "With a size, put() waits while the queue is full.")
    def queue(line, size=None):
        need(line)
        if size is not None:
            size = check_type(line, size, "whole", "parallel.Queue", "size")
            if size < 1:
                fail_value(line, "parallel.Queue() needs a size of at least 1 (or no size).")
        return make_object(Queue, {"_q": _Q(size)})

    # -- Lock --------------------------------------------------------------------

    Lock = native_class("Lock", "Lets only one task at a time run a piece of code.")

    @method(Lock, "hold", 1, doc="hold(fn) -> value\nWait until no other task holds the lock, run fn() "
                                 "while holding it, then let go (even if fn fails). Gives back fn's result.")
    def l_hold(line, me, fn):
        check_type(line, fn, "function", "lock.hold", "fn")
        lock = me.fields["_lock"]
        with _tasks.blocking():
            while not lock.acquire(timeout=0.05):
                _tasks.check_cancelled()
        try:
            return interp.current().call(fn, [], line)
        finally:
            lock.release()

    @method(Lock, "text", 0, doc="text() -> text\nHow the lock shows in say().")
    def l_text(line, me):
        return "<lock>"

    @m.fn("Lock", 0, doc="Lock() -> Lock\nA new lock. lock.hold(fn) runs fn() while no other task "
                         "holds the same lock: use it around changes to shared lists, maps and counters.")
    def lock(line):
        need(line)
        return make_object(Lock, {"_lock": threading.RLock()})

    # -- timers ------------------------------------------------------------------

    Timer = native_class("Timer", "Calls a function later, or again and again, until stopped.")

    @method(Timer, "stop", 0, doc="stop() -> bool\nStop the timer (a call already running finishes "
                                  "first). False if it had already stopped.")
    def t_stop(line, me):
        state = me.fields["_state"]
        if state["stopped"].is_set():
            return False
        state["stopped"].set()
        return True

    @method(Timer, "running", 0, doc="running() -> bool\nTrue until the timer stops (or an after() "
                                     "timer has run).")
    def t_running(line, me):
        return not me.fields["_state"]["stopped"].is_set()

    @method(Timer, "count", 0, doc="count() -> number\nHow many times the function has been called.")
    def t_count(line, me):
        return me.fields["_state"]["count"]

    @method(Timer, "text", 0, doc="text() -> text\nHow the timer shows in say().")
    def t_text(line, me):
        state = me.fields["_state"]
        return f"<timer {'running' if not state['stopped'].is_set() else 'stopped'}, {state['count']} calls>"

    def start_timer(line, ms, fn, repeat, where):
        need(line)
        interval = _ms_arg(line, ms, where)
        check_type(line, fn, "function", where, "fn")
        if repeat and interval < 0.001:
            fail_value(line, f"{where}() needs ms to be at least 1.")
        state = {"stopped": threading.Event(), "count": 0}
        obj = make_object(Timer, {"_state": state})

        def work(view):
            stopped = state["stopped"]
            next_at = time.monotonic() + interval
            try:
                while True:
                    if _tasks.wait_event(stopped, max(0.0, next_at - time.monotonic())):
                        return None
                    state["count"] += 1
                    try:
                        result = view.call(fn, [], line)
                    except _tb.TBError as err:
                        _tasks._report(err)
                        return None
                    if not repeat or result is False:
                        return None
                    next_at += interval
                    now = time.monotonic()
                    if next_at < now:  # running late: skip the missed beats
                        next_at = now + interval - ((now - next_at) % interval)
            finally:
                stopped.set()

        _tasks.spawn(interp, work, "timer")
        return obj

    @m.fn("every", 2, doc="every(ms, fn) -> Timer\nCall fn() every ms milliseconds, on its own thread, "
                         "until timer.stop() (or fn gives back false). Errors are printed and stop the "
                         "timer. The program doesn't wait for timers when it ends.")
    def every(line, ms, fn):
        return start_timer(line, ms, fn, True, "parallel.every")

    @m.fn("after", 2, doc="after(ms, fn) -> Timer\nCall fn() once, ms milliseconds from now, on its own "
                         "thread. timer.stop() before then cancels it.")
    def after(line, ms, fn):
        return start_timer(line, ms, fn, False, "parallel.after")

    return m
