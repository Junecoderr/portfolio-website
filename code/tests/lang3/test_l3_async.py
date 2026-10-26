"""TB 3.0 Part C: async fn / await (tasks on threads, see tbcore/tasks.py)."""

import time

import tb
from conftest import run_tb, tb_error
from tbcore import tasks


def test_async_fn_returns_a_task_and_await_gives_the_result():
    src = '''async fn double(x) {
  return x * 2
}
let t = double(21)
say(type(t), t.done() or true)
say(await t, await t, t.wait(), t.done())
say(await 5, await "plain", await nil)'''
    assert run_tb(src) == ["Task true", "42 42 42 true", "5 plain nil"]


def test_async_runs_at_the_same_time():
    src = '''import time
async fn nap(name) {
  time.sleep(0.2)
  return name
}
let start = time.timestamp()
let a = nap("a")
let b = nap("b")
let c = nap("c")
say(await a, await b, await c)
say(time.timestamp() - start < 0.5)'''
    assert run_tb(src) == ["a b c", "true"]


def test_async_params_defaults_and_named_inputs():
    src = '''async fn greet(name, punct = "!", ...rest) {
  return f"hi {name}{punct} {len(rest)}"
}
say(await greet("T"), await greet("T", punct = "?"), await greet("T", ".", 1, 2))'''
    assert run_tb(src) == ["hi T! 0 hi T? 0 hi T. 2"]
    assert "greet() needs" in str(tb_error('async fn greet(name) { }\ngreet()'))  # checked at the call


def test_async_methods_and_async_fn_values():
    src = '''class Api {
  let base = "x"
  async fn fetch(path) { return self.base + path }
  static async fn ping() { return "pong" }
}
let api = Api()
say(await api.fetch("/a"), await Api.ping())
let f = async fn(n) { return n + 1 }
say(await f(1), type(f(1)))
export async fn shared() { return 1 }
say(await shared())'''
    assert run_tb(src) == ["x/a pong", "2 Task", "1"]


def test_await_inside_async_and_nested_tasks():
    src = '''async fn leaf(x) { return x * 10 }
async fn branch(x) {
  let a = leaf(x)
  let b = leaf(x + 1)
  return await a + await b
}
async fn root() {
  let parts = [branch(1), branch(3)]
  let total = 0
  for p in parts { total += await p }
  return total
}
say(await root())'''
    assert run_tb(src) == ["100"]


def test_async_errors_keep_their_class_message_and_line():
    src = '''async fn load(path) {
  throw FileError(f"no {path}")
}
async fn add() {
  return 1 + nil
}
try { await load("a.txt") } catch err: FileError { say(err.message, err.line) }
try { await add() } catch err: TypeError { say("type:", err.line) }
let t = add()
try { t.wait() } catch err { say(type(err), t.status()) }'''
    assert run_tb(src) == ["no a.txt 2", "type: 5", "TypeError failed"]
    err = tb_error('async fn f() {\n  throw ValueError("bad")\n}\nawait f()')
    assert err.message == "bad" and err.line == 2 and err.cls.name == "ValueError"
    assert "f()" in err.trace_text()


def test_unawaited_async_error_does_not_stop_the_program():
    assert run_tb('async fn f() { throw "boom" }\nlet t = f()\nsay("still here")') == ["still here"]


def test_await_with_timeout_and_cancel():
    src = '''import parallel
async fn forever() {
  while true { parallel.sleep(0.01) }
}
let t = forever()
try { t.wait(0.05) } catch err: TimeoutError { say("timeout") }
say(t.cancel())
try { await t } catch err { say(err.message, t.status()) }'''
    assert run_tb(src) == ["timeout", "true", "The task was cancelled. cancelled"]


def test_await_on_objects_with_wait():
    src = '''class Box {
  let v = 3
  fn wait() { return self.v * 2 }
}
say(await Box(), await [1, 2], await {"a": 1})'''
    assert run_tb(src) == ['6 [1, 2] {"a": 1}']


def test_async_recursion_depth_is_per_task():
    src = '''fn deep(n) {
  if n == 0 { return 0 }
  return 1 + deep(n - 1)
}
async fn go(n) { return deep(n) }
let tasks = [go(2500), go(2500), go(2500)]
let total = 0
for t in tasks { total += await t }
say(total)'''
    assert run_tb(src) == ["7500"]


def test_async_shares_globals():
    src = '''let log = []
async fn note(x) { log.push(x) }
let ts = []
for i in range(20) { ts.push(note(i)) }
for t in ts { await t }
say(len(log), sorted(log) == range(20))'''
    assert run_tb(src) == ["20 true"]


def test_async_parse_rules():
    assert "An async fn can't use yield" in str(tb_error("async fn g() { yield 1 }"))
    assert "An async fn can't use yield" in str(tb_error("let g = async fn() { yield 1 }"))
    assert "'async' goes right before 'fn'" in str(tb_error("let x = async 5"))
    assert "'async' goes right before 'fn'" in str(tb_error("class A { async let x = 1 }"))


def test_async_in_the_browser_runs_at_once(monkeypatch):
    monkeypatch.setattr(tasks, "THREADS_OK", False)
    src = '''async fn f(x) { return x + 1 }
let t = f(1)
say(t.done(), await t)
async fn bad() { throw "nope" }
try { await bad() } catch err { say(err.message) }'''
    assert run_tb(src) == ["true 2", "nope"]


def test_await_works_everywhere_in_the_browser_on_plain_values(monkeypatch):
    monkeypatch.setattr(tasks, "THREADS_OK", False)
    assert run_tb("say(await 1 + 1)") == ["2"]


def test_interpreter_views_are_independent():
    interp = tb.Interpreter(output=lambda s: None)
    tb.run("let x = 1", interpreter=interp)
    view = interp.fork()
    assert view.globals is interp.globals and view.modules is interp.modules
    assert view.call_stack is not interp.call_stack and view.depth == 0
    assert view._exec[tb.A.ExprStmt].__self__ is view if hasattr(tb, "A") else True
    assert interp.current() is interp
