import tb
from conftest import run_tb, tb_error


def body(source):
    return tb.parse(source).body


def test_statement_kinds():
    stmts = body("let x = 1\nx = 2\nfn f(a) { return a }\nf(1)\nif x { } \nwhile false { }\nfor i in [] { }")
    assert [type(s).__name__ for s in stmts] == ["Let", "Assign", "FnDecl", "ExprStmt", "If", "While", "For"]


def test_precedence():
    expr = body("1 + 2 * 3")[0].expr
    assert isinstance(expr, tb.Binary) and expr.op == "+"
    assert isinstance(expr.right, tb.Binary) and expr.right.op == "*"
    assert run_tb("say(1 + 2 * 3, (1 + 2) * 3, 10 - 4 - 3, 2 * 3 % 4)") == ["7 9 3 2"]


def test_comparison_and_logic_precedence():
    assert run_tb("say(1 < 2 and 3 > 4 or 5 == 5)") == ["true"]


def test_not_reads_like_english():
    # 'not x == 5' means 'not (x == 5)'
    assert run_tb('let answer = "no"\nsay(not answer == "yes")') == ["true"]
    assert run_tb("say(not true or true)") == ["true"]
    assert run_tb("say(not false and false)") == ["false"]


def test_bang_binds_tightly():
    assert run_tb("say(!false == true)") == ["true"]


def test_else_if_chain_parses():
    stmt = body("if a { } else if b { } else if c { } else { }")[0]
    assert len(stmt.branches) == 3 and stmt.otherwise == []


def test_optional_semicolons():
    assert run_tb("let a = 1; let b = 2;; say(a + b);") == ["3"]


def test_newline_paren_starts_new_statement():
    # Without the rule this would be: say("a")("b") -> calling nil.
    assert run_tb('say("a")\n(say("b"))') == ["a", "b"]


def test_newline_bracket_starts_new_statement():
    src = "let xs = [1, 2]\nlet y = xs\n[3, 4]\nsay(y)"
    assert run_tb(src) == ["[1, 2]"]


def test_same_line_call_and_index_still_work():
    assert run_tb("let xs = [[1, 2], [3, 4]]\nsay(xs[1][0])") == ["3"]
    assert run_tb("fn f() { return fn(x) { return x * 2 } }\nsay(f()(21))") == ["42"]


def test_call_arguments_can_span_lines():
    assert run_tb('say(\n  "a",\n  "b",\n)') == ["a b"]


def test_list_trailing_comma_and_multiline():
    assert run_tb("let xs = [\n 1,\n 2,\n]\nsay(len(xs))") == ["2"]


def test_return_without_value_on_own_line():
    src = "fn f() {\n  return\n}\nsay(f())"
    assert run_tb(src) == ["nil"]


def test_return_value_on_next_line_is_not_returned():
    src = "fn f() {\n  return\n  5\n}\nsay(f())"
    assert run_tb(src) == ["nil"]


def test_return_before_closing_brace_same_line():
    assert run_tb("fn f() { return }\nsay(f())") == ["nil"]
    assert run_tb("fn f() { return; }\nsay(f())") == ["nil"]


def test_return_expression():
    assert run_tb("fn f() { return -1 }\nsay(f())") == ["-1"]


def test_fn_expression_as_value():
    assert run_tb("let double = fn(x) { return x * 2 }\nsay(double(4))") == ["8"]


def test_assignment_target_must_be_name_or_slot():
    err = tb_error("1 = 2")
    assert "name or a list slot" in str(err)


def test_stray_else():
    err = tb_error("say(1)\nelse { }")
    assert err.line == 2 and "doesn't belong" in str(err)


def test_unclosed_block_points_at_opening_line():
    err = tb_error("say(1)\nif true {\n  say(2)\n")
    assert err.line == 2 and "never closed" in str(err) and "'}'" in str(err)


def test_unclosed_paren_points_at_opening_line():
    err = tb_error('say(1)\nsay("hi"\n\n')
    assert err.line == 2 and "'(' was never closed" in str(err)


def test_unclosed_list_points_at_opening_line():
    err = tb_error("let xs = [1,\n2,\n3\n")
    assert err.line == 1 and "'[' was never closed" in str(err)


def test_missing_closing_paren_mid_program():
    err = tb_error("say(1 2")
    assert "I expected ')'" in str(err)


def test_single_equals_in_condition_hint():
    err = tb_error("let x = 1\nif x = 1 { }")
    assert err.line == 2 and "'=='" in str(err)


def test_let_needs_name_and_equals():
    assert "a name after 'let'" in str(tb_error("let = 5"))
    assert "'=' after 'let x'" in str(tb_error("let x"))


def test_for_needs_in():
    assert "'in'" in str(tb_error("for x [1] { }"))


def test_unexpected_token():
    err = tb_error("say(1)\n)")
    assert err.line == 2 and "didn't expect ')'" in str(err)


def test_other_language_keywords_get_hints():
    assert "'fn'" in str(tb_error("def greet() { }"))
    assert "'fn'" in str(tb_error("function greet() { }"))
    assert "'let'" in str(tb_error("var x = 1"))
    assert "'else if'" in str(tb_error("if false { } elif true { }"))


def test_deep_nesting_is_friendly():
    err = tb_error("say(" + "(" * 20000 + "1" + ")" * 20000 + ")")
    assert "too deep" in str(err)
