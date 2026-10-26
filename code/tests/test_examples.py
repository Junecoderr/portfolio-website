"""Run every example program and check its output.

For each ``examples/<name>.tb`` (plus ``welcome.tb`` at the repo root):

* ``examples/<name>.in`` (optional) holds the answers typed into ``ask()``,
  one per line. Programs that call ``ask(`` must have one.
* ``examples/<name>.out`` holds the expected output: one line per ``say()`` /
  ``care()`` call, exactly as ``tb.run`` passes it to ``output``. The prompts
  given to ``ask()`` are not part of it (they go to ``input_fn``), so for
  interactive programs it differs slightly from what a terminal shows.
  The expected output for ``welcome.tb`` lives in ``tests/welcome.out``.

``care()`` picks a random message, so its lines can't be compared exactly.
Instead of seeding the random generator (which would break whenever the list
of care messages changes), ``.out`` files use a placeholder line:

* ``♥ {care}``       matches any line starting with ``♥ `` (from ``care()``)
* ``♥ {care:NAME}``  matches any line starting with ``♥ NAME, `` (from ``care(NAME)``)

Every other line must match exactly. To regenerate an ``.out`` file after
changing an example, run it, copy the output, and swap the care line for the
placeholder.

Each code block on ``site/examples.html`` must also match its ``.tb`` file
exactly (after HTML-unescaping), so the website never drifts from the files.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

import tb

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.tb"))
CARE_PLACEHOLDER = re.compile(r"^♥ \{care(?::(?P<name>.+))?\}$")


def expected_path(program: Path) -> Path:
    if program.parent == ROOT:
        return ROOT / "tests" / (program.stem + ".out")
    return program.with_suffix(".out")


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def line_matches(expected: str, actual: str) -> bool:
    m = CARE_PLACEHOLDER.match(expected)
    if m is None:
        return expected == actual
    if m.group("name") is not None:
        prefix = "♥ " + m.group("name") + ", "
    else:
        prefix = "♥ "
    return actual.startswith(prefix) and len(actual) > len(prefix)


def run_program(program: Path) -> list[str]:
    source = program.read_text(encoding="utf-8")
    in_path = program.with_suffix(".in")
    answers = read_lines(in_path) if in_path.exists() else []
    remaining = list(answers)
    lines: list[str] = []

    def input_fn(prompt: str) -> str:
        if not remaining:
            # Not EOFError: ask() would quietly turn that into "".
            raise AssertionError(f"{program.name} asked for more input than its .in file has")
        return remaining.pop(0)

    tb.run(source, output=lines.append, input_fn=input_fn)
    assert not remaining, f"{program.name} did not use all of its .in answers: {remaining}"
    return lines


ALL_PROGRAMS = EXAMPLE_FILES + [ROOT / "welcome.tb"]


def test_there_are_enough_examples():
    assert len(EXAMPLE_FILES) >= 6
    assert (EXAMPLES / "care_bot.tb").exists()


@pytest.mark.parametrize("program", ALL_PROGRAMS, ids=lambda p: p.stem)
def test_example_output(program: Path):
    expected = read_lines(expected_path(program))
    actual = run_program(program)
    assert len(actual) == len(expected), (
        f"{program.name}: expected {len(expected)} lines, got {len(actual)}:\n"
        + "\n".join(actual)
    )
    for number, (want, got) in enumerate(zip(expected, actual), start=1):
        assert line_matches(want, got), f"{program.name} line {number}: expected {want!r}, got {got!r}"


@pytest.mark.parametrize("program", EXAMPLE_FILES, ids=lambda p: p.stem)
def test_example_header(program: Path):
    lines = read_lines(program)
    assert len(lines) >= 2
    assert re.match(r"^# Title: \S", lines[0]), f"{program.name}: first line must be '# Title: ...'"
    assert re.match(r"^# About: \S", lines[1]), f"{program.name}: second line must be '# About: ...'"


@pytest.mark.parametrize("program", EXAMPLE_FILES, ids=lambda p: p.stem)
def test_interactive_examples_have_input(program: Path):
    interactive = "ask(" in program.read_text(encoding="utf-8")
    assert program.with_suffix(".in").exists() == interactive


def site_code_blocks() -> dict[str, str]:
    page = (ROOT / "site" / "examples.html").read_text(encoding="utf-8")
    blocks = re.findall(
        r'<pre class="tb-code"[^>]*\bid="code-([\w-]+)"[^>]*><code>(.*?)</code></pre>',
        page,
        flags=re.DOTALL,
    )
    return {name: html.unescape(code) for name, code in blocks}


def test_examples_page_has_every_example():
    blocks = site_code_blocks()
    names = {p.stem for p in EXAMPLE_FILES} | {"welcome"}
    assert set(blocks) == names


@pytest.mark.parametrize("program", ALL_PROGRAMS, ids=lambda p: p.stem)
def test_examples_page_code_matches_file(program: Path):
    blocks = site_code_blocks()
    assert program.stem in blocks, f"site/examples.html has no code block for {program.stem}"
    file_code = program.read_text(encoding="utf-8").rstrip("\n")
    assert blocks[program.stem].rstrip("\n") == file_code, (
        f"The code for {program.stem} on site/examples.html is out of date. "
        f"Copy {program.relative_to(ROOT)} into it again (escape < > &)."
    )


def test_docs_page_examples_run():
    """Every runnable snippet on the Learn page runs; the 'mistake' ones give a friendly Oops."""
    page = (ROOT / "site" / "docs.html").read_text(encoding="utf-8")
    blocks = re.findall(
        r'<pre class="tb-code" data-runnable([^>]*)><code>(.*?)</code></pre>', page, flags=re.DOTALL
    )
    assert len(blocks) >= 10
    for attrs, code in blocks:
        source = html.unescape(code)
        lines: list[str] = []
        if "data-expect-error" in attrs:
            with pytest.raises(tb.TBError) as err:
                tb.run(source, output=lines.append, input_fn=lambda prompt: "3")
            assert str(err.value).startswith("Oops! On line ")
        else:
            tb.run(source, output=lines.append, input_fn=lambda prompt: "3")
