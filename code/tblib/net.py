"""net: IP addresses, DNS and simple TCP checks (defensive use only)."""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout

from tblib._api import Module, check_type, fail

import tb as _tb

MAX_HOSTS = 65_536          # cidr_hosts() cap
MAX_PORTS = 1024            # check_ports() cap per call
MAX_WORKERS = 64            # threads used by check_ports()
MAX_BANNER = 512            # bytes banner() reads at most
MAX_TIMEOUT = 60            # seconds
DNS_TIMEOUT = 5             # seconds for resolve()/reverse()
MAX_UDP = 65_507            # bytes in one UDP message

AUTHORISED = ("Only check computers you own or have written permission to test; "
              "checking other people's systems can be against the law.")

# Well-known ports and what usually listens there (for reports and apps).
COMMON_PORTS = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 80: "http",
    110: "pop3", 135: "msrpc", 139: "netbios", 143: "imap", 443: "https",
    445: "smb", 587: "submission", 993: "imaps", 995: "pop3s", 1433: "mssql",
    1521: "oracle", 2049: "nfs", 3306: "mysql", 3389: "rdp", 5000: "dev-server",
    5432: "postgres", 5900: "vnc", 6379: "redis", 8000: "dev-server",
    8080: "http-alt", 8443: "https-alt", 9200: "elasticsearch", 27017: "mongodb",
}


# -- helpers other tblib modules can reuse ------------------------------------


def parse_ip(text):
    """An ipaddress object for text, or None if it isn't an IP address."""
    if not isinstance(text, str):
        return None
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def _run_with_timeout(work, timeout):
    """Run work() in a helper thread and give up after timeout seconds
    (getaddrinfo has no timeout of its own). Raises TimeoutError."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(work).result(timeout=timeout)
    except _FutureTimeout:
        raise TimeoutError from None
    finally:
        pool.shutdown(wait=False)


def lookup(host: str, timeout: float = DNS_TIMEOUT) -> list:
    """The IP addresses of host (IPv4 first). Raises socket.gaierror or
    TimeoutError; callers turn those into friendly messages."""
    ip = parse_ip(host)
    if ip is not None:
        return [str(ip)]
    infos = _run_with_timeout(lambda: socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP), timeout)
    seen = []
    for family, _type, _proto, _canon, addr in infos:
        if family in (socket.AF_INET, socket.AF_INET6) and addr[0] not in seen:
            seen.append(addr[0])
    seen.sort(key=lambda a: (":" in a))  # IPv4 first, keep order otherwise
    return seen


def load(api):
    m = Module(
        "net",
        "IP addresses, CIDR ranges, DNS lookups, simple TCP checks and UDP messages. "
        "The checking functions (tcp_check, check_ports, banner) make ordinary connections and are "
        "for defence. " + AUTHORISED,
    )

    # -- argument helpers ---------------------------------------------------

    def ip_arg(line, value, fn, arg="ip"):
        check_type(line, value, "text", f"net.{fn}", arg)
        ip = parse_ip(value)
        if ip is None:
            fail(line, f"net.{fn}() needs a valid IP address, but '{value}' isn't one.", reason="invalid_ip")
        return ip

    def cidr_arg(line, value, fn):
        check_type(line, value, "text", f"net.{fn}", "cidr")
        try:
            return ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            fail(line, f"net.{fn}() needs a CIDR range like \"192.168.1.0/24\", but '{value}' isn't one.",
                 reason="invalid_cidr")

    def host_arg(line, value, fn):
        check_type(line, value, "text", f"net.{fn}", "host")
        host = value.strip()
        if not host:
            fail(line, f"net.{fn}() needs a host name or IP address, but got empty text.")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host

    def port_arg(line, value, fn, arg="port"):
        check_type(line, value, "whole", f"net.{fn}", arg)
        port = int(value)
        if not 1 <= port <= 65535:
            fail(line, f"net.{fn}() needs a port from 1 to 65535, not {port}.", reason="invalid_port")
        return port

    def timeout_arg(line, value, fn, default):
        if value is None:
            return default
        check_type(line, value, "number", f"net.{fn}", "timeout")
        if not 0 < value <= MAX_TIMEOUT:
            fail(line, f"net.{fn}() needs a timeout between 0 and {MAX_TIMEOUT} seconds, not {_tb.to_text(value)}.")
        return float(value)

    def resolve_one(line, host, fn, timeout=DNS_TIMEOUT):
        """The first IP for host, with friendly errors."""
        try:
            ips = lookup(host, timeout)
        except TimeoutError:
            fail(line, f"Looking up '{host}' took too long. Check your internet connection.",
                 reason="timeout", host=host)
        except (socket.gaierror, UnicodeError, OSError):
            fail(line, f"I couldn't find the host '{host}'. Check the spelling, or your internet connection.",
                 reason="dns", host=host)
        if not ips:
            fail(line, f"I couldn't find an address for '{host}'.", reason="dns", host=host)
        return ips[0]

    def connect(ip, port, timeout):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((ip, port))
        except BaseException:
            sock.close()
            raise
        return sock

    def is_open(ip, port, timeout) -> bool:
        try:
            connect(ip, port, timeout).close()
            return True
        except OSError:
            return False

    # -- IP and CIDR helpers (work everywhere, even in the browser) ----------

    @m.fn("is_ip", 1, doc="is_ip(x) -> bool\nTrue if x is text holding an IPv4 or IPv6 address, like \"8.8.8.8\" or \"::1\".")
    def is_ip(line, value):
        return parse_ip(value) is not None

    @m.fn("is_ipv4", 1, doc="is_ipv4(x) -> bool\nTrue if x is an IPv4 address like \"192.168.1.10\".")
    def is_ipv4(line, value):
        return isinstance(parse_ip(value), ipaddress.IPv4Address)

    @m.fn("is_ipv6", 1, doc="is_ipv6(x) -> bool\nTrue if x is an IPv6 address like \"2001:db8::1\".")
    def is_ipv6(line, value):
        return isinstance(parse_ip(value), ipaddress.IPv6Address)

    @m.fn("is_private", 1, doc=(
        "is_private(ip) -> bool\n"
        "True for addresses that only work inside a home or company network (10.x, 172.16-31.x, "
        "192.168.x, fc00::/7), plus loopback and link-local. Handy to skip internal IPs before "
        "looking them up online."))
    def is_private(line, ip):
        return ip_arg(line, ip, "is_private").is_private

    @m.fn("is_loopback", 1, doc="is_loopback(ip) -> bool\nTrue for this computer talking to itself (127.x.x.x or ::1).")
    def is_loopback(line, ip):
        return ip_arg(line, ip, "is_loopback").is_loopback

    @m.fn("ip_info", 1, doc=(
        "ip_info(ip) -> map\n"
        "Facts about an address: {\"ip\", \"version\" (4 or 6), \"private\", \"loopback\", \"multicast\", "
        "\"reserved\", \"global\"}. \"global\" is true for normal public internet addresses."))
    def ip_info(line, ip):
        addr = ip_arg(line, ip, "ip_info")
        return {
            "ip": str(addr), "version": addr.version, "private": addr.is_private,
            "loopback": addr.is_loopback, "multicast": addr.is_multicast,
            "reserved": addr.is_reserved, "global": addr.is_global,
        }

    @m.fn("in_cidr", 2, doc="in_cidr(ip, cidr) -> bool\nTrue if ip is inside the range: in_cidr(\"10.0.0.7\", \"10.0.0.0/24\") is true.")
    def in_cidr(line, ip, cidr):
        addr = ip_arg(line, ip, "in_cidr")
        net = cidr_arg(line, cidr, "in_cidr")
        return addr.version == net.version and addr in net

    @m.fn("cidr_info", 1, doc=(
        "cidr_info(cidr) -> map\n"
        "About a range: {\"network\", \"netmask\", \"broadcast\", \"prefix\", \"size\" (number of addresses), "
        "\"first\", \"last\"} (first/last usable host)."))
    def cidr_info(line, cidr):
        net = cidr_arg(line, cidr, "cidr_info")
        if net.num_addresses <= 2:
            first, last = net[0], net[-1]
        else:
            first, last = net[1], net[-2]
        return {
            "network": str(net.network_address), "netmask": str(net.netmask),
            "broadcast": str(net.broadcast_address), "prefix": net.prefixlen,
            "size": net.num_addresses, "first": str(first), "last": str(last),
        }

    @m.fn("cidr_hosts", 1, doc=(
        f"cidr_hosts(cidr) -> list\n"
        f"Every usable host address in a range: cidr_hosts(\"192.168.1.0/30\") is [\"192.168.1.1\", \"192.168.1.2\"]. "
        f"At most {MAX_HOSTS:,} addresses (a /16), so big ranges give an error."))
    def cidr_hosts(line, cidr):
        net = cidr_arg(line, cidr, "cidr_hosts")
        if net.num_addresses > MAX_HOSTS + 2:
            fail(line, f"net.cidr_hosts() can list at most {MAX_HOSTS:,} addresses, and {cidr} has "
                       f"{net.num_addresses:,}. Pick a smaller range.", reason="too_many")
        if net.num_addresses <= 2:
            return [str(a) for a in net]
        return [str(a) for a in net.hosts()]

    # -- DNS ------------------------------------------------------------------

    @m.fn("resolve", 1, 2, doc=(
        "resolve(host, timeout?) -> list\n"
        "The IP addresses of a host name (IPv4 first): resolve(\"example.com\"). "
        "Gives an error you can catch if the name doesn't exist. timeout is in seconds (default 5)."))
    def resolve(line, host, timeout=None):
        api.need_desktop(line, "The net library")
        host = host_arg(line, host, "resolve")
        timeout = timeout_arg(line, timeout, "resolve", DNS_TIMEOUT)
        try:
            ips = lookup(host, timeout)
        except TimeoutError:
            fail(line, f"Looking up '{host}' took too long. Check your internet connection.",
                 reason="timeout", host=host)
        except (socket.gaierror, UnicodeError, OSError):
            fail(line, f"I couldn't find the host '{host}'. Check the spelling, or your internet connection.",
                 reason="dns", host=host)
        return ips

    @m.fn("reverse", 1, 2, doc=(
        "reverse(ip, timeout?) -> text\n"
        "The host name an IP address points back to (reverse DNS, PTR record), or nil if it has none."))
    def reverse(line, ip, timeout=None):
        api.need_desktop(line, "The net library")
        addr = ip_arg(line, ip, "reverse")
        timeout = timeout_arg(line, timeout, "reverse", DNS_TIMEOUT)
        try:
            return _run_with_timeout(lambda: socket.gethostbyaddr(str(addr))[0], timeout)
        except TimeoutError:
            fail(line, f"Looking up '{ip}' took too long. Check your internet connection.", reason="timeout")
        except (socket.herror, socket.gaierror, OSError):
            return None

    @m.fn("hostname", 0, doc="hostname() -> text\nThis computer's own name on the network.")
    def hostname(line):
        api.need_desktop(line, "The net library")
        return socket.gethostname()

    @m.fn("my_ip", 0, doc=(
        "my_ip() -> text\n"
        "This computer's local IP address on its network (like \"192.168.1.23\"), or \"127.0.0.1\" "
        "when it isn't connected. It sends nothing over the network; it is not your public IP."))
    def my_ip(line):
        api.need_desktop(line, "The net library")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Connecting a UDP socket only picks a route; no packet is sent.
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed on the internet
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    # -- TCP checks (authorised use only) ---------------------------------------

    @m.fn("tcp_check", 2, 3, doc=(
        "tcp_check(host, port, timeout?) -> bool\n"
        "True if something accepts a TCP connection on that port (a normal connect, then it hangs up). "
        "timeout is in seconds (default 1). " + AUTHORISED))
    def tcp_check(line, host, port, timeout=None):
        api.need_desktop(line, "The net library")
        host = host_arg(line, host, "tcp_check")
        port = port_arg(line, port, "tcp_check")
        timeout = timeout_arg(line, timeout, "tcp_check", 1.0)
        ip = resolve_one(line, host, "tcp_check")
        return is_open(ip, port, timeout)

    def ports_arg(line, ports):
        fn = "net.check_ports"
        if isinstance(ports, str):
            items = []
            for part in ports.replace(" ", "").split(","):
                if not part:
                    continue
                low, sep, high = part.partition("-")
                if not low.isdigit() or (sep and not high.isdigit()):
                    fail(line, f"{fn}() couldn't read the ports '{ports}'. Use a list like [22, 80] "
                               "or text like \"22,80,8000-8010\".", reason="invalid_port")
                a, b = int(low), int(high) if sep else int(low)
                if b < a:
                    a, b = b, a
                if b - a >= MAX_PORTS:
                    fail(line, f"{fn}() checks at most {MAX_PORTS} ports at a time.", reason="too_many")
                items.extend(range(a, b + 1))
        else:
            check_type(line, ports, "list|text", fn, "ports")
            items = ports
        result = []
        for p in items:
            p = port_arg(line, p, "check_ports", "each port")
            if p not in result:
                result.append(p)
            if len(result) > MAX_PORTS:
                fail(line, f"{fn}() checks at most {MAX_PORTS} ports at a time; split them into smaller groups.",
                     reason="too_many")
        return result

    @m.fn("check_ports", 2, 3, doc=(
        f"check_ports(host, ports, timeout?) -> map\n"
        f"Try a normal TCP connection to each port and give back a map port -> true/false (open or not), "
        f"in the order given. ports is a list like [22, 80, 443] or text like \"22,80,8000-8010\" "
        f"(at most {MAX_PORTS}). Several ports are tried at once; timeout is per port, in seconds "
        f"(default 1). {AUTHORISED}"))
    def check_ports(line, host, ports, timeout=None):
        api.need_desktop(line, "The net library")
        host = host_arg(line, host, "check_ports")
        wanted = ports_arg(line, ports)
        timeout = timeout_arg(line, timeout, "check_ports", 1.0)
        ip = resolve_one(line, host, "check_ports")
        if not wanted:
            return {}
        workers = min(MAX_WORKERS, len(wanted))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            states = list(pool.map(lambda p: is_open(ip, p, timeout), wanted))
        return dict(zip(wanted, states))

    @m.fn("banner", 2, 3, doc=(
        f"banner(host, port, timeout?) -> text\n"
        f"Connect and read the greeting a service sends first (SSH, FTP and mail servers do), at most "
        f"{MAX_BANNER} bytes. Gives \"\" if the service says nothing before the timeout (seconds, default 2). "
        f"It sends nothing itself. {AUTHORISED}"))
    def banner(line, host, port, timeout=None):
        api.need_desktop(line, "The net library")
        host = host_arg(line, host, "banner")
        port = port_arg(line, port, "banner")
        timeout = timeout_arg(line, timeout, "banner", 2.0)
        ip = resolve_one(line, host, "banner")
        try:
            sock = connect(ip, port, timeout)
        except ConnectionRefusedError:
            fail(line, f"Nothing is listening on {host} port {port}.", reason="refused", host=host, port=port)
        except (TimeoutError, socket.timeout):
            fail(line, f"{host} port {port} didn't answer in time.", reason="timeout", host=host, port=port)
        except OSError as err:
            fail(line, f"I couldn't connect to {host} port {port}: {err.strerror or err}.",
                 reason="connect", host=host, port=port)
        data = b""
        try:
            while len(data) < MAX_BANNER:
                chunk = sock.recv(MAX_BANNER - len(data))
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
        except OSError:  # timeout or reset: keep what arrived
            pass
        finally:
            sock.close()
        return data.decode("utf-8", errors="replace").strip()

    # -- UDP (data-agent) ---------------------------------------------------------

    def udp_socket(ip):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return socket.socket(family, socket.SOCK_DGRAM)

    def data_arg(line, data, fn):
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        check_type(line, data, "text", f"net.{fn}", "data")
        raw = data.encode("utf-8")
        if len(raw) > MAX_UDP:
            fail(line, f"net.{fn}() sends at most {MAX_UDP:,} bytes in one UDP message, not {len(raw):,}.")
        return raw

    @m.fn("udp_send", 3, 4, doc=(
        "udp_send(host, port, data, timeout?) -> text\n"
        "Send one UDP message (text) to host:port. Without a timeout it just sends and gives nil (UDP has no "
        "delivery confirmation). With a timeout in seconds it waits that long for one reply and gives it as "
        "text, or nil if nothing came back. " + AUTHORISED))
    def udp_send(line, host, port, data, timeout=None):
        api.need_desktop(line, "The net library")
        host = host_arg(line, host, "udp_send")
        port = port_arg(line, port, "udp_send")
        raw = data_arg(line, data, "udp_send")
        wait = timeout_arg(line, timeout, "udp_send", None)
        ip = resolve_one(line, host, "udp_send")
        sock = udp_socket(ip)
        try:
            sock.settimeout(wait if wait is not None else 2.0)
            try:
                sock.sendto(raw, (ip, port))
            except OSError as err:
                fail(line, f"I couldn't send to {host} port {port}: {err.strerror or err}.", reason="send",
                     host=host, port=port)
            if wait is None:
                return None
            try:
                reply, _addr = sock.recvfrom(MAX_UDP)
            except (TimeoutError, socket.timeout):
                return None
            except ConnectionError:
                # ICMP port unreachable: nothing is listening. Linux and macOS
                # report it as ConnectionRefusedError, Windows as
                # ConnectionResetError (WSAECONNRESET).
                return None
            except OSError as err:
                fail(line, f"Waiting for a reply from {host} port {port} failed: {err.strerror or err}.",
                     reason="receive", host=host, port=port)
            return reply.decode("utf-8", errors="replace")
        finally:
            sock.close()

    @m.fn("udp_listen", 2, 3, doc=(
        "udp_listen(port, fn, options?) -> listener\n"
        "Receive UDP messages on a port, in the background. fn(message) is called for each one with "
        "{\"data\": text, \"ip\", \"port\"}; if fn gives back text, it is sent as the reply. Port 0 picks a "
        "free port. options: {\"host\": \"127.0.0.1\" (default; \"0.0.0.0\" to accept from the network)}. "
        "The listener has .port, .host, .running, stop() and wait(seconds?) (block until stopped). " + AUTHORISED))
    def udp_listen(line, port, fn, options=None):
        api.need_desktop(line, "The net library")
        check_type(line, port, "whole", "net.udp_listen", "port")
        port = int(port)
        if not 0 <= port <= 65535:
            fail(line, f"net.udp_listen() needs a port from 0 to 65535, not {port}.", reason="invalid_port")
        check_type(line, fn, "function", "net.udp_listen", "fn")
        host = "127.0.0.1"
        if options is not None:
            check_type(line, options, "map", "net.udp_listen", "options")
            for key in options:
                if key != "host":
                    fail(line, f'net.udp_listen() doesn\'t know the option "{key}". Options: host.')
            if options.get("host") is not None:
                host = host_arg(line, options["host"], "udp_listen")
        ip = resolve_one(line, host, "udp_listen") if parse_ip(host) is None else host
        sock = udp_socket(ip)
        # No SO_REUSEADDR: for UDP it lets two listeners share a port (Linux,
        # macOS) or steal it (Windows). On Windows ask for the port exclusively.
        from tblib import _sockets
        try:
            _sockets.exclusive(sock)
            sock.bind((ip, port))
        except OSError as err:
            sock.close()
            if _sockets.port_in_use(err, port):
                fail(line, f"Port {port} is already in use by another program.", reason="in_use", port=port)
            if isinstance(err, PermissionError):
                fail(line, f"I'm not allowed to listen on port {port} (ports below 1024 need administrator "
                     "rights).", reason="permission", port=port)
            fail(line, f"I couldn't listen on {host} port {port}: {err.strerror or err}.", reason="bind", port=port)
        sock.settimeout(0.2)
        from tblib._threads import guarded_call
        import threading
        stop = threading.Event()
        state = {"error": None}
        listener = {"port": sock.getsockname()[1], "host": ip, "running": True}

        def serve():
            try:
                while not stop.is_set():
                    try:
                        data, addr = sock.recvfrom(MAX_UDP)
                    except (TimeoutError, socket.timeout):
                        continue
                    except ConnectionResetError:
                        # Windows: an earlier reply hit a closed port (ICMP
                        # port unreachable). The listener itself is fine.
                        continue
                    except OSError:
                        break
                    message = {"data": data.decode("utf-8", errors="replace"), "ip": addr[0], "port": addr[1]}
                    reply, err = guarded_call(api, fn, [message], line)
                    if err is not None:
                        state["error"] = err
                        api.output(f"net.udp_listen: {err.message}")
                        continue
                    if isinstance(reply, (str, bytes, bytearray)) and reply != "":
                        raw = reply.encode("utf-8") if isinstance(reply, str) else bytes(reply)
                        try:
                            sock.sendto(raw[:MAX_UDP], addr)
                        except OSError:
                            pass
            finally:
                listener["running"] = False
                sock.close()

        thread = threading.Thread(target=serve, name=f"tb-udp-{listener['port']}", daemon=True)
        thread.start()

        def stop_(line):
            stop.set()
            thread.join(2.0)
            listener["running"] = False
            return None

        def wait(line, seconds=None):
            if seconds is not None:
                check_type(line, seconds, "number", "listener.wait", "seconds")
            try:
                thread.join(seconds)
            except KeyboardInterrupt:
                stop_(line)
            return not thread.is_alive()

        listener["stop"] = _tb.Builtin("listener.stop", stop_, (0, 0), "stop()\nStop listening and free the port.")
        listener["wait"] = _tb.Builtin("listener.wait", wait, (0, 1),
                                       "wait(seconds?) -> bool\nBlock until the listener stops (or the seconds "
                                       "pass); true if it has stopped. Ctrl+C stops it.")
        return listener

    m.value("common_ports", dict(COMMON_PORTS), doc=(
        "A map of well-known port numbers to the service usually found there: "
        "net.common_ports[22] is \"ssh\"."))
    return m
