"""report: tables, Markdown, CSV, HTML pages and timelines from records."""

from __future__ import annotations

import csv as _csv
import datetime as _dt
import html as _html
import io
import os

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_WIDTH = 40
LEVEL_COLORS = {"critical": "#b42318", "high": "#c4320a", "medium": "#b54708", "low": "#175cd3",
                "informational": "#475467", "info": "#475467"}
SKIP_COLUMNS = {"records", "raw", "Message"}


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and all(not isinstance(v, (list, dict)) for v in value):
        return ", ".join(_tb.to_text(v) for v in value)
    return _tb.to_text(value)


def one_line(text: str) -> str:
    return " ".join(text.split()) if ("\n" in text or "\r" in text or "\t" in text) else text


def shorten(text: str, width: int) -> str:
    if width and len(text) > width:
        return text[:max(width - 3, 1)] + "..."
    return text


def table_shape(line, rows, columns, fn):
    """(column names, list of row value lists) from a list of maps or lists."""
    check_type(line, rows, "list", f"report.{fn}", "rows")
    if columns is not None:
        check_type(line, columns, "list", f"report.{fn}", "columns")
        columns = [_tb.to_text(c) for c in columns]
    if not rows:
        return columns or [], []
    if all(isinstance(r, dict) for r in rows):
        if columns is None:
            columns = []
            for r in rows:
                for key in r:
                    name = _tb.to_text(key)
                    if name not in columns and name not in SKIP_COLUMNS:
                        columns.append(name)
        data = []
        for r in rows:
            lookup = {_tb.to_text(k): v for k, v in r.items()}
            data.append([lookup.get(c) for c in columns])
        return columns, data
    if all(isinstance(r, list) for r in rows):
        if columns is None:
            columns, rows = [cell_text(c) for c in rows[0]], rows[1:]
        width = len(columns)
        return columns, [list(r[:width]) + [None] * (width - len(r)) for r in rows]
    if all(not isinstance(r, (dict, list)) for r in rows):
        return columns or ["value"], [[r] for r in rows]
    fail(line, f"report.{fn}() needs rows to be all maps or all lists.")


def render_table(columns, data, max_width=MAX_WIDTH) -> str:
    texts = [[shorten(one_line(cell_text(v)), max_width) for v in row] for row in data]
    heads = [shorten(c, max_width) for c in columns]
    numeric = [bool(data) and all(_tb.is_number(row[i]) or row[i] is None for row in data) for i in range(len(columns))]
    widths = [max([len(heads[i])] + [len(row[i]) for row in texts]) for i in range(len(columns))]

    def fmt(values):
        parts = [(v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i])) for i, v in enumerate(values)]
        return "  ".join(parts).rstrip()

    lines = [fmt(heads), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in texts)
    return "\n".join(lines)


def md_cell(value) -> str:
    return one_line(cell_text(value)).replace("\\", "\\\\").replace("|", "\\|")


def render_markdown(columns, data) -> str:
    if not columns:
        return ""
    lines = ["| " + " | ".join(md_cell(c) for c in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    lines.extend("| " + " | ".join(md_cell(v) for v in row) + " |" for row in data)
    return "\n".join(lines)


def render_csv(columns, data) -> str:
    out = io.StringIO()
    writer = _csv.writer(out, lineterminator="\n")
    if columns:
        writer.writerow(columns)
    for row in data:
        writer.writerow([cell_text(v) for v in row])
    return out.getvalue()


CSS = """
:root{--bg:#f7f7f8;--card:#fff;--ink:#1d2433;--muted:#5b6475;--line:#e3e5ea;--head:#f0f1f4;--accent:#7a3cff}
@media (prefers-color-scheme:dark){:root{--bg:#121318;--card:#1b1d24;--ink:#e8e9ee;--muted:#a1a7b5;--line:#2c2f3a;--head:#23262f;--accent:#b38cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:1200px;margin:0 auto;padding:32px 16px 48px}h1{margin:0 0 4px;font-size:26px}h2{font-size:18px;margin:32px 0 10px}
.sub{color:var(--muted);margin:0 0 20px}.intro{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;white-space:pre-wrap}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:22px}.stat span{color:var(--muted);font-size:13px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--head);font-weight:600;position:sticky;top:0}tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums}
td{word-break:break-word;max-width:420px}code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.lvl{display:inline-block;padding:1px 8px;border-radius:999px;color:#fff;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.empty{color:var(--muted);padding:14px}footer{color:var(--muted);font-size:13px;margin-top:32px}
"""


def html_table(columns, data) -> str:
    if not data:
        return '<div class="wrap"><div class="empty">Nothing to show.</div></div>'
    head = "".join(f"<th>{_html.escape(c)}</th>" for c in columns)
    body = []
    for row in data:
        cells = []
        for column, value in zip(columns, row):
            text = cell_text(value)
            if column.lower() == "level" and isinstance(value, str) and value.lower() in LEVEL_COLORS:
                color = LEVEL_COLORS[value.lower()]
                cells.append(f'<td><span class="lvl" style="background:{color}">{_html.escape(text)}</span></td>')
            elif _tb.is_number(value):
                cells.append(f'<td class="num">{_html.escape(text)}</td>')
            else:
                cells.append(f"<td>{_html.escape(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def time_of(record):
    if not isinstance(record, dict):
        return None
    for key in ("time", "first"):
        value = record.get(key)
        if _tb.is_number(value):
            return value
    return None


def when_text(record, ts):
    for key in ("when", "first_when"):
        if isinstance(record.get(key), str):
            return record[key]
    if ts is None:
        return "(no time)"
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return "(bad time)"


def describe(record) -> str:
    if "rule" in record and "message" not in record:
        level = str(record.get("level") or "").upper()
        by = f" {record['by']}" if record.get("by") not in (None, "") else ""
        count = f" x{_tb.to_text(record['count'])}" if record.get("count") not in (None, 1) else ""
        return f"[{level}] {record.get('rule')}{by}{count}"
    message = one_line(cell_text(record.get("message") if record.get("message") is not None else record.get("raw")))
    source = record.get("format")
    return f"[{source}] {message}" if source else message


def load(api):
    m = Module(
        "report",
        "Turn records and alerts into something to read or share: aligned text tables, Markdown, CSV, a "
        "self-contained HTML page, and a timeline. Rows are maps (column names are the keys) or lists.",
    )

    def width_arg(line, options, fn):
        if options is None:
            return MAX_WIDTH
        check_type(line, options, "map", f"report.{fn}", "options")
        width = options.get("max_width", MAX_WIDTH)
        width = check_type(line, width, "whole", f"report.{fn}", "max_width")
        if width < 4:
            fail(line, f"report.{fn}() needs max_width of 4 or more.")
        return width

    @m.fn("table", 1, 3, doc=(
        "table(rows, columns?, options?) -> text\n"
        "An aligned text table, ready to say(). Numbers line up on the right.\n"
        "\n"
        "Arguments:\n"
        "- rows: a list of maps, or a list of lists with the first list as the header.\n"
        "- columns: which columns to show, in order.\n"
        "  The default is every key, except big ones like records and raw.\n"
        "- options: {\"max_width\": 60} changes how wide a cell can be (default: 40).\n"
        "  Longer cells are cut to fit, ending with ...\n"
        "\n"
        "Example:\n"
        "    let rows = [{\"user\": \"Tanisha\", \"logins\": 3}, {\"user\": \"root\", \"logins\": 12}]\n"
        "    say(report.table(rows))"))
    def table(line, rows, columns=None, options=None):
        width = width_arg(line, options, "table")
        cols, data = table_shape(line, rows, columns, "table")
        if not cols:
            return "(no rows)"
        if not data:
            return render_table(cols, data, width) + "\n(no rows)"
        return render_table(cols, data, width)

    @m.fn("markdown", 1, 2, doc=(
        "markdown(rows, columns?) -> text\n"
        "A Markdown table (for GitHub, tickets and notes). | characters in cells are escaped."))
    def markdown(line, rows, columns=None):
        cols, data = table_shape(line, rows, columns, "markdown")
        return render_markdown(cols, data)

    @m.fn("csv", 1, 2, doc=(
        "csv(rows, columns?) -> text\n"
        "CSV text with a header row, ready for a spreadsheet. Lists in a cell become \"a, b\"."))
    def csv(line, rows, columns=None):
        cols, data = table_shape(line, rows, columns, "csv")
        return render_csv(cols, data)

    @m.fn("html", 2, 3, doc=(
        "html(title, rows, options?) -> text\n"
        "A complete HTML page with a title and a table of rows.\n"
        "It is one file, with styles included, in light and dark mode.\n"
        "\n"
        "Every value is escaped, so log lines can't inject HTML. A \"level\" column gets coloured badges.\n"
        "\n"
        "Options:\n"
        "- columns: a list of the columns to show.\n"
        "- intro: text shown above the table.\n"
        "- stats: numbers to show, as a map of {label: number}.\n"
        "- sections: more tables, as a list of {\"title\", \"rows\", \"columns\"?} maps."))
    def html(line, title, rows, options=None):
        check_type(line, title, "text", "report.html", "title")
        options = check_type(line, options, "map|nil", "report.html", "options") or {}
        cols, data = table_shape(line, rows, options.get("columns"), "html")
        parts = [f"<h1>{_html.escape(title)}</h1>",
                 f'<p class="sub">Generated {_html.escape(_dt.datetime.now().strftime("%Y-%m-%d %H:%M"))} '
                 f"&middot; {len(data)} row{'s' if len(data) != 1 else ''}</p>"]
        if options.get("intro") is not None:
            parts.append(f'<div class="intro">{_html.escape(cell_text(options["intro"]))}</div>')
        stats = options.get("stats")
        if stats is not None:
            check_type(line, stats, "map", "report.html", "stats")
            parts.append('<div class="stats">' + "".join(
                f'<div class="stat"><b>{_html.escape(cell_text(v))}</b><span>{_html.escape(cell_text(k))}</span></div>'
                for k, v in stats.items()) + "</div>")
        parts.append(html_table(cols, data))
        sections = options.get("sections")
        if sections is not None:
            check_type(line, sections, "list", "report.html", "sections")
            for section in sections:
                check_type(line, section, "map", "report.html", "each section")
                s_cols, s_data = table_shape(line, section.get("rows") or [], section.get("columns"), "html")
                parts.append(f"<h2>{_html.escape(cell_text(section.get('title', '')))}</h2>")
                parts.append(html_table(s_cols, s_data))
        parts.append("<footer>Made with TB.</footer>")
        return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                f"<title>{_html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
                + "\n".join(parts) + "\n</main>\n</body>\n</html>\n")

    @m.fn("timeline", 1, 2, doc=(
        "timeline(records, options?) -> text\n"
        "One line per record or alert, oldest first, like:\n"
        "\"2026-03-12 09:14:05  [auth] Failed password for root ...\".\n"
        "Records without a time go last.\n"
        "\n"
        "Options:\n"
        "- limit: keep only the first n lines.\n"
        "- max_width: cut lines longer than this (default: 160)."))
    def timeline(line, records, options=None):
        check_type(line, records, "list", "report.timeline", "records")
        options = check_type(line, options, "map|nil", "report.timeline", "options") or {}
        limit = options.get("limit")
        if limit is not None:
            limit = check_type(line, limit, "whole", "report.timeline", "limit")
        width = check_type(line, options.get("max_width", 160), "whole", "report.timeline", "max_width")
        items = [r for r in records if isinstance(r, dict)]
        ordered = sorted(enumerate(items), key=lambda p: (time_of(p[1]) is None, time_of(p[1]) or 0, p[0]))
        lines = []
        for _i, record in ordered:
            ts = time_of(record)
            lines.append(shorten(f"{when_text(record, ts)}  {describe(record)}", max(width, 20)))
        if limit is not None and len(lines) > limit:
            hidden = len(lines) - limit
            lines = lines[:limit] + [f"... and {hidden} more"]
        return "\n".join(lines) if lines else "(nothing to show)"

    @m.fn("save", 2, doc=(
        "save(path, text) -> text\n"
        "Write a report to a file (making missing folders) and give back the full path, to print or open."))
    def save(line, path, text):
        check_type(line, path, "text", "report.save", "path")
        if path == "":
            fail(line, "report.save() needs a path, but got empty text.")
        text = text if isinstance(text, str) else _tb.to_text(text)
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as err:
            fail_os(line, err, path, "write to")
        return os.path.abspath(path)

    return m
