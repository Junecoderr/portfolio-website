"""The release packaging: package-manager manifests, .deb/.rpm metadata, macOS
signing order, and a lint of .github/workflows/tb-release.yml (#40, #41)."""

from __future__ import annotations

import gzip
import io
import json
import os
import plistlib
import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
sys.path.insert(0, str(ROOT / "packaging" / "macos"))

import build  # noqa: E402
import linux_packages  # noqa: E402
import release_manifests as rm  # noqa: E402
import sign as macsign  # noqa: E402

from tblib import yaml  # noqa: E402  (TB's own stdlib-only YAML reader)

VERSION = build.VERSION
WORKFLOW = ROOT.parent / ".github" / "workflows" / "tb-release.yml"


# ---------------------------------------------------------------- helpers

def make_deb(path: Path, control: str) -> None:
    """A minimal .deb: an ar archive with debian-binary and control.tar.xz."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        data = control.encode()
        info = tarfile.TarInfo("./control")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    members = [("debian-binary", b"2.0\n"), ("control.tar.xz", buf.getvalue()), ("data.tar.xz", b"x")]
    out = bytearray(b"!<arch>\n")
    for name, body in members:
        out += f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}`\n".encode()
        out += body + (b"\n" if len(body) % 2 else b"")
    path.write_bytes(bytes(out))


def fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in list(rm.BREW_ASSETS.values()) + [rm.WINDOWS_SETUP]:
        (dist / name).write_bytes(name.encode())
    for arch in ("x64", "arm64"):
        make_deb(dist / linux_packages.deb_name(VERSION, arch), linux_packages.deb_control(VERSION, arch, 1234))
    return dist


# ---------------------------------------------------------------- Homebrew

def test_homebrew_formula_has_every_platform_with_versioned_urls():
    sums = {name: f"{i:064x}" for i, name in enumerate(rm.BREW_ASSETS.values())}
    text = rm.homebrew_formula("9.8.7", sums)
    assert text.startswith("# Generated") and "class Tb < Formula" in text
    for (os_name, arch), name in rm.BREW_ASSETS.items():
        block = re.search(rf"on_{os_name} do\n(.*?)\n  end", text, re.S).group(1)
        inner = re.search(rf"on_{arch} do\n(.*?)\n    end", block, re.S).group(1)
        assert f'url "https://code.tanisha.ceo/dl/9.8.7/{name}"' in inner
        assert f'sha256 "{sums[name]}"' in inner
    assert 'bin.install_symlink libexec/"tb"' in text
    assert 'assert_equal "TB #{version}"' in text
    assert "github.com" not in text
    # Balanced Ruby blocks.
    assert len(re.findall(r"\bdo\b|^class |\bdef ", text, re.M)) == len(re.findall(r"^\s*end$", text, re.M))


# ---------------------------------------------------------------- winget

def test_winget_manifests_are_valid_yaml_and_consistent():
    files = rm.winget_manifests("9.8.7", "ab" * 32)
    assert set(files) == {"TBLang.TB.yaml", "TBLang.TB.installer.yaml", "TBLang.TB.locale.en-US.yaml"}
    docs = {name: yaml.parse_text(text) for name, text in files.items()}
    types = {d["ManifestType"] for d in docs.values()}
    assert types == {"version", "installer", "defaultLocale"}
    for name, doc in docs.items():
        assert doc["PackageIdentifier"] == "TBLang.TB" and doc["PackageVersion"] == "9.8.7"
        assert doc["ManifestVersion"] == rm.WINGET_SCHEMA
        assert files[name].startswith("# yaml-language-server: $schema=https://aka.ms/winget-manifest.")
    inst = docs["TBLang.TB.installer.yaml"]
    assert inst["InstallerType"] == "inno" and inst["Scope"] == "user"
    assert inst["Installers"] == [{
        "Architecture": "x64",
        "InstallerUrl": "https://code.tanisha.ceo/dl/9.8.7/tb-windows-x64-setup.exe",
        "InstallerSha256": "AB" * 32,
    }]
    # The ProductCode is Inno Setup's uninstall key: the AppId GUID + _is1.
    iss = (ROOT / "packaging" / "windows" / "tb.iss").read_text(encoding="utf-8")
    assert inst["ProductCode"] == "{5F5E3C55-FA16-4258-A72E-3F5BE66D4DF4}_is1"
    assert "AppId={{5F5E3C55-FA16-4258-A72E-3F5BE66D4DF4}" in iss
    assert docs["TBLang.TB.locale.en-US.yaml"]["License"] == "MIT"


# ---------------------------------------------------------------- apt

def test_deb_control_round_trip(tmp_path):
    deb = tmp_path / "x.deb"
    make_deb(deb, "Package: tb\nArchitecture: arm64\n")
    assert rm.deb_control(deb) == "Package: tb\nArchitecture: arm64\n"
    (tmp_path / "bad.deb").write_bytes(b"not an ar archive")
    with pytest.raises(SystemExit):
        rm.deb_control(tmp_path / "bad.deb")


def test_apt_index_lists_every_deb_with_hashes(tmp_path):
    dist = fake_dist(tmp_path)
    written = rm.write_apt(dist, VERSION)
    assert [p.name for p in written] == ["Packages", "Packages.gz", "Release"]
    packages = (dist / "Packages").read_text(encoding="utf-8")
    stanzas = packages.strip().split("\n\n")
    assert len(stanzas) == 2
    for stanza, arch in zip(stanzas, ("amd64", "arm64")):
        deb = dist / f"tb_{VERSION}_{arch}.deb"
        assert f"Architecture: {arch}\n" in stanza
        assert f"Filename: apt/{deb.name}\n" in stanza
        assert f"Size: {deb.stat().st_size}\n" in stanza
        assert f"SHA256: {build.sha256(deb)}" in stanza
    assert gzip.decompress((dist / "Packages.gz").read_bytes()).decode() == packages
    release = (dist / "Release").read_text(encoding="utf-8")
    assert "Architectures: amd64 arm64\n" in release
    assert f" {build.sha256(dist / 'Packages')} {len(packages.encode())} Packages\n" in release
    assert re.search(r"^ [0-9a-f]{64} \d+ Packages\.gz$", release, re.M)


def test_release_manifests_main_writes_everything(tmp_path, monkeypatch, capsys):
    dist = fake_dist(tmp_path)
    monkeypatch.setattr(sys, "argv", ["release_manifests.py", "--dist", str(dist)])
    assert rm.main() == 0
    formula = (dist / "manifests" / "homebrew" / "tb.rb").read_text(encoding="utf-8")
    assert build.sha256(dist / "tb-macos-x64.zip") in formula
    winget = dist / "manifests" / "winget" / "manifests" / "t" / "TBLang" / "TB" / VERSION
    assert len(list(winget.iterdir())) == 3
    assert build.sha256(dist / rm.WINDOWS_SETUP).upper() in (winget / "TBLang.TB.installer.yaml").read_text()
    assert (dist / "InRelease").exists() is False and (dist / "Release").exists()
    # A missing asset is an error, not a formula with a wrong hash.
    (dist / "tb-linux-arm64.tar.gz").unlink()
    monkeypatch.setattr(sys, "argv", ["release_manifests.py", "--dist", str(dist), "--only", "homebrew"])
    with pytest.raises(SystemExit, match="tb-linux-arm64.tar.gz"):
        rm.main()


# ---------------------------------------------------------------- .deb / .rpm

def test_deb_control_fields():
    text = linux_packages.deb_control("9.8.7", "arm64", 4321)
    fields = dict(line.split(": ", 1) for line in text.splitlines() if not line.startswith(" "))
    assert fields["Package"] == "tb" and fields["Version"] == "9.8.7"
    assert fields["Architecture"] == "arm64" and fields["Installed-Size"] == "4321"
    assert fields["Depends"] == "libc6 (>= 2.35)"
    assert re.fullmatch(r".+ <[^@\s]+@[^@\s]+>", fields["Maintainer"])
    continuation = [line for line in text.splitlines() if line.startswith(" ")]
    assert continuation and text.endswith("\n")
    assert linux_packages.deb_name("9.8.7", "x64") == "tb_9.8.7_amd64.deb"
    assert linux_packages.rpm_name("9.8.7", "arm64") == "tb-9.8.7-1.aarch64.rpm"


def test_rpm_spec_never_strips_or_scans_the_frozen_app(tmp_path):
    spec = linux_packages.rpm_spec("9.8.7", "x64", tmp_path / "stage")
    assert "Version: 9.8.7\n" in spec and "BuildArch: x86_64\n" in spec
    # Stripping would cut PyInstaller's appended archive; the dependency scan
    # would require every bundled library.
    for line in ("AutoReqProv: no", "%global __os_install_post %{nil}", "%global debug_package %{nil}"):
        assert line in spec
    assert spec.rstrip().endswith("%files\n/usr/lib/tb\n/usr/bin/tb")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_stage_tree_links_usr_bin_tb(tmp_path):
    folder = tmp_path / "tb"
    (folder / "_internal").mkdir(parents=True)
    (folder / "tb").write_bytes(b"\x7fELF" + b"\0" * 2044)
    (folder / "_internal" / "x.so").write_bytes(b"y" * 10)
    kb = linux_packages.stage_tree(folder, tmp_path / "root")
    link = tmp_path / "root" / "usr" / "bin" / "tb"
    assert os.readlink(link) == "../lib/tb/tb"
    assert link.resolve() == (tmp_path / "root" / "usr" / "lib" / "tb" / "tb").resolve()
    assert kb == 3  # 2058 bytes, rounded up


# ---------------------------------------------------------------- macOS signing

def test_macho_detection(tmp_path):
    cases = {
        "thin64": b"\xcf\xfa\xed\xfe" + b"\0" * 8,
        "fat": b"\xca\xfe\xba\xbe\x00\x00\x00\x02" + b"\0" * 8,
        "javaclass": b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\0" * 8,
        "text": b"#!/bin/sh\n",
    }
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
    assert {n for n in cases if macsign.is_macho(tmp_path / n)} == {"thin64", "fat"}
    assert not macsign.is_macho(tmp_path)


def test_signing_order_and_arguments(tmp_path):
    macho = b"\xcf\xfa\xed\xfe" + b"\0" * 8
    for rel in ("tb", "tblaunch", "_internal/Python", "_internal/lib-dynload/_ssl.so", "welcome.tb"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"say(1)\n" if rel.endswith(".tb") else macho)
    order = [p.relative_to(tmp_path).as_posix() for p in macsign.signing_order(tmp_path)]
    assert order == ["_internal/lib-dynload/_ssl.so", "_internal/Python", "tb", "tblaunch"]
    lib = macsign.codesign_args(tmp_path / "_internal/Python", "Developer ID Application: X (T)", False)
    assert lib[:5] == ["codesign", "--force", "--options", "runtime", "--sign"] and "--timestamp" in lib
    assert "--entitlements" not in lib
    prog = macsign.codesign_args(tmp_path / "tb", "-", True)
    assert "--timestamp" not in prog  # ad-hoc signatures cannot be timestamped
    assert prog[prog.index("--entitlements") + 1] == str(macsign.ENTITLEMENTS)


def test_entitlements_are_the_hardened_runtime_exceptions():
    with open(macsign.ENTITLEMENTS, "rb") as f:
        ents = plistlib.load(f)
    assert ents == {
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
    }


# ---------------------------------------------------------------- build.py

def test_universal_target_and_asset_names(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(build.platform, "machine", lambda: "arm64")  # "" on Windows once sys.platform is faked
    monkeypatch.setenv("TB_TARGET_ARCH", "universal2")
    assert build.detect_target() == ("macos", "universal")
    assert build.asset_name("macos", "universal") == "tb-macos-universal.zip"
    assert build.asset_name("linux", "arm64") == "tb-linux-arm64.tar.gz"
    for name in ("tb-macos-x64.zip", "tb-macos-universal.zip", "tb-linux-arm64.tar.gz",
                 f"tb_{VERSION}_amd64.deb", f"tb_{VERSION}_arm64.deb",
                 f"tb-{VERSION}-1.x86_64.rpm", f"tb-{VERSION}-1.aarch64.rpm"):
        assert name in build.RELEASE_ASSETS


# ---------------------------------------------------------------- the workflow

@pytest.fixture(scope="module")
def workflow():
    return yaml.parse_text(WORKFLOW.read_text(encoding="utf-8"))


def steps_of(job):
    return {s.get("name", s.get("uses", "")): s for s in job["steps"]}


def test_workflow_matrix_targets(workflow):
    include = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    targets = {m["name"]: m for m in include}
    assert set(targets) == {"windows-x64", "macos-arm64", "macos-x64", "macos-universal", "linux-x64", "linux-arm64"}
    assert targets["macos-x64"]["os"] == "macos-15-intel"
    assert targets["linux-arm64"]["os"].startswith("ubuntu-") and targets["linux-arm64"]["os"].endswith("-arm")
    assert targets["macos-universal"]["target_arch"] == "universal2"
    assert targets["linux-arm64"]["xvfb"] and targets["linux-x64"]["xvfb"]
    job = workflow["jobs"]["build"]
    assert job["env"]["TB_TARGET_ARCH"] == "${{ matrix.target_arch }}"
    uploaded = job["steps"][-1]["with"]["path"]
    for pattern in ("code/dist/tb_*.deb", "code/dist/tb-*.rpm", "code/dist/tb-*.zip", "code/dist/tb-*.tar.gz"):
        assert pattern in uploaded


def test_workflow_builds_then_signs_then_packages(workflow):
    steps = workflow["jobs"]["build"]["steps"]
    names = [s.get("name", s.get("uses")) for s in steps]

    def at(prefix):
        return next(i for i, n in enumerate(names) if n.startswith(prefix))

    assert at("Build dist/tb/") < at("Sign the programs (Windows, Azure") < at("Test and package")
    assert at("Sign the programs (macOS") < at("Test and package") < at("Notarise (macOS)")
    assert at("Test and package") < at("Sign the installer (Windows, Azure") < at("Test the installer (Windows)")
    assert "--build-only" in steps[at("Build dist/tb/")]["run"]
    assert "--skip-build" in steps[at("Test and package")]["run"]
    # Every signing step is gated on the secrets check, which logs a skip.
    for step in steps:
        name = step.get("name", "")
        if name.startswith(("Sign the", "Notarise", "Verify the signatures")):
            assert "steps.signing.outputs." in step["if"], name
    detect = steps[at("Which signing secrets exist")]["run"]
    assert detect.count("::notice::") >= 3
    linux = steps[at("Build and test the .deb and .rpm")]
    assert linux["if"] == "runner.os == 'Linux'" and "test_linux_packages.sh" in linux["run"]


def test_workflow_secrets_stay_in_steps(workflow):
    """Secrets are only read by the steps that need them, never job-wide."""
    for job_name, job in workflow["jobs"].items():
        assert "secrets." not in json.dumps(job.get("env", {})), job_name
    text = WORKFLOW.read_text(encoding="utf-8")
    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    documented = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for secret in used:
        assert f"`{secret}`" in documented, f"{secret} is not described in CONTRIBUTING.md"


def test_workflow_dispatch_is_a_dry_run(workflow):
    on = workflow["on"]
    assert "workflow_dispatch" in on and on["push"] == {"tags": ["tb-v*"]}
    release = workflow["jobs"]["release"]
    assert "if" not in release, "the release job also runs (as a dry run) from workflow_dispatch"
    steps = steps_of(release)
    publish = steps["Create or update the release"]
    assert "github.event_name == 'push'" in publish["if"]
    assert steps["Keep the dry run's files"]["if"] == "github.event_name != 'push'"
    for step in release["steps"]:
        if "gh release" in step.get("run", ""):
            assert step is publish
    run = publish["run"]
    for name in build.RELEASE_ASSETS:
        if "windows-arm64" in name:
            continue  # listed for SHA256SUMS only; no runner builds it
        forms = (name.replace(VERSION, "${version}"), name.replace(VERSION, "*"))
        assert any(f"dist/{form}" in run for form in forms), name
    assert "release_manifests.py" in steps["Package-manager manifests and apt index"]["run"]


def test_vercel_serves_versioned_downloads_and_apt():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    rewrites = {r["source"]: r["destination"] for r in config["rewrites"]}
    base = "https://github.com/Junecoderr/portfolio-website/releases/download/"
    assert rewrites["/dl/:version/:file"] == base + "tb-v:version/:file"
    assert rewrites["/dl/:file"] == rewrites["/apt/:file"] == base + f"tb-v{VERSION}/:file"
