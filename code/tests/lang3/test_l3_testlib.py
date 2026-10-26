"""TB 3.0 Part F5: test.mock, fixture, each, approx, raises(fn, Class), skip, tb test --coverage."""

import subprocess
import sys
from pathlib import Path

import tb

ROOT = Path(__file__).resolve().parents[2]


def run_with_state(src):
    lines = []
    interp = tb.Interpreter(output=lines.append)
    tb.run(src, interpreter=interp)
    return lines, interp.test_state


def test_mock_restores_after_case_and_restore():
    src = '''import test
import math
test.case("mocked", () => {
  test.mock(math, "sqrt", x => 42)
  test.equal(math.sqrt(4), 42)
})
test.equal(math.sqrt(4), 2)
let m = {"a": 1}
test.mock(m, "a", 2)
test.mock(m, "new", 3)
test.equal([m["a"], m["new"]], [2, 3])
test.restore()
test.equal(m, {"a": 1})'''
    lines, state = run_with_state(src)
    assert state == {"passed": 4, "failed": 0, "skipped": 0}
    assert lines == ["  ok   mocked"]


def test_mock_unknown_library_member():
    lines = []
    try:
        tb.run('import test\nimport math\ntest.mock(math, "nope", 1)', output=lines.append)
    except tb.TBError as err:
        assert "nothing called 'nope'" in str(err)
    else:
        raise AssertionError("expected an error")


def test_fixtures_setup_and_teardown():
    src = '''import test
test.fixture("nums", () => [1, 2, 3], xs => say("teardown", len(xs)))
test.fixture("name", () => "Tanisha")
test.case("uses both", (nums, name) => test.equal([len(nums), name], [3, "Tanisha"]))
test.case("fails but tears down", nums => test.equal(len(nums), 4))'''
    lines, state = run_with_state(src)
    assert state == {"passed": 1, "failed": 1, "skipped": 0}
    assert lines[0] == "teardown 3"
    assert lines[1] == "  ok   uses both"
    assert "teardown 3" in lines and lines[-1] == "  FAIL fails but tears down"


def test_each_runs_named_cases():
    src = '''import test
test.each([[1, 2, 3], [2, 2, 5]], (a, b, total) => test.equal(a + b, total))
test.each(["a", "bb"], s => test.true(len(s) > 0), "text")'''
    lines, state = run_with_state(src)
    assert state["passed"] == 3 and state["failed"] == 1
    assert "  ok   case 1: [1, 2, 3]" in lines
    assert "  FAIL case 2: [2, 2, 5]" in lines
    assert '  ok   text 2: "bb"' in lines


def test_approx():
    src = '''import test
test.approx(0.1 + 0.2, 0.3)
test.approx([1.0000001, 2], [1, 2])
test.approx(10, 11, 0.5)
test.approx(10, 10.4, 0.5)'''
    lines, state = run_with_state(src)
    assert state == {"passed": 3, "failed": 1, "skipped": 0}
    assert "expected about 11 (give or take 0.5), but got 10" in lines[0]


def test_raises_with_error_class():
    src = '''import test
import json
test.raises(() => json.parse("{"), ParseError)
test.raises(() => json.parse("{"), ParseError, "valid JSON")
test.raises(() => json.parse("{"), ValueError)
test.raises(() => 1, ValueError)
let e = test.raises(() => int("zz"), "zz")
say(e["kind"])'''
    lines, state = run_with_state(src)
    assert state == {"passed": 3, "failed": 2, "skipped": 0}
    assert "expected a ValueError, but got a ParseError" in lines[0]
    assert "expected a ValueError, but there wasn't one" in lines[1]


def test_skip_in_case_and_file(tmp_path):
    src = '''import test
test.case("later", () => {
  test.skip("not today")
  test.equal(1, 2)
})
test.equal(1, 1)'''
    lines, state = run_with_state(src)
    assert state == {"passed": 1, "failed": 0, "skipped": 1}
    assert lines == ["  skip later (not today)"]
    (tmp_path / "s_test.tb").write_text('import test\ntest.equal(1, 1)\ntest.skip("needs a network")\ntest.equal(1, 2)\n')
    done = subprocess.run([sys.executable, str(ROOT / "tb.py"), "test", str(tmp_path)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout
    assert "skip the rest of this file (needs a network)" in done.stdout
    assert "OK: 1 checks passed, 1 skipped in 1 test file." in done.stdout


def test_coverage_report(tmp_path):
    (tmp_path / "helper.tb").write_text("fn used() {\n  return 1\n}\nfn unused() {\n  return 2\n}\n")
    (tmp_path / "h_test.tb").write_text('import test\nimport "helper.tb"\ntest.equal(helper.used(), 1)\n')
    done = subprocess.run([sys.executable, str(ROOT / "tb.py"), "test", "--coverage", str(tmp_path)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Coverage (lines the tests ran):" in done.stdout
    assert "h_test.tb: 100% (3 of 3 lines)" in done.stdout
    assert "helper.tb: 75% (3 of 4 lines) - missed 5" in done.stdout
