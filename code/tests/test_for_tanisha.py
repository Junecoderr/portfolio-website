import random

import tb
from conftest import ROOT

EXPECTED = [
    "Hello, Tanisha!",
    "This language is called TB. Can you guess why?",
    "",
    "A few reasons I love you:",
    "1. your laugh",
    "2. the way you care about everyone",
    "3. how brave you are",
    "4. your terrible puns",
    "...and 4 is nowhere near all of them.",
    "",
    "Days I've been happy today: 3",
    "<3 <3 <3 <3 <3 ",
    "",
    "3...",
    "2...",
    "1...",
    "I love you more than 4 reasons can say.",
    "",
    "A note from TB, just for you:",
    "♥ Tanisha, proud of you, always.",
    "",
    "With love, always.",
]


def source():
    return (ROOT / "for_tanisha.tb").read_text(encoding="utf-8")


def test_for_tanisha_exact_output_with_seeded_care():
    lines = []
    interp = tb.Interpreter(output=lines.append, rng=random.Random(42))
    tb.run(source(), interpreter=interp)
    assert lines == EXPECTED


def test_for_tanisha_care_line_any_seed():
    for seed in range(20):
        lines = []
        tb.run(source(), interpreter=tb.Interpreter(output=lines.append, rng=random.Random(seed)))
        assert lines[:19] == EXPECTED[:19] and lines[20:] == EXPECTED[20:]
        assert lines[19].startswith("♥ Tanisha, ")


def test_for_tanisha_via_cli(capsys, monkeypatch):
    real_random = random.Random
    monkeypatch.setattr(tb.random, "Random", lambda *a: real_random(42))
    code = tb.main([str(ROOT / "for_tanisha.tb")])
    out = capsys.readouterr()
    assert code == 0 and out.err == ""
    assert out.out.splitlines() == EXPECTED
