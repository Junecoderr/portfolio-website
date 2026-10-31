"""logs: read and parse log files (syslog, auth.log, web servers, JSON, CSV, Windows)."""

from __future__ import annotations

import csv as _csv
import datetime as _dt
import io
import ipaddress
import itertools
import json as _json
import os
import re

from tblib import _magic
from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

FORMATS = ["auto", "syslog", "auth", "apache", "nginx", "json", "csv", "windows", "text", "evtx",
           "zeek", "suricata", "cloudtrail", "okta", "m365", "cef", "leef"]
MAX_RECORDS = 5_000_000
SNIFF_BYTES = 64 * 1024
MAX_LINE = 4 * 1024 * 1024             # characters kept from one log line; longer lines are cut
MAX_JSON_DOCUMENT = 64 * 1024 * 1024  # characters of a JSON array/document file read whole
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# =============================================================================
# Time
# =============================================================================

ISO_TIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?",
    re.IGNORECASE)
SLASH_YMD = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?")
APACHE_TIME = re.compile(r"(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})(?:\s*([+-]\d{4}))?")
SYSLOG_TIME = re.compile(r"([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\s+(\d{4}))?")
US_TIME = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*([AaPp][Mm]))?")
RFC2822_TIME = re.compile(
    r"(?:[A-Za-z]{3},\s*)?(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(Z|GMT|UTC|[+-]\d{4})?",
    re.IGNORECASE)
DOTNET_DATE = re.compile(r"\\?/Date\((-?\d+)([+-]\d{4})?\)\\?/")


def _offset(text):
    if not text:
        return None
    if text.upper() in ("Z", "GMT", "UTC"):
        return _dt.timezone.utc
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) >= 4 else 0
    return _dt.timezone(sign * _dt.timedelta(hours=hours, minutes=minutes))


def _stamp(year, month, day, hour, minute, second, micro=0, tz=None):
    """(timestamp, "YYYY-MM-DD HH:MM:SS") or None. Times without a zone are
    the computer's local time, like the time library."""
    when = _make(year, month, day, hour, minute, second, micro, tz)
    return None if when is None else _pair(when)


def _make(year, month, day, hour, minute, second, micro=0, tz=None):
    """A datetime (zone-aware when the log said its zone, else naive local
    time), or None when the numbers don't make a real date."""
    try:
        when = _dt.datetime(year, month, day, hour, minute, second, micro, tzinfo=tz)
        when.timestamp()  # too far from today? (OverflowError / OSError)
    except (ValueError, OverflowError, OSError):
        return None
    return when


def _pair(when):
    """(timestamp, display text) for a datetime from _make()."""
    return _tb.tidy_number(round(when.timestamp(), 6)), when.strftime("%Y-%m-%d %H:%M:%S")


def zone_text(when) -> str | None:
    """How a record shows its zone: "UTC", "+02:00", "-05:30"; None when the
    log line carried no zone (the time was read as local time)."""
    if when is None or when.tzinfo is None:
        return None
    offset = when.utcoffset()
    if offset is None:
        return None
    total = int(offset.total_seconds())
    if total == 0:
        return "UTC"
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}"


def utc_text(ts) -> str | None:
    """A timestamp as "YYYY-MM-DD HH:MM:SS" in UTC, so records from logs in
    different zones sort and compare the same way. None for no timestamp."""
    if ts is None or isinstance(ts, bool) or not _tb.is_number(ts):
        return None
    try:
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return None


def guess_year(month, day, now=None):
    """Syslog lines have no year: use this year, unless that date would be
    in the future (then it was last year: December logs read in January)."""
    now = now or _dt.datetime.now()
    year = now.year
    try:
        if _dt.datetime(year, month, day) > now + _dt.timedelta(days=2):
            year -= 1
    except ValueError:
        pass
    return year


def parse_time(value, year=None):
    """A timestamp (seconds since 1970) and display text from many log time
    formats, or (None, None). The display text is the time as written in the
    log (its own zone, or local time when the log gave none); use
    utc_text(ts) / zone_text(parse_datetime(value)) for the zone-aware view."""
    when = parse_datetime(value, year)
    if when is None:
        return None, None
    return _pair(when)


def parse_datetime(value, year=None):
    """A datetime for a log time written in many ways, or None. It is
    zone-aware when the text carried a zone ("Z", "+02:00", "+0000") or was
    a numeric timestamp (UTC); otherwise it is naive local time. The
    datetime library reuses this so both read the same formats."""
    if value is None or isinstance(value, bool):
        return None
    if _tb.is_number(value):
        ts = value / 1000 if abs(value) > 1e11 else value
        try:
            return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).astimezone()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, dict):
        for key in ("SystemTime", "systemtime", "#text", "value", "DateTime"):
            if key in value:
                return parse_datetime(value[key], year)
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
        return parse_datetime(float(text), year)
    m = DOTNET_DATE.search(text)
    if m:
        ms = int(m.group(1))
        tz = _offset(m.group(2)) or _dt.timezone.utc
        try:
            return _dt.datetime.fromtimestamp(ms / 1000, tz)
        except (ValueError, OverflowError, OSError):
            return None
    m = ISO_TIME.match(text)
    if m:
        y, mo, d, h, mi, s, frac, tz = m.groups()
        micro = int((frac or "0")[:6].ljust(6, "0"))
        return _make(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), micro, _offset(tz))
    m = SLASH_YMD.match(text)
    if m:
        y, mo, d, h, mi, s = m.groups()
        return _make(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    m = APACHE_TIME.match(text)
    if m and m.group(2).lower() in MONTHS:
        d, mon, y, h, mi, s, tz = m.groups()
        return _make(int(y), MONTHS[mon.lower()], int(d), int(h), int(mi), int(s), 0, _offset(tz))
    m = US_TIME.match(text)
    if m:
        a, b, y, h, mi, s, ampm = m.groups()
        month, day = int(a), int(b)
        if month > 12 >= day:  # 25/12/2024 is day-first
            month, day = day, month
        hour = int(h)
        if ampm:
            hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
        return _make(int(y), month, day, hour, int(mi), int(s or 0))
    m = SYSLOG_TIME.match(text)
    if m and m.group(1).lower() in MONTHS:
        mon, d, h, mi, s, y = m.groups()
        month, day = MONTHS[mon.lower()], int(d)
        yr = int(y) if y else (year or guess_year(month, day))
        return _make(yr, month, day, int(h), int(mi), int(s))
    m = RFC2822_TIME.match(text)
    if m:
        d, mon, y, h, mi, s, tz = m.groups()
        if mon.lower() in MONTHS:
            return _make(int(y), MONTHS[mon.lower()], int(d), int(h), int(mi), int(s or 0), 0, _offset(tz))
    return None


class YearTracker:
    """Fills in the missing year of RFC 3164 syslog lines. With a given year,
    a jump from December back to January moves to the next year."""

    def __init__(self, year=None):
        self.year = year
        self.last_month = None

    def year_for(self, month, day):
        if self.year is None:
            return guess_year(month, day)
        if self.last_month == 12 and month == 1:
            self.year += 1
        self.last_month = month
        return self.year


# =============================================================================
# Records
# =============================================================================

def make_record(fmt, raw, time_value=None, when=None, message=None, fields=None):
    """A log record map. "time" is the UTC timestamp, "when" the time as the
    log wrote it, "when_utc" the same instant as UTC text (compare and sort
    records from different zones by this), and "zone" the zone the line
    carried ("UTC", "+02:00") or the local zone it was read in."""
    record = {"time": time_value, "when": when, "when_utc": utc_text(time_value),
              "zone": _zone_of(time_value, when), "format": fmt,
              "message": raw if message is None else message}
    if fields:
        for key, value in fields.items():
            if key not in ("time", "when", "when_utc", "zone", "format", "message", "raw"):
                record[key] = value
    record["raw"] = raw
    return record


def _zone_of(ts, when):
    """The zone a record's "when" text is in, worked out from the gap between
    that wall-clock text and the UTC timestamp."""
    if ts is None or not isinstance(when, str) or isinstance(ts, bool) or not _tb.is_number(ts):
        return None
    try:
        wall = _dt.datetime.strptime(when, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc)
        gap = round((wall.timestamp() - ts) / 60) * 60
        return zone_text(_dt.datetime.fromtimestamp(0, _dt.timezone(_dt.timedelta(seconds=gap))))
    except (ValueError, OverflowError, OSError):
        return None


def text_record(line, fmt="text"):
    """A line nobody recognised: keep it, with a time if it starts with one."""
    ts, when = parse_time(line.lstrip()[:40])
    return make_record(fmt, line, ts, when, line.strip())


def none_if_dash(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if value in ("", "-", "--", "N/A", "n/a") else value


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# =============================================================================
# Syslog and auth.log
# =============================================================================

SYSLOG_3164 = re.compile(
    r"^(?:<(\d{1,3})>)?([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\s+(\d{4}))?\s+(\S+)\s+(.*)$")
SYSLOG_ISO = re.compile(
    r"^(?:<(\d{1,3})>)?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(\S+)\s+(.*)$")
SYSLOG_5424 = re.compile(
    r"^<(\d{1,3})>1\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(-|(?:\[.*?\])+)\s?(.*)$")
TAG = re.compile(r"^([^\s:\[\]]+)(?:\[(\d+)\])?:\s?(.*)$", re.DOTALL)
SEVERITIES = ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]

AUTH_PROGRAMS = {"sshd", "sudo", "su", "login", "useradd", "userdel", "usermod", "groupadd", "passwd",
                 "chpasswd", "chage", "systemd-logind", "gdm-password", "polkitd", "pkexec", "cron", "CRON",
                 "unix_chkpwd", "dropbear", "vsftpd", "proftpd", "dovecot"}


def parse_syslog(line, years: YearTracker):
    fields = {}
    m = SYSLOG_5424.match(line)
    if m:
        pri, stamp, host, app, procid, msgid, _sd, msg = m.groups()
        ts, when = parse_time(stamp)
        fields.update(host=none_if_dash(host), program=none_if_dash(app), pid=to_int(procid))
        add_priority(fields, pri)
        return finish_syslog(line, ts, when, msg, fields, tagged=True)
    m = SYSLOG_3164.match(line)
    if m:
        pri, mon, day, hh, mi, ss, year, host, rest = m.groups()
        if mon.lower() not in MONTHS:
            return None
        month = MONTHS[mon.lower()]
        yr = int(year) if year else years.year_for(month, int(day))
        stamp = _stamp(yr, month, int(day), int(hh), int(mi), int(ss))
        ts, when = stamp if stamp else (None, None)
    else:
        m = SYSLOG_ISO.match(line)
        if not m:
            return None
        pri, stamp_text, host, rest = m.groups()
        ts, when = parse_time(stamp_text)
    fields["host"] = host
    add_priority(fields, pri)
    return finish_syslog(line, ts, when, rest, fields)


def add_priority(fields, pri):
    if pri is None:
        return
    value = int(pri)
    fields["facility"] = value // 8
    fields["severity"] = SEVERITIES[value % 8]


def finish_syslog(line, ts, when, rest, fields, tagged=False):
    if not tagged:
        tag = TAG.match(rest)
        if tag:
            fields["program"], pid, rest = tag.group(1), tag.group(2), tag.group(3)
            fields["pid"] = to_int(pid) if pid else None
        else:
            fields.setdefault("program", None)
            fields.setdefault("pid", None)
    message = rest.strip()
    if fields.get("program") == "kernel":
        message = re.sub(r"^\[\s*\d+\.\d+\]\s*", "", message)
    if "SRC=" in message and ("DPT=" in message or "PROTO=" in message):
        firewall_fields(message, fields)
    return make_record("syslog", line, ts, when, message, fields)


def firewall_fields(message, fields):
    """iptables / UFW kernel lines: SRC=1.2.3.4 DST=... PROTO=TCP SPT=... DPT=22."""
    kv = dict(re.findall(r"\b([A-Z]+)=(\S*)", message))
    upper = message.upper()
    if any(word in upper for word in ("BLOCK", "DROP", "REJECT", "DENY")):
        action = "firewall_block"
    elif any(word in upper for word in ("ALLOW", "ACCEPT", "AUDIT")):
        action = "firewall_allow"
    else:
        action = "firewall"
    fields.update(
        action=action, ip=none_if_dash(kv.get("SRC")), src_ip=none_if_dash(kv.get("SRC")),
        dst_ip=none_if_dash(kv.get("DST")), proto=none_if_dash(kv.get("PROTO")),
        src_port=to_int(kv.get("SPT")), port=to_int(kv.get("DPT")), dst_port=to_int(kv.get("DPT")),
        interface=none_if_dash(kv.get("IN")),
    )


# sshd and friends. Each: (pattern, action, success, extra field names)
SSH_USER = r"(?:invalid user )?(?P<user>\S*?)"
AUTH_PATTERNS = [
    (re.compile(r"^Failed (?P<method>password|publickey|keyboard-interactive(?:/pam)?|none|hostbased) for "
                r"(?P<invalid>invalid user )?(?P<user>\S*?) from (?P<ip>\S+) port (?P<port>\d+)"),
     "failed_login", False),
    (re.compile(r"^Accepted (?P<method>password|publickey|keyboard-interactive(?:/pam)?|hostbased|gssapi-with-mic) for "
                r"(?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)"),
     "login", True),
    (re.compile(r"^Invalid user (?P<user>\S*?) from (?P<ip>\S+)(?: port (?P<port>\d+))?"),
     "invalid_user", False),
    (re.compile(r"^(?:error: )?maximum authentication attempts exceeded for " + SSH_USER +
                r" from (?P<ip>\S+) port (?P<port>\d+)"),
     "too_many_attempts", False),
    (re.compile(r"^Connection closed by (?:authenticating user |invalid user )?(?:(?P<user>\S+) )?"
                r"(?P<ip>[0-9a-fA-F:.]+) port (?P<port>\d+)(?: \[preauth\])?"),
     "disconnect", None),
    (re.compile(r"^Disconnected from (?:authenticating user |invalid user |user )?(?:(?P<user>\S+) )?"
                r"(?P<ip>[0-9a-fA-F:.]+) port (?P<port>\d+)"),
     "disconnect", None),
    (re.compile(r"^Received disconnect from (?P<ip>\S+) port (?P<port>\d+)"),
     "disconnect", None),
    (re.compile(r"^Did not receive identification string from (?P<ip>\S+)(?: port (?P<port>\d+))?"),
     "probe", None),
    (re.compile(r"^pam_unix\((?P<service>[\w-]+):auth\): authentication failure;.*?(?:rhost=(?P<ip>\S*))?"
                r"(?:\s+user=(?P<user>\S+))?\s*$"),
     "auth_failure", False),
    (re.compile(r"^pam_unix\((?P<service>[\w-]+):session\): session opened for user (?P<user>[^\s(]+)"
                r"(?:\(uid=\d+\))?(?: by \S*)?"),
     "session_open", None),
    (re.compile(r"^pam_unix\((?P<service>[\w-]+):session\): session closed for user (?P<user>\S+)"),
     "session_close", None),
    (re.compile(r"^\s*(?P<user>\S+) : (?P<attempts>\d+) incorrect password attempts? ;"
                r"(?: TTY=(?P<tty>\S+) ;)? PWD=(?P<pwd>.*?) ; USER=(?P<target_user>\S+) ; COMMAND=(?P<command>.*)$"),
     "sudo_failed", False),
    (re.compile(r"^\s*(?P<user>\S+) : (?:TTY=(?P<tty>\S+) ; )?PWD=(?P<pwd>.*?) ; USER=(?P<target_user>\S+) ;"
                r"(?: ENV=.*? ;)? COMMAND=(?P<command>.*)$"),
     "sudo", True),
    (re.compile(r"^\s*(?P<user>\S+) : user NOT in sudoers ;.*?USER=(?P<target_user>\S+) ; COMMAND=(?P<command>.*)$"),
     "sudo_denied", False),
    (re.compile(r"^(?:FAILED SU|FAILED su).*?\(to (?P<target_user>\S+)\) (?P<user>\S+)"),
     "su_failed", False),
    (re.compile(r"^(?:Successful su|\(to (?P<target_user>\S+)\) (?P<user>\S+) on)"),
     "su", True),
    (re.compile(r"^new user: name=(?P<user>[^,\s]+),"), "user_created", True),
    (re.compile(r"^new group: name=(?P<group>[^,\s]+),"), "group_created", True),
    (re.compile(r"^delete user '(?P<user>[^']+)'"), "user_deleted", True),
    (re.compile(r"^add '(?P<user>[^']+)' to (?:shadow )?group '(?P<group>[^']+)'"), "group_member_added", True),
    (re.compile(r"(?:^|\): )password changed for (?P<user>\S+)"), "password_changed", True),
    (re.compile(r"^New session \S+ of user (?P<user>\S+?)\.?$"), "session_open", None),
]
REPEATED = re.compile(r"^message repeated (\d+) times: \[\s*(.*?)\s*\]$")


def auth_fields(record):
    """Add user, ip, action and success to a syslog record from auth.log."""
    record["format"] = "auth"
    message = record.get("message") or ""
    fields = {"user": None, "ip": record.get("ip"), "action": record.get("action"), "success": None}
    repeat = REPEATED.match(message)
    if repeat:
        fields["repeat"] = int(repeat.group(1))
        message = repeat.group(2)
    for pattern, action, success in AUTH_PATTERNS:
        m = pattern.search(message)
        if not m:
            continue
        found = {k: v for k, v in m.groupdict().items() if v is not None}
        fields["action"] = action
        fields["success"] = success
        fields["user"] = none_if_dash(found.pop("user", None))
        if "ip" in found:
            ip = none_if_dash(found.pop("ip"))
            fields["ip"] = ip
        if "port" in found:
            fields["port"] = to_int(found.pop("port"))
        if found.pop("invalid", None):
            fields["invalid_user"] = True
        for key, value in found.items():
            fields[key] = to_int(value) if key == "attempts" else value
        break
    # keep the field order tidy: put the auth fields before raw
    raw = record.pop("raw")
    for key, value in fields.items():
        if key not in record or value is not None:
            record[key] = value
    record["raw"] = raw
    return record


# =============================================================================
# Apache / nginx
# =============================================================================

QUOTED = r'"((?:[^"\\]|\\.)*)"'
APACHE = re.compile(
    rf'^(\S+) (\S+) (\S+) \[([^\]]+)\] {QUOTED} (\d{{3}}|-) (\d+|-)(?: {QUOTED}(?: {QUOTED})?)?(.*)$')


def parse_apache(line):
    m = APACHE.match(line)
    if not m:
        return None
    ip, ident, user, stamp, request, status, size, referrer, agent, _rest = m.groups()
    ts, when = parse_time(stamp)
    request = request.replace('\\"', '"')
    method = path = protocol = None
    parts = request.split(" ")
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]{3,10}", parts[0]):
        method, path = parts[0], parts[1]
        if len(parts) >= 3:
            protocol = " ".join(parts[2:])
    fields = {
        "ip": ip, "ident": none_if_dash(ident), "user": none_if_dash(user),
        "method": method, "path": path, "protocol": protocol, "request": request,
        "status": to_int(status), "bytes": to_int(size) or 0,
        "referrer": none_if_dash(referrer), "agent": none_if_dash(agent),
    }
    return make_record("apache", line, ts, when, request, fields)


# =============================================================================
# JSON and CSV
# =============================================================================

TIME_KEYS = ["time", "timestamp", "@timestamp", "ts", "datetime", "date", "eventtime", "event_time",
             "timecreated", "timegenerated", "created_at", "date and time", "systemtime", "_time"]
MESSAGE_KEYS = ["message", "msg", "log", "description", "event", "text", "summary"]
IP_KEYS = ["ip", "src_ip", "source_ip", "sourceip", "src", "client_ip", "clientip", "remote_addr",
           "remote_ip", "ipaddress", "ip_address", "source.ip", "client.ip"]
USER_KEYS = ["user", "username", "user_name", "account", "accountname", "targetusername", "user.name"]


def lower_map(obj: dict) -> dict:
    return {str(k).lower(): k for k in obj}


def lookup(obj: dict, names, lowered=None):
    lowered = lowered if lowered is not None else lower_map(obj)
    for name in names:
        if name in lowered:
            return obj[lowered[name]]
        if "." in name:
            value = dotted(obj, name)
            if value is not None:
                return value
    return None


def dotted(obj, path):
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        low = lower_map(current)
        if part not in low:
            return None
        current = current[low[part]]
    return current


def plain(value):
    """JSON values as TB values (2.0 becomes 2)."""
    if isinstance(value, float):
        return _tb.tidy_number(value)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    return value


def object_record(obj: dict, raw: str, fmt: str):
    obj = plain(obj)
    low = lower_map(obj)
    time_value = None
    for key in TIME_KEYS:
        if key in low:
            time_value = obj[low[key]]
            break
    ts, when = parse_time(time_value)
    message = lookup(obj, MESSAGE_KEYS, low)
    if not isinstance(message, str):
        message = raw
    fields = dict(obj)
    if "time" in fields and fields["time"] is not None and not _tb.is_number(fields["time"]):
        fields["time_text"] = fields["time"]
    if "ip" not in low:
        ip = lookup(obj, IP_KEYS, low)
        if isinstance(ip, str):
            fields["ip"] = ip
    if "user" not in low:
        user = lookup(obj, USER_KEYS, low)
        if isinstance(user, str):
            fields["user"] = user
    if "success" not in low:
        outcome = lookup(obj, ["outcome", "result", "status", "event.outcome"], low)
        if isinstance(outcome, str) and outcome.lower() in ("failure", "failed", "fail", "denied"):
            fields["success"] = False
        elif isinstance(outcome, str) and outcome.lower() in ("success", "succeeded", "ok", "allowed"):
            fields["success"] = True
    return make_record(fmt, raw, ts, when, message, fields)


# =============================================================================
# Windows Security events
# =============================================================================

EVENTS = {
    # id: (action, description, success)
    1102: ("audit_log_cleared", "The audit log was cleared", None),
    104: ("log_cleared", "An event log was cleared", None),
    4608: ("windows_started", "Windows is starting up", None),
    4616: ("system_time_changed", "The system time was changed", None),
    4624: ("login", "An account was successfully logged on", True),
    4625: ("failed_login", "An account failed to log on", False),
    4634: ("logoff", "An account was logged off", None),
    4647: ("logoff", "User initiated logoff", None),
    4648: ("explicit_credentials", "A logon was attempted using explicit credentials", None),
    4656: ("object_handle_requested", "A handle to an object was requested", None),
    4663: ("object_access", "An attempt was made to access an object", None),
    4670: ("permissions_changed", "Permissions on an object were changed", None),
    4672: ("special_privileges", "Special privileges assigned to new logon", None),
    4673: ("privileged_service_called", "A privileged service was called", None),
    4688: ("process_created", "A new process has been created", None),
    4689: ("process_exited", "A process has exited", None),
    4697: ("service_installed", "A service was installed in the system", None),
    4698: ("scheduled_task_created", "A scheduled task was created", None),
    4699: ("scheduled_task_deleted", "A scheduled task was deleted", None),
    4700: ("scheduled_task_enabled", "A scheduled task was enabled", None),
    4702: ("scheduled_task_updated", "A scheduled task was updated", None),
    4719: ("audit_policy_changed", "System audit policy was changed", None),
    4720: ("user_created", "A user account was created", None),
    4722: ("user_enabled", "A user account was enabled", None),
    4723: ("password_change_attempt", "An attempt was made to change an account's password", None),
    4724: ("password_reset", "An attempt was made to reset an account's password", None),
    4725: ("user_disabled", "A user account was disabled", None),
    4726: ("user_deleted", "A user account was deleted", None),
    4728: ("group_member_added", "A member was added to a security-enabled global group", None),
    4729: ("group_member_removed", "A member was removed from a security-enabled global group", None),
    4732: ("group_member_added", "A member was added to a security-enabled local group", None),
    4733: ("group_member_removed", "A member was removed from a security-enabled local group", None),
    4738: ("user_changed", "A user account was changed", None),
    4740: ("account_locked", "A user account was locked out", None),
    4756: ("group_member_added", "A member was added to a security-enabled universal group", None),
    4757: ("group_member_removed", "A member was removed from a security-enabled universal group", None),
    4767: ("account_unlocked", "A user account was unlocked", None),
    4768: ("kerberos_tgt_requested", "A Kerberos authentication ticket (TGT) was requested", None),
    4769: ("kerberos_service_ticket", "A Kerberos service ticket was requested", None),
    4771: ("kerberos_preauth_failed", "Kerberos pre-authentication failed", False),
    4776: ("credential_validation", "The computer attempted to validate the credentials for an account", None),
    4778: ("session_reconnected", "A session was reconnected to a Window Station", None),
    4779: ("session_disconnected", "A session was disconnected from a Window Station", None),
    4798: ("group_membership_enumerated", "A user's local group membership was enumerated", None),
    4799: ("group_membership_enumerated", "A security-enabled local group membership was enumerated", None),
    5140: ("share_accessed", "A network share object was accessed", None),
    5145: ("share_access_checked", "A network share object was checked", None),
    5156: ("connection_allowed", "The Windows Filtering Platform has permitted a connection", None),
    5157: ("connection_blocked", "The Windows Filtering Platform has blocked a connection", None),
    7045: ("service_installed", "A new service was installed in the system", None),
}
SYSTEM_IDS = {104, 7045}  # EVENTS from the System channel; the rest are Security

LOGON_TYPES = {2: "interactive", 3: "network", 4: "batch", 5: "service", 7: "unlock",
               8: "network_cleartext", 9: "new_credentials", 10: "remote_interactive", 11: "cached_interactive"}

# Which part of the Message names the account the event is about.
TARGET_SECTIONS = ["account for which logon failed", "new logon", "account that was locked out", "new account",
                   "target account", "account whose credentials were used", "member", "logon account",
                   "account information"]
SUBJECT_SECTIONS = ["subject", "creator subject"]
SUBJECT_EVENTS = {4672, 4688, 4689, 4697, 4698, 4699, 4700, 4702, 4719, 1102, 4663, 4656, 4673, 4798, 4799}

GROUP_EVENTS = {4728, 4729, 4732, 4733, 4756, 4757}

# Get-WinEvent's Properties are positional. Index -> EventData name, per event.
PROPERTY_NAMES = {
    4624: {1: "SubjectUserName", 2: "SubjectDomainName", 5: "TargetUserName", 6: "TargetDomainName",
           8: "LogonType", 11: "WorkstationName", 18: "IpAddress", 19: "IpPort"},
    4625: {1: "SubjectUserName", 5: "TargetUserName", 6: "TargetDomainName", 7: "Status", 8: "FailureReason",
           9: "SubStatus", 10: "LogonType", 13: "WorkstationName", 19: "IpAddress", 20: "IpPort"},
    4672: {1: "SubjectUserName", 2: "SubjectDomainName", 4: "PrivilegeList"},
    4688: {1: "SubjectUserName", 2: "SubjectDomainName", 5: "NewProcessName", 8: "CommandLine",
           13: "ParentProcessName"},
    4720: {0: "TargetUserName", 1: "TargetDomainName", 4: "SubjectUserName", 5: "SubjectDomainName"},
    4740: {0: "TargetUserName", 1: "TargetDomainName", 4: "SubjectUserName"},
    4732: {0: "MemberName", 2: "TargetUserName", 3: "TargetDomainName", 6: "SubjectUserName"},
    4771: {0: "TargetUserName", 6: "IpAddress", 7: "IpPort", 4: "Status"},
}

MESSAGE_FIELDS = {
    # (section or "" for any, label) -> EventData-style name
    "security id": "Sid", "account name": "UserName", "account domain": "DomainName", "logon id": "LogonId",
    "logon type": "LogonType", "source network address": "IpAddress", "client address": "IpAddress",
    "source port": "IpPort", "client port": "IpPort", "workstation name": "WorkstationName",
    "failure reason": "FailureReason", "status": "Status", "sub status": "SubStatus",
    "new process name": "NewProcessName", "process command line": "CommandLine",
    "creator process name": "ParentProcessName", "process name": "ProcessName",
    "logon process": "LogonProcessName", "authentication package": "AuthenticationPackageName",
    "caller computer name": "CallerComputerName", "service name": "ServiceName",
    "service file name": "ImagePath", "task name": "TaskName", "group name": "GroupName",
    "privileges": "PrivilegeList", "share name": "ShareName", "object name": "ObjectName",
}


def parse_event_message(message: str):
    """Split a Windows event Message into (first line, {section: {label: value}})."""
    sections: dict = {"": {}}
    current = ""
    lines = message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = lines[0].strip() if lines else ""
    for raw_line in lines[1:]:
        if not raw_line.strip():
            continue
        indented = raw_line[:1] in ("\t", " ")
        text = raw_line.strip()
        if ":" not in text:
            continue
        label, _, value = text.partition(":")
        label, value = label.strip().lower(), value.strip()
        if not value and not indented:
            current = label
            sections.setdefault(current, {})
            continue
        target = sections[current] if indented else sections[""]
        if label not in target:
            target[label] = value
    return first, sections


def windows_record(obj: dict, raw: str):
    obj = plain(obj)
    low = lower_map(obj)
    event_id = lookup(obj, ["eventid", "id", "event id", "eventcode", "event_id", "instanceid"], low)
    if isinstance(event_id, dict):
        event_id = event_id.get("#text", event_id.get("value", event_id.get("Value")))
    event_id = to_int(event_id)
    if event_id is not None and event_id > 0xFFFF:  # Get-EventLog InstanceId
        event_id &= 0xFFFF
    time_value = lookup(obj, ["timecreated", "date and time", "timegenerated", "systemtime", "@timestamp",
                              "timestamp", "time", "utctime"], low)
    ts, when = parse_time(time_value)
    message = lookup(obj, ["message", "column6", "description"], low)
    message = message if isinstance(message, str) else ""
    first, sections = parse_event_message(message) if message else ("", {"": {}})

    provider = none_if_dash(lookup(obj, ["providername", "source", "provider", "sourcename"], low))
    channel = lookup(obj, ["channel", "logname"], low)
    sysmon = "sysmon" in f"{provider} {channel}".lower()
    # EVENTS only applies in its own channel (or with none given): elsewhere the same number means something else.
    home = "system" if event_id in SYSTEM_IDS else "security"
    known_id = None if sysmon or str(channel or home).lower() != home else event_id

    data: dict = {}
    for key in ("eventdata", "event_data", "data"):
        if key in low and isinstance(obj[low[key]], dict):
            data.update({k: v for k, v in obj[low[key]].items()})
    props = lookup(obj, ["properties"], low)
    if isinstance(props, list) and known_id in PROPERTY_NAMES:
        for index, name in PROPERTY_NAMES[known_id].items():
            if index < len(props) and name not in data:
                value = props[index]
                if isinstance(value, dict):
                    value = value.get("Value", value.get("value"))
                data[name] = value

    def from_sections(names, label):
        for name in names:
            section = sections.get(name)
            if section and none_if_dash(section.get(label)) is not None:
                return section.get(label)
        return None

    def any_section(label):
        for section in sections.values():
            value = none_if_dash(section.get(label))
            if value is not None:
                return value
        return None

    # Message text fills in what EventData didn't give.
    msg_target = from_sections(TARGET_SECTIONS, "account name")
    msg_subject = from_sections(SUBJECT_SECTIONS, "account name")
    data.setdefault("TargetUserName", msg_target)
    data.setdefault("TargetDomainName", from_sections(TARGET_SECTIONS, "account domain"))
    data.setdefault("SubjectUserName", msg_subject)
    data.setdefault("SubjectDomainName", from_sections(SUBJECT_SECTIONS, "account domain"))
    for label, name in MESSAGE_FIELDS.items():
        if name in ("UserName", "DomainName", "Sid"):
            continue
        if data.get(name) in (None, ""):
            value = any_section(label)
            if value is not None:
                data[name] = value
    if sysmon and message:
        # Sysmon's Message is "Name: value" lines with the EventData names.
        for text_line in message.replace("\r", "\n").split("\n")[1:]:
            name, sep, value = text_line.partition(":")
            if sep and re.fullmatch(r"[A-Za-z]+", name.strip()):
                data.setdefault(name.strip(), value.strip())
    data = {k: v for k, v in data.items() if v is not None}

    action, description, success = EVENTS.get(known_id, (None, None, None))
    if sysmon:
        action, description = SYSMON_EVENTS.get(event_id, (None, None))
        if ts is None and data.get("UtcTime"):
            ts, when = parse_time(utc_if_bare(data["UtcTime"]))
    group = none_if_dash(data.get("GroupName"))
    if known_id in GROUP_EVENTS:
        # In group events EventData's TargetUserName is the group; the member is MemberName.
        if group is None and "GroupName" not in data:
            group = none_if_dash(data.get("TargetUserName"))
        if msg_target is None:
            data["TargetUserName"] = data.get("MemberName")
    target = none_if_dash(data.get("TargetUserName"))
    subject = none_if_dash(data.get("SubjectUserName"))
    user = subject if (known_id in SUBJECT_EVENTS or not target) else target
    ip = none_if_dash(data.get("IpAddress"))
    if ip and ip.lower().startswith("::ffff:"):
        ip = ip[7:]
    status = none_if_dash(data.get("Status"))
    if known_id == 4776 and status:
        success = status.lower() in ("0x0", "0", "0x00000000")
    keywords = lookup(obj, ["keywordsdisplaynames", "keywords"], low)
    keyword_text = " ".join(keywords) if isinstance(keywords, list) else str(keywords or "")
    if success is None and "Audit Failure" in keyword_text:
        success = False
    elif success is None and known_id in (4624, 4648) and "Audit Success" in keyword_text:
        success = True
    logon_type = to_int(data.get("LogonType"))

    fields = {
        "event_id": event_id, "EventID": event_id, "action": action,
        "description": description or (first or None), "success": success,
        "user": user, "domain": none_if_dash(data.get("TargetDomainName") if user == target else data.get("SubjectDomainName")),
        "ip": ip, "port": to_int(data.get("IpPort")),
        "computer": none_if_dash(lookup(obj, ["machinename", "computer", "computername", "hostname"], low)),
        "provider": provider,
        "logon_type": logon_type, "logon_type_name": LOGON_TYPES.get(logon_type),
        "target_user": target, "subject_user": subject,
        "process": none_if_dash(data.get("NewProcessName") or data.get("ProcessName")),
        "command_line": none_if_dash(data.get("CommandLine")),
        "parent_process": none_if_dash(data.get("ParentProcessName")),
        "workstation": none_if_dash(data.get("WorkstationName")),
        "group": group,
        "failure_reason": none_if_dash(data.get("FailureReason")),
        "status": status, "sub_status": none_if_dash(data.get("SubStatus")),
    }
    if sysmon:
        sysmon_fields(data, fields)
    if fields["process"]:
        data.setdefault("Image", fields["process"])
    if fields["parent_process"]:
        data.setdefault("ParentImage", fields["parent_process"])
    for key, value in data.items():  # Sigma-style names: TargetUserName, IpAddress, CommandLine...
        if key not in fields:
            fields[key] = value
    fields["Message"] = message or None
    text = (description if sysmon else None) or first or description or (
        f"Event {event_id}" if event_id is not None else raw)
    return make_record("windows", raw, ts, when, text, fields)


# Sysmon event IDs: id -> (action, description). Each description starts with the
# event's name in the Sysinternals Sysmon reference, then says what it means.
# evtx builds its Sysmon descriptions from this table too.
SYSMON_EVENTS = {
    1: ("process_created", "Process creation: a process was started"),
    2: ("file_time_changed", "File creation time changed: a process changed a file's creation time"),
    3: ("network_connection", "Network connection: a process made a TCP/UDP connection"),
    4: ("sysmon_state_changed", "Sysmon service state changed: Sysmon started or stopped"),
    5: ("process_exited", "Process terminated: a process ended"),
    6: ("driver_loaded", "Driver loaded: a kernel driver was loaded"),
    7: ("image_loaded", "Image loaded: a process loaded a DLL or other module"),
    8: ("remote_thread_created", "CreateRemoteThread: a process started a thread in another process"),
    9: ("raw_disk_read", "RawAccessRead: a process read a drive directly"),
    10: ("process_accessed", "ProcessAccess: a process opened another process"),
    11: ("file_created", "FileCreate: a file was created or overwritten"),
    12: ("registry_object_changed",
         "RegistryEvent (Object create and delete): a registry key or value was created or deleted"),
    13: ("registry_value_set", "RegistryEvent (Value Set): a registry value was written"),
    14: ("registry_renamed", "RegistryEvent (Key and Value Rename): a registry key or value was renamed"),
    15: ("file_stream_created", "FileCreateStreamHash: a named file stream (alternate data stream) was created"),
    16: ("sysmon_config_changed", "ServiceConfigurationChange: the Sysmon configuration changed"),
    17: ("pipe_created", "PipeEvent (Pipe Created): a named pipe was created"),
    18: ("pipe_connected", "PipeEvent (Pipe Connected): a client connected to a named pipe"),
    19: ("wmi_filter", "WmiEvent (WmiEventFilter activity detected): a WMI event filter was registered or removed"),
    20: ("wmi_consumer",
         "WmiEvent (WmiEventConsumer activity detected): a WMI event consumer was registered or removed"),
    21: ("wmi_binding",
         "WmiEvent (WmiEventConsumerToFilter activity detected): a WMI consumer was bound to a filter or unbound"),
    22: ("dns_query", "DNSEvent (DNS query): a process ran a DNS query"),
    23: ("file_deleted", "FileDelete (File Delete archived): a file was deleted and a copy kept"),
    24: ("clipboard_changed", "ClipboardChange (New content in the clipboard)"),
    25: ("process_tampering",
         "ProcessTampering (Process image change): a process image was changed (hollowing or herpaderping)"),
    26: ("file_delete_logged", "FileDeleteDetected (File Delete logged): a file was deleted, no copy kept"),
    27: ("file_block_executable", "FileBlockExecutable: Sysmon blocked an executable file from being created"),
    28: ("file_block_shredding", "FileBlockShredding: Sysmon blocked a file from being shredded"),
    29: ("file_executable_detected", "FileExecutableDetected: a new executable file was created"),
    255: ("sysmon_error", "Error: Sysmon hit an error"),
}


def sysmon_fields(data, fields):
    """Sysmon EventData names -> the common record names."""
    def get(*names):
        for name in names:
            value = none_if_dash(data.get(name))
            if value is not None:
                return value
        return None

    account = get("User")
    domain, _, user = account.rpartition("\\") if account else ("", "", None)
    fields.update(user=user, domain=domain or None, target_user=None, subject_user=None)
    mapped = {
        "process": get("Image", "SourceImage"), "command_line": get("CommandLine"),
        "parent_process": get("ParentImage"), "parent_command_line": get("ParentCommandLine"),
        "pid": to_int(get("ProcessId", "SourceProcessId")), "target_process": get("TargetImage"),
        "ip": get("SourceIp"), "src_ip": get("SourceIp"), "src_port": to_int(get("SourcePort")),
        "dst_ip": get("DestinationIp"), "dst_port": to_int(get("DestinationPort")),
        "port": to_int(get("DestinationPort")), "proto": get("Protocol"),
        "src_host": get("SourceHostname"), "dst_host": get("DestinationHostname"),
        "query": get("QueryName"), "file": get("TargetFilename"), "registry_key": get("TargetObject"),
        "hashes": get("Hashes"),
    }
    fields.update({k: v for k, v in mapped.items() if v is not None})


def windows_like(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    low = lower_map(obj)
    if any(k in low for k in ("eventid", "event id", "eventcode", "event_id")):
        return True
    return "id" in low and any(k in low for k in ("timecreated", "providername", "logname", "keywordsdisplaynames"))


# =============================================================================
# Security products: Zeek, Suricata, CloudTrail, Okta, Microsoft 365, CEF, LEEF
# =============================================================================
# Each record keeps the product's own fields under their documented names and
# adds the common names the other formats use (user, ip, src_ip, dst_ip,
# dst_port, proto, action, success, ...). Where the two clash, the original wins.

def utc_if_bare(value):
    """ISO text with no zone, from a product that documents its times as UTC."""
    if isinstance(value, str):
        m = ISO_TIME.match(value.strip())
        if m and m.group(8) is None:
            return value.strip() + "Z"
    return value


def ip_or_none(value):
    """An IP address, without a ":port" or [brackets]; None for anything else
    (CloudTrail writes service names like "ec2.amazonaws.com" there)."""
    value = none_if_dash(value)
    if value is None:
        return None
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\](?::\d+)?|(\d{1,3}(?:\.\d{1,3}){3}):\d+", value)
    if m:
        value = m.group(1) or m.group(2)
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def outcome_of(value):
    """True / False / None from the many ways products write an outcome."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("success", "succeeded", "true", "allow", "allowed", "ok", "partiallysucceeded"):
        return True
    if text in ("failure", "failed", "false", "deny", "denied", "fail", "blocked", "rejected"):
        return False
    return None


def vendor_record(fmt, raw, obj, time_value, message, norm):
    """A record with the common names first, then the product's own fields."""
    ts, when = parse_time(time_value)
    if not isinstance(message, str) or not message:
        message = raw
    return make_record(fmt, raw, ts, when, message, {**norm, **obj})


def endpoints(src, sport, dst, dport):
    return {"ip": src, "src_ip": src, "src_port": sport, "dst_ip": dst, "dst_port": dport, "port": dport}


def flow_text(src, sport, dst, dport):
    if src is None and dst is None:
        return ""
    return f"{src}{'' if sport is None else ':' + str(sport)} -> {dst}{'' if dport is None else ':' + str(dport)}"


# -- Zeek (TSV with #fields headers, or JSON) ------------------------------------

ZEEK_NUMBERS = {"count", "int", "port", "double", "time", "interval"}


def zeek_value(text, kind, state):
    if text == state["unset"]:
        return None
    if "\\x" in text:
        text = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    kind = kind or "string"
    if kind.startswith(("set[", "vector[", "table[")):
        if text == state["empty"]:
            return []
        inner = kind[kind.index("[") + 1:-1]
        return [zeek_value(part, inner, state) for part in text.split(state["set_sep"])]
    if text == state["empty"]:
        return ""
    if kind == "bool":
        return text == "T"
    if kind in ZEEK_NUMBERS:
        try:
            return _tb.tidy_number(float(text)) if kind in ("double", "time", "interval") else int(text)
        except ValueError:
            return text
    return text


def zeek_header(line, state):
    """Read one '#name value' header line of a Zeek TSV log."""
    name, _, value = line[1:].partition(state["sep"] if not line.startswith("#separator") else " ")
    if name == "separator":
        try:
            state["sep"] = value.strip().encode().decode("unicode_escape") or "\t"
        except UnicodeDecodeError:  # a broken escape such as "\x": keep the default
            state["sep"] = "\t"
    elif name == "set_separator":
        state["set_sep"] = value or ","
    elif name == "empty_field":
        state["empty"] = value
    elif name == "unset_field":
        state["unset"] = value
    elif name == "path":
        state["path"] = value
    elif name == "fields":
        state["fields"] = value.split(state["sep"])
    elif name == "types":
        state["types"] = value.split(state["sep"])


def zeek_state():
    return {"sep": "\t", "set_sep": ",", "empty": "(empty)", "unset": "-", "path": None, "fields": None, "types": None}


def zeek_tsv_record(line, state):
    names = state["fields"]
    if not names:
        return None
    types = state["types"] or []
    values = line.split(state["sep"])
    obj = {name: zeek_value(values[i] if i < len(values) else state["unset"], types[i] if i < len(types) else None, state)
           for i, name in enumerate(names)}
    return zeek_record(obj, line, state["path"])


def zeek_record(obj, raw, path=None):
    obj = plain(obj)
    log = obj.get("_path") or path
    src, dst = lookup(obj, ["id.orig_h"]), lookup(obj, ["id.resp_h"])
    sport, dport = to_int(lookup(obj, ["id.orig_p"])), to_int(lookup(obj, ["id.resp_p"]))
    norm = {"log": log, "action": log, "user": obj.get("user") or obj.get("username"),
            **endpoints(src, sport, dst, dport), "proto": obj.get("proto"),
            "success": obj.get("auth_success") if isinstance(obj.get("auth_success"), bool) else None}
    if obj.get("query") is not None:
        norm["query"] = obj["query"]
    detail = obj.get("query") or " ".join(str(obj[k]) for k in ("method", "host", "uri") if obj.get(k)) or obj.get("service")
    message = " ".join(str(p) for p in (log or "zeek", flow_text(src, sport, dst, dport), detail) if p)
    return vendor_record("zeek", raw, obj, obj.get("ts"), message, norm)


# -- Suricata EVE JSON -----------------------------------------------------------

def suricata_record(obj, raw):
    obj = plain(obj)
    kind = obj.get("event_type")
    src, dst = obj.get("src_ip"), obj.get("dest_ip")
    sport, dport = to_int(obj.get("src_port")), to_int(obj.get("dest_port"))
    norm = {"action": kind, **endpoints(src, sport, dst, dport), "proto": obj.get("proto"), "user": None,
            "success": None}
    alert = obj.get("alert") if isinstance(obj.get("alert"), dict) else {}
    if alert:
        norm.update(rule=alert.get("signature"), rule_id=alert.get("signature_id"), category=alert.get("category"))
    dns = obj.get("dns") if isinstance(obj.get("dns"), dict) else {}
    queries = dns.get("queries") if isinstance(dns.get("queries"), list) else []  # EVE dns v3
    query = dns.get("rrname") or (queries[0].get("rrname") if queries and isinstance(queries[0], dict) else None)
    if query:
        norm["query"] = query
    http = obj.get("http") if isinstance(obj.get("http"), dict) else {}
    if http:
        norm.update(method=http.get("http_method"), path=http.get("url"), status=to_int(http.get("status")),
                    agent=http.get("http_user_agent"))
    if alert:
        message = alert.get("signature")
    elif query:
        message = f"{kind} {query}"
    elif http:
        message = f"{kind} {http.get('http_method') or ''} {http.get('hostname') or ''}{http.get('url') or ''}".strip()
    else:
        message = f"{kind} {flow_text(src, sport, dst, dport)}".strip()
    return vendor_record("suricata", raw, obj, obj.get("timestamp"), message, norm)


# -- AWS CloudTrail --------------------------------------------------------------

def cloudtrail_record(obj, raw):
    obj = plain(obj)
    who = obj.get("userIdentity") if isinstance(obj.get("userIdentity"), dict) else {}
    issuer = dotted(who, "sessioncontext.sessionissuer.username")
    arn = who.get("arn") or ""
    user = who.get("userName") or issuer or (re.split(r"[/:]", arn)[-1] if arn else None) or who.get("principalId") or who.get("invokedBy")
    error = obj.get("errorCode")
    success = error is None
    response = obj.get("responseElements")
    if isinstance(response, dict) and isinstance(response.get("ConsoleLogin"), str):
        success = response["ConsoleLogin"] == "Success"
    action, service = obj.get("eventName"), obj.get("eventSource")
    norm = {"user": user, "ip": ip_or_none(obj.get("sourceIPAddress")), "action": action, "success": success,
            "service": service, "region": obj.get("awsRegion"), "agent": obj.get("userAgent"), "error": error}
    message = f"{action} ({service})" + (f" failed: {error}" if error else "") + (" failed" if not success and not error else "")
    return vendor_record("cloudtrail", raw, obj, obj.get("eventTime"), message, norm)


# -- Okta System Log -------------------------------------------------------------

def okta_record(obj, raw):
    obj = plain(obj)
    outcome = obj.get("outcome") if isinstance(obj.get("outcome"), dict) else {}
    targets = obj.get("target") if isinstance(obj.get("target"), list) else []
    target_user = next((t.get("alternateId") for t in targets if isinstance(t, dict) and t.get("type") == "User"), None)
    norm = {"user": dotted(obj, "actor.alternateid"), "ip": ip_or_none(dotted(obj, "client.ipaddress")),
            "action": obj.get("eventType"), "success": outcome_of(outcome.get("result")),
            "reason": outcome.get("reason"), "target_user": target_user,
            "agent": dotted(obj, "client.useragent.rawuseragent")}
    return vendor_record("okta", raw, obj, obj.get("published"), obj.get("displayMessage"), norm)


# -- Microsoft 365 unified audit log ---------------------------------------------

def m365_record(obj, raw):
    obj = plain(obj)
    audit = obj.get("AuditData")
    if isinstance(audit, str):
        try:
            audit = _json.loads(audit)
        except ValueError:
            audit = None
    if isinstance(audit, dict):  # a search export row: its columns plus the AuditData JSON
        obj = {**{k: v for k, v in obj.items() if k != "AuditData"}, **plain(audit)}
    action = obj.get("Operation") or obj.get("Operations")
    user = obj.get("UserId") or obj.get("UserIds")
    norm = {"user": user, "ip": ip_or_none(obj.get("ClientIP") or obj.get("ClientIPAddress") or obj.get("ActorIpAddress")),
            "action": action, "success": outcome_of(obj.get("ResultStatus")), "service": obj.get("Workload"),
            "agent": obj.get("UserAgent"), "reason": obj.get("LogonError")}
    message = f"{action} ({obj['Workload']})" if obj.get("Workload") else action
    # CreationTime is UTC but written without a zone
    return vendor_record("m365", raw, obj, utc_if_bare(obj.get("CreationTime") or obj.get("CreationDate")), message, norm)


# -- CEF and LEEF (bare, or after a syslog header) -------------------------------

CEF_START = re.compile(r"(?:^|\s)(CEF:\s?\d+\|)")
LEEF_START = re.compile(r"(?:^|\s)(LEEF:[12]\.0\|)")
CEF_KEY = re.compile(r"(?:^|\s)([A-Za-z0-9_.\-\[\]]+)=")
CEF_TIME = re.compile(r"([A-Za-z]{3}) (\d{1,2}) (\d{4}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?: (\S+))?$")


def split_pipes(text, count):
    """Split CEF/LEEF headers on '|' (but not '\\|'), at most count times."""
    parts, current, i = [], [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "|\\":
            current.append(text[i + 1])
            i += 2
            continue
        i += 1
        if ch != "|":
            current.append(ch)
            continue
        parts.append("".join(current))
        current = []
        if len(parts) == count:  # the rest (extension / attributes) keeps its own escapes
            return parts + [text[i:]]
    parts.append("".join(current))
    return parts


def cef_extension(text):
    keys = list(CEF_KEY.finditer(text))
    out = {}
    for n, m in enumerate(keys):
        end = keys[n + 1].start() if n + 1 < len(keys) else len(text)
        value = text[m.end():end].strip()
        value = re.sub(r"\\(.)", lambda e: {"n": "\n", "r": "\r"}.get(e.group(1), e.group(1)), value)
        out[m.group(1)] = value
    return out


def device_time(value):
    """CEF rt/start/end and LEEF devTime: epoch milliseconds, ISO, or 'MMM dd yyyy HH:mm:ss'."""
    value = none_if_dash(value)
    if value is None:
        return None
    m = CEF_TIME.match(value)
    if m and m.group(1).lower() in MONTHS:
        mon, d, y, h, mi, s, frac, zone = m.groups()
        tz = _offset(zone) if zone and re.fullmatch(r"Z|GMT|UTC|[+-]\d{2}:?\d{2}", zone, re.I) else None
        return _make(int(y), MONTHS[mon.lower()], int(d), int(h), int(mi), int(s),
                     int((frac or "0")[:6].ljust(6, "0")), tz)
    return parse_datetime(value)


def syslog_prefix(line, start, years):
    """host, program and time from a syslog header in front of CEF/LEEF."""
    if not line[:start].strip():
        return {}, (None, None)
    record = parse_syslog(line, years)
    if record is None:
        return {}, (None, None)
    program = record.get("program")
    info = {"host": record.get("host"), "program": None if program in ("CEF", "LEEF") else program}
    for key in ("facility", "severity"):
        if key in record:
            info["syslog_" + key] = record[key]
    return info, (record.get("time"), record.get("when"))


def finish_device(fmt, line, obj, norm, when, header_time, message):
    ts, text = _pair(when) if when is not None else header_time
    return make_record(fmt, line, ts, text, message or line, {**norm, **obj})


def parse_cef(line, years):
    m = CEF_START.search(line)
    if not m:
        return None
    parts = split_pipes(line[m.start(1):], 7)
    if len(parts) < 8:
        return None
    version, vendor, product, product_version, event_id, name, severity, extension = parts
    head, header_ts = syslog_prefix(line, m.start(1), years)
    ext = cef_extension(extension)
    obj = {"cef_version": to_int(version.split(":", 1)[1]), "vendor": vendor, "product": product,
           "product_version": product_version, "event_id": event_id, "name": name,
           "severity": to_int(severity) if looks_numeric(severity) else severity, **ext}
    for key, value in list(ext.items()):  # cs1=... cs1Label=Rule -> "Rule"
        label = (ext.get(key + "Label") or "").strip('"')
        if label and label not in obj:
            obj[label] = value
    src, dst = none_if_dash(ext.get("src")), none_if_dash(ext.get("dst"))
    sport, dport = to_int(ext.get("spt")), to_int(ext.get("dpt"))
    norm = {**head, "host": ext.get("dvchost") or head.get("host"), "user": none_if_dash(ext.get("suser")) or none_if_dash(ext.get("duser")),
            "target_user": none_if_dash(ext.get("duser")), **endpoints(src, sport, dst, dport), "proto": ext.get("proto"),
            "action": ext.get("act") or name, "success": outcome_of(ext.get("outcome"))}
    when = device_time(ext.get("rt") or ext.get("end") or ext.get("start"))
    return finish_device("cef", line, obj, norm, when, header_ts, ext.get("msg") or name)


def parse_leef(line, years):
    m = LEEF_START.search(line)
    if not m:
        return None
    body = line[m.start(1):]
    version = body[5:8]
    parts = split_pipes(body, 6 if version == "2.0" else 5)
    if len(parts) < 6:
        return None
    delimiter, attrs = "\t", parts[5]
    if version == "2.0" and len(parts) == 7:
        mark = parts[5]
        if "=" in mark or len(mark) > 4:
            attrs = parts[5] + "|" + parts[6]
        else:
            attrs = parts[6]
            hexed = re.fullmatch(r"(?:0?x)([0-9A-Fa-f]{2,4})", mark)
            delimiter = chr(int(hexed.group(1), 16)) if hexed else (mark or "\t")
    obj = {"leef_version": version, "vendor": parts[1], "product": parts[2], "product_version": parts[3],
           "event_id": parts[4]}
    for item in attrs.split(delimiter):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            obj[key.strip()] = value.strip()
    head, header_ts = syslog_prefix(line, m.start(1), years)
    src, dst = none_if_dash(obj.get("src")), none_if_dash(obj.get("dst"))
    sport, dport = to_int(obj.get("srcPort")), to_int(obj.get("dstPort"))
    norm = {**head, "user": none_if_dash(obj.get("usrName")) or none_if_dash(obj.get("accountName")), **endpoints(src, sport, dst, dport),
            "proto": obj.get("proto"), "action": parts[4], "success": None}
    # shortcut: devTimeFormat isn't applied; epoch ms, ISO and "MMM dd yyyy HH:mm:ss" are read.
    when = device_time(obj.get("devTime"))
    message = f"{parts[1]} {parts[2]} {parts[4]}".strip()
    return finish_device("leef", line, obj, norm, when, header_ts, message)


JSON_READERS = {"zeek": zeek_record, "suricata": suricata_record, "cloudtrail": cloudtrail_record,
                "okta": okta_record, "m365": m365_record, "windows": windows_record}
JSON_FORMATS = {"json", *JSON_READERS}


# Zeek connection UIDs start with C, file IDs with F, then base62 text.
ZEEK_UID = re.compile(r"[CF][A-Za-z0-9]{15,21}")


def zeek_like(obj) -> bool:
    """Zeek's own field names, or a Zeek-shaped uid with an epoch ts (plain "uid" and "ts" are common elsewhere)."""
    if "id.orig_h" in obj or "_path" in obj or (isinstance(obj.get("id"), dict) and "orig_h" in obj["id"]):
        return True
    ts = obj.get("ts")
    return isinstance(ts, (int, float)) and not isinstance(ts, bool) and any(
        isinstance(obj.get(k), str) and ZEEK_UID.fullmatch(obj[k]) for k in ("uid", "fuid"))


def classify(obj) -> str:
    """Which product wrote this JSON object (by its documented field names)."""
    if not isinstance(obj, dict):
        return "json"
    if ("AuditData" in obj and ("Operations" in obj or "Operation" in obj)) or (
            "Operation" in obj and "Workload" in obj and "CreationTime" in obj):
        return "m365"
    if "eventSource" in obj and "eventName" in obj and "eventTime" in obj:
        return "cloudtrail"
    if "eventType" in obj and "published" in obj and ("actor" in obj or "uuid" in obj):
        return "okta"
    if "event_type" in obj and "timestamp" in obj and any(k in obj for k in ("flow_id", "src_ip", "dest_ip", "in_iface", "stats")):
        return "suricata"
    if zeek_like(obj):
        return "zeek"
    if windows_like(obj):
        return "windows"
    return "json"


def json_record(obj, raw, fmt):
    """One JSON object as a record of the given format ("auto" asks classify())."""
    if not isinstance(obj, dict):
        return make_record("json", raw, message=raw, fields={"value": plain(obj)})
    if fmt == "auto":
        fmt = classify(obj)
    reader = JSON_READERS.get(fmt)
    return reader(obj, raw) if reader else object_record(obj, raw, "json")


# =============================================================================
# Readers
# =============================================================================

def open_text(line, path, fn="logs.read"):
    """Open a log file as text, whatever its encoding (UTF-8, UTF-16 from
    PowerShell, or Windows code page bytes), streamed from disk; .gz and
    one-file .zip logs are unpacked as they are read."""
    f = _magic.open_stream(line, path, fn)
    head = f.peek(4)[:4]
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif head.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    elif len(head) >= 4 and head[1] == 0 and head[3] == 0:
        encoding = "utf-16-le"
    else:
        encoding = "utf-8"
    return io.TextIOWrapper(f, encoding=encoding, errors="replace", newline="")


def sniff_text(sample: str) -> str:
    """Guess the format from the start of a file."""
    stripped = sample.lstrip()
    if not stripped:
        return "text"
    lines = [ln for ln in sample.splitlines() if ln.strip()][:30]
    if stripped[:1] in ("[", "{"):
        objects = sample_objects(stripped, lines)
        kinds = [classify(o) for o in objects]
        vendor = [k for k in kinds if k not in ("json", "windows")]
        if vendor and len(vendor) * 2 >= len(kinds):
            return max(set(vendor), key=vendor.count)
        if any(k == "windows" for k in kinds):
            return "windows"
        if stripped.startswith("[") or not objects:
            return "windows" if re.search(r'"(?:Id|EventID|EventId|Event ID)"\s*:', stripped[:20000]) else "json"
        return "json"
    if not lines:
        return "text"
    if lines[0].startswith("#separator") or any(ln.startswith("#fields\t") for ln in lines[:10]):
        return "zeek"
    for fmt, start in (("cef", CEF_START), ("leef", LEEF_START)):
        if sum(1 for ln in lines if start.search(ln)) >= max(1, len(lines) * 0.6):
            return fmt
    apache = sum(1 for ln in lines if APACHE.match(ln))
    if apache >= max(1, len(lines) * 0.6):
        return "apache"
    syslog = [parse_syslog(ln, YearTracker(2000)) for ln in lines]
    hits = [r for r in syslog if r is not None]
    if len(hits) >= max(1, len(lines) * 0.6):
        if any((r.get("program") or "").split("/")[0] in AUTH_PROGRAMS or "pam_unix" in (r.get("message") or "")
               for r in hits):
            return "auth"
        return "syslog"
    if lines[0].startswith("#TYPE") and len(lines) > 1:
        lines = lines[1:]
    first = lines[0]
    if "," in first or "\t" in first:
        try:
            header = next(_csv.reader([first]))
        except _csv.Error:
            header = []
        names = {h.strip().lower() for h in header}
        if "auditdata" in names and names & {"operations", "operation"}:
            return "m365"
        if names & {"event id", "eventid", "id", "eventcode"} and names & {
                "date and time", "timecreated", "timegenerated", "level", "providername", "source", "message"}:
            return "windows"
        if len(header) >= 2 and all(h.strip() and not looks_numeric(h) for h in header):
            return "csv"
    return "text"


def sample_objects(stripped, lines, n=10):
    """The first few JSON objects of a sample (JSON lines, an array, or one
    document such as CloudTrail's {"Records": [...]}), even when cut short."""
    if stripped.startswith("{"):
        objects = []
        for ln in lines[:n]:
            try:
                objects.append(_json.loads(ln))
            except ValueError:
                break
        if objects and not (isinstance(objects[0], dict) and isinstance(objects[0].get("Records"), list)):
            return objects
        try:
            whole = _json.loads(stripped)
            inner = wrapper_of(whole)
            return inner[:n] if inner else [whole]
        except ValueError:
            m = re.match(r'\{\s*"[^"]+"\s*:\s*\[', stripped)
            if not m:
                return []
            stripped = stripped[m.end() - 1:]
    decoder = _json.JSONDecoder()
    objects, i = [], 1
    while len(objects) < n:
        while i < len(stripped) and stripped[i] in " \t\r\n,":
            i += 1
        try:
            obj, i = decoder.raw_decode(stripped, i)
        except ValueError:
            break
        objects.append(obj)
    return objects


def wrapper_of(obj):
    """The event list inside a {"Records": [...]}-style document, or None."""
    if isinstance(obj, dict):
        return next((v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
    return None


def is_document(stripped):
    """Whole-file JSON (an array, a pretty-printed object, or a one-line
    {"Records": [...]}) rather than one object per line."""
    if stripped[:1] == "[":
        return True
    if stripped[:1] != "{":
        return False
    first = stripped.split("\n", 1)[0]
    try:
        obj = _json.loads(first)
    except ValueError:
        return True
    return isinstance(obj, dict) and isinstance(obj.get("Records"), list)


def looks_numeric(text):
    return bool(re.fullmatch(r"\s*[-+]?\d+(\.\d+)?\s*", text))


class LineParser:
    """Turns one line into a record for the line-based formats."""

    def __init__(self, fmt, year=None):
        self.fmt = fmt
        self.years = YearTracker(year)
        self.zeek = zeek_state()

    def parse(self, line: str):
        line = line.rstrip("\r\n")
        if not line.strip():
            return None
        fmt = self.fmt
        if fmt == "auto":
            return self.auto(line)
        if fmt in ("syslog", "auth"):
            record = parse_syslog(line, self.years)
            if record is None:
                return text_record(line)
            return auth_fields(record) if fmt == "auth" else record
        if fmt in ("apache", "nginx"):
            return parse_apache(line) or text_record(line)
        if fmt == "zeek" and line.startswith("#"):
            zeek_header(line, self.zeek)
            return None
        if fmt in JSON_FORMATS and not (fmt == "zeek" and not line.lstrip().startswith("{")):
            try:
                obj = _json.loads(line)
            except ValueError:
                return text_record(line)
            if not isinstance(obj, dict):
                return make_record(fmt, line, message=line, fields={"value": plain(obj)})
            return json_record(obj, line, fmt)
        if fmt == "zeek":
            return zeek_tsv_record(line, self.zeek) or text_record(line)
        if fmt == "cef":
            return parse_cef(line, self.years) or text_record(line)
        if fmt == "leef":
            return parse_leef(line, self.years) or text_record(line)
        if fmt == "csv":
            try:
                row = next(_csv.reader([line]))
            except (_csv.Error, StopIteration):
                row = [line]
            return make_record("csv", line, message=line, fields={"fields": row})
        return text_record(line)

    def auto(self, line):
        text = line.lstrip()
        if text.startswith("{"):
            try:
                obj = _json.loads(text)
                if isinstance(obj, dict):
                    return json_record(obj, line, "auto")
            except ValueError:
                pass
        record = parse_cef(line, self.years) or parse_leef(line, self.years) or parse_apache(line)
        if record:
            return record
        record = parse_syslog(line, self.years)
        if record:
            program = (record.get("program") or "").split("/")[0]
            if program in AUTH_PROGRAMS or "pam_unix" in (record.get("message") or ""):
                return auth_fields(record)
            return record
        return text_record(line)


def check_format(line, fmt, fn):
    check_type(line, fmt, "text", f"logs.{fn}", "format")
    fmt = fmt.lower()
    if fmt not in FORMATS:
        fail(line, f"logs.{fn}() doesn't know the format \"{fmt}\". Formats: {', '.join(FORMATS)}.")
    if fmt == "evtx" and fn not in ("read", "each"):
        fail(line, f"logs.{fn}() works on text, but \"evtx\" logs are binary files. Use logs.read(path) or evtx.read(path).")
    return fmt


def iter_rows(handle, fmt):
    """CSV rows as records (also Event Viewer and PowerShell exports)."""
    reader = _csv.reader(handle)
    header = None
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        if header is None and row[0].startswith("#TYPE"):  # PowerShell 5 Export-Csv
            continue
        if header is None:
            header = [h.strip() or f"column{i + 1}" for i, h in enumerate(row)]
            continue
        obj = {}
        for i, value in enumerate(row):
            name = header[i] if i < len(header) else f"column{i + 1}"
            obj[name] = value
        raw = ",".join(row)
        if fmt in ("windows", "m365"):
            yield JSON_READERS[fmt](obj, raw)
        else:
            yield object_record(obj, raw, "csv")


def json_documents(text, line):
    """Every JSON value in the text: usually one, several when files were
    concatenated (zcat *.json.gz > all.json gives one {"Records": [...]} per line)."""
    decoder, out, i = _json.JSONDecoder(), [], 0
    while True:
        i = re.compile(r"\s*").match(text, i).end()
        if i >= len(text):
            return out
        try:
            value, i = decoder.raw_decode(text, i)
        except ValueError as err:
            fail(line, f"That log isn't valid JSON: {err}.")
        out.append(value)


def iter_json_document(text, fmt, line):
    for data in json_documents(text, line):
        if isinstance(data, dict):
            # {"Events": [...]} / {"records": [...]} wrappers, or one event
            inner = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
            data = inner if inner is not None else [data]
        if not isinstance(data, list):
            fail(line, "That JSON log should be a list of events (or one event per line).")
        for obj in data:
            raw = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            yield json_record(obj, raw, fmt)


def iter_text(text, fmt, line, year=None):
    """Records from text already in memory."""
    if fmt == "auto":
        fmt = sniff_text(text[:SNIFF_BYTES])
    stripped = text.lstrip()
    if fmt in JSON_FORMATS and is_document(stripped):
        yield from iter_json_document(text, fmt, line)
        return
    if fmt == "csv" or (fmt in ("windows", "m365") and stripped[:1] not in ("{", "[")):
        yield from iter_rows(io.StringIO(text), fmt)
        return
    parser = LineParser(fmt, year)
    for ln in text.splitlines():
        record = parser.parse(ln)
        if record is not None:
            yield record


def iter_file(path, fmt, line, year=None, fn="logs.read"):
    """Records from a file, read a line at a time where the format allows. Pulled one
    record at a time, so the reader never runs ahead of the consumer."""
    if fmt in ("auto", "evtx"):
        # Binary Windows event logs (.evtx, magic "ElfFile") go to the evtx library.
        with _magic.open_stream(line, path, fn) as fb:
            head = fb.peek(8)[:8]
        if head == b"ElfFile\x00" or fmt == "evtx":
            from tblib import evtx as _evtx
            yield from _evtx.iter_path(line, path, fn)
            return
    try:
        with open_text(line, path, fn) as f:
            sample = f.read(SNIFF_BYTES)
    except OSError as err:
        fail_os(line, err, path, "read")
    if fmt == "auto":
        fmt = sniff_text(sample)
    stripped = sample.lstrip()
    try:
        with open_text(line, path, fn) as f:
            if fmt in JSON_FORMATS and is_document(stripped):
                text = f.read(MAX_JSON_DOCUMENT + 1)
                if len(text) > MAX_JSON_DOCUMENT:
                    fail(line, f"That JSON document is over {MAX_JSON_DOCUMENT // 2**20} MB, too big to read "
                               "whole. Convert it to JSON lines (one event per line) and use logs.each().",
                         path=path)
                yield from iter_json_document(text, fmt, line)
                return
            if fmt == "csv" or (fmt in ("windows", "m365") and stripped[:1] not in ("{", "[")):
                yield from iter_rows((ln for ln, _cut in bounded_lines(f)), fmt)
                return
            parser = LineParser(fmt, year)
            for ln, cut in bounded_lines(f):
                record = parser.parse(ln)
                if record is not None:
                    if cut:
                        record["truncated"] = True
                    yield record
    except OSError as err:
        fail_os(line, err, path, "read")
    except _csv.Error as err:
        fail(line, f"That CSV log is broken: {err}.")


def bounded_lines(f):
    """(line, cut) pairs. A line longer than MAX_LINE keeps its first MAX_LINE characters (cut is
    true) and the rest is skipped, so one huge line can't fill memory."""
    while True:
        ln = f.readline(MAX_LINE)
        if not ln:
            return
        cut = len(ln) == MAX_LINE and not ln.endswith("\n")
        if cut:
            while True:  # skip to the end of this line
                rest = f.readline(MAX_LINE)
                if not rest or rest.endswith("\n"):
                    break
        yield ln, cut


def options_of(line, options, fn):
    if options is None:
        return {}
    check_type(line, options, "map", f"logs.{fn}", "options")
    result = {}
    if options.get("limit") is not None:
        result["limit"] = check_type(line, options["limit"], "whole", f"logs.{fn}", "the limit")
        if result["limit"] < 0:
            fail(line, f"logs.{fn}() needs a limit of 0 or more, but got {result['limit']}.")
    if options.get("year") is not None:
        result["year"] = check_type(line, options["year"], "whole", f"logs.{fn}", "the year")
    return result


def collect(line, records, limit=None):
    result = []
    cap = MAX_RECORDS if limit is None else min(limit, MAX_RECORDS)
    for record in records:
        if len(result) >= cap:
            if limit is None:
                fail(line, f"That log has more than {MAX_RECORDS:,} records; use logs.each() or a limit.")
            break
        result.append(record)
    return result


def load(api):
    m = Module(
        "logs",
        "Read log files into records (maps) you can search and count: syslog, Linux auth.log (SSH and sudo), "
        "Apache/nginx access logs, JSON lines, CSV, Windows Security and Sysmon events, Zeek, Suricata EVE, "
        "AWS CloudTrail, Okta, Microsoft 365 audit logs, CEF and LEEF. Every record has "
        "\"time\" (a UTC timestamp or nil), \"when\" (the time as the log wrote it), \"when_utc\" (the same "
        "instant as UTC text, safe to sort and compare across zones), \"zone\" (\"UTC\", \"+02:00\"), "
        "\"format\", \"message\" and \"raw\" "
        "(the original line), plus the fields of its format.",
    )
    m.value("formats", list(FORMATS), doc=(
        "The formats logs knows, as a list of names.\n"
        "\n"
        "Formats:\n"
        "- auto: guess from the start of the file (the default).\n"
        "- syslog, auth, apache, nginx, json, csv, text\n"
        "- windows: Windows event exports; Sysmon events get their own names too.\n"
        "- evtx: binary .evtx files, read by the evtx library.\n"
        "- zeek: Zeek logs, tab-separated with #fields headers, or JSON.\n"
        "- suricata: Suricata EVE JSON.\n"
        "- cloudtrail: AWS CloudTrail, {\"Records\": [...]} files (also several joined together) or one event per line.\n"
        "- okta: the Okta System Log (JSON).\n"
        "- m365: the Microsoft 365 unified audit log, as JSON or the CSV export with an AuditData column.\n"
        "- cef, leef: CEF and LEEF lines, bare or after a syslog header."))

    def path_arg(line, path, fn):
        check_type(line, path, "text", f"logs.{fn}", "path")
        if path == "":
            fail(line, f"logs.{fn}() needs a path, but got empty text.")
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        if not os.path.exists(path):
            fail(line, f"I couldn't find '{path}'. Is the name spelled right?", path=path)
        return path

    @m.fn("parse_line", 1, 2, doc=(
        "parse_line(line, format?) -> map\n"
        "Parse one log line into a record.\n"
        "A line that doesn't fit becomes a \"text\" record with just time, message and raw.\n"
        "\n"
        "Formats:\n"
        "- \"auto\": guess the format (the default).\n"
        "- \"syslog\", \"auth\", \"apache\", \"nginx\", \"json\"\n"
        "- \"windows\": one JSON event.\n"
        "- \"zeek\", \"suricata\", \"cloudtrail\", \"okta\", \"m365\": one JSON event from that product.\n"
        "- \"cef\", \"leef\": one CEF or LEEF line, with or without a syslog header.\n"
        "- \"csv\": gives {\"fields\": [...]}."))
    def parse_line(line, text, fmt="auto"):
        check_type(line, text, "text", "logs.parse_line", "line")
        fmt = check_format(line, fmt, "parse_line")
        record = LineParser(fmt).parse(text)
        return record if record is not None else make_record("text", text, message="")

    @m.fn("parse", 1, 3, doc=(
        "parse(text, format?, options?) -> list\n"
        "Parse a whole log that is already in a text value (handy in the browser playground). "
        "Same formats and options as read()."))
    def parse(line, text, fmt="auto", options=None):
        check_type(line, text, "text", "logs.parse", "text")
        fmt = check_format(line, fmt, "parse")
        opts = options_of(line, options, "parse")
        return collect(line, iter_text(text, fmt, line, opts.get("year")), opts.get("limit"))

    @m.fn("read", 1, 3, doc=(
        "read(path, format?, options?) -> list\n"
        "Read a log file into a list of records. Files are read a line at a time, so big logs are fine "
        "(a JSON array or a pretty-printed JSON file is read whole, up to 64 MB).\n"
        "A line over 4 MB keeps its first 4 MB and its record gets \"truncated\": true.\n"
        "\n"
        "Arguments:\n"
        "- format: the layout of the log (default: \"auto\").\n"
        "  \"auto\" looks at the start of the file and picks the format for you.\n"
        "  See logs.formats for the list.\n"
        "- options: a map of extra settings, listed below.\n"
        "\n"
        "Options:\n"
        "- limit: stop after this many records (0 or more).\n"
        "- year: the year for syslog lines, which have no year of their own.\n"
        "  Without it, the most recent year that isn't in the future is used.\n"
        "\n"
        "Returns:\n"
        "- time, when, when_utc, zone, format, message, raw: in every record.\n"
        "- user, ip, action, success: who, from where, what happened and whether it worked.\n"
        "  ip is the source address; action is the product's own event name.\n"
        "- src_ip, src_port, dst_ip, dst_port, port, proto: network fields (in network logs port is the destination port).\n"
        "- host: the machine or sensor that wrote the log; Windows events use computer.\n"
        "- process, command_line, parent_process, pid, query, file: from Sysmon, Zeek and Suricata.\n"
        "- service, region, agent, reason, target_user, rule, rule_id: from the cloud and IDS formats.\n"
        "- The product's own fields stay in the record under their documented names, "
        "like eventName, id.orig_h, alert or AuditData's fields.\n"
        "\n"
        "Example:\n"
        "    let records = logs.read(\"apps/data/auth.log\", \"auto\", {\"limit\": 4})\n"
        "    say(records[0])"))
    def read(line, path, fmt="auto", options=None):
        path = path_arg(line, path, "read")
        fmt = check_format(line, fmt, "read")
        opts = options_of(line, options, "read")
        return collect(line, iter_file(path, fmt, line, opts.get("year")), opts.get("limit"))

    @m.fn("each", 2, 4, doc=(
        "each(path, fn, format?, options?) -> number\n"
        "Call fn(record) for every record in a log file without keeping them all in memory (for huge logs). "
        "The file is streamed from disk a line at a time, and .gz and one-file .zip logs are unpacked as they "
        "are read. Gives back how many records there were. If fn gives back false, reading stops early. "
        "Lines over 4 MB are cut (the record gets \"truncated\": true), so memory stays flat."))
    def each(line, path, fn, fmt="auto", options=None):
        path = path_arg(line, path, "each")
        check_type(line, fn, "function", "logs.each", "fn")
        fmt = check_format(line, fmt, "each")
        opts = options_of(line, options, "each")
        count = 0
        for record in iter_file(path, fmt, line, opts.get("year"), "logs.each"):
            count += 1
            if api.call(fn, [record], line) is False:
                break
            if opts.get("limit") is not None and count >= opts["limit"]:
                break
        return count

    @m.fn("stream", 1, 3, doc=(
        "stream(path, format?, options?) -> generator\n"
        "A generator over the records of a log file, for for loops and .filter(), .map(), .take().\n"
        "It reads the next record only when the loop asks for it, so it never runs ahead of your code "
        "and memory stays flat however big the file is. Same formats and options as read(); "
        ".gz and one-file .zip logs are unpacked as they are read.\n"
        "\n"
        "Example:\n"
        "    for r in logs.stream(\"apps/data/auth.log\").take(2) {\n"
        "      say(r.format)\n"
        "    }"))
    def stream(line, path, fmt="auto", options=None):
        from tbcore.runtime import TBIterator
        path = path_arg(line, path, "stream")
        fmt = check_format(line, fmt, "stream")
        opts = options_of(line, options, "stream")
        records = iter_file(path, fmt, line, opts.get("year"), "logs.stream")
        if opts.get("limit") is not None:
            records = itertools.islice(records, opts["limit"])
        return TBIterator(records)

    @m.fn("sniff", 1, doc=(
        "sniff(path) -> text\n"
        "Guess a log file's format from its first lines, as one of the names in logs.formats "
        "(\"auth\", \"zeek\", \"cloudtrail\", ... or \"text\"). This is what \"auto\" uses."))
    def sniff(line, path):
        path = path_arg(line, path, "sniff")
        try:
            with open_text(line, path, "logs.sniff") as f:
                return sniff_text(f.read(SNIFF_BYTES))
        except OSError as err:
            fail_os(line, err, path, "read")

    @m.fn("parse_time", 1, 2, doc=(
        "parse_time(text, year?) -> number or nil\n"
        "Turn a time written in a log into a timestamp. Gives back nil if the text isn't a time.\n"
        "\n"
        "Formats it reads:\n"
        "- ISO 8601, like 2024-03-10T09:15:02Z\n"
        "- syslog, like Mar 10 09:15:02\n"
        "- Apache, like 10/Mar/2024:09:15:02 +0000\n"
        "- Windows, like 3/10/2024 9:15:02 AM or /Date(1710062102000)/\n"
        "- Unix seconds or milliseconds.\n"
        "\n"
        "Times without a time zone are taken as this computer's local time."))
    def parse_time_fn(line, value, year=None):
        check_type(line, value, "text|number", "logs.parse_time", "text")
        if year is not None:
            year = check_type(line, year, "whole", "logs.parse_time", "year")
        return parse_time(value, year)[0]

    @m.fn("top", 2, 3, doc=(
        "top(records, field, n?) -> list\n"
        "The most common values of a field, as [{\"value\", \"count\"}, ...] from most to least (default top 10). "
        "Records without the field are skipped. top(records, \"ip\") shows who talks the most."))
    def top(line, records, field, n=10):
        check_type(line, records, "list", "logs.top", "records")
        check_type(line, field, "text", "logs.top", "field")
        n = check_type(line, n, "whole", "logs.top", "n")
        counts: dict = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get(field)
            if value is None or isinstance(value, (list, dict)):
                continue
            key = (type(value).__name__ if isinstance(value, bool) else "v", value)
            repeat = record.get("repeat")
            counts[key] = counts.get(key, 0) + (repeat if isinstance(repeat, int) and repeat > 0 else 1)
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:max(n, 0)]
        return [{"value": key[1], "count": count} for key, count in ranked]

    return m
