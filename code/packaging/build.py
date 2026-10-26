"""Build the TB executable for the computer you are on, and package it.

Usage (from the repo root):

    uv run --group build python packaging/build.py

What it does:

1. Runs PyInstaller with packaging/tb.spec -> dist/tb (dist/tb.exe on Windows).
2. Smoke-tests that binary: `tb --version` and `tb for_tanisha.tb`.
3. Packages it into dist/ under the release asset name for this OS/CPU:
     Windows x64      -> tb-windows-x64.exe   (the exe itself)
     macOS arm64      -> tb-macos-arm64.zip   (zip with `tb`, exec bit kept)
     macOS Intel      -> tb-macos-x64.zip     (local builds only; CI builds arm64)
     Linux x64        -> tb-linux-x64.tar.gz  (tar.gz with `tb`, exec bit kept)
   The archive is unpacked again into a temp folder and run once, to prove the
   packaged copy works too.
4. Writes/updates dist/SHA256SUMS.txt (sha256sum format) for every release
   asset found in dist/.

Other modes:

    python packaging/build.py --skip-build      # package an existing dist/tb
    python packaging/build.py --checksums-only  # only rewrite SHA256SUMS.txt
    python packaging/build.py --checksums-only --dist some/folder

PyInstaller cannot cross-compile; run this on each OS you want a binary for.
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
VERSION = "1.0.0"
SUMS_NAME = "SHA256SUMS.txt"

# Every file that may appear in a release (all OSes). Used for SHA256SUMS.txt.
RELEASE_ASSETS = [
    "tb-windows-x64.exe",
    "tb-windows-arm64.exe",
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
    if os_name == "windows":
        return f"tb-windows-{arch}.exe"
    if os_name == "macos":
        return f"tb-macos-{arch}.zip"
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


def run_binary(binary: Path, *args: str, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    # stdin is never a console here, so the Windows "Press Enter to close"
    # pause can never block a build.
    return subprocess.run(
        [str(binary), *args],
        input=(stdin_text or "").encode("utf-8"),
        capture_output=True,
        timeout=120,
        cwd=ROOT,
    )


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def smoke_test(binary: Path, label: str) -> None:
    log(f"smoke test ({label}): {binary}")
    res = run_binary(binary, "--version")
    out = decode(res.stdout).strip()
    print(f"  $ {binary.name} --version\n  {out}")
    if res.returncode != 0 or out != f"TB {VERSION}":
        fail(f"--version gave exit {res.returncode}, output {out!r}, stderr {decode(res.stderr)!r}")

    res = run_binary(binary, str(DEMO))
    out = decode(res.stdout)
    print(f"  $ {binary.name} {DEMO.name}")
    for line in out.splitlines():
        print(f"  | {line}")
    if res.returncode != 0:
        fail(f"{DEMO.name} exited {res.returncode}: {decode(res.stderr)}")
    if "Tanisha" not in out:
        fail(f"{DEMO.name} output did not mention Tanisha")


def make_zip(binary: Path, dest: Path) -> None:
    info = zipfile.ZipInfo("tb", date_time=(2024, 1, 1, 0, 0, 0))
    info.create_system = 3  # Unix, so the permission bits below are honoured
    info.external_attr = (0o100755 << 16)  # regular file, rwxr-xr-x
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(info, binary.read_bytes())


def make_tar_gz(binary: Path, dest: Path) -> None:
    def reset(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.mode = 0o755
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        return ti

    with tarfile.open(dest, "w:gz") as tf:
        tf.add(binary, arcname="tb", filter=reset)


def unpack_and_test(archive: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if archive.suffix == ".zip":
            # Use the system unzip when present: it restores the exec bit the
            # same way Finder / Archive Utility would. Python's zipfile does not.
            if shutil.which("unzip"):
                subprocess.run(["unzip", "-q", str(archive), "-d", tmp], check=True)
            else:
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(tmp)
                os.chmod(tmp_path / "tb", 0o755)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(tmp, filter="data")
        unpacked = tmp_path / "tb"
        if not os.access(unpacked, os.X_OK):
            fail(f"`tb` inside {archive.name} is not executable")
        smoke_test(unpacked, f"unpacked from {archive.name}")


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
    parser.add_argument("--skip-build", action="store_true", help="reuse an existing dist/tb binary")
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

    binary = dist / ("tb.exe" if os_name == "windows" else "tb")
    if not args.skip_build:
        run_pyinstaller(dist, ROOT / "build" / "pyinstaller")
    if not binary.is_file():
        fail(f"{binary} not found")

    smoke_test(binary, "fresh build")

    out = dist / name
    if out.exists():
        out.unlink()
    if os_name == "windows":
        shutil.copy2(binary, out)
    elif os_name == "macos":
        make_zip(binary, out)
        unpack_and_test(out)
    else:
        make_tar_gz(binary, out)
        unpack_and_test(out)
    log(f"packaged {out} ({out.stat().st_size / 1_000_000:.1f} MB)")

    write_checksums(dist)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
