"""TB 3.0 Part A1 (reserved words) and A2 (numbers)."""

import pytest

from conftest import run_tb, tb_error

NEW_WORDS = ["class", "self", "super", "static", "extends", "match", "const", "export",
             "from", "yield", "async", "await", "is", "enum", "finally", "then"]


@pytest.mark.parametrize("word", NEW_WORDS)
def test_new_keywords_cannot_be_names(word):
    err = tb_error(f"let {word} = 1")
    assert f"'{word}' is a keyword in TB 3.0" in str(err)
    assert "instead" in str(err)


def test_keyword_hint_suggests_a_name():
    assert "call it 'found' instead" in str(tb_error("let match = 1"))


@pytest.mark.parametrize("word", ["match", "from", "is", "then", "class"])
def test_keywords_still_work_after_a_dot(word):
    src = f'let m = {{"{word}": 7}}\nsay(m.{word})'
    assert run_tb(src) == ["7"]


def test_re_match_still_works():
    assert run_tb('import re\nsay(re.match("a+", "aaa") != nil)') == ["true"]


def test_keywords_as_function_parameter_names_fail():
    assert "keyword" in str(tb_error("fn f(class) { return 1 }"))


def test_number_literals():
    assert run_tb("say(1_000_000, 1e3, 2.5e-3, 0x1F, 0b1010, 0o17, 0xff_ff)") == [
        "1000000 1000 0.0025 31 10 15 65535"
    ]


def test_bad_number_literal_is_friendly():
    assert "Oops!" in str(tb_error("say(0x)"))


def test_division_kinds():
    assert run_tb("say(6 / 3, 7 / 2, 7 // 2, -7 // 2, 7 % 3, -7 % 3, 2 ** 10, 2 ** 0.5 > 1.41)") == [
        "2 3.5 3 -4 1 2 1024 true"
    ]


def test_slash_gives_float_but_displays_tidy():
    assert run_tb("let x = 6 / 3\nsay(x, x.is_float(), (6 // 3).is_int())") == ["2 true true"]


def test_divide_by_zero_is_friendly():
    assert "zero" in str(tb_error("say(1 // 0)")).lower()
    assert "zero" in str(tb_error("say(1 % 0)")).lower()


def test_bitwise_operators():
    assert run_tb("say(5 & 3, 5 | 3, 5 ^ 3, ~5, 1 << 4, 256 >> 2)") == ["1 7 6 -6 16 64"]


def test_bitwise_needs_whole_numbers():
    assert "whole" in str(tb_error("say(1.5 & 1)"))


def test_compound_assignment_operators():
    src = """let x = 5
x **= 2
x //= 3
x %= 5
x <<= 3
x >>= 1
x |= 1
x &= 7
x ^= 2
say(x)"""
    # 25 -> 8 -> 3 -> 24 -> 12 -> 13 -> 5 -> 7
    assert run_tb(src) == ["7"]


def test_int_and_float_helpers():
    assert run_tb('say(int(3.7), int(-3.7), float(3), int("42"), (3.9).to_int(), (5).is_int(), (2.5).is_float(), type(1.5))') == [
        "3 -3 3 42 3 true true number"
    ]


def test_int_of_bad_text():
    err = tb_error('int("abc")')
    assert "abc" in str(err)


def test_chained_comparison():
    assert run_tb("let x = 5\nsay(0 < x <= 10, 1 < x < 3, 1 < 2 < 3 < 4, 3 > 2 > 1)") == [
        "true false true true"
    ]


def test_chained_comparison_evaluates_middle_once():
    src = """let calls = 0
fn mid() { calls += 1
 return 5 }
say(1 < mid() < 10, calls)"""
    assert run_tb(src) == ["true 1"]


def test_big_ints_are_unlimited_for_power():
    assert run_tb("say(len(str(2 ** 1000)))") == ["302"]


def test_absurd_power_is_refused():
    assert "too big" in str(tb_error("say(2 ** 100000)"))


def test_float_overflow_is_refused():
    assert "too big" in str(tb_error("say(1e308 * 10)"))
