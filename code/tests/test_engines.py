"""The bytecode VM and the tree-walker must behave the same.

Runs a corpus of programs under both engines and compares what they print and
how they fail (message, line, file, error class, the "in f() ← g()" trace):

* every program in examples/ (with its .in answers) and welcome.tb,
* the command-line apps in apps/ that print deterministic output,
* every TB snippet in tests/lang3/ (pulled out of the run_tb / tb_error
  calls with Python's ast module),
* a set of scoping, closure, error and generator corner cases below.
"""

from __future__ import annotations

import ast
import os
import random
import re
import shutil
from pathlib import Path

import pytest

import tb
from tbcore.interp import Interpreter as TreeInterpreter
from tbcore.vm import VMInterpreter

ROOT = Path(__file__).resolve().parent.parent
ENGINES = {"tree": TreeInterpreter, "vm": VMInterpreter}


def outcome(engine: str, source: str, *, inputs=None, path=None, args=None, cwd=None):
    """What running source on an engine shows: output lines, then the error."""
    lines: list[str] = []
    answers = list(inputs or [])

    def ask(prompt):
        if not answers:
            raise EOFError
        return answers.pop(0)

    interp = ENGINES[engine](output=lines.append, input_fn=ask, rng=random.Random(7))
    old = os.getcwd()
    if cwd is not None:
        os.chdir(cwd)
    try:
        tb.run(source, interpreter=interp, path=path, args=args)
        ending = None
    except tb.TBError as err:
        ending = ("error", str(err), err.kind, err.trace_text(),
                  [(f.get("fn"), f.get("line")) for f in (err.stack or [])])
    except tb.TBExit as stop:
        ending = ("exit", stop.code)
    finally:
        os.chdir(old)
    # care() picks a random message; the engines use the same seed, but keep it robust
    lines = [re.sub(r"^♥ .*", "♥", line) for line in lines]
    return lines, ending


def same(source: str, **kw):
    tree = outcome("tree", source, **kw)
    vm = outcome("vm", source, **kw)
    assert vm == tree
    return vm


# -- examples and apps ------------------------------------------------------------

EXAMPLES = sorted((ROOT / "examples").glob("*.tb")) + [ROOT / "welcome.tb"]


@pytest.mark.parametrize("program", EXAMPLES, ids=lambda p: p.name)
def test_examples_match(program):
    answers = program.with_suffix(".in")
    inputs = answers.read_text(encoding="utf-8").splitlines() if answers.exists() else []
    same(program.read_text(encoding="utf-8"), inputs=inputs, path=str(program))


DATA = ROOT / "apps" / "data"
APP_RUNS = [
    ("log-triage", []),
    ("log-triage", [str(DATA / "access.log")]),
    ("log-triage", ["--bogus"]),
    ("brute-force-hunter", [str(DATA / "auth.log"), "--attempts", "3", "--within", "7200"]),
    ("brute-force-hunter", ["missing.log"]),
    ("ioc-extractor", [str(DATA / "phishing_email.txt"), "--json", "--raw"]),
    ("ioc-extractor", ["--nope"]),
    ("siem-forwarder", ["--limit", "3"]),
    ("siem-forwarder", ["--alerts", "--format", "hec"]),
    ("pcap-triage", []),
    ("evtx-triage", []),
    ("file-inspector", [str(DATA / "phishing_email.txt")]),
    ("sigma-runner", []),
    ("hash-checker", [str(DATA / "auth.log")]),
    ("ioc-feed", ["--by-type", "--query", "splunk", str(DATA / "auth.log")]),
]


@pytest.mark.parametrize("name,args", APP_RUNS, ids=lambda v: v if isinstance(v, str) else " ".join(v)[-30:])
def test_apps_match(name, args, tmp_path):
    path = ROOT / "apps" / name / "main.tb"
    if not path.exists():
        pytest.skip(f"no app {name}")
    source = path.read_text(encoding="utf-8")
    runs = []
    for engine in ("tree", "vm"):
        work = tmp_path / engine
        work.mkdir()
        runs.append(outcome(engine, source, path=str(path), args=list(args), cwd=work))
    assert runs[1] == runs[0]
    # files the app wrote must match too
    tree_files = sorted(p.relative_to(tmp_path / "tree") for p in (tmp_path / "tree").rglob("*") if p.is_file())
    vm_files = sorted(p.relative_to(tmp_path / "vm") for p in (tmp_path / "vm").rglob("*") if p.is_file())
    assert vm_files == tree_files
    shutil.rmtree(tmp_path, ignore_errors=True)


# -- snippets from tests/lang3 -------------------------------------------------------------

def lang3_snippets() -> list[tuple[str, str]]:
    found = []
    for path in sorted((ROOT / "tests" / "lang3").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            strings = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    strings[node.targets[0].id] = node.value.value
            count = 0
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id in ("run_tb", "tb_error") and node.args:
                    first = node.args[0]
                    source = None
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        source = first.value
                    elif isinstance(first, ast.Name):
                        source = strings.get(first.id)
                    if source is None:
                        continue
                    # timing- and thread-dependent snippets can't print the same twice
                    if any(word in source for word in ("time.", "parallel.", "sleep", "timestamp")):
                        continue
                    count += 1
                    found.append((f"{path.stem}::{fn.name}#{count}", source))
    return found


SNIPPETS = lang3_snippets()


def test_snippet_corpus_is_big():
    assert len(SNIPPETS) > 100


@pytest.mark.parametrize("source", [s for _n, s in SNIPPETS], ids=[n for n, _s in SNIPPETS])
def test_lang3_snippets_match(source):
    same(source)


# -- corner cases ----------------------------------------------------------------------------

CORNERS = {
    "const_captured": '''fn outer() {
  const LIMIT = 3
  let f = fn() { let g = fn() { LIMIT += 1 }
    g() }
  try { f() } catch e { say(e.message, e.line) }
  let h = fn() { LIMIT = 4 }
  h()
  say(LIMIT)
}
outer()''',
    "const_global_and_redeclare": '''const A = 1
let A = 2
A = 3
say(A)
const B = [1]
B.push(2)
say(B)
B = 5''',
    "use_before_let_goes_outward": '''let x = "global"
fn f() {
  say(x)
  let x = "local"
  say(x)
  for i in 0..2 { say(x); let x = i; say(x) }
}
f()''',
    "closure_sees_later_let": '''fn outer() {
  fn early() { return v }
  let before = early()
  let v = 5
  return [before, early()]
}
let v = "global v"
say(outer())''',
    "mutual_recursion_in_function": '''fn main() {
  fn even(n) { if n == 0 { return true } return odd(n - 1) }
  fn odd(n) { if n == 0 { return false } return even(n - 1) }
  say(even(10), odd(7), even(3))
}
main()''',
    "loop_closures_capture_each_iteration": '''let fs = []
for i in 0..3 { fs.push(() => i) }
let gs = []
let j = 0
while j < 3 { let k = j * 10
  gs.push(() => k)
  j += 1 }
say(fs.map(f => f()), gs.map(g => g()))''',
    "shared_counter_closure": '''fn counter() { let n = 0
  return [() => { n += 1
    return n }, () => n] }
let [inc, get] = counter()
inc(); inc()
say(get(), inc())''',
    "finally_with_return_break_continue": '''fn f() {
  for i in 0..5 {
    try {
      if i == 1 { continue }
      if i == 3 { break }
      say("body", i)
    } finally { say("finally", i) }
  }
  try { return "r" } finally { say("last finally") }
}
say(f())
fn g() { try { throw Error("boom") } finally { return "swallowed" } }
say(g())''',
    "nested_try_and_rethrow": '''fn risky(n) { if n > 1 { throw ValueError(f"too big {n}") } return n }
for n in 0..4 {
  try {
    try { say(risky(n)) } catch e: FileError { say("never") } finally { say("inner", n) }
  } catch e: ValueError { say("caught", e.message, e.kind, e.line, len(e.stack)) }
}''',
    "defer_order_and_errors": '''fn f(x) {
  defer { say("d1", x) }
  if x { defer { say("d2") } }
  if x == 2 { throw Error("x2") }
  return x
}
say(f(0)); say(f(1))
try { f(2) } catch e { say(e.message, e.stack[0].fn) }''',
    "generators_suspended_frames": '''fn gen(n) { let i = 0
  while i < n { try { yield i } finally { }
    i += 1 }
  return "ignored" }
let g = gen(3)
say(g.next(), g.next(), g.next(), g.next() == done)
fn pairs() { for x in gen(2) { for y in gen(2) { yield [x, y] } } }
say(pairs().to_list())
fn bad() { yield 1
  throw Error("in gen") }
try { for v in bad() { say(v) } } catch e { say(e.message, e.line, e.stack.map(f => f.fn)) }''',
    "generator_params_checked_at_call": '''fn g(a, b = a * 2) { yield a; yield b }
say(g(1).to_list(), g(1, 5).to_list())
g()''',
    "match_scopes_and_or_patterns": '''let x = "outer"
fn show(v) {
  return match v {
    [a] or [a, _] => f"list {a}"
    {"k": k} => f"map {k}"
    n if n is number and n > 10 => "big"
    1..=5 => "small"
    _ => x
  }
}
say(show([1]), show([2, 3]), show({"k": 9}), show(11), show(3), show("?"))
say(match 7 { 1 => "one" })''',
    "classes_super_static_operators": '''class V {
  static let made = 0
  fn init(x, y) { self.x = x; self.y = y; V.made += 1 }
  fn add(o) { return V(self.x + o.x, self.y + o.y) }
  fn equals(o) { return self.x == o.x and self.y == o.y }
  fn text() { return f"V({self.x}, {self.y})" }
  static fn zero() { return V(0, 0) }
}
class W extends V {
  fn init(x) { super.init(x, x) }
  fn text() { return "W:" + super.text() }
  fn later() { return () => super.text() }
}
let w = W(2)
say(V(1, 2) + V(3, 4), V.zero() == V(0, 0), w, w.later()(), V.made, w is V, type(w))''',
    "kwargs_defaults_rest_patterns": '''fn f(a, b = a + 1, ...rest) { return [a, b, rest] }
fn g([x, y], {name, n = 2}) { return f"{x}{y}{name}{n}" }
fn h(a, **opts) { return [a, opts] }
say(f(1), f(1, 2, 3, 4), f(b = 9, a = 0), g([1, 2], {"name": "t"}), h(1, z = 2))
for bad in [() => f(), () => f(1, a = 2), () => g([1], {"name": 1}), () => h(1, 2)] {
  try { bad() } catch e { say(e.message) }
}''',
    "unknown_names_and_hints": '''let colour = 1
fn f(value) { let total = 0
  return totl + value }
try { f(1) } catch e { say(e.message) }
try { say(colur) } catch e { say(e.message) }
try { let m = {nme: 1} } catch e { say(e.message) }
try { undefined_thing = 3 } catch e { say(e.message) }
say(self)''',
    "deep_recursion_limit": '''fn down(n) { if n == 0 { return 0 } return down(n - 1) }
say(down(2500))
down(4000)''',
    "spread_slices_ranges_bytes_fstrings": '''let xs = [1, 2, 3, 4, 5]
say([...xs, 6], xs[1:3], xs[::-1], xs[-2:], "hello"[1:4], (0..10).step(3).to_list())
say(f"{3.14159:.2f} {42:08} {255:x} {0.25:.1%} {"hi"!r} {xs[0] + 1:>4}|")
say(b"ab" + b"\\x01", (1..=3).to_list(), 2 ** 10, 7 // 2, -7 % 3, 1 / 4, 1e3)
let big = 2 ** 1000
say(len(str(big)))
say(big * big * big * big)''',
    "if_then_else_and_chains": '''let n = 5
say(if n > 3 then "big" else "small", 0 < n <= 5, 1 < n < 3, n in [5], "a" not in "xyz")
say(not n, n == 5 and n != 4 or false, nil or "default")''',
    "multi_assign_and_destructuring_errors": '''let a = 1
let b = 2
a, b = b, a
say(a, b)
let m = {"k": [1, 2]}
let i = 0
m["k"][i], i = 9, 1
say(m, i)
try { let [p, q] = [1, 2, 3] } catch e { say(e.message) }
try { a, b = [1] } catch e { say(e.message) }
try { let {zz} = {"a": 1} } catch e { say(e.message) }''',
    "enums_and_is": '''enum Level { low, mid, high }
fn rate(l) { return match l { Level.low => 1, Level.high => 3, _ => 2 } }
say(Level.values().map(rate), Level.mid, Level.mid.index, Level.high is Level, 3 is number, "x" is text)''',
    "errors_inside_methods_have_files_and_stacks": '''class Box { fn open() { return self.missing } }
fn use_box() { return Box().open() }
use_box()''',
    "operator_errors": '''for thing in [() => "a" - 1, () => -"a", () => nil.x, () => [1][5], () => {"a": 1}["b"], () => 1 / 0, () => 5()] {
  try { thing() } catch e { say(e.kind, e.message) }
}''',
}


@pytest.mark.parametrize("name", sorted(CORNERS))
def test_corner_cases_match(name):
    same(CORNERS[name])


def test_const_captured_by_closures_is_refused_on_both_engines():
    lines, ending = same(CORNERS["const_captured"])
    assert lines[0] == "'LIMIT' is a constant (made with const), so it can't be changed. 3"
    assert ending[0] == "error" and "is a constant" in ending[1]


def test_vm_is_the_default_engine_and_tree_is_available():
    assert issubclass(tb.engine_class(), VMInterpreter) or os.environ.get("TB_ENGINE") == "tree"
    assert tb.engine_class("tree") is TreeInterpreter
    assert tb.engine_class("vm") is VMInterpreter


def test_engine_flag_on_the_command_line(tmp_path, capsys):
    prog = tmp_path / "p.tb"
    prog.write_text('say("hi")\n')
    saved = tb.Interpreter
    try:
        assert tb.main(["--engine", "tree", str(prog)]) == 0
        assert tb.Interpreter is TreeInterpreter
        assert tb.main(["--engine=vm", str(prog)]) == 0
        assert tb.Interpreter is VMInterpreter
        assert tb.main(["--engine", "jit", str(prog)]) == 2
    finally:
        tb.Interpreter = saved
        os.environ.pop("TB_ENGINE", None)
    out = capsys.readouterr()
    assert out.out.splitlines() == ["hi", "hi"]
    assert "I don't know the engine 'jit'" in out.err


def test_trace_events_match():
    src = "let x = 1\nfn f(a) {\n  let b = a + 1\n  if b > 1 { return b }\n  return 0\n}\nsay(f(x))\nfor i in 0..2 { f(i) }\n"
    seen = {}
    for engine, cls in ENGINES.items():
        events = []
        interp = cls(output=lambda s: None)
        interp.trace = lambda event, line, file, env: events.append((event, line))
        tb.run(src, interpreter=interp)
        seen[engine] = events
    assert seen["vm"] == seen["tree"]
