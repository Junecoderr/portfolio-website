"""`tb build APP.tb`: bundle a TB app with the TB runtime into one folder.

    tb build app.tb                     -> dist/app/
    tb build apps/notes/main.tb         -> dist/notes/ (main.tb is named after its folder)
    tb build app.tb --out out/x --name x --gui --include data/ --force

The folder holds everything the app needs, so it can be zipped and copied to
another computer:

    <name>.tb        the app's entry file (plus the files it imports, its
                     tb.toml / tb_packages, and - for an app that lives in
                     its own folder - every file in that folder: data, icons)
    <name>           a launcher script (macOS / Linux): ./<name> [args]
    <name>.exe       Windows launcher (a copy of tblaunch.exe, or tblaunchw.exe
                     with --gui) - only when TB itself is the installed build
    tb, _internal/   the TB runtime, when TB itself is the installed build
    runtime/         the TB runtime as source (tb.py, tbcore/, tblib/) when TB
                     runs from source; then run.sh / run.bat start the app and
                     the computer needs Python 3.10+
    README.txt       how to start it

Limits: files the app opens by a computed path outside its own folder (like
"../data") are not found by `tb build`; add them with --include (they are
copied into the bundle, so point the app at fs.here() paths). Native Python
packages are not supported (TB packages are). The bundle runs on the same
kind of computer it was built on when it carries the installed runtime.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys

__all__ = ["main", "build", "BuildError", "find_runtime", "import_closure"]

MARKER = ".tb-build"
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", "out", ".pytest_cache",
             ".venv", "venv"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class BuildError(Exception):
    pass


def _code_root() -> str:
    """The folder holding tb.py, tbcore/ and tblib/ (running from source)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_runtime() -> dict:
    """Where the TB runtime to copy lives.

    {"kind": "frozen", "exe": path, "internal": path or None, "launcher": path or None,
     "launcher_gui": path or None}  for an installed (PyInstaller) TB, or
    {"kind": "source", "root": folder with tb.py}."""
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        folder = os.path.dirname(exe)
        internal = os.path.join(folder, "_internal")
        suffix = ".exe" if os.name == "nt" else ""
        launcher = os.path.join(folder, "tblaunch" + suffix)
        launcher_gui = os.path.join(folder, "tblaunchw" + suffix)
        return {
            "kind": "frozen", "exe": exe,
            "internal": internal if os.path.isdir(internal) else None,
            "launcher": launcher if os.path.isfile(launcher) else None,
            "launcher_gui": launcher_gui if os.path.isfile(launcher_gui) else None,
        }
    return {"kind": "source", "root": _code_root()}


# -- what the app needs ----------------------------------------------------------


def _project_root(start: str) -> str | None:
    folder = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(folder, "tb.toml")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def import_closure(entry: str) -> tuple[list[str], list[str]]:
    """(.tb files the entry imports with quotes, recursively; package names it uses)."""
    from tbcore import ast as A
    from tbcore.parser import parse

    seen: list[str] = []
    packages: list[str] = []
    todo = [os.path.abspath(entry)]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.append(path)
        try:
            with open(path, encoding="utf-8") as f:
                program = parse(f.read(), os.path.dirname(path))
        except Exception:
            continue
        stack = list(program.body)
        while stack:
            node = stack.pop()
            if isinstance(node, A.Export):
                node = node.decl
            if isinstance(node, (A.Import, A.FromImport)) and node.is_file:
                package = getattr(node, "package_name", None)
                target = os.path.join(os.path.dirname(path), node.target)
                if os.path.isfile(target):
                    todo.append(os.path.abspath(target))
                elif package:
                    packages.append(package)
            for value in vars(node).values() if hasattr(node, "__dict__") else ():
                if isinstance(value, list):
                    stack.extend(v for v in value if isinstance(v, A.Node))
    return seen, sorted(set(packages))


def _copy_tree(src: str, dest: str, skip: set[str]) -> None:
    for folder, dirs, files in os.walk(src):
        rel = os.path.relpath(folder, src)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                   and os.path.abspath(os.path.join(folder, d)) not in skip]
        target_dir = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in files:
            if name.endswith((".pyc", ".pyo")) or name == MARKER:
                continue
            shutil.copy2(os.path.join(folder, name), os.path.join(target_dir, name))


def _write(path: str, text: str, executable: bool = False, newline: str = "\n") -> None:
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    if executable:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _shell_launcher(name: str, frozen: bool) -> str:
    run_frozen = f'exec "$DIR/tb" "$DIR/{name}.tb" -- "$@"'
    run_source = (
        'for PY in "${TB_PYTHON:-}" python3 python; do\n'
        '  if [ -n "$PY" ] && command -v "$PY" >/dev/null 2>&1; then\n'
        f'    exec "$PY" "$DIR/runtime/tb.py" "$DIR/{name}.tb" -- "$@"\n'
        '  fi\n'
        'done\n'
        'echo "This app needs Python 3.10 or newer: https://www.python.org/downloads/" >&2\n'
        'exit 1'
    )
    return ("#!/bin/sh\n"
            f"# Starts {name}.tb with the TB runtime in this folder (made by tb build).\n"
            'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
            + (run_frozen if frozen else run_source) + "\n")


def _bat_launcher(name: str, gui: bool) -> str:
    py = "pyw" if gui else "py"
    python = "pythonw" if gui else "python"
    start = 'start "" ' if gui else ""
    return ("@echo off\r\n"
            f"rem Starts {name}.tb with the TB runtime in this folder (made by tb build).\r\n"
            "setlocal\r\n"
            'set "DIR=%~dp0"\r\n'
            f'where {py} >nul 2>nul && ({start}{py} -3 "%DIR%runtime\\tb.py" "%DIR%{name}.tb" -- %* & exit /b %ERRORLEVEL%)\r\n'
            f'{start}{python} "%DIR%runtime\\tb.py" "%DIR%{name}.tb" -- %*\r\n')


def build(entry: str, out: str | None = None, name: str | None = None, *, gui: bool = False,
          include: list[str] | None = None, force: bool = False, runtime: dict | None = None,
          log=print) -> str:
    """Build the bundle; returns the output folder."""
    entry = os.path.abspath(entry)
    if not os.path.isfile(entry):
        raise BuildError(f"I couldn't find the file '{entry}'.")
    if not entry.lower().endswith(".tb"):
        raise BuildError("tb build needs a .tb file, like: tb build app.tb")
    app_dir = os.path.dirname(entry)
    stem = os.path.splitext(os.path.basename(entry))[0]
    if name is None:
        name = os.path.basename(app_dir) if stem == "main" else stem
    if not NAME_RE.match(name):
        raise BuildError(f"'{name}' can't be an app name. Use letters, digits, '-', '_' (like: --name my-app).")
    out = os.path.abspath(out or os.path.join("dist", name))
    if os.path.exists(out):
        if not os.path.isdir(out):
            raise BuildError(f"'{out}' is a file, not a folder.")
        if os.listdir(out) and not os.path.exists(os.path.join(out, MARKER)) and not force:
            raise BuildError(f"'{out}' already has files in it. Pick another --out, or add --force to replace it.")
        shutil.rmtree(out)
    if out == app_dir or app_dir.startswith(out + os.sep):
        raise BuildError("The output folder can't hold the app itself. Pick another --out.")
    os.makedirs(out)
    _write(os.path.join(out, MARKER), "made by tb build\n")
    runtime = runtime or find_runtime()

    # 1. the app: its own folder when it has one, else just what it imports
    files, packages = import_closure(entry)
    own_folder = stem == "main" or os.path.isfile(os.path.join(app_dir, "tb.toml"))
    if own_folder:
        _copy_tree(app_dir, out, {out})
    else:
        for path in files:
            rel = os.path.relpath(path, app_dir)
            if rel.startswith(".."):
                raise BuildError(f"{os.path.basename(entry)} imports '{path}', outside its folder; "
                                 "move it next to the app or build the folder's main.tb.")
            os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True)
            shutil.copy2(path, os.path.join(out, rel))
    entry_copy = os.path.join(out, name + ".tb")
    if os.path.abspath(entry_copy) != os.path.join(out, os.path.basename(entry)):
        shutil.copy2(entry, entry_copy)
    root = _project_root(app_dir)
    if root is not None:
        if os.path.isfile(os.path.join(root, "tb.toml")):
            shutil.copy2(os.path.join(root, "tb.toml"), os.path.join(out, "tb.toml"))
        packages_dir = os.path.join(root, "tb_packages")
        for package in packages:
            src = os.path.join(packages_dir, package)
            if os.path.isdir(src):
                _copy_tree(src, os.path.join(out, "tb_packages", package), set())
            else:
                log(f"note: the package '{package}' isn't installed (run tb add); the app may not start.")
    elif packages:
        log(f"note: packages {', '.join(packages)} are used but there is no tb.toml; run tb init and tb add.")
    for extra in include or []:
        src = os.path.abspath(extra)
        if not os.path.exists(src):
            raise BuildError(f"I couldn't find '{extra}' to include.")
        dest = os.path.join(out, os.path.basename(src.rstrip(os.sep)))
        if os.path.isdir(src):
            _copy_tree(src, dest, {out})
        else:
            shutil.copy2(src, dest)

    # 2. the runtime and the launchers
    if runtime["kind"] == "frozen":
        exe = runtime["exe"]
        shutil.copy2(exe, os.path.join(out, os.path.basename(exe)))
        if runtime.get("internal"):
            shutil.copytree(runtime["internal"], os.path.join(out, "_internal"))
        launcher = runtime.get("launcher_gui") if gui else runtime.get("launcher")
        launcher = launcher or runtime.get("launcher")
        if os.name == "nt":
            if launcher:
                shutil.copy2(launcher, os.path.join(out, name + ".exe"))
            else:
                log("note: this TB has no tblaunch.exe; start the app with run.bat.")
                _write(os.path.join(out, "run.bat"),
                       f'@echo off\r\n"%~dp0{os.path.basename(exe)}" "%~dp0{name}.tb" -- %*\r\n', newline="")
        else:
            _write(os.path.join(out, name), _shell_launcher(name, frozen=True), executable=True)
        how = f"{name}.exe" if os.name == "nt" else f"./{name}"
    else:
        src_root = runtime["root"]
        rt = os.path.join(out, "runtime")
        os.makedirs(rt)
        shutil.copy2(os.path.join(src_root, "tb.py"), os.path.join(rt, "tb.py"))
        for package in ("tbcore", "tblib"):
            _copy_tree(os.path.join(src_root, package), os.path.join(rt, package), set())
        script = _shell_launcher(name, frozen=False)
        _write(os.path.join(out, name), script, executable=True)
        _write(os.path.join(out, "run.sh"), script, executable=True)
        _write(os.path.join(out, "run.bat"), _bat_launcher(name, gui), newline="")
        how = f"./{name} (or run.sh; on Windows run.bat) - needs Python 3.10+"
    _write(os.path.join(out, "README.txt"),
           f"{name}\n{'=' * len(name)}\n\nA TB app, bundled by `tb build`.\n\n"
           f"Start it with: {how}\n\n"
           f"The app's code is {name}.tb (and the files next to it). The TB runtime is in "
           + ("this folder (tb, _internal)." if runtime["kind"] == "frozen" else "runtime/.")
           + "\n")
    return out


USAGE = """\
tb build APP.tb - bundle an app with the TB runtime into one folder

  --out FOLDER     where to put it (default: dist/NAME)
  --name NAME      the app's name (default: the file name; main.tb -> its folder's name)
  --gui            a windowed app (Windows: no console window)
  --include PATH   also copy a file or folder (data the app opens)
  --force          replace FOLDER even if it holds other files

Then zip the folder and share it. See `tb build --help` and README.txt in the bundle."""


def main(args: list[str] | None = None) -> int:
    args = list(args or [])
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    entry, out, name, gui, force, include = None, None, None, False, False, []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--out", "--name", "--include"):
            if i + 1 >= len(args):
                print(f"Oops! {a} needs a value. See: tb build --help", file=sys.stderr)
                return 2
            value = args[i + 1]
            if a == "--out":
                out = value
            elif a == "--name":
                name = value
            else:
                include.append(value)
            i += 2
            continue
        if a == "--gui":
            gui = True
        elif a == "--force":
            force = True
        elif a.startswith("--"):
            print(f"Oops! tb build doesn't know the option '{a}'. See: tb build --help", file=sys.stderr)
            return 2
        elif entry is None:
            entry = a
        else:
            print("Oops! Give tb build one .tb file, like: tb build app.tb", file=sys.stderr)
            return 2
        i += 1
    if entry is None:
        print("Oops! Which app? Like: tb build app.tb", file=sys.stderr)
        return 2
    try:
        folder = build(entry, out, name, gui=gui, include=include, force=force)
    except BuildError as err:
        print(f"Oops! {err}", file=sys.stderr)
        return 1
    except OSError as err:
        print(f"Oops! The build failed: {err.strerror or err}.", file=sys.stderr)
        return 1
    try:
        shown = os.path.relpath(folder)
    except ValueError:
        shown = folder
    print(f"Built {shown}")
    print(f"How to start it: {os.path.join(shown, 'README.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
