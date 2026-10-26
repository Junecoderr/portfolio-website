"""math: numbers beyond + - * /."""

from __future__ import annotations

import math as _math

from tblib._api import MANY, Module, check_type, fail, is_number

import tb as _tb


def load(api):
    m = Module("math", "Numbers beyond + - * /: square roots, powers, rounding, trigonometry.")

    m.value("pi", _math.pi, doc="The circle number, 3.14159...")
    m.value("e", _math.e, doc="Euler's number, 2.71828...")

    def num(line, value, fn, arg="x"):
        return check_type(line, value, "number", f"math.{fn}", arg)

    def result(line, value):
        return _tb.tidy_number(_tb.check_size(value, line)) if isinstance(value, float) else _tb.check_size(value, line)

    @m.fn("sqrt", 1, doc="sqrt(x) -> number\nThe square root: sqrt(9) is 3.")
    def sqrt(line, x):
        num(line, x, "sqrt")
        if x < 0:
            fail(line, f"math.sqrt() can't take the square root of a negative number ({_tb.to_text(x)}).")
        return result(line, _math.sqrt(x))

    @m.fn("pow", 2, doc="pow(x, y) -> number\nx to the power y: pow(2, 10) is 1024.")
    def power(line, x, y):
        num(line, x, "pow")
        num(line, y, "pow", "y")
        if x == 0 and y < 0:
            fail(line, "math.pow() can't raise 0 to a negative power (that divides by zero).")
        if isinstance(x, int) and isinstance(y, int) and y >= 0:
            if y * max(abs(x), 1).bit_length() > _tb.MAX_INT_BITS + 64:
                fail(line, _tb.TOO_BIG)
            return result(line, x ** y)
        try:
            value = float(x) ** float(y)
        except OverflowError:
            fail(line, _tb.TOO_BIG)
        if isinstance(value, complex):
            fail(line, "math.pow() can't raise a negative number to a fractional power.")
        return result(line, value)

    @m.fn("floor", 1, doc="floor(x) -> number\nThe whole number at or below x: floor(2.7) is 2.")
    def floor(line, x):
        return result(line, _math.floor(num(line, x, "floor")))

    @m.fn("ceil", 1, doc="ceil(x) -> number\nThe whole number at or above x: ceil(2.1) is 3.")
    def ceil(line, x):
        return result(line, _math.ceil(num(line, x, "ceil")))

    @m.fn("abs", 1, doc="abs(x) -> number\nx without its minus sign.")
    def absolute(line, x):
        return abs(num(line, x, "abs"))

    @m.fn("round", 1, 2, doc="round(x, digits?) -> number\nRounded to digits after the dot (default 0). Halves round away from zero.")
    def rounded(line, x, digits=0):
        return _tb.tb_round(x, digits, line)

    @m.fn("log", 1, 2, doc="log(x, base?) -> number\nThe logarithm of x (natural log, or in the given base).")
    def log(line, x, base=None):
        num(line, x, "log")
        if x <= 0:
            fail(line, "math.log() needs a number bigger than 0.")
        if base is None:
            return result(line, _math.log(x))
        num(line, base, "log", "base")
        if base <= 0 or base == 1:
            fail(line, "math.log() needs a base bigger than 0 and not 1.")
        return result(line, _math.log(x, base))

    @m.fn("sin", 1, doc="sin(x) -> number\nThe sine of x (x in radians).")
    def sin(line, x):
        return result(line, _math.sin(num(line, x, "sin")))

    @m.fn("cos", 1, doc="cos(x) -> number\nThe cosine of x (x in radians).")
    def cos(line, x):
        return result(line, _math.cos(num(line, x, "cos")))

    @m.fn("tan", 1, doc="tan(x) -> number\nThe tangent of x (x in radians).")
    def tan(line, x):
        return result(line, _math.tan(num(line, x, "tan")))

    def pick(name, chooser):
        def fn(line, *args):
            values = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
            for v in values:
                if not is_number(v):
                    fail(line, f"math.{name}() needs numbers, not {_tb.type_name(v)}.")
            if not values:
                fail(line, f"math.{name}() needs at least one number.")
            return chooser(values)
        return fn

    m.fn("min", 1, MANY, doc="min(a, b, ...) or min(list) -> number\nThe smallest number.")(pick("min", min))
    m.fn("max", 1, MANY, doc="max(a, b, ...) or max(list) -> number\nThe biggest number.")(pick("max", max))

    @m.fn("clamp", 3, doc="clamp(x, low, high) -> number\nx, kept between low and high: clamp(15, 0, 10) is 10.")
    def clamp(line, x, low, high):
        num(line, x, "clamp")
        num(line, low, "clamp", "low")
        num(line, high, "clamp", "high")
        if low > high:
            fail(line, "math.clamp() needs low to be no bigger than high.")
        return min(max(x, low), high)

    return m
