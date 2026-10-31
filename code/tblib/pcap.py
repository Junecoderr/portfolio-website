"""pcap: read, decode, summarise and write network captures (pcap and pcapng)."""

from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import ipaddress
import os
import re
import struct
import urllib.parse
import zlib

from tblib import _magic
from tblib._api import Module, check_type, fail, fail_os

MAX_PACKETS = 1_000_000
MAX_FILE = 2 * 1024 * 1024 * 1024

LINK_TYPES = {0: "null", 1: "ethernet", 12: "raw", 101: "raw", 113: "linux_sll", 228: "ipv4", 229: "ipv6",
              276: "linux_sll2", 105: "ieee802_11", 127: "radiotap"}

TCP_FLAGS = [(0x01, "FIN"), (0x02, "SYN"), (0x04, "RST"), (0x08, "PSH"), (0x10, "ACK"), (0x20, "URG"),
             (0x40, "ECE"), (0x80, "CWR")]

IP_PROTOCOLS = {1: "icmp", 2: "igmp", 6: "tcp", 17: "udp", 41: "ipv6", 47: "gre", 50: "esp", 51: "ah", 58: "icmpv6",
                89: "ospf", 132: "sctp"}

DNS_TYPES = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX", 16: "TXT", 28: "AAAA", 33: "SRV",
             35: "NAPTR", 41: "OPT", 43: "DS", 46: "RRSIG", 47: "NSEC", 48: "DNSKEY", 52: "TLSA", 65: "HTTPS",
             99: "SPF", 255: "ANY", 257: "CAA", 64: "SVCB"}
DNS_RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}

HTTP_METHODS = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"DELETE ", b"OPTIONS ", b"PATCH ", b"CONNECT ", b"TRACE ",
                b"PROPFIND ")

TLS_VERSIONS = {0x0300: "SSL 3.0", 0x0301: "TLS 1.0", 0x0302: "TLS 1.1", 0x0303: "TLS 1.2", 0x0304: "TLS 1.3"}

WELL_KNOWN_PORTS = {20: "ftp-data", 21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 67: "dhcp", 68: "dhcp",
                    69: "tftp", 80: "http", 88: "kerberos", 110: "pop3", 111: "rpc", 123: "ntp", 135: "msrpc",
                    137: "netbios", 138: "netbios", 139: "netbios", 143: "imap", 161: "snmp", 389: "ldap",
                    443: "https", 445: "smb", 465: "smtps", 514: "syslog", 587: "submission", 636: "ldaps",
                    993: "imaps", 995: "pop3s", 1433: "mssql", 1521: "oracle", 1723: "pptp", 2049: "nfs",
                    3306: "mysql", 3389: "rdp", 4444: "metasploit", 5060: "sip", 5432: "postgres", 5900: "vnc",
                    5985: "winrm", 6379: "redis", 8080: "http-alt", 8443: "https-alt", 9200: "elasticsearch",
                    27017: "mongodb"}


class CaptureFormat(Exception):
    pass


def when_text(ts) -> str:
    """UTC, like analysts compare captures across time zones."""
    try:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def tidy(ts: float):
    return round(ts, 6)


# =============================================================================
# File readers
# =============================================================================

def iter_frames(data: bytes):
    """Yield (timestamp, original_length, link_type, frame_bytes, interface_name|None) from pcap or pcapng bytes."""
    if len(data) < 4:
        raise CaptureFormat("the file is too short to be a capture")
    magic = data[:4]
    if magic == b"\x0a\x0d\x0d\x0a":
        yield from iter_pcapng(data)
        return
    if magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian, nanos = ">", magic == b"\xa1\xb2\x3c\x4d"
    elif magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian, nanos = "<", magic == b"\x4d\x3c\xb2\xa1"
    else:
        kind = _magic.sniff(data)
        hint = f" It looks like {kind['description']}." if kind["type"] not in ("pcap", "pcapng", "unknown") else ""
        raise CaptureFormat(f"it doesn't start with a pcap or pcapng magic number.{hint}")
    if len(data) < 24:
        raise CaptureFormat("the pcap header is cut off")
    _vmaj, _vmin, _zone, _sig, snaplen, link = struct.unpack_from(endian + "HHiIII", data, 4)
    link &= 0x0FFFFFFF
    pos = 24
    count = 0
    while pos + 16 <= len(data):
        sec, frac, incl, orig = struct.unpack_from(endian + "IIII", data, pos)
        pos += 16
        if incl > len(data) - pos:
            incl = len(data) - pos  # the last packet was cut off; keep what there is
        frame = data[pos:pos + incl]
        pos += incl
        ts = sec + frac / (1e9 if nanos else 1e6)
        yield tidy(ts), orig, link, frame, None
        count += 1
        if count >= MAX_PACKETS:
            return


def iter_pcapng(data: bytes):
    pos = 0
    endian = "<"
    interfaces = []
    count = 0
    while pos + 12 <= len(data):
        block_type = struct.unpack_from(endian + "I", data, pos)[0]
        if block_type == 0x0A0D0D0A:  # section header: byte order from the magic
            bom = data[pos + 8:pos + 12]
            if bom == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif bom == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise CaptureFormat("the pcapng section header has a bad byte-order mark")
            interfaces = []
        length = struct.unpack_from(endian + "I", data, pos + 4)[0]
        if length < 12 or length % 4 or pos + length > len(data):
            if block_type in (6, 3, 2) and pos + length > len(data):
                return  # cut-off last block
            raise CaptureFormat(f"a pcapng block at offset {pos} has an impossible length {length}")
        body = data[pos + 8:pos + length - 4]
        if block_type == 1:  # interface description
            link, _res, snaplen = struct.unpack_from(endian + "HHI", body, 0)
            iface = {"link": link, "snaplen": snaplen, "tsresol": 6, "name": None}
            for code, value in iter_options(body[8:], endian):
                if code == 2:
                    iface["name"] = value.decode("utf-8", "replace").rstrip("\x00")
                elif code == 9 and value:
                    r = value[0]
                    iface["tsresol"] = r if not (r & 0x80) else -(r & 0x7F)
            interfaces.append(iface)
        elif block_type == 6:  # enhanced packet
            iface_id, ts_hi, ts_lo, captured, orig = struct.unpack_from(endian + "IIIII", body, 0)
            frame = body[20:20 + captured]
            iface = interfaces[iface_id] if iface_id < len(interfaces) else {"link": 1, "tsresol": 6, "name": None}
            ts = ((ts_hi << 32) | ts_lo)
            res = iface["tsresol"]
            ts = ts / (10 ** res) if res >= 0 else ts / (2 ** -res)
            yield tidy(ts), orig, iface["link"], frame, iface["name"]
            count += 1
        elif block_type == 3:  # simple packet
            (orig,) = struct.unpack_from(endian + "I", body, 0)
            iface = interfaces[0] if interfaces else {"link": 1, "name": None}
            frame = body[4:4 + orig]
            yield 0.0, orig, iface["link"], frame, iface["name"]
            count += 1
        elif block_type == 2:  # obsolete packet block
            iface_id, _drops, ts_hi, ts_lo, captured, orig = struct.unpack_from(endian + "HHIIII", body, 0)
            frame = body[20:20 + captured]
            iface = interfaces[iface_id] if iface_id < len(interfaces) else {"link": 1, "tsresol": 6, "name": None}
            ts = ((ts_hi << 32) | ts_lo) / 1e6
            yield tidy(ts), orig, iface["link"], frame, iface["name"]
            count += 1
        pos += length
        if count >= MAX_PACKETS:
            return


def iter_options(data: bytes, endian: str):
    pos = 0
    while pos + 4 <= len(data):
        code, length = struct.unpack_from(endian + "HH", data, pos)
        pos += 4
        if code == 0:
            return
        yield code, data[pos:pos + length]
        pos += (length + 3) // 4 * 4


# =============================================================================
# Decoders
# =============================================================================

def mac(data: bytes) -> str:
    return ":".join(f"{b:02x}" for b in data[:6])


def ip4(data: bytes) -> str:
    return ".".join(str(b) for b in data[:4])


def ip6(data: bytes) -> str:
    return str(ipaddress.IPv6Address(bytes(data[:16])))


def decode_frame(ts, orig_len, link, frame: bytes, iface_name, number: int) -> dict:
    p = {"number": number, "time": ts, "when": when_text(ts), "length": orig_len, "captured": len(frame),
         "link": LINK_TYPES.get(link, f"linktype_{link}"), "protocol": None, "src": None, "dst": None,
         "sport": None, "dport": None, "app": None, "payload": b"", "payload_length": 0, "raw": frame,
         "link_type": link}
    if iface_name:
        p["interface"] = iface_name
    try:
        if link == 1:
            decode_ethernet(p, frame)
        elif link in (101, 12, 228, 229):
            decode_ip_auto(p, frame)
        elif link == 113:
            if len(frame) >= 16:
                p["ethertype"] = struct.unpack_from(">H", frame, 14)[0]
                decode_ethertype(p, p["ethertype"], frame[16:])
        elif link == 276:
            if len(frame) >= 20:
                p["ethertype"] = struct.unpack_from(">H", frame, 0)[0]
                decode_ethertype(p, p["ethertype"], frame[20:])
        elif link == 0:
            if len(frame) >= 4:
                family = struct.unpack_from("<I", frame, 0)[0]
                if family > 0xFFFF:
                    family = struct.unpack_from(">I", frame, 0)[0]
                if family == 2:
                    decode_ipv4(p, frame[4:])
                elif family in (24, 28, 30, 10):
                    decode_ipv6(p, frame[4:])
        else:
            p["protocol"] = p["link"]
    except (struct.error, IndexError, ValueError):
        p["truncated"] = True
    p["summary"] = summarize(p)
    return p


def decode_ethernet(p: dict, frame: bytes):
    if len(frame) < 14:
        p["protocol"] = "ethernet"
        return
    p["dst_mac"], p["src_mac"] = mac(frame[0:6]), mac(frame[6:12])
    ethertype = struct.unpack_from(">H", frame, 12)[0]
    pos = 14
    vlans = []
    while ethertype in (0x8100, 0x88A8, 0x9100) and pos + 4 <= len(frame):
        tci, ethertype = struct.unpack_from(">HH", frame, pos)
        vlans.append(tci & 0x0FFF)
        pos += 4
    if vlans:
        p["vlan"] = vlans[0] if len(vlans) == 1 else vlans
    p["ethertype"] = ethertype
    decode_ethertype(p, ethertype, frame[pos:])


def decode_ethertype(p: dict, ethertype: int, body: bytes):
    if ethertype == 0x0800:
        decode_ipv4(p, body)
    elif ethertype == 0x86DD:
        decode_ipv6(p, body)
    elif ethertype == 0x0806:
        decode_arp(p, body)
    elif ethertype == 0x88CC:
        p["protocol"] = "lldp"
    elif ethertype <= 1500:
        p["protocol"] = "llc"
    else:
        p["protocol"] = f"ethertype_0x{ethertype:04x}"


def decode_ip_auto(p: dict, body: bytes):
    if not body:
        return
    version = body[0] >> 4
    if version == 4:
        decode_ipv4(p, body)
    elif version == 6:
        decode_ipv6(p, body)


def decode_arp(p: dict, body: bytes):
    p["protocol"] = "arp"
    if len(body) < 8:
        return
    _hw, _proto, hlen, plen, op = struct.unpack_from(">HHBBH", body, 0)
    pos = 8
    sha = body[pos:pos + hlen]
    spa = body[pos + hlen:pos + hlen + plen]
    tha = body[pos + hlen + plen:pos + 2 * hlen + plen]
    tpa = body[pos + 2 * hlen + plen:pos + 2 * hlen + 2 * plen]
    p["arp"] = {"operation": {1: "request", 2: "reply"}.get(op, str(op)), "sender_mac": mac(sha),
                "sender_ip": ip4(spa) if plen == 4 else spa.hex(), "target_mac": mac(tha),
                "target_ip": ip4(tpa) if plen == 4 else tpa.hex()}
    p["src"], p["dst"] = p["arp"]["sender_ip"], p["arp"]["target_ip"]


def decode_ipv4(p: dict, body: bytes):
    if len(body) < 20:
        p["protocol"] = "ipv4"
        return
    vihl, tos, total, ident, frag, ttl, proto, _csum = struct.unpack_from(">BBHHHBBH", body, 0)
    ihl = (vihl & 0x0F) * 4
    p["ip_version"] = 4
    p["src"], p["dst"] = ip4(body[12:16]), ip4(body[16:20])
    p["ttl"], p["ip_id"], p["ip_protocol"] = ttl, ident, proto
    p["protocol"] = IP_PROTOCOLS.get(proto, f"ip_proto_{proto}")
    if ihl < 20 or ihl > len(body):
        p["truncated"] = True  # a header length no real IPv4 packet has: nothing after it can be trusted
        return
    offset = (frag & 0x1FFF) * 8
    more = bool(frag & 0x2000)
    end = min(total, len(body)) if total >= ihl else len(body)
    payload = body[ihl:end]
    if offset or more:
        p["fragment"] = {"offset": offset, "more": more, "id": ident, "protocol": proto, "data": payload,
                         "ip_at": len(p["raw"]) - len(body)}
    if offset:
        p["payload"], p["payload_length"] = payload, len(payload)
        return
    decode_transport(p, proto, payload)


def decode_ipv6(p: dict, body: bytes):
    if len(body) < 40:
        p["protocol"] = "ipv6"
        return
    _vtf, plen, nxt, hop = struct.unpack_from(">IHBB", body, 0)
    p["ip_version"] = 6
    p["src"], p["dst"] = ip6(body[8:24]), ip6(body[24:40])
    p["ttl"] = hop
    pos = 40
    end = min(40 + plen, len(body)) if plen else len(body)
    while nxt in (0, 43, 60, 51, 44, 135) and pos + 8 <= len(body):
        if nxt == 44:
            nxt_header, _res, frag_off, ident = struct.unpack_from(">BBHI", body, pos)
            nxt, pos = nxt_header, pos + 8
            p["fragment"] = {"offset": (frag_off >> 3) * 8, "more": bool(frag_off & 1), "id": ident,
                             "protocol": nxt, "data": body[pos:end], "ip_at": len(p["raw"]) - len(body)}
            if p["fragment"]["offset"]:
                p["protocol"] = IP_PROTOCOLS.get(nxt, f"ip_proto_{nxt}")
                p["payload"] = body[pos:end]
                p["payload_length"] = len(p["payload"])
                return
            continue
        nxt_header, hlen = body[pos], body[pos + 1]
        pos += (hlen + 2) * 4 if nxt == 51 else (hlen + 1) * 8
        nxt = nxt_header
    p["ip_protocol"] = nxt
    p["protocol"] = IP_PROTOCOLS.get(nxt, f"ip_proto_{nxt}")
    decode_transport(p, nxt, body[pos:end])


def decode_transport(p: dict, proto: int, payload: bytes):
    if proto == 6:
        decode_tcp(p, payload)
    elif proto == 17:
        decode_udp(p, payload)
    elif proto in (1, 58):
        if len(payload) >= 4:
            p["icmp"] = {"type": payload[0], "code": payload[1], "name": icmp_name(proto, payload[0], payload[1])}
            if proto == 1 and payload[0] in (0, 8) and len(payload) >= 8:
                p["icmp"]["id"], p["icmp"]["seq"] = struct.unpack_from(">HH", payload, 4)
        p["payload"], p["payload_length"] = payload[8:], max(0, len(payload) - 8)
    else:
        p["payload"], p["payload_length"] = payload, len(payload)


def icmp_name(proto, kind, code):
    if proto == 1:
        return {0: "echo_reply", 3: {0: "net_unreachable", 1: "host_unreachable", 3: "port_unreachable",
                                     13: "admin_prohibited"}.get(code, "unreachable"),
                5: "redirect", 8: "echo_request", 11: "time_exceeded"}.get(kind, f"type_{kind}")
    return {1: "destination_unreachable", 2: "packet_too_big", 3: "time_exceeded", 128: "echo_request",
            129: "echo_reply", 133: "router_solicitation", 134: "router_advertisement", 135: "neighbor_solicitation",
            136: "neighbor_advertisement"}.get(kind, f"type_{kind}")


def decode_tcp(p: dict, seg: bytes):
    if len(seg) < 20:
        return
    sport, dport, seq, ack, off_flags, window, _csum, _urg = struct.unpack_from(">HHIIHHHH", seg, 0)
    hlen = (off_flags >> 12) * 4
    flags = off_flags & 0x1FF
    p["sport"], p["dport"], p["seq"], p["ack"], p["window"] = sport, dport, seq, ack, window
    p["flags"] = [name for bit, name in TCP_FLAGS if flags & bit]
    payload = seg[hlen:] if hlen >= 20 else b""
    p["payload"], p["payload_length"] = payload, len(payload)
    p["service"] = WELL_KNOWN_PORTS.get(dport) or WELL_KNOWN_PORTS.get(sport)
    if payload:
        decode_app(p, payload, tcp=True)


def decode_udp(p: dict, dgram: bytes):
    if len(dgram) < 8:
        return
    sport, dport, length, _csum = struct.unpack_from(">HHHH", dgram, 0)
    p["sport"], p["dport"] = sport, dport
    payload = dgram[8:length] if 8 <= length <= len(dgram) else dgram[8:]
    p["payload"], p["payload_length"] = payload, len(payload)
    p["service"] = WELL_KNOWN_PORTS.get(dport) or WELL_KNOWN_PORTS.get(sport)
    if payload:
        decode_app(p, payload, tcp=False)


def decode_app(p: dict, payload: bytes, tcp: bool):
    sport, dport = p["sport"], p["dport"]
    if 53 in (sport, dport) or 5353 in (sport, dport):
        body = payload[2:] if tcp and len(payload) > 2 else payload
        dns = decode_dns(body)
        if dns:
            p["dns"], p["app"] = dns, "dns"
            return
    if payload[:1] == b"\x16" and len(payload) >= 6 and payload[1:2] == b"\x03":
        tls = decode_tls(payload)
        if tls:
            p["tls"], p["app"] = tls, "tls"
            return
    if payload.startswith(HTTP_METHODS) or payload.startswith(b"HTTP/1."):
        http = decode_http(payload)
        if http:
            p["http"], p["app"] = http, "http"
            return
    if tcp and payload.startswith(b"SSH-"):
        p["app"] = "ssh"
        p["ssh"] = {"banner": payload.split(b"\r\n")[0].decode("latin-1")[:128]}
    elif not tcp and 123 in (sport, dport) and len(payload) >= 48:
        p["app"] = "ntp"
    elif not tcp and (67 in (sport, dport) or 68 in (sport, dport)) and len(payload) >= 240:
        p["app"] = "dhcp"
    elif tcp and 445 in (sport, dport) and payload[4:8] in (b"\xffSMB", b"\xfeSMB"):
        p["app"] = "smb"


# -- DNS ------------------------------------------------------------------------

def read_name(msg: bytes, pos: int, depth=0):
    labels = []
    jumped = False
    end = pos
    for _ in range(128):
        if pos >= len(msg):
            raise ValueError("name runs past the message")
        length = msg[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(msg):
                raise ValueError("bad pointer")
            pointer = ((length & 0x3F) << 8) | msg[pos + 1]
            if not jumped:
                end = pos + 2
            jumped = True
            if pointer >= pos or depth > 10:
                raise ValueError("pointer loop")
            pos = pointer
            depth += 1
            continue
        labels.append(msg[pos + 1:pos + 1 + length].decode("latin-1"))
        pos += 1 + length
    if not jumped:
        end = pos
    return ".".join(labels) or ".", end


def decode_dns(msg: bytes):
    if len(msg) < 12:
        return None
    try:
        ident, flags, qd, an, ns, ar = struct.unpack_from(">HHHHHH", msg, 0)
        if qd > 64 or an > 1024:
            return None
        pos = 12
        questions = []
        for _ in range(qd):
            name, pos = read_name(msg, pos)
            qtype, qclass = struct.unpack_from(">HH", msg, pos)
            pos += 4
            questions.append({"name": name, "type": DNS_TYPES.get(qtype, str(qtype))})
        answers = []
        for section, count in (("answer", an), ("authority", ns), ("additional", ar)):
            for _ in range(count):
                if pos >= len(msg):
                    break
                name, pos = read_name(msg, pos)
                rtype, rclass, ttl, rdlen = struct.unpack_from(">HHIH", msg, pos)
                pos += 10
                rdata = msg[pos:pos + rdlen]
                value = None
                if rtype == 1 and rdlen == 4:
                    value = ip4(rdata)
                elif rtype == 28 and rdlen == 16:
                    value = ip6(rdata)
                elif rtype in (2, 5, 12, 39):
                    value, _ = read_name(msg, pos)
                elif rtype == 15 and rdlen >= 3:
                    value, _ = read_name(msg, pos + 2)
                elif rtype == 16:
                    parts, q = [], 0
                    while q < len(rdata):
                        n = rdata[q]
                        parts.append(rdata[q + 1:q + 1 + n].decode("latin-1"))
                        q += 1 + n
                    value = "".join(parts)
                elif rtype == 6:
                    mname, q = read_name(msg, pos)
                    rname, _ = read_name(msg, q)
                    value = f"{mname} {rname}"
                elif rtype == 33 and rdlen >= 7:
                    prio, weight, port = struct.unpack_from(">HHH", rdata, 0)
                    target, _ = read_name(msg, pos + 6)
                    value = f"{target}:{port}"
                elif rtype != 41:
                    value = rdata.hex()
                pos += rdlen
                if rtype != 41:
                    answers.append({"name": name, "type": DNS_TYPES.get(rtype, str(rtype)), "ttl": ttl,
                                    "value": value, "section": section})
    except (struct.error, ValueError, IndexError):
        return None
    response = bool(flags & 0x8000)
    return {"id": ident, "response": response, "opcode": (flags >> 11) & 0xF,
            "rcode": DNS_RCODES.get(flags & 0xF, str(flags & 0xF)), "truncated": bool(flags & 0x0200),
            "recursion_desired": bool(flags & 0x0100), "authoritative": bool(flags & 0x0400),
            "questions": questions, "answers": [a for a in answers if a["section"] == "answer"],
            "records": answers, "name": questions[0]["name"] if questions else None,
            "type": questions[0]["type"] if questions else None}


# -- HTTP -----------------------------------------------------------------------

def decode_http(payload: bytes):
    head, _, body = payload.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines:
        return None
    first = lines[0].decode("latin-1", "replace")
    parts = first.split(" ", 2)
    headers = {}
    for raw in lines[1:]:
        if b":" not in raw:
            continue
        key, _, value = raw.partition(b":")
        headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1", "replace").strip()
    if payload.startswith(b"HTTP/1."):
        if len(parts) < 2 or not parts[1].isdigit():
            return None
        return {"kind": "response", "version": parts[0], "status": int(parts[1]),
                "reason": parts[2] if len(parts) > 2 else "", "headers": headers,
                "content_type": headers.get("content-type"), "server": headers.get("server"),
                "content_length": int(headers["content-length"]) if headers.get("content-length", "").isdigit() else None,
                "body_preview": body[:256].decode("latin-1", "replace")}
    if len(parts) < 2:
        return None
    method, path = parts[0], parts[1]
    version = parts[2] if len(parts) > 2 else ""
    host = headers.get("host")
    return {"kind": "request", "method": method, "path": path, "version": version, "host": host,
            "url": f"http://{host}{path}" if host else path, "headers": headers,
            "user_agent": headers.get("user-agent"), "content_type": headers.get("content-type"),
            "body_preview": body[:256].decode("latin-1", "replace")}


# -- TLS ------------------------------------------------------------------------

def decode_tls(payload: bytes):
    try:
        content, version, length = struct.unpack_from(">BHH", payload, 0)
        if content != 0x16:
            return None
        pos = 5
        hs_type = payload[pos]
        hs_len = int.from_bytes(payload[pos + 1:pos + 4], "big")
        pos += 4
        if hs_type not in (1, 2):
            return {"handshake": f"type_{hs_type}", "record_version": TLS_VERSIONS.get(version, hex(version))}
        hello_version = struct.unpack_from(">H", payload, pos)[0]
        pos += 2 + 32  # version + random
        sid_len = payload[pos]
        pos += 1 + sid_len
        info = {"handshake": "client_hello" if hs_type == 1 else "server_hello",
                "record_version": TLS_VERSIONS.get(version, hex(version)),
                "version": TLS_VERSIONS.get(hello_version, hex(hello_version)), "sni": None, "alpn": [],
                "cipher_suites": [], "cipher_suite": None}
        if hs_type == 1:
            cs_len = struct.unpack_from(">H", payload, pos)[0]
            pos += 2
            suites = []
            for i in range(0, min(cs_len, len(payload) - pos), 2):
                suites.append(struct.unpack_from(">H", payload, pos + i)[0])
            info["cipher_suites"] = [f"0x{s:04x}" for s in suites if (s & 0x0F0F) != 0x0A0A]
            pos += cs_len
            comp_len = payload[pos]
            pos += 1 + comp_len
        else:
            info["cipher_suite"] = f"0x{struct.unpack_from('>H', payload, pos)[0]:04x}"
            pos += 3
        if pos + 2 <= len(payload):
            ext_len = struct.unpack_from(">H", payload, pos)[0]
            pos += 2
            end = min(pos + ext_len, len(payload))
            while pos + 4 <= end:
                etype, elen = struct.unpack_from(">HH", payload, pos)
                pos += 4
                ext = payload[pos:pos + elen]
                pos += elen
                if etype == 0 and len(ext) >= 5:
                    name_len = struct.unpack_from(">H", ext, 3)[0]
                    info["sni"] = ext[5:5 + name_len].decode("latin-1", "replace")
                elif etype == 16 and len(ext) >= 3:
                    q, protos = 2, []
                    while q < len(ext):
                        n = ext[q]
                        protos.append(ext[q + 1:q + 1 + n].decode("latin-1", "replace"))
                        q += 1 + n
                    info["alpn"] = protos
                elif etype == 43 and ext:
                    if hs_type == 1 and len(ext) >= 3:
                        versions = [struct.unpack_from(">H", ext, 1 + i)[0] for i in range(0, ext[0], 2) if 1 + i + 2 <= len(ext)]
                        real = [TLS_VERSIONS[v] for v in versions if v in TLS_VERSIONS]
                        if real:
                            info["version"] = real[0]
                    elif len(ext) == 2:
                        v = struct.unpack_from(">H", ext, 0)[0]
                        info["version"] = TLS_VERSIONS.get(v, info["version"])
        return info
    except (struct.error, IndexError):
        return None


# -- one-line summary -------------------------------------------------------------

def endpoint(ip, port):
    if ip is None:
        return "?"
    if port is None:
        return ip
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def summarize(p: dict) -> str:
    proto = (p.get("protocol") or p.get("link") or "?").upper()
    if p.get("protocol") == "arp" and p.get("arp"):
        a = p["arp"]
        if a["operation"] == "request":
            return f"ARP who has {a['target_ip']}? tell {a['sender_ip']}"
        return f"ARP {a['sender_ip']} is at {a['sender_mac']}"
    head = f"{endpoint(p.get('src'), p.get('sport'))} -> {endpoint(p.get('dst'), p.get('dport'))} {proto}"
    if p.get("flags"):
        head += " [" + " ".join(p["flags"]) + "]"
    if p.get("icmp"):
        head += f" {p['icmp']['name']}"
    app = p.get("app")
    if app == "dns" and p.get("dns"):
        d = p["dns"]
        if d["response"]:
            values = [str(a["value"]) for a in d["answers"] if a["value"] is not None][:3]
            head += f" DNS response {d['name']} {d['rcode']}" + (f" -> {', '.join(values)}" if values else "")
        else:
            head += f" DNS query {d['type']} {d['name']}"
    elif app == "http" and p.get("http"):
        h = p["http"]
        if h["kind"] == "request":
            head += f" HTTP {h['method']} {h['url']}"
        else:
            head += f" HTTP {h['status']} {h['reason']}".rstrip()
    elif app == "tls" and p.get("tls"):
        t = p["tls"]
        head += f" TLS {t['handshake'].replace('_', ' ')}" + (f" SNI {t['sni']}" if t.get("sni") else "")
        if t.get("version"):
            head += f" ({t['version']})"
    elif app:
        head += f" {app.upper()}"
    if p.get("payload_length"):
        head += f" len={p['payload_length']}"
    return head


# =============================================================================
# Reading from TB
# =============================================================================

def read_capture_bytes(line, path, fn):
    check_type(line, path, "text", fn, "path")
    if path == "":
        fail(line, f"{fn}() needs a path, but got empty text.")
    full = os.path.expanduser(path)
    if os.path.isdir(full):
        fail(line, f"'{path}' is a folder, not a file.", path=path)
    try:
        if os.path.getsize(full) > MAX_FILE:
            fail(line, f"'{path}' is bigger than 2 GB; pcap reads whole files, so split it first.", path=path)
        with open(full, "rb") as f:
            return f.read()
    except OSError as err:
        fail_os(line, err, path, "read")


def packets_from(line, data: bytes, fn, path=None, limit=None):
    out = []
    try:
        for n, (ts, orig, link, frame, iface) in enumerate(iter_frames(data), 1):
            if limit is not None and len(out) >= limit:
                break
            out.append(decode_frame(ts, orig, link, frame, iface, n))
    except CaptureFormat as err:
        what = f"'{path}'" if path else "that data"
        fail(line, f"{what} isn't a capture I can read: {err}", path=path)
    except struct.error:
        what = f"'{path}'" if path else "that data"
        fail(line, f"{what} is a cut-off or damaged capture.", path=path)
    return out


def capture_format(data: bytes) -> str:
    if data[:4] == b"\x0a\x0d\x0d\x0a":
        return "pcapng"
    if data[:4] in (b"\xa1\xb2\x3c\x4d", b"\x4d\x3c\xb2\xa1"):
        return "pcap-ns"
    return "pcap"


def check_packets(line, packets, fn):
    check_type(line, packets, "list", fn, "packets")
    for p in packets:
        if not isinstance(p, dict) or "protocol" not in p:
            fail(line, f"{fn}() needs the packet maps pcap.read() gives back.")
    return packets


# =============================================================================
# Analysis
# =============================================================================

def flow_key(p):
    a = (p.get("src"), p.get("sport"))
    b = (p.get("dst"), p.get("dport"))
    proto = p.get("protocol")
    if proto in ("tcp", "udp"):
        pair = tuple(sorted([a, b], key=lambda x: (str(x[0]), x[1] or 0)))
    else:
        pair = tuple(sorted([(p.get("src"), None), (p.get("dst"), None)], key=lambda x: str(x[0])))
    return (proto,) + pair


def build_flows(packets: list) -> list:
    flows = {}
    for p in packets:
        if p.get("src") is None:
            continue
        key = flow_key(p)
        f = flows.get(key)
        forward = True
        if f is None:
            f = {"protocol": p["protocol"], "src": p["src"], "sport": p.get("sport"), "dst": p["dst"],
                 "dport": p.get("dport"), "packets": 0, "bytes": 0, "client_bytes": 0, "server_bytes": 0,
                 "client_packets": 0, "server_packets": 0, "first": p["time"], "last": p["time"],
                 "first_when": p["when"], "last_when": p["when"], "flags": [], "app": None, "service": p.get("service"),
                 "client_flags": set(), "server_flags": set(), "names": []}
            flows[key] = f
        else:
            forward = (p["src"], p.get("sport")) == (f["src"], f["sport"])
        f["packets"] += 1
        f["bytes"] += p["length"]
        if forward:
            f["client_bytes"] += p.get("payload_length", 0)
            f["client_packets"] += 1
        else:
            f["server_bytes"] += p.get("payload_length", 0)
            f["server_packets"] += 1
        if p["time"] < f["first"]:
            f["first"], f["first_when"] = p["time"], p["when"]
        if p["time"] > f["last"]:
            f["last"], f["last_when"] = p["time"], p["when"]
        for flag in p.get("flags") or []:
            if flag not in f["flags"]:
                f["flags"].append(flag)
        if p["protocol"] == "tcp":
            f["client_flags" if forward else "server_flags"].update(p.get("flags") or [])
        if p.get("app") and not f["app"]:
            f["app"] = p["app"]
        name = None
        if p.get("app") == "tls" and p.get("tls", {}).get("sni"):
            name = p["tls"]["sni"]
        elif p.get("app") == "http" and p.get("http", {}).get("host"):
            name = p["http"]["host"]
        elif p.get("app") == "dns" and p.get("dns", {}).get("name"):
            name = p["dns"]["name"]
        if name and name not in f["names"]:
            f["names"].append(name)
    result = []
    for f in flows.values():
        f["duration"] = round(f["last"] - f["first"], 6)
        client, server = f.pop("client_flags"), f.pop("server_flags")
        if f["protocol"] == "tcp":
            if "SYN" in client and "SYN" in server and "ACK" in client:
                f["state"] = "established"
            elif "SYN" in client and "RST" in server:
                f["state"] = "refused"
            elif "SYN" in client and not server:
                f["state"] = "no_reply"
            elif "SYN" in client and "SYN" in server:
                f["state"] = "half_open"
            else:
                f["state"] = "other"
        result.append(f)
    result.sort(key=lambda f: (f["first"], str(f["src"])))
    return result


def top_talkers(packets: list, n: int) -> list:
    stats = {}
    for p in packets:
        for ip, direction in ((p.get("src"), "sent"), (p.get("dst"), "received")):
            if ip is None:
                continue
            s = stats.setdefault(ip, {"ip": ip, "packets": 0, "bytes": 0, "sent": 0, "received": 0,
                                      "sent_packets": 0, "received_packets": 0, "peers": set()})
            s["packets"] += 1
            s["bytes"] += p["length"]
            s[direction] += p["length"]
            s[direction + "_packets"] += 1
            other = p.get("dst") if direction == "sent" else p.get("src")
            if other:
                s["peers"].add(other)
    out = []
    for s in stats.values():
        s["peers"] = len(s["peers"])
        out.append(s)
    out.sort(key=lambda s: (-s["bytes"], -s["packets"], s["ip"]))
    return out[:n]


def dns_queries(packets: list) -> list:
    responses = {}
    for p in packets:
        d = p.get("dns")
        if d and d["response"]:
            responses.setdefault((d["id"], d["name"]), d)
    out = []
    for p in packets:
        d = p.get("dns")
        if not d or d["response"]:
            continue
        reply = responses.get((d["id"], d["name"]))
        answers = [a["value"] for a in reply["answers"] if a["value"] is not None] if reply else []
        out.append({"time": p["time"], "when": p["when"], "src": p["src"], "dst": p["dst"], "name": d["name"],
                    "type": d["type"], "answers": answers, "rcode": reply["rcode"] if reply else None,
                    "answered": reply is not None, "id": d["id"]})
    return out


def http_requests(packets: list) -> list:
    out = []
    for p in packets:
        h = p.get("http")
        if h and h["kind"] == "request":
            out.append({"time": p["time"], "when": p["when"], "src": p["src"], "sport": p["sport"], "dst": p["dst"],
                        "dport": p["dport"], "method": h["method"], "host": h["host"], "path": h["path"],
                        "url": h["url"], "user_agent": h["user_agent"], "headers": h["headers"],
                        "content_type": h["content_type"], "number": p["number"]})
    return out


def sni_list(packets: list) -> list:
    out = []
    for p in packets:
        t = p.get("tls")
        if t and t["handshake"] == "client_hello":
            out.append({"time": p["time"], "when": p["when"], "src": p["src"], "sport": p["sport"], "dst": p["dst"],
                        "dport": p["dport"], "sni": t["sni"], "version": t["version"], "alpn": t["alpn"],
                        "number": p["number"]})
    return out


def port_scans(packets: list, min_ports: int) -> list:
    pairs = {}
    replied = {}
    for p in packets:
        if p.get("protocol") != "tcp" or not p.get("flags"):
            continue
        flags = set(p["flags"])
        if flags == {"SYN"}:
            s = pairs.setdefault((p["src"], p["dst"]), {"ports": {}, "first": p["time"], "last": p["time"]})
            s["ports"].setdefault(p["dport"], 0)
            s["ports"][p["dport"]] += 1
            s["first"], s["last"] = min(s["first"], p["time"]), max(s["last"], p["time"])
        elif flags == {"SYN", "ACK"}:
            replied.setdefault((p["dst"], p["src"]), set()).add(p["sport"])
    out = []
    for (src, dst), s in pairs.items():
        if len(s["ports"]) >= min_ports:
            open_ports = sorted(replied.get((src, dst), set()) & set(s["ports"]))
            out.append({"src": src, "dst": dst, "ports": sorted(s["ports"]), "count": len(s["ports"]),
                        "open_ports": open_ports, "first": s["first"], "last": s["last"],
                        "first_when": when_text(s["first"]), "last_when": when_text(s["last"]),
                        "duration": round(s["last"] - s["first"], 6)})
    out.sort(key=lambda s: -s["count"])
    return out


def summary_of(packets: list, fmt: str, path=None) -> dict:
    protocols, apps, ports = {}, {}, {}
    total = 0
    for p in packets:
        protocols[p["protocol"] or "unknown"] = protocols.get(p["protocol"] or "unknown", 0) + 1
        if p.get("app"):
            apps[p["app"]] = apps.get(p["app"], 0) + 1
        if p.get("dport") is not None and p.get("protocol") in ("tcp", "udp"):
            key = f"{p['protocol']}/{p['dport']}"
            ports[key] = ports.get(key, 0) + 1
        total += p["length"]
    times = [p["time"] for p in packets if p["time"]]
    first, last = (min(times), max(times)) if times else (None, None)
    flows = build_flows(packets)
    top_ports = dict(sorted(ports.items(), key=lambda kv: (-kv[1], kv[0]))[:15])
    out = {"format": fmt, "packets": len(packets), "bytes": total, "first": first, "last": last,
           "first_when": when_text(first) if first else None, "last_when": when_text(last) if last else None,
           "duration": round(last - first, 6) if times else 0,
           "link": packets[0]["link"] if packets else None,
           "protocols": dict(sorted(protocols.items(), key=lambda kv: -kv[1])),
           "apps": dict(sorted(apps.items(), key=lambda kv: -kv[1])),
           "ports": top_ports, "flows": len(flows), "hosts": len({ip for p in packets for ip in (p.get("src"), p.get("dst")) if ip}),
           "top_talkers": top_talkers(packets, 10), "dns_queries": len(dns_queries(packets)),
           "http_requests": len(http_requests(packets)), "tls_hellos": len(sni_list(packets)),
           "port_scans": port_scans(packets, 10)}
    if path:
        out["path"] = path
    return out


# =============================================================================
# Reassembly: IP fragments, TCP streams, HTTP over them
# =============================================================================

MAX_STREAM_BYTES = 16 * 1024 * 1024   # default cap on the bytes kept per stream direction
MAX_DATAGRAM = 65535
DECODE_BUDGET = 4      # one http_exchanges() call unpacks at most this many times max_bytes of gzip/deflate
MAX_FRAGMENTS = 8192   # pieces kept for one datagram before the set is dropped
# what a hand-made or damaged packet map can raise; such a packet or stream is skipped, not fatal
MALFORMED = (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError, struct.error, zlib.error)


def ip_checksum(data) -> int:
    if len(data) % 2:
        data = bytes(data) + b"\x00"
    total = sum(struct.unpack(f">{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def join_fragments(parts: list, total: int) -> bytes:
    """The whole IP payload of a fully covered set. Where fragments overlap, the one that arrived first wins."""
    buf = bytearray(total)
    for p in reversed(parts):  # earliest written last, so it wins
        f = p["fragment"]
        piece = f["data"][:max(0, total - f["offset"])]
        buf[f["offset"]:f["offset"] + len(piece)] = piece
    return bytes(buf)


def rebuild_datagram(first: dict, data: bytes) -> bytes:
    """The first fragment's frame with its IP header fixed up to carry the whole payload."""
    raw, at = first["raw"], first["fragment"]["ip_at"]
    if first.get("ip_version") == 4:
        ihl = (raw[at] & 0x0F) * 4
        head = bytearray(raw[at:at + ihl])
        struct.pack_into(">H", head, 2, ihl + len(data))
        struct.pack_into(">H", head, 6, 0)   # no more fragments, offset 0
        struct.pack_into(">H", head, 10, 0)  # checksum, recomputed next
        struct.pack_into(">H", head, 10, ip_checksum(head))
    else:
        # shortcut: IPv6 extension headers before the fragment header are dropped; they only matter for routing.
        head = bytearray(raw[at:at + 40])
        struct.pack_into(">H", head, 4, len(data))
        head[6] = first["fragment"]["protocol"]
    return bytes(raw[:at]) + bytes(head) + data


def cover(g: dict, start: int, end: int):
    """Mark the 8-byte units a fragment fills (offsets are always multiples of 8) and count what's still missing."""
    lo, hi = start // 8, len(g["have"]) if end >= g["total"] else end // 8
    if hi > lo:
        g["missing"] -= (hi - lo) - g["have"].count(1, lo, hi)
        g["have"][lo:hi] = b"\x01" * (hi - lo)


def defragment(packets: list) -> list:
    """Packets with each complete set of IP fragments replaced by one decoded packet (where the last piece
    arrived). Incomplete or malformed sets are left as they are."""
    groups, whole, used = {}, {}, set()
    for i, p in enumerate(packets):
        try:
            f = p.get("fragment")
            if not isinstance(f, dict) or "data" not in f:
                continue
            key = (p.get("ip_version"), p.get("src"), p.get("dst"), f["id"], f["protocol"])
            start, end, more = f["offset"], f["offset"] + len(f["data"]), bool(f["more"])
            if not isinstance(start, int) or start < 0 or start % 8:
                continue
            hash(key)
        except MALFORMED:
            continue
        g = groups.setdefault(key, {"parts": [], "total": None})
        g["parts"].append((i, p))
        if len(g["parts"]) > MAX_FRAGMENTS:
            del groups[key]  # a flood of pieces for one datagram: give up on it
            continue
        if g["total"] is None:
            if more:
                continue  # nothing to join until the last piece says how long the datagram is
            if end > MAX_DATAGRAM:
                del groups[key]
                continue
            g["total"], g["have"] = end, bytearray(-(-end // 8))
            g["missing"] = len(g["have"])
            for _, q in g["parts"]:
                cover(g, q["fragment"]["offset"], q["fragment"]["offset"] + len(q["fragment"]["data"]))
        else:
            cover(g, start, end)
        if g["missing"]:
            continue
        del groups[key]  # the id may be reused later for a new datagram
        parts = [q for _, q in g["parts"]]
        try:
            first = min(parts, key=lambda q: q["fragment"]["offset"])
            frame = rebuild_datagram(first, join_fragments(parts, g["total"]))
            out = decode_frame(p["time"], len(frame), p.get("link_type", 1), frame, p.get("interface"), p["number"])
            out["fragments"] = sorted(q["number"] for q in parts)
        except MALFORMED:
            continue
        whole[i] = out
        used.update(j for j, _ in g["parts"])
    return [whole.get(i, p) for i, p in enumerate(packets) if i in whole or i not in used]


def seq_offset(seq: int, anchor: int) -> int:
    r = (seq - anchor) & 0xFFFFFFFF
    return r - (1 << 32) if r >= (1 << 31) else r


def assemble_direction(pkts: list, cap: int):
    """One direction of a TCP connection as ordered bytes plus (offset, packet) marks for timing."""
    d = {"data": b"", "bytes": 0, "packets": len(pkts), "gaps": [], "missing": 0, "retransmissions": 0,
         "out_of_order": 0, "overlaps": 0, "truncated": False,
         "fin": any("FIN" in p["flags"] for p in pkts)}
    syn = next((p for p in pkts if "SYN" in p["flags"]), None)
    data_pkts = [p for p in pkts if p.get("payload")]
    if not data_pkts:
        return d, []
    anchor = (syn["seq"] + 1) & 0xFFFFFFFF if syn else data_pkts[0]["seq"]
    segs = sorted(((seq_offset(p["seq"], anchor), i, p) for i, p in enumerate(data_pkts)), key=lambda s: s[:2])
    pos = 0 if syn else segs[0][0]
    chunks, marks, fresh, size = [], [], set(), 0
    for start, i, p in segs:
        data = p["payload"]
        end = start + len(data)
        if end <= pos:
            d["retransmissions"] += 1
            continue
        if start > pos:
            d["gaps"].append({"at": size, "length": start - pos})
            d["missing"] += start - pos
        elif start < pos:
            d["overlaps"] += 1
            data = data[pos - start:]
        if size + len(data) > cap:
            data = data[:cap - size]
            d["truncated"] = True
        if data:
            marks.append((size, p))
            chunks.append(data)
            size += len(data)
            fresh.add(i)
        pos = end
        if d["truncated"]:
            break
    highest = None
    for start, i, _p in sorted(segs, key=lambda s: s[1]):
        if i in fresh and highest is not None and start < highest:
            d["out_of_order"] += 1
        highest = start if highest is None else max(highest, start)
    d["data"] = b"".join(chunks)
    d["bytes"] = size
    return d, marks


def stream_state(cf: set, sf: set) -> str:
    if "RST" in cf or "RST" in sf:
        return "refused" if "SYN" in cf and "SYN" not in sf else "reset"
    if "FIN" in cf and "FIN" in sf:
        return "closed"
    if "FIN" in cf or "FIN" in sf:
        return "closing"
    if "SYN" in cf and "SYN" in sf:
        return "established"
    if "SYN" in cf:
        return "no_reply"
    return "partial"


def stream_app(client: bytes, server: bytes):
    if client.startswith(HTTP_METHODS) or server.startswith(b"HTTP/1."):
        return "http"
    if client[:2] == b"\x16\x03" or server[:2] == b"\x16\x03":
        return "tls"
    if client.startswith(b"SSH-") or server.startswith(b"SSH-"):
        return "ssh"
    return None


def reassemble(packets: list, cap: int) -> list:
    """[(stream map, {"client": marks, "server": marks})] for every TCP connection, oldest first."""
    conns, found = {}, []
    for p in defragment(packets):
        try:
            if p.get("protocol") != "tcp" or p.get("seq") is None or p.get("fragment"):
                continue
            a, b = (p["src"], p["sport"]), (p["dst"], p["dport"])
            key = (a, b) if (str(a[0]), a[1]) <= (str(b[0]), b[1]) else (b, a)
            flags = p["flags"]
            opening = "SYN" in flags and "ACK" not in flags
            s = conns.get(key)
            if s is not None and opening and s["closed"] and p["seq"] != s["isn"]:
                s = None  # the same ports reused for a new connection
            if s is None:
                client = a
                if "SYN" in flags and "ACK" in flags:
                    client = b
                elif "SYN" not in flags and p["sport"] in WELL_KNOWN_PORTS and p["dport"] not in WELL_KNOWN_PORTS:
                    client = b
                s = {"client": client, "pkts": [], "closed": False, "isn": p["seq"] if opening else None}
                conns[key] = s
                found.append(s)
            s["pkts"].append(p)
            if "FIN" in flags or "RST" in flags:
                s["closed"] = True
        except MALFORMED:
            continue  # a damaged or hand-made packet map
    out = []
    for n, s in enumerate(found, 1):
        try:
            (cip, cport), pkts = s["client"], s["pkts"]
            mine = [p for p in pkts if (p["src"], p["sport"]) == s["client"]]
            theirs = [p for p in pkts if (p["src"], p["sport"]) != s["client"]]
            cdir, cmarks = assemble_direction(mine, cap)
            sdir, smarks = assemble_direction(theirs, cap)
            server = theirs[0] if theirs else None
            sip = server["src"] if server else mine[0]["dst"]
            sport = server["sport"] if server else mine[0]["dport"]
            first, last = min(p["time"] for p in pkts), max(p["time"] for p in pkts)
            cf = {f for p in mine for f in p["flags"]}
            sf = {f for p in theirs for f in p["flags"]}
            out.append(({"id": n, "src": cip, "sport": cport, "dst": sip, "dport": sport,
                         "state": stream_state(cf, sf), "handshake": "SYN" in cf and "SYN" in sf,
                         "packets": len(pkts), "first": first, "last": last, "first_when": when_text(first),
                         "last_when": when_text(last), "duration": round(last - first, 6),
                         "app": stream_app(cdir["data"], sdir["data"]),
                         "service": WELL_KNOWN_PORTS.get(sport) or WELL_KNOWN_PORTS.get(cport),
                         "client": cdir, "server": sdir},
                        {"client": cmarks, "server": smarks}))
        except MALFORMED:
            continue
    return out


# -- HTTP over reassembled streams ----------------------------------------------------

def http_head(data: bytes, pos: int):
    end = data.find(b"\r\n\r\n", pos)
    if end < 0:
        return None
    lines = data[pos:end].split(b"\r\n")
    headers = {}
    for raw in lines[1:]:
        key, sep, value = raw.partition(b":")
        if sep:
            headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1", "replace").strip()
    return lines[0].decode("latin-1", "replace"), headers, end + 4


def dechunk(data: bytes, pos: int):
    out = []
    while True:
        nl = data.find(b"\r\n", pos)
        if nl < 0:
            return b"".join(out), len(data), False
        size = data[pos:nl].split(b";")[0].strip()
        if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", size):  # never "-6": pos must only move forward
            return b"".join(out), len(data), False
        size, pos = int(size, 16), nl + 2
        if size == 0:
            if data[pos:pos + 2] == b"\r\n":
                return b"".join(out), pos + 2, True
            end = data.find(b"\r\n\r\n", pos)  # trailer headers
            return b"".join(out), (end + 4 if end >= 0 else len(data)), end >= 0
        piece = data[pos:pos + size]
        out.append(piece)
        if len(piece) < size:
            return b"".join(out), len(data), False
        pos += size + 2


def http_body(data: bytes, pos: int, headers: dict, to_end: bool):
    """(body, next position, complete)."""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        return dechunk(data, pos)
    length = headers.get("content-length", "").strip()
    if re.fullmatch(r"[0-9]{1,18}", length):  # ASCII digits only: "²" is a digit to str.isdigit()
        body = data[pos:pos + int(length)]
        return body, pos + len(body), len(body) == int(length)
    if to_end:
        return data[pos:], len(data), True
    return b"", pos, True


def decode_content(body: bytes, headers: dict, cap: int, budget: list):
    """(body, decoded). gzip and deflate bodies are unpacked while budget[0], the bytes one call may still
    unpack, lasts; a broken, oversized or over-budget body comes back as sent, with decoded false."""
    coding = headers.get("content-encoding", "").lower().strip()
    if not body or coding not in ("gzip", "x-gzip", "deflate"):
        return body, True
    limit = min(cap, budget[0])
    if limit <= 0:
        return body, False
    for wbits in (47, -15):  # gzip or zlib, then raw deflate
        try:
            z = zlib.decompressobj(wbits)
            out = z.decompress(body, limit)  # the limit stops decompression bombs
        except zlib.error:
            continue
        budget[0] -= len(out)
        if z.unconsumed_tail or (len(out) >= limit and not z.eof):
            return body, False
        return out, True
    return body, False


def http_messages(data: bytes, requests: bool, methods=None) -> list:
    out, pos = [], 0
    while pos < len(data) and len(out) < 10000:
        start = pos
        head = http_head(data, pos)
        if head is None:
            break
        first, headers, pos = head
        parts = first.split(" ", 2)
        if requests:
            if not (first + " ").encode("latin-1").startswith(HTTP_METHODS) or len(parts) < 2:
                break
            body, pos, complete = http_body(data, pos, headers, to_end=False)
            out.append({"at": start, "method": parts[0], "path": parts[1], "headers": headers, "body": body,
                        "complete": complete})
            continue
        if not first.startswith("HTTP/1.") or len(parts) < 2 or not re.fullmatch(r"[0-9]{3}", parts[1]):
            break
        status = int(parts[1])
        if status < 200:
            continue  # 100 Continue and friends: a real response follows
        method = methods[len(out)] if methods and len(out) < len(methods) else None
        if method == "HEAD" or status in (204, 304):
            body, complete = b"", True
        else:
            body, pos, complete = http_body(data, pos, headers, to_end=True)
        out.append({"at": start, "status": status, "reason": parts[2] if len(parts) > 2 else "",
                    "headers": headers, "body": body, "complete": complete})
    return out


def mark_at(marks: list, offsets: list, offset: int):
    if not marks:
        return None
    i = bisect.bisect_right(offsets, offset) - 1
    return marks[max(i, 0)][1]


def http_exchanges(packets: list, cap: int) -> list:
    out, budget = [], [DECODE_BUDGET * cap]  # one unpacking budget for the whole call, not per body
    for s, marks in reassemble(packets, cap):
        if s["app"] != "http":
            continue
        try:
            out.extend(stream_exchanges(s, marks, cap, budget))
        except MALFORMED:
            continue
    return out


def stream_exchanges(s: dict, marks: dict, cap: int, budget: list) -> list:
    out = []
    reqs = http_messages(s["client"]["data"], True)
    resps = http_messages(s["server"]["data"], False, [r["method"] for r in reqs])
    coffs, soffs = [m[0] for m in marks["client"]], [m[0] for m in marks["server"]]
    for k in range(max(len(reqs), len(resps))):
        req = reqs[k] if k < len(reqs) else None
        resp = resps[k] if k < len(resps) else None
        when = mark_at(marks["client"], coffs, req["at"]) if req else mark_at(marks["server"], soffs, resp["at"])
        rh = req["headers"] if req else {}
        host = rh.get("host")
        path = req["path"] if req else None
        url = path if path is None or path.startswith("http") or not host else f"http://{host}{path}"
        sh = resp["headers"] if resp else {}
        qbody, qok = decode_content(req["body"], rh, cap, budget) if req else (b"", True)
        rbody, rok = decode_content(resp["body"], sh, cap, budget) if resp else (b"", True)
        out.append({"stream": s["id"], "time": when["time"] if when else None,
                    "when": when["when"] if when else None, "src": s["src"], "sport": s["sport"],
                    "dst": s["dst"], "dport": s["dport"], "method": req["method"] if req else None,
                    "host": host, "path": path, "url": url, "user_agent": rh.get("user-agent"),
                    "request_headers": rh, "request_body": qbody, "request_decoded": qok,
                    "status": resp["status"] if resp else None, "reason": resp["reason"] if resp else None,
                    "response_headers": sh, "content_type": sh.get("content-type"),
                    "response_body": rbody, "response_decoded": rok,
                    "complete": bool(req and resp and req["complete"] and resp["complete"])})
    return out


WINDOWS_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9\u00b9\u00b2\u00b3]|LPT[0-9\u00b9\u00b2\u00b3])", re.I)


def object_name(url, headers: dict, content_type) -> str:
    disposition = headers.get("content-disposition", "")
    name = ""
    m = re.search(r"filename\*\s*=\s*(?:[\w!#$%&+^`{}~-]+'[^']*')?\"?([^\";]+)", disposition, re.I)
    if m:
        name = urllib.parse.unquote(m.group(1).strip())
    else:
        m = re.search(r'filename\s*=\s*(?:"([^"]*)"|([^;]+))', disposition, re.I)
        if m:
            name = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
    if not name and url:
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    # never a path: a hostile server can send "../../x" or "C:\\x" (or "x:stream", an NTFS stream)
    name = name.replace("\\", "/").rsplit("/", 1)[-1].split(":")[-1]
    # and a name Windows can save: no <>"|?* or control characters, no trailing dots or spaces
    name = re.sub(r'[<>"|?*\x00-\x1f\x7f]', "_", name).strip().lstrip(".")[:200].rstrip(". ")
    if not name:
        return "index.html" if "html" in (content_type or "") else "object"
    if WINDOWS_RESERVED.fullmatch(name.split(".")[0].rstrip()):
        name = "_" + name  # CON, NUL, COM1 and friends are devices, not files, on Windows
    return name


def http_objects(packets: list, cap: int) -> list:
    out = []
    for e in http_exchanges(packets, cap):
        found = []
        if e["response_body"] and e["status"] is not None:
            found.append(("download", e["response_body"], e["response_headers"], e["response_decoded"]))
        if e["request_body"]:
            found.append(("upload", e["request_body"], e["request_headers"], e["request_decoded"]))
        for direction, data, headers, decoded in found:
            ctype = headers.get("content-type")
            out.append({"name": object_name(e["url"], headers, ctype), "direction": direction, "url": e["url"],
                        "host": e["host"], "status": e["status"], "content_type": ctype, "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
                        "data": data, "decoded": decoded, "stream": e["stream"], "time": e["time"],
                        "when": e["when"], "src": e["src"], "dst": e["dst"], "dport": e["dport"]})
    return out


# =============================================================================
# Writing
# =============================================================================

def write_pcap(packets: list, link: int = 1) -> bytes:
    out = bytearray(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, link))
    for p in packets:
        raw = p["raw"]
        ts = float(p.get("time") or 0)
        sec = int(ts)
        usec = int(round((ts - sec) * 1e6))
        if usec >= 1_000_000:
            sec, usec = sec + 1, usec - 1_000_000
        out += struct.pack("<IIII", sec, usec, len(raw), p.get("length") or len(raw))
        out += raw
    return bytes(out)


# =============================================================================
# The module
# =============================================================================

def load(api):
    m = Module(
        "pcap",
        "Read network captures (pcap and pcapng) into packet maps: Ethernet and VLAN, IPv4/IPv6, ARP, TCP, UDP "
        "and ICMP, with DNS questions and answers, HTTP requests, and the server name (SNI) from TLS hellos. "
        "Then group packets into flows, find top talkers, list DNS lookups, HTTP requests and TLS names, spot "
        "port scans, and write filtered packets back out as pcap. Reassemble IP fragments and TCP streams to read "
        "whole HTTP exchanges and pull out the files they carried, with hashes. Times are UTC. Pure Python, no "
        "tcpdump needed.",
    )
    m.value("MAX_PACKETS", MAX_PACKETS, doc="The most packets pcap.read() will load from one file.")

    @m.fn("read", 1, 2, doc=(
        "read(path, limit?) -> list\n"
        "Every packet in a pcap or pcapng file, as a list of maps. Fails if the file isn't a capture.\n"
        "\n"
        "Arguments:\n"
        "- limit: read only the first limit packets.\n"
        "\n"
        "Fields of each packet:\n"
        "- \"number\", \"time\", \"when\", \"length\"\n"
        "- \"protocol\": like tcp, udp, icmp or arp.\n"
        "- \"src\", \"dst\": the addresses.\n"
        "- \"sport\", \"dport\": the ports.\n"
        "- \"flags\": the TCP flags.\n"
        "- \"ttl\"\n"
        "- \"app\": the application protocol, like dns, http, tls or ssh.\n"
        "- \"dns\", \"http\", \"tls\", \"arp\", \"icmp\": detail maps for that protocol.\n"
        "- \"payload\": the payload bytes.\n"
        "- \"payload_length\": the size of the payload.\n"
        "- \"summary\": one readable line.\n"
        "- \"raw\": the whole frame."))
    def read(line, path, limit=None):
        if limit is not None:
            limit = check_type(line, limit, "whole", "pcap.read", "limit")
        data = read_capture_bytes(line, path, "pcap.read")
        return packets_from(line, data, "pcap.read", path, limit)

    @m.fn("parse", 1, 2, doc="parse(data, limit?) -> list\nLike pcap.read() but for capture bytes already in memory.")
    def parse(line, data, limit=None):
        raw = _magic.to_bytes(line, data, "pcap.parse")
        if limit is not None:
            limit = check_type(line, limit, "whole", "pcap.parse", "limit")
        return packets_from(line, raw, "pcap.parse", None, limit)

    @m.fn("each", 2, doc="each(path, fn) -> number\nCall fn(packet) for every packet in the file, one at a time, "
                         "without building the whole list of packet maps (the file's bytes are still read in one "
                         "go). Return false from fn to stop early. Gives back how many packets fn saw.")
    def each(line, path, fn):
        check_type(line, fn, "function", "pcap.each", "fn")
        data = read_capture_bytes(line, path, "pcap.each")
        count = 0
        try:
            for n, (ts, orig, link, frame, iface) in enumerate(iter_frames(data), 1):
                count = n
                if api.call(fn, [decode_frame(ts, orig, link, frame, iface, n)], line) is False:
                    break
        except CaptureFormat as err:
            fail(line, f"'{path}' isn't a capture I can read: {err}", path=path)
        except struct.error:
            fail(line, f"'{path}' is a cut-off or damaged capture.", path=path)
        return count

    @m.fn("format", 1, doc="format(path) -> text\n\"pcap\", \"pcap-ns\" (nanosecond) or \"pcapng\"; fails for other files.")
    def format_of(line, path):
        head = _magic.read_head(line, path, "pcap.format", 64)
        if _magic.sniff(head)["type"] not in ("pcap", "pcapng"):
            fail(line, f"'{path}' isn't a pcap or pcapng capture.", path=path)
        return capture_format(head)

    @m.fn("flows", 1, doc=(
        "flows(packets) -> list\n"
        "Group packets into conversations, oldest first. A conversation is one protocol, addresses and ports,\n"
        "in both directions.\n"
        "\n"
        "Fields of each flow:\n"
        "- \"protocol\", \"src\", \"sport\", \"dst\", \"dport\"\n"
        "- \"packets\", \"bytes\", \"client_bytes\", \"server_bytes\"\n"
        "- \"first\", \"last\": the first and last timestamps.\n"
        "- \"first_when\", \"last_when\": the same times as text.\n"
        "- \"duration\"\n"
        "- \"flags\", \"app\", \"service\"\n"
        "- \"names\": DNS names, HTTP hosts and SNI names seen.\n"
        "- \"state\": for TCP, one of:\n"
        "  - \"established\", \"refused\", \"no_reply\", \"half_open\" or \"other\""))
    def flows(line, packets):
        return build_flows(check_packets(line, packets, "pcap.flows"))

    @m.fn("top_talkers", 1, 2, doc=(
        "top_talkers(packets, n?) -> list\n"
        "The busiest addresses by bytes (default: top 10).\n"
        "\n"
        "Fields of each address:\n"
        "- \"ip\", \"packets\", \"bytes\", \"sent\", \"received\", \"peers\""))
    def talkers(line, packets, n=10):
        n = check_type(line, n, "whole", "pcap.top_talkers", "n")
        return top_talkers(check_packets(line, packets, "pcap.top_talkers"), n)

    @m.fn("dns_queries", 1, doc=(
        "dns_queries(packets) -> list\n"
        "Every DNS question asked, with the answers that came back.\n"
        "\n"
        "Fields of each query:\n"
        "- \"time\", \"when\", \"src\", \"dst\", \"name\", \"type\"\n"
        "- \"answers\": a list of the values that came back.\n"
        "- \"rcode\", \"answered\""))
    def dns_q(line, packets):
        return dns_queries(check_packets(line, packets, "pcap.dns_queries"))

    @m.fn("http_requests", 1, doc=(
        "http_requests(packets) -> list\n"
        "Every plain HTTP request.\n"
        "\n"
        "Fields of each request:\n"
        "- \"time\", \"when\", \"src\", \"dst\", \"dport\"\n"
        "- \"method\", \"host\", \"path\", \"url\"\n"
        "- \"user_agent\", \"headers\", \"content_type\""))
    def http_r(line, packets):
        return http_requests(check_packets(line, packets, "pcap.http_requests"))

    @m.fn("sni", 1, doc=(
        "sni(packets) -> list\n"
        "The server names from TLS Client Hellos. They show what HTTPS connections are for,\n"
        "even though the traffic is encrypted.\n"
        "\n"
        "Fields of each hello:\n"
        "- \"time\", \"when\", \"src\", \"dst\", \"dport\"\n"
        "- \"sni\": the server name.\n"
        "- \"version\", \"alpn\""))
    def sni(line, packets):
        return sni_list(check_packets(line, packets, "pcap.sni"))

    @m.fn("port_scans", 1, 2, doc=(
        "port_scans(packets, min_ports?) -> list\n"
        "Sources that sent bare TCP SYNs to many ports of one host (default: 10 or more ports).\n"
        "\n"
        "Fields of each scan:\n"
        "- \"src\", \"dst\", \"ports\", \"count\"\n"
        "- \"open_ports\": the ports that answered SYN-ACK.\n"
        "- \"first_when\", \"last_when\", \"duration\""))
    def scans(line, packets, min_ports=10):
        min_ports = check_type(line, min_ports, "whole", "pcap.port_scans", "min_ports")
        return port_scans(check_packets(line, packets, "pcap.port_scans"), max(min_ports, 1))

    @m.fn("summary", 1, doc=(
        "summary(path_or_packets) -> map\n"
        "An overview of a capture.\n"
        "\n"
        "Fields:\n"
        "- \"format\", \"packets\", \"bytes\"\n"
        "- \"first_when\", \"last_when\", \"duration\"\n"
        "- \"hosts\", \"flows\"\n"
        "- \"protocols\": a map of name -> count.\n"
        "- \"apps\"\n"
        "- \"ports\": the top destination ports.\n"
        "- \"top_talkers\", \"dns_queries\", \"http_requests\", \"tls_hellos\", \"port_scans\""))
    def summary(line, target):
        if isinstance(target, list):
            return summary_of(check_packets(line, target, "pcap.summary"), "packets")
        data = read_capture_bytes(line, target, "pcap.summary")
        return summary_of(packets_from(line, data, "pcap.summary", target), capture_format(data), target)

    @m.fn("filter", 2, doc="filter(packets, fn) -> list\nKeep the packets for which fn(packet) is true "
                           "(same as packets.filter(fn); here so pipelines read naturally).")
    def filter_(line, packets, fn):
        check_type(line, fn, "function", "pcap.filter", "fn")
        kept = []
        for p in check_packets(line, packets, "pcap.filter"):
            answer = api.call(fn, [p], line)
            if answer is not None and answer is not False:
                kept.append(p)
        return kept

    @m.fn("write", 2, doc="write(path, packets) -> number\nSave packets (from pcap.read, maybe filtered) as a "
                          "classic pcap file; gives back how many were written. Lets you cut a huge capture down "
                          "to the packets that matter and open the result in Wireshark.")
    def write(line, path, packets):
        check_type(line, path, "text", "pcap.write", "path")
        check_packets(line, packets, "pcap.write")
        for p in packets:
            if not isinstance(p.get("raw"), (bytes, bytearray)):
                fail(line, "pcap.write() needs packets from pcap.read() (each has the \"raw\" frame bytes).")
        link = packets[0].get("link_type", 1) if packets else 1
        if any(p.get("link_type", 1) != link for p in packets):
            fail(line, "pcap.write() can only save packets that share one link type (for example all Ethernet).")
        try:
            folder = os.path.dirname(os.path.expanduser(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(os.path.expanduser(path), "wb") as f:
                f.write(write_pcap(packets, link))
        except OSError as err:
            fail_os(line, err, path, "write")
        return len(packets)

    def cap_of(line, options, fn):
        if options is None:
            return MAX_STREAM_BYTES
        check_type(line, options, "map", fn, "options")
        for key in options:
            if key != "max_bytes":
                fail(line, f'{fn}() doesn\'t know the option "{key}". Options: max_bytes.')
        cap = check_type(line, options.get("max_bytes", MAX_STREAM_BYTES), "whole", fn, "max_bytes")
        if cap < 1:
            fail(line, f"{fn}() needs max_bytes to be at least 1, not {cap}.")
        return cap

    @m.fn("defragment", 1, doc=(
        "defragment(packets) -> list\n"
        "Put fragmented IPv4 and IPv6 packets back together. Each complete set of fragments becomes one\n"
        "packet, decoded again (so TCP, UDP, DNS and HTTP inside it show up), in the place where its last\n"
        "fragment arrived. Sets with a piece missing are left as they are.\n"
        "\n"
        "Fields added to a reassembled packet:\n"
        "- fragments: the numbers of the packets it was built from.\n"
        "\n"
        "Fragments that are left over keep a \"fragment\" map with \"offset\", \"more\" and \"id\"."))
    def defrag(line, packets):
        return defragment(check_packets(line, packets, "pcap.defragment"))

    @m.fn("streams", 1, 2, doc=(
        "streams(packets, options?) -> list\n"
        "Every TCP connection with the bytes each side sent, in order. Fragments are joined first, segments\n"
        "that arrived out of order are sorted, and repeated bytes (retransmissions and overlaps) are kept\n"
        "once; where the same bytes arrive twice, the first copy wins.\n"
        "\n"
        "Options:\n"
        "- max_bytes: the most bytes kept for each side of a connection (default: 16 MB).\n"
        "\n"
        "Fields of each stream:\n"
        "- id: 1 for the oldest connection, then 2, 3 and so on.\n"
        "- src, sport: the client (the side that sent the first SYN).\n"
        "- dst, dport: the server.\n"
        "- state: how the connection went, one of:\n"
        "  - \"established\", \"closing\", \"closed\" (FIN both ways), \"reset\", \"refused\", \"no_reply\"\n"
        "  - \"partial\": the capture started after the handshake.\n"
        "- handshake: true when both SYNs were seen.\n"
        "- packets, first, last, first_when, last_when, duration\n"
        "- app: \"http\", \"tls\", \"ssh\" or nil, from the first bytes.\n"
        "- service: the usual name for the port.\n"
        "- client, server: what each side sent, as a map.\n"
        "\n"
        "Fields of client and server:\n"
        "- data: the bytes, in order.\n"
        "- bytes, packets\n"
        "- gaps: holes where segments were never captured, as maps of \"at\" (where in data) and \"length\".\n"
        "- missing: how many bytes the gaps add up to.\n"
        "- retransmissions, overlaps, out_of_order: how many segments were repeats, partly repeats, or late.\n"
        "- truncated: true when max_bytes cut the data short.\n"
        "- fin: true when this side sent FIN."))
    def streams(line, packets, options=None):
        cap = cap_of(line, options, "pcap.streams")
        return [s for s, _marks in reassemble(check_packets(line, packets, "pcap.streams"), cap)]

    @m.fn("http_exchanges", 1, 2, doc=(
        "http_exchanges(packets, options?) -> list\n"
        "Every HTTP request with its response, read from the reassembled TCP streams, so bodies split over\n"
        "many packets come out whole. Handles keep-alive, Content-Length, chunked transfer encoding and\n"
        "gzip or deflate content. Damaged packets and streams are skipped.\n"
        "\n"
        "Options:\n"
        "- max_bytes: the most bytes kept for each side of a connection (default: 16 MB).\n"
        "  One call unpacks at most 4 times max_bytes of gzip and deflate in total; bodies past that\n"
        "  come back as sent.\n"
        "\n"
        "Fields of each exchange:\n"
        "- stream: the id from pcap.streams().\n"
        "- time, when: when the request was sent.\n"
        "- src, sport, dst, dport\n"
        "- method, host, path, url, user_agent\n"
        "- request_headers, request_body\n"
        "- status, reason, response_headers, content_type, response_body\n"
        "- request_decoded, response_decoded: false when a gzip or deflate body comes back as sent,\n"
        "  because it was broken, bigger than max_bytes, or over the call's unpacking limit.\n"
        "- complete: true when both bodies arrived in full.\n"
        "\n"
        "A request with no answer has status nil."))
    def exchanges(line, packets, options=None):
        cap = cap_of(line, options, "pcap.http_exchanges")
        return http_exchanges(check_packets(line, packets, "pcap.http_exchanges"), cap)

    @m.fn("http_objects", 1, 2, doc=(
        "http_objects(packets, options?) -> list\n"
        "The files carried over plain HTTP: response bodies (downloads) and request bodies (uploads), with\n"
        "their hashes, ready to check against threat intelligence or save with fs.write_bytes().\n"
        "\n"
        "Options:\n"
        "- max_bytes: the most bytes kept for each side of a connection (default: 16 MB).\n"
        "  One call unpacks at most 4 times max_bytes of gzip and deflate in total.\n"
        "\n"
        "Fields of each object:\n"
        "- name: from the Content-Disposition header or the end of the URL. Never a folder path, and\n"
        "  always a name Windows can save.\n"
        "- direction: \"download\" or \"upload\".\n"
        "- url, host, status, content_type\n"
        "- size: the number of bytes.\n"
        "- sha256, md5: hashes of the bytes, as hex text.\n"
        "- data: the bytes themselves.\n"
        "- decoded: false when gzip or deflate bytes were kept as sent (see max_bytes).\n"
        "- stream, time, when\n"
        "- src, dst, dport: the client and the server."))
    def objects(line, packets, options=None):
        cap = cap_of(line, options, "pcap.http_objects")
        return http_objects(check_packets(line, packets, "pcap.http_objects"), cap)

    @m.fn("service", 1, doc="service(port) -> text\nThe usual service name for a port (443 -> \"https\"), or nil.")
    def service(line, port):
        port = check_type(line, port, "whole", "pcap.service", "port")
        return WELL_KNOWN_PORTS.get(port)

    return m
