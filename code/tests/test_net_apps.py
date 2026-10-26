"""The networking apps in apps/: ip-reputation, port-check, uptime-monitor.
Local servers and a fake urlopen only; no internet."""

import io
import json
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import tb
import tblib.intel as intel
from conftest import ROOT

APPS = ROOT / "apps"


def run_app(name, args, inputs=None):
    path = APPS / name / "main.tb"
    lines = []
    code = 0
    try:
        tb.run(path.read_text(encoding="utf-8"), output=lines.append, input_fn=lambda p: "",
               path=str(path), args=args)
    except tb.TBExit as stop:
        code = stop.code if isinstance(stop.code, int) else 0
    return lines, code


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_apps_have_readmes():
    for name in ("ip-reputation", "port-check", "uptime-monitor"):
        readme = (APPS / name / "README.md").read_text(encoding="utf-8")
        assert "## Usage" in readme and "## What it teaches" in readme
    for name in ("ip-reputation", "port-check"):
        assert "own" in (APPS / name / "README.md").read_text(encoding="utf-8")


# -- ip-reputation ---------------------------------------------------------------

@pytest.fixture
def no_keys(monkeypatch):
    for env in ("VT_API_KEY", "ABUSEIPDB_API_KEY", "URLHAUS_API_KEY"):
        monkeypatch.delenv(env, raising=False)


def test_ip_reputation_without_keys_explains(no_keys, monkeypatch):
    def never(*a, **k):
        raise AssertionError("no lookup should happen without keys")
    monkeypatch.setattr(intel, "urlopen", never)
    out, code = run_app("ip-reputation", ["8.8.8.8"])
    text = "\n".join(out)
    assert code == 1
    assert "No threat-intel API keys are set" in text
    assert "VT_API_KEY" in text and "ABUSEIPDB_API_KEY" in text


def test_ip_reputation_usage(no_keys):
    out, code = run_app("ip-reputation", [])
    assert code == 2 and out[0].startswith("Usage: tb main.tb INDICATOR")


class Resp(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.status = 200


def stats(malicious, harmless=60):
    return {"data": {"attributes": {"last_analysis_stats": {
        "malicious": malicious, "suspicious": 0, "harmless": harmless, "undetected": 10}}}}


def test_ip_reputation_lookups(no_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("VT_API_KEY", "vt-key")
    monkeypatch.setenv("ABUSEIPDB_API_KEY", "abuse-key")
    seen = []

    def fake(request, timeout):
        url = request.full_url
        seen.append(url)
        if "abuseipdb" in url:
            return Resp({"data": {"abuseConfidenceScore": 100, "totalReports": 50,
                                  "countryCode": "DE", "isp": "Bad Hosting"}})
        if "urlhaus" in url:
            return Resp({"query_status": "ok", "url_status": "online", "threat": "malware_download"})
        if "/ip_addresses/" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, io.BytesIO(b"{}"))
        if "/files/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        return Resp(stats(5))

    monkeypatch.setattr(intel, "urlopen", fake)
    listfile = tmp_path / "iocs.txt"
    listfile.write_text("# from the alert\nhxxp://bad[.]example[.]test/x.sh\n\n" + "a" * 64 + "\n")
    out, code = run_app("ip-reputation", ["185.220.101[.]1", "192.168.1.5", "evil.example.test",
                                          "not an indicator", "--file", str(listfile)])
    text = "\n".join(out)
    assert code == 0
    assert "185.220.101.1  (ip)" in text
    assert "abuseipdb   malicious   score 100   50 reports, DE, Bad Hosting" in text
    assert "virustotal  skipped: VirusTotal says you've made too many lookups" in text
    assert "192.168.1.5  (ip)\n  private/internal address" in text
    assert "evil.example.test  (domain)\n  virustotal  malicious   score   7   5 of 75 engines flagged it" in text
    assert "I don't know what this is" in text
    assert "http://bad.example.test/x.sh  (url)" in text
    assert "urlhaus     malicious   score 100   listed as malware_download (online)" in text
    assert "no report yet" in text
    assert "Summary (worst verdict per indicator):" in text
    assert "  malicious   185.220.101.1" in text and f"  unknown     {'a' * 64}" in text
    assert not any("192.168.1.5" in u for u in seen)
    assert "vt-key" not in text and "abuse-key" not in text


# -- port-check -------------------------------------------------------------------

@pytest.fixture
def ssh_like():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.sendall(b"SSH-2.0-OpenSSH_9.9\r\n")
            except OSError:
                pass
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    yield srv.getsockname()[1]
    stop.set()
    srv.close()


def test_port_check_table(ssh_like):
    closed = closed_port()
    out, code = run_app("port-check", ["127.0.0.1", f"{ssh_like},{closed}", "--banners"])
    assert code == 0
    assert out[0] == "Port check for 127.0.0.1 (127.0.0.1), 2 ports"
    assert out[2] == "PORT   SERVICE        STATE"
    assert any(line.startswith(str(ssh_like)) and "OPEN" in line and "SSH-2.0-OpenSSH_9.9" in line for line in out)
    assert any(line.startswith(str(closed)) and line.endswith("closed") for line in out)
    assert "1 open, 1 closed or filtered." in out


def test_port_check_default_is_localhost_common_ports(monkeypatch):
    import tblib.net as net
    # keep it fast and deterministic: pretend nothing is listening
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (_ for _ in ()).throw(ConnectionRefusedError()))
    out, code = run_app("port-check", [])
    assert code == 0
    assert out[0] == f"Port check for 127.0.0.1 (127.0.0.1), {len(net.COMMON_PORTS)} ports"
    assert any(line.startswith("22     ssh") for line in out)
    assert f"0 open, {len(net.COMMON_PORTS)} closed or filtered." in out


def test_port_check_refuses_public_hosts_without_permission(monkeypatch):
    def never(*a, **k):
        raise AssertionError("must not connect")
    monkeypatch.setattr(socket.socket, "connect", never)
    out, code = run_app("port-check", ["8.8.8.8"])
    assert code == 2
    assert "outside your own network" in out[0] and "--authorised" in out[2]


def test_port_check_bad_ports():
    out, code = run_app("port-check", ["127.0.0.1", "1-5000"])
    assert code == 2 and "at most 1024 ports" in out[0]


# -- uptime-monitor -----------------------------------------------------------------

class Site(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        status = 200 if self.path == "/" else 503
        body = b"ok"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_uptime_all_up(site, tmp_path):
    cfg = tmp_path / "sites.json"
    cfg.write_text(json.dumps({"timeout": 5, "sites": [{"name": "Home", "url": site + "/"}]}))
    out, code = run_app("uptime-monitor", [str(cfg)])
    assert code == 0
    assert out[0].startswith("Uptime check, ")
    assert out[1].split() == ["SITE", "STATE", "STATUS", "TIME", "NOTE"]
    assert out[2].split()[:3] == ["Home", "UP", "200"] and out[2].endswith("ms")
    assert out[-1] == "All 1 sites are up."


def test_uptime_some_down(site, tmp_path):
    cfg = tmp_path / "list.json"
    cfg.write_text(json.dumps([site + "/", site + "/broken", f"http://127.0.0.1:{closed_port()}/"]))
    out, code = run_app("uptime-monitor", [str(cfg)])
    assert code == 1
    assert out[3].split()[1:3] == ["DOWN", "503"]
    assert "DOWN" in out[4] and "refused the connection" in out[4]
    assert out[-1] == "2 of 3 sites are DOWN."


def test_uptime_bad_config(tmp_path):
    out, code = run_app("uptime-monitor", [str(tmp_path / "nope.json")])
    assert code == 2 and out[0].startswith("I couldn't read the site list:")


def test_uptime_sample_config_lists_the_sites():
    data = json.loads((APPS / "uptime-monitor" / "sites.json").read_text())
    urls = [s["url"] for s in data["sites"]]
    assert urls == ["https://code.tanisha.ceo", "https://example.com"]
