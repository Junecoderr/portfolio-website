"""Install, check and uninstall tb-windows-x64-setup.exe silently (Windows only).

Usage (from code/):
    python packaging/windows/test_installer.py dist/tb-windows-x64-setup.exe

Steps:
1. Silent per-user install into a fresh temp folder:
   setup.exe /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /DIR=<tmp>\\TB
2. Checks: tb.exe, tbw.exe, apps\\, examples\\, welcome.tb are there;
   `tb.exe --version` prints the version; the folder is on the user PATH
   (HKCU\\Environment); .tb is associated with TB.Program;
   packaging/run_examples.py passes against the installed tb.exe and the
   installed examples/apps; tbw.exe opens a GUI app that closes itself
   (TB_GUI_AUTOCLOSE_MS) and exits 0.
3. Silent uninstall, then: the folder is gone, PATH no longer has it, the
   .tb association is gone.
Exit code 0 when everything passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(CODE / "packaging"))
from build import VERSION  # noqa: E402


def step(msg: str) -> None:
    print(f"[installer] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[installer] FAIL: {msg}", flush=True)
    sys.exit(1)


def user_path() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "Path")[0]
    except FileNotFoundError:
        return ""


def on_path(folder: Path) -> bool:
    want = os.path.normcase(str(folder).rstrip("\\"))
    return any(os.path.normcase(p.strip().rstrip("\\")) == want for p in user_path().split(";") if p.strip())


def tb_association() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\TB.Program\shell\open\command") as key:
            return winreg.QueryValueEx(key, "")[0]
    except FileNotFoundError:
        return None


def main() -> int:
    if os.name != "nt":
        print("This test only runs on Windows.")
        return 2
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    setup = Path(sys.argv[1]).resolve()
    if not setup.is_file():
        fail(f"{setup} not found")

    base = Path(tempfile.mkdtemp(prefix="tb-install-"))
    app = base / "TB"
    log_file = base / "setup.log"
    step(f"installing {setup.name} into {app}")
    res = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER",
                          "/NORESTART", f"/DIR={app}", f"/LOG={log_file}"], timeout=600)
    if res.returncode != 0:
        print(log_file.read_text(errors="replace") if log_file.exists() else "(no log)")
        fail(f"setup exited {res.returncode}")

    for rel in ("tb.exe", "tbw.exe", "_internal", "apps", "examples", "welcome.tb", "unins000.exe"):
        if not (app / rel).exists():
            fail(f"{rel} missing from the install folder")
    step("installed files are there")

    tb = app / "tb.exe"
    out = subprocess.run([str(tb), "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         timeout=120)
    print(f"  $ tb.exe --version -> {out.stdout.strip()!r} (exit {out.returncode})")
    if out.returncode != 0 or out.stdout.strip() != f"TB {VERSION}":
        fail("tb.exe --version")

    if not on_path(app):
        fail(f"{app} is not on the user PATH: {user_path()!r}")
    step("install folder is on the user PATH")
    assoc = tb_association()
    if not assoc or "tb.exe" not in assoc.lower():
        fail(f".tb association missing or wrong: {assoc!r}")
    step(f".tb opens with: {assoc}")

    step("running examples and apps from the installed copy")
    res = subprocess.run([sys.executable, str(CODE / "packaging" / "run_examples.py"), str(tb),
                          "--root", str(app)], timeout=1800)
    if res.returncode != 0:
        fail("run_examples.py against the installed tb.exe")

    # tbw.exe has no console: the window closes itself after 0.5 s and TB exits.
    # Output is not captured on purpose, so tbw.exe really runs without std
    # handles (as from a Start-menu shortcut). If the app failed, tbw.exe would
    # show its errors in a dialog at exit and wait for a click, so a hang here
    # means "the app had an error", reported via the timeout below.
    env = dict(os.environ, TB_GUI_AUTOCLOSE_MS="500")
    try:
        gui = subprocess.run([str(app / "tbw.exe"), str(app / "apps" / "calculator" / "main.tb")],
                             env=env, timeout=90, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        fail("tbw.exe did not exit after the calculator closed itself "
             "(probably an error dialog is waiting; run the app with tb.exe to see the error)")
    print(f"  $ tbw.exe apps\\calculator\\main.tb (autoclose) -> exit {gui.returncode}")
    if gui.returncode != 0:
        fail("tbw.exe could not run the calculator app")

    step("uninstalling")
    res = subprocess.run([str(app / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                         timeout=600)
    if res.returncode != 0:
        fail(f"uninstaller exited {res.returncode}")
    # The uninstaller re-launches itself from a temp copy and returns early:
    # wait for it to finish removing the folder.
    for _ in range(120):
        if not app.exists():
            break
        time.sleep(1)
    if app.exists():
        left = [str(p.relative_to(app)) for p in app.rglob("*")]
        fail(f"{app} still exists after uninstall; left over: {left[:50]}")
    step("install folder removed")
    if on_path(app):
        fail("the install folder is still on the user PATH")
    step("PATH entry removed")
    if tb_association() is not None:
        fail(".tb association still present")
    step(".tb association removed")
    step("all installer checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
