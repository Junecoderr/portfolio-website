"""time: clocks, dates and waiting."""

from __future__ import annotations

import datetime as _dt
import time as _time

from tblib._api import Module, check_type, fail

import tb as _tb

DEFAULT_PATTERN = "%Y-%m-%d %H:%M:%S"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def load(api):
    m = Module(
        "time",
        "Clocks and dates: the time now, timestamps, formatting, parsing and waiting.",
    )

    def stamp(line, ts, fn):
        check_type(line, ts, "number", f"time.{fn}", "timestamp")
        try:
            return _dt.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            fail(line, f"time.{fn}() got a timestamp that is too far from today: {_tb.to_text(ts)}.")

    def as_map(when: _dt.datetime) -> dict:
        return {
            "year": when.year, "month": when.month, "day": when.day,
            "hour": when.hour, "minute": when.minute, "second": when.second,
            "weekday": WEEKDAYS[when.weekday()], "month_name": MONTHS[when.month - 1],
            "timestamp": _tb.tidy_number(round(when.timestamp(), 6)),
        }

    @m.fn("now", 0, doc="now() -> map\nThe date and time right now: {\"year\", \"month\", \"day\", \"hour\", \"minute\", \"second\", \"weekday\", \"month_name\", \"timestamp\"}.")
    def now(line):
        return as_map(_dt.datetime.now())

    @m.fn("timestamp", 0, doc="timestamp() -> number\nSeconds since 1 January 1970 (UTC), with fractions. Good for timing and storing.")
    def timestamp(line):
        return _time.time()

    @m.fn("sleep", 1, doc="sleep(seconds)\nWait for that many seconds (fractions are fine) before going on.")
    def sleep(line, seconds):
        check_type(line, seconds, "number", "time.sleep", "seconds")
        if seconds < 0:
            fail(line, "time.sleep() needs 0 or more seconds.")
        if seconds > 86_400:
            fail(line, "time.sleep() waits at most one day (86400 seconds).")
        _time.sleep(seconds)
        return None

    @m.fn("format", 1, 2, doc="format(timestamp, pattern?) -> text\nA timestamp as readable text, in local time. The pattern uses %Y year, %m month, %d day, %H hour, %M minute, %S second, %A weekday, %B month name (default \"%Y-%m-%d %H:%M:%S\").")
    def format_(line, ts, pattern=DEFAULT_PATTERN):
        when = stamp(line, ts, "format")
        check_type(line, pattern, "text", "time.format", "pattern")
        try:
            return when.strftime(pattern)
        except ValueError as err:
            fail(line, f"time.format() can't use that pattern: {err}.")

    @m.fn("parse", 1, 2, doc="parse(text, pattern?) -> number\nRead a date/time written as text and give its timestamp. Without a pattern it reads forms like \"2026-10-26\", \"2026-10-26 14:30:00\", \"2026-10-26T14:30:00Z\" and common log forms (\"Oct 26 14:30:00\"). See the datetime library for zones.")
    def parse(line, text, pattern=None):
        check_type(line, text, "text", "time.parse", "text")
        try:
            if pattern is None:
                when = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
            else:
                check_type(line, pattern, "text", "time.parse", "pattern")
                when = _dt.datetime.strptime(text.strip(), pattern)
        except ValueError:
            when = None
            if pattern is None:  # the log forms the logs/datetime libraries read
                try:
                    from tblib import logs as _logs
                    when = _logs.parse_datetime(text)
                except Exception:
                    when = None
            if when is None:
                how = f' with the pattern "{pattern}"' if pattern else ' (try a pattern, or a form like "2026-10-26 14:30:00")'
                fail(line, f'time.parse() couldn\'t read "{text}" as a date{how}.')
        try:
            return _tb.tidy_number(when.timestamp())
        except (OverflowError, OSError, ValueError):
            fail(line, f'time.parse() can\'t turn "{text}" into a timestamp.')

    @m.fn("today", 0, doc="today() -> text\nToday's date, like \"2026-10-26\".")
    def today(line):
        return _dt.date.today().isoformat()

    @m.fn("since", 1, doc="since(timestamp) -> number\nHow many seconds have passed since that timestamp.")
    def since(line, ts):
        check_type(line, ts, "number", "time.since", "timestamp")
        return _time.time() - ts

    return m
