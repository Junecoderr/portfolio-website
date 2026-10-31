"""The TB Package Index from the command line (tbtools/pkg.py): tb add with
version ranges and checksums, tb search / info, tb login / logout, tb publish
and tb yank, against a fake registry served on 127.0.0.1. No internet is used.
The real server is tested in tests/api/registry.test.mjs."""

from __future__ import annotations

import base64
import hashlib
import http.server
import random
import shutil
import subprocess
import io
import json
import os
import re
import stat
import sys
import threading
import urllib.parse
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tb  # noqa: E402
from tbtools import ed25519, pkg  # noqa: E402

TOKEN = "tbr_" + "A" * 43
OTHER_TOKEN = "tbr_" + "B" * 43


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_package(folder: Path, name: str, version: str, body: str | None = None, deps: dict | None = None) -> Path:
    needs = "".join(f'{d} = "{r}"\n' for d, r in (deps or {}).items())
    write(folder / "tb.toml", f'[package]\nname = "{name}"\nversion = "{version}"\ndescription = "Says hello nicely."\nmain = "lib.tb"\nkeywords = ["greeting"]\n\n[dependencies]\n{needs}')
    write(folder / "lib.tb", body or f'export fn hello(n) {{ return "Hello " + n }}\nexport let VERSION = "{version}"\n')
    write(folder / "README.md", f"# {name}\n\nVersion {version}.\n")
    write(folder / "lib_test.tb", 'import "lib.tb" as g\ntest.check(g.hello("x") == "Hello x")\n')
    return folder


def archive_of(folder: Path, name: str) -> bytes:
    checked = pkg.check_package(str(folder))
    return pkg.build_archive(str(folder), name, checked["files"])


def ed25519_sign(seed: bytes, message: bytes) -> tuple[bytes, bytes]:
    """(public key, signature) per RFC 8032 5.1.6. Tests only: not constant-time."""
    def encode(point):
        zi = pow(point[2], ed25519.P - 2, ed25519.P)
        x, y = point[0] * zi % ed25519.P, point[1] * zi % ed25519.P
        return (y | (x & 1) << 255).to_bytes(32, "little")

    def h(data):
        return int.from_bytes(hashlib.sha512(data).digest(), "little") % ed25519.L

    digest = hashlib.sha512(seed).digest()
    a = int.from_bytes(digest[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    public = encode(ed25519._mul(a, ed25519.G))
    r = h(digest[32:] + message)
    big_r = encode(ed25519._mul(r, ed25519.G))
    s = (r + h(big_r + public + message) * a) % ed25519.L
    return public, big_r + s.to_bytes(32, "little")


class FakeRegistry:
    """Just enough of /api/registry for the CLI."""

    def __init__(self):
        self.packages: dict[str, dict] = {}
        self.tokens = {TOKEN: "bob", OTHER_TOKEN: "carol"}
        self.uploads: list[tuple[str, str, bytes]] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.base = ""
        self.old_index = False  # an index from before TB 3.3: no dependencies per version
        self.shuffle = None  # a random.Random: list packages and versions in a random order
        self.signing_seed = None  # an Ed25519 seed: sign the index

    def add(self, name, version, data, status="approved", owner="bob"):
        p = self.packages.setdefault(name, {"owner": owner, "versions": {}, "description": "Says hello nicely.", "keywords": ["greeting"]})
        p["versions"][version] = {"zip": data, "sha256": hashlib.sha256(data).hexdigest(), "status": status, "downloads": 0,
                                  "dependencies": pkg._zip_manifest(data)}

    def listed(self):
        out = {}
        for name, p in sorted(self.packages.items()):
            visible = {v: i for v, i in p["versions"].items() if i["status"] in ("approved", "yanked")}
            if not visible:
                continue
            approved = [v for v, i in visible.items() if i["status"] == "approved" and not pkg.parse_version(v)[3]]
            latest = max(approved, key=pkg.version_key) if approved else None
            out[name] = {
                "description": p["description"], "keywords": p["keywords"], "version": latest, "owner": p["owner"],
                "downloads": sum(i["downloads"] for i in p["versions"].values()),
                "versions": {v: ({"sha256": i["sha256"], "yanked": True} if i["status"] == "yanked" else {"sha256": i["sha256"]})
                             for v, i in visible.items()},
            }
            if not self.old_index:
                for v, info in out[name]["versions"].items():
                    info["dependencies"] = visible[v]["dependencies"]
        if self.shuffle:
            for entry in out.values():
                items = list(entry["versions"].items())
                self.shuffle.shuffle(items)
                entry["versions"] = dict(items)
            items = list(out.items())
            self.shuffle.shuffle(items)
            out = dict(items)
        return out

    def handle(self, method, path, headers, body):
        self.requests.append((method, path, dict(headers)))
        url = urllib.parse.urlparse(path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p][2:]  # after api/registry
        auth = headers.get("Authorization", "")
        user = self.tokens.get(auth[7:]) if auth.startswith("Bearer ") else None
        if method == "GET" and parts == ["index"]:
            return 200, {"format": "tb-registry-1", "registry": self.base, "packages": self.listed()}
        if method == "GET" and parts == ["search"]:
            q = urllib.parse.parse_qs(url.query).get("q", [""])[0].lower()
            return 200, {"results": [dict(name=n, **e) for n, e in self.listed().items() if q in n or q in e["description"].lower()]}
        if method == "GET" and parts == ["me"]:
            return 200, ({"signedIn": True, "user": {"id": "2", "login": user}} if user else {"signedIn": False})
        if len(parts) >= 2 and parts[0] == "packages":
            name = parts[1]
            p = self.packages.get(name)
            if method == "GET" and len(parts) == 2:
                if not p or name not in self.listed():
                    return 404, {"error": f"There's no package called '{name}'."}
                versions = sorted(p["versions"], key=pkg.version_key, reverse=True)
                return 200, {"name": name, "description": p["description"], "owner": p["owner"], "latest": self.listed()[name]["version"],
                             "status": "approved", "license": "MIT", "keywords": p["keywords"], "downloads": 0,
                             "versions": [{"version": v, "status": p["versions"][v]["status"], "sha256": p["versions"][v]["sha256"],
                                           "downloads": p["versions"][v]["downloads"]} for v in versions]}
            if method == "GET" and len(parts) == 4 and parts[3] == "download":
                info = p and p["versions"].get(parts[2])
                if not info:
                    return 404, {"error": "no such version"}
                info["downloads"] += 1
                return 200, info["zip"]
            if method == "POST" and len(parts) == 3:
                if not user:
                    return 401, {"error": "That API token isn't valid."}
                if p and p["owner"] != user:
                    return 403, {"error": f"'{name}' belongs to another account. Pick another name."}
                if p and parts[2] in p["versions"]:
                    return 409, {"error": f"{name} {parts[2]} already exists. Versions can't be replaced; bump the version in tb.toml."}
                assert headers.get("Content-Type") == "application/zip"
                status = "approved" if p else "pending"
                self.add(name, parts[2], body, status=status, owner=user)
                self.uploads.append((name, parts[2], body))
                return 201, {"name": name, "version": parts[2], "status": status, "sha256": hashlib.sha256(body).hexdigest(),
                             "flags": ["uses the tb- prefix of the bundled packages"] if name.startswith("tb-") else []}
            if method == "POST" and len(parts) == 4 and parts[3] in ("yank", "unyank"):
                info = p and p["versions"].get(parts[2])
                if not user or not info or p["owner"] != user:
                    return 404, {"error": "no version you can change"}
                info["status"] = "yanked" if parts[3] == "yank" else "approved"
                info["reason"] = json.loads(body or b"{}").get("reason", "")
                return 200, {"message": f"{'Yanked' if parts[3] == 'yank' else 'Restored'} {name} {parts[2]}."}
        return 404, {"error": "no such route"}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    fake = FakeRegistry()
    src = tmp_path / "src"
    for version in ("1.0.0", "1.1.0", "1.2.0", "2.0.0", "2.1.0-beta.1"):
        folder = make_package(src / version, "greet", version)
        fake.add("greet", version, archive_of(folder, "greet"))
    fake.packages["greet"]["versions"]["1.2.0"]["status"] = "yanked"
    folder = make_package(src / "hidden", "secret-pkg", "1.0.0")
    fake.add("secret-pkg", "1.0.0", archive_of(folder, "secret-pkg"), status="pending")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = fake.handle(self.command, self.path, self.headers, body)
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/zip" if isinstance(payload, bytes) else "application/json")
            self.send_header("Content-Length", str(len(data)))
            if fake.signing_seed and self.path.endswith("/index"):
                signature = ed25519_sign(fake.signing_seed, pkg.SIGNATURE_CONTEXT + data)[1]
                self.send_header("X-TB-Signature", "ed25519 " + base64.b64encode(signature).decode())
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _serve

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.base = f"http://127.0.0.1:{server.server_address[1]}/api/registry"
    monkeypatch.setenv("TB_PACKAGE_INDEX", fake.base + "/index")
    monkeypatch.delenv("TB_REGISTRY", raising=False)
    monkeypatch.delenv("TB_TOKEN", raising=False)
    monkeypatch.setenv("TB_CREDENTIALS", str(tmp_path / "config" / "tb" / "credentials.toml"))
    monkeypatch.setenv("TB_NO_BROWSER", "1")
    yield fake
    server.shutdown()
    server.server_close()


@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    monkeypatch.chdir(folder)
    assert pkg.main(["init", "demo"]) == 0
    return folder


def installed_version(project: Path) -> str:
    text = (project / "tb_packages" / "greet" / "lib.tb").read_text(encoding="utf-8")
    return re.search(r'VERSION = "([^"]+)"', text).group(1)


# --- ranges -----------------------------------------------------------------------

@pytest.mark.parametrize("version,spec,ok", [
    ("1.2.5", "^1.2", True), ("1.9.0", "^1.2.3", True), ("2.0.0", "^1.2", False), ("1.2.2", "^1.2.3", False),
    ("0.2.9", "^0.2.3", True), ("0.3.0", "^0.2.3", False), ("0.0.3", "^0.0.3", True), ("0.0.4", "^0.0.3", False),
    ("1.2.9", "~1.2.3", True), ("1.3.0", "~1.2.3", False), ("1.9.0", "~1", True),
    ("1.5.0", ">=1.0 <2", True), ("2.0.0", ">=1.0 <2", False), ("0.9.0", ">=1.0 <2", False),
    ("1.5.0", ">= 1.0 < 2", True), ("1.2.3", "1.2.3", True), ("1.2.4", "1.2.3", False), ("1.2.4", "=1.2.4", True),
    ("1.2.7", "1.2", True), ("1.3.0", "1.2", False), ("1.4.0", "1", True), ("3.0.0", "*", True),
    ("2.0.0-beta.1", "^1.0", False), ("2.0.0-beta.1", "*", False), ("2.0.0-beta.2", ">=2.0.0-beta.1", True),
    ("2.0.0", ">=2.0.0-beta.1", True), ("1.5.0", "<1.0 || >=1.4", True), ("1.2.0", "<1.0 || >=1.4", False),
    ("1.3.5", ">1.2", True), ("1.2.9", ">1.2", False), ("1.2.9", "<=1.2", True), ("1.3.0", "<=1.2", False),
])
def test_ranges(version, spec, ok):
    assert pkg.satisfies(version, spec) is ok


def test_version_order_and_bad_ranges():
    order = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta.2", "1.0.0-beta.10", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.10.0"]
    assert sorted(reversed(order), key=pkg.version_key) == order
    with pytest.raises(pkg.PkgError, match="isn't a version range"):
        pkg.parse_range("about one")
    versions = {"1.0.0": {}, "1.1.0": {"yanked": True}}
    assert pkg.pick_version("x", versions, "^1.0") == "1.0.0"
    assert pkg.pick_version("x", versions, "1.1.0") == "1.1.0"  # pinned exactly: yanked is fine
    assert pkg.pick_version("x", versions, "^1.0", prefer="1.1.0") == "1.1.0"  # what tb.lock says
    with pytest.raises(pkg.PkgError, match="No version of 'x' matches '\\^2'"):
        pkg.pick_version("x", versions, "^2")


# --- tb add from the registry ---------------------------------------------------------

def test_add_picks_newest_and_verifies(project, registry, capsys):
    assert pkg.main(["add", "greet"]) == 0
    out = capsys.readouterr().out
    assert "+ greet 2.0.0 (TB Package Index)" in out
    assert installed_version(project) == "2.0.0"
    assert pkg.read_manifest(str(project))["dependencies"] == {"greet": "^2.0.0"}
    lock = pkg.read_lock(str(project))["greet"]
    assert lock["source"] == "registry" and lock["version"] == "2.0.0" and lock["registry"] == registry.base
    assert lock["sha256"] == registry.packages["greet"]["versions"]["2.0.0"]["sha256"]
    assert lock["url"] == registry.base + "/packages/greet/2.0.0/download"
    source = 'import "tb_packages/greet/lib.tb" as greet\nsay(greet.hello("Tanisha"))\n'
    lines = []
    tb.run(source, interpreter=tb.Interpreter(output=lines.append), path=str(project / "main.tb"))
    assert lines == ["Hello Tanisha"]
    assert pkg.main(["packages"]) == 0
    assert "index" in capsys.readouterr().out


@pytest.mark.parametrize("spec,expected", [
    ("greet@^1.0", "1.1.0"),  # 1.2.0 is yanked
    ("greet@~1.0.0", "1.0.0"),
    ("greet@>=1.0 <2", "1.1.0"),
    ("greet@1.2.0", "1.2.0"),  # an exact pin still gets a yanked version
    ("greet@2.1.0-beta.1", "2.1.0-beta.1"),
])
def test_add_with_ranges(project, registry, spec, expected, capsys):
    assert pkg.main(["add", spec]) == 0
    assert installed_version(project) == expected
    assert pkg.read_manifest(str(project))["dependencies"]["greet"] == spec.split("@", 1)[1]
    if expected == "1.2.0":
        assert "yanked" in capsys.readouterr().out


def test_add_unknown_range_and_hidden_package(project, registry, capsys):
    assert pkg.main(["add", "greet@^3"]) == 1
    err = capsys.readouterr().err
    assert "No version of 'greet' matches '^3'" in err and "2.0.0" in err
    assert pkg.main(["add", "secret-pkg"]) == 1  # pending review: not in the index
    assert "couldn't find a package called 'secret-pkg'" in capsys.readouterr().err
    assert not (project / "tb_packages" / "greet").exists()


def test_add_refuses_a_checksum_mismatch(project, registry, capsys):
    registry.packages["greet"]["versions"]["2.0.0"]["zip"] = archive_of(make_package(project.parent / "evil", "greet", "2.0.0", "say(1)\n"), "greet")
    assert pkg.main(["add", "greet"]) == 1
    err = capsys.readouterr().err
    assert "doesn't match the index's checksum" in err
    assert not (project / "tb_packages" / "greet").exists()
    assert "greet" not in pkg.read_manifest(str(project))["dependencies"]


def test_lock_is_reproduced_and_update_moves_on(project, registry, capsys):
    assert pkg.main(["add", "greet@^1.0"]) == 0
    assert installed_version(project) == "1.1.0"
    # A newer 1.x appears; `tb add` with no arguments keeps what tb.lock says.
    registry.add("greet", "1.3.0", archive_of(make_package(project.parent / "v13", "greet", "1.3.0"), "greet"))
    (project / "tb_packages" / "greet" / "lib.tb").unlink()
    assert pkg.main(["add"]) == 0
    assert installed_version(project) == "1.1.0"
    assert pkg.main(["update", "greet"]) == 0
    assert installed_version(project) == "1.3.0"
    assert pkg.read_lock(str(project))["greet"]["version"] == "1.3.0"
    # A locked version that was yanked later still installs from the lock.
    registry.packages["greet"]["versions"]["1.3.0"]["status"] = "yanked"
    assert pkg.main(["add"]) == 0
    assert installed_version(project) == "1.3.0"


def test_old_style_dependencies_still_work(project, registry):
    """A tb.toml written by TB 3.0.0 (`greet = "*"`) resolves against the new index."""
    write(project / "tb.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\ngreet = "*"\n')
    assert pkg.main(["add"]) == 0
    assert installed_version(project) == "2.0.0"


# --- search and info -----------------------------------------------------------------

def test_search_and_info(registry, capsys):
    assert pkg.main(["search", "hello"]) == 0
    out = capsys.readouterr().out
    assert "greet" in out and "2.0.0" in out and "Says hello nicely." in out
    assert pkg.main(["search", "zebra"]) == 0
    assert "No packages match 'zebra'" in capsys.readouterr().out
    assert pkg.main(["search"]) == 1
    assert pkg.main(["info", "greet"]) == 0
    out = capsys.readouterr().out
    assert "greet 2.0.0" in out and "owner: bob" in out and "yanked" in out
    assert registry.packages["greet"]["versions"]["2.0.0"]["sha256"][:16] in out
    assert pkg.main(["info", "nope-nope"]) == 1
    assert "There's no package called 'nope-nope'" in capsys.readouterr().err


def test_search_against_a_static_index(tmp_path, monkeypatch, capsys):
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"tb-colors": {"description": "ANSI colours", "zip": "https://h/x.zip", "version": "1.0.0"}}))
    monkeypatch.setenv("TB_PACKAGE_INDEX", index.as_uri())
    assert pkg.main(["search", "ansi"]) == 0
    assert "tb-colors" in capsys.readouterr().out
    assert pkg.main(["info", "tb-colors"]) == 0
    assert "https://h/x.zip" in capsys.readouterr().out


# --- login / logout -------------------------------------------------------------------

def test_login_stores_a_private_token(registry, monkeypatch, capsys):
    assert pkg.main(["login", "--token", TOKEN]) == 0
    out = capsys.readouterr().out
    assert "as bob" in out
    path = Path(os.environ["TB_CREDENTIALS"])
    data = pkg.parse_toml(path.read_text(encoding="utf-8"))
    assert data[registry.base] == {"token": TOKEN, "user": "bob"}
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert pkg.stored_token(registry.base) == TOKEN
    # The token went to the registry only, as a bearer token.
    assert registry.requests[-1][0:2] == ("GET", "/api/registry/me")
    assert registry.requests[-1][2]["Authorization"] == f"Bearer {TOKEN}"
    # Pasted on standard input works too.
    monkeypatch.setattr(sys, "stdin", io.StringIO(OTHER_TOKEN + "\n"))
    assert pkg.main(["login"]) == 0
    out = capsys.readouterr().out
    assert "/packages/publish#tokens" in out and "as carol" in out
    assert pkg.main(["logout"]) == 0
    assert "Forgot the token" in capsys.readouterr().out
    assert pkg.stored_token(registry.base) is None
    monkeypatch.setenv("TB_TOKEN", TOKEN)
    assert pkg.stored_token(registry.base) == TOKEN


def test_login_refuses_bad_tokens(registry, capsys):
    assert pkg.main(["login", "--token", "hunter2"]) == 1
    assert "start with tbr_" in capsys.readouterr().err
    assert pkg.main(["login", "--token", "tbr_" + "Z" * 43]) == 1
    assert "doesn't accept that token" in capsys.readouterr().err
    assert not Path(os.environ["TB_CREDENTIALS"]).exists()


def test_credentials_path_per_platform(monkeypatch):
    monkeypatch.delenv("TB_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/cfg")
    if os.name != "nt":
        assert pkg.credentials_path() == os.path.join("/cfg", "tb", "credentials.toml")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("APPDATA", "C:\\Users\\me\\AppData\\Roaming")
    assert pkg.credentials_path().endswith(os.path.join("TB", "credentials.toml"))
    assert pkg.credentials_path().startswith("C:\\Users\\me\\AppData\\Roaming")


# --- publish -----------------------------------------------------------------------------

@pytest.fixture
def author(tmp_path, monkeypatch):
    folder = make_package(tmp_path / "hello-world", "hello-world", "1.0.0")
    write(folder / ".git" / "config", "[core]\n")  # hidden: left out
    write(folder / "tb_packages" / "dep" / "lib.tb", "x\n")  # dependencies: left out
    write(folder / "tb.lock", "# lock\n")
    monkeypatch.chdir(folder)
    return folder


def test_publish_needs_a_token(registry, author, capsys):
    assert pkg.main(["publish"]) == 1
    err = capsys.readouterr().err
    assert "not signed in" in err and "tb login" in err and "/packages/publish" in err
    assert registry.uploads == []


def test_publish_dry_run_and_reproducible_archive(registry, author, tmp_path, capsys):
    out_zip = tmp_path / "out.zip"
    assert pkg.main(["publish", "--dry-run", "--out", str(out_zip)]) == 0
    out = capsys.readouterr().out
    assert "Checked hello-world 1.0.0: 4 files" in out and "Dry run" in out
    data = out_zip.read_bytes()
    assert hashlib.sha256(data).hexdigest() in out
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["hello-world/README.md", "hello-world/lib.tb", "hello-world/lib_test.tb", "hello-world/tb.toml"]
        assert {i.date_time for i in zf.infolist()} == {pkg.ZIP_DATE}
    os.utime(author / "lib.tb", (1, 1))  # timestamps don't matter
    assert pkg.main(["publish", "--dry-run", "--out", str(out_zip)]) == 0
    assert out_zip.read_bytes() == data
    assert registry.uploads == []


def test_publish_new_then_trusted(registry, author, monkeypatch, capsys):
    monkeypatch.setenv("TB_TOKEN", TOKEN)
    assert pkg.main(["publish"]) == 0
    out = capsys.readouterr().out
    assert "Submitted hello-world 1.0.0 for review" in out and "/packages/account" in out
    name, version, body = registry.uploads[0]
    assert (name, version) == ("hello-world", "1.0.0")
    assert hashlib.sha256(body).hexdigest() in out
    # Same version again: the server's message comes through.
    assert pkg.main(["publish"]) == 1
    assert "already exists" in capsys.readouterr().err
    # Once approved, new versions are published at once.
    registry.packages["hello-world"]["versions"]["1.0.0"]["status"] = "approved"
    write(author / "tb.toml", (author / "tb.toml").read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))
    assert pkg.main(["publish"]) == 0
    assert "Published hello-world 1.0.1" in capsys.readouterr().out
    # Someone else's package.
    monkeypatch.setenv("TB_TOKEN", OTHER_TOKEN)
    write(author / "tb.toml", (author / "tb.toml").read_text().replace('version = "1.0.1"', 'version = "1.0.2"'))
    assert pkg.main(["publish"]) == 1
    assert "belongs to another account" in capsys.readouterr().err


@pytest.mark.parametrize("change,message", [
    (lambda f: write(f / "tb.toml", '[package]\nname = "Hello"\nversion = "1.0.0"\n'), "lower-case"),
    (lambda f: write(f / "tb.toml", '[package]\nname = "json"\nversion = "1.0.0"\n'), "reserved"),
    (lambda f: write(f / "tb.toml", '[package]\nname = "tb"\nversion = "1.0.0"\n'), "3 to 40"),
    (lambda f: write(f / "tb.toml", '[package]\nname = "hello-world"\nversion = "1.0"\n'), "semantic versioning"),
    (lambda f: write(f / "tb.toml", '[package]\nname = "hello-world"\nversion = "1.0.0"\nkeywords = ["Not OK"]\n'), "keywords"),
    (lambda f: (f / "tool.exe").write_bytes(b"MZ\x00\x01"), "can't go in a package"),
    (lambda f: (f / "data.txt").write_bytes(b"a\x00b"), "binary"),
    (lambda f: (f / "logo.png").write_bytes(b"not a png"), "isn't the image"),
    (lambda f: (f / "big.txt").write_text("a" * (1024 * 1024 + 1)), "bigger than 1 MB"),
    (lambda f: (f / "lib.tb").unlink(), "no lib.tb"),
])
def test_publish_checks_locally(registry, author, monkeypatch, change, message, capsys):
    monkeypatch.setenv("TB_TOKEN", TOKEN)
    change(author)
    assert pkg.main(["publish"]) == 1
    assert message in capsys.readouterr().err
    assert registry.uploads == []


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="symlinks need privileges on Windows")
def test_publish_refuses_symlinks(registry, author, monkeypatch, capsys):
    monkeypatch.setenv("TB_TOKEN", TOKEN)
    os.symlink("/etc/hosts", author / "hosts.txt")
    assert pkg.main(["publish"]) == 1
    assert "symbolic link" in capsys.readouterr().err


def test_reserved_words_match_the_server():
    js = (ROOT / "api" / "_lib" / "registry" / "rules.js").read_text(encoding="utf-8")
    block = re.search(r"export const RESERVED_WORDS = \[(.*?)\];", js, re.S).group(1)
    assert tuple(re.findall(r'"([^"]+)"', block)) == pkg.RESERVED_WORDS
    exts = re.search(r"export const TEXT_EXTENSIONS = \[(.*?)\];", js, re.S).group(1)
    assert tuple(re.findall(r'"([^"]+)"', exts)) == pkg.TEXT_EXTENSIONS
    assert re.search(r"export const NAME_RE = /(.*?)/;", js).group(1) == pkg.REGISTRY_NAME_RE.pattern
    assert set(tb.list_libraries()) <= pkg.reserved_names()


# --- yank ----------------------------------------------------------------------------------

def test_yank_and_undo(registry, monkeypatch, capsys):
    assert pkg.main(["yank", "greet@2.0.0"]) == 1
    assert "not signed in" in capsys.readouterr().err
    monkeypatch.setenv("TB_TOKEN", TOKEN)
    assert pkg.main(["yank", "greet@2.0.0", "--reason", "broken on Windows"]) == 0
    assert "Yanked greet 2.0.0" in capsys.readouterr().out
    info = registry.packages["greet"]["versions"]["2.0.0"]
    assert info["status"] == "yanked" and info["reason"] == "broken on Windows"
    assert pkg.main(["yank", "greet@2.0.0", "--undo"]) == 0
    assert info["status"] == "approved"
    assert pkg.main(["yank", "greet"]) == 1
    assert "Which version" in capsys.readouterr().err
    monkeypatch.setenv("TB_TOKEN", OTHER_TOKEN)
    assert pkg.main(["yank", "greet@2.0.0"]) == 1


def test_registry_base(monkeypatch):
    monkeypatch.delenv("TB_REGISTRY", raising=False)
    monkeypatch.delenv("TB_PACKAGE_INDEX", raising=False)
    assert pkg.index_url() == "https://code.tanisha.ceo/api/registry/index.json"
    assert pkg.registry_base() == "https://code.tanisha.ceo/api/registry"
    assert pkg.site_url() == "https://code.tanisha.ceo"
    monkeypatch.setenv("TB_PACKAGE_INDEX", "https://example.com/mirror/index")
    assert pkg.registry_base() == "https://example.com/mirror"
    monkeypatch.setenv("TB_PACKAGE_INDEX", "https://example.com/static/index.json")
    assert pkg.registry_base() == "https://code.tanisha.ceo/api/registry"
    monkeypatch.setenv("TB_REGISTRY", "https://example.com/api/registry/")
    assert pkg.registry_base() == "https://example.com/api/registry"


# --- dependency trees -------------------------------------------------------------------
# webby -> fetchy -> utilz is three levels; utilz is also reached through logger
# (a diamond); fetchy 1.2.0 is yanked; clash needs a utilz no one else accepts.

TREE = {
    ("webby", "1.0.0"): {"fetchy": "^1.0", "logger": "^1.0"},
    ("webby", "1.1.0"): {"fetchy": "^1.1", "logger": "^1.2"},
    ("fetchy", "1.0.0"): {"utilz": "^1.0"},
    ("fetchy", "1.1.0"): {"utilz": "^1.0"},
    ("fetchy", "1.2.0"): {"utilz": "^1.0"},
    ("logger", "1.0.0"): {"utilz": "^1.0"},
    ("logger", "1.2.0"): {"utilz": ">=1.1 <2"},
    ("utilz", "1.0.0"): {},
    ("utilz", "1.1.0"): {},
    ("utilz", "2.0.0"): {},
    ("clash", "1.0.0"): {"utilz": "^2.0"},
}
RESOLVED = {"fetchy": "1.1.0", "logger": "1.2.0", "utilz": "1.1.0", "webby": "1.1.0"}


def publish_tree(registry, folder: Path) -> None:
    for (name, version), deps in TREE.items():
        registry.add(name, version, archive_of(make_package(folder / f"{name}-{version}", name, version, deps=deps), name))
    registry.packages["fetchy"]["versions"]["1.2.0"]["status"] = "yanked"


def tree_project(folder: Path, deps: list[str]) -> Path:
    write(folder / "tb.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\n'
          + "".join(f'{name} = "^1.0"\n' for name in deps))
    return folder


def snapshot(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_dependency_tree_resolves_deterministically(registry, tmp_path, monkeypatch, capsys):
    publish_tree(registry, tmp_path / "src")
    results = []
    for seed, order in ((1, ["webby", "logger"]), (2, ["logger", "webby"]), (3, ["webby", "logger"])):
        registry.shuffle = random.Random(seed)  # the index lists packages and versions in another order each time
        folder = tree_project(tmp_path / f"p{seed}", order)
        monkeypatch.chdir(folder)
        assert pkg.main(["add"]) == 0, capsys.readouterr().err
        results.append(((folder / "tb.lock").read_bytes(), snapshot(folder / "tb_packages")))
    assert results[0] == results[1] == results[2]
    lock = pkg.parse_toml(results[0][0].decode("utf-8"))
    assert {name: entry["version"] for name, entry in lock.items()} == RESOLVED
    assert lock["webby"]["dependencies"] == {"fetchy": "^1.1", "logger": "^1.2"}
    assert "dependencies" not in lock["utilz"]
    for name, version in RESOLVED.items():
        assert lock[name]["sha256"] == registry.packages[name]["versions"][version]["sha256"]
    # The same again from scratch, and from an index that doesn't list dependencies
    # (TB 3.2 and older: tb reads each package's own tb.toml instead).
    for old in (False, True):
        registry.old_index = old
        shutil.rmtree(folder / "tb_packages")
        (folder / "tb.lock").unlink()
        assert pkg.main(["add"]) == 0
        assert ((folder / "tb.lock").read_bytes(), snapshot(folder / "tb_packages")) == results[0]
    # Deeper packages are importable by name from the project.
    lines = []
    tb.run('import "utilz" as u\nsay(u.VERSION)\n', interpreter=tb.Interpreter(output=lines.append), path=str(folder / "main.tb"))
    assert lines == ["1.1.0"]


def test_dependency_conflict_names_both_sides(registry, tmp_path, monkeypatch, capsys):
    publish_tree(registry, tmp_path / "src")
    folder = tree_project(tmp_path / "proj", ["clash", "logger", "webby"])
    monkeypatch.chdir(folder)
    assert pkg.main(["add"]) == 1
    err = capsys.readouterr().err
    assert "No version of 'utilz' works for everything that needs it" in err
    assert "clash 1.0.0 needs utilz ^2.0" in err and "logger 1.2.0 needs utilz >=1.1 <2" in err
    assert "Traceback" not in err
    assert not (folder / "tb_packages").exists() and not (folder / "tb.lock").exists()


def test_lock_wins_until_update_and_remove_prunes(project, registry, tmp_path, capsys):
    publish_tree(registry, tmp_path / "src")
    assert pkg.main(["add", "webby"]) == 0
    assert pkg.read_manifest(str(project))["dependencies"] == {"webby": "^1.1.0"}
    first = (project / "tb.lock").read_bytes()
    assert {n: e["version"] for n, e in pkg.read_lock(str(project)).items()} == RESOLVED
    # A newer utilz appears, then the locked one is yanked: tb.lock still decides.
    registry.add("utilz", "1.2.0", archive_of(make_package(tmp_path / "u12", "utilz", "1.2.0"), "utilz"))
    assert pkg.main(["add"]) == 0
    assert (project / "tb.lock").read_bytes() == first
    registry.packages["utilz"]["versions"]["1.1.0"]["status"] = "yanked"
    assert pkg.main(["add"]) == 0
    assert (project / "tb.lock").read_bytes() == first
    capsys.readouterr()
    assert pkg.main(["update"]) == 0
    assert "Updating utilz" in capsys.readouterr().out
    assert pkg.read_lock(str(project))["utilz"]["version"] == "1.2.0"
    # Removing webby drops what only it needed.
    assert pkg.main(["remove", "webby"]) == 0
    out = capsys.readouterr().out
    assert "- fetchy (nothing needs it any more)" in out and "- utilz (nothing needs it any more)" in out
    assert pkg.read_lock(str(project)) == {}
    assert not any((project / "tb_packages").iterdir())


def test_reinstall_checks_every_sha256(project, registry, tmp_path, capsys):
    publish_tree(registry, tmp_path / "src")
    assert pkg.main(["add", "webby"]) == 0
    shutil.rmtree(project / "tb_packages")
    other = archive_of(make_package(tmp_path / "evil", "fetchy", "1.1.0", "say(1)\n", deps={"utilz": "^1.0"}), "fetchy")
    info = registry.packages["fetchy"]["versions"]["1.1.0"]
    info["zip"] = other  # the download changes, the index's checksum doesn't
    assert pkg.main(["add"]) == 1
    assert "doesn't match the index's checksum" in capsys.readouterr().err
    info["sha256"] = hashlib.sha256(other).hexdigest()  # now the index agrees, but tb.lock doesn't
    assert pkg.main(["add"]) == 1
    assert "isn't the one in tb.lock" in capsys.readouterr().err
    assert not (project / "tb_packages" / "fetchy").exists()


def test_resolver_cycles_pins_and_order():
    def index_of(tree, yanked=()):
        index = {}
        for (name, version), _deps in tree.items():
            index.setdefault(name, {"versions": {}})["versions"][version] = {"yanked": True} if (name, version) in yanked else {}
        return index

    tree = {("a", "1.0.0"): {"b": "^1"}, ("b", "1.0.0"): {"a": "^1"}, ("b", "1.1.0"): {"a": "^1"}}
    deps = lambda n, v: tree[(n, v)]  # noqa: E731
    assert pkg.resolve_tree({"a": [("tb.toml", "*")]}, index_of(tree), deps) == {"a": "1.0.0", "b": "1.1.0"}
    # tb.lock's version first; a yanked one only when pinned exactly or locked.
    index = index_of(tree, yanked={("b", "1.1.0")})
    assert pkg.resolve_tree({"a": [("tb.toml", "*")]}, index, deps)["b"] == "1.0.0"
    assert pkg.resolve_tree({"a": [("tb.toml", "*")]}, index, deps, locked={"b": "1.1.0"})["b"] == "1.1.0"
    assert pkg.resolve_tree({"a": [("tb.toml", "*")], "b": [("tb.toml", "1.1.0")]}, index, deps)["b"] == "1.1.0"
    # Backtracking: the newest webby needs logger ^1.2, so a tb.toml pin on logger 1.0 picks webby 1.0.
    full = index_of(TREE, yanked={("fetchy", "1.2.0")})
    tree_deps = lambda n, v: TREE[(n, v)]  # noqa: E731
    picked = pkg.resolve_tree({"webby": [("tb.toml", "^1")], "logger": [("tb.toml", "1.0.0")]}, full, tree_deps)
    assert picked == {"fetchy": "1.1.0", "logger": "1.0.0", "utilz": "1.1.0", "webby": "1.0.0"}
    # The answer doesn't depend on the order of anything.
    rng = random.Random(7)
    for _ in range(5):
        shuffled = {}
        for name in rng.sample(sorted(full), len(full)):
            versions = list(full[name]["versions"].items())
            rng.shuffle(versions)
            shuffled[name] = {"versions": dict(versions)}
        reqs = [("logger", [("tb.toml", "^1.0")]), ("webby", [("tb.toml", "^1.0")])]
        rng.shuffle(reqs)
        assert pkg.resolve_tree(dict(reqs), shuffled, tree_deps) == RESOLVED
    with pytest.raises(pkg.PkgError, match="couldn't find a package called 'nope'"):
        pkg.resolve_tree({"nope": [("tb.toml", "*")]}, full, tree_deps)
    with pytest.raises(pkg.PkgError, match="from a git, zip or folder source"):
        pkg._dependency_ranges("webby", "1.0.0", {"x": {"git": "https://example.com/x.git"}}, {})
    assert pkg._dependency_ranges("webby", "1.0.0", {"x": {"git": "u"}, "y": {"version": "^2"}}, {"x": "*"}) == {"y": "^2"}


# --- the signed index -------------------------------------------------------------------

RFC8032 = [  # RFC 8032 section 7.1, tests 1 to 3: secret key, public key, message, signature
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("secret,public,message,signature", RFC8032)
def test_ed25519_rfc8032_vectors(secret, public, message, signature):
    pub, msg, sig = bytes.fromhex(public), bytes.fromhex(message), bytes.fromhex(signature)
    assert ed25519.verify(pub, msg, sig)
    assert ed25519_sign(bytes.fromhex(secret), msg) == (pub, sig)  # the test signer is right too
    assert not ed25519.verify(pub, msg + b"x", sig)
    assert not ed25519.verify(pub, msg, sig[:63] + bytes([sig[63] ^ 1]))
    assert not ed25519.verify(pub, msg, bytes([sig[0] ^ 1]) + sig[1:])
    assert not ed25519.verify(bytes([pub[0] ^ 1]) + pub[1:], msg, sig)
    s = int.from_bytes(sig[32:], "little") + ed25519.L  # same point, S not reduced: refused (malleability)
    assert not ed25519.verify(pub, msg, sig[:32] + s.to_bytes(32, "little"))
    for bad in (b"", sig[:63], sig + b"\0", None, "text"):
        assert not ed25519.verify(pub, msg, bad)
    assert not ed25519.verify(pub[:31], msg, sig)
    assert not ed25519.verify(b"\xff" * 32, msg, sig)  # y >= p doesn't decode


def test_ed25519_matches_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = ("const c=require('crypto');const k=c.generateKeyPairSync('ed25519');const m=Buffer.from('tb index \\u00e9');"
              "console.log(k.publicKey.export({format:'der',type:'spki'}).subarray(12).toString('hex'),"
              "c.sign(null,m,k.privateKey).toString('hex'),m.toString('hex'))")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    pub, sig, msg = (bytes.fromhex(x) for x in out)
    assert ed25519.verify(pub, msg, sig) and not ed25519.verify(pub, msg + b"!", sig)


def test_signed_index_is_required_once_a_key_is_pinned(project, registry, monkeypatch, capsys):
    seed = bytes(range(32))
    public = base64.b64encode(ed25519_sign(seed, b"")[0]).decode()
    registry.signing_seed = seed
    monkeypatch.setenv("TB_REGISTRY_PUBLIC_KEY", public)
    assert pkg.main(["add", "greet"]) == 0
    other = base64.b64encode(ed25519_sign(b"\1" * 32, b"")[0]).decode()
    monkeypatch.setenv("TB_REGISTRY_PUBLIC_KEY", other)
    assert pkg.main(["add", "greet"]) == 1
    assert "doesn't match TB's key" in capsys.readouterr().err
    monkeypatch.setenv("TB_REGISTRY_PUBLIC_KEY", f"{other},{public}")  # a second key, as in a key rotation
    assert pkg.main(["add", "greet"]) == 0
    registry.signing_seed = None
    assert pkg.main(["add", "greet"]) == 1
    assert "isn't signed, so I won't use it" in capsys.readouterr().err
    monkeypatch.setenv("TB_REGISTRY_PUBLIC_KEY", "not-a-key")
    assert pkg.main(["add", "greet"]) == 1
    assert "isn't an Ed25519 public key" in capsys.readouterr().err
    # No key pinned for a self-hosted index: unsigned is fine, and quiet.
    monkeypatch.delenv("TB_REGISTRY_PUBLIC_KEY")
    capsys.readouterr()
    assert pkg.main(["add", "greet"]) == 0
    assert "signed" not in capsys.readouterr().err


def test_official_index_signature_policy(monkeypatch, capsys):
    monkeypatch.delenv("TB_REGISTRY_PUBLIC_KEY", raising=False)
    body = b'{"format": "tb-registry-1", "packages": {}}'
    # Until TB ships with the index's public key: accepted, with a note.
    assert pkg.check_index_signature(pkg.DEFAULT_INDEX, body, None) is False
    assert "isn't signed yet" in capsys.readouterr().err
    seed = b"\7" * 32
    public, signature = ed25519_sign(seed, pkg.SIGNATURE_CONTEXT + body)
    monkeypatch.setattr(pkg, "OFFICIAL_INDEX_KEYS", (base64.b64encode(public).decode(),))
    header = "ed25519 " + base64.b64encode(signature).decode()
    assert pkg.check_index_signature(pkg.DEFAULT_INDEX, body, header) is True
    with pytest.raises(pkg.PkgError, match="isn't signed"):
        pkg.check_index_signature(pkg.DEFAULT_INDEX, body, None)
    with pytest.raises(pkg.PkgError, match="doesn't match"):
        pkg.check_index_signature(pkg.DEFAULT_INDEX, body + b" ", header)
    with pytest.raises(pkg.PkgError, match="doesn't match"):
        pkg.check_index_signature(pkg.DEFAULT_INDEX, body, "rsa " + header.split()[1])
    # The pinned key is only for the official index.
    assert pkg.check_index_signature("https://example.com/index", body, None) is False
