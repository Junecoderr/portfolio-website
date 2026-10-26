import pytest

from conftest import run_tb, tb_error


def test_test_and_match():
    src = r'''import re
say(re.test("\d+", "abc 123"), re.test("^\d+$", "abc"))
let m = re.match("user=(\w+) ip=(?P<ip>[\d.]+)", "x user=tanisha ip=10.0.0.5 y")
say(m[0], m[1], m[2], m.ip, m.start, m.end)
say(re.match("zzz", "abc"))'''
    assert run_tb(src) == ["true false", "user=tanisha ip=10.0.0.5 tanisha 10.0.0.5 10.0.0.5 2 26", "nil"]


def test_find_all_shapes():
    src = r'''import re
say(re.find_all("\d+", "a1 b22 c333"))
say(re.find_all("user=(\w+)", "user=a user=b"))
say(re.find_all("(\w)=(\d)", "a=1 b=2"))
say(re.find_all("x", "abc"))'''
    assert run_tb(src) == ['["1", "22", "333"]', '["a", "b"]', '[["a", "1"], ["b", "2"]]', "[]"]


def test_replace_split_escape():
    src = r'''import re
say(re.replace("\d", "a1b22", "#"))
say(re.replace("(\w+)@(\w+)", "me@site", "\2 at \1"))
say(re.replace("\d+", "a1b22", fn(m) { return str(num(m[0]) * 2) }))
say(re.split(",\s*", "a, b,c"))
say(re.escape("1.2.3.4"), re.test(re.escape("a.b"), "axb"))'''
    assert run_tb(src) == ["a#b##", "site at me", "a2b44", '["a", "b", "c"]', r"1\.2\.3\.4 false"]


@pytest.mark.parametrize("src,message", [
    ('re.test("(", "x")', "That pattern doesn't work: missing ), unterminated subpattern at position 0. (Pattern: ()"),
    ('re.find_all("[a-", "x")', "That pattern doesn't work: unterminated character set"),
    ("re.test(1, \"x\")", "re.test() needs pattern to be text, not a number."),
    ('re.match("a", nil)', "re.match() needs text to be text, not nil."),
    (r're.replace("(a)", "a", "\2")', "can't use that replacement text"),
    ('re.replace("a", "a", 5)', "re.replace() needs with to be a function, not a number."),
])
def test_re_errors(src, message):
    assert message in str(tb_error(f"import re\n{src}"))


def test_bad_pattern_is_catchable():
    assert run_tb('import re\ntry { re.test("(", "") } catch e { say("bad pattern") }') == ["bad pattern"]
