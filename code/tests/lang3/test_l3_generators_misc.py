"""TB 3.0 Part A11 (generators), A12 (misc, debugger hook, comments), CLI dispatch."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import tb
from conftest import run_tb, tb_error

ROOT = Path(__file__).resolve().parents[2]


# -- A11 generators -------------------------------------------------------------

def test_generator_basics():
    src = '''fn three() { yield 1; yield 2; yield 3 }
for x in three() { say(x) }
let g = three()
say(type(g), g.next(), g.next(), g.next(), g.next() == done, g.next() == done)'''
    assert run_tb(src) == ["1", "2", "3", "generator 1 2 3 true true"]


def test_generator_helpers():
    src = '''fn nums() { let i = 0
 while true { i += 1
  yield i } }
say(nums().take(3).to_list(), nums().map(x => x * 2).take(2).to_list(), nums().filter(x => x % 2 == 0).take(2).to_list())
say(nums().zip(["a", "b"]).to_list(), nums().take(2).enumerate().to_list())'''
    assert run_tb(src) == ["[1, 2, 3] [2, 4] [2, 4]", '[[1, "a"], [2, "b"]] [[0, 1], [1, 2]]']


def test_generator_with_return_stops():
    src = "fn g() { yield 1\n return\n yield 2 }\nsay(g().to_list())"
    assert run_tb(src) == ["[1]"]


def test_generator_errors_reach_the_caller():
    err = tb_error('fn g() { yield 1\n throw ValueError("bad") }\nfor x in g() { say(x) }')
    assert "bad" in str(err)


def test_yield_outside_function():
    assert "Oops!" in str(tb_error("yield 1"))


def test_lazy_iteration_helpers():
    src = '''say([1, 2].iter().to_list(), {"a": 1}.iter().to_list())
for i, x in ["a", "b"].enumerate() { say(i, x) }
for k, v in {"a": 1, "b": 2} { say(k, v) }
for k in {"a": 1} { say(k) }'''
    assert run_tb(src) == ['[1, 2] [["a", 1]]', "0 a", "1 b", "a 1", "b 2", "a"]


def test_abandoned_generators_do_not_pile_up_threads():
    before = threading.active_count()
    run_tb("fn g() { let i = 0\n while true { i += 1\n yield i } }\nfor n in 0..200 { let x = g()\n x.next() }")
    for _ in range(50):
        if threading.active_count() <= before + 5:
            break
        time.sleep(0.05)
    assert threading.active_count() <= before + 5


# -- A12 misc ---------------------------------------------------------------------

def test_defer_runs_at_return_in_reverse_order():
    src = '''fn f() {
  defer { say("first deferred") }
  defer { say("second deferred") }
  say("body")
  return 1
}
say(f())'''
    assert run_tb(src) == ["body", "second deferred", "first deferred", "1"]


def test_defer_runs_on_error():
    src = 'fn f() { defer { say("closed") }\n throw Error("x") }\ntry { f() } catch e { say("caught") }'
    assert run_tb(src) == ["closed", "caught"]


def test_assert():
    assert run_tb('assert 1 == 1, "fine"\nassert true\nsay("ok")') == ["ok"]
    err = tb_error('assert 1 == 2, "numbers differ"')
    assert "numbers differ" in str(err) and err.kind == "assertion_error"
    assert run_tb('try { assert false } catch e: AssertionError { say(e.kind) }') == ["assertion_error"]


def test_list_repeat():
    assert run_tb("say([1, 2] * 2, 2 * [0], [] * 5)") == ["[1, 2, 1, 2] [0, 0] []"]


def test_print_hint_still_there():
    assert "say" in str(tb_error('print("x")'))


def test_type_annotations_are_accepted_and_ignored():
    src = '''let x: number = 1
fn f(a: text, b: list<number> = []) -> text { return a }
class C { let count: number = 0 }
let y: map<text, number> | nil = nil
let z: fn(number, number) -> number = nil
let w: any = "x"
say(x, f("t"), C().count, y, z, w)'''
    assert run_tb(src) == ["1 t 0 nil nil x"]


def test_annotations_are_kept_on_nodes():
    program = tb.parse("let x: number = 1\nfn f(a: text) -> text { return a }")
    let, fn = program.body
    assert let.annotation is not None
    assert fn.params[0].annotation is not None


def test_comments_survive_on_tokens_and_nodes():
    src = "# lead\n\n# second\n\nlet x = 1  # trailing\nfn f() {\n  # inner\n  return 1\n}\n"
    tokens = tb.tokenize(src)
    assert tokens[0].comments_before == ["# lead", "", "# second", ""]
    program = tb.parse(src)
    first, fn = program.body
    assert first.comments == ["# lead", "", "# second", ""]
    assert first.trailing_comment == "# trailing"
    assert fn.body[0].comments == ["# inner"]
    assert fn.blank_before == 0


def test_blank_lines_before_statements():
    program = tb.parse("let a = 1\n\n\nlet b = 2\n")
    assert program.body[1].blank_before == 2


def test_trace_frames_and_eval():
    src = "let x = 1\nfn f(a) {\n  let b = a + 1\n  return b\n}\nsay(f(x))\n"
    events = []
    interp = tb.Interpreter(output=lambda s: None)

    def hook(event, line, file, env):
        events.append((event, line))
        if event == "line" and line == 4:
            frames = interp.frames()
            events.append(("frames", [(fr["fn"], fr["line"]) for fr in frames], frames[-1]["locals"]["b"]))
            events.append(("eval", interp.eval("a * 10 + b", env)))

    interp.trace = hook
    tb.run(src, interpreter=interp)
    assert ("call", 6) in events
    assert ("frames", [("<main>", 6), ("f", 4)], 2) in events
    assert ("eval", 12) in events
    assert ("return", 4) in events


def test_trace_reports_exceptions_and_file(tmp_path):
    prog = tmp_path / "p.tb"
    prog.write_text('fn bad() { throw Error("x") }\nbad()\n')
    seen = []
    interp = tb.Interpreter(output=lambda s: None)
    interp.trace = lambda event, line, file, env: seen.append((event, line, file))
    try:
        tb.run(prog.read_text(), interpreter=interp, path=str(prog))
    except tb.TBError:
        pass
    assert ("exception", 1, str(prog)) in seen
    assert ("line", 2, str(prog)) in seen


# -- CLI ----------------------------------------------------------------------------

def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(ROOT / "tb.py"), *args], capture_output=True,
                          text=True, timeout=120, cwd=cwd)


def test_unknown_command_is_friendly():
    done = run_cli("frobnicate")
    assert done.returncode == 2
    assert "I don't know the command 'frobnicate'" in done.stderr


def test_tool_dispatch_reaches_tbtools(tmp_path):
    done = run_cli("packages", cwd=tmp_path)
    assert "I don't know the command" not in done.stderr


def test_tb_test_skips_tb_packages(tmp_path):
    (tmp_path / "a_test.tb").write_text('import test\ntest.equal(1, 1)\n')
    pkg = tmp_path / "tb_packages" / "x"
    pkg.mkdir(parents=True)
    (pkg / "x_test.tb").write_text('import test\ntest.equal(1, 2)\n')
    done = run_cli("test", str(tmp_path))
    assert done.returncode == 0, done.stdout
    assert "1 test file" in done.stdout


def test_help_mentions_tools():
    done = run_cli("--help")
    assert "tb fmt" in done.stdout and "tb test --coverage" in done.stdout
    assert run_cli("--version").stdout.strip() == "TB 3.1.0"
