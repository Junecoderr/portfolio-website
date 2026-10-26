import pytest

import tb
from conftest import tb_error


def kinds(source):
    return [(t.kind, t.value) for t in tb.tokenize(source)]


def test_numbers_int_and_float():
    assert kinds("12 3.5") == [("NUMBER", 12), ("NUMBER", 3.5), ("EOF", None)]
    assert isinstance(tb.tokenize("7")[0].value, int)
    assert isinstance(tb.tokenize("7.0")[0].value, float)


def test_identifiers_and_keywords():
    toks = kinds("let x_1 = fn")
    assert toks[:4] == [("KEYWORD", "let"), ("IDENT", "x_1"), ("OP", "="), ("KEYWORD", "fn")]
    for word in ["if", "else", "while", "for", "in", "return", "true", "false", "nil", "and", "or", "not"]:
        assert tb.tokenize(word)[0].kind == "KEYWORD"


def test_operators_two_char_first():
    ops = [v for k, v in kinds("== != <= >= < > = ! + - * / % ( ) { } [ ] , ;") if k == "OP"]
    assert ops == ["==", "!=", "<=", ">=", "<", ">", "=", "!", "+", "-", "*", "/", "%",
                   "(", ")", "{", "}", "[", "]", ",", ";"]


def test_strings_both_quotes_and_escapes():
    assert tb.tokenize('"hi"')[0].value == "hi"
    assert tb.tokenize("'hi'")[0].value == "hi"
    src = r'"a\nb\tc\"d\'e\\f"'
    assert tb.tokenize(src)[0].value == "a\nb\tc\"d'e\\f"


def test_unknown_escape_is_kept():
    assert tb.tokenize(r'"\q"')[0].value == "\\q"


def test_other_quote_inside_string():
    assert tb.tokenize("\"it's\"")[0].value == "it's"
    assert tb.tokenize("'say \"hi\"'")[0].value == 'say "hi"'


def test_comments_are_ignored():
    assert kinds("# hello\n1 # two\n") == [("NUMBER", 1), ("EOF", None)]


def test_line_numbers():
    toks = tb.tokenize("a\n\nb\r\nc")
    assert [t.line for t in toks[:3]] == [1, 3, 4]


def test_unclosed_string_reports_start_line():
    err = tb_error('say(1)\nsay("oops)\nsay(2)')
    assert err.line == 2
    assert "never ends" in str(err)
    assert str(err).startswith("Oops! On line 2:")


def test_unclosed_string_at_end_of_file():
    err = tb_error('"abc')
    assert err.line == 1 and "closing \"" in str(err)


def test_backslash_newline_does_not_hide_unclosed_string():
    err = tb_error('let a = "abc\\\nsay(1)')
    assert err.line == 1 and "never ends" in str(err)


def test_trailing_backslash_at_end_of_input():
    err = tb_error('"abc\\')
    assert "never ends" in str(err)


def test_unknown_character():
    err = tb_error("let x = 5 @ 3")
    assert "character '@'" in str(err) and err.line == 1


@pytest.mark.parametrize("ch", ["“", "”", "‘", "’"])
def test_curly_quotes_get_a_hint(ch):
    err = tb_error(f"say({ch}hi{ch})")
    assert "curly quote" in str(err)


def test_and_or_symbols_get_hints():
    assert "'and'" in str(tb_error("true && false"))
    assert "'or'" in str(tb_error("true || false"))


def test_non_ascii_digits_are_not_numbers():
    # '²' is a digit to Python, but int('²') fails; it must not crash TB.
    err = tb_error("let x = ²")
    assert isinstance(err, tb.TBError)


def test_unicode_letters_in_names():
    from conftest import run_tb
    assert run_tb("let café = 2\nsay(café)") == ["2"]
