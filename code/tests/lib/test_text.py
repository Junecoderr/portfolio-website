import pytest

from conftest import run_tb, tb_error


def say(expr):
    return run_tb(f"import text\nsay({expr})")[0]


@pytest.mark.parametrize("expr,expected", [
    ('text.pad_left(7, 3, "0")', "007"),
    ('text.pad_left("ab", 4)', "  ab"),
    ('text.pad_left("long", 2)', "long"),
    ('text.pad_right("ab", 4, ".")', "ab.."),
    ('text.center("hi", 6, "*")', "**hi**"),
    ('text.wrap("the quick brown fox jumps", 10)', "the quick\nbrown fox\njumps"),
    ('text.wrap("a b\\n\\nc", 10)', "a b\n\nc"),
    ('text.is_digit("0123")', "true"),
    ('text.is_digit("12a")', "false"),
    ('text.is_digit("")', "false"),
    ('text.is_digit("-1")', "false"),
    ("text.format_number(1234567)", "1,234,567"),
    ("text.format_number(1234.5, 2)", "1,234.50"),
    ("text.format_number(-0.125, 2)", "-0.13"),
    ("text.format_number(2.5)", "3"),
    ('text.reverse("abc")', "cba"),
    ('text.title("hello there world")', "Hello There World"),
    ('text.count("banana", "an")', "2"),
])
def test_text_values(expr, expected):
    assert say(expr) == expected


def test_random_id():
    out = run_tb("import text\nlet a = text.random_id()\nlet b = text.random_id(20)\nsay(len(a), len(b), a == b)\nsay(a)")
    assert out[0] == "8 20 false"
    assert out[1].isalnum()


@pytest.mark.parametrize("expr,message", [
    ('text.pad_left("a", "3")', "text.pad_left() needs width to be a whole number, not text."),
    ('text.pad_left("a", 2.5)', "needs width to be a whole number, not 2.5"),
    ('text.pad_left("a", 3, "ab")', "fill to be exactly one letter"),
    ('text.center("a", 99999999)', "can't make text that wide"),
    ('text.wrap("a", 0)', "width of at least 1"),
    ("text.wrap(5, 10)", "text.wrap() needs text to be text, not a number."),
    ('text.format_number("1")', "needs n to be a number"),
    ("text.format_number(1, 30)", "decimals from 0 to 20"),
    ("text.random_id(0)", "length from 1 to 1000"),
    ('text.count("a", "")', "part that isn't empty"),
])
def test_text_errors(expr, message):
    assert message in str(tb_error(f"import text\nsay({expr})"))
