"""Native desktop hooks for the gui library: file drops and tray icons.

Tk has neither, so this talks to the operating system with ctypes
(standard library only):

* Windows: DragAcceptFiles on the window's frame plus a subclassed window
  procedure that reads WM_DROPFILES; Shell_NotifyIconW for a tray icon,
  whose clicks arrive at a hidden message-only window.
* macOS: the Objective-C runtime. The Tk NSWindow registers for file URLs
  and Tk's TKWindow class gets the NSDraggingDestination methods; the tray
  is an NSStatusItem in the menu bar with a native NSMenu.
* Anywhere else (Linux): tkdnd for drops when that Tk extension is
  installed. There is no tray icon; gui.py keeps its corner chip.

Every entry point returns None (or False) instead of raising, so gui.py can
fall back to what it did before.

Native callbacks (a window procedure, an Objective-C method) must never call
tkinter: _tkinter keeps per-thread state that only its own callbacks restore,
and a tkinter call from a ctypes callback inside Tk's event loop leaves it
cleared, so the next Tk callback crashes Python. They append to an inbox
instead, and a Tk timer runs what is there.
"""

from __future__ import annotations

import collections
import ctypes
import os
import sys

# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _inbox(top):
    """The toplevel's queue of work from native callbacks (deque.append is
    safe from them); a Tk timer drains it every 100 ms while the window lives.
    Must be first called from ordinary Python/Tk code."""
    box = getattr(top, "_tb_native_inbox", None)
    if box is None:
        box = top._tb_native_inbox = collections.deque()

        def drain():
            try:
                while box:
                    box.popleft()()
            finally:
                try:
                    top.after(100, drain)
                except Exception:
                    pass   # the window is gone
        top.after(100, drain)
    return box


def enable_drop(top, cb):
    """Let files be dropped on the Tk toplevel `top`; cb(paths) gets them.
    Gives back how ("windows", "macos", "tkdnd") or None when drops are not
    possible here."""
    def deliver(paths):
        found = [p for p in paths if p and os.path.exists(p)]
        if found:
            top._tb_native_inbox.append(lambda: cb(found))
    hooks = {"win32": [("windows", _win_drop)], "darwin": [("macos", _mac_drop)]}.get(sys.platform, [])
    for how, hook in hooks + [("tkdnd", _tkdnd_drop)]:
        try:
            if hook(top, deliver):
                _inbox(top)
                return how
        except Exception:
            continue
    return None


def make_tray(top, title, items, on_restore):
    """A real tray icon (Windows) or menu bar item (macOS), or None.
    items are (label, fn) pairs or None for a separator; on_restore runs on
    a double click (Windows only: a menu bar item has no double click)."""
    try:
        if sys.platform == "win32":
            return _WinTray(top, title, items, on_restore)
        if sys.platform == "darwin":
            return _MacTray(top, title, items)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# tkdnd (any platform, when the extension is installed)
# ---------------------------------------------------------------------------


def _tkdnd_drop(top, deliver):
    try:
        top.tk.call("package", "require", "tkdnd")
    except Exception:
        return False

    def dropped(data):
        deliver(list(top.tk.splitlist(data)))
        return "copy"

    name = top.register(dropped)
    top.tk.call("tkdnd::drop_target", "register", top._w, "DND_Files")
    top.tk.call("bind", top._w, "<<Drop:DND_Files>>", name + " %D")
    return True


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

_W = None


def _win():
    """user32/shell32 functions with exact prototypes (64-bit safe)."""
    global _W
    if _W is not None:
        return _W
    from ctypes import wintypes as wt

    class W:
        pass
    w = W()
    u = ctypes.WinDLL("user32", use_last_error=True)
    s = ctypes.WinDLL("shell32", use_last_error=True)
    w.LRESULT = ctypes.c_ssize_t
    w.WNDPROC = ctypes.WINFUNCTYPE(w.LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    # SetWindowLongPtrW only exists in 64-bit user32; 32-bit uses SetWindowLongW.
    w.set_long = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
    w.set_long.restype = ctypes.c_void_p
    w.set_long.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
    w.call_proc = u.CallWindowProcW
    w.call_proc.restype = w.LRESULT
    w.call_proc.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    w.create = u.CreateWindowExW
    w.create.restype = wt.HWND
    w.create.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    w.destroy = u.DestroyWindow
    w.destroy.argtypes = [wt.HWND]
    w.load_icon = u.LoadIconW
    w.load_icon.restype = wt.HICON
    w.load_icon.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    w.cursor = u.GetCursorPos
    w.cursor.argtypes = [ctypes.POINTER(wt.POINT)]
    w.foreground = u.SetForegroundWindow
    w.foreground.argtypes = [wt.HWND]
    w.accept = s.DragAcceptFiles
    w.accept.restype = None
    w.accept.argtypes = [wt.HWND, wt.BOOL]
    w.query = s.DragQueryFileW
    w.query.restype = wt.UINT
    w.query.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.c_wchar_p, wt.UINT]
    w.finish = s.DragFinish
    w.finish.restype = None
    w.finish.argtypes = [ctypes.c_void_p]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                    ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                    ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                    ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                    ("guidItem", ctypes.c_ubyte * 16), ("hBalloonIcon", wt.HICON)]
    w.NOTIFYICONDATAW = NOTIFYICONDATAW
    w.notify = s.Shell_NotifyIconW
    w.notify.restype = wt.BOOL
    w.notify.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    _W = w
    return w


GWLP_WNDPROC = -4
WM_DROPFILES = 0x0233
WM_TRAY = 0x8000 + 0x51          # WM_APP + 0x51: our tray callback message
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205


class _WinHook:
    """Subclasses one HWND's window procedure; handlers[msg](wparam, lparam)
    may return a result, or None to let the old procedure handle it."""

    def __init__(self, hwnd):
        w = _win()
        self.hwnd, self.handlers = hwnd, {}
        self.proc = w.WNDPROC(self._proc)   # kept on self: Windows calls it for the window's life
        self.old = w.set_long(hwnd, GWLP_WNDPROC, ctypes.cast(self.proc, ctypes.c_void_p))
        if not self.old:
            raise OSError("SetWindowLongPtrW failed")

    def _proc(self, hwnd, msg, wparam, lparam):
        handler = self.handlers.get(msg)
        if handler is not None:
            try:
                result = handler(wparam, lparam)
            except Exception:
                result = None
            if result is not None:
                return result
        return _win().call_proc(self.old, hwnd, msg, wparam, lparam)

    def unhook(self):
        try:
            _win().set_long(self.hwnd, GWLP_WNDPROC, self.old)
        except Exception:
            pass


def _frame_hwnd(top):
    """The HWND of the toplevel's outer frame (what Explorer drops onto)."""
    return int(top.wm_frame(), 16)


def _win_drop(top, deliver):
    w = _win()
    state = {"hook": None}

    def dropped(wparam, lparam):
        hdrop = ctypes.c_void_p(wparam)
        paths = []
        for i in range(w.query(hdrop, 0xFFFFFFFF, None, 0)):
            size = w.query(hdrop, i, None, 0) + 1
            buf = ctypes.create_unicode_buffer(size)
            w.query(hdrop, i, buf, size)
            paths.append(buf.value)
        w.finish(hdrop)
        deliver(paths)
        return 0

    def install(event=None):
        if event is not None and event.widget is not top:
            return
        hwnd = _frame_hwnd(top)
        hook = state["hook"]
        if hook is not None and hook.hwnd == hwnd:
            return
        # shortcut: Tk rebuilds the frame for a few wm changes; we re-hook on the next <Map>.
        state["hook"] = _WinHook(hwnd)
        state["hook"].handlers[WM_DROPFILES] = dropped
        w.accept(hwnd, True)

    def remove(event=None):
        if event is None or event.widget is top:
            if state["hook"] is not None:
                state["hook"].unhook()
                state["hook"] = None

    # The frame HWND only exists once the window is on screen.
    top.bind("<Map>", install, add=True)
    top.bind("<Destroy>", remove, add=True)
    if top.winfo_ismapped():
        install()
    top._tb_drop_state = state   # tests and debugging can see the hook
    return True


class _WinTray:
    """A notification-area icon. Clicks pop up a Tk menu at the mouse."""
    native = "windows"

    def __init__(self, top, title, items, on_restore):
        import tkinter as tk
        w = _win()
        self.top, self.on_restore, self.inbox = top, on_restore, _inbox(top)
        HWND_MESSAGE = ctypes.c_void_p(-3)
        self.hwnd = w.create(0, "STATIC", "TB tray", 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed")
        self.hook = _WinHook(self.hwnd)
        self.hook.handlers[WM_TRAY] = self._clicked
        self.menu = tk.Menu(top, tearoff=0)
        for item in items:
            if item is None:
                self.menu.add_separator()
            else:
                self.menu.add_command(label=item[0], command=item[1])
        data = w.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = 0x1 | 0x2 | 0x4            # NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        # shortcut: the stock application icon, not the window's own; load a .ico if apps need branding.
        data.hIcon = w.load_icon(None, ctypes.c_void_p(32512))   # IDI_APPLICATION
        data.szTip = str(title)[:127]
        self.data = data
        self.shown = False

    def _clicked(self, wparam, lparam):
        event = lparam & 0xFFFF
        if event == WM_LBUTTONDBLCLK:
            self.inbox.append(self.on_restore)
        elif event in (WM_LBUTTONUP, WM_RBUTTONUP):
            self.inbox.append(self._popup)
        return 0

    def _popup(self):
        w = _win()
        from ctypes import wintypes as wt
        pt = wt.POINT()
        w.cursor(ctypes.byref(pt))
        try:  # without a foreground window the menu would not close on an outside click
            w.foreground(_frame_hwnd(self.top))
        except Exception:
            pass
        try:
            self.menu.tk_popup(pt.x, pt.y)
        finally:
            self.menu.grab_release()

    def show(self):
        if not self.shown:
            self.shown = bool(_win().notify(0, ctypes.byref(self.data)))   # NIM_ADD

    def hide(self):
        if self.shown:
            _win().notify(2, ctypes.byref(self.data))                       # NIM_DELETE
            self.shown = False

    def destroy(self):
        self.hide()
        if self.hwnd:
            self.hook.unhook()
            _win().destroy(self.hwnd)
            self.hwnd = None
        try:
            self.menu.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# macOS (Objective-C runtime)
# ---------------------------------------------------------------------------

_M = None


def _mac():
    global _M
    if _M is not None:
        return _M
    import ctypes.util

    class M:
        pass
    m = M()
    m.objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    m.appkit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    for name, res, args in (
            ("objc_getClass", ctypes.c_void_p, [ctypes.c_char_p]),
            ("sel_registerName", ctypes.c_void_p, [ctypes.c_char_p]),
            ("objc_allocateClassPair", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]),
            ("objc_registerClassPair", None, [ctypes.c_void_p]),
            ("class_addMethod", ctypes.c_bool, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]),
            ("class_getInstanceMethod", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
            ("method_setImplementation", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
            ("object_getClass", ctypes.c_void_p, [ctypes.c_void_p])):
        fn = getattr(m.objc, name)
        fn.restype, fn.argtypes = res, args
    m.cls = lambda name: m.objc.objc_getClass(name.encode())
    m.sel = lambda name: m.objc.sel_registerName(name.encode())
    m.funcs = {}

    def send(obj, sel, *args, restype=ctypes.c_void_p, argtypes=()):
        key = (restype, tuple(argtypes))
        fn = m.funcs.get(key)
        if fn is None:   # objc_msgSend must be called with the method's exact prototype (arm64)
            fn = m.funcs[key] = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
                ("objc_msgSend", m.objc))
        return fn(obj, m.sel(sel), *args)
    m.send = send
    m.string = lambda text: send(m.cls("NSString"), "stringWithUTF8String:", str(text).encode("utf-8"),
                                 argtypes=(ctypes.c_char_p,))
    m.text = lambda ns: (send(ns, "UTF8String", restype=ctypes.c_char_p) or b"").decode("utf-8", "replace")
    m.const = lambda name: ctypes.c_void_p.in_dll(m.appkit, name).value
    m.drop_windows = {}    # NSWindow pointer -> deliver(paths)
    m.tray_targets = {}    # target pointer -> _MacTray
    m.imps = []            # keep every IMP alive
    _M = m
    return m


def _ns_window(top):
    """The NSWindow behind a mapped Tk toplevel, or None."""
    m = _mac()
    top.update_idletasks()
    try:
        fn = ctypes.CDLL(None).Tk_MacOSXGetNSWindowForDrawable
        fn.restype, fn.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        win = fn(top.winfo_id())
        if win:
            return win
    except Exception:
        pass
    # Older Tk: find it by a unique temporary title.
    title, marker = top.title(), f"tb-drop-{id(top)}"
    top.title(marker)
    try:
        wins = m.send(m.send(m.cls("NSApplication"), "sharedApplication"), "windows")
        for i in range(m.send(wins, "count", restype=ctypes.c_ulong)):
            win = m.send(wins, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,))
            if m.text(m.send(win, "title")) == marker:
                return win
    finally:
        top.title(title)
    return None


def _mac_patch_window_class(m, win):
    """Give the Tk window class the NSDraggingDestination methods (once)."""
    if getattr(m, "patched", None):
        return
    cls = m.objc.object_getClass(win)
    OP = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    YES = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

    def entered(self, _cmd, sender):
        return 1 if self in m.drop_windows else 0     # NSDragOperationCopy

    def prepare(self, _cmd, sender):
        return self in m.drop_windows

    def perform(self, _cmd, sender):
        deliver = m.drop_windows.get(self)
        if deliver is None:
            return False
        try:
            deliver(_mac_drag_paths(m, sender))
        except Exception:
            return False
        return True

    for name, kind, fn in (("draggingEntered:", OP, entered), ("draggingUpdated:", OP, entered),
                           ("prepareForDragOperation:", YES, prepare), ("performDragOperation:", YES, perform)):
        imp = kind(fn)
        m.imps.append(imp)
        types = b"Q@:@" if kind is OP else b"c@:@"
        if not m.objc.class_addMethod(cls, m.sel(name), ctypes.cast(imp, ctypes.c_void_p), types):
            # The class already has its own: replace it (Tk itself does not drag-and-drop).
            m.objc.method_setImplementation(m.objc.class_getInstanceMethod(cls, m.sel(name)),
                                            ctypes.cast(imp, ctypes.c_void_p))
    m.patched = True


def _mac_drag_paths(m, sender):
    """File paths on a drag's pasteboard (file URLs only)."""
    pb = m.send(sender, "draggingPasteboard")
    classes = m.send(m.cls("NSArray"), "arrayWithObject:", m.cls("NSURL"), argtypes=(ctypes.c_void_p,))
    yes = m.send(m.cls("NSNumber"), "numberWithBool:", True, argtypes=(ctypes.c_bool,))
    options = m.send(m.cls("NSDictionary"), "dictionaryWithObject:forKey:", yes,
                     m.const("NSPasteboardURLReadingFileURLsOnlyKey"), argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    urls = m.send(pb, "readObjectsForClasses:options:", classes, options, argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    paths = []
    if urls:
        for i in range(m.send(urls, "count", restype=ctypes.c_ulong)):
            url = m.send(urls, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,))
            paths.append(m.text(m.send(url, "path")))
    return paths


def _mac_drop(top, deliver):
    m = _mac()
    state = {"window": None}

    def install(event=None):
        if event is not None and event.widget is not top:
            return
        win = _ns_window(top)
        if not win or win == state["window"]:
            return
        _mac_patch_window_class(m, win)
        types = m.send(m.cls("NSArray"), "arrayWithObject:", m.const("NSPasteboardTypeFileURL"),
                       argtypes=(ctypes.c_void_p,))
        m.send(win, "registerForDraggedTypes:", types, argtypes=(ctypes.c_void_p,))
        m.drop_windows.pop(state["window"], None)
        state["window"] = win
        m.drop_windows[win] = deliver

    def remove(event=None):
        if event is None or event.widget is top:
            m.drop_windows.pop(state["window"], None)
            state["window"] = None

    top.bind("<Map>", install, add=True)
    top.bind("<Destroy>", remove, add=True)
    if top.winfo_ismapped():
        install()
    top._tb_drop_state = state
    return True


class _MacTray:
    """A menu bar status item with a native menu."""
    native = "macos"

    def __init__(self, top, title, items):
        m = _mac()
        self.m, self.top, self.items, self.inbox = m, top, list(items), _inbox(top)
        target_cls = m.cls("TBTrayTarget")
        if not target_cls:
            target_cls = m.objc.objc_allocateClassPair(m.cls("NSObject"), b"TBTrayTarget", 0)
            imp = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(_mac_tray_choose)
            m.imps.append(imp)
            m.objc.class_addMethod(target_cls, m.sel("tbChoose:"), ctypes.cast(imp, ctypes.c_void_p), b"v@:@")
            m.objc.objc_registerClassPair(target_cls)
        self.target = m.send(m.send(target_cls, "alloc"), "init")
        m.tray_targets[self.target] = self
        bar = m.send(m.cls("NSStatusBar"), "systemStatusBar")
        # The status bar does not keep the item alive, so retain it.
        self.item = m.send(m.send(bar, "statusItemWithLength:", -1.0, argtypes=(ctypes.c_double,)), "retain")
        m.send(m.send(self.item, "button"), "setTitle:", m.string(title), argtypes=(ctypes.c_void_p,))
        menu = m.send(m.send(m.cls("NSMenu"), "alloc"), "initWithTitle:", m.string(title), argtypes=(ctypes.c_void_p,))
        for index, item in enumerate(self.items):
            if item is None:
                entry = m.send(m.cls("NSMenuItem"), "separatorItem")
            else:
                entry = m.send(m.send(m.cls("NSMenuItem"), "alloc"), "initWithTitle:action:keyEquivalent:",
                               m.string(item[0]), m.sel("tbChoose:"), m.string(""),
                               argtypes=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
                m.send(entry, "setTarget:", self.target, argtypes=(ctypes.c_void_p,))
                m.send(entry, "setTag:", index, argtypes=(ctypes.c_long,))
            m.send(menu, "addItem:", entry, argtypes=(ctypes.c_void_p,))
        m.send(self.item, "setMenu:", menu, argtypes=(ctypes.c_void_p,))
        self.menu = menu
        self.shown = True
        self.hide()

    def choose(self, index):
        item = self.items[index]
        if item is not None:
            self.inbox.append(item[1])

    def show(self):
        self.m.send(self.item, "setVisible:", True, argtypes=(ctypes.c_bool,))
        self.shown = True

    def hide(self):
        self.m.send(self.item, "setVisible:", False, argtypes=(ctypes.c_bool,))
        self.shown = False

    def destroy(self):
        m = self.m
        if self.item:
            m.send(m.send(m.cls("NSStatusBar"), "systemStatusBar"), "removeStatusItem:", self.item,
                   argtypes=(ctypes.c_void_p,))
            m.send(self.item, "release")
            self.item = None
            m.tray_targets.pop(self.target, None)


def _mac_tray_choose(target, _cmd, sender):
    m = _mac()
    tray = m.tray_targets.get(target)
    if tray is not None:
        tray.choose(m.send(sender, "tag", restype=ctypes.c_long))
