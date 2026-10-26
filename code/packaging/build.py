r"""Build TB for the computer you are on, and package it.

Usage (from code/):

    uv run --group build python packaging/build.py

What it does:

1. Runs PyInstaller with packaging/tb.spec -> dist/tb/ (a one-folder build):
     dist/tb/tb  (tb.exe on Windows, plus tbw.exe = no console window)
     dist/tb/_internal/...
2. Smoke-tests dist/tb/tb from a folder outside the repo (so the bundled
   standard library is what gets used): `tb --version`, `tb for_tanisha.tb`,
   `tb doc`, `tb doc gui`, a program importing json/re/hash/ioc/logs, and a GUI
   app (apps/calculator, closed after 0.5 s via TB_GUI_AUTOCLOSE_MS) when the
   build Python has Tk and a display is available. Set TB_REQUIRE_GUI=1 to make
   a missing Tk/display an error instead of a skip (CI does this).
3. Packages the folder into dist/ under the release asset name(s):
     Windows x64  -> tb-windows-x64.zip         (folder tb\ with tb.exe, tbw.exe, _internal\,
                                                 apps\, examples\, for_tanisha.tb)
                     tb-windows-x64-setup.exe   (Inno Setup installer, packaging/windows/tb.iss;
                                                 needs ISCC.exe; --no-installer skips it)
     macOS arm64  -> tb-macos-arm64.zip         (folder tb/ with tb, _internal/, apps/, examples/,
                                                 for_tanisha.tb; exec bits and symlinks kept)
     macOS Intel  -> tb-macos-x64.zip           (local builds only; CI builds arm64)
     Linux x64    -> tb-linux-x64.tar.gz        (folder tb/, same layout as macOS)
   apps/, examples/ and for_tanisha.tb are copied into dist/tb/ first, so they
   ship next to the launcher. Each archive is unpacked again into a temp
   folder, smoke-tested, and every example/app is run from the unpacked copy
   (packaging/run_examples.py --root <unpacked tb/>).
4. Writes/updates dist/SHA256SUMS.txt (sha256sum format) for every release
   asset found in dist/.

Other modes:

    python packaging/build.py --skip-build      # package an existing dist/tb/
    python packaging/build.py --checksums-only  # only rewrite SHA256SUMS.txt
    python packaging/build.py --checksums-only --dist some/folder

PyInstaller cannot cross-compile; run this on each OS you want a build for.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "tb.spec"
DEMO = ROOT / "for_tanisha.tb"
VERSION = "2.0.0"
GUI_APP = ROOT / "apps" / "calculator" / "main.tb"

# A program that touches several native libraries, with its exact output.
LIB_SNIPPET = r'''import json
import re
import hash
import ioc
import logs
let data = json.parse("{\"a\": [1, 2, 3]}")
say(json.stringify(data))
say(re.find_all("[0-9]+", "port 22 and 443"))
say(hash.sha256("tb"))
let found = ioc.extract("bad host 203.0.113.7 at evil.example.com")
say(found.ips)
let rec = logs.parse_line("Jan  1 10:00:00 host sshd[1]: Failed password for root from 203.0.113.7 port 22 ssh2", "auth")
say(f"{rec.user} {rec.ip} {rec.success}")
'''
LIB_SNIPPET_OUT = """{"a": [1, 2, 3]}
["22", "443"]
7aa5ae9ecf82f6fc5208ecd0940fd71d50cd4ac596fccac2ede05af806a87b6e
["203.0.113.7"]
root 203.0.113.7 false
"""
SUMS_NAME = "SHA256SUMS.txt"
ISS = ROOT / "packaging" / "windows" / "tb.iss"
FIXED_TIME = (2024, 1, 1, 0, 0, 0)

# Every file that may appear in a release (all OSes). Used for SHA256SUMS.txt.
RELEASE_ASSETS = [
    "tb-windows-x64-setup.exe",
    "tb-windows-x64.zip",
    "tb-windows-arm64-setup.exe",
    "tb-windows-arm64.zip",
    "tb-macos-arm64.zip",
    "tb-macos-x64.zip",
    "tb-linux-x64.tar.gz",
    "tb-linux-arm64.tar.gz",
    "tb-python.zip",
]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[build] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def detect_target() -> tuple[str, str]:
    """Return (os_name, arch) using release naming: windows/macos/linux, x64/arm64."""
    if sys.platform == "win32":
        os_name = "windows"
    elif sys.platform == "darwin":
        os_name = "macos"
    elif sys.platform.startswith("linux"):
        os_name = "linux"
    else:
        fail(f"Unsupported platform: {sys.platform}")
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        fail(f"Unsupported CPU architecture: {machine}")
    return os_name, arch


def asset_name(os_name: str, arch: str) -> str:
    if os_name in ("windows", "macos"):
        return f"tb-{os_name}-{arch}.zip"
    return f"tb-linux-{arch}.tar.gz"


def run_pyinstaller(dist: Path, work: Path) -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm", "--clean",
        "--distpath", str(dist),
        "--workpath", str(work),
    ]
    log("running: " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        fail("PyInstaller failed (is it installed? use `uv run --group build ...`)")


def run_binary(binary: Path, *args: str, stdin_text: str | None = None,
               cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    # stdin is never a console here, so the Windows "Press Enter to close"
    # pause can never block a build.
    return subprocess.run(
        [str(binary), *args],
        input=(stdin_text or "").encode("utf-8"),
        capture_output=True,
        timeout=120,
        cwd=cwd or ROOT,
        env=env,
    )


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def show(binary: Path, args: list[str], res: subprocess.CompletedProcess, max_lines: int = 40) -> str:
    out = decode(res.stdout)
    print(f"  $ {binary.name} {' '.join(args)}    (exit {res.returncode})")
    lines = out.splitlines()
    for line in lines[:max_lines]:
        print(f"  | {line}")
    if len(lines) > max_lines:
        print(f"  | ... ({len(lines) - max_lines} more lines)")
    err = decode(res.stderr).strip()
    if err:
        for line in err.splitlines()[:20]:
            print(f"  ! {line}")
    return out


def check(binary: Path, args: list[str], cwd: Path, env: dict | None = None,
          max_lines: int = 40) -> str:
    res = run_binary(binary, *args, cwd=cwd, env=env)
    out = show(binary, args, res, max_lines)
    if res.returncode != 0:
        fail(f"`tb {' '.join(args)}` exited {res.returncode}")
    return out


def tk_available() -> tuple[bool, str]:
    """Can the binary we just built show a window here?"""
    try:
        import tkinter  # noqa: F401  (if the build Python lacks Tk, so does the binary)
    except ImportError:
        return False, "the Python that ran PyInstaller has no tkinter, so the binary has no gui"
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False, "no display (run under xvfb-run to test the gui)"
    return True, ""


def smoke_test(binary: Path, label: str) -> None:
    log(f"smoke test ({label}): {binary}")
    with tempfile.TemporaryDirectory() as tmp:
        # Run from an empty folder so only the bundled tblib can be found.
        work = Path(tmp)

        out = check(binary, ["--version"], work).strip()
        if out != f"TB {VERSION}":
            fail(f"--version printed {out!r}, expected 'TB {VERSION}'")

        out = check(binary, [str(DEMO)], work)
        if "Tanisha" not in out:
            fail(f"{DEMO.name} output did not mention Tanisha")

        out = check(binary, ["doc"], work, max_lines=8)
        for lib in ("gui", "json", "hash", "ioc", "logs", "detect"):
            if f" {lib} " not in out:
                fail(f"`tb doc` does not list the {lib} library")

        out = check(binary, ["doc", "gui"], work, max_lines=4)
        if "gui.window" not in out:
            fail("`tb doc gui` does not describe gui.window")

        snippet = work / "libs.tb"
        snippet.write_text(LIB_SNIPPET, encoding="utf-8")
        out = check(binary, [str(snippet)], work)
        if out != LIB_SNIPPET_OUT:
            fail(f"library snippet printed {out!r}, expected {LIB_SNIPPET_OUT!r}")

        ok, why = tk_available()
        if not ok:
            if os.environ.get("TB_REQUIRE_GUI") == "1":
                fail(f"gui check required but impossible: {why}")
            log(f"SKIP gui check: {why}")
        else:
            env = dict(os.environ, TB_GUI_AUTOCLOSE_MS="500")
            check(binary, [str(GUI_APP)], work, env=env)
            log(f"gui check passed: {GUI_APP.relative_to(ROOT)} opened and closed itself")


def folder_files(folder: Path) -> list[Path]:
    """Every file, symlink and folder under folder, sorted (folders first)."""
    return sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix())


def make_zip(folder: Path, dest: Path) -> None:
    """Zip folder as <folder.name>/..., keeping exec bits and symlinks (macOS
    one-folder builds contain symlinks, e.g. inside Python.framework)."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in folder_files(folder):
            arc = (Path(folder.name) / path.relative_to(folder)).as_posix()
            st = path.lstat()
            if path.is_symlink():
                info = zipfile.ZipInfo(arc, date_time=FIXED_TIME)
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                zf.writestr(info, os.readlink(path))
            elif path.is_dir():
                info = zipfile.ZipInfo(arc + "/", date_time=FIXED_TIME)
                info.create_system = 3
                info.external_attr = (0o40755 << 16) | 0x10
                zf.writestr(info, b"")
            else:
                info = zipfile.ZipInfo(arc, date_time=FIXED_TIME)
                info.create_system = 3
                executable = os.name == "nt" and path.suffix.lower() in (".exe", ".dll", ".pyd") \
                    or bool(st.st_mode & 0o111)
                info.external_attr = ((0o100755 if executable else 0o100644) << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())


def make_tar_gz(folder: Path, dest: Path) -> None:
    def reset(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.mtime = 1704067200  # 2024-01-01
        return ti

    with tarfile.open(dest, "w:gz") as tf:
        tf.add(folder, arcname=folder.name, filter=reset)


def unpack_and_test(archive: Path, exe_name: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if archive.suffix == ".zip":
            # Use the system unzip when present: it restores exec bits and
            # symlinks the way Finder / Archive Utility would.
            if os.name != "nt" and shutil.which("unzip"):
                subprocess.run(["unzip", "-q", str(archive), "-d", tmp], check=True)
            else:
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(tmp)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(tmp, filter="tar")
        unpacked = tmp_path / "tb" / exe_name
        if not unpacked.is_file():
            fail(f"{archive.name} does not contain tb/{exe_name}")
        if os.name != "nt" and not os.access(unpacked, os.X_OK):
            fail(f"tb/{exe_name} inside {archive.name} is not executable")
        smoke_test(unpacked, f"unpacked from {archive.name}")
        run_examples_in(tmp_path / "tb", exe_name)


# Copied next to the launcher, so the apps and examples run right after unzipping.
EXTRAS = ["apps", "examples", "for_tanisha.tb"]
SKIP_NAMES = {"__pycache__", ".pytest_cache", "todo.json", "journal.json", ".DS_Store"}


def add_extras(folder: Path) -> None:
    """Copy apps/, examples/ and for_tanisha.tb into the built tb/ folder
    (skipping caches and data an app saved while being tried out)."""
    def ignore(_dir, names):
        return [n for n in names if n in SKIP_NAMES or n.startswith(".") or n.endswith((".pyc", ".pyo"))]

    for name in EXTRAS:
        src, dst = ROOT / name, folder / name
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst, ignore=ignore)
        else:
            shutil.copy2(src, dst)
    log(f"added {', '.join(EXTRAS)} to {folder}")


def run_examples_in(folder: Path, exe_name: str) -> None:
    """Run every example/app from the copies inside folder (as a user would)."""
    cmd = [sys.executable, str(ROOT / "packaging" / "run_examples.py"),
           str(folder / exe_name), "--root", str(folder)]
    log("running: " + " ".join(cmd))
    if subprocess.run(cmd).returncode != 0:
        fail(f"examples/apps failed inside {folder}")


def find_iscc() -> str | None:
    candidates = [
        os.environ.get("ISCC", ""),
        shutil.which("iscc") or "",
        shutil.which("ISCC") or "",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def build_installer(folder: Path, dist: Path, arch: str) -> Path:
    iscc = find_iscc()
    if not iscc:
        fail("Inno Setup (ISCC.exe) not found. Install it (choco install innosetup -y) "
             "or pass --no-installer.")
    out = dist / f"tb-windows-{arch}-setup.exe"
    if out.exists():
        out.unlink()
    cmd = [
        iscc, "/Q",
        f"/DAppVersion={VERSION}",
        f"/DSourceDir={folder}",
        f"/DRepoDir={ROOT}",
        f"/DOutputDir={dist}",
        f"/Ftb-windows-{arch}-setup",
        str(ISS),
    ]
    log("running: " + " ".join(cmd))
    if subprocess.run(cmd, cwd=ROOT).returncode != 0 or not out.is_file():
        fail("Inno Setup failed")
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(dist: Path) -> Path:
    """(Re)write SHA256SUMS.txt for every known release asset present in dist."""
    names = [n for n in RELEASE_ASSETS if (dist / n).is_file()]
    sums = dist / SUMS_NAME
    lines = [f"{sha256(dist / n)}  {n}\n" for n in sorted(names)]
    with open(sums, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    log(f"wrote {sums} ({len(lines)} entries)")
    for line in lines:
        print("  " + line, end="")
    return sums


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", default=str(ROOT / "dist"), help="output folder (default: dist/)")
    parser.add_argument("--skip-build", action="store_true", help="reuse an existing dist/tb/ folder")
    parser.add_argument("--no-installer", action="store_true", help="Windows: skip the Inno Setup installer")
    parser.add_argument("--checksums-only", action="store_true", help="only rewrite SHA256SUMS.txt")
    args = parser.parse_args()

    dist = Path(args.dist).resolve()
    dist.mkdir(parents=True, exist_ok=True)

    if args.checksums_only:
        write_checksums(dist)
        return 0

    os_name, arch = detect_target()
    name = asset_name(os_name, arch)
    log(f"target: {os_name}-{arch} -> {name}")
    if os_name == "macos" and arch == "x64":
        log("note: Intel Mac build. The official release (CI) only ships tb-macos-arm64.zip.")

    folder = dist / "tb"
    exe_name = "tb.exe" if os_name == "windows" else "tb"
    binary = folder / exe_name
    if not args.skip_build:
        if folder.is_file() or folder.is_symlink():
            folder.unlink()  # an old one-file build used to live at dist/tb
        run_pyinstaller(dist, ROOT / "build" / "pyinstaller")
    if not binary.is_file():
        fail(f"{binary} not found")
    if os_name == "windows" and not (folder / "tbw.exe").is_file():
        fail(f"{folder / 'tbw.exe'} not found")

    add_extras(folder)
    smoke_test(binary, "fresh build")

    out = dist / name
    if out.exists():
        out.unlink()
    if os_name == "linux":
        make_tar_gz(folder, out)
    else:
        make_zip(folder, out)
    log(f"packaged {out} ({out.stat().st_size / 1_000_000:.1f} MB)")
    unpack_and_test(out, exe_name)

    if os_name == "windows" and not args.no_installer:
        setup = build_installer(folder, dist, arch)
        log(f"built {setup} ({setup.stat().st_size / 1_000_000:.1f} MB)")
        log("test it with: python packaging/windows/test_installer.py " + str(setup))

    write_checksums(dist)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
