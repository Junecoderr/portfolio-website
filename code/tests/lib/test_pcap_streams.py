"""pcap reassembly: IP fragments, TCP streams, HTTP exchanges and objects (synthetic captures)."""

import gzip
import hashlib
import ipaddress
import struct

import pytest

from binfixtures import MAC_GW, MAC_WS, checksum, dns_name, ethernet, ipv4, ipv6, pcap_file, pcapng_file, tcp, udp
from conftest import run_tb, tb_error

C, S = "192.0.2.10", "203.0.113.50"


def tb_path(p):
    return str(p).replace("\\", "/")


def up(payload, flags="PA", seq=0, ack=0, sport=51000, dport=80, t=0.0):
    """Client -> server segment."""
    return t, ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(C, S, 6, tcp(sport, dport, flags, payload, seq=seq, ack=ack)))


def down(payload, flags="PA", seq=0, ack=0, sport=51000, dport=80, t=0.0):
    """Server -> client segment."""
    return t, ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(S, C, 6, tcp(dport, sport, flags, payload, seq=seq, ack=ack)))


def handshake(c_isn=1000, s_isn=5000, sport=51000, dport=80):
    return [up(b"", "S", c_isn, 0, sport, dport), down(b"", "SA", s_isn, c_isn + 1, sport, dport),
            up(b"", "A", c_isn + 1, s_isn + 1, sport, dport)]


def ipv4_fragment(src, dst, proto, chunk, offset, more, ident=0x4242):
    total = 20 + len(chunk)
    frag = (0x2000 if more else 0) | (offset // 8)
    head = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total, ident, frag, 64, proto, 0,
                       ipaddress.IPv4Address(src).packed, ipaddress.IPv4Address(dst).packed)
    head = head[:10] + struct.pack("!H", checksum(head)) + head[12:]
    return ethernet(MAC_WS, MAC_GW, 0x0800, head + chunk)


def ipv6_fragment(src, dst, nxt, chunk, offset, more, ident=0x01020304):
    frag = struct.pack("!BBHI", nxt, 0, offset | (1 if more else 0), ident)
    return ethernet(MAC_WS, MAC_GW, 0x86DD, ipv6(src, dst, 44, frag + chunk))


def write(tmp_path, frames, kind="pcap"):
    path = tmp_path / ("x." + kind)
    path.write_bytes(pcap_file(frames) if kind == "pcap" else pcapng_file(frames))
    return tb_path(path)


REQUEST = b"GET /report.pdf HTTP/1.1\r\nHost: files.example.com\r\nUser-Agent: test\r\n\r\n"
BODY = bytes(range(256)) * 8  # 2048 bytes, not text
RESPONSE = (b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nContent-Length: 2048\r\n"
            b"Content-Disposition: attachment; filename=\"../../evil\\report.pdf\"\r\n\r\n") + BODY


def messy_capture():
    """The response is cut in 4 pieces sent out of order, one retransmitted, one overlapping."""
    frames = handshake()
    frames.append(up(REQUEST, seq=1001, ack=5001))
    pieces = [(0, 600), (600, 1200), (1200, 1800), (1800, len(RESPONSE))]
    seg = {i: RESPONSE[a:b] for i, (a, b) in enumerate(pieces)}
    base = 5001
    order = [(0, seg[0]), (2, seg[2]), (1, seg[1]), (1, seg[1]),             # 2 before 1, then 1 again
             (3, RESPONSE[1700:])]                                           # 3 starts 100 bytes early
    for i, data in order:
        start = pieces[i][0] if i != 3 else 1700
        frames.append(down(data, seq=base + start, ack=1001 + len(REQUEST)))
    frames.append(up(b"", "FA", 1001 + len(REQUEST), base + len(RESPONSE)))
    frames.append(down(b"", "FA", base + len(RESPONSE), 1002 + len(REQUEST)))
    return [(1000 + i * 0.01, f) for i, (_t, f) in enumerate(frames)]


@pytest.mark.parametrize("kind", ["pcap", "pcapng"])
def test_out_of_order_retransmitted_and_overlapping_segments(tmp_path, kind):
    path = write(tmp_path, messy_capture(), kind)
    out = run_tb(f'import pcap\nlet ss = pcap.streams(pcap.read("{path}"))\nsay(len(ss))\nlet s = ss[0]\n'
                 'say(s.src, s.sport, s.dst, s.dport, s.state, s.handshake, s.app, s.service)\n'
                 'say(s.client.bytes, s.server.bytes, s.server.packets, s.server.gaps, s.server.missing)\n'
                 'say(s.server.retransmissions, s.server.overlaps, s.server.out_of_order, s.server.truncated)\n'
                 'say(s.client.fin, s.server.fin, s.client.data.to_text().split("\\r\\n")[0])\n'
                 'say(s.server.data.hex())')
    assert out[0] == "1"
    assert out[1] == f"{C} 51000 {S} 80 closed true http http"
    assert out[2] == f"{len(REQUEST)} {len(RESPONSE)} 7 [] 0"
    assert out[3] == "1 1 1 false"
    assert out[4] == "true true GET /report.pdf HTTP/1.1"
    assert out[5] == RESPONSE.hex()


def test_extracted_file_has_the_right_hash_and_a_safe_name(tmp_path):
    path = write(tmp_path, messy_capture())
    out = run_tb(f'import pcap\nlet ps = pcap.read("{path}")\n'
                 'let e = pcap.http_exchanges(ps)[0]\n'
                 'say(e.method, e.url, e.status, e.reason, e.content_type, e.complete, e.stream, e.user_agent)\n'
                 'say(len(e.response_body), e.when)\n'
                 'let objs = pcap.http_objects(ps)\nsay(len(objs))\nlet o = objs[0]\n'
                 'say(o.name, o.direction, o.size, o.content_type, o.status, o.host)\nsay(o.sha256)\nsay(o.md5)\n'
                 'say(o.data.hex() == e.response_body.hex())')
    assert out[0] == "GET http://files.example.com/report.pdf 200 OK application/pdf true 1 test"
    assert out[1] == "2048 1970-01-01 00:16:40"
    assert out[2] == "1"
    assert out[3] == "report.pdf download 2048 application/pdf 200 files.example.com"
    assert out[4] == hashlib.sha256(BODY).hexdigest()
    assert out[5] == hashlib.md5(BODY).hexdigest()
    assert out[6] == "true"


def test_missing_segment_is_marked_as_a_gap(tmp_path):
    frames = handshake() + [up(b"AAAA", seq=1001), up(b"CCCC", seq=1009), up(b"DD", seq=1013)]  # BBBB lost
    path = write(tmp_path, [(1000 + i, f) for i, (_t, f) in enumerate(frames)])
    out = run_tb(f'import pcap\nlet s = pcap.streams(pcap.read("{path}"))[0]\n'
                 'say(s.client.data.to_text(), s.client.gaps, s.client.missing, s.state)')
    assert out == ['AAAACCCCDD [{"at": 4, "length": 4}] 4 established']


def test_sequence_numbers_that_wrap_around(tmp_path):
    isn = 0xFFFFFFFD
    frames = handshake(c_isn=isn) + [up(b"wxyz", seq=(isn + 5) & 0xFFFFFFFF), up(b"abcd", seq=(isn + 1) & 0xFFFFFFFF)]
    path = write(tmp_path, [(1000 + i, f) for i, (_t, f) in enumerate(frames)])
    out = run_tb(f'import pcap\nlet s = pcap.streams(pcap.read("{path}"))[0]\n'
                 'say(s.client.data.to_text(), s.client.out_of_order, s.client.gaps)')
    assert out == ["abcdwxyz 1 []"]


def test_connection_states_and_port_reuse(tmp_path):
    frames = [
        up(b"", "S", 10, 0, 52000, 81), down(b"", "RA", 0, 11, 52000, 81),                  # refused
        up(b"", "S", 20, 0, 52001, 82),                                                       # no reply
        up(b"hi", "PA", 30, 7, 52002, 83), down(b"yo", "PA", 7, 32, 52002, 83),              # partial
        *handshake(40, 90, 52003, 84), up(b"", "R", 41, 0, 52003, 84),                        # reset
        *handshake(50, 60, 52004, 85), up(b"", "FA", 51, 61, 52004, 85),                      # closing
        down(b"", "FA", 61, 52, 52004, 85), *handshake(500, 600, 52004, 85),                  # closed, reused
    ]
    path = write(tmp_path, [(1000 + i, f) for i, (_t, f) in enumerate(frames)])
    out = run_tb(f'import pcap\nlet ss = pcap.streams(pcap.read("{path}"))\n'
                 'say(ss.map(fn(s) { return s.state }).join(","))\n'
                 'say(ss.map(fn(s) { return s.dport }).join(","))\nsay(ss[2].src, ss[2].dst, ss[2].handshake)')
    assert out[0] == "refused,no_reply,partial,reset,closed,established"
    assert out[1] == "81,82,83,84,85,85"
    assert out[2] == f"{C} {S} false"


def test_chunked_gzip_keep_alive_head_and_continue(tmp_path):
    chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/plain\r\n\r\n" \
              b"5;note=x\r\nhello\r\n7\r\n, world\r\n0\r\nX-Trailer: 1\r\n\r\n"
    packed = gzip.compress(b"squeezed text " * 50)
    zipped = (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: %d\r\n\r\n" % len(packed)) + packed
    head = b"HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n"  # HEAD: no body follows
    upload = b"stolen,data\n1,2\n"
    reqs = (b"GET /a HTTP/1.1\r\nHost: h.example\r\n\r\n" b"GET /b.txt HTTP/1.1\r\nHost: h.example\r\n\r\n"
            b"HEAD /c HTTP/1.1\r\nHost: h.example\r\n\r\n"
            b"POST /up HTTP/1.1\r\nHost: h.example\r\nContent-Type: text/csv\r\nContent-Length: %d\r\n\r\n" % len(upload)
            + upload)
    resps = chunked + zipped + head + b"HTTP/1.1 100 Continue\r\n\r\n" + \
        b"HTTP/1.1 201 Created\r\nContent-Length: 2\r\n\r\nok"
    frames = handshake()
    # the client's requests split at awkward places, sent as three segments
    cuts = [0, 17, 60, len(reqs)]
    for a, b in zip(cuts, cuts[1:]):
        frames.append(up(reqs[a:b], seq=1001 + a, ack=5001))
    frames.append(down(resps[:40], seq=5001))
    frames.append(down(resps[40:], seq=5041))
    path = write(tmp_path, [(1000 + i, f) for i, (_t, f) in enumerate(frames)])
    out = run_tb(f'import pcap\nlet ps = pcap.read("{path}")\nlet ex = pcap.http_exchanges(ps)\nsay(len(ex))\n'
                 'for e in ex { say(e.method, e.path, e.status, len(e.response_body), e.complete) }\n'
                 'say(ex[0].response_body.to_text())\nsay(ex[1].response_body.to_text().slice(0, 14))\n'
                 'say(ex[3].request_body.to_text().trim())\n'
                 'for o in pcap.http_objects(ps) { say(o.name, o.direction, o.size, o.content_type) }')
    assert out[0] == "4"
    assert out[1:5] == ["GET /a 200 12 true", "GET /b.txt 200 700 true", "HEAD /c 200 0 true",
                        "POST /up 201 2 true"]
    assert out[5] == "hello, world"
    assert out[6] == "squeezed text "
    assert out[7] == "stolen,data\n1,2"
    assert out[8:] == ["a download 12 text/plain", "b.txt download 700 nil", "up download 2 nil",
                       "up upload 16 text/csv"]


def dns_frames_fragmented_v4():
    big = struct.pack("!HHHHHH", 7, 0x8180, 1, 1, 0, 0) + dns_name("big.example.org") + struct.pack("!HH", 16, 1)
    txt = b"x" * 200
    big += b"\xc0\x0c" + struct.pack("!HHIH", 16, 1, 60, len(txt) + 1) + bytes([len(txt)]) + txt
    dgram = udp(53, 5353, big)
    a, b = dgram[:96], dgram[96:]
    return dgram, [
        ipv4_fragment(S, C, 17, b, 96, False),            # last piece first
        ipv4_fragment(S, C, 17, a[:64], 0, True),
        ipv4_fragment(S, C, 17, a[48:], 48, True),        # overlaps the piece before
    ]


def test_ipv4_fragments_out_of_order_and_overlapping(tmp_path):
    _dgram, frags = dns_frames_fragmented_v4()
    lonely = ipv4_fragment(S, C, 17, b"z" * 16, 0, True, ident=0x99)  # never completed
    path = write(tmp_path, [(1000 + i, f) for i, f in enumerate(frags + [lonely])])
    saved = tb_path(tmp_path / "whole.pcap")
    out = run_tb(f'import pcap\nlet ps = pcap.read("{path}")\nsay(len(ps), ps[0].fragment.offset, ps[1].fragment.more)\n'
                 'let ws = pcap.defragment(ps)\nsay(len(ws))\nlet w = ws[0]\n'
                 'say(w.protocol, w.sport, w.dport, w.app, w.dns.name, w.fragments, w.number, w.has("fragment"))\n'
                 'say(ws[1].fragment.id, ws[1].number)\n'
                 f'pcap.write("{saved}", [w])\nlet back = pcap.read("{saved}")[0]\n'
                 'say(len(back.dns.answers[0].value), back.has("fragment"))')
    assert out[0] == "4 96 true"
    assert out[1] == "2"
    assert out[2] == "udp 53 5353 dns big.example.org [1, 2, 3] 3 false"
    assert out[3] == "153 4"
    assert out[4] == "200 false"


def test_ipv6_fragments_and_tcp_inside_fragments(tmp_path):
    payload = udp(5000, 6000, b"0123456789" * 20)
    v6 = [ipv6_fragment("2001:db8::1", "2001:db8::2", 17, payload[80:], 80, False),
          ipv6_fragment("2001:db8::1", "2001:db8::2", 17, payload[:80], 0, True)]
    seg = tcp(51000, 80, "PA", REQUEST, seq=1001, ack=5001)
    v4 = [ipv4_fragment(C, S, 6, seg[:40], 0, True, ident=7), ipv4_fragment(C, S, 6, seg[40:], 40, False, ident=7)]
    frames = [f for _t, f in handshake()] + v4 + v6
    path = write(tmp_path, [(1000 + i, f) for i, f in enumerate(frames)])
    out = run_tb(f'import pcap\nlet ps = pcap.read("{path}")\nlet ws = pcap.defragment(ps)\nsay(len(ps), len(ws))\n'
                 'let v6 = ws.last()\nsay(v6.ip_version, v6.protocol, v6.dport, v6.payload_length, v6.fragments)\n'
                 'let s = pcap.streams(ps)[0]\nsay(s.client.bytes, s.app)\n'
                 'say(pcap.http_exchanges(ps)[0].url)')
    assert out[0] == "7 5"
    assert out[1] == "6 udp 6000 200 [6, 7]"
    assert out[2] == f"{len(REQUEST)} http"
    assert out[3] == "http://files.example.com/report.pdf"


def test_max_bytes_caps_memory_and_bad_options(tmp_path):
    path = write(tmp_path, messy_capture())
    out = run_tb(f'import pcap\nlet ps = pcap.read("{path}")\n'
                 'let s = pcap.streams(ps, {"max_bytes": 100})[0]\n'
                 'say(s.server.bytes, s.server.truncated, s.client.truncated, len(s.client.data))\n'
                 'say(pcap.http_objects(ps, {"max_bytes": 100}).len())')
    assert out == [f"100 true false {len(REQUEST)}", "0"]
    assert "doesn't know the option" in str(tb_error('import pcap\npcap.streams([], {"cap": 1})'))
    assert "at least 1" in str(tb_error('import pcap\npcap.http_objects([], {"max_bytes": 0})'))
    assert "packet maps" in str(tb_error('import pcap\npcap.defragment([1])'))
    assert run_tb('import pcap\nsay(pcap.streams([]), pcap.http_exchanges([]), pcap.defragment([]))') == ["[] [] []"]


def test_object_names():
    from tblib.pcap import object_name
    assert object_name("http://h/x/y.exe?a=1", {}, None) == "y.exe"
    assert object_name("http://h/", {}, "text/html") == "index.html"
    assert object_name("http://h/", {}, None) == "object"
    assert object_name("http://h/a", {"content-disposition": "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.doc"},
                       None) == "résumé.doc"
    assert object_name("http://h/a", {"content-disposition": 'attachment; filename="C:\\Windows\\x.dll"'}, None) == "x.dll"
    assert object_name("http://h/a", {"content-disposition": "inline; filename=.."}, None) == "object"


# -- hostile input: each must be skipped quickly, never hang or abort the analysis --------------

def decoded(frames):
    from tblib.pcap import decode_frame
    return [decode_frame(1000.0 + i, len(f), 1, f, None, i + 1) for i, f in enumerate(frames)]


def one_exchange(tmp_path, resp, req=b"GET /x HTTP/1.1\r\nHost: h\r\n\r\n"):
    frames = handshake() + [up(req, seq=1001, ack=5001), down(resp, seq=5001, ack=1001 + len(req))]
    return write(tmp_path, [(1000 + i * 0.01, f) for i, (_t, f) in enumerate(frames)])


@pytest.mark.timeout(5)
def test_negative_or_odd_chunk_sizes_stop_instead_of_looping():
    from tblib.pcap import dechunk
    assert dechunk(b"-6\r\nXXXXXXXXXXXX", 0) == (b"", 16, False)
    for size in (b"+5", b"0x5", b"5 5", b"", b"\xb2"):
        assert dechunk(size + b"\r\nhello\r\n0\r\n\r\n", 0)[2] is False
    assert dechunk(b"5;ext=1\r\nhello\r\n-1\r\n", 0) == (b"hello", 20, False)
    assert dechunk(b"FFFFFFFFFFFFFFFFFFFF\r\nab", 0) == (b"", 24, False)


@pytest.mark.timeout(10)
def test_non_ascii_content_length_does_not_abort_the_capture(tmp_path):
    from tblib.pcap import http_body
    assert http_body(b"ab", 0, {"content-length": "²"}, True) == (b"ab", 2, True)
    assert http_body(b"ab", 0, {"content-length": "9" * 5000}, False) == (b"", 0, True)
    path = one_exchange(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: \xb2\r\n\r\nab")
    assert run_tb(f'import pcap\nlet o = pcap.http_objects(pcap.read("{path}"))\nsay(len(o), o[0].size)') == ["1 2"]
    path = one_exchange(tmp_path, b"HTTP/1.1 \xb2\xb2\xb2 OK\r\n\r\n")
    assert run_tb(f'import pcap\nsay(pcap.http_exchanges(pcap.read("{path}"))[0].status)') == ["nil"]


def short_ihl_fragment(chunk, offset, more, vihl):
    total = (vihl & 15) * 4 + len(chunk)
    head = struct.pack("!BBHHHBBH4s4s", vihl, 0, total, 7, (0x2000 if more else 0) | (offset // 8), 64, 17, 0,
                       ipaddress.IPv4Address(C).packed, ipaddress.IPv4Address(S).packed)
    return ethernet(MAC_WS, MAC_GW, 0x0800, head + chunk)


@pytest.mark.timeout(10)
def test_ipv4_header_length_below_20_or_past_the_packet_is_skipped(tmp_path):
    from tblib.pcap import defragment
    frames = [short_ihl_fragment(b"A" * 8, 0, True, 0x41), short_ihl_fragment(b"B" * 8, 8, False, 0x41),
              short_ihl_fragment(b"C" * 8, 0, True, 0x4F), short_ihl_fragment(b"D" * 8, 8, False, 0x4F)]
    ps = decoded(frames)
    assert all(p["truncated"] and "fragment" not in p for p in ps)
    assert len(defragment(ps)) == 4
    path = write(tmp_path, [(1000 + i, f) for i, f in enumerate(frames)])
    assert run_tb(f'import pcap\nlet ps = pcap.read("{path}")\n'
                  'say(len(pcap.defragment(ps)), pcap.streams(ps), pcap.http_objects(ps))') == ["4 [] []"]


@pytest.mark.timeout(10)
def test_fragment_floods_finish_quickly():
    from tblib.pcap import defragment
    no_final = decoded([ipv4_fragment(S, C, 17, b"A" * 8, 8 * (i % 8000), True) for i in range(50000)])
    assert len(defragment(no_final)) == 50000
    # the last piece first, then far more pieces than one datagram may have: the set is dropped
    flood = decoded([ipv4_fragment(S, C, 17, b"Z" * 8, 64000, False)] +
                    [ipv4_fragment(S, C, 17, b"A" * 8, 0, True) for _ in range(20000)])
    assert len(defragment(flood)) == 20001
    # a big datagram sent in 8000 eight-byte pieces, last piece first, still comes back whole
    body = udp(1, 2, bytes(range(256)) * 250)[:64000]
    pieces = [ipv4_fragment(S, C, 17, body[o:o + 8], o, o + 8 < len(body)) for o in range(0, len(body), 8)]
    out = defragment(decoded(pieces[-1:] + pieces[:-1]))
    assert len(out) == 1 and len(out[0]["fragments"]) == 8000 and out[0]["sport"] == 1


def test_decompression_budget_is_shared_by_the_whole_call(tmp_path):
    packed = gzip.compress(b"a" * 1500)
    one = (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: %d\r\n\r\n" % len(packed)) + packed
    req = b"GET /z HTTP/1.1\r\nHost: h\r\n\r\n"
    path = one_exchange(tmp_path, one * 7, req * 7)
    # max_bytes 2000: at most 8000 unpacked bytes per call, so five 1500-byte bodies, then raw ones
    out = run_tb(f'import pcap\nlet ps = pcap.read("{path}")\n'
                 'let ex = pcap.http_exchanges(ps, {"max_bytes": 2000})\n'
                 'say(ex.map(fn(e) { return len(e.response_body) }).join(","))\n'
                 'say(ex.map(fn(e) { return e.response_decoded }).join(","))\n'
                 'say(pcap.http_objects(ps, {"max_bytes": 2000}).map(fn(o) { return o.decoded }).join(","))\n'
                 'say(pcap.http_exchanges(ps)[6].response_decoded, ex[0].request_decoded)')
    raw = str(len(packed))
    assert out[0] == ",".join(["1500"] * 5 + [raw, raw])
    assert out[1] == out[2] == "true,true,true,true,true,false,false"
    assert out[3] == "true true"


def test_object_names_are_safe_on_windows(tmp_path):
    from tblib.pcap import object_name

    def named(filename):
        return object_name("http://h/a", {"content-disposition": f'attachment; filename="{filename}"'}, None)
    assert named("CON") == "_CON"
    assert named("nul.txt") == "_nul.txt"
    assert named("Com1.tar.gz") == "_Com1.tar.gz"
    assert named("lpt9") == "_lpt9"
    assert named("COM¹.txt") == "_COM¹.txt"
    assert named("console.txt") == "console.txt"
    assert named("report.pdf. . ") == "report.pdf"
    assert named("a<b>c|d?e*f\x01g.exe") == "a_b_c_d_e_f_g.exe"
    assert named("file.txt:hidden") == "hidden"
    assert named("..\\..\\x") == "x"
    assert named(". .") == "object"
    assert object_name("http://h/dir/AUX", {}, None) == "_AUX"
    path = one_exchange(tmp_path, b'HTTP/1.1 200 OK\r\nContent-Disposition: attachment; filename="prn. "\r\n'
                                  b'Content-Length: 2\r\n\r\nok')
    assert run_tb(f'import pcap\nsay(pcap.http_objects(pcap.read("{path}"))[0].name)') == ["_prn"]


def test_each_request_gets_the_time_of_its_own_segment(tmp_path):
    reqs = [b"GET /%d HTTP/1.1\r\nHost: h\r\n\r\n" % i for i in range(3)]
    frames, seq = handshake(), 1001
    for r in reqs:
        frames.append(up(r, seq=seq, ack=5001))
        seq += len(r)
    path = write(tmp_path, [(1000 + i, f) for i, (_t, f) in enumerate(frames)])
    out = run_tb(f'import pcap\nlet ex = pcap.http_exchanges(pcap.read("{path}"))\n'
                 'say(ex.map(fn(e) { return e.path + "@" + str(e.time) }).join(","))')
    assert out == ["/0@1003,/1@1004,/2@1005"]


def test_hand_made_or_damaged_packet_maps_are_skipped():
    from tblib.pcap import defragment, http_exchanges, reassemble
    junk = [{"protocol": "tcp", "seq": 1},
            {"protocol": "tcp", "seq": 1.5, "src": "a", "sport": 1, "dst": "b", "dport": 2, "flags": None},
            {"protocol": "tcp", "seq": "x", "src": "a", "sport": [1], "dst": "b", "dport": 2, "flags": ["SYN"]},
            {"protocol": "udp", "fragment": {"data": 1}},
            {"protocol": "udp", "fragment": {"data": b"", "id": [1], "protocol": 17}},
            {"protocol": "udp", "src": "a",
             "fragment": {"data": b"x", "id": 1, "protocol": 17, "offset": 0, "more": False}}]
    good = decoded([f for _t, f in handshake()] + [up(REQUEST, seq=1001, ack=5001)[1]])
    assert len(defragment(junk)) == len(junk)
    assert [s["app"] for s, _m in reassemble(junk + good, 1000)] == ["http"]
    assert http_exchanges(junk + good, 1000)[0]["path"] == "/report.pdf"
    assert run_tb('import pcap\nsay(pcap.streams([{"protocol": "tcp", "seq": 1}]))') == ["[]"]
