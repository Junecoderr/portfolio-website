# TB

TB is a friendly programming language for desktop apps, security tools and
everything in between. It is a gentle first language whose mistakes come
with a kind, plain-English "Oops!". Version 2.0 keeps that voice and adds what you need
for real work: maps, methods, f-strings, modules, error handling, and a
standard library of 20 libraries, from windows and buttons to log parsing,
detection rules and IOC extraction.

Website: **https://code.tanisha.ceo** (guide, tutorials, library reference,
examples and apps, and an in-browser playground).

```tb
import logs
import detect

let records = logs.read("apps/data/auth.log", "auth")
for hit in detect.brute_force(records) {
  say(f"{hit.level}: {hit.count} failed logins from {hit.by}")
}
```

The interpreter is one Python file, `tb.py` (standard library only, Python
3.10+); the standard library lives next to it in `tblib/`. TB ships as a
Windows installer, portable folders for Windows, macOS (Apple silicon) and
Linux built with PyInstaller, a fallback zip for running `tb.py` with your own
Python, and the website, whose playground runs the real `tb.py` and `tblib/`
in the browser through Pyodide.

## Quick start

You need [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned in
`.python-version`; uv fetches it if needed.

```sh
uv run python tb.py welcome.tb                   # a short tour of TB
uv run python tb.py apps/log-triage/main.tb apps/data/auth.log
uv run python tb.py                              # interactive prompt (type `exit` to leave)
uv run python tb.py doc                          # list the libraries
uv run python tb.py doc ioc                      # one library's functions
uv run python tb.py test apps/log-triage         # run *_test.tb files
uv run pytest                                    # the whole test suite
```

On this Mac a shell hook blocks bare `python3`; always go through `uv run python`.
If uv hangs looking for Python, add `--python /opt/homebrew/bin/python3.12`.
That Python has no Tk, so GUI apps need a Python with Tk to open windows
(for example `/opt/homebrew/bin/python3.13`).

## The language

Everything from 1.0 still works: `let`, `=`, `fn` (named or as values, with
closures), `return`, `if / else if / else`, `while`, `for x in list_or_text`,
numbers, text, `true` / `false` / `nil`, lists, `and or not`, `#` comments,
and the built-ins `say len str num range push ask care`. 2.0 adds:

```tb
let me = {"name": "Alex", "plants": 4}             # maps
me.city = "Lisbon"                                 # dot access on maps
say(f"{me.name} has {me.plants} plants")           # f-strings
say("a,b".split(","), [3, 1, 2].sort(), me.keys()) # methods on text, lists, maps, numbers
let big = [5, 12, 9].filter(fn(n) { return n > 8 })

import json                                        # libraries (and import "file.tb")
try {
  json.parse("{oops")
} catch err {
  say(err.message, err.line)                       # every error is catchable
}
throw "custom error"                               # or throw a map with a "message"

for n in range(10) {
  if n == 3 { continue }
  if n > 5 { break }
}
let total = 0
total += 1                                         # += -= *= /=
say(2 in [1, 2], "x" not in "TB")                  # membership
```

New built-ins: `type keys int round min max sum sorted exit`. The design
contract is in [`DESIGN-2.0.md`](DESIGN-2.0.md).

## Standard library

Twenty libraries, all inside the TB app (reference with examples:
https://code.tanisha.ceo/library, or `tb doc NAME`):

| Group | Libraries |
|---|---|
| Core | `math` `text` `time` `random` `re` `os` |
| Files and data | `fs` `json` `csv` |
| Security (SOC) | `logs` `detect` `ioc` `hash` `encode` `report` |
| Network and intel | `net` `http` `intel` |
| Desktop GUI | `gui` |
| Testing | `test` |

Native libraries are `tblib/<name>.py` (see `tblib/_api.py`); libraries written
in TB are `tblib/<name>.tb`.

## Apps

`apps/` holds complete programs, each a folder with `main.tb` and a
`README.md`. They ship inside every download; sample data is in `apps/data/`
(auth.log, an Apache access log, a firewall log, Windows Security events as
JSON and CSV, a phishing email and Sigma rules).

- Desktop (gui): `todo`, `care`, `notes`, `calculator`, `hash-checker`,
  `log-triage-gui`, `ioc-workbench`
- Security tools: `log-triage`, `ioc-extractor`, `brute-force-hunter`, `sigma-runner`
- Network and intel: `ip-reputation`, `port-check`, `uptime-monitor`

Screenshots for the website are in `site/assets/img/apps/<name>.png`.

## Tests

```sh
uv run pytest                          # everything
uv run pytest tests/lib/test_ioc.py    # one library
uv run pytest tests/test_site.py       # website scripts, tutorial snippets, page shell
```

- `tests/` covers the lexer, parser, interpreter, 2.0 language features, CLI,
  each library (`tests/lib/test_<name>.py`), the apps and the website.
- GUI tests use a fake backend, so they run anywhere. The few tests that open
  real Tk windows are skipped unless the Python running them has Tk and a display.
- `tests/test_examples.py` runs every example against its `.out` file and
  checks that `site/examples.html` and the runnable snippets on
  `site/docs.html` match and run.
- `tests/test_site.py` runs every library example, playground example and
  tutorial snippet, checks the generated library reference, the shared page
  header and footer, that pages have no inline scripts (the CSP forbids them),
  that every release asset the site links to is uploaded by the release
  workflow, and does a full site build when Pyodide has been fetched.

## Building the binaries locally

```sh
uv run --group build python packaging/build.py
```

This runs PyInstaller with `packaging/tb.spec` and makes a **one-folder** app in
`dist/tb/`: the `tb` program (`tb.exe`, plus `tbw.exe` without a console window
on Windows) next to `_internal/` (Python, the `tblib` standard library, Tcl/Tk).
A one-folder app starts in a fraction of a second; a one-file app re-unpacks
itself on every start (about 4 s on macOS). Unused Tcl/Tk data (translations,
time zones, most encodings, demos) is left out in the spec.

`build.py` then smoke-tests `dist/tb/tb` from an empty folder (`--version`,
`welcome.tb`, `tb doc`, `tb doc gui`, a json/re/hash/ioc/logs program, and
the calculator GUI app with `TB_GUI_AUTOCLOSE_MS=500` when Tk and a display are
available; `TB_REQUIRE_GUI=1` makes that mandatory), packages the folder under
the release name for the current OS, unpacks the archive and tests that copy,
and updates `dist/SHA256SUMS.txt`:

| OS | Assets | Inside |
|---|---|---|
| Windows x64 | `tb-windows-x64-setup.exe` (Inno Setup installer) and `tb-windows-x64.zip` (portable) | zip: `tb\tb.exe`, `tb\tbw.exe`, `tb\_internal\` |
| macOS Apple Silicon | `tb-macos-arm64.zip` | `tb/tb`, `tb/_internal/` (exec bits and symlinks kept) |
| Linux x64 | `tb-linux-x64.tar.gz` | `tb/tb`, `tb/_internal/` |

The gui needs a Python with Tk to build from: CI's `actions/setup-python` has it;
Homebrew's `python3.12` here does not (the build then works but skips the gui
check), so for a local GUI build use `uv run --no-project --python
/opt/homebrew/bin/python3.13 --with pyinstaller==6.11.1 python packaging/build.py`.
An Intel Mac build is named `tb-macos-x64.zip` (CI only ships arm64).
PyInstaller can't cross-compile, so each OS builds its own (CI does all three).

`uv run python packaging/run_examples.py dist/tb/tb` runs every example, every
non-GUI app (`apps/*/main.tb`) and `tb test apps/log-triage` through a build.

**Windows installer** (`packaging/windows/tb.iss`, built by `build.py` on Windows
when Inno Setup's `ISCC.exe` is installed; `--no-installer` skips it): per-user
install, no admin rights, into `%LOCALAPPDATA%\Programs\TB`; adds that folder to
the user PATH; `.tb` files run with a double-click (TB waits for Enter before
the window closes) and have an Edit (Notepad) verb; Start menu folder "TB" with
TB Prompt, TB website, TB Examples and the GUI apps (Care check-in, To-do, Notes,
Calculator, Hash checker, Log triage, IOC workbench, started with `tbw.exe` so
no console window flashes). `apps/`, `examples/` and `welcome.tb` are
installed too. Uninstall removes all of it again but keeps files the apps saved
(the Care journal, the to-do list). The `AppId` GUID in `tb.iss` must never
change. `python packaging/windows/test_installer.py dist/tb-windows-x64-setup.exe`
installs silently into a temp folder, checks it and uninstalls (CI runs it).

The fallback zip (`tb.py`, `tblib/`, `welcome.tb`, `examples/*.tb`, `apps/`, `README.txt`):

```sh
uv run python packaging/make_fallback_zip.py   # -> dist/tb-python.zip
```

## Releasing

Tag the version and push the tag:

```sh
git tag tb-v2.0.1 && git push origin tb-v2.0.1
```

`.github/workflows/tb-release.yml` (at the repo root) builds Windows, macOS
and Linux on their own runners, tests each build (on Windows it also installs,
tests and uninstalls the installer), and creates the GitHub release
`tb-v2.0.1` with `--latest=false` (so it doesn't show as the portfolio's latest
release). Assets:

| Asset | What it is |
|---|---|
| `tb-windows-x64-setup.exe` | Recommended for Windows. Per-user installer, no admin |
| `tb-windows-x64.zip` | Portable `tb\` folder: `tb.exe`, `tbw.exe`, `_internal\`, `apps\`, `examples\`, `welcome.tb` |
| `tb-macos-arm64.zip` | `tb/` folder: `tb`, `_internal/`, `apps/`, `examples/`, `welcome.tb` |
| `tb-linux-x64.tar.gz` | Same layout as macOS |
| `tb-python.zip` | `tb.py`, `tblib/`, examples, apps, `README.txt` |
| `SHA256SUMS.txt` | `sha256sum` lines for all of the above |

The Download page links to site-relative `/dl/<asset>` URLs (asset names
above); a rewrite on the hosting side maps `/dl/:file` to the matching release
asset, so update that rewrite when you release a new version, and release
before deploying a version bump. The version shown on the site comes from
`tb.__version__`. Keep `tb.__version__`, `pyproject.toml` and the tag in step. If creating
the release fails with "Resource not accessible by integration": **Settings,
Actions, General, Workflow permissions, "Read and write permissions"**.
`.github/workflows/tb-ci.yml` runs the tests on Windows, macOS and Linux
whenever something in `code/` changes.

## The website

```sh
uv run python scripts/fetch_pyodide.py          # once: pinned Pyodide 0.27.7 into site/vendor/pyodide/ (gitignored)
uv run python scripts/build_site.py             # assembles dist/site/
uv run python -m http.server -d dist/site 8000  # open http://localhost:8000
```

`build_site.py` copies `site/`, `tb.py`, `tblib/` (with `tblib/index.json`),
`apps/data/` (with an index, loaded into the playground's virtual disk) and
every app's source; adds `scripts/playground_examples/` to the playground;
fills the Apps gallery on `examples.html` (`scripts/apps_gallery.py`); and
generates the library reference, `library.html` and `lib/<name>.html`, from
`tb.describe_library()` (`scripts/gen_lib_docs.py`, with one example per
library in `scripts/lib_examples/`). Generated pages aren't committed.

Hand-written pages share the header and footer in `scripts/site_shell.py`;
the tests fail if a page drifts from it. Code is coloured at runtime by
`site/assets/js/highlight.js`, so the HTML keeps plain code (the tests compare
it with the `.tb` files). The live site's CSP allows no inline scripts or event
handlers.

### Deploying

The site is served at **https://code.tanisha.ceo** by a separate Vercel
project, **`tanisha-code`**, with Root Directory `code`. `code/vercel.json`
holds the build (`scripts/fetch_pyodide.py` then `scripts/build_site.py --out
dist/site`), clean URLs and security headers. The project isn't connected to
git, so deploy from the **repo root**:

```sh
vercel deploy --prod
```

The portfolio's own Vercel project ignores `code/`.

## The welcome program

`welcome.tb` is the first program people run (the playground opens on it).
If its output changes, update `tests/welcome.out`, the expected lines in
`tests/test_welcome.py` and the copy of the code on `site/examples.html`
(`uv run pytest tests/test_examples.py tests/test_welcome.py` tells you if
any of them is out of date).

The site never names its author; `scripts/name_guard.py` checks generated
pages for that without spelling the name out.

## Examples

Every `examples/<name>.tb` starts with `# Title: ...` and `# About: ...`
lines, which the playground menu uses. Next to it, `<name>.out` holds the
expected output (`♥ {care}` / `♥ {care:NAME}` stand for random `care()`
lines) and `<name>.in` the answers for `ask()`. `site/examples.html` has a
static copy of each example's code in `<pre class="tb-code" id="code-<name>">`:
update it when you change an example.

## Project layout

```
tb.py                  the interpreter: lexer, parser, tree-walking interpreter, module loader, REPL, CLI
tblib/                 the standard library (native .py modules and .tb modules; _api.py helpers)
apps/                  example applications (+ data/ sample logs, emails, Sigma rules)
examples/              small example programs (+ .out expected output, .in answers)
welcome.tb             a short tour of TB, the first program to run
tests/                 pytest suite (tests/lib/ for the libraries)
scripts/               fetch_pyodide.py, build_site.py, gen_lib_docs.py, apps_gallery.py,
                       site_shell.py, name_guard.py, lib_examples/, playground_examples/
site/                  the website (index, docs, guide-*, examples, download, playground)
  assets/              css/style.css, js/ (theme, highlight, runner, tb-pyodide, playground, home, docs, download), img/
  vendor/pyodide/      downloaded by scripts/fetch_pyodide.py, gitignored
packaging/             PyInstaller spec, build.py, make_fallback_zip.py, run_examples.py
  windows/             tb.iss (Inno Setup installer), test_installer.py
vercel.json            Vercel build and headers for code.tanisha.ceo
DESIGN-2.0.md          the 2.0 design contract
```

## Known limitations

- **Unsigned binaries.** There's no code-signing certificate, so Windows
  SmartScreen warns the first time the installer (or the portable `tb.exe`)
  runs: More info, Run anyway.
- **macOS quarantine.** Every file in the downloaded `tb/` folder is
  quarantined and Gatekeeper blocks the unsigned program, so run
  `xattr -dr com.apple.quarantine ~/tb` once on the unzipped folder. `tb` must
  stay next to `_internal/`. The first run takes a few seconds.
- **macOS builds are Apple silicon only** (`tb-macos-arm64.zip`). Intel Macs
  can use `tb-python.zip` with their own Python, or the playground.
- **The playground can't do everything.** `gui`, `http`, `intel`, network
  lookups and port checks in `net`, and `os.run` need the TB app; in the
  browser they stop with a friendly message. `fs` works on a virtual disk
  holding `apps/data/`.
- **The playground runs on the page's main thread**: the page can't repaint
  while a program runs, so output appears when it finishes, and `ask()` uses
  the browser's pop-up box (showing the last lines printed). Programs are
  stopped after 10 seconds without a pause. It downloads about 10 MB
  (Pyodide) the first time.
