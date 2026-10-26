"""server library: a real HTTP server on 127.0.0.1 (port 0), driven from TB
(via the http library) and from Python (raw requests for the safety checks)."""

import http.client
import json
import random
import socket

import pytest

import tb
import tblib.server as serverlib
from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def stop_servers():
    yield
    serverlib.stop_all()


class Live:
    """Run TB source that starts an app in the background; keep the interpreter
    alive so the handlers can run while Python sends requests."""

    def __init__(self, source):
        self.lines = []
        self.interp = tb.Interpreter(output=self.lines.append, input_fn=lambda p: "", rng=random.Random(0))
        tb.run(source, interpreter=self.interp)
        self.url = next(l for l in self.lines if l.startswith("http://"))
        self.port = int(self.url.rsplit(":", 1)[1])

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data.decode("utf-8", "replace")
        finally:
            conn.close()


def test_routes_params_query_json_and_post():
    out = run_tb('''
import server
import http
let app = server.create({"log": false})
app.get("/", fn(req) { return "home" })
app.get("/items/{id}", fn(req) { return {"id": req.params.id, "q": req.query.q} })
app.post("/echo", fn(req) { return server.json({"got": req.json.name}, 201) })
app.get("/html", fn(req) { return server.html("<b>hi</b>") })
app.get("/go", fn(req) { return server.redirect("/") })
let url = app.start(0)
say(url.starts_with("http://127.0.0.1:"))
say(app.running)
let r = http.get(url + "/")
say(r.status + " " + r.text)
r = http.get(url + "/items/42?q=abc")
say(r.json.id + " " + r.json.q)
r = http.post(url + "/echo", {"name": "Tanisha"})
say(r.status + " " + r.json.got)
r = http.get(url + "/html")
say(r.headers["content-type"])
r = http.get(url + "/go")
say(r.text)
r = http.get(url + "/missing")
say(r.status + " " + r.json.error)
app.stop()
say(app.running)
''')
    assert out == ["true", "true", "200 home", "42 abc", "201 Tanisha", "text/html; charset=utf-8",
                   "home", "404 No route for GET /missing.", "false"]


def test_handler_error_becomes_500_json_and_server_keeps_going():
    out = run_tb('''
import server
import http
let app = server.create({"log": false})
app.get("/boom", fn(req) { throw "the database is away" })
app.get("/ok", fn(req) { return "fine" })
let url = app.start(0)
let r = http.get(url + "/boom")
say(r.status)
say(r.json.error)
say(http.get(url + "/ok").text)
app.stop()
''')
    assert out == ["500", "the database is away", "fine"]


def test_request_logging_line():
    out = run_tb('''
import server
import http
let app = server.create()
app.get("/", fn(req) { return "x" })
let url = app.start(0)
http.get(url + "/")
app.stop()
''')
    assert any(" GET / 200 " in line for line in out)


def test_binds_loopback_by_default_and_wrong_method_is_405():
    live = Live('''
import server
let app = server.create({"log": false})
app.post("/only-post", fn(req) { return "ok" })
say(app.start(0))
say(app.host)
''')
    assert live.lines[-1] == "127.0.0.1"
    status, headers, body = live.request("GET", "/only-post")
    assert status == 405 and headers["allow"] == "POST"


def test_static_files_and_path_traversal_blocked(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "index.html").write_text("<h1>index</h1>")
    (public / "app.css").write_text("body{}")
    (tmp_path / "secret.txt").write_text("top secret")
    live = Live(f'''
import server
let app = server.create({{"log": false}})
app.static("/assets", {json.dumps(str(public))})
say(app.start(0))
''')
    status, headers, body = live.request("GET", "/assets/app.css")
    assert status == 200 and body == "body{}" and headers["content-type"].startswith("text/css")
    assert live.request("GET", "/assets")[2] == "<h1>index</h1>"
    for evil in ("/assets/../secret.txt", "/assets/%2e%2e/secret.txt", "/assets/..%2fsecret.txt",
                 "/assets/%252e%252e/secret.txt"):
        status, _, body = live.request("GET", evil)
        assert status in (403, 404), evil
        assert "top secret" not in body


def test_body_cap_rejects_big_bodies():
    live = Live('''
import server
let app = server.create({"log": false, "max_body": 100})
app.post("/up", fn(req) { return "len " + len(req.body) })
say(app.start(0))
''')
    assert live.request("POST", "/up", body="x" * 50)[2] == "len 50"
    status, _, body = live.request("POST", "/up", body="x" * 500)
    assert status == 413 and "too big" in body


def test_default_body_cap_is_10_mb():
    assert serverlib.MAX_BODY == 10 * 1024 * 1024
    assert run_tb("import server\nsay(server.max_body)") == [str(10 * 1024 * 1024)]


def test_form_cookies_and_headers():
    live = Live('''
import server
let app = server.create({"log": false})
app.post("/form", fn(req) { return {"who": req.form.who, "c": req.cookies.session, "h": req.headers["x-tag"]} })
say(app.start(0))
''')
    status, _, body = live.request("POST", "/form", body="who=Tanisha",
                                   headers={"Content-Type": "application/x-www-form-urlencoded",
                                            "Cookie": "session=abc", "X-Tag": "t1"})
    assert status == 200 and json.loads(body) == {"who": "Tanisha", "c": "abc", "h": "t1"}


def test_response_helpers_reject_header_injection():
    err = tb_error('import server\nserver.text("x", 200, {"X-A": "a\\r\\nSet-Cookie: y"})')
    assert "line breaks" in err.message


def test_friendly_errors():
    assert "start with" in tb_error('import server\nlet a = server.create()\na.get("items", fn(r) { return 1 })').message
    assert "add a route first" in tb_error('import server\nlet a = server.create()\na.start(0)').message
    assert "doesn't know the option" in tb_error('import server\nserver.create({"colour": 1})').message
    assert "100 to 599" in tb_error('import server\nserver.text("x", 42)').message


def test_port_in_use_is_friendly():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        err = tb_error(f'import server\nlet a = server.create({{"log": false}})\na.get("/", fn(r) {{ return 1 }})\na.start({port})')
        assert "already in use" in err.message
    finally:
        s.close()


def test_parse_query_and_escape():
    out = run_tb('''
import server
say(server.parse_query("a=1&b=x%20y").b)
say(server.escape("<a href='x'>"))
''')
    assert out == ["x y", "&lt;a href=&#x27;x&#x27;&gt;"]


def test_documented():
    lib = tb.describe_library("server")
    text = json.dumps(lib, default=str)
    for name in ("create", "json", "text", "html", "file", "redirect", "stop_all"):
        assert name in text


def test_server_survives_sigpipe_default_from_tb_main():
    """tb.main() sets SIGPIPE to SIG_DFL; a server must switch it off, or a
    browser hanging up mid-response would kill the program."""
    import signal
    previous = signal.getsignal(signal.SIGPIPE)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        run_tb('import server\nlet a = server.create({"log": false})\na.get("/", fn(r) { return 1 })\na.start(0)\na.stop()')
        assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGPIPE, previous)
