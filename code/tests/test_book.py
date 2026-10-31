"""The TB book, the SOC cookbook, versioned docs and the site-wide search.

* every ```tb block in docs/<version>/book/ and cookbook/ passes `tb check`
  with no errors (next to apps/data/ and the example packages); ```tb and ```tb desktop blocks also run, with the same
  output on the bytecode VM and the tree-walker, using only the local
  fixtures in apps/data/ (```tb norun blocks need the network or windows)
* every library page links to at least one tutorial, and the build stops
  when one does not
* the pages have a table of contents, prev/next links, a version label and
  the shared shell; a later docs version takes over /book/ while /v/3.x/
  keeps working
* the search index covers pages, book sections, recipes and library
  functions, and search.js is an external file (the CSP allows no inline JS)
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys

import pytest

from conftest import ROOT

import tb
from tbcore.types import check_source

SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import book  # noqa: E402
import gen_lib_docs  # noqa: E402
import name_guard  # noqa: E402
import search_index  # noqa: E402
import site_shell  # noqa: E402

BLOCKS = [
    pytest.param(doc.path, n, mode, code, id=f"{ver}/{kind}/{doc.path.stem}#{n}")
    for ver in book.versions() for kind in book.KINDS for doc in book.load(kind, ver)
    for n, (mode, code) in enumerate(book.code_blocks(doc.text))
]


def fixtures(folder):
    """A fresh folder like the playground's pretend disk (apps/data/), plus the
    example packages in tb_packages/ (as `tb add` would put them)."""
    shutil.copytree(ROOT / "apps" / "data", folder / "apps" / "data")
    for pkg in (ROOT / "packages").iterdir():
        if pkg.is_dir():
            shutil.copytree(pkg, folder / "tb_packages" / pkg.name)
    return folder


def check_errors(code: str, folder) -> list[str]:
    """`tb check` on the block as a file in folder (so its imports resolve)."""
    return [d.format("block") for d in check_source(code, str(folder / "block.tb")) if d.severity == "error"]


def outcome(engine: str, source: str, folder):
    fixtures(folder)
    lines: list[str] = []
    interp = tb.engine_class(engine)(output=lines.append, input_fn=lambda prompt: "3", rng=random.Random(7))
    old = os.getcwd()
    os.chdir(folder)
    try:
        tb.run(source, interpreter=interp)
        return lines, None
    except tb.TBError as err:
        return lines, str(err)
    finally:
        os.chdir(old)


def test_there_is_a_book_and_a_cookbook():
    ver = book.current_version(tb.__version__)
    chapters = book.load("book", ver)[1:]
    recipes = book.load("cookbook", ver)[1:]
    assert len(chapters) >= 13 and len(recipes) >= 12
    for doc in chapters + recipes:
        assert book.code_blocks(doc.text), f"{doc.path.name} has no TB code"
        assert doc.blurb, f"{doc.path.name} has no first paragraph"


@pytest.mark.parametrize("path, n, mode, code", BLOCKS)
def test_code_block(path, n, mode, code, tmp_path):
    assert mode in ("", "desktop", "norun"), f"{path.name}: unknown block kind ```tb {mode}"
    if mode == "norun":
        errors = check_errors(code, fixtures(tmp_path))
        assert not errors, f"{path.name} block {n}: " + "; ".join(errors)
        return
    vm = outcome("vm", code, tmp_path / "vm")
    tree = outcome("tree", code, tmp_path / "tree")
    errors = check_errors(code, tmp_path / "vm")  # after the run: files the block wrote are there
    assert not errors, f"{path.name} block {n}: " + "; ".join(errors)
    assert vm[1] is None, f"{path.name} block {n} failed: {vm[1]}"
    assert vm[0], f"{path.name} block {n} printed nothing"
    assert vm == tree, f"{path.name} block {n}: the engines differ"


def test_every_library_has_a_tutorial():
    tutorials = book.tutorial_map(tb.__version__, tb.list_libraries())
    assert book.missing_tutorials(tutorials) == []


def test_build_stops_when_a_library_has_no_tutorial(tmp_path):
    tutorials = book.tutorial_map(tb.__version__, tb.list_libraries())
    tutorials["ioc"] = []
    with pytest.raises(SystemExit, match="ioc"):
        gen_lib_docs.build(tmp_path, tb.__version__, tutorials)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The hand-written pages, the library reference, the book and the search index."""
    out = tmp_path_factory.mktemp("site")
    for page in (ROOT / "site").glob("*.html"):
        shutil.copy2(page, out / page.name)
    gen_lib_docs.build(out, tb.__version__)
    book.render(out, tb.__version__)
    count = search_index.build(out, tb.__version__)
    return out, count


def test_library_pages_link_to_tutorials(built):
    out, _ = built
    for lib in tb.list_libraries():
        text = (out / "lib" / f"{lib}.html").read_text(encoding="utf-8")
        section = text.split('id="tutorials"', 1)[1].split("</section>", 1)[0]
        links = re.findall(r'href="\.\./((?:book|cookbook)/[\w-]+\.html|guide-[\w-]+\.html)"', section)
        assert links, lib
        for link in links:
            assert (ROOT / "site" / link).exists() or link.split("/")[0] in book.KINDS, link
        assert "TB 3.x" in text or f"TB {book.current_version(tb.__version__)}" in text


def check_shell(text: str, prefix: str):
    assert len(re.findall(r"<h1[\s>]", text)) == 1
    head = text.split("</head>", 1)[0]
    assert site_shell.search_script(prefix) in head and site_shell.stats_script(prefix) in head
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text)
    assert not re.search(r"\son[a-z]+\s*=\s*\"", text)
    assert "github.com" not in text.lower() and not name_guard.contains_name(text)


def test_book_pages(tmp_path):
    written = book.render(tmp_path, tb.__version__)
    ver = book.current_version(tb.__version__)
    for kind in book.KINDS:
        docs = book.load(kind, ver)
        index = (tmp_path / kind / "index.html").read_text(encoding="utf-8")
        for doc in docs[1:]:
            assert f'href="../{kind}/{doc.slug}.html"' in index
        for i, doc in enumerate(docs):
            text = (tmp_path / kind / f"{doc.slug}.html").read_text(encoding="utf-8")
            check_shell(text, "../")
            assert f'<span class="version-tag">TB {ver} (current)</span>' in text
            assert ('rel="prev"' in text) == (i > 0) and ('rel="next"' in text) == (i + 1 < len(docs))
            assert 'rel="canonical"' in text
            assert "data-runnable" in text or "data-desktop" in text or i == 0
            copy = (tmp_path / "v" / ver / kind / f"{doc.slug}.html").read_text(encoding="utf-8")
            check_shell(copy, "../../../")
    # Links between pages land on pages that exist.
    for path in written:
        text = path.read_text(encoding="utf-8")
        for href in re.findall(r'href="([^"#:]+\.html)', text):
            target = (path.parent / href).resolve()
            in_site = target.relative_to(tmp_path.resolve()).as_posix()
            if in_site.startswith(("book/", "cookbook/", "v/")):
                assert target.exists(), (path.name, href)
            elif not in_site.startswith("lib/"):
                generated = {"library.html", "spec.html", "migration.html", "releases.html", "search.html", "packages.html"}
                assert (ROOT / "site" / in_site).exists() or in_site in generated, (path.name, href)
            else:
                assert in_site[4:-5] in tb.list_libraries(), (path.name, href)


def test_a_new_major_version_keeps_old_urls(tmp_path, monkeypatch):
    """When docs/4.x/ arrives, /book/ shows 4.x and /v/3.x/book/ still works."""
    docs = tmp_path / "docs"
    shutil.copytree(book.DOCS / "3.x", docs / "3.x")
    shutil.copytree(book.DOCS / "3.x", docs / "4.x")  # stand-in content for this test only
    monkeypatch.setattr(book, "DOCS", docs)
    assert book.versions() == ["4.x", "3.x"]
    out = tmp_path / "site"
    book.render(out, "4.0.0")
    current = (out / "book" / "index.html").read_text(encoding="utf-8")
    assert "TB 4.x (current)" in current and 'href="../v/3.x/book/index.html">TB 3.x</a>' in current
    old = (out / "v" / "3.x" / "book" / "index.html").read_text(encoding="utf-8")
    assert "These pages are for TB 3.x." in old and 'href="../../../book/index.html">TB 4.x</a>' in old
    assert 'rel="canonical"' not in old
    assert (out / "v" / "4.x" / "cookbook" / "index.html").exists()
    # Links inside an old version stay inside it.
    chapter = next((out / "v" / "3.x" / "book").glob("*.html"))
    body = chapter.read_text(encoding="utf-8").split('<p class="doc-version">')[1].split("</p>", 1)[1]
    assert 'href="../../../book/' not in body.split("</article>")[0]
    with pytest.raises(SystemExit, match="5.x"):
        book.render(out, "5.0.0")


def test_search_index(built):
    tmp_path, count = built
    entries = json.loads((tmp_path / "search-index.json").read_text(encoding="utf-8"))
    assert count == len(entries) > 500
    by_url = {e["u"]: e for e in entries}
    assert by_url["lib/ioc.html#fn-defang"]["t"].startswith("ioc.defang(")
    assert by_url["lib/ioc.html#fn-defang"]["k"] == "Library" and by_url["lib/ioc.html#fn-defang"]["x"]
    assert any(u.startswith("book/") and "#" in u for u in by_url), "book sections"
    assert any(e["k"] == "Cookbook" for e in entries)
    assert "docs.html" in by_url and any(u.startswith("guide-sigma.html#") for u in by_url)
    assert not any(u.startswith(("v/", "search.html")) for u in by_url)
    for e in entries:
        assert set(e) == {"t", "u", "p", "k", "x"} and e["t"] and len(e["x"]) <= 145
    assert (tmp_path / "search-index.json").stat().st_size < 400_000
    check_shell((tmp_path / "search.html").read_text(encoding="utf-8"), "")


def test_search_script_is_csp_safe():
    js = (ROOT / "site" / "assets" / "js" / "search.js").read_text(encoding="utf-8")
    assert "eval(" not in js and "innerHTML" not in js and "http" not in js.replace("https://", "")
    assert 'e.key !== "/"' in js  # the / shortcut
    header = site_shell.header("", "", tb.__version__)
    assert 'class="icon-button search-toggle" href="search.html"' in header
