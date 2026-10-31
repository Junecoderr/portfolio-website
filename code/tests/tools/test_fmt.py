"""`tb fmt`: canonical layout, idempotent, never changes what a program means."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tbcore.parser import parse  # noqa: E402
from tbtools import fmt  # noqa: E402

REPO_FILES = sorted(
    p for folder in ("examples", "apps", "packages", "scripts", "spec")
    for p in (ROOT / folder).rglob("*.tb")
    if "node_modules" not in p.parts
) + [ROOT / "welcome.tb"]


def comments(source: str) -> list[str]:
    from tbcore.lexer import tokenize

    found = []
    for tok in tokenize(source):
        found += [c for c in tok.comments_before if c]
        if tok.trailing_comment:
            found.append(tok.trailing_comment)
    return found


@pytest.mark.parametrize("path", REPO_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_round_trip_on_every_repo_file(path):
    source = path.read_text(encoding="utf-8")
    once = fmt.format_source(source)
    assert fmt.ast_signature(parse(once)) == fmt.ast_signature(parse(source))
    assert fmt.format_source(once) == once, "formatting twice changed the result"
    assert comments(once) == comments(source)


def test_repo_is_formatted():
    """`tb fmt --check` passes on the code that ships with TB."""
    result = subprocess.run([sys.executable, str(ROOT / "tb.py"), "fmt", "--check",
                             "examples", "apps", "packages", "scripts", "spec", "welcome.tb"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


CASES = [
    ("let   x=1+2*3", "let x = 1 + 2 * 3\n"),
    ("say(  1,2 )", "say(1, 2)\n"),
    ("a = 1; b = 2", "a = 1\nb = 2\n"),
    ("if x { say(1) } else if y { say(2) } else { say(3) }",
     "if x {\n  say(1)\n} else if y {\n  say(2)\n} else {\n  say(3)\n}\n"),
    ("fn f(a,b=2,...rest)->number{return a}", "fn f(a, b = 2, ...rest) -> number {\n  return a\n}\n"),
    ("let x = 1\n\n\n\nlet y = 2", "let x = 1\n\nlet y = 2\n"),
    ("say(0x1F, 1_000, r\"a\\n\", b\"\\x00\", f\"{x:>8}\")", "say(0x1F, 1_000, r\"a\\n\", b\"\\x00\", f\"{x:>8}\")\n"),
    ("let f = x=>x*2", "let f = x => x * 2\n"),
    ("let v = if a then 1 else 2", "let v = if a then 1 else 2\n"),
    ("say(not a == b, (not a) == b, -x ** 2, (-x) ** 2, (a + b) * c)",
     "say(not a == b, (not a) == b, -x ** 2, (-x) ** 2, (a + b) * c)\n"),
    ("say(!done)", "say(not done)\n"),
    ("class A extends B { let n = 1\n static fn make() { return A() } }",
     "class A extends B {\n  let n = 1\n  static fn make() {\n    return A()\n  }\n}\n"),
    ("enum Level { low,medium,high }", "enum Level { low, medium, high }\n"),
    ("let m = match x { 1 => \"one\"\n [a, ...rest] => a\n _ => { say(x); nil } }",
     "let m = match x {\n  1 => \"one\"\n  [a, ...rest] => a\n  _ => {\n    say(x)\n    nil\n  }\n}\n"),
    ("let a, b = f()\nreturn_value = 1", "let a, b = f()\nreturn_value = 1\n"),
    ("try { x() } catch err: FileError or ParseError { } finally { y() }",
     "try {\n  x()\n} catch err: FileError or ParseError {} finally {\n  y()\n}\n"),
    ("let f = async fn(n) { return await g(n) }", "let f = async fn(n) {\n  return await g(n)\n}\n"),
]


@pytest.mark.parametrize("source,expected", CASES)
def test_canonical_layout(source, expected):
    assert fmt.format_source(source) == expected
    assert fmt.format_source(expected) == expected


def test_long_lines_wrap_with_trailing_commas():
    source = 'let config = {"name": "Tanisha", "role": "analyst", "team": "blue", "shift": "night", "level": 3, "on_call": true}\n'
    out = fmt.format_source(source)
    assert out == ('let config = {\n  "name": "Tanisha",\n  "role": "analyst",\n  "team": "blue",\n'
                   '  "shift": "night",\n  "level": 3,\n  "on_call": true,\n}\n')
    call = "say(" + ", ".join(f"value_{i}" for i in range(20)) + ")\n"
    out = fmt.format_source(call)
    assert out.startswith("say(\n  value_0,\n") and out.endswith("  value_19,\n)\n")
    assert all(len(line) <= 100 for line in out.splitlines())


def test_last_function_argument_hugs():
    source = 'gui.button("OK", fn() { say("clicked") })\n'
    assert fmt.format_source(source) == 'gui.button("OK", fn() {\n  say("clicked")\n})\n'


def test_comments_and_blank_lines_survive():
    source = (
        "# header\n\n"
        "let items = [ # the list\n  1,  # one\n\n  2\n  # before the end\n]\n"
        "fn f() {   # opener\n  # inside\n  return 1  # trailing\n  # at the end\n}\n"
        "# the end\n"
    )
    out = fmt.format_source(source)
    assert comments(out) == comments(source)
    assert "  1,  # one\n\n  2,\n  # before the end\n]" in out
    assert out.startswith("# header\n\nlet items = [")
    assert fmt.format_source(out) == out


def test_comment_in_an_odd_place_keeps_the_statement_as_written():
    source = "let total = 1 +  # first part\n    2\nsay(total)\n"
    out = fmt.format_source(source)
    assert out == source


def test_cli_check_write_and_stdin(tmp_path):
    messy = tmp_path / "messy.tb"
    messy.write_text("let   x=1\n", encoding="utf-8")
    tidy = tmp_path / "tidy.tb"
    tidy.write_text("let x = 1\n", encoding="utf-8")
    tb = [sys.executable, str(ROOT / "tb.py"), "fmt"]
    check = subprocess.run(tb + ["--check", str(tmp_path)], capture_output=True, text=True)
    assert check.returncode == 1 and "messy.tb" in check.stdout and "tidy.tb" not in check.stdout
    assert messy.read_text() == "let   x=1\n"
    write = subprocess.run(tb + [str(tmp_path)], capture_output=True, text=True)
    assert write.returncode == 0 and messy.read_text() == "let x = 1\n"
    assert subprocess.run(tb + ["--check", str(tmp_path)], capture_output=True).returncode == 0
    piped = subprocess.run(tb + ["-"], input="say( 1 )", capture_output=True, text=True)
    assert piped.returncode == 0 and piped.stdout == "say(1)\n"
    broken = subprocess.run(tb + ["-"], input="let = 1", capture_output=True, text=True)
    assert broken.returncode == 1 and "Oops" in broken.stderr and broken.stdout == ""


def test_crlf_files_keep_their_line_endings(tmp_path):
    """On Windows, git and editors save CRLF: a tidy CRLF file is tidy, and
    tb fmt rewrites a messy one with CRLF still."""
    tidy = tmp_path / "tidy.tb"
    tidy.write_bytes(b"let x = 1\r\nsay(x)\r\n")
    messy = tmp_path / "messy.tb"
    messy.write_bytes(b"let   x=1\r\nsay( x )\r\n")
    tb = [sys.executable, str(ROOT / "tb.py"), "fmt"]
    check = subprocess.run(tb + ["--check", str(tmp_path)], capture_output=True, text=True)
    assert check.returncode == 1 and "messy.tb" in check.stdout and "tidy.tb" not in check.stdout
    assert subprocess.run(tb + [str(tmp_path)], capture_output=True).returncode == 0
    assert messy.read_bytes() == b"let x = 1\r\nsay(x)\r\n"
    assert tidy.read_bytes() == b"let x = 1\r\nsay(x)\r\n"


def test_tb_toml_fmt_options(tmp_path):
    """[fmt] line_width and indent in the project's tb.toml (a CRLF tb.toml is fine)."""
    from tbtools.pkg import PkgError, fmt_options

    source = "fn f(a) {\nif a { return [1111111, 2222222, 3333333, 4444444, 5555555] }\n}\n"
    assert fmt.format_source(source, {"line_width": 40, "indent": 4}) == (
        "fn f(a) {\n    if a {\n        return [\n            1111111,\n            2222222,\n"
        "            3333333,\n            4444444,\n            5555555,\n        ]\n    }\n}\n")
    (tmp_path / "src").mkdir()
    prog = tmp_path / "src" / "prog.tb"
    prog.write_text(source, encoding="utf-8")
    assert fmt_options(str(prog)) == {}
    (tmp_path / "tb.toml").write_bytes(b'[package]\r\nname = "demo"\r\n\r\n[fmt]\r\nline_width = 40\r\nindent = 4\r\n')
    assert fmt_options(str(prog)) == {"line_width": 40, "indent": 4}
    tb = [sys.executable, str(ROOT / "tb.py"), "fmt"]
    assert subprocess.run(tb + [str(prog)], capture_output=True, cwd=tmp_path).returncode == 0
    assert prog.read_text(encoding="utf-8").startswith("fn f(a) {\n    if a {\n        return [\n")
    for bad, words in (("width = 80", "don't know: 'width'"), ("indent = 0", "from 1 to 8"),
                       ("line_width = true", "from 40 to 200")):
        (tmp_path / "tb.toml").write_text(f"[fmt]\n{bad}\n", encoding="utf-8")
        with pytest.raises(PkgError, match=words):
            fmt_options(str(tmp_path))
    result = subprocess.run(tb + [str(prog)], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 1 and "Oops!" in result.stderr and "Traceback" not in result.stderr
