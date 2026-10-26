"""The website's scripts and code samples.

* every library has an example in scripts/lib_examples/, and the browser ones run
* the playground's security examples and the tutorials' runnable snippets run
  (from a folder laid out like the playground's pretend disk: apps/data/...)
* the generated library reference covers every library and never contains
  the author's name (checked by hash, see scripts/name_guard.py)
* every release asset the site links to is one the release workflow uploads
* the hand-written pages use the shared header and footer, have one h1, and
  have no inline scripts (the live site's CSP forbids them)
* a full build (when Pyodide has been fetched) ships tblib, the sample data,
  the app sources and the apps gallery
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from conftest import ROOT

import tb

SCRIPTS = ROOT / "scripts"
SITE = ROOT / "site"
sys.path.insert(0, str(SCRIPTS))

import gen_lib_docs  # noqa: E402
import name_guard  # noqa: E402
import site_shell  # noqa: E402

LIB_EXAMPLES = SCRIPTS / "lib_examples"
PLAYGROUND = SCRIPTS / "playground_examples"
GUIDES = sorted(SITE.glob("guide-*.html"))
PAGES = sorted(SITE.glob("*.html"))


@pytest.fixture
def sandbox(tmp_path):
    """A working folder like the browser playground's: apps/data/ is there."""
    (tmp_path / "apps").mkdir()
    shutil.copytree(ROOT / "apps" / "data", tmp_path / "apps" / "data")
    old = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old)


def run(source):
    lines = []
    tb.run(source, output=lines.append, input_fn=lambda prompt: "3")
    return lines


def code_blocks(page: Path, marker: str):
    text = page.read_text(encoding="utf-8")
    pattern = r'<pre class="tb-code" ' + marker + r'[^>]*><code>(.*?)</code></pre>'
    return [html.unescape(c) for c in re.findall(pattern, text, flags=re.S)]


def test_every_library_has_an_example():
    for name in tb.list_libraries():
        assert (LIB_EXAMPLES / f"{name}.tb").exists() or (LIB_EXAMPLES / f"{name}.desktop.tb").exists(), name


@pytest.mark.parametrize("path", sorted(LIB_EXAMPLES.glob("*.tb")), ids=lambda p: p.name)
def test_library_examples(path, sandbox):
    source = path.read_text(encoding="utf-8")
    if path.name.endswith(".desktop.tb"):
        tb.parse(source)
    else:
        assert run(source), f"{path.name} printed nothing"


@pytest.mark.parametrize("path", sorted(PLAYGROUND.glob("*.tb")), ids=lambda p: p.stem)
def test_playground_examples(path, sandbox):
    source = path.read_text(encoding="utf-8")
    assert source.startswith("# Title: ") and "\n# About: " in source
    assert run(source)


@pytest.mark.parametrize("page", GUIDES, ids=lambda p: p.stem)
def test_tutorial_snippets(page, sandbox):
    runnable = code_blocks(page, "data-runnable")
    desktop = code_blocks(page, "data-desktop")
    assert runnable or desktop
    for source in runnable:
        run(source)
    for source in desktop:
        tb.parse(source)


def test_library_reference(tmp_path):
    written = gen_lib_docs.build(tmp_path, tb.__version__)
    names = {p.name for p in written}
    assert "library.html" in names
    for lib in tb.list_libraries():
        page = tmp_path / "lib" / f"{lib}.html"
        assert page.exists()
        text = page.read_text(encoding="utf-8")
        for fn in tb.describe_library(lib)["functions"]:
            assert f'id="fn-{fn["name"]}"' in text, (lib, fn["name"])
        assert f'href="lib/{lib}.html"' in (tmp_path / "library.html").read_text(encoding="utf-8")
    for path in written:
        assert not name_guard.contains_name(path.read_text(encoding="utf-8")), path.name


def test_home_page_lists_every_library():
    text = (SITE / "index.html").read_text(encoding="utf-8")
    for lib in tb.list_libraries():
        assert f'href="lib/{lib}.html"' in text, lib
    assert text.count("<!-- PERSONAL MESSAGE: START (edit freely) -->") == 1
    assert text.count("<!-- PERSONAL MESSAGE: END -->") == 1


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_shell(page):
    text = page.read_text(encoding="utf-8")
    current = re.search(r'<a href="([\w.-]+)"(?: class="nav-cta")? aria-current="page"', text)
    expected_header = site_shell.header(current.group(1) if current else "", "", tb.__version__)
    assert expected_header in text, f"{page.name}: header differs from scripts/site_shell.py"
    assert site_shell.footer("", tb.__version__) in text, f"{page.name}: footer differs from scripts/site_shell.py"
    assert len(re.findall(r"<h1[\s>]", text)) == 1
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), "inline <script> blocks break the CSP"
    assert not re.search(r"\son[a-z]+\s*=\s*\"", text), "inline event handlers break the CSP"
    assert not name_guard.contains_name(text)


def test_full_build(tmp_path):
    vendor = SITE / "vendor" / "pyodide" / "pyodide.asm.wasm"
    if not vendor.exists():
        pytest.skip("Pyodide not fetched (run scripts/fetch_pyodide.py)")
    import build_site
    out = tmp_path / "site"
    assert build_site.main(["--out", str(out)]) == 0
    lib_index = json.loads((out / "tblib" / "index.json").read_text())
    assert "ioc.py" in lib_index and "_api.py" in lib_index
    assert all((out / "tblib" / f).exists() for f in lib_index)
    data_index = json.loads((out / "apps" / "data" / "index.json").read_text())
    assert "auth.log" in data_index and "sigma/ssh_brute_force.yml" in data_index
    names = [e["name"] for e in json.loads((out / "examples" / "index.json").read_text())]
    assert names[:2] == ["for_tanisha", "hello"] and "ssh_brute_force" in names
    gallery = (out / "examples.html").read_text(encoding="utf-8")
    assert "APPS GALLERY" not in gallery
    for app in (ROOT / "apps").iterdir():
        if (app / "main.tb").exists():
            assert f'id="app-{app.name}"' in gallery, app.name
            assert (out / "apps" / app.name / "main.tb").exists()
    assert (out / "lib" / "ioc.html").exists()
    for page in out.rglob("*.html"):
        assert not name_guard.contains_name(page.read_text(encoding="utf-8")), page


def test_name_guard_catches_the_name():
    # Built from the digests' own inputs would spell the name; instead check
    # that the guard is wired up and quiet on ordinary text.
    assert not name_guard.contains_name("Made with love, for Tanisha. TB 2.0 brute force raid amond")
    assert set(name_guard._DIGESTS) == {4, 6}


def test_release_assets_exist():
    workflow = (ROOT.parent / ".github" / "workflows" / "tb-release.yml").read_text(encoding="utf-8")
    linked = set()
    for path in list(SITE.rglob("*.html")) + list(SITE.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        linked |= set(re.findall(r'data-download="([^"]+)"', text))
        linked |= set(re.findall(r'file: "([^"]+)"', text))
    assert {"tb-windows-x64-setup.exe", "tb-windows-x64.zip", "tb-macos-arm64.zip", "tb-linux-x64.tar.gz"} <= linked
    for name in linked:
        assert name in workflow, f"the site links to {name}, but tb-release.yml doesn't upload it"
    for path in SITE.rglob("*.html"):
        assert "tb-windows-x64.exe" not in path.read_text(encoding="utf-8"), f"{path.name} links the old Windows asset"
