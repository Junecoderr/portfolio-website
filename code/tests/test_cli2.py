"""TB 2.0 command line: program arguments, tb doc, tb test, exit codes."""

import subprocess
import sys

import pytest

import tb
from conftest import ROOT

STDLIB = ["csv", "detect", "encode", "fs", "gui", "hash", "http", "intel", "ioc", "json", "logs", "math", "net", "os", "random", "re", "report", "test", "text", "time"]


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(tb, "owns_console_window", lambda: False)


@pytest.fixture
def script(tmp_path):
    def make(text, name="prog.tb"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)
    return make


# -- running programs ---------------------------------------------------------------

def test_program_args_reach_os_args(capsys, script):
    path = script("import os\nsay(os.args, len(os.args))")
    assert tb.main([path, "alpha", "--beta", "3"]) == 0
    assert capsys.readouterr().out == '["alpha", "--beta", "3"] 3\n'


def test_double_dash_passes_tb_file_names(capsys, script):
    path = script("import os\nsay(os.args)")
    other = script("say(1)", "other.tb")
    assert tb.main([path, "--", other, "x"]) == 0
    shown = other.replace("\\", "\\\\")  # lists show text as you'd type it
    assert capsys.readouterr().out == f'["{shown}", "x"]\n'


def test_extra_tb_file_still_refused_without_double_dash(capsys, script):
    assert tb.main([script("say(1)"), "b.tb"]) == 2
    err = capsys.readouterr().err
    assert "just one .tb file" in err and "tb main.tb -- other.tb" in err


def test_unknown_option(capsys):
    assert tb.main(["--frobnicate"]) == 2
    assert "I don't know the option '--frobnicate'" in capsys.readouterr().err


def test_help_after_file_goes_to_program(capsys, script):
    path = script("import os\nsay(os.args)")
    assert tb.main([path, "--help"]) == 0
    assert capsys.readouterr().out == '["--help"]\n'


def test_help_mentions_new_commands(capsys):
    assert tb.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "tb doc" in out and "tb test" in out and "args" in out and "{name}" in out


def test_exit_code_from_program(capsys, script):
    assert tb.main([script('say("bye")\nexit(3)\nsay("never")')]) == 3
    assert capsys.readouterr().out == "bye\n"
    assert tb.main([script("import os\nos.exit(4)")]) == 4
    assert tb.main([script("exit()")]) == 0


def test_imports_relative_to_script_not_cwd(capsys, script, tmp_path, monkeypatch):
    script('let msg = "from helper"\n', "sub/helper.tb")
    main = script('import "helper.tb"\nsay(helper.msg)', "sub/main.tb")
    monkeypatch.chdir(ROOT)
    assert tb.main([main]) == 0
    assert capsys.readouterr().out == "from helper\n"


def test_error_in_imported_file_via_cli(capsys, script):
    script("fn f() {\n  return 1 / 0\n}\n", "lib.tb")
    assert tb.main([script('import "lib.tb"\nlib.f()')]) == 1
    # 3.0 adds a short call trace under uncaught errors
    assert capsys.readouterr().err.strip() == "Oops! On line 2 of lib.tb: You can't divide by zero.\n  in f()"


def test_real_process_library_errors_are_catchable(tmp_path):
    """Run tb.py as a script (module __main__): tblib must share its TBError."""
    prog = tmp_path / "p.tb"
    prog.write_text('import json\ntry { json.parse("{") } catch e { say("caught", e.kind) }\njson.parse("[")\n')
    done = subprocess.run([sys.executable, str(ROOT / "tb.py"), str(prog)], capture_output=True, text=True, timeout=60)
    assert done.stdout == "caught parse_error\n"  # 3.0: typed errors
    assert done.returncode == 1 and done.stderr.startswith("Oops! On line 3: That text isn't valid JSON")
    assert "Traceback" not in done.stderr


def test_real_process_version():
    done = subprocess.run([sys.executable, str(ROOT / "tb.py"), "--version"], capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "TB 3.1.0"


# -- tb doc -----------------------------------------------------------------------------

def test_doc_lists_every_library(capsys):
    assert tb.main(["doc"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("TB 3.1.0 standard library.")
    for name in STDLIB:
        assert f"\n  {name} " in out
    assert "Read and write CSV files." in out
    assert "_api" not in out and "tb doc NAME" in out


def test_doc_one_library(capsys):
    assert tb.main(["doc", "fs"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("fs - Files and folders")
    assert "import fs" in out
    assert "  fs.read(path) -> text\n      The whole text file." in out
    assert "fs.walk(folder?) -> list" in out


def test_doc_library_values(capsys):
    assert tb.main(["doc", "math"]) == 0
    out = capsys.readouterr().out
    assert "math.pi = 3.141592653589793" in out and "math.sqrt(x) -> number" in out


def test_doc_unknown_library(capsys):
    assert tb.main(["doc", "jsn"]) == 1
    assert "There's no library called 'jsn'. Did you mean 'json'?" in capsys.readouterr().err


def test_doc_builtins(capsys):
    assert tb.main(["doc", "builtins"]) == 0
    out = capsys.readouterr().out
    assert "say(...)" in out and "round(number, digits?) -> number" in out
    assert "Text methods" in out and "upper() -> text" in out
    assert "List methods" in out and "sort_by(fn) -> list" in out
    assert "Map methods" in out and "Number methods" in out


def test_every_stdlib_function_is_documented():
    assert tb.list_libraries() == STDLIB or set(STDLIB) <= set(tb.list_libraries())
    for name in STDLIB:
        info = tb.describe_library(name)
        assert info["doc"], name
        assert info["functions"], name
        for fn in info["functions"]:
            assert fn["signature"].startswith(fn["name"] + "("), (name, fn)
            assert fn["doc"], (name, fn["name"])
        for value in info["values"]:
            assert value["doc"], (name, value["name"])


def test_every_method_and_builtin_is_documented():
    info = tb.describe_builtins()
    for entry in info["functions"]:
        assert entry["signature"].startswith(entry["name"] + "(") and entry["doc"]
    for kind, entries in info["methods"].items():
        assert entries, kind
        for entry in entries:
            assert entry["signature"].startswith(entry["name"] + "(") and entry["doc"], (kind, entry)


def test_describe_tb_written_library(tmp_path, monkeypatch):
    lib = tmp_path / "tblib"
    lib.mkdir()
    (lib / "greet.tb").write_text("# Friendly greetings.\n\n# Say hi.\nfn hi(name) { return \"hi \" + name }\nlet VERSION = \"1\"\n")
    original = tb.library_dirs
    monkeypatch.setattr(tb, "library_dirs", lambda: original() + [str(lib)])
    info = tb.describe_library("greet")
    assert info["doc"] == "Friendly greetings."
    assert info["functions"] == [{"name": "hi", "signature": "hi(name)", "doc": "Say hi."}]
    assert info["values"] == [{"name": "VERSION", "value": '"1"', "doc": ""}]
    assert tb.run('import greet\nsay(greet.hi("T"))', output=lambda s: None)


# -- tb test ---------------------------------------------------------------------------------

PASSING = 'import test\ntest.equal(1 + 1, 2)\ntest.true("x".contains("x"))\n'
FAILING = 'import test\ntest.equal(2 * 2, 5, "maths")\ntest.equal(1, 1)\n'


def test_tb_test_all_passing(capsys, script, tmp_path):
    script(PASSING, "a_test.tb")
    script(PASSING, "sub/b_test.tb")
    script('say("not a test file")', "helper.tb")
    assert tb.main(["test", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "a_test.tb" in out and "b_test.tb" in out and "not a test file" not in out
    assert "All 2 checks passed." in out
    assert out.strip().endswith("OK: 4 checks passed in 2 test files.")


def test_tb_test_with_failure(capsys, script, tmp_path):
    script(PASSING, "a_test.tb")
    script(FAILING, "b_test.tb")
    assert tb.main(["test", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "  FAIL on line 2: expected 5, but got 4 (maths)" in out
    assert "1 of 2 checks failed." in out
    assert out.strip().endswith("FAILED: 1 failed, 3 passed, in 1 of 2 test files.")


def test_tb_test_crashing_file(capsys, script, tmp_path):
    script('import test\ntest.equal(1, 1)\nsay(nope)\ntest.equal(2, 2)\n', "crash_test.tb")
    assert tb.main(["test", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "Oops! On line 3: I don't know what 'nope' is." in out
    assert "stopped early" in out and "FAILED" in out


def test_tb_test_exit_codes_inside_tests(capsys, script, tmp_path):
    script("import test\ntest.true(true)\nexit(0)\n", "ok_test.tb")
    assert tb.main(["test", str(tmp_path)]) == 0
    script("exit(2)\n", "bad_test.tb")
    assert tb.main(["test", str(tmp_path)]) == 1
    assert "stopped with exit(2)" in capsys.readouterr().out


def test_tb_test_single_file_and_relative_imports(capsys, script, tmp_path):
    script('fn add(a, b) { return a + b }\n', "lib/maths.tb")
    path = script('import test\nimport "maths.tb"\ntest.equal(maths.add(2, 3), 5)\n', "lib/maths_test.tb")
    assert tb.main(["test", path]) == 0
    assert "OK: 1 checks passed in 1 test file." in capsys.readouterr().out


def test_tb_test_no_files(capsys, tmp_path):
    assert tb.main(["test", str(tmp_path)]) == 1
    assert "No test files found" in capsys.readouterr().out


def test_tb_test_missing_folder(capsys, tmp_path):
    assert tb.main(["test", str(tmp_path / "nope")]) == 1
    assert "couldn't find" in capsys.readouterr().err


def test_tb_test_defaults_to_current_folder(capsys, script, tmp_path, monkeypatch):
    script(PASSING, "x_test.tb")
    script(FAILING, ".hidden/y_test.tb")
    monkeypatch.chdir(tmp_path)
    assert tb.main(["test"]) == 0
    assert "x_test.tb" in capsys.readouterr().out


def test_running_a_test_file_directly_prints_summary(capsys, script):
    assert tb.main([script(FAILING, "f_test.tb")]) == 1
    out = capsys.readouterr().out
    assert "FAIL on line 2" in out and out.strip().endswith("1 of 2 checks failed.")
    assert tb.main([script(PASSING, "p_test.tb")]) == 0
    assert capsys.readouterr().out.strip().endswith("All 2 checks passed.")


# -- REPL --------------------------------------------------------------------------------

def test_repl_new_syntax_and_exit(capsys, monkeypatch):
    answers = ['let m = {"a": 1}', "m.a += 1", "m", 'f"a is {m.a}"', "import math", "math.floor(2.5)", "exit(5)"]

    def fake_input(prompt=""):
        if not answers:
            raise EOFError
        return answers.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    assert tb.main([]) == 5
    assert capsys.readouterr().out.splitlines()[1:] == ['{"a": 2}', '"a is 2"', "2"]


def test_repl_multiline_map(capsys, monkeypatch):
    answers = ["let m = {", '"x": 1,', "}", "m.x"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0) if answers else (_ for _ in ()).throw(EOFError))
    assert tb.main([]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "1"
