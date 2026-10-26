import random

import tb
from conftest import ROOT

EXPECTED = [
    "Hello, Alex! Welcome to TB.",
    "This program is a quick tour of the language.",
    "",
    "A few programming languages:",
    "1. TB",
    "2. Python",
    "3. JavaScript",
    "4. Go",
    "That's 4 languages in one list.",
    "",
    "Biscuit is a cat who is 3 years old.",
    "",
    "Steps taken so far: 3",
    "* * * * * ",
    "",
    "3...",
    "2...",
    "1...",
    "Lift-off! You have seen the basics of TB.",
    "",
    "A note from TB:",
    "♥ Alex, well done for showing up today.",
    "",
    "Have fun writing your own programs.",
]
CARE_LINE = 21


def source():
    return (ROOT / "welcome.tb").read_text(encoding="utf-8")


def test_welcome_exact_output_with_seeded_care():
    lines = []
    interp = tb.Interpreter(output=lines.append, rng=random.Random(42))
    tb.run(source(), interpreter=interp)
    assert lines == EXPECTED


def test_welcome_care_line_any_seed():
    for seed in range(20):
        lines = []
        tb.run(source(), interpreter=tb.Interpreter(output=lines.append, rng=random.Random(seed)))
        assert lines[:CARE_LINE] == EXPECTED[:CARE_LINE] and lines[CARE_LINE + 1:] == EXPECTED[CARE_LINE + 1:]
        assert lines[CARE_LINE].startswith("♥ Alex, ")


def test_welcome_via_cli(capsys, monkeypatch):
    real_random = random.Random
    monkeypatch.setattr(tb.random, "Random", lambda *a: real_random(42))
    code = tb.main([str(ROOT / "welcome.tb")])
    out = capsys.readouterr()
    assert code == 0 and out.err == ""
    assert out.out.splitlines() == EXPECTED
