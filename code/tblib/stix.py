"""stix: read and write STIX 2.1 threat-intel bundles, turn indicators into detect rules, pull from TAXII 2.1."""

from __future__ import annotations

import base64 as _b64
import datetime as _dt
import json as _json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # the URL namespace; ids are stable for the same IOC
TAXII_TYPE = "application/taxii+json;version=2.1"
STIX_TYPE = "application/stix+json;version=2.1"
TIMEOUT = 30
MAX_PAGES = 20

# A STIX pattern comparison: [object-type:property.path OP value]
COMPARISON = re.compile(
    r"([a-z0-9][a-z0-9\-]*):((?:[A-Za-z0-9_\-]+|'[^']*'|\[\d+\]|\[\*\]|\.)+)\s*"
    r"(NOT\s+)?(=|!=|<=|>=|<|>|IN|LIKE|MATCHES|ISSUBSET|ISSUPERSET)\s*"
    r"('(?:[^'\\]|\\.)*'|\((?:[^()'\\]|'(?:[^'\\]|\\.)*')*\)|-?\d+(?:\.\d+)?|true|false|[bht]'[^']*')",
    re.IGNORECASE)
QUOTED = re.compile(r"'((?:[^'\\]|\\.)*)'")

HASH_KINDS = {"md5": "md5", "sha-1": "sha1", "sha1": "sha1", "sha-256": "sha256", "sha256": "sha256",
              "sha-512": "sha512", "sha512": "sha512"}
# (object type, property path) -> IOC kind
PROPERTY_KINDS = {
    ("ipv4-addr", "value"): "ip", ("ipv6-addr", "value"): "ip", ("domain-name", "value"): "domain",
    ("url", "value"): "url", ("email-addr", "value"): "email", ("file", "name"): "filename",
    ("network-traffic", "dst_ref.value"): "ip", ("network-traffic", "src_ref.value"): "ip",
    ("windows-registry-key", "key"): "registry", ("mutex", "name"): "mutex",
    ("process", "command_line"): "command", ("process", "name"): "process",
    ("email-message", "from_ref.value"): "email", ("email-message", "subject"): "subject",
    ("autonomous-system", "number"): "asn", ("user-account", "user_id"): "user",
    ("mac-addr", "value"): "mac", ("x509-certificate", "hashes.'sha-1'"): "sha1",
}
# IOC kind -> STIX pattern path used by stix.bundle()
WRITE_PATHS = {"ip": None, "domain": "domain-name:value", "url": "url:value", "email": "email-addr:value",
               "md5": "file:hashes.MD5", "sha1": "file:hashes.'SHA-1'", "sha256": "file:hashes.'SHA-256'",
               "sha512": "file:hashes.'SHA-512'", "filename": "file:name", "registry": "windows-registry-key:key",
               "mutex": "mutex:name", "command": "process:command_line", "process": "process:name"}
EXTRACT_KEYS = {"ips": "ip", "domains": "domain", "urls": "url", "emails": "email", "md5": "md5", "sha1": "sha1",
                "sha256": "sha256", "sha512": "sha512"}
# IOC kind -> record fields the generated detect rules look at (field -> modifier)
DEFAULT_FIELDS = {
    "ip": ["ip", "src_ip", "dst_ip", "source_ip", "dest_ip", "client_ip", "IpAddress", "SourceIp", "DestinationIp"],
    "domain": ["domain", "host", "hostname", "query", "QueryName", "url|contains"],
    "url": ["url", "message|contains"],
    "email": ["email", "from", "sender", "to"],
    "md5": ["md5", "hash", "Hashes|contains"],
    "sha1": ["sha1", "hash", "Hashes|contains"],
    "sha256": ["sha256", "hash", "Hashes|contains"],
    "sha512": ["sha512", "hash"],
    "filename": ["file", "filename", "TargetFilename|endswith", "Image|endswith"],
    "registry": ["TargetObject", "registry"],
    "mutex": ["mutex"],
    "command": ["CommandLine|contains", "command|contains"],
    "process": ["Image|endswith", "process"],
    "user": ["user"],
}


class StixError(Exception):
    pass


# =============================================================================
# Reading
# =============================================================================

def as_bundle(value) -> dict:
    """A bundle map from a bundle, a list of objects, a single object, or a TAXII envelope."""
    if isinstance(value, list):
        return {"type": "bundle", "objects": [o for o in value if isinstance(o, dict)]}
    if not isinstance(value, dict):
        raise StixError("a STIX bundle should be a map with \"type\": \"bundle\" and \"objects\"")
    if value.get("type") == "bundle" or ("objects" in value and isinstance(value.get("objects"), list)):
        objects = value.get("objects") or []
        if not isinstance(objects, list):
            raise StixError("the bundle's \"objects\" should be a list")
        return {"type": "bundle", "id": value.get("id"), "objects": [o for o in objects if isinstance(o, dict)]}
    if isinstance(value.get("type"), str):
        return {"type": "bundle", "objects": [value]}
    raise StixError("a STIX bundle should be a map with \"type\": \"bundle\" and \"objects\"")


def unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def kind_for(obj_type: str, path: str, value: str):
    obj_type = obj_type.lower()
    if obj_type in ("file", "artifact", "x509-certificate") and path.lower().startswith("hashes."):
        algo = path[7:].strip("'").lower()
        return HASH_KINDS.get(algo)
    if obj_type == "network-traffic" and path.endswith("_ref.value"):
        return "ip"
    return PROPERTY_KINDS.get((obj_type, path.lower()))


def pattern_iocs(pattern: str) -> dict:
    """{"iocs": [{"type", "value", "object", "property"}], "skipped": [comparison texts we can't turn into IOCs]}."""
    iocs, skipped = [], []
    if not isinstance(pattern, str):
        return {"iocs": iocs, "skipped": skipped}
    for m in COMPARISON.finditer(pattern):
        obj_type, path, negated, op, raw = m.group(1), m.group(2), m.group(3), m.group(4).upper(), m.group(5)
        if negated or op not in ("=", "IN"):
            skipped.append(m.group(0))
            continue
        values = [unescape(v) for v in QUOTED.findall(raw)] if raw.startswith(("'", "(")) else [raw]
        kind = None
        for value in values:
            kind = kind_for(obj_type, path, value)
            if kind == "ip" and "/" in value:
                kind = "cidr"
            if kind is None:
                continue
            item = {"type": kind, "value": value.lower() if kind in ("md5", "sha1", "sha256", "sha512", "domain") else value,
                    "object": obj_type.lower(), "property": path}
            if not any(i["type"] == item["type"] and i["value"] == item["value"] for i in iocs):
                iocs.append(item)
        if kind is None:
            skipped.append(m.group(0))
    return {"iocs": iocs, "skipped": skipped}


def parse_time(text):
    if not isinstance(text, str) or not text:
        return None
    s = text.strip().replace("Z", "+00:00")
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            when = _dt.datetime.fromisoformat(re.sub(r"\.(\d{6})\d+", r".\1", s))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.timestamp()


def attack_tags(obj: dict) -> list:
    tags = []
    for phase in obj.get("kill_chain_phases") or []:
        if isinstance(phase, dict) and str(phase.get("kill_chain_name", "")).lower() == "mitre-attack":
            tag = "attack." + str(phase.get("phase_name", "")).replace("-", "_").lower()
            if tag not in tags:
                tags.append(tag)
    for ref in obj.get("external_references") or []:
        if isinstance(ref, dict) and str(ref.get("source_name", "")).lower() == "mitre-attack":
            eid = str(ref.get("external_id") or "")
            if re.match(r"^T\d{4}(\.\d{3})?$", eid, re.IGNORECASE):
                tag = "attack." + eid.lower()
                if tag not in tags:
                    tags.append(tag)
    return tags


def indicators_of(bundle: dict, now=None, include_expired=False) -> list:
    out = []
    now = _dt.datetime.now(_dt.timezone.utc).timestamp() if now is None else now
    for obj in bundle["objects"]:
        if obj.get("type") != "indicator" or obj.get("revoked") is True:
            continue
        until = parse_time(obj.get("valid_until"))
        expired = until is not None and until < now
        if expired and not include_expired:
            continue
        pattern_type = str(obj.get("pattern_type") or "stix")
        found = pattern_iocs(obj.get("pattern")) if pattern_type.lower() == "stix" else {"iocs": [], "skipped": []}
        out.append({
            "id": obj.get("id"), "name": obj.get("name") or "", "description": obj.get("description") or "",
            "pattern": obj.get("pattern") or "", "pattern_type": pattern_type,
            "indicator_types": list(obj.get("indicator_types") or obj.get("labels") or []),
            "valid_from": obj.get("valid_from"), "valid_until": obj.get("valid_until"), "expired": expired,
            "confidence": obj.get("confidence"), "created": obj.get("created"), "modified": obj.get("modified"),
            "tags": attack_tags(obj), "iocs": [{"type": i["type"], "value": i["value"]} for i in found["iocs"]],
            "skipped": found["skipped"],
        })
    return out


def observable_iocs(bundle: dict) -> list:
    """IOCs from STIX cyber observables (SCOs) placed straight in the bundle."""
    out = []
    for obj in bundle["objects"]:
        t = obj.get("type")
        if t in ("ipv4-addr", "ipv6-addr") and isinstance(obj.get("value"), str):
            out.append({"type": "cidr" if "/" in obj["value"] else "ip", "value": obj["value"]})
        elif t == "domain-name" and isinstance(obj.get("value"), str):
            out.append({"type": "domain", "value": obj["value"].lower()})
        elif t == "url" and isinstance(obj.get("value"), str):
            out.append({"type": "url", "value": obj["value"]})
        elif t == "email-addr" and isinstance(obj.get("value"), str):
            out.append({"type": "email", "value": obj["value"]})
        elif t == "file":
            for algo, value in (obj.get("hashes") or {}).items():
                kind = HASH_KINDS.get(str(algo).lower())
                if kind and isinstance(value, str):
                    out.append({"type": kind, "value": value.lower()})
            if isinstance(obj.get("name"), str):
                out.append({"type": "filename", "value": obj["name"]})
    return out


# =============================================================================
# Detect rules
# =============================================================================

def level_for(indicator, default):
    conf = indicator.get("confidence")
    if default is not None:
        return default
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        return "critical" if conf >= 85 else "high" if conf >= 60 else "medium" if conf >= 30 else "low"
    return "high"


def detection_for(iocs: list, fields: dict) -> dict | None:
    alternatives = []
    by_kind: dict = {}
    for i in iocs:
        kind = "ip" if i["type"] == "cidr" else i["type"]
        values = by_kind.setdefault(kind, {"plain": [], "cidr": []})
        values["cidr" if i["type"] == "cidr" else "plain"].append(i["value"])
    for kind, values in by_kind.items():
        for field in fields.get(kind, []):
            name, _, mod = field.partition("|")
            if values["plain"]:
                alternatives.append({field: list(values["plain"])})
            if values["cidr"] and kind == "ip" and not mod:
                alternatives.append({f"{name}|cidr": list(values["cidr"])})
    if not alternatives:
        return None
    return {"ioc": alternatives, "condition": "ioc"}


def to_rules(bundle: dict, options: dict) -> list:
    fields = {k: list(v) for k, v in DEFAULT_FIELDS.items()}
    for k, v in (options.get("fields") or {}).items():
        fields[str(k)] = [v] if isinstance(v, str) else [str(x) for x in v]
    level = options.get("level")
    group = options.get("group", "indicator")
    inds = indicators_of(bundle, include_expired=bool(options.get("expired")))
    rules = []
    if group == "type":
        by_type: dict = {}
        for ind in inds:
            for i in ind["iocs"]:
                key = "ip" if i["type"] == "cidr" else i["type"]
                bucket = by_type.setdefault(key, [])
                if i not in bucket:
                    bucket.append(i)
        for i in observable_iocs(bundle):
            key = "ip" if i["type"] == "cidr" else i["type"]
            bucket = by_type.setdefault(key, [])
            if i not in bucket:
                bucket.append(i)
        for kind in sorted(by_type):
            det = detection_for(by_type[kind], fields)
            if det:
                rules.append({"name": f"Threat intel match: {kind} ({len(by_type[kind])} IOCs)",
                              "level": level or "high", "detection": det, "tags": ["stix", f"ioc.{kind}"],
                              "description": f"A record mentions one of {len(by_type[kind])} {kind} IOCs from a STIX feed."})
        return rules
    for ind in inds:
        det = detection_for(ind["iocs"], fields)
        if not det:
            continue
        name = ind["name"] or ", ".join(i["value"] for i in ind["iocs"][:2])
        desc = ind["description"] or f"Matches the STIX indicator {ind['pattern']}"
        rules.append({"name": f"Threat intel: {name}", "level": level_for(ind, level), "detection": det,
                      "tags": ["stix"] + ind["tags"] + [f"stix.{t}" for t in ind["indicator_types"]],
                      "description": desc, "stix_id": ind["id"]})
    return rules


# =============================================================================
# Writing
# =============================================================================

def now_text():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def pattern_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ioc_items(line, iocs) -> list:
    """Normalise the many ways to give IOCs: texts, {type, value} maps, or ioc.extract()'s map."""
    from tblib.ioc import kind_of
    items = []
    if isinstance(iocs, dict):
        if "type" in iocs and "value" in iocs:
            iocs = [iocs]
        else:
            flat = []
            for key, kind in EXTRACT_KEYS.items():
                for v in iocs.get(key) or []:
                    flat.append({"type": kind, "value": v})
            iocs = flat
    for item in iocs:
        if isinstance(item, str):
            value = item.strip()
            kind = kind_of(value)
            if kind is None and re.fullmatch(r"[0-9a-fA-F:.]+/\d{1,3}", value):
                kind = "cidr"
            if kind is None or kind == "cve":
                fail(line, f"stix.bundle() can't tell what kind of IOC '{value}' is; give it as {{\"type\", \"value\"}}.")
            items.append({"type": kind, "value": value})
        elif isinstance(item, dict) and isinstance(item.get("value"), str) and isinstance(item.get("type"), str):
            kind = item["type"].lower()
            if kind not in WRITE_PATHS and kind != "cidr":
                fail(line, f"stix.bundle() can't write the IOC type '{item['type']}'. Types: "
                           f"{', '.join(sorted(list(WRITE_PATHS) + ['cidr']))}.")
            items.append({"type": kind, "value": item["value"].strip(), "name": item.get("name"),
                          "description": item.get("description")})
        else:
            fail(line, "stix.bundle() needs IOCs as text, {\"type\", \"value\"} maps, or the map from ioc.extract().")
    return items


def make_pattern(item) -> str:
    kind, value = item["type"], item["value"]
    if kind in ("ip", "cidr"):
        path = "ipv6-addr:value" if ":" in value else "ipv4-addr:value"
    else:
        path = WRITE_PATHS[kind]
    return f"[{path} = {pattern_quote(value)}]"


def make_bundle(items, options) -> dict:
    stamp = options.get("time") or now_text()
    types = options.get("indicator_types") or ["malicious-activity"]
    objects, patterns = [], []
    for item in items:
        pattern = make_pattern(item)
        if pattern in patterns:
            continue
        patterns.append(pattern)
        obj = {"type": "indicator", "spec_version": "2.1", "id": f"indicator--{uuid.uuid5(NAMESPACE, pattern)}",
               "created": stamp, "modified": stamp, "name": item.get("name") or f"{item['type']}: {item['value']}",
               "indicator_types": list(types), "pattern": pattern, "pattern_type": "stix", "valid_from": stamp}
        if item.get("description") or options.get("description"):
            obj["description"] = item.get("description") or options.get("description")
        if options.get("valid_until"):
            obj["valid_until"] = options["valid_until"]
        if options.get("confidence") is not None:
            obj["confidence"] = options["confidence"]
        if options.get("tags"):
            phases = []
            for tag in options["tags"]:
                m = re.match(r"^attack\.([a-z_]+)$", str(tag))
                if m and not re.match(r"^t\d{4}", m.group(1)):
                    phases.append({"kill_chain_name": "mitre-attack", "phase_name": m.group(1).replace("_", "-")})
            if phases:
                obj["kill_chain_phases"] = phases
        objects.append(obj)
    bundle_id = f"bundle--{uuid.uuid5(NAMESPACE, '|'.join(patterns) + stamp)}"
    return {"type": "bundle", "id": bundle_id, "objects": objects}


# =============================================================================
# TAXII 2.1
# =============================================================================

def auth_headers(line, auth, fn) -> dict:
    if auth is None:
        return {}
    check_type(line, auth, "text|map", fn, "auth")
    if isinstance(auth, str):
        return {"Authorization": f"Bearer {auth.strip()}"}
    if "user" in auth and "password" in auth:
        pair = f"{_tb.to_text(auth['user'])}:{_tb.to_text(auth['password'])}".encode("utf-8")
        return {"Authorization": "Basic " + _b64.b64encode(pair).decode("ascii")}
    if "token" in auth:
        return {"Authorization": f"Bearer {_tb.to_text(auth['token'])}"}
    if "header" in auth and "key" in auth:
        return {_tb.to_text(auth["header"]): _tb.to_text(auth["key"])}
    fail(line, f"{fn}() needs auth as a token (text), {{\"user\", \"password\"}}, {{\"token\"}} or {{\"header\", \"key\"}}.")


def taxii_get(line, url, headers, timeout, fn):
    """GET a TAXII endpoint -> (status, parsed JSON or None, text). Errors never show the headers."""
    from tblib.http import describe_error, open_url
    hdrs = {"Accept": TAXII_TYPE, "User-Agent": f"TB/{getattr(_tb, '__version__', '3.0.0')}"}
    hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        resp = open_url(req, timeout)
        status = resp.status if getattr(resp, "status", None) else resp.getcode()
    except urllib.error.HTTPError as err:
        resp, status = err, err.code
    except (urllib.error.URLError, OSError, ssl.SSLError) as err:
        problem = describe_error(err, url)
        fail(line, f"TAXII: {problem.message}", reason=problem.reason)
    try:
        raw = resp.read(50_000_000) or b""
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
    if status in (401, 403):
        fail(line, f"The TAXII server refused access (HTTP {status}). Check the user name, password or token.",
             reason="bad_key", status=status)
    return status, parsed, text


def load(api):
    m = Module(
        "stix",
        "Threat intelligence in STIX 2.1: read bundles, pull out indicators and their IOCs (IPs, domains, URLs, "
        "hashes, emails) from STIX patterns, turn them into detect rules you can run on logs, write your own IOCs "
        "as a bundle, and download from TAXII 2.1 servers.",
    )

    def bundle_arg(line, value, fn):
        check_type(line, value, "map|list", fn, "bundle")
        try:
            return as_bundle(value)
        except StixError as err:
            fail(line, f"{fn}(): {err}.")

    def full_path(path):
        return path

    @m.fn("parse", 1, doc=(
        "parse(text) -> map\n"
        "Read STIX JSON text into a bundle map {\"type\": \"bundle\", \"id\", \"objects\"}. A bare list of objects "
        "or a single object is wrapped in a bundle; so is a TAXII envelope ({\"objects\": [...]})."))
    def parse(line, text):
        check_type(line, text, "text", "stix.parse", "text")
        try:
            data = _json.loads(text)
        except ValueError as err:
            fail(line, f"stix.parse() needs JSON text: {err}.")
        from tblib.json import from_json
        return bundle_arg(line, from_json(data), "stix.parse")

    @m.fn("read", 1, doc="read(path) -> map\nRead a STIX JSON file into a bundle map (see stix.parse).")
    def read(line, path):
        check_type(line, path, "text", "stix.read", "path")
        try:
            with open(full_path(path), encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as err:
            fail_os(line, err, path, "read")
        return parse(line, text)

    @m.fn("write", 2, doc="write(path, bundle) -> nil\nWrite a bundle (from stix.bundle() or stix.read()) as pretty JSON.")
    def write(line, path, bundle):
        check_type(line, path, "text", "stix.write", "path")
        b = bundle_arg(line, bundle, "stix.write")
        from tblib.json import to_plain
        try:
            with open(full_path(path), "w", encoding="utf-8") as f:
                _json.dump(to_plain(b, line), f, indent=2, ensure_ascii=False)
                f.write("\n")
        except OSError as err:
            fail_os(line, err, path, "write")
        return None

    @m.fn("stringify", 1, doc="stringify(bundle) -> text\nThe bundle as pretty-printed STIX JSON text.")
    def stringify(line, bundle):
        b = bundle_arg(line, bundle, "stix.stringify")
        from tblib.json import to_plain
        return _json.dumps(to_plain(b, line), indent=2, ensure_ascii=False)

    @m.fn("indicators", 1, 2, doc=(
        "indicators(bundle, options?) -> list\n"
        "The indicator objects in a bundle, each as a map.\n"
        "\n"
        "Fields of each indicator:\n"
        "- \"id\", \"name\", \"description\": from the STIX object.\n"
        "- \"pattern\", \"pattern_type\", \"indicator_types\": from the STIX object.\n"
        "- \"valid_from\", \"valid_until\", \"expired\", \"confidence\": from the STIX object.\n"
        "- \"tags\": ATT&CK tags from the kill chain phases.\n"
        "- \"iocs\": a list of {\"type\", \"value\"} maps.\n"
        "- \"skipped\": comparisons that aren't exact (LIKE, MATCHES, !=).\n"
        "\n"
        "IOCs come from pattern comparisons with = or IN. For example, "
        "[ipv4-addr:value = '198.51.100.7'] gives {\"type\": \"ip\", ...}.\n"
        "\n"
        "IOC types:\n"
        "- ip, domain, url, email, filename, cidr.\n"
        "- md5, sha1, sha256 (from file hashes).\n"
        "\n"
        "Revoked indicators are left out. So are expired ones (valid_until in the past).\n"
        "\n"
        "Options:\n"
        "- expired: true keeps the expired indicators."))
    def indicators(line, bundle, options=None):
        b = bundle_arg(line, bundle, "stix.indicators")
        opts = options or {}
        if options is not None:
            check_type(line, options, "map", "stix.indicators", "options")
        return indicators_of(b, include_expired=bool(opts.get("expired")))

    @m.fn("iocs", 1, doc=(
        "iocs(bundle) -> map\n"
        "Every IOC in a bundle (from indicator patterns and from cyber observables such as ipv4-addr objects), "
        "grouped like ioc.extract(): {\"ips\", \"domains\", \"urls\", \"emails\", \"md5\", \"sha1\", \"sha256\", "
        "\"sha512\", \"cidrs\", \"filenames\"}, each a list without repeats."))
    def iocs(line, bundle):
        b = bundle_arg(line, bundle, "stix.iocs")
        out = {k: [] for k in ("ips", "domains", "urls", "emails", "md5", "sha1", "sha256", "sha512", "cidrs", "filenames")}
        keys = {"ip": "ips", "domain": "domains", "url": "urls", "email": "emails", "md5": "md5", "sha1": "sha1",
                "sha256": "sha256", "sha512": "sha512", "cidr": "cidrs", "filename": "filenames"}
        found = [i for ind in indicators_of(b) for i in ind["iocs"]] + observable_iocs(b)
        for i in found:
            key = keys.get(i["type"])
            if key and i["value"] not in out[key]:
                out[key].append(i["value"])
        return out

    @m.fn("pattern", 1, doc=(
        "pattern(text) -> list\n"
        "The IOCs in one STIX pattern: stix.pattern(\"[ipv4-addr:value = '203.0.113.9' OR domain-name:value = "
        "'bad.example']\") gives [{\"type\": \"ip\", \"value\": \"203.0.113.9\", \"object\", \"property\"}, ...]."))
    def pattern(line, text):
        check_type(line, text, "text", "stix.pattern", "text")
        return pattern_iocs(text)["iocs"]

    @m.fn("to_detect_rules", 1, 2, doc=(
        "to_detect_rules(bundle, options?) -> list\n"
        "Detect rules (for detect.run()) from a bundle's indicators. "
        "By default there is one rule per indicator.\n"
        "\n"
        "A rule alerts when a record field holds one of its IOCs:\n"
        "- ip, src_ip or dst_ip for IPs (CIDR ranges use |cidr).\n"
        "- domain, host or query for domains.\n"
        "- url, hash, email or file name fields for those types.\n"
        "\n"
        "The level comes from the indicator's confidence (high when there is none). "
        "ATT&CK kill chain phases become tags, so attack.map() works on the alerts.\n"
        "\n"
        "Options:\n"
        "- level: a fixed level for every rule, like \"critical\".\n"
        "- group: \"type\" makes one rule per IOC type instead of per indicator.\n"
        "  This is better for big feeds.\n"
        "- fields: which record fields to check for a type, like {\"ip\": [\"client\"]}.\n"
        "- expired: true keeps expired indicators."))
    def to_detect_rules(line, bundle, options=None):
        b = bundle_arg(line, bundle, "stix.to_detect_rules")
        opts = {}
        if options is not None:
            check_type(line, options, "map", "stix.to_detect_rules", "options")
            unknown = [k for k in options if k not in ("level", "group", "fields", "expired")]
            if unknown:
                fail(line, f"stix.to_detect_rules() doesn't know the option {_tb.to_text(unknown[0], True)}. "
                           "Options: level, group, fields, expired.")
            opts = options
            if opts.get("level") is not None and str(opts["level"]).lower() not in (
                    "informational", "low", "medium", "high", "critical"):
                fail(line, "stix.to_detect_rules(): level should be informational, low, medium, high or critical.")
            if opts.get("group", "indicator") not in ("indicator", "type"):
                fail(line, "stix.to_detect_rules(): group should be \"indicator\" or \"type\".")
            if opts.get("fields") is not None:
                check_type(line, opts["fields"], "map", "stix.to_detect_rules", "fields")
        return to_rules(b, opts)

    @m.fn("bundle", 1, 2, doc=(
        "bundle(iocs, options?) -> map\n"
        "Write IOCs as a STIX 2.1 bundle of indicators. "
        "Ids are stable: the same IOC always gets the same indicator id.\n"
        "\n"
        "iocs can be:\n"
        "- a list of text. The kind is worked out: IP, domain, URL, email, hash, CIDR.\n"
        "- a list of {\"type\", \"value\", \"name\"?, \"description\"?} maps.\n"
        "- the map from ioc.extract().\n"
        "\n"
        "Options:\n"
        "- indicator_types: the STIX indicator types (default: [\"malicious-activity\"]).\n"
        "- description: text for every indicator.\n"
        "- confidence: 0-100.\n"
        "- valid_until: when the indicators expire.\n"
        "- time: the created / valid_from timestamp (default: now).\n"
        "- tags: attack.<tactic> tags become kill chain phases.\n"
        "\n"
        "Example:\n"
        "    let b = stix.bundle([\"198.51.100.7\", \"bad.example\"])\n"
        "    say(len(b.objects))"))
    def bundle_fn(line, iocs_value, options=None):
        check_type(line, iocs_value, "list|map", "stix.bundle", "iocs")
        opts = {}
        if options is not None:
            check_type(line, options, "map", "stix.bundle", "options")
            opts = options
        return make_bundle(ioc_items(line, iocs_value), opts)

    @m.fn("taxii", 1, 3, doc=(
        "taxii(url, auth?, options?) -> client\n"
        "A TAXII 2.1 client. url is the server's discovery address "
        "(like https://intel.example/taxii2/) or an API root.\n"
        "\n"
        "auth can be:\n"
        "- nil: no login.\n"
        "- text: a bearer token.\n"
        "- {\"user\", \"password\"}, {\"token\"} or {\"header\", \"key\"}.\n"
        "The auth is never printed.\n"
        "\n"
        "Methods:\n"
        "- discover(): gives back a map about the server.\n"
        "- collections(): a list of {\"id\", \"title\", \"description\", \"can_read\", \"can_write\"}.\n"
        "- objects(collection_id, added_after?): a bundle.\n"
        "  It follows the server's paging, up to max_pages pages.\n"
        "\n"
        "Options:\n"
        "- timeout: seconds to wait.\n"
        "- max_pages: the most pages objects() reads (default: 20)."))
    def taxii(line, url, auth=None, options=None):
        fn = "stix.taxii"
        from tblib.http import check_url
        url = check_url(line, url, fn)
        if not url.endswith("/"):
            url += "/"
        headers = auth_headers(line, auth, fn)
        opts = {}
        if options is not None:
            check_type(line, options, "map", fn, "options")
            opts = options
        timeout = opts.get("timeout", TIMEOUT)
        max_pages = opts.get("max_pages", MAX_PAGES)
        if not _tb.is_number(timeout) or timeout <= 0:
            fail(line, f"{fn}(): timeout should be a number of seconds.")
        if not _tb.is_whole(max_pages) or max_pages < 1:
            fail(line, f"{fn}(): max_pages should be a whole number of 1 or more.")
        cache = {}

        def get(line2, target, what):
            api.need_desktop(line2, "The stix TAXII client")
            status, data, text = taxii_get(line2, target, headers, timeout, what)
            return status, data, text

        def root(line2):
            if "root" not in cache:
                status, data, _ = get(line2, url, "discover")
                if status == 200 and isinstance(data, dict) and ("api_roots" in data or "default" in data):
                    chosen = data.get("default") or (data.get("api_roots") or [None])[0]
                    if not chosen:
                        fail(line2, "The TAXII server lists no API roots.")
                    chosen = urllib.parse.urljoin(url, chosen)
                    cache["root"] = chosen if chosen.endswith("/") else chosen + "/"
                else:
                    cache["root"] = url
            return cache["root"]

        def discover(line2):
            status, data, text = get(line2, url, "discover")
            if status != 200 or not isinstance(data, dict):
                fail(line2, f"The TAXII server answered HTTP {status} at {url}.", status=status)
            from tblib.json import from_json
            return from_json(data)

        def collections(line2):
            target = root(line2) + "collections/"
            status, data, _ = get(line2, target, "collections")
            if status != 200 or not isinstance(data, dict):
                fail(line2, f"The TAXII server answered HTTP {status} for its collections list.", status=status)
            out = []
            for c in data.get("collections") or []:
                if isinstance(c, dict):
                    out.append({"id": c.get("id"), "title": c.get("title") or "", "description": c.get("description") or "",
                                "can_read": bool(c.get("can_read")), "can_write": bool(c.get("can_write")),
                                "media_types": list(c.get("media_types") or [])})
            return out

        def objects(line2, collection_id, added_after=None):
            check_type(line2, collection_id, "text", "taxii.objects", "collection_id")
            if added_after is not None:
                check_type(line2, added_after, "text", "taxii.objects", "added_after")
            base = root(line2) + "collections/" + urllib.parse.quote(collection_id, safe="") + "/objects/"
            found, pages, next_token = [], 0, None
            while True:
                params = {}
                if added_after:
                    params["added_after"] = added_after
                if next_token:
                    params["next"] = next_token
                target = base + ("?" + urllib.parse.urlencode(params) if params else "")
                status, data, _ = get(line2, target, "objects")
                if status == 404:
                    fail(line2, f"The TAXII server has no collection '{collection_id}'.", status=404)
                if status != 200 or not isinstance(data, dict):
                    fail(line2, f"The TAXII server answered HTTP {status} for collection '{collection_id}'.", status=status)
                found.extend(o for o in data.get("objects") or [] if isinstance(o, dict))
                pages += 1
                next_token = data.get("next")
                if not data.get("more") or not next_token or pages >= max_pages:
                    break
            from tblib.json import from_json
            return {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": from_json(found),
                    "more": bool(next_token) and pages >= max_pages}

        client = {"url": url}
        client["discover"] = _tb.Builtin("taxii.discover", discover, (0, 0),
                                         "discover() -> map\nThe server's discovery document (title, api_roots, default).")
        client["collections"] = _tb.Builtin("taxii.collections", collections, (0, 0),
                                            "collections() -> list\nThe collections in the API root.")
        client["objects"] = _tb.Builtin("taxii.objects", objects, (1, 2),
                                        "objects(collection_id, added_after?) -> map\nThe collection's objects as a bundle.")
        return client

    return m
