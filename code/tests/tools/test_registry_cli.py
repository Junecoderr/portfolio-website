"""The TB Package Index from the command line (tbtools/pkg.py): tb add with
version ranges and checksums, tb search / info, tb login / logout, tb publish
and tb yank, against a fake registry served on 127.0.0.1. No internet is used.
The real server is tested in tests/api/registry.test.mjs."""

from __future__ import annotations

import hashlib
import http.server
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
from tbtools import pkg  # noqa: E402

TOKEN = "tbr_" + "A" * 43
OTHER_TOKEN = "tbr_" + "B" * 43


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_package(folder: Path, name: str, version: str, body: str | None = None) -> Path:
    write(folder / "tb.toml", f'[package]\nname = "{name}"\nversion = "{version}"\ndescription = "Says hello nicely."\nmain = "lib.tb"\nkeywords = ["greeting"]\n\n[dependencies]\n')
    write(folder / "lib.tb", body or f'export fn hello(n) {{ return "Hello " + n }}\nexport let VERSION = "{version}"\n')
    write(folder / "README.md", f"# {name}\n\nVersion {version}.\n")
    write(folder / "lib_test.tb", 'import "lib.tb" as g\ntest.check(g.hello("x") == "Hello x")\n')
    return folder


def archive_of(folder: Path, name: str) -> bytes:
    checked = pkg.check_package(str(folder))
    return pkg.build_archive(str(folder), name, checked["files"])


class FakeRegistry:
    """Just enough of /api/registry for the CLI."""

    def __init__(self):
        self.packages: dict[str, dict] = {}
        self.tokens = {TOKEN: "bob", OTHER_TOKEN: "carol"}
        self.uploads: list[tuple[str, str, bytes]] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.base = ""

    def add(self, name, version, data, status="approved", owner="bob"):
        p = self.packages.setdefault(name, {"owner": owner, "versions": {}, "description": "Says hello nicely.", "keywords": ["greeting"]})
        p["versions"][version] = {"zip": data, "sha256": hashlib.sha256(data).hexdigest(), "status": status, "downloads": 0}

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
    assert pkg.index_url() == "https://code.tanisha.ceo/api/registry/index"
    assert pkg.registry_base() == "https://code.tanisha.ceo/api/registry"
    assert pkg.site_url() == "https://code.tanisha.ceo"
    monkeypatch.setenv("TB_PACKAGE_INDEX", "https://example.com/mirror/index")
    assert pkg.registry_base() == "https://example.com/mirror"
    monkeypatch.setenv("TB_PACKAGE_INDEX", "https://example.com/static/index.json")
    assert pkg.registry_base() == "https://code.tanisha.ceo/api/registry"
    monkeypatch.setenv("TB_REGISTRY", "https://example.com/api/registry/")
    assert pkg.registry_base() == "https://example.com/api/registry"
