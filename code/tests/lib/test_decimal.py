"""decimal library: exact arithmetic, rounding, comparisons."""

import pytest

import tb
from conftest import run_tb, tb_error


def test_exact_arithmetic():
    src = '''import decimal
let a = decimal.of("0.1")
say(a.add("0.2").text, 0.1 + 0.2)
say(decimal.add(0.1, 0.2).text, decimal.sub("1.00", "0.1").text, decimal.mul("1.5", 3).text, decimal.div(1, 8).text)
say(a.sub(1).text, a.mul(a).text, a.div(3).round(5).text, a.mod("0.03").text, decimal.of(2).pow(10).text)
say(a.number, decimal.of("1.50").text, decimal.of("1.50").places(), decimal.of(100).text, decimal.text("1e3"))
say(decimal.sum(["0.1", "0.2", 0.3]).text, decimal.sum("1", "2").text, decimal.sum([]).text)
'''
    assert run_tb(src) == [
        "0.3 0.30000000000000004",
        "0.3 0.90 4.5 0.125",
        "-0.9 0.01 0.03333 0.01 1024",
        "0.1 1.50 2 100 1000",
        "0.6 3 0",
    ]


def test_rounding_modes():
    src = '''import decimal
say(decimal.round("2.675", 2).text, decimal.round("2.675", 2, "half_up").text, decimal.round("2.5", 0).text, decimal.round("3.5", 0).text)
say(decimal.of("1.2345").round(2, "down").text, decimal.of("1.2345").round(2, "up").text, decimal.of("-1.2345").round(2, "floor").text, decimal.of("-1.2345").round(2, "ceiling").text)
say(decimal.of("1234.5").round(-2).text, decimal.of(7).round().text, decimal.of("0.1").round(3).text)
say(decimal.money("1234.5"), decimal.money("-0.005"), decimal.money("99", 0))
'''
    assert run_tb(src) == [
        "2.68 2.68 2 4",
        "1.23 1.24 -1.24 -1.23",
        "1200 7 0.100",
        "1,234.50 -0.01 99",
    ]


def test_compare_and_predicates():
    src = '''import decimal
let a = decimal.of("1.0")
say(a.equals(1), a.equals("1.000"), a.compare(2), a.compare("0.5"), decimal.compare("1", 1))
say(a.less(2), a.greater(2), a.sign(), decimal.of("-3").abs().text, a.negate().text, decimal.of(0).is_zero(), a.is_zero())
say(decimal.is_decimal(a), decimal.is_decimal(1), decimal.number("2.50"), a.to_text(), a.to_number())
let prices = ["10.5", "2.25", "7"].map(fn(p) { return decimal.of(p) }).sort_by(fn(d) { return d.number })
say(prices.map(fn(d) { return d.text }))
say(decimal.precision, len(decimal.modes))
'''
    assert run_tb(src) == [
        "true true -1 1 0",
        "true false 1 3 -1.0 true false",
        "true false 2.5 1.0 1",
        '["2.25", "7", "10.5"]',
        "34 7",
    ]


def test_big_and_small_values():
    src = '''import decimal
say(decimal.of("123456789012345678901234567890").add(1).text)
say(decimal.of("0.000000000000000000001").mul(2).text)
say(decimal.of("1,234.56").text, decimal.of(" 5 ").text)
'''
    assert run_tb(src) == ["123456789012345678901234567891", "0.000000000000000000002", "1234.56 5"]


@pytest.mark.parametrize("src,message", [
    ('decimal.of("abc")', 'couldn\'t read "abc" as a decimal number'),
    ('decimal.of("")', "got empty text"),
    ('decimal.of(true)', "not true/false"),
    ('decimal.of([1])', "needs value to be a decimal, a number or text, not list"),
    ('decimal.div(1, 0)', "You can't divide by zero."),
    ('decimal.of(1).div("0.0")', "You can't divide by zero."),
    ('decimal.round(1, 2, "nearest")', 'doesn\'t know the rounding mode "nearest"'),
    ('decimal.round(1, 50)', "between -30 and 30 places"),
    ('decimal.round(1, 1.5)', "needs places to be a whole number"),
    ('decimal.of("inf")', "can't make a decimal from"),
    ('decimal.sum([1, "x"])', 'couldn\'t read "x"'),
])
def test_decimal_errors(src, message):
    assert message in str(tb_error("import decimal\n" + src))


def test_error_reason_and_docs():
    assert tb_error('import decimal\ndecimal.div(1, 0)').fields["reason"] == "zero_division"
    assert tb_error('import decimal\ndecimal.of("x")').fields["reason"] == "parse"
    info = tb.describe_library("decimal")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
