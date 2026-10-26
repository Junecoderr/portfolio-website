"""tls library against a local TLS server with a self-signed certificate."""

import socket
import ssl
import threading

import pytest

import tb
import tblib.tls as tlslib
from conftest import run_tb, tb_error
from tls_fixture import CERT_PEM, server_context, write_pair


@pytest.fixture
def tls_server(tmp_path):
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
            except (socket.timeout, OSError):
                continue
            try:
                tls = ctx.wrap_socket(conn, server_side=True)
                tls.settimeout(1)
                try:
                    tls.recv(1)
                except OSError:
                    pass
                tls.close()
            except (ssl.SSLError, OSError):
                conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cert, _key = write_pair(str(tmp_path))
    yield {"port": raw.getsockname()[1], "ca": cert.replace("\\", "/")}
    stop.set()
    t.join(2)
    raw.close()


def test_certificate_self_signed_then_trusted(tls_server):
    port, ca = tls_server["port"], tls_server["ca"]
    src = f'''import tls
let c = tls.certificate("localhost", {port})
say(c.verified, c.self_signed, c.subject.common_name, c.subject.organization, c.issuer.common_name)
say(c.san, c.hostname_matches, c.expired, c.days_left > 30000, c.not_after.starts_with("2126-"))
say(c.pem.starts_with("-----BEGIN CERTIFICATE-----"), c.version.starts_with("TLSv1."), type(c.cipher), c.verify_error != nil)
let t = tls.certificate("127.0.0.1:{port}", nil, {{"ca_file": "{ca}", "server_name": "localhost"}})
say(t.verified, t.verify_error, t.hostname_matches, t.port)
let u = tls.certificate("https://localhost:{port}/path", nil, {{"ca_file": "{ca}"}})
say(u.verified, u.port)
'''
    assert run_tb(src) == [
        "false true localhost TB Tests localhost",
        '["localhost", "127.0.0.1"] true false true true',
        "true true text true",
        "true nil true " + str(port),
        "true " + str(port),
    ]


def test_check_and_versions(tls_server):
    port, ca = tls_server["port"], tls_server["ca"]
    src = f'''import tls
let r = tls.check("localhost", {port})
say(r.ok, len(r.issues), r.issues[0].contains("isn't trusted"), r.host, r.port, r.days_left > 0)
let g = tls.check("localhost", {port}, {{"ca_file": "{ca}"}})
say(g.ok, g.issues, g.verified, g.hostname_matches, g.subject.common_name)
let w = tls.check("localhost", {port}, {{"ca_file": "{ca}", "warn_days": 100000}})
say(w.ok, w.issues[0].contains("expires in"))
let v = tls.versions("localhost", {port}, {{"timeout": 3}})
say(keys(v), v["TLSv1.2"] or v["TLSv1.3"])
'''
    assert run_tb(src) == [
        "false 1 true localhost " + str(port) + " true",
        "true [] true true localhost",
        "false true",
        '["TLSv1.0", "TLSv1.1", "TLSv1.2", "TLSv1.3"] true',
    ]


def test_hostname_mismatch_is_reported(tls_server):
    port, ca = tls_server["port"], tls_server["ca"]
    src = f'''import tls
let r = tls.check("127.0.0.1", {port}, {{"ca_file": "{ca}", "server_name": "other.test"}})
say(r.hostname_matches, r.issues.filter(fn(i) {{ return i.contains("isn't for other.test") }}).len())
'''
    out = run_tb(src)
    assert out == ["false 1"]


def test_parse_pem_and_hostname_matching():
    src = 'import tls\nlet p = tls.parse_pem(' + __import__("json").dumps(CERT_PEM) + ')\nsay(p.subject.common_name, p.san, p.self_signed, p.expired, p.days_left > 0)'
    assert run_tb(src) == ["localhost [\"localhost\", \"127.0.0.1\"] true false true"]
    assert "needs PEM text" in str(tb_error('import tls\ntls.parse_pem("nope")'))
    cert = {"subjectAltName": (("DNS", "*.example.test"), ("DNS", "example.test"), ("IP Address", "192.0.2.1"))}
    assert tlslib.hostname_matches(cert, "www.example.test") is True
    assert tlslib.hostname_matches(cert, "a.b.example.test") is False
    assert tlslib.hostname_matches(cert, "example.test") is True
    assert tlslib.hostname_matches(cert, "192.0.2.1") is True
    assert tlslib.hostname_matches({"subject": ((("commonName", "cn.test"),),)}, "cn.test") is True
    assert tlslib.hostname_matches({}, "x") is None


def test_connection_errors():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    err = tb_error(f'import tls\ntls.certificate("127.0.0.1", {port}, {{"timeout": 1}})')
    assert err.fields["reason"] in ("refused", "timeout")
    # A plain (non-TLS) server: the handshake fails.
    plain = socket.socket()
    plain.bind(("127.0.0.1", 0))
    plain.listen(1)
    pport = plain.getsockname()[1]

    def talk():
        try:
            c, _ = plain.accept()
            c.sendall(b"HELLO\r\n")
            c.close()
        except OSError:
            pass
    threading.Thread(target=talk, daemon=True).start()
    err = tb_error(f'import tls\ntls.certificate("127.0.0.1", {pport}, {{"timeout": 2}})')
    plain.close()
    assert err.fields["reason"] in ("tls", "timeout", "connect") and "127.0.0.1" in str(err)


@pytest.mark.parametrize("src,message", [
    ('tls.certificate("")', "needs a host name, but got empty text"),
    ('tls.certificate("h", 0)', "needs a port from 1 to 65535"),
    ('tls.certificate("h", 443, {"verify": false})', 'doesn\'t know the option "verify"'),
    ('tls.certificate("h", 443, {"ca_file": "nope.pem"})', "I couldn't find the CA file 'nope.pem'"),
    ('tls.check("h", 443, {"timeout": 0})', "needs a timeout between 0 and 60"),
    ('tls.certificate(5)', "needs host to be text"),
])
def test_tls_argument_errors(src, message):
    assert message in str(tb_error("import tls\n" + src))


def test_browser_and_docs(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert "The tls library needs the TB app" in str(tb_error('import tls\ntls.check("example.test")'))
    info = tb.describe_library("tls")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
    assert "written permission" in info["doc"]
