"""The TB Package Index pages, written by scripts/build_site.py:

    packages.html            search and the list of packages
    packages/view.html       one package (?name=...): README, versions, install
    packages/publish.html    how to publish, GitHub sign-in and API tokens
    packages/account.html    your packages and their review status
    packages/admin.html      the review queue (the API only answers admins)
    packages/bundled.json    the bundled packages, for when the API isn't there

The pages are static HTML on the shared shell; site/assets/js/registry.js fills
them from /api/registry/* and site/assets/css/registry.css styles them. With no
API (local previews, tests) the list and package pages fall back to
packages/bundled.json and the account pages say the index isn't reachable.
No inline scripts (the CSP forbids them) and no links to the source host.
Standard library only.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import site_shell as shell

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"
TITLE = "TB Package Index"
CSS_TAG = 'assets/css/style.css">'


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def bundled() -> list[dict]:
    """The bundled example packages (packages/<name>/), as the API lists them."""
    import gen_registry_seed

    out = []
    for entry in gen_registry_seed.seed_entries():
        item = {k: v for k, v in entry.items() if k != "zip"}
        item["owner"] = "tb"
        item["status"] = "approved"
        out.append(item)
    return out


def page(title: str, description: str, main: str, prefix: str, version: str) -> str:
    import inspect

    kwargs = dict(current="", prefix=prefix, scripts=("highlight.js", "registry.js"), version=version)
    if "styles" in inspect.signature(shell.page).parameters:
        return shell.page(title, description, main, styles=("registry.css",), **kwargs)
    # registry.css right after the shared stylesheet
    text = shell.page(title, description, main, **kwargs)
    return text.replace(f'{prefix}{CSS_TAG}', f'{prefix}{CSS_TAG}\n  <link rel="stylesheet" href="{prefix}assets/css/registry.css">', 1)


def card(pkg: dict, base: str) -> str:
    name = esc(pkg["name"])
    keywords = "".join(f'<li>{esc(k)}</li>' for k in pkg.get("keywords", []))
    return f"""          <li class="pkg-card registry-card">
            <h3><a href="{base}view.html?name={name}"><code>{name}</code></a> <span class="pkg-version">{esc(pkg.get("version", ""))}</span></h3>
            <p>{esc(pkg.get("description", ""))}</p>
            <p class="registry-card-meta"><span>by {esc(pkg.get("owner", "tb"))}</span></p>
            {f'<ul class="registry-tags" aria-label="Keywords">{keywords}</ul>' if keywords else ''}
          </li>"""


def list_page(packages: list[dict], version: str) -> str:
    cards = "\n".join(card(p, "packages/") for p in packages)
    main = f"""    <div class="container registry" data-registry-page="list" data-base="packages/" data-bundled="packages/bundled.json">
      <header class="registry-hero">
        <p class="registry-kicker">code.tanisha.ceo/packages</p>
        <h1>{TITLE}</h1>
        <p class="lead">TB code other people wrote, installed into your project with one command. Every new package is reviewed before it is listed.</p>
        <form class="registry-search" role="search" action="packages.html" method="get" data-registry-search>
          <label class="visually-hidden" for="pkg-q">Search packages</label>
          <input id="pkg-q" name="q" type="search" placeholder="Search by name, keyword or description" autocomplete="off" maxlength="100">
          <button class="button button-primary" type="submit">Search</button>
        </form>
        <p class="registry-links"><a href="packages/publish.html">Publish a package</a><a href="packages/account.html">Your packages</a><a href="#install">How to install</a></p>
      </header>
      <section class="registry-section" aria-labelledby="list-title">
        <div class="registry-section-head">
          <h2 id="list-title">Packages</h2>
          <p class="registry-status" data-registry-status aria-live="polite">{len(packages)} packages</p>
        </div>
        <ul class="pkg-grid registry-list" data-registry-list>
{cards}
        </ul>
      </section>
      <section class="registry-section md-page" id="install" aria-labelledby="install-title">
        <h2 id="install-title">Install a package</h2>
        <ol>
          <li>In your project folder, make a <code>tb.toml</code> once: <code>tb init</code>.</li>
          <li>Add the package: <code>tb add tb-tables</code>. It lands in <code>tb_packages/</code>, and <code>tb.lock</code> records the exact version and its sha256.</li>
          <li>Import it by name: <code>import "tb-tables" as tables</code>.</li>
        </ol>
        <pre class="md-code"><code>tb init
tb add tb-tables              # the newest version (recorded as ^1.0.0)
tb add tb-tables@^1.0         # any 1.x from 1.0
tb add tb-tables@"~1.0.2"     # 1.0.x from 1.0.2
tb add tb-tables@1.0.0        # exactly this version
tb search tables              # find packages
tb info tb-tables             # versions, owner, checksums
tb update                     # newer versions, within the ranges in tb.toml
tb remove tb-tables</code></pre>
        <p>Commit <code>tb.toml</code> and <code>tb.lock</code>: anyone who runs <code>tb add</code> with no arguments gets the same versions, checked against the same checksums. You can still install straight from a zip or a git URL: <code>tb add https://example.com/my-package.zip</code>.</p>
        <p>Only install packages you trust: a package's code runs with the same rights as your program. Reviews catch obvious problems, not every bug. If something looks wrong, use <strong>Report</strong> on the package's page.</p>
      </section>
    </div>"""
    return page(f"{TITLE} · TB", "The TB Package Index: search TB packages, install them with tb add and publish your own.",
                main, "", version)


def view_page(version: str) -> str:
    main = f"""    <div class="container registry" data-registry-page="view" data-base="" data-bundled="bundled.json">
      <nav class="registry-crumbs" aria-label="Breadcrumb"><a href="../packages.html">{TITLE}</a></nav>
      <header class="registry-pkg-head">
        <h1 data-pkg-title>Package</h1>
        <p class="lead" data-pkg-description>Loading the package…</p>
        <ul class="registry-tags" data-pkg-keywords aria-label="Keywords"></ul>
      </header>
      <p class="registry-message" data-registry-message hidden></p>
      <div class="registry-layout" data-pkg-body hidden>
        <article class="registry-readme md-page" aria-label="README" data-pkg-readme></article>
        <aside class="registry-side" aria-label="Package details">
          <h2>Install</h2>
          <pre class="md-code registry-install"><code data-pkg-install></code></pre>
          <p class="registry-note">Then <code data-pkg-import></code> in your program.</p>
          <dl class="registry-facts" data-pkg-facts></dl>
          <h2>Versions</h2>
          <div class="registry-table-wrap">
            <table class="registry-table">
              <thead><tr><th scope="col">Version</th><th scope="col">Published</th><th scope="col">Downloads</th></tr></thead>
              <tbody data-pkg-versions></tbody>
            </table>
          </div>
          <section class="registry-report" aria-labelledby="report-title">
            <h2 id="report-title">Report</h2>
            <p data-report-signin>Something wrong with this package (malware, spam, a stolen name)? <a href="publish.html">Sign in</a> to report it to the index admins.</p>
            <form class="registry-form" data-report-form hidden>
              <label for="report-reason">What's wrong?</label>
              <textarea id="report-reason" name="reason" rows="3" minlength="5" maxlength="1000" required></textarea>
              <button class="button" type="submit">Send report</button>
              <p class="registry-form-status" data-form-status aria-live="polite"></p>
            </form>
          </section>
        </aside>
      </div>
      <noscript><p class="registry-message">This page needs JavaScript to show a package. The <a href="../packages.html">list of packages</a> works without it.</p></noscript>
    </div>"""
    return page(f"Package · {TITLE}", "A package in the TB Package Index: README, versions, checksums and how to install it.",
                main, "../", version)


SIGN_IN = '<a class="button button-primary" href="/api/registry/auth/login?next={next}">Sign in with GitHub</a>'


def publish_page(version: str) -> str:
    main = f"""    <div class="container registry" data-registry-page="publish" data-base="">
      <nav class="registry-crumbs" aria-label="Breadcrumb"><a href="../packages.html">{TITLE}</a></nav>
      <header class="registry-pkg-head">
        <h1>Publish a package</h1>
        <p class="lead">Sign in with GitHub, make an API token, and run <code>tb publish</code> in your package's folder.</p>
      </header>
      <p class="registry-message registry-error" data-registry-error hidden role="alert"></p>
      <div class="registry-layout">
        <article class="md-page registry-steps">
          <h2 id="steps">Steps</h2>
          <ol>
            <li>Give <code>[package]</code> in <code>tb.toml</code> a name, a version and a one-line description (keywords and a license help people find it):
<pre class="md-code"><code>[package]
name = "tb-greet"
version = "1.0.0"
description = "Friendly greetings."
license = "MIT"
keywords = ["greeting", "text"]
main = "lib.tb"</code></pre></li>
            <li>Put the code people import in <code>lib.tb</code> (or the file named by <code>main</code>). Mark public names with <code>export</code> and write a <code>##</code> doc comment above each. Add a <code>README.md</code>: it becomes the package's page here.</li>
            <li>Test it: <code>tb test</code>, <code>tb fmt</code>, <code>tb check</code>.</li>
            <li>Sign in below and make a token, then run <code>tb login</code> and paste it. The token is stored in your user folder, readable only by you.</li>
            <li>Run <code>tb publish --dry-run</code> to see exactly what would be uploaded, then <code>tb publish</code>.</li>
          </ol>
          <pre class="md-code"><code>$ tb login
$ tb publish
Checked tb-greet 1.0.0: 4 files, 3 KB zipped, entry lib.tb.
Uploading to https://code.tanisha.ceo/api/registry ...
Submitted tb-greet 1.0.0 for review.</code></pre>
          <h2 id="rules">The rules</h2>
          <ul>
            <li><strong>Review.</strong> The first version of a new package waits for an admin's approval before it is listed. After that, new versions from the package's owner are published at once.</li>
            <li><strong>Names</strong> are 3 to 40 characters of lower-case letters, digits and <code>-</code>. Standard library names (<code>json</code>, <code>http</code>, ...) and a few words like <code>tb</code> are reserved; names one letter away from an existing package get a closer look.</li>
            <li><strong>Versions</strong> follow semantic versioning (<code>1.2.3</code>, <code>2.0.0-beta.1</code>) and can't be replaced once published. To take one back, <em>yank</em> it: <code>tb yank tb-greet@1.0.1 --reason "breaks on Windows"</code>. Version ranges skip yanked versions; projects that pin one exactly still install it.</li>
            <li><strong>Contents.</strong> A zip of up to 4 MB with <code>tb.toml</code>, an entry <code>.tb</code> file and text files (<code>.tb .toml .md .txt .json .csv .yml</code>, LICENSE, ...), plus PNG, JPEG, GIF or WebP images for the README. No links, no hidden files, no programs. <code>tb publish</code> checks all of this before it uploads.</li>
            <li>At most 10 uploads an hour per account.</li>
          </ul>
        </article>
        <aside class="registry-side" aria-labelledby="tokens-title">
          <section class="registry-panel" id="tokens">
            <h2 id="tokens-title">Your API tokens</h2>
            <div data-auth-unknown><p>Checking whether you're signed in…</p></div>
            <div data-auth-offline hidden><p>The package index API isn't reachable from here (a local preview?). On code.tanisha.ceo this is where you sign in and make tokens.</p></div>
            <div data-auth-unavailable hidden><p>Sign-in isn't switched on yet. Check back soon.</p></div>
            <div data-auth-out hidden>
              <p>Sign in to make a token for <code>tb publish</code>. The index only reads your public GitHub profile (your user name).</p>
              <p>{SIGN_IN.format(next="/packages/publish")}</p>
            </div>
            <div data-auth-in hidden>
              <p class="registry-whoami">Signed in as <strong data-user-login></strong>. <button class="link-button" type="button" data-sign-out>Sign out</button></p>
              <div class="registry-new-token" data-new-token hidden>
                <p><strong>Your new token.</strong> Copy it now: it won't be shown again.</p>
                <pre class="md-code"><code data-new-token-value></code></pre>
                <p><button class="button" type="button" data-copy-token>Copy</button> then run <code>tb login</code> and paste it.</p>
              </div>
              <form class="registry-form" data-token-form>
                <label for="token-name">Token name</label>
                <input id="token-name" name="name" type="text" maxlength="40" placeholder="laptop" autocomplete="off">
                <button class="button button-primary" type="submit">Make a token</button>
                <p class="registry-form-status" data-form-status aria-live="polite"></p>
              </form>
              <ul class="registry-token-list" data-token-list></ul>
              <p><a href="account.html">Your packages and their review status</a></p>
            </div>
          </section>
        </aside>
      </div>
    </div>"""
    return page(f"Publish a package · {TITLE}", "How to publish a TB package: sign in with GitHub, make an API token and run tb publish.",
                main, "../", version)


def account_page(version: str) -> str:
    main = f"""    <div class="container registry" data-registry-page="account" data-base="">
      <nav class="registry-crumbs" aria-label="Breadcrumb"><a href="../packages.html">{TITLE}</a></nav>
      <header class="registry-pkg-head">
        <h1>Your packages</h1>
        <p class="lead">Every package you have published and where each version stands: waiting for review, published, rejected (with the reviewer's reason) or yanked.</p>
      </header>
      <div data-auth-unknown><p>Checking whether you're signed in…</p></div>
      <div data-auth-offline hidden><p class="registry-message">The package index API isn't reachable from here (a local preview?).</p></div>
      <div data-auth-unavailable hidden><p class="registry-message">Sign-in isn't switched on yet. Check back soon.</p></div>
      <div data-auth-out hidden><p>Sign in to see your packages.</p><p>{SIGN_IN.format(next="/packages/account")}</p></div>
      <div data-auth-in hidden>
        <p class="registry-whoami">Signed in as <strong data-user-login></strong>. <a href="publish.html#tokens">API tokens</a> · <button class="link-button" type="button" data-sign-out>Sign out</button></p>
        <p class="registry-message" data-registry-message hidden></p>
        <div data-account-list></div>
      </div>
    </div>"""
    return page(f"Your packages · {TITLE}", "Your packages in the TB Package Index and their review status.", main, "../", version)


def admin_page(version: str) -> str:
    main = f"""    <div class="container registry" data-registry-page="admin" data-base="">
      <nav class="registry-crumbs" aria-label="Breadcrumb"><a href="../packages.html">{TITLE}</a></nav>
      <header class="registry-pkg-head">
        <h1>Review queue</h1>
        <p class="lead">New packages and versions waiting for approval, reports from users, and the log of admin actions. Only index admins can use this page; the API checks every request.</p>
      </header>
      <div data-auth-unknown><p>Checking whether you're signed in…</p></div>
      <div data-auth-offline hidden><p class="registry-message">The package index API isn't reachable from here (a local preview?).</p></div>
      <div data-auth-unavailable hidden><p class="registry-message">Sign-in isn't switched on yet.</p></div>
      <div data-auth-out hidden><p>Sign in as an index admin.</p><p>{SIGN_IN.format(next="/packages/admin")}</p></div>
      <div data-auth-in hidden>
        <p class="registry-whoami">Signed in as <strong data-user-login></strong>. <button class="link-button" type="button" data-sign-out>Sign out</button></p>
        <p class="registry-message" data-registry-message hidden></p>
        <div data-admin-body hidden>
          <section class="registry-section" aria-labelledby="queue-title">
            <h2 id="queue-title">Waiting for review</h2>
            <div data-admin-queue></div>
          </section>
          <section class="registry-section" aria-labelledby="reports-title">
            <h2 id="reports-title">Reports</h2>
            <div data-admin-reports></div>
          </section>
          <section class="registry-section" aria-labelledby="actions-title">
            <h2 id="actions-title">Yank or remove</h2>
            <form class="registry-form registry-form-inline" data-admin-action>
              <label for="act-name">Package</label>
              <input id="act-name" name="name" type="text" required maxlength="40" autocomplete="off">
              <label for="act-version">Version</label>
              <input id="act-version" name="version" type="text" maxlength="64" placeholder="1.2.3 (empty: whole package)" autocomplete="off">
              <label for="act-kind">Action</label>
              <select id="act-kind" name="action">
                <option value="yank">Yank the version</option>
                <option value="unyank">Undo a yank</option>
                <option value="remove-version">Remove the version</option>
                <option value="remove-package">Remove the whole package</option>
              </select>
              <label for="act-reason">Reason</label>
              <input id="act-reason" name="reason" type="text" maxlength="500" autocomplete="off">
              <button class="button" type="submit">Do it</button>
              <p class="registry-form-status" data-form-status aria-live="polite"></p>
            </form>
          </section>
          <section class="registry-section" aria-labelledby="audit-title">
            <h2 id="audit-title">Recent admin actions</h2>
            <ul class="registry-audit" data-admin-audit></ul>
          </section>
        </div>
      </div>
    </div>"""
    return page(f"Review queue · {TITLE}", "The TB Package Index review queue for admins.", main, "../", version)


def render(out: Path, version: str) -> list[str]:
    """Write the index pages into the built site; returns their paths."""
    packages = bundled()
    folder = out / "packages"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "bundled.json").write_text(json.dumps({"packages": packages}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    pages = {
        out / "packages.html": list_page(packages, version),
        folder / "view.html": view_page(version),
        folder / "publish.html": publish_page(version),
        folder / "account.html": account_page(version),
        folder / "admin.html": admin_page(version),
    }
    for path, text in pages.items():
        path.write_text(text, encoding="utf-8")
    return [p.relative_to(out).as_posix() for p in [*pages, folder / "bundled.json"]]
