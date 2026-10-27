"""dns: look up DNS records (A, AAAA, MX, TXT, NS, CNAME, PTR, SOA, SRV) with a pure-Python client."""

from __future__ import annotations

import ipaddress
import os
import random
import socket
import struct
import sys

from tblib._api import Module, check_type, fail
from tblib._threads import ignore_sigpipe

import tb as _tb

TYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28, "SRV": 33,
         "CAA": 257, "ANY": 255}
TYPE_NAMES = {v: k for k, v in TYPES.items()}
RCODES = {0: "ok", 1: "format_error", 2: "server_failure", 3: "nxdomain", 4: "not_implemented", 5: "refused"}
DEFAULT_TIMEOUT = 3.0
MAX_TIMEOUT = 60
MAX_MESSAGE = 65_535
FALLBACK_SERVERS = ["1.1.1.1", "8.8.8.8"]


class DnsProblem(Exception):
    def __init__(self, message, reason):
        super().__init__(message)
        self.message, self.reason = message, reason


# -- wire format ------------------------------------------------------------------


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        if not label:
            continue
        raw = label.encode("idna") if not label.isascii() else label.encode("ascii")
        if len(raw) > 63:
            raise DnsProblem(f"The name '{name}' has a part longer than 63 characters.", "invalid_name")
        out += bytes([len(raw)]) + raw
    if len(out) > 253:
        raise DnsProblem(f"The name '{name}' is too long for DNS.", "invalid_name")
    return out + b"\x00"


def build_query(name: str, qtype: int, ident: int, recursion: bool = True) -> bytes:
    flags = 0x0100 if recursion else 0
    header = struct.pack("!HHHHHH", ident, flags, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack("!HH", qtype, 1)


def read_name(message: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    """(name, offset after it); follows compression pointers."""
    labels = []
    jumped = False
    end = offset
    hops = 0
    while True:
        if offset >= len(message):
            raise DnsProblem("The DNS answer was cut short.", "bad_answer")
        length = message[offset]
        if length == 0:
            offset += 1
            if not jumped:
                end = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise DnsProblem("The DNS answer was cut short.", "bad_answer")
            pointer = struct.unpack("!H", message[offset:offset + 2])[0] & 0x3FFF
            if not jumped:
                end = offset + 2
            jumped = True
            offset = pointer
            hops += 1
            if hops > 64:
                raise DnsProblem("The DNS answer has a name loop.", "bad_answer")
            continue
        offset += 1
        labels.append(message[offset:offset + length].decode("ascii", errors="replace"))
        offset += length
    return ".".join(labels), end


def read_record(message: bytes, offset: int) -> tuple[dict, int]:
    name, offset = read_name(message, offset)
    if offset + 10 > len(message):
        raise DnsProblem("The DNS answer was cut short.", "bad_answer")
    rtype, rclass, ttl, length = struct.unpack("!HHIH", message[offset:offset + 10])
    offset += 10
    data = message[offset:offset + length]
    if len(data) < length:
        raise DnsProblem("The DNS answer was cut short.", "bad_answer")
    record = {"name": name, "type": TYPE_NAMES.get(rtype, str(rtype)), "ttl": ttl}
    if rtype == 1 and length == 4:
        record["value"] = socket.inet_ntop(socket.AF_INET, data)
    elif rtype == 28 and length == 16:
        record["value"] = socket.inet_ntop(socket.AF_INET6, data)
    elif rtype in (2, 5, 12):
        record["value"], _ = read_name(message, offset)
    elif rtype == 15:
        priority = struct.unpack("!H", data[:2])[0]
        host, _ = read_name(message, offset + 2)
        record.update({"priority": priority, "host": host, "value": f"{priority} {host}"})
    elif rtype == 16:
        parts = []
        i = 0
        while i < length:
            n = data[i]
            parts.append(data[i + 1:i + 1 + n].decode("utf-8", errors="replace"))
            i += 1 + n
        record["value"] = "".join(parts)
        record["parts"] = parts
    elif rtype == 6:
        mname, pos = read_name(message, offset)
        rname, pos = read_name(message, pos)
        serial, refresh, retry, expire, minimum = struct.unpack("!IIIII", message[pos:pos + 20])
        record.update({"primary": mname, "mailbox": rname, "serial": serial, "refresh": refresh,
                       "retry": retry, "expire": expire, "minimum": minimum,
                       "value": f"{mname} {rname} {serial}"})
    elif rtype == 33:
        priority, weight, port = struct.unpack("!HHH", data[:6])
        target, _ = read_name(message, offset + 6)
        record.update({"priority": priority, "weight": weight, "port": port, "target": target,
                       "value": f"{priority} {weight} {port} {target}"})
    elif rtype == 257 and length >= 2:
        flags, tag_len = data[0], data[1]
        tag = data[2:2 + tag_len].decode("ascii", errors="replace")
        value = data[2 + tag_len:].decode("utf-8", errors="replace")
        record.update({"flags": flags, "tag": tag, "value": f'{flags} {tag} "{value}"'})
    else:
        record["value"] = data.hex()
    return record, offset + length


def parse_response(message: bytes, ident: int) -> dict:
    if len(message) < 12:
        raise DnsProblem("The DNS answer was too short to read.", "bad_answer")
    rid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", message[:12])
    if rid != ident:
        raise DnsProblem("The DNS answer belongs to a different question (wrong id).", "bad_answer")
    offset = 12
    questions = []
    for _ in range(qd):
        qname, offset = read_name(message, offset)
        qtype, _qclass = struct.unpack("!HH", message[offset:offset + 4])
        offset += 4
        questions.append({"name": qname, "type": TYPE_NAMES.get(qtype, str(qtype))})
    sections = {}
    for key, count in (("answers", an), ("authority", ns), ("additional", ar)):
        records = []
        for _ in range(count):
            record, offset = read_record(message, offset)
            records.append(record)
        sections[key] = records
    rcode = flags & 0xF
    return {
        "status": RCODES.get(rcode, str(rcode)),
        "truncated": bool(flags & 0x0200),
        "authoritative": bool(flags & 0x0400),
        "recursion_available": bool(flags & 0x0080),
        "question": questions[0] if questions else None,
        **sections,
    }


# -- transport ---------------------------------------------------------------------


def split_server(server: str) -> tuple[str, int]:
    text = server.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else 53
        return host, port
    if text.count(":") == 1:
        host, _, port = text.partition(":")
        if port.isdigit():
            return host, int(port)
    return text, 53


def exchange(message: bytes, server: str, port: int, timeout: float, tcp: bool = False) -> bytes:
    if tcp:
        ignore_sigpipe()
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    if tcp:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((server, port))
            sock.sendall(struct.pack("!H", len(message)) + message)
            head = recv_exact(sock, 2)
            size = struct.unpack("!H", head)[0]
            return recv_exact(sock, size)
        finally:
            sock.close()
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(message, (server, port))
        while True:
            data, addr = sock.recvfrom(MAX_MESSAGE)
            if addr[0] == server or True:  # some resolvers answer from another address
                return data
    finally:
        sock.close()


def recv_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise DnsProblem("The DNS server closed the TCP connection early.", "bad_answer")
        data += chunk
    return data


def query(name: str, qtype: str, server: str, timeout: float = DEFAULT_TIMEOUT, tcp: bool = False,
          retries: int = 1) -> dict:
    """Ask one server; falls back to TCP when the UDP answer is truncated."""
    host, port = split_server(server)
    if qtype not in TYPES:
        raise DnsProblem(f'I don\'t know the record type "{qtype}". Types: {", ".join(TYPES)}.', "invalid_type")
    ident = random.randint(0, 0xFFFF)
    message = build_query(name, TYPES[qtype], ident)
    last_error = None
    for attempt in range(retries + 1):
        try:
            raw = exchange(message, host, port, timeout, tcp)
            response = parse_response(raw, ident)
            if response["truncated"] and not tcp:
                raw = exchange(message, host, port, timeout, tcp=True)
                response = parse_response(raw, ident)
            return response
        except (TimeoutError, socket.timeout):
            last_error = DnsProblem(f"The DNS server {server} didn't answer within {_tb.to_text(timeout)} seconds.",
                                    "timeout")
        except ConnectionRefusedError:
            raise DnsProblem(f"Nothing is listening for DNS at {server}.", "refused") from None
        except socket.gaierror:
            raise DnsProblem(f"'{server}' isn't a DNS server address I can use (give an IP like 1.1.1.1).",
                             "invalid_server") from None
        except OSError as err:
            raise DnsProblem(f"I couldn't talk to the DNS server {server}: {err.strerror or err}.", "network") from None
    raise last_error


def system_servers() -> list:
    """The DNS servers this computer uses, best effort."""
    found = []
    if sys.platform == "win32":
        try:
            import winreg
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters")
            for key in ("NameServer", "DhcpNameServer"):
                try:
                    value, _ = winreg.QueryValueEx(root, key)
                    found.extend(v for v in value.replace(",", " ").split() if v)
                except OSError:
                    pass
            if not found:
                ifaces = winreg.OpenKey(root, "Interfaces")
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(ifaces, i)
                    except OSError:
                        break
                    i += 1
                    sub = winreg.OpenKey(ifaces, name)
                    for key in ("NameServer", "DhcpNameServer"):
                        try:
                            value, _ = winreg.QueryValueEx(sub, key)
                            found.extend(v for v in value.replace(",", " ").split() if v)
                        except OSError:
                            pass
        except Exception:
            pass
    else:
        for path in ("/etc/resolv.conf", "/var/run/resolv.conf"):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2 and parts[0] == "nameserver":
                            found.append(parts[1].split("%")[0])
            except OSError:
                continue
            if found:
                break
    out = []
    for s in found:
        try:
            ipaddress.ip_address(s)
        except ValueError:
            continue
        if s not in out:
            out.append(s)
    return out


def reverse_name(ip) -> str:
    addr = ipaddress.ip_address(ip)
    if addr.version == 4:
        return ".".join(reversed(str(addr).split("."))) + ".in-addr.arpa"
    digits = addr.exploded.replace(":", "")
    return ".".join(reversed(digits)) + ".ip6.arpa"


def load(api):
    m = Module(
        "dns",
        "Look up DNS records yourself, with any server: dns.lookup(\"example.com\", \"MX\") gives a list of "
        "record maps; dns.reverse(ip) finds the name behind an address; dns.servers() shows the resolvers this "
        "computer uses. It is a small pure-Python DNS client over UDP (TCP when the answer is big). "
        "Records have \"name\", \"type\", \"ttl\" and \"value\", plus fields per type (MX: priority, host; "
        "SRV: priority, weight, port, target; SOA: primary, mailbox, serial...).",
    )

    def name_arg(line, name, fn):
        check_type(line, name, "text", f"dns.{fn}", "name")
        text = name.strip().rstrip(".")
        if not text:
            fail(line, f"dns.{fn}() needs a name like \"example.com\", but got empty text.")
        if " " in text or "/" in text:
            fail(line, f"dns.{fn}() needs a host name like \"example.com\", not '{name}'.", reason="invalid_name")
        return text

    def type_arg(line, qtype, fn):
        check_type(line, qtype, "text", f"dns.{fn}", "type")
        upper = qtype.strip().upper()
        if upper not in TYPES:
            fail(line, f'dns.{fn}() doesn\'t know the record type "{qtype}". Types: {", ".join(TYPES)}.',
                 reason="invalid_type")
        return upper

    def options_arg(line, options, fn):
        if options is None:
            return {}
        check_type(line, options, "map", f"dns.{fn}", "options")
        for key in options:
            if key not in ("timeout", "tcp", "retries"):
                fail(line, f'dns.{fn}() doesn\'t know the option "{key}". Options: timeout, tcp, retries.')
        out = {}
        if options.get("timeout") is not None:
            t = check_type(line, options["timeout"], "number", f"dns.{fn}", '"timeout"')
            if not 0 < t <= MAX_TIMEOUT:
                fail(line, f"dns.{fn}() needs a timeout between 0 and {MAX_TIMEOUT} seconds.")
            out["timeout"] = float(t)
        if options.get("tcp") is not None:
            out["tcp"] = check_type(line, options["tcp"], "bool", f"dns.{fn}", '"tcp"')
        if options.get("retries") is not None:
            r = check_type(line, options["retries"], "whole", f"dns.{fn}", '"retries"')
            if not 0 <= r <= 5:
                fail(line, f"dns.{fn}() allows 0 to 5 retries.")
            out["retries"] = r
        return out

    def servers_arg(line, server, fn):
        if server is None:
            servers = system_servers() or list(FALLBACK_SERVERS)
            return servers
        if isinstance(server, list):
            if not server:
                fail(line, f"dns.{fn}() got an empty list of servers.")
            return [servers_arg(line, s, fn)[0] for s in server]
        check_type(line, server, "text", f"dns.{fn}", "server")
        text = server.strip()
        if not text:
            fail(line, f"dns.{fn}() got empty text as the server.")
        host, port = split_server(text)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            fail(line, f"dns.{fn}() needs the server as an IP address like \"1.1.1.1\" or \"1.1.1.1:53\", "
                       f"not '{server}'.", reason="invalid_server")
        return [text]

    def ask(line, name, qtype, servers, o, fn):
        problem = None
        for server in servers:
            try:
                return query(name, qtype, server, o.get("timeout", DEFAULT_TIMEOUT), o.get("tcp", False),
                             o.get("retries", 1)), server
            except DnsProblem as err:
                problem = err
                if err.reason not in ("timeout", "network", "refused"):
                    break
        fail(line, problem.message, reason=problem.reason, name=name, type=qtype)

    @m.fn("lookup", 1, 4, doc=(
        "lookup(name, type?, server?, options?) -> list\n"
        "The records of a name, like the A records of \"example.com\".\n"
        "\n"
        "Arguments:\n"
        "- type: the kind of record (default: \"A\"). One of:\n"
        "  - A, AAAA, MX, TXT, NS, CNAME, PTR, SOA, SRV or CAA\n"
        "- server: an IP like \"1.1.1.1\", or \"ip:port\", or a list to try in turn\n"
        "  (default: this computer's resolvers).\n"
        "\n"
        "Options:\n"
        "- timeout: seconds to wait (default: 3).\n"
        "- tcp: true to ask over TCP (default: false).\n"
        "- retries: how many more tries after a failure (default: 1).\n"
        "\n"
        "Results:\n"
        "- An empty list means the name exists but has no such records.\n"
        "- A name that doesn't exist gives an error with reason \"nxdomain\".\n"
        "\n"
        "Example:\n"
        "    say(dns.lookup(\"example.com\"))\n"
        "    say(dns.lookup(\"example.com\", \"MX\"))"))
    def lookup(line, name, qtype="A", server=None, options=None):
        api.need_desktop(line, "The dns library")
        name = name_arg(line, name, "lookup")
        qtype = type_arg(line, qtype, "lookup")
        servers = servers_arg(line, server, "lookup")
        o = options_arg(line, options, "lookup")
        response, used = ask(line, name, qtype, servers, o, "lookup")
        if response["status"] == "nxdomain":
            fail(line, f"The name '{name}' doesn't exist (NXDOMAIN from {used}).", reason="nxdomain", name=name)
        if response["status"] != "ok":
            fail(line, f"The DNS server {used} answered with an error: {response['status']}.",
                 reason=response["status"], name=name)
        wanted = TYPE_NAMES[TYPES[qtype]]
        answers = response["answers"]
        if qtype != "ANY":
            answers = [r for r in answers if r["type"] in (wanted, "CNAME")] or answers
        return answers

    @m.fn("query", 2, 4, doc=(
        "query(name, type, server?, options?) -> map\n"
        "The full DNS answer. Use it when you want the authority section,\n"
        "or to see an nxdomain without an error.\n"
        "\n"
        "Returns a map with:\n"
        "- \"status\": \"ok\", \"nxdomain\", ...\n"
        "- \"answers\", \"authority\", \"additional\": lists of records.\n"
        "- \"truncated\", \"authoritative\", \"recursion_available\": true or false.\n"
        "- \"server\": the server that answered."))
    def query_(line, name, qtype, server=None, options=None):
        api.need_desktop(line, "The dns library")
        name = name_arg(line, name, "query")
        qtype = type_arg(line, qtype, "query")
        servers = servers_arg(line, server, "query")
        o = options_arg(line, options, "query")
        response, used = ask(line, name, qtype, servers, o, "query")
        response["server"] = used
        return response

    @m.fn("reverse", 1, 3, doc=(
        "reverse(ip, server?, options?) -> text\n"
        "The host name an IP points back to (its PTR record), or nil when it has none."))
    def reverse(line, ip, server=None, options=None):
        api.need_desktop(line, "The dns library")
        check_type(line, ip, "text", "dns.reverse", "ip")
        try:
            name = reverse_name(ip.strip())
        except ValueError:
            fail(line, f"dns.reverse() needs an IP address, but '{ip}' isn't one.", reason="invalid_ip")
        servers = servers_arg(line, server, "reverse")
        o = options_arg(line, options, "reverse")
        response, _used = ask(line, name, "PTR", servers, o, "reverse")
        for record in response["answers"]:
            if record["type"] == "PTR":
                return record["value"]
        return None

    @m.fn("reverse_name", 1, doc=(
        "reverse_name(ip) -> text\n"
        "The PTR name for an address: reverse_name(\"8.8.4.4\") is \"4.4.8.8.in-addr.arpa\"."))
    def reverse_name_(line, ip):
        check_type(line, ip, "text", "dns.reverse_name", "ip")
        try:
            return reverse_name(ip.strip())
        except ValueError:
            fail(line, f"dns.reverse_name() needs an IP address, but '{ip}' isn't one.", reason="invalid_ip")

    @m.fn("servers", 0, doc=(
        "servers() -> list\n"
        "The DNS servers this computer is set up to use (from /etc/resolv.conf or the Windows registry).\n"
        "Empty when none can be found. lookup() then uses 1.1.1.1 and 8.8.8.8."))
    def servers(line):
        api.need_desktop(line, "The dns library")
        return system_servers()

    @m.fn("is_name", 1, doc="is_name(x) -> bool\nTrue if x looks like a host name DNS could look up (letters, digits, dashes, dots).")
    def is_name(line, value):
        if not isinstance(value, str):
            return False
        text = value.strip().rstrip(".")
        if not text or len(text) > 253:
            return False
        import re
        return all(re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?", p) for p in text.split("."))

    m.value("types", list(TYPES), doc="The record types lookup() knows.")
    m.value("fallback_servers", list(FALLBACK_SERVERS), doc="Public resolvers used when the computer lists none.")
    return m
