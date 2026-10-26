"""Assemble the TB website into a folder you can serve as static files.

Usage:
    uv run python scripts/fetch_pyodide.py          # once, downloads Pyodide
    uv run python scripts/build_site.py [--out dist/site]
    uv run python -m http.server -d dist/site 8000  # then open http://localhost:8000

What it does:
  * copies site/ (pages, assets, vendored Pyodide) to --out
  * copies tb.py and welcome.tb to the site root
  * copies examples/*.tb into <out>/examples/ (plus welcome.tb)
  * writes <out>/examples/index.json from the "# Title:" / "# About:" header
    comments of each example (welcome first)
  * writes <out>/assets/js/config.js with the version; downloads are
    site-relative /dl/<asset> links (a rewrite on the host serves them)
  * copies the standard library (tblib/*.py, *.tb) and writes tblib/index.json,
    so the browser playground can `import` every library
  * copies apps/data/ (sample logs, the phishing email, Sigma rules) and writes
    apps/data/index.json; the playground loads these into its pretend disk
  * copies every app's source (apps/<name>/) so the site can link to it, and
    fills the Apps gallery on examples.html from apps/*/main.tb + README.md
  * adds the security examples in scripts/playground_examples/ to the playground
  * generates the library reference (library.html, lib/<name>.html) with
    scripts/gen_lib_docs.py

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
EXAMPLES = ROOT / "examples"
TBLIB = ROOT / "tblib"
APPS = ROOT / "apps"
SCRIPTS = Path(__file__).resolve().parent
PLAYGROUND_EXAMPLES = SCRIPTS / "playground_examples"
APPS_MARKER = "<!-- APPS GALLERY (filled in by scripts/build_site.py) -->"
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)

REQUIRED_PYODIDE = [
    "pyodide.js",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]


def fail(message: str) -> None:
    print(f"build_site: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_header(path: Path) -> tuple[str | None, str | None]:
    """Return (title, about) from the leading comment lines of a .tb file."""
    title = about = None
    for line in path.read_text(encoding="utf-8").splitlines()[:10]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            if stripped:
                break
            continue
        text = stripped.lstrip("#").strip()
        low = text.lower()
        if low.startswith("title:") and title is None:
            title = text[len("title:"):].strip()
        elif low.startswith("about:") and about is None:
            about = text[len("about:"):].strip()
    return title, about


def example_entry(name: str, path: Path) -> dict:
    title, about = read_header(path)
    if name == "welcome":
        title = title or "Welcome to TB"
        about = about or "A short tour of TB: variables, lists, loops, functions, maps and care()."
    return {
        "name": name,
        "title": title or name.replace("_", " ").capitalize(),
        "description": about or "",
        "interactive": "ask(" in path.read_text(encoding="utf-8"),
    }


def copy_tblib(out: Path) -> list[str]:
    """Copy tblib's .py and .tb files; return their names for tblib/index.json."""
    dest = out / "tblib"
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for path in sorted(TBLIB.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".tb") and "__pycache__" not in path.parts:
            rel = path.relative_to(TBLIB).as_posix()
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest / rel)
            names.append(rel)
    (dest / "index.json").write_text(json.dumps(names, indent=2) + "\n", encoding="utf-8")
    return names


def copy_app_data(out: Path) -> list[str]:
    src = APPS / "data"
    dest = out / "apps" / "data"
    names = []
    if not src.is_dir():
        return names
    for path in sorted(src.rglob("*")):
        if path.is_file() and path.name != ".DS_Store":
            rel = path.relative_to(src).as_posix()
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest / rel)
            names.append(rel)
    (dest / "index.json").write_text(json.dumps(names, indent=2) + "\n", encoding="utf-8")
    return names


# Files an app keeps for its user (to-do lists, journals) are not published.
APP_PRIVATE = {"todo.json", "journal.json", "notes.txt"}


def copy_app_sources(out: Path) -> list[str]:
    names = []
    if not APPS.is_dir():
        return names
    for folder in sorted(APPS.iterdir()):
        if not folder.is_dir() or folder.name == "data" or not (folder / "main.tb").exists():
            continue
        for path in sorted(folder.rglob("*")):
            if (path.is_file() and path.name not in APP_PRIVATE and path.name != ".DS_Store"
                    and "__pycache__" not in path.parts and path.suffix in (".tb", ".md", ".json", ".yml", ".txt", ".log", ".csv")):
                rel = path.relative_to(APPS)
                (out / "apps" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out / "apps" / rel)
        names.append(folder.name)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the TB website.")
    parser.add_argument("--repo", default=None, help=argparse.SUPPRESS)  # ignored; kept so old commands still work
    parser.add_argument("--out", default="dist/site", help="output folder (default: dist/site)")
    args = parser.parse_args(argv)

    if not SITE.is_dir():
        fail(f"{SITE} not found")
    vendor = SITE / "vendor" / "pyodide"
    missing = [name for name in REQUIRED_PYODIDE if not (vendor / name).is_file()]
    if missing:
        fail(
            "Pyodide is not vendored yet (missing: " + ", ".join(missing) + ").\n"
            "  Run:  uv run python scripts/fetch_pyodide.py"
        )

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if out.resolve() in (ROOT.resolve(), SITE.resolve()):
        fail("--out must be a separate build folder")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out, ignore=shutil.ignore_patterns(".DS_Store", "*.part"))

    shutil.copy2(ROOT / "tb.py", out / "tb.py")
    lib_files = copy_tblib(out)
    data_files = copy_app_data(out)
    app_names = copy_app_sources(out)
    shutil.copy2(ROOT / "welcome.tb", out / "welcome.tb")

    ex_out = out / "examples"
    ex_out.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "welcome.tb", ex_out / "welcome.tb")
    entries = [example_entry("welcome", ROOT / "welcome.tb")]
    extra = []
    if PLAYGROUND_EXAMPLES.is_dir():
        for path in sorted(PLAYGROUND_EXAMPLES.glob("*.tb")):
            shutil.copy2(path, ex_out / path.name)
            extra.append(example_entry(path.stem, path))
    if EXAMPLES.is_dir():
        # "hello" is the natural first lesson, so it comes right after welcome.
        for path in sorted(EXAMPLES.glob("*.tb"), key=lambda p: (p.stem != "hello", p.stem)):
            if path.stem == "welcome":
                continue
            shutil.copy2(path, ex_out / path.name)
            entries.append(example_entry(path.stem, path))
            if path.stem == "hello":
                entries.extend(extra)  # the security examples come right after hello
                extra = []
    entries.extend(extra)
    (ex_out / "index.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    match = VERSION_RE.search((ROOT / "tb.py").read_text(encoding="utf-8"))
    version = match.group(1) if match else "2.0.1"

    sys.path.insert(0, str(SCRIPTS))
    import gen_lib_docs  # noqa: E402  (imports tb, so only after the checks above)
    import apps_gallery  # noqa: E402
    lib_pages = gen_lib_docs.build(out, version)
    gallery_page = out / "examples.html"
    if gallery_page.exists():
        page = gallery_page.read_text(encoding="utf-8")
        if APPS_MARKER in page:
            page = page.replace(APPS_MARKER, apps_gallery.render(APPS, out))
            gallery_page.write_text(page, encoding="utf-8")
        else:
            print("  note: examples.html has no apps gallery marker; skipped the gallery")
    config_path = out / "assets" / "js" / "config.js"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "// Generated by scripts/build_site.py from tb.__version__.\n"
        "// Downloads are served from /dl/<asset> on this site.\n"
        f"window.TB_CONFIG = {{ version: {json.dumps(version)}, downloads: \"/dl/\" }};\n",
        encoding="utf-8",
    )

    print(f"Built site in {out}")
    print(f"  {len(entries)} example(s): " + ", ".join(e["name"] for e in entries))
    print(f"  standard library: {len(lib_files)} files; sample data: {len(data_files)} files")
    print(f"  apps: {', '.join(app_names) or 'none'}; library pages: {len(lib_pages)}")
    print("  downloads point at /dl/<asset> (TB " + version + ")")
    print(f"Serve it with:  uv run python -m http.server -d {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
