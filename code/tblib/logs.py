"""logs: read and parse log files (syslog, auth.log, web servers, JSON, CSV, Windows)."""

from __future__ import annotations

import csv as _csv
import datetime as _dt
import io
import json as _json
import os
import re

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

FORMATS = ["auto", "syslog", "auth", "apache", "nginx", "json", "csv", "windows", "text", "evtx"]
MAX_RECORDS = 5_000_000
SNIFF_BYTES = 64 * 1024
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

    data: dict = {}
    for key in ("eventdata", "event_data", "data"):
        if key in low and isinstance(obj[low[key]], dict):
            data.update({k: v for k, v in obj[low[key]].items()})
    props = lookup(obj, ["properties"], low)
    if isinstance(props, list) and event_id in PROPERTY_NAMES:
        for index, name in PROPERTY_NAMES[event_id].items():
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
    data = {k: v for k, v in data.items() if v is not None}

    action, description, success = EVENTS.get(event_id, (None, None, None))
    group = none_if_dash(data.get("GroupName"))
    if event_id in GROUP_EVENTS:
        # In group events EventData's TargetUserName is the group; the member is MemberName.
        if group is None and "GroupName" not in data:
            group = none_if_dash(data.get("TargetUserName"))
        if msg_target is None:
            data["TargetUserName"] = data.get("MemberName")
    target = none_if_dash(data.get("TargetUserName"))
    subject = none_if_dash(data.get("SubjectUserName"))
    user = subject if (event_id in SUBJECT_EVENTS or not target) else target
    ip = none_if_dash(data.get("IpAddress"))
    if ip and ip.lower().startswith("::ffff:"):
        ip = ip[7:]
    status = none_if_dash(data.get("Status"))
    if event_id == 4776 and status:
        success = status.lower() in ("0x0", "0", "0x00000000")
    keywords = lookup(obj, ["keywordsdisplaynames", "keywords"], low)
    keyword_text = " ".join(keywords) if isinstance(keywords, list) else str(keywords or "")
    if success is None and "Audit Failure" in keyword_text:
        success = False
    elif success is None and event_id in (4624, 4648) and "Audit Success" in keyword_text:
        success = True
    logon_type = to_int(data.get("LogonType"))

    fields = {
        "event_id": event_id, "EventID": event_id, "action": action,
        "description": description or (first or None), "success": success,
        "user": user, "domain": none_if_dash(data.get("TargetDomainName") if user == target else data.get("SubjectDomainName")),
        "ip": ip, "port": to_int(data.get("IpPort")),
        "computer": none_if_dash(lookup(obj, ["machinename", "computer", "computername", "hostname"], low)),
        "provider": none_if_dash(lookup(obj, ["providername", "source", "provider", "sourcename"], low)),
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
    if fields["process"]:
        data.setdefault("Image", fields["process"])
    if fields["parent_process"]:
        data.setdefault("ParentImage", fields["parent_process"])
    for key, value in data.items():  # Sigma-style names: TargetUserName, IpAddress, CommandLine...
        if key not in fields:
            fields[key] = value
    fields["Message"] = message or None
    text = first or description or (f"Event {event_id}" if event_id is not None else raw)
    return make_record("windows", raw, ts, when, text, fields)


def windows_like(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    low = lower_map(obj)
    if any(k in low for k in ("eventid", "event id", "eventcode", "event_id")):
        return True
    return "id" in low and any(k in low for k in ("timecreated", "providername", "logname", "keywordsdisplaynames"))


# =============================================================================
# Readers
# =============================================================================

def open_text(path):
    """Open a log file as text, whatever its encoding (UTF-8, UTF-16 from
    PowerShell, or Windows code page bytes)."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif head.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    elif len(head) >= 4 and head[1] == 0 and head[3] == 0:
        encoding = "utf-16-le"
    else:
        encoding = "utf-8"
    return open(path, encoding=encoding, errors="replace", newline="")


def sniff_text(sample: str) -> str:
    """Guess the format from the start of a file."""
    stripped = sample.lstrip()
    if not stripped:
        return "text"
    lines = [ln for ln in sample.splitlines() if ln.strip()][:30]
    if stripped.startswith("["):
        return "windows" if re.search(r'"(?:Id|EventID|EventId|Event ID)"\s*:', stripped[:20000]) else "json"
    if stripped.startswith("{"):
        objects = []
        for ln in lines[:10]:
            try:
                objects.append(_json.loads(ln))
            except ValueError:
                break
        if not objects:  # one pretty-printed object
            try:
                objects = [_json.loads(stripped)]
            except ValueError:
                return "windows" if re.search(r'"(?:EventID|Id)"\s*:', stripped[:20000]) else "json"
        return "windows" if any(windows_like(o) for o in objects) else "json"
    if not lines:
        return "text"
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
        if names & {"event id", "eventid", "id", "eventcode"} and names & {
                "date and time", "timecreated", "timegenerated", "level", "providername", "source", "message"}:
            return "windows"
        if len(header) >= 2 and all(h.strip() and not looks_numeric(h) for h in header):
            return "csv"
    return "text"


def looks_numeric(text):
    return bool(re.fullmatch(r"\s*[-+]?\d+(\.\d+)?\s*", text))


class LineParser:
    """Turns one line into a record for the line-based formats."""

    def __init__(self, fmt, year=None):
        self.fmt = fmt
        self.years = YearTracker(year)

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
        if fmt in ("json", "windows"):
            try:
                obj = _json.loads(line)
            except ValueError:
                return text_record(line)
            if not isinstance(obj, dict):
                return make_record(fmt, line, message=line, fields={"value": plain(obj)})
            return windows_record(obj, line) if fmt == "windows" else object_record(obj, line, "json")
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
                    return windows_record(obj, line) if windows_like(obj) else object_record(obj, line, "json")
            except ValueError:
                pass
        record = parse_apache(line)
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
        yield windows_record(obj, raw) if fmt == "windows" else object_record(obj, raw, "csv")


def iter_json_document(text, fmt, line):
    try:
        data = _json.loads(text)
    except ValueError as err:
        fail(line, f"That log isn't valid JSON: {err}.")
    if isinstance(data, dict):
        # {"Events": [...]} / {"records": [...]} wrappers, or one event
        inner = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
        data = inner if inner is not None else [data]
    if not isinstance(data, list):
        fail(line, "That JSON log should be a list of events (or one event per line).")
    for obj in data:
        raw = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if not isinstance(obj, dict):
            yield make_record("json", raw, message=raw, fields={"value": plain(obj)})
        elif fmt == "windows" or (fmt == "auto" and windows_like(obj)):
            yield windows_record(obj, raw)
        else:
            yield object_record(obj, raw, "json")


def iter_text(text, fmt, line, year=None):
    """Records from text already in memory."""
    if fmt == "auto":
        fmt = sniff_text(text[:SNIFF_BYTES])
    stripped = text.lstrip()
    if fmt in ("json", "windows") and stripped[:1] == "[" or (
            fmt in ("json", "windows") and stripped[:1] == "{" and not first_line_is_json(stripped)):
        yield from iter_json_document(text, fmt, line)
        return
    if fmt == "csv" or (fmt == "windows" and stripped[:1] not in ("{", "[")):
        yield from iter_rows(io.StringIO(text), fmt)
        return
    parser = LineParser(fmt, year)
    for ln in text.splitlines():
        record = parser.parse(ln)
        if record is not None:
            yield record


def first_line_is_json(text):
    first = text.split("\n", 1)[0]
    try:
        _json.loads(first)
        return True
    except ValueError:
        return False


def iter_file(path, fmt, line, year=None):
    """Records from a file, read a line at a time where the format allows."""
    if fmt in ("auto", "evtx"):
        # Binary Windows event logs (.evtx, magic "ElfFile") go to the evtx library.
        try:
            with open(path, "rb") as fb:
                head = fb.read(8)
        except OSError as err:
            fail_os(line, err, path, "read")
        if head == b"ElfFile\x00" or fmt == "evtx":
            from tblib import evtx as _evtx
            yield from _evtx.iter_path(line, path, "logs.read")
            return
    try:
        with open_text(path) as f:
            sample = f.read(SNIFF_BYTES)
    except OSError as err:
        fail_os(line, err, path, "read")
    if fmt == "auto":
        fmt = sniff_text(sample)
    stripped = sample.lstrip()
    try:
        with open_text(path) as f:
            if fmt in ("json", "windows") and (stripped[:1] == "[" or (
                    stripped[:1] == "{" and not first_line_is_json(stripped))):
                yield from iter_json_document(f.read(), fmt, line)
                return
            if fmt == "csv" or (fmt == "windows" and stripped[:1] not in ("{", "[")):
                yield from iter_rows(f, fmt)
                return
            parser = LineParser(fmt, year)
            for ln in f:
                record = parser.parse(ln)
                if record is not None:
                    yield record
    except OSError as err:
        fail_os(line, err, path, "read")
    except _csv.Error as err:
        fail(line, f"That CSV log is broken: {err}.")


def options_of(line, options, fn):
    if options is None:
        return {}
    check_type(line, options, "map", f"logs.{fn}", "options")
    result = {}
    if options.get("limit") is not None:
        result["limit"] = check_type(line, options["limit"], "whole", f"logs.{fn}", "the limit")
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
        "Apache/nginx access logs, JSON lines, CSV and Windows Security event exports. Every record has "
        "\"time\" (a UTC timestamp or nil), \"when\" (the time as the log wrote it), \"when_utc\" (the same "
        "instant as UTC text, safe to sort and compare across zones), \"zone\" (\"UTC\", \"+02:00\"), "
        "\"format\", \"message\" and \"raw\" "
        "(the original line), plus the fields of its format.",
    )
    m.value("formats", list(FORMATS), doc="The formats logs knows: auto, syslog, auth, apache, nginx, json, csv, windows, text, evtx (binary .evtx files, read by the evtx library).")

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
        "Read a log file into a list of records. Files are read a line at a time, so big logs are fine.\n"
        "\n"
        "Arguments:\n"
        "- format: the layout of the log (default: \"auto\").\n"
        "  \"auto\" looks at the start of the file and picks the format for you.\n"
        "- options: a map of extra settings, listed below.\n"
        "\n"
        "Options:\n"
        "- limit: stop after this many records.\n"
        "- year: the year for syslog lines, which have no year of their own.\n"
        "  Without it, the most recent year that isn't in the future is used.\n"
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
        "Gives back how many records there were. If fn gives back false, reading stops early."))
    def each(line, path, fn, fmt="auto", options=None):
        path = path_arg(line, path, "each")
        check_type(line, fn, "function", "logs.each", "fn")
        fmt = check_format(line, fmt, "each")
        opts = options_of(line, options, "each")
        count = 0
        for record in iter_file(path, fmt, line, opts.get("year")):
            count += 1
            if api.call(fn, [record], line) is False:
                break
            if opts.get("limit") is not None and count >= opts["limit"]:
                break
        return count

    @m.fn("sniff", 1, doc=(
        "sniff(path) -> text\n"
        "Guess a log file's format from its first lines: \"auth\", \"syslog\", \"apache\", \"json\", \"csv\", "
        "\"windows\" or \"text\". This is what \"auto\" uses."))
    def sniff(line, path):
        path = path_arg(line, path, "sniff")
        try:
            with open_text(path) as f:
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
