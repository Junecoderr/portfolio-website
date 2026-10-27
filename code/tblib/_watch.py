"""fs.watch / fs.watch_stop: a polling file watcher (data-agent).

fs.py has no watch anchor, so this lives on its own. To switch it on, fs.py's
load() calls, just before `return m`:

    from tblib._watch import install as _install_watch
    _install_watch(m, api)

install() is idempotent (it does nothing if fs already has `watch`).
Polling keeps it stdlib-only and identical on macOS, Linux and Windows.
"""

from __future__ import annotations

import os
import threading

from tblib._api import check_type, fail
from tblib._threads import guarded_call

import tb as _tb

MIN_INTERVAL = 0.05
MAX_INTERVAL = 3600
MAX_ENTRIES = 20_000          # files tracked under one watched folder
SETTLE = 0.02                 # seconds between re-checks while files are still changing
SETTLE_TRIES = 5              # at most this many re-checks before reporting anyway
WATCHERS: list = []           # every live watcher, so fs.watch_stop() and tests can stop them


def snapshot(path: str) -> dict:
    """{path: (mtime_ns, size)} for a file, or every file under a folder."""
    out = {}
    try:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in files:
                    full = os.path.join(root, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    out[full] = (st.st_mtime_ns, st.st_size)
                    if len(out) >= MAX_ENTRIES:
                        return out
        elif os.path.exists(path):
            st = os.stat(path)
            out[path] = (st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return out


def changes(before: dict, after: dict) -> list:
    """Events between two snapshots, sorted by path."""
    events = []
    for p in sorted(set(before) | set(after)):
        if p not in before:
            events.append({"kind": "created", "path": p})
        elif p not in after:
            events.append({"kind": "deleted", "path": p})
        elif before[p] != after[p]:
            events.append({"kind": "changed", "path": p})
    return events


class Watcher:
    def __init__(self, api, path, fn, interval, line):
        self.api, self.path, self.fn, self.interval, self.line = api, path, fn, interval, line
        self.stop_event = threading.Event()
        self.state = snapshot(path)
        self.thread = threading.Thread(target=self.run, name=f"tb-watch-{os.path.basename(path)}", daemon=True)
        self.handle = None
        self.events = 0

    def run(self):
        while not self.stop_event.wait(self.interval):
            now = self.settled(snapshot(self.path))
            for event in changes(self.state, now):
                if self.stop_event.is_set():
                    break
                self.events += 1
                result, err = guarded_call(self.api, self.fn, [event], self.line)
                if err is not None:
                    self.api.output(f"fs.watch: {err.message}")
                elif result is False:
                    self.stop_event.set()
            self.state = now
        self.finish()

    def settled(self, now: dict) -> dict:
        """A snapshot taken once writes in progress have finished.

        Writing a file is several steps (create or truncate, then write), and
        a poll that lands between them would report one write as two events
        ("created" + "changed", or "changed" twice). When something changed,
        look again a moment later until two looks agree."""
        if now == self.state:
            return now
        for _ in range(SETTLE_TRIES):
            if self.stop_event.wait(SETTLE):
                break
            again = snapshot(self.path)
            if again == now:
                break
            now = again
        return now

    def finish(self):
        if self in WATCHERS:
            WATCHERS.remove(self)
        if self.handle is not None:
            self.handle["running"] = False

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(max(2.0, self.interval * 2))
        self.finish()


def stop_all():
    for w in list(WATCHERS):
        w.stop()


def install(m, api):
    """Add watch and watch_stop to the fs Module m."""
    if "watch" in m.fields:
        return m

    @m.fn("watch", 2, 3, doc=(
        "watch(path, fn, interval?) -> watcher\n"
        "Watch a file or folder (and its subfolders) for changes, checking every interval seconds (1 by "
        "default). fn(event) gets {\"kind\": \"created\"|\"changed\"|\"deleted\", \"path\": ...} for each "
        "change; give back false to stop. The program keeps going: call watcher.wait() to block, "
        "watcher.stop() or fs.watch_stop() to end. Errors in fn are printed, not fatal."))
    def watch(line, path, fn, interval=1):
        api.need_desktop(line, "fs.watch")
        check_type(line, path, "text", "fs.watch", "path")
        check_type(line, fn, "function", "fs.watch", "fn")
        check_type(line, interval, "number", "fs.watch", "interval")
        if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            fail(line, f"fs.watch() needs an interval from {MIN_INTERVAL} to {MAX_INTERVAL} seconds, not "
                       f"{_tb.to_text(interval)}.")
        full = os.path.expanduser(path)
        if not os.path.exists(full):
            fail(line, f"I couldn't find '{path}' to watch. Is the name spelled right?", path=path)
        w = Watcher(api, full, fn, float(interval), line)
        handle = {"path": path, "interval": interval, "running": True}
        w.handle = handle

        def stop(line):
            w.stop()
            return None

        def wait(line, seconds=None):
            if seconds is not None:
                check_type(line, seconds, "number", "watcher.wait", "seconds")
            try:
                w.thread.join(seconds)
            except KeyboardInterrupt:
                w.stop()
            return not w.thread.is_alive()

        handle["stop"] = _tb.Builtin("watcher.stop", stop, (0, 0), "stop()\nStop watching.")
        handle["wait"] = _tb.Builtin("watcher.wait", wait, (0, 1),
                                     "wait(seconds?) -> bool\nBlock until the watcher stops (or the seconds pass); "
                                     "true if it stopped. Ctrl+C stops it.")
        WATCHERS.append(w)
        w.thread.start()
        return handle

    @m.fn("watch_stop", 0, 1, doc=(
        "watch_stop(watcher?)\nStop one watcher made by fs.watch, or every watcher when called with nothing."))
    def watch_stop(line, watcher=None):
        if watcher is None:
            stop_all()
            return None
        check_type(line, watcher, "map", "fs.watch_stop", "watcher")
        stopper = watcher.get("stop")
        if stopper is None or not isinstance(stopper, _tb.Builtin):
            fail(line, "fs.watch_stop() needs a watcher made by fs.watch().")
        api.call(stopper, [], line)
        return None

    return m
