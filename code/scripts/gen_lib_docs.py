"""Generate the standard-library reference pages of the TB website.

    uv run python scripts/gen_lib_docs.py [--out dist/site]

Writes <out>/library.html (all libraries, grouped, plus the built-in
functions and methods) and <out>/lib/<name>.html (one page per library with
every function's signature and description, and examples).

Everything comes from tb.list_libraries(), tb.describe_library() and
tb.describe_builtins(), so the pages can't drift from the code. Examples live
in scripts/lib_examples/:
    <name>.tb          runs in the browser (gets a Run button)
    <name>.desktop.tb  needs the TB app (shown, not run)
build_site.py calls this on every build; tests/test_site.py checks that every
library has an example and that the browser examples run.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tb  # noqa: E402
import name_guard  # noqa: E402
import site_shell as shell  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent / "lib_examples"

GROUPS = [
    ("core", "Core", "Everyday building blocks: numbers, text, dates, randomness, patterns and the computer you're on.",
     ["math", "text", "time", "random", "re", "os"]),
    ("data", "Files and data", "Read and write files, JSON and CSV.", ["fs", "json", "csv"]),
    ("security", "Security (SOC)", "Parse logs, detect attacks, pull out and defang indicators, hash and decode, and report.",
     ["logs", "detect", "ioc", "hash", "encode", "report"]),
    ("network", "Network and intel", "IP and CIDR maths, port checks, web requests and threat-intelligence lookups.",
     ["net", "http", "intel"]),
    ("gui", "Desktop GUI", "Windows, buttons, tables, menus and dialogs.", ["gui"]),
    ("testing", "Testing", "Checks for your own programs, run with tb test.", ["test"]),
]

DESKTOP_ONLY = {
    "gui": "opens real windows, so it needs the TB app on your computer",
    "http": "talks to websites, so it needs the TB app on your computer",
    "intel": "calls threat-intelligence services over the internet, so it needs the TB app on your computer",
}
PARTLY = {
    "net": "The IP and CIDR helpers work everywhere, including the playground. Lookups and port checks (resolve, reverse, tcp_check, check_ports, banner, my_ip) need the TB app on your computer.",
    "os": "os.run needs the TB app on your computer. In the playground, os.platform is \"browser\".",
    "fs": "In the playground, files live in a pretend folder inside your browser that holds the sample data in apps/data/. Nothing touches your real disk.",
}
NO_VALUES = {"os"}  # values that depend on the computer the docs were built on


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def rich(text: str) -> str:
    """Escape doc text; `code` becomes <code>."""
    out = esc(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


def first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text.strip(), re.S)
    return (m.group(1) if m else text).strip()


def all_groups() -> list[tuple[str, str, str, list[str]]]:
    libs = set(tb.list_libraries())
    groups = [(k, t, d, [n for n in names if n in libs]) for k, t, d, names in GROUPS]
    placed = {n for *_, names in groups for n in names}
    rest = sorted(libs - placed)
    if rest:
        groups.append(("more", "More", "Other libraries.", rest))
    return groups


def examples_for(name: str) -> list[tuple[str, bool]]:
    """[(code, runnable_in_browser)]"""
    found = []
    browser = EXAMPLES / f"{name}.tb"
    desktop = EXAMPLES / f"{name}.desktop.tb"
    if browser.exists():
        found.append((browser.read_text(encoding="utf-8").rstrip("\n"), True))
    if desktop.exists():
        found.append((desktop.read_text(encoding="utf-8").rstrip("\n"), False))
    return found


def signature_html(sig: str, name: str) -> str:
    # "extract(text, options?) -> map"  ->  name + (args) + returns
    m = re.match(r"^(\w+)(\(.*?\))?\s*(?:->\s*(.+))?$", sig)
    if not m:
        return f'<h3><span class="sig-name">{esc(sig)}</span></h3>', ""
    args = m.group(2) or ""
    rest = sig[len(m.group(1)) + len(args):].strip()
    alt = ""
    # Signatures like "min(a, b, ...) or min(list) -> number" keep their tail.
    returns = ""
    if rest.startswith("->"):
        returns = rest[2:].strip()
    elif rest:
        alt = rest
        if "->" in alt:
            alt, returns = [p.strip() for p in alt.rsplit("->", 1)]
    head = f'<h3><span class="sig-name">{esc(name)}</span><span class="sig-args">{esc(args)}{(" " + esc(alt)) if alt else ""}</span></h3>'
    return head, returns


def sidebar(current: str, prefix: str) -> str:
    parts = []
    for key, title, _, names in all_groups():
        items = []
        for n in names:
            cur = ' class="is-active" aria-current="page"' if n == current else ""
            items.append(f'            <li><a href="{prefix}lib/{n}.html"{cur}><code>{n}</code></a></li>')
        parts.append(f"          <h2>{esc(title)}</h2>\n          <ul>\n" + "\n".join(items) + "\n          </ul>")
    chevron = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
               'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    return f"""      <aside class="docs-sidebar" aria-label="Libraries">
        <button class="docs-nav-toggle" type="button" aria-expanded="false" aria-controls="docs-nav">Libraries {chevron}</button>
        <div class="docs-sidebar-inner" id="docs-nav">
          <ul>
            <li><a href="{prefix}library.html"{' class="is-active" aria-current="page"' if current == "" else ""}>All libraries</a></li>
            <li><a href="{prefix}library.html#builtins">Built-in functions</a></li>
          </ul>
{chr(10).join(parts)}
        </div>
      </aside>"""


def availability(name: str) -> str:
    if name in DESKTOP_ONLY:
        return f'<p class="note">This library {DESKTOP_ONLY[name]}. The examples here show the code; run them with <code>tb file.tb</code>.</p>'
    if name in PARTLY:
        return f'<p class="note">{esc(PARTLY[name])}</p>'
    return ""


def lib_page(name: str, version: str) -> str:
    d = tb.describe_library(name)
    prefix = "../"
    funcs = []
    for f in d["functions"]:
        head, returns = signature_html(f["signature"], f["name"])
        ret = f'<span class="sig-returns">returns {esc(returns)}</span>' if returns else ""
        doc = "".join(f"<p>{rich(p.strip())}</p>" for p in re.split(r"\n\s*\n", f["doc"]) if p.strip()) or "<p></p>"
        funcs.append(f"""        <article class="sig" id="fn-{f['name']}">
          <div class="sig-head">
            {head}
            {ret}
          </div>
          <div class="sig-body">{doc}</div>
        </article>""")
    values = ""
    if d["values"]:
        rows = []
        for v in d["values"]:
            shown = "depends on the computer" if name in NO_VALUES else v["value"]
            if len(shown) > 80:
                shown = shown[:77] + "..."
            code = f"<code>{esc(name)}.{esc(v['name'])}</code>"
            val = f"<em>{esc(shown)}</em>" if name in NO_VALUES else f"<code>{esc(shown)}</code>"
            rows.append(f"            <tr><td>{code}</td><td>{val}</td><td>{rich(v['doc'])}</td></tr>")
        values = f"""      <section id="values">
        <h2>Values</h2>
        <div class="table-wrap" role="region" tabindex="0" aria-label="Values of the {esc(name)} library">
          <table class="kw-table">
            <thead><tr><th scope="col">Name</th><th scope="col">Value</th><th scope="col">What it is</th></tr></thead>
            <tbody>
{chr(10).join(rows)}
            </tbody>
          </table>
        </div>
      </section>"""
    ex_html = []
    for code, runnable in examples_for(name):
        attrs = " data-runnable" if runnable else ' data-desktop'
        label = "" if runnable else '<p class="ex-note">Needs the TB app on your computer, so there is no Run button here.</p>'
        ex_html.append(f'        {label}<pre class="tb-code"{attrs}><code>{esc(code)}</code></pre>')
    examples = ""
    if ex_html:
        examples = f"""      <section id="examples">
        <h2>Example</h2>
{chr(10).join(ex_html)}
      </section>"""
    main = f"""    <div class="container docs">
{sidebar(name, prefix)}
      <article class="docs-main">
        <nav class="breadcrumb" aria-label="Breadcrumb"><a href="{prefix}library.html">Library</a> <span aria-hidden="true">/</span> <span aria-current="page">{esc(name)}</span></nav>
        <h1 class="lib-title"><code>{esc(name)}</code></h1>
        <p class="lead">{rich(d['doc'])}</p>
        {availability(name)}
        <pre class="tb-code"><code>import {esc(name)}</code></pre>
{examples}
      <section id="functions">
        <h2>Functions</h2>
        <div class="sig-list">
{chr(10).join(funcs)}
        </div>
      </section>
{values}
      </article>
      <nav class="on-this-page" aria-label="On this page">
        <div class="on-this-page-inner">
          <h2>On this page</h2>
          <ul id="on-this-page-list"></ul>
        </div>
      </nav>
    </div>"""
    return shell.page(f"{name} · TB library", first_sentence(d["doc"]), main, current="library.html",
                      prefix=prefix, scripts=("highlight.js", "docs.js", "runner.js"), version=version)


def index_page(version: str) -> str:
    groups = []
    for key, title, desc, names in all_groups():
        cards = []
        for n in names:
            d = tb.describe_library(n)
            where = "desktop" if n in DESKTOP_ONLY else ("browser and desktop" if n not in PARTLY else "mostly in the browser")
            tag_cls = "tag-desktop" if n in DESKTOP_ONLY else ""
            cards.append(f"""          <li>
            <a class="lib-card" href="lib/{n}.html">
              <span class="lib-card-name"><code>{n}</code></span>
              <span class="lib-card-doc">{esc(first_sentence(d['doc']))}</span>
              <span class="lib-card-meta"><span>{len(d['functions'])} functions</span><span class="lib-tag {tag_cls}">{where}</span></span>
            </a>
          </li>""")
        groups.append(f"""      <section id="{key}" aria-labelledby="{key}-title">
        <h2 id="{key}-title">{esc(title)}</h2>
        <p>{esc(desc)}</p>
        <ul class="lib-grid">
{chr(10).join(cards)}
        </ul>
      </section>""")
    b = tb.describe_builtins()
    brows = "\n".join(
        f'            <tr><td><code>{esc(f["signature"])}</code></td><td>{rich(f["doc"])}</td></tr>' for f in b["functions"])
    mtabs = []
    for kind, methods in b["methods"].items():
        rows = "\n".join(
            f'              <tr><td><code>.{esc(m["signature"])}</code></td><td>{rich(m["doc"])}</td></tr>' for m in methods)
        mtabs.append(f"""        <h3 id="methods-{kind}">Methods on {esc(PLURAL.get(kind, kind))}</h3>
        <div class="table-wrap" role="region" tabindex="0" aria-label="Methods on {esc(kind)} values">
          <table class="kw-table">
            <tbody>
{rows}
            </tbody>
          </table>
        </div>""")
    count = len(tb.list_libraries())
    main = f"""    <div class="container docs">
{sidebar("", "")}
      <article class="docs-main">
        <h1>Library</h1>
        <p class="lead">TB comes with {count} libraries, built into the app. Bring one in with <code>import</code>, then use its functions with a dot.</p>
        <pre class="tb-code" data-runnable><code>import ioc

let note = "Beacon to hxxp://evil[.]example[.]com from 203.0.113.9"
let found = ioc.extract(note)
say(found.urls, found.ips)</code></pre>
{chr(10).join(groups)}
      <section id="builtins" aria-labelledby="builtins-title">
        <h2 id="builtins-title">Built in, no import needed</h2>
        <p>These functions are always there, and every text, list, map and number has methods you call with a dot, like <code>"hi".upper()</code> or <code>names.sort()</code>.</p>
        <div class="table-wrap" role="region" tabindex="0" aria-label="Built-in functions">
          <table class="kw-table">
            <tbody>
{brows}
            </tbody>
          </table>
        </div>
{chr(10).join(mtabs)}
      </section>
      </article>
      <nav class="on-this-page" aria-label="On this page">
        <div class="on-this-page-inner">
          <h2>On this page</h2>
          <ul id="on-this-page-list"></ul>
        </div>
      </nav>
    </div>"""
    return shell.page("Library · TB", f"The {count} libraries that come with TB: files, JSON, logs, detection rules, IOCs, hashing, networking and desktop windows.",
                      main, current="library.html", scripts=("highlight.js", "docs.js", "runner.js"), version=version)


PLURAL = {"text": "text", "list": "lists", "map": "maps", "number": "numbers"}


def build(out: Path, version: str | None = None) -> list[Path]:
    version = version or tb.__version__
    out = Path(out)
    (out / "lib").mkdir(parents=True, exist_ok=True)
    written = []
    pages = {out / "library.html": index_page(version)}
    for name in tb.list_libraries():
        pages[out / "lib" / f"{name}.html"] = lib_page(name, version)
    for path, text in pages.items():
        if name_guard.contains_name(text):
            raise SystemExit(f"gen_lib_docs: {path.name} would contain the author's name; refusing to write it.")
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the TB library reference pages.")
    parser.add_argument("--out", default="dist/site", help="site folder to write into (default: dist/site)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    written = build(out)
    print(f"Wrote {len(written)} library pages to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
