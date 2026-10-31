"""`tb debug` (scripted stdin), `tb build` (build and run a bundle), `tb doc FILE.tb`
and the spec conformance tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TB = [sys.executable, str(ROOT / "tb.py")]

PROGRAM = """fn square(n) {
  let result = n * n
  return result
}
let total = 0
for i in [1, 2, 3] {
  total += square(i)
}
say(f"total {total}")
"""


def run_tb(args, cwd, stdin="", env=None):
    full_env = dict(os.environ, **(env or {}))
    return subprocess.run(TB + args, cwd=cwd, input=stdin, capture_output=True, text=True,
                          timeout=120, env=full_env)


# -- tb debug -------------------------------------------------------------------


def test_debugger_breakpoints_step_print_locals(tmp_path):
    (tmp_path / "prog.tb").write_text(PROGRAM, encoding="utf-8")
    script = "\n".join([
        "break 2", "run", "locals", "print n * 10", "backtrace", "next", "print result",
        "continue", "print total", "delete", "continue", "quit",
    ]) + "\n"
    result = run_tb(["debug", "prog.tb"], tmp_path, script)
    out = result.stdout
    assert result.returncode == 0, result.stderr
    assert "Breakpoint at prog.tb:2" in out
    assert "-> prog.tb:2   let result = n * n" in out
    assert "  n = 1" in out
    assert "(tb) 10\n" in out
    assert "square() at prog.tb:2" in out and "<main> at prog.tb:7" in out
    assert "-> prog.tb:3   return result" in out
    assert "(tb) 1\n" in out
    assert "(tb) 1\n" in out and "(tb) 1\n" in out
    assert "total 14" in out and "The program finished." in out


SCOPES = """let total = 0
let label = "x"
fn outer(a) {
  let b = a + 1
  for i in [1] {
    let c = i
    say(c)
  }
  return b
}
for k in [5] {
  total += outer(k)
}
say(total)
"""


@pytest.mark.parametrize("engine", ["vm", "tree"])
def test_debugger_locals_and_globals(tmp_path, engine):
    (tmp_path / "prog.tb").write_text(SCOPES, encoding="utf-8")
    script = "break 7\nbreak 14\nrun\nlocals\nprint 0\nglobals\nprint 1\ncontinue\nlocals\nquit\n"
    out = run_tb(["debug", "prog.tb"], tmp_path, script, env={"TB_ENGINE": engine}).stdout
    # "print 0" / "print 1" mark where each command's output ends
    in_fn, rest = out.split("-> prog.tb:7")[1].split("(tb) 0\n", 1)
    in_globals = rest.split("(tb) 1\n")[0]
    top = out.split("-> prog.tb:14")[1]
    for name in ("a = 5", "b = 6", "i = 1", "c = 1"):
        assert f"  {name}" in in_fn
    assert "total" not in in_fn and "label" not in in_fn and "outer" not in in_fn
    assert "  total = 0" in in_globals and '  label = "x"' in in_globals
    assert "  a = " not in in_globals and "  b = " not in in_globals
    assert "  total = 6" in top   # at the top level, locals are the module's names
    assert "globals" in run_tb(["debug", "--help"], tmp_path).stdout


CLOSURE = """fn outer(a) {
  let hidden = 7
  let shared = a * 2
  fn inner(x) {
    let y = x + shared
    return y
  }
  return inner(1)
}
say(outer(5))
"""


@pytest.mark.parametrize("engine", ["vm", "tree"])
def test_debugger_locals_in_a_closure_leave_out_the_outer_function(tmp_path, engine):
    (tmp_path / "prog.tb").write_text(CLOSURE, encoding="utf-8")
    out = run_tb(["debug", "prog.tb"], tmp_path, "break 6\nrun\nlocals\nprint 0\nquit\n",
                 env={"TB_ENGINE": engine}).stdout
    in_inner = out.split("-> prog.tb:6")[1].split("(tb) 0\n")[0]
    assert "  x = 1" in in_inner and "  y = 11" in in_inner
    for name in ("a", "hidden", "shared", "inner"):
        assert f"  {name} = " not in in_inner


def test_debugger_step_into_and_out(tmp_path):
    (tmp_path / "prog.tb").write_text(PROGRAM, encoding="utf-8")
    script = "step\nstep\nstep\nstep\nstep\nout\nprint total\nquit\n"
    out = run_tb(["debug", "prog.tb"], tmp_path, script).stdout
    lines = [line for line in out.splitlines() if "-> prog.tb:" in line]
    assert [ln.split("-> prog.tb:")[1].split()[0] for ln in lines] == ["1", "5", "6", "7", "2", "7"]
    assert "(tb) 1\n" in out   # out: back in the loop, after total += square(1)


def test_debugger_reports_errors_and_quits_on_eof(tmp_path):
    (tmp_path / "bad.tb").write_text('let x = 1\nsay(x + nope)\n', encoding="utf-8")
    result = run_tb(["debug", "bad.tb"], tmp_path, "run\n")
    assert "I don't know what 'nope' is" in result.stdout
    assert "The program stopped because of the error above." in result.stdout
    assert result.returncode == 1
    missing = run_tb(["debug", "missing.tb"], tmp_path)
    assert missing.returncode == 1 and "couldn't find" in missing.stderr


# -- tb build ----------------------------------------------------------------------


def test_build_from_source_and_run_the_launcher(tmp_path):
    app = tmp_path / "hello-app"
    app.mkdir()
    (app / "main.tb").write_text('import "helpers.tb" as h\nimport os\nimport fs\n'
                                 'say(h.greet("Tanisha"), os.args, fs.read(fs.here("data.txt")).trim())\n',
                                 encoding="utf-8")
    (app / "helpers.tb").write_text('fn greet(name) { return f"Hello, {name}!" }\n', encoding="utf-8")
    (app / "data.txt").write_text("from data\n", encoding="utf-8")
    out = tmp_path / "bundle"
    result = run_tb(["build", str(app / "main.tb"), "--out", str(out)], tmp_path)
    assert result.returncode == 0, result.stderr
    for name in ("hello-app", "hello-app.tb", "helpers.tb", "data.txt", "run.sh", "run.bat", "README.txt",
                 "runtime/tb.py", "runtime/tbcore/parser.py", "runtime/tblib/fs.py"):
        assert (out / name).exists(), name
    if os.name == "nt":
        pytest.skip("the shell launcher needs a POSIX shell")
    run = subprocess.run([str(out / "hello-app"), "one", "two.tb"], capture_output=True, text=True,
                         timeout=120, env=dict(os.environ, TB_PYTHON=sys.executable), cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == 'Hello, Tanisha! ["one", "two.tb"] from data'
    # building again replaces the bundle; a folder with other files is refused
    assert run_tb(["build", str(app / "main.tb"), "--out", str(out)], tmp_path).returncode == 0
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    refused = run_tb(["build", str(app / "main.tb"), "--out", str(other)], tmp_path)
    assert refused.returncode == 1 and "--force" in refused.stderr


def test_build_single_file_copies_only_its_imports(tmp_path):
    (tmp_path / "tool.tb").write_text('import "lib/util.tb" as u\nsay(u.x)\n', encoding="utf-8")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.tb").write_text("let x = 1\n", encoding="utf-8")
    (tmp_path / "unrelated.tb").write_text("say(2)\n", encoding="utf-8")
    result = run_tb(["build", "tool.tb", "--name", "tool"], tmp_path)
    assert result.returncode == 0, result.stderr
    out = tmp_path / "dist" / "tool"
    assert (out / "tool.tb").exists() and (out / "lib" / "util.tb").exists()
    assert not (out / "unrelated.tb").exists()


# -- tb doc FILE.tb ------------------------------------------------------------------

LIB = '''# shapes: small geometry helpers.
#
# Everything works in centimetres.

## The circle constant.
const PI = 3.14159

## The area of a circle.
## r is the radius.
fn area(r) {
  return PI * r * r
}

# A plain comment works as a doc too.
class Box {
  ## How wide it is.
  let width = 1

  ## The box's volume.
  fn volume(depth = 1) {
    return self.width * depth
  }
}

fn _private() {
}
'''


def test_doc_text_markdown_html(tmp_path):
    (tmp_path / "shapes.tb").write_text(LIB, encoding="utf-8")
    text = run_tb(["doc", "shapes.tb"], tmp_path)
    assert text.returncode == 0, text.stderr
    out = text.stdout
    assert out.startswith("shapes.tb - shapes: small geometry helpers.")
    assert "const PI = 3.14159\n      The circle constant." in out
    assert "fn area(r)\n      The area of a circle.\n      r is the radius." in out
    assert "class Box\n      A plain comment works as a doc too." in out
    assert "fn volume(depth = 1)" in out and "The box's volume." in out
    assert "_private" not in out
    md = run_tb(["doc", "shapes.tb", "--md"], tmp_path).stdout
    assert "# shapes" in md and "## area" in md and "```tb\nfn area(r)\n```" in md
    page = run_tb(["doc", str(tmp_path), "--html", "--out", "docs.html"], tmp_path)
    assert page.returncode == 0
    html_text = (tmp_path / "docs.html").read_text(encoding="utf-8")
    assert html_text.startswith("<!doctype html>") and "The area of a circle." in html_text
    # tb doc NAME still shows a library
    assert run_tb(["doc", "fs"], tmp_path).stdout.startswith("fs - ")


def test_doc_respects_export():
    from tbtools.docgen import document_source

    info = document_source("export fn shown() { }\nfn hidden() { }\n", "m.tb")
    assert [item["name"] for item in info["items"]] == ["shown"]


# -- spec conformance -------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["vm", "tree"])
def test_spec_conformance(engine):
    result = run_tb(["test", "spec"], ROOT, env={"TB_ENGINE": engine})
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr
