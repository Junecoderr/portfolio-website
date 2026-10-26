# TB 3.0 design

TB 3.0 brings the language to parity with mainstream languages in what you
can express and build: classes, pattern matching, destructuring, first-class
functions with defaults and varargs, bytes, real number formatting, typed
errors, generators, a bytecode VM, concurrency, a package manager, editor
tooling (formatter, linter, language server, VS Code extension, debugger),
gradual types, and a much deeper security library.

TB stays implemented in Python (3.10+, stdlib only at runtime). A native
rewrite is out of scope for 3.0.

This file is the contract between the agents. `DESIGN-2.0.md` still
describes the 2.x surface (modules, the native-module `_api`, the stdlib);
3.0 is additive to it except where "Breaking" is marked below. If you must
deviate, say so in your report; never silently diverge.

## Ground rules

- Work under `code/` of the portfolio repo (the folder containing this file).
- Run: `uv run --python /opt/homebrew/bin/python3.12 python -m pytest -q`
  from `code/` (a shell hook blocks bare `python3`). Real Tk is only in
  `/opt/homebrew/bin/python3.13`.
- Version everywhere: `3.0.0`. Release tag `tb-v3.0.0`.
- No romantic content and no author credit anywhere (see 2.0.1 notes).
  The sample name used in greetings, examples and app data is **Tanisha**
  (decided for 3.0; replaces Alex/Sam/Priya). The site lives at
  `code.tanisha.ceo`.
- Friendly errors stay: `Oops! On line N: <plain English>` with hints.
- Every public function/class/method documented; `tb doc` and the website
  read the docs.
- Don't git commit; the lead commits.
- Keep `tb.py` importable as `import tb` with the existing public API
  (`run`, `Interpreter`, `TBError`, `main`, `parse`, `tokenize`,
  `list_libraries`, `describe_library`, `describe_builtins`, `__version__`).
  `tb.py` may now import sibling files: the implementation can be split
  into `tb.py` (entry + public API) plus a package `tbcore/` (lexer, parser,
  ast, compiler, vm, runtime). PyInstaller/Pyodide loaders must then include
  `tbcore/` like `tblib/` (packaging-agent + site-agent handle that; core
  documents the file list in `tbcore/__init__.py`).

## Layout (new pieces)

```
code/
  tb.py                 entry point, public API, CLI dispatch
  tbcore/               (core-agent) lexer.py parser.py ast.py runtime.py interp.py
                        compiler.py vm.py (vm-agent) types.py (types-agent) ...
  tblib/                stdlib (existing + new libraries below)
  tbtools/              (tooling-agent) fmt.py check.py lsp.py debug.py pkg.py
                        docgen.py build.py  — each has main(args) -> int
  editors/vscode/       (tooling-agent) VS Code extension
  spec/                 (tooling-agent) SPEC.md + conformance *_test.tb files
  bench/                (vm-agent) benchmark programs + bench.py
  apps/                 existing + new showcase apps
  tests/                pytest; tests/lang3/ for new syntax, tests/lib/ per library
```

CLI dispatch rule (core-agent): `tb <sub> ...` where `<sub>` is not a file
and not a built-in command (`doc`, `test`, `run`, `--version`, `--help`)
tries `importlib.import_module(f"tbtools.{sub}").main(args)`; unknown →
friendly error listing available commands. So `tb fmt`, `tb check`,
`tb lsp`, `tb debug`, `tb add`, `tb remove`, `tb packages`, `tb update`,
`tb build`, `tb init` live in `tbtools/` (`add/remove/packages/update/init`
all map to `tbtools/pkg.py` via small shim modules or a dispatch table in
`tbtools/__init__.py`).

## Part A — Language (core-agent)

### A1. Reserved words (Breaking)
New keywords: `class self super static extends match const export from
yield async await is enum finally then`. Using them as names is an error
with a hint ("'match' is a keyword in TB 3.0; call it 'found' instead").
Dot access (`re.match`, `obj.match`) is unaffected. core-agent updates any
`examples/`, `apps/`, `scripts/lib_examples/`, site docs snippets that break
(grep first).

### A2. Numbers (Breaking, display-compatible)
- `int` and `float` are distinct at runtime. Literals: `1_000_000`, `1e3`,
  `2.5e-3`, `0x1F`, `0b1010`, `0o17`.
- `/` always gives float; `//` integer division (floor); `%` floor-mod;
  `**` power. Display keeps `tidy`: `6 / 3` prints `2`, `7 / 2` prints
  `3.5`, so 2.x programs print the same.
- Bitwise on ints: `& | ^ ~ << >>` (`~` prefix). Compound: `**= //= %= &=
  |= ^= <<= >>=` plus existing `+= -= *= /=`.
- `type(x)` stays `"number"`; new `x.is_int()`, `x.is_float()`,
  `int(x)`, `float(x)`, `x.to_int()`.
- Chained comparison: `0 < x <= 10`.
- Big ints unlimited; keep the "too big" guard at 1e330-equivalent only for
  float overflow and for `**` results beyond ~10k bits.

### A3. Text
- Escapes: `\n \t \r \0 \\ \" \' \xNN \u{HEX}`; raw text `r"..."` (no
  escapes), multi-line `"""..."""` (and `r"""..."""`, `f"""..."""`).
- f-string format specs: `{x:.2f}`, `{n:8}`, `{n:>8}`, `{n:<8}`, `{n:^8}`,
  `{n:08.3f}`, `{n:,}`, `{n:x}`/`{n:X}`/`{n:b}`/`{n:o}`, `{p:.1%}`,
  `{s:.5}` (truncate), `{x!r}` (as typed). Also `text.format(spec)` and the
  free function `format(value, spec)`. Specs follow Python's mini-language
  subset above; unknown spec → friendly error.
- `text[a:b]`, `text[i]`, negative indexes; `text.bytes()` → bytes.

### A4. Bytes
- Literal `b"..."` (ASCII + `\xNN` escapes). Type name `"bytes"`.
- Methods: `len() hex() slice(a, b?) to_text(encoding="utf-8") at(i)
  starts_with(b) find(b) split(b) join(list) reverse() to_list() copy()`;
  `+` concatenates; `==`; indexing `data[i]` → int; `for byte in data`.
- Globals: `bytes(list_of_ints | text | hex_text?)` → use library instead:
  new stdlib `binary`: `binary.from_hex(text)`, `binary.from_list(ints)`,
  `binary.pack(fmt, ...values)`, `binary.unpack(fmt, data, offset=0)`
  (struct format subset `< > ! B H I Q b h i q f d s x`), `binary.read_u16le`
  etc. helpers, `binary.hexdump(data, width=16) -> text`, `binary.entropy`.
- `fs.read_bytes(path)`, `fs.write_bytes(path, data)`, `fs.open(path,
  mode)` → stream object with `read(n?) read_line() write(x) seek(pos)
  tell() close()` and `with`-less usage (`defer`? no: provide `fs.with_file(
  path, mode, fn(f))` helper too).
- `hash.sha256(bytes)` and friends accept bytes as well as text.

### A5. Destructuring and spread
- `let [a, b, ...rest] = xs`, `let {name, ip: address, port = 22} = rec`,
  nested; in `for [k, v] in m.items() {}`; in fn params `fn f([a, b], {x})`.
- `return a, b` returns a list; `let a, b = f()`.
- Spread: `f(...xs)`, `[...a, ...b]`, `{...m, "k": 1}`.
- Swap: `a, b = b, a`.

### A6. Functions
- Defaults `fn f(a, b = 2)`, keyword args at call `f(1, b = 3)` (any
  param by name), varargs `fn f(a, ...rest)`, keyword rest `fn f(...rest,
  ...**opts)`? No — keep: positional varargs only; a trailing map param can
  receive named args when declared `fn f(a, **opts)`.
- Lambdas: `x => x * 2`, `(a, b) => a + b`, `() => 1`, block body
  `x => { ... }`. `fn(x) { }` still works.
- Functions have `.name`, `.arity`; `fn.call(list)`; `fn.bind(...)`.
- `const NAME = value` (reassignment is an error).

### A7. Ranges and slices
- `a..b` exclusive, `a..=b` inclusive, `a..b step n`? Use method:
  `(0..10).step(2)`. Range is lazy, iterable, `in`, `len()`, `to_list()`.
  `range()` builtin stays and returns a list (2.x compat).
- Slices on lists/text/bytes: `xs[1:3]`, `xs[:2]`, `xs[-2:]`, `xs[::2]`,
  `xs[::-1]`.

### A8. Conditional expression and match
- `if cond then a else b` as an expression.
- `match`:
  ```
  let label = match level {
    "critical" or "high" => "act now"
    n if n is number and n > 7 => f"score {n}"
    [first, ...rest] => f"starts with {first}"
    {"kind": "alert", "level": lv} => lv
    1..=5 => "low range"
    Alert(a) => a.name          # class pattern: instance of Alert bound to a
    _ => "unknown"
  }
  ```
  Patterns: literal, name (binds), `_`, `or`, guard `if`, list with
  `...rest`, map (keys exact, values are patterns; extra keys allowed unless
  `...` absent? — extra keys allowed), range, class `Name(binding)` /
  `Name {field: pattern}`, `is Type`. Arms are `=> expr` or `=> { block }`
  (value = last expression of the block, else nil). Non-exhaustive match with
  no `_` and no arm matching → friendly error naming the value. Match is
  also usable as a statement.

### A9. Classes
```
class Animal {
  let sound = "..."                 # field with default (per instance)
  static let count = 0              # class field
  fn init(name) { self.name = name; Animal.count += 1 }
  fn speak() { return f"{self.name} says {self.sound}" }
  static fn make(name) { return Animal(name) }
  fn text() { return f"Animal({self.name})" }        # used by say()/str()
}
class Dog extends Animal {
  let sound = "woof"
  fn init(name) { super.init(name); self.tricks = [] }
  fn speak() { return super.speak() + "!" }
}
let d = Dog("Rex"); say(d.speak()); say(d is Animal, type(d))   # true Dog
```
- `self` is implicit inside methods (not a parameter). `super.method()`.
- Construction by calling the class. Missing `init` → fields with
  defaults only; positional args error.
- Special methods: `text()` (to_text), `equals(other)` (`==`),
  `compare(other)` (<, sort), `hash()`? (maps keyed by instances: use
  identity unless `equals`+`hash` defined — keep: keys must be text/number;
  instances not allowed as keys. Simpler.), `get(i)` / `set(i, v)`
  (indexing), `call(...)` (callable instance), `iter()` (for loops; returns
  an iterator: object with `next()` returning value or raising/returning a
  sentinel — define: `next()` returns `[true, value]` or `[false, nil]`? Too
  clunky. Define: an iterator is any object with `fn next()` that `throw`s
  `StopIteration`? Use: `next()` returns the special value `done` (a new
  global constant `done`) when finished.), `len()`, `contains(x)` (`in`).
- `type(instance)` → class name; `type(Dog)` → `"class"`. `x is Dog`,
  `x is number`, `x is text`, etc. (`is` works on built-in type names too).
- Instances show as `Dog {name: "Rex", tricks: []}` unless `text()`.
- Instances have `.fields()` → map copy; dot access to missing field →
  friendly error with "Did you mean".
- `enum Level { low, medium, high }` → `Level.low` values that compare by
  identity, show as `Level.low`, have `.name` and `.index`; `Level.values()`;
  usable in `match`.
- Modules may `export` names: `export fn ...`, `export let ...`, `export class
  ...`. If a module uses `export` anywhere, only exported names are public;
  otherwise all top-level names are public (2.x behaviour).
- `from "helpers.tb" import a, b as c`, `from fs import read, write`.
- Private-by-convention `_name` (no enforcement); `tb check` warns on
  external use.

### A10. Errors (typed)
- Built-in class `Error` with fields `message`, `line`, `file`, `kind`
  (class name in snake case; base = "error"), plus any extra fields passed
  as a map: `throw Error("msg", {"code": 7})` or `throw Error("msg")`;
  `throw "text"` still works (becomes `Error`); `throw {"message":...}`
  still works (fields copied).
- Standard subclasses used by the stdlib (core defines; libraries must
  raise them via `_api.fail(line, msg, cls=FileError)` or new helpers
  `fail_file/fail_parse/fail_network/fail_type/fail_value`): `TypeError`,
  `ValueError`, `FileError`, `ParseError`, `NetworkError`, `PermissionError`,
  `NotFoundError`, `TimeoutError`, `StopIteration`. Users subclass `Error`.
- `try { } catch err: FileError { } catch err: ParseError or ValueError { }
  catch err { } finally { }`. `err.message`, `err.line`, `err.kind`,
  `err.stack` (list of `{fn, line, file}`), `err is FileError`.
- Uncaught: `Oops! On line N: <message>` plus a short "in f() ← g()" trace
  line when the stack has frames.

### A11. Generators and iteration
- `fn f() { yield 1; yield 2 }` → calling returns a generator (iterator).
  `for x in f()`, `f().next()`, `.to_list()`, `.map(fn)`, `.filter(fn)`,
  `.take(n)`, `.zip(other)`, `.enumerate()`. `yield from other`? Use `for x in
  other { yield x }`; skip `yield from`.
- Lazy helpers: `list.iter()`, `map.iter()`, `(0..n)`, `logs.each` can return
  a generator-like.
- Implementation in the tree-walker may use one Python thread per live
  generator with handoff (simple and correct); the VM (Part B) replaces it
  with suspended frames. Generators must be garbage-collected cleanly
  (daemon threads, closed on exhaustion/finalisation).
- `for i, x in xs.enumerate()` and `for k, v in m` (maps iterate pairs when
  two targets are given; one target → keys as in 2.x).

### A12. Misc
- `defer { ... }` inside a function: runs when the function returns (file
  closing). Optional; include if cheap.
- `assert cond, "message"` statement → throws `AssertionError`.
- `del m["k"]`? Use `m.remove(k)` (exists). Skip `del`.
- String `*` and list `*` repeat already exist; add `list * n`.
- `say` unchanged; new `print`? No. Keep `say` as the only output builtin;
  the hint "print → say" stays.
- Debugger hook (needed by tooling-agent): `Interpreter.trace: callable |
  None` called as `trace(event, line, file, env)` for `event in ("line",
  "call", "return", "exception")`, plus `Interpreter.frames()` → list of
  `{fn, line, file, locals}` and `Interpreter.eval(source, env)`. The VM
  must preserve this API.
- Comments must survive for the formatter: tokens carry `comments_before:
  list[str]` and `blank_lines_before: int`; the parser stores them on
  statement nodes (`node.comments`, `node.blank_before`) and a trailing
  comment on the same line (`node.trailing_comment`). `tb.parse(source)`
  keeps this data.
- Type annotations are parsed and kept on nodes but ignored at runtime:
  `let x: number = 1`, `fn f(a: text, b: list<number> = []) -> text`,
  class fields `let count: number = 0`, `class Box<T>`? Skip generics
  declarations; allow `list<T>`, `map<K, V>`, `fn(a, b) -> r`, `T | nil`,
  `any` in annotation syntax. Type names: `number int float text bool nil
  list map bytes function class module any` + class names + `Error` family.
- Package resolution for `import name`: 1) `./name.tb` relative to the
  importing file (only when written with quotes, as today), 2) stdlib,
  3) `tb_packages/<name>/` found by walking up from the importing file's
  folder to the nearest folder containing `tb.toml` (the project root);
  package entry is `tb_packages/<name>/lib.tb`, or `tb_packages/<name>/
  <name>.tb`, or `tb_packages/<name>/tb.toml`'s `[package] main = "..."`.
  Native (Python) packages are not supported in 3.0.

### A13. Tests and docs
- `tests/lang3/`: thorough tests per feature, incl. error messages.
- Update `tb doc builtins`, HELP text, `README.md` language section with
  3.0 syntax; a `MIGRATION-3.0.md` listing breaking changes and fixes.
- Update examples/apps/lib_examples that hit breaking changes; keep every
  existing test green (adjust tests only for intended breaking changes).

## Part B — Bytecode VM (vm-agent, after Part A lands)

- `tbcore/compiler.py` compiles the AST to bytecode; `tbcore/vm.py` runs a
  stack machine; identical semantics and error messages to the tree-walker
  (the test suite is the oracle). Generators become suspended frames (no
  threads). Closures via cells. Tail position `return f(x)` need not be
  optimised.
- Preserve `Interpreter.trace`, `frames()`, `eval()`, `run()`, `evaluate()`
  (REPL uses it), `load_library`, and the `_api` boundary (native modules
  receive the same `api`).
- Switch: the VM is the default; `TB_ENGINE=tree` keeps the tree-walker
  for one release; `tb --engine tree file.tb` too. Both must pass the suite
  (CI runs the suite twice: core job + `TB_ENGINE=tree` job; packaging-agent
  updates CI).
- `bench/`: fib(27), loop 5M, string building, map churn, method calls,
  `bench.py` prints a table tree vs vm vs python baseline. Target ≥3× over
  the tree-walker on the mean; report real numbers.

## Part C — Concurrency (concurrency-agent, after Part A lands)

- Library `parallel`: `parallel.run(fn, ...args) -> Task` (`.wait(timeout?)
  -> value`, `.done()`, `.cancel()` cooperative), `parallel.map(fn, items,
  workers = 8)`, `parallel.each`, `Queue` class (`put get(timeout?) size
  close`), `Lock` (`.hold(fn)`), `parallel.every(ms, fn) -> Timer`,
  `parallel.after(ms, fn)`, `parallel.sleep(s)`, `parallel.cpu_count()`.
  Threads (Python threading). Interpreter must be thread-safe enough: per-
  thread call depth and frame stacks, no shared mutable interpreter state
  except globals (dict ops are atomic under the GIL); errors in a task are
  captured and re-thrown by `wait()`.
- `async fn f() {}` / `await expr`: `async fn` call returns a Task; `await`
  on a Task waits (also accepts plain values). Implemented on threads; no
  event loop in 3.0. `parallel.all(tasks)`, `parallel.race(tasks)`.
- GUI integration: `win.run_in_background(fn, on_done, on_error?)` and
  `gui.on_main(fn)` so background tasks can update widgets safely (Tk is
  single-threaded: marshal via `after(0, ...)`).
- `http.get` etc. must release the GIL during I/O (they do, via urllib).
- Tests: parallel.map correctness and speedup on sleep-bound work, queue
  producer/consumer, error propagation, cancel, async/await, GUI
  background update on the fake backend.

## Part D — Gradual types and lint (types-agent, after Part A lands)

- `tbtools/check.py` → `tb check [paths]`: parse errors; undefined names;
  unused variables/imports; wrong arity for user functions and stdlib
  functions (from `@m.fn` min/max); unknown methods on known receiver
  types; unknown module members; assignment to `const`; `return` outside
  fn; unreachable code after `return`; type mismatches where types are
  known (annotations + literal inference + stdlib return kinds parsed from
  the doc signature's `-> kind`); missing `_` in `match` over an enum.
  Output: `file:line:col: error|warning: message` plus a friendly summary;
  exit 1 on errors. `--json` for the LSP.
- `tbcore/types.py` exposes `check_source(source, path) -> list[Diagnostic]`
  used by both `tb check` and the LSP.
- Never reject a program that would run fine because of inference limits:
  unknown → `any`.

## Part E — Tooling (tooling-agent)

Wave 1 (independent of new AST): `editors/vscode/` and `tbtools/pkg.py`.
Wave 2 (after Part A): `fmt`, `lsp`, `debug`, `docgen`, `build`, `spec/`.

- **Package manager** `tbtools/pkg.py`: `tb init` (writes `tb.toml`:
  `[package] name version description main`, `[dependencies]`), `tb add
  <name>|<git-url>[@tag]` (resolves names via the index at
  `https://code.tanisha.ceo/packages/index.json` → `{name: {git, description,
  tags?}}`; clones with `git` CLI into `tb_packages/<name>/` at the tag or
  default branch, records in `tb.toml` + `tb.lock` (commit hash)), `tb
  remove`, `tb update`, `tb packages` (list). Packages are TB-only (a folder
  with `tb.toml` and `lib.tb`). Also `tb publish` → prints the JSON snippet
  to submit to the index. The site serves `packages/index.json` (site-agent)
  seeded with 2–3 packages extracted from this repo into `packages/` folder
  as example packages (e.g. `tb-colors`, `tb-tables`) — the index points at
  the repo's own git URL? No GitHub links on the site: index entries are
  plain git URLs supplied by package authors; seed entries use
  `https://code.tanisha.ceo/packages/<name>.zip` as a `zip` source
  (support `zip` URLs as well as `git`).
- **Formatter** `tb fmt [paths] [--check]`: canonical style (2-space
  indent, spaces around binary ops, one statement per line, trailing commas
  in multi-line literals, comments preserved, max line 100 with sensible
  wrapping for calls/lists/maps). Idempotent; `--check` exits 1 on diff.
  Round-trip test: format every `.tb` in the repo, re-parse → same AST
  (ignoring comments), format again → identical.
- **Language server** `tb lsp` (stdio, LSP 3.17): diagnostics (parse +
  `check_source`), completion (keywords, builtins, stdlib modules and
  their members, methods by receiver type, user symbols), hover (doc
  strings), signature help, go-to-definition (same file + relative
  imports), document symbols, formatting, rename within file. Pure stdlib
  JSON-RPC.
- **VS Code extension** `editors/vscode/`: `package.json`, TextMate grammar
  `syntaxes/tb.tmLanguage.json` (all 3.0 keywords, f-strings with holes,
  bytes, raw strings, numbers incl. hex/bin, comments), `language-
  configuration.json` (brackets, comments, auto-closing), snippets, client
  (`client/extension.js`, plain JS, no bundler: starts `tb lsp` using the
  `tb` on PATH or a configured path), commands: "TB: Run File" (terminal),
  "TB: Format", "TB: Check". `npm run package` uses `@vscode/vsce` to build
  `tb-vscode-3.0.0.vsix` (CI job by packaging-agent). Icon: the TB mark
  (reuse site favicon as PNG).
- **Debugger** `tb debug file.tb`: CLI with `break LINE|FILE:LINE`, `run`,
  `step`, `next`, `continue`, `print EXPR`, `locals`, `backtrace`, `quit`;
  built on `Interpreter.trace`/`frames()`/`eval()`. Also `--dap`? Skip DAP.
- **Doc generator** `tb doc FILE.tb|DIR`: `##` doc comments above `fn`,
  `class`, `const`, `export` → `tb doc` text and `--html`/`--md` output; the
  website uses it for apps (site-agent).
- **Build** `tb build app.tb [--out dist/app] [--name app]`: produces a
  folder with a copy of the TB runtime (`tb`/`tb.exe` + `_internal`; when
  running from source, falls back to copying `tb.py` + `tbcore` + `tblib`
  plus a `run.bat`/`run.sh`) and the app with its imports/assets, plus a
  launcher: on Windows `app.exe` = copy of `tblaunch.exe` (packaging-agent
  adds this PyInstaller entry: it runs `<own name>.tb` next to it;
  windowed variant `applaunchw.exe` for gui apps when `--gui`), elsewhere a
  shell script `app`. Document limits.
- **Spec** `spec/SPEC.md`: grammar (EBNF) and semantics for 3.0;
  `spec/*_test.tb` conformance tests runnable with `tb test spec`.

## Part F — Standard library additions

### F1. soc-bin-agent (binary formats)
- `pcap`: read pcap and pcapng (`pcap.read(path, limit?) -> list of packet
  maps`, `pcap.each(path, fn)`), decode Ethernet, IPv4/IPv6, ARP, TCP, UDP,
  ICMP, DNS (queries/answers), HTTP request lines/headers, TLS ClientHello
  SNI; `pcap.flows(packets)` (5-tuple aggregation with bytes/packets/
  first/last), `pcap.top_talkers`, `pcap.dns_queries`, `pcap.http_requests`,
  `pcap.sni`, `pcap.summary(path)`, `pcap.write(path, packets)` (for
  filtered output). Sample `apps/data/sample.pcap` generated by the agent
  (synthetic, documentation IPs) with a port scan, DNS lookups, an HTTP
  request to a defanged-looking host, and a TLS hello.
- `evtx`: read binary Windows EVTX (`evtx.read(path) -> records` with the
  same field names `logs` uses: EventID, TimeCreated, Computer, Channel,
  Provider, EventData fields, plus `action`/`description` via the same
  mapping as `logs`), `evtx.each`, `evtx.summary`. Implement the binary
  XML (chunks, records, templates, substitutions) yourself; test with a
  small synthetic EVTX you generate (write a minimal EVTX writer in the
  tests) — report fidelity limits honestly. `logs.read(path)` with
  `format="evtx"` or auto by magic `ElfFile` delegates to `evtx`.
- `pe` and `elf`: headers, sections (name, size, entropy), imports/exports
  (PE), compile timestamp, architecture, entry point, `is_signed` (PE
  security directory present), suspicious indicators list (high-entropy
  sections, writable+executable, unusual entry section, packed-like
  names); `binary.file_type(path)` magic sniffing (PE/ELF/Mach-O/PDF/ZIP/
  PNG/...).
- `scan`: YARA-like rules in a TB-friendly form (map or a small text rule
  format mirroring YARA: `rule Name { meta: ... strings: $a = "text"
  nocase, $b = { 4D 5A ?? 00 }, $c = /regex/ condition: $a and ($b or $c)
  }` — parse this subset), `scan.rule(text)`, `scan.file(path, rules)`,
  `scan.bytes(data, rules)`, `scan.folder(dir, rules)` → matches with
  offsets; `scan.strings(data, min=4)` (ASCII/UTF-16LE strings extraction).
- Apps: `apps/pcap-triage/`, `apps/evtx-triage/`, `apps/file-inspector/`
  (pe/elf/scan/hash/strings; CLI + a GUI variant `apps/file-inspector-gui/`).

### F2. soc-intel-agent (crypto, SIEM, intel formats, full Sigma)
- `crypto`: AES-GCM and AES-CBC (pure Python AES, PKCS7; document speed),
  `random_bytes(n)`, `pbkdf2(password, salt, iterations, length)`, `scrypt`,
  `hmac(key, data, algo)`, `totp(secret_base32, time?)` + `verify_totp`,
  `jwt_sign(payload, key, alg="HS256")`, `jwt_verify(token, key)` (HS256/
  HS384/HS512; RS256 verify via pure-Python RSA with PEM/DER public-key
  parsing), `jwt_decode(token)` (unverified, flagged), `password_hash(pw)`
  (scrypt, encoded string) / `password_verify`, `x509.parse(pem)` →
  subject/issuer/validity/SANs/fingerprints (DER parser, no OpenSSL
  dependency beyond stdlib `ssl` for fetching), `tls.certificate(host,
  port=443)`.
- `siem`: `siem.cef(event_map) -> text`, `siem.leef`, `siem.syslog_line`,
  `siem.send_syslog(host, port, text, proto="udp")`, Splunk HEC
  `siem.splunk(url, token).send(events)`, Elasticsearch bulk
  `siem.elastic(url, auth).index(index, docs)`, Sentinel (Log Analytics
  data collector HTTP API with shared key signature)
  `siem.sentinel(workspace_id, key).send(log_type, records)`; all over
  `http`; dry-run mode that returns the payload; tests use a local server.
- `attack`: bundled compact MITRE ATT&CK Enterprise table (`tblib/data/
  attack.json`: technique id, name, tactics, platforms, short description;
  build it from your knowledge, ~600 techniques/sub-techniques — mark the
  snapshot version; don't claim completeness), `attack.technique("T1110")`,
  `attack.search("brute")`, `attack.tactic("credential-access")`,
  `attack.map(alerts)` (uses rule `tags` like `attack.t1110`).
- `stix`: parse STIX 2.1 bundles → indicators (pattern → simple IOC
  extraction: `[ipv4-addr:value = '1.2.3.4']`, hashes, domains, URLs),
  `stix.indicators(bundle)`, `stix.to_detect_rules(bundle)`, `stix.bundle(
  iocs)` (write), TAXII 2.1 client `stix.taxii(url, auth).collections()`,
  `.objects(collection_id, added_after?)` over `http`.
- Sigma (extend `tblib/detect.py` — coordinate: only this agent edits
  detect.py this round): all modifiers (`base64`, `base64offset`, `utf16`,
  `utf16le`, `utf16be`, `wide`, `windash`, `cidr`, `re` flags, `lt/lte/
  gt/gte`, `exists`, `expand`?, `contains|all`, `fieldref`), conditions
  with `all of them`, `1 of selection_*`, `not`, aggregation `count()
  by`, `count(distinct field)`, `min/max/avg/sum`, `near` (time proximity),
  Sigma correlation rules (`correlation: type: event_count / value_count /
  temporal / temporal_ordered`), `logsource` → record-format mapping table,
  `fields` output, and `detect.sigma_to_query(rule, backend)` for
  `"splunk"`, `"elastic"` (Lucene/KQL), `"kql"` (Sentinel) with field
  mappings. YAML: anchors/aliases/multi-doc (extend the YAML subset parser
  in detect.py or move it to `tblib/yaml.py` — move it, and `yaml` becomes
  a public library: `yaml.parse`, `yaml.stringify`, `yaml.read`, `yaml.write`;
  data-agent must NOT write yaml.py then — coordinated here).
- Apps: `apps/sigma-studio/` (CLI: validate, convert to Splunk/Elastic/KQL,
  run), `apps/ioc-feed/` (STIX/TAXII pull → detect rules → run on logs),
  `apps/siem-forwarder/` (tail a log, forward as CEF/HEC; dry-run default).

### F3. data-agent (data, net, misc)
- `sqlite`: `sqlite.open(path|":memory:") -> db` with `run(sql, params?)`,
  `all(sql, params?) -> list of maps`, `one`, `value`, `tables()`,
  `columns(t)`, `execute_many`, `transaction(fn)`, `close()`; friendly SQL
  error messages.
- `datetime`: time-zone aware: `datetime.now(zone?)`, `datetime.parse(text,
  pattern?, zone?)` (ISO 8601 + common log formats), `.to_zone(z)`,
  `.format(pattern)`, `.add({days:1, hours:2})`, `.diff(other)`, `.date`,
  `.weekday`, `datetime.zones()` (zoneinfo), `.to_utc()`, `.timestamp`.
  Make `time` library interoperate (`time.parse` returning timestamps).
  Fix the known mixed-zone ordering issue in `logs`: records get `zone`
  info when present and `when_utc`.
- `decimal`: exact decimals (`decimal.of("0.1") + decimal.of("0.2")` →
  `0.3`), `.round(places)`, arithmetic via methods or operators (operators
  through class special methods).
- `xml`: `xml.parse(text) -> node` (tag, attrs, children, text), `.find(path)`
  (simple XPath subset), `xml.stringify`, `xml.read/write`; safe against
  entity expansion (defusing: reject DOCTYPE).
- `toml`: `toml.parse/stringify/read/write` (use tomllib on 3.11+, vendored
  minimal parser fallback for 3.10; stringify yourself).
- `archive`: `archive.zip(target, paths)`, `archive.unzip(path, dest,
  options {safe: true})` (zip-slip safe), `archive.list(path)`, `gzip`/
  `gunzip`, `tar` create/extract with safety checks.
- `template`: `template.render(text, values)` with `{{name}}`, `{{#list}}
  ...{{/list}}`, `{{^empty}}`, `{{name|upper}}` filters, HTML-escaping by
  default, `{{{raw}}}`; `template.file(path, values)`.
- `net` additions: UDP (`net.udp_send`, `net.udp_listen(port, fn)` with
  stop), `dns` library: pure-Python DNS client over UDP/TCP: `dns.lookup(
  name, type="A", server?)` for A/AAAA/MX/TXT/NS/CNAME/PTR/SOA/SRV,
  `dns.reverse`, `dns.servers()`; `tls` library: `tls.certificate(host)`
  (coordinate: soc-intel writes `x509.parse`; data-agent writes `tls.
  certificate` using `ssl` and returns PEM + parsed via `x509` if present),
  `tls.versions(host)`, `tls.check(host)` (expiry days, hostname match).
- `websocket`: client (`websocket.connect(url) -> ws` with `send`,
  `receive(timeout?)`, `close`, `each(fn)`), RFC 6455 framing, TLS via ssl.
- `server`: tiny HTTP server: `server.create() -> app`; `app.get(path, fn)`,
  `app.post`, path params `/items/{id}`, `req` map (method, path, params,
  query, headers, body, json), `res` helpers (`server.json(data)`,
  `server.text`, `server.html`, `server.file(path)`, `server.redirect`),
  `app.static("/assets", dir)`, `app.listen(port, host="127.0.0.1")`
  (blocking; `app.start()` background + `app.stop()`), request logging,
  errors → 500 JSON with friendly message; `app.ws(path, fn)` optional.
  Security defaults: binds 127.0.0.1 unless told otherwise, max body 10 MB,
  path traversal safe static.
- `fs.watch(path, fn, interval=1)` polling watcher (also `fs.watch_stop`).
- Apps: `apps/dashboard/` (server + sqlite + template: alerts dashboard
  fed from log-triage; `apps/notes-web/`? one web app is enough),
  `apps/dns-check/`, `apps/cert-check/`.

### F4. gui-agent (GUI additions)
- Widgets: `tabs([...names])` → `.page(name)` container; `tree(columns)`
  (hierarchical, `.add(parent_id, values) -> id`, expand/collapse,
  `on_select`); `chart({"type": "bar"|"line"|"pie", "data": ..., "labels":
  ...})` drawn on a Canvas with axes/legend/tooltips-on-hover, `.update(
  data)`; `canvas(width, height)` with `line rect oval text image clear`
  primitives and mouse events; `slider`, `spinner` (numbers), `date_picker`
  (simple), `color` (picker dialog), `link` (opens URL), `badge`, `card`
  container, `scroll` container, `splitter` (paned); drag-and-drop of
  files onto a widget (`on_drop(paths)`; Tk has no native DnD without
  tkdnd: implement at least drop of text/paths via clipboard-free fallback
  — if impossible, provide `on_paste_paths` and document); `win.icon(path)`,
  `win.always_on_top`, `win.fullscreen`, `win.center()`, `win.resize`,
  `win.on_close(fn)` (confirm), `win.on_key`, `win.bind_shortcut`; system
  tray: implement with a hidden Toplevel menu fallback on Linux/Windows if
  real tray impossible — document; notifications `gui.notify(title, text)`
  (macOS `osascript`, Windows PowerShell toast, Linux `notify-send`,
  fallback alert).
- Theming: `gui.theme("light"|"dark"|"system")`, accent colour option,
  high-DPI scaling awareness.
- `win.run_in_background(fn, on_done, on_error)` + `gui.on_main(fn)`
  (contract with concurrency-agent: gui-agent implements the Tk side using
  `after(0)` marshalling; it can be used before `parallel` exists via
  Python threads inside gui).
- Keep the fake backend in step so apps remain testable headless.
- Apps: `apps/alerts-dashboard-gui/` (tabs + chart + tree over logs/detect),
  `apps/network-monitor/` (live chart of tcp_check results), update
  `apps/log-triage-gui` to use tabs/chart.

### F5. Testing library (whoever touches `test`: core-agent)
- `test.mock(module_or_map, name, fn)` with auto-restore, `test.fixture`,
  `test.each(cases, fn)` (parametrised), `test.approx`, `test.raises(fn,
  ErrorClass?)`, `test.skip(reason)`, `tb test --coverage` (line coverage
  via the trace hook; prints per-file %).

## Part G — Website and docs (site-agent, wave 3)

- Version 3.0.0 everywhere; "What's new in 3.0" page; migration guide page;
  Learn: lessons for every Part A feature; library pages for all new
  libraries (generated); Tools page (`tb fmt/check/lsp/debug/build/add`,
  VS Code extension download `/dl/tb-vscode-3.0.0.vsix` + install steps);
  Packages page (`packages/index.json` + how to publish); Spec page from
  `spec/SPEC.md`; apps gallery includes the new apps with screenshots;
  playground: 3.0 syntax highlighting, runs the new libraries that work in
  the browser (sqlite via Pyodide works; server/ws/udp/dns/parallel-threads?
  threads don't work in Pyodide → friendly error), examples for match,
  classes, generators, pcap on a sample file.
- Downloads page updated for new assets (`tb-vscode-3.0.0.vsix`, example
  packages).

## Part H — Packaging and CI (packaging-agent, wave 3)

- Bundle `tbcore/`, `tbtools/`, `tblib/data/`, new `.tb` libs; add
  `tblaunch`/`tblaunchw` entries; version 3.0.0 in spec/iss/build;
  installer adds the new apps' shortcuts (GUI ones) and registers `tb`
  file-type icon; `tb-vscode-3.0.0.vsix` built in CI (node + `@vscode/vsce`)
  and attached; CI matrix runs pytest with VM (default) and
  `TB_ENGINE=tree`; smoke tests cover `tb check`, `tb fmt --check` on the
  repo, `tb build` of an example app and running the built launcher.
- Example packages zipped into `dist/site/packages/<name>.zip` by
  `build_site.py` (site-agent) from `packages/<name>/`.

## Part I — QA (qa-agent, wave 4)

Independent verification of everything above, as in 2.0, plus: write a
non-trivial 3.0 program (classes + match + generators + bytes + parallel +
sqlite + server) and run it; VS Code extension installs from the .vsix and
the LSP gives completions and diagnostics (test the LSP over stdio with a
scripted JSON-RPC session); `tb build` output runs on this Mac; formatter
idempotent on the whole repo; benchmarks reproduced.
