"""TB 3.0 Part C: the parallel library (tasks, pools, queues, locks, timers, gui)."""

import sys
import threading
import time

import pytest

import tb
from conftest import run_tb, tb_error
from tblib import gui


def test_run_and_wait():
    src = '''import parallel
let t = parallel.run(fn(a, b) { return a + b }, 2, 3)
say(type(t), t.wait(), t.done(), t.status(), t.name)
fn add() { return 1 }
let named = parallel.run(add)
say(named.wait(1), named, await named)'''
    assert run_tb(src) == ["Task 5 true done task", "1 <task add done> 1"]


def test_map_keeps_order_and_is_faster_than_one_at_a_time():
    src = '''import parallel
import time
fn slow(x) {
  parallel.sleep(0.15)
  return x * x
}
let start = time.timestamp()
say(parallel.map(slow, [1, 2, 3, 4, 5, 6, 7, 8]))
let fast = time.timestamp() - start
start = time.timestamp()
say(parallel.map(slow, [1, 2, 3, 4], 1))
let one = time.timestamp() - start
say(fast < 0.6, one >= 0.55)
say(parallel.map(x => x + 1, 1..=3), parallel.map(x => x, []))'''
    assert run_tb(src) == ["[1, 4, 9, 16, 25, 36, 49, 64]", "[1, 4, 9, 16]", "true true", "[2, 3, 4] []"]


def test_each_with_a_lock():
    src = '''import parallel
let lock = parallel.Lock()
let seen = {"n": 0, "items": []}
parallel.each(fn(i) {
  lock.hold(fn() {
    seen.n += 1
    seen.items.push(i)
  })
}, range(300), 16)
say(seen.n, len(seen.items), sorted(seen.items) == range(300))
say(lock.hold(fn() { return "inside" }), lock)'''
    assert run_tb(src) == ["300 300 true", "inside <lock>"]


def test_queue_producer_consumer():
    src = '''import parallel
let q = parallel.Queue(2)
let producer = parallel.run(fn() {
  for i in 1..=10 { q.put(i) }
  q.close()
  return "produced"
})
let total = 0
for item in q { total += item }
say(total, producer.wait(), q.is_closed(), q.get() == done, q.size())
let q2 = parallel.Queue()
q2.put("a")
say(q2.get(), q2)
try { q2.get(0.05) } catch err: TimeoutError { say("empty:", err.message) }
q2.close()
try { q2.put(1) } catch err { say(err.message) }'''
    assert run_tb(src) == [
        "55 produced true true 0", "a <queue 0 waiting>",
        "empty: queue.get() waited 0.05 seconds and gave up.",
        "This queue is closed, so nothing more can be put in it.",
    ]


def test_many_producers_and_consumers():
    src = '''import parallel
let jobs = parallel.Queue()
let results = parallel.Queue()
let workers = []
for w in range(4) {
  workers.push(parallel.run(fn() {
    for job in jobs { results.put(job * 10) }
  }))
}
for i in range(50) { jobs.put(i) }
jobs.close()
parallel.all(workers)
results.close()
let got = []
for r in results { got.push(r) }
say(len(got), sum(got))'''
    assert run_tb(src) == ["50 12250"]


def test_errors_keep_their_class_message_and_line():
    src = '''import parallel
fn check(x) {
  if x == 3 {
    throw ValueError("bad item 3")
  }
  return x
}
try {
  parallel.map(check, [1, 2, 3, 4, 5])
} catch err: ValueError {
  say("map:", err.message, err.line)
}
let t = parallel.run(fn() { return 1 + nil })
try { t.wait() } catch err: TypeError { say("run:", err.message, err.line, t.status()) }
try { parallel.all([parallel.run(check, 3)]) } catch err: ValueError { say("all:", err.line) }'''
    assert run_tb(src) == [
        "map: bad item 3 4", "run: I can't add number and nil. 13 failed", "all: 4",
    ]
    err = tb_error('import parallel\nlet t = parallel.run(fn() { throw FileError("gone") })\nt.wait()')
    assert err.message == "gone" and err.line == 2 and err.cls.name == "FileError"


def test_uncaught_task_error_reads_like_a_normal_error():
    err = tb_error('import parallel\nfn work() {\n  let x = [1][5]\n}\nparallel.run(work).wait()')
    assert str(err).startswith("Oops! On line 3:")
    assert "work()" in err.trace_text()


def test_cancel_stops_a_busy_task_and_runs_finally():
    src = '''import parallel
let log = parallel.Queue()
let busy = parallel.run(fn() {
  try {
    let i = 0
    while true { i += 1 }
  } finally {
    log.put("cleaned up")
  }
})
parallel.sleep(0.05)
say(busy.cancel(), busy.cancel() or true)
try { busy.wait(2) } catch err { say(err.message, err.cancelled) }
say(busy.status(), busy.cancelled(), log.get(1))
let sleeper = parallel.run(fn() { parallel.sleep(30) })
let started = parallel.run(fn() { return 1 })
started.wait()
say(started.cancel())
sleeper.cancel()
try { sleeper.wait(2) } catch err { say("woke:", err.message) }'''
    start = time.monotonic()
    assert run_tb(src) == [
        "true true", "The task was cancelled. true", "cancelled true cleaned up", "false",
        "woke: The task was cancelled.",
    ]
    assert time.monotonic() - start < 5


def test_cancel_reaches_map_workers():
    src = '''import parallel
let outer = parallel.run(fn() {
  parallel.map(fn(x) { parallel.sleep(30) }, range(4))
})
parallel.sleep(0.05)
outer.cancel()
try { outer.wait(3) } catch err { say(err.message) }'''
    start = time.monotonic()
    assert run_tb(src) == ["The task was cancelled."]
    assert time.monotonic() - start < 3


def test_wait_timeout():
    src = '''import parallel
let t = parallel.run(fn() { parallel.sleep(5) })
try { t.wait(0.05) } catch err: TimeoutError { say(err.message, t.done()) }
t.cancel()
try { parallel.all([t], 0.01) } catch err { say(type(err)) }'''
    out = run_tb(src)
    assert out[0] == "The task 'task' didn't finish within 0.05 seconds. false"


def test_all_and_race():
    src = '''import parallel
fn after(s, v) {
  parallel.sleep(s)
  return v
}
say(parallel.all([parallel.run(after, 0.1, "a"), "plain", parallel.run(after, 0, "c")]))
say(parallel.race([parallel.run(after, 1, "slow"), parallel.run(after, 0, "quick")]))
say(parallel.race(["now", parallel.run(after, 1, "x")]))
try { parallel.race([parallel.run(after, 2, "x")], 0.05) } catch err: TimeoutError { say(err.message) }'''
    assert run_tb(src) == ['["a", "plain", "c"]', "quick", "now", "None of the tasks finished within 0.05 seconds."]


def test_nested_tasks():
    src = '''import parallel
fn fib(n) {
  if n < 2 { return n }
  if n < 12 { return fib(n - 1) + fib(n - 2) }
  let a = parallel.run(fib, n - 1)
  let b = parallel.run(fib, n - 2)
  return a.wait() + b.wait()
}
say(fib(15))
say(parallel.map(fn(x) { return parallel.map(y => x * y, [1, 2, 3]) }, [1, 2]))'''
    assert run_tb(src) == ["610", "[[1, 2, 3], [2, 4, 6]]"]


def test_recursion_depth_is_per_thread():
    # Each task has its own call depth: two 2000-deep recursions at once are
    # fine (together they'd pass the 3000 limit), and the limit still works.
    src = '''import parallel
fn deep(n) {
  if n == 0 { return 0 }
  return 1 + deep(n - 1)
}
let a = parallel.run(deep, 2000)
let b = parallel.run(deep, 2000)
say(deep(2000), a.wait(), b.wait())
fn forever(n) { return forever(n + 1) }
try { parallel.run(forever, 0).wait() } catch err { say(err.message.contains("too many times")) }'''
    assert run_tb(src) == ["2000 2000 2000", "true"]


def test_tasks_share_globals_and_generators_work_inside():
    src = '''import parallel
let shared = {"hits": []}
fn gen() { yield 1; yield 2; yield 3 }
let t = parallel.run(fn() {
  for x in gen() { shared.hits.push(x) }
  return gen().map(x => x * 10).to_list()
})
let g = gen()
let t2 = parallel.run(fn() { return g.to_list() })
say(t.wait(), shared.hits, t2.wait())'''
    assert run_tb(src) == ["[10, 20, 30] [1, 2, 3] [1, 2, 3]"]


def test_timers():
    src = '''import parallel
let ticks = parallel.Queue()
let timer = parallel.every(20, fn() { ticks.put("tick") })
for k in range(3) { ticks.get(5) }
timer.stop()
say(timer.count() >= 3, timer.running(), timer.stop())
let once = parallel.after(10, fn() { ticks.put("later") })
while ticks.get(5) != "later" { }
while once.running() { parallel.sleep(0.01) }
say(once.count(), once.running())
let never = parallel.after(5000, fn() { say("never") })
say(never.stop(), never.count())
let n = {"c": 0}
let stopper = parallel.every(5, fn() {
  n.c += 1
  return n.c < 3
})
while stopper.running() { parallel.sleep(0.01) }
say(n.c, stopper.running())'''
    assert run_tb(src) == ["true false false", "1 false", "true 0", "3 false"]


def test_timer_errors_are_printed(capsys):
    run_tb('import parallel\nlet t = parallel.after(1, fn() { let x = nil + 1 })\nparallel.sleep(0.2)\nsay(t.running())')
    assert "Oops! On line 2" in capsys.readouterr().err


def test_on_done_without_a_window():
    src = '''import parallel
let q = parallel.Queue()
let t = parallel.run(fn() { return 21 })
t.on_done(fn(r) { q.put(r * 2) })
say(q.get(2))
parallel.run(fn() { throw "nope" }).on_done(fn(r) { q.put("ok") }, fn(message) { q.put("failed: " + message) })
say(q.get(2))
let finished = parallel.run(fn() { return 1 })
finished.wait()
finished.on_done(fn(r) { q.put("already") })
say(q.get(2))'''
    assert run_tb(src) == ["42", "failed: nope", "already"]


def test_checks_inputs():
    assert "parallel.run() needs fn to be a function, not text." in str(tb_error('import parallel\nparallel.run("x")'))
    assert "parallel.map() needs at least 1 worker." in str(tb_error('import parallel\nparallel.map(x => x, [1], 0)'))
    assert "parallel.Queue() needs a size of at least 1" in str(tb_error('import parallel\nparallel.Queue(0)'))
    assert "parallel.sleep() needs seconds to be 0 or more" in str(tb_error('import parallel\nparallel.sleep(-1)'))
    assert "needs timeout to be a number" in str(tb_error('import parallel\nparallel.run(fn() {}).wait("x")'))
    assert "parallel.race() needs at least one task." in str(tb_error('import parallel\nparallel.race([])'))


def test_browser_needs_the_app(monkeypatch):
    monkeypatch.setattr(sys, "platform", "emscripten")
    err = tb_error('import parallel\nparallel.run(fn() { return 1 })')
    assert "The parallel library needs the TB app on your computer" in str(err)


def test_library_callbacks_from_other_threads_get_their_own_view():
    # A native library calling back into TB from its own thread (a server, a
    # gui worker) must not disturb the main program's call stack.
    seen = []
    interp = tb.Interpreter(output=seen.append)
    tb.run('fn f(x) { return x + 1 }', interpreter=interp)
    fn = interp.globals.vars["f"]
    results = []
    threads = [threading.Thread(target=lambda: results.append(interp.api.call(fn, [1]))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [2] * 8
    assert interp.depth == 0 and len(interp.call_stack) == 1


def test_documented():
    info = tb.describe_library("parallel")
    names = [f["name"] for f in info["functions"]]
    assert names == ["run", "map", "each", "all", "race", "sleep", "cpu_count", "loop", "group", "timeout",
                     "processes", "shared_queue", "Queue", "Lock", "every", "after"]
    for fn in info["functions"]:
        assert fn["signature"].startswith(fn["name"] + "(") and fn["doc"]


# -- gui integration (FakeBackend) ---------------------------------------------------


@pytest.fixture
def fake():
    backend = gui.FakeBackend()
    previous = gui._use_backend(backend)
    yield backend
    gui._use_backend(previous)


def _settle(fake, label, want, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        fake.advance(0)
        if fake.window_.find("label").text == want:
            return True
        time.sleep(0.01)
    return False


def test_gui_task_updates_widgets_on_the_window_thread(fake):
    src = '''import gui
import parallel
let win = gui.window("scan")
let label = win.label("waiting")
win.button("Go", fn() {
  label.set("working")
  parallel.run(fn() {
    parallel.sleep(0.05)
    return 6 * 7
  }).on_done(fn(result) { label.set(f"got {result}") })
})
win.run()
'''
    run_tb(src)
    fake.window_.find("button").click()
    assert fake.window_.find("label").text == "working"   # the click didn't block
    main = threading.main_thread()
    assert _settle(fake, "label", "got 42")
    assert threading.current_thread() is main


def test_gui_task_errors_go_to_on_error_or_a_dialog(fake, capsys):
    src = '''import gui
import parallel
let win = gui.window("x")
let label = win.label("waiting")
parallel.run(fn() { throw NetworkError("offline") }).on_done(fn(r) { label.set("never") }, fn(m) { label.set("failed: " + m) })
'''
    run_tb(src)
    assert _settle(fake, "label", "failed: offline")
    run_tb('import gui\nimport parallel\nlet win = gui.window("x")\nwin.label("l")\n'
           'parallel.run(fn() { return 1 }).on_done(fn(r) { let x = r + nil })')
    end = time.monotonic() + 3
    while time.monotonic() < end and not fake.dialogs:
        fake.advance(0)
        time.sleep(0.01)
    assert fake.dialogs and fake.dialogs[-1]["kind"] == "error"
    assert "Traceback" not in capsys.readouterr().err


def test_gui_on_main_from_a_task(fake):
    src = '''import gui
import parallel
let win = gui.window("x")
let label = win.label("waiting")
parallel.run(fn() { gui.on_main(fn() { label.set("from task") }) }).wait()
'''
    run_tb(src)
    assert _settle(fake, "label", "from task")


def test_run_in_background_with_real_threads_and_parallel(fake):
    fake.threads = True
    src = '''import gui
import parallel
let win = gui.window("x")
let label = win.label("waiting")
win.run_in_background(fn() { return parallel.map(x => x * 2, [1, 2, 3]) }, fn(r) { label.set(str(r)) })
'''
    run_tb(src)
    end = time.monotonic() + 3
    while time.monotonic() < end and not fake.window_._pending:
        time.sleep(0.01)
    fake.window_.deliver()
    assert fake.window_.find("label").text == "[2, 4, 6]"


# -- apps/parallel-scan and the library example ----------------------------------------


def _run_app(args):
    import socket  # noqa: F401
    from conftest import ROOT
    path = ROOT / "apps" / "parallel-scan" / "main.tb"
    lines, code = [], 0
    try:
        tb.run(path.read_text(encoding="utf-8"), output=lines.append, input_fn=lambda p: "",
               path=str(path), args=args)
    except tb.TBExit as stop:
        code = stop.code if isinstance(stop.code, int) else 0
    return lines, code


def test_parallel_scan_app():
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    open_port = listener.getsockname()[1]
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed = probe.getsockname()[1]
    probe.close()
    try:
        out, code = _run_app(["127.0.0.1", f"{open_port},{closed},1-6", "--latency", "100", "--workers", "8"])
    finally:
        listener.close()
    assert code == 0
    assert out[0] == "Checking 8 ports on 1 host(s) with 8 workers...", out
    assert any(line.startswith("127.0.0.1") and str(open_port) in line and line.endswith("OPEN") for line in out)
    assert "1 open, 7 closed or filtered." in out, out
    speed = [line for line in out if line.startswith("speed-up:")]
    assert speed and float(speed[0].split()[-1].rstrip("x")) > 2, out  # about 8x when idle


def test_parallel_scan_refuses_other_hosts():
    out, code = _run_app(["127.0.0.1,8.8.8.8", "80", "--no-compare"])
    assert code == 2 and out[0] == "8.8.8.8 isn't this computer."
    out, code = _run_app(["--help"])
    assert code == 0 and out[0].startswith("Usage:")
    out, code = _run_app(["127.0.0.1", "0-2"])
    assert code == 2 and "isn't a port number" in out[0]


def test_parallel_scan_has_a_readme():
    from conftest import ROOT
    readme = (ROOT / "apps" / "parallel-scan" / "README.md").read_text(encoding="utf-8")
    assert "## Usage" in readme and "## What it teaches" in readme and "own" in readme


def test_library_example_runs():
    from conftest import ROOT
    src = (ROOT / "scripts" / "lib_examples" / "parallel.desktop.tb").read_text(encoding="utf-8")
    out = run_tb(src)
    assert out[:3] == ["working... false", "49", "[1, 4, 9, 16, 25, 36, 49, 64]"]
    assert "100" in out and "caught: no thanks" in out
