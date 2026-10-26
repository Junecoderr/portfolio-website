# Contributing to TB

This guide covers working on TB itself: running it from source, the test
suite, builds, releases and the website. For using the language, see
[`README.md`](README.md) and https://code.tanisha.ceo/docs.

## Development setup

You need [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned in
`.python-version`; uv fetches it if needed.

```sh
uv run python tb.py welcome.tb                   # run a program from source
uv run python tb.py                              # REPL
uv run python tb.py doc ioc                      # one library's reference
uv run python tb.py test apps/log-triage         # TB's own test runner
uv run pytest                                    # the whole test suite
```

GUI programs need a Python with Tk. Homebrew's `python3.12` has none; use one
that does (for example `/opt/homebrew/bin/python3.13`) to open real windows.

Design documents: [`DESIGN-2.0.md`](DESIGN-2.0.md) (modules, native-module
API, standard library conventions) and [`DESIGN-3.0.md`](DESIGN-3.0.md)
(the 3.0 language, VM, tooling and libraries). Native libraries are
`tblib/<name>.py` built on `tblib/_api.py` (see `tblib/README.md`);
libraries written in TB are `tblib/<name>.tb`.

## Tests

```sh
uv run pytest                          # everything
uv run pytest tests/lib/test_ioc.py    # one library
uv run pytest tests/lang3              # 3.0 language features
TB_ENGINE=tree uv run pytest           # the suite on the tree-walking engine
```

- `tests/` covers the lexer, parser, both engines, every language feature, the
  CLI and tools, each library (`tests/lib/test_<name>.py`), the apps and the
  website. GUI tests use a fake backend so they run anywhere; the few that open
  real Tk windows are skipped without Tk and a display.
- `tests/test_examples.py` runs every example against its `.out` file and
  checks that `site/examples.html` and the runnable snippets on
  `site/docs.html` match and run.
- `tests/test_site.py` runs every library example, playground example and
  tutorial snippet, checks the generated library reference and the shared
  page header/footer, that pages have no inline scripts (the CSP forbids
  them), that every download the site links to is produced by the release
  workflow, and does a full site build when Pyodide has been fetched.
- `spec/*_test.tb` are conformance tests: `tb test spec`.

## Examples and the welcome program

Every `examples/<name>.tb` starts with `# Title: ...` and `# About: ...`.
Next to it, `<name>.out` holds the expected output (`♥ {care}` /
`♥ {care:NAME}` stand for random `care()` lines) and `<name>.in` the answers
for `ask()`. `site/examples.html` keeps a static copy of each example in
`<pre class="tb-code" id="code-<name>">`; update it with the example.

`welcome.tb` is the first program people run (the playground opens on it).
If its output changes, update `tests/welcome.out`, `tests/test_welcome.py`
and its copy on `site/examples.html`.

## Building the binaries

```sh
uv run --group build python packaging/build.py
```

PyInstaller (`packaging/tb.spec`) produces a one-folder app in `dist/tb/`: the
`tb` program (`tb.exe`, plus `tbw.exe` without a console on Windows) and the
`tb build` launcher `tblaunch` (`packaging/tblaunch.py`: runs `<its own
name>.tb` next to itself; Windows also `tblaunchw.exe` for GUI apps), all
sharing `_internal/` (Python, `tbcore`, `tbtools`, `tblib` incl.
`tblib/data/`, sqlite3, ssl, Tcl/Tk), plus `apps/`, `examples/` and
`welcome.tb`. Unused Tcl/Tk data is excluded in the spec; new modules in
`tbcore/`, `tbtools/` and `tblib/` are picked up automatically.
`build.py` smoke-tests the build from an empty folder (`--version` and its
start-up time, `welcome.tb`, `tb check`, `tb fmt --check`, `tb doc`, a library
program, a 3.0 class/match/generator program on both engines, `tb build` plus
running the built launcher, and a GUI app when Tk and a display exist),
packages it under the release name for the current OS, unpacks and re-tests
the archive (including `packaging/run_examples.py --gui`: every example, every
app - GUI apps close themselves, the dashboard server is started and queried -
and `tb test spec`, `tb test apps/log-triage`, `tb test packages`), and
updates `dist/SHA256SUMS.txt`.

| OS | Assets |
|---|---|
| Windows x64 | `tb-windows-x64-setup.exe` (Inno Setup), `tb-windows-x64.zip` (portable) |
| macOS Apple silicon | `tb-macos-arm64.zip` |
| Linux x64 | `tb-linux-x64.tar.gz` |

PyInstaller cannot cross-compile; CI builds each OS on its own runner. For a
local build with GUI support use a Python with Tk:
`uv run --no-project --python /opt/homebrew/bin/python3.13 --with pyinstaller==6.11.1 python packaging/build.py`.
`uv run python packaging/run_examples.py dist/tb/tb [--gui]` runs every
example, app and test suite through a build.

**Windows installer** (`packaging/windows/tb.iss`): per-user install into
`%LOCALAPPDATA%\Programs\TB`, adds it to the user `PATH`, registers `.tb`
files (run on double-click, Edit opens Notepad), and creates a Start menu
folder with TB Prompt, the website, examples and the desktop apps. `.tb`
files get `packaging/windows/tb.ico`, made from `site/assets/img/favicon.svg`
by `packaging/windows/make_icon.py` (needs `rsvg-convert`; the `.ico` is
committed). The `AppId` GUID must never change.
`python packaging/windows/test_installer.py dist/tb-windows-x64-setup.exe`
installs silently, verifies and uninstalls (CI runs it).

Fallback zip for running with your own Python:
`uv run python packaging/make_fallback_zip.py` → `dist/tb-python.zip`.

## Releasing

Keep `tb.__version__`, `pyproject.toml` (then `uv lock`), `packaging/tb.spec`,
`packaging/build.py`, `packaging/windows/tb.iss`,
`editors/vscode/package.json` and the tag in step, then:

```sh
git tag tb-v3.0.0 && git push origin tb-v3.0.0
```

`.github/workflows/tb-release.yml` builds and tests Windows, macOS and Linux
(installing and uninstalling the Windows installer), builds the VS Code
extension (`npm ci` + `npm run package` in `editors/vscode/` ->
`tb-vscode-<version>.vsix`), builds `tb-python.zip`, and publishes them all
with `SHA256SUMS.txt` with `--latest=false`.
`.github/workflows/tb-ci.yml` runs the tests on all three systems whenever
something under `code/` changes, once on the VM and once with
`TB_ENGINE=tree`.

The website's download links are site-relative (`/dl/<asset>`); the rewrite
in `vercel.json` maps them to the release for the current version, so update
it when releasing and release before deploying a version bump.

## The website

```sh
uv run python scripts/fetch_pyodide.py          # once: pinned Pyodide into site/vendor/pyodide/
uv run python scripts/build_site.py             # assembles dist/site/
uv run python -m http.server -d dist/site 8000  # http://localhost:8000
```

`build_site.py` copies `site/`, the runtime (`tb.py`, `tbcore/`, `tblib/`) for
the playground, sample data and app sources; generates the library reference
from `tb.describe_library()` with one example per library in
`scripts/lib_examples/`; and fills the apps gallery. Hand-written pages share
the header and footer in `scripts/site_shell.py`; after changing the shell, adding a
library or bumping the version, run `uv run python scripts/sync_pages.py` to update
`site/*.html` (it also fills the home page's libraries grid). `spec.html`,
`migration.html` and `packages.html` are generated from Markdown by
`scripts/md2html.py`; the playground loads extra Pyodide packages (sqlite3,
hashlib/openssl, ssl, tzdata) vendored by `fetch_pyodide.py` on demand. Code is highlighted at
runtime by `site/assets/js/highlight.js`. The site's CSP allows no inline
scripts or event handlers, and the site never links to the source
repository.

### Deploying

The site is served at https://code.tanisha.ceo by the Vercel project
`tanisha-code` (Root Directory `code`; build and headers in `vercel.json`).
It is not connected to git; deploy from the repository root:

```sh
vercel deploy --prod
```

## Code style

- Python: standard library only at runtime, Python 3.10+.
- TB: `tb fmt` before committing; `tb check` must report no errors.
- Errors keep TB's voice: `Oops! On line N: <plain English>` with a hint.
- Every public function has a doc string whose first line is its signature
  (`name(args) -> kind`); `tb doc` and the website read it.
