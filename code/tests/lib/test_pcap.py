"""pcap library: pcap/pcapng reading, protocol decoding, flows and helpers."""

import pytest

from binfixtures import (MAC_GW, MAC_WS, arp, dns_query, dns_response, ethernet, http_request, ipv4, ipv6,
                         pcap_file, pcapng_file, sample_pcap_bytes, tcp, tls_client_hello, udp)
from conftest import ROOT, run_tb, tb_error

SAMPLE = (ROOT / "apps" / "data" / "sample.pcap").as_posix()


def tb_path(p):
    return str(p).replace("\\", "/")


def small_frames():
    return [
        (1000.0, ethernet(MAC_WS, b"\xff" * 6, 0x0806, arp(1, MAC_WS, "192.0.2.10", b"\x00" * 6, "192.0.2.1"))),
        (1000.5, ethernet(MAC_WS, MAC_GW, 0x0800, ipv4("192.0.2.10", "192.0.2.1", 17,
                                                       udp(5555, 53, dns_query(7, "a.example.org"))))),
        (1000.6, ethernet(MAC_GW, MAC_WS, 0x0800, ipv4("192.0.2.1", "192.0.2.10", 17,
                                                       udp(53, 5555, dns_response(7, "a.example.org",
                                                                                  [(1, "203.0.113.9")]))))),
        (1001.0, ethernet(MAC_WS, MAC_GW, 0x0800, ipv4("192.0.2.10", "203.0.113.9", 6,
                                                       tcp(40000, 80, "PA", http_request("POST", "/up", "a.example.org"))))),
        (1002.0, ethernet(MAC_WS, MAC_GW, 0x0800, ipv4("192.0.2.10", "203.0.113.9", 6,
                                                       tcp(40001, 443, "PA", tls_client_hello("a.example.org"))))),
        (1003.0, ethernet(MAC_WS, MAC_GW, 0x86DD, ipv6("2001:db8::1", "2001:db8::2", 17, udp(1, 2, b"hi")))),
    ]


def test_sample_file_matches_the_generator():
    assert (ROOT / "apps" / "data" / "sample.pcap").read_bytes() == sample_pcap_bytes()


@pytest.mark.parametrize("kind", ["pcap", "pcap-ns", "pcap-be", "pcapng"])
def test_read_every_container(tmp_path, kind):
    frames = small_frames()
    data = {"pcap": lambda: pcap_file(frames), "pcap-ns": lambda: pcap_file(frames, nanos=True),
            "pcap-be": lambda: pcap_file(frames, big=True), "pcapng": lambda: pcapng_file(frames)}[kind]()
    path = tmp_path / "x.cap"
    path.write_bytes(data)
    out = run_tb(f'import pcap\nlet ps = pcap.read("{tb_path(path)}")\n'
                 'say(len(ps))\nsay(ps.map(fn(p) { return p.protocol }).join(","))\n'
                 'say(ps[1].src, ps[1].dst, ps[1].sport, ps[1].dport, ps[1].app)\n'
                 'say(ps[0].arp.operation, ps[0].time)\nsay(ps[5].ip_version, ps[5].src)\n'
                 f'say(pcap.format("{tb_path(path)}"))')
    assert out[0] == "6"
    assert out[1] == "arp,udp,udp,tcp,tcp,udp"
    assert out[2] == "192.0.2.10 192.0.2.1 5555 53 dns"
    assert out[3] == "request 1000"
    assert out[4] == "6 2001:db8::1"
    assert out[5] == {"pcap": "pcap", "pcap-ns": "pcap-ns", "pcap-be": "pcap", "pcapng": "pcapng"}[kind]


def test_dns_http_sni_helpers(tmp_path):
    path = tmp_path / "x.pcap"
    path.write_bytes(pcap_file(small_frames()))
    out = run_tb(f'import pcap\nlet ps = pcap.read("{tb_path(path)}")\n'
                 'let q = pcap.dns_queries(ps)\nsay(len(q), q[0].name, q[0].type, q[0].answers, q[0].rcode)\n'
                 'let h = pcap.http_requests(ps)[0]\nsay(h.method, h.url, h.dport)\n'
                 'let s = pcap.sni(ps)[0]\nsay(s.sni, s.dport, s.alpn)')
    assert out == ['1 a.example.org A ["203.0.113.9"] NOERROR', "POST http://a.example.org/up 80",
                   'a.example.org 443 ["h2", "http/1.1"]']


def test_limit_each_filter_and_write(tmp_path):
    path = tmp_path / "x.pcap"
    path.write_bytes(pcap_file(small_frames()))
    out_path = tmp_path / "only-tcp.pcap"
    out = run_tb(f'import pcap\nsay(len(pcap.read("{tb_path(path)}", 2)))\n'
                 'let n = 0\n'
                 f'say(pcap.each("{tb_path(path)}", fn(p) {{ n += 1\n return n < 3 }}))\nsay(n)\n'
                 f'let tcp = pcap.filter(pcap.read("{tb_path(path)}"), fn(p) {{ return p.protocol == "tcp" }})\n'
                 f'say(pcap.write("{tb_path(out_path)}", tcp))\n'
                 f'say(pcap.read("{tb_path(out_path)}").map(fn(p) {{ return p.dport }}))')
    assert out == ["2", "3", "3", "2", "[80, 443]"]


def test_sample_flows_talkers_scans_and_summary():
    out = run_tb(f'import pcap\nlet ps = pcap.read("{SAMPLE}")\n'
                 'say(len(ps))\n'
                 'let t = pcap.top_talkers(ps, 1)[0]\nsay(t.ip, t.packets)\n'
                 'let scan = pcap.port_scans(ps)[0]\nsay(scan.src, scan.count, scan.open_ports)\n'
                 'let f = pcap.flows(ps)\nsay(len(f) > 20, f.all(fn(x) { return x.packets >= 1 and x.bytes > 0 }))\n'
                 f'let s = pcap.summary("{SAMPLE}")\nsay(s.packets, s.format, s.tls_hellos, s.dns_queries)\n'
                 'say(pcap.service(22), pcap.service(65000))')
    assert out == ["94", "192.0.2.10 92", "198.51.100.77 25 [22, 80, 445]", "true true", "94 pcap 5 4",
                   "ssh nil"]


def test_friendly_errors(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    assert "I couldn't find" in str(tb_error('import pcap\npcap.read("nope.pcap")'))
    err = str(tb_error(f'import pcap\npcap.read("{tb_path(text)}")'))
    assert "isn't a pcap" in err or "not a pcap" in err or "capture" in err
    cut = tmp_path / "cut.pcap"
    cut.write_bytes(pcap_file(small_frames())[:-10])
    # A capture cut off mid-packet keeps what there is of the last one.
    assert run_tb(f'import pcap\nlet ps = pcap.read("{tb_path(cut)}")\nsay(len(ps), ps[5].captured < ps[5].length, ps[0].captured == ps[0].length)') == ["6 true true"]
