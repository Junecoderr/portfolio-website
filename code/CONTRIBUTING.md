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
- `tests/tools/test_pkg.py` and `tests/tools/test_registry_cli.py` cover the
  package manager (local git repos, zips, a static index and a fake TB Package
  Index); `tests/api/*.test.mjs` are the Vercel Functions' Node tests (run by
  `tests/test_site.py` when `node` is installed).
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
`site/*.html` (it also fills the home page's libraries grid). `spec.html` and
`migration.html` are generated from Markdown by `scripts/md2html.py`, and the
package index pages by `scripts/registry_pages.py` (see "Package index" below); the playground loads extra Pyodide packages (sqlite3,
hashlib/openssl, ssl, tzdata) vendored by `fetch_pyodide.py` on demand. Code is highlighted at
runtime by `site/assets/js/highlight.js`. The site's CSP allows no inline
scripts or event handlers, and the site never links to the source
repository.

### "Visualize" pictures in the library reference

Every function, value, widget, window method, built-in function and method in
the reference has a collapsed "Visualize" drop-down: an SVG of what it does,
a legend chip per parameter (chip and drawing share a colour and light up
together) and, where a parameter is a size, count, width or similar, a
slider. `scripts/visuals/` draws them from declarative specs:
`scripts/visuals/specs/<lib>.py` (plus `methods.py`, `builtins.py`) each hold
one dict literal read with `ast.literal_eval`, mapping a function to a
template (`flow`, `list_op`, `net`, `lanes`, `threshold`, `timeline`, `gui`,
`generic`), example arguments and parameter roles; see the docstring in
`scripts/visuals/__init__.py` for the keys. Results are real: the examples run
through TB while the site builds (in a scratch folder, seeded, paths
scrubbed); functions that need the network, other programs or windows use
`"run": False` with a `"static"` example result, which the page labels as an
illustration. Slider positions are each run for real and drawn as variants.
`site/assets/css/visuals.css` and `site/assets/js/visuals.js` style and drive
them (no inline scripts). `tests/test_site.py` checks every entry has a
picture whose legend names every parameter, the SVGs are well-formed, and no
template fell back (`visuals.PROBLEMS`).

### Visit and download counters

The home page (a strip under the hero), the download page (a count next to
each file, plus a total) and every footer show live numbers. They come from
two Vercel Functions in `api/` (Node.js, ESM, no dependencies; shared code in
`api/_lib/stats.js`, which is not deployed as a function). Vercel deploys
`api/` as functions on its own, so `vercel.json` needs no `functions` entry,
and `build_site.py` only copies `site/`, so the function source is never
served.

- `POST /api/hit` with `{"path": "/download"}` adds 1 to the total
  (`tb:visits`) and to that page's field in the hash `tb:pages`. No cookies;
  IP addresses and user agents are never stored (the user agent is only
  checked against a list of bots, which are not counted). It accepts only
  POST, a body under 1 KB, a short same-site path and same-site requests.
- `GET /api/stats` returns `{visits, downloads: {total, assets}, updated}`.
  Downloads are the GitHub release assets' own `download_count`, summed over
  every `tb-v*` release and fetched on the server (browsers never contact
  GitHub), cached in memory for 10 minutes; the response is cached by the CDN
  for 30 seconds (`s-maxage=30, stale-while-revalidate=300`), and each visitor sees the count returned by their own `/api/hit`. Only the visit total is
  shown on the site; the per-page counts stay in Redis.
- Environment variables (Vercel project settings): `KV_REST_API_URL` and
  `KV_REST_API_TOKEN`, or `UPSTASH_REDIS_REST_URL` and
  `UPSTASH_REDIS_REST_TOKEN` (Upstash Redis from the Vercel Marketplace, used
  over its REST API with `fetch`); optionally `GITHUB_TOKEN` for a higher
  GitHub rate limit. Without Redis, `visits` is `null`; if GitHub fails,
  `downloads` is `null`. Neither ever turns into an error page.

`site/assets/js/stats.js` (loaded in every page's `<head>` by the shell)
posts the hit, fetches the stats and fills elements marked `data-stat="visits"`,
`data-stat="downloads"` or `data-stat="dl:<asset name>"`. Their
`data-stat-wrap` / `data-stats` containers start `hidden` and appear only
when a number arrives, so the site looks complete without the API. Local
previews (`localhost`, `127.0.0.1`) and automated browsers never count; add
`?stats` to a local URL to fetch the numbers anyway (for example under
`vercel dev`). The functions' unit tests are `tests/api/stats.test.mjs`
(`node --test tests/api/stats.test.mjs`; `tests/test_site.py` runs them when
`node` is installed).

### Package index (TB Package Index)

https://code.tanisha.ceo/packages is a reviewed package registry. Authors sign
in with GitHub, make an API token and run `tb publish`; an admin approves the
first version of every new package; `tb add` installs from it.

**Pieces.** The API is one Vercel Function, `api/registry.js` (code in
`api/_lib/registry/`: `app.js` routes and storage, `rules.js` names, versions,
limits and the tb.toml reader, `zip.js` archive checks, `services.js` cookies,
HMAC, Upstash and Blob over REST, `seed.js` generated). `vercel.json` rewrites
`/api/registry/<route>` to it (`?route=<route>`). Still no npm dependencies.
The pages are written by `scripts/registry_pages.py` during the site build:
`packages.html` (search and list), `packages/view.html?name=x` (README rendered
by a small Markdown-to-DOM renderer that never uses HTML strings, versions,
install command, sha256, Report), `packages/publish.html` (how to publish,
sign-in, tokens), `packages/account.html` (your packages and their review
status) and `packages/admin.html` (review queue, reports, yank/remove, audit
log), all filled by `site/assets/js/registry.js` and styled by
`site/assets/css/registry.css`. Without the API they fall back to
`packages/bundled.json` (list and package pages) or say the index isn't
reachable.

**Routes** (all under `/api/registry/`):

| Route | Who | What |
|---|---|---|
| `GET index` | anyone | approved packages for `tb`: `{format: "tb-registry-1", packages: {name: {description, version, keywords, owner, downloads, versions: {v: {sha256, yanked?}}, zip}}}` (CDN-cached 60 s) |
| `GET search?q=` | anyone | ranked matches on name, keywords, description |
| `GET packages/<name>` / `packages/<name>/<version>` | anyone (owner/admin also see pending ones) | metadata, versions, README |
| `GET packages/<name>/<version>/download` | anyone | the zip (counted per version); yanked versions stay downloadable |
| `POST packages/<name>/<version>` | API token | publish (`Content-Type: application/zip`, at most 4 MB) |
| `POST packages/<name>/<version>/yank` / `unyank` | owner or admin | yank (`{reason}`); owners can't undo an admin's yank |
| `POST packages/<name>/report` | signed in | report a package to the admins (5 an hour) |
| `GET me` | anyone | `{signedIn, user, admin, setup}` |
| `GET auth/login?next=` / `GET auth/callback` / `POST auth/logout` | browser | GitHub sign-in |
| `GET tokens` / `POST tokens` / `DELETE tokens/<id>` | signed in (creating needs the website session) | API tokens, at most 10 per account |
| `GET account/packages` | signed in | your packages with every version's status and review reason |
| `GET admin/queue` | admin | pending versions (files, flags, README), reports, audit log |
| `POST admin/packages/<name>/<version>/approve` / `reject` (`{reason}` required) / `remove` | admin | review |
| `POST admin/packages/<name>/remove` / `admin/reports/<id>/dismiss` | admin | remove a package (frees the name) / close a report |

**Review rules.** A new package's first version is `pending` and invisible to
everyone but its owner and the admins until an admin approves it; then the
package is `approved` and later versions from its owner are published at once.
A rejected version's archive is deleted and its owner sees the reason on
`/packages/account`; they keep the name and may upload the same version again.
Versions are immutable (a published or removed version number is never reused).
Yanked versions leave version ranges and "latest" but stay installable when
pinned exactly or locked. Names close to an existing name or a standard library
name (edit distance 1, or equal without dashes) and names starting with `tb-`
are flagged for the reviewer. The three bundled packages (`packages/<name>/`)
are approved packages owned by the system owner `tb`; admins may publish new
versions of them.

**Publishing checks** (server side, and by `tb publish` before it uploads):
names `^[a-z][a-z0-9-]{1,38}[a-z0-9]$` without `--`, not a standard library
name or a reserved word (`RESERVED_WORDS` in `rules.js` and `tbtools/pkg.py`,
kept equal by a test); semantic versions; a zip of at most 4 MB (Vercel
Functions accept bodies up to 4.5 MB) with at most 200 files, 1 MB per file and
10 MB unpacked; no absolute paths, `..`, backslashes, hidden files, symlinks or
other special files, ZIP64 or encryption; `tb.toml` whose name and version match
the upload; an entry `.tb` file; only UTF-8 text files plus README images whose
bytes match their extension; 10 uploads an hour per account.

**Storage.** Upstash Redis (the counters' database), every key prefixed `pkg:`:
`pkg:pkg:<name>` package summary (JSON), `pkg:versions:<name>` hash of version
records (status, sha256, size, publisher, dependencies, flags, reasons),
`pkg:readme:<name>:<version>`, `pkg:owner:<name>` and `pkg:claim:<name>:<version>`
(set with NX, so names and versions can't be taken twice), `pkg:names`,
`pkg:listed`, `pkg:queue` (`name@version`), `pkg:owned:<user>`,
`pkg:downloads` (total per package) and `pkg:dl:<name>` (per version),
`pkg:user:<github id>`, `pkg:session:<hmac>` (expires after 7 days),
`pkg:token:<hmac>` and `pkg:tokens:<user>`, `pkg:reports`, `pkg:audit`,
`pkg:rl:<kind>:<user>:<hour>`. Archives live in the private Vercel Blob store
at `packages/<name>/<version>.zip`, read and written with the Blob REST API
(the same requests `@vercel/blob` makes) and only ever served through the
download route. The bundled packages' archives are embedded in `seed.js` and
are byte-identical to the static `packages/<name>.zip` downloads.

**Security.** Sign-in is GitHub's OAuth web flow with a random `state` and
PKCE (S256), both kept in a short-lived signed cookie, and no scopes (public
profile only); the GitHub access token is used once to read the user id and
login, then dropped. The session is one cookie, `__Host-tb_registry` (HMAC
signed, `HttpOnly; Secure; SameSite=Lax; Path=/`, 7 days), holding a random
session id that Redis maps to the user, so signing out ends it on the server.
It is the site's only cookie and is set only for people who sign in. Writes
made with the cookie must come from this site (`Sec-Fetch-Site` / `Origin`
checks, JSON bodies). API tokens (`tbr_` + 43 random characters) are shown
once and stored only as HMAC-SHA256 hashes; tokens act as you in the API (an
admin.s token can review) but can't make new tokens. Package data is shown on the pages with
`textContent` only, links in READMEs are limited to http(s)/mailto/anchors and
images are not loaded. The site's CSP is unchanged (`connect-src 'self'`).

**Setting it up** (Vercel project `tanisha-code`, Settings, Environment
Variables, Production):

1. `KV_REST_API_URL` / `KV_REST_API_TOKEN` (already there for the counters) and
   `BLOB_READ_WRITE_TOKEN` (a *private* Blob store connected to the project).
2. Create a GitHub OAuth App under the project's own GitHub account or
   organisation, not a personal account: GitHub's consent screen shows the
   app's owner to everyone who signs in. In that account: Settings, Developer
   settings, OAuth Apps, New OAuth App. Homepage URL
   `https://code.tanisha.ceo/packages`, Authorization callback URL
   `https://code.tanisha.ceo/api/registry/auth/callback`; leave "Enable Device
   Flow" off. Generate a client secret, then set `GITHUB_CLIENT_ID` and
   `GITHUB_CLIENT_SECRET`.
3. `REGISTRY_SESSION_SECRET`: at least 32 random characters, for example
   `openssl rand -base64 48`. It signs cookies and hashes tokens, so changing it
   signs everyone out and invalidates every API token.
4. `REGISTRY_ADMINS`: comma-separated numeric GitHub user ids of the reviewers
   (ids never change, unlike user names). Find one with
   `curl -s https://api.github.com/users/<login>` (the `id` field), or sign in
   once and read `user.id` from `/api/registry/me`.
5. Optional: `REGISTRY_SITE_URL` (default: the request's origin) if the site
   moves; the OAuth callback must match it.

Until a variable is set the API says what's missing (`503` with the variable's
name, or `/packages/publish?error=signin-unavailable`) and the public index
still lists the bundled packages. Redeploy after changing variables.

**When packages/ or the standard library changes**, regenerate the seed:
`uv run python scripts/gen_registry_seed.py` (the build refuses to run with a
stale `seed.js`). Tests: `tests/api/registry.test.mjs` (the API, with fake
Redis, Blob and GitHub), `tests/tools/test_registry_cli.py` (the CLI against a
fake registry) and `tests/test_registry_site.py` (the pages).

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
