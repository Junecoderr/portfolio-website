"""`tb check [paths] [--json] [--strict]`: find mistakes without running.

Each problem prints as `file:line:col: error|warning: message` (with a hint
line under some), then a friendly summary. Exit code: 1 when there are
errors (or warnings too, with --strict), 2 when a path doesn't exist,
0 otherwise. `--json` prints one JSON object instead (the LSP calls
tbcore.types.check_source directly).
"""

from __future__ import annotations

import json
import os
import sys

HELP = """\
tb check - find mistakes in TB programs without running them

Usage:
  tb check                 check every .tb file under this folder
  tb check FILE|DIR ...    check these files / folders (recursively)

Options:
  --json      print the problems as JSON (for editors and scripts)
  --strict    exit with 1 on warnings too, not only errors
  --help      show this help

What it finds:
  errors    names that don't exist, wrong number of inputs, unknown methods
            or library members, changing a const, calling something that
            isn't a function, self outside a class, type mismatches with
            annotations (let x: number = "hi") or operators (5 - "a")
  warnings  unused variables and imports, code that never runs, a match
            over an enum that misses members, private names of other files

Names starting with '_' are never reported as unused. A line ending in the
comment  # tb-check: ignore  is never reported (for code that breaks on
purpose). The checker is gradual: when it can't be sure of a type, it
stays quiet.

Each problem looks like:  file.tb:12:5: error: I don't know what 'nmae' is.
Exit code: 0 all good, 1 errors found (or warnings with --strict), 2 bad path.
"""

SKIP_DIRS = {"tb_packages", "node_modules", "__pycache__", "dist", "build"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def find_files(paths: list[str]) -> tuple[list[str], list[str]]:
    """(the .tb files to check, paths that don't exist)."""
    files: list[str] = []
    missing: list[str] = []
    for path in paths:
        if os.path.isfile(path):
            files.append(path)
        elif os.path.isdir(path):
            for root, dirs, names in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
                for name in sorted(names):
                    if name.endswith(".tb"):
                        files.append(os.path.join(root, name))
        else:
            missing.append(path)
    seen = set()
    unique = []
    for f in files:
        key = os.path.abspath(f)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, missing


def main(args: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    as_json = strict = False
    paths: list[str] = []
    for arg in args:
        if arg in ("-h", "--help", "help"):
            print(HELP, end="")
            return 0
        if arg == "--json":
            as_json = True
        elif arg == "--strict":
            strict = True
        elif arg.startswith("-") and arg != "-":
            print(f"tb check: I don't know the option '{arg}'. See: tb check --help", file=sys.stderr)
            return 2
        else:
            paths.append(arg)
    if not paths:
        paths = ["."]

    from tbcore.types import check_file

    files, missing = find_files(paths)
    for path in missing:
        print(f"tb check: I can't find '{path}'.", file=sys.stderr)
    results = []
    for path in files:
        for diag in check_file(path):
            diag.file = path
            results.append(diag)
    errors = sum(1 for d in results if d.severity == "error")
    warnings = sum(1 for d in results if d.severity == "warning")

    if as_json:
        print(json.dumps({
            "files": len(files), "errors": errors, "warnings": warnings,
            "diagnostics": [d.to_dict() for d in results],
        }, indent=2))
    else:
        for diag in results:
            print(diag.format(diag.file))
        bad_files = len({d.file for d in results if d.severity == "error"})
        checked = _plural(len(files), "file")
        if not files:
            print("No .tb files to check.")
        elif not results:
            print(f"All good! Checked {checked}: no problems found.")
        elif errors:
            extra = f" and {_plural(warnings, 'warning')}" if warnings else ""
            print(f"Found {_plural(errors, 'error')}{extra} in {bad_files} of {checked}. "
                  "Fix the errors and run tb check again.")
        else:
            print(f"Checked {checked}: no errors, {_plural(warnings, 'warning')}."
                  + (" (--strict counts warnings as failures.)" if strict else ""))

    if missing:
        return 2
    if errors or (strict and warnings):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
