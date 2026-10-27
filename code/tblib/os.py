"""os: the computer TB is running on."""

from __future__ import annotations

import getpass
import os as _os
import shlex
import subprocess
import sys

from tblib._api import Module, check_type, fail

import tb as _tb

MAX_OUTPUT = 10_000_000  # characters of out/err kept from os.run


def _platform() -> str:
    if sys.platform == "emscripten":
        return "browser"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return _os.environ.get("USER") or _os.environ.get("USERNAME") or ""


def load(api):
    m = Module(
        "os",
        "The computer TB runs on: your program's arguments, environment variables, "
        "running other programs, and exiting.",
    )
    m.value("args", api.args, doc="The words after the file name: `tb main.tb a b` gives [\"a\", \"b\"].")
    m.value("platform", _platform(), doc="\"windows\", \"mac\", \"linux\" or \"browser\".")
    m.value("user", _user(), doc="The name of the person logged in to this computer.")
    m.value("script", api.interpreter.script_path or "", doc="The path of the running .tb file (\"\" in the REPL).")

    @m.fn("env", 1, 2, doc="env(name, default?) -> text\nAn environment variable, like env(\"HOME\"), or default (nil) if it isn't set.")
    def env(line, name, default=None):
        check_type(line, name, "text", "os.env", "name")
        return _os.environ.get(name, default)

    @m.fn("run", 1, 2, doc=(
        "run(command, options?) -> map\n"
        "Run another program and wait for it. Not available in the browser playground.\n"
        "\n"
        "Returns:\n"
        "- \"code\": the exit code. 0 means it worked.\n"
        "- \"out\": what it printed.\n"
        "- \"err\": the errors it printed.\n"
        "\n"
        "The command:\n"
        "- A text command goes through the system shell, so pipes and wildcards work:\n"
        "  run(\"ls -l | head\"). Never build that text from untrusted input.\n"
        "- A list runs the program directly with exactly those arguments, with no shell:\n"
        "  run([\"ping\", \"-c\", \"1\", host]). Prefer this whenever input comes from outside.\n"
        "\n"
        "Options:\n"
        "- timeout: the most seconds to wait.\n"
        "- cwd: the folder to run it in.\n"
        "- input: text to send it."))
    def run(line, command, options=None):
        api.need_desktop(line, "os.run()")
        check_type(line, command, "text|list", "os.run", "command")
        options = {} if options is None else check_type(line, options, "map", "os.run", "options")
        if isinstance(command, list):
            if not command:
                fail(line, "os.run() needs at least the program's name in the list.")
            args = [_tb.to_text(a) for a in command]
            shell = False
            shown = args[0]
        else:
            if not command.strip():
                fail(line, "os.run() needs a command, but got empty text.")
            args = command
            shell = True
            shown = command.split()[0] if command.split() else command
        timeout = options.get("timeout")
        if timeout is not None:
            check_type(line, timeout, "number", "os.run", "the timeout")
            if timeout <= 0:
                fail(line, "os.run() needs a timeout bigger than 0.")
        cwd = options.get("cwd")
        if cwd is not None:
            check_type(line, cwd, "text", "os.run", "the cwd")
            if not _os.path.isdir(cwd):
                fail(line, f"os.run() couldn't find the folder '{cwd}'.")
        given = options.get("input")
        if given is not None:
            given = _tb.to_text(given)
        try:
            done = subprocess.run(
                args, shell=shell, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                input=given, encoding="utf-8", errors="replace",
                stdin=None if given is not None else subprocess.DEVNULL,
            )
        except FileNotFoundError:
            if cwd is not None and not _os.path.isdir(cwd):
                fail(line, f"os.run() couldn't find the folder '{cwd}'.")
            fail(line, f"I couldn't find the program '{shown}'. Is it installed, and spelled right?")
        except PermissionError:
            fail(line, f"I'm not allowed to run '{shown}'.")
        except subprocess.TimeoutExpired:
            fail(line, f"'{shown}' took longer than {_tb.to_text(timeout)} seconds, so I stopped it.",
                 timeout=True)
        except OSError as err:
            fail(line, f"I couldn't run '{shown}': {err.strerror or err}.")
        return {
            "code": done.returncode,
            "out": (done.stdout or "")[:MAX_OUTPUT],
            "err": (done.stderr or "")[:MAX_OUTPUT],
        }

    @m.fn("quote", 1, doc="quote(text) -> text\nMake text safe to put inside a shell command for os.run (mac/linux shells).")
    def quote(line, text):
        check_type(line, text, "text", "os.quote", "text")
        return shlex.quote(text)

    @m.fn("exit", 0, 1, doc="exit(code?)\nStop the program now. 0 (the default) means all went well.")
    def exit_(line, code=0):
        code = check_type(line, code, "whole", "os.exit", "code")
        raise _tb.TBExit(code)

    return m
