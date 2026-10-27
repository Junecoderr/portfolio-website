"""Pictures for the gui library: windows, every widget, window methods and
dialogs. One template, "gui", picks a drawing by the spec's "draw" key.

Sliders change the drawing live: elements carry data-fv-bind (see
site/assets/js/visuals.js), e.g. a window frame whose width follows the
"width" slider. Parameters and option keys (options.width...) each get their
own colour, shared with their legend chip.
"""

from __future__ import annotations

import json
import math

from . import svg as S
from .core import Control, Fig, arg_value, call_line, esc, returns_of
from .runner import Opaque, literal, parse_literal

K = 0.3   # window pixels -> drawing units


def bind(*ops) -> str:
    return " data-fv-bind='" + esc(json.dumps(list(ops), separators=(",", ":"))) + "'"


def val(a: dict, name: str, default=None):
    if name not in a:
        return default
    v = arg_value(a[name])
    return default if v is S.NONE else v


def opt(a: dict, key: str, default=None):
    o = val(a, "options", {})
    return o.get(key, default) if isinstance(o, dict) else default


class X:
    """Drawing helpers bound to one figure."""

    def __init__(self, fig: Fig, a: dict):
        self.fig, self.a = fig, a

    def p(self, name):
        q = self.fig.p(name)
        if q is None and name.startswith("options."):
            role = self.fig.spec.get("opts", {}).get(name[8:], "a setting in options")
            q = self.fig.extra(name, role)
        return q

    def part(self, name, body):
        return S.part(self.p(name) if name else None, body)


def win(x0, y0, w, h, title="My app", tcls="t sm b", wb="", hb="", tb_="", extra_cls=""):
    """A window: frame, title bar with three dots, centred title."""
    s = f'<rect x="{S.n(x0)}" y="{S.n(y0)}" width="{S.n(w)}" height="{S.n(h)}" rx="7" class="win{extra_cls}"{wb}{hb}/>'
    s += f'<rect x="{S.n(x0)}" y="{S.n(y0)}" width="{S.n(w)}" height="20" rx="7" class="winbar"{wb}/>'
    s += S.line(x0, y0 + 20, x0 + w, y0 + 20, "ln2", wb.replace('"width"', '"x2"') if False else "")
    for i in range(3):
        s += f'<circle cx="{S.n(x0 + 10 + i * 9)}" cy="{S.n(y0 + 10)}" r="3" class="wdot"/>'
    if title is not None:
        s += S.text(x0 + w / 2, y0 + 14, S.clip(str(title), int(max(w - 60, 20) // 6)), tcls, "middle", tb_)
    return s


def btn(x0, y0, w, label, primary=False, h=22):
    return S.rect(x0, y0, w, h, "gbtnp" if primary else "gbtn", r=5) + S.text(x0 + w / 2, y0 + h / 2 + 4, S.clip(label, int(w // 6.5)), "tbtnp sm2 b" if primary else "t sm2 b", "middle")


def field(x0, y0, w, txt, placeholder=False, h=22):
    return S.rect(x0, y0, w, h, "gfield", r=4) + S.text(x0 + 7, y0 + h / 2 + 4, S.clip(txt, int((w - 10) // 6.5)), "tf sm2" if placeholder else "t sm2")


def bubble(x0, y0, code, cls="pb", tcls="tp m sm"):
    w = len(code) * 6.4 + 14
    return S.rect(x0, y0, w, 20, cls, r=10) + S.text(x0 + 7, y0 + 14, code, tcls), w


def cursor(x0, y0):
    pts = [(0, 0), (0, 14), (4, 10.5), (7, 16), (9.5, 15), (6.5, 9.5), (11, 9.5)]
    return f'<polygon points="{" ".join(f"{S.n(x0 + a)},{S.n(y0 + b)}" for a, b in pts)}" class="cur"/>'


def keycap(x0, y0, label, cls="key"):
    w = max(22, len(label) * 7 + 12)
    return S.rect(x0, y0, w, 22, cls, r=4) + S.text(x0 + w / 2, y0 + 15, label, "t m sm2 b", "middle"), w


def tag(x0, y0, label, p_cls="pb", t_cls="tp m sm"):
    w = len(label) * 6.3 + 12
    return S.rect(x0, y0, w, 18, p_cls, r=9) + S.text(x0 + 6, y0 + 13, label, t_cls), w


def dashed(x1, y1, x2, y2):
    return S.line(x1, y1, x2, y2, "pl dash") + S.head(x2, y2, "down" if y2 > y1 and abs(y2 - y1) > abs(x2 - x1) else ("up" if abs(y2 - y1) > abs(x2 - x1) else ("right" if x2 > x1 else "left")), "pah", 4)


def screen(x0, y0, w, h):
    s = S.rect(x0, y0, w, h, "scr", r=6)
    s += S.rect(x0 + w / 2 - 18, y0 + h, 36, 8, "scrst", r=1) + S.rect(x0 + w / 2 - 36, y0 + h + 8, 72, 4, "scrst", r=2)
    return s


def callout_fn(x: X, name, x0, y0, code=None):
    code = code or (x.a.get(name) or "fn() { … }")
    code = S.clip(code.replace("\n", " "), 34)
    b, w = bubble(x0, y0, code)
    return x.part(name, b), w


# ------------------------------------------------------------- widgets
def body_window(x: X, inner_h, title="My app", draw=None):
    """A window at the top of the picture with room for one widget demo."""
    X0, Y0, Wd = 10, 6, S.W - 20
    s = win(X0, Y0, Wd, inner_h + 34, title)
    return s, (X0 + 12, Y0 + 28, Wd - 24), inner_h + 40


def d_label(x: X, a, v):
    text = val(a, "text", "Hello")
    style = opt(a, "style", "normal")
    s, (bx, by, bw), h = body_window(x, 70)
    size = {"title": "lg b", "display": "xl b", "heading": "lg", "bold": "b", "small": "sm", "muted": "ts"}.get(style, "")
    t = S.text(bx + 4, by + 30, text, f"t {size}")
    tw = len(str(text)) * (11 if "lg" in size or "xl" in size else 7) + 10
    s += x.part("text", S.rect(bx - 2, by + 8, tw, 32, "phl dash", r=4) + t)
    s += x.part("options.style", tag(bx + tw + 10, by + 16, f"style: \"{style}\"")[0])
    s += x.part("options", S.text(bx, by + 58, "options also: wrap, align, width", "tp sm"))
    return s, h


def d_button(x: X, a, v):
    text = val(a, "text", "Save")
    primary = bool(opt(a, "primary", False))
    s, (bx, by, bw), h = body_window(x, 92)
    s += x.part("text", btn(bx, by + 8, 110, text, primary) + S.rect(bx - 3, by + 5, 116, 28, "phl dash", r=7))
    s += cursor(bx + 80, by + 22)
    b, w = callout_fn(x, "on_click", bx + 130, by + 10)
    s += b + x.part("on_click", S.text(bx + 130, by + 46, "runs on every click", "tp sm"))
    s += x.part("options", tag(bx, by + 58, f"options: {literal(val(a, 'options', {}))}")[0])
    if primary:
        s += x.part("options.primary", S.text(bx, by + 92, "primary: the filled main button", "tp sm"))
    return s, h + 6


def d_input(x: X, a, v):
    ph = opt(a, "placeholder", "Search")
    s, (bx, by, bw), h = body_window(x, 96)
    s += x.part("options.placeholder", field(bx, by + 8, 170, ph, placeholder=True) + S.text(bx + 176, by + 23, "placeholder", "tp sm"))
    s += field(bx, by + 40, 170, "ssh brute") + S.line(bx + 68, by + 44, bx + 68, by + 58, "ln w2")
    kc, kw = keycap(bx + 180, by + 40, "Enter")
    s += kc
    b, w = bubble(bx + 180 + kw + 6, by + 41, "on_enter(text)")
    s += x.part("options.on_enter", b)
    s += x.part("options", S.text(bx, by + 84, "options also: value, on_change, secret, width", "tp sm"))
    return s, h


def d_textarea(x: X, a, v):
    lines = int(opt(a, "height", 4) or 4)
    s, (bx, by, bw), h = body_window(x, 26 + lines * 14 + 30)
    s += S.rect(bx, by + 6, 200, lines * 14 + 10, "gfield", r=4)
    for i in range(lines):
        s += S.line(bx + 8, by + 18 + i * 14, bx + 8 + [150, 120, 170, 90, 140, 110][i % 6], by + 18 + i * 14, "txl")
    s += x.part("options.height", S.line(bx + 210, by + 8, bx + 210, by + 14 + lines * 14, "pl w2") + S.text(bx + 216, by + 14 + lines * 7, f"height: {lines} lines", "tp sm"))
    s += x.part("options.mono", S.text(bx, by + lines * 14 + 34, "mono: true uses a code font", "tp sm"))
    return s, h


def d_checkbox(x: X, a, v):
    text = val(a, "text", "Remember me")
    checked = bool(opt(a, "checked", True))
    s, (bx, by, bw), h = body_window(x, 70)
    box = S.rect(bx, by + 10, 16, 16, "gbox" if not checked else "gboxon", r=3)
    if checked:
        box += S.icon("check", bx + 2, by + 12, 12, "icw")
    s += x.part("options.checked", box + S.text(bx - 2, by + 44, f"checked: {literal(checked)}", "tp sm"))
    s += x.part("text", S.text(bx + 24, by + 23, text, "t sm2") + S.rect(bx + 20, by + 8, len(str(text)) * 7 + 10, 22, "phl dash", r=4))
    b, w = bubble(bx + 150, by + 36, "on_change(checked)")
    s += x.part("options.on_change", b)
    return s, h


def d_select(x: X, a, v):
    choices = val(a, "choices", ["low", "medium", "high"])
    cur = opt(a, "value", choices[0] if choices else "")
    s, (bx, by, bw), h = body_window(x, 30 + len(choices) * 20 + 16)
    s += x.part("options.value", field(bx, by + 6, 120, str(cur)) + S.path(f"M{bx + 106} {by + 15} l4 4 4-4", "ln w2") + S.text(bx + 128, by + 21, "value", "tp sm"))
    lst = S.rect(bx, by + 30, 120, len(choices) * 20 + 4, "gpop", r=4)
    for i, c in enumerate(choices):
        if c == cur:
            lst += S.rect(bx + 2, by + 32 + i * 20, 116, 20, "gsel", r=3)
        lst += S.text(bx + 8, by + 46 + i * 20, str(c), "t sm2")
    s += x.part("choices", lst + S.rect(bx - 3, by + 27, 126, len(choices) * 20 + 10, "phl dash", r=6))
    b, w = bubble(bx + 140, by + 40, "on_change(choice)")
    s += x.part("options.on_change", b)
    return s, h


def d_table(x: X, a, v):
    cols = val(a, "columns", ["ip", "count"])
    rows_n = int(opt(a, "height", 3) or 3)
    s, (bx, by, bw), h = body_window(x, 26 + rows_n * 18 + 34)
    cw = (bw - 70) / max(len(cols), 1)
    hdr = S.rect(bx, by + 4, cw * len(cols), 18, "gth", r=3)
    for i, c in enumerate(cols):
        hdr += S.text(bx + 6 + i * cw, by + 17, str(c), "t sm2 b")
    s += x.part("columns", hdr + S.rect(bx - 3, by + 1, cw * len(cols) + 6, 24, "phl dash", r=5))
    sample = [["203.0.113.42", "22"], ["198.51.100.7", "9"], ["192.0.2.15", "3"], ["10.0.0.4", "1"], ["10.0.0.9", "1"]]
    for r in range(rows_n):
        y = by + 22 + r * 18
        if r == 0:
            s += x.part("options.on_select", S.rect(bx, y, cw * len(cols), 18, "gsel", r=2))
        for i in range(len(cols)):
            s += S.text(bx + 6 + i * cw, y + 13, sample[r % 5][i % 2], "t m sm")
        s += S.line(bx, y + 18, bx + cw * len(cols), y + 18, "ln2")
    s += x.part("options.height", S.line(bx + cw * len(cols) + 8, by + 22, bx + cw * len(cols) + 8, by + 22 + rows_n * 18, "pl w2") +
                S.text(bx + cw * len(cols) + 13, by + 26 + rows_n * 9, f"height {rows_n}", "tp sm"))
    s += x.part("options.on_select", S.text(bx, by + 40 + rows_n * 18, "on_select(row, index) runs when you pick a row", "tp sm"))
    return s, h


def d_list_box(x: X, a, v):
    items = val(a, "items", ["auth.log", "access.log", "firewall.log"])
    s, (bx, by, bw), h = body_window(x, 22 + len(items) * 20 + 26)
    lst = S.rect(bx, by + 4, 150, len(items) * 20 + 4, "gfield", r=4)
    for i, it in enumerate(items):
        if i == 1:
            lst += S.rect(bx + 2, by + 6 + i * 20, 146, 20, "gsel", r=3)
        lst += S.text(bx + 8, by + 20 + i * 20, str(it), "t sm2")
    s += x.part("items", lst + S.rect(bx - 3, by + 1, 156, len(items) * 20 + 10, "phl dash", r=6))
    b, w = bubble(bx + 166, by + 26, "on_select(item, i)")
    s += x.part("options.on_select", b)
    s += x.part("options", S.text(bx, by + len(items) * 20 + 30, "options also: height, on_open, expand", "tp sm"))
    return s, h


def d_image(x: X, a, v):
    path = val(a, "path", "logo.png")
    s, (bx, by, bw), h = body_window(x, 100)
    pic = S.rect(bx, by + 4, 120, 80, "gpic", r=4) + S.path(f"M{bx + 8} {by + 76} l30 -34 22 22 14 -12 38 24z", "mtn")
    pic += f'<circle cx="{bx + 92}" cy="{by + 24}" r="8" class="sun"/>'
    s += pic
    s += x.part("path", S.icon("file", bx + 140, by + 20, 22, "icp") + S.text(bx + 168, by + 36, S.clip(str(path), 16), "tp m sm2") +
                dashed(bx + 138, by + 32, bx + 124, by + 40) + S.text(bx + 140, by + 64, "a PNG or GIF file", "tp sm"))
    return s, h


def d_progress(x: X, a, v):
    value = opt(a, "value", 60)
    s, (bx, by, bw), h = body_window(x, 64)
    full = 220
    s += S.rect(bx, by + 10, full, 14, "gtrack", r=7)
    s += x.part("options.value", f'<rect x="{bx}" y="{by + 10}" width="{S.n(full * value / 100)}" height="14" rx="7" class="pfill"'
                + bind(["width", 0, {"value": full / 100}]) + "/>"
                + S.text(bx + full + 8, by + 22, f"{value}%", "tp sm b", extra=bind(["text", "{value}%"])))
    s += S.text(bx, by + 46, "0", "tf sm") + S.text(bx + full, by + 46, "100", "tf sm", "end")
    x.fig.controls.append(Control("value", x.p("options.value"), lo=0, hi=100, step=1, value=value, unit="%"))
    return s, h


def d_separator(x: X, a, v):
    s, (bx, by, bw), h = body_window(x, 70)
    s += S.text(bx, by + 18, "Above the line", "t sm2")
    s += S.line(bx, by + 32, bx + bw, by + 32, "ln w2")
    s += S.text(bx, by + 56, "Below the line", "t sm2")
    s += S.text(bx + bw, by + 28, "separator()", "ta m sm", "end")
    return s, h


def _boxes(x0, y0, n_, w, h, horizontal):
    s = ""
    for i in range(n_):
        if horizontal:
            s += S.rect(x0 + i * (w + 6), y0, w, h, "gbtn", r=4) + S.text(x0 + i * (w + 6) + w / 2, y0 + h / 2 + 4, f"widget {i + 1}", "ts sm", "middle")
        else:
            s += S.rect(x0, y0 + i * (h + 6), w, h, "gbtn", r=4) + S.text(x0 + w / 2, y0 + i * (h + 6) + h / 2 + 4, f"widget {i + 1}", "ts sm", "middle")
    return s


def d_row(x: X, a, v):
    s, (bx, by, bw), h = body_window(x, 150)
    s += S.text(bx, by + 10, "row(): side by side", "ta m sm b")
    s += S.rect(bx - 4, by + 16, bw + 8, 36, "phl2 dash", r=6) + _boxes(bx, by + 22, 3, 80, 24, True)
    s += S.text(bx, by + 72, "column(): top to bottom", "ta m sm b")
    s += _boxes(bx, by + 78, 2, 120, 22, False)
    s += x.part("options.expand", S.text(bx + 136, by + 100, "expand: true", "tp sm b") + S.text(bx + 136, by + 114, "fills the spare room", "tp sm"))
    return s, h


def d_card(x: X, a, v):
    title = opt(a, "title", "Summary")
    s, (bx, by, bw), h = body_window(x, 104)
    s += S.rect(bx, by + 10, bw - 40, 80, "gcard", r=8)
    s += x.part("options.title", S.text(bx + 10, by + 28, str(title), "tp sm2 b"))
    s += S.text(bx + 10, by + 50, "Failed logins: 22", "t sm2") + S.text(bx + 10, by + 70, "Sources: 3", "t sm2")
    s += x.part("options.direction", S.text(bx + bw - 34, by + 104, "direction: column", "tp sm", "end"))
    return s, h


def d_scroll(x: X, a, v):
    hh = int(opt(a, "height", 120) or 120)
    s, (bx, by, bw), h = body_window(x, 100)
    s += S.rect(bx, by + 6, 200, 84, "gfield", r=4)
    for i in range(5):
        s += S.text(bx + 8, by + 22 + i * 16, f"line {i + 1}", "t sm2")
    s += S.rect(bx + 190, by + 10, 6, 76, "gtrack", r=3) + S.rect(bx + 190, by + 10, 6, 30, "gthumb", r=3)
    s += x.part("options.height", S.line(bx + 210, by + 6, bx + 210, by + 90, "pl w2") + S.text(bx + 216, by + 52, f"height: {hh}", "tp sm"))
    return s, h


def d_tabs(x: X, a, v):
    names = val(a, "names", ["Overview", "Alerts", "Raw"])
    s, (bx, by, bw), h = body_window(x, 100)
    tx = bx
    hdr = ""
    for i, nm in enumerate(names):
        w = len(str(nm)) * 7 + 18
        hdr += S.rect(tx, by + 4, w, 22, "gtabon" if i == 0 else "gtab", r=4) + S.text(tx + w / 2, by + 19, str(nm), "t sm2 b" if i == 0 else "ts sm2", "middle")
        tx += w + 3
    s += x.part("names", hdr + S.rect(bx - 3, by + 1, tx - bx + 3, 28, "phl dash", r=6))
    s += S.rect(bx, by + 26, bw, 52, "gcard", r=4) + S.text(bx + 10, by + 48, "tabs.page(\"Overview\") is a container", "ts m sm")
    b, w = bubble(bx, by + 84, "on_change(name)")
    s += x.part("options.on_change", b)
    return s, h + 10


def d_splitter(x: X, a, v):
    direction = opt(a, "direction", "horizontal")
    s, (bx, by, bw), h = body_window(x, 104)
    if direction == "horizontal":
        s += S.rect(bx, by + 6, 120, 84, "gcard", r=4) + S.rect(bx + 128, by + 6, bw - 128, 84, "gcard", r=4)
        s += x.part("options.direction", S.rect(bx + 121, by + 30, 6, 36, "gthumb", r=3) + S.text(bx + 124, by + 102, "drag to resize ↔", "tp sm", "middle"))
        s += S.text(bx + 60, by + 52, "pane 1", "ts sm", "middle") + S.text(bx + 128 + (bw - 128) / 2, by + 52, "pane 2", "ts sm", "middle")
    else:
        s += S.rect(bx, by + 6, bw, 38, "gcard", r=4) + S.rect(bx, by + 52, bw, 38, "gcard", r=4)
        s += x.part("options.direction", S.rect(bx + bw / 2 - 18, by + 45, 36, 6, "gthumb", r=3) + S.text(bx + bw / 2, by + 102, "drag to resize ↕", "tp sm", "middle"))
        s += S.text(bx + bw / 2, by + 30, "pane 1", "ts sm", "middle") + S.text(bx + bw / 2, by + 76, "pane 2", "ts sm", "middle")
    return s, h + 6


def d_tree(x: X, a, v):
    cols = val(a, "columns", ["name", "size"])
    s, (bx, by, bw), h = body_window(x, 112)
    hdr = S.rect(bx, by + 2, bw, 18, "gth", r=3) + "".join(S.text(bx + 8 + i * 150, by + 15, str(c), "t sm2 b") for i, c in enumerate(cols[:2]))
    s += x.part("columns", hdr)
    rows = [(0, "▾ logs", "3 files"), (1, "auth.log", "12 KB"), (1, "access.log", "40 KB"), (0, "▸ data", "2 files")]
    for i, (lvl, nm, sz) in enumerate(rows):
        y = by + 22 + i * 18
        s += S.text(bx + 8 + lvl * 16, y + 13, nm, "t sm2") + S.text(bx + 158, y + 13, sz, "ts m sm")
    s += S.text(bx, by + 108, ".add(parent_id, values) gives each row an id", "ts sm")
    return s, h


def d_chart(x: X, a, v):
    kind = opt(a, "type", "bar")
    data = opt(a, "data", [4, 9, 6, 12])
    labels = opt(a, "labels", ["Mon", "Tue", "Wed", "Thu"])
    s, (bx, by, bw), h = body_window(x, 128)
    top = max([d for d in data if isinstance(d, (int, float))] + [1])
    ch = 80
    pts = []
    body = ""
    if kind == "pie":
        tot = sum(d for d in data if isinstance(d, (int, float))) or 1
        cx, cy, r = bx + 60, by + 50, 42
        ang = -math.pi / 2
        for i, d in enumerate(data):
            a2 = ang + 2 * math.pi * d / tot
            large = 1 if a2 - ang > math.pi else 0
            body += S.path(f"M{cx} {cy} L{S.n(cx + r * math.cos(ang))} {S.n(cy + r * math.sin(ang))} A{r} {r} 0 {large} 1 {S.n(cx + r * math.cos(a2))} {S.n(cy + r * math.sin(a2))}z", f"slice s{i % 4}")
            ang = a2
    else:
        step = (bw - 90) / len(data)
        for i, d in enumerate(data):
            hgt = ch * d / top
            xx = bx + 10 + i * step
            if kind == "line":
                pts.append((xx + step / 2, by + 6 + ch - hgt))
            else:
                body += S.rect(xx + 4, by + 6 + ch - hgt, step - 10, hgt, f"bar s{i % 4}", r=2)
        if pts:
            body += S.path("M" + " L".join(f"{S.n(px)} {S.n(py)}" for px, py in pts), "pl w3 lnchart")
            body += "".join(f'<circle cx="{S.n(px)}" cy="{S.n(py)}" r="3" class="pdot"/>' for px, py in pts)
    s += x.part("options.data", body)
    if kind != "pie":
        step = (bw - 90) / len(data)
        s += x.part("options.labels", "".join(S.text(bx + 10 + i * step + step / 2, by + ch + 20, str(l), "tp sm", "middle") for i, l in enumerate(labels[: len(data)])))
    else:
        s += x.part("options.labels", "".join(S.text(bx + 130, by + 20 + i * 16, str(l), "tp sm") for i, l in enumerate(labels[: len(data)])))
    s += x.part("options.type", tag(bx + bw - 76, by + 4, f"type: \"{kind}\"")[0])
    return s, h


def d_canvas(x: X, a, v):
    cw = val(a, "width", 400)
    chh = val(a, "height", 300)
    s, (bx, by, bw), h = body_window(x, 400 * K * 0.6 + 70)
    k = 0.45 * K * 2
    rect_ = f'<rect x="{bx}" y="{by + 4}" width="{S.n(cw * k / 2)}" height="{S.n(chh * k / 2)}" rx="3" class="gcanvas"' + bind(["width", 0, {"width": k / 2}], ["height", 0, {"height": k / 2}]) + "/>"
    s += rect_
    s += f'<circle cx="{bx + 30}" cy="{by + 30}" r="14" class="slice s1"/>' + S.rect(bx + 54, by + 18, 40, 24, "bar s2", r=2) + S.line(bx + 10, by + 60, bx + 90, by + 48, "pl w2")
    s += x.part("width", f'<g{bind(["translate", [bx, {"width": 0}], [by + 4 + 0, {"height": k / 2}]])} transform="translate({bx} {S.n(by + 4 + chh * k / 2)})">'
                + S.line(0, 8, 0, 8, "pl") + f'<line x1="0" y1="8" x2="{S.n(cw * k / 2)}" y2="8" class="pl w2"{bind(["x2", 0, {"width": k / 2}])}/>'
                + S.text(4, 22, f"width {cw}", "tp sm b", extra=bind(["text", "width {width}"])) + "</g>")
    s += x.part("height", f'<g{bind(["translate", [bx + 8, {"width": k / 2}], [by + 4, {}]])} transform="translate({S.n(bx + 8 + cw * k / 2)} {by + 4})">'
                + f'<line x1="0" y1="0" x2="0" y2="{S.n(chh * k / 2)}" class="pl w2"{bind(["y2", 0, {"height": k / 2}])}/>'
                + S.text(5, 14, f"height {chh}", "tp sm b", extra=bind(["text", "height {height}"])) + "</g>")
    b, w = bubble(bx + 200, by + 4, "on_click(x, y)")
    s += x.part("options", b)
    x.fig.controls.append(Control("width", x.p("width"), lo=100, hi=600, step=10, value=cw))
    x.fig.controls.append(Control("height", x.p("height"), lo=60, hi=320, step=10, value=chh))
    return s, h + 20


def d_slider(x: X, a, v):
    lo, hi = val(a, "min", 0), val(a, "max", 100)
    value = opt(a, "value", 30)
    s, (bx, by, bw), h = body_window(x, 70)
    full = 200
    k = full / max(hi - lo, 1)
    s += S.rect(bx + 30, by + 18, full, 6, "gtrack", r=3)
    s += f'<rect x="{bx + 30}" y="{by + 18}" width="{S.n((value - lo) * k)}" height="6" rx="3" class="pfill k{(x.p("options.value") or 0) % 8}"' + bind(["width", -lo * k, {"value": k}]) + "/>"
    s += x.part("options.value", f'<circle cx="{S.n(bx + 30 + (value - lo) * k)}" cy="{by + 21}" r="8" class="knob"' + bind(["cx", bx + 30 - lo * k, {"value": k}]) + "/>"
                + S.text(bx + 30 + (value - lo) * k, by + 46, str(value), "tp sm b", "middle",
                         extra=bind(["x", bx + 30 - lo * k, {"value": k}], ["text", "{value}"])))
    s += x.part("min", S.text(bx + 22, by + 25, str(lo), "tp m sm2 b", "end") + S.text(bx + 22, by + 60, "min", "tp sm", "end"))
    s += x.part("max", S.text(bx + 38 + full, by + 25, str(hi), "tp m sm2 b") + S.text(bx + 38 + full, by + 60, "max", "tp sm"))
    x.fig.controls.append(Control("value", x.p("options.value"), lo=lo, hi=hi, step=1, value=value))
    return s, h


def d_spinner(x: X, a, v):
    lo, hi = val(a, "min", 1), val(a, "max", 10)
    value = opt(a, "value", 3)
    s, (bx, by, bw), h = body_window(x, 66)
    s += x.part("options.value", field(bx, by + 8, 70, str(value)))
    s += S.rect(bx + 70, by + 8, 18, 22, "gbtn", r=3) + S.path(f"M{bx + 74} {by + 17} l5 -5 5 5 M{bx + 74} {by + 22} l5 5 5 -5", "ln w2")
    s += x.part("min", S.text(bx + 110, by + 18, f"min {lo}", "tp sm b")) + x.part("max", S.text(bx + 110, by + 34, f"max {hi}", "tp sm b"))
    s += x.part("options.step", S.text(bx + 170, by + 26, f"step {opt(a, 'step', 1)}", "tp sm"))
    return s, h


def d_date_picker(x: X, a, v):
    value = opt(a, "value", "2026-03-14")
    s, (bx, by, bw), h = body_window(x, 128)
    s += x.part("options.value", field(bx, by + 4, 110, str(value)) + S.icon("calendar", bx + 88, by + 7, 16, "icp"))
    cal = S.rect(bx + 130, by + 4, 150, 112, "gpop", r=6) + S.text(bx + 205, by + 20, "March 2026", "t sm b", "middle")
    for i in range(28):
        r_, c_ = divmod(i + 6, 7)
        cx_, cy_ = bx + 140 + c_ * 20, by + 34 + r_ * 16
        if i + 1 == 14:
            cal += x.part("options.value", S.rect(cx_ - 2, cy_ - 1, 18, 15, "pb", r=3))
        cal += S.text(cx_ + 7, cy_ + 10, str(i + 1), "t sm", "middle")
    s += cal
    return s, h


def d_color(x: X, a, v):
    value = opt(a, "value", "#2a7de1")
    s, (bx, by, bw), h = body_window(x, 70)
    s += x.part("options.value", f'<rect x="{bx}" y="{by + 8}" width="40" height="24" rx="4" fill="{esc(value)}" class="swatch"/>' + S.text(bx + 48, by + 25, str(value), "tp m sm2"))
    s += S.text(bx, by + 52, "click: the system colour chooser opens", "ts sm")
    b, w = bubble(bx + 150, by + 10, "on_change(colour)")
    s += x.part("options.on_change", b)
    return s, h


def d_link(x: X, a, v):
    text = val(a, "text", "Read the docs")
    url = val(a, "url", "https://code.tanisha.ceo")
    s, (bx, by, bw), h = body_window(x, 76)
    s += x.part("text", S.text(bx, by + 22, text, "tlink sm2") + S.line(bx, by + 25, bx + len(str(text)) * 6.8, by + 25, "lnlink"))
    s += cursor(bx + 40, by + 16)
    s += x.part("url", S.icon("globe", bx + 150, by + 6, 22, "icp") + S.text(bx + 176, by + 22, S.clip(str(url), 20), "tp m sm") + dashed(bx + 110, by + 18, bx + 146, by + 18))
    s += S.text(bx, by + 58, "opens in the web browser", "ts sm")
    return s, h


def d_badge(x: X, a, v):
    text = val(a, "text", "high")
    style = opt(a, "style", "danger")
    s, (bx, by, bw), h = body_window(x, 60)
    w = len(str(text)) * 7 + 18
    s += x.part("text", S.rect(bx, by + 8, w, 20, f"badge b-{style}", r=10) + S.text(bx + w / 2, by + 22, str(text), "tbadge sm b", "middle"))
    s += x.part("options.style", tag(bx + w + 14, by + 9, f"style: \"{style}\"")[0])
    return s, h


def d_clear(x: X, a, v):
    s, (bx, by, bw), h = body_window(x, 70)
    s += S.rect(bx, by + 4, 120, 56, "gcard", r=4) + _boxes(bx + 8, by + 10, 2, 104, 18, False)
    s += S.arrow(bx + 130, by + 32, bx + 170, by + 32) + S.text(bx + 150, by + 26, "clear()", "ta m sm", "middle")
    s += S.rect(bx + 178, by + 4, 120, 56, "gcard dash", r=4) + S.text(bx + 238, by + 36, "empty", "tf sm", "middle")
    return s, h


# ------------------------------------------------------- window geometry
def d_window(x: X, a, v):
    title = val(a, "title", "Log viewer")
    w0, h0 = opt(a, "width", 520), opt(a, "height", 400)
    X0, Y0 = 12, 8
    pw, ph = x.p("options.width"), x.p("options.height")
    wb = bind(["width", 0, {"width": K}])
    frame = f'<rect x="{X0}" y="{Y0}" width="{S.n(w0 * K)}" height="{S.n(h0 * K)}" rx="7" class="win"{bind(["width", 0, {"width": K}], ["height", 0, {"height": K}])}/>'
    frame += f'<rect x="{X0}" y="{Y0}" width="{S.n(w0 * K)}" height="20" rx="7" class="winbar"{wb}/>'
    for i in range(3):
        frame += f'<circle cx="{X0 + 10 + i * 9}" cy="{Y0 + 10}" r="3" class="wdot"/>'
    s = frame
    s += x.part("title", S.text(X0 + w0 * K / 2, Y0 + 14, title, "tp sm b", "middle", bind(["x", X0, {"width": K / 2}])))
    s += S.text(X0 + 10, Y0 + 40, "widgets stack here,", "ts sm") + S.text(X0 + 10, Y0 + 54, "top to bottom", "ts sm")
    # width arrow under the window
    s += x.part("options.width", f'<g{bind(["translate", [X0, {}], [Y0 + 10, {"height": K}]])} transform="translate({X0} {S.n(Y0 + 10 + h0 * K)})">'
                + f'<line x1="0" y1="0" x2="{S.n(w0 * K)}" y2="0" class="pl w2"{bind(["x2", 0, {"width": K}])}/>'
                + S.line(0, -5, 0, 5, "pl w2") + f'<line x1="{S.n(w0 * K)}" y1="-5" x2="{S.n(w0 * K)}" y2="5" class="pl w2"{bind(["x1", 0, {"width": K}], ["x2", 0, {"width": K}])}/>'
                + S.text(w0 * K / 2, 16, f"width {w0} px", "tp sm b", "middle", bind(["x", 0, {"width": K / 2}], ["text", "width {width} px"])) + "</g>")
    s += x.part("options.height", f'<g{bind(["translate", [X0 + 10, {"width": K}], [Y0, {}]])} transform="translate({S.n(X0 + 10 + w0 * K)} {Y0})">'
                + f'<line x1="0" y1="0" x2="0" y2="{S.n(h0 * K)}" class="pl w2"{bind(["y2", 0, {"height": K}])}/>'
                + S.line(-5, 0, 5, 0, "pl w2") + f'<line x1="-5" y1="{S.n(h0 * K)}" x2="5" y2="{S.n(h0 * K)}" class="pl w2"{bind(["y1", 0, {"height": K}], ["y2", 0, {"height": K}])}/>'
                + f'<text x="6" y="{S.n(h0 * K / 2)}" class="tp sm b"{bind(["y", 0, {"height": K / 2}], ["text", "height {height}"])}>height {h0}</text>' + "</g>")
    # resizable grip
    s += x.part("options.resizable", f'<g{bind(["translate", [X0 - 14, {"width": K}], [Y0 - 14, {"height": K}]])} transform="translate({S.n(X0 - 14 + w0 * K)} {S.n(Y0 - 14 + h0 * K)})">'
                + S.path("M4 12 L12 4 M8 12 L12 8", "pl w2") + "</g>")
    s += x.part("options.theme", tag(X0 + 10, Y0 + 66, f"theme: \"{opt(a, 'theme', 'light')}\"")[0])
    x.fig.controls.append(Control("width", pw, lo=200, hi=900, step=10, value=w0, unit=" px"))
    x.fig.controls.append(Control("height", ph, lo=150, hi=560, step=10, value=h0, unit=" px"))
    return s, Y0 + 560 * K + 30


def d_resize(x: X, a, v):
    w0, h0 = val(a, "w", 700), val(a, "h", 360)
    X0, Y0 = 12, 8
    s = f'<rect x="{X0}" y="{Y0}" width="{S.n(520 * K)}" height="{S.n(400 * K)}" rx="7" class="ghostwin"/>'
    s += S.text(X0 + 6, Y0 + 400 * K + 14, "before: 520 × 400", "tf sm")
    wb = bind(["width", 0, {"w": K}])
    s += f'<rect x="{X0}" y="{Y0}" width="{S.n(w0 * K)}" height="{S.n(h0 * K)}" rx="7" class="win wintr"{bind(["width", 0, {"w": K}], ["height", 0, {"h": K}])}/>'
    s += f'<rect x="{X0}" y="{Y0}" width="{S.n(w0 * K)}" height="20" rx="7" class="winbar"{wb}/>'
    for i in range(3):
        s += f'<circle cx="{X0 + 10 + i * 9}" cy="{Y0 + 10}" r="3" class="wdot"/>'
    s += S.text(X0 + w0 * K / 2, Y0 + 14, "My app", "t sm b", "middle", bind(["x", X0, {"w": K / 2}]))
    s += x.part("w", f'<g{bind(["translate", [X0, {}], [Y0 + 30, {"h": K}]])} transform="translate({X0} {S.n(Y0 + 30 + h0 * K)})">'
                + f'<line x1="0" y1="0" x2="{S.n(w0 * K)}" y2="0" class="pl w3"{bind(["x2", 0, {"w": K}])}/>'
                + S.line(0, -6, 0, 6, "pl w2") + f'<line x1="{S.n(w0 * K)}" y1="-6" x2="{S.n(w0 * K)}" y2="6" class="pl w2"{bind(["x1", 0, {"w": K}], ["x2", 0, {"w": K}])}/>'
                + S.text(w0 * K / 2, 17, f"w = {w0} px", "tp sm b", "middle", bind(["x", 0, {"w": K / 2}], ["text", "w = {w} px"])) + "</g>")
    s += x.part("h", f'<g{bind(["translate", [X0 + 10, {"w": K}], [Y0, {}]])} transform="translate({S.n(X0 + 10 + w0 * K)} {Y0})">'
                + f'<line x1="0" y1="0" x2="0" y2="{S.n(h0 * K)}" class="pl w3"{bind(["y2", 0, {"h": K}])}/>'
                + S.line(-6, 0, 6, 0, "pl w2") + f'<line x1="-6" y1="{S.n(h0 * K)}" x2="6" y2="{S.n(h0 * K)}" class="pl w2"{bind(["y1", 0, {"h": K}], ["y2", 0, {"h": K}])}/>'
                + f'<text x="7" y="{S.n(h0 * K / 2)}" class="tp sm b"{bind(["y", 0, {"h": K / 2}], ["text", "h = {h} px"])}>h = {h0} px</text>' + "</g>")
    x.fig.controls.append(Control("w", x.p("w"), lo=200, hi=900, step=10, value=w0, unit=" px"))
    x.fig.controls.append(Control("h", x.p("h"), lo=120, hi=600, step=10, value=h0, unit=" px"))
    return s, Y0 + 600 * K + 50


def d_size(x: X, a, v):
    s = win(12, 8, 520 * K, 400 * K, "My app")
    s += S.line(12, 8 + 400 * K + 10, 12 + 520 * K, 8 + 400 * K + 10, "ln w2") + S.text(12 + 260 * K, 8 + 400 * K + 24, "520", "t sm b", "middle")
    s += S.line(22 + 520 * K, 8, 22 + 520 * K, 8 + 400 * K, "ln w2") + S.text(28 + 520 * K, 8 + 200 * K, "400", "t sm b")
    return s, 8 + 400 * K + 34


def d_center(x: X, a, v):
    s = screen(20, 6, 300, 170)
    s += f'<rect x="30" y="16" width="90" height="60" rx="5" class="ghostwin"/>'
    s += S.line(170, 6, 170, 176, "ln2 dash") + S.line(20, 91, 320, 91, "ln2 dash")
    s += win(125, 61, 90, 60, None)
    s += S.arrow(80, 50, 140, 80)
    return s, 200


def d_fullscreen(x: X, a, v):
    on = val(a, "bool", True)
    if v is not None:
        on = v == "true"
    s = screen(20, 6, 300, 170)
    if on:
        s += x.part("bool", f'<rect x="22" y="8" width="296" height="166" rx="4" class="win"/>' + S.text(170, 96, "fills the whole screen", "tp sm b", "middle"))
    else:
        s += x.part("bool", win(70, 40, 160, 100, "My app") + S.text(150, 160, "a normal window", "tp sm", "middle"))
    return s, 200


def d_always_on_top(x: X, a, v):
    on = (v == "true") if v is not None else bool(val(a, "bool", True))
    s = screen(20, 6, 300, 170)
    other = win(110, 40, 170, 100, "Browser")
    mine = win(50, 60, 150, 90, "My app")
    if on:
        s += other + x.part("bool", mine + S.text(125, 168, "stays in front of other windows", "tp sm", "middle"))
    else:
        s += mine + other + x.part("bool", S.text(170, 168, "other windows can cover it", "tp sm", "middle"))
    return s, 200


def d_title(x: X, a, v):
    t = val(a, "text", "Log viewer — auth.log")
    s = x.part("text", win(12, 8, 316, 80, t, "tp sm b") + S.rect(80, 9, 180, 18, "phl dash", r=5))
    s += S.text(24, 60, "title() with no input gives the current title", "ts sm")
    return s, 96


def d_menu(x: X, a, v):
    s = win(12, 8, 316, 150, "My app")
    s += S.rect(13, 28, 314, 18, "gth", r=0) + S.text(22, 41, "File", "t sm2 b") + S.text(60, 41, "Edit", "ts sm2")
    pop = S.rect(18, 46, 120, 70, "gpop", r=4) + S.text(28, 62, "Open", "t sm2") + S.line(24, 72, 132, 72, "ln2") + S.text(28, 90, "Quit", "t sm2")
    s += x.part("map", pop + S.rect(15, 27, 128, 92, "phl dash", r=6))
    b, w = bubble(160, 54, "\"Open\": fn() { … }")
    s += x.part("map", b) + S.text(160, 92, "\"-\": nil draws a line", "ts sm")
    return s, 166


def d_status(x: X, a, v):
    t = val(a, "text", "Loaded 1,204 lines")
    s = win(12, 8, 316, 110, "My app")
    s += S.text(24, 48, "… widgets …", "tf sm")
    s += x.part("text", S.rect(13, 96, 314, 20, "gth", r=0) + S.text(22, 110, t, "tp sm2") + S.rect(14, 94, 312, 24, "phl dash", r=4))
    return s, 126


def d_timer(x: X, a, v, once=False):
    ms = val(a, "ms", 1000)
    if v is not None:
        ms = int(v)
    s = S.arrow(10, 40, S.W - 6, 40, "ln w2") + S.text(S.W - 6, 58, "time →", "tf sm", "end")
    k = (S.W - 40) / 4000
    s += S.text(10, 58, "0", "tf sm")
    t = ms
    i = 0
    marks = ""
    while t <= 4000 and i < 20:
        xx = 10 + t * k
        marks += f'<circle cx="{S.n(xx)}" cy="40" r="5" class="pdot"/>'
        if i < 4:
            marks += S.text(xx, 28, f"{t} ms", "tp sm", "middle")
        if once:
            break
        t += ms
        i += 1
    s += x.part("ms", marks)
    b, w = bubble(10, 72, "fn() runs at each dot" if not once else "fn() runs once, then stops")
    s += x.part("fn", b)
    s += S.text(10, 112, "the timer gives back .stop() to cancel it", "ts sm")
    return s, 120


def d_every(x, a, v):
    return d_timer(x, a, v)


def d_after(x, a, v):
    return d_timer(x, a, v, once=True)


def d_key(x: X, a, v):
    keys = str(val(a, "keys", "Ctrl+S"))
    s = win(12, 8, 316, 70, "My app")
    kx = 30
    caps = ""
    for i, k in enumerate(keys.split("+")):
        if i:
            caps += S.text(kx + 2, 52, "+", "tp sm b")
            kx += 12
        c, w = keycap(kx, 36, k, "keyp")
        caps += c
        kx += w + 4
    s += x.part("keys", caps)
    s += S.arrow(kx + 6, 47, kx + 40, 47)
    b, w = bubble(kx + 46, 37, "fn() { save() }")
    s += x.part("fn", b)
    s += S.text(24, 100, "\"Mod\" means Cmd on a Mac and Ctrl elsewhere", "ts sm")
    return s, 108


def d_run(x: X, a, v):
    s = S.arrow(10, 30, S.W - 8, 30, "ln w2") + S.text(S.W - 8, 46, "your program →", "tf sm", "end")
    s += f'<circle cx="60" cy="30" r="6" class="pdot"/>' + S.text(60, 18, "run()", "ta m sm b", "middle")
    s += S.rect(60, 24, 200, 12, "lane", r=6) + S.text(160, 60, "waits here while the window is open", "ts sm", "middle")
    s += f'<circle cx="260" cy="30" r="6" class="dot"/>' + S.text(260, 18, "closed", "ts sm", "middle")
    s += win(70, 70, 180, 70, "My app") + S.text(160, 120, "shown, events handled", "ts sm", "middle")
    return s, 150


def d_close(x: X, a, v):
    s = f'<rect x="20" y="10" width="160" height="90" rx="7" class="ghostwin"/>'
    s += S.icon("cross", 88, 44, 24, "ic")
    s += S.arrow(190, 55, 230, 55) + S.text(236, 52, "window gone;", "ts sm") + S.text(236, 66, "run() returns", "ts sm")
    return s, 110


def d_is_open(x: X, a, v):
    s = win(12, 8, 150, 80, "My app") + S.arrow(170, 48, 210, 48)
    b, _ = S.v_bool(True, 216, 36)
    return s + b, 96


def d_icon(x: X, a, v):
    path = val(a, "path", "icon.png")
    s = S.rect(30, 70, 280, 44, "gth", r=12)
    for i in range(5):
        s += S.rect(46 + i * 52, 78, 30, 30, "gbtn", r=7)
    s += x.part("path", S.rect(150, 76, 34, 34, "pb", r=8) + S.icon("spark", 156, 82, 22, "icp") + S.text(167, 60, str(path), "tp m sm", "middle") + dashed(167, 64, 167, 74))
    s += S.text(170, 132, "the picture in the Dock / taskbar and title bar", "ts sm", "middle")
    return s, 140


def d_is_fullscreen(x: X, a, v):
    s = screen(20, 6, 140, 80) + f'<rect x="22" y="8" width="136" height="76" rx="4" class="win"/>'
    s += S.arrow(170, 46, 210, 46)
    b, _ = S.v_bool(True, 216, 34)
    return s + b, 110


def d_theme_get(x: X, a, v):
    s = win(12, 8, 150, 70, "My app") + S.arrow(170, 43, 210, 43) + S.text(216, 48, '"light"', "t m b")
    return s, 86


def d_on_close(x: X, a, v):
    s = win(12, 8, 180, 80, "My app") + cursor(26, 6)
    s += f'<circle cx="22" cy="18" r="6" class="phl"/>'
    b, w = bubble(206, 14, "fn() { … }")
    s += x.part("fn", b + dashed(32, 18, 202, 24))
    s += x.part("fn", S.text(206, 52, "give back false", "tp sm b") + S.text(206, 66, "to keep it open", "tp sm"))
    return s, 96


def d_on_key(x: X, a, v):
    s = ""
    kx = 10
    for k in ["a", "b", "Ctrl+S", "Enter"]:
        c, w = keycap(kx, 10, k)
        s += c
        kx += w + 6
    s += S.arrow(kx, 21, kx + 20, 21)
    b, w = bubble(10, 48, "fn(key) gets \"a\", \"b\", \"Ctrl+S\" …")
    s += x.part("fn", b)
    return s, 76


def d_on_drop(x: X, a, v):
    s = win(12, 30, 200, 100, "My app")
    for i in range(3):
        s += S.icon("file", 230 + i * 10, 4 + i * 8, 22, "ic")
    s += S.arrow(240, 44, 160, 80) + S.text(60, 90, "drop files here", "ts sm")
    b, w = bubble(12, 140, "fn(paths) gets [\"/x/a.log\", …]")
    s += x.part("fn", b)
    return s, 168


def d_tray(x: X, a, v):
    s = screen(20, 6, 300, 170)
    s += f'<rect x="60" y="40" width="140" height="90" rx="7" class="ghostwin"/>' + S.text(130, 90, "minimize()", "tf m sm", "middle")
    title = opt(a, "title", "TB")
    s += x.part("options.title", S.rect(240, 14, 70, 20, "pb", r=10) + S.text(275, 28, str(title), "tp sm b", "middle"))
    pop = S.rect(230, 38, 84, 50, "gpop", r=4) + S.text(240, 56, "Show", "t sm2") + S.text(240, 76, "Quit", "t sm2")
    s += x.part("menu", pop)
    return s, 200


def d_hide(x: X, a, v):
    s = f'<rect x="20" y="10" width="150" height="80" rx="7" class="ghostwin"/>' + S.text(95, 56, "hidden (still running)", "tf sm", "middle")
    return s, 100


def d_show(x: X, a, v):
    s = win(20, 10, 150, 80, "My app") + S.text(95, 60, "visible again", "ts sm", "middle")
    return s, 100


def d_run_in_background(x: X, a, v):
    s = S.text(0, 14, "window thread", "ts sm b") + S.rect(90, 4, 240, 14, "lane", r=7)
    s += S.text(0, 50, "other thread", "ts sm b") + S.rect(90, 40, 240, 14, "lane", r=7)
    s += x.part("fn", S.rect(110, 41, 150, 12, "pb", r=6) + S.text(116, 51, "fn(): slow work", "tp sm"))
    s += S.text(96, 30, "stays responsive →", "tf sm")
    s += x.part("on_done", S.path("M260 47 C280 47 280 11 296 11", "pl dash") + S.rect(270, 60, 64, 18, "pb", r=9) + S.text(276, 73, "on_done", "tp m sm"))
    s += x.part("on_error", S.text(96, 92, "on_error(message) if it fails", "tp sm"))
    return s, 100


# ---------------------------------------------------------- module funcs
def dialog(x: X, a, msg_name, title_name, buttons, extra=""):
    msg = val(a, msg_name, "Saved!")
    title = val(a, title_name, "TB") if title_name else None
    s = f'<rect x="10" y="4" width="320" height="140" rx="8" class="dim"/>'
    s += x.part(title_name, S.rect(60, 20, 220, 104, "gpop", r=8) + S.text(170, 40, str(title or ""), "tp sm b", "middle")) if title_name else S.rect(60, 20, 220, 104, "gpop", r=8)
    s += x.part(msg_name, S.text(170, 66, S.clip(str(msg), 32), "tp sm2", "middle"))
    bx = 280 - 12
    for i, b in enumerate(reversed(buttons)):
        w = len(b) * 7 + 18
        bx -= w
        s += btn(bx, 92, w, b, primary=(i == 0))
        bx -= 6
    return s + extra, 150


def d_alert(x, a, v):
    return dialog(x, a, "message", "title", ["OK"])


def d_confirm(x, a, v):
    return dialog(x, a, "question", "title", ["No", "Yes"])


def d_ask(x, a, v):
    s, h = dialog(x, a, "question", None, ["Cancel", "OK"])
    s += x.part("default", field(80, 74, 180, str(val(a, "default", "admin")), h=16).replace('height="16"', 'height="16"'))
    return s, h


def d_pick(x: X, a, v, kind="open"):
    s = f'<rect x="10" y="4" width="320" height="160" rx="8" class="dim"/>'
    s += S.rect(30, 16, 280, 136, "gpop", r=8)
    s += x.part("title", S.text(170, 34, str(val(a, "title", {"open": "Open a log", "save": "Save report", "folder": "Choose a folder"}[kind])), "tp sm b", "middle"))
    for i, (ic, nm) in enumerate([("folder", "logs"), ("file", "auth.log"), ("file", "access.log")]):
        y = 44 + i * 22
        if i == 1:
            s += S.rect(40, y, 260, 20, "gsel", r=3)
        s += S.icon(ic, 46, y + 3, 14, "ic") + S.text(66, y + 14, nm, "t sm2")
    if kind == "save":
        s += x.part("name", field(40, 112, 150, str(val(a, "name", "report.html")), h=18))
    if kind in ("open", "save"):
        f_ = val(a, "filters", [".log"])
        lab = ", ".join(f_) if isinstance(f_, list) else ", ".join(f"{k}: {v_}" for k, v_ in f_.items()) if isinstance(f_, dict) else str(f_)
        s += x.part("filters", tag(200 if kind == "save" else 40, 112, S.clip(lab, 18))[0])
    s += btn(250, 128, 50, "OK" if kind != "open" else "Open", True, h=18)
    return s, 168


def d_open_file(x, a, v):
    return d_pick(x, a, v, "open")


def d_save_file(x, a, v):
    return d_pick(x, a, v, "save")


def d_pick_folder(x, a, v):
    return d_pick(x, a, v, "folder")


def d_notify(x: X, a, v):
    s = screen(20, 6, 300, 150)
    s += S.rect(170, 16, 140, 46, "gpop", r=8) + S.icon("bell", 178, 24, 16, "ic")
    s += x.part("title", S.text(200, 34, S.clip(str(val(a, "title", "Scan done")), 14), "tp sm b"))
    s += x.part("text", S.text(200, 52, S.clip(str(val(a, "text", "3 alerts")), 14), "tp sm"))
    return s, 180


def d_clipboard(x: X, a, v, paste=False):
    t = val(a, "text", "203.0.113.42")
    s = S.rect(110, 10, 120, 60, "gcard", r=8) + S.icon("doc", 120, 18, 22, "ic") + S.text(150, 34, "clipboard", "ts sm b")
    if paste:
        s += S.text(120, 56, '"203.0.113.42"', "t m sm")
        s += S.arrow(230, 40, 280, 40) + S.text(284, 44, "text", "t sm b")
    else:
        s += x.part("text", S.rect(0, 26, 92, 26, "pb", r=6) + S.text(6, 43, S.clip(str(t), 12), "tp m sm") + S.arrow(94, 39, 108, 39, "pl", "pah"))
        s += S.text(170, 90, "ready to paste in any program", "ts sm", "middle")
    return s, 100


def d_copy(x, a, v):
    return d_clipboard(x, a, v)


def d_paste(x, a, v):
    return d_clipboard(x, a, v, paste=True)


def d_available(x: X, a, v):
    s = S.icon("laptop", 20, 10, 34, "ic") + S.text(66, 24, "TB app on a computer", "t sm") + S.text(66, 40, "→ true", "t m b")
    s += S.icon("globe", 20, 56, 34, "ic") + S.text(66, 70, "browser playground", "t sm") + S.text(66, 86, "→ false", "t m b")
    return s, 100


def d_theme_set(x: X, a, v):
    name = (v or val(a, "name", "dark"))
    s = x.part("name", win(12, 8, 170, 90, f"theme: {name}", "tp sm b", extra_cls=" windark" if name == "dark" else ""))
    s += S.text(200, 40, "set it before", "ts sm") + S.text(200, 56, "gui.window()", "ta m sm b")
    return s, 106


def d_system_theme(x: X, a, v):
    s = S.icon("gear", 20, 16, 30, "ic") + S.text(60, 36, "computer set to dark mode", "t sm") + S.arrow(60, 50, 120, 50) + S.text(126, 54, '"dark"', "t m b")
    return s, 70


def d_accent(x: X, a, v):
    c = val(a, "colour", "#2a7de1")
    s = win(12, 8, 200, 90, "My app")
    s += x.part("colour", f'<rect x="28" y="40" width="80" height="22" rx="5" fill="{esc(c)}" class="swatch"/>' + S.text(68, 55, "Save", "tbtnp sm2 b", "middle")
                + S.text(230, 50, str(c), "tp m sm2"))
    s += f'<rect x="120" y="44" width="70" height="6" rx="3" fill="{esc(c)}" class="swatch"/>'
    return s, 106


def d_scale(x: X, a, v):
    f = val(a, "factor", 1.5)
    s = f'<g{bind(["scale", 20, 10, {"factor": 1}])} transform="translate(20 10) scale({f})">' + win(0, 0, 100, 50, "My app") + btn(10, 26, 50, "OK", True, h=16) + "</g>"
    s += x.part("factor", S.text(S.W - 8, 20, f"factor {f}", "tp sm b", "end", bind(["text", "factor {factor}"])))
    x.fig.controls.append(Control("factor", x.p("factor"), lo=0.8, hi=2, step=0.1, value=f, unit="×"))
    return s, 120


def d_on_main(x: X, a, v):
    s = S.text(0, 14, "other thread", "ts sm b") + S.rect(90, 4, 240, 14, "lane", r=7)
    s += S.text(0, 54, "window thread", "ts sm b") + S.rect(90, 44, 240, 14, "lane", r=7)
    s += x.part("fn", S.rect(160, 45, 110, 12, "pb", r=6) + S.text(166, 55, "fn(): update widgets", "tp sm") + S.arrow(150, 18, 170, 43, "pl", "pah"))
    return s, 70


DRAW = {k[2:]: v for k, v in globals().items() if k.startswith("d_") and callable(v)}


def t_gui(fig: Fig):
    spec = fig.spec
    args = dict(spec.get("args", {}))
    draw = DRAW[spec.get("draw", fig.name)]
    vary = spec.get("vary")
    values = vary["values"] if vary else [None]
    default = vary.get("default", values[0]) if vary else None
    bodies, maxh = [], 0
    for value in values:
        a = dict(args)
        if vary:
            pname = vary["param"]
            if any(q.name == pname for q in fig.params):
                a[pname] = value
            else:   # an option key: put it inside options
                o = arg_value(a.get("options", "{}"))
                o = dict(o) if isinstance(o, dict) else {}
                av = arg_value(value)
                o[pname] = value if av is S.NONE or isinstance(av, Opaque) else av
                a["options"] = literal(o)
        if vary and fig.controls:
            fig.controls = []   # drawings add their sliders once
        body, h = draw(X(fig, a), a, value)
        if vary:
            body = f'<g data-fv-variant="{esc(vary["param"])}:{esc(value)}"{"" if value == default else " display=\"none\""}>{body}</g>'
        bodies.append(body)
        maxh = max(maxh, h)
    y = maxh + 6
    ret = returns_of(fig.signature)
    res_txt = spec.get("static")
    if res_txt is not None:
        try:
            rv = parse_literal(res_txt)
        except ValueError:
            rv = Opaque(res_txt)
        b, bh = S.block(0, y, S.W, label="gives back" + (f" · {ret}" if ret else ""), kind="out", value=rv,
                        icon_name=None, sub=spec.get("result_note"))
        bodies.append(b)
        y += bh
    fig.default_legend(used=set(args) | set(spec.get("used", [])) | ({vary["param"]} if vary else set()))
    if vary:
        p = fig.p(vary["param"])
        if p is None:
            p = X(fig, args).p("options." + vary["param"])
        fig.controls.insert(0, Control(vary["param"], p if p is not None else 0, values=values, value=default, labels=vary.get("labels")))
    fig.call_html = call_line(spec.get("call") or fig.call_expr(args), res_txt, True)
    if spec.get("call_tpl"):   # the call follows the sliders
        fig.call_html = fig.call_html.replace("<code>", "<code" + bind(["text", spec["call_tpl"]]) + ">", 1)
    fig.real = False
    fig.note = spec.get("note") or "Illustration: windows need the TB app on a computer, so this is drawn from what the function does."
    fig.draw("".join(bodies), y, spec.get("alt") or f"{fig.qual()}: what it does to the window")


TEMPLATES = {"gui": t_gui}
