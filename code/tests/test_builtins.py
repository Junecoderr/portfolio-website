import random

import pytest

import tb
from conftest import run_tb, tb_error


def test_say_joins_with_spaces_and_no_args():
    assert run_tb('say("a", 1, true, nil, [1])\nsay()') == ["a 1 true nil [1]", ""]


def test_say_many_args():
    assert run_tb("say(" + ", ".join(["1"] * 100) + ")") == [" ".join(["1"] * 100)]


def test_len():
    assert run_tb('say(len("abc"), len([1, 2]), len(""), len([]))') == ["3 2 0 0"]
    assert "len() needs text or a list, not number" in str(tb_error("len(5)"))


def test_str():
    assert run_tb('say(str(5) + str(2.5) + str(true) + str(nil) + str([1, "a"]))') == ['52.5truenil[1, "a"]']
    assert run_tb('say(len(str(100)))') == ["3"]


@pytest.mark.parametrize("text,expected", [
    ('"42"', "42"), ('" 7 "', "7"), ('"3.5"', "3.5"), ('"-2"', "-2"),
    ('"4.0"', "4"), ('"1e3"', "1000"), ("12", "12"), ("2.5", "2.5"),
    ('"+4"', "4"), ('".5"', "0.5"), ('"2."', "2"), ('"-2.5E-1"', "-0.25"), ('"007"', "7"),
])
def test_num_ok(text, expected):
    assert run_tb(f"say(num({text}))") == [expected]


def test_num_result_is_a_number():
    assert run_tb('say(num("2") + 3)') == ["5"]


@pytest.mark.parametrize("text", ['"abc"', '""', '"   "', '"nan"', '"inf"', '"-infinity"', '"1,000"', '"12abc"',
                                  '"1_000"', '"١٢"', '"１２"', '"²"', '"0x10"', '"1e"', '"."', '"-"', '"e5"'])
def test_num_bad_text(text):
    err = tb_error(f"num({text})")
    assert "into a number" in str(err)


@pytest.mark.parametrize("value", ["true", "nil", "[1]"])
def test_num_wrong_type(value):
    assert "num() needs text or a number" in str(tb_error(f"num({value})"))


def test_range_forms():
    assert run_tb("say(range(3), range(2, 5), range(10, 0, -3), range(0), range(5, 2))") == [
        "[0, 1, 2] [2, 3, 4] [10, 7, 4, 1] [] []"
    ]


def test_range_errors():
    assert "whole numbers" in str(tb_error("range(1.5)"))
    assert "whole numbers" in str(tb_error('range("3")'))
    assert "steps of 0" in str(tb_error("range(1, 5, 0)"))
    assert "range() needs 1 to 3 inputs, but got 0" in str(tb_error("range()"))


def test_push_returns_list():
    assert run_tb("let xs = []\nlet same = push(xs, 1)\npush(push(xs, 2), 3)\nsay(xs, same == xs)") == [
        "[1, 2, 3] true"
    ]
    assert "push() needs a list first, not text" in str(tb_error('push("a", 1)'))


def test_ask_uses_input_fn_and_returns_text():
    out = run_tb('let name = ask("Name? ")\nsay("Hi " + name)\nsay(ask())', inputs=["Alex", "42"])
    assert out == ["Hi Alex", "42"]
    assert run_tb.last_prompts == ["Name? ", ""]


def test_ask_returns_text_even_for_numbers():
    assert run_tb('let a = ask("n")\nsay(a + 1)', inputs=["2"]) == ["21"]
    assert run_tb('let a = num(ask("n"))\nsay(a + 1)', inputs=["2"]) == ["3"]


def test_ask_end_of_input_gives_empty_text():
    assert run_tb('say("[" + ask("x") + "]")', inputs=[]) == ["[]"]


def test_ask_via_public_run():
    lines = []
    tb.run('say(ask("q"))', output=lines.append, input_fn=lambda prompt: "answer:" + prompt)
    assert lines == ["answer:q"]


def test_care_is_seeded_and_deterministic():
    a = run_tb("care()\ncare()", seed=7)
    b = run_tb("care()\ncare()", seed=7)
    assert a == b
    for line in a:
        assert line.startswith("♥ ")
        assert line[2:] in tb.CARE_MESSAGES


def test_care_matches_rng_choice():
    expected = random.Random(3).choice(tb.CARE_MESSAGES)
    assert run_tb("care()", seed=3) == ["♥ " + expected]


def test_care_with_name_personalises():
    msg = random.Random(5).choice(tb.CARE_MESSAGES)
    assert run_tb('care("Alex")', seed=5) == ["♥ Alex, " + msg[0].lower() + msg[1:]]


def test_care_returns_nil():
    assert run_tb("say(care())", seed=1)[1] == "nil"


def test_every_care_message_is_reachable():
    seen = set()
    rng = random.Random(0)
    interp = tb.Interpreter(output=seen.add, rng=rng)
    tb.run("for i in range(500) { care() }", interpreter=interp)
    assert {s[2:] for s in seen} == set(tb.CARE_MESSAGES)


@pytest.mark.parametrize("call,needs", [
    ("len()", "len() needs 1 input, but got 0"),
    ("len(1, 2)", "len() needs 1 input, but got 2"),
    ("str()", "str() needs 1 input"),
    ("num()", "num() needs 1 input"),
    ("push([])", "push() needs 2 inputs, but got 1"),
    ('ask("a", "b")', "ask() needs 0 to 1 inputs, but got 2"),
    ("care(1, 2)", "care() needs 0 to 1 inputs, but got 2"),
])
def test_builtin_arity(call, needs):
    assert needs in str(tb_error(call))


def test_builtins_can_be_shadowed():
    assert run_tb("let len = 3\nsay(len)") == ["3"]


@pytest.mark.parametrize("answer", [" 2", "2 ", "2\r", "\t2\r\n"])
def test_ask_strips_whitespace_and_windows_cr(answer):
    assert run_tb('say(ask("n") == "2")', inputs=[answer]) == ["true"]


@pytest.mark.parametrize("text", ['"NaN"', '"INF"', '"Infinity"', '"+inf"', '"-Infinity"', '"1e999"'])
def test_num_rejects_non_finite_any_case(text):
    assert "into a number" in str(tb_error(f"num({text})"))


def test_run_accepts_rng_keyword():
    a, b = [], []
    tb.run("care()\ncare()", output=a.append, rng=random.Random(9))
    tb.run("care()\ncare()", output=b.append, rng=random.Random(9))
    assert a == b and len(a) == 2


def test_num_too_big():
    assert "too big" in str(tb_error('num("' + "9" * 500 + '")'))
    assert "too big" in str(tb_error('num("' + "9" * 5000 + '")'))


def test_range_length_is_capped():
    err = tb_error("say(1)\nlet xs = range(20000000)")
    assert err.line == 2 and "range is too long" in str(err) and "10,000,000" in str(err)
    assert "too long" in str(tb_error("range(0, 10000000000000000000000000000, 1)"))
    assert run_tb("say(len(range(0, 10000000, 1000)))") == ["10000"]
