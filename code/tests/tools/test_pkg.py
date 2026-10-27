"""The TB package manager (tbtools/pkg.py): tb init / add / remove / update /
packages / publish, with local git repos, zip files and a local index server.
No internet is used."""

from __future__ import annotations

import http.server
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tb  # noqa: E402
from tbtools import pkg  # noqa: E402

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


def git(*args, cwd):
    env = dict(os.environ, GIT_AUTHOR_NAME="TB Tests", GIT_AUTHOR_EMAIL="tests@example.com",
               GIT_COMMITTER_NAME="TB Tests", GIT_COMMITTER_EMAIL="tests@example.com")
    return subprocess.run([GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_zip(folder: Path, target: Path, top: str | None = None) -> Path:
    """Zip a folder; `top` adds a shared top-level folder like GitHub archives."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                rel = file.relative_to(folder).as_posix()
                zf.write(file, f"{top}/{rel}" if top else rel)
    return target


@pytest.fixture
def repo(tmp_path):
    """A small TB package in a git repo with tag v1.0 and a later commit."""
    if GIT is None:
        pytest.skip("git is not installed")
    folder = tmp_path / "greet-src"
    write(folder / "tb.toml", '[package]\nname = "greet"\nversion = "1.0.0"\ndescription = "Says hello"\nmain = "lib.tb"\n')
    write(folder / "lib.tb", 'fn hello(name) { return "Hello, " + name + "!" }\nlet VERSION = "1.0.0"\n')
    git("init", "-q", "-b", "main", cwd=folder)
    git("add", ".", cwd=folder)
    git("commit", "-q", "-m", "v1", cwd=folder)
    git("tag", "v1.0", cwd=folder)
    first = git("rev-parse", "HEAD", cwd=folder)
    write(folder / "lib.tb", 'fn hello(name) { return "Hi, " + name + "!" }\nlet VERSION = "1.1.0"\n')
    write(folder / "tb.toml", '[package]\nname = "greet"\nversion = "1.1.0"\ndescription = "Says hello"\nmain = "lib.tb"\n')
    git("commit", "-qam", "v1.1", cwd=folder)
    second = git("rev-parse", "HEAD", cwd=folder)
    return {"path": folder, "v1": first, "head": second}


@pytest.fixture
def index_server(tmp_path, monkeypatch):
    """Serve an index.json plus zips of the repo's example packages over HTTP."""
    site = tmp_path / "site"
    site.mkdir()
    index = {}
    for folder in sorted((ROOT / "packages").iterdir()):
        if folder.is_dir() and (folder / "tb.toml").is_file():
            make_zip(folder, site / f"{folder.name}.zip", top=f"{folder.name}-main")
            meta = pkg.read_manifest(str(folder))["package"]
            index[folder.name] = {"description": meta.get("description", ""), "version": meta.get("version", "")}
    # a flat zip without a top folder, and a zip that is not a TB package
    write(tmp_path / "flat" / "lib.tb", 'fn two() { return 2 }\n')
    make_zip(tmp_path / "flat", site / "flat.zip")
    write(tmp_path / "notpkg" / "README.md", "nothing here\n")
    make_zip(tmp_path / "notpkg", site / "notpkg.zip")
    with zipfile.ZipFile(site / "evil.zip", "w") as zf:
        zf.writestr("../escape.tb", "say(1)\n")
        zf.writestr("lib.tb", "say(1)\n")

    handler = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {
        "log_message": lambda self, *a: None,
        "directory": str(site),
    })

    def make(*args, **kwargs):
        return handler(*args, directory=str(site), **kwargs)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    for name in index:
        index[name]["zip"] = f"{base}/{name}.zip"
    index["flat"] = {"description": "A flat zip", "zip": f"{base}/flat.zip"}
    index["notpkg"] = {"description": "Not a package", "zip": f"{base}/notpkg.zip"}
    index["evil"] = {"description": "Zip slip", "zip": f"{base}/evil.zip"}
    index["gone"] = {"description": "404", "zip": f"{base}/missing.zip"}
    (site / "index.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setenv("TB_PACKAGE_INDEX", f"{base}/index.json")
    yield {"base": base, "index": index, "site": site}
    server.shutdown()
    server.server_close()


@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    monkeypatch.chdir(folder)
    assert pkg.main(["init", "demo"]) == 0
    return folder


def run_tb(source: str, path: str) -> list[str]:
    lines = []
    interp = tb.Interpreter(output=lines.append)
    tb.run(source, interpreter=interp, path=path)
    return lines


# --- init -------------------------------------------------------------------

def test_init_writes_manifest_and_starter(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert pkg.main(["init"]) == 0
    data = pkg.read_manifest(str(tmp_path))
    assert data["package"]["name"] == tmp_path.name and data["package"]["main"] == "main.tb"
    assert data["package"]["version"] == "0.1.0" and data["dependencies"] == {}
    assert (tmp_path / "main.tb").read_text().startswith("#")
    out = capsys.readouterr().out
    assert "Started the TB project" in out and "starter main.tb" in out
    # a second init leaves things alone
    (tmp_path / "tb.toml").write_text("[package]\nname = \"kept\"\n")
    assert pkg.main(["init"]) == 0
    assert "already" in capsys.readouterr().out
    assert pkg.read_manifest(str(tmp_path))["package"]["name"] == "kept"


def test_init_uses_existing_tb_file_as_main(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "app.tb", "say(1)\n")
    assert pkg.main(["init", "my-app"]) == 0
    assert pkg.read_manifest(str(tmp_path))["package"]["main"] == "app.tb"
    assert not (tmp_path / "main.tb").exists()


def test_init_rejects_bad_name(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert pkg.main(["init", "bad name!"]) == 1
    assert "isn't a valid project name" in capsys.readouterr().err


# --- add from the index (zip) -------------------------------------------------

def test_add_from_index_installs_zip_and_runs(project, index_server, capsys):
    assert pkg.main(["add", "tb-colors"]) == 0
    out = capsys.readouterr().out
    assert "+ tb-colors 1.0.0 (zip)" in out
    assert 'import "tb_packages/tb-colors/lib.tb" as tb_colors' in out
    lib = project / "tb_packages" / "tb-colors" / "lib.tb"
    assert lib.is_file()
    assert not (project / "tb_packages" / "tb-colors" / "tb-colors-main").exists()  # top folder stripped
    manifest = pkg.read_manifest(str(project))
    assert manifest["dependencies"] == {"tb-colors": "*"}
    lock = pkg.read_lock(str(project))
    assert lock["tb-colors"]["source"] == "zip" and len(lock["tb-colors"]["sha256"]) == 64
    assert lock["tb-colors"]["version"] == "1.0.0" and lock["tb-colors"]["entry"] == "lib.tb"
    # and a program can use it
    write(project / "main.tb", 'import "tb_packages/tb-colors/lib.tb" as colors\ncolors.force(false)\nsay(colors.red("Tanisha"))\nsay(colors.width(colors.bold("abc")))\n')
    assert run_tb((project / "main.tb").read_text(), str(project / "main.tb")) == ["Tanisha", "3"]


def test_add_all_example_packages_and_their_tests_pass(project, index_server, capsys):
    for name in ("tb-colors", "tb-tables", "tb-progress"):
        assert pkg.main(["add", name]) == 0
    assert sorted(pkg.read_manifest(str(project))["dependencies"]) == ["tb-colors", "tb-progress", "tb-tables"]
    assert tb.main(["test", str(project / "tb_packages")]) == 0
    assert "checks passed" in capsys.readouterr().out


def test_add_flat_zip_and_zip_url(project, index_server, capsys):
    assert pkg.main(["add", "flat"]) == 0
    assert (project / "tb_packages" / "flat" / "lib.tb").is_file()
    url = index_server["base"] + "/flat.zip"
    assert pkg.main(["add", url, "--as", "flat2"]) == 0
    assert (project / "tb_packages" / "flat2" / "lib.tb").is_file()
    manifest = pkg.read_manifest(str(project))
    assert manifest["dependencies"]["flat2"] == {"zip": url}


def test_add_local_zip_file(project, index_server):
    local = index_server["site"] / "flat.zip"
    assert pkg.main(["add", str(local)]) == 0
    assert (project / "tb_packages" / "flat" / "lib.tb").is_file()


def test_add_rejects_non_package_zip(project, index_server, capsys):
    assert pkg.main(["add", "notpkg"]) == 1
    assert "doesn't look like a TB package" in capsys.readouterr().err
    assert not (project / "tb_packages" / "notpkg").exists()
    assert "notpkg" not in pkg.read_manifest(str(project))["dependencies"]


def test_add_refuses_zip_slip(project, index_server, capsys):
    assert pkg.main(["add", "evil"]) == 1
    assert "outside its folder" in capsys.readouterr().err
    assert not (project / "escape.tb").exists() and not (project / "tb_packages" / "escape.tb").exists()


def test_add_missing_download_is_friendly(project, index_server, capsys):
    assert pkg.main(["add", "gone"]) == 1
    assert "(404)" in capsys.readouterr().err


def test_add_unknown_name_suggests_close_matches(project, index_server, capsys):
    assert pkg.main(["add", "tb_color"]) == 1
    err = capsys.readouterr().err
    assert "couldn't find a package called 'tb_color'" in err and "tb-colors" in err
    assert pkg.main(["add", "zzz"]) == 1
    assert "tb packages --index" in capsys.readouterr().err


def test_add_tag_on_zip_package_is_an_error(project, index_server, capsys):
    assert pkg.main(["add", "tb-colors@v2"]) == 1
    assert "no tags" in capsys.readouterr().err


def test_add_unreachable_index(project, monkeypatch, capsys):
    monkeypatch.setenv("TB_PACKAGE_INDEX", "http://127.0.0.1:9/index.json")
    assert pkg.main(["add", "tb-colors"]) == 1
    err = capsys.readouterr().err
    assert "couldn't reach" in err and "TB_PACKAGE_INDEX" in err


def test_add_without_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert pkg.main(["add", "tb-colors"]) == 1
    err = capsys.readouterr().err
    assert "no tb.toml here" in err and "tb init" in err


def test_add_bad_spec_and_options(project, capsys):
    assert pkg.main(["add", "not a name!"]) == 1
    assert "isn't a package name or a URL" in capsys.readouterr().err
    assert pkg.main(["add", "--bogus"]) == 1
    assert "don't know the option" in capsys.readouterr().err


# --- git sources -------------------------------------------------------------

@needs_git
def test_add_git_url_at_tag_records_commit(project, repo, capsys):
    url = str(repo["path"])
    assert pkg.main(["add", f"{url}@v1.0", "--as", "greet"]) == 0
    out = capsys.readouterr().out
    assert f"+ greet 1.0.0 ({repo['v1'][:12]})" in out
    lock = pkg.read_lock(str(project))
    assert lock["greet"]["commit"] == repo["v1"] and lock["greet"]["tag"] == "v1.0"
    assert pkg.read_manifest(str(project))["dependencies"]["greet"] == {"git": url, "tag": "v1.0"}
    assert not (project / "tb_packages" / "greet" / ".git").exists()
    source = 'import "tb_packages/greet/lib.tb" as greet\nsay(greet.hello("Tanisha"), greet.VERSION)\n'
    assert run_tb(source, str(project / "main.tb")) == ["Hello, Tanisha! 1.0.0"]


@needs_git
def test_add_git_default_branch_then_update(project, repo, capsys):
    url = str(repo["path"])
    assert pkg.main(["add", url, "--as", "greet"]) == 0
    assert pkg.read_lock(str(project))["greet"]["commit"] == repo["head"]
    # publish a new commit upstream, then tb update picks it up
    write(repo["path"] / "lib.tb", 'fn hello(name) { return "Hey, " + name }\nlet VERSION = "2.0.0"\n')
    git("commit", "-qam", "v2", cwd=repo["path"])
    newest = git("rev-parse", "HEAD", cwd=repo["path"])
    capsys.readouterr()
    assert pkg.main(["update"]) == 0
    assert pkg.read_lock(str(project))["greet"]["commit"] == newest
    assert "Updating greet" in capsys.readouterr().out
    assert pkg.main(["update", "greet"]) == 0
    assert "already up to date" in capsys.readouterr().out


@needs_git
def test_add_local_git_folder_uses_its_name(project, repo):
    assert pkg.main(["add", str(repo["path"])]) == 0
    assert (project / "tb_packages" / "greet-src" / "lib.tb").is_file()


@needs_git
def test_add_git_missing_tag_is_friendly(project, repo, capsys):
    assert pkg.main(["add", f"{repo['path']}@v9.9"]) == 1
    err = capsys.readouterr().err
    assert "no tag or branch called 'v9.9'" in err


@needs_git
def test_add_non_package_repo(project, tmp_path, capsys):
    other = tmp_path / "other"
    write(other / "README.md", "hi\n")
    git("init", "-q", "-b", "main", cwd=other)
    git("add", ".", cwd=other)
    git("commit", "-q", "-m", "x", cwd=other)
    assert pkg.main(["add", str(other)]) == 1
    assert "doesn't look like a TB package" in capsys.readouterr().err
    assert not (project / "tb_packages" / "other").exists()


@needs_git
def test_add_without_args_reinstalls_from_lock(project, repo, index_server, capsys):
    url = str(repo["path"])
    assert pkg.main(["add", url, "--as", "greet"]) == 0
    assert pkg.main(["add", "tb-progress"]) == 0
    locked = pkg.read_lock(str(project))
    # upstream moves on, but the lock pins the old commit
    write(repo["path"] / "lib.tb", 'fn hello(name) { return "changed" }\n')
    git("commit", "-qam", "v3", cwd=repo["path"])
    shutil.rmtree(project / "tb_packages")
    capsys.readouterr()
    assert pkg.main(["add"]) == 0
    out = capsys.readouterr().out
    assert "Installing 2 packages" in out
    assert pkg.read_lock(str(project))["greet"]["commit"] == locked["greet"]["commit"]
    assert pkg.read_lock(str(project))["tb-progress"]["sha256"] == locked["tb-progress"]["sha256"]
    assert (project / "tb_packages" / "greet" / "lib.tb").is_file()
    assert (project / "tb_packages" / "tb-progress" / "lib.tb").is_file()


def test_git_missing_is_explained(project, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert pkg.main(["add", "https://example.invalid/repo.git"]) == 1
    assert "can't find the `git` command" in capsys.readouterr().err


# --- remove / packages / publish -----------------------------------------------

def test_remove(project, index_server, capsys):
    assert pkg.main(["add", "tb-colors"]) == 0
    assert pkg.main(["add", "flat"]) == 0
    capsys.readouterr()
    assert pkg.main(["remove", "tb-colors"]) == 0
    assert "- tb-colors removed" in capsys.readouterr().out
    assert not (project / "tb_packages" / "tb-colors").exists()
    assert (project / "tb_packages" / "flat").exists()
    assert pkg.read_manifest(str(project))["dependencies"] == {"flat": "*"}
    assert "tb-colors" not in pkg.read_lock(str(project))
    assert pkg.main(["remove", "nope"]) == 1
    err = capsys.readouterr().err
    assert "'nope' isn't installed" in err and "Installed: flat" in err
    assert pkg.main(["remove"]) == 1
    assert "Which package" in capsys.readouterr().err


def test_packages_listing(project, index_server, capsys):
    assert pkg.main(["packages"]) == 0
    assert "No packages yet" in capsys.readouterr().out
    assert pkg.main(["add", "tb-tables"]) == 0
    capsys.readouterr()
    assert pkg.main(["packages"]) == 0
    out = capsys.readouterr().out
    assert "Packages for 'demo'" in out and "tb-tables" in out and "1.0.0" in out and "zip" in out
    shutil.rmtree(project / "tb_packages")
    assert pkg.main(["packages"]) == 0
    assert "missing - run: tb add" in capsys.readouterr().out
    assert pkg.main(["packages", "--index"]) == 0
    out = capsys.readouterr().out
    assert "tb-colors" in out and "tb-progress" in out and "ANSI colour" in out
    assert pkg.main(["packages", "--what"]) == 1


def test_update_unknown_and_empty(project, capsys):
    assert pkg.main(["update"]) == 0
    assert "No dependencies" in capsys.readouterr().out
    assert pkg.main(["update", "ghost"]) == 1
    assert "isn't in tb.toml" in capsys.readouterr().err


def test_publish_prints_index_entry(tmp_path, monkeypatch, capsys):
    folder = ROOT / "packages" / "tb-colors"
    monkeypatch.chdir(folder)
    assert pkg.main(["publish", "--zip", "https://code.tanisha.ceo/packages/tb-colors.zip"]) == 0
    out = capsys.readouterr().out
    start = out.index("{")
    end = out.rindex("}") + 1
    entry = json.loads(out[start:end])
    assert entry == {"tb-colors": {
        "description": "ANSI colour and style helpers for terminal output (honours NO_COLOR).",
        "version": "1.0.0",
        "zip": "https://code.tanisha.ceo/packages/tb-colors.zip",
    }}
    assert "tb add tb-colors" in out
    # without a source it suggests a placeholder
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "tb.toml", '[package]\nname = "mine"\nversion = "0.2.0"\n')
    assert pkg.main(["publish"]) == 1  # no lib.tb yet
    assert "no lib.tb" in capsys.readouterr().err
    write(tmp_path / "lib.tb", "fn x() { return 1 }\n")
    assert pkg.main(["publish"]) == 0
    assert "Replace the git URL" in capsys.readouterr().out


def test_index_matches_example_packages():
    index = json.loads((ROOT / "packages" / "index.json").read_text(encoding="utf-8"))
    folders = sorted(p.name for p in (ROOT / "packages").iterdir() if p.is_dir())
    assert sorted(index) == folders == ["tb-colors", "tb-progress", "tb-tables"]
    for name, entry in index.items():
        meta = pkg.read_manifest(str(ROOT / "packages" / name))["package"]
        assert entry["zip"] == f"https://code.tanisha.ceo/packages/{name}.zip"
        assert entry["version"] == meta["version"] and entry["description"] == meta["description"]
        assert (ROOT / "packages" / name / "lib.tb").is_file()
        assert (ROOT / "packages" / name / "README.md").is_file()
        assert list((ROOT / "packages" / name).glob("*_test.tb"))


def test_example_package_tests_pass(capsys):
    assert tb.main(["test", str(ROOT / "packages")]) == 0
    assert "OK:" in capsys.readouterr().out


# --- plumbing ------------------------------------------------------------------

def test_help_and_unknown_command(capsys):
    assert pkg.main([]) == 0
    assert "tb add <name>" in capsys.readouterr().out
    assert pkg.main(["frobnicate"]) == 2
    assert "don't know the package command" in capsys.readouterr().err


def test_tbtools_dispatch_aliases(project, index_server, capsys):
    import tbtools

    assert tbtools.available_commands() == ["add", "init", "packages", "pkg", "publish", "remove", "update"]
    add = importlib.import_module("tbtools.add")
    assert add.main(["tb-colors"]) == 0
    assert (project / "tb_packages" / "tb-colors" / "lib.tb").is_file()
    assert importlib.import_module("tbtools.packages").main([]) == 0
    assert "tb-colors" in capsys.readouterr().out
    assert tbtools.dispatch("remove", ["tb-colors"]) == 0
    assert not (project / "tb_packages" / "tb-colors").exists()


def test_spec_parsing():
    assert pkg._split_tag("name@v1") == ("name", "v1")
    assert pkg._split_tag("git@host:org/repo.git") == ("git@host:org/repo.git", None)
    assert pkg._split_tag("git@host:org/repo.git@v2") == ("git@host:org/repo.git", "v2")
    assert pkg._split_tag("https://h/x.git@1.0") == ("https://h/x.git", "1.0")
    assert pkg.resolve_spec("https://h/org/tb-x.zip", {}) == {"name": "tb-x", "kind": "zip", "url": "https://h/org/tb-x.zip"}
    src = pkg.resolve_spec("https://h/org/tb-x.git@v3", {})
    assert src == {"name": "tb-x", "kind": "git", "url": "https://h/org/tb-x.git", "tag": "v3"}
    src = pkg.resolve_spec("tb-x", {"tb-x": {"git": "https://h/x.git", "description": "d"}})
    assert src["kind"] == "git" and src["url"] == "https://h/x.git" and src["description"] == "d"
    with pytest.raises(pkg.PkgError, match="neither"):
        pkg.resolve_spec("tb-x", {"tb-x": {"description": "d"}})


TOML_SAMPLE = '''
# comment
[package]
name = "demo"            # trailing comment
version = "0.1.0"
main = 'main.tb'
count = 3
ok = true

[dependencies]
tb-colors = "*"
helpers = { git = "https://h/x.git", tag = "v1" }
"quoted-key" = { zip = "https://h/y.zip" }
list = ["a", "b"]
'''


def test_mini_toml_matches_tomllib():
    expected = {
        "package": {"name": "demo", "version": "0.1.0", "main": "main.tb", "count": 3, "ok": True},
        "dependencies": {
            "tb-colors": "*",
            "helpers": {"git": "https://h/x.git", "tag": "v1"},
            "quoted-key": {"zip": "https://h/y.zip"},
            "list": ["a", "b"],
        },
    }
    assert pkg._MiniToml(TOML_SAMPLE).parse() == expected
    assert pkg.parse_toml(TOML_SAMPLE) == expected
    # dump -> parse round trip, both parsers
    text = pkg.dump_toml(expected, "header line")
    assert text.startswith("# header line\n")
    assert pkg.parse_toml(text) == expected
    assert pkg._MiniToml(text).parse() == expected


def test_mini_toml_errors():
    with pytest.raises(pkg.PkgError, match="isn't 'key = value'"):
        pkg._MiniToml("[a]\nboom\n").parse()
    with pytest.raises(pkg.PkgError, match="can't read"):
        pkg._MiniToml("a = what\n").parse()


def test_find_project_root_walks_up(tmp_path):
    write(tmp_path / "tb.toml", "[package]\nname = \"x\"\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert pkg.find_project_root(str(deep)) == str(tmp_path)
    assert pkg.find_project_root(str(tmp_path / "a")) == str(tmp_path)


def test_package_name_from_windows_path_and_url():
    assert pkg._name_from_url("C:\\Users\\me\\Downloads\\flat.zip") == "flat"
    assert pkg._name_from_url("https://example.com/a/helpers.git") == "helpers"
    assert pkg._name_from_url("git@github.com:me/tb-colors.git") == "tb-colors"


def test_rmtree_removes_read_only_files(tmp_path):
    """git makes its object files read-only; Windows won't delete those."""
    import stat
    obj = tmp_path / "pkg" / ".git" / "objects" / "2e" / "74df"
    obj.parent.mkdir(parents=True)
    obj.write_text("x")
    obj.chmod(stat.S_IREAD)
    pkg._rmtree(str(tmp_path / "pkg" / ".git"))
    assert not (tmp_path / "pkg" / ".git").exists()
