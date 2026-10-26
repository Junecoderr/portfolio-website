"""decimal: exact decimal numbers for money and anything where 0.1 + 0.2 must be 0.3."""

from __future__ import annotations

import decimal as _d

from tblib._api import MANY, Module, check_type, fail, make_object, method, native_class

import tb as _tb

PRECISION = 34  # significant digits kept by arithmetic (plenty for money)
CONTEXT = _d.Context(prec=PRECISION, rounding=_d.ROUND_HALF_EVEN, traps=[])
ROUNDING = {
    "half_even": _d.ROUND_HALF_EVEN, "half_up": _d.ROUND_HALF_UP, "half_down": _d.ROUND_HALF_DOWN,
    "up": _d.ROUND_UP, "down": _d.ROUND_DOWN, "ceiling": _d.ROUND_CEILING, "floor": _d.ROUND_FLOOR,
}


def is_dec(value) -> bool:
    """True for a value made by the decimal library."""
    return isinstance(value, _tb.Instance) and isinstance(value.fields.get("_dec"), _d.Decimal)


def to_decimal(value, line, fn, arg="value"):
    """A Python Decimal from a TB value: a decimal handle, a number or text."""
    if isinstance(value, _d.Decimal):
        return value
    if is_dec(value):
        return value.fields["_dec"]
    if isinstance(value, bool):
        fail(line, f"{fn}() needs {arg} to be a decimal, a number or text, not true/false.")
    if isinstance(value, int):
        return _d.Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            fail(line, f"{fn}() can't make a decimal from {_tb.to_text(value)}.")
        # Go through the shortest text form so 0.1 stays 0.1, not 0.1000000000000000055...
        return _d.Decimal(repr(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("_", "")
        if not text:
            fail(line, f"{fn}() needs {arg} to be a number written as text, but got empty text.")
        try:
            result = _d.Decimal(text)
        except _d.InvalidOperation:
            fail(line, f"{fn}() couldn't read \"{value}\" as a decimal number.", reason="parse")
        if not result.is_finite():
            fail(line, f"{fn}() can't make a decimal from \"{value}\".", reason="parse")
        return result
    fail(line, f"{fn}() needs {arg} to be a decimal, a number or text, not {_tb.type_name(value)}.")


def plain_text(d: _d.Decimal) -> str:
    """Decimal as plain digits (no exponent): 1E+2 -> "100", 1.50 stays "1.50"."""
    if d == d.to_integral_value() and d.as_tuple().exponent > 0:
        d = d.quantize(_d.Decimal(1))
    text = format(d, "f")
    if text == "-0" or (text.startswith("-") and _d.Decimal(text) == 0):
        text = text[1:]
    return text


def load(api):
    m = Module(
        "decimal",
        "Exact decimal numbers: decimal.of(\"0.1\").add(\"0.2\") is exactly 0.3, where plain numbers give "
        "0.30000000000000004. Use them for money and anything that is counted in decimal places. "
        "A decimal has .text (what it shows), .number (nearest plain number) and methods add, sub, mul, div, "
        "round, compare, equals, abs, negate, is_zero; decimal.sum() adds a list. "
        "Arithmetic keeps 34 significant digits.",
    )


    def rounding_arg(line, mode, fn):
        if mode is None:
            return _d.ROUND_HALF_EVEN
        check_type(line, mode, "text", fn, "mode")
        if mode not in ROUNDING:
            fail(line, f'{fn}() doesn\'t know the rounding mode "{mode}". Modes: {", ".join(ROUNDING)}.')
        return ROUNDING[mode]

    def do_round(line, d, places, mode=None, fn="decimal.round"):
        places = check_type(line, places, "whole", fn, "places")
        if not -30 <= places <= 30:
            fail(line, f"{fn}() rounds to between -30 and 30 places, not {places}.")
        rounding = rounding_arg(line, mode, fn)
        try:
            return d.quantize(_d.Decimal(1).scaleb(-places), rounding=rounding, context=CONTEXT)
        except _d.InvalidOperation:
            fail(line, f"{fn}() can't round {plain_text(d)} to {places} places: too many digits.")

    def arith(line, op, a, b, fn):
        x = to_decimal(a, line, fn, "a")
        y = to_decimal(b, line, fn, "b")
        if op == "+":
            return CONTEXT.add(x, y)
        if op == "-":
            return CONTEXT.subtract(x, y)
        if op == "*":
            return CONTEXT.multiply(x, y)
        if op == "/":
            if y == 0:
                fail(line, "You can't divide by zero.", reason="zero_division")
            return CONTEXT.divide(x, y)
        if op == "%":
            if y == 0:
                fail(line, "You can't divide by zero.", reason="zero_division")
            return CONTEXT.remainder(x, y)
        if op == "//":
            if y == 0:
                fail(line, "You can't divide by zero.", reason="zero_division")
            return CONTEXT.divide_int(x, y)
        if op == "**":
            try:
                result = CONTEXT.power(x, y)
            except _d.InvalidOperation:
                fail(line, f"decimal.pow() can't raise {plain_text(x)} to {plain_text(y)}.")
            if not result.is_finite():
                fail(line, "decimal.pow() gave a result that is too big.")
            return result
        raise AssertionError(op)

    # Decimals are objects of a Python-backed TB class (see native_class in _api.py),
    # so the operators work: decimal.of("0.1") + decimal.of("0.2") == decimal.of("0.3").
    Decimal = native_class("Decimal", "An exact decimal number from the decimal library.")

    def make(d: _d.Decimal):
        if not d.is_finite():
            fail(None, "The decimal result is too big to represent.")
        text = plain_text(d)
        try:
            number = _tb.tidy_number(float(d))
        except (OverflowError, ValueError):
            number = None
        return make_object(Decimal, {"text": text, "number": number, "_dec": d})

    def dec(me):
        return me.fields["_dec"]

    def arith_method(name, op, swapped=False):
        def run(line, me, other):
            a, b = (other, dec(me)) if swapped else (dec(me), other)
            return make(arith(line, op, a, b, f"decimal.{name}"))
        return run

    methods = [
        ("add", "+", "add(other) -> decimal\nThis plus other (a decimal, number or text). Same as this + other."),
        ("sub", "-", "sub(other) -> decimal\nThis minus other. Same as this - other."),
        ("mul", "*", "mul(other) -> decimal\nThis times other. Same as this * other."),
        ("div", "/", "div(other) -> decimal\nThis divided by other (34 significant digits; round() after). Same as this / other."),
        ("floor_div", "//", "floor_div(other) -> decimal\nThe whole number of times other fits. Same as this // other."),
        ("mod", "%", "mod(other) -> decimal\nThe remainder of dividing by other. Same as this % other."),
        ("pow", "**", "pow(other) -> decimal\nThis raised to the power other. Same as this ** other."),
    ]
    for name, op, doc in methods:
        method(Decimal, name, 1, doc=doc)(arith_method(name, op))
        method(Decimal, "r" + name, 1, doc=f"r{name}(other) -> decimal\nother {op} this (used when a plain number is on the left).")(
            arith_method(name, op, swapped=True))

    @method(Decimal, "round", 0, 2, doc="round(places?, mode?) -> decimal\nRounded to that many decimal places "
                                        "(default 0). mode: \"half_even\" (default, banker's), \"half_up\" (school "
                                        "rounding), \"half_down\", \"up\", \"down\", \"ceiling\", \"floor\".")
    def round_m(line, me, places=0, mode=None):
        return make(do_round(line, dec(me), places, mode))

    @method(Decimal, "compare", 1, doc="compare(other) -> number\n-1 if smaller than other, 0 if equal, 1 if bigger. Also used by < > and sort().")
    def compare_m(line, me, other):
        d, o = dec(me), to_decimal(other, line, "decimal.compare")
        return -1 if d < o else (1 if d > o else 0)

    @method(Decimal, "equals", 1, doc="equals(other) -> bool\nTrue if both are the same value (1.0 equals 1). Also used by ==.")
    def equals_m(line, me, other):
        if isinstance(other, bool) or not (_tb.is_number(other) or isinstance(other, str) or is_dec(other)):
            return False
        try:
            return dec(me) == to_decimal(other, line, "decimal.equals")
        except _tb.TBError:
            return False

    @method(Decimal, "less", 1, doc="less(other) -> bool\nTrue if this is smaller than other.")
    def less_m(line, me, other):
        return dec(me) < to_decimal(other, line, "decimal.less")

    @method(Decimal, "greater", 1, doc="greater(other) -> bool\nTrue if this is bigger than other.")
    def greater_m(line, me, other):
        return dec(me) > to_decimal(other, line, "decimal.greater")

    @method(Decimal, "abs", 0, doc="abs() -> decimal\nWithout its sign.")
    def abs_m(line, me):
        return make(abs(dec(me)))

    @method(Decimal, "negate", 0, doc="negate() -> decimal\nWith the opposite sign. Same as -this.")
    def negate_m(line, me):
        return make(-dec(me))

    @method(Decimal, "is_zero", 0, doc="is_zero() -> bool\nTrue if the value is 0.")
    def is_zero_m(line, me):
        return dec(me) == 0

    @method(Decimal, "sign", 0, doc="sign() -> number\n-1, 0 or 1.")
    def sign_m(line, me):
        d = dec(me)
        return -1 if d < 0 else (1 if d > 0 else 0)

    @method(Decimal, "places", 0, doc="places() -> number\nHow many decimal places it carries: 1.50 has 2.")
    def places_m(line, me):
        exp = dec(me).as_tuple().exponent
        return max(0, -exp) if isinstance(exp, int) else 0

    @method(Decimal, "text", 0, doc="text() -> text\nHow say(), str() and f-strings show the decimal: \"0.30\".")
    def text_m(line, me):
        return me.fields["text"]

    @method(Decimal, "to_text", 0, doc="to_text() -> text\nThe value as text, like \"0.30\" (same as .text).")
    def to_text_m(line, me):
        return me.fields["text"]

    @method(Decimal, "to_number", 0, doc="to_number() -> number\nThe nearest plain number (same as .number).")
    def to_number_m(line, me):
        return me.fields["number"]

    @m.fn("of", 1, doc=(
        "of(value) -> decimal\n"
        "An exact decimal from text like \"19.99\", a number, or another decimal. Text keeps every digit "
        "(\"1.50\" stays 1.50); a plain number is read the way it prints (0.1 becomes exactly 0.1)."))
    def of(line, value):
        return make(to_decimal(value, line, "decimal.of"))

    @m.fn("is_decimal", 1, doc="is_decimal(x) -> bool\nTrue if x came from the decimal library.")
    def is_decimal(line, value):
        return is_dec(value)

    @m.fn("add", 2, doc="add(a, b) -> decimal\na + b exactly: decimal.add(\"0.1\", \"0.2\").text is \"0.3\".")
    def add(line, a, b):
        return make(arith(line, "+", a, b, "decimal.add"))

    @m.fn("sub", 2, doc="sub(a, b) -> decimal\na - b exactly.")
    def sub(line, a, b):
        return make(arith(line, "-", a, b, "decimal.sub"))

    @m.fn("mul", 2, doc="mul(a, b) -> decimal\na * b exactly.")
    def mul(line, a, b):
        return make(arith(line, "*", a, b, "decimal.mul"))

    @m.fn("div", 2, doc="div(a, b) -> decimal\na / b to 34 significant digits. Error on division by zero.")
    def div(line, a, b):
        return make(arith(line, "/", a, b, "decimal.div"))

    @m.fn("round", 2, 3, doc=(
        "round(value, places, mode?) -> decimal\n"
        "Round to that many decimal places: decimal.round(\"2.675\", 2).text is \"2.68\" with mode "
        "\"half_up\" (default \"half_even\" rounds ties to the even digit, like banks do)."))
    def round_(line, value, places, mode=None):
        return make(do_round(line, to_decimal(value, line, "decimal.round"), places, mode))

    @m.fn("compare", 2, doc="compare(a, b) -> number\n-1 if a < b, 0 if equal, 1 if a > b. Handy for sort_by.")
    def compare(line, a, b):
        x = to_decimal(a, line, "decimal.compare", "a")
        y = to_decimal(b, line, "decimal.compare", "b")
        return -1 if x < y else (1 if x > y else 0)

    @m.fn("sum", 1, MANY, doc=(
        "sum(values, ...) -> decimal\n"
        "Add up a list of decimals, numbers or texts exactly: decimal.sum([\"0.1\", \"0.2\", 0.3]).text is \"0.6\"."))
    def sum_(line, *values):
        items = list(values[0]) if len(values) == 1 and isinstance(values[0], list) else list(values)
        total = _d.Decimal(0)
        for i, v in enumerate(items):
            total = CONTEXT.add(total, to_decimal(v, line, "decimal.sum", f"item {i + 1}"))
        return make(total)

    @m.fn("text", 1, doc="text(value) -> text\nA decimal (or number) as plain digits: decimal.text(1e21) has no exponent.")
    def text_(line, value):
        return plain_text(to_decimal(value, line, "decimal.text"))

    @m.fn("number", 1, doc="number(value) -> number\nThe nearest plain number for a decimal.")
    def number_(line, value):
        d = to_decimal(value, line, "decimal.number")
        try:
            return _tb.tidy_number(float(d))
        except (OverflowError, ValueError):
            fail(line, f"decimal.number() can't fit {plain_text(d)} into a plain number.")

    @m.fn("money", 1, 3, doc=(
        "money(value, places?, mode?) -> text\n"
        "Round like money and show with thousands separators and fixed places: decimal.money(\"1234.5\") "
        "is \"1,234.50\". places defaults to 2, mode to \"half_up\"."))
    def money(line, value, places=2, mode="half_up"):
        d = to_decimal(value, line, "decimal.money")
        rounded = do_round(line, d, places, mode, "decimal.money")
        places = int(places)
        return f"{rounded:,.{max(places, 0)}f}"

    m.value("precision", PRECISION, doc="How many significant digits arithmetic keeps (34).")
    m.value("modes", list(ROUNDING), doc="The rounding modes round() accepts.")
    return m
