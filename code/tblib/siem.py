"""siem: format events as CEF, LEEF and syslog, and forward them to Splunk, Elasticsearch or Sentinel."""

from __future__ import annotations

import base64 as _b64
import datetime as _dt
import hashlib
import hmac as _hmac
import json as _json
import socket
import ssl
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from tblib._api import Module, check_type, fail

import tb as _tb

VENDOR, PRODUCT = "TB", "detect"
SEVERITY = {"informational": 2, "low": 3, "medium": 5, "high": 8, "critical": 10}
SYSLOG_SEVERITY = {"informational": 6, "low": 5, "medium": 4, "high": 3, "critical": 2}
FACILITIES = {"kern": 0, "user": 1, "mail": 2, "daemon": 3, "auth": 4, "syslog": 5, "lpr": 6, "news": 7, "uucp": 8,
              "cron": 9, "authpriv": 10, "ftp": 11, "local0": 16, "local1": 17, "local2": 18, "local3": 19,
              "local4": 20, "local5": 21, "local6": 22, "local7": 23}
# TB / logs-library field names -> CEF extension keys
CEF_KEYS = {"ip": "src", "src_ip": "src", "source_ip": "src", "dst_ip": "dst", "dest_ip": "dst", "src_port": "spt",
            "dst_port": "dpt", "dest_port": "dpt", "user": "suser", "target_user": "duser", "message": "msg",
            "host": "dvchost", "hostname": "dvchost", "process": "sproc", "pid": "spid", "path": "request",
            "url": "request", "method": "requestMethod", "agent": "requestClientApplication", "status": "outcome",
            "bytes": "in", "count": "cnt", "action": "act", "protocol": "proto", "mac": "smac", "file": "fname",
            "filename": "fname", "hash": "fileHash", "sha256": "fileHash", "md5": "fileHash", "command": "cs1",
            "command_line": "cs1", "description": "reason", "category": "cat"}
LEEF_KEYS = {"ip": "src", "src_ip": "src", "source_ip": "src", "dst_ip": "dst", "dest_ip": "dst", "src_port": "srcPort",
             "dst_port": "dstPort", "dest_port": "dstPort", "user": "usrName", "message": "msg", "host": "identHostName",
             "protocol": "proto", "mac": "srcMAC", "url": "url", "path": "url", "action": "action", "count": "cnt",
             "level": "sev", "description": "reason", "category": "cat"}
SKIP_FIELDS = {"raw", "records", "rule", "level", "name", "vendor", "product", "version", "id", "signature_id",
               "severity", "time", "first", "last", "first_when", "last_when", "by_field", "format", "tags", "when",
               "when_utc", "zone"}
TIMEOUT = 20


class SiemError(Exception):
    pass


# =============================================================================
# Formatting
# =============================================================================

def tb_version() -> str:
    return getattr(_tb, "__version__", "3.0.0")


def flatten(value):
    """Extension values must be text: nested maps and lists become JSON."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _tb.to_text(value)
    if isinstance(value, str):
        return value
    try:
        from tblib.json import to_plain
        return _json.dumps(to_plain(value, None), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return _tb.to_text(value)


def normalise_event(event: dict) -> dict:
    """An alert from detect.run() (or any map) -> the header fields CEF/LEEF need
    plus the rest as extension fields."""
    is_alert = "rule" in event and "level" in event
    name = event.get("name") or event.get("rule") or event.get("message") or "event"
    level = str(event.get("level") or "").lower()
    severity = event.get("severity")
    if severity is None:
        severity = SEVERITY.get(level, 5)
    header = {
        "vendor": event.get("vendor") or VENDOR,
        "product": event.get("product") or PRODUCT,
        "version": _tb.to_text(event.get("version") or tb_version()),
        "id": _tb.to_text(event.get("id") or event.get("signature_id") or slug(name)),
        "name": _tb.to_text(name),
        "severity": severity,
        "level": level or None,
    }
    ext = {}
    when = event.get("time")
    if when is None:
        when = event.get("first") if is_alert else None
    if _tb.is_number(when):
        ext["time"] = when
    for key, value in event.items():
        if key in SKIP_FIELDS or value is None or value == "" or key in ("time",):
            continue
        if key == "by" and is_alert:
            by_field = event.get("by_field") or "by"
            ext[by_field if by_field not in ext else "by"] = value
            continue
        ext[key] = value
    if is_alert and event.get("records"):
        first = event["records"][0]
        if isinstance(first, dict):
            for key in ("ip", "src_ip", "dst_ip", "user", "host", "message", "path", "command_line", "dst_port"):
                if key in first and first[key] not in (None, "") and key not in ext:
                    ext[key] = first[key]
    if is_alert:
        ext.setdefault("count", event.get("count"))
        if event.get("tags"):
            ext["tags"] = ",".join(_tb.to_text(t) for t in event["tags"])
    return {"header": header, "extensions": ext}


def slug(text) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in _tb.to_text(text).strip().lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "event"


def cef_header_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def cef_ext_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("=", "\\=").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def to_cef(event: dict, options=None) -> str:
    options = options or {}
    n = normalise_event(event)
    h = n["header"]
    severity = max(0, min(10, int(round(h["severity"])))) if _tb.is_number(h["severity"]) else 5
    head = "|".join(cef_header_escape(_tb.to_text(h[k])) for k in ("vendor", "product", "version", "id", "name"))
    parts = []
    used = set()
    keymap = dict(CEF_KEYS)
    keymap.update({str(k): str(v) for k, v in (options.get("keys") or {}).items()})
    custom = 0
    for key, value in n["extensions"].items():
        if key == "time":
            parts.append(f"rt={int(value * 1000)}")
            continue
        cef_key = keymap.get(key)
        if cef_key is None:
            if key.isidentifier() and len(key) <= 23 and options.get("keep_names", True):
                cef_key = key
            else:
                custom += 1
                if custom > 6:
                    continue
                parts.append(f"cs{custom}Label={cef_ext_escape(key)}")
                cef_key = f"cs{custom}"
        if cef_key in used:
            cef_key = key
        used.add(cef_key)
        parts.append(f"{cef_key}={cef_ext_escape(flatten(value))}")
    if h.get("level") and "level" not in used:
        parts.append(f"cat={cef_ext_escape(h['level'])}")
    return f"CEF:0|{head}|{severity}|" + " ".join(parts)


def to_leef(event: dict, options=None) -> str:
    options = options or {}
    n = normalise_event(event)
    h = n["header"]
    delimiter = options.get("delimiter", "\t")
    head = "|".join(cef_header_escape(_tb.to_text(h[k])) for k in ("vendor", "product", "version", "id"))
    keymap = dict(LEEF_KEYS)
    keymap.update({str(k): str(v) for k, v in (options.get("keys") or {}).items()})
    parts = [f"sev={max(1, min(10, int(round(h['severity'])))) if _tb.is_number(h['severity']) else 5}"]
    if h.get("level"):
        parts.append(f"cat={h['level']}")
    parts.append(f"eventName={flatten(h['name']).replace(delimiter, ' ')}")
    for key, value in n["extensions"].items():
        if key == "time":
            parts.append("devTime=" + _dt.datetime.fromtimestamp(value).strftime("%b %d %Y %H:%M:%S"))
            parts.append("devTimeFormat=MMM dd yyyy HH:mm:ss")
            continue
        leef_key = keymap.get(key, key)
        text = flatten(value).replace(delimiter, " ").replace("\n", " ")
        parts.append(f"{leef_key}={text}")
    delim_field = "" if delimiter == "\t" else f"{delimiter}|"
    return f"LEEF:2.0|{head}|{delim_field}" + delimiter.join(parts)


def rfc3339(ts) -> str:
    dt = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def sd_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def to_syslog(message, options=None) -> str:
    """RFC 5424 (default) or RFC 3164 line. message is text or an event / alert map."""
    options = options or {}
    fmt = str(options.get("format", "rfc5424")).lower().replace("-", "").replace(" ", "")
    facility = options.get("facility", "user")
    if isinstance(facility, str):
        if facility.lower() not in FACILITIES:
            raise SiemError(f"the facility \"{facility}\" isn't one of {', '.join(FACILITIES)}")
        facility = FACILITIES[facility.lower()]
    host = _tb.to_text(options.get("host") or socket.gethostname() or "-")
    app = _tb.to_text(options.get("app") or "tb")
    ts = options.get("time")
    sd = ""
    level = None
    if isinstance(message, dict):
        n = normalise_event(message)
        level = n["header"].get("level")
        ts = ts if ts is not None else n["extensions"].get("time")
        text = n["header"]["name"]
        if message.get("message") and message.get("message") != text:
            text = f"{text}: {message['message']}"
        if message.get("description"):
            text = f"{text} - {message['description']}"
        params = []
        for key, value in n["extensions"].items():
            if key in ("time", "message", "description"):
                continue
            params.append(f'{key[:32]}="{sd_escape(flatten(value))[:500]}"')
        if level:
            params.insert(0, f'level="{level}"')
        if params:
            sd = "[tb@32473 " + " ".join(params) + "]"
    else:
        text = _tb.to_text(message)
    severity = options.get("severity")
    if severity is None:
        severity = SYSLOG_SEVERITY.get(level or "", 6)
    elif isinstance(severity, str):
        names = {"emerg": 0, "alert": 1, "crit": 2, "err": 3, "error": 3, "warning": 4, "warn": 4, "notice": 5,
                 "info": 6, "debug": 7}
        if severity.lower() not in names:
            raise SiemError(f"the severity \"{severity}\" isn't one of {', '.join(names)}")
        severity = names[severity.lower()]
    pri = int(facility) * 8 + int(severity)
    ts = ts if _tb.is_number(ts) else _time.time()
    text = text.replace("\r", " ").replace("\n", " ")
    if fmt == "rfc3164":
        stamp = _dt.datetime.fromtimestamp(ts).strftime("%b %d %H:%M:%S").replace(" 0", "  ", 1) if _dt.datetime.fromtimestamp(ts).day < 10 else _dt.datetime.fromtimestamp(ts).strftime("%b %d %H:%M:%S")
        return f"<{pri}>{stamp} {host} {app}: {text}"
    if fmt != "rfc5424":
        raise SiemError(f"the syslog format should be \"rfc5424\" or \"rfc3164\", not \"{options.get('format')}\"")
    msgid = _tb.to_text(options.get("msgid") or "-")
    return f"<{pri}>1 {rfc3339(ts)} {host} {app} - {msgid} {sd or '-'} {text}"


# =============================================================================
# Sending
# =============================================================================

def redact_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        if k.lower() in ("authorization", "x-api-key", "apikey", "api-key", "log-type", "x-ms-date"):
            out[k] = v if k.lower() in ("log-type", "x-ms-date") else "***"
        else:
            out[k] = v
    return out


def post(line, url, body: bytes, headers: dict, timeout: float, label: str, fn: str) -> dict:
    """POST and parse the answer; friendly errors that never show the headers."""
    from tblib.http import describe_error, open_url
    hdrs = {"User-Agent": f"TB/{tb_version()}", "Accept": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        resp = open_url(req, timeout)
        status = resp.status if getattr(resp, "status", None) else resp.getcode()
    except urllib.error.HTTPError as err:
        resp, status = err, err.code
    except (urllib.error.URLError, OSError, ssl.SSLError) as err:
        problem = describe_error(err, url)
        fail(line, f"{label}: {problem.message}", reason=problem.reason)
    try:
        raw = resp.read(5_000_000) or b""
    except OSError:
        raw = b""
    finally:
        try:
            resp.close()
        except Exception:
            pass
    text = raw.decode("utf-8", "replace")
    try:
        parsed = _json.loads(text) if text.strip() else None
    except ValueError:
        parsed = None
    from tblib.json import from_json
    return {"status": status, "ok": 200 <= status < 300, "text": text, "json": from_json(parsed) if parsed is not None else None}


def method(obj, owner, name, lo, hi, func, doc=""):
    obj[name] = _tb.Builtin(f"{owner}.{name}", func, (lo, hi), doc)


def load(api):
    m = Module(
        "siem",
        "Get alerts into a SIEM: format any map (or an alert from detect.run()) as CEF, LEEF or a syslog line, send "
        "syslog over UDP/TCP, and forward events to Splunk (HTTP Event Collector), Elasticsearch (bulk API) or "
        "Microsoft Sentinel (Log Analytics data collector API). Every sender has a dry-run mode that gives back "
        "the exact payload instead of sending it. Tokens and keys are never printed.",
    )

    def options_arg(line, options, fn, allowed):
        if options is None:
            return {}
        check_type(line, options, "map", fn, "options")
        unknown = [k for k in options if k not in allowed]
        if unknown:
            fail(line, f"{fn}() doesn't know the option {_tb.to_text(unknown[0], True)}. Options: {', '.join(sorted(allowed))}.")
        return options

    @m.fn("cef", 1, 2, doc=(
        "cef(event, options?) -> text\n"
        "One line of CEF (ArcSight's Common Event Format), like:\n"
        "\"CEF:0|TB|detect|3.0.0|brute_force|Brute force|8|src=... suser=... cnt=...\"\n"
        "\n"
        "event is an alert from detect.run() or any map.\n"
        "\n"
        "Event fields:\n"
        "- \"vendor\", \"product\", \"version\", \"id\": override the header.\n"
        "- \"severity\": overrides the header's severity (0-10).\n"
        "- \"level\": sets the severity (critical = 10).\n"
        "- Other fields become extensions with standard CEF keys:\n"
        "  ip -> src, user -> suser, message -> msg, and so on.\n"
        "\n"
        "Options:\n"
        "- keys: a map {your_field: cef_key} for your own key names."))
    def cef(line, event, options=None):
        check_type(line, event, "map", "siem.cef", "event")
        return to_cef(event, options_arg(line, options, "siem.cef", {"keys", "keep_names"}))

    @m.fn("leef", 1, 2, doc=(
        "leef(event, options?) -> text\n"
        "One line of LEEF 2.0 (IBM QRadar's format), tab-separated attributes: "
        "\"LEEF:2.0|TB|detect|3.0.0|brute_force|sev=8<tab>src=...\". Same event shape as cef(). options: "
        "{\"delimiter\": \"^\", \"keys\": {...}}."))
    def leef(line, event, options=None):
        check_type(line, event, "map", "siem.leef", "event")
        return to_leef(event, options_arg(line, options, "siem.leef", {"keys", "delimiter"}))

    @m.fn("syslog_line", 1, 2, doc=(
        "syslog_line(message_or_event, options?) -> text\n"
        "A syslog line, RFC 5424 by default, like:\n"
        "\"<PRI>1 2026-03-12T08:02:11.000Z host app - - [tb@32473 level=\\\"high\\\" src=\\\"...\\\"] "
        "Brute force\"\n"
        "\n"
        "An alert map's fields go into the structured data.\n"
        "\n"
        "Options:\n"
        "- format: \"rfc3164\" for the classic \"<PRI>Mar 12 08:02:11 host app: message\".\n"
        "- facility: like \"local0\" (default: user).\n"
        "- severity: like \"warning\", or 0-7 (default: from the alert's level).\n"
        "- host, app, time, msgid: values for those parts of the line."))
    def syslog_line(line, message, options=None):
        check_type(line, message, "text|map", "siem.syslog_line", "message_or_event")
        options = options_arg(line, options, "siem.syslog_line", {"format", "facility", "severity", "host", "app", "time", "msgid"})
        try:
            return to_syslog(message, options)
        except SiemError as err:
            fail(line, f"siem.syslog_line(): {err}.")

    @m.fn("send_syslog", 3, 4, doc=(
        "send_syslog(host, port, text_or_lines, proto?) -> map\n"
        "Send one syslog line (or a list of lines) to a syslog server over \"udp\" (default) or \"tcp\" (one line per "
        "message, newline-framed). Gives back {\"sent\": lines, \"bytes\"}. Only send to servers you run."))
    def send_syslog(line, host, port, text, proto="udp"):
        fn = "siem.send_syslog"
        api.need_desktop(line, "The siem library")
        check_type(line, host, "text", fn, "host")
        port = check_type(line, port, "whole", fn, "port")
        check_type(line, text, "text|list", fn, "text_or_lines")
        check_type(line, proto, "text", fn, "proto")
        if not 1 <= port <= 65535:
            fail(line, f"{fn}() needs a port from 1 to 65535.")
        lines = [text] if isinstance(text, str) else [_tb.to_text(t) for t in text]
        proto = proto.lower()
        if proto not in ("udp", "tcp"):
            fail(line, f"{fn}() knows the protocols \"udp\" and \"tcp\", not \"{proto}\".")
        total = 0
        try:
            if proto == "udp":
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(5)
                    for item in lines:
                        total += s.sendto(item.encode("utf-8"), (host, port))
            else:
                with socket.create_connection((host, port), timeout=5) as s:
                    for item in lines:
                        data = item.encode("utf-8") + b"\n"
                        s.sendall(data)
                        total += len(data)
        except socket.gaierror:
            fail(line, f"I couldn't find the host '{host}'. Check the name, or your network.", reason="dns")
        except ConnectionRefusedError:
            fail(line, f"{host}:{port} refused the connection. Is the syslog server listening there?", reason="refused")
        except (socket.timeout, TimeoutError):
            fail(line, f"{host}:{port} didn't answer in time.", reason="timeout")
        except OSError as err:
            fail(line, f"I couldn't send to {host}:{port}: {err.strerror or err}.", reason="connection")
        return {"sent": len(lines), "bytes": total}

    @m.fn("event", 1, doc=(
        "event(alert) -> map\n"
        "The flat event that cef(), leef() and the senders build from an alert. "
        "Handy to see what will be sent, or to tweak it before sending.\n"
        "\n"
        "Fields:\n"
        "- \"name\", \"level\", \"id\", \"time\": from the alert.\n"
        "- \"severity\": 0-10.\n"
        "- The alert's by field, under its own name.\n"
        "- count, description and tags.\n"
        "- ip, user, host and message from the first record."))
    def event(line, alert):
        check_type(line, alert, "map", "siem.event", "alert")
        n = normalise_event(alert)
        out = dict(n["header"])
        for k, v in n["extensions"].items():
            out.setdefault(k, v)
        return out

    def sender_options(line, options, fn, extra=()):
        allowed = {"dry_run", "timeout", "verify"} | set(extra)
        options = options_arg(line, options, fn, allowed)
        timeout = options.get("timeout", TIMEOUT)
        check_type(line, timeout, "number", fn, "the timeout option")
        return dict(options, timeout=float(timeout), dry_run=bool(options.get("dry_run", False)))

    def events_arg(line, events, fn):
        check_type(line, events, "list|map", fn, "events")
        events = [events] if isinstance(events, dict) else events
        if not events:
            fail(line, f"{fn}() got an empty list; nothing to send.")
        for e in events:
            if not isinstance(e, dict):
                fail(line, f"{fn}() needs a list of maps (events or alerts), not a list with {_tb.type_name(e)} in it.")
        return events

    def plain(line, value):
        from tblib.json import to_plain
        return to_plain(value, line)

    def deliver(line, url, body, headers, opts, label, fn, count):
        if opts["dry_run"]:
            return {"dry_run": True, "url": url, "headers": redact_headers(headers),
                    "body": body.decode("utf-8", "replace"), "count": count, "sent": 0}
        api.need_desktop(line, "The siem library")
        return post(line, url, body, headers, opts["timeout"], label, fn)

    # -- Splunk HEC ---------------------------------------------------------------------------

    @m.fn("splunk", 2, 3, doc=(
        "splunk(url, token, options?) -> sender\n"
        "A Splunk HTTP Event Collector client. The token is never printed.\n"
        "\n"
        "Methods:\n"
        "- send(events): posts each map as an event.\n"
        "  Alerts from detect.run() are flattened with siem.event().\n"
        "\n"
        "Options:\n"
        "- index, source, host: set on every event.\n"
        "- sourcetype: the source type (default: \"tb:detect\").\n"
        "- dry_run: true to get the payload back instead of sending.\n"
        "- timeout: seconds to wait.\n"
        "\n"
        "Returns (from send):\n"
        "- \"sent\", \"status\", \"ok\", \"response\".\n"
        "- In dry-run: \"dry_run\": true, \"url\", \"headers\" (token hidden), \"body\", \"count\".\n"
        "\n"
        "Example:\n"
        "    let s = siem.splunk(\"https://splunk.example:8088\", \"my-token\", {\"dry_run\": true})\n"
        "    let out = s.send([{\"user\": \"Tanisha\", \"action\": \"login\"}])\n"
        "    say(out.body)"))
    def splunk(line, url, token, options=None):
        fn = "siem.splunk"
        from tblib.http import check_url
        url = check_url(line, url, fn)
        check_type(line, token, "text", fn, "token")
        if not token.strip():
            fail(line, f"{fn}() needs the HEC token (Settings > Data inputs > HTTP Event Collector in Splunk).")
        opts = sender_options(line, options, fn, ("index", "sourcetype", "source", "host"))
        endpoint = url if "/services/collector" in url else url.rstrip("/") + "/services/collector/event"
        obj = {"kind": "splunk", "url": endpoint, "index": opts.get("index"), "sourcetype": opts.get("sourcetype", "tb:detect"),
               "dry_run": opts["dry_run"]}

        def send(line2, events):
            events = events_arg(line2, events, "splunk.send")
            lines = []
            for e in events:
                is_alert = "rule" in e and "level" in e
                payload = {"event": plain(line2, flat_event(e) if is_alert else e), "sourcetype": obj["sourcetype"]}
                when = e.get("time", e.get("first") if is_alert else None)
                if _tb.is_number(when):
                    payload["time"] = when
                for key in ("index", "source", "host"):
                    if opts.get(key):
                        payload[key] = opts[key]
                lines.append(_json.dumps(payload, ensure_ascii=False))
            body = "\n".join(lines).encode("utf-8")
            headers = {"Authorization": f"Splunk {token.strip()}", "Content-Type": "application/json"}
            answer = deliver(line2, endpoint, body, headers, opts, "Splunk", "splunk.send", len(events))
            if answer.get("dry_run"):
                return answer
            info = answer["json"] if isinstance(answer["json"], dict) else {}
            if answer["status"] in (401, 403):
                fail(line2, f"Splunk rejected the HEC token (HTTP {answer['status']}): {info.get('text') or 'check the token'}.",
                     reason="bad_key", status=answer["status"])
            if not answer["ok"]:
                fail(line2, f"Splunk answered HTTP {answer['status']}: {info.get('text') or answer['text'][:200] or 'no details'}.",
                     reason="status", status=answer["status"])
            return {"sent": len(events), "status": answer["status"], "ok": True, "response": answer["json"]}

        method(obj, "splunk", "send", 1, 1, send, "send(events) -> map\nPost events (a list of maps or alerts) to the collector.")
        return obj

    def flat_event(alert):
        n = normalise_event(alert)
        out = {"name": n["header"]["name"], "severity": n["header"]["severity"], "level": n["header"]["level"],
               "signature_id": n["header"]["id"]}
        for k, v in n["extensions"].items():
            out.setdefault(k, v)
        return out

    # -- Elasticsearch bulk -------------------------------------------------------------------

    @m.fn("elastic", 1, 3, doc=(
        "elastic(url, auth?, options?) -> sender\n"
        "An Elasticsearch / OpenSearch client for the bulk API.\n"
        "\n"
        "auth can be:\n"
        "- nil: no login.\n"
        "- {\"user\", \"password\"}: basic auth.\n"
        "- {\"api_key\": \"...\"}: an API key.\n"
        "- text: a bearer token.\n"
        "\n"
        "Methods:\n"
        "- index(name, docs): indexes each map into the index name.\n"
        "  Alerts from detect.run() are flattened and get @timestamp.\n"
        "\n"
        "Options:\n"
        "- dry_run: true to get the payload back instead of sending.\n"
        "- timeout: seconds to wait.\n"
        "\n"
        "Returns (from index):\n"
        "- \"sent\", \"status\", \"response\".\n"
        "- \"errors\": how many documents failed.\n"
        "\n"
        "Example:\n"
        "    let auth = {\"user\": \"tb\", \"password\": \"secret\"}\n"
        "    let es = siem.elastic(\"https://es.example:9200\", auth, {\"dry_run\": true})\n"
        "    let out = es.index(\"alerts-tb\", [{\"user\": \"Tanisha\"}])\n"
        "    say(out.body)"))
    def elastic(line, url, auth=None, options=None):
        fn = "siem.elastic"
        from tblib.http import check_url
        url = check_url(line, url, fn).rstrip("/")
        headers = {"Content-Type": "application/x-ndjson"}
        if auth is not None:
            check_type(line, auth, "text|map", fn, "auth")
            if isinstance(auth, str):
                headers["Authorization"] = f"Bearer {auth.strip()}"
            elif "api_key" in auth:
                headers["Authorization"] = f"ApiKey {_tb.to_text(auth['api_key'])}"
            elif "user" in auth and "password" in auth:
                pair = f"{_tb.to_text(auth['user'])}:{_tb.to_text(auth['password'])}".encode("utf-8")
                headers["Authorization"] = "Basic " + _b64.b64encode(pair).decode("ascii")
            else:
                fail(line, f"{fn}() needs auth to be {{\"user\", \"password\"}}, {{\"api_key\"}} or a token.")
        opts = sender_options(line, options, fn)
        obj = {"kind": "elastic", "url": url, "dry_run": opts["dry_run"]}

        def index(line2, name, docs):
            check_type(line2, name, "text", "elastic.index", "index")
            if not name.strip() or any(ch in name for ch in ' "*\\<|,>/?#') or name.strip() != name.strip().lower():
                fail(line2, f"elastic.index(): \"{name}\" isn't a valid index name (lower case, no spaces or / \\ * ? \" < > | , #).")
            docs = events_arg(line2, docs, "elastic.index")
            lines = []
            for d in docs:
                is_alert = "rule" in d and "level" in d
                doc = dict(flat_event(d) if is_alert else d)
                when = doc.pop("time", None) if "time" in doc else d.get("first") if is_alert else None
                if _tb.is_number(when) and "@timestamp" not in doc:
                    doc["@timestamp"] = _dt.datetime.fromtimestamp(when, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                lines.append(_json.dumps({"index": {"_index": name}}))
                lines.append(_json.dumps(plain(line2, doc), ensure_ascii=False))
            body = ("\n".join(lines) + "\n").encode("utf-8")
            answer = deliver(line2, f"{url}/_bulk", body, headers, opts, "Elasticsearch", "elastic.index", len(docs))
            if answer.get("dry_run"):
                return answer
            info = answer["json"] if isinstance(answer["json"], dict) else {}
            if answer["status"] in (401, 403):
                fail(line2, f"Elasticsearch rejected the credentials (HTTP {answer['status']}).", reason="bad_key",
                     status=answer["status"])
            if not answer["ok"]:
                why = (info.get("error") or {}).get("reason") if isinstance(info.get("error"), dict) else answer["text"][:200]
                fail(line2, f"Elasticsearch answered HTTP {answer['status']}: {why or 'no details'}.", reason="status",
                     status=answer["status"])
            failed = 0
            for item in info.get("items") or []:
                action = item.get("index") or item.get("create") or {}
                if isinstance(action, dict) and action.get("error"):
                    failed += 1
            return {"sent": len(docs) - failed, "errors": failed, "status": answer["status"], "response": answer["json"]}

        method(obj, "elastic", "index", 2, 2, index, "index(index, docs) -> map\nBulk-index maps into an index.")
        return obj

    # -- Microsoft Sentinel (Log Analytics data collector API) ------------------------------------

    @m.fn("sentinel", 2, 3, doc=(
        "sentinel(workspace_id, key, options?) -> sender\n"
        "A Microsoft Sentinel / Log Analytics client. It uses the HTTP Data Collector API "
        "with the shared-key signature.\n"
        "\n"
        "key is the workspace's primary or secondary key (base64).\n"
        "\n"
        "Methods:\n"
        "- send(log_type, records): posts records to a custom table.\n"
        "  send(\"TBAlerts\", records) creates the table TBAlerts_CL.\n"
        "\n"
        "Options:\n"
        "- dry_run: true to get the payload back instead of sending.\n"
        "- timeout: seconds to wait.\n"
        "- url: another endpoint (for tests and sovereign clouds).\n"
        "- time_field: the record field holding the event time.\n"
        "\n"
        "Returns (from send):\n"
        "- \"sent\", \"status\", \"ok\".\n"
        "\n"
        "Example:\n"
        "    let s = siem.sentinel(\"my-workspace-id\", \"c2VjcmV0\", {\"dry_run\": true})\n"
        "    let out = s.send(\"TBAlerts\", [{\"user\": \"Tanisha\"}])\n"
        "    say(out.url)"))
    def sentinel(line, workspace_id, key, options=None):
        fn = "siem.sentinel"
        check_type(line, workspace_id, "text", fn, "workspace_id")
        check_type(line, key, "text", fn, "key")
        if not workspace_id.strip() or not key.strip():
            fail(line, f"{fn}() needs the workspace id and its shared key (Log Analytics workspace > Agents).")
        try:
            key_bytes = _b64.b64decode(key.strip(), validate=True)
        except Exception:
            fail(line, f"{fn}(): the key should be the base64 shared key from the workspace's Agents page.")
        opts = sender_options(line, options, fn, ("url", "time_field"))
        base = opts.get("url") or f"https://{workspace_id.strip()}.ods.opinsights.azure.com"
        endpoint = base.rstrip("/") + "/api/logs?api-version=2016-04-01"
        obj = {"kind": "sentinel", "url": endpoint, "workspace_id": workspace_id.strip(), "dry_run": opts["dry_run"]}

        def send(line2, log_type, records):
            check_type(line2, log_type, "text", "sentinel.send", "log_type")
            if not log_type.replace("_", "").isalnum() or not log_type[:1].isalpha() or len(log_type) > 100:
                fail(line2, f"sentinel.send(): \"{log_type}\" isn't a valid log type (letters, numbers and _, "
                            "starting with a letter, up to 100 characters).")
            records = events_arg(line2, records, "sentinel.send")
            docs = [flat_event(r) if "rule" in r and "level" in r else r for r in records]
            body = _json.dumps(plain(line2, docs), ensure_ascii=False).encode("utf-8")
            date = _dt.datetime.now(_dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
            to_sign = f"POST\n{len(body)}\napplication/json\nx-ms-date:{date}\n/api/logs".encode("utf-8")
            signature = _b64.b64encode(_hmac.new(key_bytes, to_sign, hashlib.sha256).digest()).decode("ascii")
            headers = {"Content-Type": "application/json", "Log-Type": log_type, "x-ms-date": date,
                       "Authorization": f"SharedKey {workspace_id.strip()}:{signature}"}
            if opts.get("time_field"):
                headers["time-generated-field"] = _tb.to_text(opts["time_field"])
            answer = deliver(line2, endpoint, body, headers, opts, "Sentinel", "sentinel.send", len(records))
            if answer.get("dry_run"):
                return answer
            if answer["status"] in (401, 403):
                fail(line2, f"Sentinel rejected the shared key (HTTP {answer['status']}). Check the workspace id and key.",
                     reason="bad_key", status=answer["status"])
            if not answer["ok"]:
                fail(line2, f"Sentinel answered HTTP {answer['status']}: {answer['text'][:200] or 'no details'}.",
                     reason="status", status=answer["status"])
            return {"sent": len(records), "status": answer["status"], "ok": True}

        method(obj, "sentinel", "send", 2, 2, send, "send(log_type, records) -> map\nPost records to a custom log table.")
        return obj

    m.value("severities", dict(SEVERITY), doc="Alert level -> CEF severity (0-10) used by cef() and the senders.")
    return m
