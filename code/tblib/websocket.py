"""websocket: talk to WebSocket servers (RFC 6455), ws:// and wss://."""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import urllib.parse

from tbcore import tasks as _tasks
from tblib._api import Module, check_type, fail
from tblib._threads import ignore_sigpipe

import tb as _tb

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 3600
MAX_MESSAGE = 16 * 1024 * 1024   # bytes in one (possibly fragmented) message
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
CLOSE_REASONS = {
    1000: "normal", 1001: "going away", 1002: "protocol error", 1003: "unsupported data",
    1006: "connection lost", 1007: "bad data", 1008: "policy violation", 1009: "message too big",
    1011: "server error",
}


class WsProblem(Exception):
    def __init__(self, message, reason):
        super().__init__(message)
        self.message, self.reason = message, reason


# -- framing (shared with the server library) ---------------------------------


def encode_frame(opcode: int, payload: bytes, mask: bool) -> bytes:
    head = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        head += bytes([(0x80 if mask else 0) | length])
    elif length < 65536:
        head += bytes([(0x80 if mask else 0) | 126]) + struct.pack("!H", length)
    else:
        head += bytes([(0x80 if mask else 0) | 127]) + struct.pack("!Q", length)
    if mask:
        key = os.urandom(4)
        masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return head + key + masked
    return head + payload


def recv_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise WsProblem("The other side closed the connection.", "closed")
        data += chunk
    return data


def read_frame(sock) -> tuple[bool, int, bytes]:
    """(fin, opcode, payload) for one frame; unmasks client frames."""
    b1, b2 = recv_exact(sock, 2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    if length > MAX_MESSAGE:
        raise WsProblem(f"The other side sent a message bigger than {MAX_MESSAGE // 1024 // 1024} MB.", "too_big")
    key = recv_exact(sock, 4) if masked else None
    payload = recv_exact(sock, length) if length else b""
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


class Connection:
    """One WebSocket connection, either side. Thread-safe sends."""

    def __init__(self, sock, mask: bool, url: str = ""):
        self.sock = sock
        self.mask = mask
        self.url = url
        self.open = True
        self.close_code = None
        self.close_reason = None
        self.send_lock = threading.Lock()
        self.recv_lock = threading.Lock()

    def send(self, data, binary: bool = False):
        if not self.open:
            raise WsProblem("This WebSocket is closed.", "closed")
        payload = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        if len(payload) > MAX_MESSAGE:
            raise WsProblem(f"A message can be at most {MAX_MESSAGE // 1024 // 1024} MB.", "too_big")
        opcode = OP_BINARY if binary or isinstance(data, (bytes, bytearray)) else OP_TEXT
        self._write(encode_frame(opcode, bytes(payload), self.mask))

    def _write(self, raw: bytes):
        with _tasks.blocking(), self.send_lock:
            try:
                self.sock.sendall(raw)
            except OSError as err:
                self.open = False
                raise WsProblem(f"Sending on the WebSocket failed: {err.strerror or err}.", "network") from None

    def receive(self, timeout=None):
        """The next text (or bytes) message; None on timeout; raises WsProblem
        "closed" when the connection ends. Handles ping/pong and fragments.
        Inside parallel.loop() other tasks run while it waits."""
        with _tasks.blocking():
            return self._receive(timeout)

    def _receive(self, timeout):
        if not self.open:
            raise WsProblem("This WebSocket is closed.", "closed")
        with self.recv_lock:
            self.sock.settimeout(timeout)
            parts, opcode = [], None
            while True:
                try:
                    fin, op, payload = read_frame(self.sock)
                except (TimeoutError, socket.timeout):
                    return None
                except WsProblem:
                    self.open = False
                    self.close_code = self.close_code or 1006
                    raise
                except OSError as err:
                    self.open = False
                    raise WsProblem(f"Reading from the WebSocket failed: {err.strerror or err}.", "network") from None
                if op == OP_PING:
                    try:
                        self._write(encode_frame(OP_PONG, payload, self.mask))
                    except WsProblem:
                        pass
                    continue
                if op == OP_PONG:
                    continue
                if op == OP_CLOSE:
                    code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                    self.close_code = code
                    self.close_reason = payload[2:].decode("utf-8", errors="replace")
                    try:
                        self._write(encode_frame(OP_CLOSE, payload[:2], self.mask))
                    except WsProblem:
                        pass
                    self.open = False
                    self._shutdown()
                    raise WsProblem(f"The other side closed the WebSocket ({code} {CLOSE_REASONS.get(code, '')}"
                                    f"{': ' + self.close_reason if self.close_reason else ''}).", "closed")
                if op in (OP_TEXT, OP_BINARY):
                    opcode = op
                    parts = [payload]
                elif op == OP_CONT:
                    if opcode is None:
                        raise WsProblem("The other side sent a broken frame sequence.", "protocol")
                    parts.append(payload)
                else:
                    raise WsProblem(f"The other side sent an unknown frame type ({op}).", "protocol")
                if sum(len(p) for p in parts) > MAX_MESSAGE:
                    raise WsProblem(f"The other side sent a message bigger than {MAX_MESSAGE // 1024 // 1024} MB.",
                                    "too_big")
                if fin:
                    data = b"".join(parts)
                    if opcode == OP_TEXT:
                        return data.decode("utf-8", errors="replace")
                    return bytes(data)

    def close(self, code: int = 1000, reason: str = ""):
        with _tasks.blocking():
            self._close(code, reason)

    def _close(self, code, reason):
        if not self.open:
            self._shutdown()
            return
        self.open = False
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:120]
        try:
            with self.send_lock:
                self.sock.sendall(encode_frame(OP_CLOSE, payload, self.mask))
            self.sock.settimeout(1.0)
            try:
                fin, op, _ = read_frame(self.sock)
            except Exception:
                pass
        except OSError:
            pass
        self.close_code = self.close_code or code
        self._shutdown()

    def _shutdown(self):
        try:
            self.sock.close()
        except OSError:
            pass


# -- client handshake ------------------------------------------------------------


def connect(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
            ca_file: str | None = None, insecure: bool = False) -> Connection:
    ignore_sigpipe()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise WsProblem(f"A WebSocket address starts with ws:// or wss://, not '{parts.scheme or url}'.", "url")
    if not parts.hostname:
        raise WsProblem(f"The address '{url}' has no host name.", "url")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except socket.gaierror:
        raise WsProblem(f"I couldn't find the host '{host}'. Check the spelling, or your internet connection.", "dns") from None
    except ConnectionRefusedError:
        raise WsProblem(f"Nothing is listening on {host} port {port}.", "refused") from None
    except (TimeoutError, socket.timeout):
        raise WsProblem(f"{host} port {port} didn't answer within {_tb.to_text(timeout)} seconds.", "timeout") from None
    except OSError as err:
        raise WsProblem(f"I couldn't connect to {host} port {port}: {err.strerror or err}.", "connect") from None
    try:
        if parts.scheme == "wss":
            ctx = ssl.create_default_context(cafile=ca_file)
            if insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            try:
                sock = ctx.wrap_socket(sock, server_hostname=host)
            except ssl.SSLCertVerificationError as err:
                raise WsProblem(f"The secure connection to {host} failed: {getattr(err, 'verify_message', err)}. "
                                "The certificate couldn't be trusted.", "tls") from None
            except ssl.SSLError as err:
                raise WsProblem(f"The secure connection to {host} failed ({err.reason or err}).", "tls") from None
        key = base64.b64encode(os.urandom(16)).decode()
        hostport = host if port in (80, 443) else f"{host}:{port}"
        lines = [f"GET {path} HTTP/1.1", f"Host: {hostport}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13", "User-Agent: TB/3.0"]
        for name, value in (headers or {}).items():
            lines.append(f"{name}: {value}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise WsProblem(f"{host} closed the connection during the WebSocket handshake.", "handshake")
            response += chunk
            if len(response) > 65536:
                raise WsProblem(f"{host} sent a handshake answer that is too long.", "handshake")
        head, _, rest = response.partition(b"\r\n\r\n")
        status_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
        status_parts = status_line.split(" ", 2)
        code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
        resp_headers = {}
        for h in header_lines:
            name, _, value = h.partition(":")
            resp_headers[name.strip().lower()] = value.strip()
        if code != 101:
            raise WsProblem(f"{host} didn't accept the WebSocket: it answered HTTP {code} "
                            f"{status_parts[2] if len(status_parts) > 2 else ''}".strip() + ".", "handshake")
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if resp_headers.get("sec-websocket-accept") != expected:
            raise WsProblem(f"{host} sent a wrong WebSocket accept key; this isn't a proper WebSocket server.",
                            "handshake")
        conn = Connection(sock, mask=True, url=url)
        conn.response_headers = resp_headers
        if rest:  # frames that arrived with the handshake
            conn.sock = _Prefixed(sock, rest)
        return conn
    except (TimeoutError, socket.timeout):
        sock.close()
        raise WsProblem(f"{host} didn't finish the WebSocket handshake within {_tb.to_text(timeout)} seconds.",
                        "timeout") from None
    except OSError as err:
        sock.close()
        raise WsProblem(f"The WebSocket handshake with {host} failed: {err.strerror or err}.", "network") from None
    except WsProblem:
        sock.close()
        raise


class _Prefixed:
    """A socket wrapper that first hands out bytes already received."""

    def __init__(self, sock, data: bytes):
        self.sock, self.buffer = sock, data

    def recv(self, n):
        if self.buffer:
            out, self.buffer = self.buffer[:n], self.buffer[n:]
            return out
        return self.sock.recv(n)

    def __getattr__(self, name):
        return getattr(self.sock, name)


# -- TB handle (shared with the server library) ---------------------------------


def make_handle(api, conn: Connection, line=None, owner="ws") -> dict:
    """A TB map with send/receive/close/each for a Connection."""
    handle = {"url": conn.url}

    def method(name, lo, hi, func, doc):
        handle[name] = _tb.Builtin(f"{owner}.{name}", func, (lo, hi), doc)

    def problem(line, err: WsProblem):
        handle["open"] = conn.open
        fail(line, err.message, reason=err.reason, code=conn.close_code)

    def send(line, data):
        if isinstance(data, (dict, list)):
            from tblib import json as _json
            data = _json.dump(data, line)
        elif not isinstance(data, (str, bytes, bytearray)):
            data = _tb.to_text(data)
        try:
            conn.send(data)
        except WsProblem as err:
            problem(line, err)
        return None

    def receive(line, timeout=None):
        if timeout is not None:
            check_type(line, timeout, "number", f"{owner}.receive", "timeout")
            if not 0 < timeout <= MAX_TIMEOUT:
                fail(line, f"{owner}.receive() needs a timeout between 0 and {MAX_TIMEOUT} seconds.")
        try:
            message = conn.receive(timeout)
        except WsProblem as err:
            problem(line, err)
        handle["open"] = conn.open
        if isinstance(message, bytes):
            return message.decode("utf-8", errors="replace")
        return message

    def close(line, code=1000, reason=""):
        code = check_type(line, code, "whole", f"{owner}.close", "code")
        check_type(line, reason, "text", f"{owner}.close", "reason")
        conn.close(code, reason)
        handle["open"] = False
        return None

    def each(line, fn, timeout=None):
        check_type(line, fn, "function", f"{owner}.each", "fn")
        if timeout is not None:
            check_type(line, timeout, "number", f"{owner}.each", "timeout")
        while conn.open:
            try:
                message = conn.receive(timeout)
            except WsProblem as err:
                if err.reason == "closed":
                    break
                problem(line, err)
            if message is None:
                break
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            keep_going = api.call(fn, [message], line)
            if keep_going is False:
                break
        handle["open"] = conn.open
        return None

    def is_open(line):
        handle["open"] = conn.open
        return conn.open

    method("send", 1, 1, send, "send(data)\nSend a text message (a map or list is sent as JSON).")
    method("receive", 0, 1, receive, "receive(timeout?) -> text\nWait for the next message. With a timeout in "
                                      "seconds, gives nil when nothing arrives in time. Pings are answered for "
                                      "you. Error (reason \"closed\") when the other side hangs up.")
    method("close", 0, 2, close, "close(code?, reason?)\nHang up politely (code 1000 by default).")
    method("each", 1, 2, each, "each(fn, timeout?)\nCall fn(message) for every message until the connection "
                                 "closes, fn gives back false, or (with a timeout) nothing arrives for that long.")
    method("is_open", 0, 0, is_open, "is_open() -> bool\nTrue while the connection is up.")
    handle["open"] = conn.open
    handle["close_code"] = None
    return handle


def load(api):
    m = Module(
        "websocket",
        "Talk to WebSocket servers: websocket.connect(\"wss://host/path\") gives a connection with send(text), "
        "receive(timeout?), each(fn), close() and .open. Text and JSON (maps/lists) messages, TLS for wss://, "
        "pings answered automatically, messages up to 16 MB. For the server side see the server library's "
        "app.ws().",
    )

    @m.fn("connect", 1, 2, doc=(
        "connect(url, options?) -> ws\n"
        "Open a WebSocket to ws://host:port/path, or to wss://... (TLS).\n"
        "\n"
        "Options:\n"
        "- headers: a map of {name: value} to send, for tokens.\n"
        "- timeout: seconds to connect and complete the handshake (default: 10).\n"
        "- ca_file: a PEM file of certificates to trust.\n"
        "- insecure: true to skip certificate checks (testing only).\n"
        "\n"
        "Example:\n"
        "    let ws = websocket.connect(\"wss://example.com/chat\")\n"
        "    ws.send(\"hello\")\n"
        "    say(ws.receive(5))\n"
        "    ws.close()"))
    def connect_(line, url, options=None):
        api.need_desktop(line, "The websocket library")
        check_type(line, url, "text", "websocket.connect", "url")
        headers, timeout, ca_file, insecure = None, DEFAULT_TIMEOUT, None, False
        if options is not None:
            check_type(line, options, "map", "websocket.connect", "options")
            for key in options:
                if key not in ("headers", "timeout", "ca_file", "insecure"):
                    fail(line, f'websocket.connect() doesn\'t know the option "{key}". Options: headers, timeout, '
                               f'ca_file, insecure.')
            if options.get("headers") is not None:
                headers = check_type(line, options["headers"], "map", "websocket.connect", '"headers"')
                headers = {str(k): _tb.to_text(v) for k, v in headers.items()}
                for name in headers:
                    if "\r" in name or "\n" in name or "\r" in headers[name] or "\n" in headers[name]:
                        fail(line, "websocket.connect() header names and values can't contain line breaks.")
            if options.get("timeout") is not None:
                timeout = check_type(line, options["timeout"], "number", "websocket.connect", '"timeout"')
                if not 0 < timeout <= MAX_TIMEOUT:
                    fail(line, f"websocket.connect() needs a timeout between 0 and {MAX_TIMEOUT} seconds.")
            if options.get("ca_file") is not None:
                ca_file = os.path.expanduser(check_type(line, options["ca_file"], "text", "websocket.connect", '"ca_file"'))
                if not os.path.isfile(ca_file):
                    fail(line, f"I couldn't find the CA file '{options['ca_file']}'.")
            if options.get("insecure") is not None:
                insecure = check_type(line, options["insecure"], "bool", "websocket.connect", '"insecure"')
        try:
            with _tasks.blocking():
                conn = connect(url.strip(), headers, float(timeout), ca_file, insecure)
        except WsProblem as err:
            fail(line, err.message, reason=err.reason, url=url)
        handle = make_handle(api, conn, line)
        handle["headers"] = dict(getattr(conn, "response_headers", {}))
        return handle

    @m.fn("is_url", 1, doc="is_url(x) -> bool\nTrue if x is text starting with ws:// or wss:// and naming a host.")
    def is_url(line, value):
        if not isinstance(value, str):
            return False
        parts = urllib.parse.urlsplit(value.strip())
        return parts.scheme in ("ws", "wss") and bool(parts.hostname)

    m.value("max_message", MAX_MESSAGE, doc="The biggest message send() or receive() handles (16 MB).")
    m.value("close_reasons", dict(CLOSE_REASONS), doc="What the standard close codes mean.")
    return m
