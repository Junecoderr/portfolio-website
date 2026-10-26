"""dns library against an in-test fake DNS server (no internet)."""

import pytest

import tb
import tblib.dns as dnslib
from conftest import run_tb, tb_error
from dns_fake import FakeDns

ZONE = {
    ("example.test", "A"): [("A", 300, "192.0.2.10"), ("A", 300, "192.0.2.11")],
    ("example.test", "AAAA"): [("AAAA", 60, "2001:db8::1")],
    ("example.test", "MX"): [("MX", 3600, (10, "mail.example.test")), ("MX", 3600, (20, "mail2.example.test"))],
    ("example.test", "TXT"): [("TXT", 60, ["v=spf1 ", "-all"]), ("TXT", 60, "hello")],
    ("example.test", "NS"): [("NS", 86400, "ns1.example.test")],
    ("www.example.test", "A"): [("CNAME", 60, "example.test"), ("A", 300, "192.0.2.10")],
    ("example.test", "SOA"): [("SOA", 60, ("ns1.example.test", "hostmaster.example.test", 2026102601, 7200, 900, 1209600, 300))],
    ("_sip._tcp.example.test", "SRV"): [("SRV", 60, (5, 10, 5060, "sip.example.test"))],
    ("example.test", "CAA"): [("CAA", 60, (0, "issue", "letsencrypt.org"))],
    ("10.2.0.192.in-addr.arpa", "PTR"): [("PTR", 60, "example.test")],
    ("empty.test", "A"): [],
}


@pytest.fixture
def server():
    s = FakeDns(ZONE, nxdomain={"nope.test"}, servfail={"broken.test"})
    yield s
    s.close()


def test_lookup_types(server):
    src = f'''import dns
let s = "{server.address}"
say(dns.lookup("example.test", "A", s).map(fn(r) {{ return r.value }}))
let a = dns.lookup("example.test", "A", s)[0]
say(a.name, a.type, a.ttl)
say(dns.lookup("example.test", "AAAA", s)[0].value)
say(dns.lookup("example.test", "MX", s).map(fn(r) {{ return [r.priority, r.host] }}))
say(dns.lookup("example.test", "TXT", s).map(fn(r) {{ return r.value }}), dns.lookup("example.test", "TXT", s)[0].parts)
say(dns.lookup("example.test", "NS", s)[0].value, dns.lookup("www.example.test", "A", s).map(fn(r) {{ return r.type }}))
let soa = dns.lookup("example.test", "soa", s)[0]
say(soa.primary, soa.mailbox, soa.serial, soa.refresh, soa.minimum)
let srv = dns.lookup("_sip._tcp.example.test", "SRV", s)[0]
say(srv.priority, srv.weight, srv.port, srv.target)
let caa = dns.lookup("example.test", "CAA", s)[0]
say(caa.flags, caa.tag, caa.value)
say(dns.lookup("empty.test", "A", s))
'''
    assert run_tb(src) == [
        '["192.0.2.10", "192.0.2.11"]',
        "example.test A 300",
        "2001:db8::1",
        '[[10, "mail.example.test"], [20, "mail2.example.test"]]',
        '["v=spf1 -all", "hello"] ["v=spf1 ", "-all"]',
        'ns1.example.test ["CNAME", "A"]',
        "ns1.example.test hostmaster.example.test 2026102601 7200 300",
        "5 10 5060 sip.example.test",
        '0 issue 0 issue "letsencrypt.org"',
        "[]",
    ]


def test_query_reverse_and_status(server):
    src = f'''import dns
let s = "{server.address}"
let q = dns.query("nope.test", "A", s)
say(q.status, q.answers, q.server, q.truncated, q.question)
say(dns.reverse("192.0.2.10", s), dns.reverse("192.0.2.99", s))
say(dns.reverse_name("8.8.4.4"), dns.reverse_name("2001:db8::1").ends_with(".ip6.arpa"))
say(dns.is_name("example.com"), dns.is_name("bad name"), dns.is_name(""), "MX" in dns.types)
'''
    assert run_tb(src) == [
        'nxdomain [] 127.0.0.1:' + str(server.port) + ' false {"name": "nope.test", "type": "A"}',
        "example.test nil",
        "4.4.8.8.in-addr.arpa true",
        "true false false true",
    ]


def test_nxdomain_and_servfail_errors(server):
    err = tb_error(f'import dns\ndns.lookup("nope.test", "A", "{server.address}")')
    assert "The name 'nope.test' doesn't exist" in str(err) and err.fields["reason"] == "nxdomain"
    err = tb_error(f'import dns\ndns.lookup("broken.test", "A", "{server.address}")')
    assert "answered with an error: server_failure" in str(err) and err.fields["reason"] == "server_failure"


def test_truncated_udp_falls_back_to_tcp():
    s = FakeDns(ZONE, truncate_udp=True)
    try:
        out = run_tb(f'import dns\nsay(dns.lookup("example.test", "A", "{s.address}")[0].value)')
        assert out == ["192.0.2.10"]
        assert [q[2] for q in s.questions] == ["udp", "tcp"]
        out = run_tb(f'import dns\nsay(len(dns.lookup("example.test", "A", "{s.address}", {{"tcp": true}})))')
        assert out == ["2"] and s.questions[-1][2] == "tcp"
    finally:
        s.close()


def test_timeout_and_server_list():
    silent = FakeDns({}, silent=True)
    good = FakeDns(ZONE)
    try:
        err = tb_error(f'import dns\ndns.lookup("example.test", "A", "{silent.address}", {{"timeout": 0.2, "retries": 0}})')
        assert "didn't answer within 0.2 seconds" in str(err) and err.fields["reason"] == "timeout"
        out = run_tb(f'import dns\nsay(dns.query("example.test", "A", ["{silent.address}", "{good.address}"], {{"timeout": 0.2, "retries": 0}}).server)')
        assert out == [good.address]
    finally:
        silent.close()
        good.close()


def test_refused_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    err = tb_error(f'import dns\ndns.lookup("example.test", "A", "127.0.0.1:{port}", {{"tcp": true, "timeout": 1}})')
    assert err.fields["reason"] in ("refused", "timeout", "network")


@pytest.mark.parametrize("src,message", [
    ('dns.lookup("")', "needs a name like"),
    ('dns.lookup("has space.test")', "needs a host name like"),
    ('dns.lookup("a.test", "WRONG")', 'doesn\'t know the record type "WRONG"'),
    ('dns.lookup("a.test", "A", "dns.example.test")', "needs the server as an IP address"),
    ('dns.lookup("a.test", "A", "1.1.1.1", {"ttl": 1})', 'doesn\'t know the option "ttl"'),
    ('dns.lookup("a.test", "A", "1.1.1.1", {"timeout": 0})', "needs a timeout between 0 and 60"),
    ('dns.lookup("a.test", "A", "1.1.1.1", {"retries": 9})', "allows 0 to 5 retries"),
    ('dns.reverse("not-an-ip")', "needs an IP address"),
    ('dns.lookup("a.test", "A", [])', "empty list of servers"),
])
def test_dns_argument_errors(src, message):
    assert message in str(tb_error("import dns\n" + src))


def test_servers_and_system_parsing(monkeypatch, tmp_path):
    out = run_tb("import dns\nsay(type(dns.servers()))")
    assert out == ["list"]
    assert dnslib.split_server("1.1.1.1") == ("1.1.1.1", 53)
    assert dnslib.split_server("1.1.1.1:5353") == ("1.1.1.1", 5353)
    assert dnslib.split_server("[::1]:53") == ("::1", 53)
    assert dnslib.split_server("2001:db8::1") == ("2001:db8::1", 53)


def test_wire_errors():
    with pytest.raises(dnslib.DnsProblem):
        dnslib.parse_response(b"\x00" * 5, 0)
    with pytest.raises(dnslib.DnsProblem):
        dnslib.parse_response(b"\x00\x01" + b"\x00" * 10, 0)  # wrong id
    with pytest.raises(dnslib.DnsProblem):
        dnslib.encode_name("a" * 70 + ".test")


def test_browser_and_docs(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert "The dns library needs the TB app" in str(tb_error('import dns\ndns.lookup("example.test")'))
    info = tb.describe_library("dns")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
