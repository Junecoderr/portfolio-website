# TB

TB is a tiny, friendly programming language, made as a gift for
Tanisha Brahma (TB = her initials). The whole language is one Python file,
`tb.py` (standard library only, Python 3.10+). It ships as:

- standalone binaries for Windows, macOS (Apple Silicon) and Linux, built with PyInstaller;
- a fallback zip (`tb-python.zip`) for running `tb.py` with your own Python;
- a static website with a gentle tutorial, examples, and an in-browser
  playground that runs the real `tb.py` through Pyodide.

```tb
let name = ask("What's your name? ")
say("Hello, " + name + "!")
care(name)
```

## Quick start

You need [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned in
`.python-version`; uv fetches it if needed.

```sh
uv run python tb.py for_tanisha.tb   # run the demo
uv run python tb.py examples/care_bot.tb
uv run python tb.py                  # interactive prompt (type `exit` to leave)
uv run python tb.py --version
uv run pytest                        # the whole test suite
uv run pytest tests/test_examples.py # just the examples + website code blocks
```

On this Mac a shell hook blocks bare `python3`; always go through `uv run python`.
If uv hangs looking for Python, add `--python /opt/homebrew/bin/python3.12`.

## Building the binaries locally

```sh
uv run --group build python packaging/build.py
```

This runs PyInstaller with `packaging/tb.spec`, smoke-tests the binary
(`--version` and `for_tanisha.tb`), packages it under the release asset name
for the current OS (`tb-windows-x64.exe`, `tb-macos-arm64.zip`,
`tb-linux-x64.tar.gz`) in `dist/`, and updates `dist/SHA256SUMS.txt`.
PyInstaller can't cross-compile, so each OS builds its own binary (CI does all
three). `uv run python packaging/run_examples.py dist/tb` runs every example
through a built binary.

The fallback zip (`tb.py`, `for_tanisha.tb`, `examples/*.tb`, `README.txt`):

```sh
uv run python packaging/make_fallback_zip.py   # -> dist/tb-python.zip
```

## Where it lives and how it deploys

TB sits in the `code/` folder of the portfolio repo and is served at
**https://code.tanisha.ceo**.

- **Website:** a separate Vercel project (`tanisha-code`) with Root Directory
  `code`. `code/vercel.json` holds the build (`scripts/fetch_pyodide.py` then
  `scripts/build_site.py --out dist/site`), clean URLs and security headers.
  The portfolio's own Vercel project ignores this folder.
- **Tests:** `.github/workflows/tb-ci.yml` (repo root) runs the suite on
  Windows, macOS and Linux whenever something in `code/` changes.
- **Downloads:** tag a version and push the tag:
  ```sh
  git tag tb-v1.0.0 && git push origin tb-v1.0.0
  ```
  `.github/workflows/tb-release.yml` builds the Windows, macOS and Linux
  binaries on their own runners, tests them, and creates the GitHub release
  `tb-v1.0.0` with all assets plus `tb-python.zip` and `SHA256SUMS.txt`
  (marked "not latest" so it doesn't look like a portfolio release).
  The Download page links to `releases/download/tb-v<version>/`, where the
  version comes from `tb.__version__`, so release before deploying a version bump.
  If creating the release fails with "Resource not accessible by integration":
  **Settings -> Actions -> General -> Workflow permissions -> "Read and write
  permissions"**.

Keep `tb.__version__`, `pyproject.toml` and the tag in step when you bump the version.

## Previewing the website offline

```sh
uv run python scripts/fetch_pyodide.py          # once: downloads pinned Pyodide 0.27.7 into site/vendor/pyodide/
uv run python scripts/build_site.py             # assembles dist/site/ 
uv run python -m http.server -d dist/site 8000  # open http://localhost:8000
```

Opening the HTML files straight from disk won't work for the playground; serve them.

## Editing the personal message

- **Website:** `site/index.html`, between `<!-- PERSONAL MESSAGE: START (edit freely) -->`
  and `<!-- PERSONAL MESSAGE: END -->`. Sign it however you like.
- **Demo program:** the closing lines of `for_tanisha.tb` (and anything else in it).
  If its output changes, update `tests/for_tanisha.out` and the copy of the
  code on `site/examples.html` (`uv run pytest tests/test_examples.py` tells
  you if either is out of date). The interpreter tests may also check
  `for_tanisha.tb` output; run the full `uv run pytest`.

## Examples and keeping the website in sync

Every `examples/<name>.tb` starts with two header lines, which
`scripts/build_site.py` reads for the playground's example menu:

```tb
# Title: Care bot
# About: A gentle little bot that asks how you are feeling and answers kindly.
```

Next to each example:

- `<name>.out` is the expected output (one line per `say()`/`care()`), checked by
  `tests/test_examples.py`. `care()` is random, so a care line is written as the
  placeholder `♥ {care}` or `♥ {care:NAME}` (for `care(NAME)`).
- `<name>.in` (only for programs that call `ask()`) has one answer per line.

`site/examples.html` has a static copy of every example's code (HTML-escaped,
in `<pre class="tb-code" id="code-<name>">`). **When you add or change an example,
update that page too.** The tests fail if any code block differs from its file
or an example is missing from the page. Programs that call `ask()` get no
`data-runnable` (they open in the playground instead). The snippets on
`site/docs.html` are also run by the tests; blocks marked `data-expect-error`
must fail with an "Oops!" message.

## Project layout

```
tb.py                  the whole interpreter (lexer, parser, tree-walking interpreter, REPL, CLI)
for_tanisha.tb         the demo program
examples/              example programs (+ .out expected output, .in answers for ask())
tests/                 pytest suite (test_examples.py runs the examples and checks the site's code blocks)
packaging/             PyInstaller spec, build.py, make_fallback_zip.py, run_examples.py, README-python.txt
vercel.json            Vercel build + headers for code.tanisha.ceo
site/                  static website: index, download, playground, docs (Learn), examples
  assets/              css/style.css, js/ (config, theme, runner, tb-pyodide), img/
  vendor/pyodide/      downloaded by scripts/fetch_pyodide.py, gitignored
scripts/               fetch_pyodide.py, build_site.py
```

## Known limitations

- **Unsigned binaries.** There's no code-signing certificate, so Windows
  SmartScreen ("Windows protected your PC" -> More info -> Run anyway) and macOS
  Gatekeeper (right-click -> Open, or System Settings -> Privacy & Security ->
  Open Anyway) will warn the first time. The Download page walks through this.
- **macOS binary is Apple Silicon only** (`tb-macos-arm64.zip`). Intel Macs can
  use `tb-python.zip` with their own Python, or the playground.
- **The playground downloads about 10 MB** (Pyodide) the first time it runs
  code; after that the browser caches it.
- **`ask()` in the browser uses a pop-up** (`window.prompt`) rather than typing
  into the output area.
- The language is deliberately small: no `break`/`continue`, no dictionaries,
  no text helpers like lower-case or split, no random numbers apart from `care()`.
