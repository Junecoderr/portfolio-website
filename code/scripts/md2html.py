"""A small Markdown -> HTML converter for the website build (standard library only).

Covers what the project's Markdown files use: ATX headings (with ids),
paragraphs, fenced and indented code, bullet and numbered lists (with
continuation lines and one level of nesting), tables, block quotes, rules,
and inline `code`, **bold**, *emphasis* and [links](url).

Links: code.tanisha.ceo URLs become site-relative; links to the project's own
Markdown files map to their pages (see LINK_MAP); any other link to a file in
the source tree (or to a code host) is shown as plain text, because the site
never links to the source repository.
"""

from __future__ import annotations

import html
import re

LINK_MAP = {
    "spec/SPEC.md": "spec.html", "SPEC.md": "spec.html",
    "MIGRATION-3.0.md": "migration.html", "../MIGRATION-3.0.md": "migration.html",
}
SITE_HOST = "https://code.tanisha.ceo"


def slug(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).lower()
    text = re.sub(r"[`*_()\[\]{}:;,.!?'\"/\\]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "section"


def _link(label: str, url: str, prefix: str) -> str:
    url = url.strip()
    if url.startswith(SITE_HOST):
        path = url[len(SITE_HOST):].lstrip("/") or "index.html"
        if "." not in path.split("/")[-1] and "#" not in path:
            path += ".html"
        return f'<a href="{prefix}{html.escape(path, quote=True)}">{label}</a>'
    base = url.split("#")[0]
    if base in LINK_MAP:
        frag = url[len(base):]
        return f'<a href="{prefix}{LINK_MAP[base]}{html.escape(frag, quote=True)}">{label}</a>'
    if url.startswith("#"):
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
    if re.match(r"https?://", url) and "github" not in url.lower():
        return f'<a href="{html.escape(url, quote=True)}" rel="noopener">{label}</a>'
    return label  # a file in the source tree: text only


def inline(text: str, prefix: str = "") -> str:
    """Escape text and apply inline Markdown (bold and links may span `code`)."""
    codes: list[str] = []

    def keep(m):
        codes.append(f"<code>{html.escape(m.group(1), quote=False)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`+([^`]*?)`+", keep, text)
    s = html.escape(s, quote=False)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: _link(m.group(1), html.unescape(m.group(2)), prefix), s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


def _table(rows: list[str], prefix: str) -> str:
    def cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        # split on | not inside backticks
        out, cur, tick = [], "", False
        for ch in row:
            if ch == "`":
                tick = not tick
            if ch == "|" and not tick:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        out.append(cur)
        return [c.strip() for c in out]
    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    th = "".join(f'<th scope="col">{inline(c, prefix)}</th>' for c in head)
    trs = "\n".join("<tr>" + "".join(f"<td>{inline(c, prefix)}</td>" for c in r) + "</tr>" for r in body)
    return (f'<div class="table-wrap" role="region" tabindex="0" aria-label="Table">'
            f'<table class="kw-table"><thead><tr>{th}</tr></thead><tbody>\n{trs}\n</tbody></table></div>')


def _code(code: str, lang: str) -> str:
    body = html.escape(code.rstrip("\n"), quote=False)
    if lang in ("tb", ""):
        if lang == "tb":
            return f'<pre class="tb-code"><code>{body}</code></pre>'
    return f'<pre class="md-code"><code>{body}</code></pre>'


def convert(text: str, *, shift: int = 0, prefix: str = "", drop_title: bool = False,
            id_prefix: str = "") -> tuple[str, str, list[tuple[int, str, str]]]:
    """Markdown -> (html, title, headings). title is the first level-1 heading;
    with drop_title it is left out of the HTML. shift adds to heading levels.
    headings: [(level, id, text)] for a table of contents."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    headings: list[tuple[int, str, str]] = []
    title = ""
    used: set[str] = set()
    i = 0
    para: list[str] = []

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(p.strip() for p in para), prefix)}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        fence = re.match(r"^(\s*)(```+|~~~+)\s*([\w+-]*)", line)
        if fence:
            flush()
            marker, lang = fence.group(2), fence.group(3)
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith(marker):
                block.append(lines[i][len(fence.group(1)):] if lines[i].startswith(fence.group(1)) else lines[i])
                i += 1
            i += 1
            out.append(_code("\n".join(block), lang))
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            flush()
            level = len(h.group(1))
            content = inline(h.group(2), prefix)
            if level == 1 and not title:
                title = re.sub(r"<[^>]+>", "", content)
                if drop_title:
                    i += 1
                    continue
            hid = id_prefix + slug(h.group(2))
            base, n = hid, 2
            while hid in used:
                hid = f"{base}-{n}"
                n += 1
            used.add(hid)
            lvl = min(6, level + shift)
            headings.append((level, hid, re.sub(r"<[^>]+>", "", content)))
            out.append(f'<h{lvl} id="{hid}">{content}</h{lvl}>')
            i += 1
            continue
        if re.match(r"^\s*(---+|\*\*\*+|___+)\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows, prefix))
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            inner, _, _ = convert("\n".join(quote), shift=shift, prefix=prefix)
            out.append(f"<blockquote>{inner}</blockquote>")
            continue
        if line.startswith("    ") and not para and stripped:
            block = []
            while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
                block.append(lines[i][4:])
                i += 1
            while block and not block[-1].strip():
                block.pop()
            out.append(_code("\n".join(block), "text"))
            continue
        item = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if item and len(item.group(1)) < 4:
            flush()
            out.append(_list(lines, i, prefix))
            i = _list_end(lines, i)
            continue
        if not stripped:
            flush()
            i += 1
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out), title, headings


ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def _list_end(lines: list[str], i: int) -> int:
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if ITEM_RE.match(nxt) or nxt.startswith("  "):
                i += 1
                continue
            return i
        if ITEM_RE.match(line) or line.startswith(" ") or line.startswith("\t"):
            i += 1
            continue
        # a lazy continuation line of the previous item
        if lines[i - 1].strip():
            i += 1
            continue
        return i
    return i


def _list(lines: list[str], i: int, prefix: str) -> str:
    end = _list_end(lines, i)
    first = ITEM_RE.match(lines[i])
    base_indent = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    items: list[list[str]] = []
    for line in lines[i:end]:
        m = ITEM_RE.match(line)
        if m and len(m.group(1)) <= base_indent + 1:
            items.append([m.group(3)])
        elif items:
            items[-1].append(line)
    html_items = []
    for chunk in items:
        text_lines, nested = [chunk[0]], []
        for extra in chunk[1:]:
            if ITEM_RE.match(extra) or nested:
                nested.append(extra)
            else:
                text_lines.append(extra.strip())
        body = inline(" ".join(t for t in text_lines if t), prefix)
        if nested:
            dedent = min((len(n) - len(n.lstrip()) for n in nested if n.strip()), default=0)
            body += _list([n[dedent:] for n in nested], 0, prefix)
        html_items.append(f"<li>{body}</li>")
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(html_items) + f"</{tag}>"
