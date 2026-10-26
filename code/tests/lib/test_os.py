import os
import sys

import pytest

import tb
from conftest import run_tb, tb_error


def test_values():
    out = run_tb("import os\nsay(os.args, os.platform, type(os.user), os.script)")
    assert out == [f'[] {dict(darwin="mac", linux="linux", win32="windows").get(sys.platform, sys.platform)} text ']


def test_args_from_run():
    lines = []
    tb.run("import os\nsay(os.args)", output=lines.append, args=["a", "b"])
    assert lines == ['["a", "b"]']


def test_env(monkeypatch):
    monkeypatch.setenv("TB_TEST_VALUE", "hello")
    monkeypatch.delenv("TB_TEST_MISSING", raising=False)
    assert run_tb('import os\nsay(os.env("TB_TEST_VALUE"), os.env("TB_TEST_MISSING"), os.env("TB_TEST_MISSING", "dflt"))') == ["hello nil dflt"]


def test_run_list_has_no_shell():
    py = sys.executable.replace("\\", "\\\\")
    src = f'import os\nlet r = os.run(["{py}", "-c", "import sys; print(sys.argv[1]); sys.exit(3)", "a; echo injected"])\nsay(r.code, r.out.trim(), r.err)'
    assert run_tb(src) == ["3 a; echo injected "]


def test_run_text_uses_shell():
    out = run_tb('import os\nlet r = os.run("echo one && echo two")\nsay(r.code, r.out.lines())')
    # cmd.exe keeps the space before && ("one "); other shells don't.
    assert out in (['0 ["one", "two"]'], ['0 ["one ", "two"]'])


def test_run_input_cwd_and_stderr(tmp_path):
    py = sys.executable.replace("\\", "\\\\")
    code = "import sys, os; data = sys.stdin.read(); print(data.upper()); print(os.path.basename(os.getcwd())); print('oops', file=sys.stderr)"
    src = f'import os\nlet r = os.run(["{py}", "-c", "{code}"], {{"input": "hi", "cwd": "{str(tmp_path).replace(chr(92), chr(92) * 2)}"}})\nsay(r.out.lines(), r.err.trim())'
    assert run_tb(src) == [f'["HI", "{tmp_path.name}"] oops']


def test_run_errors(tmp_path):
    py = sys.executable.replace("\\", "\\\\")
    assert "I couldn't find the program 'definitely-not-a-program-xyz'" in str(
        tb_error('import os\nos.run(["definitely-not-a-program-xyz"])'))
    err = tb_error(f'import os\nos.run(["{py}", "-c", "import time; time.sleep(5)"], {{"timeout": 0.2}})')
    assert "took longer than 0.2 seconds" in str(err) and err.fields == {"timeout": True}
    assert "needs at least the program's name" in str(tb_error("import os\nos.run([])"))
    assert "needs a command, but got empty text" in str(tb_error('import os\nos.run("  ")'))
    assert "os.run() needs command to be text or a list, not a number." in str(tb_error("import os\nos.run(5)"))
    assert "timeout bigger than 0" in str(tb_error('import os\nos.run("echo", {"timeout": 0})'))
    assert "couldn't find the folder" in str(tb_error(f'import os\nos.run(["{py}"], {{"cwd": "/no/such/dir/xyz"}})'))


def test_run_refused_in_browser(monkeypatch):
    monkeypatch.setattr(sys, "platform", "emscripten")
    err = tb_error('import os\nos.run("echo hi")')
    assert "os.run() needs the TB app on your computer" in str(err)


def test_quote_and_exit():
    assert run_tb('import os\nsay(os.quote("a b\'c"))') == ["'a b'\"'\"'c'"]
    with pytest.raises(tb.TBExit) as info:
        run_tb("import os\nos.exit(7)")
    assert info.value.code == 7
    assert "os.exit() needs code to be a whole number" in str(tb_error('import os\nos.exit("x")'))
