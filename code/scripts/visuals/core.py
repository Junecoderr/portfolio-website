"""The figure model shared by every template, and the HTML around a picture.

A Fig is one "Visualize" drop-down: the SVG drawing, a legend with a chip per
parameter (chip and drawing part share data-part="pN" and a colour), optional
sliders, the example call with its result, and a one-line note saying whether
the values are real (run through TB while building the site) or an
illustration (functions that need the desktop app or the network).
"""

from __future__ import annotations

import ast
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import runner
from .runner import literal, parse_literal
from . import svg as S

SPECS = Path(__file__).resolve().parent / "specs"
LAZY_BYTES = 5000   # bigger pictures wait in a <template> until opened


@dataclass
class Param:
    name: str
    optional: bool = False
    variadic: bool = False


def parse_params(signature: str) -> list[Param]:
    """Every parameter named in a signature, in order, without repeats.

    "pad_left(value, width, fill?) -> text" -> value, width, fill?
    "min(a, b, ...) or min(list) -> number" -> a, b, list
    """
    out: list[Param] = []
    seen = set()
    for group in re.findall(r"\(([^()]*)\)", signature.split("->")[0]):
        for raw in group.split(","):
            raw = raw.strip()
            if not raw or raw == "...":
                continue
            variadic = raw.startswith("...")
            name = raw.lstrip(".").rstrip("?").strip()
            name = re.split(r"[\s=]", name)[0]
            if not re.match(r"^[A-Za-z_]\w*$", name) or name in seen:
                continue
            seen.add(name)
            out.append(Param(name, raw.endswith("?"), variadic))
    return out


def returns_of(signature: str) -> str:
    return signature.split("->", 1)[1].strip() if "->" in signature else ""


# ----------------------------------------------------------- spec loading
_SPEC_CACHE: dict[str, dict] = {}


def load_spec(name: str) -> dict:
    """specs/<name>.py holds one literal: SPEC = {...} (read with ast, never run)."""
    if name in _SPEC_CACHE:
        return _SPEC_CACHE[name]
    path = SPECS / f"{name}.py"
    spec: dict = {}
    if path.exists():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SPEC" for t in node.targets):
                spec = ast.literal_eval(node.value)
    _SPEC_CACHE[name] = spec
    return spec


# --------------------------------------------------------- parameter roles
ROLES = {
    "path": "the file or folder it works on", "text": "the text it works on", "data": "the bytes (or text) it works on",
    "options": "a map of extra settings", "fn": "your function", "pattern": "the pattern to look for",
    "records": "log records (maps) to look through", "rules": "the rules to apply", "rule": "the rule to apply",
    "key": "the secret key", "timeout": "how long to wait at most (seconds)", "host": "the computer to talk to",
    "port": "the port number on that computer", "url": "the web address", "ip": "an IP address", "cidr": "a range like 10.0.0.0/24",
    "n": "how many", "width": "how wide the result is", "fill": "what fills the gap", "value": "the value it works on",
    "name": "a name", "format": "the layout to use", "zone": "a time zone, like Europe/Berlin", "title": "the title shown",
    "rows": "a list of maps (or lists), one per row", "columns": "which columns, in order", "limit": "stop after this many",
    "list": "a list", "items": "the items to work on", "workers": "how many run at the same time", "ms": "milliseconds",
    "seconds": "how many seconds", "offset": "the byte position to start at", "encoding": "how text becomes bytes",
    "separator": "what goes between", "sep": "what goes between", "part": "the piece to look for", "message": "the message shown",
    "default": "what you get when nothing is there", "dest": "the folder things go into", "target": "the file it writes",
    "paths": "the files and folders to include", "bundle": "a STIX bundle map", "packets": "packets from pcap.read()",
    "alert": "an alert from detect.run()", "alerts": "alerts from detect.run()", "event": "an alert or event map",
    "words": "words to search for", "id": "an id, like T1110.001", "secret": "the shared base32 secret", "hash": "the hash (hex text)",
    "password": "the password", "salt": "random salt so equal passwords hash differently", "token": "the token text",
    "payload": "the map of claims inside the token", "alg": "the signing algorithm", "iterations": "how many rounds (slower is safer)",
    "length": "how many bytes long the key is", "domain": "a domain name", "service": "the service name", "days": "how many days back",
    "columns_": "", "folder": "the folder", "from": "where it comes from", "to": "where it goes", "mode": "how to open it",
    "interval": "seconds between checks", "watcher": "the watcher fs.watch() gave", "field": "which field of each record",
    "max_count": "the most times a value may show up", "attempts": "how many failures count as an attack",
    "within": "the time window, in seconds", "ports": "the ports to try", "min_ports": "how many ports make a scan",
    "backend": "which SIEM query language", "map": "a map", "tasks": "tasks from parallel.run()", "cases": "the test cases",
    "kind": "the kind of error expected", "actual": "what your code gave", "expected": "what it should give",
    "tolerance": "how far apart still counts as equal", "reason": "why", "target_": "", "setup": "makes the fixture value",
    "teardown": "cleans up afterwards", "status": "the HTTP status code", "body": "what is sent back", "headers": "extra HTTP headers",
    "method": "GET, POST, PUT...", "low": "the smallest it can be", "high": "the biggest it can be", "x": "the number",
    "y": "the second number", "base": "the base of the logarithm", "digits": "digits after the dot", "a": "the first value",
    "b": "the second value", "places": "digits after the dot", "parts": "year, month, day...", "timestamp": "seconds since 1970",
    "count": "how many", "start": "where it starts", "end": "where it stops (not included)", "step": "the gap between numbers",
    "values": "the values", "file": "the file", "node": "an XML node map", "tag": "the element name", "attrs": "attributes map",
    "children": "nodes inside", "indent": "spaces per level", "pretty": "true for indented output", "content": "the content",
    "label": "the label", "callback": "your function", "on_done": "runs with the result", "on_error": "runs with the error message",
    "size": "how many it holds", "args": "the inputs", "counter": "the counter value", "digits_": "", "time": "the moment (a timestamp)",
    "code": "the code to check", "pem": "PEM text (-----BEGIN ...)", "signature": "the signature to check",
    "public_key": "the PEM public key", "aad": "extra text that must match when decrypting", "iv": "the starting block (random if left out)",
    "r": "block size factor", "p": "parallel factor", "base64": "base64 text", "hex": "hex text", "number": "the number",
    "numbers": "numbers from 0 to 255", "results": "the results", "workspace_id": "the Log Analytics workspace id",
    "auth": "user and password, or an API key", "proto": "udp or tcp", "text_or_lines": "one line or a list of lines",
    "message_or_event": "a text message or an alert map", "text_or_path": "rule text or a .yml file", "path_or_info": "a file path or a parse() map",
    "path_or_records": "a file path or records", "pem_or_der": "the certificate (PEM, or DER as base64/hex)",
    "filter": "only names containing this", "all": "true to include sub-techniques", "platform": "only this platform",
    "min": "the shortest string to keep", "strings": "strings from scan.strings()", "dir": "the folder to scan",
    "server": "the DNS server to ask", "type": "the record type, like A or MX", "obj": "the value", "factor": "how much bigger",
    "colour": "a colour like #2a7de1", "question": "the question asked", "filters": "which kinds of files to offer",
    "iocs": "a list of IOC texts", "found": "the map extract() gave", "with": "what replaces each match", "pattern_": "",
    "year": "the year to assume", "separator_": "", "values_": "", "line": "one log line", "first": "", "listener": "",
}


def role_for(name: str, spec_roles: dict | None) -> str:
    if spec_roles and name in spec_roles:
        return spec_roles[name]
    return ROLES.get(name) or "an input"


# ------------------------------------------------------------------- Fig
@dataclass
class Legend:
    p: int
    label: str
    role: str
    optional: bool = False
    unused: bool = False


@dataclass
class Control:
    param: str
    p: int
    values: list | None = None       # discrete values (variants)
    lo: float = 0
    hi: float = 100
    step: float = 1
    value: float | str = 0
    unit: str = ""
    labels: list | None = None


class Fig:
    def __init__(self, lib: str, name: str, signature: str, doc: str, spec: dict, *, owner: str | None = None,
                 desktop: bool = False, receiver: str | None = None):
        self.lib = lib
        self.name = name
        self.signature = signature
        self.doc = doc
        self.spec = spec or {}
        self.owner = owner if owner is not None else lib   # "text" -> text.pad_left; "win" -> win.resize
        self.desktop = desktop
        self.receiver = receiver
        self.params = parse_params(signature)
        self.legend: list[Legend] = []
        self.controls: list[Control] = []
        self.svg = ""
        self.call_html = ""
        self.note = ""
        self.real = False
        self.label = ""

    # parts --------------------------------------------------------------
    def p(self, name: str) -> int | None:
        for i, prm in enumerate(self.params):
            if prm.name == name:
                return i
        for e in self.legend:
            if e.label == name:
                return e.p
        return None

    def extra(self, label: str, role: str) -> int:
        """A legend entry for something inside a parameter, like options.width."""
        for e in self.legend:
            if e.label == label:
                return e.p
        i = len(self.params) + sum(1 for e in self.legend if e.p >= len(self.params))
        self.legend.append(Legend(i, label, role))
        return i

    def default_legend(self, used: set | None = None):
        roles = self.spec.get("roles", {})
        have = {e.label for e in self.legend}
        entries = []
        for i, prm in enumerate(self.params):
            if prm.name in have:
                continue
            unused = used is not None and prm.name not in used
            entries.append(Legend(i, prm.name, role_for(prm.name, roles), prm.optional, unused))
        self.legend = entries + self.legend

    # call text -----------------------------------------------------------
    def qual(self) -> str:
        if self.receiver is not None:
            return f"{self.receiver}.{self.name}"
        return f"{self.owner}.{self.name}" if self.owner else self.name

    def call_expr(self, args: dict) -> str:
        if self.spec.get("call"):
            return self.spec["call"]
        given = [i for i, prm in enumerate(self.params) if prm.name in args]
        last = max(given) if given else -1
        parts = []
        for i, prm in enumerate(self.params[: last + 1]):
            if prm.name in args:
                parts.append(args[prm.name])
            elif not prm.variadic:
                parts.append("nil")
        return f"{self.qual()}({', '.join(parts)})"

    def draw(self, body: str, h: float, label: str | None = None):
        self.svg = S.svg_doc(body, h, label or self.label or f"Picture of {self.qual()}")


# ------------------------------------------------------------ evaluation
def arg_value(src: str):
    try:
        return parse_literal(src)
    except Exception:
        return S.NONE


def evaluate(fig: Fig, args: dict, *, override: dict | None = None):
    """Run the example (or take the static result). Gives (ok, value, text)."""
    spec = dict(fig.spec)
    if override:
        spec.update(override)
    expr = spec.get("call") or fig.call_expr(args)
    if "static" in spec:
        try:
            v = parse_literal(spec["static"])
        except ValueError:
            v = runner.Opaque(spec["static"])
        return True, v, spec["static"], False
    if spec.get("run") is False:
        return False, None, "", False
    libs = [fig.lib] + list(spec.get("imports", [])) if fig.lib and fig.lib != "__builtins__" else list(spec.get("imports", []))
    res = runner.run(libs, spec.get("setup", ""), expr)
    if res.ok:
        return True, res.value, res.text, True
    return False, runner.Opaque(res.text), res.text, True


# ------------------------------------------------------------------ HTML
def esc(s) -> str:
    return html.escape(str(s), quote=True)


SUMMARY_ICON = ('<svg class="fv-sum-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" '
                'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M3 3h7v7H3z M14 14h7v7h-7z M10 6.5h4.5a2 2 0 0 1 2 2V14"/></svg>')


def figure_html(fig: Fig, *, sub_title: str | None = None, ident: str | None = None, cls: str = "") -> str:
    legend = []
    for e in sorted(fig.legend, key=lambda e: e.p):
        flag = ""
        if e.unused:
            flag = '<span class="fv-flag">not used here</span>'
        elif e.optional:
            flag = '<span class="fv-flag">optional</span>'
        legend.append(
            f'<li><button type="button" class="fv-chip k{e.p % 8}" data-part="p{e.p}" aria-pressed="false">'
            f'<span class="fv-sw" aria-hidden="true"></span><code>{esc(e.label)}</code>{flag}'
            f'<span class="fv-role">{esc(e.role)}</span></button></li>')
    controls = []
    for c in fig.controls:
        if c.values is not None:
            idx = c.values.index(c.value) if c.value in c.values else 0
            shown = (c.labels or c.values)[idx]
            attrs = (f'min="0" max="{len(c.values) - 1}" step="1" value="{idx}" '
                     f"data-fv-values='{esc(json.dumps([str(v) for v in c.values]))}'")
            if c.labels:
                attrs += f" data-fv-labels='{esc(json.dumps([str(v) for v in c.labels]))}'"
        else:
            shown = f"{S.n(c.value)}"
            attrs = f'min="{S.n(c.lo)}" max="{S.n(c.hi)}" step="{S.n(c.step)}" value="{S.n(c.value)}"'
        controls.append(
            f'<label class="fv-range k{c.p % 8}"><span class="fv-range-name"><span class="fv-sw" aria-hidden="true"></span>'
            f'<code>{esc(c.param)}</code></span>'
            f'<input type="range" data-fv-param="{esc(c.param)}" data-part="p{c.p}" {attrs}>'
            f'<output>{esc(shown)}{esc(c.unit)}</output></label>')
    stage = fig.svg
    if len(stage) > LAZY_BYTES:
        stage = f'<template class="fv-tpl">{stage}</template><p class="fv-wait">Drawing…</p>'
    note = fig.note or ("Real result: worked out by running this example with TB while the site was built."
                        if fig.real else "Illustration: example values, drawn from what the function does.")
    title = sub_title or "Visualize"
    who = ("." + fig.name) if fig.receiver is not None else (fig.qual() if fig.owner else fig.name)
    hint = "" if sub_title else f'<span class="fv-sum-sub">how {esc(who)} works</span>'
    idattr = f' id="{esc(ident)}"' if ident else ""
    ctrl = f'<div class="fv-controls">{"".join(controls)}</div>' if controls else ""
    leg = f'<ul class="fv-legend" aria-label="Inputs and what they control">{"".join(legend)}</ul>' if legend else \
        '<p class="fv-noparams">No inputs: call it as it is.</p>'
    call = f'<p class="fv-call">{fig.call_html}</p>' if fig.call_html else ""
    return (f'<details class="fn-visual{(" " + cls) if cls else ""}"{idattr}>'
            f'<summary>{SUMMARY_ICON}<span class="fv-sum-title">{title}</span>{hint}</summary>'
            f'<div class="fv">{call}<div class="fv-grid"><div class="fv-stage">{stage}</div>'
            f'<div class="fv-side">{leg}{ctrl}<p class="fv-note">{esc(note)}</p></div></div></div></details>')


def call_line(expr: str, result_text: str | None, ok=True, variant: str | None = None, hidden=False) -> str:
    v = (" hidden" if hidden else "") + (f' data-fv-variant="{esc(variant)}"' if variant else "")
    res = ""
    if result_text is not None and result_text != "":
        r = S.clip(result_text, 90)
        res = (f' <span class="fv-gives" aria-hidden="true">→</span> <span class="visually-hidden">gives</span> <code>{esc(r)}</code>'
               if ok else f' <span class="fv-gives" aria-hidden="true">→</span> <span class="fv-err">stops with an error: {esc(r)}</span>')
    return f'<span class="fv-callrow"{v}><code>{esc(expr)}</code>{res}</span>'
