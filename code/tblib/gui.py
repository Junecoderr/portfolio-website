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
GAP = 8


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
    simulate people: click(), type(text), enter(), toggle(), choose(i), open(i)."""

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
        }.get(kind, lambda: None)()
        self.items = list(spec.get("items", []))   # select / list_box texts
        self.rows = []                              # table texts
        self.selected = None

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

    # -- simulating a person ---------------------------------------------------

    @property
    def text(self):
        """What the widget shows (label/button text, input value, ...)."""
        return self.state

    def _fire(self, name):
        cb = self.spec.get(name)
        if cb is not None and self.enabled:
            cb()

    def click(self):
        assert self.kind == "button", f"can't click a {self.kind}"
        self._fire("on_click")

    def type(self, text):
        assert self.kind in ("input", "textarea"), f"can't type into a {self.kind}"
        self.state = text
        self._fire("on_change")

    def enter(self):
        assert self.kind == "input"
        self._fire("on_enter")

    def toggle(self):
        assert self.kind == "checkbox"
        self.state = not self.state
        self._fire("on_change")

    def choose(self, which):
        """select: choose an option (text); table/list_box: select row i."""
        if self.kind == "select":
            assert which in self.items, f"{which!r} is not one of {self.items}"
            self.state = which
            self._fire("on_change")
        else:
            assert self.kind in ("table", "list_box")
            self.selected = which
            self._fire("on_select")

    def open(self, index):
        """Double-click a row of a table or list_box."""
        self.selected = index
        self._fire("on_open")

    def __repr__(self):
        return f"<FakeWidget {self.kind} {self.state!r}>"


class FakeContainer:
    def __init__(self, window, kind, spec=None):
        self.window = window
        self.kind = kind
        self.spec = spec or {}
        self.children = []
        self.enabled = True

    def add(self, kind, spec):
        if kind in ("row", "column"):
            child = FakeContainer(self.window, kind, spec)
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


class FakeWindow:
    def __init__(self, backend, title, width, height, theme, resizable):
        self.backend = backend
        self.title = title
        self.width, self.height, self.theme, self.resizable = width, height, theme, resizable
        self.body = FakeContainer(self, "column")
        self.menu = []
        self.status_text = None
        self.keys = {}
        self.open = True
        self.ran = False
        self._timers = set()

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

    # -- simulating a person ---------------------------------------------------

    def widgets(self, kind=None):
        return [w for w in self.body.walk() if kind is None or w.kind == kind]

    def find(self, kind=None, text=None):
        """The first widget of that kind (and with that text, if given)."""
        for w in self.widgets(kind):
            if text is None or w.state == text:
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
        seq = key_sequence(combo)
        assert seq in self.keys, f"nothing bound to {combo} ({seq})"
        self.keys[seq]()


class FakeBackend:
    """A backend with no screen. answers: what dialogs reply, in order
    (confirm -> bool, ask/open_file/save_file/pick_folder -> text or None)."""

    def __init__(self, answers=None):
        self.windows = []
        self.dialogs = []
        self.answers = list(answers or [])
        self.now = 0
        self._heap = []
        self._seq = 0
        self._live = {}

    def window(self, title, width, height, theme, resizable):
        win = FakeWindow(self, title, width, height, theme, resizable)
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
    }


def _apply_style(root, theme, fonts):
    from tkinter import ttk

    p = THEMES[theme]
    P = "Tb" + theme.capitalize()
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
    def __init__(self):
        self.windows = []

    @property
    def root(self):
        return _tk_root()

    @property
    def fonts(self):
        _tk_root()
        return _TK_SHARED["fonts"]

    def styled(self, theme):
        root = _tk_root()
        if theme not in _TK_SHARED["styled"]:
            _apply_style(root, theme, _TK_SHARED["fonts"])
            _TK_SHARED["styled"].add(theme)

    def window(self, title, width, height, theme, resizable):
        tk, _ = _tk()
        top = tk.Toplevel(_tk_root())
        win = _TkWindow(self, top, title, width, height, theme, resizable)
        self.windows.append(win)
        return win

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
    def __init__(self, backend, top, title, width, height, theme, resizable):
        _, ttk = _tk()
        self.backend, self.top = backend, top
        self.theme, self.pal, self.P = theme, THEMES[theme], "Tb" + theme.capitalize()
        self.fonts = backend.fonts
        backend.styled(theme)
        top.title(title)
        top.configure(background=self.pal["bg"])
        top.minsize(min(width, 280), min(height, 160))
        top.resizable(resizable, resizable)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        x, y = max((sw - width) // 2, 0), max((sh - height) // 3, 0)
        top.geometry(f"{width}x{height}+{x}+{y}")
        top.protocol("WM_DELETE_WINDOW", self.close)
        self.open = True
        self.afters = set()
        self.status_bar = None
        self.body_frame = ttk.Frame(top, style=f"{self.P}.TFrame", padding=18)
        self.body_frame.pack(side="top", fill="both", expand=True)
        self.body = _TkContainer(self, self.body_frame, "column")

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
            self.top.attributes("-topmost", True)
            self.top.after(300, lambda: self.open and self.top.attributes("-topmost", False))
            self.top.focus_force()
        except Exception:
            pass
        self.top.wait_window()

    def close(self):
        if not self.open:
            return
        self.open = False
        for token in list(self.afters):
            self.cancel(token)
        try:
            self.top.destroy()
        except Exception:
            pass

    def is_open(self):
        return self.open


class _TkContainer:
    """A frame that stacks widgets: "column" (top to bottom) or "row"."""

    def __init__(self, win, frame, direction):
        self.win, self.frame, self.direction = win, frame, direction

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

    def add(self, kind, spec):
        tk, ttk = _tk()
        w, P, p, fonts = self.win, self.win.P, self.win.pal, self.win.fonts
        column = self.direction == "column"
        expand = spec.get("expand")
        if kind in ("row", "column"):
            frame = ttk.Frame(self.frame, style=f"{P}.TFrame")
            grow = "x" if (kind == "row" and column) else "both"
            self._place(frame, grow, expand)
            return _TkContainer(w, frame, kind)
        handle = _TK_WIDGETS[kind](self, spec, tk, ttk, P, p, fonts)
        # Keep the handle (and its Tk variables) alive as long as the widget,
        # even if the program throws away the map it got back.
        handle.outer._tb_handle = handle
        default_grow = {
            "label": "x" if column else "none", "button": "none", "checkbox": "x" if column else "none",
            "input": "x", "select": "x", "progress": "x", "separator": "x" if column else "y",
            "textarea": "both", "table": "both", "list_box": "both", "image": "none",
        }[kind]
        if expand is None and not column and kind in ("input", "progress"):
            expand = True  # a text box in a row takes the space the buttons leave
        self._place(handle.outer, default_grow, expand)
        return handle


class _TkHandle:
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
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        self.var = tk.DoubleVar(master=c.frame, value=spec.get("value", 0))
        self.widget = self.outer = ttk.Progressbar(c.frame, variable=self.var, maximum=100, mode="determinate",
                                                   style=f"{P}.Horizontal.TProgressbar")

    def get(self):
        return _tb.tidy_number(self.var.get())

    def set(self, value):
        self.var.set(value)


class _TkSeparator(_TkHandle):
    def __init__(self, c, spec, tk, ttk, P, p, fonts):
        size = {"height": 1} if c.direction == "column" else {"width": 1}
        self.widget = self.outer = tk.Frame(c.frame, background=p["border"], **size)


_TK_WIDGETS = {
    "label": _TkLabel, "button": _TkButton, "input": _TkInput, "textarea": _TkTextArea,
    "checkbox": _TkCheckbox, "select": _TkSelect, "table": _TkTable, "list_box": _TkListBox,
    "image": _TkImage, "progress": _TkProgress, "separator": _TkSeparator,
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
Add widgets to a window, a row() or a column() (they stack top to bottom; a row puts them side by side):
  label(text, options?)            options: style ("normal", "title", "heading", "bold", "muted", "accent", "small", "display", "success", "danger", "mono"), wrap (pixels), align ("left", "center", "right"), width (characters)
  button(text, on_click?, options?) options: primary (true for the main button), width, expand
  input(options?)                  options: placeholder, value, on_enter(text), on_change(text), secret, width, expand
  textarea(options?)               options: value, on_change(text), height (lines), mono, wrap, readonly, expand
  checkbox(text, options?)         options: checked, on_change(checked)
  select(choices, options?)        options: value, on_change(choice), width, expand
  table(columns, options?)         options: height (rows), widths, on_select(row, index), on_open(row, index), expand
  list_box(items?, options?)       options: height, on_select(item, index), on_open(item, index), expand
  image(path)                      a PNG or GIF picture
  progress(options?)               a bar from 0 to 100; options: value
  separator()                      a thin line
  row(options?) / column(options?) containers with all of these too; options: expand
  clear()                          remove everything inside
Every widget gives back a map you can use later:
  label/button: .set(text) .value()   label: .style(name)   button: .click() .enable() .disable()
  input/textarea: .value() .set(text) .clear() .focus()   textarea: .append(text)
  checkbox: .value() .set(bool)            select: .value() .set(choice) .options() .set_options(list)
  table: .add_row(list) .set_rows(rows) .rows() .selected() .selected_index() .select(i) .remove(i) .clear() .count()
  list_box: .add(item) .set_items(list) .items() .selected() .selected_index() .select(i) .remove(i) .clear() .count()
  image: .set(path)   progress: .set(0..100) .value()
Window only: title(text?), menu({"File": {"Open": fn, "-": nil, "Quit": fn}}), status(text),
  every(ms, fn) and after(ms, fn) (both give a timer with .stop()), key("Ctrl+S", fn) ("Mod" is Cmd on a Mac,
  Ctrl elsewhere), run() (shows the window and waits until it closes), close(), is_open().
Your functions can take fewer inputs than offered: fn() { } is fine for on_change too.
If a function you gave the window hits an error, the window shows it in a friendly message and keeps going."""

WINDOW_OPTIONS = {"width": "whole", "height": "whole", "theme": "text", "resizable": "bool"}


def _arity(fn):
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
        """Run a TB callback from a window event. Errors become a friendly
        message (printed and shown in a dialog); exit() closes the windows."""
        try:
            _tb._guarded(lambda: self.api.call(fn, _fit_args(fn, args), line))
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
        theme = o.get("theme", "light")
        if theme not in THEMES:
            fail(line, f'gui.window() knows the themes "light" and "dark", not "{theme}".')
        width, height = o.get("width", 520), o.get("height", 400)
        for name, size in (("width", width), ("height", height)):
            if not 100 <= size <= 5000:
                fail(line, f"gui.window() needs a {name} between 100 and 5000 pixels, not {size}.")
        backend = self.backend(line)
        handle = self.guard(line, lambda: backend.window(title, width, height, theme, o.get("resizable", True)))
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
    from tblib._api import Module

    m = Module("gui", DOC)
    gui = _Gui(api)

    @m.fn("window", 1, 2, doc="window(title, options?) -> window\nMake a window. options: width, height "
                               "(pixels; default 520 x 400), theme (\"light\" or \"dark\"), resizable. "
                               "Add widgets to it, then call win.run() to show it.\n" + WIDGET_DOC.strip())
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

    m.value("THEMES", sorted(THEMES), doc="The window themes: \"dark\" and \"light\".")
    return m
