"""template: fill text templates ({{name}}, {{#list}}...{{/list}}) with values, HTML-safe by default."""

from __future__ import annotations

import html as _html
import os
import re

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_OUTPUT = 50_000_000
MAX_DEPTH = 64
TAG = re.compile(r"\{\{(\{?)\s*([#^/!>&]?)\s*(.*?)\s*(\}?)\}\}", re.DOTALL)
FILTER_SPLIT = re.compile(r"\s*\|\s*")


class TemplateProblem(Exception):
    def __init__(self, message, line):
        super().__init__(message)
        self.message, self.line = message, line


# -- filters -------------------------------------------------------------------


def _as_text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else _tb.to_text(value)


def _filter_round(value, arg):
    places = int(arg) if arg not in (None, "") else 0
    if not _tb.is_number(value):
        raise ValueError(f"round needs a number, not {_tb.type_name(value)}")
    return _tb.tb_round(value, places)


def _filter_join(value, arg):
    if not isinstance(value, list):
        raise ValueError(f"join needs a list, not {_tb.type_name(value)}")
    return (", " if arg is None else arg).join(_as_text(v) for v in value)


def _filter_default(value, arg):
    return arg if (value is None or value == "" or value == [] or value == {}) else value


def _filter_truncate(value, arg):
    n = int(arg) if arg else 40
    text = _as_text(value)
    return text if len(text) <= n else text[:max(n - 3, 0)] + "..."


def _filter_pad(value, arg, left=True):
    n = int(arg) if arg else 0
    text = _as_text(value)
    return text.rjust(n) if left else text.ljust(n)


def _filter_date(value, arg):
    import datetime as _dt
    if not _tb.is_number(value):
        raise ValueError("date needs a timestamp number")
    pattern = arg or "%Y-%m-%d %H:%M:%S"
    return _dt.datetime.fromtimestamp(value).strftime(pattern)


def _filter_json(value, arg):
    from tblib import json as _json
    return _json.dump(value, None, pretty=bool(arg))


FILTERS = {
    "upper": lambda v, a: _as_text(v).upper(),
    "lower": lambda v, a: _as_text(v).lower(),
    "title": lambda v, a: _as_text(v).title(),
    "capitalize": lambda v, a: _as_text(v)[:1].upper() + _as_text(v)[1:],
    "trim": lambda v, a: _as_text(v).strip(),
    "len": lambda v, a: len(v) if isinstance(v, (list, dict, str)) else 0,
    "length": lambda v, a: len(v) if isinstance(v, (list, dict, str)) else 0,
    "first": lambda v, a: (v[0] if isinstance(v, list) and v else (_as_text(v)[:1] if isinstance(v, str) else None)),
    "last": lambda v, a: (v[-1] if isinstance(v, list) and v else (_as_text(v)[-1:] if isinstance(v, str) else None)),
    "reverse": lambda v, a: (list(reversed(v)) if isinstance(v, list) else _as_text(v)[::-1]),
    "sort": lambda v, a: sorted(v, key=lambda x: (_as_text(x) if not _tb.is_number(x) else x)) if isinstance(v, list) else v,
    "join": _filter_join,
    "round": _filter_round,
    "default": _filter_default,
    "truncate": _filter_truncate,
    "pad_left": lambda v, a: _filter_pad(v, a, True),
    "pad_right": lambda v, a: _filter_pad(v, a, False),
    "date": _filter_date,
    "json": _filter_json,
    "escape": lambda v, a: _html.escape(_as_text(v), quote=True),
    "urlencode": lambda v, a: __import__("urllib.parse").parse.quote(_as_text(v), safe=""),
    "number": lambda v, a: f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else (f"{v:,.{int(a) if a else 2}f}" if isinstance(v, float) else _as_text(v)),
    "yes_no": lambda v, a: ("yes" if _tb.truthy(v) else "no") if not a else (a.split(",") + ["no"])[0 if _tb.truthy(v) else 1].strip(),
}


# -- compiling -----------------------------------------------------------------


def compile_template(text: str):
    """Parse template text into a node tree. Raises TemplateProblem."""
    root: list = []
    stack = [("", root, 0)]
    pos = 0
    for m in TAG.finditer(text):
        before = text[pos:m.start()]
        line = text.count("\n", 0, m.start()) + 1
        triple_open, sigil, body, triple_close = m.group(1), m.group(2), m.group(3), m.group(4)
        if triple_open and not triple_close:
            raise TemplateProblem("{{{ needs }}} to close it", line)
        if not triple_open and triple_close:
            body = body + "}"  # a stray '}' inside, keep it as part of the name
            triple_close = ""
        raw = bool(triple_open) or sigil == "&"
        standalone = sigil in ("#", "^", "/", "!", ">") and _standalone(text, m.start(), m.end())
        if standalone:
            # Drop the whitespace before the tag on its line and the newline after it.
            cut = before.rfind("\n")
            before = before[:cut + 1] if cut != -1 else ""
            end = m.end()
            if text.startswith("\r\n", end):
                end += 2
            elif text.startswith("\n", end):
                end += 1
            pos = end
        else:
            pos = m.end()
        if before:
            stack[-1][1].append(("text", before))
        if sigil == "!":
            continue
        if sigil in ("#", "^"):
            name = body
            if not name:
                raise TemplateProblem("a section needs a name, like {{#items}}", line)
            children: list = []
            stack[-1][1].append(("section" if sigil == "#" else "inverted", name, children, line))
            stack.append((name, children, line))
            if len(stack) > MAX_DEPTH:
                raise TemplateProblem(f"sections are nested more than {MAX_DEPTH} deep", line)
            continue
        if sigil == "/":
            if len(stack) == 1:
                raise TemplateProblem(f"{{{{/{body}}}}} closes a section that was never opened", line)
            name, _, opened = stack.pop()
            if body and body != name:
                raise TemplateProblem(f"{{{{/{body}}}}} doesn't match the open section {{{{#{name}}}}} from line {opened}", line)
            continue
        if sigil == ">":
            raise TemplateProblem("partials ({{> name}}) aren't supported; use template.render() for the part and pass its result in as a value", line)
        parts = FILTER_SPLIT.split(body)
        name, filters = parts[0], []
        if not name:
            raise TemplateProblem("{{ }} needs a name inside", line)
        for f in parts[1:]:
            fname, _, arg = f.partition(":")
            fname = fname.strip()
            arg = arg.strip() if arg else None
            if arg and len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
                arg = arg[1:-1]
            if fname == "raw":
                raw = True
                continue
            if fname not in FILTERS:
                raise TemplateProblem(f"I don't know the filter '{fname}'. Filters: {', '.join(sorted(FILTERS) + ['raw'])}", line)
            filters.append((fname, arg))
        stack[-1][1].append(("value", name, filters, raw, line))
    rest = text[pos:]
    if rest:
        stack[-1][1].append(("text", rest))
    if len(stack) > 1:
        name, _, opened = stack[-1]
        raise TemplateProblem(f"the section {{{{#{name}}}}} opened on line {opened} is never closed with {{{{/{name}}}}}", opened)
    return root


def _standalone(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    if text[line_start:start].strip():
        return False
    line_end = text.find("\n", end)
    after = text[end:] if line_end == -1 else text[end:line_end]
    return not after.strip()


# -- rendering -----------------------------------------------------------------


def lookup(name: str, stack: list):
    """Resolve a dotted name against the context stack (innermost first)."""
    if name == ".":
        return stack[-1], True
    first, _, rest = name.partition(".")
    for ctx in reversed(stack):
        found, value = _get(ctx, first)
        if found:
            for part in rest.split(".") if rest else []:
                found, value = _get(value, part)
                if not found:
                    return None, False
            return value, True
    return None, False


def _get(ctx, key):
    if isinstance(ctx, dict):
        if key in ctx:
            return True, ctx[key]
        if key.lstrip("-").isdigit():
            try:
                k = _tb.tidy_number(float(key))
                if k in ctx:
                    return True, ctx[k]
            except ValueError:
                pass
        return False, None
    if isinstance(ctx, list) and key.lstrip("-").isdigit():
        i = int(key)
        if -len(ctx) <= i < len(ctx):
            return True, ctx[i]
    return False, None


def render_nodes(nodes, stack, out, escape, line_hint):
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "value":
            _, name, filters, raw, line = node
            value, found = lookup(name, stack)
            if not found and not filters:
                value = None
            for fname, arg in filters:
                try:
                    value = FILTERS[fname](value, arg)
                except (ValueError, TypeError, OverflowError) as err:
                    raise TemplateProblem(f"the filter '{fname}' failed on {{{{{name}}}}}: {err}", line)
            text = _as_text(value)
            out.append(text if raw or not escape else _html.escape(text, quote=True))
        elif kind == "section":
            _, name, children, line = node
            value, found = lookup(name, stack)
            if isinstance(value, list):
                for item in value:
                    stack.append(item)
                    render_nodes(children, stack, out, escape, line)
                    stack.pop()
            elif isinstance(value, dict):
                stack.append(value)
                render_nodes(children, stack, out, escape, line)
                stack.pop()
            elif _tb.truthy(value):
                render_nodes(children, stack, out, escape, line)
        elif kind == "inverted":
            _, name, children, line = node
            value, found = lookup(name, stack)
            if not _tb.truthy(value) or value == [] or value == {}:
                render_nodes(children, stack, out, escape, line)


def render_text(text: str, values, escape=True) -> str:
    nodes = compile_template(text)
    out: list = []
    render_nodes(nodes, [values], out, escape, 1)
    result = "".join(out)
    if len(result) > MAX_OUTPUT:
        raise TemplateProblem("the result is too long (over 50 MB)", 1)
    return result


def load(api):
    m = Module(
        "template",
        "Fill in text templates. {{name}} inserts a value (HTML-escaped, so it is safe in web pages), "
        "{{{name}}} inserts it as is, {{#items}}...{{/items}} repeats for each item of a list (or shows the "
        "part when the value is true/non-empty), {{^items}}...{{/items}} shows when it is empty or false, "
        "{{name|upper}} applies a filter, {{a.b.c}} reaches into maps, {{.}} is the current item and "
        "{{! note }} is a comment. Filters: upper lower title capitalize trim len first last reverse sort "
        "join:\", \" round:2 default:\"-\" truncate:40 pad_left:8 pad_right:8 date:\"%Y-%m-%d\" json escape "
        "urlencode number yes_no raw.",
    )

    def options_arg(line, options, fn):
        if options is None:
            return {}
        check_type(line, options, "map", f"template.{fn}", "options")
        for key in options:
            if key not in ("escape",):
                fail(line, f'template.{fn}() doesn\'t know the option "{key}". Options: escape.')
        if "escape" in options:
            check_type(line, options["escape"], "bool", f"template.{fn}", '"escape"')
        return options

    def do_render(line, text, values, options, fn):
        check_type(line, text, "text", f"template.{fn}", "text")
        check_type(line, values, "map|list|nil", f"template.{fn}", "values")
        o = options_arg(line, options, fn)
        try:
            return render_text(text, values if values is not None else {}, o.get("escape", True))
        except TemplateProblem as err:
            fail(line, f"template.{fn}(): on line {err.line} of the template, {err.message}.",
                 reason="template", template_line=err.line)
        except RecursionError:
            fail(line, f"template.{fn}(): the template or its values are nested too deeply.")

    @m.fn("render", 1, 3, doc=(
        "render(text, values?, options?) -> text\n"
        "Fill the template with values (a map): template.render(\"Hi {{name}}!\", {\"name\": \"Tanisha\"}).\n"
        "\n"
        "Values are HTML-escaped, unless:\n"
        "- they are written {{{like this}}},\n"
        "- they are given the raw filter,\n"
        "- or options is {\"escape\": false} (for plain text, emails or config files)."))
    def render(line, text, values=None, options=None):
        return do_render(line, text, values, options, "render")

    @m.fn("file", 2, 3, doc=(
        "file(path, values, options?) -> text\n"
        "Read a template file and fill it in, like render(). Relative paths start where you ran tb."))
    def file_(line, path, values, options=None):
        check_type(line, path, "text", "template.file", "path")
        if not path.strip():
            fail(line, "template.file() needs a path, but got empty text.")
        full = os.path.expanduser(path)
        if os.path.isdir(full):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        try:
            from tblib.fs import read_text
            text = read_text(full).replace("\r\n", "\n")
        except OSError as err:
            fail_os(line, err, path, "read")
        return do_render(line, text, values, options, "file")

    @m.fn("check", 1, doc=(
        "check(text) -> list\n"
        "Problems in a template without rendering it (unclosed sections, unknown filters), as a list of "
        "text messages; empty when it is fine."))
    def check(line, text):
        check_type(line, text, "text", "template.check", "text")
        try:
            compile_template(text)
        except TemplateProblem as err:
            return [f"line {err.line}: {err.message}"]
        return []

    @m.fn("names", 1, doc=(
        "names(text) -> list\n"
        "The value names a template uses (top-level, sorted): names(\"{{a}} {{#b}}{{c}}{{/b}}\") is [\"a\", \"b\"]."))
    def names(line, text):
        check_type(line, text, "text", "template.names", "text")
        try:
            nodes = compile_template(text)
        except TemplateProblem as err:
            fail(line, f"template.names(): on line {err.line} of the template, {err.message}.", reason="template")
        found = set()
        for node in nodes:
            if node[0] in ("value", "section", "inverted") and node[1] != ".":
                found.add(node[1].split(".")[0])
        return sorted(found)

    @m.fn("escape", 1, doc="escape(text) -> text\nText made safe for HTML: < > & \" become entities.")
    def escape(line, value):
        return _html.escape(_as_text(value), quote=True)

    m.value("filters", sorted(FILTERS) + ["raw"], doc="The filter names {{name|filter}} understands.")
    return m
