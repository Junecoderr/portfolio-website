import tb
from conftest import run_tb, tb_error


def test_error_format_has_line_number():
    err = tb_error("say(1)\nsay(2)\nsay(nope)")
    assert err.line == 3
    assert str(err).startswith("Oops! On line 3: ")


def test_error_without_line():
    assert str(tb.TBError("hi")) == "Oops! hi"


def test_undefined_name_did_you_mean():
    err = tb_error("let reasons = [1]\nsay(reasons)\nsay(reasonz)")
    assert err.line == 3
    assert "I don't know what 'reasonz' is. Did you mean 'reasons'?" in str(err)


def test_undefined_name_suggests_builtin():
    assert "Did you mean 'range'?" in str(tb_error("rnage(3)"))


def test_undefined_name_without_suggestion():
    err = tb_error("qqqqqqq")
    assert "I don't know what 'qqqqqqq' is." in str(err) and "Did you mean" not in str(err)


def test_other_language_names_get_tb_names():
    assert "Did you mean 'say'?" in str(tb_error('print("hi")'))
    assert "Did you mean 'ask'?" in str(tb_error('input("hi")'))
    assert "Did you mean 'nil'?" in str(tb_error("let x = None"))
    assert "Did you mean 'true'?" in str(tb_error("let x = True"))


def test_wrong_arity_user_function():
    err = tb_error("fn add(a, b) { return a + b }\n\nadd(1)")
    assert err.line == 3 and "add() needs 2 inputs, but got 1." in str(err)
    assert "greet() needs 1 input, but got 0." in str(tb_error("fn greet(n) { }\ngreet()"))


def test_divide_by_zero():
    err = tb_error("let x = 0\nsay(10 / x)")
    assert err.line == 2 and "can't divide by zero" in str(err)
    assert "divide by zero" in str(tb_error("say(5 % 0)"))
    assert "divide by zero" in str(tb_error("say(5 / 0.0)"))


def test_index_out_of_range():
    err = tb_error("let xs = [1, 2, 3]\nsay(xs[3])")
    assert err.line == 2
    assert "Position 3 is outside the list. It has 3 items, numbered 0 to 2." in str(err)
    assert "Position -4 is outside" in str(tb_error("let xs = [1, 2, 3]\nsay(xs[-4])"))
    assert "It has 1 item, numbered 0 to 0" in str(tb_error("say([1][1])"))
    assert "outside the text. It has 2 letters, numbered 0 to 1." in str(tb_error('say("ab"[2])'))
    assert "The text is empty" in str(tb_error('say(""[0])'))


def test_index_empty_list():
    err = tb_error("let xs = []\nsay(xs[0])")
    assert "The list is empty, so there is nothing at position 0." in str(err)


def test_index_assignment_out_of_range():
    assert "outside the list" in str(tb_error("let xs = [1]\nxs[5] = 2"))


def test_index_must_be_whole_number():
    assert "A position in a list must be a whole number" in str(tb_error("let xs = [1]\nsay(xs[0.5])"))
    assert "A position in text must be a whole number" in str(tb_error('say("ab"[0.5])'))
    assert "whole number" in str(tb_error('let xs = [1]\nsay(xs["0"])'))


def test_index_non_container():
    assert "Only lists and text can be indexed, not number" in str(tb_error("let x = 5\nsay(x[0])"))


def test_type_errors_in_arithmetic():
    assert "can only subtract numbers, not text and number" in str(tb_error('say("a" - 1)'))
    assert "can't add list and number" in str(tb_error("say([1] + 1)"))
    assert "can only make numbers negative" in str(tb_error('say(-"a")'))
    # 3.0: list * n repeats the list, so a list times a list explains that instead.
    assert "can only be repeated a whole number of times" in str(tb_error("say([1] * [2])"))
    assert "can only multiply numbers" in str(tb_error('say({"a": 1} * 2)'))


def test_calling_non_function():
    assert "I can only call functions, not a number." in str(tb_error("let x = 5\nx()"))
    assert "not nil" in str(tb_error("let x = nil\nx()"))


def test_error_inside_function_reports_inner_line():
    err = tb_error("fn f(xs) {\n  return xs[10]\n}\nf([1])")
    assert err.line == 2


def test_output_before_error_is_kept():
    lines = []
    try:
        tb.run('say("before")\nsay(boom)', output=lines.append)
    except tb.TBError:
        pass
    assert lines == ["before"]


def test_tberror_is_exception():
    assert issubclass(tb.TBError, Exception)


def test_list_inside_itself_compare_works():
    src = "let a = [1]\npush(a, a)\nlet b = [1]\npush(b, b)\nlet c = [2]\npush(c, c)\nsay(a == b, a == c, a == a, a != b)"
    assert run_tb(src) == ["true false true false"]


def test_return_outside_function():
    err = tb_error("say(1)\nreturn 5")
    assert err.line == 2 and "'return' only works inside a function" in str(err)
    assert "only works inside a function" in str(tb_error("if true {\n  return\n}"))
    # inside a function (declared or anonymous) it is fine
    assert run_tb("let f = fn() { if true { return 1 } }\nsay(f())") == ["1"]


def test_duplicate_parameter_names():
    err = tb_error("say(1)\nfn f(a, b, a) { }")
    assert err.line == 2 and "The input name 'a' is used twice" in str(err)
    assert "used twice" in str(tb_error("let g = fn(x, x) { }"))


def test_missing_file_notepad_txt_hint(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(tb, "owns_console_window", lambda: False)
    (tmp_path / "hello.tb.txt").write_text('say("hi")')
    assert tb.main([str(tmp_path / "hello.tb")]) == 1
    err = capsys.readouterr().err
    assert "Did Notepad save it as" in err and "hello.tb.txt" in err
