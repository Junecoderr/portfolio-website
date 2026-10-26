"""A tiny fake DNS server for tests: answers over UDP and TCP on 127.0.0.1.

Zone entries: (name, type) -> list of record tuples. Written independently of
tblib/dns.py so the tests check real wire format, including name compression.
"""

import socket
import struct
import threading

TYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28, "SRV": 33, "CAA": 257}


def enc_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        if label:
            out += bytes([len(label)]) + label.encode()
    return out + b"\x00"


def rdata(rtype, value):
    if rtype == "A":
        return socket.inet_aton(value)
    if rtype == "AAAA":
        return socket.inet_pton(socket.AF_INET6, value)
    if rtype in ("NS", "CNAME", "PTR"):
        return enc_name(value)
    if rtype == "MX":
        prio, host = value
        return struct.pack("!H", prio) + enc_name(host)
    if rtype == "TXT":
        parts = value if isinstance(value, list) else [value]
        return b"".join(bytes([len(p)]) + p.encode() for p in parts)
    if rtype == "SOA":
        mname, rname, *nums = value
        return enc_name(mname) + enc_name(rname) + struct.pack("!IIIII", *nums)
    if rtype == "SRV":
        prio, weight, port, target = value
        return struct.pack("!HHH", prio, weight, port) + enc_name(target)
    if rtype == "CAA":
        flags, tag, val = value
        return bytes([flags, len(tag)]) + tag.encode() + val.encode()
    raise ValueError(rtype)


class FakeDns:
    def __init__(self, zone, truncate_udp=False, silent=False, nxdomain=(), servfail=()):
        self.zone = zone
        self.truncate_udp = truncate_udp
        self.silent = silent
        self.nxdomain = set(nxdomain)
        self.servfail = set(servfail)
        self.questions = []
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.port = self.udp.getsockname()[1]
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(("127.0.0.1", self.port))
        self.tcp.listen(5)
        self.udp.settimeout(0.2)
        self.tcp.settimeout(0.2)
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self.serve_udp, daemon=True),
                        threading.Thread(target=self.serve_tcp, daemon=True)]
        for t in self.threads:
            t.start()

    @property
    def address(self):
        return f"127.0.0.1:{self.port}"

    def close(self):
        self.stop.set()
        for t in self.threads:
            t.join(2)
        self.udp.close()
        self.tcp.close()

    def serve_udp(self):
        while not self.stop.is_set():
            try:
                data, addr = self.udp.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            reply = self.answer(data, udp=True)
            if reply is not None:
                self.udp.sendto(reply, addr)

    def serve_tcp(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.tcp.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                try:
                    size = struct.unpack("!H", conn.recv(2))[0]
                    data = conn.recv(size)
                    reply = self.answer(data, udp=False)
                    if reply is not None:
                        conn.sendall(struct.pack("!H", len(reply)) + reply)
                except OSError:
                    pass

    def answer(self, data, udp):
        ident, flags, qd, *_ = struct.unpack("!HHHHHH", data[:12])
        # Decode the question name.
        offset, labels = 12, []
        while data[offset]:
            n = data[offset]
            labels.append(data[offset + 1:offset + 1 + n].decode())
            offset += 1 + n
        offset += 1
        qtype = struct.unpack("!H", data[offset:offset + 2])[0]
        qname = ".".join(labels)
        tname = {v: k for k, v in TYPES.items()}.get(qtype, qtype)
        self.questions.append((qname, tname, "udp" if udp else "tcp"))
        if self.silent:
            return None
        question = data[12:offset + 4]
        rcode = 0
        if qname in self.nxdomain:
            rcode = 3
        if qname in self.servfail:
            rcode = 2
        records = self.zone.get((qname, tname), []) if rcode == 0 else []
        if udp and self.truncate_udp:
            header = struct.pack("!HHHHHH", ident, 0x8180 | 0x0200, 1, 0, 0, 0)
            return header + question
        body = b""
        for rtype, ttl, value in records:
            # Use a compression pointer to the question name (offset 12) for the owner.
            body += b"\xc0\x0c" + struct.pack("!HHIH", TYPES[rtype], 1, ttl, len(rdata(rtype, value))) + rdata(rtype, value)
        header = struct.pack("!HHHHHH", ident, 0x8180 | rcode, 1, len(records), 0, 0)
        return header + question + body
