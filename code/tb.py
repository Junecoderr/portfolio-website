#!/usr/bin/env python3
"""TB - a small, friendly programming language.

This file is the entry point and the public API: `import tb` gives you
`run`, `Interpreter`, `TBError`, `parse`, `tokenize`, `main` and friends, and
`python tb.py file.tb` runs a program. The language itself lives in the
`tbcore/` package next to this file (lexer, parser, syntax tree, runtime,
interpreter) and the standard library in `tblib/`. Everything needs only
Python 3.10+ and its standard library, so it also runs in the browser through
Pyodide.

Usage:
    tb file.tb [args...]   run a TB program (args go to os.args)
    tb                     start the interactive prompt (REPL)
    tb doc [library]       show the standard library
    tb test [folder]       run every *_test.tb file (--coverage for line coverage)
    tb <command> ...       a tool from tbtools/ (fmt, check, lsp, debug, add, ...)
    tb --version           print the version
    tb --help              print help
"""

from __future__ import annotations

import sys

# When this file runs as a script (`python tb.py`, or the one-file app), make
# `import tb` (used by tblib) find this very module instead of loading a second
# copy whose TBError would be a different class.
if __name__ == "__main__":
    sys.modules.setdefault("tb", sys.modules[__name__])

import importlib  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402

from tbcore.errors import (  # noqa: E402,F401
    MANY, MAX_DEPTH, MAX_INT_BITS, MAX_POW_BITS, MAX_RANGE, TOO_BIG, UNSET, BreakSignal,
    ContinueSignal, ReturnSignal, TBError, TBExit, check_size, snake_case,
)
from tbcore.lexer import (  # noqa: E402,F401
    ASSIGN_OPS, CHAR_HINTS, KEYWORDS, NEW_KEYWORDS, ONE_CHAR_OPS, TWO_CHAR_OPS, Token, tokenize,
)
from tbcore.ast import *  # noqa: E402,F401,F403
from tbcore.ast import Node, Program  # noqa: E402,F401
from tbcore.parser import (  # noqa: E402,F401
    NAME_RE, OTHER_LANGUAGE_NAMES, OTHER_LANGUAGE_WORDS, Parser, describe, parse, parse_expression,
)
from tbcore.runtime import *  # noqa: E402,F401,F403
from tbcore.runtime import (  # noqa: E402,F401
    CARE_MESSAGES, DONE, ERROR_CLASS, ERROR_CLASSES, METHOD_HINTS, METHOD_KIND_LABEL, METHODS,
    Bound, Builtin, EnumType, EnumValue, Env, Function, Instance, Module, NativeMethod, Partial,
    Range, TBClass, TBIterator, arity_message, error_class, error_value, format_value,
    is_callable, is_number, is_whole, looks_like_number, map_key, method, method_kind,
    missing_key, sort_values, tb_equal, tb_round, tb_type, text_to_number, tidy_number, to_text,
    truthy, type_name, _extreme, _sum, _need_fn, _need_text, _need_whole, _slice,
)
from tbcore.interp import (  # noqa: E402,F401
    BUILTIN_DOCS, BUILTIN_VALUE_DOCS, Frame, find_package, find_project_root,
    library_dirs, list_libraries, package_entry, read_source, tb_source_docs, toml_main,
    _native_spec, _tb_library_path, _tblib_package,
)
from tbcore.interp import Interpreter as TreeInterpreter  # noqa: E402
from tbcore.vm import VMInterpreter  # noqa: E402

# The engine: the bytecode VM by default; TB_ENGINE=tree (or `tb --engine tree
# file.tb`) keeps the 2.x-style tree-walking interpreter for one release.
ENGINES = {"vm": VMInterpreter, "tree": TreeInterpreter}


def engine_class(name: str | None = None):
    """The Interpreter class for an engine name ("vm" or "tree"; None: $TB_ENGINE, else vm)."""
    if name is None:
        name = os.environ.get("TB_ENGINE", "") or "vm"
    return ENGINES.get(name.strip().lower(), VMInterpreter)


Interpreter = engine_class()

__version__ = "3.1.0"


# ---------------------------------------------------------------------------
# Public API (also used by the browser playground through Pyodide)
# ---------------------------------------------------------------------------


# Python needs room for TB's own recursion. From Python 3.11 on, Python calls
# don't use the C stack, so a high limit is safe; 3.10 keeps a modest one.
_PYTHON_RECURSION_LIMIT = 60000 if sys.version_info >= (3, 11) else 20000


def _guarded(work):
    """Run work() with enough Python recursion room, turning a Python
    RecursionError into a friendly TBError."""
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, _PYTHON_RECURSION_LIMIT))
    try:
        return work()
    except RecursionError:
        raise TBError(
            "The program went too deep (too many functions inside functions, "
            "or a list inside itself)."
        ) from None
    finally:
        sys.setrecursionlimit(old_limit)


def run(
    source: str,
    output=None,
    input_fn=None,
    interpreter: Interpreter | None = None,
    rng: random.Random | None = None,
    *,
    path: str | None = None,
    args: list | None = None,
):
    """Run TB source code. Raises TBError on mistakes in the program.
    Pass a seeded random.Random as rng to make care() predictable.
    path: the file the source came from (imports are relative to it).
    args: the program's command-line arguments (os.args)."""
    interp = interpreter or Interpreter(output=output, input_fn=input_fn, rng=rng)
    if path is not None:
        interp.script_path = os.path.abspath(path)
        interp.base_dir = os.path.dirname(interp.script_path)
        # The main file counts as "being imported", so a file that imports
        # it back is reported as a circle instead of running it twice.
        interp.loading = [interp.script_path]
    if args is not None:
        interp.argv = list(args)
    _guarded(lambda: interp.run(parse(source, interp.base_dir)))
    return interp


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

HELP = f"""TB {__version__} - a small, friendly programming language.

Usage:
  tb FILE.tb [args...]   run a TB program (it sees the args as os.args)
  tb                     start the interactive prompt (type 'exit' to leave)
  tb doc                 list the standard libraries
  tb doc NAME            show what a library can do (try: tb doc fs)
  tb doc builtins        show the built-in functions, methods and error classes
  tb doc FILE.tb|FOLDER  documentation from ## comments (--md, --html, --out PATH)
  tb test [FOLDER]       run every *_test.tb file in FOLDER (default: here)
  tb test --coverage     ... and show how many lines of each file the tests ran
  tb fmt [FILES]         tidy up the layout of TB files (--check to only report)
  tb check [FILES]       look for mistakes without running the program
  tb debug FILE.tb       step through a program line by line
  tb init / add / remove / packages / update   manage packages (tb_packages/)
  tb build APP.tb        bundle an app with the TB runtime into a folder
  tb lsp                 start the language server (used by editors)
  tb --engine tree FILE.tb   run with the tree-walking interpreter (the VM is the default)
  tb --version           show the version
  tb --help              show this help

Example program (save as hello.tb, then run: tb hello.tb):
  let name = ask("What's your name? ")
  say(f"Hello, {{name}}!")
  care(name)

The language in one breath (TB 3.0): let/const, fn with defaults and ...rest,
x => x * 2, classes (class Dog extends Animal), match, if a then b else c,
ranges 1..10, slices xs[1:3], destructuring let [a, b] = xs, f"{{x:.2f}}",
bytes b"..", generators (yield), typed errors (catch err: FileError), enums.
"""


def repl() -> int:
    print(f"TB {__version__} - type some code, or 'exit' to leave.")
    interp = Interpreter()
    buffer: list[str] = []
    while True:
        try:
            line = input("... " if buffer else "tb> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not buffer and line.strip() in ("exit", "quit", "exit()", "quit()"):
            return 0
        buffer.append(line)
        source = "\n".join(buffer)
        if open_brackets(source) > 0:
            continue
        buffer = []
        try:
            _guarded(lambda: _repl_run(interp, source))
        except TBError as err:
            print(err)
        except TBExit as stop:
            return stop.code if isinstance(stop.code, int) else 0
        except KeyboardInterrupt:
            previous = ignore_ctrl_c()
            print("Stopped.")
            restore_ctrl_c(previous)
        except Exception as err:  # never show a Python traceback
            print(crash_message(err))


def ignore_ctrl_c():
    """Ignore further Ctrl+C presses. The PyInstaller one-file exe forwards a
    second Ctrl+C that would otherwise land inside our handler and show a
    traceback. Returns the old handler (or None if this isn't possible, e.g.
    in Pyodide or outside the main thread)."""
    try:
        import signal

        return signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        return None


def restore_ctrl_c(previous, settle: float = 0.2) -> None:
    """Wait briefly for a forwarded Ctrl+C to arrive (and be ignored), then
    let Ctrl+C work again."""
    if previous is None:
        return
    try:
        import signal
        import time

        time.sleep(settle)
        signal.signal(signal.SIGINT, previous)
    except Exception:
        pass


def _repl_run(interp: Interpreter, source: str) -> None:
    program = parse(source)
    # Show the value of a lone expression, like a calculator.
    if len(program.body) == 1 and isinstance(program.body[0], ExprStmt):
        value = interp.evaluate(program.body[0].expr, interp.globals)
        if value is not None:
            print(to_text(value, nested=True))
    else:
        interp.run(program)


def open_brackets(source: str) -> int:
    """How many brackets are still open (used by the REPL to wait for more lines)."""
    try:
        tokens = tokenize(source)
    except TBError:
        return 0
    depth = 0
    for tok in tokens:
        if tok.kind == "OP" and tok.value in "({[":
            depth += 1
        elif tok.kind == "OP" and tok.value in ")}]":
            depth -= 1
    return depth


def crash_message(err: BaseException) -> str:
    if isinstance(err, MemoryError):
        return "Oops! The program needed more memory than this computer has."
    return (
        "Oops! Something went wrong inside TB itself. This is not your fault.\n"
        f"(If it keeps happening, share this: {type(err).__name__}: {err})"
    )


# -- Windows: keep the window open -----------------------------------------


def owns_console_window() -> bool:
    """True on Windows when TB has its own console window, meaning it was
    started by double-clicking tb.exe or dropping a .tb file on it. Then the
    window would vanish the moment TB finishes, so we wait for Enter first.
    Safe everywhere: any problem means False."""
    if sys.platform != "win32":
        return False
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        import ctypes

        processes = (ctypes.c_uint32 * 8)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(processes, 8)
    except Exception:
        return False
    # A PyInstaller one-file exe is two processes (a small launcher plus TB);
    # the one-folder build TB ships (tb.exe next to _internal) is one.
    alone = 2 if _is_onefile() else 1
    return 0 < count <= alone


def _is_onefile() -> bool:
    """True inside a PyInstaller one-file exe, which unpacks itself to a temp
    folder (sys._MEIPASS) instead of running from next to the executable."""
    if not getattr(sys, "frozen", False):
        return False
    bundle = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", "")))
    home = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    return not bundle.startswith(home)


class _WindowedErrors:
    """tbw.exe (used by Start-menu shortcuts) has no console, so anything TB
    would print as an error is collected and shown in one dialog at exit."""

    def __init__(self):
        self.parts: list[str] = []

    def write(self, text):
        self.parts.append(str(text))
        return len(text)

    def flush(self):
        pass

    def show(self):
        text = "".join(self.parts).strip()
        if not text:
            return
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("TB", text[-4000:])
            root.destroy()
        except Exception:
            pass


def pause_before_closing() -> None:
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def _read_program(path: str):
    """Read a .tb file for running; on failure print why and return None."""
    try:
        return read_source(path)
    except FileNotFoundError:
        message = f"Oops! I couldn't find the file '{path}'. Is the name spelled right?"
        if os.path.isfile(path + ".txt"):
            message = (
                f"Oops! I couldn't find '{path}', but I found '{path}.txt'.\n"
                f"Did Notepad save it as '{path}.txt'? Rename it so it ends in .tb "
                "(in File Explorer, turn on View > File name extensions to see the .txt)."
            )
        print(message, file=sys.stderr)
    except OSError as err:
        print(f"Oops! I couldn't open '{path}': {err.strerror or err}.", file=sys.stderr)
    return None


def checks_summary(state: dict) -> str:
    passed, failed = state.get("passed", 0), state.get("failed", 0)
    checks = passed + failed
    if failed:
        return f"{failed} of {checks} check{'' if checks == 1 else 's'} failed."
    return f"All {checks} check{'' if checks == 1 else 's'} passed."


def run_file(path: str, args: list | None = None) -> int:
    source = _read_program(path)
    if source is None:
        return 1
    try:
        interp = run(source, path=path, args=list(args or []))
    except TBError as err:
        print(err, file=sys.stderr)
        trace = err.trace_text()
        if trace:
            print(trace, file=sys.stderr)
        return 1
    except TBExit as stop:
        return stop.code if isinstance(stop.code, int) else 0
    except KeyboardInterrupt:
        ignore_ctrl_c()
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as err:  # never show a Python traceback
        print(crash_message(err), file=sys.stderr)
        return 1
    state = getattr(interp, "test_state", None)
    if isinstance(state, dict) and state.get("passed", 0) + state.get("failed", 0):
        print(checks_summary(state))
        return 1 if state.get("failed") else 0
    return 0


# -- tb doc -------------------------------------------------------------------


def describe_library(name: str) -> dict:
    """Everything `tb doc NAME` shows, as data (the website uses this too):
    {"name", "doc", "functions": [{"name", "signature", "doc"}],
     "values": [{"name", "value", "doc"}]}. Raises TBError if unknown."""
    interp = Interpreter(output=lambda text: None, input_fn=lambda prompt: "")
    module = interp.load_library(name)
    functions, values = [], []
    for field in module.public_names():
        value = module.fields[field]
        doc = module.docs.get(field) or getattr(value, "doc", "") or ""
        if isinstance(value, (Builtin, Function)) or callable(value):
            first, _, rest = doc.partition("\n")
            if not first.startswith(field + "("):
                first, rest = f"{field}(...)", doc
            functions.append({"name": field, "signature": first.strip(), "doc": rest.strip()})
        elif not isinstance(value, Module):
            values.append({"name": field, "value": to_text(value, nested=True), "doc": doc.strip()})
    return {"name": module.name, "doc": module.doc.strip(), "functions": functions, "values": values}


def describe_builtins() -> dict:
    """The built-in functions, values, error classes and the methods of text,
    lists, maps, numbers, bytes, ranges, generators and functions."""
    return {
        "functions": [
            {"name": n, "signature": d.partition("\n")[0], "doc": d.partition("\n")[2]}
            for n, d in BUILTIN_DOCS.items()
        ],
        "values": [
            {"name": n, "signature": d.partition("\n")[0], "doc": d.partition("\n")[2]}
            for n, d in BUILTIN_VALUE_DOCS.items()
        ],
        "errors": [
            {"name": name, "signature": f"{name}(message, extra?)" if name == "Error" else f"{name} extends {cls.base.name}",
             "doc": cls.doc}
            for name, cls in ERROR_CLASSES.items()
        ],
        "methods": {
            kind: [
                {"name": n, "signature": spec[3].partition("\n")[0], "doc": spec[3].partition("\n")[2]}
                for n, spec in sorted(table.items())
            ]
            for kind, table in METHODS.items()
        },
    }


METHOD_EXAMPLES = {
    "text": '"hi".upper()', "list": "xs.push(1)", "map": "m.keys()", "number": "3.7.round()",
    "bytes": 'b"hi".hex()', "range": "(1..10).to_list()", "generator": "gen.next()",
    "function": "f.bind(1)",
}


def _print_entry(signature: str, doc: str) -> None:
    """One function or value for `tb doc`: the signature, then its doc string
    (summary, Options:, bullets, Example:) wrapped to fit a terminal."""
    from tbcore import docfmt
    print("  " + signature)
    for text in docfmt.to_text(doc, indent="      ", width=78):
        print(text)


def first_sentence(text: str) -> str:
    text = text.strip().partition("\n")[0]
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text


def shown_path(path: str) -> str:
    try:
        rel = os.path.relpath(path)
    except ValueError:  # Windows: path is on another drive
        return path
    return path if rel.startswith("..") else rel


def doc_command(args: list) -> int:
    if not args:
        names = list_libraries()
        print(f"TB {__version__} standard library. Use one with: import NAME")
        print()
        width = max((len(n) for n in names), default=4)
        for name in names:
            try:
                about = first_sentence(describe_library(name)["doc"])
            except TBError as err:
                about = f"(can't load: {err.message})"
            print(f"  {name.ljust(width)}  {about}")
        print()
        print("See one library with: tb doc NAME   (built-in functions: tb doc builtins)")
        return 0
    name = args[0]
    # tb doc FILE.tb / tb doc FOLDER: documentation from ## comments (tbtools/docgen.py)
    if any((a.lower().endswith(".tb") and os.path.isfile(a)) or (os.path.isdir(a) and a not in list_libraries())
           for a in args if not a.startswith("--")):
        return run_tool("docgen", args)
    if name in ("builtins", "builtin", "methods"):
        info = describe_builtins()
        print("Built-in functions (no import needed):")
        print()
        for entry in info["functions"]:
            _print_entry(entry["signature"], entry["doc"])
        print()
        print("Built-in values:")
        print()
        for entry in info["values"]:
            _print_entry(entry["signature"], entry["doc"])
        print()
        print("Error classes (throw Error(\"msg\"); catch err: FileError { }; class Mine extends Error { }):")
        print()
        for entry in info["errors"]:
            _print_entry(entry["signature"], entry["doc"])
        for kind, entries in info["methods"].items():
            print()
            print(f"{kind.capitalize()} methods (like {METHOD_EXAMPLES[kind]}):")
            print()
            for entry in entries:
                _print_entry(entry["signature"], entry["doc"])
        return 0
    try:
        info = describe_library(name)
    except TBError as err:
        print(err, file=sys.stderr)
        return 1
    import textwrap
    head = f"{info['name']} - {info['doc']}" if info["doc"] else info["name"]
    print("\n".join(textwrap.wrap(head, 78, break_long_words=False, break_on_hyphens=False)))
    print(f"Use it with: import {info['name']}")
    if info["values"]:
        print()
        for entry in info["values"]:
            shown = f"{info['name']}.{entry['name']} = {entry['value']}"
            if len(shown) > 78:
                shown = shown[:75] + "..."
            _print_entry(shown, entry["doc"])
    if info["functions"]:
        for entry in info["functions"]:
            print()
            _print_entry(f"{info['name']}.{entry['signature']}", entry["doc"])
    return 0


# -- tb test ------------------------------------------------------------------

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".pytest_cache", "tb_packages"}


def find_test_files(target: str) -> list[str]:
    if os.path.isfile(target):
        return [target]
    found = []
    for folder, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith("_test.tb"):
                found.append(os.path.join(folder, name))
    return found


STATEMENT_TYPES = (
    ExprStmt, Let, Assign, MultiAssign, FnDecl, Return, If, While, For, Break, Continue, Import,
    FromImport, Try, Throw, ClassDecl, EnumDecl, Export, Defer, Assert,
)


def statement_lines(node, found: set | None = None) -> set:
    """Every line that holds a statement (what `tb test --coverage` counts)."""
    if found is None:
        found = set()
    if isinstance(node, STATEMENT_TYPES):
        found.add(node.line)
    if isinstance(node, (list, tuple)):
        for item in node:
            statement_lines(item, found)
        return found
    if isinstance(node, Node) or type(node).__name__ in ("Catch", "MatchArm", "Param"):
        for value in _node_values(node):
            if isinstance(value, (Node, list, tuple)) or type(value).__name__ in ("Catch", "MatchArm", "Param"):
                statement_lines(value, found)
    return found


def _node_values(node) -> list:
    """The attribute values of a syntax node (plain or __slots__ classes)."""
    values = list(getattr(node, "__dict__", {}).values())
    for cls in type(node).__mro__:
        for name in getattr(cls, "__slots__", ()):
            if hasattr(node, name):
                values.append(getattr(node, name))
    return values


class Coverage:
    """Collects the lines each file ran, through Interpreter.trace."""

    def __init__(self):
        self.ran: dict = {}      # file path -> set of lines
        self.sources: dict = {}  # file path -> source text

    def hook(self, event, line, file, env):
        if event == "line" and file is not None:
            self.ran.setdefault(file, set()).add(line)

    def note(self, path: str, source: str) -> None:
        self.sources.setdefault(os.path.abspath(path), source)

    def report(self) -> list[str]:
        lines = []
        for path in sorted(self.sources):
            if path.startswith(tuple(library_dirs())):
                continue
            try:
                total = statement_lines(parse(self.sources[path]).body)
            except TBError:
                continue
            if not total:
                continue
            ran = self.ran.get(path, set()) & total
            missed = sorted(total - ran)
            percent = 100 * len(ran) // len(total)
            shown = shown_path(path)
            text = f"  {shown}: {percent}% ({len(ran)} of {len(total)} lines)"
            if missed:
                text += " - missed " + ", ".join(str(n) for n in missed[:12]) + (", ..." if len(missed) > 12 else "")
            lines.append(text)
        return lines


def run_tests(args: list) -> int:
    coverage = None
    args = list(args)
    if "--coverage" in args:
        args.remove("--coverage")
        coverage = Coverage()
    target = args[0] if args else "."
    if not os.path.exists(target):
        print(f"Oops! I couldn't find '{target}'.", file=sys.stderr)
        return 1
    files = find_test_files(target)
    if not files:
        print(f"No test files found in '{target}'. Test files end in _test.tb, like math_test.tb.")
        return 1
    totals = {"passed": 0, "failed": 0, "skipped": 0}
    bad_files = 0
    for path in files:
        print(shown_path(path))
        source = _read_program(path)
        interp = Interpreter()
        interp.test_state = {"passed": 0, "failed": 0, "skipped": 0}
        if coverage is not None:
            interp.trace = coverage.hook
        crashed = source is None
        if source is not None:
            if coverage is not None:
                coverage.note(path, source)
            try:
                run(source, interpreter=interp, path=path)
            except TBError as err:
                if type(err).__name__ == "SkipTest":   # test.skip() outside a case
                    interp.test_state["skipped"] = interp.test_state.get("skipped", 0) + 1
                    print(f"  skip the rest of this file ({err.message})")
                else:
                    print(f"  {err}")
                    crashed = True
            except TBExit as stop:
                crashed = stop.code not in (0, None)
                if crashed:
                    print(f"  The test file stopped with exit({stop.code}).")
            except KeyboardInterrupt:
                print("\nStopped.", file=sys.stderr)
                return 130
            except Exception as err:  # never show a Python traceback
                print("  " + crash_message(err))
                crashed = True
            if coverage is not None:
                for module in interp.modules.values():
                    if isinstance(module, Module) and module.path and not module.std:
                        try:
                            coverage.note(module.path, read_source(module.path))
                        except OSError:
                            pass
        state = interp.test_state
        totals["passed"] += state["passed"]
        totals["failed"] += state["failed"]
        totals["skipped"] += state.get("skipped", 0)
        if crashed or state["failed"]:
            bad_files += 1
        if crashed:
            print("  The file stopped early because of the error above.")
        print(f"  {checks_summary(state)}" if state["passed"] + state["failed"] else "  (no checks)")
    print()
    if coverage is not None:
        report = coverage.report()
        print("Coverage (lines the tests ran):")
        print("\n".join(report) if report else "  (nothing to measure)")
        print()
    count = len(files)
    files_text = f"{count} test file{'' if count == 1 else 's'}"
    skipped = f", {totals['skipped']} skipped" if totals["skipped"] else ""
    if bad_files:
        print(f"FAILED: {totals['failed']} failed, {totals['passed']} passed{skipped}, "
              f"in {bad_files} of {files_text}.")
        return 1
    print(f"OK: {totals['passed']} checks passed{skipped} in {files_text}.")
    return 0


# -- tb <command> (tbtools) -------------------------------------------------------

BUILTIN_COMMANDS = ("doc", "test", "run")


def tool_commands() -> list[str]:
    """The commands tbtools/ provides in this build (empty if it isn't shipped)."""
    try:
        tools = importlib.import_module("tbtools")
    except Exception:
        return []
    names = set(getattr(tools, "COMMANDS", {}) or {})
    try:
        import pkgutil

        for info in pkgutil.iter_modules(tools.__path__):
            if not info.name.startswith("_"):
                names.add(info.name)
    except Exception:
        pass
    return sorted(names)


def run_tool(name: str, args: list) -> int:
    """tb <name> ...: tbtools.<name>.main(args), or a COMMANDS alias in tbtools/__init__.py."""
    try:
        tools = importlib.import_module("tbtools")
    except ImportError:
        print(f"Oops! The command '{name}' isn't available in this build of TB. "
              f"(The tbtools folder is missing.) See: tb --help", file=sys.stderr)
        return 2
    # tbtools/__init__.py registers tbtools.<alias> modules for shared tools (add,
    # remove, ... -> pkg); a COMMANDS entry that is plain text names another module.
    target = (getattr(tools, "COMMANDS", {}) or {}).get(name, name)
    if not isinstance(target, str):
        target = name
    try:
        module = importlib.import_module(f"tbtools.{target}")
    except ImportError as err:
        if getattr(err, "name", "") not in (f"tbtools.{target}", "tbtools"):
            raise
        available = tool_commands()
        listing = f" Commands: {', '.join(list(BUILTIN_COMMANDS) + available)}." if available else ""
        print(f"Oops! I don't know the command '{name}'.{listing} "
              "To run a program, give me its file name (ending in .tb). See: tb --help", file=sys.stderr)
        return 2
    entry = getattr(module, "main", None)
    if not callable(entry):
        print(f"Oops! The command '{name}' isn't available in this build of TB.", file=sys.stderr)
        return 2
    try:
        code = entry(list(args))
    except TBError as err:
        print(err, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return int(code or 0)


def looks_like_command(word: str) -> bool:
    """`tb fmt` is a command; `tb hello.tb`, `tb ./x` and `tb hello` (a file) are not."""
    if os.path.isfile(word) or word.lower().endswith(".tb"):
        return False
    if "/" in word or "\\" in word or "." in word:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][\w-]*", word))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # A windowed build (tbw.exe) has no console at all.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        import atexit

        sys.stderr = _WindowedErrors()
        atexit.register(sys.stderr.show)
    # `tb prog.tb | head -1` should just stop quietly when the reader leaves.
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, OSError):
        pass  # Windows has no SIGPIPE; not the main thread
    # Never crash on a console that can't show characters like the care() symbol.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if args and args[0] == "--engine" or args and args[0].startswith("--engine="):
        if args[0] == "--engine":
            name, args = (args[1] if len(args) > 1 else ""), args[2:]
        else:
            name, args = args[0].split("=", 1)[1], args[1:]
        if name not in ENGINES:
            print(f"Oops! I don't know the engine '{name}'. Engines: vm (the default), tree.", file=sys.stderr)
            return 2
        global Interpreter
        Interpreter = ENGINES[name]
        os.environ["TB_ENGINE"] = name  # so tools started from here use it too
    if not args:
        return repl()
    first = args[0]
    if first in ("-h", "--help"):
        print(HELP)
        return 0
    if first in ("-V", "--version"):
        print(f"TB {__version__}")
        return 0
    if first == "doc" and not os.path.isfile(first):
        return doc_command(args[1:])
    if first == "test" and not os.path.isfile(first):
        return run_tests(args[1:])
    if first == "run" and not os.path.isfile(first) and len(args) > 1:
        args = args[1:]
        first = args[0]
    if first.startswith("-") and len(first) > 1:
        print(f"Oops! I don't know the option '{first}'. See: tb --help", file=sys.stderr)
        return 2
    if looks_like_command(first):
        return run_tool(first, args[1:])
    program_args = args[1:]
    if program_args and program_args[0] == "--":
        program_args = program_args[1:]
        code = run_file(first, program_args)
    elif any(a.lower().endswith(".tb") for a in program_args):
        # 1.0 refused several files; keep that unless the user asks with '--'.
        print(
            "Oops! Please give me just one .tb file to run. "
            "(To hand other file names to your program, put -- first: tb main.tb -- other.tb)",
            file=sys.stderr,
        )
        code = 2
    else:
        code = run_file(first, program_args)
    if owns_console_window():
        sys.stdout.flush()
        pause_before_closing()
    return code


if __name__ == "__main__":
    try:
        exit_code = main()
    except KeyboardInterrupt:  # a stray Ctrl+C: leave quietly
        exit_code = 130
    sys.exit(exit_code)
