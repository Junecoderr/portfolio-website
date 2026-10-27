"""The TB Package Index on the website: the pages scripts/registry_pages.py
writes, site/assets/js/registry.js and registry.css, the generated seed the API
serves (api/_lib/registry/seed.js) and the vercel.json rewrite. The API itself
is tested in tests/api/registry.test.mjs (run by tests/test_site.py)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys

import pytest

from conftest import ROOT

import tb

SCRIPTS = ROOT / "scripts"
SITE = ROOT / "site"
sys.path.insert(0, str(SCRIPTS))

import gen_registry_seed  # noqa: E402
import name_guard  # noqa: E402
import registry_pages  # noqa: E402
import site_shell  # noqa: E402

PAGES = ["packages.html", "packages/view.html", "packages/publish.html", "packages/account.html", "packages/admin.html"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    written = registry_pages.render(out, tb.__version__)
    return out, written


def test_seed_is_up_to_date():
    """api/_lib/registry/seed.js matches packages/ and the library list
    (run scripts/gen_registry_seed.py after changing either)."""
    assert gen_registry_seed.main(["--check"]) == 0
    text = gen_registry_seed.SEED_JS.read_text(encoding="utf-8")
    assert json.dumps(sorted(tb.list_libraries())) in text
    assert "github.com" not in text.lower()


def test_seed_archives_match_the_static_zips():
    for entry in gen_registry_seed.seed_entries():
        folder = ROOT / "packages" / entry["name"]
        data = gen_registry_seed.package_zip(folder)
        assert base64.b64decode(entry["zip"]) == data
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert gen_registry_seed.package_zip(folder) == data, "zips are reproducible"
        assert entry["entry"] == "lib.tb" and "tb.toml" in entry["files"]


@pytest.mark.parametrize("name", PAGES)
def test_pages_use_the_shell_and_the_csp(built, name):
    out, written = built
    assert name in written
    text = (out / name).read_text(encoding="utf-8")
    prefix = "../" if "/" in name else ""
    assert site_shell.header("", prefix, tb.__version__) in text
    assert site_shell.footer(prefix, tb.__version__) in text
    head = text.split("</head>", 1)[0]
    assert site_shell.stats_script(prefix) in head
    assert f'<link rel="stylesheet" href="{prefix}assets/css/registry.css">' in head
    assert f'<script src="{prefix}assets/js/registry.js" defer></script>' in text
    assert len(re.findall(r"<h1[\s>]", text)) == 1
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), "inline <script> blocks break the CSP"
    assert not re.search(r"\son[a-z]+\s*=", text), "inline event handlers break the CSP"
    assert not re.search(r"\sstyle\s*=", text)
    assert "github.com" not in text.lower()
    assert not name_guard.contains_name(text)
    assert re.search(r'data-registry-page="(list|view|publish|account|admin)"', text)


def test_list_page_works_without_javascript(built):
    out, _ = built
    text = (out / "packages.html").read_text(encoding="utf-8")
    assert "<h1>TB Package Index</h1>" in text
    for name in ("tb-colors", "tb-progress", "tb-tables"):
        assert f'href="packages/view.html?name={name}"' in text
    assert 'data-bundled="packages/bundled.json"' in text
    assert '<form class="registry-search" role="search"' in text
    assert 'href="packages/publish.html"' in text


def test_bundled_json(built):
    out, _ = built
    data = json.loads((out / "packages" / "bundled.json").read_text(encoding="utf-8"))
    names = [p["name"] for p in data["packages"]]
    assert names == ["tb-colors", "tb-progress", "tb-tables"]
    seed = {e["name"]: e for e in gen_registry_seed.seed_entries()}
    for p in data["packages"]:
        assert p["owner"] == "tb" and p["status"] == "approved"
        assert p["sha256"] == seed[p["name"]]["sha256"]
        assert p["readme"].startswith(f"# {p['name']}")
        assert "zip" not in p


def test_signed_in_pages(built):
    out, _ = built
    publish = (out / "packages" / "publish.html").read_text(encoding="utf-8")
    # Sign-in goes through our own API; the redirect to GitHub happens on the server.
    assert 'href="/api/registry/auth/login?next=/packages/publish"' in publish
    for hook in ("data-auth-offline", "data-auth-out", "data-auth-in", "data-token-form", "data-token-list", 'id="tokens"'):
        assert hook in publish
    assert "tb publish" in publish and "tb login" in publish and "tb yank" in publish
    account = (out / "packages" / "account.html").read_text(encoding="utf-8")
    assert "data-account-list" in account and "next=/packages/account" in account
    admin = (out / "packages" / "admin.html").read_text(encoding="utf-8")
    for hook in ("data-admin-queue", "data-admin-reports", "data-admin-audit", "data-admin-action"):
        assert hook in admin
    view = (out / "packages" / "view.html").read_text(encoding="utf-8")
    for hook in ("data-pkg-readme", "data-pkg-versions", "data-pkg-install", "data-report-form", "<noscript>"):
        assert hook in view


def test_registry_script_builds_dom_safely():
    js = (SITE / "assets" / "js" / "registry.js").read_text(encoding="utf-8")
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert unsafe not in js, unsafe
    assert "textContent" in js and "createElement" in js
    assert 'var API = "/api/registry/";' in js
    assert 'credentials: "same-origin"' in js
    assert "document.cookie" not in js and "localStorage" not in js
    assert "github.com" not in js.lower()
    assert "(?<" not in js, "no regex lookbehind (older Safari can't parse it)"
    # Links from READMEs: only http(s), mailto and anchors, and marked as user content.
    assert "SAFE_URL = /^(https?:\\/\\/|#|mailto:)/i" in js
    assert 'rel: "nofollow ugc noopener noreferrer"' in js


def test_registry_styles():
    css = (SITE / "assets" / "css" / "registry.css").read_text(encoding="utf-8")
    assert ".registry-layout" in css and "var(--" in css
    assert "@import" not in css and "url(" not in css


def test_site_mentions_the_index():
    tools = (SITE / "tools.html").read_text(encoding="utf-8")
    assert "tb publish" in tools and "tb search" in tools and 'id="publish"' in tools
    assert "tb publish" in (SITE / "docs.html").read_text(encoding="utf-8")
    assert "TB Package Index" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_vercel_routes_the_api():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    rewrites = {r["source"]: r["destination"] for r in config["rewrites"]}
    assert rewrites["/api/registry/:route*"] == "/api/registry?route=:route*"
    assert config["installCommand"] == "", "the functions have no npm dependencies"
    assert "connect-src 'self'" in json.dumps(config)
    assert (ROOT / "api" / "registry.js").exists()
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "dependencies" not in package
    # Server code never ships as a served file.
    assert not (SITE / "api").exists()
