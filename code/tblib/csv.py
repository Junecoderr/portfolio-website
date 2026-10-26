"""csv: read and write CSV (spreadsheet) files."""

from __future__ import annotations

import csv as _csv
import io

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb


def _options(line, options, fn):
    if options is None:
        return ",", True
    check_type(line, options, "map", f"csv.{fn}", "options")
    delimiter = options.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        fail(line, f"csv.{fn}() needs the delimiter to be one letter, like \",\" or \";\".", cls="ValueError")
    return delimiter, _tb.truthy(options.get("header", True))


def parse_rows(text: str, line, delimiter=",", header=True) -> list:
    try:
        rows = list(_csv.reader(io.StringIO(text), delimiter=delimiter))
    except _csv.Error as err:
        fail(line, f"That CSV text is broken: {err}.", cls="ParseError")
    rows = [r for r in rows if r]  # skip blank lines
    if not header:
        return rows
    if not rows:
        return []
    names = [h.strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        record = {}
        for i, name in enumerate(names):
            key = name or f"column{i + 1}"
            record[key] = row[i] if i < len(row) else ""
        for i in range(len(names), len(row)):
            record[f"column{i + 1}"] = row[i]
        result.append(record)
    return result


def _cell(value) -> str:
    return "" if value is None else _tb.to_text(value)


def rows_to_text(rows, line, fn="stringify", delimiter=",") -> str:
    check_type(line, rows, "list", f"csv.{fn}", "rows")
    out = io.StringIO()
    writer = _csv.writer(out, delimiter=delimiter, lineterminator="\n")
    if rows and all(isinstance(r, dict) for r in rows):
        columns: list = []
        for r in rows:
            for k in r:
                if k not in columns:
                    columns.append(k)
        writer.writerow([_cell(c) for c in columns])
        for r in rows:
            writer.writerow([_cell(r.get(c)) for c in columns])
    else:
        for r in rows:
            if isinstance(r, list):
                writer.writerow([_cell(v) for v in r])
            else:
                fail(line, f"csv.{fn}() needs a list of maps or a list of lists, but one row is {_tb.type_name(r)}.", cls="TypeError")
    return out.getvalue()


def load(api):
    m = Module(
        "csv",
        "Read and write CSV files. Each row becomes a map, using the first row as the column names.",
    )

    @m.fn("parse", 1, 2, doc="parse(text, options?) -> list\nCSV text as a list of maps (one per row). options: {\"delimiter\": \";\", \"header\": false} (no header gives lists).")
    def parse(line, text, options=None):
        check_type(line, text, "text", "csv.parse", "text")
        delimiter, header = _options(line, options, "parse")
        return parse_rows(text, line, delimiter, header)

    @m.fn("read", 1, 2, doc="read(path, options?) -> list\nRead a .csv file as a list of maps (values are text). Same options as parse.")
    def read(line, path, options=None):
        check_type(line, path, "text", "csv.read", "path")
        delimiter, header = _options(line, options, "read")
        try:
            text = _tb.read_source(path)
        except OSError as err:
            fail_os(line, err, path, "read")
        return parse_rows(text, line, delimiter, header)

    @m.fn("stringify", 1, doc="stringify(rows) -> text\nTurn a list of maps (or a list of lists) into CSV text.")
    def stringify(line, rows):
        return rows_to_text(rows, line)

    @m.fn("write", 2, doc="write(path, rows)\nSave a list of maps (or lists) as a .csv file. Map keys become the header row.")
    def write(line, path, rows):
        check_type(line, path, "text", "csv.write", "path")
        text = rows_to_text(rows, line, "write")
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    return m
