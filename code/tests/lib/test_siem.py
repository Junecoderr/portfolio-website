"""siem library: CEF / LEEF / syslog formatting, and the Splunk HEC, Elasticsearch bulk and
Sentinel senders against a local HTTP server (nothing leaves the machine)."""

import base64
import hashlib
import hmac
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import run_tb, tb_error

KEY = base64.b64encode(b"sentinel-test-key-0123456789abcd").decode()
ALERT = ('{"rule": "Brute force", "level": "high", "by": "203.0.113.42", "by_field": "ip", "count": 12, '
         '"first": 1773300000, "last": 1773300100, "description": "Many failed logins", '
         '"tags": ["attack.t1110"], "records": [{"user": "root", "ip": "203.0.113.42", "host": "web01"}]}')


def run(source):
    return run_tb("import siem\n" + source)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        auth = self.headers.get("Authorization", "")
        if "wrong" in auth:
            status, reply = 403, {"text": "Invalid token"}
        elif self.path.startswith("/_bulk"):
            lines = [l for l in body.decode().splitlines() if l]
            status, reply = 200, {"errors": False, "items": [{"index": {"status": 201}}] * (len(lines) // 2)}
        elif self.path.startswith("/api/logs"):
            status, reply = 200, None
        else:
            status, reply = 200, {"text": "Success", "code": 0}
        data = json.dumps(reply).encode() if reply is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.seen = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def url(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}"


# -- formats -------------------------------------------------------------------------------

def test_cef_from_alert_and_escaping():
    [line] = run(f"say(siem.cef({ALERT}))")
    assert line.startswith("CEF:0|TB|detect|")
    assert "|Brute force|8|" in line
    assert "src=203.0.113.42" in line and "cnt=12" in line
    [line] = run('say(siem.cef({"name": "a|b", "message": "x=y\\\\z", "level": "low"}))')
    assert "|a\\|b|3|" in line and "msg=x\\=y\\\\z" in line


def test_leef_and_syslog():
    [line] = run(f"say(siem.leef({ALERT}))")
    assert line.startswith("LEEF:2.0|TB|detect|") and "src=203.0.113.42" in line and "\t" in line
    [line] = run('say(siem.syslog_line("hello", {"host": "h", "app": "tb", "time": 0, "facility": "local0", "severity": "warning"}))')
    assert line == "<132>1 1970-01-01T00:00:00.000Z h tb - - - hello"
    [line] = run('say(siem.syslog_line("hi", {"format": "rfc3164", "host": "h", "app": "tb", "time": 0}))')
    assert line.startswith("<14>Jan ") and line.endswith(" h tb: hi")  # RFC 3164 uses local time
    [line] = run(f"say(siem.syslog_line({ALERT}, {{\"host\": \"h\", \"app\": \"tb\", \"time\": 0}}))")
    assert line.startswith("<11>1 ") and '[tb@32473' in line and "Brute force" in line


def test_event_flattens_alert():
    [out] = run(f"let e = siem.event({ALERT})\nsay(e.name, e.severity, e.level)")
    assert out == "Brute force 8 high"


def test_send_syslog_udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5)
    port = sock.getsockname()[1]
    try:
        out = run(f'say(siem.send_syslog("127.0.0.1", {port}, ["one", "two"]))')
        assert out == ['{"sent": 2, "bytes": 6}']
        assert sock.recv(100) == b"one" and sock.recv(100) == b"two"
    finally:
        sock.close()


# -- senders ---------------------------------------------------------------------------------

def test_splunk_dry_run_hides_token():
    out = run(f'let s = siem.splunk("https://splunk.example:8088", "secret-token", {{"dry_run": true, "index": "sec"}})\n'
              f'let r = s.send([{ALERT}])\nsay(r.url)\nsay(r.headers)\nsay(r.body)')
    assert out[0] == "https://splunk.example:8088/services/collector/event"
    assert "secret-token" not in out[1] and "***" in out[1]
    body = json.loads(out[2])
    assert body["index"] == "sec" and body["event"]["name"] == "Brute force" and body["time"] == 1773300000


def test_splunk_sends_to_local_server(server):
    out = run(f'let s = siem.splunk("{url(server)}", "tok")\nsay(s.send([{{"msg": "a"}}, {{"msg": "b"}}]).sent)')
    assert out == ["2"]
    seen = server.seen[0]
    assert seen["path"] == "/services/collector/event"
    assert seen["headers"]["Authorization"] == "Splunk tok"
    assert [json.loads(l)["event"]["msg"] for l in seen["body"].decode().splitlines()] == ["a", "b"]


def test_splunk_bad_token_is_friendly_and_secret_free(server):
    err = str(tb_error(f'import siem\nsiem.splunk("{url(server)}", "wrong-token").send([{{"a": 1}}])'))
    assert "rejected the HEC token" in err and "wrong-token" not in err


def test_elastic_bulk(server):
    out = run(f'let es = siem.elastic("{url(server)}", {{"user": "tb", "password": "pw"}})\n'
              f'let r = es.index("alerts-tb", [{ALERT}, {{"a": 1}}])\nsay(r.sent, r.errors)')
    assert out == ["2 0"]
    seen = server.seen[0]
    assert seen["path"] == "/_bulk"
    assert seen["headers"]["Authorization"] == "Basic " + base64.b64encode(b"tb:pw").decode()
    lines = [json.loads(l) for l in seen["body"].decode().splitlines()]
    assert lines[0] == {"index": {"_index": "alerts-tb"}} and "@timestamp" in lines[1]
    assert "isn't a valid index name" in str(tb_error(f'import siem\nsiem.elastic("{url(server)}").index("Bad Name", [{{"a": 1}}])'))


def test_sentinel_signature(server):
    out = run(f'let s = siem.sentinel("ws-1", "{KEY}", {{"url": "{url(server)}"}})\nsay(s.send("TBAlerts", [{{"a": 1}}]))')
    assert out == ['{"sent": 1, "status": 200, "ok": true}']
    seen = server.seen[0]
    h = {k.lower(): v for k, v in seen["headers"].items()}
    assert seen["path"] == "/api/logs?api-version=2016-04-01" and h["log-type"] == "TBAlerts"
    to_sign = f"POST\n{len(seen['body'])}\napplication/json\nx-ms-date:{h['x-ms-date']}\n/api/logs".encode()
    sig = base64.b64encode(hmac.new(base64.b64decode(KEY), to_sign, hashlib.sha256).digest()).decode()
    assert h["authorization"] == f"SharedKey ws-1:{sig}"


def test_sentinel_dry_run_and_errors():
    out = run(f'let r = siem.sentinel("ws-1", "{KEY}", {{"dry_run": true}}).send("TB", [{{"a": 1}}])\nsay(r.url)\nsay(r.headers.Authorization)')
    assert out == ["https://ws-1.ods.opinsights.azure.com/api/logs?api-version=2016-04-01", "***"]
    assert "base64 shared key" in str(tb_error('import siem\nsiem.sentinel("ws", "not base64!")'))
    assert "isn't a valid log type" in str(tb_error(f'import siem\nsiem.sentinel("ws", "{KEY}", {{"dry_run": true}}).send("9bad", [{{"a": 1}}])'))


def test_unreachable_server_is_friendly():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    err = str(tb_error(f'import siem\nsiem.splunk("http://127.0.0.1:{port}", "tok").send([{{"a": 1}}])'))
    assert "refused the connection" in err


def test_option_errors():
    assert "doesn't know the option" in str(tb_error('import siem\nsiem.cef({"a": 1}, {"colour": 1})'))
    assert "empty list" in str(tb_error('import siem\nsiem.splunk("http://x.example", "t", {"dry_run": true}).send([])'))
