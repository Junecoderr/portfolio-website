"""Sign a PyInstaller build folder (dist/tb/) for macOS Gatekeeper.

Usage (from code/, on a Mac):

    python packaging/macos/sign.py dist/tb "Developer ID Application: Name (TEAMID)"
    python packaging/macos/sign.py dist/tb -      # ad-hoc: test the hardened runtime locally

Every Mach-O file inside (Python, extension modules, Tcl/Tk, ...) is signed
first, deepest first, then the programs tb and tblaunch with the hardened
runtime and packaging/macos/entitlements.plist. Each signature gets a secure
timestamp (except ad-hoc ones), both of which notarisation requires. Symlinks
are skipped (they point at files that are signed themselves).

Notarising and stapling are separate steps in .github/workflows/tb-release.yml.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ENTITLEMENTS = Path(__file__).resolve().parent / "entitlements.plist"
PROGRAMS = ("tb", "tblaunch")
# Thin 32/64-bit Mach-O in either byte order, and fat (universal) files.
MACHO_MAGICS = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}
FAT_MAGIC = b"\xca\xfe\xba\xbe"


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] in MACHO_MAGICS:
        return True
    # Java .class files share 0xCAFEBABE; there the next word is a version (>= 45),
    # in a fat Mach-O it is the number of architectures (a handful).
    return head[:4] == FAT_MAGIC and 0 < int.from_bytes(head[4:8], "big") < 20


def signing_order(folder: Path) -> list[Path]:
    """Mach-O files to sign: libraries deepest first, then the programs."""
    found = [p for p in folder.rglob("*") if is_macho(p)]
    programs = [folder / name for name in PROGRAMS if folder / name in found]
    libs = [p for p in found if p not in programs]
    libs.sort(key=lambda p: (-len(p.relative_to(folder).parts), p.as_posix()))
    return libs + programs


def codesign_args(path: Path, identity: str, program: bool) -> list[str]:
    cmd = ["codesign", "--force", "--options", "runtime", "--sign", identity]
    if identity != "-":
        cmd.append("--timestamp")
    if program:
        cmd += ["--entitlements", str(ENTITLEMENTS)]
    return cmd + [str(path)]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    folder, identity = Path(argv[0]).resolve(), argv[1]
    order = signing_order(folder)
    missing = [n for n in PROGRAMS if folder / n not in order]
    if missing:
        print(f"sign.py: {folder} has no {', '.join(missing)}", file=sys.stderr)
        return 1
    for path in order:
        program = path.parent == folder and path.name in PROGRAMS
        subprocess.run(codesign_args(path, identity, program), check=True)
    for name in PROGRAMS:
        subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", str(folder / name)], check=True)
    print(f"sign.py: signed {len(order)} Mach-O files in {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
