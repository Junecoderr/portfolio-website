"""datetime: dates and times that know their time zone."""

from __future__ import annotations

import datetime as _dt
import re

from tblib._api import Module, check_type, fail

import tb as _tb

DEFAULT_PATTERN = "%Y-%m-%d %H:%M:%S"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
OFFSET = re.compile(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)
UNITS = {"years": None, "months": None, "weeks": 7 * 86400, "days": 86400, "hours": 3600,
         "minutes": 60, "seconds": 1}
UNIT_ALIASES = {"year": "years", "month": "months", "week": "weeks", "day": "days", "hour": "hours",
                "minute": "minutes", "second": "seconds"}


# -- zones ---------------------------------------------------------------------


def zone_for(name, line=None, fn="datetime"):
    """A tzinfo for a zone name: "UTC", "local", an IANA name like
    "Europe/Berlin" or "Asia/Kolkata", or an offset like "+05:30"."""
    if name is None or name == "" or str(name).lower() == "local":
        return None  # None means "this computer's local time"
    check_type(line, name, "text", fn, "zone")
    text = name.strip()
    if text.upper() in ("UTC", "Z", "GMT", "UCT", "ZULU"):
        return _dt.timezone.utc
    m = OFFSET.match(text)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        hours, minutes = int(m.group(2)), int(m.group(3) or 0)
        if hours > 23 or minutes > 59:
            fail(line, f"{fn}() got an impossible zone offset '{name}'.")
        return _dt.timezone(sign * _dt.timedelta(hours=hours, minutes=minutes))
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(text)
    except Exception:  # ZoneInfoNotFoundError, ValueError, ImportError (no tzdata on Windows)
        hint = ""
        try:
            import zoneinfo
            names = zoneinfo.available_timezones()
            if not names:
                hint = " This computer has no time-zone database; install the 'tzdata' package."
            else:
                import difflib
                close = difflib.get_close_matches(text, names, n=1)
                if close:
                    hint = f" Did you mean '{close[0]}'?"
                else:
                    hint = ' Zone names look like "Europe/Berlin" or "America/New_York"; see datetime.zones().'
        except Exception:
            hint = " This computer has no time-zone database."
        fail(line, f"I don't know the time zone '{name}'.{hint}", reason="zone")


def zone_name(when: _dt.datetime) -> str:
    tz = when.tzinfo
    key = getattr(tz, "key", None)
    if key:
        return key
    offset = when.utcoffset() or _dt.timedelta(0)
    total = int(offset.total_seconds())
    if total == 0:
        return "UTC"
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}"


def offset_text(when: _dt.datetime) -> str:
    offset = when.utcoffset() or _dt.timedelta(0)
    total = int(offset.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}"


def aware(when: _dt.datetime, tz=None) -> _dt.datetime:
    """Give a datetime a zone: naive ones are local time."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz) if tz is not None else when.astimezone()
    return when


# -- parsing -------------------------------------------------------------------


def parse_text(text: str, pattern=None, tz=None, line=None):
    """A zone-aware datetime from text. Without a pattern: ISO 8601 and the
    common log formats (shared with the logs library). tz is the zone for
    text that doesn't say its own."""
    raw = text.strip()
    if pattern is not None:
        try:
            when = _dt.datetime.strptime(raw, pattern)
        except ValueError:
            fail(line, f'datetime.parse() couldn\'t read "{text}" with the pattern "{pattern}".', reason="parse")
        return aware(when, tz)
    when = None
    try:
        when = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        pass
    if when is None:
        try:
            from tblib import logs as _logs
            when = _logs.parse_datetime(raw)
            if when is not None and when.tzinfo is None and tz is not None:
                when = when.replace(tzinfo=tz)
        except Exception:
            when = None
    if when is None:
        try:
            when = _dt.datetime.combine(_dt.date.fromisoformat(raw), _dt.time())
        except ValueError:
            pass
    if when is None:
        fail(line, f'datetime.parse() couldn\'t read "{text}" as a date. Try ISO form like '
                   f'"2026-10-26T14:30:00+02:00", a log form like "Oct 26 14:30:00", or give a pattern.',
             reason="parse")
    return aware(when, tz)


def load(api):
    m = Module(
        "datetime",
        "Dates and times that know their time zone. datetime.now(zone?) and datetime.parse(text) give a "
        "moment with fields (year, month, day, hour, minute, second, weekday, date, zone, timestamp, iso) "
        "and methods to_zone(z), to_utc(), format(pattern), add({days: 1}), diff(other), before/after/equals. "
        "Zones are names like \"UTC\", \"Europe/Berlin\", \"Asia/Kolkata\", \"local\" or offsets like \"+05:30\". "
        "The timestamp field works with the time library.",
    )

    def method(obj, name, lo, hi, func, doc):
        obj[name] = _tb.Builtin(f"datetime.{name}", func, (lo, hi), doc)

    def is_moment(value) -> bool:
        return isinstance(value, dict) and isinstance(value.get("_when"), _dt.datetime)

    def moment_arg(line, value, fn, arg="other"):
        if is_moment(value):
            return value["_when"]
        if _tb.is_number(value) and not isinstance(value, bool):
            return from_timestamp(line, value, fn)
        if isinstance(value, str):
            return parse_text(value, None, None, line)
        fail(line, f"{fn}() needs {arg} to be a datetime (from datetime.now/parse), a timestamp or date text, "
                   f"not {_tb.type_name(value)}.")

    def from_timestamp(line, ts, fn, tz=None):
        check_type(line, ts, "number", fn, "timestamp")
        try:
            when = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            fail(line, f"{fn}() got a timestamp that is too far from today: {_tb.to_text(ts)}.")
        return when.astimezone(tz) if tz is not None else when.astimezone()

    def make(when: _dt.datetime) -> dict:
        when = aware(when)
        ts = _tb.tidy_number(round(when.timestamp(), 6))
        d = {
            "iso": when.isoformat(sep="T", timespec="seconds" if when.microsecond == 0 else "microseconds"),
            "year": when.year, "month": when.month, "day": when.day,
            "hour": when.hour, "minute": when.minute, "second": when.second,
            "microsecond": when.microsecond,
            "weekday": WEEKDAYS[when.weekday()], "month_name": MONTHS[when.month - 1],
            "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M:%S"),
            "zone": zone_name(when), "offset": offset_text(when),
            "timestamp": ts,
            "_when": when,
        }

        def to_zone(line, zone):
            tz = zone_for(zone, line, "datetime.to_zone")
            return make(when.astimezone(tz) if tz is not None else when.astimezone())

        def to_utc(line):
            return make(when.astimezone(_dt.timezone.utc))

        def format_(line, pattern=DEFAULT_PATTERN):
            check_type(line, pattern, "text", "datetime.format", "pattern")
            try:
                return when.strftime(pattern)
            except ValueError as err:
                fail(line, f"datetime.format() can't use that pattern: {err}.")

        def add(line, amounts):
            check_type(line, amounts, "map", "datetime.add", "amounts")
            result = when
            seconds = 0.0
            for key, value in amounts.items():
                unit = UNIT_ALIASES.get(str(key), str(key))
                if unit not in UNITS:
                    fail(line, f'datetime.add() doesn\'t know the unit "{key}". Use years, months, weeks, days, '
                               f'hours, minutes or seconds.')
                check_type(line, value, "number", "datetime.add", f'"{key}"')
                if unit in ("years", "months"):
                    if value != int(value):
                        fail(line, f"datetime.add() needs whole {unit}, not {_tb.to_text(value)}.")
                    months = int(value) * (12 if unit == "years" else 1)
                    total = result.year * 12 + (result.month - 1) + months
                    year, month = divmod(total, 12)
                    month += 1
                    import calendar
                    day = min(result.day, calendar.monthrange(year, month)[1])
                    try:
                        result = result.replace(year=year, month=month, day=day)
                    except ValueError:
                        fail(line, "datetime.add() went outside the dates I can handle (years 1 to 9999).")
                else:
                    seconds += value * UNITS[unit]
            try:
                result = result + _dt.timedelta(seconds=seconds)
            except OverflowError:
                fail(line, "datetime.add() went outside the dates I can handle (years 1 to 9999).")
            tz = result.tzinfo
            if getattr(tz, "key", None):  # re-normalise wall time across DST changes
                result = result.astimezone(_dt.timezone.utc).astimezone(tz)
            return make(result)

        def diff(line, other):
            o = moment_arg(line, other, "datetime.diff")
            return _tb.tidy_number(round((when - o).total_seconds(), 6))

        def before(line, other):
            return when < moment_arg(line, other, "datetime.before")

        def after(line, other):
            return when > moment_arg(line, other, "datetime.after")

        def equals(line, other):
            return when == moment_arg(line, other, "datetime.equals")

        def start_of(line, unit):
            check_type(line, unit, "text", "datetime.start_of", "unit")
            u = UNIT_ALIASES.get(unit, unit)
            if u == "years":
                return make(when.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0))
            if u == "months":
                return make(when.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
            if u == "weeks":
                start = when - _dt.timedelta(days=when.weekday())
                return make(start.replace(hour=0, minute=0, second=0, microsecond=0))
            if u == "days":
                return make(when.replace(hour=0, minute=0, second=0, microsecond=0))
            if u == "hours":
                return make(when.replace(minute=0, second=0, microsecond=0))
            if u == "minutes":
                return make(when.replace(second=0, microsecond=0))
            fail(line, f'datetime.start_of() knows "year", "month", "week", "day", "hour" and "minute", not "{unit}".')

        def text(line):
            return d["iso"]

        for name, lo, hi, func, doc in [
            ("to_zone", 1, 1, to_zone, "to_zone(zone) -> datetime\nThe same moment shown in another zone: "
                                         "d.to_zone(\"Asia/Kolkata\")."),
            ("to_utc", 0, 0, to_utc, "to_utc() -> datetime\nThe same moment in UTC."),
            ("format", 0, 1, format_, "format(pattern?) -> text\nThe date as text. %Y year, %m month, %d day, %H hour, "
                                       "%M minute, %S second, %A weekday, %B month name, %Z zone, %z offset "
                                       "(default \"%Y-%m-%d %H:%M:%S\")."),
            ("add", 1, 1, add, "add(amounts) -> datetime\nMove forward (or back with negatives): "
                                "d.add({\"days\": 1, \"hours\": -2}). Units: years, months, weeks, days, hours, "
                                "minutes, seconds. Months clamp to the month's last day."),
            ("diff", 1, 1, diff, "diff(other) -> number\nSeconds from other to this moment (negative when other is later). "
                                  "other may be a datetime, a timestamp or date text."),
            ("before", 1, 1, before, "before(other) -> bool\nTrue if this moment is earlier than other."),
            ("after", 1, 1, after, "after(other) -> bool\nTrue if this moment is later than other."),
            ("equals", 1, 1, equals, "equals(other) -> bool\nTrue if both are the same instant (even in different zones)."),
            ("start_of", 1, 1, start_of, "start_of(unit) -> datetime\nThe start of this \"year\", \"month\", \"week\" "
                                          "(Monday), \"day\", \"hour\" or \"minute\"."),
            ("text", 0, 0, text, "text() -> text\nThe ISO 8601 form, like \"2026-10-26T14:30:00+02:00\" (same as .iso)."),
        ]:
            method(d, name, lo, hi, func, doc)
        return d

    @m.fn("now", 0, 1, doc=(
        "now(zone?) -> datetime\n"
        "The moment right now, in this computer's zone or the zone given: datetime.now(\"UTC\")."))
    def now(line, zone=None):
        tz = zone_for(zone, line, "datetime.now")
        return make(_dt.datetime.now(tz) if tz is not None else _dt.datetime.now().astimezone())

    @m.fn("parse", 1, 3, doc=(
        "parse(text, pattern?, zone?) -> datetime\n"
        "Read a date written as text. Without a pattern it reads ISO 8601 (\"2026-10-26T14:30:00Z\", "
        "\"2026-10-26 14:30\") and common log forms (\"Oct 26 14:30:00\", \"26/Oct/2026:14:30:00 +0000\", "
        "\"10/26/2026 2:30 PM\"). A pattern uses %Y %m %d %H %M %S %z. zone is used when the text has none "
        "(default: this computer's zone). Pass nil as the pattern to give only a zone."))
    def parse(line, text, pattern=None, zone=None):
        check_type(line, text, "text", "datetime.parse", "text")
        if pattern is not None:
            check_type(line, pattern, "text", "datetime.parse", "pattern")
        tz = zone_for(zone, line, "datetime.parse")
        return make(parse_text(text, pattern, tz, line))

    @m.fn("from_timestamp", 1, 2, doc=(
        "from_timestamp(timestamp, zone?) -> datetime\n"
        "The moment for a timestamp (seconds since 1970, like time.timestamp() gives), shown in the zone "
        "given or this computer's."))
    def from_timestamp_(line, ts, zone=None):
        tz = zone_for(zone, line, "datetime.from_timestamp")
        return make(from_timestamp(line, ts, "datetime.from_timestamp", tz))

    @m.fn("make", 1, 2, doc=(
        "make(parts, zone?) -> datetime\n"
        "Build a moment from parts: datetime.make({\"year\": 2026, \"month\": 10, \"day\": 26, \"hour\": 9}, "
        "\"Europe/Berlin\"). Missing parts are 1 for month and day, 0 for the time."))
    def make_(line, parts, zone=None):
        check_type(line, parts, "map", "datetime.make", "parts")
        tz = zone_for(zone, line, "datetime.make")
        values = {}
        for key in ("year", "month", "day", "hour", "minute", "second", "microsecond"):
            value = parts.get(key)
            if value is None:
                value = 1 if key in ("month", "day") else 0
            values[key] = check_type(line, value, "whole", "datetime.make", f'"{key}"')
        unknown = [k for k in parts if k not in values]
        if unknown:
            fail(line, f'datetime.make() doesn\'t know the part "{unknown[0]}". Parts: year, month, day, hour, '
                       f'minute, second, microsecond.')
        if "year" not in parts:
            fail(line, 'datetime.make() needs at least a "year".')
        try:
            when = _dt.datetime(**values, tzinfo=tz)
        except (ValueError, OverflowError) as err:
            fail(line, f"datetime.make() can't build that date: {err}.")
        return make(when if tz is not None else when.astimezone())

    @m.fn("today", 0, 1, doc="today(zone?) -> text\nToday's date in a zone, like \"2026-10-26\".")
    def today(line, zone=None):
        tz = zone_for(zone, line, "datetime.today")
        return (_dt.datetime.now(tz) if tz is not None else _dt.datetime.now()).strftime("%Y-%m-%d")

    @m.fn("zones", 0, 1, doc=(
        "zones(filter?) -> list\n"
        "The time-zone names this computer knows (sorted), like \"Europe/Berlin\"; with filter, only names "
        "containing that text: datetime.zones(\"Asia\"). Empty if the computer has no zone database."))
    def zones(line, flt=None):
        if flt is not None:
            check_type(line, flt, "text", "datetime.zones", "filter")
        try:
            import zoneinfo
            names = sorted(zoneinfo.available_timezones())
        except Exception:
            names = []
        if "UTC" not in names:
            names.append("UTC")
            names.sort()
        if flt:
            low = flt.lower()
            names = [n for n in names if low in n.lower()]
        return names

    @m.fn("is_zone", 1, doc="is_zone(name) -> bool\nTrue if name is a zone this computer knows, or an offset like \"+02:00\".")
    def is_zone(line, name):
        if not isinstance(name, str):
            return False
        try:
            zone_for(name, line, "datetime.is_zone")
            return True
        except _tb.TBError:
            return False

    @m.fn("is_datetime", 1, doc="is_datetime(x) -> bool\nTrue if x came from datetime.now/parse/make/from_timestamp.")
    def is_datetime(line, value):
        return is_moment(value)

    @m.fn("local_zone", 0, doc="local_zone() -> text\nThis computer's zone as an offset like \"+05:30\" (or \"UTC\").")
    def local_zone(line):
        return zone_name(_dt.datetime.now().astimezone())

    return m
