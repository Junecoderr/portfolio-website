"""Make the Linux .deb and .rpm packages from a built dist/tb/ folder.

Usage (from code/, on Linux, after packaging/build.py):

    python packaging/linux_packages.py                  # both, for this computer's CPU
    python packaging/linux_packages.py --deb            # only the .deb (needs dpkg-deb)
    python packaging/linux_packages.py --rpm            # only the .rpm (needs rpmbuild)

Both install TB into /usr/lib/tb/ (tb, tblaunch, _internal/, apps/, examples/,
welcome.tb) with a /usr/bin/tb symlink, and land in dist/ as

    tb_<version>_amd64.deb   tb_<version>_arm64.deb
    tb-<version>-1.x86_64.rpm   tb-<version>-1.aarch64.rpm

The frozen programs carry their Python inside, so the only hard dependency is
glibc (the build runs on Ubuntu 22.04: glibc 2.35). The X11 libraries the gui
library needs are left to the desktop (PyInstaller never bundles them), so
they are recommendations. The rpm turns off stripping and automatic
dependency scanning: stripping would cut the archive PyInstaller appends to
the programs, and the scan would require every bundled library separately.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import build  # noqa: E402  (VERSION, log, fail)

DEB_ARCH = {"x64": "amd64", "arm64": "arm64"}
RPM_ARCH = {"x64": "x86_64", "arm64": "aarch64"}
SUMMARY = "TB - a small, friendly programming language"
DESCRIPTION = ("TB is a small, friendly programming language with a standard library for\n"
               "security work: logs, packet captures, malware triage, detection rules.")
HOMEPAGE = "https://code.tanisha.ceo"
# shortcut: a no-reply address; the owner can set TB_PACKAGE_MAINTAINER to a real one.
MAINTAINER = os.environ.get("TB_PACKAGE_MAINTAINER", "TB <noreply@code.tanisha.ceo>")
GUI_LIBS_DEB = "libx11-6, libxft2, libxss1, libfontconfig1"


def deb_name(version: str, arch: str) -> str:
    return f"tb_{version}_{DEB_ARCH[arch]}.deb"


def rpm_name(version: str, arch: str) -> str:
    return f"tb-{version}-1.{RPM_ARCH[arch]}.rpm"


def deb_control(version: str, arch: str, size_kb: int) -> str:
    return (
        "Package: tb\n"
        f"Version: {version}\n"
        f"Architecture: {DEB_ARCH[arch]}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Installed-Size: {size_kb}\n"
        "Depends: libc6 (>= 2.35)\n"
        f"Recommends: {GUI_LIBS_DEB}\n"
        "Section: devel\n"
        "Priority: optional\n"
        f"Homepage: {HOMEPAGE}\n"
        f"Description: {SUMMARY}\n"
        + "".join(f" {line}\n" for line in DESCRIPTION.splitlines())
    )


def rpm_spec(version: str, arch: str, stage: Path) -> str:
    return f"""Name: tb
Version: {version}
Release: 1
Summary: {SUMMARY}
License: MIT
URL: {HOMEPAGE}
BuildArch: {RPM_ARCH[arch]}
AutoReqProv: no
Requires: glibc >= 2.35
Recommends: libX11 libXft libXScrnSaver fontconfig
%global __os_install_post %{{nil}}
%global __arch_install_post %{{nil}}
%global debug_package %{{nil}}
%global _build_id_links none

%description
{DESCRIPTION}

%install
cp -a "{stage.as_posix()}/." "%{{buildroot}}/"

%files
/usr/lib/tb
/usr/bin/tb
"""


def stage_tree(folder: Path, root: Path) -> int:
    """Copy folder to root/usr/lib/tb and link root/usr/bin/tb; returns the size in KiB."""
    lib = root / "usr" / "lib" / "tb"
    shutil.copytree(folder, lib, symlinks=True)
    (root / "usr" / "bin").mkdir(parents=True)
    os.symlink("../lib/tb/tb", root / "usr" / "bin" / "tb")
    size = sum(p.lstat().st_size for p in lib.rglob("*") if not p.is_dir())
    return (size + 1023) // 1024


def build_deb(folder: Path, dist: Path, version: str, arch: str) -> Path:
    out = dist / deb_name(version, arch)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        size_kb = stage_tree(folder, root)
        (root / "DEBIAN").mkdir()
        (root / "DEBIAN" / "control").write_text(deb_control(version, arch, size_kb), encoding="utf-8")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(out)], check=True)
    return out


def build_rpm(folder: Path, dist: Path, version: str, arch: str) -> Path:
    out = dist / rpm_name(version, arch)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        stage_tree(folder, tmp_path / "stage")
        spec = tmp_path / "tb.spec"
        spec.write_text(rpm_spec(version, arch, tmp_path / "stage"), encoding="utf-8")
        subprocess.run([
            "rpmbuild", "-bb", str(spec), "--target", RPM_ARCH[arch],
            "--define", f"_topdir {tmp_path / 'rpmbuild'}",
            "--define", f"_rpmdir {dist}",
            "--define", "_build_name_fmt %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm",
        ], check=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", default=str(ROOT / "dist"))
    parser.add_argument("--deb", action="store_true", help="only the .deb")
    parser.add_argument("--rpm", action="store_true", help="only the .rpm")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        build.fail("the .deb and .rpm are built on Linux")
    dist = Path(args.dist).resolve()
    folder = dist / "tb"
    if not (folder / "tb").is_file():
        build.fail(f"{folder / 'tb'} not found: run packaging/build.py first")
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if not arch:
        build.fail(f"unsupported CPU: {platform.machine()}")
    both = not (args.deb or args.rpm)
    made = []
    if args.deb or both:
        made.append(build_deb(folder, dist, build.VERSION, arch))
    if args.rpm or both:
        made.append(build_rpm(folder, dist, build.VERSION, arch))
    for path in made:
        if not path.is_file():
            build.fail(f"{path.name} was not made")
        build.log(f"built {path} ({path.stat().st_size / 1_000_000:.1f} MB)")
    build.write_checksums(dist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
