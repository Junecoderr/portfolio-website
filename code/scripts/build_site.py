"""Assemble the TB website into a folder you can serve as static files.

Usage:
    uv run python scripts/fetch_pyodide.py          # once, downloads Pyodide
    uv run python scripts/build_site.py [--out dist/site]
    uv run python scripts/serve_site.py   # then open http://localhost:8000 (Vercel's headers)

What it does:
  * copies site/ (pages, assets, vendored Pyodide) to --out
  * copies tb.py and welcome.tb to the site root, and the language itself
    (tbcore/: lexer, parser, compiler, VM, ...) with tbcore/index.json (the
    files listed in tbcore/__init__.py FILES plus any other tbcore/*.py), so
    the playground can load it
  * copies examples/*.tb into <out>/examples/ (plus welcome.tb)
  * writes <out>/examples/index.json from the "# Title:" / "# About:" header
    comments of each example (welcome first)
  * writes <out>/assets/js/config.js with the version; downloads are
    site-relative /dl/<asset> links (a rewrite on the host serves them)
  * copies the standard library (tblib/*.py, *.tb and tblib/data/) and writes
    tblib/index.json, so the browser playground can `import` every library
  * copies apps/data/ (sample logs, the phishing email, Sigma rules) and writes
    apps/data/index.json; the playground loads these into its pretend disk
  * copies every app's source (apps/<name>/) so the site can link to it, and
    fills the Apps gallery on examples.html from apps/*/main.tb + README.md
  * adds the security examples in scripts/playground_examples/ to the playground
  * generates the library reference (library.html, lib/<name>.html) with
    scripts/gen_lib_docs.py
  * renders spec.html (spec/SPEC.md) and migration.html (MIGRATION-3.0.md)
    with scripts/md2html.py
  * writes the TB Package Index pages (packages.html, packages/view.html,
    publish.html, account.html, admin.html, bundled.json) with
    scripts/registry_pages.py; the API behind them is api/registry.js
  * writes releases.html and the releases.xml feed from CHANGELOG.md with
    scripts/changelog.py (the newest release there must match tb.py)
  * zips every example package (packages/<name>/) into packages/<name>.zip and
    copies packages/index.json, the static index TB 3.0.0's `tb add` reads
  * refuses to build if api/_lib/registry/seed.js (the bundled packages the
    API serves) is out of date: run scripts/gen_registry_seed.py
  * refuses to finish if a served text file mentions github.com (downloads
    and links stay on this site)

Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
EXAMPLES = ROOT / "examples"
TBLIB = ROOT / "tblib"
TBCORE = ROOT / "tbcore"
PACKAGES = ROOT / "packages"
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


def tbcore_files() -> list[str]:
    """The files of tbcore/: FILES from tbcore/__init__.py (the documented
    list), plus any other tbcore/*.py so a new module is never left out."""
    listed: list[str] = []
    try:
        tree = ast.parse((TBCORE / "__init__.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "FILES" for t in node.targets):
                listed = [str(x) for x in ast.literal_eval(node.value)]
    except (OSError, SyntaxError, ValueError):
        listed = []
    found = sorted(p.name for p in TBCORE.glob("*.py"))
    names = [n for n in listed if (TBCORE / n).is_file()]
    names += [n for n in found if n not in names]
    if "__init__.py" not in names:
        fail("tbcore/__init__.py is missing")
    return names


def copy_tbcore(out: Path) -> list[str]:
    dest = out / "tbcore"
    dest.mkdir(parents=True, exist_ok=True)
    names = tbcore_files()
    for name in names:
        shutil.copy2(TBCORE / name, dest / name)
    (dest / "index.json").write_text(json.dumps(names, indent=2) + "\n", encoding="utf-8")
    return names


def copy_tblib(out: Path) -> list[str]:
    """Copy tblib's .py and .tb files and tblib/data/; return their names for tblib/index.json."""
    dest = out / "tblib"
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for path in sorted(TBLIB.rglob("*")):
        rel_parts = path.relative_to(TBLIB).parts
        data_file = rel_parts[0] == "data" and path.name != ".DS_Store"
        if path.is_file() and (path.suffix in (".py", ".tb") or data_file) and "__pycache__" not in path.parts:
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


def build_packages(out: Path) -> list[str]:
    """Zip packages/<name>/ into <out>/packages/<name>.zip (one top folder,
    <name>/, which tb add strips) and copy packages/index.json (the static
    index TB 3.0.0 reads). The zips are byte-identical to the bundled archives
    the package index API serves (scripts/gen_registry_seed.py)."""
    import gen_registry_seed
    dest = out / "packages"
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for folder in gen_registry_seed.example_packages():
        (dest / f"{folder.name}.zip").write_bytes(gen_registry_seed.package_zip(folder))
        names.append(folder.name)
    if (PACKAGES / "index.json").exists():
        shutil.copy2(PACKAGES / "index.json", dest / "index.json")
    return names


def sectionize(body: str) -> str:
    """Wrap each <h2 id=X> and what follows it in <section id=X>, the shape
    docs.js reads for its "On this page" list."""
    parts = re.split(r'(<h2 id="[^"]+">)', body)
    out = [parts[0]]
    for k in range(1, len(parts), 2):
        hid = re.search(r'id="([^"]+)"', parts[k]).group(1)
        out.append(f'<section id="{hid}">\n<h2>' + parts[k + 1].rstrip() + "\n</section>\n")
    return "".join(out)


def _docs_page(title: str, description: str, h1: str, lead: str, body: str, headings, version: str,
               *, current: str = "", aside_label: str = "Contents", min_level: int = 2) -> str:
    import site_shell as shell
    items = []
    for level, hid, text in headings:
        if level == min_level:
            items.append(f'            <li><a href="#{hid}">{html.escape(text, quote=False)}</a></li>')
    chevron = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
               'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    main = f"""    <div class="container docs">
      <aside class="docs-sidebar" aria-label="{aside_label}">
        <button class="docs-nav-toggle" type="button" aria-expanded="false" aria-controls="docs-nav">{aside_label} {chevron}</button>
        <div class="docs-sidebar-inner" id="docs-nav">
          <h2>{html.escape(h1, quote=False)}</h2>
          <ul>
{chr(10).join(items)}
          </ul>
        </div>
      </aside>
      <article class="docs-main md-page">
        <h1>{html.escape(h1, quote=False)}</h1>
        <p class="lead">{lead}</p>
{sectionize(body)}
      </article>
      <nav class="on-this-page" aria-label="On this page">
        <div class="on-this-page-inner">
          <h2>On this page</h2>
          <ul id="on-this-page-list"></ul>
        </div>
      </nav>
    </div>"""
    return shell.page(title, description, main, current=current, scripts=("highlight.js", "docs.js", "runner.js"),
                      version=version)


def render_spec(out: Path, version: str) -> None:
    import md2html
    text = (ROOT / "spec" / "SPEC.md").read_text(encoding="utf-8")
    body, _title, headings = md2html.convert(text, drop_title=True)
    lead = (f"The formal definition of TB {html.escape(version)}: lexical structure, grammar, values, statements, "
            "classes, errors, generators, patterns and formatting. For a gentle introduction, start with the "
            '<a href="docs.html">guide</a>.')
    page = _docs_page("Language specification · TB", f"The TB {version} language specification.",
                      "Language specification", lead, body, headings, version)
    (out / "spec.html").write_text(page, encoding="utf-8")


def render_migration(out: Path, version: str) -> None:
    import md2html
    text = (ROOT / "MIGRATION-3.0.md").read_text(encoding="utf-8")
    text = text.split("\n## Updated in this repository", 1)[0]  # notes for the project itself
    body, title, headings = md2html.convert(text, drop_title=True)
    body = body.replace(
        'Everything new is described in <code>README.md</code> ("New in 3.0") and the contract is <code>DESIGN-3.0.md</code>.',
        'Everything new is on <a href="whats-new.html">What\'s new in 3.0</a>, and the exact rules are in the '
        '<a href="spec.html">language specification</a>.')
    lead = "What can break when you move a TB 2.x program to TB 3.0, and how to fix it."
    page = _docs_page("Moving from TB 2.x to 3.0 · TB", "How to move a TB 2.x program to TB 3.0: new keywords, numbers, typed errors and other changes.",
                      title or "Moving from TB 2.x to TB 3.0", lead, body, headings, version)
    (out / "migration.html").write_text(page, encoding="utf-8")


SERVED_TEXT = (".html", ".xml", ".js", ".css", ".json", ".tb", ".md", ".txt", ".yml", ".toml", ".csv", ".log")


def check_no_github(out: Path) -> None:
    """Downloads and links stay on this site: no served text mentions github.com."""
    bad = []
    for path in out.rglob("*"):
        if not path.is_file() or "vendor" in path.relative_to(out).parts or path.suffix not in SERVED_TEXT:
            continue
        if "github.com" in path.read_text(encoding="utf-8", errors="replace").lower():
            bad.append(path.relative_to(out).as_posix())
    if bad:
        fail("these files would be served but mention github.com (the site never links to the "
             "source repository): " + ", ".join(sorted(bad)))


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

    sys.path.insert(0, str(SCRIPTS))
    import gen_registry_seed  # noqa: E402
    if not gen_registry_seed.SEED_JS.exists() or gen_registry_seed.SEED_JS.read_text(encoding="utf-8") != gen_registry_seed.render():
        fail("api/_lib/registry/seed.js is out of date (packages/ or the library list changed).\n"
             "  Run:  uv run python scripts/gen_registry_seed.py")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if out.resolve() in (ROOT.resolve(), SITE.resolve()):
        fail("--out must be a separate build folder")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out, ignore=shutil.ignore_patterns(".DS_Store", "*.part"))

    shutil.copy2(ROOT / "tb.py", out / "tb.py")
    core_files = copy_tbcore(out)
    lib_files = copy_tblib(out)
    package_names = build_packages(out)
    data_files = copy_app_data(out)
    app_names = copy_app_sources(out)
    shutil.copy2(ROOT / "welcome.tb", out / "welcome.tb")

    ex_out = out / "examples"
    ex_out.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "welcome.tb", ex_out / "welcome.tb")
    entries = [example_entry("welcome", ROOT / "welcome.tb")]
    extra = []
    if PLAYGROUND_EXAMPLES.is_dir():
        for path in sorted(PLAYGROUND_EXAMPLES.glob("*.tb"), key=lambda p: (p.stem != "tb3_tour", p.stem)):
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
    version = match.group(1) if match else "3.1.0"

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
    render_spec(out, version)
    render_migration(out, version)
    import registry_pages  # noqa: E402
    registry_pages.render(out, version)
    import changelog  # noqa: E402
    changelog.render(out, version)
    config_path = out / "assets" / "js" / "config.js"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "// Generated by scripts/build_site.py from tb.__version__.\n"
        "// Downloads are served from /dl/<asset> on this site.\n"
        f"window.TB_CONFIG = {{ version: {json.dumps(version)}, downloads: \"/dl/\" }};\n",
        encoding="utf-8",
    )

    check_no_github(out)
    print(f"Built site in {out}")
    print(f"  {len(entries)} example(s): " + ", ".join(e["name"] for e in entries))
    print(f"  language (tbcore): {len(core_files)} files; standard library: {len(lib_files)} files; sample data: {len(data_files)} files")
    print(f"  packages: {', '.join(package_names) or 'none'}")
    print(f"  apps: {', '.join(app_names) or 'none'}; library pages: {len(lib_pages)}")
    print("  downloads point at /dl/<asset> (TB " + version + ")")
    print(f"Serve it with:  uv run python scripts/serve_site.py --dir {(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
