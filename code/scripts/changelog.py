"""TB's release notes, read from CHANGELOG.md.

scripts/build_site.py calls render() to write:

    releases.html   every release, newest first (the Releases page)
    releases.xml    the same as an Atom feed, for feed readers

and the release workflow prints one version's notes for its GitHub release:

    uv run python scripts/changelog.py notes 3.0.0

CHANGELOG.md explains the format. "## Unreleased" is a draft and is never
published. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import md2html

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
SITE_URL = "https://code.tanisha.ceo"
RELEASE_RE = re.compile(r"^## (\d+\.\d+\.\d+(?:-[\w.]+)?) - (\d{4}-\d{2}-\d{2})\s*$")
SECTIONS = ("Breaking", "Security", "Added", "Changed", "Fixed", "Removed")


@dataclass
class Release:
    version: str
    date: dt.date
    summary: str = ""  # Markdown
    sections: list[tuple[str, list[str]]] = field(default_factory=list)  # (title, Markdown bullets)

    @property
    def anchor(self) -> str:
        return f"v{self.version}"

    @property
    def date_human(self) -> str:
        return f"{self.date.day} {self.date:%B %Y}"


def releases(path: Path = CHANGELOG) -> list[Release]:
    """The published releases, newest first, as written in the file."""
    out: list[Release] = []
    current: Release | None = None
    summary: list[str] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            m = RELEASE_RE.match(line)
            if not m and line.strip() != "## Unreleased":
                raise ValueError(f"CHANGELOG.md line {n}: expected '## <version> - <YYYY-MM-DD>', got {line!r}")
            current = Release(m.group(1), dt.date.fromisoformat(m.group(2))) if m else None
            if current:
                out.append(current)
            summary = []
        elif current is None:
            continue
        elif line.startswith("### "):
            title = line[4:].strip()
            if title not in SECTIONS:
                raise ValueError(f"CHANGELOG.md line {n}: unknown section {title!r} (use one of {', '.join(SECTIONS)})")
            current.sections.append((title, []))
        elif line.startswith("- "):
            if not current.sections:
                raise ValueError(f"CHANGELOG.md line {n}: a bullet needs a ### section above it")
            current.sections[-1][1].append(line[2:].strip())
        elif line.startswith("  ") and current.sections and current.sections[-1][1]:
            current.sections[-1][1][-1] += " " + line.strip()  # a bullet's continuation line
        elif line.strip() and not current.sections:
            summary.append(line.strip())
            current.summary = " ".join(summary)
    return out


def kind(release: Release, older: Release | None) -> str:
    """Major, Minor or Patch, from the version before it."""
    if older is None:
        return "Major"
    new, old = (r.version.split("-")[0].split(".") for r in (release, older))
    return "Major" if new[0] != old[0] else "Minor" if new[1] != old[1] else "Patch"


def notes(version: str, path: Path = CHANGELOG) -> str:
    """One release's notes as Markdown (for the GitHub release)."""
    for r in releases(path):
        if r.version == version:
            parts = [r.summary] if r.summary else []
            for title, bullets in r.sections:
                parts.append(f"### {title}\n\n" + "\n".join(f"- {b}" for b in bullets))
            parts.append(f"Downloads and install steps: {SITE_URL}/download")
            return "\n\n".join(parts) + "\n"
    raise KeyError(f"{version} is not in CHANGELOG.md")


def _release_html(r: Release, older: Release | None, latest: bool) -> str:
    k = kind(r, older)
    badges = f'<span class="rel-badge rel-{k.lower()}">{k}</span>'
    if latest:
        badges += '<span class="rel-badge rel-latest">Latest</span>'
    body = [f'          <p class="rel-summary">{md2html.inline(r.summary)}</p>'] if r.summary else []
    for title, bullets in r.sections:
        items = "\n".join(f"            <li>{md2html.inline(b)}</li>" for b in bullets)
        body.append(f'          <h3 class="rel-h rel-h-{title.lower()}">{title}</h3>\n          <ul>\n{items}\n          </ul>')
    body_html = "\n".join(body)
    return f"""      <li class="rel" id="{r.anchor}">
        <div class="rel-meta">
          <h2 class="rel-version"><a href="#{r.anchor}">TB {html.escape(r.version)}</a></h2>
          <time datetime="{r.date.isoformat()}">{r.date_human}</time>
          <p class="rel-badges">{badges}</p>
        </div>
        <div class="rel-body">
{body_html}
        </div>
      </li>"""


def page_html(rels: list[Release], version: str) -> str:
    import site_shell as shell

    items = "\n".join(_release_html(r, rels[i + 1] if i + 1 < len(rels) else None, i == 0)
                      for i, r in enumerate(rels))
    latest = rels[0]
    main = f"""    <div class="container rel-page">
      <header class="rel-hero">
        <h1>Releases</h1>
        <p class="lead">Every version of TB, newest first: what's new, what changed and what might affect your programs.</p>
        <p class="rel-actions"><a class="button button-primary" href="download.html">Download TB {html.escape(latest.version)}</a><a class="button" href="releases.xml">Subscribe to the feed</a></p>
      </header>
      <ol class="rel-list">
{items}
      </ol>
    </div>"""
    text = shell.page("Releases · TB", f"TB release notes: every version, newest first. Latest: TB {latest.version}.",
                      main, version=version, styles=("releases.css",))
    feed = '\n  <link rel="alternate" type="application/atom+xml" title="TB releases" href="releases.xml">'
    return text.replace("</title>", "</title>" + feed, 1)


def feed_xml(rels: list[Release]) -> str:
    def x(s: str) -> str:
        return html.escape(s, quote=True)

    entries = []
    for i, r in enumerate(rels):
        content = _release_html(r, rels[i + 1] if i + 1 < len(rels) else None, False)
        # Feed readers open links from elsewhere, so make them absolute.
        content = re.sub(r'href="(?!https?:|#)', f'href="{SITE_URL}/', content).replace('href="#', f'href="{SITE_URL}/releases#')
        stamp = f"{r.date.isoformat()}T00:00:00Z"
        entries.append(f"""  <entry>
    <title>TB {x(r.version)}</title>
    <id>{SITE_URL}/releases#{r.anchor}</id>
    <link rel="alternate" type="text/html" href="{SITE_URL}/releases#{r.anchor}"/>
    <updated>{stamp}</updated>
    <summary>{x(re.sub(r"[`*]|\[([^]]*)\]\([^)]*\)", r"\1", r.summary))}</summary>
    <content type="html">{x(content)}</content>
  </entry>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>TB releases</title>
  <subtitle>Release notes for the TB programming language.</subtitle>
  <id>{SITE_URL}/releases</id>
  <link rel="self" type="application/atom+xml" href="{SITE_URL}/releases.xml"/>
  <link rel="alternate" type="text/html" href="{SITE_URL}/releases"/>
  <updated>{rels[0].date.isoformat()}T00:00:00Z</updated>
  <author><name>TB</name></author>
{chr(10).join(entries)}
</feed>
"""


def render(out: Path, version: str) -> list[Release]:
    rels = releases()
    if not rels or rels[0].version != version:
        raise SystemExit(f"error: the newest release in CHANGELOG.md is "
                         f"{rels[0].version if rels else 'missing'}, but tb.py says {version}. "
                         f"Add a '## {version} - YYYY-MM-DD' entry.")
    (out / "releases.html").write_text(page_html(rels, version), encoding="utf-8")
    (out / "releases.xml").write_text(feed_xml(rels), encoding="utf-8")
    return rels


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "notes":
        sys.stdout.write(notes(argv[1]))
        return 0
    print("usage: changelog.py notes <version>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
