import datetime
import time

import pytest

import tb
from conftest import run_tb, tb_error


def test_now_map():
    out = run_tb('import time\nlet n = time.now()\nsay(keys(n))\nsay(n.year >= 2024, n.month >= 1 and n.month <= 12, type(n.weekday), type(n.timestamp))')
    assert out[0] == '["year", "month", "day", "hour", "minute", "second", "weekday", "month_name", "timestamp"]'
    assert out[1] == "true true text number"


def test_timestamp_and_since():
    before = time.time()
    out = run_tb("import time\nlet t = time.timestamp()\nsay(t)\nsay(time.since(t) >= 0, time.since(t) < 5)")
    assert before - 1 <= float(out[0]) <= time.time() + 1
    assert out[1] == "true true"


def test_format_and_parse_round_trip():
    ts = datetime.datetime(2026, 10, 26, 14, 30, 5).timestamp()
    src = f'import time\nsay(time.format({ts}))\nsay(time.format({ts}, "%d/%m/%Y %A %B"))\nsay(time.parse("2026-10-26 14:30:05") == {ts})\nsay(time.parse("26/10/2026", "%d/%m/%Y") == time.parse("2026-10-26"))'
    assert run_tb(src) == ["2026-10-26 14:30:05", "26/10/2026 Monday October", "true", "true"]


def test_parse_iso_with_timezone():
    out = run_tb('import time\nsay(time.parse("2026-01-01T00:00:00Z"))')
    assert out == [str(int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp()))]


def test_today():
    assert run_tb("import time\nsay(time.today())") == [datetime.date.today().isoformat()]


def test_sleep(monkeypatch):
    import tblib.time as tbtime
    slept = []
    monkeypatch.setattr(tbtime._time, "sleep", slept.append)
    assert run_tb("import time\nsay(time.sleep(0.25))") == ["nil"]
    assert slept == [0.25]


@pytest.mark.parametrize("expr,message", [
    ('time.format("x")', "time.format() needs timestamp to be a number, not text."),
    ("time.format(1e400)" if False else "time.format(99999999999999999)", "too far from today"),
    ('time.parse("not a date")', 'couldn\'t read "not a date" as a date (try a pattern'),
    ('time.parse("2026", "%d/%m/%Y")', 'with the pattern "%d/%m/%Y"'),
    ("time.sleep(-1)", "0 or more seconds"),
    ("time.sleep(100000)", "at most one day"),
    ("time.since(nil)", "needs timestamp to be a number, not nil"),
])
def test_time_errors(expr, message):
    assert message in str(tb_error(f"import time\nsay({expr})"))
