import tb
from conftest import run_tb, tb_error


def run_with_state(src):
    lines = []
    interp = tb.Interpreter(output=lines.append)
    tb.run(src, interpreter=interp)
    return lines, interp.test_state


def test_passing_checks_are_quiet_and_counted():
    lines, state = run_with_state('import test\ntest.equal(1 + 1, 2)\ntest.equal({"a": [1]}, {"a": [1]})\ntest.not_equal(1, 2)\ntest.true(1)\ntest.false(nil)\nsay(test.results())')
    assert lines == ['{"passed": 5, "failed": 0, "skipped": 0}']
    assert state == {"passed": 5, "failed": 0, "skipped": 0}


def test_failures_are_reported_with_line_and_message():
    lines, state = run_with_state('import test\ntest.equal(2 * 2, 5, "maths")\ntest.true(nil)\ntest.false(0)\ntest.not_equal("a", "a")\nsay(test.equal(1, 2))')
    assert lines == [
        "  FAIL on line 2: expected 5, but got 4 (maths)",
        "  FAIL on line 3: expected something true, but got nil",
        "  FAIL on line 4: expected false or nil, but got 0",
        '  FAIL on line 5: expected something other than "a"',
        "  FAIL on line 6: expected 2, but got 1",
        "false",
    ]
    assert state == {"passed": 0, "failed": 5, "skipped": 0}


def test_raises():
    lines, state = run_with_state(
        'import test\nlet e = test.raises(fn() { throw {"message": "bad thing", "code": 4} })\nsay(e.code)\n'
        'test.raises(fn() { return 1 })\n'
        'test.raises(fn() { say(1 / 0) }, "divide by zero")\n'
        'test.raises(fn() { say(1 / 0) }, "something else")'
    )
    assert lines == [
        "4",
        "  FAIL on line 4: expected an error, but there wasn't one",
        '  FAIL on line 6: expected an error about "something else", but got: You can\'t divide by zero.',
    ]
    assert state == {"passed": 2, "failed": 2, "skipped": 0}


def test_case_runs_each_and_keeps_going():
    lines, state = run_with_state(
        'import test\ntest.case("adds", fn() { test.equal(1 + 1, 2) })\n'
        'test.case("breaks", fn() {\n  let x = nope\n})\n'
        'test.case("fails", fn() { test.equal(1, 2) })\nsay("after")'
    )
    assert lines == [
        "  ok   adds",
        "  FAIL on line 4: 'breaks' stopped with an error: I don't know what 'nope' is.",
        "  FAIL breaks",
        "  FAIL on line 6: expected 2, but got 1",
        "  FAIL fails",
        "after",
    ]
    assert state == {"passed": 1, "failed": 2, "skipped": 0}


def test_argument_errors():
    assert "test.raises() needs fn to be a function, not a number." in str(tb_error("import test\ntest.raises(1)"))
    assert "test.equal() needs 2 to 3 inputs, but got 1." in str(tb_error("import test\ntest.equal(1)"))
    assert "true() needs 1 to 2 inputs" in str(tb_error("import test\ntest.true()"))


def test_runs_without_tb_test():
    assert run_tb("import test\ntest.equal(1, 2)") == ["  FAIL on line 2: expected 2, but got 1"]
