"""intel library with a fake urlopen: request URLs/headers, normalised
results, missing keys, rate limits and 404s. No internet."""

import base64
import io
import json
import socket
import urllib.error
import urllib.parse

import pytest

import tblib.intel as intel
from conftest import run_tb, tb_error

VT_KEY = "vt-secret-key-123"
ABUSE_KEY = "abuse-secret-key-456"
HASH = "44d88612fea8a8f36de82e1278abb02f"


class FakeResponse(io.BytesIO):
    def __init__(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        super().__init__(body)
        self.status = status

    def getcode(self):
        return self.status


class FakeNet:
    """Records requests; answers from a list of (status, payload)."""

    def __init__(self, monkeypatch):
        self.requests = []
        self.answers = []
        monkeypatch.setattr(intel, "urlopen", self)

    def answer(self, status, payload):
        self.answers.append((status, payload))
        return self

    def __call__(self, request, timeout):
        self.requests.append(request)
        status, payload = self.answers.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        if status >= 400:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            raise urllib.error.HTTPError(request.full_url, status, "Error", {}, io.BytesIO(body))
        return FakeResponse(status, payload)

    @property
    def last(self):
        return self.requests[-1]

    def header(self, name):
        return self.last.get_header(name.capitalize())


@pytest.fixture
def fake(monkeypatch):
    for env in ("VT_API_KEY", "ABUSEIPDB_API_KEY", "URLHAUS_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return FakeNet(monkeypatch)


def vt_report(malicious=0, suspicious=0, harmless=60, undetected=10, **attrs):
    attributes = {"last_analysis_stats": {"malicious": malicious, "suspicious": suspicious,
                                          "harmless": harmless, "undetected": undetected, "timeout": 0},
                  "reputation": -5}
    attributes.update(attrs)
    return {"data": {"id": "x", "type": "t", "attributes": attributes}}


# -- VirusTotal ----------------------------------------------------------------

def test_vt_file_malicious(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(200, vt_report(malicious=50, harmless=0, undetected=20, meaningful_name="eicar.com",
                               type_description="DOS COM", size=68))
    src = f'import intel\nlet r = intel.virustotal_file("{HASH.upper()}")\nsay(r.source, r.indicator, r.verdict, r.score)\nsay(r.details.malicious, r.details.name, r.details.type, r.details.size)\nsay(r.details.link, type(r.raw))'
    assert run_tb(src) == [
        f"virustotal {HASH} malicious 71",
        "50 eicar.com DOS COM 68",
        f"https://www.virustotal.com/gui/file/{HASH} map",
    ]
    assert fake.last.full_url == f"https://www.virustotal.com/api/v3/files/{HASH}"
    assert fake.last.get_method() == "GET"
    assert fake.header("x-apikey") == VT_KEY
    assert fake.header("user-agent") == "TB/2.0"


def test_vt_verdicts():
    assert intel.vt_verdict({"malicious": 2}) == ("malicious", 100)
    assert intel.vt_verdict({"malicious": 1, "harmless": 9}) == ("suspicious", 10)
    assert intel.vt_verdict({"suspicious": 1, "harmless": 3}) == ("suspicious", 25)
    assert intel.vt_verdict({"harmless": 60, "undetected": 10}) == ("harmless", 0)
    assert intel.vt_verdict({}) == ("unknown", 0)


def test_vt_ip_with_key_argument(fake):
    fake.answer(200, vt_report(country="NL", as_owner="Example Hosting"))
    src = f'import intel\nlet r = intel.virustotal_ip("192.0.2.5", "{VT_KEY}")\nsay(r.verdict, r.score, r.details.country, r.details.owner, r.details.reputation)'
    assert run_tb(src) == ["harmless 0 NL Example Hosting -5"]
    assert fake.last.full_url == "https://www.virustotal.com/api/v3/ip_addresses/192.0.2.5"
    assert fake.header("x-apikey") == VT_KEY


def test_vt_domain(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(200, vt_report(suspicious=2, registrar="Reg Inc"))
    assert run_tb('import intel\nlet r = intel.virustotal_domain("Example.COM.")\nsay(r.indicator, r.verdict, r.details.registrar)') == [
        "example.com suspicious Reg Inc"
    ]
    assert fake.last.full_url == "https://www.virustotal.com/api/v3/domains/example.com"


def test_vt_url_uses_unpadded_base64_id(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    url = "http://malware.example.test/a?b=c"
    fake.answer(200, vt_report(malicious=3))
    assert run_tb(f'import intel\nsay(intel.virustotal_url("{url}").verdict)') == ["malicious"]
    expected = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    assert fake.last.full_url == f"https://www.virustotal.com/api/v3/urls/{expected}"


def test_vt_404_is_unknown(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(404, {"error": {"code": "NotFoundError"}})
    src = f'import intel\nlet r = intel.virustotal_file("{HASH}")\nsay(r.verdict, r.score, r.details.found, r.raw)'
    assert run_tb(src) == ["unknown 0 false nil"]


def test_vt_missing_key(fake):
    err = tb_error(f'import intel\nintel.virustotal_file("{HASH}")')
    assert "needs a VirusTotal API key" in str(err) and "VT_API_KEY" in str(err)
    assert err.fields == {"reason": "no_key", "service": "virustotal"}
    assert fake.requests == []


def test_rate_limit(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(429, {"error": {"code": "QuotaExceededError"}})
    err = tb_error('import intel\nintel.virustotal_ip("192.0.2.5")')
    assert "too many lookups (rate limit)" in str(err)
    assert err.fields["reason"] == "rate_limit" and err.fields["status"] == 429
    assert VT_KEY not in str(err) and VT_KEY not in repr(err.fields)


def test_bad_key_never_shows_the_key(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(401, {"error": {"code": "WrongCredentialsError"}})
    err = tb_error('import intel\nintel.virustotal_ip("192.0.2.5")')
    assert "rejected the API key (HTTP 401). Check the key in VT_API_KEY." in str(err)
    assert VT_KEY not in str(err)


def test_server_error_and_bad_json(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(500, b"oops")
    assert "VirusTotal answered with an error (HTTP 500)." in str(tb_error('import intel\nintel.virustotal_ip("192.0.2.5")'))
    fake.answer(200, b"<html>")
    assert "isn't JSON" in str(tb_error('import intel\nintel.virustotal_ip("192.0.2.5")'))


def test_network_errors_are_friendly(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    fake.answer(0, urllib.error.URLError(socket.gaierror(8, "nodename nor servname")))
    err = tb_error('import intel\nintel.virustotal_ip("192.0.2.5")')
    assert "VirusTotal lookup failed. I couldn't find the website 'www.virustotal.com'" in str(err)
    assert err.fields["reason"] == "dns"
    fake.answer(0, urllib.error.URLError(TimeoutError("timed out")))
    assert tb_error('import intel\nintel.virustotal_ip("192.0.2.5")').fields["reason"] == "timeout"


def test_errors_are_catchable(fake):
    src = 'import intel\ntry { intel.abuseipdb("192.0.2.5") } catch err { say(err.reason, err.service) }'
    assert run_tb(src) == ["no_key abuseipdb"]


# -- AbuseIPDB -----------------------------------------------------------------

def abuse_answer(score, reports=3):
    return {"data": {"ipAddress": "192.0.2.5", "abuseConfidenceScore": score, "totalReports": reports,
                     "countryCode": "US", "isp": "Example ISP", "usageType": "Data Center",
                     "domain": "example.test", "isWhitelisted": False,
                     "lastReportedAt": "2026-10-01T10:00:00+00:00"}}


def test_abuseipdb(fake, monkeypatch):
    monkeypatch.setenv("ABUSEIPDB_API_KEY", ABUSE_KEY)
    fake.answer(200, abuse_answer(100, 42))
    src = 'import intel\nlet r = intel.abuseipdb("192.0.2.5")\nsay(r.source, r.verdict, r.score)\nsay(r.details.reports, r.details.country, r.details.isp, r.details.usage, r.details.whitelisted, r.details.link)'
    assert run_tb(src) == [
        "abuseipdb malicious 100",
        "42 US Example ISP Data Center false https://www.abuseipdb.com/check/192.0.2.5",
    ]
    parts = urllib.parse.urlsplit(fake.last.full_url)
    assert parts.netloc == "api.abuseipdb.com" and parts.path == "/api/v2/check"
    assert urllib.parse.parse_qs(parts.query) == {"ipAddress": ["192.0.2.5"], "maxAgeInDays": ["90"]}
    assert fake.header("key") == ABUSE_KEY and fake.header("accept") == "application/json"
    assert ABUSE_KEY not in fake.last.full_url


def test_abuseipdb_levels_and_days(fake):
    fake.answer(200, abuse_answer(30)).answer(200, abuse_answer(0, 0))
    src = f'import intel\nsay(intel.abuseipdb("192.0.2.5", "{ABUSE_KEY}", 30).verdict, intel.abuseipdb("192.0.2.5", "{ABUSE_KEY}").verdict)'
    assert run_tb(src) == ["suspicious harmless"]
    assert "maxAgeInDays=30" in fake.requests[0].full_url
    assert intel.abuse_verdict(75) == "malicious" and intel.abuse_verdict(24) == "harmless"


def test_abuseipdb_missing_key_and_bad_ip(fake):
    assert "needs an AbuseIPDB API key" in str(tb_error('import intel\nintel.abuseipdb("192.0.2.5")'))
    assert "needs a valid IP address, but 'nope' isn't one." in str(tb_error('import intel\nintel.abuseipdb("nope", "k")'))
    assert "needs days from 1 to 365" in str(tb_error('import intel\nintel.abuseipdb("192.0.2.5", "k", 0)'))


def test_abuseipdb_rate_limit(fake):
    fake.answer(429, {"errors": [{"detail": "Daily rate limit exceeded"}]})
    err = tb_error(f'import intel\nintel.abuseipdb("192.0.2.5", "{ABUSE_KEY}")')
    assert "AbuseIPDB says you've made too many lookups" in str(err) and ABUSE_KEY not in str(err)


# -- URLhaus ---------------------------------------------------------------------

def test_urlhaus_listed_online(fake):
    fake.answer(200, {"query_status": "ok", "url_status": "online", "threat": "malware_download",
                      "tags": ["elf", "mirai"], "date_added": "2026-10-01 10:00:00 UTC",
                      "urlhaus_reference": "https://urlhaus.abuse.ch/url/1/"})
    src = 'import intel\nlet r = intel.urlhaus_url("http://bad.example.test/x.sh")\nsay(r.verdict, r.score, r.details.status, r.details.threat, r.details.tags, r.details.link)'
    assert run_tb(src) == ['malicious 100 online malware_download ["elf", "mirai"] https://urlhaus.abuse.ch/url/1/']
    assert fake.last.full_url == "https://urlhaus-api.abuse.ch/v1/url/"
    assert fake.last.get_method() == "POST"
    assert urllib.parse.parse_qs(fake.last.data.decode()) == {"url": ["http://bad.example.test/x.sh"]}
    assert fake.header("auth-key") is None


def test_urlhaus_offline_and_no_results(fake, monkeypatch):
    monkeypatch.setenv("URLHAUS_API_KEY", "uh-key")
    fake.answer(200, {"query_status": "ok", "url_status": "offline"}).answer(200, {"query_status": "no_results"})
    src = 'import intel\nlet a = intel.urlhaus_url("http://a.test/")\nlet b = intel.urlhaus_url("https://b.test/")\nsay(a.verdict, a.score, b.verdict, b.score, b.details.listed)'
    assert run_tb(src) == ["malicious 80 unknown 0 false"]
    assert fake.requests[0].get_header("Auth-key") == "uh-key"


def test_urlhaus_needs_key_now(fake):
    fake.answer(401, b"Unauthorized")
    err = tb_error('import intel\nintel.urlhaus_url("http://a.test/")')
    assert "URLhaus wants an Auth-Key now" in str(err) and "URLHAUS_API_KEY" in str(err)
    assert err.fields["reason"] == "no_key"


def test_urlhaus_invalid_answer(fake):
    fake.answer(200, {"query_status": "invalid_url"})
    assert "URLhaus couldn't check that URL (invalid_url)." in str(tb_error('import intel\nintel.urlhaus_url("http://a.test/")'))


# -- helpers and validation ----------------------------------------------------------

def test_has_key_and_key_help(fake, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", VT_KEY)
    assert run_tb('import intel\nsay(intel.has_key("virustotal"), intel.has_key("AbuseIPDB"), intel.has_key("urlhaus"))') == ["true false false"]
    help_text = "\n".join(run_tb("import intel\nsay(intel.key_help())"))
    assert "VT_API_KEY" in help_text and "ABUSEIPDB_API_KEY" in help_text and "URLHAUS_API_KEY" in help_text
    assert VT_KEY not in help_text
    assert "doesn't know 'shodan'" in str(tb_error('import intel\nintel.has_key("shodan")'))


@pytest.mark.parametrize("src,message", [
    ('intel.virustotal_file("abc", "k")', "needs an MD5, SHA-1 or SHA-256 hash, but 'abc' isn't one."),
    ('intel.virustotal_ip("1.2.3", "k")', "needs a valid IP address"),
    ('intel.virustotal_domain("not a domain", "k")', "needs a domain name like \"example.com\""),
    ('intel.virustotal_url("example.com", "k")', "needs a web address starting with http:// or https://"),
    ('intel.urlhaus_url(5)', "needs url to be text"),
])
def test_bad_indicators(fake, src, message):
    assert message in str(tb_error("import intel\n" + src))
    assert fake.requests == []


def test_browser_needs_desktop(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert "The intel library needs the TB app on your computer" in str(tb_error('import intel\nintel.abuseipdb("192.0.2.5", "k")'))
