import csv
import io

import pytest

from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


ROWS = '[{"ip": "203.0.113.42", "count": 22, "users": ["root", "admin"]}, {"ip": "198.51.100.7", "count": 3, "users": nil}]'


def test_table_aligns_and_right_aligns_numbers():
    out = run_tb(f"import report\nsay(report.table({ROWS}))")
    assert out == [
        "ip            count  users\n"
        "------------  -----  -----------\n"
        "203.0.113.42     22  root, admin\n"
        "198.51.100.7      3"
    ]


def test_table_columns_truncation_and_newlines():
    src = '''import report
let rows = [{"a": "x".repeat(50), "b": "line one\\nline two", "records": [1], "raw": "zzz"}]
say(report.table(rows))
say(report.table(rows, ["b", "missing"], {"max_width": 8}))'''
    out = run_tb(src)
    assert out[0].splitlines()[0].split() == ["a", "b"]  # records and raw are left out by default
    assert "x" * 37 + "..." in out[0] and "line one line two" in out[0]
    assert out[1].splitlines() == ["b         missing", "--------  -------", "line ..."]


def test_table_from_lists_and_plain_values_and_empty():
    src = '''import report
say(report.table([["name", "n"], ["a", 1], ["bb", 22, "extra"]]))
say(report.table(["x", "y"]))
say(report.table([]))
say(report.table([], ["a", "b"]))'''
    assert run_tb(src) == [
        "name   n\n----  --\na      1\nbb    22",
        "value\n-----\nx\ny",
        "(no rows)",
        "a  b\n-  -\n(no rows)",
    ]


def test_markdown_escapes_pipes():
    src = 'import report\nsay(report.markdown([{"cmd": "a | b", "n": 1}, {"cmd": "multi\\nline", "n": nil}]))'
    assert run_tb(src) == ["| cmd | n |\n| --- | --- |\n| a \\| b | 1 |\n| multi line |  |"]


def test_csv_round_trips_through_python_csv():
    src = f'import report\nsay(report.csv({ROWS}))'
    text = "\n".join(run_tb(src))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows == [{"ip": "203.0.113.42", "count": "22", "users": "root, admin"},
                    {"ip": "198.51.100.7", "count": "3", "users": ""}]


def test_html_page_is_complete_and_escaped(tmp_path):
    src = '''import report
let rows = [{"level": "critical", "path": "<script>alert(1)</script>", "n": 5}, {"level": "low", "path": "a & b", "n": 1}]
let page = report.html("Alerts <today>", rows, {"intro": "Read me & weep", "stats": {"alerts": 2},
  "sections": [{"title": "More", "rows": [{"k": "\\"quoted\\""}]}]})
say(report.save("out/report.html", page) != "")'''
    assert run_tb(src) == ["true"]
    page = (tmp_path / "out" / "report.html").read_text()
    assert page.startswith("<!doctype html>") and page.rstrip().endswith("</html>")
    assert "<title>Alerts &lt;today&gt;</title>" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "a &amp; b" in page and "Read me &amp; weep" in page and "&quot;quoted&quot;" in page
    assert 'class="lvl" style="background:#b42318">critical<' in page
    assert '<td class="num">5</td>' in page
    assert "prefers-color-scheme:dark" in page and "<h2>More</h2>" in page
    assert "http" not in page.split("<style>")[0]  # self-contained: no external links


def test_html_with_no_rows():
    out = run_tb('import report\nsay(report.html("Empty", []).contains("Nothing to show."))')
    assert out == ["true"]


def test_timeline_orders_by_time_and_handles_alerts():
    src = '''import report
let items = [
  {"time": 200, "when": "2026-03-12 09:00:02", "format": "auth", "message": "second"},
  {"message": "no time"},
  {"time": 100, "when": "2026-03-12 09:00:01", "format": "auth", "message": "first\\nline"},
  {"rule": "Brute force", "level": "critical", "by": "203.0.113.42", "count": 22, "first": 150, "first_when": "2026-03-12 09:00:01"},
]
say(report.timeline(items))
say(report.timeline(items, {"limit": 1}))
say(report.timeline([]))'''
    out = run_tb(src)
    assert out[0].splitlines() == [
        "2026-03-12 09:00:01  [auth] first line",
        "2026-03-12 09:00:01  [CRITICAL] Brute force 203.0.113.42 x22",
        "2026-03-12 09:00:02  [auth] second",
        "(no time)  no time",
    ]
    assert out[1].splitlines() == ["2026-03-12 09:00:01  [auth] first line", "... and 3 more"]
    assert out[2] == "(nothing to show)"


def test_timeline_of_sample_auth_log(root):
    path = root / "apps" / "data" / "auth.log"
    out = run_tb(f'import logs\nimport report\nsay(report.timeline(logs.read("{path}"), {{"limit": 3, "max_width": 60}}))')
    lines = out[0].splitlines()
    assert len(lines) == 4 and lines[0].endswith("...") and len(lines[0]) == 60 and "[auth]" in lines[0]


@pytest.mark.parametrize("src,message", [
    ("report.table(5)", "report.table() needs rows to be a list, not a number."),
    ('report.table([1, {"a": 1}])', "report.table() needs rows to be all maps or all lists."),
    ('report.table([], nil, {"max_width": 2})', "needs max_width of 4 or more"),
    ('report.html(1, [])', "report.html() needs title to be text"),
    ('report.html("t", [], {"stats": 3})', "report.html() needs stats to be a map"),
    ('report.timeline([], {"limit": "x"})', "report.timeline() needs limit to be a whole number"),
    ('report.save("", "x")', "report.save() needs a path"),
    ('report.save(".", "x")', "is a folder, not a file."),
])
def test_report_errors(src, message):
    assert message in str(tb_error(f"import report\n{src}"))
