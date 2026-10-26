"""http library against a local http.server in a thread (no internet)."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import run_tb, tb_error


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def reply(self, status, body, ctype="text/plain; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        info = {
            "method": self.command, "path": self.path, "body": body,
            "ua": self.headers.get("User-Agent"), "ctype": self.headers.get("Content-Type"),
            "token": self.headers.get("X-Token"),
        }
        self.reply(200, json.dumps(info), "application/json")

    def do_GET(self):
        if self.path == "/hello":
            self.reply(200, "Hello, Tanisha!", extra={"X-Thing": "yes"})
        elif self.path == "/json":
            self.reply(200, '{"a": 1, "b": [true, null, 2.0]}', "application/json")
        elif self.path == "/missing":
            self.reply(404, "not here")
        elif self.path == "/boom":
            self.reply(500, '{"error": "boom"}', "application/json")
        elif self.path == "/slow":
            time.sleep(1.0)
            self.reply(200, "late")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/hello")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/big":
            self.reply(200, "x" * 5000)
        elif self.path == "/latin":
            self.reply(200, "café".encode("latin-1"), "text/plain; charset=latin-1")
        else:
            self.echo()

    do_POST = do_PUT = do_DELETE = echo

    def do_HEAD(self):
        self.reply(200, "ignored")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_get_text_and_headers(server):
    src = f'import http\nlet r = http.get("{server}/hello")\nsay(r.status, r.ok, r.text)\nsay(r.headers["x-thing"], r.headers["content-type"], r.json)\nsay(type(r.elapsed), r.url)'
    assert run_tb(src) == ["200 true Hello, Tanisha!", "yes text/plain; charset=utf-8 nil", f"number {server}/hello"]


def test_get_json_is_parsed(server):
    assert run_tb(f'import http\nlet r = http.get("{server}/json")\nsay(r.json, r.json.b[2])') == ['{"a": 1, "b": [true, nil, 2]} 2']


def test_4xx_and_5xx_do_not_raise(server):
    src = f'import http\nlet a = http.get("{server}/missing")\nlet b = http.get("{server}/boom")\nsay(a.status, a.ok, a.text)\nsay(b.status, b.ok, b.json.error)'
    assert run_tb(src) == ["404 false not here", "500 false boom"]


def test_default_user_agent_params_and_headers(server):
    src = (f'import http\nlet r = http.get("{server}/echo", {{"params": {{"q": "a b", "n": 2}}, "headers": {{"X-Token": "t1"}}}})\n'
           'say(r.json.path, r.json.ua, r.json.token)')
    assert run_tb(src) == ["/echo?q=a+b&n=2 TB/2.0 t1"]
    src = f'import http\nsay(http.get("{server}/e?x=1", {{"params": {{"y": 2}}, "user_agent": "Mine"}}).json.path, http.USER_AGENT)'
    assert run_tb(src) == ["/e?x=1&y=2 TB/2.0"]


def test_custom_user_agent(server):
    assert run_tb(f'import http\nsay(http.get("{server}/e", {{"user_agent": "Mine/1"}}).json.ua)') == ["Mine/1"]


def test_post_json_and_text(server):
    src = (f'import http\nlet a = http.post("{server}/p", {{"name": "Tanisha"}})\nsay(a.json.method, a.json.body, a.json.ctype)\n'
           f'let b = http.post("{server}/p", "plain")\nsay(b.json.body, b.json.ctype)\n'
           f'let c = http.post("{server}/p", nil, {{"json": [1, 2]}})\nsay(c.json.body, c.json.ctype)')
    assert run_tb(src) == [
        'POST {"name": "Tanisha"} application/json',
        "plain text/plain; charset=utf-8",
        "[1, 2] application/json",
    ]


def test_request_methods(server):
    src = (f'import http\nsay(http.request("put", "{server}/x", {{"body": "hi"}}).json.method)\n'
           f'say(http.request("DELETE", "{server}/x").json.method)\n'
           f'let h = http.request("HEAD", "{server}/x")\nsay(h.status, h.text == "")')
    assert run_tb(src) == ["PUT", "DELETE", "200 true"]


def test_redirect_is_followed(server):
    assert run_tb(f'import http\nlet r = http.get("{server}/redirect")\nsay(r.status, r.text, r.url)') == [
        f"200 Hello, Tanisha! {server}/hello"
    ]


def test_charset(server):
    assert run_tb(f'import http\nsay(http.get("{server}/latin").text)') == ["café"]


def test_max_bytes(server):
    err = tb_error(f'import http\nhttp.get("{server}/big", {{"max_bytes": 100}})')
    assert "bigger than 100 bytes" in str(err) and err.fields["reason"] == "too_big"


def test_timeout(server):
    err = tb_error(f'import http\nhttp.get("{server}/slow", {{"timeout": 0.2}})')
    assert "didn't answer in time" in str(err) and err.fields["reason"] == "timeout"


def test_connection_refused():
    err = tb_error(f'import http\nhttp.get("http://127.0.0.1:{closed_port()}/")')
    assert "refused the connection" in str(err) and err.fields["reason"] == "refused"


def test_dns_failure(monkeypatch):
    def fail_dns(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")
    monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
    err = tb_error('import http\nhttp.get("https://no-such-site.test/")')
    assert "I couldn't find the website 'no-such-site.test'" in str(err) and err.fields["reason"] == "dns"


def test_tls_error_is_friendly(server):
    port = server.rsplit(":", 1)[1]
    err = tb_error(f'import http\nhttp.get("https://127.0.0.1:{port}/hello", {{"timeout": 3}})')
    assert "The secure (TLS) connection to 127.0.0.1 failed" in str(err) and err.fields["reason"] == "tls"


def test_certificate_error_mentions_mac_fix():
    import ssl
    import urllib.error
    from tblib.http import describe_error
    inner = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate")
    inner.verify_message = "unable to get local issuer certificate"
    problem = describe_error(urllib.error.URLError(inner), "https://code.tanisha.ceo/")
    assert problem.reason == "tls" and "Install Certificates.command" in problem.message


def test_errors_are_catchable(server):
    src = f'import http\ntry {{ http.get("http://127.0.0.1:{closed_port()}/") }} catch err {{ say("caught", err.reason) }}'
    assert run_tb(src) == ["caught refused"]


@pytest.mark.parametrize("src,message", [
    ('http.get("ftp://example.com/x")', "http.get() needs a web address starting with http:// or https://"),
    ('http.get("file:///etc/passwd")', "starting with http:// or https://"),
    ('http.get("example.com")', "starting with http:// or https://"),
    ('http.get(5)', "http.get() needs url to be text"),
    ('http.get("http://x.test", {"nope": 1})', "http.get() doesn't know the option \"nope\""),
    ('http.get("http://x.test", {"timeout": 0})', "needs a timeout between 0 and 300 seconds"),
    ('http.request("BREW", "http://x.test")', "http.request() doesn't know the method 'BREW'"),
    ('http.post("http://x.test", 5)', "needs body to be text, a map or a list"),
])
def test_bad_arguments(src, message):
    assert message in str(tb_error("import http\n" + src))


def test_download(server, tmp_path):
    out = run_tb(f'import http\nlet d = http.download("{server}/big", "big.txt")\nsay(d.bytes, d.status, d.path)')
    assert out == ["5000 200 big.txt"]
    assert (tmp_path / "big.txt").read_text() == "x" * 5000


def test_download_cap_and_404(server, tmp_path):
    err = tb_error(f'import http\nhttp.download("{server}/big", "big.txt", {{"max_bytes": 1000}})')
    assert "bigger than 1,000 bytes" in str(err)
    assert not (tmp_path / "big.txt").exists() and not (tmp_path / "big.txt.part").exists()
    err = tb_error(f'import http\nhttp.download("{server}/missing", "m.txt")')
    assert "the server answered 404" in str(err) and err.fields["status"] == 404
    assert not (tmp_path / "m.txt").exists()
    assert "is a folder, not a file" in str(tb_error(f'import http\nhttp.download("{server}/big", ".")'))


def test_encode_params():
    assert run_tb('import http\nsay(http.encode_params({"q": "a b", "n": 2}))') == ["q=a+b&n=2"]


def test_browser_needs_desktop(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert "The http library needs the TB app on your computer" in str(tb_error('import http\nhttp.get("https://x.test")'))
