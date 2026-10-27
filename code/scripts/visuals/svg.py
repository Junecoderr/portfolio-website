"""Small SVG kit for the reference pictures.

Everything is drawn in a 340-unit-wide viewBox, top to bottom, so a picture
reads the same on a phone and on a desktop. Colours come from classes styled
in site/assets/css/visuals.css (theme tokens), never from inline colours, so
light and dark themes both work. A "part" is a group tied to one parameter:
<g data-part="p0" class="k0"> gets that parameter's colour, and the legend
chip with the same data-part lights it up.
"""

from __future__ import annotations

import html
import math
from .runner import Bytes, Opaque, literal

W = 340
CW = 7.25          # width of one monospace character at 12px
SW = 6.3           # rough width of one sans character at 11px
NONE = object()


def x(text) -> str:
    """Escape text for SVG; show control characters visibly."""
    s = str(text)
    s = s.replace("\t", "⇥").replace("\r", "␍")
    s = "".join(ch if (ord(ch) >= 32 or ch == "\n") else "·" for ch in s)
    return html.escape(s, quote=True)


def n(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def clip(s: str, chars: int) -> str:
    chars = max(2, int(chars))
    return s if len(s) <= chars else s[: chars - 1] + "…"


# ------------------------------------------------------------- primitives
def rect(x0, y0, w, h, cls="bx", r=6, extra=""):
    return f'<rect x="{n(x0)}" y="{n(y0)}" width="{n(max(w, 0))}" height="{n(max(h, 0))}" rx="{r}" class="{cls}"{extra}/>'


def text(x0, y0, s, cls="t", anchor=None, extra=""):
    a = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text x="{n(x0)}" y="{n(y0)}" class="{cls}"{a}{extra}>{x(s)}</text>'


def line(x1, y1, x2, y2, cls="ln", extra=""):
    return f'<line x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}" class="{cls}"{extra}/>'


def path(d, cls="ln", extra=""):
    return f'<path d="{d}" class="{cls}"{extra}/>'


def head(x0, y0, direction="down", cls="ah", size=5):
    """A filled arrow head with its tip at (x0, y0)."""
    s = size
    pts = {
        "down": [(x0, y0), (x0 - s, y0 - s * 1.5), (x0 + s, y0 - s * 1.5)],
        "up": [(x0, y0), (x0 - s, y0 + s * 1.5), (x0 + s, y0 + s * 1.5)],
        "right": [(x0, y0), (x0 - s * 1.5, y0 - s), (x0 - s * 1.5, y0 + s)],
        "left": [(x0, y0), (x0 + s * 1.5, y0 - s), (x0 + s * 1.5, y0 + s)],
    }[direction]
    return f'<polygon points="{" ".join(f"{n(a)},{n(b)}" for a, b in pts)}" class="{cls}"/>'


def arrow(x1, y1, x2, y2, cls="ar", hcls="ah", both=False):
    if abs(x2 - x1) >= abs(y2 - y1):
        d1, d2 = ("right", "left") if x2 > x1 else ("left", "right")
    else:
        d1, d2 = ("down", "up") if y2 > y1 else ("up", "down")
    out = line(x1, y1, x2, y2, cls) + head(x2, y2, d1, hcls)
    if both:
        out += head(x1, y1, d2, hcls)
    return out


def part(i, body, extra=""):
    if i is None:
        return body
    return f'<g data-part="p{i}" class="k{i % 8}"{extra}>{body}</g>'


def pill(cx, y0, label, cls="fb", tcls="t m b", h=22, minw=40):
    w = max(minw, len(label) * CW + 18)
    return rect(cx - w / 2, y0, w, h, cls, r=h / 2) + text(cx, y0 + h / 2 + 4, label, tcls, "middle"), w


# ------------------------------------------------------------------ icons
ICONS = {
    "file": "M5 2h9l5 5v15H5z M14 2v5h5",
    "folder": "M2 6h7l2 2h11v13H2z",
    "server": "M3 3h18v7H3z M3 14h18v7H3z M7 6.5h.01 M7 17.5h.01",
    "cloud": "M7 19a5 5 0 0 1-.6-9.96A7 7 0 0 1 19.6 11 4 4 0 0 1 18 19z",
    "globe": "M12 2a10 10 0 1 0 .01 0z M2 12h20 M12 2c3 3 4 6.5 4 10s-1 7-4 10c-3-3-4-6.5-4-10s1-7 4-10z",
    "lock": "M5 11h14v10H5z M8 11V7a4 4 0 0 1 8 0v4",
    "unlock": "M5 11h14v10H5z M8 11V7a4 4 0 0 1 7.5-2",
    "key": "M8 15a4 4 0 1 1 3.5-6H22v4h-3v3h-3v-3h-4.5A4 4 0 0 1 8 15z",
    "clock": "M12 2a10 10 0 1 0 .01 0z M12 6v6l4 2",
    "db": "M4 5c0-1.7 3.6-3 8-3s8 1.3 8 3v14c0 1.7-3.6 3-8 3s-8-1.3-8-3z M4 5c0 1.7 3.6 3 8 3s8-1.3 8-3 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
    "gear": "M12 8a4 4 0 1 0 .01 0z M12 1v4 M12 19v4 M1 12h4 M19 12h4 M4.2 4.2l2.8 2.8 M17 17l2.8 2.8 M4.2 19.8 7 17 M17 7l2.8-2.8",
    "user": "M12 3a4 4 0 1 0 .01 0z M4 21c0-4 3.6-7 8-7s8 3 8 7",
    "terminal": "M2 4h20v16H2z M6 9l3 3-3 3 M11 15h6",
    "shield": "M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5z",
    "bell": "M6 16V10a6 6 0 0 1 12 0v6l2 2H4z M10 21h4",
    "cpu": "M6 6h12v12H6z M9 2v4 M15 2v4 M9 18v4 M15 18v4 M2 9h4 M2 15h4 M18 9h4 M18 15h4",
    "list": "M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01",
    "doc": "M5 2h14v20H5z M8 7h8 M8 11h8 M8 15h5",
    "window": "M2 4h20v16H2z M2 8h20 M5 6h.01 M7.5 6h.01",
    "laptop": "M4 5h16v10H4z M2 19h20",
    "zip": "M5 2h14v20H5z M12 2v2 M12 6v2 M12 10v2 M10 14h4v4h-4z",
    "packet": "M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10",
    "alert": "M12 3l10 18H2z M12 10v5 M12 18h.01",
    "check": "M4 12l5 5L20 6",
    "cross": "M5 5l14 14 M19 5L5 19",
    "search": "M10 3a7 7 0 1 0 .01 0z M15 15l6 6",
    "fp": "M12 4a8 8 0 0 1 8 8 M4 12a8 8 0 0 1 4-6.9 M8 12a4 4 0 0 1 8 0c0 3-1 6-2 8 M12 12c0 3-.5 6-2 9 M6 16c.5 2 1 3.5 2 5",
    "chart": "M3 21h18 M6 17V10 M11 17V5 M16 17v-6 M21 17v-3",
    "mail": "M2 5h20v14H2z M2 5l10 8 10-8",
    "tag": "M3 3h8l10 10-8 8L3 11z M7 7h.01",
    "plug": "M9 2v6 M15 2v6 M6 8h12v4a6 6 0 0 1-12 0z M12 18v4",
    "spark": "M12 2v6 M12 16v6 M2 12h6 M16 12h6 M5 5l3.5 3.5 M15.5 15.5 19 19 M5 19l3.5-3.5 M15.5 8.5 19 5",
    "dice": "M4 4h16v16H4z M8.5 8.5h.01 M15.5 15.5h.01 M12 12h.01",
    "calendar": "M3 5h18v16H3z M3 10h18 M8 3v4 M16 3v4",
    "test": "M9 2h6 M10 2v6L4 20h16L14 8V2",
    "code": "M8 7l-5 5 5 5 M16 7l5 5-5 5",
}


def icon(name, x0, y0, size=22, cls="ic"):
    d = ICONS.get(name, ICONS["doc"])
    k = size / 24
    return f'<path d="{d}" class="{cls}" transform="translate({n(x0)} {n(y0)}) scale({n(round(k, 3))})"/>'


# ----------------------------------------------------------- value drawing
def kind_of(v) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "text"
    if isinstance(v, Bytes):
        return "bytes"
    if isinstance(v, list):
        if v and all(isinstance(i, dict) for i in v):
            return "records"
        return "list"
    if isinstance(v, dict):
        return "map"
    return "other"


def short(v, chars=26) -> str:
    return clip(literal(v), chars)


def wrap_chars(s: str, width: int) -> list[str]:
    out = []
    for raw in s.split("\n"):
        if not raw:
            out.append("")
            continue
        while len(raw) > width:
            cut = raw.rfind(" ", 0, width + 1)
            if cut <= width // 3:
                cut = width
            out.append(raw[:cut].rstrip(" ") if cut != width else raw[:cut])
            raw = raw[cut:].lstrip(" ") if cut != width else raw[cut:]
        out.append(raw)
    return out


def v_lines(s: str, x0, y0, w, max_lines=7, cls="t m", quote=False):
    chars = max(4, int((w - 4) // CW))
    lines = wrap_chars(s, chars)
    if s.endswith("\n") and lines and lines[-1] == "":
        lines = lines[:-1]
    more = 0
    if len(lines) > max_lines:
        more = len(lines) - max_lines + 1
        lines = lines[: max_lines - 1]
    out = []
    yy = y0 + 13
    for i, ln_ in enumerate(lines):
        t = ln_
        if quote and len(lines) == 1:
            t = clip('"' + t + '"', chars)
        out.append(text(x0, yy, t if t else " ", cls))
        yy += 16
    if more:
        out.append(text(x0, yy, f"… {more} more line{'s' if more > 1 else ''}", "tf sm"))
        yy += 16
    return "".join(out), yy - y0 - 9


def v_cells(s: str, x0, y0, w, marks=None, cell=15):
    """One cell per character; spaces drawn as a faint dot. marks: index -> part."""
    per = max(1, int(w // cell))
    out = []
    marks = dict(marks or {})
    bracket = marks.pop("_bracket", None)
    rows = max(1, math.ceil(len(s) / per)) if s else 1
    if not s:
        out.append(text(x0, y0 + 14, '"" (empty)', "tf m"))
        return "".join(out), 20
    for i, ch in enumerate(s):
        r, c = divmod(i, per)
        cx, cy = x0 + c * cell, y0 + r * 22
        p = (marks or {}).get(i)
        cell_svg = rect(cx, cy, cell - 2, 19, "cl" if p is None else "pb", r=3)
        if ch == " ":
            cell_svg += text(cx + (cell - 2) / 2, cy + 14, "·", "tf m", "middle")
        else:
            cell_svg += text(cx + (cell - 2) / 2, cy + 14, ch, "t m" if p is None else "tp m b", "middle")
        out.append(part(p, cell_svg) if p is not None and not isinstance(p, str) else
                   (cell_svg.replace('class="cl"', 'class="rb"') if p == "r" else cell_svg))
    h = rows * 22 - 3
    if bracket is not None and rows == 1:
        bp, blabel = bracket
        x2 = x0 + len(s) * cell - 2
        br = path(f"M{n(x0)} {n(y0 + h + 3)} v5 H{n(x2)} v-5", "pl w2") + text((x0 + x2) / 2, y0 + h + 21, blabel, "tp sm b", "middle")
        out.append(part(bp, br))
        h += 24
    return "".join(out), h


def v_bool(v, x0, y0):
    label = "true" if v else "false"
    cls = "okb" if v else "nob"
    w = 66
    body = rect(x0, y0 + 1, w, 22, cls, r=11)
    body += icon("check" if v else "cross", x0 + 6, y0 + 4, 16, "ic2")
    body += text(x0 + 26, y0 + 16, label, "t m b")
    return f'<g class="{"okg" if v else "nog"}">{body}</g>', 24


def v_number(v, x0, y0, w):
    s = literal(v)
    return text(x0, y0 + 19, clip(s, int(w // 10)), "t m lg b"), 24


def v_list(items, x0, y0, w, max_items=14, part_of=None):
    """Items as chips that wrap; part_of(i) -> part index or None."""
    out = []
    cx, cy = x0, y0
    shown = items[:max_items]
    for i, it in enumerate(shown):
        lab = short(it, 22)
        cw_ = len(lab) * CW + 12
        if cx + cw_ > x0 + w and cx > x0:
            cx, cy = x0, cy + 26
        p = part_of(i) if part_of else None
        chip = rect(cx, cy, cw_, 21, "cl" if p is None else "pb", r=5) + text(cx + 6, cy + 15, lab, "t m" if p is None else "tp m")
        out.append(part(p, chip) if p is not None else chip)
        cx += cw_ + 5
    if len(items) > max_items:
        lab = f"+{len(items) - max_items} more"
        if cx + 70 > x0 + w:
            cx, cy = x0, cy + 26
        out.append(text(cx + 2, cy + 15, lab, "tf sm"))
    if not items:
        out.append(text(x0, cy + 15, "[] (empty list)", "tf m"))
    return "".join(out), cy - y0 + 21


def v_map(m, x0, y0, w, max_rows=7):
    out = []
    keys = list(m.keys())
    kw = min(max((len(str(k)) for k in keys), default=1), 14) * CW + 10
    yy = y0
    for k in keys[:max_rows]:
        out.append(text(x0, yy + 13, clip(str(k), 14), "ts m"))
        out.append(text(x0 + kw, yy + 13, short(m[k], int((w - kw) // CW)), "t m"))
        yy += 17
    if len(keys) > max_rows:
        out.append(text(x0, yy + 13, f"… {len(keys) - max_rows} more keys", "tf sm"))
        yy += 17
    if not keys:
        out.append(text(x0, yy + 13, "{} (empty map)", "tf m"))
        yy += 17
    return "".join(out), yy - y0


def v_records(rows, x0, y0, w, max_rows=5, columns=None):
    cols = list(columns or [])
    if not cols:
        for r in rows:
            for k, val_ in r.items():
                if k not in cols and not isinstance(val_, (dict, list, Opaque)):
                    cols.append(k)
        cols = cols[:4] if w > 250 else cols[:3]
    if not cols:
        return v_list(rows, x0, y0, w)
    cw_ = w / len(cols)
    chars = int((cw_ - 6) // CW)
    out = [rect(x0, y0, w, 18, "th", r=3)]
    for j, c in enumerate(cols):
        out.append(text(x0 + j * cw_ + 4, y0 + 13, clip(str(c), chars), "ts m b"))
    yy = y0 + 18
    for r in rows[:max_rows]:
        for j, c in enumerate(cols):
            v = r.get(c, "")
            s = v if isinstance(v, str) else literal(v)
            out.append(text(x0 + j * cw_ + 4, yy + 13, clip(s, chars), "t m"))
        out.append(line(x0, yy + 18, x0 + w, yy + 18, "ln2"))
        yy += 18
    if len(rows) > max_rows:
        out.append(text(x0, yy + 14, f"\u2026 {len(rows) - max_rows} more rows ({len(rows)} in all)", "tf sm"))
        yy += 18
    return "".join(out), yy - y0


def v_bytes(b: bytes, x0, y0, w, max_bytes=None, marks=None, cell=22):
    per = max(1, int(w // cell))
    max_bytes = max_bytes or per * 2
    data = b[:max_bytes]
    out = []
    for i, byte in enumerate(data):
        r, c = divmod(i, per)
        cx, cy = x0 + c * cell, y0 + r * 36
        p = (marks or {}).get(i)
        box_cls = "cl" if p is None else ("rb" if p == "r" else "pb")
        body = rect(cx, cy, cell - 2, 18, box_cls, r=3)
        body += text(cx + (cell - 2) / 2, cy + 13, f"{byte:02x}", "t m" if p is None else ("t m b" if p == "r" else "tp m b"), "middle")
        ch = chr(byte) if 32 <= byte < 127 else "·"
        body += text(cx + (cell - 2) / 2, cy + 31, ch, "tf m sm", "middle")
        out.append(part(p, body) if isinstance(p, int) else body)
    rows = max(1, math.ceil(len(data) / per))
    h = rows * 36 - 2
    if len(b) > max_bytes:
        out.append(text(x0, y0 + h + 12, f"… {len(b)} bytes in all", "tf sm"))
        h += 16
    if not b:
        out.append(text(x0, y0 + 14, 'b"" (no bytes)', "tf m"))
        h = 20
    return "".join(out), h


def _tree_rows(v, depth, rows, key=None, max_rows=12):
    if len(rows) >= max_rows:
        return
    if isinstance(v, dict) and v:
        rows.append((depth, key, "{map}" if key is not None else None, True))
        for k, x_ in list(v.items())[:8]:
            _tree_rows(x_, depth + (1 if key is not None or depth else 1), rows, str(k), max_rows)
    elif isinstance(v, list) and v and any(isinstance(i, (dict, list)) for i in v):
        rows.append((depth, key, f"[list of {len(v)}]", True))
        for i, x_ in enumerate(v[:4]):
            _tree_rows(x_, depth + 1, rows, str(i), max_rows)
    else:
        rows.append((depth, key, literal(v), False))


def v_tree(v, x0, y0, w, max_rows=12):
    """A nested value as an indented tree: maps and lists open up."""
    rows = []
    _tree_rows(v, 0, rows, None, max_rows)
    rows = [r for r in rows if not (r[1] is None and r[2] is None)]
    out = []
    yy = y0
    for depth, key, shown, branch in rows[:max_rows]:
        ix = x0 + max(depth - 1, 0) * 14
        if depth > 0:
            out.append(path(f"M{n(ix - 8)} {n(yy)} V{n(yy + 9)} H{n(ix - 2)}", "ln"))
        chars = int((x0 + w - ix) // CW)
        if key is not None:
            out.append(text(ix, yy + 13, clip(str(key), 16), "ts m b" if branch else "ts m"))
            kx = ix + min(len(str(key)), 16) * CW + 8
            out.append(text(kx, yy + 13, clip(shown or "", max(3, int((x0 + w - kx) // CW))), "tf m" if branch else "t m"))
        else:
            out.append(text(ix, yy + 13, clip(shown or "", chars), "t m"))
        yy += 17
    if len(rows) > max_rows:
        out.append(text(x0, yy + 13, "\u2026", "tf"))
        yy += 17
    return "".join(out), max(yy - y0, 17)


def v_files(paths, x0, y0, w, max_rows=10):
    """Paths as a little file tree: the folders they share, then files with icons."""
    items = [str(p_) for p_ in (paths if isinstance(paths, list) else [paths])]
    out = []
    yy = y0
    if not items:
        out.append(text(x0, yy + 13, "[] (nothing)", "tf m"))
        return "".join(out), 18
    parts = [p_.rstrip("/").split("/") for p_ in items]
    common = []
    if len(parts) > 1 or len(parts[0]) > 1:
        for segs in zip(*[ps[:-1] for ps in parts]):
            if len(set(segs)) == 1:
                common.append(segs[0])
            else:
                break
    rows = []
    if common:
        rows.append((0, "/".join(common), True))
    seen = set()
    base = 1 if common else 0
    for p_, segs in zip(items, parts):
        rest = segs[len(common):]
        for d in range(1, len(rest)):
            key = tuple(rest[:d])
            if key not in seen:
                seen.add(key)
                rows.append((base + d - 1, rest[d - 1], True))
        rows.append((base + len(rest) - 1, rest[-1] if rest else p_, p_.endswith("/") or "." not in (rest[-1] if rest else p_)))
    for depth, name, is_dir in rows[:max_rows]:
        ix = x0 + min(depth, 5) * 14
        out.append(icon("folder" if is_dir else "file", ix, yy + 1, 15, "ic"))
        out.append(text(ix + 20, yy + 13, clip(name, int((x0 + w - ix - 20) // CW)), "t m b" if is_dir else "t m"))
        yy += 18
    if len(rows) > max_rows:
        out.append(text(x0, yy + 13, f"\u2026 {len(rows) - max_rows} more", "tf sm"))
        yy += 18
    return "".join(out), yy - y0


def v_http(m, x0, y0, w):
    """A response map {status, headers, body} drawn like an HTTP message."""
    status = m.get("status", 200)
    reason = {200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently", 302: "Found", 400: "Bad Request",
              401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Server Error"}.get(status, "")
    out = [rect(x0, y0, 66, 20, "okb" if isinstance(status, int) and status < 400 else "nob", r=4),
           text(x0 + 33, y0 + 14, str(status), "t m b", "middle"), text(x0 + 74, y0 + 14, reason, "ts sm b")]
    yy = y0 + 26
    heads = m.get("headers") or {}
    for k, v_ in list(heads.items())[:4]:
        out.append(text(x0, yy + 12, clip(f"{k}: {v_}", int(w // CW)), "ts m sm"))
        yy += 15
    body = m.get("body")
    if body not in (None, ""):
        out.append(line(x0, yy + 4, x0 + w, yy + 4, "ln2"))
        b = body if isinstance(body, str) else (literal(body) if not isinstance(body, Bytes) else body.data.decode("utf-8", "replace"))
        s_, h = v_lines(b or 'b""', x0, yy + 6, w, max_lines=4)
        out.append(s_)
        yy += h + 8
    return "".join(out), yy - y0


def render_value(v, x0, y0, w, mode=None, marks=None):
    """Draw a value in the box area; gives (svg, height)."""
    if mode == "cells" and isinstance(v, str):
        return v_cells(v, x0, y0, w, marks)
    if mode == "lines" and isinstance(v, str):
        return v_lines(v, x0, y0, w, max_lines=9)
    if mode == "tree" and isinstance(v, (dict, list)):
        return v_tree(v, x0, y0, w)
    if mode == "files" and isinstance(v, (list, str)):
        return v_files(v, x0, y0, w)
    if mode == "http" and isinstance(v, dict):
        return v_http(v, x0, y0, w)
    if mode == "hex" and isinstance(v, str):
        return v_lines(v, x0, y0, w, max_lines=5)
    k = kind_of(v)
    if k == "bool":
        return v_bool(v, x0, y0)
    if k == "number":
        return v_number(v, x0, y0, w)
    if k == "nil":
        return text(x0, y0 + 15, "nil", "tf m b"), 20
    if k == "text":
        if mode == "auto-cells" and marks and len(v) <= 2 * int(w // 15) and "\n" not in v:
            return v_cells(v, x0, y0, w, marks)
        return v_lines(v, x0, y0, w, quote=True)
    if k == "bytes":
        return v_bytes(v.data, x0, y0, w, marks=marks)
    if k == "records":
        return v_records(v, x0, y0, w)
    if k == "list":
        return v_list(v, x0, y0, w)
    if k == "map":
        return v_map(v, x0, y0, w)
    if isinstance(v, Opaque):
        return v_lines(v.text, x0, y0, w)
    return v_lines(str(v), x0, y0, w)


# ------------------------------------------------------------------ blocks
def block(x0, y0, w, *, label=None, p=None, value=NONE, mode=None, lines=None, icon_name=None,
          kind="in", src=None, marks=None, minh=0, sub=None):
    """A labelled box. kind: in (an input), out (the result), ghost (not given), plain."""
    inner_x = x0 + 9 + (30 if icon_name else 0)
    inner_w = w - 18 - (30 if icon_name else 0)
    cy = y0 + (21 if label else 9)
    body = []
    if value is not NONE:
        svg_, h = render_value(value, inner_x, cy, inner_w, mode, marks)
    elif lines is not None:
        svg_, h = v_lines("\n".join(lines) if isinstance(lines, list) else lines, inner_x, cy - 2, inner_w, max_lines=9,
                          cls="t" if kind == "plain-sans" else "t m")
    else:
        svg_, h = "", 0
    if src:
        body.append(text(inner_x, cy + h + 14, clip(src, int(inner_w // (CW * 0.9))), "tf m sm"))
        h += 17
    if sub:
        body.append(text(inner_x, cy + h + 14, clip(sub, int(inner_w // SW)), "tf sm"))
        h += 17
    total = max(minh, (cy - y0) + h + 10, 30 if icon_name else 0)
    cls = {"in": "pb" if p is not None else "bx", "out": "ob", "ghost": "gb", "plain": "bx", "plain-sans": "bx",
           "fn": "fb"}.get(kind, "bx")
    frame = rect(x0, y0, w, total, cls, r=7)
    head_ = ""
    if label:
        lab_cls = "tp sm b" if p is not None else ("ta sm b" if kind == "out" else "ts sm b")
        head_ = text(x0 + 9, y0 + 14, label, lab_cls)
    ic = icon(icon_name, x0 + 9, y0 + (total - 22) / 2 if not label else y0 + 20, 22, "icp" if p is not None else "ic") if icon_name else ""
    return part(p, frame + head_ + ic + svg_ + "".join(body)), total


# -------------------------------------------------------------------- flow
class Row:
    def __init__(self, *blocks, cols=2, gap=8):
        self.blocks = [b for b in blocks if b]
        self.cols = cols
        self.gap = gap


class Arrow:
    def __init__(self, label=None, p=None, note=None, h=34, back=False):
        self.label, self.p, self.note, self.h, self.back = label, p, note, h, back


class Raw:
    """A pre-drawn piece: fn(x, y, w) -> (svg, h)."""

    def __init__(self, fn):
        self.fn = fn


def flow(items, w=W, x0=0, y0=0):
    out = []
    y = y0
    for it in items:
        if isinstance(it, Row):
            bl = it.blocks
            cols = min(it.cols, len(bl)) or 1
            for start in range(0, len(bl), cols):
                chunk = bl[start:start + cols]
                bw = (w - it.gap * (len(chunk) - 1)) / len(chunk)
                drawn = []
                for j, spec in enumerate(chunk):
                    drawn.append((spec, x0 + j * (bw + it.gap), bw))
                hs = [block(bx, y, bw, **spec)[1] for spec, bx, bw in drawn]
                hh = max(hs)
                for spec, bx, bw_ in drawn:
                    s = dict(spec)
                    s["minh"] = hh
                    out.append(block(bx, y, bw_, **s)[0])
                y += hh + it.gap
            y -= it.gap
        elif isinstance(it, Arrow):
            cx = x0 + w / 2
            top, bot = y + 3, y + it.h - 3
            body = arrow(cx, top, cx, bot) if not it.back else arrow(cx, bot, cx, top)
            if it.label:
                lw = len(it.label) * CW + 16
                lx = cx + 10
                if lx + lw > x0 + w:
                    lx = max(x0, x0 + w - lw)
                body += rect(lx, y + it.h / 2 - 10, lw, 20, "fb", r=10)
                body += text(lx + 8, y + it.h / 2 + 4, it.label, "ta m b sm2")
            if it.note:
                body += text(cx - 10, y + it.h / 2 + 4, it.note, "ts sm", "end")
            out.append(part(it.p, body) if it.p is not None else body)
            y += it.h
        elif isinstance(it, Raw):
            s, h = it.fn(x0, y, w)
            out.append(s)
            y += h
        elif isinstance(it, (int, float)):
            y += it
    return "".join(out), y - y0


def svg_doc(body: str, h: float, label: str, w=W) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="fv-svg" viewBox="0 0 {n(w)} {n(math.ceil(h + 2))}" '
            f'role="img" aria-label="{html.escape(label, quote=True)}"><title>{x(label)}</title>{body}</svg>')
