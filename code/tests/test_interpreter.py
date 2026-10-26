import tb
from conftest import run_tb, tb_error


# -- arithmetic ---------------------------------------------------------------

def test_arithmetic():
    assert run_tb("say(1 + 2, 5 - 7, 3 * 4, 7 % 3, -5 + 2)") == ["3 -2 12 1 -3"]


def test_division_gives_whole_numbers_when_exact():
    assert run_tb("say(6 / 3, 7 / 2, 1 / 4)") == ["2 3.5 0.25"]


def test_float_display():
    assert run_tb("say(0.5 + 0.5, 2.5 * 2, 0.1 + 0.2)") == ["1 5 0.30000000000000004"]


def test_negative_remainder_and_floats():
    assert run_tb("say(-7 % 3, 7.5 % 2)") == ["2 1.5"]


def test_huge_numbers_overflow_friendly():
    err = tb_error("let x = 1\nfor i in range(400) { x = x * 1000 }\nsay(x / 3)")
    assert err.line == 2 and "too big" in str(err)


def test_qa_overflow_loop_is_a_tb_error():
    err = tb_error("let n = 1\nfor i in range(5000) { n = n * 10 }\nsay(n)")
    assert err.line == 2 and "That number got too big for me to work with." in str(err)


def test_repeated_squaring_stops_quickly():
    err = tb_error("let n = 2\nwhile true {\n  n = n * n\n}")
    assert err.line == 3 and "too big" in str(err)


def test_big_but_allowed_numbers():
    # 10^300 is fine; display works.
    out = run_tb("let n = 1\nfor i in range(300) { n = n * 10 }\nsay(len(str(n)))")
    assert out == ["301"]


def test_float_overflow_is_friendly():
    assert "too big" in str(tb_error("let x = 1.5\nfor i in range(2000) { x = x * 10 }"))


def test_huge_number_literal():
    assert "too big" in str(tb_error("say(" + "9" * 500 + ")"))
    assert "too big" in str(tb_error("say(" + "9" * 5000 + ")"))
    assert "too big" in str(tb_error("say(" + "9" * 400 + ".5)"))


# -- text ---------------------------------------------------------------------

def test_text_joining_with_plus():
    assert run_tb('say("a" + "b", "n=" + 5, 5 + "!", "x" + true, "v" + nil, "l" + [1, "a"])') == [
        'ab n=5 5! xtrue vnil l[1, "a"]'
    ]


def test_text_repeat():
    assert run_tb('say("ab" * 3)') == ["ababab"]


def test_text_indexing_and_negative_index():
    assert run_tb('let t = "hello"\nsay(t[0], t[-1], t[-5], len(t))') == ["h o h 5"]


def test_text_cannot_be_changed_by_slot():
    assert "one letter at a time" in str(tb_error('let t = "hi"\nt[0] = "y"'))


def test_list_plus_list():
    assert run_tb("say([1] + [2, 3])") == ["[1, 2, 3]"]


# -- comparisons and truthiness ----------------------------------------------

def test_comparisons():
    assert run_tb("say(1 < 2, 2 <= 2, 3 > 4, 4 >= 5, 1 == 1.0, 1 != 2)") == [
        "true true false false true true"
    ]
    assert run_tb('say("apple" < "banana", "a" == "a", "1" == 1)') == ["true true false"]


def test_equality_of_lists_and_bools():
    assert run_tb("say([1, [2]] == [1, [2]], [1] == [2], true == 1, nil == nil, nil == false)") == [
        "true false false true false"
    ]


def test_compare_mismatched_types_is_error():
    assert "can't compare number with text" in str(tb_error('say(1 < "a")'))


def test_truthiness_only_false_and_nil_are_falsy():
    src = """
for v in [0, "", [], false, nil, true, "x"] {
  if v { say("yes") } else { say("no") }
}
"""
    assert run_tb(src) == ["yes", "yes", "yes", "no", "no", "yes", "yes"]


def test_and_or_return_values_and_short_circuit():
    assert run_tb('say(nil or "default", 1 and 2, false and boom, true or boom)') == [
        "default 2 false true"
    ]


def test_not_and_bang():
    assert run_tb("say(not nil, !0, not not true)") == ["true false true"]


# -- variables and scope ------------------------------------------------------

def test_let_and_assign():
    assert run_tb("let x = 1\nx = x + 1\nsay(x)") == ["2"]


def test_assign_to_unknown_name():
    err = tb_error("y = 3")
    assert "'y' doesn't exist yet" in str(err) and "let y" in str(err)


def test_block_scope_and_outer_assignment():
    src = "let x = 1\nif true {\n  let y = 2\n  x = 10\n}\nsay(x)\nsay(y)"
    err = tb_error(src)
    assert err.line == 7 and "'y'" in str(err)


def test_shadowing_in_block():
    assert run_tb("let x = 1\nif true { let x = 2\nsay(x) }\nsay(x)") == ["2", "1"]


# -- lists --------------------------------------------------------------------

def test_lists_index_and_assign():
    src = "let xs = [10, 20, 30]\nxs[1] = 99\nxs[-1] = 7\nsay(xs, xs[0], xs[-2], len(xs))"
    assert run_tb(src) == ["[10, 99, 7] 10 99 3"]


def test_float_index_whole_number_ok():
    assert run_tb("let xs = [1, 2]\nsay(xs[1.0])") == ["2"]


def test_nested_list_display():
    assert run_tb('say([1, "a", [true, nil]], [])') == ['[1, "a", [true, nil]] []']


def test_list_containing_itself_displays():
    assert run_tb("let a = [1]\npush(a, a)\nsay(a)") == ["[1, [...]]"]


def test_lists_are_shared_references():
    assert run_tb("let a = [1]\nlet b = a\npush(b, 2)\nsay(a)") == ["[1, 2]"]


# -- control flow --------------------------------------------------------------

def test_if_else_if_else():
    src = """
fn grade(n) {
  if n >= 90 { return "A" } else if n >= 80 { return "B" } else { return "C" }
}
say(grade(95), grade(85), grade(10))
"""
    assert run_tb(src) == ["A B C"]


def test_if_without_else_does_nothing():
    assert run_tb("if false { say(1) }\nsay(2)") == ["2"]


def test_while_loop():
    assert run_tb("let i = 0\nwhile i < 3 {\n  say(i)\n  i = i + 1\n}") == ["0", "1", "2"]


def test_for_over_list_and_text():
    assert run_tb("for x in [1, 2] { say(x) }") == ["1", "2"]
    assert run_tb('for c in "hey" { say(c) }') == ["h", "e", "y"]


def test_for_over_range():
    assert run_tb("let total = 0\nfor i in range(1, 11) { total = total + i }\nsay(total)") == ["55"]


def test_for_loop_copy_allows_push_during_loop():
    assert run_tb("let xs = [1, 2]\nfor x in xs { push(xs, x) }\nsay(xs)") == ["[1, 2, 1, 2]"]


def test_for_over_number_is_error():
    err = tb_error("for x in 5 { }")
    assert "needs a list or text, not number" in str(err)


def test_return_inside_function_inside_loop_at_top_level():
    assert run_tb("for i in [1] {\n  let f = fn() { return i }\n  say(f())\n}") == ["1"]


# -- functions, closures, recursion --------------------------------------------

def test_function_without_return_gives_nil():
    assert run_tb("fn f() { let x = 1 }\nsay(f())") == ["nil"]


def test_return_from_inside_loops():
    src = """
fn find(xs, v) {
  let i = 0
  while i < len(xs) {
    for x in [xs[i]] { if x == v { return i } }
    i = i + 1
  }
  return -1
}
say(find([5, 6, 7], 7), find([1], 9))
"""
    assert run_tb(src) == ["2 -1"]


def test_closure_counter():
    src = """
fn make_counter() {
  let n = 0
  return fn() {
    n = n + 1
    return n
  }
}
let a = make_counter()
let b = make_counter()
a()
a()
say(a(), b())
"""
    assert run_tb(src) == ["3 1"]


def test_closure_captures_loop_scope():
    src = """
let fs = []
for i in [1, 2, 3] { push(fs, fn() { return i * 10 }) }
say(fs[0](), fs[2]())
"""
    assert run_tb(src) == ["10 30"]


def test_recursion_factorial_and_fib():
    src = """
fn fact(n) { if n <= 1 { return 1 } return n * fact(n - 1) }
fn fib(n) { if n < 2 { return n } return fib(n - 1) + fib(n - 2) }
say(fact(10), fact(25), fib(20))
"""
    assert run_tb(src) == ["3628800 15511210043330985984000000 6765"]


def test_deep_recursion_works():
    src = """
fn countdown(n) {
  if n == 0 { return "liftoff" }
  return countdown(n - 1)
}
fn sum(n) { if n == 0 { return 0 } return n + sum(n - 1) }
say(countdown(1000), sum(2500))
"""
    assert run_tb(src) == ["liftoff 3126250"]


def test_mutual_recursion():
    src = """
fn is_even(n) { if n == 0 { return true } return is_odd(n - 1) }
fn is_odd(n) { if n == 0 { return false } return is_even(n - 1) }
say(is_even(1000), is_odd(7))
"""
    assert run_tb(src) == ["true true"]


def test_infinite_recursion_is_friendly():
    err = tb_error("fn forever(n) {\n  return forever(n + 1)\n}\nforever(0)")
    assert err.line == 2
    assert "'forever' called itself too many times" in str(err)


def test_infinite_recursion_anonymous():
    err = tb_error("let f = fn() { return f() }\nf()")
    assert "This function called itself too many times" in str(err)


def test_depth_counter_recovers_after_error():
    interp = tb.Interpreter(output=lambda s: None)
    for _ in range(2):
        try:
            tb.run("fn f() { return f() }\nf()", interpreter=interp)
        except tb.TBError:
            pass
        assert interp.depth == 0
    out = []
    interp.output = out.append
    tb.run("fn g(n) { if n == 0 { return 0 } return g(n - 1) }\nsay(g(2000))", interpreter=interp)
    assert out == ["0"]


def test_recursion_limit_is_restored():
    import sys
    before = sys.getrecursionlimit()
    run_tb("say(1)")
    tb_error("fn f() { return f() }\nf()")
    assert sys.getrecursionlimit() == before


def test_functions_display():
    assert run_tb("fn hi() { }\nsay(hi, fn() { }, say)") == ["<fn hi> <fn anonymous> <built-in fn say>"]


def test_run_returns_interpreter_with_globals():
    interp = tb.run("let x = 41 + 1", output=lambda s: None)
    assert isinstance(interp, tb.Interpreter)
    assert interp.globals.vars["x"] == 42


def test_run_default_output_prints(capsys):
    tb.run('say("printed")')
    assert capsys.readouterr().out == "printed\n"


def test_text_repeat_is_symmetric_and_whole():
    assert run_tb('say(3 * "ab", "ab" * 2.0, "x" * 0 == "")') == ["ababab abab true"]


import pytest  # noqa: E402


@pytest.mark.parametrize("expr", ['"ab" * 2.5', '"ab" * -1', '2.5 * "ab"', '"ab" * "c"', '"ab" * true'])
def test_text_repeat_needs_whole_non_negative_number(expr):
    err = tb_error(f"say({expr})")
    assert "whole number of times" in str(err)


def test_text_repeat_too_long():
    assert "too long" in str(tb_error('say("abc" * 1000000000)'))
