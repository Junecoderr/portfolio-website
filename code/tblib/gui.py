"""gui: desktop windows with buttons, text boxes, tables and dialogs (tkinter).

How it is built
---------------
The TB-facing part (load(api) below) turns TB calls into calls on a small
*backend*. There are two backends:

* _TkBackend: real windows with tkinter/ttk ("clam", restyled in TB's
  colours). tkinter is imported lazily, so `tb doc gui` and the browser
  playground can load this module on any computer.
* FakeBackend: no screen needed. Tests swap it in with
  `tblib.gui._use_backend(FakeBackend())` and then click buttons, type
  into inputs and read what the program put in its windows.

A backend makes windows; a window has a `body` container; a container adds
widgets (`add(kind, spec)`) and returns a handle. Handles are duck-typed:
get/set, set_items, insert_row/set_rows, selected_index/select, set_enabled,
focus, append. Callbacks in `spec` are plain Python callables with no
arguments; the TB layer decides what to pass to the TB function.

Testing aid: if the environment variable TB_GUI_AUTOCLOSE_MS is a whole
number, every window closes itself that many milliseconds after win.run()
starts. CI and the smoke tests use it to open real windows without a person.
"""

from __future__ import annotations

import heapq
import os
import sys

from tblib._api import TBError, check_type, fail, to_text

import tb as _tb

# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------

THEMES = {
    "light": {
        "bg": "#faf6f8", "surface": "#f3ecf0", "fg": "#2b1d2e", "muted": "#7d6b80",
        "border": "#e2d4dc", "accent": "#b0123f", "accent_hover": "#8e0e33",
        "accent_text": "#b0123f", "accent_fg": "#ffffff", "select": "#f6d3de",
        "select_fg": "#2b1d2e", "field": "#ffffff", "button": "#efe5ea",
        "button_hover": "#e5d6de", "stripe": "#fbf6f9", "success": "#1d7a4c", "danger": "#b3261e",
    },
    "dark": {
        "bg": "#1b1220", "surface": "#241a2a", "fg": "#f4eaf0", "muted": "#a993ad",
        "border": "#3b2c42", "accent": "#c2184b", "accent_hover": "#d9285d",
        "accent_text": "#ff7398", "accent_fg": "#ffffff", "select": "#5c1a35",
        "select_fg": "#ffffff", "field": "#251a2c", "button": "#30233a",
        "button_hover": "#3d2d47", "stripe": "#21182a", "success": "#5fd39a", "danger": "#ff7b72",
    },
}

LABEL_STYLES = ("normal", "title", "heading", "muted", "accent", "small", "bold", "display", "success", "danger", "mono")
BADGE_STYLES = ("accent", "success", "danger", "warning", "muted", "info")
GAP = 8

# Colours for the lines, bars and pie slices of a chart (the first is swapped
# for the window's accent colour).
SERIES_COLOURS = ["#b0123f", "#2a7de1", "#1d7a4c", "#e0862a", "#7a4fd6", "#17a2b8", "#d4a017", "#6d7f3b", "#8c5a3c"]


def _hex_rgb(colour):
    """'#b0123f' -> (176, 18, 63). Raises ValueError for anything else."""
    text = colour.strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if len(text) != 7 or not text.startswith("#"):
        raise ValueError(colour)
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def _rgb_hex(rgb):
    """An (r, g, b) tuple -> "#rrggbb"."""
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def _mix(a, b, amount):
    """Blend colour a towards colour b (amount 0..1)."""
    ra, rb = _hex_rgb(a), _hex_rgb(b)
    return _rgb_hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb)))


def _shade(colour, amount):
    """Lighten (amount > 0) or darken (amount < 0) a colour."""
    return _mix(colour, "#ffffff" if amount > 0 else "#000000", abs(amount))


def is_colour(text):
    """True for '#rgb' or '#rrggbb' text."""
    try:
        _hex_rgb(text)
        return isinstance(text, str)
    except (ValueError, AttributeError, TypeError):
        return False


def _palette(theme, accent=None, surface=False):
    """The colours of a window: a theme, optionally with another accent colour.
    surface=True swaps bg and surface (used inside cards, which sit on the
    'surface' colour)."""
    p = dict(THEMES[theme])
    if accent:
        accent = _rgb_hex(_hex_rgb(accent))
        p["accent"] = accent
        p["accent_hover"] = _shade(accent, -0.15 if theme == "light" else 0.15)
        p["accent_text"] = accent if theme == "light" else _shade(accent, 0.35)
        p["select"] = _mix(accent, p["field"], 0.72)
        p["accent_fg"] = "#ffffff" if sum(_hex_rgb(accent)) < 500 else "#1b1220"
    if surface:
        p["bg"], p["surface"] = p["surface"], p["bg"]
        p["stripe"] = p["surface"]
    return p


def _prefix(theme, accent=None, surface=False):
    """The ttk style prefix for a palette, e.g. 'TbDark', 'TbLightA2a7de1S'."""
    name = "Tb" + theme.capitalize()
    if accent:
        name += "A" + _rgb_hex(_hex_rgb(accent))[1:]
    if surface:
        name += "S"
    return name


def system_theme():
    """'dark' or 'light': what the operating system is set to (light when
    unknown). macOS asks `defaults`, Windows reads the registry, Linux asks
    gsettings; none of them is required."""
    try:
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                                 text=True, timeout=3)
            return "dark" if "dark" in out.stdout.lower() else "light"
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if value else "dark"
        import subprocess
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=3)
        return "dark" if "dark" in out.stdout.lower() else "light"
    except Exception:
        return "light"


class _Problem(Exception):
    """A friendly problem from a backend; the TB layer turns it into fail()."""


class GuiUnavailable(_Problem):
    """No tkinter, or no screen to draw on."""


_backend = None


def _use_backend(backend):
    """Swap the backend (tests pass a FakeBackend; None goes back to Tk).
    Returns the previous one."""
    global _backend
    previous, _backend = _backend, backend
    return previous


def _get_backend():
    """The backend in use: the one a test installed, else real Tk."""
    global _backend
    if _backend is None:
        _backend = _TkBackend()
    return _backend


# ---------------------------------------------------------------------------
# Keyboard shortcuts: "Ctrl+S", "Mod+O", "Enter", "+", "F5" -> Tk sequences
# ---------------------------------------------------------------------------

_KEY_NAMES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "tab": "Tab",
    "space": "space", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "+": "plus", "-": "minus", "*": "asterisk", "/": "slash", ".": "period",
    "=": "equal", ",": "comma", "%": "percent", "(": "parenleft", ")": "parenright",
}
_MODIFIERS = {"ctrl": "Control", "control": "Control", "alt": "Alt", "option": "Alt",
              "shift": "Shift", "cmd": "Command", "command": "Command"}


def key_sequence(combo: str) -> str | None:
    """'Ctrl+S' -> '<Control-s>'. None if the combination isn't understood."""
    text = combo.strip()
    if not text:
        return None
    if text == "+":
        parts = ["+"]
    else:
        parts = text.split("+")
        if text.endswith("++"):
            parts = parts[:-2] + ["+"]
    *mods, key = [p.strip() for p in parts]
    out = []
    shift = False
    for mod in mods:
        low = mod.lower()
        if low == "mod":
            out.append("Command" if sys.platform == "darwin" else "Control")
        elif low == "shift":
            shift = True
        elif low in _MODIFIERS:
            out.append(_MODIFIERS[low])
        else:
            return None
    low = key.lower()
    if low in _KEY_NAMES:
        sym = _KEY_NAMES[low]
        if shift:
            out.append("Shift")
    elif len(key) == 1 and key.isalnum():
        sym = key.upper() if shift and key.isalpha() else key.lower()
        if shift and not key.isalpha():
            out.append("Shift")
    elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        sym = "F" + low[1:]
        if shift:
            out.append("Shift")
    else:
        return None
    if len(sym) == 1 and sym.isdigit():
        sym = "Key-" + sym
    return "<" + "-".join(out + [sym]) + ">"


# ---------------------------------------------------------------------------
# The fake backend (for tests; no screen needed)
# ---------------------------------------------------------------------------


class FakeWidget:
    """A widget in a FakeBackend window. Tests read .kind/.state and
    simulate people: click(), type(text), enter(), toggle(), choose(i), open(i),
    slide(value), hover? (no), press on the window.

    Extra state by kind: .items (select/list_box), .rows (table),
    .nodes (tree: id -> {"parent", "values", "open"}), .data (chart spec),
    .drawn (canvas items), .url (link)."""

    def __init__(self, window, kind, spec):
        self.window = window
        self.kind = kind
        self.spec = spec
        self.enabled = True
        self.focused = False
        self.state = {
            "label": lambda: spec.get("text", ""),
            "button": lambda: spec.get("text", ""),
            "input": lambda: spec.get("value", ""),
            "textarea": lambda: spec.get("value", ""),
            "checkbox": lambda: bool(spec.get("checked", False)),
            "select": lambda: spec.get("value", ""),
            "progress": lambda: spec.get("value", 0),
            "image": lambda: spec.get("path", ""),
            "slider": lambda: spec.get("value", spec.get("min", 0)),
            "spinner": lambda: spec.get("value", spec.get("min", 0)),
            "date_picker": lambda: spec.get("value", ""),
            "color": lambda: spec.get("value", "#b0123f"),
            "link": lambda: spec.get("text", ""),
            "badge": lambda: spec.get("text", ""),
            "chart": lambda: dict(spec),
        }.get(kind, lambda: None)()
        self.items = list(spec.get("items", []))   # select / list_box texts
        self.rows = []                              # table texts
        self.selected = None
        self.nodes = {}                             # tree: id -> {"parent", "values", "open"}
        self.order = []                             # tree ids in insertion order
        self.drawn = []                             # canvas: (kind, args) in draw order
        self.url = spec.get("url", "")
        self._next_item = 0

    # -- the handle protocol (what the TB layer calls) ------------------------

    def get(self):
        return self.state

    def set(self, value):
        self.state = value

    def append(self, text):
        self.state = (self.state or "") + text

    def set_items(self, items):
        self.items = list(items)
        if self.kind == "list_box":
            self.selected = None

    def insert_row(self, texts):
        self.rows.append(list(texts))

    def set_rows(self, rows):
        self.rows = [list(r) for r in rows]
        self.selected = None

    def selected_index(self):
        return self.selected

    def select(self, index):
        self.selected = index

    def set_enabled(self, flag):
        self.enabled = bool(flag)

    def set_style(self, style):
        self.spec["style"] = style

    def focus(self):
        self.focused = True

    # tree
    def add_node(self, node_id, parent_id, texts, open_):
        self.nodes[node_id] = {"parent": parent_id, "values": list(texts), "open": bool(open_)}
        self.order.append(node_id)

    def set_node(self, node_id, texts):
        self.nodes[node_id]["values"] = list(texts)

    def remove_node(self, node_id):
        for nid in [n for n in self.order if n == node_id or self._under(n, node_id)]:
            self.nodes.pop(nid, None)
            self.order.remove(nid)
        if self.selected not in self.nodes:
            self.selected = None

    def _under(self, node_id, ancestor):
        parent = self.nodes.get(node_id, {}).get("parent")
        while parent is not None:
            if parent == ancestor:
                return True
            parent = self.nodes.get(parent, {}).get("parent")
        return False

    def clear_nodes(self):
        self.nodes, self.order, self.selected = {}, [], None

    def expand(self, node_id, flag):
        self.nodes[node_id]["open"] = bool(flag)

    def is_expanded(self, node_id):
        return self.nodes[node_id]["open"]

    def selected_id(self):
        return self.selected

    def select_id(self, node_id):
        self.selected = node_id

    # chart
    def update(self, spec):
        self.state = dict(self.state or {}, **spec)

    @property
    def data(self):
        """The chart's current spec: {"type", "data", "labels", ...}."""
        return self.state

    # canvas
    def _draw(self, kind, *args):
        self._next_item += 1
        self.drawn.append((self._next_item, kind) + args)
        return self._next_item

    def line(self, x1, y1, x2, y2, o):
        return self._draw("line", x1, y1, x2, y2, dict(o))

    def rect(self, x, y, w, h, o):
        return self._draw("rect", x, y, w, h, dict(o))

    def oval(self, x, y, w, h, o):
        return self._draw("oval", x, y, w, h, dict(o))

    def draw_text(self, x, y, text, o):
        return self._draw("text", x, y, text, dict(o))

    def image(self, x, y, path):
        return self._draw("image", x, y, path)

    def clear(self):
        self.drawn = []

    def remove(self, item):
        self.drawn = [d for d in self.drawn if d[0] != item]

    def move(self, item, dx, dy):
        for i, d in enumerate(self.drawn):
            if d[0] == item:
                self.drawn[i] = d[:2] + (d[2] + dx, d[3] + dy) + d[4:]

    def size(self):
        return [self.spec.get("width", 400), self.spec.get("height", 300)]

    # link
    def set_url(self, url):
        self.url = url

    # -- simulating a person ---------------------------------------------------

    @property
    def text(self):
        """What the widget shows (label/button text, input value, ...)."""
        return self.state

    def _fire(self, name, *args):
        cb = self.spec.get(name)
        if cb is not None and self.enabled:
            cb(*args)

    def click(self, x=None, y=None):
        """Click a button, a link, a colour swatch, or a point on a canvas."""
        if self.kind == "canvas":
            self._fire("on_click", x or 0, y or 0)
        elif self.kind == "link":
            self._fire("on_click")
            if self.url:
                self.window.backend.open_url(self.url)
        elif self.kind == "color":
            got = self.window.backend.pick_colour(self.state, "Choose a colour")
            if got:
                self.state = got
                self._fire("on_change")
        else:
            assert self.kind == "button", f"can't click a {self.kind}"
            self._fire("on_click")

    def drag(self, x, y):
        assert self.kind == "canvas"
        self._fire("on_drag", x, y)

    def type(self, text):
        assert self.kind in ("input", "textarea", "date_picker"), f"can't type into a {self.kind}"
        self.state = text
        self._fire("on_change")

    def enter(self):
        assert self.kind == "input"
        self._fire("on_enter")

    def toggle(self):
        assert self.kind == "checkbox"
        self.state = not self.state
        self._fire("on_change")

    def slide(self, value):
        """Move a slider or spinner to a value."""
        assert self.kind in ("slider", "spinner"), f"can't slide a {self.kind}"
        self.state = value
        self._fire("on_change")

    def choose(self, which):
        """select: choose an option (text); table/list_box: select row i; tree: select a node id."""
        if self.kind == "select":
            assert which in self.items, f"{which!r} is not one of {self.items}"
            self.state = which
            self._fire("on_change")
        elif self.kind == "tree":
            assert which in self.nodes, f"no tree node {which!r}"
            self.selected = which
            self._fire("on_select")
        else:
            assert self.kind in ("table", "list_box")
            self.selected = which
            self._fire("on_select")

    def open(self, index):
        """Double-click a row of a table or list_box, or a node of a tree."""
        self.selected = index
        self._fire("on_open")

    def hover(self, x, y):
        """Charts: the tooltip a mouse at (x, y) would show is not modelled; this is a no-op."""

    def __repr__(self):
        return f"<FakeWidget {self.kind} {self.state!r}>"


class FakeContainer:
    """A row, column, card, scroll area, tabs page or splitter pane."""

    def __init__(self, window, kind, spec=None):
        self.window = window
        self.kind = kind
        self.spec = spec or {}
        self.children = []
        self.enabled = True

    def add(self, kind, spec):
        if kind in ("row", "column", "card", "scroll"):
            child = FakeContainer(self.window, kind, spec)
        elif kind == "tabs":
            child = FakeTabs(self.window, spec)
        elif kind == "splitter":
            child = FakeSplitter(self.window, spec)
        else:
            child = FakeWidget(self.window, kind, spec)
        self.children.append(child)
        return child

    def clear(self):
        self.children = []

    def set_enabled(self, flag):
        self.enabled = bool(flag)

    def walk(self):
        for child in self.children:
            yield child
            if isinstance(child, FakeContainer):
                yield from child.walk()


class FakeTabs(FakeContainer):
    """Tabs: .pages maps a name to its page container; .selected is the shown name."""

    def __init__(self, window, spec):
        super().__init__(window, "tabs", spec)
        self.pages = {}
        self.current = None
        for name in spec.get("names", []):
            self.add_page(name)

    def add_page(self, name):
        page = FakeContainer(self.window, "page", {"name": name})
        self.pages[name] = page
        self.children.append(page)
        if self.current is None:
            self.current = name
        return page

    def page(self, name):
        return self.pages[name]

    def select(self, name):
        self.current = name

    def selected(self):
        return self.current

    def choose(self, name):
        """Click a tab."""
        assert name in self.pages, f"no tab {name!r}"
        self.current = name
        cb = self.spec.get("on_change")
        if cb:
            cb()

    def clear(self):
        self.pages, self.children, self.current = {}, [], None


class FakeSplitter(FakeContainer):
    """A headless splitter: each pane() is a FakeContainer."""
    def __init__(self, window, spec):
        super().__init__(window, "splitter", spec)
        self.panes = []

    def pane(self, weight=1):
        pane = FakeContainer(self.window, "pane", {"weight": weight})
        self.panes.append(pane)
        self.children.append(pane)
        return pane

    def clear(self):
        self.panes, self.children = [], []


class FakeTray:
    """A headless tray chip that records whether it is shown and can pick menu items."""
    def __init__(self, window, title, items):
        self.window, self.title, self.items = window, title, items
        self.shown = False

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False

    def minimize(self):
        self.window.hidden = True
        self.shown = True

    def restore(self):
        self.shown = False
        self.window.hidden = False

    def destroy(self):
        self.shown = False

    def choose(self, label):
        """Pick an item from the tray menu."""
        for item in self.items:
            if item is not None and item[0] == label:
                item[1]()
                return
        raise AssertionError(f"no tray item {label!r}")


class FakeWindow:
    """A headless window: records widgets, timers, keys, menus and background work for tests."""
    def __init__(self, backend, title, width, height, theme, resizable, accent=None):
        self.backend = backend
        self.title = title
        self.width, self.height, self.theme, self.resizable = width, height, theme, resizable
        self.accent = accent
        self.body = FakeContainer(self, "column")
        self.menu = []
        self.status_text = None
        self.keys = {}
        self.any_key = None
        self.open = True
        self.ran = False
        self._timers = set()
        self.icon = None
        self.topmost = False
        self.fullscreen = False
        self.centered = False
        self.hidden = False
        self.close_cb = None
        self.drop_cb = None
        self.tray_widget = None
        self._pending = []

    # -- handle protocol -------------------------------------------------------

    def set_title(self, title):
        self.title = title

    def get_title(self):
        return self.title

    def set_menu(self, spec):
        self.menu = spec

    def status(self, text):
        self.status_text = text

    def get_status(self):
        return self.status_text

    def bind_key(self, sequence, cb):
        self.keys[sequence] = cb

    def bind_any_key(self, cb):
        self.any_key = cb

    def after(self, ms, cb):
        return self.backend._schedule(self, ms, cb)

    def cancel(self, token):
        self.backend._cancel(token)

    def run(self):
        self.ran = True

    def close(self):
        if self.open:
            self.open = False
            for token in list(self._timers):
                self.backend._cancel(token)

    def is_open(self):
        return self.open

    def set_icon(self, path):
        self.icon = path

    def set_topmost(self, flag):
        self.topmost = bool(flag)

    def set_fullscreen(self, flag):
        self.fullscreen = bool(flag)

    def is_fullscreen(self):
        return self.fullscreen

    def center(self):
        self.centered = True

    def resize(self, width, height):
        self.width, self.height = width, height

    def size(self):
        return [self.width, self.height]

    def on_close(self, cb):
        self.close_cb = cb

    def on_drop(self, cb):
        self.drop_cb = cb

    def tray(self, title, items):
        self.tray_widget = FakeTray(self, title, items)
        return self.tray_widget

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def is_shown(self):
        return not self.hidden

    def on_main(self, cb):
        """Fake windows run on one thread: cb runs on the next advance(0)."""
        self.after(0, cb)

    def background(self, work, done):
        """No real thread here: the work runs now and done() is scheduled for
        the next advance(), like a result arriving a moment later. Set
        backend.threads = True to use a real thread instead."""
        if self.backend.threads:
            import threading

            def target():
                ok, value = work()
                self._pending.append(lambda: done(ok, value))

            threading.Thread(target=target, daemon=True).start()
            return
        ok, value = work()
        self.after(0, lambda: done(ok, value))

    def deliver(self):
        """Run results that background threads have finished (threads=True)."""
        jobs, self._pending = self._pending, []
        for job in jobs:
            job()

    # -- simulating a person ---------------------------------------------------

    def widgets(self, kind=None):
        return [w for w in self.body.walk() if kind is None or w.kind == kind]

    def find(self, kind=None, text=None):
        """The first widget of that kind (and with that text, if given)."""
        for w in self.widgets(kind):
            if text is None or getattr(w, "state", None) == text:
                return w
        raise AssertionError(f"no {kind or 'widget'} with text {text!r} in {self.widgets()}")

    def menu_item(self, *path):
        items = self.menu
        for name in path:
            for entry in items:
                if entry is not None and entry[0] == name:
                    items = entry[1]
                    break
            else:
                raise AssertionError(f"no menu item {name!r}")
        return items

    def choose_menu(self, *path):
        self.menu_item(*path)()

    def press(self, combo):
        """Press keys: fires a shortcut bound with key(), and on_key if set."""
        seq = key_sequence(combo)
        if self.any_key is not None:
            self.any_key(combo)
        if seq in self.keys:
            self.keys[seq]()
        elif self.any_key is None:
            raise AssertionError(f"nothing bound to {combo} ({seq})")

    def request_close(self):
        """Click the window's close box (on_close may refuse)."""
        if self.close_cb is not None and self.close_cb() is False:
            return False
        self.close()
        return True

    def drop(self, paths):
        """Drop files on the window."""
        assert self.drop_cb is not None, "the window has no on_drop"
        self.drop_cb(list(paths))


class FakeBackend:
    """A backend with no screen. answers: what dialogs reply, in order
    (confirm -> bool, ask/open_file/save_file/pick_folder/pick_colour -> text or None).
    notifications: what gui.notify() sent. opened_urls: links that were clicked."""

    def __init__(self, answers=None):
        self.windows = []
        self.dialogs = []
        self.notifications = []
        self.opened_urls = []
        self.answers = list(answers or [])
        self.now = 0
        self._heap = []
        self._seq = 0
        self._live = {}
        self.threads = False
        self.scaling = 1.0
        self.main_queue = []

    def window(self, title, width, height, theme, resizable, accent=None):
        win = FakeWindow(self, title, width, height, theme, resizable, accent)
        self.windows.append(win)
        return win

    def _answer(self, default):
        return self.answers.pop(0) if self.answers else default

    def alert(self, message, title, error=False):
        self.dialogs.append({"kind": "error" if error else "alert", "title": title, "message": message})

    def confirm(self, message, title):
        self.dialogs.append({"kind": "confirm", "title": title, "message": message})
        return bool(self._answer(True))

    def ask(self, message, title, default):
        self.dialogs.append({"kind": "ask", "title": title, "message": message})
        return self._answer(default)

    def open_file(self, title, filters):
        self.dialogs.append({"kind": "open_file", "title": title, "filters": filters})
        return self._answer(None)

    def save_file(self, title, filters, name):
        self.dialogs.append({"kind": "save_file", "title": title, "filters": filters, "name": name})
        return self._answer(None)

    def pick_folder(self, title):
        self.dialogs.append({"kind": "pick_folder", "title": title})
        return self._answer(None)

    def pick_colour(self, initial, title):
        self.dialogs.append({"kind": "pick_colour", "title": title, "initial": initial})
        return self._answer(None)

    def open_url(self, url):
        self.opened_urls.append(url)

    def notify(self, title, text):
        self.notifications.append({"title": title, "text": text})
        return True

    def scale(self, factor=None):
        if factor is not None:
            self.scaling = float(factor)
        return self.scaling

    def on_main(self, cb):
        for win in reversed(self.windows):
            if win.is_open():
                win.on_main(cb)
                return
        self.main_queue.append(cb)

    clipboard = ""

    def copy(self, text):
        self.clipboard = text

    def paste(self):
        return self.clipboard

    # -- simulated time ---------------------------------------------------------

    def _schedule(self, window, ms, cb):
        self._seq += 1
        token = self._seq
        heapq.heappush(self._heap, (self.now + ms, token))
        self._live[token] = (window, cb)
        window._timers.add(token)
        return token

    def _cancel(self, token):
        entry = self._live.pop(token, None)
        if entry:
            entry[0]._timers.discard(token)

    def advance(self, ms):
        """Move the clock on by ms milliseconds, firing timers that are due."""
        target = self.now + ms
        fired = 0
        while self._heap and self._heap[0][0] <= target:
            due, token = heapq.heappop(self._heap)
            entry = self._live.pop(token, None)
            if entry is None:
                continue
            entry[0]._timers.discard(token)
            self.now = due
            entry[1]()
            fired += 1
            if fired > 100_000:  # pragma: no cover - a runaway timer in a test
                raise AssertionError("too many timers")
        self.now = target

    @property
    def window_(self):
        """The most recent window (handy in tests)."""
        return self.windows[-1]


# ---------------------------------------------------------------------------
# The real backend: tkinter + ttk
# ---------------------------------------------------------------------------


def _tk():
    """Import tkinter and ttk, or raise GuiUnavailable with a friendly message."""
    try:
        import tkinter
        from tkinter import ttk
    except ImportError:
        raise GuiUnavailable(
            "The gui library needs tkinter, which isn't part of this Python. "
            "Install Python from python.org (it comes with tkinter) or use the TB app."
        ) from None
    return tkinter, ttk


def _make_fonts(root):
    """The named fonts (normal, bold, title, mono...) used by every window."""
    from tkinter import font as tkfont

    base = tkfont.nametofont("TkDefaultFont", root=root)
    family = base.actual("family")
    size = abs(int(base.actual("size"))) or 10
    if sys.platform == "win32":
        family, size = "Segoe UI", 10
    elif sys.platform == "darwin":
        size = 13
    else:
        size = max(size, 10)
    mono = tkfont.nametofont("TkFixedFont", root=root).actual("family")

    def font(**kw):
        return tkfont.Font(root=root, family=kw.pop("family", family), **kw)

    return {
        "normal": font(size=size), "bold": font(size=size, weight="bold"),
        "heading": font(size=size + 3, weight="bold"), "title": font(size=size + 9, weight="bold"),
        "small": font(size=max(size - 2, 8)), "display": font(size=size + 19),
        "mono": font(family=mono, size=size),
        "link": font(size=size, underline=True), "tiny": font(size=max(size - 3, 7)),
    }


def _apply_style(root, p, P, fonts):
    """Register the ttk styles for one palette p under the prefix P."""
    from tkinter import ttk

    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    body = fonts["normal"]
    style.configure(f"{P}.TFrame", background=p["bg"])
    style.configure(f"{P}.TLabel", background=p["bg"], foreground=p["fg"], font=body)
    for name, extra in (("title", {}), ("heading", {}), ("bold", {}), ("small", {"foreground": p["muted"]}),
                        ("display", {}), ("muted", {"foreground": p["muted"]}),
                        ("accent", {"foreground": p["accent_text"]}),
                        ("success", {"foreground": p["success"], "font": fonts["bold"]}),
                        ("danger", {"foreground": p["danger"], "font": fonts["bold"]}), ("mono", {})):
        font = fonts.get(name, body)
        style.configure(f"{P}.{name.capitalize()}.TLabel", **dict({"background": p["bg"], "foreground": p["fg"], "font": font}, **extra))
    style.configure(f"{P}.Status.TLabel", background=p["surface"], foreground=p["muted"],
                    font=fonts["small"], padding=(14, 5))
    common = {"bordercolor": p["border"], "focuscolor": p["accent"], "font": body, "padding": (14, 7),
              "relief": "flat"}
    style.configure(f"{P}.TButton", background=p["button"], foreground=p["fg"],
                    lightcolor=p["button"], darkcolor=p["button"], **common)
    style.map(f"{P}.TButton",
              background=[("disabled", p["bg"]), ("pressed", p["border"]), ("active", p["button_hover"])],
              lightcolor=[("active", p["button_hover"])], darkcolor=[("active", p["button_hover"])],
              foreground=[("disabled", p["muted"])])
    style.configure(f"{P}.Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                    lightcolor=p["accent"], darkcolor=p["accent"], **dict(common, bordercolor=p["accent"],
                                                                          focuscolor=p["accent_fg"]))
    style.map(f"{P}.Accent.TButton",
              background=[("disabled", p["border"]), ("pressed", p["accent_hover"]), ("active", p["accent_hover"])],
              lightcolor=[("active", p["accent_hover"])], darkcolor=[("active", p["accent_hover"])],
              foreground=[("disabled", p["muted"])])
    entry = {"fieldbackground": p["field"], "foreground": p["fg"], "bordercolor": p["border"],
             "lightcolor": p["field"], "darkcolor": p["field"], "insertcolor": p["fg"], "padding": 7,
             "selectbackground": p["select"], "selectforeground": p["select_fg"]}
    style.configure(f"{P}.TEntry", **entry)
    style.map(f"{P}.TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])])
    style.configure(f"{P}.Placeholder.TEntry", **dict(entry, foreground=p["muted"]))
    style.map(f"{P}.Placeholder.TEntry", bordercolor=[("focus", p["accent"])])
    style.configure(f"{P}.TCheckbutton", background=p["bg"], foreground=p["fg"], font=body,
                    indicatorbackground=p["field"], indicatorforeground=p["accent_fg"],
                    upperbordercolor=p["border"], lowerbordercolor=p["border"], focuscolor=p["bg"])
    style.map(f"{P}.TCheckbutton", background=[("active", p["bg"])],
              indicatorbackground=[("selected", p["accent"]), ("active", p["button_hover"])],
              foreground=[("disabled", p["muted"])])
    style.configure(f"{P}.TCombobox", fieldbackground=p["field"], background=p["button"],
                    foreground=p["fg"], arrowcolor=p["fg"], bordercolor=p["border"],
                    lightcolor=p["field"], darkcolor=p["field"], padding=6,
                    selectbackground=p["field"], selectforeground=p["fg"])
    style.map(f"{P}.TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("disabled", p["muted"]), ("readonly", p["fg"])],
              selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])],
              bordercolor=[("focus", p["accent"])], background=[("active", p["button_hover"])])
    root.option_add("*TCombobox*Listbox.background", p["field"])
    root.option_add("*TCombobox*Listbox.foreground", p["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["accent_fg"])
    root.option_add("*TCombobox*Listbox.font", body)
    rowheight = int(body.metrics("linespace") * 1.9)
    style.configure(f"{P}.Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"],
                    bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                    rowheight=rowheight, font=body)
    style.map(f"{P}.Treeview", background=[("selected", p["select"])], foreground=[("selected", p["select_fg"])])
    style.configure(f"{P}.Treeview.Heading", background=p["surface"], foreground=p["fg"], font=fonts["bold"],
                    relief="flat", bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                    padding=(8, 6))
    style.map(f"{P}.Treeview.Heading", background=[("active", p["button_hover"])])
    style.configure(f"{P}.Horizontal.TProgressbar", background=p["accent"], troughcolor=p["button"],
                    bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"], thickness=10)
    style.configure(f"{P}.TSeparator", background=p["border"])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{P}.{orient}.TScrollbar", background=p["button"], troughcolor=p["bg"],
                        arrowcolor=p["muted"], bordercolor=p["bg"], lightcolor=p["button"], darkcolor=p["button"],
                        gripcount=0)
        style.map(f"{P}.{orient}.TScrollbar", background=[("active", p["button_hover"])])
    # 3.0 widgets
    style.configure(f"{P}.TNotebook", background=p["bg"], bordercolor=p["border"], lightcolor=p["bg"],
                    darkcolor=p["bg"], tabmargins=(0, 4, 0, 0), padding=0)
    style.configure(f"{P}.TNotebook.Tab", background=p["surface"], foreground=p["muted"], font=fonts["bold"],
                    padding=(16, 8), bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                    focuscolor=p["bg"])
    style.map(f"{P}.TNotebook.Tab", background=[("selected", p["bg"]), ("active", p["button_hover"])],
              foreground=[("selected", p["accent_text"])], lightcolor=[("selected", p["bg"])])
    style.configure(f"{P}.Card.TFrame", background=p["surface"], bordercolor=p["border"], relief="solid",
                    borderwidth=1, lightcolor=p["border"], darkcolor=p["border"])
    style.configure(f"{P}.Horizontal.TScale", background=p["accent"], troughcolor=p["button"],
                    bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"], sliderlength=20,
                    gripcount=0)
    style.map(f"{P}.Horizontal.TScale", background=[("active", p["accent_hover"])])
    style.configure(f"{P}.TSpinbox", fieldbackground=p["field"], foreground=p["fg"], background=p["button"],
                    arrowcolor=p["fg"], bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                    padding=6, insertcolor=p["fg"], arrowsize=14, selectbackground=p["select"],
                    selectforeground=p["select_fg"])
    style.map(f"{P}.TSpinbox", bordercolor=[("focus", p["accent"])], background=[("active", p["button_hover"])])
    style.configure(f"{P}.TPanedwindow", background=p["bg"])
    style.configure("Sash", sashthickness=6, gripcount=0, sashpad=0)
    style.configure(f"{P}.Link.TLabel", background=p["bg"], foreground=p["accent_text"], font=fonts["link"])
    style.map(f"{P}.Link.TLabel", background=[("focus", p["select"])])   # visible keyboard focus


_TK_SHARED = {"root": None, "fonts": None, "styled": set()}


def _tk_root():
    """The one Tk interpreter of this process: a hidden root window that
    every TB window hangs off (as a Toplevel). Making a second Tk() after
    destroying the first can crash Tk on some systems, so it is made once
    and kept for the life of the program."""
    root = _TK_SHARED["root"]
    if root is not None:
        try:
            root.winfo_exists()
            return root
        except Exception:
            _TK_SHARED["root"] = None
    tk, _ = _tk()
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        root = tk.Tk()
    except tk.TclError as err:
        raise GuiUnavailable(f"I couldn't open a window: there's no screen to show it on ({err}).") from None
    root.withdraw()

    def report(exc, value, trace):  # safety net: never print a Python traceback
        print(_tb.crash_message(value), file=sys.stderr)

    root.report_callback_exception = report
    _TK_SHARED.update(root=root, fonts=_make_fonts(root), styled=set())
    return root


class _TkBackend:
    """The real backend: Tk windows, dialogs, clipboard, notifications."""
    def __init__(self):
        self.windows = []

    @property
    def root(self):
        return _tk_root()

    @property
    def fonts(self):
        _tk_root()
        return _TK_SHARED["fonts"]

    def styled(self, theme, accent=None, surface=False):
        """Make sure the ttk styles for this palette exist; gives (prefix, palette)."""
        root = _tk_root()
        P = _prefix(theme, accent, surface)
        p = _palette(theme, accent, surface)
        if P not in _TK_SHARED["styled"]:
            _apply_style(root, p, P, _TK_SHARED["fonts"])
            _TK_SHARED["styled"].add(P)
        return P, p

    def window(self, title, width, height, theme, resizable, accent=None):
        tk, _ = _tk()
        top = tk.Toplevel(_tk_root())
        win = _TkWindow(self, top, title, width, height, theme, resizable, accent)
        self.windows.append(win)
        return win

    def scale(self, factor=None):
        """Tk's scaling (pixels per point); set it to make everything bigger or smaller."""
        root = _tk_root()
        if factor is not None:
            root.tk.call("tk", "scaling", float(factor))
            _TK_SHARED.update(fonts=_make_fonts(root), styled=set())
        return float(root.tk.call("tk", "scaling"))

    def pick_colour(self, initial, title):
        from tkinter import colorchooser
        got = colorchooser.askcolor(color=initial or None, title=title, parent=self._parent())
        return got[1] if got and got[1] else None

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def notify(self, title, text):
        """A desktop notification through the operating system; False if that
        isn't possible here (the caller then falls back to a dialog)."""
        return _os_notify(title, text)

    def on_main(self, cb):
        """Run cb on the Tk thread as soon as the loop is idle."""
        for win in reversed(self.windows):
            if win.is_open():
                win.on_main(cb)
                return
        _tk_root().after(0, cb)

    def _parent(self):
        for win in reversed(self.windows):
            if win.is_open():
                return win.top
        return _tk_root()

    def alert(self, message, title, error=False):
        from tkinter import messagebox
        (messagebox.showerror if error else messagebox.showinfo)(title, message, parent=self._parent())

    def confirm(self, message, title):
        from tkinter import messagebox
        return bool(messagebox.askyesno(title, message, parent=self._parent()))

    def ask(self, message, title, default):
        from tkinter import simpledialog
        return simpledialog.askstring(title, message, initialvalue=default or "", parent=self._parent())

    def open_file(self, title, filters):
        from tkinter import filedialog
        kw = {"filetypes": filters} if filters else {}
        return filedialog.askopenfilename(title=title, parent=self._parent(), **kw) or None

    def save_file(self, title, filters, name):
        from tkinter import filedialog
        kw = {"filetypes": filters} if filters else {}
        if name:
            kw["initialfile"] = name
        return filedialog.asksaveasfilename(title=title, parent=self._parent(), **kw) or None

    def pick_folder(self, title):
        from tkinter import filedialog
        return filedialog.askdirectory(title=title, parent=self._parent()) or None

    def copy(self, text):
        root = _tk_root()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()  # so the text stays on the clipboard

    def paste(self):
        try:
            return _tk_root().clipboard_get()
        except Exception:
            return ""


class _TkWindow:
    """One real Tk window (the root for the first, a Toplevel after that)."""
    def __init__(self, backend, top, title, width, height, theme, resizable, accent=None):
        _, ttk = _tk()
        self.backend, self.top = backend, top
        self.theme, self.accent = theme, accent
        self.P, self.pal = backend.styled(theme, accent)
        self.fonts = backend.fonts
        top.title(title)
        top.configure(background=self.pal["bg"])
        top.minsize(min(width, 280), min(height, 160))
        top.resizable(resizable, resizable)
        self.width, self.height = width, height
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        x, y = max((sw - width) // 2, 0), max((sh - height) // 3, 0)
        top.geometry(f"{width}x{height}+{x}+{y}")
        top.protocol("WM_DELETE_WINDOW", self._close_requested)
        self.open = True
        self.afters = set()
        self.status_bar = None
        self.close_cb = None
        self.drop_cb = None
        self.tray_widget = None
        self.photo = None
        self._queue = []
        self._poller = None
        self._lock = None
        self.body_frame = ttk.Frame(top, style=f"{self.P}.TFrame", padding=18)
        self.body_frame.pack(side="top", fill="both", expand=True)
        self.body = _TkContainer(self, self.body_frame, "column", self.P, self.pal)

    def set_title(self, title):
        self.top.title(title)

    def get_title(self):
        return self.top.title()

    def set_menu(self, spec):
        tk, _ = _tk()
        p = self.pal

        def build(parent, items):
            menu = tk.Menu(parent, tearoff=0, background=p["field"], foreground=p["fg"],
                           activebackground=p["accent"], activeforeground=p["accent_fg"],
                           font=self.fonts["normal"], borderwidth=0)
            for item in items:
                if item is None:
                    menu.add_separator()
                elif isinstance(item[1], list):
                    menu.add_cascade(label=item[0], menu=build(menu, item[1]))
                else:
                    menu.add_command(label=item[0], command=item[1])
            return menu

        bar = tk.Menu(self.top, tearoff=0)
        for label, items in spec:
            bar.add_cascade(label=label, menu=build(bar, items))
        self.top.configure(menu=bar)

    def status(self, text):
        _, ttk = _tk()
        if self.status_bar is None:
            self.status_bar = ttk.Label(self.top, style=f"{self.P}.Status.TLabel", anchor="w")
            self.status_bar.pack(side="bottom", fill="x", before=self.body_frame)
            tk, _ = _tk()
            tk.Frame(self.top, height=1, background=self.pal["border"]).pack(side="bottom", fill="x",
                                                                            before=self.body_frame)
        self.status_bar.configure(text=text)

    def get_status(self):
        return None if self.status_bar is None else str(self.status_bar.cget("text"))

    def bind_key(self, sequence, cb):
        self.top.bind(sequence, lambda event: cb())

    def bind_any_key(self, cb):
        """cb(combo) for every key pressed in the window ("a", "Ctrl+S", "Enter"...)."""
        self.top.bind("<Key>", lambda event: cb(combo_from_event(event)), add=True)

    def after(self, ms, cb):
        holder = {}

        def go():
            self.afters.discard(holder.get("id"))
            cb()

        holder["id"] = self.top.after(ms, go)
        self.afters.add(holder["id"])
        return holder["id"]

    def cancel(self, token):
        self.afters.discard(token)
        try:
            self.top.after_cancel(token)
        except Exception:
            pass

    def run(self):
        if not self.open:
            return
        try:
            self.top.lift()
            if not self.top.attributes("-topmost"):
                self.top.attributes("-topmost", True)
                self.top.after(300, lambda: self.open and not self._pinned and self.top.attributes("-topmost", False))
            self.top.focus_force()
        except Exception:
            pass
        self.top.wait_window()

    _pinned = False

    def _close_requested(self):
        """The person clicked the window's close box."""
        if self.close_cb is not None and self.close_cb() is False:
            return
        self.close()

    def close(self):
        if not self.open:
            return
        self.open = False
        for token in list(self.afters):
            self.cancel(token)
        if self.tray_widget is not None:
            self.tray_widget.destroy()
        try:
            self.top.destroy()
        except Exception:
            pass

    def is_open(self):
        return self.open

    # -- 3.0 extras ------------------------------------------------------------

    def set_icon(self, path):
        tk, _ = _tk()
        try:
            self.photo = tk.PhotoImage(master=self.top, file=path)
        except tk.TclError:
            raise _Problem(f"I couldn't use '{path}' as the window icon. Is it a PNG or GIF file?") from None
        self.top.iconphoto(False, self.photo)

    def set_topmost(self, flag):
        self._pinned = bool(flag)
        self.top.attributes("-topmost", bool(flag))

    def set_fullscreen(self, flag):
        self.top.attributes("-fullscreen", bool(flag))

    def is_fullscreen(self):
        return bool(self.top.attributes("-fullscreen"))

    def center(self):
        self.top.update_idletasks()
        w, h = self.top.winfo_width(), self.top.winfo_height()
        if w <= 1 or h <= 1:
            w, h = self.width, self.height
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self.top.geometry(f"+{max((sw - w) // 2, 0)}+{max((sh - h) // 2, 0)}")

    def resize(self, width, height):
        self.width, self.height = width, height
        self.top.geometry(f"{width}x{height}")

    def size(self):
        self.top.update_idletasks()
        w, h = self.top.winfo_width(), self.top.winfo_height()
        return [self.width, self.height] if w <= 1 or h <= 1 else [w, h]

    def on_close(self, cb):
        self.close_cb = cb

    def on_drop(self, cb):
        """Files dropped on the window (natively on Windows and macOS, with tkdnd
        elsewhere), on the app's Dock icon on a Mac, or pasted into the window."""
        from tblib import _gui_native
        first = self.drop_cb is None
        self.drop_cb = cb
        self.drop_kind = _gui_native.enable_drop(self.top, cb)  # a second call swaps the callback
        if first:
            self.top.bind("<<Paste>>", self._paste_paths, add=True)
        if sys.platform == "darwin":
            def open_documents(*paths):
                found = [p for p in paths if os.path.exists(p)]
                if found:
                    cb(found)
            try:
                self.top.createcommand("::tk::mac::OpenDocument", open_documents)
            except Exception:
                pass

    def _paste_paths(self, event=None):
        if self.drop_cb is None:
            return
        try:
            text = self.top.clipboard_get()
        except Exception:
            return
        paths = paths_in_text(text)
        if paths:
            self.drop_cb(paths)

    def tray(self, title, items):
        """A real tray icon on Windows, a menu bar item on macOS. Elsewhere (or if
        that fails) a tiny always-on-top chip in the corner of the screen."""
        from tblib import _gui_native
        if self.tray_widget is not None:
            self.tray_widget.destroy()
        native = _gui_native.make_tray(self.top, title, items, lambda: self.tray_widget.restore())
        self.tray_widget = _NativeTray(self, native) if native else _TkTray(self, title, items)
        return self.tray_widget

    def hide(self):
        self.top.withdraw()

    def show(self):
        self.top.deiconify()
        self.top.lift()
        try:
            self.top.focus_force()
        except Exception:
            pass

    def is_shown(self):
        return self.top.state() != "withdrawn"

    # -- background work: a thread does the work, the Tk thread gets the result -

    def on_main(self, cb):
        """Run cb on the Tk thread (safe to call from any thread)."""
        import threading
        if self._lock is None:
            self._lock = threading.Lock()
        with self._lock:
            self._queue.append(cb)
        if threading.current_thread() is threading.main_thread():
            self._drain()
        else:  # a parallel task: ask Tk (thread-safe with threaded Tcl) to drain soon
            try:
                self.top.after(0, self._drain)
            except Exception:
                pass  # the background() poller still picks it up

    def _drain(self):
        if not self.open:
            return
        with self._lock:
            jobs, self._queue = self._queue, []
        for job in jobs:
            job()

    def _poll(self):
        self._poller = None
        if not self.open:
            return
        self._drain()
        if self._jobs:
            self._poller = self.top.after(40, self._poll)

    _jobs = 0

    def background(self, work, done):
        """Run work() in a thread, then done(ok, value) on the Tk thread."""
        import threading
        if self._lock is None:
            self._lock = threading.Lock()
        self._jobs += 1

        def finish(ok, value):
            self._jobs -= 1
            if self.open:
                done(ok, value)

        def target():
            try:
                ok, value = work()
            except Exception as err:  # pragma: no cover - work() catches its own
                ok, value = False, err
            self.on_main(lambda: finish(ok, value))

        threading.Thread(target=target, daemon=True).start()
        if self._poller is None:
            self._poller = self.top.after(40, self._poll)


class _NativeTray:
    """The window side of a native tray icon (tblib/_gui_native.py)."""

    def __init__(self, win, icon):
        self.win, self.icon = win, icon
        self.native = icon.native
        if sys.platform == "darwin":
            try:  # clicking the Dock icon also brings the window back
                win.top.createcommand("::tk::mac::ReopenApplication", self.restore)
            except Exception:
                pass

    @property
    def shown(self):
        return self.icon.shown

    def show(self):
        self.icon.show()

    def hide(self):
        self.icon.hide()

    def minimize(self):
        self.win.hide()
        self.show()

    def restore(self):
        self.hide()
        self.win.show()

    def destroy(self):
        self.icon.destroy()


class _TkTray:
    """A small always-on-top window standing in for a system tray icon."""
    native = None

    def __init__(self, win, title, items):
        tk, _ = _tk()
        self.win, self.items = win, items
        p = win.pal
        self.top = tk.Toplevel(win.top)
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        label = tk.Label(self.top, text=f"● {title}", background=p["accent"], foreground=p["accent_fg"],
                         font=win.fonts["small"], padx=10, pady=4, cursor="hand2")
        label.pack()
        label.bind("<Button-1>", self.popup)
        label.bind("<Double-Button-1>", lambda e: self.restore())
        self.menu = tk.Menu(self.top, tearoff=0, background=p["field"], foreground=p["fg"],
                            activebackground=p["accent"], activeforeground=p["accent_fg"], font=win.fonts["normal"])
        for item in items:
            if item is None:
                self.menu.add_separator()
            else:
                self.menu.add_command(label=item[0], command=item[1])
        self.top.update_idletasks()
        sw = self.top.winfo_screenwidth()
        self.top.geometry(f"+{sw - self.top.winfo_reqwidth() - 24}+{24}")
        self.top.withdraw()
        self.shown = False
        if sys.platform == "darwin":
            try:  # clicking the Dock icon also brings the window back
                win.top.createcommand("::tk::mac::ReopenApplication", self.restore)
            except Exception:
                pass

    def popup(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def show(self):
        self.top.deiconify()
        self.shown = True

    def hide(self):
        self.top.withdraw()
        self.shown = False

    def minimize(self):
        """Hide the window and show the tray chip."""
        self.win.hide()
        self.show()

    def restore(self):
        self.hide()
        self.win.show()

    def destroy(self):
        try:
            self.top.destroy()
        except Exception:
            pass


def combo_from_event(event):
    """A Tk key event -> text like "a", "Ctrl+S", "Shift+Enter", "F5"."""
    state = int(getattr(event, "state", 0) or 0)
    mods = []
    if state & 0x4:
        mods.append("Ctrl")
    if sys.platform == "darwin":
        if state & 0x8:
            mods.append("Cmd")
        if state & 0x10:
            mods.append("Alt")
    elif state & 0x8 or state & 0x20000:
        mods.append("Alt")
    if state & 0x1:
        mods.append("Shift")
    sym = event.keysym or ""
    names = {"Return": "Enter", "KP_Enter": "Enter", "BackSpace": "Backspace", "Prior": "PageUp", "Next": "PageDown",
             "space": "Space", "plus": "+", "minus": "-", "asterisk": "*", "slash": "/", "period": ".",
             "equal": "=", "comma": ",", "percent": "%", "parenleft": "(", "parenright": ")"}
    if sym in names:
        key = names[sym]
    elif len(sym) == 1:
        key = sym.lower() if sym.isalpha() else sym
        if key.isalpha() and "Shift" in mods:
            mods.remove("Shift")
            key = key.upper()
    elif sym.startswith("KP_") and sym[3:].isdigit():
        key = sym[3:]
    else:
        key = sym
    return "+".join(mods + [key])


def _file_url_path(url):
    """The local path in a file:// link, in this OS's own form.

    Handles file:///home/me/a.txt, file://localhost/..., the Windows forms
    file:///C:/Users/me/a.txt and file://server/share/a.txt, and the
    not-quite-URLs some programs paste (file://C:\\Users\\me\\a.txt)."""
    from urllib.parse import unquote
    rest = unquote(url[7:])
    if rest[:10].lower() == "localhost/":
        rest = rest[9:]
    if sys.platform != "win32":
        return rest
    if rest.startswith("/") and rest[2:3] == ":":   # /C:/Users/...
        rest = rest[1:]
    elif not rest.startswith("/") and rest[1:2] != ":":   # server/share/... (UNC)
        rest = "//" + rest
    return os.path.normpath(rest)


def paths_in_text(text):
    """File paths found in pasted text (one per line, or file:// links)."""
    found = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.strip().strip('"').strip("'")
        if not line:
            continue
        if line[:7].lower() == "file://":
            line = _file_url_path(line)
        if os.path.exists(line):
            found.append(line)
    return found


def _os_notify(title, text):
    """Show a desktop notification with the operating system's own tools.
    macOS: osascript; Windows: a PowerShell toast; Linux: notify-send.
    Returns False when none of them worked."""
    import subprocess
    try:
        if sys.platform == "darwin":
            script = 'display notification {} with title {}'.format(_applescript_text(text), _applescript_text(title))
            done = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            return done.returncode == 0
        if sys.platform == "win32":
            # Toasts need a registered app id; PowerShell's own one always is, so borrow it.
            ps = (
                "$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe';"
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
                "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
                "$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{0}</text><text>{1}</text></binding></visual></toast>');"
                "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($t)"
            ).format(_xml_text(title), _xml_text(text))
            done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                  capture_output=True, timeout=10)
            return done.returncode == 0
        done = subprocess.run(["notify-send", title, text], capture_output=True, timeout=5)
        return done.returncode == 0
    except Exception:
        return False


def _applescript_text(text):
    """Text quoted as an AppleScript string literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _xml_text(text):
    """Text escaped for XML (and safe inside a PowerShell single-quoted string)."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("'", "&apos;").replace('"', "&quot;"))


class _TkContainer:
    """A frame that stacks widgets: "column" (top to bottom) or "row".
    P and pal are the ttk style prefix and palette its widgets use (a card
    gives its children a 'surface' palette)."""

    def __init__(self, win, frame, direction, P=None, pal=None):
        self.win, self.frame, self.direction = win, frame, direction
        self.P = P or win.P
        self.pal = pal or win.pal

    def clear(self):
        for child in self.frame.winfo_children():
            child.destroy()

    def set_enabled(self, flag):
        pass

    def _place(self, widget, grow, expand):
        column = self.direction == "column"
        if expand:
            grow, stretch = "both", True
        else:
            stretch = grow == "both"
            if expand is False and grow == "both":
                grow = "x" if column else "y"
        gap = (GAP if self.frame.pack_slaves() else 0, 0)  # space between widgets, not around them
        if column:
            widget.pack(side="top", fill=grow, expand=stretch, pady=gap,
                        anchor="w" if grow == "none" else "center")
        else:
            widget.pack(side="left", fill=grow, expand=stretch, padx=gap)

    def sub(self, frame, direction, P=None, pal=None):
        return _TkContainer(self.win, frame, direction, P or self.P, pal or self.pal)

    def add(self, kind, spec):
        tk, ttk = _tk()
        w, P, p, fonts = self.win, self.P, self.pal, self.win.fonts
        column = self.direction == "column"
        expand = spec.get("expand")
        if kind in ("row", "column"):
            frame = ttk.Frame(self.frame, style=f"{P}.TFrame")
            grow = "x" if (kind == "row" and column) else "both"
            self._place(frame, grow, expand)
            return self.sub(frame, kind)
        if kind == "card":
            handle = _TkCard(self, spec)
            self._place(handle.outer, "x" if column else "both", expand)
            return handle.inner
        if kind == "scroll":
            handle = _TkScroll(self, spec)
            self._place(handle.outer, "both", expand)
            return handle.inner
        if kind == "tabs":
            handle = _TkTabs(self, spec)
            self._place(handle.outer, "both", expand)
            return handle
        if kind == "splitter":
            handle = _TkSplitter(self, spec)
            self._place(handle.outer, "both", expand)
            return handle
        handle = _TK_WIDGETS[kind](self, spec, tk, ttk, P, p, fonts)
        # Keep the handle (and its Tk variables) alive as long as the widget,
        # even if the program throws away the map it got back.
        handle.outer._tb_handle = handle
        default_grow = {
            "label": "x" if column else "none", "button": "none", "checkbox": "x" if column else "none",
            "input": "x", "select": "x", "progress": "x", "separator": "x" if column else "y",
            "textarea": "both", "table": "both", "list_box": "both", "image": "none",
            "tree": "both", "chart": "both", "canvas": "none", "slider": "x", "spinner": "none",
            "date_picker": "none", "color": "none", "link": "x" if column else "none", "badge": "none",
        }[kind]
        if expand is None and not column and kind in ("input", "progress", "slider"):
            expand = True  # a text box in a row takes the space the buttons leave
        self._place(handle.outer, default_grow, expand)
        return handle



class _TkHandle:
    """Base for widget handles: enable/disable and focus."""
    outer = None
    widget = None

    def set_enabled(self, flag):
        try:
            self.widget.state(["!disabled"] if flag else ["disabled"])
        except AttributeError:
            self.widget.configure(state="normal" if flag else "disabled")

    def focus(self):
        self.widget.focus_set()


class _TkLabel(_TkHandle):
    """A ttk label with a named style."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        style = spec.get("style", "normal")
        name = f"{P}.TLabel" if style == "normal" else f"{P}.{style.capitalize()}.TLabel"
        anchor = {"left": "w", "center": "center", "right": "e"}[spec.get("align", "left")]
        self.widget = self.outer = ttk.Label(c.frame, text=spec.get("text", ""), style=name, anchor=anchor,
                                             justify={"w": "left", "center": "center", "e": "right"}[anchor],
                                             wraplength=spec.get("wrap", 0))
        if spec.get("width"):
            self.widget.configure(width=spec["width"])
        self.P = P

    def set_style(self, style):
        self.widget.configure(style=f"{self.P}.TLabel" if style == "normal" else f"{self.P}.{style.capitalize()}.TLabel")

    def get(self):
        return str(self.widget.cget("text"))

    def set(self, value):
        self.widget.configure(text=value)


class _TkButton(_TkHandle):
    """A ttk button (primary buttons use the accent style)."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        style = f"{P}.Accent.TButton" if spec.get("primary") else f"{P}.TButton"
        cb = spec.get("on_click")
        kw = {"width": spec["width"]} if spec.get("width") else {}
        self.widget = self.outer = ttk.Button(c.frame, text=spec.get("text", ""), style=style,
                                              command=(lambda: cb()) if cb else None, takefocus=True, **kw)

    def get(self):
        return str(self.widget.cget("text"))

    def set(self, value):
        self.widget.configure(text=value)


class _TkInput(_TkHandle):
    """A one-line entry with placeholder, on_enter and on_change."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.P = P
        self.var = tk.StringVar(master=c.frame)
        self.secret = "•" if spec.get("secret") else ""
        kw = {"width": spec["width"]} if spec.get("width") else {}
        self.widget = self.outer = ttk.Entry(c.frame, textvariable=self.var, style=f"{P}.TEntry",
                                             font=fonts["normal"], show=self.secret, **kw)
        self.placeholder = spec.get("placeholder") or ""
        self.showing = False
        self.muted = False
        self.var.set(spec.get("value", ""))
        self._show_placeholder()
        self.widget.bind("<FocusIn>", lambda e: self._hide_placeholder())
        self.widget.bind("<FocusOut>", lambda e: self._show_placeholder())
        if spec.get("on_enter"):
            enter = spec["on_enter"]
            self.widget.bind("<Return>", lambda e: enter())
            self.widget.bind("<KP_Enter>", lambda e: enter())
        if spec.get("on_change"):
            change = spec["on_change"]
            self.var.trace_add("write", lambda *a: None if (self.muted or self.showing) else change())

    def _show_placeholder(self):
        if self.placeholder and not self.var.get() and self.widget.focus_get() is not self.widget:
            self.muted = True
            self.var.set(self.placeholder)
            self.muted = False
            self.showing = True
            self.widget.configure(style=f"{self.P}.Placeholder.TEntry", show="")

    def _hide_placeholder(self):
        if self.showing:
            self.muted = True
            self.var.set("")
            self.muted = False
            self.showing = False
            self.widget.configure(style=f"{self.P}.TEntry", show=self.secret)

    def get(self):
        return "" if self.showing else self.var.get()

    def set(self, value):
        self._hide_placeholder()
        self.muted = True
        self.var.set(value)
        self.muted = False
        self._show_placeholder()


class _TkTextArea(_TkHandle):
    """A multi-line Text widget with a scrollbar."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        self.widget = tk.Text(
            self.outer, wrap="word" if spec.get("wrap", True) else "none", undo=True,
            font=fonts["mono" if spec.get("mono") else "normal"], height=spec.get("height", 5), width=20,
            background=p["field"], foreground=p["fg"], insertbackground=p["fg"],
            selectbackground=p["select"], selectforeground=p["select_fg"], relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=p["border"], highlightcolor=p["accent"], padx=12, pady=10,
        )
        bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.widget.yview, style=f"{P}.Vertical.TScrollbar")
        self.widget.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.widget.pack(side="left", fill="both", expand=True)
        self.muted = False
        if spec.get("value"):
            self.set(spec["value"])
        if spec.get("readonly"):
            self.widget.configure(state="disabled", insertofftime=0)
        if spec.get("on_change"):
            change = spec["on_change"]

            def modified(event):
                if self.widget.edit_modified():
                    self.widget.edit_modified(False)
                    if not self.muted:
                        change()

            self.widget.bind("<<Modified>>", modified)

    def get(self):
        return self.widget.get("1.0", "end-1c")

    def set(self, value):
        self.muted = True
        state = self.widget.cget("state")
        self.widget.configure(state="normal")
        self.widget.delete("1.0", "end")
        self.widget.insert("1.0", value)
        self.widget.edit_reset()
        self.widget.edit_modified(False)
        self.widget.configure(state=state)
        self.muted = False

    def append(self, text):
        self.muted = True
        state = self.widget.cget("state")
        self.widget.configure(state="normal")
        self.widget.insert("end", text)
        self.widget.see("end")
        self.widget.edit_modified(False)
        self.widget.configure(state=state)
        self.muted = False


class _TkCheckbox(_TkHandle):
    """A ttk checkbutton bound to a BooleanVar."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.var = tk.BooleanVar(master=c.frame, value=bool(spec.get("checked")))
        cb = spec.get("on_change")
        self.widget = self.outer = ttk.Checkbutton(c.frame, text=spec.get("text", ""), variable=self.var,
                                                   style=f"{P}.TCheckbutton", command=(lambda: cb()) if cb else None)

    def get(self):
        return bool(self.var.get())

    def set(self, value):
        self.var.set(bool(value))


class _TkSelect(_TkHandle):
    """A read-only ttk combobox."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.var = tk.StringVar(master=c.frame, value=spec.get("value", ""))
        kw = {"width": spec["width"]} if spec.get("width") else {}
        self.widget = self.outer = ttk.Combobox(c.frame, textvariable=self.var, values=spec.get("items", []),
                                                state="readonly", style=f"{P}.TCombobox", font=fonts["normal"], **kw)
        if spec.get("on_change"):
            change = spec["on_change"]
            self.widget.bind("<<ComboboxSelected>>", lambda e: change())

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)

    def set_items(self, items):
        self.widget.configure(values=list(items))

    def set_enabled(self, flag):
        self.widget.state(["!disabled", "readonly"] if flag else ["disabled"])


class _TkTable(_TkHandle):
    """A ttk Treeview used as a flat table with a scrollbar."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        columns = spec["columns"]
        ids = [f"c{i}" for i in range(len(columns))]
        self.widget = ttk.Treeview(self.outer, columns=ids, show="headings", style=f"{P}.Treeview",
                                   height=spec.get("height", 5), selectmode="browse")
        widths = spec.get("widths") or []
        for i, (cid, title) in enumerate(zip(ids, columns)):
            self.widget.heading(cid, text=title, anchor="w")
            width = widths[i] if i < len(widths) else 120
            self.widget.column(cid, width=width, minwidth=40, anchor="w", stretch=True)
        self.widget.tag_configure("odd", background=p["stripe"])
        bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.widget.yview, style=f"{P}.Vertical.TScrollbar")
        self.widget.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.widget.pack(side="left", fill="both", expand=True)
        self.muted = False
        if spec.get("on_select"):
            select = spec["on_select"]
            self.widget.bind("<<TreeviewSelect>>", lambda e: None if self.muted else select())
        if spec.get("on_open"):
            opener = spec["on_open"]

            def double(event):
                if self.widget.identify_row(event.y):
                    opener()

            self.widget.bind("<Double-1>", double)
            self.widget.bind("<Return>", lambda e: opener() if self.widget.selection() else None)

    def insert_row(self, texts):
        n = len(self.widget.get_children())
        self.widget.insert("", "end", values=list(texts), tags=("odd",) if n % 2 else ())

    def set_rows(self, rows):
        self.muted = True
        self.widget.delete(*self.widget.get_children())
        self.muted = False
        for row in rows:
            self.insert_row(row)

    def selected_index(self):
        sel = self.widget.selection()
        return self.widget.index(sel[0]) if sel else None

    def select(self, index):
        children = self.widget.get_children()
        if index is None:
            self.widget.selection_set(())
        else:
            self.widget.selection_set(children[index])
            self.widget.see(children[index])

    def set_enabled(self, flag):
        self.widget.state(["!disabled"] if flag else ["disabled"])


class _TkListBox(_TkHandle):
    """A ttk Treeview with one column, used as a list."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        self.widget = tk.Listbox(
            self.outer, height=spec.get("height", 5), font=fonts["normal"], activestyle="none",
            background=p["field"], foreground=p["fg"], selectbackground=p["accent"],
            selectforeground=p["accent_fg"], relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=p["border"], highlightcolor=p["accent"], exportselection=False,
        )
        bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.widget.yview, style=f"{P}.Vertical.TScrollbar")
        self.widget.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.widget.pack(side="left", fill="both", expand=True)
        self.set_items(spec.get("items", []))
        if spec.get("on_select"):
            select = spec["on_select"]
            self.widget.bind("<<ListboxSelect>>", lambda e: select() if self.widget.curselection() else None)
        if spec.get("on_open"):
            opener = spec["on_open"]
            self.widget.bind("<Double-Button-1>", lambda e: opener() if self.widget.curselection() else None)

    def set_items(self, items):
        self.widget.delete(0, "end")
        for item in items:
            self.widget.insert("end", item)

    def selected_index(self):
        sel = self.widget.curselection()
        return int(sel[0]) if sel else None

    def select(self, index):
        self.widget.selection_clear(0, "end")
        if index is not None:
            self.widget.selection_set(index)
            self.widget.see(index)


class _TkImage(_TkHandle):
    """A label showing a PNG or GIF picture."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.tk = tk
        self.widget = self.outer = ttk.Label(c.frame, style=f"{P}.TLabel")
        self.path = ""
        self.set(spec["path"])

    def get(self):
        return self.path

    def set(self, path):
        try:
            photo = self.tk.PhotoImage(master=self.widget, file=path)
        except self.tk.TclError:
            raise _Problem(f"I couldn't show the picture '{path}'. Is it really a PNG or GIF file?") from None
        self.photo = photo  # keep a reference, or Tk forgets the picture
        self.widget.configure(image=photo)
        self.path = path


class _TkProgress(_TkHandle):
    """A determinate ttk progress bar (0 to 100)."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.var = tk.DoubleVar(master=c.frame, value=spec.get("value", 0))
        self.widget = self.outer = ttk.Progressbar(c.frame, variable=self.var, maximum=100, mode="determinate",
                                                   style=f"{P}.Horizontal.TProgressbar")

    def get(self):
        return _tb.tidy_number(self.var.get())

    def set(self, value):
        self.var.set(value)


class _TkSeparator(_TkHandle):
    """A thin horizontal line."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        size = {"height": 1} if c.direction == "column" else {"width": 1}
        self.widget = self.outer = tk.Frame(c.frame, background=p["border"], **size)


class _TkCard(_TkHandle):
    """A bordered panel on the 'surface' colour, with an optional small title."""

    def __init__(self, c, spec):
        _, ttk = _tk()
        P, p = c.win.backend.styled(c.win.theme, c.win.accent, surface=not c.P.endswith("S"))
        self.outer = ttk.Frame(c.frame, style=f"{c.P}.Card.TFrame", padding=14)
        if spec.get("title"):
            ttk.Label(self.outer, text=spec["title"], style=f"{P}.Small.TLabel", anchor="w").pack(
                side="top", fill="x", pady=(0, 6))
        body = ttk.Frame(self.outer, style=f"{P}.TFrame")
        body.pack(side="top", fill="both", expand=True)
        self.inner = c.sub(body, spec.get("direction", "column"), P, p)
        self.inner.outer = self.outer
        self.widget = self.outer


class _TkScroll(_TkHandle):
    """A column that scrolls when its widgets don't fit."""

    def __init__(self, c, spec):
        tk, ttk = _tk()
        P, p = c.P, c.pal
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        self.canvas = tk.Canvas(self.outer, background=p["bg"], highlightthickness=0, borderwidth=0,
                                height=spec.get("height", 200))
        bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview, style=f"{P}.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.canvas, style=f"{P}.TFrame")
        self.item = self.canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.item, width=e.width))

        def wheel(event):
            if event.num == 4 or event.delta > 0:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0:
                self.canvas.yview_scroll(1, "units")

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, wheel)
            body.bind(seq, wheel)
        self.inner = c.sub(body, "column")
        self.inner.outer = self.outer
        self.widget = self.canvas


class _TkTabs:
    """A ttk Notebook; each page is a column container."""

    def __init__(self, c, spec):
        _, ttk = _tk()
        self.c = c
        self.outer = self.widget = ttk.Notebook(c.frame, style=f"{c.P}.TNotebook")
        self.pages = {}
        self.frames = {}
        for name in spec.get("names", []):
            self.add_page(name)
        if spec.get("on_change"):
            cb = spec["on_change"]
            self.widget.bind("<<NotebookTabChanged>>", lambda e: cb())

    def add_page(self, name):
        _, ttk = _tk()
        frame = ttk.Frame(self.widget, style=f"{self.c.P}.TFrame", padding=(0, 14, 0, 0))
        self.widget.add(frame, text=name)
        page = self.c.sub(frame, "column")
        self.pages[name] = page
        self.frames[name] = frame
        return page

    def page(self, name):
        return self.pages[name]

    def select(self, name):
        self.widget.select(self.frames[name])

    def selected(self):
        current = self.widget.select()
        return str(self.widget.tab(current, "text")) if current else None

    def set_enabled(self, flag):
        for name in self.frames:
            self.widget.tab(self.frames[name], state="normal" if flag else "disabled")

    def clear(self):
        for frame in self.frames.values():
            frame.destroy()
        self.pages, self.frames = {}, {}


class _TkSplitter:
    """A ttk PanedWindow; pane() adds a resizable column container."""

    def __init__(self, c, spec):
        _, ttk = _tk()
        self.c = c
        orient = "horizontal" if spec.get("direction", "horizontal") == "horizontal" else "vertical"
        self.outer = self.widget = ttk.PanedWindow(c.frame, orient=orient, style=f"{c.P}.TPanedwindow")
        self.panes = []

    def pane(self, weight=1):
        _, ttk = _tk()
        frame = ttk.Frame(self.widget, style=f"{self.c.P}.TFrame", padding=(0, 0, GAP, 0) if len(self.panes) == 0 else (GAP, 0, 0, 0))
        self.widget.add(frame, weight=weight)
        container = self.c.sub(frame, "column")
        self.panes.append(container)
        return container

    def set_enabled(self, flag):
        pass

    def clear(self):
        for pane in self.panes:
            pane.frame.destroy()
        self.panes = []


class _TkTree(_TkHandle):
    """A hierarchical Treeview: the first column is the tree itself."""

    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        columns = spec["columns"]
        ids = [f"c{i}" for i in range(1, len(columns))]
        self.widget = ttk.Treeview(self.outer, columns=ids, show="tree headings", style=f"{P}.Treeview",
                                   height=spec.get("height", 8), selectmode="browse")
        widths = spec.get("widths") or []
        self.widget.heading("#0", text=columns[0], anchor="w")
        self.widget.column("#0", width=widths[0] if widths else 220, minwidth=60, stretch=True)
        for i, cid in enumerate(ids, start=1):
            self.widget.heading(cid, text=columns[i], anchor="w")
            self.widget.column(cid, width=widths[i] if i < len(widths) else 120, minwidth=40, anchor="w", stretch=True)
        bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.widget.yview, style=f"{P}.Vertical.TScrollbar")
        self.widget.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.widget.pack(side="left", fill="both", expand=True)
        self.muted = False
        if spec.get("on_select"):
            select = spec["on_select"]
            self.widget.bind("<<TreeviewSelect>>", lambda e: None if self.muted else select())
        if spec.get("on_open"):
            opener = spec["on_open"]
            self.widget.bind("<Double-1>", lambda e: opener() if self.widget.identify_row(e.y) else None)
            self.widget.bind("<Return>", lambda e: opener() if self.widget.selection() else None)

    def add_node(self, node_id, parent_id, texts, open_):
        self.widget.insert(parent_id or "", "end", iid=node_id, text=texts[0], values=list(texts[1:]), open=open_)

    def set_node(self, node_id, texts):
        self.widget.item(node_id, text=texts[0], values=list(texts[1:]))

    def remove_node(self, node_id):
        self.widget.delete(node_id)

    def clear_nodes(self):
        self.muted = True
        self.widget.delete(*self.widget.get_children())
        self.muted = False

    def expand(self, node_id, flag):
        self.widget.item(node_id, open=bool(flag))
        if flag:
            self.widget.see(node_id)

    def is_expanded(self, node_id):
        return bool(self.widget.item(node_id, "open"))

    def selected_id(self):
        sel = self.widget.selection()
        return sel[0] if sel else None

    def select_id(self, node_id):
        if node_id is None:
            self.widget.selection_set(())
        else:
            self.widget.selection_set(node_id)
            self.widget.see(node_id)

    def set_enabled(self, flag):
        self.widget.state(["!disabled"] if flag else ["disabled"])


def nice_ticks(low, high, count=5, whole=False):
    """Round tick values covering low..high (like 0, 20, 40...). whole: the data
    are whole numbers (counts), so never step by less than 1."""
    import math
    if high <= low:
        high = low + 1
    span = high - low
    raw = span / max(count, 1)
    power = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * power
        if raw <= step:
            break
    if whole and step < 1:
        step = 1
    start = math.floor(low / step) * step
    ticks = []
    value = start
    while value <= high + step * 0.5:
        ticks.append(round(value, 10))
        value += step
    return ticks


def label_step(widths, slot, gap=10):
    """Show every n-th x label: the smallest n where neighbouring shown labels
    (each centred in its slot, n slots apart) don't touch."""
    n = 1
    while n < len(widths):
        shown = widths[::n]
        if all((a + b) / 2 + gap <= slot * n for a, b in zip(shown, shown[1:])):
            break
        n += 1
    return n


def pie_slices(labels, values, most):
    """At most `most` slices: the biggest ones, with the rest summed into "Other"."""
    pairs = [(label, v or 0) for label, v in zip(labels, values)]
    if len(pairs) <= most:
        return [p[0] for p in pairs], [p[1] for p in pairs]
    pairs.sort(key=lambda p: -p[1])
    keep, rest = pairs[:most - 1], pairs[most - 1:]
    keep.append(("Other", sum(v for _, v in rest)))
    return [p[0] for p in keep], [p[1] for p in keep]


def chart_colours(accent):
    """The accent colour first, then the series colours that don't look too much like it."""
    a = _hex_rgb(accent)
    rest = [c for c in SERIES_COLOURS if sum((x - y) ** 2 for x, y in zip(_hex_rgb(c), a)) ** 0.5 > 90]
    return [accent] + rest


def chart_series(data, labels):
    """Normalise chart data to (labels, [(name, values)...]). nil values stay None (a gap)."""
    def num(x):
        return None if x is None else float(x)
    if isinstance(data, dict):
        series = [(str(k), [num(x) for x in v]) for k, v in data.items()]
    else:
        series = [("", [num(x) for x in data])]
    n = max([len(v) for _, v in series] + [0])
    labels = [str(x) for x in (labels or [])][:n]
    labels += [str(i + 1) for i in range(len(labels), n)]
    return labels, series


class _TkChart(_TkHandle):
    """Bar, line and pie charts drawn on a Canvas, with a hover tooltip."""

    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.tk, self.p, self.fonts = tk, p, fonts
        self.spec = dict(spec)
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        self.widget = tk.Canvas(self.outer, background=p["field"], highlightthickness=1,
                                highlightbackground=p["border"], borderwidth=0,
                                width=spec.get("width", 420), height=spec.get("height", 240))
        self.widget.pack(fill="both", expand=True)
        self.regions = []
        self.tip = None
        self.widget.bind("<Configure>", lambda e: self.draw())
        self.widget.bind("<Motion>", self.hover)
        self.widget.bind("<Leave>", lambda e: self.hide_tip())

    def colours(self):
        return chart_colours(self.p["accent"])

    def update(self, spec):
        self.spec.update(spec)
        self.draw()

    def get(self):
        return self.spec

    def hide_tip(self):
        if self.tip:
            for item in self.tip:
                self.widget.delete(item)
            self.tip = None

    def hover(self, event):
        text = None
        for x0, y0, x1, y1, label in self.regions:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                text = label
                break
        self.hide_tip()
        if text is None:
            return
        cv, p = self.widget, self.p
        x = min(event.x + 12, max(cv.winfo_width() - 10, 20))
        y = max(event.y - 12, 10)
        t = cv.create_text(x, y, text=text, anchor="nw" if x == event.x + 12 else "ne", fill=p["fg"],
                           font=self.fonts["small"])
        bx0, by0, bx1, by1 = cv.bbox(t)
        r = cv.create_rectangle(bx0 - 6, by0 - 4, bx1 + 6, by1 + 4, fill=p["surface"], outline=p["border"])
        cv.tag_raise(t, r)
        self.tip = [r, t]

    def draw(self):
        cv, p = self.widget, self.p
        cv.delete("all")
        self.regions = []
        self.tip = None
        W, H = cv.winfo_width(), cv.winfo_height()
        if W <= 1 or H <= 1:
            W, H = int(cv.cget("width")), int(cv.cget("height"))
        kind = self.spec.get("type", "bar")
        labels, series = chart_series(self.spec.get("data", []), self.spec.get("labels"))
        title = self.spec.get("title")
        top = 14
        if title:
            cv.create_text(W // 2, 12, text=title, fill=p["fg"], font=self.fonts["bold"], anchor="n")
            top = 36
        if not series or not any(x is not None for _, v in series for x in v):
            cv.create_text(W // 2, H // 2, text=self.spec.get("empty", "No data yet"), fill=p["muted"],
                           font=self.fonts["normal"])
            return
        colours = self.colours()
        if kind == "pie":
            self.draw_pie(labels, series[0][1], colours, W, H, top)
            return
        named = [s for s in series if s[0]]
        legend_h = 22 if named else 0
        left, right, bottom = 52, 16, H - 34
        top += legend_h
        values = [v for _, vs in series for v in vs if v is not None]
        low, high = min(values + [0]), max(values + [0])
        if low == high:
            high = low + 1
        plot_h = max(bottom - top, 10)
        ticks = nice_ticks(low, high, max(1, min(5, int(plot_h // 28))), all(float(v).is_integer() for v in values))
        low, high = min(ticks + [low]), max(ticks + [high])

        def ypos(v):
            return bottom - (v - low) / (high - low) * plot_h

        for t in ticks:
            y = ypos(t)
            cv.create_line(left, y, W - right, y, fill=p["border"])
            cv.create_text(left - 8, y, text=_tb.tidy_number(t), anchor="e", fill=p["muted"], font=self.fonts["small"])
        cv.create_line(left, top, left, bottom, fill=p["muted"])
        cv.create_line(left, bottom, W - right, bottom, fill=p["muted"])
        n = len(labels)
        slot = (W - left - right) / max(n, 1)
        every = label_step([self.fonts["small"].measure(label) for label in labels], slot)
        for i, label in enumerate(labels):
            x = left + slot * (i + 0.5)
            shown = label if i % every == 0 else ""
            cv.create_text(x, bottom + 8, text=shown, anchor="n", fill=p["muted"], font=self.fonts["small"])
        if kind == "line":
            for s, (name, vs) in enumerate(series):
                colour = colours[s % len(colours)]
                points = [None if v is None else (left + slot * (i + 0.5), ypos(v)) for i, v in enumerate(vs)]
                run = []
                for pt in points + [None]:  # nil values break the line into pieces
                    if pt is not None:
                        run.append(pt)
                        continue
                    if len(run) > 1:
                        cv.create_line(*[c for q in run for c in q], fill=colour, width=2, smooth=False)
                    run = []
                for i, pt in enumerate(points):
                    if pt is None:
                        continue
                    x, y = pt
                    cv.create_oval(x - 3, y - 3, x + 3, y + 3, fill=colour, outline=p["field"])
                    who = f"{name}: " if name else ""
                    self.regions.append((x - slot / 2, min(y - 8, top), x + slot / 2, bottom,
                                         f"{labels[i]}\n{who}{_tb.tidy_number(vs[i])}"))
        else:
            group = slot * 0.7
            bar_w = group / max(len(series), 1)
            zero = ypos(max(low, min(0, high)))
            for i in range(n):
                for s, (name, vs) in enumerate(series):
                    if i >= len(vs) or vs[i] is None:
                        continue
                    x0 = left + slot * i + (slot - group) / 2 + bar_w * s
                    y = ypos(vs[i])
                    cv.create_rectangle(x0, min(y, zero), x0 + bar_w - 2, max(y, zero), fill=colours[s % len(colours)],
                                        outline="")
                    who = f"{name}: " if name else ""
                    self.regions.append((x0, min(y, zero) - 4, x0 + bar_w, bottom, f"{labels[i]}\n{who}{_tb.tidy_number(vs[i])}"))
        if named:
            x = left
            for s, (name, _) in enumerate(named):
                cv.create_rectangle(x, top - legend_h + 2, x + 12, top - legend_h + 14, fill=colours[s % len(colours)], outline="")
                t = cv.create_text(x + 18, top - legend_h + 8, text=name, anchor="w", fill=p["fg"], font=self.fonts["small"])
                x = cv.bbox(t)[2] + 18

    def draw_pie(self, labels, values, colours, W, H, top):
        import math
        cv, p = self.widget, self.p
        merged = len(values) > len(colours)
        labels, values = pie_slices(labels, values, len(colours))
        colours = [p["muted"] if merged and i == len(values) - 1 else colours[i % len(colours)] for i in range(len(values))]
        total = sum(v for v in values if v > 0)
        if total <= 0:
            cv.create_text(W // 2, H // 2, text="No data yet", fill=p["muted"], font=self.fonts["normal"])
            return
        small = self.fonts["small"]
        rows = min(len(values), max((H - top - 12) // 20, 1))
        shown_labels = [label if len(label) <= 32 else label[:31] + "…" for label in labels]
        legend_w = min(max([small.measure(f"{l}  {_tb.tidy_number(v)}") for l, v in zip(shown_labels, values)] + [60]) + 44,
                       int(W * 0.55))
        size = max(min(W - legend_w - 40, H - top - 20), 40)
        x0, y0 = 20, top + (H - top - size) / 2
        start = 90.0
        for i, v in enumerate(values):
            if v <= 0:
                continue
            extent = -360.0 * v / total
            colour = colours[i]
            cv.create_arc(x0, y0, x0 + size, y0 + size, start=start, extent=extent, fill=colour, outline=p["field"], width=2)
            mid = math.radians(start + extent / 2)
            cx, cy = x0 + size / 2 + math.cos(mid) * size * 0.3, y0 + size / 2 - math.sin(mid) * size * 0.3
            pct = f"{v / total * 100:.0f}%"
            self.regions.append((cx - 18, cy - 12, cx + 18, cy + 12, f"{labels[i]}: {_tb.tidy_number(v)} ({pct})"))
            if abs(extent) > 25:
                cv.create_text(cx, cy, text=pct, fill=p["accent_fg"] if colour == p["accent"] else "#ffffff",
                               font=self.fonts["small"])
            start += extent
        lx, ly = x0 + size + 24, y0 + 4
        for i, v in enumerate(values[:rows]):
            if i == rows - 1 and rows < len(values):
                cv.create_text(lx + 18, ly + 6, text=f"+{len(values) - rows + 1} more", anchor="w", fill=p["muted"], font=small)
                break
            cv.create_rectangle(lx, ly, lx + 12, ly + 12, fill=colours[i], outline="")
            cv.create_text(lx + 18, ly + 6, text=f"{shown_labels[i]}  {_tb.tidy_number(v)}", anchor="w", fill=p["fg"], font=small)
            ly += 20


class _TkCanvas(_TkHandle):
    """A drawing area with line/rect/oval/text/image and mouse events."""

    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.tk, self.p, self.fonts = tk, p, fonts
        self.widget = self.outer = tk.Canvas(c.frame, width=spec.get("width", 400), height=spec.get("height", 300),
                                             background=spec.get("background") or p["field"], highlightthickness=1,
                                             highlightbackground=p["border"], borderwidth=0)
        self.photos = []
        for name, seq in (("on_click", "<Button-1>"), ("on_drag", "<B1-Motion>"), ("on_release", "<ButtonRelease-1>"),
                          ("on_move", "<Motion>")):
            if spec.get(name):
                cb = spec[name]
                self.widget.bind(seq, lambda e, cb=cb: cb(e.x, e.y))

    def line(self, x1, y1, x2, y2, o):
        return self.widget.create_line(x1, y1, x2, y2, fill=o.get("color", self.p["fg"]), width=o.get("width", 1))

    def rect(self, x, y, w, h, o):
        return self.widget.create_rectangle(x, y, x + w, y + h, fill=o.get("fill", ""), outline=o.get("color", self.p["fg"]),
                                            width=o.get("width", 1))

    def oval(self, x, y, w, h, o):
        return self.widget.create_oval(x, y, x + w, y + h, fill=o.get("fill", ""), outline=o.get("color", self.p["fg"]),
                                       width=o.get("width", 1))

    def draw_text(self, x, y, text, o):
        font = self.fonts["bold" if o.get("bold") else "normal"]
        if o.get("size"):
            from tkinter import font as tkfont
            font = tkfont.Font(root=self.widget, family=font.actual("family"), size=int(o["size"]),
                               weight="bold" if o.get("bold") else "normal")
        return self.widget.create_text(x, y, text=text, fill=o.get("color", self.p["fg"]), anchor=o.get("anchor", "nw"), font=font)

    def image(self, x, y, path):
        try:
            photo = self.tk.PhotoImage(master=self.widget, file=path)
        except self.tk.TclError:
            raise _Problem(f"I couldn't show the picture '{path}'. Is it really a PNG or GIF file?") from None
        self.photos.append(photo)
        return self.widget.create_image(x, y, image=photo, anchor="nw")

    def clear(self):
        self.widget.delete("all")
        self.photos = []

    def remove(self, item):
        self.widget.delete(item)

    def move(self, item, dx, dy):
        self.widget.move(item, dx, dy)

    def size(self):
        self.widget.update_idletasks()
        w, h = self.widget.winfo_width(), self.widget.winfo_height()
        return [int(self.widget.cget("width")), int(self.widget.cget("height"))] if w <= 1 else [w, h]

    def set_enabled(self, flag):
        pass


class _TkSlider(_TkHandle):
    """A ttk Scale snapped to a step, with an optional value label."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.var = tk.DoubleVar(master=c.frame, value=spec.get("value", spec["min"]))
        self.step = spec.get("step") or 0
        self.lo, self.hi = spec["min"], spec["max"]
        self.muted = False
        cb = spec.get("on_change")

        def changed(_value):
            if self.muted:
                return
            snapped = self.snap(self.var.get())
            if snapped != self.var.get():
                self.muted = True
                self.var.set(snapped)
                self.muted = False
            if self.label is not None:
                self.label.configure(text=_tb.tidy_number(snapped))
            if cb:
                cb()

        self.label = None
        if spec.get("show_value"):
            self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
            self.widget = ttk.Scale(self.outer, from_=self.lo, to=self.hi, variable=self.var, orient="horizontal",
                                    style=f"{P}.Horizontal.TScale", command=changed)
            self.widget.pack(side="left", fill="x", expand=True)
            self.label = ttk.Label(self.outer, style=f"{P}.Mono.TLabel", width=6, anchor="e",
                                   text=_tb.tidy_number(self.var.get()))
            self.label.pack(side="left", padx=(8, 0))
        else:
            self.widget = self.outer = ttk.Scale(c.frame, from_=self.lo, to=self.hi, variable=self.var,
                                                 orient="horizontal", style=f"{P}.Horizontal.TScale", command=changed)
        if spec.get("width"):
            self.widget.configure(length=spec["width"])

    def snap(self, value):
        if self.step:
            value = self.lo + round((value - self.lo) / self.step) * self.step
        return _tb.tidy_number(min(max(value, self.lo), self.hi))

    def get(self):
        return self.snap(self.var.get())

    def set(self, value):
        self.muted = True
        self.var.set(value)
        self.muted = False
        if self.label is not None:
            self.label.configure(text=_tb.tidy_number(value))


class _TkSpinner(_TkHandle):
    """A ttk Spinbox for numbers between min and max."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.var = tk.StringVar(master=c.frame, value=str(_tb.tidy_number(spec.get("value", spec["min"]))))
        self.lo, self.hi, self.step = spec["min"], spec["max"], spec.get("step") or 1
        cb = spec.get("on_change")
        self.widget = self.outer = ttk.Spinbox(c.frame, from_=self.lo, to=self.hi, increment=self.step,
                                               textvariable=self.var, style=f"{P}.TSpinbox", font=fonts["normal"],
                                               width=spec.get("width", 8), wrap=False,
                                               command=(lambda: cb()) if cb else None)
        if cb:
            self.widget.bind("<Return>", lambda e: cb())
            self.widget.bind("<FocusOut>", lambda e: cb())

    def get(self):
        try:
            value = float(self.var.get())
        except ValueError:
            return _tb.tidy_number(self.lo)
        return _tb.tidy_number(min(max(value, self.lo), self.hi))

    def set(self, value):
        self.var.set(str(_tb.tidy_number(value)))


class _TkDatePicker(_TkHandle):
    """An entry showing YYYY-MM-DD with a small pop-up calendar."""

    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.tk, self.ttk, self.P, self.p, self.fonts = tk, ttk, P, p, fonts
        self.var = tk.StringVar(master=c.frame, value=spec.get("value", ""))
        self.cb = spec.get("on_change")
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        self.widget = ttk.Entry(self.outer, textvariable=self.var, style=f"{P}.TEntry", font=fonts["normal"], width=12)
        self.widget.pack(side="left")
        self.widget.bind("<Return>", lambda e: self.typed())
        self.widget.bind("<FocusOut>", lambda e: self.typed())
        ttk.Button(self.outer, text="▾", width=2, style=f"{P}.TButton", command=self.popup).pack(side="left", padx=(4, 0))
        self.popup_win = None

    def typed(self):
        import datetime
        try:
            datetime.date.fromisoformat(self.var.get().strip())
        except ValueError:
            return
        self.var.set(self.var.get().strip())
        if self.cb:
            self.cb()

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)

    def popup(self):
        import calendar
        import datetime
        tk, ttk, p = self.tk, self.ttk, self.p
        if self.popup_win is not None:
            self.popup_win.destroy()
        try:
            current = datetime.date.fromisoformat(self.var.get())
        except ValueError:
            current = datetime.date.today()
        state = {"year": current.year, "month": current.month}
        win = self.popup_win = tk.Toplevel(self.outer)
        win.overrideredirect(True)
        win.configure(background=p["border"])
        x, y = self.widget.winfo_rootx(), self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        win.geometry(f"+{x}+{y}")
        body = tk.Frame(win, background=p["surface"], padx=8, pady=8)
        body.pack(padx=1, pady=1)

        def close(*_):
            if self.popup_win is not None:
                self.popup_win.destroy()
                self.popup_win = None

        def pick(day):
            self.var.set(f"{state['year']:04d}-{state['month']:02d}-{day:02d}")
            close()
            if self.cb:
                self.cb()

        def shift(delta):
            month = state["month"] + delta
            state["year"] += (month - 1) // 12
            state["month"] = (month - 1) % 12 + 1
            render()

        def render():
            for child in body.winfo_children():
                child.destroy()
            head = tk.Frame(body, background=p["surface"])
            head.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 6))
            tk.Button(head, text="‹", command=lambda: shift(-1), relief="flat", background=p["surface"],
                      foreground=p["fg"], font=self.fonts["bold"], borderwidth=0).pack(side="left")
            tk.Label(head, text=f"{calendar.month_name[state['month']]} {state['year']}", background=p["surface"],
                     foreground=p["fg"], font=self.fonts["bold"]).pack(side="left", expand=True)
            tk.Button(head, text="›", command=lambda: shift(1), relief="flat", background=p["surface"],
                      foreground=p["fg"], font=self.fonts["bold"], borderwidth=0).pack(side="right")
            for col, name in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
                tk.Label(body, text=name, background=p["surface"], foreground=p["muted"], font=self.fonts["small"],
                         width=3).grid(row=1, column=col)
            for r, week in enumerate(calendar.monthcalendar(state["year"], state["month"]), start=2):
                for col, day in enumerate(week):
                    if day == 0:
                        continue
                    chosen = (state["year"], state["month"], day) == (current.year, current.month, current.day)
                    tk.Button(body, text=str(day), width=3, relief="flat", borderwidth=0,
                              background=p["accent"] if chosen else p["surface"],
                              foreground=p["accent_fg"] if chosen else p["fg"], font=self.fonts["small"],
                              activebackground=p["button_hover"], command=lambda d=day: pick(d)).grid(row=r, column=col)

        render()
        win.bind("<Escape>", close)
        win.bind("<FocusOut>", lambda e: win.after(150, lambda: close() if self.popup_win is win and
                                                      win.focus_get() is None else None))
        win.focus_set()


class _TkColor(_TkHandle):
    """A colour swatch; clicking it opens the colour chooser."""

    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.backend, self.p = c.win.backend, p
        self.value = spec.get("value", p["accent"])
        self.cb = spec.get("on_change")
        self.outer = ttk.Frame(c.frame, style=f"{P}.TFrame")
        self.swatch = tk.Label(self.outer, background=self.value, width=3, relief="flat", borderwidth=0,
                               highlightthickness=1, highlightbackground=p["border"], cursor="hand2")
        self.swatch.pack(side="left", fill="y")
        self.swatch.bind("<Button-1>", lambda e: self.pick())
        self.widget = ttk.Button(self.outer, text=spec.get("text", "Choose colour…"), style=f"{P}.TButton", command=self.pick)
        self.widget.pack(side="left", padx=(6, 0))

    def pick(self):
        got = self.backend.pick_colour(self.value, "Choose a colour")
        if got:
            self.set(got)
            if self.cb:
                self.cb()

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.swatch.configure(background=value)


class _TkLink(_TkHandle):
    """Underlined accent-coloured text that opens a URL when clicked."""
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.backend = c.win.backend
        self.url = spec.get("url", "")
        self.widget = self.outer = ttk.Label(c.frame, text=spec.get("text", ""), style=f"{P}.Link.TLabel", cursor="hand2",
                                             anchor="w", takefocus=True)
        for seq in ("<Button-1>", "<Return>", "<space>"):   # Tab reaches it; Enter or Space opens it
            self.widget.bind(seq, lambda e: self.click())
        self.cb = spec.get("on_click")

    def click(self):
        if self.cb:
            self.cb()
        if self.url:
            self.backend.open_url(self.url)

    def get(self):
        return str(self.widget.cget("text"))

    def set(self, value):
        self.widget.configure(text=value)

    def set_url(self, url):
        self.url = url


def badge_colours(p, style):
    """(background, foreground) of a badge style in palette p."""
    return {
        "accent": (p["accent"], p["accent_fg"]), "success": (p["success"], "#ffffff"),
        "danger": (p["danger"], "#ffffff"), "warning": ("#e0862a", "#ffffff"),
        "muted": (p["button"], p["muted"]), "info": ("#2a7de1", "#ffffff"),
    }[style]


class _TkBadge(_TkHandle):
    """A small coloured pill of text (counts, levels, states)."""

    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.p = p
        bg, fg = badge_colours(p, spec.get("style", "accent"))
        self.widget = self.outer = tk.Label(c.frame, text=spec.get("text", ""), background=bg, foreground=fg,
                                            font=fonts["small"], padx=9, pady=2, borderwidth=0)

    def get(self):
        return str(self.widget.cget("text"))

    def set(self, value):
        self.widget.configure(text=value)

    def set_style(self, style):
        bg, fg = badge_colours(self.p, style)
        self.widget.configure(background=bg, foreground=fg)

    def set_enabled(self, flag):
        pass


_TK_WIDGETS = {
    "label": _TkLabel, "button": _TkButton, "input": _TkInput, "textarea": _TkTextArea,
    "checkbox": _TkCheckbox, "select": _TkSelect, "table": _TkTable, "list_box": _TkListBox,
    "image": _TkImage, "progress": _TkProgress, "separator": _TkSeparator,
    "tree": _TkTree, "chart": _TkChart, "canvas": _TkCanvas, "slider": _TkSlider, "spinner": _TkSpinner,
    "date_picker": _TkDatePicker, "color": _TkColor, "link": _TkLink, "badge": _TkBadge,
}


# ---------------------------------------------------------------------------
# The TB library
# ---------------------------------------------------------------------------

DOC = (
    "Desktop windows: buttons, text boxes, tables, menus and dialogs. "
    "Make a window with gui.window(title), add widgets to it top to bottom "
    "(win.label(\"Hi\"), win.button(\"Go\", fn() { ... })), put widgets side by side "
    "in win.row(), then call win.run() to show it. Light and dark themes in TB's colours. "
    "Needs the TB app on a computer (not the browser playground)."
)

WIDGET_DOC = """
Widgets:
Add widgets to a window, a row() or a column(). They stack top to bottom; a row puts them side by side.
- label(text, options?): a line of text.
  - style: "normal", "title", "heading", "bold", "muted", "accent", "small", "display", "success", "danger" or "mono".
  - wrap: wrap the text at this many pixels.
  - align: "left", "center" or "right".
  - width: the width in characters.
- button(text, on_click?, options?): a button.
  - primary: true for the main button.
  - width, expand
- input(options?): a one-line text box.
  - placeholder, value
  - on_enter(text), on_change(text)
  - secret: hide what is typed.
  - width, expand
- textarea(options?): a text box with many lines.
  - value, on_change(text)
  - height: in lines.
  - mono, wrap, readonly, expand
- checkbox(text, options?): a tick box.
  - checked, on_change(checked)
- select(choices, options?): a drop-down list.
  - value, on_change(choice), width, expand
- table(columns, options?): rows and columns.
  - height: in rows.
  - widths, on_select(row, index), on_open(row, index), expand
- list_box(items?, options?): a list to pick from.
  - height, on_select(item, index), on_open(item, index), expand
- image(path): a PNG or GIF picture.
- progress(options?): a bar from 0 to 100.
  - value
- separator(): a thin line.
- row(options?) / column(options?): containers that take all of these widgets too.
  - expand
- card(options?): a bordered panel.
  - title, expand
  - direction: "column" or "row".
- scroll(options?): a column that scrolls when it is too tall.
  - height, expand
- tabs(names, options?): pages with tabs. .page(name) is a container.
  - on_change(name), expand
- splitter(options?): resizable panes. .pane(options?) adds a container.
  - direction: "horizontal" or "vertical".
- tree(columns, options?): rows inside rows.
  - height, widths, expand
  - on_select(values, id), on_open(values, id)
- chart(options?): a bar, line or pie chart.
  - type, labels, title, width, height, empty
  - data: a list, or {"series": list}. nil leaves a gap.
- canvas(width?, height?, options?): a drawing area.
  - background
  - on_click(x, y), on_drag(x, y), on_release(x, y), on_move(x, y)
- slider(min, max, options?): drag to pick a number.
  - value, step, on_change(number), show_value, width, expand
- spinner(min, max, options?): a number box with up/down arrows.
  - value, step, on_change(number), width
- date_picker(options?): a date with a pop-up calendar.
  - value: like "2026-03-14", or "today".
  - on_change(text)
- color(options?): a colour swatch that opens the colour chooser.
  - value: like "#rrggbb".
  - text, on_change(colour)
- link(text, url?, options?): underlined text that opens a web page.
  - on_click
- badge(text, options?): a small coloured pill.
  - style: "accent", "success", "danger", "warning", "muted" or "info".
- clear(): remove everything inside.

Widget methods:
Every widget gives back a map you can use later.
- label, button: .set(text) .value()
- label: .style(name)
- button: .click() .enable() .disable()
- input, textarea: .value() .set(text) .clear() .focus()
- textarea: .append(text)
- checkbox: .value() .set(bool)
- select: .value() .set(choice) .options() .set_options(list)
- table: .add_row(list) .set_rows(rows) .rows() .selected() .selected_index() .select(i) .remove(i) .clear() .count()
- list_box: .add(item) .set_items(list) .items() .selected() .selected_index() .select(i) .remove(i) .clear() .count()
- image: .set(path)
- progress: .set(0..100) .value()
- tabs: .page(name) .pages() .add(name) .select(name) .selected()
- tree: .add(parent_id or nil, values, {"open": bool}?) gives back an id.
  - .set(id, values) .values(id) .parent(id) .children(id?) .remove(id) .clear() .count() .ids()
  - .selected() .selected_id() .select(id) .expand(id) .collapse(id) .expand_all() .is_expanded(id)
- chart: .update({"data": ..., "labels": ...}) .data() .labels() .type()
- canvas: drawing calls give back an item number.
  - .line(x1, y1, x2, y2, {color, width}?)
  - .rect(x, y, w, h, {color, fill, width}?) .oval(...)
  - .text(x, y, text, {color, size, bold, anchor}?)
  - .image(x, y, path) .clear() .remove(item) .move(item, dx, dy) .size()
- slider, spinner: .value() .set(n)
- date_picker: .value() .set("2026-03-14")
- color: .value() .set("#rrggbb")
- link: .set(text) .set_url(url) .open()
- badge: .set(text) .style(name)

Window methods:
- title(text?): set the title. With no input, give it back.
- menu(map): a menu bar, like {"File": {"Open": fn, "-": nil, "Quit": fn}}.
- status(text): text in the status bar.
- every(ms, fn), after(ms, fn): run fn again and again, or once. Both give a timer with .stop().
- key(keys, fn): a keyboard shortcut like "Ctrl+S". "Mod" is Cmd on a Mac and Ctrl elsewhere.
- bind_shortcut(keys, fn): the same as key.
- run(): show the window and wait until it closes.
- close(), is_open()
- icon(path), always_on_top(bool?), fullscreen(bool?), is_fullscreen(), center()
- resize(w, h): the new size in pixels. size() gives it back.
- theme(): the window's theme.
- on_close(fn): fn can give back false to keep the window open.
- on_key(fn): fn(key) gets every key, as text like "a" or "Ctrl+S".
- on_drop(fn): fn(paths) gets files dropped on the window. Also called on_paste_paths.
  - Windows and macOS: drops work out of the box.
  - Linux: drops work when the tkdnd Tk extension is installed.
  - Everywhere: file paths pasted with Ctrl/Cmd+V, and (Mac) files dropped on the Dock icon.
  - Without tkdnd, give people an "Open…" button with gui.open_file() too.
- tray(menu, options?): an icon with a menu. menu is like {"Show": fn, "Quit": fn}. options can have "title".
  - Windows: a notification-area icon. Click for the menu; double-click to restore.
  - macOS: the title in the menu bar, with the menu.
  - Linux: a small always-on-top chip in the screen corner instead.
  - .minimize() hides the window and shows the icon.
  - .restore() brings it back.
  - .show() .hide() .visible()
- hide(), show()
- run_in_background(fn, on_done?, on_error?): run fn on another thread, then on_done(result) in the window. on_error(message) gets any error.

Callbacks:
- Your functions can take fewer inputs than offered: fn() { } is fine for on_change too.
- If a function you gave the window hits an error, the window shows it in a friendly message and keeps going.

Keyboard and screen readers:
- Tab and Shift+Tab move between widgets in the order you added them.
- Buttons, inputs, lists, tables, trees, sliders, pickers and links take the keyboard. Enter or Space presses.
- In a textarea, Tab types a tab; Ctrl+Tab moves on.
- Labels, badges, progress bars, charts and canvases are skipped. Use key() for canvas apps.
- Screen readers read window titles, menus and dialogs. Tk does not expose the widgets inside a window to them.
- So put key facts in the title, status() or an alert() too."""

WINDOW_OPTIONS = {"width": "whole", "height": "whole", "theme": "text", "resizable": "bool", "accent": "text"}


def _copy_data(value):
    """A copy of chart data (a list of numbers or a map of lists) safe to hand out."""
    if isinstance(value, dict):
        return {k: list(v) for k, v in value.items()}
    if isinstance(value, list):
        return list(value)
    return value


def _arity(fn):
    """How many inputs a TB function takes at most (None if unknown)."""
    if isinstance(fn, _tb.Function):
        return len(fn.params)
    if isinstance(fn, _tb.Builtin):
        return fn.arity[1]
    return None


def _fit_args(fn, args):
    """Give a callback only as many inputs as it takes, so on_change can be
    fn() { } or fn(text) { }."""
    most = _arity(fn)
    return list(args) if most is None else list(args)[:most]


def _filters(line, value, fn_name):
    """[".txt", ".md"] or {"Text": "*.txt *.md"} or ".txt" -> Tk filetypes."""
    if value is None:
        return []

    def pattern(text):
        parts = []
        for piece in text.replace(",", " ").split():
            if piece.startswith("."):
                piece = "*" + piece
            elif "*" not in piece:
                piece = "*." + piece
            parts.append(piece)
        return " ".join(parts)

    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        pats = []
        for item in value:
            check_type(line, item, "text", fn_name, "each filter")
            pats.append(pattern(item))
        return [("Files", " ".join(pats)), ("All files", "*")] if pats else []
    if isinstance(value, dict):
        out = []
        for label, pats in value.items():
            check_type(line, pats, "text", fn_name, f'the "{to_text(label)}" filter')
            out.append((to_text(label), pattern(pats)))
        return out + [("All files", "*")]
    fail(line, f"{fn_name}() needs filters to be a list like [\".txt\", \".md\"] or a map like "
               f"{{\"Text files\": \"*.txt\"}}, not {_tb.type_name(value)}.")


class _Gui:
    """One per running program: its windows and callback plumbing."""

    def __init__(self, api):
        self.api = api
        self.windows = []
        self.loop_depth = 0
        self.pending_exit = None
        self.default_theme = "light"   # gui.theme(): "light", "dark" or "system"
        self.default_accent = None     # gui.accent(): "#rrggbb" or None for TB's own

    # -- plumbing ---------------------------------------------------------------

    def backend(self, line):
        self.api.need_desktop(line, "The gui library")
        return _get_backend()

    def guard(self, line, work, handle=None):
        try:
            return work()
        except _Problem as problem:
            fail(line, str(problem))
        except (TBError, _tb.TBExit):
            raise
        except Exception as err:
            if handle is not None and not handle.is_open():
                fail(line, "That window is closed, so I can't change it any more.")
            fail(line, f"The window couldn't do that ({err}).")

    def report(self, err):
        print(str(err), file=sys.stderr)
        body = str(err)
        if body.startswith("Oops! "):
            body = body[len("Oops! "):]
        try:
            _get_backend().alert(body, "Oops!", error=True)
        except Exception:
            pass

    def fire(self, fn, line, args=(), on_error=None):
        """Run a TB callback from a window event and give back its result.
        Errors become a friendly message (printed and shown in a dialog) and
        give None; exit() closes the windows."""
        try:
            return _tb._guarded(lambda: self.api.call(fn, _fit_args(fn, args), line))
        except _tb.TBExit as stop:
            self.pending_exit = stop
            self.close_all()
            if self.loop_depth == 0:
                self.pending_exit = None
                raise
        except TBError as err:
            if on_error:
                on_error()
            self.report(err)
        except Exception as err:
            if on_error:
                on_error()
            self.report(TBError(f"Something went wrong in a window event: {err}", line))
        return None

    def resolve_theme(self, line, theme, fn_name):
        if theme not in THEMES and theme != "system":
            fail(line, f'{fn_name}() knows the themes "light", "dark" and "system", not "{theme}".')
        return system_theme() if theme == "system" else theme

    def check_colour(self, line, value, fn_name, what="colour"):
        if not is_colour(value):
            fail(line, f'{fn_name}() needs {what} like "#b0123f" (a # and six hex digits), not {to_text(value, nested=True)}.')
        return _rgb_hex(_hex_rgb(value))

    def callback(self, fn, line, args_fn=lambda: [], on_error=None):
        if fn is None:
            return None
        return lambda *_: self.fire(fn, line, args_fn(), on_error)

    def close_all(self):
        for handle in self.windows:
            try:
                handle.close()
            except Exception:
                pass

    @staticmethod
    def options(line, value, allowed, fn_name):
        if value is None:
            return {}
        check_type(line, value, "map", fn_name, "options")
        out = {}
        for key, v in value.items():
            if key not in allowed:
                fail(line, f'{fn_name}() doesn\'t know the option {to_text(key, nested=True)}. '
                           f'Options: {", ".join(allowed)}.')
            if v is None:
                continue
            out[key] = check_type(line, v, allowed[key], fn_name, f'the "{key}" option')
        return out

    @staticmethod
    def method(obj, owner, name, lo, hi, func, doc=""):
        obj[name] = _tb.Builtin(f"{owner}.{name}", func, (lo, hi), doc)

    # -- windows ----------------------------------------------------------------

    def window(self, line, title, options=None):
        title = to_text(check_type(line, title, "text|number", "gui.window", "title"))
        o = self.options(line, options, WINDOW_OPTIONS, "gui.window")
        theme = self.resolve_theme(line, o.get("theme", self.default_theme), "gui.window")
        accent = self.default_accent
        if "accent" in o:
            accent = self.check_colour(line, o["accent"], "gui.window", 'the "accent" option to be a colour')
        width, height = o.get("width", 520), o.get("height", 400)
        for name, size in (("width", width), ("height", height)):
            if not 100 <= size <= 5000:
                fail(line, f"gui.window() needs a {name} between 100 and 5000 pixels, not {size}.")
        backend = self.backend(line)
        handle = self.guard(line, lambda: backend.window(title, width, height, theme, o.get("resizable", True), accent))
        self.windows.append(handle)
        win = {"kind": "window"}
        self.container_fns(win, handle.body, handle, "window")
        self.window_fns(win, handle)
        return win

    def window_fns(self, win, h):
        M = lambda name, lo, hi, func: self.method(win, "window", name, lo, hi, func)  # noqa: E731
        g = lambda line, work: self.guard(line, work, h)  # noqa: E731

        def title(line, text=None):
            if text is None:
                return g(line, h.get_title)
            g(line, lambda: h.set_title(to_text(text)))
            return None

        def menu(line, spec):
            check_type(line, spec, "map", "window.menu", "menus")
            bar = []
            for name, items in spec.items():
                check_type(line, items, "map", "window.menu", f'the "{to_text(name)}" menu')
                bar.append((to_text(name), self.menu_items(line, items)))
            g(line, lambda: h.set_menu(bar))
            return None

        status_box = {}

        def status(line, text=None):
            if text is not None:
                g(line, lambda: h.status(to_text(text)))
            if not status_box:
                status_box["kind"] = "status"
                self.method(status_box, "status", "set", 1, 1,
                            lambda ln, t: g(ln, lambda: h.status(to_text(t))))
                self.method(status_box, "status", "value", 0, 0, lambda ln: g(ln, h.get_status) or "")
                if text is None:
                    g(line, lambda: h.status(""))
            return status_box

        def timer(line, ms, fn, repeat):
            name = "window.every" if repeat else "window.after"
            ms = check_type(line, ms, "whole", name, "ms")
            check_type(line, fn, "function", name, "fn")
            if ms < (10 if repeat else 0):
                fail(line, f"{name}() needs at least {10 if repeat else 0} milliseconds, not {ms}.")
            state = {"token": None, "active": True}

            def stop(*_):
                state["active"] = False
                if state["token"] is not None:
                    h.cancel(state["token"])
                    state["token"] = None

            def tick():
                state["token"] = None
                if not state["active"] or not h.is_open():
                    return
                if not repeat:
                    state["active"] = False
                self.fire(fn, line, [], on_error=stop if repeat else None)
                if repeat and state["active"] and h.is_open():
                    state["token"] = h.after(ms, tick)

            state["token"] = g(line, lambda: h.after(ms, tick))
            t = {"kind": "timer"}
            self.method(t, "timer", "stop", 0, 0, lambda ln: stop())
            self.method(t, "timer", "active", 0, 0, lambda ln: state["active"])
            return t

        def key(line, combo, fn):
            check_type(line, combo, "text", "window.key", "keys")
            check_type(line, fn, "function", "window.key", "fn")
            seq = key_sequence(combo)
            if seq is None:
                fail(line, f'window.key() doesn\'t understand "{combo}". Try forms like "Ctrl+S", "Mod+O", '
                           '"Enter", "Escape", "F5", "Up" or a single key like "a" or "+".')
            g(line, lambda: h.bind_key(seq, self.callback(fn, line)))
            return None

        def run(line):
            ms = os.environ.get("TB_GUI_AUTOCLOSE_MS", "").strip()
            if ms.isdigit() and h.is_open():
                g(line, lambda: h.after(int(ms), h.close))
            self.loop_depth += 1
            try:
                g(line, h.run)
            finally:
                self.loop_depth -= 1
            if self.pending_exit is not None and self.loop_depth == 0:
                stop, self.pending_exit = self.pending_exit, None
                raise stop
            return None

        # -- 3.0 extras --

        def icon(line, path):
            check_type(line, path, "text", "window.icon", "path")
            found = path if os.path.isfile(path) else os.path.join(self.api.base_dir, path)
            if not os.path.isfile(found):
                fail(line, f"I couldn't find the icon '{path}'. Is the name spelled right?", path=path)
            g(line, lambda: h.set_icon(found))
            return None

        def flag_method(setter):
            def method(line, flag=True):
                g(line, lambda: setter(_tb.truthy(flag)))
                return None
            return method

        def resize(line, width, height):
            width = check_type(line, width, "whole", "window.resize", "width")
            height = check_type(line, height, "whole", "window.resize", "height")
            for name, size in (("width", width), ("height", height)):
                if not 100 <= size <= 5000:
                    fail(line, f"window.resize() needs a {name} between 100 and 5000 pixels, not {size}.")
            g(line, lambda: h.resize(width, height))
            return None

        def on_close(line, fn):
            check_type(line, fn, "function", "window.on_close", "fn")

            def asked():
                return self.fire(fn, line, [])

            g(line, lambda: h.on_close(asked))
            return None

        def on_key(line, fn):
            check_type(line, fn, "function", "window.on_key", "fn")
            g(line, lambda: h.bind_any_key(lambda combo: self.fire(fn, line, [combo])))
            return None

        def on_drop(line, fn):
            check_type(line, fn, "function", "window.on_drop", "fn")
            g(line, lambda: h.on_drop(lambda paths: self.fire(fn, line, [list(paths)])))
            return None

        def tray(line, items, options=None):
            check_type(line, items, "map", "window.tray", "menu")
            o = self.options(line, options, {"title": "text"}, "window.tray")
            menu_items = self.menu_items(line, items)
            for item in menu_items:
                if item is not None and isinstance(item[1], list):
                    fail(line, f'window.tray() menus can\'t have sub-menus ("{item[0]}").')
            tray_h = g(line, lambda: h.tray(o.get("title") or h.get_title(), menu_items))
            t = {"kind": "tray"}
            self.method(t, "tray", "minimize", 0, 0, lambda ln: g(ln, tray_h.minimize))
            self.method(t, "tray", "restore", 0, 0, lambda ln: g(ln, tray_h.restore))
            self.method(t, "tray", "show", 0, 0, lambda ln: g(ln, tray_h.show))
            self.method(t, "tray", "hide", 0, 0, lambda ln: g(ln, tray_h.hide))
            self.method(t, "tray", "visible", 0, 0, lambda ln: bool(tray_h.shown))
            return t

        def run_in_background(line, fn, on_done=None, on_error=None):
            check_type(line, fn, "function", "window.run_in_background", "fn")
            check_type(line, on_done, "function|nil", "window.run_in_background", "on_done")
            check_type(line, on_error, "function|nil", "window.run_in_background", "on_error")

            def work():
                try:
                    return True, self.api.call(fn, [], line)
                except TBError as err:
                    return False, err
                except _tb.TBExit as stop:
                    return False, TBError("exit() can't be used from background work; call it from on_done instead.", line)
                except Exception as err:
                    return False, TBError(f"Something went wrong in background work: {err}", line)

            def done(ok, value):
                if ok:
                    if on_done is not None:
                        self.fire(on_done, line, [value])
                elif on_error is not None:
                    self.fire(on_error, line, [value.message if hasattr(value, "message") else str(value)])
                else:
                    self.report(value)

            g(line, lambda: h.background(work, done))
            return None

        M("icon", 1, 1, icon)
        M("always_on_top", 0, 1, flag_method(h.set_topmost))
        M("fullscreen", 0, 1, flag_method(h.set_fullscreen))
        M("is_fullscreen", 0, 0, lambda line: bool(g(line, h.is_fullscreen)))
        M("center", 0, 0, lambda line: g(line, h.center))
        M("resize", 2, 2, resize)
        M("size", 0, 0, lambda line: list(g(line, h.size)))
        M("on_close", 1, 1, on_close)
        M("on_key", 1, 1, on_key)
        M("bind_shortcut", 2, 2, key)
        M("on_drop", 1, 1, on_drop)
        M("on_paste_paths", 1, 1, on_drop)
        M("tray", 1, 2, tray)
        M("hide", 0, 0, lambda line: g(line, h.hide))
        M("show", 0, 0, lambda line: g(line, h.show))
        M("run_in_background", 1, 3, run_in_background)
        M("theme", 0, 0, lambda line: h.theme)

        M("title", 0, 1, title)
        M("menu", 1, 1, menu)
        M("status", 0, 1, status)
        M("every", 2, 2, lambda line, ms, fn: timer(line, ms, fn, True))
        M("after", 2, 2, lambda line, ms, fn: timer(line, ms, fn, False))
        M("key", 2, 2, key)
        M("run", 0, 0, run)
        M("close", 0, 0, lambda line: g(line, h.close))
        M("is_open", 0, 0, lambda line: bool(h.is_open()))

    def menu_items(self, line, spec):
        items = []
        for name, value in spec.items():
            label = to_text(name)
            if label and set(label) == {"-"}:
                items.append(None)
            elif isinstance(value, dict):
                items.append((label, self.menu_items(line, value)))
            elif _tb.type_name(value) == "function":
                items.append((label, self.callback(value, line)))
            else:
                fail(line, f'window.menu() needs a function (or a map for a sub-menu) for "{label}", '
                           f'not {_tb.type_name(value)}. Use "-" as the name for a separator line.')
        return items

    # -- widgets ----------------------------------------------------------------

    def container_fns(self, obj, c, h, owner):
        """Give obj (a window, row or column map) the widget-adding functions."""
        gui = self

        def M(name, lo, hi, func):
            gui.method(obj, owner, name, lo, hi, func)

        def g(line, work):
            return gui.guard(line, work, h)

        def add(line, kind, spec):
            if not h.is_open():
                fail(line, "That window is closed, so I can't add to it any more.")
            return g(line, lambda: c.add(kind, spec))

        def enable_fns(w, wh, kind, state=None):
            state = {} if state is None else state

            def set_enabled(flag):
                state["enabled"] = flag
                wh.set_enabled(flag)

            gui.method(w, kind, "enable", 0, 1,
                       lambda line, flag=True: g(line, lambda: set_enabled(_tb.truthy(flag))))
            gui.method(w, kind, "disable", 0, 0, lambda line: g(line, lambda: set_enabled(False)))

        def text_fns(w, wh, kind):
            gui.method(w, kind, "set", 1, 1, lambda line, t: g(line, lambda: wh.set(to_text(t))))
            gui.method(w, kind, "value", 0, 0, lambda line: g(line, wh.get))

        def label(line, text, options=None):
            o = gui.options(line, options, {"style": "text", "wrap": "whole", "align": "text", "width": "whole"},
                            "label")
            if o.get("style", "normal") not in LABEL_STYLES:
                fail(line, f'label() knows the styles {", ".join(LABEL_STYLES)}, not "{o["style"]}".')
            if o.get("align", "left") not in ("left", "center", "right"):
                fail(line, f'label() can align "left", "center" or "right", not "{o["align"]}".')
            wh = add(line, "label", dict(o, text=to_text(text)))
            w = {"kind": "label"}
            text_fns(w, wh, "label")

            def restyle(ln, name):
                check_type(ln, name, "text", "label.style", "style")
                if name not in LABEL_STYLES:
                    fail(ln, f'label.style() knows the styles {", ".join(LABEL_STYLES)}, not "{name}".')
                g(ln, lambda: wh.set_style(name))

            gui.method(w, "label", "style", 1, 1, restyle)
            return w

        def button(line, text, on_click=None, options=None):
            if isinstance(on_click, dict) and options is None:
                on_click, options = None, on_click
            check_type(line, on_click, "function|nil", "button", "on_click")
            o = gui.options(line, options, {"primary": "bool", "width": "whole", "expand": "bool"}, "button")
            spec = dict(o, text=to_text(text), on_click=gui.callback(on_click, line))
            wh = add(line, "button", spec)
            w = {"kind": "button"}
            text_fns(w, wh, "button")
            state = {"enabled": True}
            # A disabled button ignores click(), like a real click would.
            gui.method(w, "button", "click", 0, 0,
                       lambda ln: (gui.fire(on_click, line)
                                   if on_click is not None and state["enabled"] else None))
            enable_fns(w, wh, "button", state)
            return w

        def input_(line, options=None):
            o = gui.options(line, options, {"placeholder": "text", "value": "text|number", "on_enter": "function",
                                            "on_change": "function", "secret": "bool", "width": "whole",
                                            "expand": "bool"}, "input")
            box = {}
            current = lambda: [box["h"].get()]  # noqa: E731
            spec = dict(o, value=to_text(o.get("value", "")), on_enter=gui.callback(o.get("on_enter"), line, current),
                        on_change=gui.callback(o.get("on_change"), line, current))
            wh = box["h"] = add(line, "input", spec)
            w = {"kind": "input"}
            text_fns(w, wh, "input")
            gui.method(w, "input", "clear", 0, 0, lambda ln: g(ln, lambda: wh.set("")))
            gui.method(w, "input", "focus", 0, 0, lambda ln: g(ln, wh.focus))
            enable_fns(w, wh, "input")
            return w

        def textarea(line, options=None):
            o = gui.options(line, options, {"value": "text", "on_change": "function", "height": "whole",
                                            "mono": "bool", "wrap": "bool", "readonly": "bool",
                                            "expand": "bool"}, "textarea")
            box = {}
            spec = dict(o, on_change=gui.callback(o.get("on_change"), line, lambda: [box["h"].get()]))
            wh = box["h"] = add(line, "textarea", spec)
            w = {"kind": "textarea"}
            text_fns(w, wh, "textarea")
            gui.method(w, "textarea", "append", 1, 1, lambda ln, t: g(ln, lambda: wh.append(to_text(t))))
            gui.method(w, "textarea", "clear", 0, 0, lambda ln: g(ln, lambda: wh.set("")))
            gui.method(w, "textarea", "focus", 0, 0, lambda ln: g(ln, wh.focus))
            enable_fns(w, wh, "textarea")
            return w

        def checkbox(line, text, options=None):
            o = gui.options(line, options, {"checked": "bool", "on_change": "function"}, "checkbox")
            box = {}
            spec = dict(o, text=to_text(text),
                        on_change=gui.callback(o.get("on_change"), line, lambda: [bool(box["h"].get())]))
            wh = box["h"] = add(line, "checkbox", spec)
            w = {"kind": "checkbox"}
            gui.method(w, "checkbox", "value", 0, 0, lambda ln: bool(g(ln, wh.get)))
            gui.method(w, "checkbox", "set", 1, 1, lambda ln, v: g(ln, lambda: wh.set(_tb.truthy(v))))
            enable_fns(w, wh, "checkbox")
            return w

        def select(line, choices, options=None):
            check_type(line, choices, "list", "select", "choices")
            o = gui.options(line, options, {"value": "any", "on_change": "function", "width": "whole",
                                            "expand": "bool"}, "select")
            state = {"choices": [to_text(x) for x in choices]}

            def pick(ln, value, fn_name):
                text = to_text(value)
                if text not in state["choices"]:
                    shown = ", ".join(state["choices"]) or "(none)"
                    fail(ln, f'{fn_name}() needs one of the choices ({shown}), not "{text}".')
                return text

            first = state["choices"][0] if state["choices"] else ""
            value = pick(line, o["value"], "select") if "value" in o else first
            box = {}
            spec = dict(o, items=state["choices"], value=value,
                        on_change=gui.callback(o.get("on_change"), line, lambda: [box["h"].get()]))
            wh = box["h"] = add(line, "select", spec)
            w = {"kind": "select"}
            gui.method(w, "select", "value", 0, 0, lambda ln: g(ln, wh.get))
            gui.method(w, "select", "set", 1, 1, lambda ln, v: g(ln, lambda: wh.set(pick(ln, v, "select.set"))))
            gui.method(w, "select", "options", 0, 0, lambda ln: list(state["choices"]))

            def set_options(ln, items):
                check_type(ln, items, "list", "select.set_options", "choices")
                state["choices"] = [to_text(x) for x in items]
                keep = wh.get()
                g(ln, lambda: wh.set_items(state["choices"]))
                if keep not in state["choices"]:
                    g(ln, lambda: wh.set(state["choices"][0] if state["choices"] else ""))

            gui.method(w, "select", "set_options", 1, 1, set_options)
            enable_fns(w, wh, "select")
            return w

        def rows_widget(w, wh, kind, items, cols, line):
            """Shared by table (rows are lists) and list_box (items are values)."""
            is_table = kind == "table"

            def shown(value):
                if not is_table:
                    return to_text(value)
                return [to_text(v) for v in value] + [""] * (cols - len(value))

            def check_row(ln, row, fn_name):
                if is_table:
                    check_type(ln, row, "list", fn_name, "row")
                    if len(row) > cols:
                        fail(ln, f"{fn_name}() got a row with {len(row)} values, but the table has "
                                 f"{cols} column{'s' if cols != 1 else ''}.")
                    return list(row)
                return row

            def index_arg(ln, i, fn_name):
                i = check_type(ln, i, "whole", fn_name, "index")
                if not 0 <= i < len(items):
                    fail(ln, f"{fn_name}() got index {i}, but there "
                             f"{'is' if len(items) == 1 else 'are'} {len(items)} "
                             f"{'row' if is_table else 'item'}{'' if len(items) == 1 else 's'} "
                             f"(the first is 0).")
                return i

            def refresh(ln):
                if is_table:
                    g(ln, lambda: wh.set_rows([shown(r) for r in items]))
                else:
                    g(ln, lambda: wh.set_items([shown(x) for x in items]))

            def add_one(ln, row):
                row = check_row(ln, row, f"{kind}.{'add_row' if is_table else 'add'}")
                items.append(row)
                if is_table:
                    g(ln, lambda: wh.insert_row(shown(row)))
                else:
                    refresh(ln)

            def set_all(ln, rows):
                name = f"{kind}.{'set_rows' if is_table else 'set_items'}"
                check_type(ln, rows, "list", name, "rows" if is_table else "items")
                new = [check_row(ln, r, name) for r in rows]
                items[:] = new
                refresh(ln)

            def selected_index(ln=None):
                i = g(ln, wh.selected_index)
                return i if i is not None and 0 <= i < len(items) else None

            def copy(x):
                return list(x) if isinstance(x, list) else x

            def selected(ln):
                i = selected_index(ln)
                return None if i is None else copy(items[i])

            def remove(ln, i):
                i = index_arg(ln, i, f"{kind}.remove")
                del items[i]
                refresh(ln)

            def select_(ln, i):
                i = None if i is None else index_arg(ln, i, f"{kind}.select")
                g(ln, lambda: wh.select(i))

            gui.method(w, kind, "add_row" if is_table else "add", 1, 1, add_one)
            gui.method(w, kind, "set_rows" if is_table else "set_items", 1, 1, set_all)
            gui.method(w, kind, "rows" if is_table else "items", 0, 0, lambda ln: [copy(x) for x in items])
            gui.method(w, kind, "selected", 0, 0, selected)
            gui.method(w, kind, "selected_index", 0, 0, lambda ln: selected_index(ln))
            gui.method(w, kind, "select", 1, 1, select_)
            gui.method(w, kind, "remove", 1, 1, remove)
            gui.method(w, kind, "clear", 0, 0, lambda ln: set_all(ln, []))
            gui.method(w, kind, "count", 0, 0, lambda ln: len(items))
            enable_fns(w, wh, kind)

            def event_args():
                i = selected_index(line)
                return [None, None] if i is None else [copy(items[i]), i]

            return event_args

        def table(line, columns, options=None):
            check_type(line, columns, "list", "table", "columns")
            if not columns:
                fail(line, "table() needs at least one column name, like table([\"Name\", \"Score\"]).")
            o = gui.options(line, options, {"height": "whole", "widths": "list", "on_select": "function",
                                            "on_open": "function", "expand": "bool"}, "table")
            for width in o.get("widths", []):
                check_type(line, width, "whole", "table", "each width")
            items, box = [], {}
            ev = lambda: box["ev"]()  # noqa: E731
            spec = dict(o, columns=[to_text(c) for c in columns],
                        on_select=gui.callback(o.get("on_select"), line, ev),
                        on_open=gui.callback(o.get("on_open"), line, ev))
            wh = add(line, "table", spec)
            w = {"kind": "table", "columns": [to_text(c) for c in columns]}
            box["ev"] = rows_widget(w, wh, "table", items, len(columns), line)
            return w

        def list_box(line, items_in=None, options=None):
            if isinstance(items_in, dict) and options is None:
                items_in, options = None, items_in
            check_type(line, items_in, "list|nil", "list_box", "items")
            o = gui.options(line, options, {"height": "whole", "on_select": "function", "on_open": "function",
                                            "expand": "bool"}, "list_box")
            items, box = list(items_in or []), {}
            ev = lambda: box["ev"]()  # noqa: E731
            spec = dict(o, items=[to_text(x) for x in items],
                        on_select=gui.callback(o.get("on_select"), line, ev),
                        on_open=gui.callback(o.get("on_open"), line, ev))
            wh = add(line, "list_box", spec)
            w = {"kind": "list_box"}
            box["ev"] = rows_widget(w, wh, "list_box", items, 0, line)
            return w

        def picture_path(ln, path, fn_name):
            check_type(ln, path, "text", fn_name, "path")
            found = path
            if not os.path.isfile(found):
                beside = os.path.join(gui.api.base_dir, path)
                if os.path.isfile(beside):
                    found = beside
                else:
                    fail(ln, f"I couldn't find the picture '{path}'. Is the name spelled right?", path=path)
            ext = os.path.splitext(found)[1].lower()
            if ext not in (".png", ".gif"):
                fail(ln, f"{fn_name}() shows PNG and GIF pictures, and '{path}' is a "
                         f"{ext or 'file without an extension'}{' file' if ext else ''}.")
            return found

        def image(line, path, options=None):
            gui.options(line, options, {}, "image")
            wh = add(line, "image", {"path": picture_path(line, path, "image")})
            w = {"kind": "image"}
            gui.method(w, "image", "set", 1, 1, lambda ln, p: g(ln, lambda: wh.set(picture_path(ln, p, "image.set"))))
            gui.method(w, "image", "value", 0, 0, lambda ln: g(ln, wh.get))
            return w

        def progress(line, options=None):
            o = gui.options(line, options, {"value": "number"}, "progress")

            def clamp(v):
                return _tb.tidy_number(min(max(v, 0), 100))

            wh = add(line, "progress", {"value": clamp(o.get("value", 0))})
            w = {"kind": "progress"}

            def set_(ln, v):
                check_type(ln, v, "number", "progress.set", "value")
                g(ln, lambda: wh.set(clamp(v)))

            gui.method(w, "progress", "set", 1, 1, set_)
            gui.method(w, "progress", "value", 0, 0, lambda ln: g(ln, wh.get))
            return w

        def separator(line):
            add(line, "separator", {})
            return None

        def container(kind):
            def make(line, options=None):
                o = gui.options(line, options, {"expand": "bool"}, kind)
                ch = add(line, kind, o)
                w = {"kind": kind}
                gui.container_fns(w, ch, h, kind)
                return w
            return make

        # -- 3.0 widgets --

        def tabs(line, names, options=None):
            check_type(line, names, "list", "tabs", "names")
            if not names:
                fail(line, 'tabs() needs at least one page name, like tabs(["Overview", "Details"]).')
            o = gui.options(line, options, {"on_change": "function", "expand": "bool"}, "tabs")
            page_names = [to_text(n) for n in names]
            if len(set(page_names)) != len(page_names):
                fail(line, "tabs() needs every page name to be different.")
            box = {}
            spec = dict(o, names=page_names, on_change=gui.callback(o.get("on_change"), line, lambda: [box["h"].selected()]))
            th = box["h"] = add(line, "tabs", spec)
            w = {"kind": "tabs"}
            pages = {}
            for name in page_names:
                page = {"kind": "page", "name": name}
                gui.container_fns(page, th.page(name), h, "page")
                pages[name] = page

            def page_of(ln, name, fn_name):
                name = to_text(name)
                if name not in pages:
                    fail(ln, f'{fn_name}() knows the pages {", ".join(pages)}, not "{name}".')
                return name

            def add_page(ln, name):
                name = to_text(name)
                if name in pages:
                    fail(ln, f'tabs.add() already has a page called "{name}".')
                page = {"kind": "page", "name": name}
                gui.container_fns(page, g(ln, lambda: th.add_page(name)), h, "page")
                pages[name] = page
                return page

            gui.method(w, "tabs", "page", 1, 1, lambda ln, n: pages[page_of(ln, n, "tabs.page")])
            gui.method(w, "tabs", "pages", 0, 0, lambda ln: list(pages))
            gui.method(w, "tabs", "add", 1, 1, add_page)
            gui.method(w, "tabs", "select", 1, 1, lambda ln, n: g(ln, lambda: th.select(page_of(ln, n, "tabs.select"))))
            gui.method(w, "tabs", "selected", 0, 0, lambda ln: g(ln, th.selected))
            enable_fns(w, th, "tabs")
            return w

        def tree(line, columns, options=None):
            check_type(line, columns, "list", "tree", "columns")
            if not columns:
                fail(line, "tree() needs at least one column name, like tree([\"Name\", \"Count\"]).")
            o = gui.options(line, options, {"height": "whole", "widths": "list", "on_select": "function",
                                            "on_open": "function", "expand": "bool"}, "tree")
            for width in o.get("widths", []):
                check_type(line, width, "whole", "tree", "each width")
            cols = len(columns)
            nodes = {}       # id -> {"parent": id|None, "values": list}
            counter = {"n": 0}
            box = {}

            def shown(values):
                return [to_text(v) for v in values] + [""] * (cols - len(values))

            def node_arg(ln, node_id, fn_name):
                if node_id not in nodes:
                    fail(ln, f"{fn_name}() got the id {to_text(node_id, nested=True)}, but there is no such row in the tree.")
                return node_id

            def values_arg(ln, values, fn_name):
                if not isinstance(values, list):
                    values = [values]
                if len(values) > cols:
                    fail(ln, f"{fn_name}() got a row with {len(values)} values, but the tree has "
                             f"{cols} column{'s' if cols != 1 else ''}.")
                return list(values)

            def event_args():
                i = g(line, box["h"].selected_id)
                return [None, None] if i not in nodes else [list(nodes[i]["values"]), i]

            spec = dict(o, columns=[to_text(c) for c in columns],
                        on_select=gui.callback(o.get("on_select"), line, event_args),
                        on_open=gui.callback(o.get("on_open"), line, event_args))
            th = box["h"] = add(line, "tree", spec)
            w = {"kind": "tree", "columns": [to_text(c) for c in columns]}

            def add_node(ln, parent, values, options=None):
                if parent is not None:
                    node_arg(ln, parent, "tree.add")
                values = values_arg(ln, values, "tree.add")
                opts = gui.options(ln, options, {"open": "bool"}, "tree.add")
                counter["n"] += 1
                node_id = f"n{counter['n']}"
                nodes[node_id] = {"parent": parent, "values": values}
                g(ln, lambda: th.add_node(node_id, parent, shown(values), opts.get("open", True)))
                return node_id

            def set_node(ln, node_id, values):
                node_arg(ln, node_id, "tree.set")
                values = values_arg(ln, values, "tree.set")
                nodes[node_id]["values"] = values
                g(ln, lambda: th.set_node(node_id, shown(values)))

            def remove(ln, node_id):
                node_arg(ln, node_id, "tree.remove")
                doomed = {node_id}
                changed = True
                while changed:
                    changed = False
                    for nid, info in nodes.items():
                        if info["parent"] in doomed and nid not in doomed:
                            doomed.add(nid)
                            changed = True
                for nid in doomed:
                    del nodes[nid]
                g(ln, lambda: th.remove_node(node_id))

            def clear(ln):
                nodes.clear()
                g(ln, th.clear_nodes)

            def selected_id(ln):
                i = g(ln, th.selected_id)
                return i if i in nodes else None

            def selected(ln):
                i = selected_id(ln)
                return None if i is None else list(nodes[i]["values"])

            def select(ln, node_id):
                if node_id is not None:
                    node_arg(ln, node_id, "tree.select")
                g(ln, lambda: th.select_id(node_id))

            def children(ln, node_id=None):
                if node_id is not None:
                    node_arg(ln, node_id, "tree.children")
                return [nid for nid, info in nodes.items() if info["parent"] == node_id]

            def expand(ln, node_id, flag=True):
                node_arg(ln, node_id, "tree.expand")
                g(ln, lambda: th.expand(node_id, _tb.truthy(flag)))

            def expand_all(ln, flag=True):
                for nid in list(nodes):
                    g(ln, lambda nid=nid: th.expand(nid, _tb.truthy(flag)))

            gui.method(w, "tree", "add", 2, 3, add_node)
            gui.method(w, "tree", "set", 2, 2, set_node)
            gui.method(w, "tree", "values", 1, 1, lambda ln, i: list(nodes[node_arg(ln, i, "tree.values")]["values"]))
            gui.method(w, "tree", "parent", 1, 1, lambda ln, i: nodes[node_arg(ln, i, "tree.parent")]["parent"])
            gui.method(w, "tree", "children", 0, 1, children)
            gui.method(w, "tree", "remove", 1, 1, remove)
            gui.method(w, "tree", "clear", 0, 0, clear)
            gui.method(w, "tree", "count", 0, 0, lambda ln: len(nodes))
            gui.method(w, "tree", "ids", 0, 0, lambda ln: list(nodes))
            gui.method(w, "tree", "selected", 0, 0, selected)
            gui.method(w, "tree", "selected_id", 0, 0, selected_id)
            gui.method(w, "tree", "select", 1, 1, select)
            gui.method(w, "tree", "expand", 1, 2, expand)
            gui.method(w, "tree", "collapse", 1, 1, lambda ln, i: expand(ln, i, False))
            gui.method(w, "tree", "expand_all", 0, 1, expand_all)
            gui.method(w, "tree", "is_expanded", 1, 1,
                       lambda ln, i: bool(g(ln, lambda: th.is_expanded(node_arg(ln, i, "tree.is_expanded")))))
            enable_fns(w, th, "tree")
            return w

        CHART_OPTIONS = {"type": "text", "data": "list|map", "labels": "list", "title": "text", "width": "whole",
                         "height": "whole", "empty": "text", "expand": "bool"}

        def chart_spec(ln, options, fn_name):
            o = gui.options(ln, options, CHART_OPTIONS, fn_name)
            if "type" in o and o["type"] not in ("bar", "line", "pie"):
                fail(ln, f'{fn_name}() draws "bar", "line" or "pie" charts, not "{o["type"]}".')
            data = o.get("data")
            if isinstance(data, dict):
                for name, values in data.items():
                    check_type(ln, values, "list", fn_name, f'the values of "{to_text(name)}"')
                    for v in values:
                        check_type(ln, v, "number|nil", fn_name, f'each value of "{to_text(name)}"')
                if o.get("type") == "pie" and len(data) > 1:
                    fail(ln, f"{fn_name}() draws a pie from one list of numbers, not several series.")
            elif isinstance(data, list):
                for v in data:
                    check_type(ln, v, "number|nil", fn_name, "each value in data")
            if "labels" in o:
                o["labels"] = [to_text(x) for x in o["labels"]]
            return o

        def chart(line, options=None):
            o = chart_spec(line, options, "chart")
            o.setdefault("type", "bar")
            o.setdefault("data", [])
            o["data"] = _copy_data(o["data"])
            ch = add(line, "chart", dict(o))
            w = {"kind": "chart"}

            def update(ln, options):
                new = chart_spec(ln, options, "chart.update")
                if "data" in new:
                    new["data"] = _copy_data(new["data"])
                g(ln, lambda: ch.update(new))

            def current(ln, key):
                spec = g(ln, ch.get) or {}
                return _copy_data(spec.get(key))

            gui.method(w, "chart", "update", 1, 1, update)
            gui.method(w, "chart", "set", 1, 1, update)
            gui.method(w, "chart", "data", 0, 0, lambda ln: current(ln, "data"))
            gui.method(w, "chart", "labels", 0, 0, lambda ln: current(ln, "labels") or [])
            gui.method(w, "chart", "type", 0, 0, lambda ln: (g(ln, ch.get) or {}).get("type", "bar"))
            return w

        def canvas(line, width=None, height=None, options=None):
            if isinstance(width, dict) and options is None:
                width, options = None, width
            o = gui.options(line, options, {"width": "whole", "height": "whole", "background": "text",
                                            "on_click": "function", "on_drag": "function", "on_release": "function",
                                            "on_move": "function", "expand": "bool"}, "canvas")
            if width is not None:
                o["width"] = check_type(line, width, "whole", "canvas", "width")
            if height is not None:
                o["height"] = check_type(line, height, "whole", "canvas", "height")
            if "background" in o:
                o["background"] = gui.check_colour(line, o["background"], "canvas", 'the "background" option to be a colour')
            spec = dict(o)
            for name in ("on_click", "on_drag", "on_release", "on_move"):
                if o.get(name):
                    fn = o[name]
                    spec[name] = lambda x, y, fn=fn: gui.fire(fn, line, [x, y])
            cv = add(line, "canvas", spec)
            w = {"kind": "canvas"}

            def num(ln, v, fn_name, what):
                return check_type(ln, v, "number", fn_name, what)

            def draw_opts(ln, options, fn_name, allowed):
                o = gui.options(ln, options, allowed, fn_name)
                for key in ("color", "fill"):
                    if key in o:
                        o[key] = gui.check_colour(ln, o[key], fn_name, f'the "{key}" option to be a colour')
                return o

            SHAPE = {"color": "text", "fill": "text", "width": "number"}

            def line_(ln, x1, y1, x2, y2, options=None):
                o = draw_opts(ln, options, "canvas.line", {"color": "text", "width": "number"})
                return g(ln, lambda: cv.line(*[num(ln, v, "canvas.line", "each coordinate") for v in (x1, y1, x2, y2)], o))

            def shape(name, method):
                def draw(ln, x, y, w_, h_, options=None):
                    o = draw_opts(ln, options, f"canvas.{name}", SHAPE)
                    args = [num(ln, v, f"canvas.{name}", "each coordinate or size") for v in (x, y, w_, h_)]
                    return g(ln, lambda: getattr(cv, method)(*args, o))
                return draw

            def text_(ln, x, y, text, options=None):
                o = draw_opts(ln, options, "canvas.text", {"color": "text", "size": "whole", "bold": "bool", "anchor": "text"})
                if o.get("anchor", "nw") not in ("nw", "n", "ne", "w", "center", "e", "sw", "s", "se"):
                    fail(ln, 'canvas.text() needs anchor to be one of nw, n, ne, w, center, e, sw, s, se.')
                return g(ln, lambda: cv.draw_text(num(ln, x, "canvas.text", "x"), num(ln, y, "canvas.text", "y"), to_text(text), o))

            def image_(ln, x, y, path):
                found = picture_path(ln, path, "canvas.image")
                return g(ln, lambda: cv.image(num(ln, x, "canvas.image", "x"), num(ln, y, "canvas.image", "y"), found))

            gui.method(w, "canvas", "line", 4, 5, line_)
            gui.method(w, "canvas", "rect", 4, 5, shape("rect", "rect"))
            gui.method(w, "canvas", "oval", 4, 5, shape("oval", "oval"))
            gui.method(w, "canvas", "text", 3, 4, text_)
            gui.method(w, "canvas", "image", 3, 3, image_)
            gui.method(w, "canvas", "clear", 0, 0, lambda ln: g(ln, cv.clear))
            gui.method(w, "canvas", "remove", 1, 1, lambda ln, item: g(ln, lambda: cv.remove(check_type(ln, item, "whole", "canvas.remove", "item"))))
            gui.method(w, "canvas", "move", 3, 3, lambda ln, item, dx, dy: g(ln, lambda: cv.move(
                check_type(ln, item, "whole", "canvas.move", "item"), num(ln, dx, "canvas.move", "dx"), num(ln, dy, "canvas.move", "dy"))))
            gui.method(w, "canvas", "size", 0, 0, lambda ln: list(g(ln, cv.size)))
            return w

        def number_widget(kind, line, lo, hi, options):
            lo = check_type(line, lo, "number", kind, "min")
            hi = check_type(line, hi, "number", kind, "max")
            if hi <= lo:
                fail(line, f"{kind}() needs max to be bigger than min, not {_tb.tidy_number(lo)} to {_tb.tidy_number(hi)}.")
            allowed = {"value": "number", "step": "number", "on_change": "function", "width": "whole", "expand": "bool"}
            if kind == "slider":
                allowed["show_value"] = "bool"
            o = gui.options(line, options, allowed, kind)
            if "step" in o and o["step"] <= 0:
                fail(line, f"{kind}() needs step to be more than 0, not {_tb.tidy_number(o['step'])}.")
            box = {}

            def clamp(v):
                return _tb.tidy_number(min(max(v, lo), hi))

            value = clamp(o.get("value", lo))
            spec = dict(o, min=lo, max=hi, value=value,
                        on_change=gui.callback(o.get("on_change"), line, lambda: [_tb.tidy_number(box["h"].get())]))
            wh = box["h"] = add(line, kind, spec)
            w = {"kind": kind, "min": _tb.tidy_number(lo), "max": _tb.tidy_number(hi)}

            def set_(ln, v):
                check_type(ln, v, "number", f"{kind}.set", "value")
                g(ln, lambda: wh.set(clamp(v)))

            gui.method(w, kind, "set", 1, 1, set_)
            gui.method(w, kind, "value", 0, 0, lambda ln: _tb.tidy_number(g(ln, wh.get)))
            enable_fns(w, wh, kind)
            return w

        def date_picker(line, options=None):
            import datetime
            o = gui.options(line, options, {"value": "text", "on_change": "function"}, "date_picker")

            def check_date(ln, text, fn_name):
                text = to_text(text).strip()
                if text == "today":
                    return datetime.date.today().isoformat()
                try:
                    return datetime.date.fromisoformat(text).isoformat()
                except ValueError:
                    fail(ln, f'{fn_name}() needs a date like "2026-03-14" (year-month-day), not "{text}".')

            box = {}
            value = check_date(line, o["value"], "date_picker") if "value" in o else datetime.date.today().isoformat()
            spec = dict(o, value=value, on_change=gui.callback(o.get("on_change"), line, lambda: [box["h"].get()]))
            wh = box["h"] = add(line, "date_picker", spec)
            w = {"kind": "date_picker"}
            gui.method(w, "date_picker", "set", 1, 1, lambda ln, t: g(ln, lambda: wh.set(check_date(ln, t, "date_picker.set"))))
            gui.method(w, "date_picker", "value", 0, 0, lambda ln: g(ln, wh.get))
            enable_fns(w, wh, "date_picker")
            return w

        def color(line, options=None):
            o = gui.options(line, options, {"value": "text", "text": "text", "on_change": "function"}, "color")
            if "value" in o:
                o["value"] = gui.check_colour(line, o["value"], "color", 'the "value" option to be a colour')
            box = {}
            spec = dict(o, on_change=gui.callback(o.get("on_change"), line, lambda: [box["h"].get()]))
            wh = box["h"] = add(line, "color", spec)
            w = {"kind": "color"}
            gui.method(w, "color", "set", 1, 1,
                       lambda ln, v: g(ln, lambda: wh.set(gui.check_colour(ln, v, "color.set", "a colour"))))
            gui.method(w, "color", "value", 0, 0, lambda ln: g(ln, wh.get))
            enable_fns(w, wh, "color")
            return w

        def link(line, text, url=None, options=None):
            if isinstance(url, dict) and options is None:
                url, options = None, url
            check_type(line, url, "text|nil", "link", "url")
            o = gui.options(line, options, {"on_click": "function"}, "link")
            if url is not None and not (url.startswith("http://") or url.startswith("https://") or url.startswith("mailto:")
                                        or url.startswith("file://")):
                fail(line, f'link() needs url to start with http://, https://, mailto: or file://, not "{url}".')
            spec = dict(text=to_text(text), url=url or "", on_click=gui.callback(o.get("on_click"), line))
            wh = add(line, "link", spec)
            w = {"kind": "link"}
            text_fns(w, wh, "link")
            gui.method(w, "link", "set_url", 1, 1,
                       lambda ln, u: g(ln, lambda: wh.set_url(check_type(ln, u, "text", "link.set_url", "url"))))
            gui.method(w, "link", "open", 0, 0, lambda ln: g(ln, wh.click))
            return w

        def badge(line, text, options=None):
            o = gui.options(line, options, {"style": "text"}, "badge")
            if o.get("style", "accent") not in BADGE_STYLES:
                fail(line, f'badge() knows the styles {", ".join(BADGE_STYLES)}, not "{o["style"]}".')
            wh = add(line, "badge", dict(o, text=to_text(text)))
            w = {"kind": "badge"}
            text_fns(w, wh, "badge")

            def restyle(ln, name):
                check_type(ln, name, "text", "badge.style", "style")
                if name not in BADGE_STYLES:
                    fail(ln, f'badge.style() knows the styles {", ".join(BADGE_STYLES)}, not "{name}".')
                g(ln, lambda: wh.set_style(name))

            gui.method(w, "badge", "style", 1, 1, restyle)
            return w

        def card(line, options=None):
            if isinstance(options, str):
                options = {"title": options}
            o = gui.options(line, options, {"title": "text", "direction": "text", "expand": "bool"}, "card")
            if o.get("direction", "column") not in ("row", "column"):
                fail(line, f'card() needs direction to be "row" or "column", not "{o["direction"]}".')
            ch = add(line, "card", o)
            w = {"kind": "card"}
            gui.container_fns(w, ch, h, "card")
            return w

        def scroll(line, options=None):
            o = gui.options(line, options, {"height": "whole", "expand": "bool"}, "scroll")
            ch = add(line, "scroll", o)
            w = {"kind": "scroll"}
            gui.container_fns(w, ch, h, "scroll")
            return w

        def splitter(line, options=None):
            o = gui.options(line, options, {"direction": "text", "expand": "bool"}, "splitter")
            if o.get("direction", "horizontal") not in ("horizontal", "vertical"):
                fail(line, f'splitter() needs direction to be "horizontal" or "vertical", not "{o["direction"]}".')
            sh = add(line, "splitter", o)
            w = {"kind": "splitter"}

            def pane(ln, options=None):
                po = gui.options(ln, options, {"weight": "whole"}, "splitter.pane")
                ph = g(ln, lambda: sh.pane(po.get("weight", 1)))
                p = {"kind": "pane"}
                gui.container_fns(p, ph, h, "pane")
                return p

            gui.method(w, "splitter", "pane", 0, 1, pane)
            return w

        M("tabs", 1, 2, tabs)
        M("tree", 1, 2, tree)
        M("chart", 0, 1, chart)
        M("canvas", 0, 3, canvas)
        M("slider", 2, 3, lambda line, lo, hi, options=None: number_widget("slider", line, lo, hi, options))
        M("spinner", 2, 3, lambda line, lo, hi, options=None: number_widget("spinner", line, lo, hi, options))
        M("date_picker", 0, 1, date_picker)
        M("color", 0, 1, color)
        M("link", 1, 3, link)
        M("badge", 1, 2, badge)
        M("card", 0, 1, card)
        M("scroll", 0, 1, scroll)
        M("splitter", 0, 1, splitter)

        M("label", 1, 2, label)
        M("button", 1, 3, button)
        M("input", 0, 1, input_)
        M("textarea", 0, 1, textarea)
        M("checkbox", 1, 2, checkbox)
        M("select", 1, 2, select)
        M("table", 1, 2, table)
        M("list_box", 0, 2, list_box)
        M("image", 1, 2, image)
        M("progress", 0, 1, progress)
        M("separator", 0, 0, separator)
        M("row", 0, 1, container("row"))
        M("column", 0, 1, container("column"))
        M("clear", 0, 0, lambda line: g(line, c.clear))

    # -- dialogs ----------------------------------------------------------------

    def dialog(self, line, work):
        backend = self.backend(line)
        return self.guard(line, lambda: work(backend))


def load(api):
    """Build the gui module for the TB interpreter."""
    from tblib._api import Module

    m = Module("gui", DOC)
    gui = _Gui(api)

    def post_to_window(fn, args, line):
        """parallel / async Task.on_done callbacks run on the window thread
        while a window is open (tbcore.tasks.MAIN_POSTER)."""
        backend = _backend
        if backend is None or not any(w.is_open() for w in getattr(backend, "windows", [])):
            return False
        backend.on_main(lambda: gui.fire(fn, line, list(args)))
        return True

    from tbcore import tasks as _tasks
    _tasks.MAIN_POSTER = post_to_window

    @m.fn("window", 1, 2, doc="window(title, options?) -> window\n"
                               "Make a window. Add widgets to it, then call win.run() to show it.\n"
                               "\n"
                               "Options:\n"
                               "- width, height: the size in pixels (default: 520 x 400).\n"
                               "- theme: \"light\" or \"dark\".\n"
                               "- resizable: true if the window can be resized.\n"
                               "\n" + WIDGET_DOC.strip())
    def window(line, title, options=None):
        return gui.window(line, title, options)

    @m.fn("alert", 1, 2, doc="alert(message, title?)\nShow a message in a small box with an OK button.")
    def alert(line, message, title="TB"):
        gui.dialog(line, lambda b: b.alert(to_text(message), to_text(title)))
        return None

    @m.fn("confirm", 1, 2, doc="confirm(question, title?) -> bool\nAsk a yes/no question. true means Yes.")
    def confirm(line, message, title="TB"):
        return bool(gui.dialog(line, lambda b: b.confirm(to_text(message), to_text(title))))

    @m.fn("ask", 1, 2, doc="ask(question, default?) -> text\nAsk for a line of text in a small box. "
                           "Gives nil if the person presses Cancel.")
    def ask(line, message, default=""):
        answer = gui.dialog(line, lambda b: b.ask(to_text(message), "TB", to_text(default)))
        return None if answer is None else str(answer)

    @m.fn("open_file", 0, 2, doc="open_file(filters?, title?) -> text\nLet the person pick a file to open. "
                                 "filters: a list like [\".txt\", \".md\"] or a map like {\"Logs\": \"*.log\"}. "
                                 "Gives the path, or nil if they cancel.")
    def open_file(line, filters=None, title="Open"):
        types = _filters(line, filters, "gui.open_file")
        answer = gui.dialog(line, lambda b: b.open_file(to_text(title), types))
        return str(answer) if answer else None

    @m.fn("save_file", 0, 2, doc="save_file(name?, filters?) -> text\nLet the person choose where to save a "
                                 "file (name is the suggested file name). Gives the path, or nil if they cancel.")
    def save_file(line, name=None, filters=None):
        check_type(line, name, "text|nil", "gui.save_file", "name")
        types = _filters(line, filters, "gui.save_file")
        answer = gui.dialog(line, lambda b: b.save_file("Save", types, name))
        return str(answer) if answer else None

    @m.fn("pick_folder", 0, 1, doc="pick_folder(title?) -> text\nLet the person choose a folder. "
                                   "Gives its path, or nil if they cancel.")
    def pick_folder(line, title="Choose a folder"):
        answer = gui.dialog(line, lambda b: b.pick_folder(to_text(title)))
        return str(answer) if answer else None

    @m.fn("available", 0, doc="available() -> bool\nTrue if this computer can open windows "
                              "(tkinter is installed and this isn't the browser playground).")
    def available(line):
        if api.in_browser:
            return False
        backend = _get_backend()
        if isinstance(backend, _TkBackend):
            try:
                _tk()
            except GuiUnavailable:
                return False
        return True

    @m.fn("copy", 1, doc="copy(text)\nPut text on the clipboard, ready to paste into another program.")
    def copy(line, text):
        gui.dialog(line, lambda b: b.copy(to_text(text)))
        return None

    @m.fn("paste", 0, doc="paste() -> text\nThe text on the clipboard (\"\" if there is none).")
    def paste(line):
        got = gui.dialog(line, lambda b: b.paste())
        return got if isinstance(got, str) else ""

    @m.fn("notify", 1, 2, doc="notify(title, text?)\nShow a desktop notification (the small banner the operating "
                             "system shows in a corner). macOS uses Notification Center, Windows a toast, Linux "
                             "notify-send; if none of those work, a plain alert box is shown instead.")
    def notify(line, title, text=""):
        title, text = to_text(title), to_text(text)

        def send(b):
            if not b.notify(title, text):
                b.alert(text or title, title if text else "TB")

        gui.dialog(line, send)
        return None

    @m.fn("theme", 0, 1, doc="theme(name?) -> text\nThe theme new windows use: \"light\", \"dark\" or \"system\" "
                            "(whatever the computer is set to). Call it before gui.window() to set it; "
                            "with no input it gives the current setting.")
    def theme(line, name=None):
        if name is None:
            return gui.default_theme
        check_type(line, name, "text", "gui.theme", "name")
        gui.resolve_theme(line, name, "gui.theme")
        gui.default_theme = name
        return name

    @m.fn("system_theme", 0, doc="system_theme() -> text\n\"dark\" or \"light\": what the operating system is set to.")
    def system_theme_(line):
        return system_theme()

    @m.fn("accent", 0, 1, doc="accent(colour?) -> text\nThe accent colour new windows use for buttons, selections "
                             "and charts, like \"#2a7de1\". nil goes back to TB's own red. With no input it gives "
                             "the current colour (nil when it is TB's own).")
    def accent(line, colour="__keep__"):
        if colour == "__keep__":
            return gui.default_accent
        if colour is None:
            gui.default_accent = None
            return None
        gui.default_accent = gui.check_colour(line, colour, "gui.accent", "a colour")
        return gui.default_accent

    @m.fn("scale", 0, 1, doc="scale(factor?) -> number\nHow big everything is drawn (high-DPI scaling). 1 is "
                            "normal; 1.5 makes text and widgets half as big again. Set it before making windows. "
                            "With no input it gives the current factor.")
    def scale(line, factor=None):
        if factor is not None:
            factor = check_type(line, factor, "number", "gui.scale", "factor")
            if not 0.5 <= factor <= 4:
                fail(line, f"gui.scale() needs a factor between 0.5 and 4, not {_tb.tidy_number(factor)}.")
        got = gui.dialog(line, lambda b: b.scale(factor))
        return _tb.tidy_number(got)

    @m.fn("on_main", 1, doc="on_main(fn)\nRun fn on the window thread as soon as it is free. Background work "
                           "(win.run_in_background, parallel tasks) must change widgets through this, because "
                           "windows can only be touched from their own thread.")
    def on_main(line, fn):
        check_type(line, fn, "function", "gui.on_main", "fn")
        gui.dialog(line, lambda b: b.on_main(lambda: gui.fire(fn, line, [])))
        return None

    m.value("THEMES", sorted(THEMES) + ["system"], doc="The window themes: \"dark\", \"light\" and \"system\".")
    m.value("BADGE_STYLES", list(BADGE_STYLES), doc="The badge() styles.")
    return m
