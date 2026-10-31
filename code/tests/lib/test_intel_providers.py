"""intel: AlienVault OTX, GreyNoise and Shodan against a local HTTP server that speaks
their documented REST APIs. Keys come only from the environment and never show up in output."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import tblib.intel as intel
from conftest import run_tb, tb_error

OTX_KEY, GN_KEY, SHODAN_KEY = "otx-secret-111", "gn-secret-222", "shodan-secret-333"

ANSWERS = {
    "/otx/IPv4/203.0.113.42/general": (200, {
        "indicator": "203.0.113.42", "reputation": 3, "country_name": "Netherlands", "asn": "AS64500 Example",
        "pulse_info": {"count": 7, "pulses": [{"name": f"SSH scanners {i}", "tags": ["ssh", "bruteforce"]} for i in range(7)]}}),
    "/otx/domain/example.com/general": (200, {"indicator": "example.com", "pulse_info": {"count": 0, "pulses": []}}),
    "/otx/file/44d88612fea8a8f36de82e1278abb02f/general": (200, {"pulse_info": {"count": 2, "pulses": [
        {"name": "EICAR", "tags": ["test"]}, {"name": "AV test", "tags": ["test", "eicar"]}]}}),
    "/otx/url/http%3A%2F%2Fbad.example%2Fx/general": (404, {"detail": "not found"}),
    "/gn/203.0.113.42": (200, {"ip": "203.0.113.42", "noise": True, "riot": False, "classification": "malicious",
                               "name": "unknown", "link": "https://viz.greynoise.io/ip/203.0.113.42",
                               "last_seen": "2026-10-30", "message": "Success"}),
    "/gn/8.8.8.8": (200, {"ip": "8.8.8.8", "noise": False, "riot": True, "classification": "benign",
                          "name": "Google Public DNS", "last_seen": "2026-10-30"}),
    "/gn/198.51.100.7": (200, {"ip": "198.51.100.7", "noise": True, "riot": False, "classification": "unknown"}),
    "/gn/192.0.2.1": (404, {"ip": "192.0.2.1", "noise": False, "riot": False,
                            "message": "IP not observed scanning the internet or contained in RIOT data set."}),
    "/shodan/198.51.100.14": (200, {"ip_str": "198.51.100.14", "ports": [443, 22, 80], "hostnames": ["web.example"],
                                    "org": "Example Hosting", "isp": "Example ISP", "country_name": "Germany", "os": None,
                                    "vulns": ["CVE-2024-6387", "CVE-2023-38408"], "tags": ["cloud"],
                                    "last_update": "2026-10-29T01:02:03"}),
    "/shodan/203.0.113.9": (200, {"ip_str": "203.0.113.9", "ports": [8080], "tags": ["c2"]}),
    "/shodan/192.0.2.1": (404, {"error": "No information available for that IP."}),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        self.server.seen.append({"path": path, "query": query, "headers": {k.lower(): v for k, v in self.headers.items()}})
        if self.headers.get("X-OTX-API-KEY") == "wrong" or "key=wrong" in query:
            status, reply = 403, {"detail": "Authentication failed"}
        elif path.startswith("/limited"):
            status, reply = 429, {"message": "slow down"}
        else:
            status, reply = ANSWERS.get(path, (500, {"error": "unexpected"}))
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(intel, "OTX_BASE", base + "/otx")
    monkeypatch.setattr(intel, "GREYNOISE_BASE", base + "/gn")
    monkeypatch.setattr(intel, "SHODAN_BASE", base + "/shodan")
    for env in ("OTX_API_KEY", "GREYNOISE_API_KEY", "SHODAN_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    srv.base = base
    yield srv
    srv.shutdown()
    srv.server_close()


def run(src):
    return run_tb("import intel\n" + src)


def test_otx_kinds_and_verdicts(server, monkeypatch):
    monkeypatch.setenv("OTX_API_KEY", OTX_KEY)
    out = run('let r = intel.otx("203.0.113.42")\nsay(r.source, r.verdict, r.score, r.details.pulses, r.details.tags)\n'
              'say(r.details.country, r.details.asn, len(r.details.pulse_names), r.details.link)\n'
              'say(intel.otx("Example.COM").verdict, intel.otx("44D88612FEA8A8F36DE82E1278ABB02F").verdict)\n'
              'say(intel.otx("http://bad.example/x").verdict)')
    assert out == ['otx malicious 70 7 ["ssh", "bruteforce"]',
                   "Netherlands AS64500 Example 7 https://otx.alienvault.com/indicator/ip/203.0.113.42",
                   "unknown suspicious", "unknown"]
    assert [s["path"] for s in server.seen][:2] == ["/otx/IPv4/203.0.113.42/general", "/otx/domain/example.com/general"]
    assert all(s["headers"]["x-otx-api-key"] == OTX_KEY for s in server.seen)


def test_greynoise_classifications(server):
    out = run('for ip in ["203.0.113.42", "8.8.8.8", "198.51.100.7", "192.0.2.1"] {\n'
              '  let r = intel.greynoise(ip)\n  say(r.indicator, r.verdict, r.score, r.details.noise, r.details.riot)\n}')
    assert out == ["203.0.113.42 malicious 90 true false", "8.8.8.8 harmless 0 false true",
                   "198.51.100.7 suspicious 50 true false", "192.0.2.1 unknown 0 false false"]
    assert "key" not in server.seen[0]["headers"]  # no key set: the community API without one


def test_greynoise_sends_env_key(server, monkeypatch):
    monkeypatch.setenv("GREYNOISE_API_KEY", GN_KEY)
    assert run('say(intel.greynoise("8.8.8.8").details.name)') == ["Google Public DNS"]
    assert server.seen[-1]["headers"]["key"] == GN_KEY


def test_shodan(server, monkeypatch):
    monkeypatch.setenv("SHODAN_API_KEY", SHODAN_KEY)
    out = run('let r = intel.shodan("198.51.100.14")\nsay(r.verdict, r.score, r.details.ports, r.details.vulns)\n'
              'say(r.details.org, r.details.country, r.details.link)\n'
              'say(intel.shodan("203.0.113.9").verdict, intel.shodan("192.0.2.1").verdict)')
    assert out == ['suspicious 20 [22, 80, 443] ["CVE-2023-38408", "CVE-2024-6387"]',
                   "Example Hosting Germany https://www.shodan.io/host/198.51.100.14", "malicious unknown"]
    assert server.seen[0]["query"] == f"key={SHODAN_KEY}"


def test_keys_only_from_the_environment(server):
    for src, env in (('intel.otx("1.2.3.4")', "OTX_API_KEY"), ('intel.shodan("1.2.3.4")', "SHODAN_API_KEY")):
        err = tb_error("import intel\n" + src)
        assert env in str(err) and err.fields["reason"] == "no_key"
    assert server.seen == []
    # no key argument: these functions take exactly one input
    assert "intel.shodan" in str(tb_error('import intel\nintel.shodan("1.2.3.4", "k")'))
    assert run('say(intel.has_key("otx"), intel.has_key("greynoise"), intel.has_key("shodan"))') == ["false false false"]
    assert "OTX_API_KEY" in "\n".join(run("say(intel.key_help())"))


def test_bad_key_and_rate_limit_never_show_the_key(server, monkeypatch):
    monkeypatch.setenv("SHODAN_API_KEY", "wrong")
    err = tb_error('import intel\nintel.shodan("198.51.100.14")')
    assert "Shodan rejected the API key (HTTP 403)" in str(err) and "key=" not in str(err)
    monkeypatch.setenv("OTX_API_KEY", OTX_KEY)
    monkeypatch.setattr(intel, "OTX_BASE", server.base + "/limited")
    err = tb_error('import intel\nintel.otx("203.0.113.42")')
    assert err.fields["reason"] == "rate_limit" and OTX_KEY not in str(err)


def test_bad_indicators_and_unreachable(server, monkeypatch):
    monkeypatch.setenv("OTX_API_KEY", OTX_KEY)
    assert "needs an IP address, domain, URL or file hash" in str(tb_error('import intel\nintel.otx("not valid!")'))
    assert "needs a valid IP address" in str(tb_error('import intel\nintel.greynoise("1.2.3")'))
    assert server.seen == []
    monkeypatch.setattr(intel, "GREYNOISE_BASE", "http://127.0.0.1:9/gn")
    err = tb_error('import intel\nintel.greynoise("8.8.8.8")')
    assert "GreyNoise lookup failed" in str(err)
