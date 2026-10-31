"""The site-wide search: a static index built from the finished pages.

build_site.py calls build(out, version) last. It reads the HTML it wrote
(the pages at the root, lib/, book/ and cookbook/; not the /v/<x>/ copies of
older docs) and writes:

  search-index.json  [{"t": title, "u": url, "p": page, "k": kind, "x": text}]
                     one entry per page and per section (h2/h3 with an id,
                     or a <section>/<article> with an id); library functions
                     are the lib/<name>.html cards, titled name.function(...)
  search.html        the results page (assets/js/search.js fills it; the
                     header's search box opens the same search in place)

No third-party service: the browser fetches the JSON from this site.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import site_shell as shell

FOLDERS = ("", "lib", "book", "cookbook")
SKIP_PAGES = {"search.html", "account.html", "admin.html", "publish.html", "404.html"}
SKIP_TAGS = {"nav", "aside", "pre", "script", "style", "details", "button", "svg", "table", "header", "footer"}
SKIP_CLASSES = ("sig-returns", "doc-version")  # "returns list", "TB 3.x (current)": noise in a snippet
SNIPPET = 140


def kind_of(url: str) -> str:
    if url.startswith("lib/") or url == "library.html":
        return "Library"
    if url.startswith("book/"):
        return "Book"
    if url.startswith("cookbook/"):
        return "Cookbook"
    if url.startswith("guide-"):
        return "Tutorial"
    return "Page"


class _Page(HTMLParser):
    """Collects (anchor, heading, following text) inside <main>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.in_title = self.in_main = False
        self.skip = 0
        self.pending_id = ""
        self.heading: list | None = None  # [level, id, text]
        self.sections: list[list] = []     # [level, id, heading, text]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title" and not self.title:  # the page's <title>, not an SVG's
            self.in_title = True
        if tag == "main":
            self.in_main = True
        if not self.in_main:
            return
        if self.skip or tag in SKIP_TAGS or any(c in (a.get("class") or "") for c in SKIP_CLASSES):
            self.skip += tag not in ("br", "img", "input", "hr", "meta", "link")
            return
        if tag in ("section", "article") and a.get("id"):
            self.pending_id = a["id"]
        if tag in ("h1", "h2", "h3"):
            hid = a.get("id") or self.pending_id
            self.pending_id = ""
            self.heading = [int(tag[1]), hid if tag != "h1" else "", ""]

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "main":
            self.in_main = False
        if not self.in_main:
            return
        if self.skip:
            if tag not in ("br", "img", "input", "hr", "meta", "link"):
                self.skip -= 1
            return
        if self.heading and tag == f"h{self.heading[0]}":
            level, hid, text = self.heading
            text = re.sub(r"^(\d+\.)(?=\S)", r"\1 ", " ".join(text.split()))  # "5.Defang" -> "5. Defang"
            self.sections.append([level, hid, text, ""])
            self.heading = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.in_main or self.skip:
            return
        if self.heading:
            self.heading[2] += data
        elif self.sections and len(self.sections[-1][3]) < SNIPPET * 2:
            self.sections[-1][3] += " " + data


def _cut(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= SNIPPET:
        return text
    return text[:SNIPPET].rsplit(" ", 1)[0] + "..."


def entries_for(path: Path, url: str) -> list[dict]:
    parser = _Page()
    # The "Visualize" pictures are most of a library page and never searched: drop them first (faster).
    parser.feed(re.sub(r"<svg\b.*?</svg>", "", path.read_text(encoding="utf-8"), flags=re.S))
    page = re.sub(r"\s*·\s*TB( library)?$", "", " ".join(parser.title.split()))
    kind = kind_of(url)
    found = []
    for level, hid, heading, text in parser.sections:
        if not heading:
            continue
        if level == 1:
            found.append({"t": heading, "u": url, "p": page, "k": kind, "x": _cut(text)})
        elif hid:
            title = heading
            if url.startswith("lib/") and hid.startswith("fn-"):
                title = f"{url[4:-5]}.{heading}"
            found.append({"t": title, "u": f"{url}#{hid}", "p": page, "k": kind, "x": _cut(text)})
    return found


def collect(out: Path) -> list[dict]:
    entries = []
    for folder in FOLDERS:
        base = out / folder if folder else out
        for path in sorted(base.glob("*.html")):
            if path.name in SKIP_PAGES or path.name.startswith("packages"):
                continue
            url = f"{folder}/{path.name}" if folder else path.name
            entries.extend(entries_for(path, url))
    return entries


def search_page(version: str) -> str:
    main = """    <div class="container narrow search-page">
      <h1>Search</h1>
      <form class="search-form" role="search" action="search.html" method="get">
        <label for="search-page-q">Search the guide, the book, the cookbook and the library</label>
        <div class="search-row">
          <input id="search-page-q" name="q" type="search" autocomplete="off" spellcheck="false" placeholder="for example: defang, sigma, csv.read">
          <button class="button" type="submit">Search</button>
        </div>
      </form>
      <p class="search-status" id="search-page-status" role="status" aria-live="polite"></p>
      <ol class="search-results-page" id="search-page-results"></ol>
    </div>"""
    return shell.page("Search · TB", "Search the TB guide, book, cookbook and library reference.", main,
                      version=version)


def build(out: Path, version: str) -> int:
    entries = collect(out)
    (out / "search-index.json").write_text(
        json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (out / "search.html").write_text(search_page(version), encoding="utf-8")
    return len(entries)
