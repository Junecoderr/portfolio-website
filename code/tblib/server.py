"""server: a small web server for local tools and dashboards (HTTP + WebSocket)."""

from __future__ import annotations

import base64
import hashlib
import json as _pyjson
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tblib._api import MANY, Module, check_type, fail, fail_os
from tblib._threads import guarded_call, ignore_sigpipe

import tb as _tb

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_BODY = 10 * 1024 * 1024          # bytes a request body may have
MAX_HEADER = 64 * 1024
MAX_STATIC = 500 * 1024 * 1024
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(\*?)\}")
RUNNING: list = []                   # every live server, so tests (and Ctrl+C) can stop them all
RESPONSE_KEY = "_response"


def stop_all():
    """Stop every running TB server (used by tests)."""
    for app in list(RUNNING):
        try:
            app.shutdown()
        except Exception:
            pass


def compile_path(pattern: str):
    """'/items/{id}' -> regex with named groups; '{rest*}' matches slashes too."""
    if not pattern.startswith("/"):
        pattern = "/" + pattern
    pattern = pattern.rstrip("/") or "/"
    regex = ""
    pos = 0
    for m in PARAM.finditer(pattern):
        regex += re.escape(pattern[pos:m.start()])
        regex += f"(?P<{m.group(1)}>.+)" if m.group(2) else f"(?P<{m.group(1)}>[^/]+)"
        pos = m.end()
    regex += re.escape(pattern[pos:])
    return re.compile("^" + regex + "/?$")


def make_response(status: int, body, headers: dict | None = None) -> dict:
    return {RESPONSE_KEY: True, "status": int(status), "body": body, "headers": dict(headers or {})}


def to_response(value, line=None) -> dict:
    """What a handler gave back, as a response map."""
    if isinstance(value, dict) and value.get(RESPONSE_KEY):
        return value
    if value is None:
        return make_response(204, b"")
    if isinstance(value, str):
        return make_response(200, value, {"Content-Type": "text/plain; charset=utf-8"})
    if isinstance(value, (bytes, bytearray)):
        return make_response(200, bytes(value), {"Content-Type": "application/octet-stream"})
    if isinstance(value, (dict, list)):
        from tblib import json as _json
        return make_response(200, _json.dump(value, line), {"Content-Type": "application/json; charset=utf-8"})
    return make_response(200, _tb.to_text(value), {"Content-Type": "text/plain; charset=utf-8"})


class App:
    def __init__(self, api, options: dict):
        self.api = api
        self.routes: list = []          # (methods set|None, pattern, regex, fn, kind)
        self.statics: list = []         # (prefix, folder)
        self.log = options.get("log", True)
        self.max_body = options.get("max_body", MAX_BODY)
        self.cors = options.get("cors", False)
        self.name = options.get("name", "TB server")
        self.server = None
        self.thread = None
        self.host = None
        self.port = None
        self.handle = None
        self.errors: list = []

    # -- routing ----------------------------------------------------------------

    def add(self, methods, pattern, fn, kind="http"):
        self.routes.append((methods, pattern, compile_path(pattern), fn, kind))

    def match(self, method, path):
        """(route, params) or (None, allowed_methods). path is already %-decoded."""
        allowed = set()
        for methods, pattern, regex, fn, kind in self.routes:
            m = regex.match(path)
            if not m:
                continue
            if kind == "ws":
                return (methods, pattern, regex, fn, kind), dict(m.groupdict())
            if methods is None or method in methods or (method == "HEAD" and "GET" in methods):
                return (methods, pattern, regex, fn, kind), dict(m.groupdict())
            allowed |= methods
        return None, allowed

    def static_file(self, path):
        for prefix, folder in self.statics:
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                rel = path[len(prefix.rstrip("/")):].lstrip("/")
                rel = urllib.parse.unquote(rel)
                if "\x00" in rel:
                    return None, 400
                root = os.path.realpath(folder)
                target = os.path.realpath(os.path.join(root, *[p for p in rel.split("/") if p]))
                if target != root and not target.startswith(root + os.sep):
                    return None, 403
                if os.path.isdir(target):
                    target = os.path.join(target, "index.html")
                if not os.path.isfile(target):
                    return None, 404
                return target, 200
        return None, None

    # -- lifecycle --------------------------------------------------------------

    def bind(self, line, host, port):
        ignore_sigpipe()
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "TB/3.0"
            sys_version = ""

            def log_message(self, fmt, *args):  # quiet; we log ourselves
                pass

            def do_GET(self): app.dispatch(self)
            def do_POST(self): app.dispatch(self)
            def do_PUT(self): app.dispatch(self)
            def do_PATCH(self): app.dispatch(self)
            def do_DELETE(self): app.dispatch(self)
            def do_HEAD(self): app.dispatch(self)
            def do_OPTIONS(self): app.dispatch(self)

        from tblib import _sockets

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            # SO_REUSEADDR lets a restarted server reuse a port whose old
            # connections linger (Linux, macOS). On Windows it would let the
            # server share a port another program is using, so there the
            # socket asks for the port exclusively instead.
            allow_reuse_address = not _sockets.WINDOWS
            request_queue_size = 64
            address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

            def server_bind(self):
                _sockets.exclusive(self.socket)
                super().server_bind()

        try:
            self.server = Server((host, port), Handler)
        except OSError as err:
            if _sockets.port_in_use(err, port):
                fail(line, f"Port {port} is already in use by another program. Pick another port, or stop it.",
                     reason="in_use", port=port)
            if isinstance(err, PermissionError):
                fail(line, f"I'm not allowed to listen on port {port} (ports below 1024 need administrator "
                     "rights).", reason="permission", port=port)
            if _sockets.no_such_address(err):
                fail(line, f"This computer has no address '{host}' to listen on.", reason="bind", host=host)
            fail(line, f"I couldn't listen on {host} port {port}: {err.strerror or err}.", reason="bind", port=port)
        self.server.timeout = 0.5
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        RUNNING.append(self)
        if host not in ("127.0.0.1", "::1", "localhost") and self.log:
            self.api.output(f"Note: listening on {host}, so other computers on the network can reach this server.")

    @property
    def url(self):
        if self.port is None:
            return None
        host = f"[{self.host}]" if ":" in str(self.host) else self.host
        return f"http://{host}:{self.port}"

    def serve_forever(self):
        server = self.server
        if server is None:
            return
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            try:
                server.server_close()
            except OSError:
                pass

    def shutdown(self):
        server, self.server = self.server, None
        if server is not None:
            server.shutdown()
            if threading.current_thread() is not self.thread:
                if self.thread is not None:
                    self.thread.join(5)
            try:
                server.server_close()
            except OSError:
                pass
        if self in RUNNING:
            RUNNING.remove(self)
        if self.handle is not None:
            self.handle["running"] = False

    # -- one request ------------------------------------------------------------

    def dispatch(self, h: BaseHTTPRequestHandler):
        started = time.perf_counter()
        status = 500
        try:
            status = self.handle_request(h)
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        except Exception as err:  # a bug in the server library itself: never kill the thread
            try:
                self.send(h, make_response(500, _pyjson.dumps({"error": f"Server problem: {err}"}),
                                           {"Content-Type": "application/json; charset=utf-8"}), "GET")
            except Exception:
                pass
        if self.log and status != 101:
            ms = (time.perf_counter() - started) * 1000
            self.api.output(f"{h.client_address[0]} {h.command} {h.path} {status} {ms:.0f}ms")

    def handle_request(self, h: BaseHTTPRequestHandler) -> int:
        method = h.command
        parsed = urllib.parse.urlsplit(h.path)
        path = urllib.parse.unquote(parsed.path) or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/") or "/"
        if self.cors and method == "OPTIONS":
            resp = make_response(204, b"", self.cors_headers())
            return self.send(h, resp, method)
        route, params = self.match(method, path)
        if route is None:
            target, code = self.static_file(path) if method in ("GET", "HEAD") else (None, None)
            if target is not None:
                return self.send(h, file_response(target, None), method)
            if code == 403:
                return self.send(h, json_error(403, "That path isn't allowed."), method)
            if code == 400:
                return self.send(h, json_error(400, "Bad path."), method)
            if params:  # the path exists for other methods
                allowed = ", ".join(sorted(params))
                resp = json_error(405, f"{method} isn't allowed for {path}; try {allowed}.")
                resp["headers"]["Allow"] = allowed
                return self.send(h, resp, method)
            return self.send(h, json_error(404, f"No route for {method} {path}."), method)
        methods, pattern, regex, fn, kind = route
        if kind == "ws":
            if h.headers.get("Upgrade", "").lower() != "websocket":
                return self.send(h, json_error(426, "This path speaks WebSocket; connect with a WebSocket client."), method)
            return self.websocket(h, fn, path, params, parsed)
        body, problem = self.read_body(h)
        if problem is not None:
            return self.send(h, problem, method)
        req = self.request_map(h, method, path, params, parsed, body)
        result, err = guarded_call(self.api, fn, [req])
        if err is not None:
            self.errors.append(err)
            message = err.message
            if self.log:
                self.api.output(f"Error in the handler for {method} {pattern}: {message}" +
                                (f" (line {err.line})" if err.line else ""))
            return self.send(h, json_error(500, message, line=err.line), method)
        try:
            resp = to_response(result)
        except _tb.TBError as err:
            return self.send(h, json_error(500, err.message), method)
        return self.send(h, resp, method)

    def read_body(self, h):
        length = h.headers.get("Content-Length")
        if h.headers.get("Transfer-Encoding", "").lower() == "chunked":
            return b"", json_error(411, "Send a Content-Length; chunked uploads aren't supported.")
        if not length:
            return b"", None
        try:
            size = int(length)
        except ValueError:
            return b"", json_error(400, "Bad Content-Length.")
        if size > self.max_body:
            return b"", json_error(413, f"The request body is too big (over {self.max_body // 1024 // 1024} MB).")
        data = b""
        while len(data) < size:
            chunk = h.rfile.read(min(65536, size - len(data)))
            if not chunk:
                break
            data += chunk
        return data, None

    def request_map(self, h, method, path, params, parsed, body: bytes) -> dict:
        headers = {k.lower(): v for k, v in h.headers.items()}
        query = {}
        query_all = {}
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            query[key] = value
            query_all.setdefault(key, []).append(value)
        text = body.decode("utf-8", errors="replace")
        ctype = headers.get("content-type", "")
        json_value = None
        form = {}
        if body:
            if "json" in ctype or text[:1] in ("{", "["):
                try:
                    json_value = _pyjson.loads(text)
                    from tblib.json import from_json
                    json_value = from_json(json_value)
                except (ValueError, ImportError, AttributeError):
                    json_value = None
            if "x-www-form-urlencoded" in ctype:
                for key, value in urllib.parse.parse_qsl(text, keep_blank_values=True):
                    form[key] = value
        cookies = {}
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                cookies[name] = value
        return {
            "method": method, "path": path, "params": params, "query": query, "query_all": query_all,
            "headers": headers, "body": text, "json": json_value, "form": form, "cookies": cookies,
            "ip": h.client_address[0], "url": h.path, "content_type": ctype,
        }

    def cors_headers(self):
        origin = self.cors if isinstance(self.cors, str) else "*"
        return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": ", ".join(METHODS),
                "Access-Control-Allow-Headers": "Content-Type, Authorization"}

    def send(self, h, resp: dict, method: str) -> int:
        status = int(resp.get("status", 200))
        body = resp.get("body", b"")
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif body is None:
            body = b""
        headers = {str(k): str(v) for k, v in (resp.get("headers") or {}).items()}
        lower = {k.lower() for k in headers}
        if "content-type" not in lower and body:
            headers["Content-Type"] = "text/plain; charset=utf-8"
        if self.cors:
            headers.update(self.cors_headers())
        headers.setdefault("Cache-Control", "no-store")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        h.send_response(status)
        for key, value in headers.items():
            h.send_header(key, value)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if method != "HEAD" and status not in (204, 304):
            h.wfile.write(body)
        return status

    # -- websocket ----------------------------------------------------------------

    def websocket(self, h, fn, path, params, parsed) -> int:
        from tblib import websocket as _ws
        key = h.headers.get("Sec-WebSocket-Key")
        if not key or h.headers.get("Sec-WebSocket-Version") != "13":
            return self.send(h, json_error(400, "Bad WebSocket handshake."), "GET")
        accept = base64.b64encode(hashlib.sha1((key + _ws.GUID).encode()).digest()).decode()
        h.send_response(101)
        h.send_header("Upgrade", "websocket")
        h.send_header("Connection", "Upgrade")
        h.send_header("Sec-WebSocket-Accept", accept)
        h.end_headers()
        h.wfile.flush()
        h.close_connection = True
        conn = _ws.Connection(h.connection, mask=False, url=h.path)
        req = self.request_map(h, "GET", path, params, parsed, b"")
        handle = _ws.make_handle(self.api, conn, owner="ws")
        handle["path"] = path
        handle["ip"] = h.client_address[0]
        if self.log:
            self.api.output(f"{h.client_address[0]} WS {h.path} open")
        try:
            # Long-lived: runs without the shared callback lock so other requests keep flowing.
            self.api.call(fn, [handle, req])
        except _tb.TBError as err:
            self.errors.append(err)
            if self.log:
                self.api.output(f"Error in the WebSocket handler for {path}: {err.message}")
            try:
                conn.close(1011, "server error")
            except Exception:
                pass
        except Exception as err:
            try:
                conn.close(1011, "server error")
            except Exception:
                pass
        finally:
            if conn.open:
                try:
                    conn.close(1000, "")
                except Exception:
                    pass
            if self.log:
                self.api.output(f"{h.client_address[0]} WS {h.path} closed")
        return 101


def json_error(status: int, message: str, **extra) -> dict:
    payload = {"error": message, "status": status}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return make_response(status, _pyjson.dumps(payload), {"Content-Type": "application/json; charset=utf-8"})


def file_response(path: str, download: str | None, content_type: str | None = None) -> dict:
    try:
        size = os.path.getsize(path)
        if size > MAX_STATIC:
            return json_error(413, "That file is too big to serve.")
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return json_error(404, "File not found.")
    ctype = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if ctype.startswith("text/") and "charset" not in ctype:
        ctype += "; charset=utf-8"
    headers = {"Content-Type": ctype}
    if download:
        safe = download.replace('"', "").replace("\r", "").replace("\n", "")
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    return make_response(200, data, headers)


def load(api):
    m = Module(
        "server",
        "A small web server for local tools and dashboards. app = server.create(); app.get(\"/items/{id}\", "
        "fn(req) { return server.json({...}) }); app.listen(8000). Handlers get a req map (method, path, "
        "params, query, headers, body, json, form, cookies, ip) and give back text, a map/list (sent as JSON) "
        "or a response from server.json/text/html/file/redirect. Safe defaults: listens on 127.0.0.1 only, "
        "bodies up to 10 MB, static files can't escape their folder, handler errors become a 500 JSON answer. "
        "app.ws(path, fn) serves WebSockets.",
    )

    def method(obj, owner, name, lo, hi, func, doc):
        obj[name] = _tb.Builtin(f"{owner}.{name}", func, (lo, hi), doc)

    def status_arg(line, status, fn):
        status = check_type(line, status, "whole", f"server.{fn}", "status")
        if not 100 <= status <= 599:
            fail(line, f"server.{fn}() needs an HTTP status from 100 to 599, not {status}.")
        return status

    def headers_arg(line, headers, fn):
        if headers is None:
            return {}
        check_type(line, headers, "map", f"server.{fn}", "headers")
        out = {}
        for k, v in headers.items():
            key, value = str(k), _tb.to_text(v)
            if any(ch in key + value for ch in "\r\n"):
                fail(line, f"server.{fn}() header names and values can't contain line breaks.")
            out[key] = value
        return out

    def path_pattern(line, path, fn):
        check_type(line, path, "text", f"app.{fn}", "path")
        if not path.startswith("/"):
            fail(line, f'app.{fn}() paths start with "/", like "/items/{{id}}", not "{path}".')
        try:
            compile_path(path)
        except re.error:
            fail(line, f'app.{fn}() can\'t use the path "{path}".')
        return path

    # -- responses ----------------------------------------------------------------

    @m.fn("json", 1, 3, doc=(
        "json(data, status?, headers?) -> response\n"
        "A JSON answer (Content-Type application/json), status 200 unless given."))
    def json_(line, data, status=200, headers=None):
        from tblib import json as _json
        status = status_arg(line, status, "json")
        hdrs = {"Content-Type": "application/json; charset=utf-8"}
        hdrs.update(headers_arg(line, headers, "json"))
        return make_response(status, _json.dump(data, line), hdrs)

    @m.fn("text", 1, 3, doc="text(body, status?, headers?) -> response\nA plain-text answer.")
    def text_(line, body, status=200, headers=None):
        status = status_arg(line, status, "text")
        hdrs = {"Content-Type": "text/plain; charset=utf-8"}
        hdrs.update(headers_arg(line, headers, "text"))
        return make_response(status, body if isinstance(body, str) else _tb.to_text(body), hdrs)

    @m.fn("html", 1, 3, doc="html(body, status?, headers?) -> response\nAn HTML page answer (the template library makes good bodies).")
    def html_(line, body, status=200, headers=None):
        status = status_arg(line, status, "html")
        hdrs = {"Content-Type": "text/html; charset=utf-8"}
        hdrs.update(headers_arg(line, headers, "html"))
        return make_response(status, body if isinstance(body, str) else _tb.to_text(body), hdrs)

    @m.fn("file", 1, 2, doc=(
        "file(path, options?) -> response\n"
        "Send a file from disk, with its type guessed from the name. A missing file gives a 404 answer.\n"
        "\n"
        "Options:\n"
        "- download: a file name like \"name.csv\". It makes browsers save the file.\n"
        "- type: the type to send, like \"text/csv\"."))
    def file_(line, path, options=None):
        check_type(line, path, "text", "server.file", "path")
        download = None
        ctype = None
        if options is not None:
            check_type(line, options, "map", "server.file", "options")
            for key in options:
                if key not in ("download", "type"):
                    fail(line, f'server.file() doesn\'t know the option "{key}". Options: download, type.')
            if options.get("download") is not None:
                download = check_type(line, options["download"], "text", "server.file", '"download"')
            if options.get("type") is not None:
                ctype = check_type(line, options["type"], "text", "server.file", '"type"')
        full = os.path.expanduser(path)
        if not os.path.isfile(full):
            return json_error(404, f"File not found: {os.path.basename(path)}.")
        return file_response(full, download, ctype)

    @m.fn("redirect", 1, 2, doc="redirect(url, status?) -> response\nSend the browser somewhere else (status 302 unless given, 301 for permanent).")
    def redirect(line, url, status=302):
        check_type(line, url, "text", "server.redirect", "url")
        status = status_arg(line, status, "redirect")
        if any(ch in url for ch in "\r\n"):
            fail(line, "server.redirect() URLs can't contain line breaks.")
        return make_response(status, b"", {"Location": url})

    @m.fn("response", 1, 3, doc=(
        "response(status, body?, headers?) -> response\n"
        "Any answer you like: server.response(201, \"created\", {\"Location\": \"/items/7\"})."))
    def response_(line, status, body="", headers=None):
        status = status_arg(line, status, "response")
        return make_response(status, body if isinstance(body, (str, bytes, bytearray)) else _tb.to_text(body),
                             headers_arg(line, headers, "response"))

    @m.fn("error", 2, doc="error(status, message) -> response\nA JSON error answer: {\"error\": message, \"status\": status}.")
    def error_(line, status, message):
        status = status_arg(line, status, "error")
        return json_error(status, message if isinstance(message, str) else _tb.to_text(message))

    # -- the app --------------------------------------------------------------------

    @m.fn("create", 0, 1, doc=(
        "create(options?) -> app\n"
        "Make a web app.\n"
        "\n"
        "Options:\n"
        "- log: print one line per request (default: true).\n"
        "- max_body: the biggest request body, in bytes (default: 10 MB).\n"
        "- cors: true or an origin. It lets browser pages from elsewhere call the API.\n"
        "- name: a name for the app (text).\n"
        "\n"
        "The app has:\n"
        "- get, post, put, patch, delete, route(path, fn): add a handler for a path.\n"
        "- static(prefix, folder): serve the files in a folder.\n"
        "- ws(path, fn): serve WebSockets.\n"
        "- listen(port?, host?): run the server until Ctrl+C.\n"
        "- start(port?, host?): run the server in the background. stop() stops it.\n"
        "- .port, .url, .running: where it runs and whether it is on.\n"
        "\n"
        "Example:\n"
        "    let app = server.create()\n"
        "    app.get(\"/hello\", fn(req) { return \"Hi!\" })\n"
        "    app.listen(8000)"))
    def create(line, options=None):
        api.need_desktop(line, "The server library")
        o = {}
        if options is not None:
            check_type(line, options, "map", "server.create", "options")
            for key in options:
                if key not in ("log", "max_body", "cors", "name"):
                    fail(line, f'server.create() doesn\'t know the option "{key}". Options: log, max_body, cors, name.')
            if options.get("log") is not None:
                o["log"] = check_type(line, options["log"], "bool", "server.create", '"log"')
            if options.get("max_body") is not None:
                o["max_body"] = check_type(line, options["max_body"], "whole", "server.create", '"max_body"')
                if not 0 < o["max_body"] <= 1024 ** 3:
                    fail(line, "server.create() needs max_body between 1 byte and 1 GB.")
            if options.get("cors") is not None:
                o["cors"] = check_type(line, options["cors"], "bool|text", "server.create", '"cors"')
            if options.get("name") is not None:
                o["name"] = check_type(line, options["name"], "text", "server.create", '"name"')
        app = App(api, o)
        handle = {"port": None, "host": None, "url": None, "running": False}
        app.handle = handle

        def add_route(fn_name, methods):
            def register(line, path, fn):
                path = path_pattern(line, path, fn_name)
                check_type(line, fn, "function", f"app.{fn_name}", "fn")
                app.add(methods, path, fn)
                return None
            return register

        def route(line, methods, path, fn):
            if isinstance(methods, str):
                methods = [methods]
            check_type(line, methods, "list", "app.route", "methods")
            wanted = set()
            for mth in methods:
                check_type(line, mth, "text", "app.route", "each method")
                if mth.upper() not in METHODS:
                    fail(line, f'app.route() doesn\'t know the method "{mth}". Methods: {", ".join(METHODS)}.')
                wanted.add(mth.upper())
            path = path_pattern(line, path, "route")
            check_type(line, fn, "function", "app.route", "fn")
            app.add(wanted or None, path, fn)
            return None

        def static(line, prefix, folder):
            check_type(line, prefix, "text", "app.static", "prefix")
            check_type(line, folder, "text", "app.static", "folder")
            if not prefix.startswith("/"):
                fail(line, f'app.static() prefixes start with "/", like "/assets", not "{prefix}".')
            full = os.path.expanduser(folder)
            if not os.path.isdir(full):
                fail(line, f"app.static(): the folder '{folder}' doesn't exist.", path=folder)
            app.statics.append((prefix.rstrip("/") or "/", full))
            return None

        def ws(line, path, fn):
            path = path_pattern(line, path, "ws")
            check_type(line, fn, "function", "app.ws", "fn")
            app.add(None, path, fn, kind="ws")
            return None

        def port_host(line, port, host, fn):
            if port is None:
                port = DEFAULT_PORT
            port = check_type(line, port, "whole", f"app.{fn}", "port")
            if not 0 <= port <= 65535:
                fail(line, f"app.{fn}() needs a port from 0 to 65535 (0 picks a free one), not {port}.")
            if host is None:
                host = DEFAULT_HOST
            check_type(line, host, "text", f"app.{fn}", "host")
            host = host.strip() or DEFAULT_HOST
            if host == "localhost":
                host = DEFAULT_HOST
            return port, host

        def started(line, port, host, fn):
            if app.server is not None:
                fail(line, f"app.{fn}(): this app is already running on {app.url}.")
            if not app.routes and not app.statics:
                fail(line, f"app.{fn}(): add a route first, like app.get(\"/\", fn(req) {{ return \"hi\" }}).")
            app.bind(line, host, port)
            handle.update({"port": app.port, "host": app.host, "url": app.url, "running": True})

        def listen(line, port=None, host=None):
            port, host = port_host(line, port, host, "listen")
            started(line, port, host, "listen")
            if app.log:
                api.output(f"{app.name} listening on {app.url} (Ctrl+C to stop)")
            app.thread = threading.current_thread()
            try:
                app.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                try:
                    app.shutdown()
                except Exception:
                    pass
                handle["running"] = False
            if app.log:
                api.output(f"{app.name} stopped.")
            return None

        def start(line, port=None, host=None):
            port, host = port_host(line, port, host, "start")
            started(line, port, host, "start")
            app.thread = threading.Thread(target=app.serve_forever, name=f"tb-server-{app.port}", daemon=True)
            app.thread.start()
            return app.url

        def stop(line):
            app.shutdown()
            handle["running"] = False
            return None

        def wait(line, seconds=None):
            if seconds is not None:
                check_type(line, seconds, "number", "app.wait", "seconds")
            if app.thread is None:
                return True
            try:
                app.thread.join(seconds)
            except KeyboardInterrupt:
                app.shutdown()
            return not app.thread.is_alive()

        def routes(line):
            out = []
            for methods, pattern, regex, fn, kind in app.routes:
                out.append({"methods": sorted(methods) if methods else ["*"], "path": pattern, "kind": kind})
            for prefix, folder in app.statics:
                out.append({"methods": ["GET"], "path": prefix + "/...", "kind": "static", "folder": folder})
            return out

        for name, mths, doc in [
            ("get", {"GET"}, "get(path, fn)\nAnswer GET requests for a path. {name} parts become req.params: "
                              "app.get(\"/items/{id}\", fn(req) { return req.params.id })."),
            ("post", {"POST"}, "post(path, fn)\nAnswer POST requests (req.json holds a JSON body, req.form a form)."),
            ("put", {"PUT"}, "put(path, fn)\nAnswer PUT requests."),
            ("patch", {"PATCH"}, "patch(path, fn)\nAnswer PATCH requests."),
            ("delete", {"DELETE"}, "delete(path, fn)\nAnswer DELETE requests."),
        ]:
            method(handle, "app", name, 2, 2, add_route(name, mths), doc)
        method(handle, "app", "route", 3, 3, route, "route(methods, path, fn)\nAnswer several methods at once: "
                                                     "app.route([\"GET\", \"POST\"], \"/form\", fn).")
        method(handle, "app", "static", 2, 2, static, "static(prefix, folder)\nServe the files in a folder under a URL "
                                                       "prefix: app.static(\"/assets\", \"public\"). index.html is "
                                                       "served for folders; paths can't escape the folder.")
        method(handle, "app", "ws", 2, 2, ws, "ws(path, fn)\nServe WebSockets on a path: fn(ws, req) runs for each "
                                               "connection, with ws.send/receive/each/close.")
        method(handle, "app", "listen", 0, 2, listen, "listen(port?, host?)\nRun the server until Ctrl+C (blocking). "
                                                       "port defaults to 8000 (0 picks a free one); host to 127.0.0.1 "
                                                       "(this computer only; \"0.0.0.0\" opens it to the network).")
        method(handle, "app", "start", 0, 2, start, "start(port?, host?) -> text\nRun the server in the background and "
                                                     "give its URL; the program goes on. Stop with app.stop().")
        method(handle, "app", "stop", 0, 0, stop, "stop()\nStop the server and free the port.")
        method(handle, "app", "wait", 0, 1, wait, "wait(seconds?) -> bool\nBlock until a background server stops "
                                                   "(or the seconds pass). Ctrl+C stops it.")
        method(handle, "app", "routes", 0, 0, routes, "routes() -> list\nThe routes added so far.")
        return handle

    @m.fn("stop_all", 0, doc="stop_all()\nStop every running server made by this program.")
    def stop_all_(line):
        stop_all()
        return None

    @m.fn("escape", 1, doc="escape(text) -> text\nText made safe inside HTML (same as template.escape).")
    def escape(line, value):
        import html as _html
        return _html.escape(value if isinstance(value, str) else _tb.to_text(value), quote=True)

    @m.fn("parse_query", 1, doc="parse_query(text) -> map\nRead a query string: parse_query(\"a=1&b=x%20y\") is {\"a\": \"1\", \"b\": \"x y\"}.")
    def parse_query(line, text):
        check_type(line, text, "text", "server.parse_query", "text")
        return dict(urllib.parse.parse_qsl(text.lstrip("?"), keep_blank_values=True))

    m.value("max_body", MAX_BODY, doc="The default request-body limit in bytes (10 MB).")
    m.value("methods", list(METHODS), doc="The HTTP methods route() accepts.")
    return m
