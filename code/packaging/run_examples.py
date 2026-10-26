"""Run every examples/*.tb, welcome.tb, every app and the TB test suites through a built TB binary.

Usage:
    uv run python packaging/run_examples.py dist/tb/tb        # or dist/tb/tb.exe
    python packaging/run_examples.py <TB>/tb.exe --root <TB>  # an installed copy:
        use the examples/, apps/ and welcome.tb found in <TB>
    ... --gui    also open every GUI app (closed after 0.5 s via
                 TB_GUI_AUTOCLOSE_MS); needs Tk in the build and a display

- An example with a matching <name>.in file gets that file on stdin (for ask()).
- An example with a matching <name>.out file has its output compared, unless it
  calls care() (random message) or reads input (prompts make output differ).
- Every example must exit with code 0.
- Every apps/*/main.tb that does not `import gui` is run with no arguments and
  must exit 0, except the apps in APP_EXIT_CODES (they need network, API keys
  or arguments, so other exit codes are legitimate there) and the server apps
  in SERVER_APPS (started on a free port, must answer HTTP, then stopped).
- GUI apps (`import gui`) are skipped unless --gui is given.
- `tb test spec`, `tb test apps/log-triage` and `tb test packages` must pass.
  spec/ and packages/ are not part of the binary downloads, so with --root
  they come from <root> when present, else from this source checkout.

Used by .github/workflows/tb-release.yml (and build.py / test_installer.py)
to check the real binaries.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT  # the source checkout (for spec/ and packages/)

# Apps that legitimately do not exit 0 when run with no arguments on a CI box.
APP_EXIT_CODES = {
    "ip-reputation": {1, 2},    # no indicator given -> usage (2); no API keys -> 1
    "uptime-monitor": {0, 1},   # 1 = a site is down or the runner is offline
    "cert-check": {0, 1, 2},    # no host given -> usage (2); TLS needs the network
    "dns-check": {0, 1, 2},     # no domain given -> usage (2); DNS needs the network
}

# Server apps: started with these arguments (PORT is replaced by a free port),
# must answer GET <path> with 200 within the timeout, and are then stopped.
SERVER_APPS = {
    "dashboard": (["--port", "PORT"], "/api/summary"),
}

# Test suites run with `tb test <folder>` (folders relative to the root).
TEST_SUITES = ["spec", "apps/log-triage", "packages"]


def norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip("\n").split("\n"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server_app(binary: Path, app: Path, args: list[str], path: str, timeout: float = 60) -> tuple[bool, str]:
    port = free_port()
    argv = [str(binary), str(app), "--", *[str(port) if a == "PORT" else a for a in args]]
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=ROOT)
    url = f"http://127.0.0.1:{port}{path}"
    deadline = time.monotonic() + timeout
    ok, why = False, f"no answer from {url} within {timeout:.0f} s"
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                why = f"exited {proc.returncode} before answering"
                break
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    ok, why = resp.status == 200, f"GET {path} -> {resp.status}"
                    break
            except OSError:
                time.sleep(0.3)
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    if not ok:
        why += "\n" + out.decode("utf-8", "replace")[-2000:]
    return ok, why


def main() -> int:
    global ROOT
    args = sys.argv[1:]
    gui = "--gui" in args
    args = [a for a in args if a != "--gui"]
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

    programs = sorted((ROOT / "examples").glob("*.tb")) + [ROOT / "welcome.tb"]
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
    app_failures = 0
    for app in apps:
        name = app.parent.name
        rel = app.relative_to(ROOT)
        is_gui = "import gui" in app.read_text(encoding="utf-8")
        if is_gui and not gui:
            print(f"skip {rel} (GUI app; run with --gui to open it)")
            continue
        if name in SERVER_APPS:
            server_args, path = SERVER_APPS[name]
            ok, why = run_server_app(binary, app, server_args, path)
            print(f"{'ok  ' if ok else 'FAIL'} {rel} (server: {why})")
            app_failures += not ok
            continue
        allowed = APP_EXIT_CODES.get(name, {0})
        env = dict(os.environ, TB_GUI_AUTOCLOSE_MS="500") if is_gui else None
        try:
            res = subprocess.run([str(binary), str(app)], input=b"", capture_output=True,
                                 timeout=120, cwd=ROOT, env=env)
        except subprocess.TimeoutExpired:
            print(f"FAIL {rel}: timed out")
            app_failures += 1
            continue
        if res.returncode not in allowed:
            print(f"FAIL {rel}: exit {res.returncode} (allowed: {sorted(allowed)})")
            print(res.stdout.decode("utf-8", "replace")[-2000:])
            print(res.stderr.decode("utf-8", "replace")[-2000:])
            app_failures += 1
            continue
        note = " (GUI, closed itself)" if is_gui else ""
        if allowed != {0}:
            note = f" (exit {res.returncode}; allowed {sorted(allowed)}: needs network/keys/arguments)"
        print(f"ok   {rel}{note}")
    failures += app_failures
    print(f"\n{len(apps) - app_failures}/{len(apps)} apps checked without failure\n")

    for suite in TEST_SUITES:
        folder = ROOT / suite
        if not folder.is_dir():
            folder = SOURCE / suite
        if not folder.is_dir():
            print(f"FAIL tb test {suite}: folder not found")
            failures += 1
            continue
        res = subprocess.run([str(binary), "test", str(folder)], input=b"", capture_output=True,
                             timeout=300, cwd=ROOT)
        summary = res.stdout.decode("utf-8", "replace").strip().splitlines()
        if res.returncode != 0:
            print(f"FAIL tb test {suite}\n" + "\n".join(summary[-30:]))
            print(res.stderr.decode("utf-8", "replace"))
            failures += 1
        else:
            print(f"ok   tb test {suite}: {summary[-1] if summary else ''}")

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
