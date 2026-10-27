"""The shared page shell (head, header, footer) for pages the build generates.

The hand-written pages in site/*.html carry the same markup; if you change the
header or footer here, change it there too (tests/test_site.py checks that
they match).
"""

from __future__ import annotations

import html

RELEASE_DATE_HUMAN = "26 October 2026"


def whats_new_url(prefix: str = "") -> str:
    return f"{prefix}whats-new.html"


LOGO = ('<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">'
        '<rect width="32" height="32" rx="8" fill="#b3123e"/>'
        '<path d="M9.5 7.5v11.2c0 1.7.9 2.6 2.6 2.6M6.6 11.8h6.2" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M16.4 6.8v14.6" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'
        '<circle cx="20.4" cy="17.3" r="4.1" fill="none" stroke="#fff" stroke-width="2.6"/>'
        '<rect x="25.6" y="19.4" width="3.6" height="2.8" rx="0.8" fill="#f2a93b"/></svg>')
MENU_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>')
PLAY_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="currentColor">'
             '<path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.6-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>')
DL_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
           'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14"/></svg>')

NAV = [("docs.html", "Learn"), ("library.html", "Library"), ("tools.html", "Tools"),
       ("examples.html", "Examples"), ("playground.html", "Playground"), ("download.html", "Download")]


def stats_script(prefix: str = "") -> str:
    """The <script> tag for assets/js/stats.js (the visit and download
    counters), which every page loads in its <head> right after theme.js."""
    return f'<script src="{prefix}assets/js/stats.js" defer></script>'


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def header(current: str = "", prefix: str = "", version: str = "3.0.0") -> str:
    links = []
    for href, label in NAV:
        attrs = f'href="{prefix}{href}"'
        if href == "download.html":
            attrs += ' class="nav-cta"'
        if href == current:
            attrs += ' aria-current="page"'
        links.append(f"        <a {attrs}>{label}</a>")
    nav = "\n".join(links)
    return f"""  <header class="site-header">
    <div class="header-inner">
      <a class="brand" href="{prefix}index.html" aria-label="TB home">{LOGO}<span>TB</span></a>
      <a class="version-badge" href="{whats_new_url(prefix)}" aria-label="Version {version}: what's new">v<span data-version>{version}</span></a>
      <button class="icon-button nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav" aria-label="Menu">{MENU_ICON}</button>
      <nav class="site-nav" id="site-nav" aria-label="Main">
{nav}
      </nav>
      <button class="theme-toggle" type="button" aria-label="Switch light or dark mode"></button>
    </div>
  </header>"""


def footer(prefix: str = "", version: str = "3.0.0") -> str:
    p = prefix
    return f"""  <footer class="site-footer">
    <div class="footer-inner">
      <div class="footer-brand">
        <a class="brand" href="{p}index.html" aria-label="TB home">{LOGO}<span>TB</span></a>
        <p>A friendly programming language for desktop apps, security tools and everything in between.</p>
      </div>
      <nav aria-labelledby="footer-learn">
        <h2 id="footer-learn">Learn</h2>
        <ul>
          <li><a href="{p}docs.html">Guide</a></li>
          <li><a href="{p}docs.html#tb3">TB 3.0 lessons</a></li>
          <li><a href="{p}docs.html#guides">Tutorials</a></li>
          <li><a href="{p}library.html">Library reference</a></li>
          <li><a href="{p}examples.html">Examples and apps</a></li>
        </ul>
      </nav>
      <nav aria-labelledby="footer-tools">
        <h2 id="footer-tools">Tools</h2>
        <ul>
          <li><a href="{p}playground.html">Playground</a></li>
          <li><a href="{p}tools.html">Command-line tools</a></li>
          <li><a href="{p}tools.html#vscode">VS Code extension</a></li>
          <li><a href="{p}packages.html">Packages</a></li>
          <li><a href="{p}download.html">Download</a></li>
        </ul>
      </nav>
      <nav aria-labelledby="footer-project">
        <h2 id="footer-project">Project</h2>
        <ul>
          <li><a href="{whats_new_url(p)}">What's new in 3.0</a></li>
          <li><a href="{p}migration.html">Moving from 2.x</a></li>
          <li><a href="{p}spec.html">Language specification</a></li>
          <li><a href="{p}download.html#verify">Checksums</a></li>
          <li><a href="{p}index.html#why">Why TB</a></li>
        </ul>
      </nav>
    </div>
    <div class="footer-bottom">
      <p>TB — a friendly programming language.</p>
      <p class="footer-stats" data-stats hidden><span data-stat-wrap hidden><span data-stat="downloads"></span> downloads</span><span data-stat-wrap hidden><span data-stat="visits"></span> visits</span></p>
      <p>TB <span data-version>{version}</span>, released {RELEASE_DATE_HUMAN}.</p>
    </div>
  </footer>"""


def page(title: str, description: str, main: str, *, current: str = "", prefix: str = "",
         scripts: tuple[str, ...] = (), version: str = "3.0.0") -> str:
    tags = "\n".join(f'  <script src="{prefix}assets/js/{s}" defer></script>' for s in scripts)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <meta name="description" content="{html.escape(description, quote=True)}">
  <meta name="theme-color" content="#b3123e">
  <link rel="icon" href="{prefix}assets/img/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{prefix}assets/css/style.css">
  <script src="{prefix}assets/js/config.js"></script>
  <script src="{prefix}assets/js/theme.js"></script>
  {stats_script(prefix)}
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
{header(current, prefix, version)}
  <main id="main">
{main}
  </main>
{footer(prefix, version)}
{tags}
</body>
</html>
"""
