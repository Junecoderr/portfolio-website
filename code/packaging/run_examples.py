"""Run every examples/*.tb (plus for_tanisha.tb) through a built TB binary.

Usage:
    uv run python packaging/run_examples.py dist/tb        # or dist/tb.exe

- An example with a matching <name>.in file gets that file on stdin (for ask()).
- An example with a matching <name>.out file has its output compared, unless it
  calls care() (random message) or reads input (prompts make output differ).
- Every example must exit with code 0.

Used by .github/workflows/release.yml to check the real binaries.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip("\n").split("\n"))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        print(f"binary not found: {binary}")
        return 2

    programs = sorted((ROOT / "examples").glob("*.tb")) + [ROOT / "for_tanisha.tb"]
    failures = 0
    for prog in programs:
        src = prog.read_text(encoding="utf-8")
        stdin_file = prog.with_suffix(".in")
        expected_file = prog.with_suffix(".out")
        stdin = stdin_file.read_bytes() if stdin_file.is_file() else b""
        try:
            res = subprocess.run(
                [str(binary), str(prog)], input=stdin, capture_output=True, timeout=60, cwd=ROOT
            )
        except subprocess.TimeoutExpired:
            print(f"FAIL {prog.name}: timed out")
            failures += 1
            continue
        out = res.stdout.decode("utf-8", errors="replace")
        err = res.stderr.decode("utf-8", errors="replace")
        rel = prog.relative_to(ROOT)
        if res.returncode != 0:
            print(f"FAIL {rel}: exit {res.returncode}\n{err}")
            failures += 1
            continue
        compare = expected_file.is_file() and "care(" not in src and not stdin_file.is_file()
        if compare:
            expected = expected_file.read_text(encoding="utf-8")
            if norm(out) != norm(expected):
                if expected.isascii():
                    print(f"FAIL {rel}: output differs from {expected_file.name}")
                    print("--- expected ---\n" + expected + "--- got ---\n" + out)
                    failures += 1
                    continue
                # Non-ASCII text can be re-encoded by a Windows console code page.
                print(f"WARN {rel}: non-ASCII output differs from {expected_file.name} (not failing)")
        how = " (stdin from .in)" if stdin_file.is_file() else ""
        how += " (output matches .out)" if compare else ""
        print(f"ok   {rel}{how}")

    print(f"\n{len(programs) - failures}/{len(programs)} programs passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
