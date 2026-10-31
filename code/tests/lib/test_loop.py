"""TB 3.2: parallel.loop (the event loop), groups and time limits, and
parallel.processes (worker processes) with shared queues."""

import subprocess
import sys
import threading
import time

import pytest

import tb
import tblib.server as serverlib
from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def stop_servers():
    yield
    serverlib.stop_all()


# -- the event loop ---------------------------------------------------------------


def test_async_fns_take_turns_on_the_loop():
    src = '''import parallel
import time
async fn twice(x) {
  parallel.sleep(0.2)
  return x * 2
}
let start = time.timestamp()
say(parallel.loop(fn() { return parallel.all([twice(1), twice(2), twice(3)]) }))
say(time.timestamp() - start < 0.6)
let n = {"v": 0}
parallel.loop(fn() {
  parallel.map(fn(i) {
    let v = n.v
    n.v = v + 1
  }, range(200), 16)
})
say(n.v)
say(parallel.loop(fn(a, b) { return parallel.loop(fn() { return a + b }) }, 2, 3))'''
    assert run_tb(src) == ["[2, 4, 6]", "true", "200", "5"]


def test_loop_waits_for_every_task_and_passes_errors_on():
    src = '''import parallel
let seen = []
parallel.loop(fn() {
  parallel.run(fn() {
    parallel.sleep(0.1)
    seen.push("late task")
  })
  seen.push("main done")
})
say(seen)
try { parallel.loop(fn() { throw "nope" }) } catch err { say(err.message) }
let q = parallel.Queue(1)
say(parallel.loop(fn() {
  parallel.run(fn() {
    for i in 1..=20 { q.put(i) }
    q.close()
  })
  let total = 0
  for x in q { total += x }
  return total
}))
let lock = parallel.Lock()
say(parallel.loop(fn() {
  return parallel.map(fn(i) {
    return lock.hold(fn() {
      parallel.sleep(0.01)
      return i
    })
  }, [1, 2, 3], 3)
}))'''
    assert run_tb(src) == ['["main done", "late task"]', "nope", "210", "[1, 2, 3]"]


def test_group_results_failures_and_cancel():
    src = '''import parallel
import time
say(parallel.group(fn(g) {
  g.run(fn() {
    parallel.sleep(0.1)
    return "slow"
  })
  g.run(fn(x) { return x }, "fast")
}))
let start = time.timestamp()
let stopped = {"n": 0}
try {
  parallel.group(fn(g) {
    g.run(fn() {
      try { parallel.sleep(5) } finally { stopped.n += 1 }
    })
    g.run(fn() { throw "boom" })
  })
} catch err { say("caught", err.message, stopped.n, time.timestamp() - start < 2) }
say(parallel.group(fn(g) {
  g.run(fn() { return 1 })
  g.run(fn() { parallel.sleep(5) })
  parallel.sleep(0.05)
  g.cancel()
}))
let kept = nil
parallel.group(fn(g) { kept = g })
try { kept.run(fn() {}) } catch err { say(err.message) }
say(parallel.loop(fn() { return parallel.group(fn(g) { for i in 1..=3 { g.run(fn(x) { return x * x }, i) } }) }))'''
    assert run_tb(src) == [
        '["slow", "fast"]', "caught boom 1 true", "[1, nil]",
        "This group has finished. Start tasks inside the fn given to parallel.group().", "[1, 4, 9]",
    ]


def test_timeout_is_a_cancellation_scope():
    src = '''import parallel
let finished = {"n": 0}
try {
  parallel.timeout(0.2, fn() {
    parallel.group(fn(g) {
      g.run(fn() {
        try { parallel.sleep(5) } finally { finished.n += 1 }
      })
    })
  })
} catch err: TimeoutError { say(err.message, err.cancelled, finished.n) }
say(parallel.timeout(1, fn() { return 7 }))
try { parallel.timeout(1, fn() { throw "inner" }) } catch err { say(err.message) }
say(parallel.loop(fn() { return parallel.timeout(1, fn() { return "in loop" }) }))'''
    assert run_tb(src) == [
        "parallel.timeout(): fn didn't finish within 0.2 seconds, so it was cancelled. true 1",
        "7", "inner", "in loop",
    ]


def test_server_and_websocket_inside_a_loop():
    src = '''import parallel
import server
import http
import websocket
parallel.loop(fn() {
  let app = server.create({"log": false})
  app.get("/hi/{name}", fn(req) { return "hi " + req.params.name })
  app.post("/sum", fn(req) { return server.json({"sum": req.json.a + req.json.b}) })
  app.ws("/echo", fn(ws, req) {
    ws.each(fn(msg) {
      if msg == "bye" { return false }
      ws.send("echo: " + msg)
    })
  })
  let url = app.start(0)
  let answers = parallel.map(fn(name) { return http.get(url + "/hi/" + name).text }, ["a", "b", "c"], 3)
  say(answers)
  say(http.post(url + "/sum", nil, {"json": {"a": 2, "b": 3}}).json)
  say(http.get(url + "/nope").status)
  let ws = websocket.connect("ws" + url.slice(4) + "/echo")
  ws.send("one")
  say(ws.receive(5))
  ws.send("bye")
  ws.close()
})
say("loop ended")'''
    assert run_tb(src) == ['["hi a", "hi b", "hi c"]', '{"sum": 5}', "404", "echo: one", "loop ended"]


def test_check_ports_inside_a_loop_uses_the_loop():
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    try:
        out = run_tb(f'''import parallel
import net
say(parallel.loop(fn() {{ return net.check_ports("127.0.0.1", [{port}]) }}))''')
    finally:
        listener.close()
    assert out == [f"{{{port}: true}}"]


def test_new_functions_check_inputs(monkeypatch):
    assert "parallel.loop() needs fn to be a function" in str(tb_error('import parallel\nparallel.loop(1)'))
    assert "parallel.group() needs fn to be a function" in str(tb_error('import parallel\nparallel.group(1)'))
    assert "parallel.timeout() needs seconds to be 0 or more" in str(
        tb_error('import parallel\nparallel.timeout(-1, fn() {})'))
    assert "parallel.shared_queue() needs a size of at least 1" in str(
        tb_error('import parallel\nparallel.shared_queue(0)'))
    monkeypatch.setattr(sys, "platform", "emscripten")
    for call in ("loop(fn() {})", "group(fn(g) {})", "timeout(1, fn() {})", "processes(fn(x) {}, [1])",
                 "shared_queue()"):
        assert "needs the TB app on your computer" in str(tb_error(f"import parallel\nparallel.{call}"))


# -- 10,000 connections on one loop thread ------------------------------------------

CLIENTS = r'''
import asyncio, sys

async def main(port, n):
    gate = asyncio.Semaphore(100)

    async def connect():
        async with gate:
            for attempt in range(20):
                try:
                    return await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.1 * (attempt + 1))
            raise SystemExit("could not connect")

    conns = await asyncio.gather(*(connect() for _ in range(n)))
    print("open", len(conns), flush=True)
    await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)

    async def ask(i, conn):
        reader, writer = conn
        writer.write(b"GET /hi/%d HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n" % i)
        data = await reader.read()
        writer.close()
        return data.endswith(b"hi %d" % i)

    oks = await asyncio.gather(*(ask(i, c) for i, c in enumerate(conns)))
    print("ok", sum(oks), flush=True)

asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
'''


def _socket_budget(want):
    """How many connections this process can hold, after raising the open
    file limit as far as the system lets us (macOS: kern.maxfilesperproc,
    10240 by default; Windows has no such limit for sockets)."""
    try:
        import resource
    except ImportError:
        return want, None
    old = resource.getrlimit(resource.RLIMIT_NOFILE)
    soft, hard = old
    for target in (want + 300, 10240, 8192, 4096):
        if hard != resource.RLIM_INFINITY:
            target = min(target, hard)
        if target <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            break
        except (ValueError, OSError):
            continue
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    return min(want, soft - 200), old


def test_ten_thousand_connections_without_ten_thousand_threads(capsys):
    want = 10_000
    n, old_limit = _socket_budget(want)
    if n < 1000:
        pytest.skip(f"this system allows only {n + 200} open files")
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # a peer hanging up must not kill the test run
    except (AttributeError, ValueError):
        pass
    lines = []
    src = '''import parallel
import server
parallel.loop(fn() {
  let app = server.create({"log": false})
  app.get("/hi/{i}", fn(req) { return "hi " + req.params.i })
  say(app.start(0))
  app.wait()
})
say("loop ended")'''
    interp = tb.Interpreter(output=lines.append)
    baseline = threading.active_count()
    runner = threading.Thread(target=lambda: tb.run(src, interpreter=interp), daemon=True)
    runner.start()
    deadline = time.monotonic() + 10
    while not lines and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lines and lines[0].startswith("http://127.0.0.1:"), lines
    port = int(lines[0].rsplit(":", 1)[1])
    app = serverlib.RUNNING[-1]
    peak = [threading.active_count()]
    sampling = threading.Event()

    def sample():
        while not sampling.is_set():
            peak[0] = max(peak[0], threading.active_count() - 1)  # not counting this sampler
            time.sleep(0.005)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.monotonic()
    clients = subprocess.Popen([sys.executable, "-c", CLIENTS, str(port), str(n)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    try:
        first = clients.stdout.readline().split()
        assert first == ["open", str(n)], first
        deadline = time.monotonic() + 30
        while len(app.conns) < n and time.monotonic() < deadline:
            time.sleep(0.01)
        held = len(app.conns)
        clients.stdin.write("go\n")
        clients.stdin.flush()
        second = clients.stdout.readline().split()
    finally:
        clients.stdin.close()
        clients.wait(60)
        sampling.set()
        sampler.join()
        serverlib.stop_all()
        runner.join(10)
        if old_limit is not None:
            import resource
            resource.setrlimit(resource.RLIMIT_NOFILE, old_limit)
    took = time.monotonic() - started
    with capsys.disabled():
        print(f"\n[loop] {held} connections open at once, all {second[1]} answered in {took:.1f} s; "
              f"threads: {baseline} before, at most {peak[0]} during"
              + ("" if n == want else f" (only {n} connections: open-file limit)"))
    assert held == n
    assert second == ["ok", str(n)]
    assert peak[0] - baseline <= 5, (baseline, peak)
    assert lines[-1] == "loop ended"


# -- processes ----------------------------------------------------------------------


def test_processes_run_functions_with_what_they_use():
    src = '''import parallel
import math
const OFFSET = 3
let table = {"a": [1, 2]}
fn helper(x) { return math.floor(x) + OFFSET + len(table.a) }
fn work(n) {
  let s = 0
  for i in range(n) { s += i }
  return helper(s)
}
say(parallel.processes(work, [10, 100, 1000], 2))
fn make_adder(k) { return x => x + k }
say(parallel.processes(make_adder(10), [1, 2, 3]))
fn fact(n) { return if n < 2 then 1 else n * fact(n - 1) }
say(parallel.processes(fact, [5, 10]))
say(parallel.processes(x => {"in": x, "bytes": b"ok"}, [nil, [1, "two"]], 1))
say(parallel.processes(x => x, []))'''
    assert run_tb(src) == [
        "[50, 4955, 499505]", "[11, 12, 13]", "[120, 3628800]",
        '[{"in": nil, "bytes": b"ok"}, {"in": [1, "two"], "bytes": b"ok"}]', "[]",
    ]


def test_processes_explain_values_that_cannot_cross():
    err = tb_error('import parallel\nclass Dog { fn init(name) { self.name = name } }\nparallel.processes(x => x, [1, Dog("rex")])')
    assert err.cls.name == "TypeError"
    assert "item 2 is a Dog object, which can't cross to another process" in err.message
    err = tb_error('import parallel\nlet lock = parallel.Lock()\nparallel.processes(fn(x) { lock.hold(fn() {}) }, [1])')
    assert "can't send a Lock object (the name 'lock' in fn) to another process" in err.message
    err = tb_error('import parallel\nclass Dog { fn init(name) { self.name = name } }\nparallel.processes(x => Dog(x), [1])')
    assert "can't send a class" in err.message
    err = tb_error('import parallel\nfn bad(x) {\n  return 1 / x\n}\nparallel.processes(bad, [1, 0])')
    assert err.message == "You can't divide by zero." and err.line == 3 and err.fields["item"] == 2
    err = tb_error('import parallel\nparallel.processes(fn(x) { return x }, [1], 0)')
    assert "at least 1 worker" in err.message
    err = tb_error('import parallel\nparallel.processes(say, [1])')
    assert "needs fn to be a function written in TB" in err.message


def test_shared_queue_crosses_processes():
    src = '''import parallel
let q = parallel.shared_queue()
let results = parallel.processes(fn(n) {
  q.put(n * 10)
  return n
}, [1, 2, 3, 4], 2)
q.close()
let got = []
for x in q { got.push(x) }
say(results, sorted(got), q.is_closed(), q.size())
try { q.put(1) } catch err { say(err.message) }
let q2 = parallel.shared_queue(1)
try { q2.put(parallel.Lock()) } catch err: TypeError { say(err.message) }
try { q2.get(0.05) } catch err: TimeoutError { say(err.message) }
say(q2)'''
    assert run_tb(src) == [
        "[1, 2, 3, 4] [10, 20, 30, 40] true 0",
        "This queue is closed, so nothing more can be put in it.",
        "queue.put(): a Lock object can't go into a shared queue. Use plain values: nil, true/false, numbers, "
        "text, bytes, lists and maps.",
        "queue.get() waited 0.05 seconds and gave up.",
        "<shared queue 0 waiting>",
    ]
