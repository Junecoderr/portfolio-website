"""net library: IP helpers, DNS (monkeypatched), TCP checks against local sockets."""

import socket
import threading
import types

import pytest

import tb
from conftest import run_tb, tb_error


@pytest.fixture
def listener():
    """A local TCP server on 127.0.0.1 that sends a greeting, then closes."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    srv.settimeout(5)
    greeting = {"data": b"SSH-2.0-TestServer\r\n"}
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                if greeting["data"] is None:
                    stop.wait(1.0)  # a silent service: say nothing for a while
                elif greeting["data"]:
                    conn.sendall(greeting["data"])
            except OSError:
                pass
            finally:
                conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield types.SimpleNamespace(port=srv.getsockname()[1], greeting=greeting)
    stop.set()
    srv.close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- IP and CIDR helpers --------------------------------------------------------

def test_is_ip_family():
    src = 'import net\nsay(net.is_ip("8.8.8.8"), net.is_ip("::1"), net.is_ip("999.1.1.1"), net.is_ip(5), net.is_ip("example.com"))\nsay(net.is_ipv4("1.2.3.4"), net.is_ipv4("::1"), net.is_ipv6("2001:db8::1"), net.is_ipv6("1.2.3.4"))'
    assert run_tb(src) == ["true true false false false", "true false true false"]


def test_private_loopback_info():
    src = 'import net\nsay(net.is_private("10.1.2.3"), net.is_private("192.168.0.9"), net.is_private("8.8.8.8"), net.is_loopback("127.0.0.1"), net.is_loopback("::1"))\nlet i = net.ip_info("8.8.8.8")\nsay(i.version, i.global, i.private)'
    assert run_tb(src) == ["true true false true true", "4 true false"]


def test_in_cidr():
    src = 'import net\nsay(net.in_cidr("10.0.0.7", "10.0.0.0/24"), net.in_cidr("10.0.1.7", "10.0.0.0/24"), net.in_cidr("::1", "10.0.0.0/8"), net.in_cidr("2001:db8::5", "2001:db8::/32"))'
    assert run_tb(src) == ["true false false true"]


def test_cidr_hosts_and_info():
    assert run_tb('import net\nsay(net.cidr_hosts("192.168.1.0/30"))') == ['["192.168.1.1", "192.168.1.2"]']
    assert run_tb('import net\nsay(net.cidr_hosts("10.0.0.5/32"), len(net.cidr_hosts("10.0.0.0/16")))') == ['["10.0.0.5"] 65534']
    out = run_tb('import net\nlet c = net.cidr_info("192.168.1.77/24")\nsay(c.network, c.netmask, c.broadcast, c.prefix, c.size, c.first, c.last)')
    assert out == ["192.168.1.0 255.255.255.0 192.168.1.255 24 256 192.168.1.1 192.168.1.254"]


@pytest.mark.parametrize("src,message", [
    ('net.is_private("nope")', "net.is_private() needs a valid IP address, but 'nope' isn't one."),
    ('net.in_cidr("1.2.3.4", "10.0.0.0/33")', "needs a CIDR range like"),
    ('net.cidr_hosts("10.0.0.0/8")', "net.cidr_hosts() can list at most 65,536 addresses"),
    ('net.cidr_hosts("2001:db8::/64")', "Pick a smaller range."),
    ('net.tcp_check("127.0.0.1", 0)', "net.tcp_check() needs a port from 1 to 65535, not 0."),
    ('net.tcp_check("127.0.0.1", 70000)', "not 70000"),
    ('net.tcp_check("127.0.0.1", "80")', "net.tcp_check() needs port to be a whole number"),
    ('net.tcp_check("127.0.0.1", 80, 0)', "needs a timeout between 0 and 60 seconds"),
    ('net.tcp_check("", 80)', "net.tcp_check() needs a host name or IP address"),
    ('net.check_ports("127.0.0.1", "1-2000")', "net.check_ports() checks at most 1024 ports at a time."),
    ('net.check_ports("127.0.0.1", "abc")', "couldn't read the ports 'abc'"),
    ('net.check_ports("127.0.0.1", [22, "x"])', "needs each port to be a whole number"),
    ('net.check_ports("127.0.0.1", 22)', "net.check_ports() needs ports to be a list or text"),
])
def test_net_errors(src, message):
    assert message in str(tb_error("import net\n" + src))


def test_errors_are_catchable_with_reason():
    src = 'import net\ntry { net.is_private("x") } catch err { say(err.reason) }'
    assert run_tb(src) == ["invalid_ip"]


# -- DNS (monkeypatched, no internet) ---------------------------------------------

def fake_getaddrinfo(host, port, *args, **kwargs):
    if host == "good.test":
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 0)),
        ]
    if host == "loop.test":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")


def test_resolve(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert run_tb('import net\nsay(net.resolve("good.test"), net.resolve("10.0.0.1"))') == [
        '["192.0.2.10", "2001:db8::1"] ["10.0.0.1"]'
    ]
    err = tb_error('import net\nnet.resolve("nope.test")')
    assert "I couldn't find the host 'nope.test'." in str(err) and err.fields["reason"] == "dns"


def test_resolve_timeout(monkeypatch):
    import time
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: time.sleep(1) or [])
    err = tb_error('import net\nnet.resolve("slow.test", 0.05)')
    assert "took too long" in str(err) and err.fields["reason"] == "timeout"


def test_reverse(monkeypatch):
    def fake(ip):
        if ip == "192.0.2.10":
            return ("host.example.test", [], [ip])
        raise socket.herror(1, "Unknown host")
    monkeypatch.setattr(socket, "gethostbyaddr", fake)
    assert run_tb('import net\nsay(net.reverse("192.0.2.10"), net.reverse("192.0.2.11"))') == ["host.example.test nil"]


def test_my_ip_and_hostname():
    out = run_tb('import net\nsay(net.is_ip(net.my_ip()), type(net.hostname()))')
    assert out == ["true text"]


# -- TCP checks against local sockets ---------------------------------------------

def test_tcp_check_open_and_closed(listener):
    port = listener.port
    src = f'import net\nsay(net.tcp_check("127.0.0.1", {port}), net.tcp_check("127.0.0.1", {closed_port()}, 0.5))'
    assert run_tb(src) == ["true false"]


def test_tcp_check_uses_dns(listener, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    port = listener.port
    assert run_tb(f'import net\nsay(net.tcp_check("loop.test", {port}))') == ["true"]
    assert "I couldn't find the host 'nope.test'" in str(tb_error('import net\nnet.tcp_check("nope.test", 80)'))


def test_check_ports_map_in_order(listener):
    port = listener.port
    closed = closed_port()
    out = run_tb(f'import net\nlet r = net.check_ports("127.0.0.1", [{closed}, {port}, {port}])\nsay(r)\nsay(keys(r))')
    assert out == [f"{{{closed}: false, {port}: true}}", f"[{closed}, {port}]"]


def test_check_ports_text_spec(listener):
    port = listener.port
    out = run_tb(f'import net\nlet r = net.check_ports("127.0.0.1", "{port},{port}-{port + 2}", 0.3)\nsay(len(r), r[{port}])')
    assert out == ["3 true"]
    assert run_tb('import net\nsay(net.check_ports("127.0.0.1", []))') == ["{}"]


def test_check_ports_1024_is_fine():
    src = 'import net\nlet ports = []\nlet i = 40000\nwhile i < 41024 {\n  ports.push(i)\n  i += 1\n}\nsay(len(net.check_ports("127.0.0.1", ports, 0.2)))'
    assert run_tb(src) == ["1024"]


def test_banner(listener):
    port = listener.port
    assert run_tb(f'import net\nsay(net.banner("127.0.0.1", {port}))') == ["SSH-2.0-TestServer"]


def test_banner_is_capped(listener):
    listener.greeting["data"] = b"A" * 5000
    port = listener.port
    assert run_tb(f'import net\nsay(len(net.banner("127.0.0.1", {port})))') == ["512"]


def test_banner_silent_service_gives_empty_text(listener):
    listener.greeting["data"] = None
    port = listener.port
    assert run_tb(f'import net\nsay(net.banner("127.0.0.1", {port}, 0.3) == "")') == ["true"]


def test_banner_refused():
    err = tb_error(f'import net\nnet.banner("127.0.0.1", {closed_port()}, 0.5)')
    assert "Nothing is listening on 127.0.0.1 port" in str(err) and err.fields["reason"] == "refused"


def test_common_ports_and_docs():
    assert run_tb('import net\nsay(net.common_ports[22], net.common_ports[443])') == ["ssh https"]
    info = tb.describe_library("net")
    for fn in info["functions"]:
        assert fn["doc"] and fn["signature"].startswith(fn["name"] + "(")
    docs = {fn["name"]: fn["doc"] for fn in info["functions"]}
    for name in ("tcp_check", "check_ports", "banner"):
        assert "written permission" in docs[name]


def test_browser_needs_desktop(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert run_tb('import net\nsay(net.is_ip("1.2.3.4"))') == ["true"]
    assert "The net library needs the TB app on your computer" in str(tb_error('import net\nnet.tcp_check("127.0.0.1", 80)'))
