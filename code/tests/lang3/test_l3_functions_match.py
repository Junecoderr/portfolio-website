"""TB 3.0 Part A5 (destructuring, spread), A6 (functions), A7 (ranges, slices), A8 (if-then, match)."""

import pytest

from conftest import run_tb, tb_error


# -- A5 destructuring and spread ----------------------------------------------

def test_list_destructuring_with_rest():
    assert run_tb("let [a, b, ...rest] = [1, 2, 3, 4]\nsay(a, b, rest)") == ["1 2 [3, 4]"]
    assert run_tb("let [a, ...rest] = [1]\nsay(a, rest)") == ["1 []"]


def test_map_destructuring_with_rename_and_default():
    src = 'let {name, ip: address, port = 22} = {"name": "web", "ip": "192.0.2.1"}\nsay(name, address, port)'
    assert run_tb(src) == ["web 192.0.2.1 22"]


def test_nested_destructuring():
    src = 'let {host: {name, ports: [first, ...others]}} = {"host": {"name": "h", "ports": [22, 80, 443]}}\nsay(name, first, others)'
    assert run_tb(src) == ["h 22 [80, 443]"]


def test_destructuring_in_for_and_params():
    src = '''for [k, v] in {"a": 1, "b": 2}.items() { say(k, v) }
fn f([p, q], {x}) { return p + q + x }
say(f([1, 2], {"x": 3}))'''
    assert run_tb(src) == ["a 1", "b 2", "6"]


def test_destructuring_errors():
    assert "Oops!" in str(tb_error("let [a, b] = [1]"))
    assert "Oops!" in str(tb_error("let [a] = 5"))
    assert "name" in str(tb_error('let {name} = {"other": 1}'))


def test_multiple_return_and_assignment():
    src = "fn two() { return 1, 2 }\nlet a, b = two()\nsay(a, b, two())\na, b = b, a\nsay(a, b)"
    assert run_tb(src) == ["1 2 [1, 2]", "2 1"]


def test_spread_in_calls_lists_and_maps():
    src = '''fn add3(a, b, c) { return a + b + c }
let xs = [1, 2, 3]
say(add3(...xs), [0, ...xs, ...xs], {...{"a": 1, "k": 0}, "k": 1})
say(add3(1, ...[2, 3]))'''
    assert run_tb(src) == ['6 [0, 1, 2, 3, 1, 2, 3] {"a": 1, "k": 1}', "6"]


def test_spread_of_wrong_type():
    assert "Oops!" in str(tb_error("fn f(a) { return a }\nf(...5)"))
    assert "Oops!" in str(tb_error("say({...[1]})"))


# -- A6 functions -------------------------------------------------------------

def test_defaults_and_keyword_arguments():
    src = '''fn f(a, b = 2, c = a + 1) { return [a, b, c] }
say(f(1), f(1, 5), f(1, c = 9), f(b = 3, a = 0))'''
    assert run_tb(src) == ["[1, 2, 2] [1, 5, 2] [1, 2, 9] [0, 3, 1]"]


def test_varargs_and_keyword_map():
    src = '''fn g(first, ...rest) { return [first, rest] }
fn k(a, **opts) { return opts }
say(g(1), g(1, 2, 3), k(1), k(1, color = "red", size = 2))'''
    assert run_tb(src) == ['[1, []] [1, [2, 3]] {} {"color": "red", "size": 2}']


@pytest.mark.parametrize("src, part", [
    ("fn f(a) { return a }\nf(1, 2)", "f()"),
    ("fn f(a) { return a }\nf()", "f()"),
    ("fn f(a) { return a }\nf(b = 1)", "b"),
    ("fn f(a) { return a }\nf(1, a = 2)", "a"),
])
def test_argument_errors(src, part):
    assert part in str(tb_error(src))


def test_lambdas():
    src = '''let dbl = x => x * 2
let add = (a, b) => a + b
let one = () => 1
let blk = x => {
  let y = x + 1
  return y * 10
}
say(dbl(4), add(1, 2), one(), blk(1), [1, 2, 3].map(x => x * x))'''
    assert run_tb(src) == ["8 3 1 20 [1, 4, 9]"]


def test_function_properties_call_and_bind():
    src = '''fn f(a, b) { return a - b }
let sub10 = f.bind(10)
say(f.name, f.arity, f.call([5, 3]), sub10(4), (x => x).name)'''
    out = run_tb(src)
    assert out[0].startswith("f 2 2 6 ")


def test_const():
    assert run_tb("const LIMIT = 5\nsay(LIMIT * 2)") == ["10"]
    assert "constant" in str(tb_error("const LIMIT = 5\nLIMIT = 6"))
    assert "constant" in str(tb_error("const LIMIT = 5\nLIMIT += 1"))


def test_closures_still_work():
    src = "fn counter() { let n = 0\n return () => { n += 1\n return n } }\nlet c = counter()\nc()\nsay(c())"
    assert run_tb(src) == ["2"]


# -- A7 ranges and slices -----------------------------------------------------

def test_ranges():
    src = '''say((0..5).to_list(), (0..=5).to_list(), (0..10).step(3).to_list(), (5..0).to_list())
say(3 in 0..5, 5 in 0..5, 5 in 0..=5, (0..5).len(), len(1..=3), range(3), type(0..2))
let total = 0
for i in 1..=4 { total += i }
say(total, sum(1..=4))'''
    assert run_tb(src) == [
        "[0, 1, 2, 3, 4] [0, 1, 2, 3, 4, 5] [0, 3, 6, 9] []",
        "true false true 5 3 [0, 1, 2] range",
        "10 10",
    ]


def test_range_is_lazy():
    assert run_tb("say((0..1000000000).len(), 999999999 in 0..1000000000)") == ["1000000000 true"]


def test_list_slices():
    src = "let xs = [1, 2, 3, 4, 5]\nsay(xs[1:3], xs[:2], xs[-2:], xs[::2], xs[::-1], xs[1:-1], xs[10:])"
    assert run_tb(src) == ["[2, 3] [1, 2] [4, 5] [1, 3, 5] [5, 4, 3, 2, 1] [2, 3, 4] []"]


def test_slice_step_zero():
    assert "Oops!" in str(tb_error("say([1, 2][::0])"))


# -- A8 conditional expression and match ---------------------------------------

def test_if_then_else_expression():
    assert run_tb('let x = 5\nsay(if x > 3 then "big" else "small", if x > 9 then 1 else if x > 4 then 2 else 3)') == [
        "big 2"
    ]


def test_match_patterns():
    src = '''fn label(level) {
  return match level {
    "critical" or "high" => "act now"
    n if n is number and n > 7 => f"score {n}"
    1..=5 => "low range"
    [first, ...rest] => f"starts with {first}"
    {"kind": "alert", "level": lv} => f"alert {lv}"
    nil => "nothing"
    _ => "unknown"
  }
}
for v in ["high", 9, 3, [4, 5], {"kind": "alert", "level": 2, "extra": 1}, nil, 6.5, "x"] {
  say(label(v))
}'''
    assert run_tb(src) == ["act now", "score 9", "low range", "starts with 4", "alert 2",
                           "nothing", "unknown", "unknown"]


def test_match_block_arms_and_statement_use():
    src = '''let r = match 2 {
  1 => "one"
  2 => {
    let a = 20
    a + 2
  }
  _ => nil
}
say(r)
match "go" {
  "go" => say("going")
  _ => say("staying")
}'''
    assert run_tb(src) == ["22", "going"]


def test_match_binds_whole_value_and_exact_lists():
    src = '''say(match [1, 2] { [a] => "one"
 [a, b] => a + b
 _ => 0 })
say(match "zz" { other => other + "!" })'''
    assert run_tb(src) == ["3", "zz!"]


def test_match_is_type_patterns():
    src = '''for v in [1, "t", [1], nil] {
  say(match v { is number => "num"
   is text => "text"
   is list => "list"
   _ => "other" })
}'''
    assert run_tb(src) == ["num", "text", "list", "other"]


def test_non_exhaustive_match_names_the_value():
    err = tb_error("say(match 9 { 1 => \"one\" })")
    assert "Nothing in this match fits 9" in str(err)
    assert "_ =>" in str(err)
