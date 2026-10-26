# TB 2.0 design

TB 1.0 was a teaching language. TB 2.0 makes it a small but real
general-purpose language: you can build desktop GUI apps, security (SOC)
tools, scripts and command-line apps with it, using a standard library that
ships inside `tb`. Every 1.0 program must keep working unchanged
(the first demo program, `examples/*.tb`, the docs snippets).

This file is the contract between the agents building 2.0. If you need to
change a contract, say so in your report; never silently diverge.

## Ground rules

- Python 3.10+ standard library only. No third-party runtime dependencies.
- Work under `code/` of the portfolio repo.
- Run things with `uv run --python /opt/homebrew/bin/python3.12 ...` from
  `code/` (a shell hook blocks bare `python3`). Tk is NOT available in that
  Python; for GUI tests use `/opt/homebrew/bin/python3.13` (has Tk 9).
- Never put the author's name anywhere (code, docs, site). The project is
  simply "TB", a friendly programming language; it credits no person.
- Errors keep the 1.0 voice: `Oops! On line N: <plain English>`.
- Do not git commit; the lead commits.

## Layout

```
code/
  tb.py               core: lexer, parser, interpreter, module loader, CLI (single file, importable)
  tblib/              the standard library
    __init__.py       empty
    _api.py           helpers for native modules (see "Native module API")
    <name>.py         native (Python) modules, one per library
    <name>.tb         libraries written in TB itself
  tests/              pytest (one test file per library: tests/lib/test_<name>.py)
  examples/           1.0 examples (unchanged)
  apps/               2.0 example applications (GUI apps, SOC tools), each a folder:
    <app>/main.tb, <app>/README.md, optional sample data
```

## Language additions (core-agent)

All additive; nothing in 1.0 changes meaning.

1. **Maps**: `{"name": "Alex", "age": 21}`. Keys are text or numbers.
   Empty map `{}`. Trailing comma allowed. Insertion-ordered (Python dict).
   `m["k"]`, `m["k"] = v`. Missing key read is an error
   ("There's no key "k" in this map. It has: a, b.") — use `.get(k, default)`.
   A `{` at the start of an expression is a map; blocks only follow
   `if/else/while/for/fn/try/catch`, so there is no ambiguity.
   `to_text` shows maps as `{"name": "Alex", "age": 21}`. `==` compares deeply.
   `for k in map` iterates keys. `len(map)` is the key count.
2. **Dot access**: `x.name`.
   - On a map: `m.name` reads key `"name"`; `m.name = v` sets it.
     If the value is a function, `m.greet()` just calls it (no implicit self).
   - On a module: `fs.read(...)`, `gui.window(...)`.
   - On built-in values, methods (implemented in the core):
     - text: `upper() lower() trim() split(sep?) lines() contains(s)
       starts_with(s) ends_with(s) replace(old, new) find(s) (index or -1)
       slice(start, end?) repeat(n) is_number() to_number() chars() len()`
     - list: `push(v) pop() insert(i, v) remove_at(i) contains(v) index_of(v)
       join(sep?) sort() sort_by(fn) reverse() map(fn) filter(fn) reduce(fn, start)
       each(fn) slice(start, end?) first() last() is_empty() copy() len() sum() min() max() unique()`
       (`sort/reverse` return a new list; `push/pop/insert/remove_at` change it in place)
     - map: `keys() values() items() (list of [k, v]) has(k) get(k, default?)
       remove(k) set(k, v) merge(other) (new map) copy() len() is_empty()`
     - number: `round(digits?) floor() ceil() abs() to_text()`
   - Unknown method → friendly error listing the available ones.
3. **Interpolated text**: `f"Hello {name}, you have {len(xs)} items"`.
   Any expression inside `{}`. `{{` and `}}` give literal braces. Plain
   `"..."` strings never interpolate (so regexes and JSON are safe).
4. **Modules**:
   - `import fs` binds the standard-library module `fs`.
   - `import fs as files` binds it as `files`.
   - `import "helpers.tb"` / `import "lib/helpers.tb" as h`: a TB file, path
     relative to the importing file (or cwd in the REPL/playground). Bound
     name defaults to the file stem. A TB module's top-level `let`/`fn` names
     become its fields. Modules are cached (imported once). Import cycles →
     friendly error.
   - Resolution of `import name`: `tblib/name.py` (native) else
     `tblib/name.tb`. Unknown → "There's no library called 'x'. Libraries: ...".
   - `import` is a statement, allowed anywhere at top level of a file
     (or inside blocks; same behaviour).
5. **Errors as values**:
   ```
   try {
     let data = json.parse(text)
   } catch err {
     say(f"Bad file: {err.message} (line {err.line})")
   }
   throw "Something went wrong"          # text
   throw {"message": "custom", "code": 7}  # map with message
   ```
   `err` is a map: `{"message": ..., "line": ..., "kind": "error"}` plus any
   fields from a thrown map. Every TBError (built-in or from a library) is
   catchable. `catch` without a name is allowed: `catch { ... }`.
   Uncaught throws print like other errors: `Oops! On line N: <message>`.
   Python exceptions inside native libraries must be turned into TBError
   with a friendly message by the library (`_api.fail(...)`), never leak.
6. **Loops**: `break` and `continue` inside `while`/`for` (error outside).
7. **Compound assignment**: `+=`, `-=`, `*=`, `/=` on names, indexes and dot fields.
8. **Membership**: `x in list`, `"sub" in text`, `key in map` as an
   expression (precedence of comparisons). `not in` too.
9. **Built-ins added** (global, no import): `type(x)` → one of "number",
   "text", "bool", "nil", "list", "map", "function", "module" (error messages
   may keep their 1.0 wording). `keys(map)`, `int(x)` (whole
   number, truncates), `round(x, digits?)`, `min(...)`, `max(...)`,
   `sum(list)`, `sorted(list)`, `exit(code?)`, `print` stays unknown (hint to say).
   The 8 originals are unchanged.
10. **Numbers**: allow `1e3` literals? No. Keep 1.0 number rules.

## Native module API (core-agent writes `tblib/_api.py`)

A native module `tblib/foo.py` exposes:

```python
from tblib._api import Module, fail, check_type

def load(api) -> Module:
    m = Module("foo", "One-line description shown by `tb doc foo`.")
    @m.fn("greet", 1, 1, doc="greet(name) -> text")   # min/max args
    def greet(line, name):
        check_type(line, name, "text", "greet", "name")
        return f"Hi {name}"
    m.value("VERSION", "1.0")
    return m
```

- Values crossing the boundary: nil=None, bool, int/float, text=str,
  list=list, map=dict, functions = callables TB can call.
- `api.call(fn, args, line)` calls a TB function (callbacks: gui events,
  `list.map`). `api.interpreter` is the running Interpreter;
  `api.output(text)` prints through TB's output (so tests/playground capture it);
  `api.input(prompt)`; `api.base_dir` = directory of the running script.
- `fail(line, message)` raises a catchable TBError.
- `api.in_browser` is True under Pyodide; modules that cannot work there
  (gui, net, http, intel, os.run) raise `fail(line, "The gui library needs the
  TB app on your computer; it can't open windows inside the browser playground.")`.
- Every module and function has a doc string; `tb doc <module>` prints them,
  and `scripts/gen_lib_docs.py` (site-agent) renders them into the website.

## CLI (core-agent)

- `tb FILE.tb [args...]` — args available as `os.args`.
- `tb` REPL, `tb --version` → `TB 2.0.0`, `tb --help`.
- `tb doc` lists libraries; `tb doc fs` shows a library's functions.
- `tb test [DIR]` runs every `*_test.tb` file; inside, `import test` gives
  `test.equal(a, b, msg?)`, `test.true(x, msg?)`, `test.raises(fn)`;
  prints a summary, exit 1 on failure.
- `__version__ = "2.0.0"`.

## Standard library (who builds what)

core-agent (with tb.py):
- `math`: pi, e, sqrt, pow, floor, ceil, abs, round, log, sin, cos, min, max, clamp
- `text`: helpers not covered by methods: pad_left, pad_right, center, wrap, is_digit, format_number(n, decimals), random_id
- `time`: now() (map), timestamp(), sleep(seconds), format(ts, pattern), parse(text, pattern), today(), since(ts) (seconds)
- `fs`: read, write, append, lines, exists, is_dir, list(dir), walk(dir), mkdir, remove (files only), copy, size, modified, home(), cwd(), join(...), name(path), ext(path), parent(path)
- `json`: parse(text), stringify(value, pretty?), read(path), write(path, value)
- `csv`: read(path) → list of maps (header row), parse(text), write(path, rows), stringify(rows)
- `re`: test(pattern, text), match(pattern, text) → map of groups or nil, find_all(pattern, text), replace(pattern, text, with), split(pattern, text). Friendly error on bad patterns.
- `random`: int(a, b), number(), choice(list), shuffle(list), seed(n)
- `os`: args, env(name, default?), platform, user, run(command) → {"code","out","err"} (not in browser), exit(code)
- `test`: as above

soc-agent:
- `hash`: md5/sha1/sha256/sha512(text), file_md5/file_sha1/file_sha256(path), hmac_sha256(key, text), compare(a, b) (constant-time)
- `encode`: base64(text), unbase64(text), hex(text), unhex(text), url(text), unurl(text), rot13(text), entropy(text)
- `ioc`: extract(text) → {"ips": [...], "domains": [...], "urls": [...], "emails": [...], "md5": [...], "sha1": [...], "sha256": [...], "cves": [...]}, defang(text), refang(text), is_ip/is_domain/is_hash(x), kind(x)
- `logs`: parse_line(line, format), read(path, format) → list of maps, formats: "syslog", "auth" (sshd/sudo events with fields user, ip, action, success), "apache"/"nginx" (combined), "json" (JSON lines), "csv", "windows" (Windows Event Log CSV/JSON export: EventID, TimeCreated, Account, IpAddress...), "auto" (sniff). Each record has at least "time" (timestamp or nil), "message", "raw".
- `detect`: rules engine. `detect.rule({"name", "level", "where": {field: pattern-or-value}, "count": n, "within": seconds, "by": field})`, `detect.run(rules, records)` → list of alerts maps; built-in rule packs: `detect.brute_force(records, attempts?, within?)`, `detect.port_scan(...)` on connection logs, `detect.rare(records, field)`, `detect.sigma(path_or_text)` (load a basic subset of Sigma YAML: detection selection/condition with simple AND/OR, field|contains/startswith/endswith modifiers) — implement a small YAML subset parser yourself, stdlib only.
- `report`: table(rows, columns?) → aligned text, markdown(rows), csv(rows), html(title, rows) → full HTML page text, timeline(records) → text, save(path, text)

net-agent:
- `net`: is_ip(x), is_private(ip), in_cidr(ip, cidr), cidr_hosts(cidr) (cap 65536), resolve(host) → list of IPs, reverse(ip), tcp_check(host, port, timeout?) → true/false, check_ports(host, ports, timeout?) → map port→bool (cap 1024 ports, defensive use; doc that you must only check hosts you own/are authorised for), banner(host, port, timeout?), my_ip() (local), whois? no.
- `http`: get(url, options?) / post(url, body, options?) / request(method, url, options) → {"status","headers","text","json"} ; options: headers, timeout, params, json; download(url, path). Friendly errors for offline/DNS/timeouts/HTTP errors (no raise on 4xx/5xx; status returned).
- `intel`: virustotal_file(hash, key?), virustotal_ip(ip, key?), virustotal_domain/url, abuseipdb(ip, key?), urlhaus_url(url) — keys default to env vars `VT_API_KEY`, `ABUSEIPDB_API_KEY`; return small normalised maps {"verdict","score","source","raw"}; friendly error when no key.

gui-agent:
- `gui` (tkinter): `gui.window(title, {"width", "height", "theme": "light"/"dark"})` returns a window object (map with methods); widgets added in order with simple layout helpers `row()`/`column()` containers: `label(text)`, `button(text, on_click)`, `input({"placeholder","on_enter"})` (`.value`/`.set()`), `textarea()`, `checkbox(text)`, `select(options)`, `table(columns)` (`.add_row(list)`, `.clear()`, `.selected()`), `list_box(items)`, `image(path)` (PNG/GIF), `progress()`, `separator()`, `menu({"File": {"Open": fn, ...}})`, `status(text)`; dialogs: `gui.alert(msg)`, `gui.confirm(msg)`, `gui.ask(msg)`, `gui.open_file(filters?)`, `gui.save_file()`, `gui.pick_folder()`; timers `win.every(ms, fn)`, `win.after(ms, fn)`; `win.run()` (blocks until closed), `win.close()`. Nice default styling (ttk, padding, a consistent font, light/dark).
  Must be testable without a display: keep tkinter use behind a thin layer with a fake backend for tests; real smoke test with python3.13 locally.
- Example apps in `apps/` that use gui: a to-do app, a "hash checker" (pick file, show SHA-256, compare), a "log triage" desktop app (open log, table of alerts using logs+detect), a mood journal / care app.

## Browser (site-agent later)

- `tb-pyodide.js` must load `tb.py` plus every `tblib/*.py|*.tb` into the
  Pyodide FS (build_site copies `tblib/` and writes `tblib/index.json`).
- Playground can run all non-OS libraries. GUI apps show screenshots on the site.

## Version

`2.0.0`; release tag `tb-v2.0.0`. Site shows 2.0. (2.0.1: neutral sample programs and docs, no language changes; the first demo program is now `welcome.tb`.)
