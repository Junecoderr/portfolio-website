"""Run every examples/*.tb, for_tanisha.tb and the non-GUI apps through a built TB binary.

Usage:
    uv run python packaging/run_examples.py dist/tb/tb        # or dist/tb/tb.exe
    python packaging/run_examples.py <TB>/tb.exe --root <TB>  # an installed copy:
        use the examples/, apps/ and for_tanisha.tb found in <TB>

- An example with a matching <name>.in file gets that file on stdin (for ask()).
- An example with a matching <name>.out file has its output compared, unless it
  calls care() (random message) or reads input (prompts make output differ).
- Every example must exit with code 0.
- Every apps/*/main.tb that does not `import gui` is run with no arguments and
  must exit 0, except the apps in APP_EXIT_CODES (they need network or API
  keys, so other exit codes are legitimate there).
- `tb test apps/log-triage` must pass.

Used by .github/workflows/release.yml to check the real binaries.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Apps that legitimately do not exit 0 when run with no arguments on a CI box.
APP_EXIT_CODES = {
    "ip-reputation": {1, 2},   # no indicator given -> usage (2); no API keys -> 1
    "uptime-monitor": {0, 1},  # 1 = a site is down or the runner is offline
}


def norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip("\n").split("\n"))


def main() -> int:
    global ROOT
    args = sys.argv[1:]
    if len(args) == 3 and args[1] == "--root":
        ROOT = Path(args[2]).resolve()  # e.g. an installed TB folder with examples/ and apps/
        args = args[:1]
    if len(args) != 1:
        print(__doc__)
        return 2
    binary = Path(args[0]).resolve()
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

    print(f"\n{len(programs) - failures}/{len(programs)} example programs passed\n")

    apps = sorted((ROOT / "apps").glob("*/main.tb"))
    for app in apps:
        name = app.parent.name
        rel = app.relative_to(ROOT)
        if "import gui" in app.read_text(encoding="utf-8"):
            print(f"skip {rel} (GUI app; build.py opens one GUI app)")
            continue
        allowed = APP_EXIT_CODES.get(name, {0})
        try:
            res = subprocess.run([str(binary), str(app)], input=b"", capture_output=True,
                                 timeout=120, cwd=ROOT)
        except subprocess.TimeoutExpired:
            print(f"FAIL {rel}: timed out")
            failures += 1
            continue
        if res.returncode not in allowed:
            print(f"FAIL {rel}: exit {res.returncode} (allowed: {sorted(allowed)})")
            print(res.stdout.decode("utf-8", "replace")[-2000:])
            print(res.stderr.decode("utf-8", "replace")[-2000:])
            failures += 1
            continue
        note = "" if allowed == {0} else f" (exit {res.returncode}; allowed {sorted(allowed)}: needs network/keys)"
        print(f"ok   {rel}{note}")

    res = subprocess.run([str(binary), "test", "apps/log-triage"], input=b"", capture_output=True,
                         timeout=120, cwd=ROOT)
    summary = res.stdout.decode("utf-8", "replace").strip().splitlines()
    if res.returncode != 0:
        print("FAIL tb test apps/log-triage\n" + "\n".join(summary[-30:]))
        print(res.stderr.decode("utf-8", "replace"))
        failures += 1
    else:
        print(f"ok   tb test apps/log-triage: {summary[-1] if summary else ''}")

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
