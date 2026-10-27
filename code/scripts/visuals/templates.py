"""Reusable picture templates. Each takes a Fig (with its spec) and fills in
fig.svg, fig.legend, fig.controls and fig.call_html.

Templates (the "t" key of a spec):
  flow        inputs -> function -> result, top to bottom; nodes can be
              parameters, plain boxes with icons, arrows, side-by-side rows
              and the real result. The default for every function.
  list_op     a row of items -> the operation -> the new row, with lines
              showing where each item went (filter, sort, slice, map...)
  net         your computer -> host:port (request, response, timeout clock)
  lanes       work spread over worker lanes (parallel map/each/run)
  threshold   counts per source against a threshold line (detections)
  timeline    moments on a time axis (time, datetime)
  value       a library value (constant)
  generic     inputs (with their roles) -> function -> what kind comes back
The GUI templates live in gui_draw.py.
"""

from __future__ import annotations

import json
import math
import re

from . import svg as S
from .core import Control, Fig, arg_value, call_line, esc, evaluate, returns_of, role_for
from .runner import Bytes, Opaque, literal, parse_literal

RESULT_ICON = {"text": "doc", "list": "list", "map": "db", "bool": "check", "number": "chart", "bytes": "cpu",
               "nil": None, "value": "spark", "records": "list"}


def _sub(src: str, args: dict) -> str:
    return re.sub(r"\$(\w+)", lambda m: args.get(m.group(1), m.group(0)), src)


def _variants(fig: Fig):
    vary = fig.spec.get("vary")
    if not vary:
        return None, [None]
    return vary["param"], list(vary["values"])


def _wrap_variant(param, val, body, default, h=None, heights=None):
    """One slider position's drawing. heights (a dict) collects each one's
    height so the picture can take the height of the one on show."""
    if heights is not None and h is not None:
        heights[val] = h
    if param is None:
        return body
    hide = "" if val == default else ' display="none"'
    hh = f' data-fv-h="{S.n(math.ceil(h + 2))}"' if h is not None else ""
    return f'<g data-fv-variant="{esc(param)}:{esc(val)}"{hide}{hh}>{body}</g>'


def _add_vary_control(fig: Fig, param, values, default):
    p = fig.p(param)
    labels = fig.spec["vary"].get("labels")
    fig.controls.append(Control(param, p if p is not None else 0, values=values, value=default, labels=labels,
                                unit=fig.spec["vary"].get("unit", "")))


def _shown(v):
    """How a result reads in the call line."""
    if isinstance(v, Opaque):
        return v.text
    return literal(v)


# --------------------------------------------------------------- marking
def _text_of(src):
    v = arg_value(src) if isinstance(src, str) else src
    return v if isinstance(v, str) else (None if v is S.NONE else (literal(v) if not isinstance(v, (Bytes,)) else None))


def _find_all(hay: str, needle: str):
    out, i = [], 0
    if not needle:
        return out
    while True:
        j = hay.find(needle, i)
        if j < 0:
            return out
        out.append(j)
        i = j + len(needle)


def out_marks(fig: Fig, args: dict, out_text: str) -> dict:
    marks = {}
    spec = fig.spec
    for name in spec.get("mark_out", []):
        p = fig.p(name)
        t = _text_of(args.get(name)) if name in args else None
        if t is None and name == "fill":
            t = " "
        if p is None or not t:
            continue
        for j in _find_all(out_text, t):
            for k in range(j, j + len(t)):
                marks.setdefault(k, p)
    pad = spec.get("pad")
    if pad:
        p = fig.p(pad)
        base = _text_of(args.get(spec.get("pad_of", "value"))) if spec.get("pad_of", "value") in args else None
        if fig.receiver is not None and base is None:
            base = _text_of(fig.receiver)
        if base is not None and p is not None:
            base = str(base)
            j = out_text.find(base)
            if j >= 0:
                for k in range(len(out_text)):
                    if k < j or k >= j + len(base):
                        marks[k] = p
    return marks


def in_marks(fig: Fig, name: str, in_text: str, out_v) -> dict:
    """Mark the part of an input that ended up in the result."""
    if name not in fig.spec.get("trace", []) or not isinstance(out_v, str) or not out_v:
        return {}
    j = in_text.find(out_v)
    return {k: "r" for k in range(j, j + len(out_v))} if j >= 0 else {}


# ------------------------------------------------------------------ flow
def _param_block(fig: Fig, name: str, args: dict, node: dict, out_v=None):
    p = fig.p(name)
    prm = next((q for q in fig.params if q.name == name), None)
    label = node.get("label") or (name + ("?" if prm and prm.optional else ""))
    spec_ = dict(label=label, p=p, icon_name=node.get("icon"))
    if node.get("lines") is not None:
        spec_["lines"] = node["lines"]
        return spec_
    if name in args:
        src = args[name]
        v = arg_value(src)
        mode = node.get("mode") or fig.spec.get("modes", {}).get(name)
        if v is S.NONE:
            spec_["lines"] = [src]
        else:
            spec_["value"] = v
            if isinstance(v, str):
                mode = mode or "auto-cells"
                spec_["mode"] = mode
                if mode in ("cells", "auto-cells"):
                    marks = in_marks(fig, name, v, out_v)
                    other = fig.spec.get("find_in", {}).get(name)
                    if other and other in args:
                        needle = _text_of(args[other])
                        po = fig.p(other)
                        for j in _find_all(v, needle or ""):
                            for k in range(j, j + len(needle)):
                                marks[k] = po
                        if marks and mode == "auto-cells" and len(v) <= 60:
                            spec_["mode"] = "cells"
                    spec_["marks"] = marks
            elif isinstance(v, Bytes) and fig.spec.get("bytes_read", {}).get("param") == name:
                spec_["marks"] = _bytes_marks(fig, args, v)
            else:
                spec_["mode"] = mode
        if node.get("src") or (v is not S.NONE and fig.spec.get("show_src")):
            spec_["src"] = src
        return spec_
    role = fig.spec.get("roles", {}).get(name)
    defaults = fig.spec.get("defaults", {})
    if node.get("role") or fig.spec.get("show_roles"):
        spec_["lines"] = S.wrap_chars(role_for(name, fig.spec.get("roles")), 30)[:3]
        spec_["kind"] = "plain-sans" if p is None else "in"
        return spec_
    spec_["kind"] = "ghost"
    spec_["label"] = label
    if name in defaults:
        spec_["lines"] = [f"left out: {defaults[name]}"]
    else:
        spec_["lines"] = ["left out" + (f" ({role})" if role and len(role) < 22 else "")]
    spec_["p"] = p
    return spec_


def _bytes_marks(fig, args, v: Bytes):
    br = fig.spec["bytes_read"]
    off = arg_value(args.get("offset", "0"))
    off = off if isinstance(off, int) else 0
    nbytes = br.get("n", 1)
    return {k: "r" for k in range(off, min(off + nbytes, len(v.data)))}


def _result_block(fig: Fig, ok, v, node: dict, args: dict):
    ret = returns_of(fig.signature)
    label = node.get("label") or ("result" + (f" · {ret}" if ret and len(ret) < 18 else ""))
    if not ok and v is None:   # not run (needs the desktop app or the network) and no example result
        first = (ret.split()[0].strip(",") if ret else "")
        return dict(label="gives back", kind="out", icon_name=node.get("icon") or RESULT_ICON.get(first, "spark"),
                    lines=S.wrap_chars(node.get("lines_text") or fig.spec.get("gives") or (ret or "nothing; it does its job"), 34)[:4])
    if not ok:
        msg = v.text if isinstance(v, Opaque) else str(v)
        return dict(label="stops with an error", kind="out", icon_name="alert", lines=S.wrap_chars(msg, 34)[:4])
    mode = node.get("mode") or fig.spec.get("mode")
    b = dict(label=label, kind="out", icon_name=node.get("icon"))
    if node.get("lines") is not None:
        b["lines"] = node["lines"]
        return b
    if isinstance(v, str):
        mode = mode or "auto-cells"
        marks = out_marks(fig, args, v)
        if marks and mode == "auto-cells":
            mode = "cells"
        br = fig.spec.get("bracket")
        if br and fig.p(br) is not None and mode in ("cells", "auto-cells"):
            marks["_bracket"] = (fig.p(br), f"{br} = {len(v)}")
            mode = "cells"
        b["marks"] = marks
    b["value"] = _pick(fig, v)
    b["mode"] = mode
    if node.get("sub"):
        b["sub"] = node["sub"]
    return b


def _pick(fig, v):
    keys = fig.spec.get("keys")
    if keys and isinstance(v, dict):
        return {k: v[k] for k in keys if k in v}
    if keys and isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
        return [{k: i[k] for k in keys if k in i} for i in v]
    if fig.spec.get("first") and isinstance(v, list):
        return v[: fig.spec["first"]]
    return v


def _box(node):
    return dict(label=node.get("box"), icon_name=node.get("icon"), lines=node.get("lines", []),
                kind=node.get("kind", "plain-sans"), p=node.get("_p"))


def default_nodes(fig: Fig):
    nodes = []
    if fig.spec.get("recv_box", True) and fig.receiver is not None:
        nodes.append({"recv": True})
    nodes += [{"param": prm.name} for prm in fig.params if not prm.variadic or prm.name in fig.spec.get("args", {})]
    row = {"row": nodes} if nodes else None
    out = [row] if row else []
    out += [{"arrow": True}, {"result": True}]
    return out


def _build_items(fig: Fig, nodes, args, ok, v):
    items = []
    for node in nodes:
        if "row" in node:
            blocks = [_node_block(fig, sub, args, ok, v) for sub in node["row"]]
            items.append(S.Row(*[b for b in blocks if b], cols=node.get("cols", 2)))
        elif "arrow" in node:
            lab = node["arrow"] if isinstance(node["arrow"], str) else fig.qual()
            p = fig.p(node["p"]) if node.get("p") else None
            items.append(S.Arrow(lab, p=p, note=node.get("note"), back=node.get("back", False)))
        elif "gap" in node:
            items.append(node["gap"])
        else:
            b = _node_block(fig, node, args, ok, v)
            if b:
                items.append(S.Row(b, cols=1))
    return items


def _node_block(fig, node, args, ok, v):
    if "param" in node:
        return _param_block(fig, node["param"], args, node, v if ok else None)
    if "result" in node:
        return _result_block(fig, ok, v, node, args)
    if "recv" in node:
        rv = arg_value(fig.receiver)
        lab = fig.spec.get("recv_label", "the value")
        if rv is S.NONE:
            return dict(label=lab, lines=[fig.receiver], kind="plain")
        b = dict(label=lab, value=rv, kind="plain")
        if isinstance(rv, str):
            b["mode"] = "auto-cells"
        return b
    if "box" in node:
        b = _box(node)
        if node.get("p"):
            b["p"] = fig.p(node["p"])
            b["kind"] = "in"
        return b
    return None


def t_flow(fig: Fig):
    spec = fig.spec
    args = dict(spec.get("args", {}))
    param, values = _variants(fig)
    default = spec.get("vary", {}).get("default", values[0]) if param else None
    bodies, calls, maxh = [], [], 0
    heights = {}
    real_any = False
    for val in values:
        a = dict(args)
        if param:
            a[param] = val
        override = {"call": _sub(spec["call"], a)} if spec.get("call") else None
        ok, v, txt, real = evaluate(fig, a, override=override)
        real_any = real_any or real
        nodes = spec.get("nodes") or default_nodes(fig)
        items = _build_items(fig, nodes, a, ok, v)
        body, h = S.flow(items)
        maxh = max(maxh, h)
        bodies.append(_wrap_variant(param, val, body, default, h, heights))
        expr = _sub(spec["show_call"], a) if spec.get("show_call") else ((override or {}).get("call") or fig.call_expr(a))
        shown = _shown(v) if ok else (v.text if isinstance(v, Opaque) else "")
        if spec.get("hide_result_text"):
            shown = None
        calls.append(call_line(expr, shown, ok, f"{param}:{val}" if param else None, hidden=bool(param) and val != default))
    fig.real = real_any and "static" not in spec and spec.get("run") is not False
    fig.note = spec.get("note", "")
    fig.call_html = "".join(calls)
    fig.default_legend(used=set(args) | ({param} if param else set()) | set(spec.get("used", [])))
    if param:
        _add_vary_control(fig, param, values, default)
    fig.draw("".join(bodies), heights.get(default, maxh), spec.get("alt") or f"{fig.qual()}: inputs flowing into the function and the result")


# --------------------------------------------------------------- generic
def doc_example(fig: Fig):
    """A call written in the doc string, like pad_left(7, 3, "0")."""
    text = fig.doc
    pat = re.compile(r"(?<![\w.])(?:" + re.escape(fig.owner or "") + r"\.)?" + re.escape(fig.name) + r"\(")
    for m in pat.finditer(text):
        i = m.end()
        depth, j, quote = 1, i, None
        while j < len(text) and depth:
            ch = text[j]
            if quote:
                if ch == "\\":
                    j += 1
                elif ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            j += 1
        if depth == 0:
            inner = text[i:j - 1]
            if inner.strip():
                yield inner


# Doc-string examples that would touch the network, other programs or the
# real disk are never run while building the site.
NO_AUTO_RUN = {"net", "os", "dns", "http", "intel", "tls", "websocket", "server", "parallel", "gui", "siem"}
NO_AUTO_FNS = {"time.sleep", "fs.remove", "fs.watch", "fs.watch_stop", "fs.home", "fs.cwd", "test.skip"}


def t_generic(fig: Fig):
    auto = fig.lib not in NO_AUTO_RUN and f"{fig.lib}.{fig.name}" not in NO_AUTO_FNS
    if auto and not fig.desktop and fig.spec.get("run", True) is not False:
        for inner in doc_example(fig):
            expr = f"{fig.qual()}({inner})"
            ok, v, txt, real = evaluate(fig, {}, override={"call": expr})
            if ok:
                fig.spec = dict(fig.spec, call=expr, used=[p.name for p in fig.params][: inner.count(",") + 1])
                return t_flow(fig)
    ret = returns_of(fig.signature) or "nothing (it does its job)"
    blocks = [dict(label=p.name + ("?" if p.optional else ""), p=i,
                   lines=S.wrap_chars(role_for(p.name, fig.spec.get("roles")), 30)[:3],
                   kind="in", icon_name=None)
              for i, p in enumerate(fig.params)]
    items = []
    if blocks:
        items.append(S.Row(*blocks))
    items.append(S.Arrow(fig.qual()))
    first = ret.split()[0].strip(",") if ret else ""
    items.append(S.Row(dict(label="gives back", kind="out", icon_name=RESULT_ICON.get(first, "spark"),
                            lines=S.wrap_chars(ret, 26)[:3])))
    body, h = S.flow(items)
    fig.default_legend()
    args = ", ".join(p.name for p in fig.params)
    fig.call_html = call_line(f"{fig.qual()}({args})", None)
    fig.draw(body, h, f"{fig.qual()}: what goes in and what comes back")


# --------------------------------------------------------------- list_op
def chips_row(items, x0, y0, w, part_of=None, max_items=12):
    """Like svg.v_list but also gives each chip's centre."""
    out, pos = [], []
    cx, cy = x0, y0
    for i, it in enumerate(items[:max_items]):
        lab = S.short(it, 14)
        cw = len(lab) * S.CW + 12
        if cx + cw > x0 + w and cx > x0:
            cx, cy = x0, cy + 28
        p = part_of(i) if part_of else None
        chip = S.rect(cx, cy, cw, 22, "cl" if p is None else ("rb" if p == "r" else "pb"), r=5)
        chip += S.text(cx + 6, cy + 15, lab, "t m" if p in (None, "r") else "tp m")
        out.append(S.part(p, chip) if isinstance(p, int) else chip)
        pos.append((cx + cw / 2, cy, cy + 22, cx, cw))
        cx += cw + 6
    if len(items) > max_items:
        out.append(S.text(cx, cy + 15, f"+{len(items) - max_items}", "tf sm"))
    if not items:
        out.append(S.text(x0, cy + 15, "[] empty", "tf m"))
    return "".join(out), (cy - y0 + 22), pos


def t_list_op(fig: Fig):
    spec = fig.spec
    args = dict(spec.get("args", {}))
    param, values = _variants(fig)
    default = spec.get("vary", {}).get("default", values[0]) if param else None
    recv_v = arg_value(fig.receiver)
    items_in = recv_v if isinstance(recv_v, list) else (list(range(*recv_v)) if False else [])
    if isinstance(recv_v, str):
        items_in = list(recv_v)
    bodies, calls, maxh = [], [], 0
    heights = {}
    real_any = False
    for val in values:
        a = dict(args)
        if param:
            a[param] = val
        ok, v, txt, real = evaluate(fig, a, override={"call": _sub(spec["call"], a)} if spec.get("call") else None)
        real_any = real_any or real
        out, y = [], 0
        # the list going in
        w = S.W
        lab = spec.get("recv_label", "the list")
        sel = _selected(fig, a, items_in, v, ok)
        in_svg, in_h, in_pos = chips_row(items_in, 10, y + 22, w - 20,
                                         part_of=(lambda i: "r" if i in sel["kept"] else None))
        extra_h = 12 if any(nm == "end" for nm, _ in sel.get("markers", [])) else 0
        out.append(S.rect(0, y, w, in_h + 32 + extra_h, "bx", r=7) + S.text(9, y + 14, lab, "ts sm b") + in_svg)
        # markers for start/end style params
        for name, idx in sel.get("markers", []):
            p = fig.p(name)
            if p is None or not in_pos:
                continue
            idx = max(0, min(idx, len(in_pos)))
            if idx < len(in_pos):
                mx = in_pos[idx][3] - 3
                my = in_pos[idx][1]
            else:
                mx = in_pos[-1][3] + in_pos[-1][4] + 3
                my = in_pos[-1][1]
            below = name == "end"
            out.append(S.part(p, S.line(mx, my - 4, mx, my + 26, "pl w2") +
                              S.text(mx, my + 38 if below else my - 7, f"{name} {idx}", "tp sm b", "middle")))
        y += in_h + 32 + extra_h
        # the operation, with fn-like params as pills
        ay = y + 4
        pills = []
        cx = S.W / 2
        out.append(S.arrow(cx, ay, cx, ay + 46))
        label = "." + fig.name + "(" + ", ".join(a[p.name] for p in fig.params if p.name in a) + ")"
        lw = min(len(label) * S.CW + 16, S.W - cx - 14)
        txt_l = S.clip(label, int((lw - 16) // S.CW))
        fn_param = next((p.name for p in fig.params if p.name in a and arg_value(a[p.name]) is S.NONE), None)
        pl = S.rect(cx + 10, ay + 13, lw, 20, "fb" if fn_param is None else "pb", r=10) + S.text(cx + 18, ay + 27, txt_l, "ta m b sm2" if fn_param is None else "tp m b sm2")
        pills.append(S.part(fig.p(fn_param), pl) if fn_param else pl)
        out += pills
        y += 54
        # the result
        rlab = "result" + (f" · {returns_of(fig.signature)}" if returns_of(fig.signature) else "")
        if ok and isinstance(v, list):
            o_svg, o_h, o_pos = chips_row(v, 10, y + 22, w - 20, part_of=lambda i: "r")
            out.append(S.rect(0, y, w, o_h + 32, "ob", r=7) + S.text(9, y + 14, rlab, "ta sm b") + o_svg)
            # connectors from where each item came from
            used = set()
            conns = []
            for j, item in enumerate(v[: len(o_pos)]):
                src = _source_index(items_in, item, used, j, sel)
                if src is None or src >= len(in_pos):
                    continue
                used.add(src)
                x1, _, y1b, _, _ = in_pos[src]
                x2, y2t, _, _, _ = o_pos[j]
                conns.append(S.path(f"M{S.n(x1)} {S.n(y1b + 2)} C{S.n(x1)} {S.n(y1b + 40)} {S.n(x2)} {S.n(y + 22 - 40 + y2t - (y + 22))} {S.n(x2)} {S.n(y2t - 2)}", "cn"))
            out.insert(0, "".join(conns))
            y += o_h + 32
        else:
            rb = _result_block(fig, ok, v, {}, a)
            b_svg, b_h = S.block(0, y, w, **rb)
            out.append(b_svg)
            y += b_h
        maxh = max(maxh, y)
        bodies.append(_wrap_variant(param, val, "".join(out), default, y, heights))
        expr = _sub(spec["call"], a) if spec.get("call") else fig.call_expr(a)
        calls.append(call_line(expr, _shown(v) if ok else (v.text if isinstance(v, Opaque) else ""), ok,
                               f"{param}:{val}" if param else None, hidden=bool(param) and val != default))
    fig.real = real_any and "static" not in spec
    fig.call_html = "".join(calls)
    fig.default_legend(used=set(args) | ({param} if param else set()))
    if param:
        _add_vary_control(fig, param, values, default)
    fig.draw("".join(bodies), heights.get(default, maxh), spec.get("alt") or f"{fig.qual()}: the items going in, and where each one ends up")


def _selected(fig, a, items_in, v, ok):
    """Which input items count as 'used' (lit up) and any boundary markers."""
    res = {"kept": set(), "markers": []}
    if fig.name in ("slice", "take", "skip"):
        n_ = len(items_in)
        def idx(src, d):
            if src is None:
                return d
            vv = arg_value(src)
            if not isinstance(vv, int):
                return d
            return vv + n_ if vv < 0 else vv
        if fig.name == "slice":
            s = idx(a.get("start"), 0)
            e = idx(a.get("end"), n_)
            res["markers"] = [("start", s)] + ([("end", e)] if "end" in a else [])
        elif fig.name == "take":
            s, e = 0, idx(a.get("n"), 0)
            res["markers"] = [("n", e)]
        else:
            s, e = idx(a.get("n"), 0), n_
            res["markers"] = [("n", s)]
        res["kept"] = set(range(max(0, s), min(n_, e)))
        return res
    if not ok:
        return res
    if isinstance(v, list):
        used = set()
        for j, item in enumerate(v):
            src = _source_index(items_in, item, used, j, res)
            if src is not None:
                used.add(src)
        res["kept"] = used
    elif fig.name in ("find", "first", "last", "max", "min", "pop", "remove_at", "index_of", "contains", "remove"):
        target = v
        if fig.name in ("index_of",) and isinstance(v, int) and 0 <= v < len(items_in):
            res["kept"] = {v}
        elif fig.name in ("contains", "remove"):
            vv = arg_value(a.get("value", "nil"))
            if vv in items_in:
                res["kept"] = {items_in.index(vv)}
        elif fig.name == "remove_at":
            vv = arg_value(a.get("position", "0"))
            if isinstance(vv, int) and 0 <= vv < len(items_in):
                res["kept"] = {vv}
        elif target in items_in:
            idx_ = len(items_in) - 1 - items_in[::-1].index(target) if fig.name in ("last", "pop") else items_in.index(target)
            res["kept"] = {idx_}
    elif fig.name in ("sum", "join", "reduce", "len", "all", "any", "is_empty", "count", "each"):
        res["kept"] = set(range(len(items_in)))
    return res


def _source_index(items_in, item, used, j, sel):
    kept = sorted(sel.get("kept") or [])
    for i in kept + [k for k in range(len(items_in)) if k not in kept]:   # items known to be kept come first
        if i not in used and items_in[i] == item:
            return i
    if isinstance(item, list) and item and item[-1] in items_in:   # enumerate / zip pairs
        for i, it in enumerate(items_in):
            if i not in used and it == item[-1] or (i not in used and it == item[0]):
                return i
    # map(): same position
    if j < len(items_in) and j not in used and len(items_in) and not sel.get("markers"):
        return j
    return None


# ------------------------------------------------------------------- net
def t_net(fig: Fig):
    """Your computer on the left, the other host on the right."""
    spec = fig.spec
    args = dict(spec.get("args", {}))
    ok, v, txt, real = evaluate(fig, args)
    fig.real = real and "static" not in spec
    w = S.W
    out = []
    # machines: your computer on the left, the host (with its port) on the right
    out.append(S.rect(0, 8, 92, 56, "bx", r=8) + S.icon("laptop", 10, 14, 22) + S.text(10, 54, "your computer", "ts sm"))
    hp = fig.p(spec.get("host_param", "host")) if spec.get("host_param", "host") else None
    host = spec.get("host_label") or (_text_of(args.get(spec.get("host_param", "host"), '""')) or "host")
    rx, rw = 186, w - 186
    host_box = S.rect(rx, 8, rw, 56, "pb" if hp is not None else "bx", r=8) + S.icon(spec.get("icon", "server"), rx + rw - 30, 14, 22, "icp" if hp is not None else "ic")
    host_box += S.text(rx + 10, 26, spec.get("host_title", "host"), "tp sm b" if hp is not None else "ts sm b")
    host_box += S.text(rx + 10, 50, S.clip(str(host), int((rw - 16) // 6.4)), "tp m sm" if hp is not None else "t m sm")
    out.append(S.part(hp, host_box))
    pp = fig.p(spec.get("port_param", "port")) if spec.get("port_param", "port") else None
    port = spec.get("port_label") or _text_of(args.get(spec.get("port_param", "port"), "")) or ""
    ax2 = rx - 4
    if port:
        sock = S.rect(rx - 50, 22, 46, 28, "pb", r=6) + S.text(rx - 27, 41, S.clip(str(port), 6), "tp m b sm2", "middle")
        sock += S.text(rx - 27, 62, spec.get("port_title", "port"), "tp sm", "middle")
        out.append(S.part(pp, sock))
        ax2 = rx - 54
    # request / response arrows, with their words on their own lines below
    req = spec.get("request", "connect")
    resp = spec.get("response", "answer")
    ax1 = 96
    out.append(S.arrow(ax1, 28, ax2, 28))
    if resp:
        out.append(S.arrow(ax2, 46, ax1, 46, "ar dash"))
    out.append(S.arrow(4, 84, 22, 84) + S.text(28, 88, S.clip("sends: " + req, 44), "t sm"))
    if resp:
        out.append(S.arrow(22, 102, 4, 102, "ar dash") + S.text(28, 106, S.clip("gets back: " + resp, 44), "t sm"))
    y = 118 if resp else 100
    # extra parameter boxes (data, options, method...)
    extra = [n_ for n_ in spec.get("extra", []) if fig.p(n_) is not None]
    if extra:
        blocks = [_param_block(fig, n_, args, {}) for n_ in extra]
        body, h = S.flow([S.Row(*blocks)], y0=y)
        out.append(body)
        y += h + 8
    # timeout clock
    tp = fig.p("timeout")
    if tp is not None:
        secs = arg_value(args.get("timeout", str(spec.get("timeout_default", 3))))
        secs = secs if isinstance(secs, (int, float)) else 3
        cx, cy, r = 26, y + 26, 20
        frac = min(secs / 10, 1)
        ang = -math.pi / 2 + frac * 2 * math.pi
        ex, ey = cx + r * math.cos(ang), cy + r * math.sin(ang)
        large = 1 if frac > 0.5 else 0
        clock = S.path(f"M{cx} {cy - r} A{r} {r} 0 {large} 1 {S.n(ex)} {S.n(ey)}", "pl w3",
                       extra=f" data-fv-bind='[[\"arc\",{cx},{cy},{r},{{\"timeout\":0.1}}]]'")
        clock += f'<circle cx="{cx}" cy="{cy}" r="{r}" class="ln"/>' + S.line(cx, cy, cx, cy - 12, "pl w2")
        clock += S.text(56, cy - 2, "timeout", "tp sm b")
        clock += S.text(56, cy + 13, f"gives up after {S.n(secs)} s", "tp sm",
                        extra=" data-fv-bind='[[\"text\",\"gives up after {timeout} s\"]]'")
        out.append(S.part(tp, clock))
        fig.controls.append(Control("timeout", tp, lo=0.5, hi=10, step=0.5, value=secs, unit=" s"))
        y += 58
    # result
    rb = _result_block(fig, ok, v, spec.get("result_node", {}), args)
    b_svg, b_h = S.block(0, y, w, **rb)
    out.append(S.arrow(w / 2, y - (6 if tp is None and not extra else 4), w / 2, y) if False else "")
    out.append(b_svg)
    y += b_h
    fig.default_legend(used=set(args) | set(spec.get("used", [])))
    expr = spec.get("call") or fig.call_expr(args)
    fig.call_html = call_line(expr, _shown(v) if ok and not spec.get("hide_result_text") else None, ok)
    if tp is not None and not spec.get("call") and "timeout" in args:   # the call follows the timeout slider
        tpl = json.dumps([["text", fig.call_expr(dict(args, timeout="{timeout}"))]])
        fig.call_html = fig.call_html.replace("<code>", f"<code data-fv-bind='{esc(tpl)}'>", 1)
    fig.draw("".join(out), y, spec.get("alt") or f"{fig.qual()}: your computer talking to another one")


# ----------------------------------------------------------------- lanes
def t_lanes(fig: Fig):
    """Items handed to worker lanes; the workers slider changes the lanes."""
    spec = fig.spec
    args = dict(spec.get("args", {}))
    items = spec.get("items", ["a", "b", "c", "d", "e", "f"])
    durs = spec.get("durations", [3, 2, 4, 2, 3, 1, 2, 3])
    values = spec.get("workers", [1, 2, 3, 4])
    default = spec.get("workers_default", values[min(1, len(values) - 1)])
    ok, v, txt, real = evaluate(fig, args)
    fig.real = real and "static" not in spec
    wp = fig.p(spec.get("workers_param", "workers"))
    ip = fig.p(spec.get("items_param", "items"))
    fp = fig.p("fn")
    bodies, maxh = [], 0
    heights = {}
    for nw in values:
        out = []
        lanes = [0.0] * nw
        place = []
        for i, it in enumerate(items):
            k = min(range(nw), key=lambda j: lanes[j])
            place.append((k, lanes[k], durs[i % len(durs)], it))
            lanes[k] += durs[i % len(durs)]
        total = max(lanes)
        y = 0
        out.append(S.part(ip, S.text(0, 13, spec.get("items_title", "items"), "tp sm b") +
                          "".join(S.rect(56 + i * 34, 2, 30, 18, "pb", r=4) + S.text(71 + i * 34, 15, S.clip(str(it), 3), "tp m sm2", "middle") for i, it in enumerate(items))))
        y = 30
        unit = (S.W - 70) / max(total, 1)
        for j in range(nw):
            ly = y + j * 26
            out.append(S.part(wp, S.text(0, ly + 15, f"worker {j + 1}", "tp sm")))
            out.append(S.rect(64, ly + 2, S.W - 66, 20, "lane", r=4))
        for k, start, d, it in place:
            ly = y + k * 26
            bx = 66 + start * unit
            out.append(S.part(fp, S.rect(bx, ly + 4, d * unit - 3, 16, "pb", r=3) + S.text(bx + 4, ly + 16, f"fn({S.clip(str(it), 3)})", "tp m sm")))
        y += nw * 26 + 4
        out.append(S.arrow(66, y + 6, S.W - 4, y + 6, "ln") + S.text(66, y + 22, "time →", "tf sm"))
        out.append(S.text(S.W - 4, y + 22, f"done after {S.n(total)} steps", "ts sm", "end"))
        y += 30
        rb = _result_block(fig, ok, v, spec.get("result_node", {}), args)
        b_svg, b_h = S.block(0, y, S.W, **rb)
        out.append(b_svg)
        y += b_h
        maxh = max(maxh, y)
        bodies.append(_wrap_variant("workers", str(nw), "".join(out), str(default), y, heights))
    fig.default_legend(used=set(args) | {"workers"})
    if wp is not None or spec.get("workers_param") is None:
        fig.controls.append(Control(spec.get("workers_param", "workers"), wp if wp is not None else 0,
                                    values=[str(x) for x in values], value=str(default)))
    expr = spec.get("call") or fig.call_expr(args)
    fig.call_html = call_line(expr, _shown(v) if ok else None, ok)
    fig.draw("".join(bodies), heights.get(str(default), maxh), spec.get("alt") or f"{fig.qual()}: work spread over worker lanes")


# ------------------------------------------------------------- threshold
def t_threshold(fig: Fig):
    """Counts per source as bars, the threshold parameter as a line; every
    slider position runs the function again with that threshold."""
    spec = fig.spec
    args = dict(spec.get("args", {}))
    bars_res = evaluate(fig, {}, override={"call": spec["bars"], "static": spec["bars_static"]} if "bars_static" in spec else {"call": spec["bars"]})
    bars = bars_res[1] if bars_res[0] and isinstance(bars_res[1], list) else spec.get("bars_fallback", [])
    bars = [b for b in bars if isinstance(b, dict)][:6]
    tparam = spec["param"]
    tp = fig.p(tparam)
    values = [str(x) for x in spec.get("values", [3, 5, 10, 20])]
    default = str(spec.get("default", values[1]))
    data_param = spec.get("data_param", fig.params[0].name if fig.params else None)
    dp = fig.p(data_param) if data_param else None
    others = [p_.name for p_ in fig.params if p_.name not in (tparam, data_param) and p_.name in args]
    top = max([b.get("count", 0) for b in bars] + [max(float(x) for x in values)])
    bodies, calls, maxh = [], [], 0
    heights = {}
    real_any = False
    for tv in values:
        a = dict(args, **{tparam: tv})
        ok, v, txt, real = evaluate(fig, a)
        real_any = real_any or real
        tvn = float(tv)
        out = []
        y = 4
        bars_svg = S.text(0, y + 10, spec.get("bars_title", "count per source"), "tp sm b" if dp is not None else "ts sm b")
        y += 18
        bx, bw = 112, S.W - 112 - 74
        flagged = None   # the sources the real run alerted on, when it says
        if ok and isinstance(v, list) and all(isinstance(i, dict) and "by" in i for i in v):
            flagged = {str(i["by"]) for i in v}
        for bar in bars:
            c = bar.get("count", 0)
            hit = (str(bar.get("value")) in flagged) if flagged is not None else c >= tvn
            ln_ = max(2, bw * c / top)
            bars_svg += S.text(0, y + 13, S.clip(str(bar.get("value")), 15), "t m sm2")
            bars_svg += S.rect(bx, y + 2, ln_, 15, "hit" if hit else "cl", r=3)
            bars_svg += S.text(bx + 4 + ln_, y + 14, str(c) + (" \u2192 alert" if hit else ""), "t sm b" if hit else "ts sm")
            y += 20
        out.append(S.part(dp, bars_svg))
        tx = bx + bw * tvn / top
        out.append(S.part(tp, S.line(tx, 20, tx, y + 4, "pl w2 dash") + S.text(min(max(tx, 40), S.W - 40), y + 16, f"{tparam} = {tv}", "tp sm b", "middle")))
        y += 24
        ox = 0
        for name in others:
            lab = f"{name} = {args[name]}" + (f" {spec['units'][name]}" if name in spec.get("units", {}) else "")
            w_ = len(lab) * 6.4 + 14
            out.append(S.part(fig.p(name), S.rect(ox, y, w_, 20, "pb", r=10) + S.text(ox + 7, y + 14, lab, "tp sm b")))
            ox += w_ + 6
        if others:
            y += 28
        if spec.get("show_result", True):
            b_svg, b_h = S.block(0, y, S.W, **_result_block(fig, ok, v, spec.get("result_node", {}), a))
            out.append(b_svg)
            y += b_h
        maxh = max(maxh, y)
        bodies.append(_wrap_variant(tparam, tv, "".join(out), default, y, heights))
        expr = _sub(spec["call"], a) if spec.get("call") else fig.call_expr(a)
        calls.append(call_line(_sub(spec["show_call"], a) if spec.get("show_call") else expr, None, ok, f"{tparam}:{tv}", hidden=tv != default))
    fig.real = real_any and "static" not in spec
    fig.note = spec.get("note", "")
    fig.default_legend(used=set(args) | {tparam})
    fig.controls.append(Control(tparam, tp if tp is not None else 0, values=values, value=default))
    fig.call_html = "".join(calls)
    fig.draw("".join(bodies), heights.get(default, maxh), spec.get("alt") or f"{fig.qual()}: counts against the {tparam} line")


# -------------------------------------------------------------- timeline
def t_timeline(fig: Fig):
    """Points on a time axis; marks: list of {label, at (0..1), p (param)}."""
    spec = fig.spec
    args = dict(spec.get("args", {}))
    ok, v, txt, real = evaluate(fig, args)
    fig.real = real and "static" not in spec
    out = []
    y = 26
    x1, x2 = 8, S.W - 8
    out.append(S.arrow(x1, y, x2, y, "ln w2") + S.text(x2, y + 18, spec.get("axis", "time →"), "tf sm", "end"))
    for mk in spec.get("marks", []):
        mx = x1 + (x2 - x1 - 10) * mk.get("at", 0.5)
        p = fig.p(mk["p"]) if mk.get("p") else None
        body = f'<circle cx="{S.n(mx)}" cy="{y}" r="6" class="{"pdot" if p is not None else "dot"}"/>'
        body += S.text(mx, y - 11, mk.get("label", ""), "tp sm b" if p is not None else "t sm b", "middle")
        if mk.get("under"):
            body += S.text(mx, y + 19, mk["under"], "tp sm" if p is not None else "ts sm", "middle")
        out.append(S.part(p, body))
    for sp in spec.get("spans", []):
        a_, b_ = x1 + (x2 - x1 - 10) * sp["from"], x1 + (x2 - x1 - 10) * sp["to"]
        p = fig.p(sp["p"]) if sp.get("p") else None
        body = S.rect(a_, y - 4, b_ - a_, 8, "pb" if p is not None else "cl", r=4)
        body += S.text((a_ + b_) / 2, y + 22, sp.get("label", ""), "tp sm b" if p is not None else "ts sm", "middle")
        out.append(S.part(p, body))
    y += 34
    nodes = spec.get("nodes") or ([{"row": [{"param": p_.name} for p_ in fig.params if p_.name in args or not p_.optional]}] if fig.params else []) + [{"arrow": True}, {"result": True}]
    body, h = S.flow(_build_items(fig, nodes, args, ok, v), y0=y)
    out.append(body)
    y += h
    fig.default_legend(used=set(args) | set(spec.get("used", [])))
    expr = spec.get("call") or fig.call_expr(args)
    fig.call_html = call_line(expr, _shown(v) if ok and not spec.get("hide_result_text") else None, ok)
    fig.draw("".join(out), y, spec.get("alt") or f"{fig.qual()}: moments on a time line")


# ----------------------------------------------------------------- value
def value_fig(lib: str, name: str, value_text: str, doc: str, spec: dict, hide=False) -> Fig:
    fig = Fig(lib, name, name, doc, spec)
    fig.params = []
    try:
        v = parse_literal(value_text)
    except ValueError:
        v = Opaque(value_text)
    if hide:
        v = Opaque("depends on the computer")
    items = [S.Row(dict(label=f"{lib}.{name}", kind="out", value=v, icon_name=spec.get("icon")))]
    if spec.get("lines"):
        items += [6, S.Row(dict(label="what it is for", kind="plain-sans", lines=spec["lines"]))]
    elif doc:
        items += [6, S.Row(dict(label="what it is", kind="plain-sans", lines=S.wrap_chars(doc, 44)[:4]))]
    body, h = S.flow(items)
    fig.draw(body, h, f"The value {lib}.{name}")
    fig.call_html = call_line(f"{lib}.{name}", None if hide else S.clip(value_text, 80))
    fig.real = not hide
    fig.note = ("Real value, read from TB while the site was built." if not hide else
                "This value depends on the computer the program runs on.")
    return fig


TEMPLATES = {
    "flow": t_flow,
    "call": t_flow,
    "list_op": t_list_op,
    "net": t_net,
    "lanes": t_lanes,
    "threshold": t_threshold,
    "timeline": t_timeline,
    "generic": t_generic,
}
