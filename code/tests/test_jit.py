"""The VM's fast tier (tbcore/jit.py): hot code translated to Python must behave
exactly like the VM loop: same output, same errors, lines and tracebacks."""

from __future__ import annotations

import random

import pytest

import tb
from tbcore import vm

PROGRAMS = {
    "loops_and_branches": """
let total = 0
for i in 0..300 {
  if i % 3 == 0 { total += i } else if i % 5 == 0 { total -= 1 } else { continue }
  if total > 5000 { break }
}
let j = 0
while j < 50 { j += 2 }
say(total, j)
""",
    "calls_closures_defaults": """
fn adder(n) { return x => x + n }
fn count(xs, start = 10, ...rest) {
  let c = start
  for x in xs { c += x }
  return [c, len(rest)]
}
let add2 = adder(2)
let acc = []
for i in 0..40 { acc.push(add2(i)) }
say(acc[39], count([1, 2, 3]), count([1], 0, 9, 9))
fn counter() {
  let n = 0
  fn tick() { n += 1; return n }
  return tick
}
let t = counter()
for i in 0..30 { t() }
say(t())
""",
    "methods_and_cache": """
class Animal { fn init(name) { self.name = name } fn speak() { return "..." } }
class Dog extends Animal { fn speak() { return f"{self.name}: woof" } }
class Cat extends Animal { fn speak() { return f"{self.name}: meow" } }
let pets = []
for i in 0..20 { pets.push(if i % 2 == 0 then Dog(f"d{i}") else Cat(f"c{i}")) }
let out = []
for p in pets { out.push(p.speak()) }
say(out[0], out[1], out[19], len(out))
let m = {"has": 1}
say(m.has, {"a": 1}.has("a"), [3, 1, 2].sorted(), "a-b".split("-"))
""",
    "generators_and_text": """
fn evens(n) {
  let i = 0
  while i < n { if i % 2 == 0 { yield i } ; i += 1 }
}
let s = 0
for x in evens(500) { s += x }
let words = []
for i in 0..60 { words.push(f"w{i:03}") }
say(s, words.join(",").len(), words[7], 7 / 2, 2 ** 10, -3 // 2)
""",
    "try_catch_defer_match": """
fn risky(i) {
  if i == 7 { throw ValueError("seven") }
  return i
}
let log = []
fn run() {
  defer { log.push("deferred") }
  let caught = 0
  for i in 0..20 {
    try { risky(i) } catch e: ValueError { caught += 1; log.push(e.message) } finally { log.push("f") }
  }
  return caught
}
say(run(), len(log), log[0], log[-1])
fn kind(v) {
  return match v {
    0 => "zero",
    1..10 => "small",
    [a, b] => f"pair {a}{b}",
    {"k": k} => f"map {k}",
    _ => "other",
  }
}
let kinds = []
for v in [0, 5, [1, 2], {"k": 3}, 99] { kinds.push(kind(v)) }
say(kinds)
""",
    "error_in_hot_function": """
fn inner(a, b) {
  let t = a * 2
  return t / b
}
fn outer(n) {
  let s = 0
  for i in 0..n { s += inner(i, n - i - 1) }
  return s
}
say(outer(5))
""",
    "error_type_in_hot_loop": """
let items = [1, 2, 3, 4, "five", 6]
let total = 0
for x in items { total = total - x }
say(total)
""",
    "too_deep": """
fn down(n) { return down(n + 1) }
down(0)
""",
    "wrong_arity_and_unknown_name": """
fn two(a, b) { return a + b }
let r = 0
for i in 0..30 { r += two(i, 1) }
say(r)
say(len(1, 2))
""",
}


def run(source, monkeypatch, hot):
    """Run with every Code translated at once (hot=True) or never (hot=False)."""
    level = 1 if hot else 1 << 62
    monkeypatch.setattr(vm, "JIT_HOT", level)
    monkeypatch.setattr(vm, "JIT_HOT_LOOPS", level)
    lines = []
    interp = tb.VMInterpreter(output=lines.append, rng=random.Random(0))
    try:
        tb.run(source, interpreter=interp)
        err = None
    except tb.TBError as e:
        err = (str(e), e.line, e.trace_text() if hasattr(e, "trace_text") else "")
    return lines, err, interp


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_translated_code_behaves_like_the_vm_loop(name, monkeypatch):
    source = PROGRAMS[name]
    slow = run(source, monkeypatch, hot=False)[:2]
    fast = run(source, monkeypatch, hot=True)[:2]
    assert fast == slow
    assert slow[0] or slow[1]  # the program did something


def test_constant_conditions_translate_without_python_warnings(monkeypatch):
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        source = 'let n = 0\nwhile 1 { n += 1; if n > 3 { break } }\nif "x" { say(n) }\nif not 0 { say(0) }\nsay(not "")'
        fast = run(source, monkeypatch, hot=True)[:2]
    assert fast == run(source, monkeypatch, hot=False)[:2] == (["4", "false"], None)
    assert not [w for w in caught if issubclass(w.category, SyntaxWarning)]


def test_hot_code_gets_translated_and_cold_code_does_not(monkeypatch):
    monkeypatch.setattr(vm, "JIT_HOT", 16)
    monkeypatch.setattr(vm, "JIT_HOT_LOOPS", 200)
    lines = []
    interp = tb.VMInterpreter(output=lines.append)
    tb.run("fn once() { return 1 }\nfn often(n) { return n + 1 }\n"
           "let t = once()\nfor i in 0..100 { t += often(i) }\nsay(t)", interpreter=interp)
    assert lines == ["5051"]
    often = interp.globals.vars["often"].tmpl._code
    once = interp.globals.vars["once"].tmpl._code
    assert often.fast is not None and once.fast is None
    assert "def tb_often" in often.fast.source


def test_a_loop_in_a_running_function_switches_to_translated_code(monkeypatch):
    # build() is called once; its loop gets hot while it runs
    monkeypatch.setattr(vm, "JIT_HOT", 16)
    monkeypatch.setattr(vm, "JIT_HOT_LOOPS", 200)
    lines = []
    interp = tb.VMInterpreter(output=lines.append)
    tb.run("fn build(n) {\n  let s = 0\n  for i in 0..n { s += i }\n  return s\n}\nsay(build(5000))",
           interpreter=interp)
    assert lines == ["12497500"]
    assert interp.globals.vars["build"].tmpl._code.fast is not None


def attach_mid_loop(monkeypatch, hot):
    level = 1 if hot else 1 << 62
    monkeypatch.setattr(vm, "JIT_HOT", level)
    monkeypatch.setattr(vm, "JIT_HOT_LOOPS", level)
    events = []
    interp = tb.VMInterpreter(output=lambda s: None)

    def hook(event, line, file, env):
        events.append((event, line))

    def attach(line, *args):
        interp.trace = hook  # like a debugger or the playground's time limit
        return None

    interp.builtins.vars["attach"] = tb.Builtin("attach", attach, (0, 0))
    tb.run("let i = 0\nwhile i < 5 {\n  if i == 2 { attach() }\n  i += 1\n}\nsay(i)", interpreter=interp)
    return events


def test_a_debugger_attaching_mid_loop_still_gets_events(monkeypatch):
    fast = attach_mid_loop(monkeypatch, hot=True)
    assert fast and fast == attach_mid_loop(monkeypatch, hot=False)  # loop back-edges report to the hook


def test_frames_made_by_translated_calls_have_the_fields_vm_frames_have(monkeypatch):
    # jit.py writes the VM's simple-call path out by hand (no VMFrame() call); if
    # VMFrame gets a new field, jit.py's inline_call must set it too.
    monkeypatch.setattr(vm, "JIT_HOT", 1)
    monkeypatch.setattr(vm, "JIT_HOT_LOOPS", 1)
    seen = []
    interp = tb.VMInterpreter(output=lambda s: None)
    interp.builtins.vars["grab"] = tb.Builtin("grab", lambda line: seen.append(interp.call_stack[-1]), (0, 0))
    tb.run("fn f(a) { grab(); return a }\nfn g(n) { return f(n) }\ng(1)\ng(2)\ng(3)", interpreter=interp)
    made = seen[-1]
    assert made.fn == "f"
    reference = vm.VMFrame(made.code, [1], made.func)
    reference.entered = True
    reference.back = made.back
    reference.pc = made.code.entry + 1
    assert set(vars(made)) == set(vars(reference))
