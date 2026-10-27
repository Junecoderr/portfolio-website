"""tls: look at a server's TLS certificate and protocol versions (defensive checks)."""

from __future__ import annotations

import datetime as _dt
import os
import socket
import ssl
import tempfile
import warnings

from tblib._api import Module, check_type, fail
from tblib._threads import ignore_sigpipe

import tb as _tb

DEFAULT_TIMEOUT = 5.0
MAX_TIMEOUT = 60
VERSION_NAMES = ["TLSv1.0", "TLSv1.1", "TLSv1.2", "TLSv1.3"]
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # TLSv1/1.1 enums are deprecated but still worth probing
    VERSION_ENUM = {
        "TLSv1.0": getattr(ssl.TLSVersion, "TLSv1", None), "TLSv1.1": getattr(ssl.TLSVersion, "TLSv1_1", None),
        "TLSv1.2": getattr(ssl.TLSVersion, "TLSv1_2", None), "TLSv1.3": getattr(ssl.TLSVersion, "TLSv1_3", None),
    }
AUTHORISED = ("Only check servers you own or have written permission to test. "
              "Checking other people's systems can be against the law.")


class TlsProblem(Exception):
    def __init__(self, message, reason):
        super().__init__(message)
        self.message, self.reason = message, reason


def decode_der(der: bytes) -> dict:
    """The fields of a DER certificate as ssl's getpeercert() dict, even for
    an unverified certificate (via ssl's own decoder on a temp file)."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decoder is None:
        return {}
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        return decoder(path) or {}
    except Exception:
        return {}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def name_map(rdns) -> dict:
    """ssl's ((('commonName', 'x'),), ...) as a plain map with friendly keys."""
    out = {}
    keys = {"commonName": "common_name", "organizationName": "organization", "countryName": "country",
            "stateOrProvinceName": "state", "localityName": "locality", "organizationalUnitName": "unit",
            "emailAddress": "email"}
    for rdn in rdns or ():
        for key, value in rdn:
            out[keys.get(key, key)] = value
    return out


def parse_time(text) -> _dt.datetime | None:
    if not text:
        return None
    try:
        return _dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(text), _dt.timezone.utc)
    except (ValueError, OverflowError):
        return None


def hostname_matches(cert: dict, host: str) -> bool | None:
    """Does the certificate cover host? None when the certificate has no names."""
    names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    ips = [v for k, v in cert.get("subjectAltName", ()) if k == "IP Address"]
    if not names and not ips:
        cn = name_map(cert.get("subject")).get("common_name")
        if not cn:
            return None
        names = [cn]
    host_low = host.lower().rstrip(".")
    if host_low in [ip.lower() for ip in ips]:
        return True
    for pattern in names:
        p = pattern.lower().rstrip(".")
        if p == host_low:
            return True
        if p.startswith("*.") and host_low.count(".") == p.count(".") and host_low.endswith(p[1:]):
            return True
    return False


def fetch(host: str, port: int, timeout: float, server_name: str | None = None, ca_file: str | None = None,
          version=None) -> dict:
    """Connect once and describe the certificate. Raises TlsProblem."""
    server_name = server_name or host
    ctx = ssl.create_default_context(cafile=ca_file)
    if version is not None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ctx.maximum_version = version
    result = {"host": host, "port": port, "verified": False, "verify_error": None}

    ignore_sigpipe()

    def handshake(context):
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            tls = context.wrap_socket(sock, server_hostname=server_name)
        except BaseException:
            sock.close()
            raise
        return tls

    try:
        try:
            tls = handshake(ctx)
            result["verified"] = True
        except ssl.SSLCertVerificationError as err:
            result["verify_error"] = getattr(err, "verify_message", None) or str(err)
            loose = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            loose.check_hostname = False
            loose.verify_mode = ssl.CERT_NONE
            if version is not None:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    loose.minimum_version = loose.maximum_version = version
            tls = handshake(loose)
    except ssl.SSLError as err:
        reason = getattr(err, "reason", None) or str(err)
        if version is not None:
            raise TlsProblem(f"{host} doesn't speak that TLS version ({reason}).", "version") from None
        raise TlsProblem(f"The TLS handshake with {host}:{port} failed ({reason}). Is it really a TLS server?",
                         "tls") from None
    except socket.gaierror:
        raise TlsProblem(f"I couldn't find the host '{host}'. Check the spelling, or your internet connection.",
                         "dns") from None
    except ConnectionRefusedError:
        raise TlsProblem(f"Nothing is listening on {host} port {port}.", "refused") from None
    except (TimeoutError, socket.timeout):
        raise TlsProblem(f"{host} port {port} didn't answer within {_tb.to_text(timeout)} seconds.", "timeout") from None
    except OSError as err:
        raise TlsProblem(f"I couldn't connect to {host} port {port}: {err.strerror or err}.", "connect") from None
    try:
        der = tls.getpeercert(binary_form=True)
        info = tls.getpeercert() or {}
        result["version"] = tls.version()
        cipher = tls.cipher()
        result["cipher"] = cipher[0] if cipher else None
        result["cipher_bits"] = cipher[2] if cipher else None
    finally:
        tls.close()
    if not der:
        raise TlsProblem(f"{host} sent no certificate.", "tls")
    if not info:
        info = decode_der(der)
    result["pem"] = ssl.DER_cert_to_PEM_cert(der)
    result["subject"] = name_map(info.get("subject"))
    result["issuer"] = name_map(info.get("issuer"))
    result["serial"] = info.get("serialNumber")
    result["san"] = [v for _k, v in info.get("subjectAltName", ())]
    before, after = parse_time(info.get("notBefore")), parse_time(info.get("notAfter"))
    result["not_before"] = before.strftime("%Y-%m-%dT%H:%M:%SZ") if before else None
    result["not_after"] = after.strftime("%Y-%m-%dT%H:%M:%SZ") if after else None
    now = _dt.datetime.now(_dt.timezone.utc)
    result["days_left"] = int((after - now).total_seconds() // 86400) if after else None
    result["expired"] = bool(after and after < now)
    result["not_yet_valid"] = bool(before and before > now)
    result["self_signed"] = bool(info and info.get("subject") == info.get("issuer"))
    result["hostname_matches"] = hostname_matches(info, server_name) if info else None
    result["parsed"] = parse_with_crypto(result["pem"])
    return result


def parse_with_crypto(pem: str):
    """The crypto library's full X.509 parse (soc-intel's x509_parse_bytes),
    or None when that library or helper isn't there."""
    try:
        from tblib import crypto as _crypto
        parser = getattr(_crypto, "x509_parse_bytes", None)
        if parser is None:
            return None
        parsed = parser(ssl.PEM_cert_to_DER_cert(pem.strip()))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_ip(text: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def load(api):
    m = Module(
        "tls",
        "Look at the TLS (https) side of a server: tls.certificate(host) fetches and describes its "
        "certificate (subject, issuer, dates, names, whether it is trusted), tls.versions(host) tries each "
        "TLS version, and tls.check(host) gives a verdict (expiry, hostname match, trust). "
        "No data is sent beyond the handshake. " + AUTHORISED,
    )

    def host_arg(line, host, fn):
        check_type(line, host, "text", f"tls.{fn}", "host")
        text = host.strip()
        if text.startswith("https://"):
            text = text[8:].split("/", 1)[0]
        if text.startswith("[") and "]" in text:
            text = text[1:text.index("]")]
        elif text.count(":") == 1:
            text = text.split(":", 1)[0]
        if not text:
            fail(line, f"tls.{fn}() needs a host name, but got empty text.")
        return text

    def port_from(host, port):
        text = host.strip()
        if text.startswith("https://"):
            text = text[8:].split("/", 1)[0]
        if not text.startswith("[") and text.count(":") == 1 and text.split(":")[1].isdigit() and port is None:
            return int(text.split(":")[1])
        return port

    def port_arg(line, port, fn):
        if port is None:
            return 443
        port = check_type(line, port, "whole", f"tls.{fn}", "port")
        if not 1 <= port <= 65535:
            fail(line, f"tls.{fn}() needs a port from 1 to 65535, not {port}.")
        return port

    def options_arg(line, options, fn, extra=()):
        if options is None:
            return {}
        check_type(line, options, "map", f"tls.{fn}", "options")
        allowed = ("timeout", "server_name", "ca_file") + tuple(extra)
        for key in options:
            if key not in allowed:
                fail(line, f'tls.{fn}() doesn\'t know the option "{key}". Options: {", ".join(allowed)}.')
        out = {}
        if options.get("timeout") is not None:
            t = check_type(line, options["timeout"], "number", f"tls.{fn}", '"timeout"')
            if not 0 < t <= MAX_TIMEOUT:
                fail(line, f"tls.{fn}() needs a timeout between 0 and {MAX_TIMEOUT} seconds.")
            out["timeout"] = float(t)
        if options.get("server_name") is not None:
            out["server_name"] = check_type(line, options["server_name"], "text", f"tls.{fn}", '"server_name"')
        if options.get("ca_file") is not None:
            ca = check_type(line, options["ca_file"], "text", f"tls.{fn}", '"ca_file"')
            if not os.path.isfile(os.path.expanduser(ca)):
                fail(line, f"I couldn't find the CA file '{ca}'.", path=ca)
            out["ca_file"] = os.path.expanduser(ca)
        for key in extra:
            if options.get(key) is not None:
                out[key] = options[key]
        return out

    def run_fetch(line, host, port, o, version=None):
        try:
            return fetch(host, port, o.get("timeout", DEFAULT_TIMEOUT), o.get("server_name"), o.get("ca_file"),
                         version)
        except TlsProblem as err:
            fail(line, err.message, reason=err.reason, host=host, port=port)

    def strip_internal(result: dict) -> dict:
        return {k: v for k, v in result.items()}

    @m.fn("certificate", 1, 3, doc=(
        "certificate(host, port?, options?) -> map\n"
        "Fetch a server's certificate and describe it.\n"
        "host may also be \"host:port\" or an https:// URL.\n"
        "\n"
        "Returns a map with:\n"
        "- \"pem\": the certificate as PEM text.\n"
        "- \"subject\", \"issuer\": maps with common_name, organization...\n"
        "- \"not_before\", \"not_after\": when it starts and stops being valid.\n"
        "- \"days_left\", \"expired\": how long it has left.\n"
        "- \"san\": the names it covers.\n"
        "- \"serial\": the serial number.\n"
        "- \"verified\": true if this computer trusts it. \"verify_error\" says why not.\n"
        "- \"self_signed\", \"hostname_matches\": true or false.\n"
        "- \"version\": the TLS version used, like \"TLSv1.3\".\n"
        "- \"cipher\": the cipher used.\n"
        "- \"parsed\": the crypto library's full x509 parse, when available.\n"
        "\n"
        "Options:\n"
        "- timeout: seconds to wait (default: 5).\n"
        "- server_name: the name to ask for (SNI).\n"
        "- ca_file: a PEM file of certificates to trust.\n"
        "\n"
        + AUTHORISED))
    def certificate(line, host, port=None, options=None):
        api.need_desktop(line, "The tls library")
        port = port_from(host if isinstance(host, str) else "", port)
        h = host_arg(line, host, "certificate")
        p = port_arg(line, port, "certificate")
        o = options_arg(line, options, "certificate")
        return strip_internal(run_fetch(line, h, p, o))

    @m.fn("versions", 1, 3, doc=(
        "versions(host, port?, options?) -> map\n"
        "Which TLS versions the server accepts.\n"
        "\n"
        "Returns a map of version name -> answer, like\n"
        "{\"TLSv1.0\": false, \"TLSv1.1\": false, \"TLSv1.2\": true, \"TLSv1.3\": true}.\n"
        "- true: the server accepts it.\n"
        "- false: the server refuses it.\n"
        "- nil: this computer's OpenSSL can't even try it.\n"
        "\n"
        "Old versions (1.0, 1.1) being on is a finding.\n"
        "\n"
        + AUTHORISED))
    def versions(line, host, port=None, options=None):
        api.need_desktop(line, "The tls library")
        port = port_from(host if isinstance(host, str) else "", port)
        h = host_arg(line, host, "versions")
        p = port_arg(line, port, "versions")
        o = options_arg(line, options, "versions")
        out = {}
        for name in VERSION_NAMES:
            enum = VERSION_ENUM[name]
            if enum is None:
                out[name] = None
                continue
            try:
                info = fetch(h, p, o.get("timeout", DEFAULT_TIMEOUT), o.get("server_name"), o.get("ca_file"), enum)
                out[name] = info.get("version") == name.replace("TLSv1.0", "TLSv1")
            except TlsProblem as err:
                if err.reason == "version":
                    out[name] = False
                elif err.reason == "tls":
                    out[name] = False
                else:
                    fail(line, err.message, reason=err.reason, host=h, port=p)
            except ValueError:  # the version isn't allowed by this OpenSSL build
                out[name] = None
        return out

    @m.fn("check", 1, 3, doc=(
        "check(host, port?, options?) -> map\n"
        "A verdict on a server's TLS.\n"
        "\n"
        "Returns a map with:\n"
        "- \"ok\": true if no issues were found.\n"
        "- \"issues\": a list of texts, one per problem.\n"
        "- \"host\", \"port\": the server checked.\n"
        "- \"days_left\", \"expires\": when the certificate runs out.\n"
        "- \"verified\", \"hostname_matches\", \"self_signed\": true or false.\n"
        "- \"version\", \"cipher\": what the connection used.\n"
        "- \"issuer\", \"subject\": who the certificate is from and for.\n"
        "\n"
        "Issues it looks for:\n"
        "- The certificate is not trusted.\n"
        "- The name does not match.\n"
        "- It has expired, or expires within warn_days.\n"
        "- An old TLS version was used.\n"
        "\n"
        "Options (as for certificate(), plus):\n"
        "- warn_days: warn when fewer days than this are left (default: 30).\n"
        "\n"
        + AUTHORISED))
    def check(line, host, port=None, options=None):
        api.need_desktop(line, "The tls library")
        port = port_from(host if isinstance(host, str) else "", port)
        h = host_arg(line, host, "check")
        p = port_arg(line, port, "check")
        o = options_arg(line, options, "check", extra=("warn_days",))
        warn = 30
        if o.get("warn_days") is not None:
            warn = check_type(line, o["warn_days"], "whole", "tls.check", '"warn_days"')
        info = run_fetch(line, h, p, o)
        issues = []
        if not info["verified"]:
            why = info.get("verify_error") or "unknown reason"
            issues.append(f"The certificate isn't trusted by this computer ({why}).")
        if info.get("hostname_matches") is False:
            names = ", ".join(info.get("san") or []) or info["subject"].get("common_name", "?")
            issues.append(f"The certificate isn't for {o.get('server_name') or h}; it covers: {names}.")
        if info.get("expired"):
            issues.append(f"The certificate expired on {info['not_after']}.")
        elif info.get("days_left") is not None and info["days_left"] < warn:
            issues.append(f"The certificate expires in {info['days_left']} day(s), on {info['not_after']}.")
        if info.get("not_yet_valid"):
            issues.append(f"The certificate isn't valid until {info['not_before']}.")
        if info.get("version") in ("TLSv1", "TLSv1.1", "SSLv3"):
            issues.append(f"The connection used an old protocol ({info['version']}); TLS 1.2 or 1.3 is expected.")
        return {
            "ok": not issues, "issues": issues, "host": h, "port": p,
            "days_left": info.get("days_left"), "expires": info.get("not_after"),
            "verified": info["verified"], "hostname_matches": info.get("hostname_matches"),
            "self_signed": info.get("self_signed"), "version": info.get("version"), "cipher": info.get("cipher"),
            "issuer": info["issuer"], "subject": info["subject"],
        }

    @m.fn("parse_pem", 1, doc=(
        "parse_pem(pem) -> map\n"
        "Describe a certificate you already have as PEM text.\n"
        "\n"
        "Returns a map with \"subject\", \"issuer\", \"not_before\", \"not_after\", \"days_left\",\n"
        "\"expired\", \"san\", \"serial\" and \"self_signed\", as for certificate()."))
    def parse_pem(line, pem):
        check_type(line, pem, "text", "tls.parse_pem", "pem")
        try:
            der = ssl.PEM_cert_to_DER_cert(pem.strip())
        except ValueError:
            fail(line, "tls.parse_pem() needs PEM text starting with -----BEGIN CERTIFICATE-----.", reason="format")
        info = decode_der(der)
        if not info:
            fail(line, "I couldn't read that certificate.", reason="format")
        before, after = parse_time(info.get("notBefore")), parse_time(info.get("notAfter"))
        now = _dt.datetime.now(_dt.timezone.utc)
        return {
            "subject": name_map(info.get("subject")), "issuer": name_map(info.get("issuer")),
            "not_before": before.strftime("%Y-%m-%dT%H:%M:%SZ") if before else None,
            "not_after": after.strftime("%Y-%m-%dT%H:%M:%SZ") if after else None,
            "days_left": int((after - now).total_seconds() // 86400) if after else None,
            "expired": bool(after and after < now),
            "san": [v for _k, v in info.get("subjectAltName", ())],
            "serial": info.get("serialNumber"),
            "self_signed": info.get("subject") == info.get("issuer"),
            "parsed": parse_with_crypto(pem),
        }

    m.value("versions_known", list(VERSION_NAMES), doc="The TLS version names versions() reports.")
    return m
