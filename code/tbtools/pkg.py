"""The TB package manager: `tb init`, `tb add`, `tb remove`, `tb update`,
`tb packages`, `tb publish`.

A TB project is a folder with a `tb.toml`:

    [package]
    name = "my-app"
    version = "0.1.0"
    description = "What it does"
    main = "main.tb"

    [dependencies]
    tb-colors = "*"                                   # from the package index
    helpers = { git = "https://example.com/helpers.git", tag = "v1.2" }
    tables = { zip = "https://example.com/tables.zip" }

Packages are TB-only: a folder with its own `tb.toml` and a `lib.tb` (or
`<name>.tb`, or `[package] main = "..."`). `tb add` installs them into
`tb_packages/<name>/` next to `tb.toml` and records exactly what was installed
in `tb.lock` (git commit or zip sha256), so `tb add` with no arguments
re-creates the same `tb_packages/` folder on another machine.

Names are looked up in the package index, a JSON document
`{ "<name>": {"description": "...", "git": "<url>" | "zip": "<url>", "version"?: "...",
"tags"?: [...]} }` served at https://code.tanisha.ceo/packages/index.json.
The environment variable TB_PACKAGE_INDEX overrides that URL (the tests point
it at a local server). Git sources need the `git` command; zip sources only
need Python.

Everything here is stdlib only and prints friendly messages; it never
touches credentials (use git's own credential helpers for private repos).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

__all__ = ["main", "PkgError", "DEFAULT_INDEX", "parse_toml", "dump_toml"]

DEFAULT_INDEX = "https://code.tanisha.ceo/packages/index.json"
PACKAGES_DIR = "tb_packages"
MANIFEST = "tb.toml"
LOCKFILE = "tb.lock"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
MAX_ZIP_BYTES = 50 * 1024 * 1024
HTTP_TIMEOUT = 30

USAGE = """\
TB packages

  tb init [name]                 start a project here (writes tb.toml)
  tb add <name>[@tag]            install a package from the index
  tb add <git-url>[@tag]         install from any git repository
  tb add <zip-url>               install from a .zip download
  tb add                         install everything listed in tb.toml
  tb remove <name>               uninstall a package
  tb update [name]               re-install packages at their newest version
  tb packages                    list installed packages (--index: the index)
  tb publish [--git URL|--zip URL]  print the index entry for this package

Packages live in tb_packages/<name>/ next to tb.toml; tb.lock records exactly
what was installed. Index: TB_PACKAGE_INDEX or """ + DEFAULT_INDEX


class PkgError(Exception):
    """A problem we can explain to the user (no traceback)."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        text = f"Oops! {self.message}"
        if self.hint:
            text += f"\n  Hint: {self.hint}"
        return text


# ---------------------------------------------------------------------------
# A small TOML reader/writer (tomllib is used when available, Python 3.11+)
# ---------------------------------------------------------------------------

def parse_toml(text: str) -> dict:
    """Parse the subset of TOML tb.toml uses (tables, strings, numbers, bools,
    arrays of scalars, inline tables). Uses tomllib when the Python has it."""
    try:
        import tomllib  # Python 3.11+

        return tomllib.loads(text)
    except ImportError:
        pass
    except Exception as err:  # tomllib.TOMLDecodeError
        raise PkgError(f"I couldn't read this TOML: {err}") from None
    return _MiniToml(text).parse()


class _MiniToml:
    """Fallback parser for Python 3.10 (no tomllib)."""

    def __init__(self, text: str):
        self.lines = text.splitlines()

    def parse(self) -> dict:
        root: dict = {}
        table = root
        for number, raw in enumerate(self.lines, 1):
            line = self._strip_comment(raw).strip()
            if not line:
                continue
            if line.startswith("[[") and line.endswith("]]"):
                path = self._key_path(line[2:-2])
                parent = self._walk(root, path[:-1])
                parent.setdefault(path[-1], []).append({})
                table = parent[path[-1]][-1]
                continue
            if line.startswith("[") and line.endswith("]"):
                table = self._walk(root, self._key_path(line[1:-1]))
                continue
            if "=" not in line:
                raise PkgError(f"Line {number} of the TOML isn't 'key = value': {raw.strip()}")
            key, _, value = line.partition("=")
            path = self._key_path(key)
            target = self._walk(table, path[:-1])
            target[path[-1]] = self._value(value.strip(), number)
        return root

    @staticmethod
    def _strip_comment(line: str) -> str:
        out = []
        quote = None
        for ch in line:
            if quote:
                out.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _key_path(text: str) -> list[str]:
        parts = []
        for part in re.findall(r'"[^"]*"|\'[^\']*\'|[^.]+', text.strip()):
            part = part.strip()
            if part[:1] in "\"'" and part[-1:] == part[:1]:
                part = part[1:-1]
            parts.append(part)
        return parts

    @staticmethod
    def _walk(table: dict, path: list[str]) -> dict:
        for key in path:
            table = table.setdefault(key, {})
            if isinstance(table, list):
                table = table[-1]
        return table

    def _value(self, text: str, number: int):
        if text.startswith('"') and text.endswith('"') and len(text) >= 2:
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        if text.startswith("'") and text.endswith("'") and len(text) >= 2:
            return text[1:-1]
        if text in ("true", "false"):
            return text == "true"
        if text.startswith("[") and text.endswith("]"):
            return [self._value(item, number) for item in self._split(text[1:-1])]
        if text.startswith("{") and text.endswith("}"):
            table: dict = {}
            for item in self._split(text[1:-1]):
                key, _, value = item.partition("=")
                table[self._key_path(key)[-1]] = self._value(value.strip(), number)
            return table
        try:
            return int(text.replace("_", ""))
        except ValueError:
            pass
        try:
            return float(text.replace("_", ""))
        except ValueError:
            raise PkgError(f"Line {number} of the TOML has a value I can't read: {text}") from None

    @staticmethod
    def _split(text: str) -> list[str]:
        items, buf, depth, quote = [], [], 0, None
        for ch in text:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf.append(ch)
            elif ch in "[{":
                depth += 1
                buf.append(ch)
            elif ch in "]}":
                depth -= 1
                buf.append(ch)
            elif ch == "," and depth == 0:
                items.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        tail = "".join(buf).strip()
        if tail:
            items.append(tail)
        return items


def _toml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {_toml_scalar(v)}" for k, v in value.items()) + " }"
    return json.dumps(str(value), ensure_ascii=False)


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def dump_toml(data: dict, header: str | None = None) -> str:
    """Write a dict of tables (and scalars) as TOML text."""
    out = []
    if header:
        out.extend(f"# {line}" if line else "#" for line in header.splitlines())
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    for key, value in scalars.items():
        out.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    for name, table in tables.items():
        if out:
            out.append("")
        out.append(f"[{_toml_key(name)}]")
        for key, value in table.items():
            out.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Project files
# ---------------------------------------------------------------------------

def find_project_root(start: str | None = None) -> str | None:
    """The nearest folder at or above `start` that has a tb.toml, or None."""
    folder = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(folder, MANIFEST)):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def require_project(start: str | None = None) -> str:
    root = find_project_root(start)
    if root is None:
        raise PkgError(
            f"There's no {MANIFEST} here (or in any folder above).",
            "Run `tb init` first to start a project in this folder.",
        )
    return root


def read_manifest(root: str) -> dict:
    path = os.path.join(root, MANIFEST)
    try:
        with open(path, encoding="utf-8") as fh:
            data = parse_toml(fh.read())
    except OSError as err:
        raise PkgError(f"I couldn't read {path}: {err.strerror}.") from None
    if not isinstance(data, dict):
        raise PkgError(f"{path} doesn't look like a tb.toml.")
    data.setdefault("package", {})
    data.setdefault("dependencies", {})
    if not isinstance(data["dependencies"], dict):
        raise PkgError(f"[dependencies] in {path} must be a table of name = source.")
    return data


def write_manifest(root: str, data: dict) -> None:
    ordered = {"package": data.get("package", {})}
    for key, value in data.items():
        if key not in ("package", "dependencies"):
            ordered[key] = value
    ordered["dependencies"] = data.get("dependencies", {})
    _write_text(os.path.join(root, MANIFEST), dump_toml(ordered))


def read_lock(root: str) -> dict:
    path = os.path.join(root, LOCKFILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = parse_toml(fh.read())
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def write_lock(root: str, lock: dict) -> None:
    header = "Written by `tb add`. It records exactly what is installed in tb_packages/.\nDon't edit by hand; commit it with your project."
    _write_text(os.path.join(root, LOCKFILE), dump_toml(dict(sorted(lock.items())), header))


def _write_text(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as err:
        raise PkgError(f"I couldn't write {path}: {err.strerror}.") from None


# ---------------------------------------------------------------------------
# Sources: where a package comes from
# ---------------------------------------------------------------------------

def index_url() -> str:
    return os.environ.get("TB_PACKAGE_INDEX") or DEFAULT_INDEX


def _fetch(url: str, what: str, limit: int = MAX_ZIP_BYTES) -> bytes:
    """Download `url` (http/https/file) with a size limit and friendly errors."""
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ("http", "https", "file"):
        raise PkgError(f"I can only download {what} over http(s), not '{url}'.")
    request = urllib.request.Request(url, headers={"User-Agent": "tb-pkg"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise PkgError(f"I couldn't find {what} at {url} (404).") from None
        raise PkgError(f"The server at {url} said {err.code} for {what}.") from None
    except (urllib.error.URLError, OSError, ValueError) as err:
        reason = getattr(err, "reason", err)
        raise PkgError(
            f"I couldn't reach {url} to get {what}: {reason}",
            "Check your internet connection, or set TB_PACKAGE_INDEX to another index.",
        ) from None
    if len(data) > limit:
        raise PkgError(f"{what} at {url} is bigger than {limit // (1024 * 1024)} MB; I won't download it.")
    return data


def load_index() -> dict:
    url = index_url()
    data = _fetch(url, "the package index", limit=10 * 1024 * 1024)
    try:
        index = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PkgError(f"The package index at {url} isn't valid JSON.") from None
    if isinstance(index, dict) and isinstance(index.get("packages"), dict):
        index = index["packages"]
    if not isinstance(index, dict):
        raise PkgError(f"The package index at {url} should be a JSON object of name -> entry.")
    return index


def _split_tag(spec: str) -> tuple[str, str | None]:
    """'name@tag' / 'url@tag' -> (name_or_url, tag). An '@' inside the URL's
    user part (git@host:...) or before a '/' is left alone."""
    at = spec.rfind("@")
    if at <= 0:
        return spec, None
    tail = spec[at + 1:]
    if not tail or "/" in tail or ":" in tail:
        return spec, None
    return spec[:at], tail


def _looks_like_url(text: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", text)) or bool(re.match(r"^[\w.-]+@[\w.-]+:", text))


def resolve_spec(spec: str, index: dict | None = None) -> dict:
    """Turn what the user typed into a source description:
    {name, kind: git|zip|path, url|path, tag?, description?}."""
    spec = spec.strip()
    if not spec:
        raise PkgError("Which package? Like: tb add tb-colors")
    base, tag = _split_tag(spec)
    if os.path.isfile(base) and base.lower().endswith(".zip"):
        return {"name": _name_from_url(base), "kind": "zip", "url": "file://" + urllib.request.pathname2url(os.path.abspath(base))}
    if _looks_like_url(base) or base.endswith(".zip") or base.endswith(".git"):
        name = _name_from_url(base)
        if base.lower().endswith(".zip"):
            return {"name": name, "kind": "zip", "url": base}
        return {"name": name, "kind": "git", "url": base, "tag": tag}
    if os.path.isdir(base):
        name = os.path.basename(os.path.abspath(base))
        if os.path.isdir(os.path.join(base, ".git")):
            return {"name": name, "kind": "git", "url": os.path.abspath(base), "tag": tag}
        return {"name": name, "kind": "path", "path": os.path.abspath(base)}
    if not NAME_RE.match(base):
        raise PkgError(
            f"'{base}' isn't a package name or a URL.",
            "Names use letters, digits, - and _, like tb-colors. URLs start with https://.",
        )
    if index is None:
        index = load_index()
    entry = index.get(base)
    if entry is None:
        close = [n for n in index if base.lower().replace("_", "-") in n.lower() or n.lower() in base.lower()]
        hint = f"Did you mean: {', '.join(sorted(close)[:3])}?" if close else "See what's available with: tb packages --index"
        raise PkgError(f"I couldn't find a package called '{base}' in the index at {index_url()}.", hint)
    if not isinstance(entry, dict):
        raise PkgError(f"The index entry for '{base}' is malformed.")
    source = {"name": base, "description": entry.get("description", ""), "version": entry.get("version")}
    if entry.get("git"):
        source.update(kind="git", url=entry["git"], tag=tag or entry.get("tag"))
    elif entry.get("zip"):
        if tag:
            raise PkgError(f"'{base}' comes as a zip download, so it has no tags to pick from.")
        source.update(kind="zip", url=entry["zip"])
    else:
        raise PkgError(f"The index entry for '{base}' has neither a 'git' nor a 'zip' source.")
    return source


def _name_from_url(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1].split(":")[-1]
    tail = re.sub(r"\.(git|zip)$", "", tail, flags=re.IGNORECASE)
    tail = re.sub(r"[^A-Za-z0-9_-]", "-", tail).strip("-")
    if not tail or not NAME_RE.match(tail):
        raise PkgError(f"I can't work out a package name from '{url}'.", "Give the folder a name with: tb add URL --as name")
    return tail


def _manifest_source(source: dict) -> object:
    """What to record under [dependencies] in tb.toml."""
    if source["kind"] == "path":
        return {"path": source["path"]}
    if source.get("from_index"):
        return source.get("tag") and {"version": source["tag"]} or "*"
    entry = {source["kind"]: source["url"]}
    if source.get("tag"):
        entry["tag"] = source["tag"]
    return entry


def _source_from_manifest(name: str, value) -> dict:
    """The reverse of _manifest_source, for `tb add` (no args) and `tb update`."""
    if isinstance(value, str):
        tag = None if value in ("*", "", "latest") else value
        source = resolve_spec(name if tag is None else f"{name}@{tag}")
        source["from_index"] = True
        return source
    if isinstance(value, dict):
        if "version" in value and not any(k in value for k in ("git", "zip", "path")):
            source = resolve_spec(f"{name}@{value['version']}")
            source["from_index"] = True
            return source
        if "git" in value:
            return {"name": name, "kind": "git", "url": value["git"], "tag": value.get("tag")}
        if "zip" in value:
            return {"name": name, "kind": "zip", "url": value["zip"]}
        if "path" in value:
            return {"name": name, "kind": "path", "path": value["path"]}
    raise PkgError(
        f"I don't understand the dependency '{name}' in {MANIFEST}.",
        'Write "*" for the index, or { git = "URL", tag = "v1" }, or { zip = "URL" }.',
    )


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------

def _git() -> str:
    exe = shutil.which("git")
    if not exe:
        raise PkgError(
            "This package comes from a git repository, but I can't find the `git` command.",
            "Install git (https://git-scm.com) or pick a package with a zip source.",
        )
    return exe


def _run_git(args: list[str], cwd: str | None = None) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo", LC_ALL="C")
    try:
        done = subprocess.run([_git(), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise PkgError("git took more than five minutes; giving up.") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        last = detail[-1] if detail else f"exit code {done.returncode}"
        raise PkgError(f"git {args[0]} failed: {last}")
    return done.stdout.strip()


def _clone(source: dict, dest: str) -> dict:
    """Clone `source` into `dest`; returns lock info {commit, tag?}."""
    args = ["clone", "--quiet", "--depth", "1"]
    if source.get("tag"):
        args += ["--branch", source["tag"]]
    args += ["--", source["url"], dest]
    try:
        _run_git(args)
    except PkgError as err:
        if source.get("tag") and ("not found" in err.message or "Remote branch" in err.message):
            raise PkgError(
                f"The repository at {source['url']} has no tag or branch called '{source['tag']}'.",
                "Leave the @tag off to get the newest version.",
            ) from None
        if "could not read Username" in err.message or "Authentication" in err.message:
            raise PkgError(
                f"The repository at {source['url']} needs a login.",
                "Set up git's credential helper for that host first; tb never asks for passwords.",
            ) from None
        raise
    commit = _run_git(["rev-parse", "HEAD"], cwd=dest)
    shutil.rmtree(os.path.join(dest, ".git"), ignore_errors=True)
    info = {"commit": commit}
    if source.get("tag"):
        info["tag"] = source["tag"]
    return info


def _unzip(source: dict, dest: str) -> dict:
    data = _fetch(source["url"], f"the package '{source['name']}'")
    digest = hashlib.sha256(data).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        members = [m for m in archive.infolist()]
    except zipfile.BadZipFile:
        raise PkgError(f"The download from {source['url']} isn't a zip file.") from None
    names = [m.filename for m in members if not m.filename.endswith("/")]
    if not names:
        raise PkgError(f"The zip from {source['url']} is empty.")
    # Strip one shared top-level folder (how GitHub-style archives look).
    heads = {n.split("/", 1)[0] for n in names}
    prefix = heads.pop() + "/" if len(heads) == 1 and all("/" in n for n in names) else ""
    total = 0
    dest_abs = os.path.abspath(dest)
    for member in members:
        rel = member.filename[len(prefix):] if member.filename.startswith(prefix) else member.filename
        if not rel or rel.endswith("/"):
            continue
        target = os.path.abspath(os.path.join(dest, *rel.split("/")))
        if os.path.commonpath([dest_abs, target]) != dest_abs or os.path.isabs(rel) or ".." in rel.split("/"):
            raise PkgError(f"The zip from {source['url']} tries to write outside its folder ({member.filename}); refusing.")
        total += member.file_size
        if total > MAX_ZIP_BYTES:
            raise PkgError(f"The zip from {source['url']} unpacks to more than {MAX_ZIP_BYTES // (1024 * 1024)} MB; refusing.")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
    return {"sha256": digest}


def _copy_path(source: dict, dest: str) -> dict:
    src = source["path"]
    if not os.path.isdir(src):
        raise PkgError(f"The folder {src} doesn't exist any more.")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", PACKAGES_DIR))
    return {}


def package_entry(folder: str, name: str) -> str | None:
    """The file `import` would load for a package folder, or None."""
    manifest = os.path.join(folder, MANIFEST)
    if os.path.isfile(manifest):
        try:
            main = read_manifest(folder)["package"].get("main")
        except PkgError:
            main = None
        if main and os.path.isfile(os.path.join(folder, main)):
            return main
    for candidate in ("lib.tb", f"{name}.tb", f"{name.replace('-', '_')}.tb"):
        if os.path.isfile(os.path.join(folder, candidate)):
            return candidate
    return None


def _installed_meta(folder: str) -> dict:
    if os.path.isfile(os.path.join(folder, MANIFEST)):
        try:
            return read_manifest(folder)["package"]
        except PkgError:
            return {}
    return {}


def install(root: str, source: dict, *, quiet: bool = False) -> dict:
    """Fetch `source` into tb_packages/<name>/ (replacing what's there) and
    return the lock entry. Doesn't touch tb.toml."""
    name = source["name"]
    if not NAME_RE.match(name):
        raise PkgError(f"'{name}' isn't a valid package name.")
    packages = os.path.join(root, PACKAGES_DIR)
    os.makedirs(packages, exist_ok=True)
    final = os.path.join(packages, name)
    staging = tempfile.mkdtemp(prefix=f".{name}-", dir=packages)
    dest = os.path.join(staging, name)
    try:
        if source["kind"] == "git":
            info = _clone(source, dest)
        elif source["kind"] == "zip":
            info = _unzip(source, dest)
        elif source["kind"] == "path":
            info = _copy_path(source, dest)
        else:
            raise PkgError(f"I don't know how to install a '{source['kind']}' package.")
        entry = package_entry(dest, name)
        if entry is None:
            raise PkgError(
                f"'{name}' doesn't look like a TB package: there's no lib.tb, {name}.tb or tb.toml main file in it.",
                "A TB package is a folder with tb.toml and lib.tb.",
            )
        if os.path.isdir(final):
            shutil.rmtree(final)
        elif os.path.exists(final):
            os.remove(final)
        os.replace(dest, final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    meta = _installed_meta(final)
    lock = {"source": source["kind"], "entry": entry}
    if source["kind"] == "path":
        lock["path"] = source["path"]
    else:
        lock["url"] = source["url"]
    lock.update(info)
    if meta.get("version") is not None:
        lock["version"] = str(meta["version"])
    if not quiet:
        version = f" {lock['version']}" if lock.get("version") else ""
        where = {"git": lock.get("commit", "")[:12], "zip": "zip", "path": "local folder"}[source["kind"]]
        print(f"  + {name}{version} ({where}) -> {PACKAGES_DIR}/{name}/")
    return lock


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args: list[str]) -> int:
    folder = os.getcwd()
    name = None
    rest = list(args)
    if rest and not rest[0].startswith("-"):
        name = rest.pop(0)
    if rest:
        raise PkgError(f"I don't know the option '{rest[0]}' for tb init.", "Just: tb init [name]")
    path = os.path.join(folder, MANIFEST)
    if os.path.exists(path):
        print(f"There's already a {MANIFEST} here. Add packages with: tb add <name>")
        return 0
    name = name or re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.basename(folder)).strip("-") or "my-project"
    if not NAME_RE.match(name):
        raise PkgError(f"'{name}' isn't a valid project name.", "Use letters, digits, - and _.")
    tb_files = sorted(f for f in os.listdir(folder) if f.endswith(".tb") and not f.endswith("_test.tb"))
    main = "main.tb" if "main.tb" in tb_files or not tb_files else tb_files[0]
    data = {
        "package": {"name": name, "version": "0.1.0", "description": "", "main": main},
        "dependencies": {},
    }
    write_manifest(folder, data)
    print(f"Started the TB project '{name}' ({MANIFEST} written).")
    if not os.path.exists(os.path.join(folder, main)):
        _write_text(os.path.join(folder, main), f'# {name}\nsay("Hello from {name}!")\n')
        print(f"Made a starter {main} too.")
    print("Next: tb add <package>   (see what's available with: tb packages --index)")
    return 0


def cmd_add(args: list[str]) -> int:
    rest = list(args)
    alias = None
    specs = []
    while rest:
        item = rest.pop(0)
        if item == "--as":
            if not rest:
                raise PkgError("--as needs a name after it.")
            alias = rest.pop(0)
        elif item.startswith("-"):
            raise PkgError(f"I don't know the option '{item}' for tb add.", "Usage: tb add <name|url>[@tag] [--as name]")
        else:
            specs.append(item)
    root = require_project()
    manifest = read_manifest(root)
    lock = read_lock(root)
    if not specs:
        return _install_all(root, manifest, lock)
    if alias and len(specs) != 1:
        raise PkgError("--as works with one package at a time.")
    index = None
    for spec in specs:
        base, _ = _split_tag(spec)
        if index is None and not _looks_like_url(base) and not os.path.exists(base) and not base.endswith((".zip", ".git")):
            if not NAME_RE.match(base):
                resolve_spec(spec, {})  # raises the friendly "not a name or URL" error
            index = load_index()
        source = resolve_spec(spec, index)
        source["from_index"] = bool(index) and source["name"] in (index or {})
        if alias:
            if not NAME_RE.match(alias):
                raise PkgError(f"'{alias}' isn't a valid package name.")
            source["name"] = alias
        print(f"Adding {source['name']}...")
        entry = install(root, source)
        manifest["dependencies"][source["name"]] = _manifest_source(source)
        lock[source["name"]] = entry
        write_manifest(root, manifest)
        write_lock(root, lock)
    last = source["name"]
    rel = os.path.relpath(os.path.join(root, PACKAGES_DIR, last, lock[last]["entry"]))
    print(f"Done. Use it with: import \"{rel.replace(os.sep, '/')}\" as {last.replace('-', '_')}")
    return 0


def _install_all(root: str, manifest: dict, lock: dict) -> int:
    deps = manifest["dependencies"]
    if not deps:
        print(f"No dependencies in {MANIFEST} yet. Add one with: tb add <name>")
        return 0
    print(f"Installing {len(deps)} package{'s' if len(deps) != 1 else ''} from {MANIFEST}...")
    for name, value in deps.items():
        source = _source_from_manifest(name, value)
        locked = lock.get(name, {})
        if source["kind"] == "git" and not source.get("tag") and locked.get("commit") and locked.get("url") == source["url"]:
            # Reproduce the lock: fetch the recorded commit.
            source = dict(source, tag=None, commit=locked["commit"])
            lock[name] = _install_locked_git(root, source)
            continue
        lock[name] = install(root, source)
    write_lock(root, lock)
    print("Done.")
    return 0


def _install_locked_git(root: str, source: dict) -> dict:
    """Clone a git source and check out the exact commit from tb.lock."""
    name = source["name"]
    packages = os.path.join(root, PACKAGES_DIR)
    os.makedirs(packages, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f".{name}-", dir=packages)
    dest = os.path.join(staging, name)
    try:
        _run_git(["clone", "--quiet", "--", source["url"], dest])
        try:
            _run_git(["checkout", "--quiet", source["commit"]], cwd=dest)
        except PkgError:
            print(f"  (the locked commit {source['commit'][:12]} for {name} is gone; using the newest)")
        commit = _run_git(["rev-parse", "HEAD"], cwd=dest)
        shutil.rmtree(os.path.join(dest, ".git"), ignore_errors=True)
        entry = package_entry(dest, name)
        if entry is None:
            raise PkgError(f"'{name}' doesn't look like a TB package (no lib.tb).")
        final = os.path.join(packages, name)
        if os.path.isdir(final):
            shutil.rmtree(final)
        os.replace(dest, final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    lock = {"source": "git", "entry": entry, "url": source["url"], "commit": commit}
    meta = _installed_meta(final)
    if meta.get("version") is not None:
        lock["version"] = str(meta["version"])
    print(f"  + {name} ({commit[:12]}) -> {PACKAGES_DIR}/{name}/")
    return lock


def cmd_remove(args: list[str]) -> int:
    if not args:
        raise PkgError("Which package should I remove? Like: tb remove tb-colors")
    root = require_project()
    manifest = read_manifest(root)
    lock = read_lock(root)
    for name in args:
        folder = os.path.join(root, PACKAGES_DIR, name)
        known = name in manifest["dependencies"] or name in lock or os.path.isdir(folder)
        if not known:
            installed = sorted(manifest["dependencies"])
            hint = f"Installed: {', '.join(installed)}" if installed else "Nothing is installed yet."
            raise PkgError(f"'{name}' isn't installed.", hint)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        manifest["dependencies"].pop(name, None)
        lock.pop(name, None)
        print(f"  - {name} removed")
    write_manifest(root, manifest)
    write_lock(root, lock)
    return 0


def cmd_update(args: list[str]) -> int:
    root = require_project()
    manifest = read_manifest(root)
    lock = read_lock(root)
    names = list(args) or sorted(manifest["dependencies"])
    if not names:
        print(f"No dependencies in {MANIFEST} to update.")
        return 0
    for name in names:
        if name.startswith("-"):
            raise PkgError(f"I don't know the option '{name}' for tb update.")
        if name not in manifest["dependencies"]:
            raise PkgError(f"'{name}' isn't in {MANIFEST}.", "Add it first with: tb add " + name)
        source = _source_from_manifest(name, manifest["dependencies"][name])
        before = lock.get(name, {})
        print(f"Updating {name}...")
        after = install(root, source)
        lock[name] = after
        same = all(before.get(k) == after.get(k) for k in ("commit", "sha256", "version"))
        if same and before:
            print(f"    {name} was already up to date.")
    write_lock(root, lock)
    return 0


def cmd_packages(args: list[str]) -> int:
    if args and args[0] in ("--index", "--available", "-a"):
        index = load_index()
        if not index:
            print(f"The index at {index_url()} is empty.")
            return 0
        width = max(len(n) for n in index)
        print(f"Packages in the index ({index_url()}):")
        for name in sorted(index):
            entry = index[name] if isinstance(index[name], dict) else {}
            version = f" {entry['version']}" if entry.get("version") else ""
            print(f"  {name.ljust(width)}{version}  {entry.get('description', '')}".rstrip())
        print("\nInstall one with: tb add <name>")
        return 0
    if args:
        raise PkgError(f"I don't know the option '{args[0]}' for tb packages.", "Try: tb packages   or   tb packages --index")
    root = require_project()
    manifest = read_manifest(root)
    lock = read_lock(root)
    deps = manifest["dependencies"]
    if not deps:
        print(f"No packages yet. Add one with: tb add <name>   (see the index: tb packages --index)")
        return 0
    width = max(len(n) for n in deps)
    print(f"Packages for '{manifest['package'].get('name', os.path.basename(root))}':")
    for name in sorted(deps):
        folder = os.path.join(root, PACKAGES_DIR, name)
        entry = lock.get(name, {})
        meta = _installed_meta(folder) if os.path.isdir(folder) else {}
        version = str(meta.get("version") or entry.get("version") or "")
        if not os.path.isdir(folder):
            status = "missing - run: tb add"
        elif entry.get("commit"):
            status = f"git {entry['commit'][:12]}"
        elif entry.get("sha256"):
            status = "zip"
        elif entry.get("path"):
            status = f"local {entry['path']}"
        else:
            status = "installed"
        print(f"  {name.ljust(width)}  {version.ljust(8)}  {status}")
    return 0


def cmd_publish(args: list[str]) -> int:
    rest = list(args)
    overrides = {}
    while rest:
        item = rest.pop(0)
        if item in ("--git", "--zip"):
            if not rest:
                raise PkgError(f"{item} needs a URL after it.")
            overrides[item[2:]] = rest.pop(0)
        else:
            raise PkgError(f"I don't know the option '{item}' for tb publish.", "Usage: tb publish [--git URL | --zip URL]")
    root = require_project()
    manifest = read_manifest(root)
    package = manifest["package"]
    name = package.get("name")
    if not name:
        raise PkgError(f"[package] in {MANIFEST} needs a name before you can publish.")
    entry = package_entry(root, name)
    if entry is None:
        raise PkgError(
            f"'{name}' has no lib.tb (or [package] main = ...) so other programs couldn't import it.",
            "Put your package's code in lib.tb.",
        )
    info = {"description": package.get("description", ""), "version": str(package.get("version", "0.1.0"))}
    if "git" in overrides:
        info["git"] = overrides["git"]
    elif "zip" in overrides:
        info["zip"] = overrides["zip"]
    else:
        remote = ""
        if shutil.which("git") and os.path.isdir(os.path.join(root, ".git")):
            try:
                remote = _run_git(["remote", "get-url", "origin"], cwd=root)
            except PkgError:
                remote = ""
        info["git"] = remote or "https://example.com/your-repo.git"
    tests = [f for f in os.listdir(root) if f.endswith("_test.tb")]
    print(f"Index entry for '{name}' (entry file: {entry}{'' if tests else '; tip: add a lib_test.tb'}):")
    print()
    print(json.dumps({name: info}, indent=2))
    print()
    print("Add this to the package index (see the Packages page on the TB site) and")
    print(f"people can install it with: tb add {name}")
    if "git" in info and info["git"].startswith("https://example.com"):
        print("Replace the git URL with your repository's public URL (or pass --git URL / --zip URL).")
    return 0


COMMANDS = {
    "init": cmd_init,
    "add": cmd_add,
    "install": cmd_add,
    "remove": cmd_remove,
    "rm": cmd_remove,
    "update": cmd_update,
    "packages": cmd_packages,
    "list": cmd_packages,
    "publish": cmd_publish,
}


def main(args: list[str] | None = None) -> int:
    """Entry point: `main(["add", "tb-colors"])` etc. Returns the exit code."""
    args = list(sys.argv[1:] if args is None else args)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command = COMMANDS.get(args[0])
    if command is None:
        print(f"Oops! I don't know the package command '{args[0]}'.\n{USAGE}", file=sys.stderr)
        return 2
    try:
        return command(args[1:])
    except PkgError as err:
        print(str(err), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
