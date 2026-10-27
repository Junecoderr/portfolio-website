"""Bring the hand-written pages in site/ in line with scripts/site_shell.py.

    uv run python scripts/sync_pages.py

* replaces each page's <header class="site-header"> and <footer
  class="site-footer"> with the shared markup (keeping the page's current
  nav item), for the version in tb.py
* makes sure each page loads assets/js/stats.js (the visit and download
  counters) in its <head>, right after theme.js
* fills the libraries grid on site/index.html (between the LIBRARIES GRID
  markers) from tb.list_libraries() and the groups in gen_lib_docs.py, and
  the "N libraries" counts marked with data-lib-count

tests/test_site.py checks the result, so run this after changing the shell,
adding a library or bumping the version. Standard library only.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

import tb  # noqa: E402
import gen_lib_docs  # noqa: E402
import site_shell  # noqa: E402

GRID_START = "<!-- LIBRARIES GRID (scripts/sync_pages.py) -->"
GRID_END = "<!-- /LIBRARIES GRID -->"
HEADER_RE = re.compile(r'  <header class="site-header">.*?</header>', re.S)
FOOTER_RE = re.compile(r'  <footer class="site-footer">.*?</footer>', re.S)
CURRENT_RE = re.compile(r'<a href="([\w.-]+)"(?: class="nav-cta")? aria-current="page"')
COUNT_RE = re.compile(r'(<span data-lib-count>)\d+(</span>)')
THEME_TAG = '  <script src="assets/js/theme.js"></script>\n'


def libraries_grid() -> str:
    rows = []
    for _key, title, _desc, names in gen_lib_docs.all_groups():
        for name in names:
            doc = gen_lib_docs.first_sentence(tb.describe_library(name)["doc"])
            rows.append(
                f'          <li><a class="lib-card" href="lib/{name}.html"><span class="lib-card-name"><code>{name}</code></span>'
                f'<span class="lib-card-doc">{html.escape(doc, quote=False)}</span>'
                f'<span class="lib-card-meta"><span>{html.escape(title, quote=False)}</span></span></a></li>')
    return GRID_START + "\n" + "\n".join(rows) + "\n          " + GRID_END


def sync_page(path: Path, version: str) -> bool:
    text = path.read_text(encoding="utf-8")
    before = text
    m = CURRENT_RE.search(text)
    current = m.group(1) if m else ""
    text = HEADER_RE.sub(lambda _: site_shell.header(current, "", version), text, count=1)
    text = FOOTER_RE.sub(lambda _: site_shell.footer("", version), text, count=1)
    stats = site_shell.stats_script("")
    if stats not in text and THEME_TAG in text:
        text = text.replace(THEME_TAG, f"{THEME_TAG}  {stats}\n", 1)
    if GRID_START in text and GRID_END in text:
        start = text.index(GRID_START)
        end = text.index(GRID_END) + len(GRID_END)
        text = text[:start] + libraries_grid() + text[end:]
    text = COUNT_RE.sub(lambda m: f"{m.group(1)}{len(tb.list_libraries())}{m.group(2)}", text)
    if text != before:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    changed = [p.name for p in sorted(SITE.glob("*.html")) if sync_page(p, tb.__version__)]
    print("updated: " + (", ".join(changed) if changed else "nothing (already in sync)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
