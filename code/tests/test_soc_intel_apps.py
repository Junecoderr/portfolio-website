"""The threat-intel apps (sigma-studio, ioc-feed, siem-forwarder) on the sample data, with local servers only."""

import json
import random
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import tb
from conftest import ROOT

APPS = ROOT / "apps"
DATA = APPS / "data"


def run_app(name, *args, env=None, monkeypatch=None):
    path = APPS / name / "main.tb"
    lines = []
    interp = tb.Interpreter(output=lines.append, input_fn=lambda prompt: "", rng=random.Random(0))
    try:
        tb.run(path.read_text(encoding="utf-8"), interpreter=interp, path=str(path), args=list(args))
    except tb.TBExit as stop:
        lines.append(f"<exit {stop.code}>")
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("TAXII_TOKEN", "TAXII_USER", "TAXII_PASSWORD", "SPLUNK_HEC_TOKEN", "ELASTIC_API_KEY",
                 "SENTINEL_WORKSPACE_ID", "SENTINEL_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


# -- sigma-studio ------------------------------------------------------------------------------

def test_sigma_studio_tour():
    out = run_app("sigma-studio")
    assert "4 rule(s) OK, 0 file(s) with problems." in out
    assert "ATT&CK T1110.001 Brute Force: Password Guessing" in out
    assert 'sourcetype="linux_secure" action="failed_login" | bin _time span=300s | stats count by _time, src | where count > 10' in out
    assert "| summarize count_ = count() by SrcIp, bin(TimeGenerated, 300s)" in out
    assert "[elastic] not converted" in out and "Queries written: 11." in out
    assert "SSH Brute Force From One Address              203.0.113.42     22" in out
    assert "Credential Access (TA0006): T1110.001" in out


def test_sigma_studio_validate_reports_broken_rules(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    shutil.copy(DATA / "sigma" / "ssh_brute_force.yml", rules)
    (rules / "bad.yml").write_text("title: bad\ndetection:\n  s:\n    a|sparkle: 1\n  condition: s\n")
    out = run_app("sigma-studio", "validate", str(rules))
    assert "BROKEN bad.yml" in out and "|sparkle" in out
    assert "1 rule(s) OK, 1 file(s) with problems." in out


def test_sigma_studio_convert_one_backend_and_run():
    out = run_app("sigma-studio", "convert", str(DATA / "sigma" / "win_new_admin_user.yml"), "--to", "kql")
    assert "SecurityEvent\n| where EventID == 4720 and not(TargetUserName endswith \"$\")" in out
    assert "[splunk]" not in out
    out = run_app("sigma-studio", "run", str(DATA / "sigma"), str(DATA / "auth.log"))
    assert "SSH Brute Force From One Address" in out and "Encoded PowerShell" not in out


def test_sigma_studio_usage_errors():
    assert run_app("sigma-studio", "convert", "--to", "qradar").endswith("<exit 2>")
    assert run_app("sigma-studio", "--to").endswith("<exit 2>")
    assert run_app("sigma-studio", "validate", "nope/").endswith("<exit 1>")


# -- ioc-feed -----------------------------------------------------------------------------------

def test_ioc_feed_sample():
    out = run_app("ioc-feed")
    assert "5 active indicator(s), 1 expired (ignored)" in out
    assert "(no exact IOC: network-traffic" in out
    assert "== 3 hit(s) in 254 record(s)" in out
    assert "high    Threat intel: SSH password guessing source" in out
    assert "T1110.001 Brute Force: Password Guessing" in out and "T1595.002" in out


def test_ioc_feed_by_type_with_queries():
    out = run_app("ioc-feed", "--by-type", "--query", "splunk", str(DATA / "auth.log"))
    assert "# Threat intel match: ip (4 IOCs)" in out
    assert 'ip="203.0.113.42" OR ip="198.51.100.77"' in out
    assert "cidrmatch(\"198.51.100.0/28\"" in out


def test_ioc_feed_usage_errors():
    assert run_app("ioc-feed", "--query", "qradar").endswith("<exit 2>")
    assert run_app("ioc-feed", "--taxii", "http://127.0.0.1:1/").endswith("<exit 2>")
    assert "I couldn't get the intel" in run_app("ioc-feed", "missing.json")


class Taxii(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.auth.append(self.headers.get("Authorization"))
        if self.path.startswith("/taxii2/"):
            data = {"title": "local", "api_roots": ["/api/"]}
        elif self.path == "/api/collections/":
            data = {"collections": [{"id": "feed-1", "title": "Sample feed", "can_read": True}]}
        elif self.path.startswith("/api/collections/feed-1/objects/"):
            data = {"objects": json.loads((DATA / "intel" / "sample-feed.stix.json").read_text())["objects"]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/taxii+json;version=2.1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_ioc_feed_from_local_taxii(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Taxii)
    srv.auth = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("TAXII_TOKEN", "secret-token-123")
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/taxii2/"
        out = run_app("ioc-feed", "--taxii", base, "--collection", "list")
        assert "feed-1  Sample feed" in out
        out = run_app("ioc-feed", "--taxii", base, "--collection", "feed-1", str(DATA / "auth.log"))
        assert "Downloading collection feed-1" in out and "Threat intel: SSH password guessing source" in out
        assert "secret-token-123" not in out
        assert set(srv.auth) == {"Bearer secret-token-123"}
    finally:
        srv.shutdown()
        srv.server_close()


# -- siem-forwarder -----------------------------------------------------------------------------

def test_forwarder_dry_run_cef_default():
    out = run_app("siem-forwarder", "--limit", "3")
    assert "(dry run: nothing is sent; add --send)" in out
    assert out.count("CEF:0|TB|detect|") == 3
    assert "# 101 event(s); forwarding the first 3" in out and "# 3 event(s) shown" in out


def test_forwarder_alerts_as_hec_hide_token(monkeypatch):
    monkeypatch.setenv("SPLUNK_HEC_TOKEN", "hec-secret")
    out = run_app("siem-forwarder", "--alerts", "--format", "hec")
    assert "POST https://splunk.example:8088/services/collector/event" in out
    body = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert {b["event"]["ip"] for b in body} == {"203.0.113.42"} and len(body) == 2
    assert "hec-secret" not in out


def test_forwarder_sends_syslog_over_udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5)
    port = sock.getsockname()[1]
    try:
        out = run_app("siem-forwarder", "--alerts", "--format", "syslog", "--send", "--to", f"127.0.0.1:{port}")
        assert f"Sent 2 syslog line(s) to 127.0.0.1:{port} over udp." in out
        first = sock.recv(4000).decode()
        assert first.startswith("<1") and "tb-forwarder" in first and "203.0.113.42" in first
    finally:
        sock.close()


def test_forwarder_follow_picks_up_new_lines(tmp_path):
    log = tmp_path / "live.log"
    log.write_text("Mar 12 08:00:00 web01 sshd[1]: Accepted password for tanisha from 192.0.2.10 port 1 ssh2\n")

    def add_line():
        time.sleep(0.4)
        with open(log, "a") as f:
            f.write("Mar 12 08:00:05 web01 sshd[2]: Failed password for root from 203.0.113.99 port 2 ssh2\n")

    t = threading.Thread(target=add_line)
    t.start()
    out = run_app("siem-forwarder", str(log), "--format", "leef", "--follow", "--polls", "2")
    t.join()
    assert "# following" in out
    assert out.count("LEEF:2.0|TB|detect|") == 2 and "203.0.113.99" in out
    assert "# 2 event(s) shown" in out


def test_forwarder_usage_errors():
    assert run_app("siem-forwarder", "--format", "xml").endswith("<exit 2>")
    assert run_app("siem-forwarder", "--format", "syslog", "--send").endswith("<exit 2>")
    assert run_app("siem-forwarder", "--format", "hec", "--send").endswith("<exit 2>")
    assert run_app("siem-forwarder", "--limit", "lots").endswith("<exit 2>")
    assert run_app("siem-forwarder", "missing.log").endswith("<exit 1>")
