import math

import pytest

from conftest import run_tb, tb_error


def say(expr):
    return run_tb(f"import math\nsay({expr})")[0]


@pytest.mark.parametrize("expr,expected", [
    ("math.pi", repr(math.pi)),
    ("math.e", repr(math.e)),
    ("math.sqrt(16)", "4"),
    ("math.sqrt(2)", repr(math.sqrt(2))),
    ("math.pow(2, 10)", "1024"),
    ("math.pow(2, 0.5)", repr(2 ** 0.5)),
    ("math.pow(4, -1)", "0.25"),
    ("math.floor(2.7)", "2"),
    ("math.floor(-2.1)", "-3"),
    ("math.ceil(2.1)", "3"),
    ("math.abs(-3)", "3"),
    ("math.round(2.5)", "3"),
    ("math.round(3.14159, 3)", "3.142"),
    ("math.log(math.e)", "1"),
    ("math.log(8, 2)", "3"),
    ("math.log(100, 10)", "2"),
    ("math.sin(0)", "0"),
    ("math.cos(0)", "1"),
    ("math.tan(0)", "0"),
    ("math.min(3, 1, 2)", "1"),
    ("math.max([3, 9, 2])", "9"),
    ("math.clamp(15, 0, 10)", "10"),
    ("math.clamp(-5, 0, 10)", "0"),
    ("math.clamp(5, 0, 10)", "5"),
])
def test_math_values(expr, expected):
    assert say(expr) == expected


@pytest.mark.parametrize("expr,message", [
    ('math.sqrt("4")', "math.sqrt() needs x to be a number, not text."),
    ("math.sqrt(-1)", "can't take the square root of a negative number (-1)"),
    ("math.pow(0, -1)", "can't raise 0 to a negative power"),
    ("math.pow(-8, 0.5)", "can't raise a negative number to a fractional power"),
    ("math.pow(10, 100000)", "too big"),
    ("math.pow(10.0, 400)", "too big"),
    ("math.log(0)", "needs a number bigger than 0"),
    ("math.log(8, 1)", "base bigger than 0 and not 1"),
    ("math.min()", "math.min() needs at least 1 input, but got 0."),
    ("math.min([])", "needs at least one number"),
    ('math.max(1, "a")', "math.max() needs numbers, not text."),
    ("math.clamp(1, 10, 0)", "low to be no bigger than high"),
    ("math.round(1, 0.5)", "whole number of digits"),
    ("math.sqrt(1, 2)", "math.sqrt() needs 1 input, but got 2."),
])
def test_math_errors(expr, message):
    err = tb_error(f"import math\nsay({expr})")
    assert message in str(err) and err.line == 2


def test_math_errors_are_catchable():
    assert run_tb('import math\ntry { math.sqrt(-1) } catch e { say(e.kind, e.line) }') == ["error 2"]
