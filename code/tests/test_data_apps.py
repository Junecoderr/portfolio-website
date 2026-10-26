"""The data-agent apps: dashboard (server + sqlite + template over log-triage's
alerts), dns-check (against an in-test DNS responder) and cert-check
(against a local TLS server). No internet needed."""

import http.client
import json
import random
import re
import threading
import time

import pytest

import tb
import tblib.server as serverlib
from conftest import ROOT

APPS = ROOT / "apps"


def run_app(name, *args):
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
    yield tmp_path
    serverlib.stop_all()


# -- dashboard ------------------------------------------------------------------


class Dashboard:
    """Run apps/dashboard/main.tb (it blocks in app.listen) in a thread."""

    def __init__(self, *args):
        self.lines = []
        self.error = None
        path = APPS / "dashboard" / "main.tb"

        def work():
            interp = tb.Interpreter(output=self.lines.append, input_fn=lambda p: "", rng=random.Random(0))
            try:
                tb.run(path.read_text(encoding="utf-8"), interpreter=interp, path=str(path), args=list(args))
            except BaseException as err:  # noqa: BLE001 - reported by the test
                self.error = err

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        self.port = None
        while time.time() < deadline and self.port is None:
            for line in list(self.lines):
                m = re.search(r"listening on http://127\.0\.0\.1:(\d+)", line)
                if m:
                    self.port = int(m.group(1))
            if self.error is not None:
                raise AssertionError(f"dashboard failed: {self.error}\n" + "\n".join(self.lines))
            time.sleep(0.05)
        assert self.port, "dashboard didn't start:\n" + "\n".join(self.lines)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode("utf-8")
        finally:
            conn.close()

    def stop(self):
        serverlib.stop_all()
        self.thread.join(10)
        assert not self.thread.is_alive()


def test_dashboard_serves_page_and_api():
    dash = Dashboard("--port", "0")
    try:
        assert any("auth.log:" in l and "new alerts" in l for l in dash.lines)
        status, headers, page = dash.request("GET", "/")
        assert status == 200 and headers["content-type"].startswith("text/html")
        assert "Alerts dashboard" in page and "Analyst: Tanisha" in page
        assert "Brute force" in page and "203.0.113.42" in page
        status, headers, body = dash.request("GET", "/api/alerts")
        assert status == 200 and headers["content-type"].startswith("application/json")
        alerts = json.loads(body)
        assert len(alerts) >= 5
        assert alerts[0]["level"] == "critical"          # most serious first
        assert {"id", "rule", "level", "source", "count", "file", "acked_by"} <= set(alerts[0])
        assert {a["file"] for a in alerts} >= {"auth.log", "access.log", "firewall.log"}

        high = json.loads(dash.request("GET", "/api/alerts?level=high")[2])
        assert high and all(a["level"] == "high" for a in high)
        summary = json.loads(dash.request("GET", "/api/summary")[2])
        assert summary["total"] == len(alerts) == summary["open"]

        first = alerts[0]["id"]
        status, _, body = dash.request("POST", f"/api/alerts/{first}/ack", body=json.dumps({"by": "Tanisha"}),
                                       headers={"Content-Type": "application/json"})
        assert status == 200 and json.loads(body)["acked_by"] == "Tanisha"
        assert json.loads(dash.request("GET", "/api/summary")[2])["open"] == len(alerts) - 1
        open_only = json.loads(dash.request("GET", "/api/alerts?open=true")[2])
        assert first not in [a["id"] for a in open_only]
        assert "Handled by Tanisha" in dash.request("GET", "/")[2]

        status, headers, _ = dash.request("POST", f"/alerts/{alerts[1]['id']}/ack", body="",
                                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert status == 303 and headers["location"] == "/"

        assert dash.request("GET", "/api/alerts/99999")[0] == 404
        assert dash.request("GET", "/api/alerts/abc")[0] == 404
        status, headers, css = dash.request("GET", "/assets/style.css")
        assert status == 200 and "text/css" in headers["content-type"]
        assert dash.request("GET", "/assets/../main.tb")[0] in (403, 404)
        assert any(" GET /api/alerts 200 " in l for l in dash.lines)   # request log
    finally:
        dash.stop()


def test_dashboard_page_escapes_log_content(tmp_path):
    log = tmp_path / "access.log"
    evil = '203.0.113.9 - - [12/Mar/2026:09:30:10 +0000] "GET /<script>alert(1)</script>/etc/passwd HTTP/1.1" 404 10 "-" "sqlmap"\n'
    log.write_text(evil * 3)
    dash = Dashboard(str(log), "--port", "0")
    try:
        page = dash.request("GET", "/")[2]
        assert "<script>alert" not in page
    finally:
        dash.stop()


def test_dashboard_keeps_alerts_in_a_database_file(tmp_path):
    db = tmp_path / "alerts.db"
    dash = Dashboard(str(APPS / "data" / "auth.log"), "--port", "0", "--db", str(db))
    try:
        assert any("new alerts" in l for l in dash.lines)
    finally:
        dash.stop()
    # A second run over the same log adds nothing new.
    again = Dashboard(str(APPS / "data" / "auth.log"), "--port", "0", "--db", str(db))
    try:
        assert any("0 new alerts" in l for l in again.lines)
    finally:
        again.stop()


def test_dashboard_bad_options():
    assert "Unknown option" in run_app("dashboard", "--nope")
    assert "--port needs a number" in run_app("dashboard", "--port", "abc")


# -- dns-check ------------------------------------------------------------------

from dns_fake import FakeDns  # noqa: E402

GOOD_ZONE = {
    ("tanisha.test", "A"): [("A", 300, "192.0.2.10")],
    ("tanisha.test", "AAAA"): [],
    ("tanisha.test", "MX"): [("MX", 3600, (10, "mail.tanisha.test"))],
    ("tanisha.test", "NS"): [("NS", 86400, "ns1.tanisha.test"), ("NS", 86400, "ns2.tanisha.test")],
    ("tanisha.test", "TXT"): [("TXT", 60, "v=spf1 mx -all")],
    ("tanisha.test", "CAA"): [("CAA", 60, (0, "issue", "letsencrypt.org"))],
    ("tanisha.test", "SOA"): [("SOA", 60, ("ns1.tanisha.test", "hostmaster.tanisha.test", 2026102601, 7200, 900, 1209600, 300))],
    ("_dmarc.tanisha.test", "TXT"): [("TXT", 60, "v=DMARC1; p=reject; rua=mailto:dmarc@tanisha.test")],
    ("10.2.0.192.in-addr.arpa", "PTR"): [("PTR", 60, "tanisha.test")],
}
WEAK_ZONE = {
    ("weak.test", "A"): [("A", 300, "192.0.2.20")],
    ("weak.test", "NS"): [("NS", 86400, "ns1.weak.test")],
    ("weak.test", "TXT"): [("TXT", 60, "v=spf1 +all")],
}


@pytest.fixture
def fake_dns():
    s = FakeDns({**GOOD_ZONE, **WEAK_ZONE}, nxdomain={"gone.test"})
    yield s
    s.close()


def test_dns_check_good_domain(fake_dns):
    out = run_app("dns-check", "tanisha.test", "--server", fake_dns.address)
    assert f"DNS check for tanisha.test (asking {fake_dns.address})" in out
    assert "MX    10 mail.tanisha.test" in out
    assert "DMARC v=DMARC1; p=reject" in out
    assert "PTR   192.0.2.10 -> tanisha.test" in out
    assert "7 of 7 checks passed." in out
    assert "<exit" not in out


def test_dns_check_weak_domain_flags_problems(fake_dns):
    out = run_app("dns-check", "weak.test", "--server", fake_dns.address)
    assert "[!!] Name servers" in out
    assert "[!!] SPF" in out and "doesn't end in -all" in out
    assert "[!!] DMARC" in out and "[!!] CAA" in out and "[!!] Mail servers" in out
    assert "[!!] Reverse DNS" in out and "no PTR for 192.0.2.20" in out
    assert "1 of 7 checks passed." in out


def test_dns_check_json_and_errors(fake_dns):
    out = run_app("dns-check", "tanisha.test", "--server", fake_dns.address, "--json")
    data = json.loads(out.rsplit("\n<exit", 1)[0])
    assert data["domain"] == "tanisha.test" and all(c["ok"] for c in data["checks"])
    assert data["records"]["A"][0]["value"] == "192.0.2.10"
    assert "doesn't exist (NXDOMAIN)" in run_app("dns-check", "gone.test", "--server", fake_dns.address)
    assert "<exit 2>" in run_app("dns-check")
    assert "doesn't look like a domain name" in run_app("dns-check", "not a name!")


# -- cert-check -----------------------------------------------------------------


@pytest.fixture
def tls_port(tmp_path):
    import socket
    import ssl
    from tls_fixture import server_context, write_pair

    ctx = server_context(str(tmp_path))
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw.bind(("127.0.0.1", 0))
    raw.listen(8)
    raw.settimeout(0.2)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = raw.accept()
            except OSError:
                continue
            try:
                s = ctx.wrap_socket(conn, server_side=True)
                s.settimeout(1)
                try:
                    s.recv(1)
                except OSError:
                    pass
                s.close()
            except (ssl.SSLError, OSError):
                conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cert, _ = write_pair(str(tmp_path))
    yield raw.getsockname()[1], cert
    stop.set()
    t.join(2)
    raw.close()


def test_cert_check_untrusted_self_signed(tls_port):
    port, _ = tls_port
    out = run_app("cert-check", f"localhost:{port}")
    assert f"localhost:{port}  ISSUES" in out
    assert "subject   localhost" in out and "self-signed true" in out
    assert "isn't trusted" in out
    assert "0 of 1 servers OK." in out and out.endswith("<exit 1>")


def test_cert_check_trusted_with_ca_file_and_versions(tls_port):
    port, ca = tls_port
    out = run_app("cert-check", f"localhost:{port}", "--ca-file", ca, "--versions")
    assert f"localhost:{port}  OK" in out and "1 of 1 servers OK." in out
    assert "accepts   " in out and ("TLSv1.2" in out or "TLSv1.3" in out)
    assert "<exit" not in out


def test_cert_check_file_warn_json_and_unreachable(tls_port, tmp_path):
    import socket
    port, ca = tls_port
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    hosts = tmp_path / "hosts.txt"
    hosts.write_text(f"# servers\nlocalhost:{port}\n127.0.0.1:{dead}\n")
    out = run_app("cert-check", "--file", str(hosts), "--ca-file", ca, "--warn", "100000", "--json")
    data = json.loads(out.rsplit("\n<exit", 1)[0])
    assert data[0]["ok"] is False and any("expires in" in i for i in data[0]["issues"])
    assert data[1]["ok"] is False and data[1]["error"]
    assert out.endswith("<exit 1>")
    assert "<exit 2>" in run_app("cert-check")
    assert "--warn needs a number" in run_app("cert-check", "x", "--warn", "soon")
