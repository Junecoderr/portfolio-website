import builtins
import sys

import pytest

import tb


@pytest.fixture
def script(tmp_path):
    def make(text, name="prog.tb", encoding="utf-8"):
        path = tmp_path / name
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding=encoding)
        return str(path)
    return make


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    """Tests run on any OS; never wait for Enter unless a test asks for it."""
    monkeypatch.setattr(tb, "owns_console_window", lambda: False)


def feed_input(monkeypatch, lines):
    """Monkeypatch input(): returns the lines, then raises EOFError."""
    prompts = []
    remaining = list(lines)

    def fake_input(prompt=""):
        prompts.append(prompt)
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    monkeypatch.setattr(builtins, "input", fake_input)
    return prompts


# -- flags --------------------------------------------------------------------

def test_version(capsys):
    assert tb.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "TB 1.0.0"
    assert tb.main(["-V"]) == 0
    assert capsys.readouterr().out.strip() == "TB 1.0.0"


def test_version_constant():
    assert tb.__version__ == "1.0.0"


def test_help(capsys):
    assert tb.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "Usage:" in out and "tb FILE.tb" in out and "--version" in out
    assert tb.main(["-h"]) == 0


# -- running files ------------------------------------------------------------

def test_run_file(capsys, script):
    assert tb.main([script('say("hi")\nsay(1 + 1)')]) == 0
    out = capsys.readouterr()
    assert out.out == "hi\n2\n" and out.err == ""


def test_run_file_with_bom_and_crlf(capsys, script):
    path = script(b'\xef\xbb\xbfsay("bom")\r\nsay("crlf")\r\n')
    assert tb.main([path]) == 0
    assert capsys.readouterr().out == "bom\ncrlf\n"


def test_run_file_utf16_notepad(capsys, script):
    path = script('say("héllo ♥")\n', encoding="utf-16")
    assert tb.main([path]) == 0
    assert capsys.readouterr().out == "héllo ♥\n"


def test_run_file_windows_ansi(capsys, script):
    path = script('say("café")\n'.encode("cp1252"))
    assert tb.main([path]) == 0
    assert capsys.readouterr().out == "café\n"


def test_missing_file(capsys):
    assert tb.main(["definitely_missing_file.tb"]) == 1
    err = capsys.readouterr().err
    assert "Oops! I couldn't find the file 'definitely_missing_file.tb'" in err


def test_directory_instead_of_file(capsys, tmp_path):
    assert tb.main([str(tmp_path)]) == 1
    assert "Oops! I couldn't open" in capsys.readouterr().err


def test_tb_error_exit_code_and_stderr(capsys, script):
    assert tb.main([script('say("before")\nsay(oops)')]) == 1
    out = capsys.readouterr()
    assert out.out == "before\n"
    assert out.err.strip() == "Oops! On line 2: I don't know what 'oops' is."
    assert "Traceback" not in out.err


def test_syntax_error_exit_code(capsys, script):
    assert tb.main([script('say("unclosed')]) == 1
    assert "never ends" in capsys.readouterr().err


def test_infinite_recursion_via_main(capsys, script):
    assert tb.main([script("fn f(n) { return f(n + 1) }\nf(0)")]) == 1
    err = capsys.readouterr().err
    assert "called itself too many times" in err and "Traceback" not in err


def test_deep_recursion_via_main(capsys, script):
    src = "fn down(n) { if n == 0 { return 0 } return down(n - 1) + 1 }\nsay(down(1000))"
    assert tb.main([script(src)]) == 0
    assert capsys.readouterr().out == "1000\n"


def test_too_many_args(capsys, script):
    assert tb.main([script("say(1)"), script("say(2)", "b.tb")]) == 2
    assert "just one .tb file" in capsys.readouterr().err


def test_unexpected_python_error_is_friendly(capsys, script, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("kaboom")
    monkeypatch.setattr(tb, "run", broken)
    assert tb.main([script("say(1)")]) == 1
    err = capsys.readouterr().err
    assert "Something went wrong inside TB itself" in err and "kaboom" in err
    assert "Traceback" not in err


def test_memory_error_is_friendly(capsys, script, monkeypatch):
    def broken(*args, **kwargs):
        raise MemoryError
    monkeypatch.setattr(tb, "run", broken)
    assert tb.main([script("say(1)")]) == 1
    assert "more memory" in capsys.readouterr().err


def test_keyboard_interrupt(capsys, script, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(tb, "run", interrupted)
    assert tb.main([script("while true { }")]) == 130
    assert "Stopped." in capsys.readouterr().err


def test_ask_from_cli_uses_input(capsys, script, monkeypatch):
    prompts = feed_input(monkeypatch, ["Tanisha"])
    assert tb.main([script('let n = ask("Name? ")\nsay("Hi " + n)')]) == 0
    assert prompts == ["Name? "]
    assert capsys.readouterr().out == "Hi Tanisha\n"


def test_main_uses_sys_argv(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tb", "--version"])
    assert tb.main() == 0
    assert "TB 1.0.0" in capsys.readouterr().out


# -- REPL ---------------------------------------------------------------------

def test_repl_expressions_and_statements(capsys, monkeypatch):
    prompts = feed_input(monkeypatch, ["1 + 2", 'let x = "hi"', "x", 'say(x + "!")', "[1, x]", "nil", "exit"])
    assert tb.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("TB 1.0.0")
    assert out[1:] == ["3", '"hi"', "hi!", '[1, "hi"]']
    assert set(prompts) == {"tb> "}


def test_repl_multiline_block(capsys, monkeypatch):
    prompts = feed_input(monkeypatch, ["fn sq(n) {", "  return n * n", "}", "sq(7)", "quit"])
    assert tb.main([]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == ["49"]
    assert prompts == ["tb> ", "... ", "... ", "tb> ", "tb> "]


def test_repl_multiline_list_and_call(capsys, monkeypatch):
    feed_input(monkeypatch, ["let xs = [", "1,", "2]", "say(", "len(xs)", ")"])
    assert tb.main([]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == ["2", ""]


def test_repl_error_then_continue(capsys, monkeypatch):
    feed_input(monkeypatch, ["say(nope)", "1 / 0", "say(2)"])
    assert tb.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Oops! On line 1: I don't know what 'nope' is."
    assert out[2] == "Oops! On line 1: You can't divide by zero."
    assert out[3] == "2"
    # EOF prints an empty line and exits cleanly
    assert out[4] == ""


def test_repl_unclosed_string_reports_error(capsys, monkeypatch):
    feed_input(monkeypatch, ['say("oops', "say(1)"])
    assert tb.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "never ends" in out[1] and out[2] == "1"


def test_repl_keeps_state_and_closures(capsys, monkeypatch):
    feed_input(monkeypatch, [
        "fn mk() { let n = 0\nreturn fn() { n = n + 1\nreturn n } }",
        "let c = mk()", "c()", "c()",
    ])
    assert tb.main([]) == 0
    assert capsys.readouterr().out.splitlines()[1:3] == ["1", "2"]


def test_repl_deep_and_infinite_recursion(capsys, monkeypatch):
    feed_input(monkeypatch, [
        "fn down(n) { if n == 0 { return 0 } return down(n - 1) + 1 }",
        "down(1500)",
        "fn f() { return f() }",
        "f()",
        "down(10)",
    ])
    assert tb.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "1500"
    assert "called itself too many times" in out[2]
    assert out[3] == "10"


def test_repl_ctrl_c_while_running_keeps_repl(capsys, monkeypatch):
    feed_input(monkeypatch, ["boom()", "say(5)"])
    interp_cls = tb.Interpreter

    class Interrupting(interp_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            def boom(line):
                raise KeyboardInterrupt
            self.globals.vars["boom"] = tb.Builtin("boom", boom, (0, 0))

    monkeypatch.setattr(tb, "Interpreter", Interrupting)
    assert tb.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Stopped." and out[2] == "5"


def test_repl_unexpected_error_is_friendly(capsys, monkeypatch):
    feed_input(monkeypatch, ["say(1)", "say(2)"])
    real = tb._repl_run
    calls = []

    def flaky(interp, source):
        calls.append(source)
        if len(calls) == 1:
            raise RuntimeError("weird")
        return real(interp, source)

    monkeypatch.setattr(tb, "_repl_run", flaky)
    assert tb.main([]) == 0
    out = capsys.readouterr().out
    assert "Something went wrong inside TB itself" in out and "Traceback" not in out
    assert "2\n" in out


def test_repl_ctrl_c_at_prompt_exits(capsys, monkeypatch):
    def ctrl_c(prompt=""):
        raise KeyboardInterrupt
    monkeypatch.setattr(builtins, "input", ctrl_c)
    assert tb.main([]) == 0


def test_open_brackets():
    assert tb.open_brackets("fn f() {") == 1
    assert tb.open_brackets("[(") == 2
    assert tb.open_brackets("x }") == -1
    assert tb.open_brackets('"{') == 0  # unclosed string: let the parser report it
    assert tb.open_brackets('say("{")') == 0


# -- Windows pause-on-exit ------------------------------------------------------

class TestWindowsPause:
    def test_pauses_after_file_run_when_owning_console(self, capsys, script, monkeypatch):
        monkeypatch.setattr(tb, "owns_console_window", lambda: True)
        prompts = feed_input(monkeypatch, [""])
        assert tb.main([script('say("hi")')]) == 0
        assert prompts == ["\nPress Enter to close this window..."]
        assert capsys.readouterr().out == "hi\n"

    def test_pauses_after_error(self, capsys, script, monkeypatch):
        monkeypatch.setattr(tb, "owns_console_window", lambda: True)
        prompts = feed_input(monkeypatch, [""])
        assert tb.main([script("say(nope)")]) == 1
        assert len(prompts) == 1 and "Press Enter" in prompts[0]

    def test_pauses_after_missing_file(self, monkeypatch, capsys):
        monkeypatch.setattr(tb, "owns_console_window", lambda: True)
        prompts = feed_input(monkeypatch, [""])
        assert tb.main(["nope.tb"]) == 1
        assert len(prompts) == 1

    def test_pause_survives_eof(self, script, monkeypatch, capsys):
        monkeypatch.setattr(tb, "owns_console_window", lambda: True)
        feed_input(monkeypatch, [])  # input() raises EOFError
        assert tb.main([script("say(1)")]) == 0

    def test_no_pause_for_version_help_or_repl(self, monkeypatch, capsys):
        monkeypatch.setattr(tb, "owns_console_window", lambda: True)
        prompts = feed_input(monkeypatch, ["exit"])
        tb.main(["--version"])
        tb.main(["--help"])
        tb.main([])
        assert prompts == ["tb> "]

    def test_no_pause_when_not_owning_console(self, script, monkeypatch, capsys):
        prompts = feed_input(monkeypatch, [])
        assert tb.main([script("say(1)")]) == 0
        assert prompts == []


class TestOwnsConsoleWindow:
    """owns_console_window() itself, made testable by monkeypatching."""

    @pytest.fixture(autouse=True)
    def real_function(self, monkeypatch):
        # Undo the module-wide no_pause fixture for these tests.
        monkeypatch.setattr(tb, "owns_console_window", REAL_OWNS_CONSOLE)

    def fake_ctypes(self, monkeypatch, count):
        import types
        kernel32 = types.SimpleNamespace(GetConsoleProcessList=lambda buf, n: count)
        fake = types.SimpleNamespace(c_uint32=__import__("ctypes").c_uint32,
                                     windll=types.SimpleNamespace(kernel32=kernel32))
        monkeypatch.setitem(sys.modules, "ctypes", fake)

    def tty(self, monkeypatch, is_tty=True):
        import io

        class FakeStdin(io.StringIO):
            def isatty(self):
                return is_tty
        monkeypatch.setattr(sys, "stdin", FakeStdin())

    def test_false_on_other_platforms(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        self.fake_ctypes(monkeypatch, 1)
        self.tty(monkeypatch)
        assert tb.owns_console_window() is False

    def test_true_when_alone_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delattr(sys, "frozen", raising=False)
        self.fake_ctypes(monkeypatch, 1)
        self.tty(monkeypatch)
        assert tb.owns_console_window() is True

    def test_false_when_started_from_a_terminal(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delattr(sys, "frozen", raising=False)
        self.fake_ctypes(monkeypatch, 2)  # cmd.exe + python
        self.tty(monkeypatch)
        assert tb.owns_console_window() is False

    def test_frozen_onefile_counts_its_launcher(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        self.tty(monkeypatch)
        self.fake_ctypes(monkeypatch, 2)  # launcher + tb
        assert tb.owns_console_window() is True
        self.fake_ctypes(monkeypatch, 3)  # cmd.exe + launcher + tb
        assert tb.owns_console_window() is False

    def test_false_when_stdin_not_a_tty(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        self.fake_ctypes(monkeypatch, 1)
        self.tty(monkeypatch, is_tty=False)
        assert tb.owns_console_window() is False

    def test_false_when_stdin_missing(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        self.fake_ctypes(monkeypatch, 1)
        monkeypatch.setattr(sys, "stdin", None)
        assert tb.owns_console_window() is False

    def test_false_when_ctypes_fails(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        self.tty(monkeypatch)
        import types
        monkeypatch.setitem(sys.modules, "ctypes", types.SimpleNamespace())  # no windll
        assert tb.owns_console_window() is False

    def test_false_when_api_returns_zero(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        self.tty(monkeypatch)
        self.fake_ctypes(monkeypatch, 0)
        assert tb.owns_console_window() is False

    def test_false_on_this_machine_under_pytest(self):
        # stdin is captured by pytest (not a TTY), and this may not be Windows.
        assert tb.owns_console_window() is False


REAL_OWNS_CONSOLE = tb.owns_console_window


# -- Ctrl+C (the one-file exe forwards a second SIGINT) -------------------------

def test_run_file_ctrl_c_ignores_further_ctrl_c(capsys, script, monkeypatch):
    import signal

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(tb, "run", interrupted)
    assert tb.main([script("say(1)")]) == 130
    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN


def test_repl_ctrl_c_handler_restores_ctrl_c(capsys, monkeypatch):
    import signal
    calls = []
    monkeypatch.setattr(tb, "ignore_ctrl_c", lambda: calls.append("ignore") or "OLD")
    monkeypatch.setattr(tb, "restore_ctrl_c", lambda prev: calls.append(("restore", prev)))
    feed_input(monkeypatch, ["boom()"])

    class Interrupting(tb.Interpreter):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            def boom(line):
                raise KeyboardInterrupt
            self.globals.vars["boom"] = tb.Builtin("boom", boom, (0, 0))

    monkeypatch.setattr(tb, "Interpreter", Interrupting)
    assert tb.main([]) == 0
    assert calls == ["ignore", ("restore", "OLD")]


def test_ignore_and_restore_ctrl_c_really_work():
    import signal
    before = signal.getsignal(signal.SIGINT)
    previous = tb.ignore_ctrl_c()
    assert previous is before
    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    tb.restore_ctrl_c(previous, settle=0)
    assert signal.getsignal(signal.SIGINT) is before


def test_ignore_ctrl_c_is_safe_outside_main_thread():
    import threading
    results = []
    t = threading.Thread(target=lambda: results.append(tb.ignore_ctrl_c()))
    t.start()
    t.join()
    assert results == [None]
    tb.restore_ctrl_c(None)  # no-op


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_double_ctrl_c_in_real_process_has_no_traceback(tmp_path):
    import signal
    import subprocess
    import time
    from conftest import ROOT

    prog = tmp_path / "loop.tb"
    prog.write_text('say("started")\nlet i = 0\nwhile true { i = i + 1 }\n')
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tb.py"), str(prog)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline() == "started\n"
    time.sleep(0.2)
    proc.send_signal(signal.SIGINT)
    time.sleep(0.05)
    proc.send_signal(signal.SIGINT)  # like the one-file launcher forwarding it
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 130
    assert "Stopped." in err and "Traceback" not in err
