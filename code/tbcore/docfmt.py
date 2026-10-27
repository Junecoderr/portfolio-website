"""The light structure of TB doc strings, and three ways to show it.

A doc string (after its first line, the signature) is a short summary, then
optional blocks separated by blank lines:

    Check a rule and fill in its defaults.

    Options:
    - name: what to call the alert.
    - level: how serious it is (default: "medium").
      - "low"
      - "high"

    Example:
        let r = detect.rule({"name": "Root login"})

A heading is a line like `Options:`, `Fields:`, `Returns:`, `Errors:` or
`Example:` on its own. Bullets start with `- `; a nested bullet starts with
two spaces then `- `; a line indented under a bullet continues it. In a
bullet, `name: explanation` shows the name as code. Under `Example:` every
line is TB code. Anything else is a plain paragraph.

parse() turns the text into blocks; to_html() (the website), to_text() (tb doc)
and to_markdown() (the language server's hover) show them.
"""

from __future__ import annotations

import html
import re
import textwrap

HEADING_RE = re.compile(r"^([A-Z][A-Za-z0-9 ,'/()-]{0,40}):$")
BULLET_RE = re.compile(r"^( *)- (.*)$")


def _is_code_heading(heading: str) -> bool:
    return heading.lower().startswith("example")


def split_term(text: str) -> tuple[str | None, str]:
    """"name: explanation" -> ("name", "explanation"); (None, text) when the
    part before ": " is not a name (it has spaces outside brackets/quotes)."""
    depth, quote = 0, None
    for i, ch in enumerate(text[:80]):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == ":" and text[i + 1:i + 2] in (" ", ""):
            term = text[:i].strip()
            if term:
                return term.strip("`") if term.count("`") == 2 and term.startswith("`") else term, text[i + 1:].strip()
            return None, text
        elif depth == 0 and ch == " " and text[i - 1:i] not in (",", "/") and text[i + 1:i + 3] not in ("or", "/ "):
            return None, text
    return None, text


def _items(lines: list[str]) -> list[dict]:
    """Bullet lines (and their continuations) -> nested items."""
    top: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for raw in lines:
        m = BULLET_RE.match(raw)
        if m:
            indent = len(m.group(1))
            term, text = split_term(m.group(2).strip())
            item = {"term": term, "text": text, "children": []}
            while stack and stack[-1][0] >= indent:
                stack.pop()
            (stack[-1][1]["children"] if stack else top).append(item)
            stack.append((indent, item))
        elif stack:
            item = stack[-1][1]
            item["text"] = (item["text"] + " " + raw.strip()).strip()
    return top


def parse(doc: str) -> list[dict]:
    """Blocks: {"kind": "para", "text"} / {"kind": "heading", "text"} /
    {"kind": "list", "items": [{"term", "text", "children"}]} / {"kind": "code", "text"}."""
    blocks: list[dict] = []
    code_mode = False
    for chunk in re.split(r"\n[ \t]*\n", (doc or "").strip("\n")):
        lines = chunk.rstrip().split("\n")
        if not chunk.strip():
            continue
        m = HEADING_RE.match(lines[0].strip())
        if m and not lines[0].startswith(" "):
            blocks.append({"kind": "heading", "text": m.group(1)})
            code_mode = _is_code_heading(m.group(1))
            lines = lines[1:]
            if not lines:
                continue
        elif code_mode and not all(ln.startswith("    ") or not ln.strip() for ln in lines):
            code_mode = False
        if code_mode:
            blocks.append({"kind": "code", "text": textwrap.dedent("\n".join(lines)).strip("\n")})
            continue
        para: list[str] = []
        i = 0
        while i < len(lines):
            if BULLET_RE.match(lines[i]):
                if para:
                    blocks.append({"kind": "para", "text": " ".join(p.strip() for p in para)})
                    para = []
                j = i + 1
                while j < len(lines) and (BULLET_RE.match(lines[j]) or lines[j].startswith(" ")):
                    j += 1
                blocks.append({"kind": "list", "items": _items(lines[i:j])})
                i = j
            else:
                para.append(lines[i])
                i += 1
        if para:
            blocks.append({"kind": "para", "text": " ".join(p.strip() for p in para)})
    return blocks


def summary(doc: str) -> str:
    """The first paragraph, as one line of plain text."""
    for block in parse(doc):
        if block["kind"] == "para":
            return block["text"]
    return ""


# -- HTML (the website) ----------------------------------------------------------------


def _rich(text: str) -> str:
    out = html.escape(text, quote=False)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


def _html_items(items: list[dict]) -> str:
    out = []
    for item in items:
        term = f'<code class="doc-term">{html.escape(item["term"], quote=False)}</code> ' if item["term"] else ""
        sub = _html_items(item["children"]) if item["children"] else ""
        out.append(f"<li>{term}{_rich(item['text'])}{sub}</li>")
    return '<ul class="doc-list">' + "".join(out) + "</ul>"


def to_html(doc: str) -> str:
    parts = []
    for block in parse(doc):
        kind = block["kind"]
        if kind == "para":
            parts.append(f"<p>{_rich(block['text'])}</p>")
        elif kind == "heading":
            parts.append(f'<p class="doc-h">{html.escape(block["text"], quote=False)}</p>')
        elif kind == "list":
            parts.append(_html_items(block["items"]))
        else:
            parts.append(f'<pre class="tb-code"><code>{html.escape(block["text"], quote=False)}</code></pre>')
    return "".join(parts)


# -- plain text (tb doc) -------------------------------------------------------------------


def _text_items(items: list[dict], indent: str, width: int) -> list[str]:
    out = []
    for item in items:
        body = (f"{item['term']}: " if item["term"] else "") + item["text"]
        out += textwrap.wrap(body, width, initial_indent=indent + "- ", subsequent_indent=indent + "  ",
                             break_long_words=False, break_on_hyphens=False) or [indent + "-"]
        out += _text_items(item["children"], indent + "  ", width)
    return out


def to_text(doc: str, indent: str = "", width: int = 78) -> list[str]:
    """Lines for a terminal, wrapped to width (counting the indent)."""
    lines: list[str] = []
    after_heading = False
    for block in parse(doc):
        kind = block["kind"]
        if kind == "para" and lines and lines[-1] != "" and not after_heading:
            lines.append("")
        after_heading = kind == "heading"
        if kind == "heading":
            if lines:
                lines.append("")
            lines.append(indent + block["text"] + ":")
        elif kind == "para":
            lines += textwrap.wrap(block["text"], width, initial_indent=indent, subsequent_indent=indent,
                                   break_long_words=False, break_on_hyphens=False)
        elif kind == "list":
            lines += _text_items(block["items"], indent, width)
        else:
            lines += [(indent + "    " + ln).rstrip() for ln in block["text"].split("\n")]
    return lines


# -- Markdown (language server hover) -----------------------------------------------------


def _md_items(items: list[dict], indent: str) -> list[str]:
    out = []
    for item in items:
        term = f"`{item['term']}`: " if item["term"] else ""
        out.append(f"{indent}- {term}{item['text']}")
        out += _md_items(item["children"], indent + "  ")
    return out


def to_markdown(doc: str) -> str:
    parts = []
    for block in parse(doc):
        kind = block["kind"]
        if kind == "para":
            parts.append(block["text"])
        elif kind == "heading":
            parts.append(f"**{block['text']}**")
        elif kind == "list":
            parts.append("\n".join(_md_items(block["items"], "")))
        else:
            parts.append("```tb\n" + block["text"] + "\n```")
    return "\n\n".join(parts)
