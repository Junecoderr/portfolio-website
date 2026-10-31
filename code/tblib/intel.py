"""intel: threat-intelligence lookups (VirusTotal, AbuseIPDB, URLhaus)."""

from __future__ import annotations

import base64
import ipaddress
import json as _json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from tblib._api import Module, check_type, fail

import tb as _tb

TIMEOUT = 20  # seconds per lookup
USER_AGENT = "TB/3.0"

VT_BASE = "https://www.virustotal.com/api/v3"
ABUSEIPDB_URL = "https://api.abuseipdb.com/api/v2/check"
URLHAUS_URL = "https://urlhaus-api.abuse.ch/v1/url/"
OTX_BASE = "https://otx.alienvault.com/api/v1/indicators"
GREYNOISE_BASE = "https://api.greynoise.io/v3/community"
SHODAN_BASE = "https://api.shodan.io/shodan/host"

SERVICES = {
    # name: (label, env var, where to get a key, key required?)
    "virustotal": ("VirusTotal", "VT_API_KEY", "https://www.virustotal.com/gui/my-apikey", True),
    "abuseipdb": ("AbuseIPDB", "ABUSEIPDB_API_KEY", "https://www.abuseipdb.com/account/api", True),
    "urlhaus": ("URLhaus", "URLHAUS_API_KEY", "https://auth.abuse.ch/", False),
    "otx": ("AlienVault OTX", "OTX_API_KEY", "https://otx.alienvault.com/api", True),
    "greynoise": ("GreyNoise", "GREYNOISE_API_KEY", "https://viz.greynoise.io/account/api-key", False),
    "shodan": ("Shodan", "SHODAN_API_KEY", "https://account.shodan.io/", True),
}
SHODAN_BAD_TAGS = {"malware", "c2", "compromised"}

HASH_RE = re.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


def urlopen(request, timeout):
    """The one place intel talks to the internet (tests replace this). The API key is in the headers,
    so a redirect to another site (or from https to http) is refused rather than followed."""
    from tblib.http import open_url
    return open_url(request, timeout, same_site=True)


def vt_verdict(stats: dict) -> tuple[str, int]:
    """(verdict, score 0-100) from VirusTotal last_analysis_stats."""
    malicious = int(stats.get("malicious") or 0)
    suspicious = int(stats.get("suspicious") or 0)
    harmless = int(stats.get("harmless") or 0)
    undetected = int(stats.get("undetected") or 0)
    total = malicious + suspicious + harmless + undetected
    score = round(100 * (malicious + suspicious) / total) if total else 0
    if malicious >= 2:
        verdict = "malicious"
    elif malicious == 1 or suspicious >= 1:
        verdict = "suspicious"
    elif total:
        verdict = "harmless"
    else:
        verdict = "unknown"
    return verdict, score


def abuse_verdict(score: int) -> str:
    if score >= 75:
        return "malicious"
    if score >= 25:
        return "suspicious"
    return "harmless"


def load(api):
    m = Module(
        "intel",
        "Threat-intelligence lookups for IPs, domains, URLs and file hashes using VirusTotal, "
        "AbuseIPDB, URLhaus, AlienVault OTX, GreyNoise and Shodan. Every lookup gives back {\"source\", "
        "\"indicator\", \"verdict\" (\"malicious\", \"suspicious\", \"harmless\" or \"unknown\"), \"score\" "
        "(0-100, higher is worse), \"details\", \"raw\"}. API keys come from environment variables (VT_API_KEY, "
        "ABUSEIPDB_API_KEY, URLHAUS_API_KEY, OTX_API_KEY, GREYNOISE_API_KEY, SHODAN_API_KEY); the first three "
        "also take one as the last argument. TB never prints or stores them.",
    )

    def key_for(line, service, key, fn):
        label, env, where, required = SERVICES[service]
        if key is not None:
            check_type(line, key, "text", fn, "key")
            key = key.strip()
        if not key:
            key = (os.environ.get(env) or "").strip()
        if not key and required:
            article = "an" if label[0] in "AEIOU" else "a"
            fail(line, f"{fn}() needs {article} {label} API key. Get a free one at {where}, then set the "
                       f"environment variable {env} (or pass the key as the last argument).",
                 reason="no_key", service=service)
        return key or None

    def fetch(line, service, url, headers, fn, data=None, not_found_ok=False):
        """GET/POST and parse JSON. Gives (status, parsed). Friendly errors;
        never puts the key (which lives in headers) into a message."""
        label = SERVICES[service][0]
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
        try:
            resp = urlopen(req, TIMEOUT)
            status = resp.status if getattr(resp, "status", None) else resp.getcode()
        except urllib.error.HTTPError as err:
            resp, status = err, err.code
        except (urllib.error.URLError, OSError, ssl.SSLError) as err:
            from tblib.http import describe_error
            problem = describe_error(err, url)
            fail(line, f"{label} lookup failed. {problem.message}", reason=problem.reason, service=service)
        try:
            body = resp.read(20_000_000) or b""
        except OSError:
            body = b""
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if status == 429:
            fail(line, f"{label} says you've made too many lookups (rate limit). Wait a minute and try again; "
                       "free keys allow only a few lookups per minute.", reason="rate_limit", status=429,
                 service=service)
        if status in (401, 403):
            env = SERVICES[service][1]
            fail(line, f"{label} rejected the API key (HTTP {status}). Check the key in {env}.",
                 reason="bad_key", status=status, service=service)
        if status == 404 and not_found_ok:
            return 404, None
        try:
            parsed = _json.loads(body.decode("utf-8", errors="replace")) if body.strip() else None
        except ValueError:
            parsed = None
        if not 200 <= status < 300:
            fail(line, f"{label} answered with an error (HTTP {status}).", reason="status",
                 status=status, service=service)
        if parsed is None:
            fail(line, f"{label} sent back something that isn't JSON.", reason="bad_answer", service=service)
        from tblib.json import from_json
        return status, from_json(parsed)

    def result(source, indicator, verdict, score, details, raw):
        return {"source": source, "indicator": indicator, "verdict": verdict,
                "score": score, "details": details, "raw": raw}

    # -- VirusTotal ---------------------------------------------------------------

    def vt(line, kind, path, indicator, key, fn, extra):
        api.need_desktop(line, "The intel library")
        key = key_for(line, "virustotal", key, fn)
        url = f"{VT_BASE}/{path}"
        status, data = fetch(line, "virustotal", url, {"x-apikey": key}, fn, not_found_ok=True)
        if status == 404:
            return result("virustotal", indicator, "unknown", 0,
                          {"found": False, "note": "VirusTotal has no report for this yet."}, None)
        attrs = (data or {}).get("data", {}).get("attributes", {}) if isinstance(data, dict) else {}
        stats = attrs.get("last_analysis_stats") or {}
        verdict, score = vt_verdict(stats)
        details = {
            "found": True,
            "malicious": int(stats.get("malicious") or 0),
            "suspicious": int(stats.get("suspicious") or 0),
            "harmless": int(stats.get("harmless") or 0),
            "undetected": int(stats.get("undetected") or 0),
            "reputation": attrs.get("reputation"),
        }
        for name, field in extra.items():
            details[name] = attrs.get(field)
        details["link"] = f"https://www.virustotal.com/gui/{kind}/{urllib.parse.quote(_vt_gui_id(kind, indicator), safe='')}"
        return result("virustotal", indicator, verdict, score, details, data)

    @m.fn("virustotal_file", 1, 2, doc=(
        "virustotal_file(hash, key?) -> map\n"
        "Look up a file by its MD5, SHA-1 or SHA-256 hash on VirusTotal (it never uploads the file). "
        "details has malicious/suspicious/harmless/undetected engine counts, name, type and a link. "
        "An unknown hash gives verdict \"unknown\". Needs VT_API_KEY."))
    def virustotal_file(line, hash_, key=None):
        check_type(line, hash_, "text", "intel.virustotal_file", "hash")
        h = hash_.strip().lower()
        if not HASH_RE.match(h):
            fail(line, f"intel.virustotal_file() needs an MD5, SHA-1 or SHA-256 hash, but '{hash_}' isn't one.",
                 reason="invalid")
        return vt(line, "file", f"files/{h}", h, key, "intel.virustotal_file",
                  {"name": "meaningful_name", "type": "type_description", "size": "size"})

    @m.fn("virustotal_ip", 1, 2, doc=(
        "virustotal_ip(ip, key?) -> map\n"
        "Look up an IP address on VirusTotal. details has engine counts, country, owner (the network "
        "that holds it) and a link. Needs VT_API_KEY."))
    def virustotal_ip(line, ip, key=None):
        addr = ip_arg(line, ip, "intel.virustotal_ip")
        return vt(line, "ip-address", f"ip_addresses/{addr}", addr, key, "intel.virustotal_ip",
                  {"country": "country", "owner": "as_owner"})

    @m.fn("virustotal_domain", 1, 2, doc=(
        "virustotal_domain(domain, key?) -> map\n"
        "Look up a domain name like \"example.com\" on VirusTotal. details has engine counts, "
        "registrar and a link. Needs VT_API_KEY."))
    def virustotal_domain(line, domain, key=None):
        d = domain_arg(line, domain, "intel.virustotal_domain")
        return vt(line, "domain", f"domains/{d}", d, key, "intel.virustotal_domain",
                  {"registrar": "registrar"})

    @m.fn("virustotal_url", 1, 2, doc=(
        "virustotal_url(url, key?) -> map\n"
        "Look up a web address on VirusTotal (it only reads existing reports; it doesn't submit the URL "
        "for scanning). A URL nobody has scanned yet gives verdict \"unknown\". Needs VT_API_KEY."))
    def virustotal_url(line, url, key=None):
        u = url_arg(line, url, "intel.virustotal_url")
        url_id = base64.urlsafe_b64encode(u.encode("utf-8")).decode("ascii").rstrip("=")
        return vt(line, "url", f"urls/{url_id}", u, key, "intel.virustotal_url",
                  {"title": "title", "final_url": "last_final_url"})

    # -- AbuseIPDB ---------------------------------------------------------------

    @m.fn("abuseipdb", 1, 3, doc=(
        "abuseipdb(ip, key?, days?) -> map\n"
        "Check an IP address on AbuseIPDB: how many people reported it for attacks in the last days "
        "(default: 90). Needs ABUSEIPDB_API_KEY.\n"
        "\n"
        "score is AbuseIPDB's confidence of abuse (0-100):\n"
        "- 75 or more: \"malicious\".\n"
        "- 25 or more: \"suspicious\".\n"
        "\n"
        "details has reports, country, isp, usage, domain, whitelisted and last_reported."))
    def abuseipdb(line, ip, key=None, days=90):
        api.need_desktop(line, "The intel library")
        fn = "intel.abuseipdb"
        addr = ip_arg(line, ip, fn)
        days = check_type(line, days, "whole", fn, "days")
        if not 1 <= days <= 365:
            fail(line, f"{fn}() needs days from 1 to 365, not {days}.")
        key = key_for(line, "abuseipdb", key, fn)
        query = urllib.parse.urlencode({"ipAddress": addr, "maxAgeInDays": days})
        _status, data = fetch(line, "abuseipdb", f"{ABUSEIPDB_URL}?{query}", {"Key": key}, fn)
        info = data.get("data") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            fail(line, "AbuseIPDB sent back an answer I don't understand.", reason="bad_answer", service="abuseipdb")
        score = int(info.get("abuseConfidenceScore") or 0)
        details = {
            "reports": int(info.get("totalReports") or 0),
            "country": info.get("countryCode"),
            "isp": info.get("isp"),
            "usage": info.get("usageType"),
            "domain": info.get("domain"),
            "whitelisted": bool(info.get("isWhitelisted")),
            "last_reported": info.get("lastReportedAt"),
            "link": f"https://www.abuseipdb.com/check/{addr}",
        }
        return result("abuseipdb", addr, abuse_verdict(score), score, details, data)

    # -- URLhaus --------------------------------------------------------------------

    @m.fn("urlhaus_url", 1, 2, doc=(
        "urlhaus_url(url, key?) -> map\n"
        "Check a web address against URLhaus, abuse.ch's list of URLs that spread malware.\n"
        "\n"
        "Verdicts:\n"
        "- A listed URL is \"malicious\": score 100 while online, 80 once offline.\n"
        "- An unlisted one is \"unknown\", because URLhaus only knows bad URLs.\n"
        "\n"
        "details has listed, status, threat, tags and a link.\n"
        "\n"
        "abuse.ch may ask for a free Auth-Key: set URLHAUS_API_KEY (or pass it)."))
    def urlhaus_url(line, url, key=None):
        api.need_desktop(line, "The intel library")
        fn = "intel.urlhaus_url"
        u = url_arg(line, url, fn)
        key = key_for(line, "urlhaus", key, fn)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if key:
            headers["Auth-Key"] = key
        data = urllib.parse.urlencode({"url": u}).encode("ascii")
        try:
            _status, answer = fetch(line, "urlhaus", URLHAUS_URL, headers, fn, data=data)
        except _tb.TBError as err:
            if (err.fields or {}).get("reason") == "bad_key" and not key:
                fail(line, "URLhaus wants an Auth-Key now. Get a free one at https://auth.abuse.ch/ and set "
                           "the environment variable URLHAUS_API_KEY.", reason="no_key", service="urlhaus")
            raise
        status = answer.get("query_status") if isinstance(answer, dict) else None
        if status == "no_results":
            return result("urlhaus", u, "unknown", 0,
                          {"listed": False, "note": "URLhaus has no record of this URL."}, answer)
        if status != "ok":
            fail(line, f"URLhaus couldn't check that URL ({status or 'no answer'}).", reason="bad_answer",
                 service="urlhaus")
        online = answer.get("url_status") == "online"
        details = {
            "listed": True,
            "status": answer.get("url_status"),
            "threat": answer.get("threat"),
            "tags": list(answer.get("tags") or []),
            "date_added": answer.get("date_added"),
            "link": answer.get("urlhaus_reference"),
        }
        return result("urlhaus", u, "malicious", 100 if online else 80, details, answer)

    # -- AlienVault OTX, GreyNoise, Shodan (keys only from the environment) ---------------

    @m.fn("otx", 1, doc=(
        "otx(indicator) -> map\n"
        "Look up an IP, domain, URL or file hash on AlienVault OTX: in how many community pulses (threat "
        "reports) it appears.\n"
        "\n"
        "Verdicts:\n"
        "- 5 or more pulses: \"malicious\".\n"
        "- 1 to 4: \"suspicious\".\n"
        "- none: \"unknown\", because OTX only records reported things.\n"
        "\n"
        "score is 10 per pulse, up to 100. details has pulses, pulse_names, tags, country, asn, "
        "reputation and a link.\n"
        "\n"
        "The key comes only from the environment variable OTX_API_KEY."))
    def otx(line, indicator):
        api.need_desktop(line, "The intel library")
        fn = "intel.otx"
        check_type(line, indicator, "text", fn, "indicator")
        kind, value = indicator_kind(indicator.strip())
        if kind is None:
            fail(line, f"{fn}() needs an IP address, domain, URL or file hash, but '{indicator}' isn't one.",
                 reason="invalid")
        key = key_for(line, "otx", None, fn)
        url = f"{OTX_BASE}/{kind}/{urllib.parse.quote(value, safe='')}/general"
        status, data = fetch(line, "otx", url, {"X-OTX-API-KEY": key}, fn, not_found_ok=True)
        if status == 404 or not isinstance(data, dict):
            return result("otx", value, "unknown", 0, {"pulses": 0, "note": "OTX has nothing on this."}, data)
        info = data.get("pulse_info") if isinstance(data.get("pulse_info"), dict) else {}
        pulses = [p for p in info.get("pulses") or [] if isinstance(p, dict)]
        raw_count = info.get("count")
        if isinstance(raw_count, str) and raw_count.strip().isdigit():
            raw_count = int(raw_count.strip())
        # A damaged answer ("many", NaN, -1, a list) falls back to the pulses we can see.
        ok = isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool) and 0 <= raw_count < 1e12
        count = int(raw_count) if ok else len(pulses)
        tags = []
        for p in pulses:
            tags.extend(t for t in p.get("tags") or [] if isinstance(t, str) and t not in tags)
        verdict = "malicious" if count >= 5 else "suspicious" if count else "unknown"
        details = {
            "pulses": count, "pulse_names": [p.get("name") for p in pulses[:10]], "tags": tags[:20],
            "country": data.get("country_name"), "asn": data.get("asn"), "reputation": data.get("reputation"),
            "link": f"https://otx.alienvault.com/indicator/{OTX_LINK_KIND[kind]}/{urllib.parse.quote(value, safe='')}",
        }
        return result("otx", value, verdict, min(100, 10 * count), details, data)

    @m.fn("greynoise", 1, doc=(
        "greynoise(ip) -> map\n"
        "Ask GreyNoise (Community API) whether an IP address is internet background noise: mass scanners, "
        "crawlers and known business services.\n"
        "\n"
        "Verdicts:\n"
        "- classified malicious: \"malicious\", score 90.\n"
        "- a known benign service (riot) or classified benign: \"harmless\".\n"
        "- seen scanning but not classified: \"suspicious\", score 50.\n"
        "- never seen: \"unknown\".\n"
        "\n"
        "details has noise, riot, classification, name, last_seen and a link.\n"
        "\n"
        "Works without a key at a low rate; set GREYNOISE_API_KEY in the environment for more."))
    def greynoise(line, ip):
        api.need_desktop(line, "The intel library")
        fn = "intel.greynoise"
        addr = ip_arg(line, ip, fn)
        key = key_for(line, "greynoise", None, fn)
        status, data = fetch(line, "greynoise", f"{GREYNOISE_BASE}/{addr}", {"key": key} if key else {}, fn,
                             not_found_ok=True)
        if status == 404 or not isinstance(data, dict):
            return result("greynoise", addr, "unknown", 0,
                          {"noise": False, "riot": False, "note": "GreyNoise hasn't seen this IP."}, data)
        classification = str(data.get("classification") or "unknown").lower()
        if classification == "malicious":
            verdict, score = "malicious", 90
        elif classification == "benign" or data.get("riot"):
            verdict, score = "harmless", 0
        elif data.get("noise"):
            verdict, score = "suspicious", 50
        else:
            verdict, score = "unknown", 0
        details = {"noise": bool(data.get("noise")), "riot": bool(data.get("riot")), "classification": classification,
                   "name": data.get("name"), "last_seen": data.get("last_seen"),
                   "link": data.get("link") or f"https://viz.greynoise.io/ip/{addr}"}
        return result("greynoise", addr, verdict, score, details, data)

    @m.fn("shodan", 1, doc=(
        "shodan(ip) -> map\n"
        "What Shodan's internet scans found on an IP address: open ports, services, software and known "
        "vulnerabilities.\n"
        "\n"
        "Verdicts:\n"
        "- tagged malware, c2 or compromised: \"malicious\", score 80.\n"
        "- known vulnerabilities (CVEs): \"suspicious\", 10 per CVE up to 70.\n"
        "- otherwise \"unknown\": Shodan shows exposure, not reputation.\n"
        "\n"
        "details has ports, hostnames, org, isp, country, os, vulns, tags, last_update and a link.\n"
        "\n"
        "The key comes only from the environment variable SHODAN_API_KEY."))
    def shodan(line, ip):
        api.need_desktop(line, "The intel library")
        fn = "intel.shodan"
        addr = ip_arg(line, ip, fn)
        key = key_for(line, "shodan", None, fn)
        # Shodan only takes the key in the query string; fetch() never puts the URL in a message.
        url = f"{SHODAN_BASE}/{addr}?" + urllib.parse.urlencode({"key": key})
        status, data = fetch(line, "shodan", url, {}, fn, not_found_ok=True)
        if status == 404 or not isinstance(data, dict):
            return result("shodan", addr, "unknown", 0, {"found": False, "note": "Shodan has no scan of this IP."}, data)
        vulns = data.get("vulns")
        vulns = sorted(vulns) if isinstance(vulns, (list, dict)) else []
        tags = [t for t in data.get("tags") or [] if isinstance(t, str)]
        if SHODAN_BAD_TAGS & {t.lower() for t in tags}:
            verdict, score = "malicious", 80
        elif vulns:
            verdict, score = "suspicious", min(70, 10 * len(vulns))
        else:
            verdict, score = "unknown", 0
        details = {"found": True, "ports": sorted(p for p in data.get("ports") or [] if _tb.is_number(p)),
                   "hostnames": list(data.get("hostnames") or []), "org": data.get("org"), "isp": data.get("isp"),
                   "country": data.get("country_name"), "os": data.get("os"), "vulns": vulns, "tags": tags,
                   "last_update": data.get("last_update"), "link": f"https://www.shodan.io/host/{addr}"}
        return result("shodan", addr, verdict, score, details, data)

    def indicator_kind(text):
        """OTX's indicator section for a value: ('IPv4', '1.2.3.4'), ('file', hash) and so on."""
        try:
            addr = ipaddress.ip_address(text)
            return ("IPv4" if addr.version == 4 else "IPv6"), str(addr)
        except ValueError:
            pass
        if HASH_RE.match(text):
            return "file", text.lower()
        parts = urllib.parse.urlsplit(text)
        if parts.scheme.lower() in ("http", "https") and parts.netloc:
            return "url", text
        d = text.lower().rstrip(".")
        if DOMAIN_RE.match(d):
            return "domain", d
        return None, None

    # -- helpers ------------------------------------------------------------------------

    @m.fn("has_key", 1, doc=(
        "has_key(service) -> bool\n"
        "True if an API key for a service (\"virustotal\", \"abuseipdb\", \"urlhaus\", \"otx\", \"greynoise\" "
        "or \"shodan\") is set in the environment. "
        "(It never shows the key.)"))
    def has_key(line, service):
        check_type(line, service, "text", "intel.has_key", "service")
        name = service.strip().lower()
        if name not in SERVICES:
            fail(line, f"intel.has_key() doesn't know '{service}'. Use one of: {', '.join(SERVICES)}.")
        return bool((os.environ.get(SERVICES[name][1]) or "").strip())

    @m.fn("key_help", 0, doc=(
        "key_help() -> text\n"
        "How to get and set the API keys the intel library uses (good to print when a key is missing)."))
    def key_help(line):
        rows = []
        for label, env, where, required in SERVICES.values():
            need = "" if required else " (optional)"
            rows.append(f"  {label}{need}: get a key at {where}\n    then set {env}, for example:\n"
                        f"    mac/linux:  export {env}=\"your-key\"\n"
                        f"    windows:    setx {env} \"your-key\"   (then open a new terminal)")
        return "API keys for the intel library:\n" + "\n".join(rows)

    def ip_arg(line, value, fn):
        check_type(line, value, "text", fn, "ip")
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            fail(line, f"{fn}() needs a valid IP address, but '{value}' isn't one.", reason="invalid")

    def domain_arg(line, value, fn):
        check_type(line, value, "text", fn, "domain")
        d = value.strip().lower().rstrip(".")
        if not DOMAIN_RE.match(d):
            fail(line, f"{fn}() needs a domain name like \"example.com\", but '{value}' isn't one.", reason="invalid")
        return d

    def url_arg(line, value, fn):
        check_type(line, value, "text", fn, "url")
        u = value.strip()
        parts = urllib.parse.urlsplit(u)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            fail(line, f"{fn}() needs a web address starting with http:// or https://, but got '{value}'.",
                 reason="invalid")
        return u

    return m


OTX_LINK_KIND = {"IPv4": "ip", "IPv6": "ip", "domain": "domain", "url": "url", "file": "file"}


def _vt_gui_id(kind: str, indicator: str) -> str:
    if kind == "url":
        import hashlib
        return hashlib.sha256(indicator.encode("utf-8")).hexdigest()
    return indicator
