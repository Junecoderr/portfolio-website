"""The TB package manager: `tb init`, `tb add`, `tb remove`, `tb update`,
`tb packages`, `tb publish`, `tb login`, `tb logout`, `tb search`, `tb info`,
`tb yank`.

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

Names are looked up in the TB Package Index (https://code.tanisha.ceo/packages),
whose machine-readable index is https://code.tanisha.ceo/api/registry/index.json:
`{"format": "tb-registry-1", "packages": {"<name>": {"description", "version",
"versions": {"1.2.0": {"sha256": "...", "yanked"?: true}}, ...}}}`. `tb add
name@range` picks the newest version that matches the range (`^1.2`, `~1.2.3`,
`>=1.0 <2`, `1.2.3`), skipping yanked versions unless pinned exactly, downloads
it from <registry>/packages/<name>/<version>/download, checks its sha256 and
records it in tb.lock.

The older static format `{ "<name>": {"description": "...", "git": "<url>" |
"zip": "<url>", "version"?: "..."} }` (https://code.tanisha.ceo/packages/index.json)
still works. TB_PACKAGE_INDEX overrides the index URL (the tests point it at
local servers); TB_REGISTRY overrides the registry API used by publish, search,
info, yank and login (by default the index URL without its trailing /index).
Git sources need the `git` command; zip sources only need Python.

Publishing: `tb login` stores an API token made on the website (in
~/.config/tb/credentials.toml, or %APPDATA%\\TB\\credentials.toml on Windows,
readable only by you; TB_TOKEN overrides it), and `tb publish` checks the
package with the index's rules, zips it reproducibly and uploads it. The first
version of a new package is reviewed before it is listed.

Everything here is stdlib only and prints friendly messages. Tokens are only
sent to the registry; git credentials stay with git's own helpers.
"""

from __future__ import annotations

import hashlib
import getpass
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile

__all__ = ["main", "PkgError", "DEFAULT_INDEX", "parse_toml", "dump_toml"]

DEFAULT_REGISTRY = "https://code.tanisha.ceo/api/registry"
DEFAULT_INDEX = DEFAULT_REGISTRY + "/index.json"
LEGACY_INDEX = "https://code.tanisha.ceo/packages/index.json"
PACKAGES_DIR = "tb_packages"
MANIFEST = "tb.toml"
LOCKFILE = "tb.lock"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
MAX_ZIP_BYTES = 50 * 1024 * 1024
HTTP_TIMEOUT = 30

# The index's publishing rules (api/_lib/registry/rules.js has the same ones).
REGISTRY_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
VERSION_RE = re.compile(
    r"^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
RESERVED_WORDS = (
    "tb", "tbcore", "tblib", "tbtools", "std", "stdlib", "core", "builtin", "builtins",
    "admin", "api", "registry", "packages", "package", "index", "official", "system", "root",
    "null", "none", "nil", "true", "false", "login", "logout", "search", "publish",
)
TEXT_EXTENSIONS = (".tb", ".toml", ".md", ".txt", ".json", ".csv", ".tsv", ".yml", ".yaml", ".xml", ".ini", ".cfg", ".log")
TEXT_NAMES = ("license", "licence", "copying", "notice", "authors", "changelog", "readme", "contributors")
IMAGE_MAGIC = {".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
               ".gif": (b"GIF8",), ".webp": (b"RIFF",)}
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_FILES = 200
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_PATH_LENGTH = 200
PUBLISH_SKIP_DIRS = {PACKAGES_DIR, "__pycache__", "node_modules"}
PUBLISH_SKIP_FILES = {LOCKFILE, ".DS_Store", "Thumbs.db"}
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
TOKEN_RE = re.compile(r"^tbr_[A-Za-z0-9_-]{43}$")

USAGE = """\
TB packages

  tb init [name]                 start a project here (writes tb.toml)
  tb add <name>[@range]          install from the TB Package Index (tb add tb-tables@^1.0)
  tb add <git-url>[@tag]         install from any git repository
  tb add <zip-url>               install from a .zip download
  tb add                         install everything listed in tb.toml (as tb.lock pins it)
  tb remove <name>               uninstall a package
  tb update [name]               re-install packages at their newest allowed version
  tb packages                    list installed packages (--index: the whole index)
  tb search <words>              find packages in the index
  tb info <name>                 versions, owner and checksums of a package
  tb login                       store an API token from the website for publishing
  tb logout                      forget the stored token
  tb publish [--dry-run]         check, zip and upload this package to the index
  tb yank <name>@<version> [--reason TEXT] [--undo]   hide a version from ranges

Ranges: ^1.2 (1.x from 1.2), ~1.2.3 (1.2.x from 1.2.3), >=1.0 <2, 1.2.3 (exactly).
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


def registry_base() -> str:
    """The registry API: TB_REGISTRY, else the index URL without /index, else the default."""
    explicit = os.environ.get("TB_REGISTRY")
    if explicit:
        return explicit.rstrip("/")
    url = index_url().rstrip("/")
    if url.endswith("/index"):
        return url[: -len("/index")]
    return DEFAULT_REGISTRY


def site_url(base: str | None = None) -> str:
    """The website that goes with a registry (where tokens are made)."""
    base = (base or registry_base()).rstrip("/")
    return base[: -len("/api/registry")] if base.endswith("/api/registry") else base


# ---------------------------------------------------------------------------
# Versions and ranges (semantic versioning)
# ---------------------------------------------------------------------------

def parse_version(text) -> tuple | None:
    """'1.2.3-beta.1' -> (1, 2, 3, ('beta', '1')), or None."""
    m = VERSION_RE.match(str(text)) if text is not None else None
    if not m or len(str(text)) > 64:
        return None
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre


def version_key(text):
    """A sort key with semver precedence (pre-releases before the release)."""
    v = parse_version(text)
    if v is None:
        return (-1, -1, -1, 0, ())
    pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in v[3])
    return (v[0], v[1], v[2], 0 if v[3] else 1, pre)


_COMPARATOR_RE = re.compile(
    r"^(\^|~|>=|<=|>|<|=)?v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:-([0-9A-Za-z.-]+))?$")


def _comparators(token: str, text: str) -> list[tuple[str, tuple]]:
    m = _COMPARATOR_RE.match(token)
    if not m:
        raise PkgError(f"'{text}' isn't a version range I understand.",
                       "Use ^1.2, ~1.2.3, >=1.0 <2, 1.2.3 or * (any version).")
    op = m.group(1) or ""
    parts = [None if p is None or p in "xX*" else int(p) for p in m.group(2, 3, 4)]
    major, minor, patch = parts
    pre = tuple(m.group(5).split(".")) if m.group(5) else ()
    if major is None:
        return []
    base = (major, minor or 0, patch or 0, pre)
    if op == "^":
        if major > 0 or minor is None:
            upper = (major + 1, 0, 0, ("0",))
        elif minor > 0 or patch is None:
            upper = (0, minor + 1, 0, ("0",))
        else:
            upper = (0, 0, patch + 1, ("0",))
        return [(">=", base), ("<", upper)]
    if op == "~":
        upper = (major + 1, 0, 0, ("0",)) if minor is None else (major, minor + 1, 0, ("0",))
        return [(">=", base), ("<", upper)]
    if op in ("", "="):
        if minor is None:
            return [(">=", base), ("<", (major + 1, 0, 0, ("0",)))]
        if patch is None:
            return [(">=", base), ("<", (major, minor + 1, 0, ("0",)))]
        return [("=", base)]
    if op == ">" and (minor is None or patch is None):
        nxt = (major + 1, 0, 0, ()) if minor is None else (major, minor + 1, 0, ())
        return [(">=", nxt)]
    if op == "<=" and (minor is None or patch is None):
        nxt = (major + 1, 0, 0, ("0",)) if minor is None else (major, minor + 1, 0, ("0",))
        return [("<", nxt)]
    return [(op, base)]


def parse_range(text: str) -> list[list[tuple[str, tuple]]]:
    """A range as alternatives (||) of comparator lists (all must hold)."""
    text = str(text or "").strip()
    if text in ("", "*", "latest", "x", "X"):
        return [[]]
    alternatives = []
    for part in text.split("||"):
        tokens = part.split()
        merged, i = [], 0
        while i < len(tokens):
            token = tokens[i]
            if token in (">=", "<=", ">", "<", "=", "^", "~") and i + 1 < len(tokens):
                token += tokens[i + 1]
                i += 1
            merged.append(token)
            i += 1
        comps = []
        for token in merged:
            comps.extend(_comparators(token, text))
        alternatives.append(comps)
    return alternatives


def _tuple_key(t: tuple):
    return version_key(f"{t[0]}.{t[1]}.{t[2]}" + ("-" + ".".join(t[3]) if t[3] else ""))


def satisfies(version: str, range_text: str) -> bool:
    """Does `version` match the range? Pre-releases only match a range that
    names a pre-release of the same major.minor.patch (as npm does)."""
    v = parse_version(version)
    if v is None:
        return False
    key = version_key(version)
    for comps in parse_range(range_text):
        ok = True
        for op, bound in comps:
            b = _tuple_key(bound)
            ok = {"=": key == b, ">=": key >= b, "<=": key <= b, ">": key > b, "<": key < b}[op]
            if not ok:
                break
        if not ok:
            continue
        if v[3] and not any(bound[3] and bound[:3] == v[:3] for _op, bound in comps):
            continue
        return True
    return False


def is_exact(range_text: str) -> bool:
    alternatives = parse_range(range_text)
    return len(alternatives) == 1 and len(alternatives[0]) == 1 and alternatives[0][0][0] == "="


def pick_version(name: str, versions: dict, range_text: str | None, prefer: str | None = None) -> str:
    """The newest version in `versions` ({version: {sha256, yanked?}}) that
    matches the range; yanked versions only when pinned exactly (or locked)."""
    wanted = range_text or "*"
    parse_range(wanted)  # friendly error for a bad range
    if prefer and prefer in versions and satisfies(prefer, wanted):
        return prefer
    exact = is_exact(wanted)
    candidates = [v for v, info in versions.items()
                  if satisfies(v, wanted) and (exact or not (isinstance(info, dict) and info.get("yanked")))]
    if not candidates:
        available = sorted((v for v, i in versions.items() if not (isinstance(i, dict) and i.get("yanked"))), key=version_key)
        hint = f"Available: {', '.join(available[-8:])}" if available else "Every version of it has been yanked."
        raise PkgError(f"No version of '{name}' matches '{wanted}'.", hint)
    return max(candidates, key=version_key)


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


def resolve_spec(spec: str, index: dict | None = None, prefer: str | None = None) -> dict:
    """Turn what the user typed into a source description:
    {name, kind: registry|git|zip|path, url|path, tag?, description?}.
    `prefer` is the version tb.lock has; it wins while it still matches."""
    spec = spec.strip()
    if not spec:
        raise PkgError("Which package? Like: tb add tb-colors")
    base, tag = _split_tag(spec)
    if os.path.isfile(base) and base.lower().endswith(".zip"):
        # as_uri gives file:///C:/... on Windows and file:///home/... elsewhere
        return {"name": _name_from_url(base), "kind": "zip", "url": pathlib.Path(os.path.abspath(base)).as_uri()}
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
    if isinstance(entry.get("versions"), dict) and entry["versions"]:
        version = pick_version(base, entry["versions"], tag, prefer)
        info = entry["versions"][version] if isinstance(entry["versions"][version], dict) else {}
        registry = registry_base()
        source.update(
            kind="registry", version=version, sha256=info.get("sha256"), range=tag, registry=registry,
            url=f"{registry}/packages/{urllib.parse.quote(base)}/{urllib.parse.quote(version)}/download",
            yanked=bool(info.get("yanked")),
        )
        return source
    if entry.get("git"):
        source.update(kind="git", url=entry["git"], tag=tag or entry.get("tag"))
    elif entry.get("zip"):
        if tag:
            raise PkgError(f"'{base}' comes as a zip download, so it has no tags to pick from.")
        source.update(kind="zip", url=entry["zip"])
    else:
        raise PkgError(f"The index entry for '{base}' has neither a 'git' nor a 'zip' source.")
    return source


def _rmtree(path: str, ignore_errors: bool = False) -> None:
    """shutil.rmtree that also removes read-only files. git marks its object
    files read-only, and Windows refuses to delete a read-only file."""
    def retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            if not ignore_errors:
                raise

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _name_from_url(url: str) -> str:
    # a URL, or a local path: C:\\Users\\me\\flat.zip on Windows
    tail = re.split(r"[/\\]", url.rstrip("/\\"))[-1].split(":")[-1]
    tail = re.sub(r"\.(git|zip)$", "", tail, flags=re.IGNORECASE)
    tail = re.sub(r"[^A-Za-z0-9_-]", "-", tail).strip("-")
    if not tail or not NAME_RE.match(tail):
        raise PkgError(f"I can't work out a package name from '{url}'.", "Give the folder a name with: tb add URL --as name")
    return tail


def _manifest_source(source: dict) -> object:
    """What to record under [dependencies] in tb.toml."""
    if source["kind"] == "path":
        return {"path": source["path"]}
    if source["kind"] == "registry":
        return source.get("range") or f"^{source['version']}"
    if source.get("from_index"):
        return source.get("tag") and {"version": source["tag"]} or "*"
    entry = {source["kind"]: source["url"]}
    if source.get("tag"):
        entry["tag"] = source["tag"]
    return entry


def _source_from_manifest(name: str, value, prefer: str | None = None, index: dict | None = None) -> dict:
    """The reverse of _manifest_source, for `tb add` (no args) and `tb update`.
    `prefer`: the version tb.lock pins, kept while it matches the range."""
    if isinstance(value, str):
        tag = None if value in ("*", "", "latest") else value
        source = resolve_spec(name if tag is None else f"{name}@{tag}", index, prefer)
        source["from_index"] = True
        return source
    if isinstance(value, dict):
        if "version" in value and not any(k in value for k in ("git", "zip", "path")):
            source = resolve_spec(f"{name}@{value['version']}", index, prefer)
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
    _rmtree(os.path.join(dest, ".git"), ignore_errors=True)
    info = {"commit": commit}
    if source.get("tag"):
        info["tag"] = source["tag"]
    return info


def _unzip(source: dict, dest: str) -> dict:
    data = _fetch(source["url"], f"the package '{source['name']}'")
    digest = hashlib.sha256(data).hexdigest()
    expected = source.get("sha256")
    if expected and digest != expected:
        raise PkgError(
            f"The download of {source['name']} {source.get('version', '')} doesn't match the index's checksum; refusing to install it.",
            f"Expected sha256 {expected}, got {digest}. Try again later, and tell the index admins if it keeps happening.",
        )
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
        elif source["kind"] in ("zip", "registry"):
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
            _rmtree(final)
        elif os.path.exists(final):
            os.remove(final)
        os.replace(dest, final)
    finally:
        _rmtree(staging, ignore_errors=True)
    meta = _installed_meta(final)
    lock = {"source": source["kind"], "entry": entry}
    if source["kind"] == "path":
        lock["path"] = source["path"]
    else:
        lock["url"] = source["url"]
    lock.update(info)
    if meta.get("version") is not None:
        lock["version"] = str(meta["version"])
    if source["kind"] == "registry":
        lock["version"] = source["version"]
        lock["registry"] = source["registry"]
    if not quiet:
        version = f" {lock['version']}" if lock.get("version") else ""
        where = {"git": lock.get("commit", "")[:12], "zip": "zip", "path": "local folder",
                 "registry": "TB Package Index" + (", yanked" if source.get("yanked") else "")}[source["kind"]]
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
        locked = lock.get(name, {})
        prefer = locked.get("version") if locked.get("source") == "registry" else None
        source = _source_from_manifest(name, value, prefer)
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
        _rmtree(os.path.join(dest, ".git"), ignore_errors=True)
        entry = package_entry(dest, name)
        if entry is None:
            raise PkgError(f"'{name}' doesn't look like a TB package (no lib.tb).")
        final = os.path.join(packages, name)
        if os.path.isdir(final):
            _rmtree(final)
        os.replace(dest, final)
    finally:
        _rmtree(staging, ignore_errors=True)
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
            _rmtree(folder)
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
        elif entry.get("source") == "registry":
            status = "index"
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


def _print_index_entry(root: str, overrides: dict) -> int:
    """`tb publish --print/--git/--zip`: the entry for a self-hosted static index."""
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
    print("Add this to your own static index.json (served anywhere, used with TB_PACKAGE_INDEX) and")
    print(f"people can install it with: tb add {name}")
    print("To list it in the TB Package Index instead, run: tb publish")
    if "git" in info and info["git"].startswith("https://example.com"):
        print("Replace the git URL with your repository's public URL (or pass --git URL / --zip URL).")
    return 0


# ---------------------------------------------------------------------------
# Publishing to the TB Package Index
# ---------------------------------------------------------------------------

def reserved_names() -> set[str]:
    """Names no package may take: the index's words and every standard library name."""
    names = set(RESERVED_WORDS)
    try:
        import tb  # the language itself, next to tbtools/

        names.update(tb.list_libraries())
    except Exception:
        pass  # the index checks again
    return names


def check_registry_name(name) -> str | None:
    if not isinstance(name, str) or not REGISTRY_NAME_RE.match(name):
        return ("Package names are 3 to 40 characters: lower-case letters, digits and -, "
                "starting with a letter and not ending with -.")
    if "--" in name:
        return "Package names can't contain two - in a row."
    if name in reserved_names():
        return f"'{name}' is reserved (a TB standard library name or a word the index uses)."
    return None


def _publish_files(root: str) -> list[tuple[str, str]]:
    """(archive path, file path) for every file `tb publish` sends, sorted.
    Leaves out tb_packages/, tb.lock, hidden files and caches; refuses links."""
    found = []
    for folder, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(folder, root)
        rel_parts = [] if rel_dir == "." else rel_dir.split(os.sep)
        keep = []
        for d in sorted(dirs):
            full = os.path.join(folder, d)
            if os.path.islink(full):
                raise PkgError(f"'{'/'.join(rel_parts + [d])}' is a symbolic link; packages can't contain links.")
            if d.startswith(".") or d in PUBLISH_SKIP_DIRS:
                continue
            keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            if f.startswith(".") or f in PUBLISH_SKIP_FILES:
                continue
            full = os.path.join(folder, f)
            rel = "/".join(rel_parts + [f])
            if os.path.islink(full):
                raise PkgError(f"'{rel}' is a symbolic link; packages can't contain links.")
            found.append((rel, full))
    return sorted(found)


def _is_text_name(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1].lower()
    ext = os.path.splitext(base)[1]
    if ext in TEXT_EXTENSIONS:
        return True
    stem = base[: -len(ext)] if ext else base
    return stem in TEXT_NAMES and ext in ("", ".md", ".txt")


def check_package(root: str) -> dict:
    """Check a package folder with the index's rules (the same ones the server
    applies). Returns {name, version, entry, files, warnings}; raises PkgError."""
    manifest = read_manifest(root)
    package = manifest["package"]
    name = package.get("name")
    if not name:
        raise PkgError(f"[package] in {MANIFEST} needs a name before you can publish.")
    entry = package_entry(root, str(name))
    if entry is None:
        raise PkgError(
            f"'{name}' has no lib.tb (or [package] main = ...) so other programs couldn't import it.",
            "Put your package's code in lib.tb.",
        )
    problem = check_registry_name(name)
    if problem:
        raise PkgError(problem, f"Change [package] name in {MANIFEST}.")
    version = package.get("version")
    if not isinstance(version, str) or parse_version(version) is None:
        raise PkgError(f"[package] version in {MANIFEST} must be a version like \"1.0.0\" (semantic versioning), not {version!r}.")
    main = package.get("main")
    if main is not None and (not isinstance(main, str) or not main.endswith(".tb")
                             or not os.path.isfile(os.path.join(root, main))):
        raise PkgError(f"[package] main in {MANIFEST} must name a .tb file in the package.")
    description = package.get("description", "")
    if not isinstance(description, str) or len(description) > 300:
        raise PkgError("[package] description must be text of at most 300 characters.")
    keywords = package.get("keywords", [])
    if not isinstance(keywords, list) or len(keywords) > 10 or not all(
            isinstance(k, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", k) for k in keywords):
        raise PkgError("[package] keywords must be a list of at most 10 short lower-case words.")
    files = _publish_files(root)
    if len(files) > MAX_FILES:
        raise PkgError(f"The package has {len(files)} files; the index takes at most {MAX_FILES}.")
    total = 0
    seen = set()
    for rel, full in files:
        if len(rel) > MAX_PATH_LENGTH or re.search(r'[<>:"|?*\\\x00-\x1f]', rel):
            raise PkgError(f"'{rel}' has a name the index won't accept (too long, or characters like : * ?).")
        if rel.lower() in seen:
            raise PkgError(f"'{rel}' clashes with another file whose name differs only in case.")
        seen.add(rel.lower())
        size = os.path.getsize(full)
        if size > MAX_FILE_BYTES:
            raise PkgError(f"'{rel}' is bigger than 1 MB; the index won't take it.")
        total += size
        ext = os.path.splitext(rel)[1].lower()
        with open(full, "rb") as fh:
            data = fh.read()
        if ext in IMAGE_MAGIC:
            if not data.startswith(IMAGE_MAGIC[ext]):
                raise PkgError(f"'{rel}' isn't the image its name says.")
            continue
        if not _is_text_name(rel):
            raise PkgError(
                f"'{rel}' can't go in a package: packages hold TB code and text files "
                f"({' '.join(TEXT_EXTENSIONS)}, LICENSE, README) plus images for the README.",
                "Move it out of the package folder.",
            )
        if b"\x00" in data:
            raise PkgError(f"'{rel}' looks like a binary file; only text is allowed.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise PkgError(f"'{rel}' isn't UTF-8 text.") from None
    if total > MAX_TOTAL_BYTES:
        raise PkgError("The package is bigger than 10 MB unpacked.")
    names = {rel for rel, _ in files}
    warnings = []
    if not any(re.fullmatch(r"(?i)readme(\.md|\.txt)?", n) for n in names):
        warnings.append("there's no README.md (the package page shows it)")
    if not description:
        warnings.append(f"[package] description in {MANIFEST} is empty")
    if not any(n.endswith("_test.tb") for n in names):
        warnings.append("there are no *_test.tb tests")
    return {"name": name, "version": version, "entry": entry, "files": files, "warnings": warnings}


def build_archive(root: str, name: str, files: list[tuple[str, str]]) -> bytes:
    """A reproducible zip: sorted, fixed timestamps and modes, under <name>/."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, full in files:
            info = zipfile.ZipInfo(f"{name}/{rel}", ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with open(full, "rb") as fh:
                zf.writestr(info, fh.read())
    return buffer.getvalue()


def credentials_path() -> str:
    """Where `tb login` keeps tokens (TB_CREDENTIALS overrides it)."""
    if os.environ.get("TB_CREDENTIALS"):
        return os.environ["TB_CREDENTIALS"]
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "TB", "credentials.toml")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tb", "credentials.toml")


def _read_credentials() -> dict:
    path = credentials_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = parse_toml(fh.read())
    except (OSError, PkgError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _write_credentials(data: dict) -> None:
    path = credentials_path()
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        text = dump_toml(dict(sorted(data.items())), "TB Package Index tokens, written by `tb login`. Keep this file private.")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
    except OSError as err:
        raise PkgError(f"I couldn't write {path}: {err.strerror}.") from None


def stored_token(base: str | None = None) -> str | None:
    """TB_TOKEN, or the token `tb login` stored for this registry."""
    env = os.environ.get("TB_TOKEN", "").strip()
    if env:
        return env
    entry = _read_credentials().get(base or registry_base(), {})
    token = entry.get("token") if isinstance(entry, dict) else None
    return token if isinstance(token, str) and token else None


def _api(method: str, path: str, *, token: str | None = None, body: bytes | None = None,
         content_type: str | None = None, base: str | None = None) -> dict:
    """Call the registry API; returns its JSON, or raises PkgError with its message."""
    url = (base or registry_base()) + path
    headers = {"User-Agent": "tb-pkg", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT * 4) as response:
            raw = response.read(10 * 1024 * 1024)
    except urllib.error.HTTPError as err:
        try:
            message = json.loads(err.read(64 * 1024).decode("utf-8")).get("error")
        except Exception:
            message = None
        hint = None
        if err.code == 401:
            hint = "Make a token on the website and run: tb login"
        elif err.code == 429:
            hint = "Wait a while before trying again."
        raise PkgError(message or f"The package index said {err.code} for {path}.", hint) from None
    except (urllib.error.URLError, OSError, ValueError) as err:
        reason = getattr(err, "reason", err)
        raise PkgError(f"I couldn't reach the package index at {url}: {reason}",
                       "Check your internet connection, or set TB_REGISTRY.") from None
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PkgError(f"The package index at {url} sent something that isn't JSON.") from None


def cmd_publish(args: list[str]) -> int:
    rest = list(args)
    overrides = {}
    dry_run = False
    print_entry = False
    out_file = None
    while rest:
        item = rest.pop(0)
        if item in ("--git", "--zip"):
            if not rest:
                raise PkgError(f"{item} needs a URL after it.")
            overrides[item[2:]] = rest.pop(0)
        elif item == "--print":
            print_entry = True
        elif item in ("--dry-run", "-n"):
            dry_run = True
        elif item == "--out":
            if not rest:
                raise PkgError("--out needs a file name after it.")
            out_file = rest.pop(0)
        else:
            raise PkgError(f"I don't know the option '{item}' for tb publish.",
                           "Usage: tb publish [--dry-run] [--out FILE.zip]   (or --print / --git URL / --zip URL for a static index)")
    root = require_project()
    if overrides or print_entry:
        return _print_index_entry(root, overrides)
    checked = check_package(root)
    name, version = checked["name"], checked["version"]
    archive = build_archive(root, name, checked["files"])
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise PkgError(f"The zipped package is {len(archive) // 1024} KB; the index takes at most 4 MB.")
    digest = hashlib.sha256(archive).hexdigest()
    print(f"Checked {name} {version}: {len(checked['files'])} files, {len(archive) // 1024 or 1} KB zipped, entry {checked['entry']}.")
    for warning in checked["warnings"]:
        print(f"  note: {warning}")
    if out_file:
        with open(out_file, "wb") as fh:
            fh.write(archive)
        print(f"Wrote {out_file} (sha256 {digest}).")
    if dry_run:
        print(f"Dry run: nothing uploaded. sha256 {digest}")
        return 0
    base = registry_base()
    token = stored_token(base)
    if not token:
        raise PkgError("You're not signed in to the TB Package Index.",
                       f"Make a token at {site_url(base)}/packages/publish and run: tb login")
    print(f"Uploading to {base} ...")
    result = _api("POST", f"/packages/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}",
                  token=token, body=archive, content_type="application/zip", base=base)
    status = result.get("status")
    if result.get("sha256") and result["sha256"] != digest:
        raise PkgError("The index stored a different checksum than the one I uploaded; try again.")
    if status == "approved":
        print(f"Published {name} {version}. Install it with: tb add {name}")
    else:
        print(f"Submitted {name} {version} for review. It is listed once an admin approves it;")
        print(f"see its status at {site_url(base)}/packages/account")
    for flag in result.get("flags") or []:
        print(f"  reviewer note: {flag}")
    print(f"sha256 {digest}")
    return 0


def cmd_login(args: list[str]) -> int:
    rest = list(args)
    token = None
    open_browser = not os.environ.get("TB_NO_BROWSER")
    while rest:
        item = rest.pop(0)
        if item == "--token":
            if not rest:
                raise PkgError("--token needs the token after it.")
            token = rest.pop(0)
        elif item == "--no-browser":
            open_browser = False
        else:
            raise PkgError(f"I don't know the option '{item}' for tb login.", "Usage: tb login [--token TOKEN] [--no-browser]")
    base = registry_base()
    page = f"{site_url(base)}/packages/publish#tokens"
    if token is None:
        print("Sign in on the website with GitHub, make an API token, then paste it here.")
        print(f"  {page}")
        if open_browser and sys.stdin.isatty():
            try:
                webbrowser.open(page)
            except Exception:
                pass
        try:
            token = getpass.getpass("Token: ") if sys.stdin.isatty() else sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            token = ""
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise PkgError("That doesn't look like a TB Package Index token (they start with tbr_).",
                       f"Make one at {page}")
    me = _api("GET", "/me", token=token, base=base)
    if not me.get("signedIn"):
        raise PkgError("The index doesn't accept that token (revoked or mistyped?).", f"Make a new one at {page}")
    data = _read_credentials()
    data[base] = {"token": token, "user": str((me.get("user") or {}).get("login", ""))}
    _write_credentials(data)
    print(f"Signed in to {base} as {data[base]['user']}. The token is in {credentials_path()} (only you can read it).")
    print("Publish a package with: tb publish")
    return 0


def cmd_logout(args: list[str]) -> int:
    if args:
        raise PkgError(f"I don't know the option '{args[0]}' for tb logout.")
    base = registry_base()
    data = _read_credentials()
    if base in data:
        del data[base]
        _write_credentials(data)
        print(f"Forgot the token for {base}. Revoke it on {site_url(base)}/packages/publish if you won't use it again.")
    else:
        print(f"There's no stored token for {base}.")
    if os.environ.get("TB_TOKEN"):
        print("Note: TB_TOKEN is still set in your environment.")
    return 0


def cmd_search(args: list[str]) -> int:
    query = " ".join(args).strip()
    if not query:
        raise PkgError("What should I look for? Like: tb search tables")
    index = load_index()
    if any(isinstance(e, dict) and isinstance(e.get("versions"), dict) for e in index.values()):
        results = _api("GET", "/search?" + urllib.parse.urlencode({"q": query})).get("results", [])
    else:
        terms = query.lower().split()
        results = [dict(name=n, **e) for n, e in sorted(index.items()) if isinstance(e, dict) and all(
            t in n.lower() or t in str(e.get("description", "")).lower() or t in [str(k) for k in e.get("keywords", [])]
            for t in terms)]
    if not results:
        print(f"No packages match '{query}'. See everything with: tb packages --index")
        return 0
    width = max(len(r["name"]) for r in results)
    for r in results:
        downloads = r.get("downloads")
        count = f"  {downloads} downloads" if isinstance(downloads, int) else ""
        print(f"  {r['name'].ljust(width)}  {str(r.get('version') or '').ljust(8)}  {r.get('description', '')}{count}".rstrip())
    print("\nInstall one with: tb add <name>   More about one: tb info <name>")
    return 0


def cmd_info(args: list[str]) -> int:
    if len(args) != 1:
        raise PkgError("Which package? Like: tb info tb-tables")
    name = args[0]
    if not NAME_RE.match(name):
        raise PkgError(f"'{name}' isn't a package name.")
    index = load_index()
    entry = index.get(name)
    registry = isinstance(entry, dict) and isinstance(entry.get("versions"), dict)
    if entry is None and not any(isinstance(e, dict) and isinstance(e.get("versions"), dict) for e in index.values()):
        raise PkgError(f"I couldn't find a package called '{name}' in the index at {index_url()}.")
    if entry is not None and not registry:
        print(f"{name} {entry.get('version', '')}".rstrip())
        print(f"  {entry.get('description', '')}")
        print(f"  source: {entry.get('git') or entry.get('zip') or '?'}")
        print(f"\nInstall: tb add {name}")
        return 0
    data = _api("GET", f"/packages/{urllib.parse.quote(name)}", token=stored_token())
    print(f"{data['name']} {data.get('latest') or ''}".rstrip())
    if data.get("description"):
        print(f"  {data['description']}")
    print(f"  owner: {data.get('owner', '?')}   license: {data.get('license') or 'not given'}   downloads: {data.get('downloads', 0)}")
    if data.get("keywords"):
        print(f"  keywords: {', '.join(data['keywords'])}")
    if data.get("status") and data["status"] != "approved":
        print(f"  status: {data['status']} (only you and the admins can see it)")
    print("  versions:")
    for v in data.get("versions", []):
        note = ""
        if v.get("status") == "yanked":
            note = "  yanked" + (f": {v['yankReason']}" if v.get("yankReason") else "")
        elif v.get("status") != "approved":
            note = f"  {v.get('status')}" + (f": {v['reason']}" if v.get("reason") else "")
        print(f"    {v['version'].ljust(12)} sha256 {v.get('sha256', '')[:16]}...  {v.get('downloads', 0)} downloads{note}")
    print(f"\nInstall: tb add {name}")
    return 0


def cmd_yank(args: list[str]) -> int:
    rest = list(args)
    undo = False
    reason = ""
    spec = None
    while rest:
        item = rest.pop(0)
        if item == "--undo":
            undo = True
        elif item == "--reason":
            if not rest:
                raise PkgError("--reason needs some text after it.")
            reason = rest.pop(0)
        elif item.startswith("-"):
            raise PkgError(f"I don't know the option '{item}' for tb yank.")
        elif spec is None:
            spec = item
        else:
            raise PkgError("Yank one version at a time.")
    if not spec or "@" not in spec:
        raise PkgError("Which version? Like: tb yank my-package@1.2.0 --reason \"breaks on Windows\"")
    name, _, version = spec.rpartition("@")
    if parse_version(version) is None:
        raise PkgError(f"'{version}' isn't a version like 1.2.3.")
    token = stored_token()
    if not token:
        raise PkgError("You're not signed in to the TB Package Index.", "Run: tb login")
    action = "unyank" if undo else "yank"
    body = json.dumps({"reason": reason}).encode("utf-8")
    result = _api("POST", f"/packages/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/{action}",
                  token=token, body=body, content_type="application/json")
    print(result.get("message") or f"{action} {name} {version}: done")
    if not undo:
        print("Ranges skip it now; projects that pin it exactly (or in tb.lock) still install it.")
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
    "login": cmd_login,
    "logout": cmd_logout,
    "search": cmd_search,
    "info": cmd_info,
    "yank": cmd_yank,
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
