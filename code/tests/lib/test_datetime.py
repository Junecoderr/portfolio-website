"""datetime library: zone-aware moments, parsing, arithmetic."""

import datetime as dt

import pytest

import tb
from conftest import run_tb, tb_error


def test_parse_iso_and_fields():
    src = '''import datetime
let d = datetime.parse("2026-10-26T14:30:05+02:00")
say(d.year, d.month, d.day, d.hour, d.minute, d.second, d.weekday, d.month_name)
say(d.date, d.time, d.zone, d.offset, d.iso)
say(d.timestamp == datetime.parse("2026-10-26T12:30:05Z").timestamp)
say(d.text(), d.to_utc().iso, d.to_utc().zone)
'''
    assert run_tb(src) == [
        "2026 10 26 14 30 5 Monday October",
        "2026-10-26 14:30:05 +02:00 +02:00 2026-10-26T14:30:05+02:00",
        "true",
        "2026-10-26T14:30:05+02:00 2026-10-26T12:30:05+00:00 UTC",
    ]


def test_zones_and_to_zone():
    src = '''import datetime
let d = datetime.parse("2026-01-15T12:00:00Z")
let k = d.to_zone("Asia/Kolkata")
say(k.hour, k.minute, k.zone, k.offset, k.equals(d))
let b = d.to_zone("Europe/Berlin")
say(b.hour, b.offset, b.to_zone("-05:00").hour)
say(datetime.is_zone("Europe/Berlin"), datetime.is_zone("Mars/Olympus"), datetime.is_zone("+05:30"))
say("Asia/Kolkata" in datetime.zones(), datetime.zones("Kolk"))
say(datetime.parse("2026-07-01 09:00", nil, "Europe/Berlin").iso)
'''
    assert run_tb(src) == [
        "17 30 Asia/Kolkata +05:30 true",
        "13 +01:00 7",
        "true false true",
        'true ["Asia/Kolkata"]',
        "2026-07-01T09:00:00+02:00",
    ]


def test_parse_log_forms_and_patterns():
    src = '''import datetime
say(datetime.parse("26/Oct/2026:14:30:00 +0000").iso)
say(datetime.parse("Oct 26 14:30:00", nil, "UTC").month, datetime.parse("Oct 26 14:30:00", nil, "UTC").hour)
say(datetime.parse("10/26/2026 2:30 PM", nil, "UTC").iso)
say(datetime.parse("26-10-2026 14:30", "%d-%m-%Y %H:%M", "UTC").iso)
say(datetime.parse("2026-10-26", nil, "UTC").iso)
say(datetime.parse("Mon, 26 Oct 2026 14:30:00 GMT").iso)
'''
    assert run_tb(src) == [
        "2026-10-26T14:30:00+00:00",
        "10 14",
        "2026-10-26T14:30:00+00:00",
        "2026-10-26T14:30:00+00:00",
        "2026-10-26T00:00:00+00:00",
        "2026-10-26T14:30:00+00:00",
    ]


def test_add_diff_compare_start_of():
    src = '''import datetime
let d = datetime.parse("2026-01-31T10:00:00Z")
say(d.add({"days": 1, "hours": 2}).iso)
say(d.add({"months": 1}).date, d.add({"years": -1, "months": 1}).date)
say(d.add({"weeks": 1, "minutes": -30, "seconds": 5}).iso)
let e = d.add({"hours": 36})
say(e.diff(d), d.diff(e), d.before(e), d.after(e), e.after("2026-01-31T10:00:00Z"))
say(d.start_of("month").iso, d.start_of("week").date, d.start_of("hour").time)
say(d.format("%d %B %Y, %A %H:%M %Z"))
'''
    assert run_tb(src) == [
        "2026-02-01T12:00:00+00:00",
        "2026-02-28 2025-02-28",
        "2026-02-07T09:30:05+00:00",
        "129600 -129600 true false true",
        "2026-01-01T00:00:00+00:00 2026-01-26 10:00:00",
        "31 January 2026, Saturday 10:00 UTC",
    ]


def test_dst_change_keeps_wall_clock_sane():
    # Adding a day across the European DST change keeps the wall-clock hour.
    src = '''import datetime
let d = datetime.parse("2026-03-28T09:00:00", nil, "Europe/Berlin")
let n = d.add({"days": 1})
say(d.offset, n.offset, n.hour, n.diff(d))
'''
    assert run_tb(src) == ["+01:00 +02:00 9 86400"]


def test_now_make_from_timestamp_today():
    src = '''import datetime
import time
let n = datetime.now("UTC")
say(n.zone, n.year >= 2026, datetime.is_datetime(n), datetime.is_datetime({}))
let m = datetime.make({"year": 2026, "month": 10, "day": 26, "hour": 9}, "Asia/Kolkata")
say(m.iso, m.to_utc().iso)
let f = datetime.from_timestamp(0, "UTC")
say(f.iso, f.timestamp)
say(datetime.from_timestamp(time.parse("2026-10-26T00:00:00Z"), "UTC").iso)
say(len(datetime.today("UTC")), type(datetime.local_zone()))
'''
    assert run_tb(src) == [
        "UTC true true false",
        "2026-10-26T09:00:00+05:30 2026-10-26T03:30:00+00:00",
        "1970-01-01T00:00:00+00:00 0",
        "2026-10-26T00:00:00+00:00",
        "10 text",
    ]


def test_time_library_reads_log_forms_too():
    out = run_tb('import time\nsay(time.parse("26/Oct/2026:14:30:00 +0000") == time.parse("2026-10-26T14:30:00Z"))')
    assert out == ["true"]


@pytest.mark.parametrize("src,message", [
    ('datetime.parse("not a date")', 'couldn\'t read "not a date" as a date'),
    ('datetime.parse("2026", "%d/%m/%Y")', 'with the pattern "%d/%m/%Y"'),
    ('datetime.now("Mars/Olympus")', "I don't know the time zone 'Mars/Olympus'"),
    ('datetime.now("Europe/Berlinn")', "Did you mean 'Europe/Berlin'?"),
    ('datetime.now(5)', "datetime.now() needs zone to be text"),
    ('datetime.now().add({"fortnights": 1})', 'doesn\'t know the unit "fortnights"'),
    ('datetime.now().add({"months": 1.5})', "needs whole months"),
    ('datetime.now().add(3)', "needs amounts to be a map"),
    ('datetime.now().diff("x")', 'couldn\'t read "x" as a date'),
    ('datetime.now().diff([1])', "needs other to be a datetime"),
    ('datetime.make({"month": 1})', 'needs at least a "year"'),
    ('datetime.make({"year": 2026, "month": 13})', "can't build that date"),
    ('datetime.make({"year": 2026, "hours": 1})', 'doesn\'t know the part "hours"'),
    ('datetime.now().start_of("decade")', 'not "decade"'),
    ('datetime.from_timestamp(1000000000000000000)', "too far from today"),
])
def test_datetime_errors(src, message):
    assert message in str(tb_error("import datetime\n" + src))


def test_errors_have_reason():
    err = tb_error('import datetime\ndatetime.now("Nowhere/Here")')
    assert err.fields["reason"] == "zone"


def test_docs():
    info = tb.describe_library("datetime")
    assert info["doc"]
    for fn in info["functions"]:
        assert fn["doc"] and fn["signature"].startswith(fn["name"] + "(")
