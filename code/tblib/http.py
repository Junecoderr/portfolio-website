"""http: talk to websites and web APIs (GET, POST, downloads)."""

from __future__ import annotations

import json as _json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

USER_AGENT = "TB/3.0"
DEFAULT_TIMEOUT = 15            # seconds
MAX_TIMEOUT = 300               # seconds
MAX_TEXT = 10_000_000           # bytes of a response body kept in "text"
MAX_DOWNLOAD = 100_000_000      # default size cap for download()
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

MAC_CERT_HINT = (
    " On a Mac with Python from python.org, run 'Install Certificates.command' "
    "(in the Python folder under Applications) once, then try again."
)


class HttpProblem(Exception):
    """A friendly description of a failed request: message + reason code."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason


def ssl_context() -> ssl.SSLContext:
    """The default context: verifies certificates with the system's CAs."""
    return ssl.create_default_context()


def describe_error(err: BaseException, url: str) -> HttpProblem:
    """Turn what urllib raised into a friendly HttpProblem. Other tblib
    modules (intel) reuse this. Never includes request headers (API keys)."""
    host = urllib.parse.urlsplit(url).hostname or url
    reason = err.reason if isinstance(err, urllib.error.URLError) else err
    if isinstance(reason, ssl.SSLCertVerificationError) or (
            isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)):
        detail = getattr(reason, "verify_message", "") or "certificate verify failed"
        hint = MAC_CERT_HINT if "local issuer" in str(reason) or "local issuer" in detail else ""
        return HttpProblem(f"The secure (TLS) connection to {host} failed: {detail}. "
                           f"The site's certificate couldn't be trusted.{hint}", "tls")
    if isinstance(reason, ssl.SSLError):
        return HttpProblem(f"The secure (TLS) connection to {host} failed ({reason.reason or reason}). "
                           "Is it really an https:// site?", "tls")
    if isinstance(reason, socket.gaierror):
        return HttpProblem(f"I couldn't find the website '{host}'. Check the address, "
                           "or your internet connection.", "dns")
    if isinstance(reason, ConnectionRefusedError):
        return HttpProblem(f"{host} refused the connection. Nothing is listening there "
                           "(is the server running?).", "refused")
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason):
        return HttpProblem(f"{host} didn't answer in time.", "timeout")
    if isinstance(reason, ConnectionResetError):
        return HttpProblem(f"{host} closed the connection unexpectedly.", "connection")
    if isinstance(reason, OSError):
        why = reason.strerror or str(reason)
        return HttpProblem(f"I couldn't connect to {host}: {why}.", "connection")
    if isinstance(err, urllib.error.URLError):
        return HttpProblem(f"I couldn't reach {host}: {reason}.", "connection")
    return HttpProblem(f"The request to {host} failed: {err}.", "connection")


def check_url(line, url, fn) -> str:
    check_type(line, url, "text", fn, "url")
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        fail(line, f"{fn}() needs a web address starting with http:// or https://, but got '{url}'.",
             reason="invalid_url")
    return url


def open_url(request: urllib.request.Request, timeout: float):
    """urlopen with TB's TLS settings (tests can swap this out)."""
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())


def _charset(headers: Message) -> str:
    charset = headers.get_content_charset() if headers is not None else None
    try:
        "".encode(charset or "utf-8")
        return charset or "utf-8"
    except LookupError:
        return "utf-8"


def _headers_map(headers) -> dict:
    result: dict = {}
    if headers is None:
        return result
    for name, value in headers.items():
        key = name.lower()
        result[key] = f"{result[key]}, {value}" if key in result else value
    return result


def _parse_json(text: str):
    from tblib.json import from_json
    try:
        return from_json(_json.loads(text))
    except (ValueError, RecursionError):
        return None


def load(api):
    m = Module(
        "http",
        "Talk to websites and web APIs: get, post, request and download. Answers come back as a map "
        "{\"status\", \"ok\", \"headers\", \"text\", \"json\", \"url\", \"elapsed\"}; a 404 or 500 is "
        "an answer too (check ok or status), while a missing site, refused connection, timeout or "
        "TLS problem is an error you can catch.",
    )

    def options_arg(line, options, fn):
        if options is None:
            return {}
        check_type(line, options, "map", fn, "options")
        known = {"headers", "params", "json", "body", "timeout", "max_bytes", "user_agent"}
        unknown = [k for k in options if k not in known]
        if unknown:
            fail(line, f"{fn}() doesn't know the option {_tb.to_text(unknown[0], nested=True)}. "
                       f"Options: {', '.join(sorted(known))}.")
        return options

    def timeout_of(line, options, fn):
        timeout = options.get("timeout", DEFAULT_TIMEOUT)
        check_type(line, timeout, "number", fn, "the timeout option")
        if not 0 < timeout <= MAX_TIMEOUT:
            fail(line, f"{fn}() needs a timeout between 0 and {MAX_TIMEOUT} seconds.")
        return float(timeout)

    def build(line, method, url, options, fn, body=None):
        """A urllib Request from TB arguments."""
        url = check_url(line, url, fn)
        params = options.get("params")
        if params is not None:
            check_type(line, params, "map", fn, "the params option")
            query = urllib.parse.urlencode({str(k): _tb.to_text(v) for k, v in params.items()})
            url += ("&" if urllib.parse.urlsplit(url).query else "?") + query
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if options.get("user_agent") is not None:
            headers["User-Agent"] = check_type(line, options["user_agent"], "text", fn, "the user_agent option")
        extra = options.get("headers")
        if extra is not None:
            check_type(line, extra, "map", fn, "the headers option")
            for k, v in extra.items():
                headers[str(k)] = _tb.to_text(v)
        data = None
        if "json" in options:
            from tblib.json import dump
            data = dump(options["json"], line).encode("utf-8")
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/json"
        elif body is None and options.get("body") is not None:
            body = options["body"]
        if body is not None and data is None:
            if isinstance(body, str):
                data = body.encode("utf-8")
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = "text/plain; charset=utf-8"
            elif isinstance(body, (dict, list)):
                from tblib.json import dump
                data = dump(body, line).encode("utf-8")
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = "application/json"
            else:
                fail(line, f"{fn}() needs body to be text, a map or a list, not {_tb.type_name(body)}.")
        try:
            return urllib.request.Request(url, data=data, headers=headers, method=method)
        except ValueError as err:
            fail(line, f"{fn}() couldn't use the address '{url}': {err}.", reason="invalid_url")

    def send(line, request, timeout, fn, max_bytes=MAX_TEXT):
        """Do the request; give back the TB response map."""
        started = time.monotonic()
        try:
            resp = open_url(request, timeout)
        except urllib.error.HTTPError as err:   # 4xx/5xx: still an answer
            resp = err
        except (urllib.error.URLError, OSError, ssl.SSLError) as err:
            problem = describe_error(err, request.full_url)
            fail(line, problem.message, reason=problem.reason, url=request.full_url,
                 cls="TimeoutError" if problem.reason == "timeout" else "NetworkError")
        except ValueError as err:
            fail(line, f"{fn}() couldn't use the address '{request.full_url}': {err}.", reason="invalid_url")
        try:
            raw = resp.read(max_bytes + 1) if request.get_method() != "HEAD" else b""
        except (OSError, ssl.SSLError) as err:
            problem = describe_error(err, request.full_url)
            fail(line, problem.message, reason=problem.reason, url=request.full_url,
                 cls="TimeoutError" if problem.reason == "timeout" else "NetworkError")
        finally:
            resp.close()
        if len(raw) > max_bytes:
            fail(line, f"The answer from {request.full_url} is bigger than {max_bytes:,} bytes. "
                       "Use http.download() for big files, or raise the max_bytes option.",
                 reason="too_big", url=request.full_url)
        elapsed = round((time.monotonic() - started) * 1000, 1)
        status = resp.status if hasattr(resp, "status") and resp.status else resp.getcode()
        text = raw.decode(_charset(resp.headers), errors="replace")
        return {
            "status": status,
            "ok": 200 <= status < 300,
            "headers": _headers_map(resp.headers),
            "text": text,
            "json": _parse_json(text) if text.strip() else None,
            "url": resp.geturl() or request.full_url,
            "elapsed": _tb.tidy_number(elapsed),
        }

    def do(line, method, url, options, fn, body=None):
        api.need_desktop(line, "The http library")
        options = options_arg(line, options, fn)
        timeout = timeout_of(line, options, fn)
        max_bytes = options.get("max_bytes", MAX_TEXT)
        check_type(line, max_bytes, "whole", fn, "the max_bytes option")
        request = build(line, method, url, options, fn, body)
        return send(line, request, timeout, fn, int(max_bytes))

    options_doc = (
        "options is a map with any of: \"headers\" (map), \"params\" (map added to the address as "
        "?key=value), \"json\" (a value sent as JSON), \"timeout\" (seconds, default 15), "
        "\"user_agent\" (default \"TB/3.0\"), \"max_bytes\" (biggest answer to accept, default 10 MB).")

    @m.fn("get", 1, 2, doc=(
        "get(url, options?) -> map\n"
        "Fetch a web page or API: let r = http.get(\"https://example.com\"), then r.status, r.ok, "
        "r.text, r.json (the parsed JSON, or nil), r.headers (names in lower case), "
        "r.elapsed (milliseconds). " + options_doc))
    def get(line, url, options=None):
        return do(line, "GET", url, options, "http.get")

    @m.fn("post", 1, 3, doc=(
        "post(url, body?, options?) -> map\n"
        "Send data to a web API. body is text (sent as plain text) or a map/list (sent as JSON). "
        "Gives back the same kind of map as get(). " + options_doc))
    def post(line, url, body=None, options=None):
        return do(line, "POST", url, options, "http.post", body)

    @m.fn("request", 2, 3, doc=(
        "request(method, url, options?) -> map\n"
        "Any kind of request: method is \"GET\", \"POST\", \"PUT\", \"PATCH\", \"DELETE\", \"HEAD\" or "
        "\"OPTIONS\". options may also have \"body\" (text, map or list). " + options_doc))
    def request(line, method, url, options=None):
        check_type(line, method, "text", "http.request", "method")
        if method.upper() not in METHODS:
            fail(line, f"http.request() doesn't know the method '{method}'. Use one of: {', '.join(METHODS)}.")
        return do(line, method.upper(), url, options, "http.request")

    @m.fn("download", 2, 3, doc=(
        "download(url, path, options?) -> map\n"
        "Save what a web address gives back into a file, and give back {\"path\", \"bytes\", \"status\"}. "
        "It fails (and saves nothing) if the answer isn't OK (like 404) or is bigger than the "
        "\"max_bytes\" option (default 100 MB). Other options as for get()."))
    def download(line, url, path, options=None):
        api.need_desktop(line, "The http library")
        fn = "http.download"
        options = options_arg(line, options, fn)
        check_type(line, path, "text", fn, "path")
        if not path:
            fail(line, f"{fn}() needs a path, but got empty text.")
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fail(line, f"'{path}' is a folder, not a file.", path=path)
        timeout = timeout_of(line, options, fn)
        max_bytes = int(check_type(line, options.get("max_bytes", MAX_DOWNLOAD), "whole", fn, "the max_bytes option"))
        req = build(line, "GET", url, options, fn)
        try:
            resp = open_url(req, timeout)
        except urllib.error.HTTPError as err:
            err.close()
            fail(line, f"The download from {req.full_url} failed: the server answered {err.code} "
                       f"{err.reason}.", reason="status", status=err.code, url=req.full_url)
        except (urllib.error.URLError, OSError, ssl.SSLError) as err:
            problem = describe_error(err, req.full_url)
            fail(line, problem.message, reason=problem.reason, url=req.full_url,
                 cls="TimeoutError" if problem.reason == "timeout" else "NetworkError")
        partial = path + ".part"
        total = 0
        try:
            with resp, open(partial, "wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise _TooBig()
                    out.write(chunk)
            os.replace(partial, path)
        except _TooBig:
            _remove(partial)
            fail(line, f"The download from {req.full_url} is bigger than {max_bytes:,} bytes, so I stopped. "
                       "Raise the max_bytes option if you trust it.", reason="too_big", url=req.full_url)
        except (urllib.error.URLError, ssl.SSLError, socket.timeout, TimeoutError, ConnectionError) as err:
            _remove(partial)
            problem = describe_error(err, req.full_url)
            fail(line, problem.message, reason=problem.reason, url=req.full_url,
                 cls="TimeoutError" if problem.reason == "timeout" else "NetworkError")
        except OSError as err:
            _remove(partial)
            fail_os(line, err, path, "write to")
        return {"path": path, "bytes": total, "status": resp.status}

    @m.fn("encode_params", 1, doc=(
        "encode_params(map) -> text\n"
        "Turn a map into URL query text: encode_params({\"q\": \"a b\", \"n\": 2}) is \"q=a+b&n=2\"."))
    def encode_params(line, params):
        check_type(line, params, "map", "http.encode_params", "map")
        return urllib.parse.urlencode({str(k): _tb.to_text(v) for k, v in params.items()})

    m.value("USER_AGENT", USER_AGENT, doc="The User-Agent header TB sends unless you choose another.")
    return m


class _TooBig(Exception):
    pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
