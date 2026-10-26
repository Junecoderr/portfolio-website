"""`tb doc FILE.tb|FOLDER`: documentation from the comments in TB code.

    tb doc lib.tb              text, like `tb doc fs` shows a library
    tb doc lib.tb --md         Markdown
    tb doc apps/ --html        one HTML page for every .tb file in a folder
    tb doc lib.tb --out x.md   write to a file instead of the screen

Doc comments start with `##` and sit right above what they describe:

    ## Greets someone by name.
    ## Gives back the greeting it printed.
    fn greet(name) { ... }

`fn`, `class` (and its fields and methods), `enum`, `const`/`let` and
`export ...` are documented. When there is no `##` comment, the plain `#`
comment block right above the declaration is used, so older code documents
itself too. The file's own description is the comment block at the very top
(before the first blank line). If the file uses `export`, only exported names
are listed; names starting with `_` are private and skipped (`--all` shows
them). `*_test.tb` files are skipped in folders.

The site and the language server use `document(path)` / `document_source()`,
which give the same information as plain data.
"""

from __future__ import annotations

import html
import os
import sys

from tbcore import ast as A
from tbcore.errors import TBError

__all__ = ["document", "document_source", "doc_comment", "render_text", "render_markdown",
           "render_html", "main"]


# -- reading the docs ---------------------------------------------------------


def doc_comment(comments: list[str] | None) -> str:
    """The doc text from the comment lines right above a declaration: the
    `##` block if there is one, else the plain `#` block (no blank line between
    it and the declaration)."""
    if not comments:
        return ""
    block: list[str] = []
    for text in reversed(comments):
        if text == "":
            break
        block.insert(0, text)
    if not block:
        return ""
    doc = [t for t in block if t.startswith("##")]
    if doc:
        # the ## lines that end the block
        tail: list[str] = []
        for text in reversed(block):
            if not text.startswith("##"):
                break
            tail.insert(0, text)
        lines = [t[2:] for t in tail]
    else:
        if block[0].startswith("#!"):
            block = block[1:]
        lines = [t[1:] for t in block]
    return _dedent(lines)


def _dedent(lines: list[str]) -> str:
    stripped = [ln.rstrip() for ln in lines]
    indents = [len(ln) - len(ln.lstrip()) for ln in stripped if ln.strip()]
    cut = min(indents) if indents else 0
    text = "\n".join(ln[cut:] for ln in stripped).strip("\n")
    return text


def _module_doc(source: str) -> str:
    lines = source.splitlines()
    block: list[str] = []
    for text in lines:
        stripped = text.strip()
        if stripped.startswith("#!") and not block:
            continue
        if stripped.startswith("#"):
            block.append(stripped)
        else:
            if stripped and block:
                return ""   # the comment belongs to the first declaration
            break
    if not block:
        return ""
    if any(t.startswith("##") for t in block):
        block = [t for t in block if t.startswith("##")]
        return _dedent([t[2:] for t in block])
    return _dedent([t[1:] for t in block])


def _render_params(printer, params) -> str:
    from tbtools.fmt import render

    return render(printer.params(params), width=10_000)


def _render_expr(printer, node) -> str:
    from tbtools.fmt import render

    try:
        text = render(printer.expr(node), width=10_000)
    except Exception:
        return "..."
    if "\n" in text:
        if isinstance(node, A.MapLit):
            return "{...}"
        if isinstance(node, A.ListLit):
            return "[...]"
        return text.split("\n", 1)[0] + " ..."
    return text


def document_source(source: str, path: str | None = None, *, include_private: bool = False) -> dict:
    """{name, path, doc, items} for TB source. items: {kind, name, signature,
    doc, line, exported, members}; kind is fn, class, enum, const, let, field,
    method. Raises TBError when the source doesn't parse."""
    from tbtools.fmt import Printer, _parse_for_format

    source = source.replace("\r\n", "\n")
    program, tokens, raw_by_id, col_by_id = _parse_for_format(source)
    printer = Printer(source, tokens, raw_by_id, col_by_id)
    uses_export = bool(getattr(program, "uses_export", False))
    module_doc = _module_doc(source)
    items: list[dict] = []
    for index, stmt in enumerate(program.body):
        exported = False
        decl = stmt
        if isinstance(stmt, A.Export):
            exported = True
            decl = stmt.decl
        comments = list(getattr(stmt, "comments", []) or [])
        if index == 0 and module_doc:
            # the header comment is the file's description, not the first item's
            comments = _after_first_blank(comments)
        item = _describe(printer, decl, comments)
        if item is None:
            continue
        item["exported"] = exported
        item["line"] = stmt.line
        name = item["name"]
        if uses_export and not exported and not include_private:
            continue
        if name.startswith("_") and not include_private:
            continue
        if item["kind"] == "let" and not item["doc"] and not include_private:
            continue   # undocumented variables are working state, not API
        items.append(item)
    name = os.path.splitext(os.path.basename(path))[0] if path else "program"
    if path and name in ("lib", "main"):
        # packages/tb-colors/lib.tb is "tb-colors"; apps/notes/main.tb is "notes"
        name = os.path.basename(os.path.dirname(os.path.abspath(path))) or name
    return {"name": name, "path": path, "doc": module_doc, "items": items}


def _after_first_blank(comments: list[str]) -> list[str]:
    if "" in comments:
        return comments[comments.index("") + 1:]
    return []


def _describe(printer, decl, comments) -> dict | None:
    doc = doc_comment(comments)
    if isinstance(decl, A.FnDecl):
        sig = ("async " if decl.is_async else "") + "fn " + decl.name + _render_params(printer, decl.params)
        if decl.returns:
            sig += " -> " + decl.returns
        return {"kind": "fn", "name": decl.name, "signature": sig, "doc": doc, "members": []}
    if isinstance(decl, A.ClassDecl):
        sig = "class " + decl.name
        if decl.base is not None:
            sig += " extends " + printer.type_ref(decl.base)
        members = []
        open_idx = -1
        if hasattr(decl, "_span"):
            start, end = decl._span
            open_idx = start
            while open_idx < end and not printer.tok_is(open_idx, "{"):
                open_idx += 1
        for kind, is_static, obj, m_start, _m_end in printer.class_member_order(decl, open_idx, -1):
            comments_above = list(printer.tokens[m_start].comments_before) if m_start is not None else []
            prefix = "static " if is_static else ""
            if kind == "field":
                fname, value, annotation = obj
                if fname.startswith("_"):
                    continue
                msig = f"{prefix}let {fname}"
                if annotation:
                    msig += ": " + annotation
                if value is not None:
                    msig += " = " + _render_expr(printer, value)
                members.append({"kind": "field", "name": fname, "signature": msig,
                                "doc": doc_comment(comments_above), "line": 0, "members": []})
            else:
                if obj.name.startswith("_"):
                    continue
                msig = prefix + ("async " if obj.is_async else "") + "fn " + obj.name + \
                    _render_params(printer, obj.params)
                if obj.returns:
                    msig += " -> " + obj.returns
                members.append({"kind": "method", "name": obj.name, "signature": msig,
                                "doc": doc_comment(obj.comments or comments_above), "line": obj.line,
                                "members": []})
        return {"kind": "class", "name": decl.name, "signature": sig, "doc": doc, "members": members}
    if isinstance(decl, A.EnumDecl):
        sig = f"enum {decl.name} {{ {', '.join(decl.members)} }}"
        return {"kind": "enum", "name": decl.name, "signature": sig, "doc": doc, "members": []}
    if isinstance(decl, A.Let) and decl.name is not None:
        word = "const" if decl.is_const else "let"
        value = _render_expr(printer, decl.value)
        if len(value) > 60:
            value = value[:57] + "..."
        sig = f"{word} {decl.name}"
        if decl.annotation:
            sig += ": " + decl.annotation
        sig += " = " + value
        return {"kind": word, "name": decl.name, "signature": sig, "doc": doc, "members": []}
    return None


def document(path: str, *, include_private: bool = False) -> dict:
    with open(path, encoding="utf-8") as f:
        source = f.read()
    return document_source(source, path, include_private=include_private)


# -- output ---------------------------------------------------------------------


def _indent(text: str, by: str) -> str:
    return "\n".join((by + ln) if ln.strip() else "" for ln in text.splitlines())


def render_text(docs: list[dict]) -> str:
    out: list[str] = []
    for info in docs:
        if out:
            out.append("")
        title = info["path"] or info["name"]
        out.append(f"{title} - {info['doc'].splitlines()[0]}" if info["doc"] else title)
        rest = "\n".join(info["doc"].splitlines()[1:]).strip()
        if rest:
            out.append(_indent(rest, "  "))
        if not info["items"]:
            out.append("  (nothing documented)")
        for item in info["items"]:
            out.append("")
            out.append("  " + item["signature"])
            if item["doc"]:
                out.append(_indent(item["doc"], "      "))
            for member in item["members"]:
                out.append("    " + member["signature"])
                if member["doc"]:
                    out.append(_indent(member["doc"], "        "))
    return "\n".join(out) + "\n"


def render_markdown(docs: list[dict]) -> str:
    out: list[str] = []
    for info in docs:
        out.append(f"# {info['name']}")
        out.append("")
        if info["path"]:
            out.append(f"`{info['path']}`")
            out.append("")
        if info["doc"]:
            out.append(info["doc"])
            out.append("")
        for item in info["items"]:
            out.append(f"## {item['name']}")
            out.append("")
            out.append("```tb")
            out.append(item["signature"])
            out.append("```")
            out.append("")
            if item["doc"]:
                out.append(item["doc"])
                out.append("")
            for member in item["members"]:
                out.append(f"### {item['name']}.{member['name']}")
                out.append("")
                out.append("```tb")
                out.append(member["signature"])
                out.append("```")
                out.append("")
                if member["doc"]:
                    out.append(member["doc"])
                    out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


_CSS = """
:root { --bg: #ffffff; --fg: #1d2330; --muted: #5b6475; --code: #f3f5f9; --line: #dde2ea; --accent: #2f6fde; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #11151c; --fg: #e6e9ef; --muted: #9aa3b5; --code: #1b212b; --line: #2a3240; --accent: #7aa7ff; }
}
body { background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, sans-serif; margin: 0; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.7em; margin: 1.4em 0 .3em; } h2 { font-size: 1.2em; margin: 1.6em 0 .3em; }
h3 { font-size: 1em; margin: 1.2em 0 .2em; color: var(--muted); }
pre { background: var(--code); border: 1px solid var(--line); border-radius: 6px; padding: 8px 12px;
      overflow-x: auto; font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
p.path { color: var(--muted); font-size: .9em; margin: 0; }
.doc { white-space: pre-wrap; }
nav a { color: var(--accent); margin-right: 12px; }
"""


def render_html(docs: list[dict], title: str = "TB documentation") -> str:
    e = html.escape
    body: list[str] = []
    if len(docs) > 1:
        body.append("<nav>" + "".join(f'<a href="#{e(d["name"])}">{e(d["name"])}</a>' for d in docs) + "</nav>")
    for info in docs:
        body.append(f'<section id="{e(info["name"])}">')
        body.append(f"<h1>{e(info['name'])}</h1>")
        if info["path"]:
            body.append(f'<p class="path">{e(info["path"])}</p>')
        if info["doc"]:
            body.append(f'<p class="doc">{e(info["doc"])}</p>')
        for item in info["items"]:
            body.append(f'<h2 id="{e(info["name"])}-{e(item["name"])}">{e(item["name"])}</h2>')
            body.append(f"<pre><code>{e(item['signature'])}</code></pre>")
            if item["doc"]:
                body.append(f'<p class="doc">{e(item["doc"])}</p>')
            for member in item["members"]:
                body.append(f"<h3>{e(item['name'])}.{e(member['name'])}</h3>")
                body.append(f"<pre><code>{e(member['signature'])}</code></pre>")
                if member["doc"]:
                    body.append(f'<p class="doc">{e(member["doc"])}</p>')
        body.append("</section>")
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{e(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
            + "\n".join(body) + "\n</main>\n</body>\n</html>\n")


# -- command line -------------------------------------------------------------------

USAGE = """\
tb doc FILE.tb|FOLDER - documentation from ## comments

  tb doc lib.tb             show it as text
  tb doc lib.tb --md        Markdown
  tb doc src/ --html        one HTML page for every .tb file in the folder
  --out PATH                write to a file instead of the screen
  --all                     include private (_name) and unexported names

Write doc comments with ## right above fn, class, enum, const and export."""


def _files(target: str) -> list[str]:
    if os.path.isdir(target):
        found = []
        skip = {".git", "node_modules", "__pycache__", "tb_packages", "build", "dist"}
        for folder, dirs, files in os.walk(target):
            dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
            for name in sorted(files):
                if name.endswith(".tb") and not name.endswith("_test.tb"):
                    found.append(os.path.join(folder, name))
        return found
    return [target]


def main(args: list[str] | None = None) -> int:
    args = list(args or [])
    if not args or any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return 0 if args else 2
    fmt_name, out_path, include_private, targets = "text", None, False, []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--md", "--markdown"):
            fmt_name = "md"
        elif a == "--html":
            fmt_name = "html"
        elif a == "--text":
            fmt_name = "text"
        elif a == "--all":
            include_private = True
        elif a == "--out":
            if i + 1 >= len(args):
                print("Oops! --out needs a file name, like: tb doc lib.tb --md --out lib.md", file=sys.stderr)
                return 2
            out_path = args[i + 1]
            i += 1
        elif a.startswith("--"):
            print(f"Oops! tb doc doesn't know the option '{a}'. See: tb doc --help", file=sys.stderr)
            return 2
        else:
            targets.append(a)
        i += 1
    if not targets:
        print("Oops! Tell me which .tb file or folder to document, like: tb doc lib.tb", file=sys.stderr)
        return 2
    docs = []
    failed = 0
    for target in targets:
        if not os.path.exists(target):
            print(f"Oops! I couldn't find '{target}'.", file=sys.stderr)
            return 1
        for path in _files(target):
            try:
                docs.append(document(path, include_private=include_private))
            except TBError as err:
                print(f"{path}: {err}", file=sys.stderr)
                failed += 1
            except OSError as err:
                print(f"Oops! I couldn't read '{path}': {err.strerror or err}.", file=sys.stderr)
                failed += 1
    if not docs and not failed:
        print("No .tb files to document there.")
        return 1
    if fmt_name == "md":
        text = render_markdown(docs)
    elif fmt_name == "html":
        title = docs[0]["name"] if len(docs) == 1 else os.path.basename(os.path.abspath(targets[0]))
        text = render_html(docs, f"{title} - TB docs")
    else:
        text = render_text(docs)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
