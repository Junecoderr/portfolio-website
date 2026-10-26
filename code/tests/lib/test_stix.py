"""stix library: STIX 2.1 parsing, pattern -> IOC extraction, detect rules, writing bundles, and a
TAXII 2.1 client against a local server."""

import base64
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT, run_tb, tb_error
from tblib import stix

FEED = str(ROOT / "apps" / "data" / "intel" / "sample-feed.stix.json")


def run(source):
    return run_tb("import stix\n" + source)


@pytest.mark.parametrize("pattern,expected", [
    ("[ipv4-addr:value = '198.51.100.7']", [("ip", "198.51.100.7")]),
    ("[ipv6-addr:value = '2001:db8::1']", [("ip", "2001:db8::1")]),
    ("[ipv4-addr:value = '10.0.0.0/8']", [("cidr", "10.0.0.0/8")]),
    ("[domain-name:value = 'Bad.Example'] OR [url:value = 'http://bad.example/a?b=1']",
     [("domain", "bad.example"), ("url", "http://bad.example/a?b=1")]),
    ("[file:hashes.'SHA-256' = 'AB" + "0" * 62 + "' AND file:name = 'x.exe']", [("sha256", "ab" + "0" * 62), ("filename", "x.exe")]),
    ("[file:hashes.MD5 = 'd41d8cd98f00b204e9800998ecf8427e']", [("md5", "d41d8cd98f00b204e9800998ecf8427e")]),
    ("[email-addr:value = 'a@b.example']", [("email", "a@b.example")]),
    ("[network-traffic:dst_ref.value = '203.0.113.5']", [("ip", "203.0.113.5")]),
    ("[ipv4-addr:value IN ('1.2.3.4', '5.6.7.8')]", [("ip", "1.2.3.4"), ("ip", "5.6.7.8")]),
    ("[url:value = 'http://x.example/it\\'s']", [("url", "http://x.example/it's")]),
    ("[domain-name:value LIKE '%.example'] OR [ipv4-addr:value != '1.1.1.1']", []),
])
def test_pattern_iocs(pattern, expected):
    assert [(i["type"], i["value"]) for i in stix.pattern_iocs(pattern)["iocs"]] == expected


def test_indicators_from_sample_feed():
    out = run(f'let b = stix.read("{FEED}")\nlet i = stix.indicators(b)\nsay(len(i))\nsay(i[0].name, i[0].iocs, i[0].tags)\n'
              f'say(i[4].skipped)\nsay(len(stix.indicators(b, {{"expired": true}})))')
    assert out[0] == "5"
    assert out[1] == ('SSH password guessing source [{"type": "ip", "value": "203.0.113.42"}] '
                      '["attack.credential_access", "attack.t1110.001"]')
    assert "LIKE 'sqlmap%'" in out[2]
    assert out[3] == "6"


def test_iocs_include_observables():
    [out] = run(f'let x = stix.iocs(stix.read("{FEED}"))\nsay(x.ips, x.cidrs, x.domains, len(x.sha256))')
    assert out == '["203.0.113.42", "198.51.100.77", "198.51.100.23"] ["198.51.100.0/28"] ["login-verify.example.net"] 1'


def test_to_detect_rules_run_on_logs():
    src = f'''
import detect
import logs
import attack
let rules = stix.to_detect_rules(stix.read("{FEED}"))
say(len(rules), rules[0].level, rules[2].level)
let recs = logs.read("{ROOT}/apps/data/auth.log") + logs.read("{ROOT}/apps/data/firewall.log")
let alerts = detect.run(rules, recs)
say(alerts.map(fn(a) {{ return a.rule }}))
say(attack.map(alerts).techniques.map(fn(t) {{ return t.id }}))
let by_type = stix.to_detect_rules(stix.read("{FEED}"), {{"group": "type", "level": "critical"}})
say(by_type.map(fn(r) {{ return r.name }}))
say(detect.match(by_type[1], {{"src_ip": "198.51.100.23"}}), detect.match(by_type[1], {{"ip": "198.51.100.5"}}))
'''
    out = run(src)
    assert out[0] == "4 high medium"  # confidence 80 -> high, 40 -> medium
    assert out[1] == ('["Threat intel: SSH password guessing source", "Threat intel: Telnet scanning range", '
                      '"Threat intel: Web vulnerability scanner"]')
    assert out[2] == '["T1110.001", "T1595.002"]'
    assert out[3] == ('["Threat intel match: domain (1 IOCs)", "Threat intel match: ip (4 IOCs)", '
                      '"Threat intel match: sha256 (1 IOCs)", "Threat intel match: url (1 IOCs)"]')
    assert out[4] == "true true"


def test_rules_convert_to_siem_queries():
    out = run(f'import detect\nlet r = stix.to_detect_rules(stix.read("{FEED}"), {{"fields": {{"ip": ["src"]}}}})[0]\n'
              'say(detect.sigma_to_query(r, "splunk"))\nsay(detect.sigma_to_query(r, "kql"))')
    assert out[0] == 'src="203.0.113.42"'
    assert out[-1] == "Event\n| where src =~ \"203.0.113.42\""


def test_bundle_writes_stable_indicators(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = '''
import ioc
let opts = {"time": "2026-01-01T00:00:00.000Z", "confidence": 70, "tags": ["attack.command_and_control"]}
let b = stix.bundle(["203.0.113.9", "bad.example.org", "d41d8cd98f00b204e9800998ecf8427e", {"type": "cidr", "value": "10.0.0.0/8"}], opts)
say(b.objects.map(fn(o) { return o.pattern }))
say(b.objects[0].kill_chain_phases, b.objects[0].confidence)
let again = stix.bundle(["203.0.113.9"], opts)
say(again.objects[0].id == b.objects[0].id)
stix.write("out.json", b)
let back = stix.read("out.json")
say(stix.iocs(back).domains, len(stix.indicators(back)))
say(len(stix.bundle(ioc.extract("see hxxp://evil[.]example/x and 198.51.100.200")).objects))
say(stix.parse(stix.stringify(b)).id == b.id)
'''
    out = run(src)
    assert out[0] == ('["[ipv4-addr:value = \'203.0.113.9\']", "[domain-name:value = \'bad.example.org\']", '
                      '"[file:hashes.MD5 = \'d41d8cd98f00b204e9800998ecf8427e\']", "[ipv4-addr:value = \'10.0.0.0/8\']"]')
    assert out[1] == '[{"kill_chain_name": "mitre-attack", "phase_name": "command-and-control"}] 70'
    assert out[2:] == ["true", '["bad.example.org"] 4', "3", "true"]
    raw = json.loads((tmp_path / "out.json").read_text())
    assert raw["objects"][0]["spec_version"] == "2.1" and raw["objects"][0]["id"].startswith("indicator--")


def test_errors():
    assert "can't tell what kind of IOC 'hello'" in str(tb_error('import stix\nstix.bundle(["hello"])'))
    assert "needs JSON text" in str(tb_error('import stix\nstix.parse("{nope")'))
    assert "should be a map with" in str(tb_error('import stix\nstix.indicators({"a": 1})'))
    assert "doesn't know the option" in str(tb_error('import stix\nstix.to_detect_rules([], {"colour": 1})'))


# -- TAXII 2.1 against a local server ---------------------------------------------------------

OBJECTS = [
    {"type": "indicator", "spec_version": "2.1", "id": f"indicator--00000000-0000-4000-8000-00000000000{i}",
     "created": "2026-03-01T00:00:00.000Z", "modified": "2026-03-01T00:00:00.000Z", "name": f"ioc {i}",
     "pattern": f"[ipv4-addr:value = '192.0.2.{i}']", "pattern_type": "stix", "valid_from": "2026-03-01T00:00:00Z"}
    for i in range(1, 6)
]


class Taxii(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/taxii+json;version=2.1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.seen.append((self.path, dict(self.headers)))
        if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"tanisha:pw").decode():
            return self.send(401, {"title": "unauthorised"})
        parts = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/taxii2/":
            return self.send(200, {"title": "Local TAXII", "default": "/api1/", "api_roots": ["/api1/"]})
        if parts.path == "/api1/collections/":
            return self.send(200, {"collections": [{"id": "c1", "title": "Sample", "can_read": True, "can_write": False,
                                                    "media_types": ["application/stix+json;version=2.1"]}]})
        if parts.path == "/api1/collections/c1/objects/":
            start = int(query.get("next", "0"))
            page = OBJECTS[start:start + 2]
            more = start + 2 < len(OBJECTS)
            data = {"more": more, "objects": page}
            if more:
                data["next"] = str(start + 2)
            return self.send(200, data)
        return self.send(404, {"title": "not found"})


@pytest.fixture
def taxii_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Taxii)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_taxii_discovery_collections_and_paging(taxii_server):
    base = f"http://127.0.0.1:{taxii_server.server_address[1]}/taxii2/"
    src = f'''
let t = stix.taxii("{base}", {{"user": "tanisha", "password": "pw"}})
say(t.discover().title)
say(t.collections().map(fn(c) {{ return c.id + ":" + c.title }}))
let b = t.objects("c1", "2026-01-01T00:00:00Z")
say(len(b.objects), b.more)
say(stix.iocs(b).ips)
say(len(stix.taxii("{base}", {{"user": "tanisha", "password": "pw"}}, {{"max_pages": 2}}).objects("c1").objects))
'''
    out = run(src)
    assert out == ["Local TAXII", '["c1:Sample"]', "5 false",
                   '["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4", "192.0.2.5"]', "4"]
    paths = [p for p, _ in taxii_server.seen]
    assert "/api1/collections/c1/objects/?added_after=2026-01-01T00%3A00%3A00Z" in paths
    assert all(h.get("Accept") == "application/taxii+json;version=2.1" for _, h in taxii_server.seen)


def test_taxii_errors_hide_credentials(taxii_server):
    base = f"http://127.0.0.1:{taxii_server.server_address[1]}/taxii2/"
    err = str(tb_error(f'import stix\nstix.taxii("{base}", {{"user": "x", "password": "secret-pw"}}).collections()'))
    assert "refused access (HTTP 401)" in err and "secret-pw" not in err
    err = str(tb_error(f'import stix\nstix.taxii("{base}", {{"user": "tanisha", "password": "pw"}}).objects("zzz")'))
    assert "no collection 'zzz'" in err
    assert "needs a web address" in str(tb_error('import stix\nstix.taxii("ftp://x")'))
