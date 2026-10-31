"""The website's scripts and code samples.

* every library has an example in scripts/lib_examples/, and the browser ones run
* the playground's security examples and the tutorials' runnable snippets run
  (from a folder laid out like the playground's pretend disk: apps/data/...)
* the generated library reference covers every library and never contains
  the author's name (checked by hash, see scripts/name_guard.py)
* every function, value, widget, window method, built-in function and method
  in the reference has a "Visualize" picture (scripts/visuals/): its legend
  names every parameter of the signature, the SVGs are well-formed XML, and
  the pages have no inline scripts or handlers
* every release asset the site links to is one the release workflow uploads
* the hand-written pages use the shared header and footer, have one h1, and
  have no inline scripts (the live site's CSP forbids them)
* a full build (when Pyodide has been fetched) ships tblib, the sample data,
  the app sources and the apps gallery
* the visit and download counters: every page loads assets/js/stats.js, the
  home page, download page and footer carry data-stat slots that stay hidden
  without the API, and the Vercel functions in api/ pass their Node unit
  tests (tests/api/, skipped without node)
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

import tb

SCRIPTS = ROOT / "scripts"
SITE = ROOT / "site"
sys.path.insert(0, str(SCRIPTS))

import gen_lib_docs  # noqa: E402
import visuals  # noqa: E402
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


@pytest.mark.parametrize("page", [p for p in PAGES if not p.name.startswith(("guide-", "docs"))], ids=lambda p: p.stem)
def test_page_snippets(page, sandbox):
    """The runnable snippets on the home, What's new and Tools pages run (docs.html: test_examples.py)."""
    for source in code_blocks(page, "data-runnable"):
        run(source)
    for source in code_blocks(page, "data-desktop"):
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


LEGEND_RE = re.compile(r'<button type="button" class="fv-chip[^"]*" data-part="p\d+"[^>]*>'
                       r'<span class="fv-sw" aria-hidden="true"></span><code>([^<]*)</code>')
SVG_RE = re.compile(r'<svg xmlns="http://www.w3.org/2000/svg" class="fv-svg".*?</svg>', re.S)


def first_visual(chunk: str) -> str:
    start = chunk.find('<details class="fn-visual')
    assert start >= 0, "no picture"
    return chunk[start:chunk.find("</details>", start)]


def check_legend(chunk: str, signature: str, where):
    names = set(LEGEND_RE.findall(first_visual(chunk)))
    for prm in visuals.parse_params(signature):
        assert html.escape(prm.name) in names, (where, prm.name, sorted(names))


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    out = tmp_path_factory.mktemp("ref")
    visuals.PROBLEMS.clear()
    gen_lib_docs.build(out, tb.__version__)
    return out


def test_every_function_has_a_picture(reference):
    assert not visuals.PROBLEMS, visuals.PROBLEMS[:3]
    for lib in tb.list_libraries():
        text = (reference / "lib" / f"{lib}.html").read_text(encoding="utf-8")
        d = tb.describe_library(lib)
        for fn in d["functions"]:
            start = text.index(f'<article class="sig" id="fn-{fn["name"]}">')
            card = text[start:text.index("</article>", start)]
            check_legend(card, fn["signature"], (lib, fn["name"]))
        for v in d["values"]:
            start = text.index(f'<tr id="value-{v["name"]}">')
            row = text[start:text.index("</tr>", start)]
            assert 'class="fn-visual' in row, (lib, v["name"])
        assert 'assets/css/visuals.css' in text and 'assets/js/visuals.js' in text


def test_gui_widgets_and_window_methods_have_pictures(reference):
    text = (reference / "lib" / "gui.html").read_text(encoding="utf-8")
    window_doc = next(f["doc"] for f in tb.describe_library("gui")["functions"] if f["name"] == "window")
    count = 0
    for title, intro, entries in visuals.gui_extras():
        for ident, sig, _ in entries:
            name = sig.split("(")[0]
            assert re.search(rf"\b{name}\(", window_doc), f"win.{name} is not in gui.window's doc any more"
            start = text.index(f'<details class="fn-visual fv-sub" id="{ident}">')
            check_legend(text[start:], sig, ident)
            count += 1
    assert count >= 50
    assert 'id="win-resize"' in text and 'data-fv-param="w"' in text and 'data-fv-param="h"' in text


def test_builtins_and_methods_have_pictures(reference):
    text = (reference / "library.html").read_text(encoding="utf-8")
    b = tb.describe_builtins()
    for f in b["functions"]:
        start = text.index(f'<tr><td><code>{html.escape(f["signature"], quote=False)}</code></td>')
        check_legend(text[start:text.index("</tr>", start)], f["signature"], f["signature"])
    for kind, methods in b["methods"].items():
        for m in methods:
            start = text.index(f'<tr><td><code>.{html.escape(m["signature"], quote=False)}</code></td>')
            check_legend(text[start:text.index("</tr>", start)], m["signature"], (kind, m["signature"]))


def test_pictures_are_well_formed_and_csp_safe(reference):
    import xml.etree.ElementTree as ET
    pages = [reference / "library.html"] + sorted((reference / "lib").glob("*.html"))
    total = 0
    for page in pages:
        text = page.read_text(encoding="utf-8")
        svgs = SVG_RE.findall(text)
        assert svgs, page.name
        for svg in svgs:
            try:
                ET.fromstring(svg)
            except ET.ParseError as err:  # pragma: no cover - shows the broken picture
                raise AssertionError(f"{page.name}: {err}: {svg[:300]}")
        total += len(svgs)
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), page.name
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text), page.name
        assert not re.search(r'\sstyle="', "".join(svgs)), f"{page.name}: inline styles in a picture"
        assert not name_guard.contains_name(text), page.name
    assert total > 600


def test_visual_parameters_are_parsed_from_signatures():
    names = lambda sig: [p.name for p in visuals.parse_params(sig)]  # noqa: E731
    assert names("pad_left(value, width, fill?) -> text") == ["value", "width", "fill"]
    assert names("min(a, b, ...) or min(list) -> number") == ["a", "b", "list"]
    assert names("run(fn, ...args) -> Task") == ["fn", "args"]
    assert names("tables() -> list") == []


def test_home_page_lists_every_library():
    text = (SITE / "index.html").read_text(encoding="utf-8")
    for lib in tb.list_libraries():
        assert f'href="lib/{lib}.html"' in text, lib
    assert "PERSONAL MESSAGE" not in text
    assert 'id="why"' in text  # the footer's "Why TB" link lands here


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_shell(page):
    text = page.read_text(encoding="utf-8")
    current = re.search(r'<a href="([\w.-]+)"(?: class="nav-cta")? aria-current="page"', text)
    expected_header = site_shell.header(current.group(1) if current else "", "", tb.__version__)
    assert expected_header in text, f"{page.name}: header differs from scripts/site_shell.py"
    assert site_shell.footer("", tb.__version__) in text, f"{page.name}: footer differs from scripts/site_shell.py"
    assert len(re.findall(r"<h1[\s>]", text)) == 1
    head = text.split("</head>", 1)[0]
    assert site_shell.stats_script("") in head, f"{page.name}: run scripts/sync_pages.py (stats.js missing)"
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
    assert names[:2] == ["welcome", "hello"] and "ssh_brute_force" in names
    gallery = (out / "examples.html").read_text(encoding="utf-8")
    assert "APPS GALLERY" not in gallery
    for app in (ROOT / "apps").iterdir():
        if (app / "main.tb").exists():
            assert f'id="app-{app.name}"' in gallery, app.name
            assert (out / "apps" / app.name / "main.tb").exists()
    assert (out / "lib" / "ioc.html").exists()
    # The counters' functions are deployed by Vercel from api/, never served as files.
    assert not (out / "api").exists() and not list(out.rglob("hit.js"))
    assert (out / "assets" / "js" / "stats.js").exists()
    assert (out / "assets" / "js" / "visuals.js").exists() and (out / "assets" / "css" / "visuals.css").exists()
    for page in [out / "library.html", out / "lib" / "ioc.html", out / "spec.html", out / "packages.html"]:
        prefix = "../" if page.parent.name == "lib" else ""
        assert site_shell.stats_script(prefix) in page.read_text(encoding="utf-8"), page.name
    for page in out.rglob("*.html"):
        assert not name_guard.contains_name(page.read_text(encoding="utf-8")), page
    config = (out / "assets" / "js" / "config.js").read_text(encoding="utf-8")
    assert f'version: "{tb.__version__}"' in config and "repo" not in config
    # Downloads and links stay on this site: no GitHub links, no release URLs.
    for path in out.rglob("*"):
        if not path.is_file() or "vendor" in path.relative_to(out).parts:
            continue
        if path.suffix not in (".html", ".js", ".css", ".json", ".tb", ".md", ".txt"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "github.com" not in text.lower(), f"{path.relative_to(out)} links to GitHub"
        assert "/releases/" not in text, f"{path.relative_to(out)} has a release URL"


def test_name_guard_catches_the_name():
    # Built from the digests' own inputs would spell the name; instead check
    # that the guard is wired up and quiet on ordinary text.
    assert not name_guard.contains_name("A friendly programming language. TB 2.0 brute force raid amond")
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
    assert f"tb-vscode-{tb.__version__}.vsix" in linked
    for name in linked:
        # Versioned assets appear in the workflow as e.g. tb-vscode-${version}.vsix.
        templated = name.replace(tb.__version__, "${version}")
        assert name in workflow or templated in workflow, f"the site links to {name}, but tb-release.yml doesn't upload it"
    for path in SITE.rglob("*.html"):
        assert "tb-windows-x64.exe" not in path.read_text(encoding="utf-8"), f"{path.name} links the old Windows asset"
    # Every download link is site-relative (/dl/<asset>); the host serves the files.
    for path in list(SITE.rglob("*.html")) + list(SITE.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "github.com" not in text.lower(), f"{path.name} links to GitHub"
        assert "/releases/" not in text, f"{path.name} has a release URL"
        for name, href in re.findall(r'data-download="([^"]+)"(?: data-primary-link)? href="([^"]+)"', text):
            assert href == f"/dl/{name}", (path.name, name, href)


# ---------------------------------------------------------------- site statistics

STAT_SLOT = re.compile(r'data-stat="([^"]+)"')


def test_stats_script_is_quiet_without_the_api():
    js = (SITE / "assets" / "js" / "stats.js").read_text(encoding="utf-8")
    assert 'fetch("/api/hit"' in js and "keepalive: true" in js and 'fetch("/api/stats"' in js
    assert "navigator.webdriver" in js and "localhost" in js and "127.0.0.1" in js
    assert "Intl.NumberFormat" in js
    assert "document.cookie" not in js and "localStorage" not in js
    assert "github" not in js.lower()


def test_stats_slots_start_hidden():
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        for tag in re.findall(r"<[^>]*data-stats?(?:-wrap)?[\s>][^>]*>", text):
            if "data-stat=" in tag:
                continue  # the number itself; its wrapper is what hides
            assert re.search(r"\shidden[\s>]", tag), f"{page.name}: {tag} must start hidden"


def test_footer_shows_the_totals():
    footer = site_shell.footer("", tb.__version__)
    assert set(STAT_SLOT.findall(footer)) == {"downloads", "visits"}
    assert 'data-stats hidden' in footer
    assert "without cookies" not in footer
    page = site_shell.page("T", "D", "<h1>x</h1>", prefix="../")
    assert '<script src="../assets/js/stats.js" defer></script>' in page.split("</head>")[0]


def test_home_page_stats_strip():
    text = (SITE / "index.html").read_text(encoding="utf-8")
    strip = re.search(r'<p class="stats-strip" data-stats hidden>(.*?)</p>', text, re.S)
    assert strip, "the home page's stats strip is missing"
    assert set(STAT_SLOT.findall(strip.group(1))) == {"downloads", "visits"}
    assert text.index('class="stats-strip"') > text.index('aria-labelledby="hero-title"')


def test_download_page_counts_every_file():
    text = (SITE / "download.html").read_text(encoding="utf-8")
    table = text[text.index('<h2 id="all-downloads">'):text.index("</table>", text.index('<h2 id="all-downloads">'))]
    files = re.findall(r'<tr><td><a data-download="([^"]+)"', table)
    assert len(files) >= 6
    slots = set(STAT_SLOT.findall(table))
    assert slots == {f"dl:{name}" for name in files}
    assert re.search(r'<p class="dl-total" data-stats hidden><strong data-stat="downloads">', text)
    assert "<strong>Site statistics.</strong>" in text
    assert "counts page views without cookies or personal data" in text


def test_stats_functions():
    """The Vercel functions (api/hit.js, api/stats.js) pass their Node unit tests."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    tests = sorted((ROOT / "tests" / "api").glob("*.test.mjs"))
    assert tests
    proc = subprocess.run([node, "--test", *map(str, tests)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]


def test_stats_functions_stay_out_of_the_site():
    assert (ROOT / "api" / "hit.js").exists() and (ROOT / "api" / "stats.js").exists()
    assert not (SITE / "api").exists()
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["outputDirectory"] == "dist/site"
    assert "connect-src 'self'" in json.dumps(config)


# -- the playground's worker (tb-worker.js) and cross-origin isolation ----------

def _site_headers() -> dict:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    rule = next(r for r in config["headers"] if r["source"] == "/(.*)")
    return {h["key"]: h["value"] for h in rule["headers"]}


def test_site_is_cross_origin_isolated():
    """COOP/COEP give the playground SharedArrayBuffer (inline ask(), instant
    Stop). Run buttons are on nearly every page, and the worker script and
    Pyodide need the headers too, so they cover the whole site."""
    headers = _site_headers()
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Embedder-Policy"] == "require-corp"
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self' 'wasm-unsafe-eval';" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert "worker-src 'self'" in csp and "default-src 'self'" in csp


_CROSS_ORIGIN = re.compile(r"""<(?:script|img|iframe|source|video|audio|embed|track)\b[^>]*\ssrc=["']?(?:https?:)?//"""
                           r"""|<link\b[^>]*\shref=["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//""", re.I)


@pytest.mark.parametrize("page", PAGES + sorted((SITE / "assets" / "css").glob("*.css")), ids=lambda p: p.name)
def test_no_cross_origin_subresources(page):
    """Under COEP require-corp a cross-origin script, image or stylesheet would fail to load."""
    assert not _CROSS_ORIGIN.search(page.read_text(encoding="utf-8")), page.name


def test_playground_runs_in_a_worker():
    js = SITE / "assets" / "js"
    client = (js / "tb-pyodide.js").read_text(encoding="utf-8")
    worker = (js / "tb-worker.js").read_text(encoding="utf-8")
    assert 'new URL("tb-worker.js", here)' in client and "new Worker(WORKER_URL)" in client
    assert "SharedArrayBuffer" in client and "crossOriginIsolated" in client
    assert "worker.terminate()" in client  # Stop without shared memory
    assert "setInterruptBuffer" in worker and "Atomics.wait" in worker and "importScripts" in worker
    assert "except KeyboardInterrupt" in worker
    for name in ("tb-pyodide.js", "tb-worker.js", "playground.js", "runner.js"):
        assert "http://" not in (js / name).read_text(encoding="utf-8")
        assert "https://" not in (js / name).read_text(encoding="utf-8")
    page = (SITE / "playground.html").read_text(encoding="utf-8")
    assert 'id="pg-stop"' in page and 'id="pg-run"' in page
    for name in ("playground.js", "runner.js"):
        text = (js / name).read_text(encoding="utf-8")
        # Stop stops this page's own run (its id), never another queued or running one.
        assert "TB.askInline(" in text and "TB.stop(runId)" in text and "runId = p.id" in text, name


def test_browser_client_stop_is_per_run():
    """tests/browser/tb-pyodide.test.mjs: tb-pyodide.js with a fake Worker (Stop per run, ask() cut on a character)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--test", "--test-timeout=20000", str(ROOT / "tests" / "browser" / "tb-pyodide.test.mjs")], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]


def test_serve_site_sends_vercel_headers():
    import threading
    import functools
    import urllib.request
    from http.server import ThreadingHTTPServer
    import serve_site
    serve_site.Handler.rules = serve_site.vercel_headers(json.loads((ROOT / "vercel.json").read_text(encoding="utf-8")))
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(serve_site.Handler, directory=str(SITE)))
    server.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/playground") as r:  # cleanUrls
            assert b'id="pg-stop"' in r.read()
            for key, value in _site_headers().items():
                assert r.headers[key] == value, key
        with urllib.request.urlopen(base + "/assets/js/tb-worker.js") as r:
            assert r.headers["Cross-Origin-Embedder-Policy"] == "require-corp"
    finally:
        server.shutdown()
        server.server_close()


def test_playground_in_a_real_browser(tmp_path):
    """Opt-in (TB_BROWSER_TESTS=1, needs node, Chrome and Pyodide): runs
    tests/browser/playground.mjs against a fresh build served like Vercel."""
    if os.environ.get("TB_BROWSER_TESTS") != "1":
        pytest.skip("set TB_BROWSER_TESTS=1 to drive headless Chrome")
    node = shutil.which("node")
    if not node or not (SITE / "vendor" / "pyodide" / "pyodide.asm.wasm").exists():
        pytest.skip("needs node and Pyodide (scripts/fetch_pyodide.py)")
    import build_site
    out = tmp_path / "site"
    assert build_site.main(["--out", str(out)]) == 0
    server = subprocess.Popen([sys.executable, str(SCRIPTS / "serve_site.py"), "--dir", str(out), "--port", "0"],
                              stdout=subprocess.PIPE, text=True)
    try:
        url = re.search(r"http://\S+", server.stdout.readline()).group(0)
        proc = subprocess.run([node, str(ROOT / "tests" / "browser" / "playground.mjs"), url],
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        server.terminate()
        server.wait(timeout=10)


# -- readable doc strings (tbcore/docfmt.py; format in tblib/README.md) --------------------

def _plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def test_doc_paragraphs_stay_short_and_lists_render_as_lists(reference):
    from tbcore import docfmt
    for lib in tb.list_libraries():
        text = (reference / "lib" / f"{lib}.html").read_text(encoding="utf-8")
        d = tb.describe_library(lib)
        for entry in d["functions"] + d["values"]:
            rendered = docfmt.to_html(entry["doc"])
            assert rendered in text, (lib, entry["name"])
            for para in re.findall(r"<p>(.*?)</p>", rendered, re.S):
                assert len(_plain(para)) <= 350, (lib, entry["name"], _plain(para)[:80])
            bullets = [ln for ln in entry["doc"].splitlines() if re.match(r"^ *- ", ln)]
            if bullets:
                assert rendered.count("<li>") == len(bullets), (lib, entry["name"])
                assert '<ul class="doc-list">' in rendered
            assert not re.search(r"<p>\s*- ", rendered), (lib, entry["name"])
            if "\nExample:\n" in "\n" + entry["doc"]:
                assert '<pre class="tb-code"><code>' in rendered, (lib, entry["name"])


def test_doc_format_parts():
    from tbcore import docfmt
    doc = ('Check a rule.\n\nOptions:\n- count: how many (default: 1).\n  - "a|b": nested\n'
           '- A plain bullet: not a name.\n  continued here\n\nExample:\n    let x = 1\n    say(x)')
    blocks = docfmt.parse(doc)
    assert [b["kind"] for b in blocks] == ["para", "heading", "list", "heading", "code"]
    items = blocks[2]["items"]
    assert items[0]["term"] == "count" and items[0]["children"][0]["term"] == '"a|b"'
    assert items[1]["term"] is None and items[1]["text"].endswith("continued here")
    assert blocks[4]["text"] == "let x = 1\nsay(x)"
    out = docfmt.to_html(doc)
    assert '<p class="doc-h">Options</p><ul class="doc-list"><li><code class="doc-term">count</code>' in out
    assert '<pre class="tb-code"><code>let x = 1\nsay(x)</code></pre>' in out
    md = docfmt.to_markdown(doc)
    assert "**Options**\n\n- `count`: how many (default: 1).\n  - `\"a|b\"`: nested" in md and "```tb\nlet x = 1" in md
    lines = docfmt.to_text(doc, "  ", 40)
    assert "  Options:" in lines and "  - count: how many (default: 1)." in lines
    assert all(len(ln) <= 40 for ln in lines if not ln.startswith("      "))


def test_tb_doc_detect_fits_a_terminal(capsys):
    assert tb.main(["doc", "detect"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines and all(len(ln) <= 100 for ln in lines), max(lines, key=len)
    assert "      Fields:" in out and '      - "path|contains": the text appears anywhere.' in out


def test_every_library_doc_fits_a_terminal(capsys):
    for lib in tb.list_libraries():
        assert tb.main(["doc", lib]) == 0
        long = [ln for ln in capsys.readouterr().out.splitlines() if len(ln) > 100]
        assert not long, (lib, long[:2])


def test_changelog_matches_the_version_and_renders(tmp_path):
    import xml.dom.minidom
    import changelog
    rels = changelog.releases()
    assert rels[0].version == tb.__version__, "add a CHANGELOG.md entry for the new version"
    assert [r.date for r in rels] == sorted((r.date for r in rels), reverse=True)
    assert all(r.summary and r.sections for r in rels)
    kinds = {r.version: changelog.kind(r, rels[i + 1] if i + 1 < len(rels) else None) for i, r in enumerate(rels)}
    assert (kinds["3.0.0"], kinds["2.0.1"], kinds.get("3.2.0", "Minor")) == ("Major", "Patch", "Minor")
    # Every published version is a release on GitHub with downloads; 2.0.0 never was.
    assert [r.version for r in rels if not r.published] == ["2.0.0"]
    assert {r.version for r in rels if r.published} >= {"2.0.1", "3.0.0"}
    changelog.render(tmp_path, tb.__version__)
    page = (tmp_path / "releases.html").read_text(encoding="utf-8")
    for r in rels:
        assert f'id="v{r.version}"' in page
    assert "<script>" not in page and "github.com" not in page.lower()
    assert 'class="rel-legend"' in page and "rel-unpublished" in page
    # The button leads to how-to-subscribe help, not the raw XML (browsers show feeds as code).
    assert 'href="#subscribe"' in page and 'id="subscribe"' in page and 'href="releases.xml"' not in page.split("<body>")[1]
    assert "assets/js/releases.js" in page
    assert page.count("rel-latest") == 1  # only the newest release is "Latest"
    feed = xml.dom.minidom.parse(str(tmp_path / "releases.xml"))
    assert len(feed.getElementsByTagName("entry")) == len(rels)
    assert "### Added" in changelog.notes(tb.__version__)
    with pytest.raises(SystemExit):
        changelog.render(tmp_path, "99.0.0")


def test_changelog_rejects_bad_entries(tmp_path, monkeypatch):
    import changelog
    bad = tmp_path / "CHANGELOG.md"
    bad.write_text("## Unreleased\n\n- draft\n\n## 1.0.0 - 2026-01-02\n\nFirst.\n\n### Added\n\n- One.\n", encoding="utf-8")
    assert [r.version for r in changelog.releases(bad)] == ["1.0.0"]  # drafts never publish
    bad.write_text("## 1.0.0 - 2026-01-02 (not published)\n\nBuilt only.\n\n### Added\n\n- One.\n", encoding="utf-8")
    assert changelog.releases(bad)[0].published is False
    unpublished = changelog.releases(bad)
    monkeypatch.setattr(changelog, "releases", lambda *a: unpublished)
    with pytest.raises(SystemExit):  # an unpublished version can't be the site's current one
        changelog.render(tmp_path, "1.0.0")
    monkeypatch.undo()
    bad.write_text("## 1.0 - soon\n", encoding="utf-8")
    with pytest.raises(ValueError):
        changelog.releases(bad)
    bad.write_text("## 1.0.0 - 2026-01-02\n\n### Improved\n\n- x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        changelog.releases(bad)
