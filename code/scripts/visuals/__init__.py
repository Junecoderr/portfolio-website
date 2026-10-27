"""Per-function pictures for the library reference ("Visualize" drop-downs).

    import visuals
    visuals.function_html("text", fn_info, desktop=False)  -> <details class="fn-visual">...
    visuals.value_html("math", value_info)
    visuals.method_html("list", method_info)
    visuals.builtin_html(fn_info)
    visuals.gui_extras_html()   # widgets and window methods, inside gui.window's card

What to draw comes from declarative specs in scripts/visuals/specs/<lib>.py
(one dict literal, SPEC = {...}, read with ast.literal_eval, never run):

    "pad_left": {"t": "flow", "args": {"value": "7", "width": "3", "fill": '"0"'},
                 "pad": "fill", "vary": {"param": "width", "values": ["3", "5", "8"]}},

Keys every template understands: t (template, see templates.py and
gui_draw.py), args (parameter -> TB source of the example value), setup (TB
lines run first), imports (extra libraries), call (the whole call, with
$param placeholders), static (the result as a TB literal, for functions that
need the desktop app or the network), run (false: don't run it), roles
(parameter -> what it controls), vary (a slider over example values: every
value is run for real), mode (how to draw the result: cells, lines, tree,
files, http), keys (which keys of a map result to show). "_default" in a spec
file applies to every function in it. A function with no spec still gets a
picture: the generic template runs the example in its doc string when there
is one, and otherwise shows what goes in and what comes back.
"""

from __future__ import annotations

import traceback

from . import runner
from .core import Fig, figure_html, load_spec, parse_params
from .templates import TEMPLATES, value_fig
from . import gui_draw

TEMPLATES.update(gui_draw.TEMPLATES)

PROBLEMS: list[str] = []      # templates that failed and fell back (tests check it stays empty)

RECEIVERS = {
    "text": '"Hello, TB"', "list": "[3, 1, 4, 1, 5]", "map": '{"name": "Ada", "role": "admin"}',
    "number": "3.14159", "bytes": 'b"MZ\\x90\\x00PE"', "range": "(1..6)", "generator": "(1..6).iter()",
    "function": "((a, b) => a + b)",
}
RECV_LABEL = {"text": "the text", "list": "the list", "map": "the map", "number": "the number", "bytes": "the bytes",
              "range": "the range", "generator": "the generator", "function": "the function"}


def _merge(spec_file: dict, name: str) -> dict:
    base = dict(spec_file.get("_default", {}))
    s = spec_file.get(name, {})
    if isinstance(s, str):
        s = {"t": s}
    base.update(s)
    return base


def _render(fig: Fig) -> Fig:
    t = fig.spec.get("t") or ("flow" if ("args" in fig.spec or "call" in fig.spec or "nodes" in fig.spec or "static" in fig.spec) else "generic")
    try:
        TEMPLATES[t](fig)
    except Exception:  # never break the build over one picture
        PROBLEMS.append(f"{fig.lib}.{fig.name} ({t}): {traceback.format_exc(limit=3)}")
        fig2 = Fig(fig.lib, fig.name, fig.signature, fig.doc, {"run": False, "roles": fig.spec.get("roles", {})},
                   owner=fig.owner, desktop=True, receiver=fig.receiver)
        TEMPLATES["generic"](fig2)
        return fig2
    return fig


def function_fig(lib: str, f: dict, desktop: bool = False) -> Fig:
    spec = _merge(load_spec(lib), f["name"])
    if desktop and "run" not in spec and "static" not in spec:
        spec["run"] = False
    fig = Fig(lib, f["name"], f["signature"], f["doc"], spec, desktop=desktop)
    return _render(fig)


def function_html(lib: str, f: dict, desktop: bool = False) -> str:
    return figure_html(function_fig(lib, f, desktop))


def value_html(lib: str, v: dict, hide: bool = False) -> str:
    spec = load_spec(lib).get("_values", {}).get(v["name"], {})
    return figure_html(value_fig(lib, v["name"], v["value"], v["doc"], spec, hide))


def method_fig(kind: str, m: dict) -> Fig:
    name = m["signature"].split("(")[0]
    spec = _merge(load_spec("methods").get(kind, {}), name)
    recv = spec.get("recv", RECEIVERS.get(kind, "nil"))
    spec.setdefault("recv_label", RECV_LABEL.get(kind, "the value"))
    if "t" not in spec:
        spec["t"] = "list_op" if kind in ("list",) and spec.get("run") is not False else "flow"
    fig = Fig("__builtins__", name, m["signature"], m["doc"], spec, owner="", receiver=recv)
    if "call" not in spec:
        pass
    return _render(fig)


def method_html(kind: str, m: dict) -> str:
    return figure_html(method_fig(kind, m))


def builtin_html(f: dict) -> str:
    name = f["signature"].split("(")[0]
    spec = _merge(load_spec("builtins"), name)
    fig = Fig("__builtins__", name, f["signature"], f["doc"], spec, owner="")
    return figure_html(_render(fig))


def gui_extras() -> list[tuple[str, str, list]]:
    """[(group title, intro, [(anchor id, signature, html)])] for the widgets and window methods."""
    spec = load_spec("gui")
    out = []
    for group in spec.get("_subs", []):
        entries = []
        for sub in group["items"]:
            s = dict(spec.get("_default", {}))
            s.update(sub)
            sig = s["sig"]
            name = sig.split("(")[0]
            s.setdefault("run", False)
            fig = Fig("gui", name, sig, s.get("doc", ""), s, owner=group.get("owner", "win"), desktop=True)
            fig = _render(fig)
            ident = f"{group.get('owner', 'win')}-{name}".replace("_", "-")
            title = f'<code class="fv-sub-sig">{group.get("owner", "win")}.{sig}</code>'
            entries.append((ident, sig, figure_html(fig, sub_title=title, ident=ident, cls="fv-sub")))
        out.append((group["title"], group.get("intro", ""), entries))
    return out


__all__ = ["function_html", "function_fig", "value_html", "method_html", "method_fig", "builtin_html", "gui_extras",
           "parse_params", "PROBLEMS", "runner"]
