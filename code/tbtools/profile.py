"""`tb profile FILE.tb [args...]`: where a TB program spends its time and memory.

    tb profile app.tb                     run app.tb, then show the report
    tb profile --html prof.html app.tb    ... and write a flame graph page
    tb profile app.tb a b                 arguments after the file go to the program

The program runs at full speed while a sampling profiler looks at it about a
thousand times a second: which TB functions are running (the call stack) and
which line each one is on. A sample taken while a library function runs
(`logs.read`, `re.find_all`, ...) is put on that library function. The report:

* functions: self time (its own code, and the library calls it makes) and
  total time (with the TB functions it calls), busiest first;
* lines: the busiest lines;
* memory: the largest values the program still holds when it ends (its
  top-level variables and those of the files it imports), measured deeply
  (a list counts its items too).

`--html FILE` writes the same report with a flame graph: one bar per function,
as wide as the time spent in it, with the functions it called below it. The
page is a single file with no scripts.

How it works: a background thread reads the interpreter's call stack
(`Interpreter.call_stack`) and, for the frame that is running, the exact
instruction from the Python frame that runs it (the VM loop's `pc`, or the
line map of code the VM translated, tbcore/jit.py). Nothing in the program
is slowed down or changed. Only the main thread is sampled (not `parallel`
tasks).
"""

from __future__ import annotations

import html
import os
import sys
import threading
import time
import zlib

__all__ = ["main", "Profiler", "profile_file"]

INTERVAL = 0.001        # seconds between samples
TOP = 15                # rows in each table of the text report


class Profiler:
    """Samples one interpreter's main-thread call stack until stop()."""

    def __init__(self, interp, script_path: str | None = None, interval: float = INTERVAL):
        self.interp = interp
        self.script_path = script_path
        self.interval = interval
        self.stacks: dict[tuple, float] = {}   # (label, ...) outermost first -> seconds
        self.lines: dict[tuple, float] = {}    # (file, line) of the running TB code -> seconds
        self.where: dict[str, tuple] = {}      # label -> (file, line) where the function is defined
        self.samples = 0
        self.elapsed = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._ident = threading.get_ident()
        self._started = 0.0
        self._codes = None

    # -- sampling ---------------------------------------------------------------

    def start(self) -> None:
        from tbcore import vm

        self._codes = (vm.VMInterpreter._run.__code__,)
        self._old_switch = sys.getswitchinterval()
        sys.setswitchinterval(min(self._old_switch, self.interval / 2))  # let the sampler in on time
        self._started = time.perf_counter()
        self._thread = threading.Thread(target=self._loop, name="tb-profiler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        sys.setswitchinterval(self._old_switch)
        self.elapsed = time.perf_counter() - self._started

    def _loop(self) -> None:
        last = time.perf_counter()
        while not self._stop.wait(self.interval):
            now = time.perf_counter()
            try:
                self.sample(now - last)
            except Exception:  # the program changed under us mid-sample: skip this one
                pass
            last = now

    def sample(self, weight: float) -> None:
        pyframe = sys._current_frames().get(self._ident)
        stack = list(getattr(self.interp, "call_stack", ()) or ())
        if pyframe is None or not stack:
            return
        exact, native = self._read_python_stack(pyframe)
        labels = []
        for fr in stack:
            labels.append(self._label(fr))
        top = stack[-1]
        file = self._file_of(top)
        line = self._line_of(top, exact.get(id(top)))
        if native is not None:
            labels.append(native)
        key = tuple(labels)
        self.stacks[key] = self.stacks.get(key, 0.0) + weight
        if line:
            spot = (file, line)
            self.lines[spot] = self.lines.get(spot, 0.0) + weight
        self.samples += 1

    def _read_python_stack(self, pyframe):
        """The exact pc of each TB frame a Python frame is running right now, and the
        library function running on top of TB code (if any)."""
        exact: dict[int, int] = {}
        native = None
        seen_tb = False
        f = pyframe
        while f is not None:
            co = f.f_code
            if co in self._codes:
                loc = f.f_locals
                fr, pc = loc.get("frame"), loc.get("pc")
                if fr is not None and type(pc) is int and id(fr) not in exact:
                    exact[id(fr)] = pc
                seen_tb = True
            elif co.co_filename.startswith("<tb fast code"):
                fr = f.f_locals.get("frame")
                if fr is not None and id(fr) not in exact:
                    pcs = getattr(getattr(fr.code, "fast", None), "pcs", None)
                    if pcs and 0 < f.f_lineno <= len(pcs):
                        exact[id(fr)] = pcs[f.f_lineno - 1] + 1
                seen_tb = True
            elif not seen_tb:
                name = _library_of(co.co_filename)
                if name is not None:
                    fn = co.co_name.lstrip("_")
                    native = f"import {name} (library)" if fn == "<module>" else f"{name}.{fn} (library)"
            f = f.f_back
        return exact, native

    def _label(self, fr) -> str:
        func = getattr(fr, "func", None)
        name = getattr(func, "name", None) if func is not None else getattr(fr, "fn", None)
        if name is None:
            file = self._file_of(fr)
            if file is None or (self.script_path and os.path.abspath(file) == self.script_path):
                return "<main>"
            return f"<{os.path.basename(file)}>"
        owner = getattr(func, "owner", None)
        if owner is not None and getattr(owner, "name", None):
            name = f"{owner.name}.{name}"
        if name not in self.where:
            tmpl = getattr(func, "tmpl", None)
            self.where[name] = (self._file_of(fr), getattr(tmpl, "line", 0) if tmpl is not None else 0)
        return name

    def _file_of(self, fr):
        code = getattr(fr, "code", None)
        file = getattr(code, "file", None) if code is not None else getattr(fr, "file", None)
        return file or self.script_path

    @staticmethod
    def _line_of(fr, pc) -> int:
        code = getattr(fr, "code", None)
        if code is None or pc is None or code.traced:
            return getattr(fr, "line", 0) or 0
        lines = code.stmt_lines
        if not lines:
            return 0
        return lines[pc - 1 if pc > 0 else 0] if pc <= len(lines) else lines[-1]

    # -- results ------------------------------------------------------------------

    def functions(self) -> list[tuple[str, float, float]]:
        """(label, self seconds, total seconds), busiest (by total, then self) first."""
        own: dict[str, float] = {}
        total: dict[str, float] = {}
        for stack, weight in self.stacks.items():
            own[stack[-1]] = own.get(stack[-1], 0.0) + weight
            for label in set(stack):
                total[label] = total.get(label, 0.0) + weight
        rows = [(label, own.get(label, 0.0), t) for label, t in total.items()]
        rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
        return rows

    def busiest_lines(self) -> list[tuple[tuple, float]]:
        return sorted(self.lines.items(), key=lambda kv: (-kv[1], kv[0][0] or "", kv[0][1]))

    def sampled(self) -> float:
        return sum(self.stacks.values())


def _library_of(filename: str):
    """'logs' for .../tblib/logs.py (the library a Python frame belongs to), else None."""
    folder, base = os.path.split(filename)
    if os.path.basename(folder) != "tblib" or not base.endswith(".py") or base.startswith("_"):
        return None
    return base[:-3]


# ---------------------------------------------------------------------------
# Memory: the largest values still held
# ---------------------------------------------------------------------------

def largest_values(interp, limit: int = 10) -> list[tuple[str, int, str]]:
    """(name, bytes, description) of the biggest top-level values of the program
    and of the TB files it imported, biggest first."""
    from tbcore.runtime import Instance

    roots = []
    for name, value in getattr(interp.globals, "vars", {}).items():
        roots.append((name, value))
    for module in list(getattr(interp, "modules", {}).values()):
        fields = getattr(module, "fields", None)
        if getattr(module, "path", None) and isinstance(fields, dict):
            for name, value in fields.items():
                roots.append((f"{module.name}.{name}", value))
    rows = []
    for name, value in roots:
        if type(value) not in (list, dict, str, bytes, Instance):
            continue  # functions, classes, modules, numbers: not where memory goes
        rows.append((name, deep_size(value), describe(value)))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:limit]


def deep_size(value, budget: int = 2_000_000) -> int:
    """Bytes used by value and everything inside it (each object counted once)."""
    from tbcore.runtime import Instance

    seen: set[int] = set()
    todo = [value]
    total = 0
    while todo and budget > 0:
        v = todo.pop()
        if id(v) in seen:
            continue
        seen.add(id(v))
        budget -= 1
        total += sys.getsizeof(v)
        t = type(v)
        if t is list or t is tuple:
            todo.extend(v)
        elif t is dict:
            todo.extend(v.keys())
            todo.extend(v.values())
        elif t is Instance:
            todo.append(v.fields)
    return total


def describe(value) -> str:
    from tbcore.runtime import Instance

    t = type(value)
    if t is list:
        return f"list of {len(value):,} item{'' if len(value) == 1 else 's'}"
    if t is dict:
        return f"map of {len(value):,} key{'' if len(value) == 1 else 's'}"
    if t is str:
        return f"text of {len(value):,} character{'' if len(value) == 1 else 's'}"
    if t is bytes:
        return f"bytes ({len(value):,})"
    if t is Instance:
        return f"{value.cls.name} object"
    return t.__name__


def human_bytes(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"  # pragma: no cover


# ---------------------------------------------------------------------------
# Running a file and reporting
# ---------------------------------------------------------------------------

def profile_file(path: str, args: list[str] | None = None, out=None):
    """Run a TB file under the profiler. Returns (profiler, interp, exit code)."""
    import tb

    out = out or sys.stdout
    path = os.path.abspath(path)
    with open(path, encoding="utf-8-sig") as f:
        source = f.read()
    interp = tb.Interpreter()
    prof = Profiler(interp, path)
    code = 0
    prof.start()
    try:
        tb.run(source, interpreter=interp, path=path, args=list(args or []))
    except tb.TBError as err:
        prof.stop()
        print(str(err), file=sys.stderr)
        trace = err.trace_text() if hasattr(err, "trace_text") else ""
        if trace:
            print(trace, file=sys.stderr)
        code = 1
    except tb.TBExit as stop:
        code = stop.code if isinstance(stop.code, int) else 0
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        code = 130
    finally:
        if not prof._stop.is_set():
            prof.stop()
    return prof, interp, code


def _shown(file, base_dir) -> str:
    if not file:
        return "?"
    try:
        rel = os.path.relpath(file, base_dir)
    except ValueError:  # another drive on Windows
        rel = file
    if rel.startswith(".."):
        rel = file
    return rel.replace(os.sep, "/")


def _source_line(cache: dict, file, line: int) -> str:
    if not file:
        return ""
    if file not in cache:
        try:
            with open(file, encoding="utf-8-sig") as f:
                cache[file] = f.read().splitlines()
        except OSError:
            cache[file] = []
    lines = cache[file]
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def report_text(prof: Profiler, interp, base_dir: str) -> str:
    total = prof.sampled() or 1e-9
    shown = os.path.basename(prof.script_path or "?")
    out = [f"Profile of {shown}: ran {prof.elapsed:.2f} s, {prof.samples:,} samples (about every "
           f"{prof.interval * 1000:.0f} ms)."]
    if not prof.samples:
        out.append("The program finished before the first sample: there is nothing to show.")
    else:
        out += ["", "Functions (self: its own code and the library calls it makes; total: with the TB",
                "functions it calls too)",
                f"  {'self':>12}  {'total':>12}  function"]
        for label, own, tot in prof.functions()[:TOP]:
            where = prof.where.get(label)
            at = f"  ({_shown(where[0], base_dir)}:{where[1]})" if where and where[1] else ""
            out.append(f"  {_pct(own, total):>12}  {_pct(tot, total):>12}  {label}{at}")
        out += ["", "Busiest lines", f"  {'time':>12}  line"]
        cache: dict = {}
        for (file, line), t in prof.busiest_lines()[:TOP]:
            text = _source_line(cache, file, line)
            text = text if len(text) <= 60 else text[:57] + "..."
            out.append(f"  {_pct(t, total):>12}  {_shown(file, base_dir)}:{line}  {text}")
    values = largest_values(interp)
    out += ["", "Largest values held at the end"]
    if values:
        for name, size, what in values:
            out.append(f"  {human_bytes(size):>12}  {name}  ({what})")
    else:
        out.append("  (none: the program keeps no lists, maps, text or objects at its top level)")
    return "\n".join(out) + "\n"


def _pct(seconds: float, total: float) -> str:
    return f"{seconds * 1000:.0f} ms {100 * seconds / total:3.0f}%"


# ---------------------------------------------------------------------------
# The flame graph page
# ---------------------------------------------------------------------------

def flame_tree(stacks: dict) -> dict:
    """{name, value, children: {label: node}} from (stack -> seconds)."""
    root = {"name": "all", "value": 0.0, "children": {}}
    for stack, weight in stacks.items():
        node = root
        node["value"] += weight
        for label in stack:
            child = node["children"].get(label)
            if child is None:
                child = node["children"][label] = {"name": label, "value": 0.0, "children": {}}
            child["value"] += weight
            node = child
    return root


def report_html(prof: Profiler, interp, base_dir: str) -> str:
    esc = html.escape
    shown = os.path.basename(prof.script_path or "?")
    total = prof.sampled() or 1e-9
    root = flame_tree(prof.stacks)
    bars = []
    depth_max = [0]

    def place(node, left, depth):
        width = 100.0 * node["value"] / total
        if width < 0.05:
            return
        depth_max[0] = max(depth_max[0], depth)
        name = node["name"]
        library = name.endswith("(library)")
        hue = 205 if library else 10 + zlib.crc32(name.encode()) % 40
        tip = f"{name}: {node['value'] * 1000:.0f} ms, {100 * node['value'] / total:.1f}%"
        bars.append(
            f'<div class="bar" style="left:{left:.3f}%;width:{width:.3f}%;top:{depth * 22}px;'
            f'--h:{hue}" title="{esc(tip)}"><span>{esc(name)}</span></div>'
        )
        x = left
        for child in sorted(node["children"].values(), key=lambda c: -c["value"]):
            place(child, x, depth + 1)
            x += 100.0 * child["value"] / total

    place(root, 0.0, 0)
    height = (depth_max[0] + 1) * 22
    fn_rows = []
    for label, own, tot in prof.functions()[:50]:
        where = prof.where.get(label)
        at = f"{_shown(where[0], base_dir)}:{where[1]}" if where and where[1] else ""
        fn_rows.append(f"<tr><td>{esc(label)}</td><td>{esc(at)}</td><td class=n>{own * 1000:.0f} ms</td>"
                       f"<td class=n>{100 * own / total:.1f}%</td><td class=n>{tot * 1000:.0f} ms</td>"
                       f"<td class=n>{100 * tot / total:.1f}%</td></tr>")
    cache: dict = {}
    line_rows = []
    for (file, line), t in prof.busiest_lines()[:30]:
        line_rows.append(f"<tr><td>{esc(_shown(file, base_dir))}:{line}</td>"
                         f"<td><code>{esc(_source_line(cache, file, line))}</code></td>"
                         f"<td class=n>{t * 1000:.0f} ms</td><td class=n>{100 * t / total:.1f}%</td></tr>")
    mem_rows = [f"<tr><td>{esc(name)}</td><td>{esc(what)}</td><td class=n>{esc(human_bytes(size))}</td></tr>"
                for name, size, what in largest_values(interp)]
    empty = "<tr><td colspan=6>(nothing)</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TB profile: {esc(shown)}</title>
<style>
:root {{ --bg: #fff; --fg: #1d1d1f; --muted: #666; --line: #ddd; --bar-l: 78%; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #17171a; --fg: #ececf0; --muted: #a0a0aa; --line: #34343a; --bar-l: 42%; }}
}}
body {{ margin: 0 auto; max-width: 1200px; padding: 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
h1 {{ font-size: 1.4em; margin: 0.2em 0; }}
h2 {{ font-size: 1.1em; margin-top: 1.6em; }}
p {{ color: var(--muted); max-width: 70ch; }}
.flame {{ position: relative; overflow: hidden; border: 1px solid var(--line); }}
.bar {{ position: absolute; height: 21px; box-sizing: border-box; border: 1px solid var(--bg);
  background: hsl(var(--h) 85% var(--bar-l)); color: var(--fg); overflow: hidden; white-space: nowrap;
  font-size: 12px; line-height: 19px; padding: 0 4px; cursor: default; }}
.bar:hover {{ filter: brightness(1.15); outline: 1px solid var(--fg); z-index: 1; }}
table {{ border-collapse: collapse; width: 100%; }}
.wrap {{ overflow-x: auto; }}
th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
td.n, th.n {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
code {{ font: 12px/1.4 ui-monospace, Menlo, Consolas, monospace; }}
</style>
</head>
<body>
<h1>Profile of {esc(shown)}</h1>
<p>Ran {prof.elapsed:.2f} s; {prof.samples:,} samples, about one every {prof.interval * 1000:.0f} ms.
Made by <code>tb profile</code>.</p>
<h2>Flame graph</h2>
<p>Each bar is a function. Its width is the time spent in it, including the functions it called, which
sit below it. Blue bars are library functions. Point at a bar for its numbers.</p>
<div class="flame" style="height:{height}px">
{chr(10).join(bars)}
</div>
<h2>Functions</h2>
<p>Self: the function's own code and the library calls it makes. Total: with the TB functions it calls.</p>
<div class="wrap"><table>
<tr><th>function</th><th>defined at</th><th class=n>self</th><th class=n></th><th class=n>total</th><th class=n></th></tr>
{chr(10).join(fn_rows) or empty}
</table></div>
<h2>Busiest lines</h2>
<div class="wrap"><table>
<tr><th>line</th><th>code</th><th class=n>time</th><th class=n></th></tr>
{chr(10).join(line_rows) or empty}
</table></div>
<h2>Largest values held at the end</h2>
<p>The program's top-level variables, and those of the files it imports, measured with everything inside them.</p>
<div class="wrap"><table>
<tr><th>name</th><th>what</th><th class=n>size</th></tr>
{chr(10).join(mem_rows) or empty}
</table></div>
</body>
</html>
"""


USAGE = "tb profile [--html REPORT.html] FILE.tb [args...] - where a program spends its time and memory"


def main(args: list[str] | None = None) -> int:
    args = list(args or [])
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        print(__doc__.split("\n\n", 1)[1].split("How it works")[0].rstrip())
        return 0 if args else 2
    html_path = None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            rest += args[i + 1:]
            break
        if a == "--html" and not rest:
            if i + 1 >= len(args):
                print("Oops! --html needs a file name, like: tb profile app.tb --html profile.html",
                      file=sys.stderr)
                return 2
            html_path = args[i + 1]
            i += 2
            continue
        rest.append(a)
        i += 1
    if not rest:
        print(USAGE, file=sys.stderr)
        return 2
    path, program_args = rest[0], rest[1:]
    if not os.path.isfile(path):
        print(f"Oops! I couldn't find the file '{path}'. Is the name spelled right?", file=sys.stderr)
        return 1
    try:
        prof, interp, code = profile_file(path, program_args)
    except OSError as err:
        print(f"Oops! I couldn't open '{path}': {err.strerror or err}.", file=sys.stderr)
        return 1
    base_dir = os.path.dirname(os.path.abspath(path))  # paths in the report are shown from here
    sys.stdout.flush()
    print("\n" + "-" * 70)
    print(report_text(prof, interp, base_dir), end="")
    if html_path:
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(report_html(prof, interp, base_dir))
        except OSError as err:
            print(f"Oops! I couldn't write '{html_path}': {err.strerror or err}.", file=sys.stderr)
            return 1
        print(f"\nFlame graph: {html_path}")
    else:
        print(f"\nFor a flame graph: tb profile --html profile.html {path}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
