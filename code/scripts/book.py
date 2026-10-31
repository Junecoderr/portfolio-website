"""The TB book and the SOC cookbook: Markdown in docs/<version>/ -> HTML pages.

    docs/3.x/book/index.md          the book's front page (intro)
    docs/3.x/book/01-first-program.md, 02-...   chapters, in file name order
    docs/3.x/cookbook/index.md      the cookbook's front page
    docs/3.x/cookbook/01-parse-auth-logs.md ... recipes

Code blocks: ```tb runs (a Run button on the page; tests/test_book.py runs it
on both engines), ```tb desktop runs in the tests but needs the TB app (no Run
button), ```tb norun is only checked (network, windows). Link to other pages
with [text](site:lib/ioc.html) (a path from the site root).

Versions: each folder docs/<major>.x/ is one version of the docs. The one that
matches tb.__version__ is current and is written at /book/ and /cookbook/.
Every version is also written at /v/<major>.x/book/ ..., so a link to a
version keeps working after the next major release takes over /book/.

The library pages link to the tutorials that use them (tutorial_map): the
current version's chapters and recipes, and site/guide-*.html.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

import md2html
import site_shell as shell

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
DOCS = ROOT / "docs"
SITE = ROOT / "site"
KINDS = {"book": ("The TB book", "Chapters"), "cookbook": ("SOC cookbook", "Recipes")}
FENCE_RE = re.compile(r"^```tb[ \t]*(\w*)[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
IMPORT_RE = re.compile(r"^\s*(?:import\s+(\w+)|from\s+(\w+)\s+import)\b", re.M)


@dataclass
class Doc:
    kind: str
    slug: str      # "index" for the front page
    title: str
    text: str      # the Markdown
    path: Path
    blurb: str     # the first paragraph, as plain text


def versions() -> list[str]:
    """The docs versions, newest first: ["3.x"] (later ["4.x", "3.x"])."""
    found = [p.name for p in DOCS.glob("*.x") if p.is_dir() and p.name[:-2].isdigit()]
    return sorted(found, key=lambda v: -int(v[:-2]))


def current_version(version: str) -> str:
    """The docs version for a TB version: "3.2.0" -> "3.x"."""
    return version.split(".")[0] + ".x"


def _plain(md: str) -> str:
    md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    return re.sub(r"[`*]", "", " ".join(md.split()))


def load(kind: str, ver: str) -> list[Doc]:
    """The front page first, then the chapters (or recipes) in file order."""
    folder = DOCS / ver / kind
    files = [folder / "index.md"] + sorted(p for p in folder.glob("*.md") if p.name != "index.md")
    docs = []
    for path in files:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        title = re.search(r"^# (.+)$", text, re.M)
        if not title:
            raise SystemExit(f"book: {path.relative_to(ROOT).as_posix()} has no '# Title' line")
        body = text[title.end():].strip().split("\n\n")
        blurb = next((_plain(p) for p in body if p and not p.startswith(("#", "```", "-", "|"))), "")
        slug = "index" if path.name == "index.md" else re.sub(r"^\d+-", "", path.stem)
        docs.append(Doc(kind, slug, title.group(1).strip(), text, path, blurb))
    return docs


def code_blocks(text: str) -> list[tuple[str, str]]:
    """[(mode, code)] for every ```tb block; mode is "", "desktop" or "norun"."""
    return [(m.group(1), m.group(2)) for m in FENCE_RE.finditer(text.replace("\r\n", "\n"))]


def imports(code: str) -> set[str]:
    return {a or b for a, b in IMPORT_RE.findall(code)}


def _guides() -> list[tuple[str, str, str]]:
    """[(title, url, all TB code)] for site/guide-*.html."""
    found = []
    for page in sorted(SITE.glob("guide-*.html")):
        text = page.read_text(encoding="utf-8")
        title = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S).group(1)
        code = "\n".join(html.unescape(c) for c in re.findall(r'<pre class="tb-code"[^>]*><code>(.*?)</code></pre>', text, re.S))
        found.append((re.sub(r"<[^>]+>", "", title).strip(), page.name, code))
    return found


def tutorial_map(version: str, libraries: list[str], limit: int = 8) -> dict[str, list[tuple[str, str]]]:
    """{library: [(title, url from the site root)]}: the chapters, recipes and
    guides whose code imports the library (at most `limit` each)."""
    ver = current_version(version)
    sources = []
    for kind in KINDS:
        for doc in load(kind, ver)[1:]:
            code = "\n".join(c for _, c in code_blocks(doc.text))
            sources.append((doc.title, f"{kind}/{doc.slug}.html", code))
    sources += _guides()
    found: dict[str, list[tuple[str, str]]] = {lib: [] for lib in libraries}
    for title, url, code in sources:
        for lib in sorted(imports(code)):
            if lib in found and len(found[lib]) < limit:
                found[lib].append((title, url))
    return found


def missing_tutorials(tutorials: dict[str, list]) -> list[str]:
    return sorted(lib for lib, links in tutorials.items() if not links)


CHEVRON = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
           'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')


def _version_note(kind: str, ver: str, current: str, all_versions: list[str], prefix: str) -> str:
    others = []
    for v in all_versions:
        if v == ver:
            continue
        href = f"{prefix}{kind}/index.html" if v == current else f"{prefix}v/{v}/{kind}/index.html"
        others.append(f'<a href="{href}">TB {v}</a>')
    label = f"TB {ver}" + (" (current)" if ver == current else "")
    more = f" Other versions: {', '.join(others)}." if others else ""
    return f'<p class="doc-version"><span class="version-tag">{label}</span> These pages are for TB {ver}.{more}</p>'


def render_doc(docs: list[Doc], i: int, ver: str, current: str, all_versions: list[str], version: str,
               base: str) -> str:
    """One page. base is "" (current, at /book/) or "v/3.x/" (a versioned copy)."""
    from build_site import sectionize
    doc = docs[i]
    kind_title, toc_label = KINDS[doc.kind]
    prefix = "../" * (base.count("/") + 1)
    here = f"{prefix}{base}{doc.kind}/"
    text = doc.text
    if base:  # links to chapters and recipes stay in this version
        text = re.sub(r"\(site:(book|cookbook)/", rf"(site:{base}\1/", text)
    body, title, headings = md2html.convert(text, drop_title=True, prefix=prefix, tb_blocks=True)
    items = []
    for n, d in enumerate(docs):
        label = html.escape(d.title, quote=False) if n else "Start here"
        cur = ' class="is-active" aria-current="page"' if n == i else ""
        items.append(f'            <li><a href="{here}{d.slug}.html"{cur}>{label}</a></li>')
    other = "cookbook" if doc.kind == "book" else "book"
    pager = []
    if i > 0:
        p = docs[i - 1]
        pager.append(f'<a class="pager-prev" rel="prev" href="{here}{p.slug}.html"><span>Previous</span>'
                     f'{html.escape(p.title if i > 1 else "Start here", quote=False)}</a>')
    if i + 1 < len(docs):
        nx = docs[i + 1]
        pager.append(f'<a class="pager-next" rel="next" href="{here}{nx.slug}.html"><span>Next</span>'
                     f'{html.escape(nx.title, quote=False)}</a>')
    crumb_tail = ""
    if i:
        unit = "Chapter" if doc.kind == "book" else "Recipe"
        crumb_tail = f' <span aria-hidden="true">/</span> <span aria-current="page">{unit} {i}</span>'
    listing = ""
    if i == 0:  # the front page lists every chapter or recipe
        rows = "\n".join(
            f'          <li><a href="{here}{d.slug}.html">{html.escape(d.title, quote=False)}</a>'
            f'<span>{html.escape(d.blurb, quote=False)}</span></li>' for d in docs[1:])
        listing = f'\n      <section id="contents">\n<h2>{toc_label}</h2>\n        <ol class="book-toc">\n{rows}\n        </ol>\n      </section>'
    # The current version is canonical at /book/; its /v/3.x/ copy points there.
    canonical = f"https://code.tanisha.ceo/{doc.kind}/{'' if i == 0 else doc.slug}" if ver == current else ""
    main = f"""    <div class="container docs">
      <aside class="docs-sidebar" aria-label="{toc_label}">
        <button class="docs-nav-toggle" type="button" aria-expanded="false" aria-controls="docs-nav">{toc_label} {CHEVRON}</button>
        <div class="docs-sidebar-inner" id="docs-nav">
          <h2>{kind_title}</h2>
          <ol class="book-nav">
{chr(10).join(items)}
          </ol>
          <h2>More to learn</h2>
          <ul>
            <li><a href="{prefix}{base}{other}/index.html">{KINDS[other][0]}</a></li>
            <li><a href="{prefix}docs.html#guides">Tutorials</a></li>
            <li><a href="{prefix}library.html">Library reference</a></li>
          </ul>
        </div>
      </aside>
      <article class="docs-main md-page">
        <nav class="breadcrumb" aria-label="Breadcrumb"><a href="{here}index.html">{kind_title}</a>{crumb_tail}</nav>
        <h1>{html.escape(title or doc.title, quote=False)}</h1>
        {_version_note(doc.kind, ver, current, all_versions, prefix)}
{sectionize(body)}{listing}
        <nav class="pager" aria-label="{toc_label}">{"".join(pager)}</nav>
      </article>
      <nav class="on-this-page" aria-label="On this page">
        <div class="on-this-page-inner">
          <h2>On this page</h2>
          <ul id="on-this-page-list"></ul>
        </div>
      </nav>
    </div>"""
    page_title = f"{doc.title} · {kind_title} · TB" if i else f"{kind_title} · TB"
    page = shell.page(page_title, doc.blurb or doc.title, main, current="docs.html", prefix=prefix,
                      scripts=("highlight.js", "docs.js", "runner.js"), version=version)
    if canonical:
        page = page.replace("</title>", f'</title>\n  <link rel="canonical" href="{canonical}">', 1)
    return page


def render(out: Path, version: str) -> list[Path]:
    """Write every version's book and cookbook; return the files written."""
    all_versions = versions()
    current = current_version(version)
    if current not in all_versions:
        raise SystemExit(f"book: docs/{current}/ is missing (TB is {version})")
    written = []
    for ver in all_versions:
        bases = [f"v/{ver}/"] + ([""] if ver == current else [])
        for kind in KINDS:
            docs = load(kind, ver)
            for base in bases:
                folder = out / base / kind
                folder.mkdir(parents=True, exist_ok=True)
                for i, doc in enumerate(docs):
                    path = folder / f"{doc.slug}.html"
                    path.write_text(render_doc(docs, i, ver, current, all_versions, version, base), encoding="utf-8")
                    written.append(path)
    return written
