# The TB Programming Language

**TB is a general-purpose programming language for desktop applications,
security operations tooling and everyday automation.** It pairs a small,
readable syntax with a batteries-included standard library and error
messages written for humans.

- Website and documentation: **https://code.tanisha.ceo**
- Language guide: https://code.tanisha.ceo/docs
- Standard library reference: https://code.tanisha.ceo/library
- Try it in the browser: https://code.tanisha.ceo/playground
- Download: https://code.tanisha.ceo/download

```tb
import logs
import detect
import report

let records = logs.read("apps/data/auth.log")
let alerts = detect.brute_force(records)

for alert in alerts {
  say(f"{alert.level:>8}  {alert.count:3} failed logins from {alert.by}")
}
report.save("alerts.html", report.html("SSH brute force", alerts))
```

## Why TB

- **Readable by default.** Blocks in braces, no semicolons, no boilerplate.
  Programs read top to bottom like a checklist.
- **Errors that explain themselves.** Every mistake names the line, says what
  went wrong in plain English and suggests a fix:
  `Oops! On line 3: I don't know what 'nmae' is. Did you mean 'name'?`
- **Batteries included.** 43 standard libraries ship inside the runtime:
  files and data formats, SQLite, HTTP, DNS, TLS, a web server, a native GUI
  toolkit, and a security library that reads logs, PCAPs and Windows EVTX
  files, runs Sigma rules and maps findings to MITRE ATT&CK.
- **A modern language.** Classes and inheritance, pattern matching,
  destructuring, first-class functions and lambdas, generators, async tasks,
  typed errors, modules and packages, optional type annotations.
- **Real tooling.** Formatter, type checker and linter, language server,
  VS Code extension, debugger, test runner with coverage, documentation
  generator, package manager and an app builder.
- **Runs everywhere.** Windows, macOS and Linux, plus the browser through
  WebAssembly. One install, no external dependencies.

## Installation

| Platform | Package |
|---|---|
| Windows 10/11 (x64) | `tb-windows-x64-setup.exe`: per-user installer, no administrator rights. Adds `tb` to `PATH` and registers `.tb` files. A portable `tb-windows-x64.zip` is also available. |
| macOS (Apple silicon) | `tb-macos-arm64.zip` |
| Linux (x64) | `tb-linux-x64.tar.gz` |
| Any OS with Python 3.10+ | `tb-python.zip`: runs from source with your own Python |

All packages are listed at https://code.tanisha.ceo/download with
step-by-step instructions and SHA-256 checksums.

Check the installation:

```sh
tb --version
```

## Getting started

```sh
tb welcome.tb            # a short tour of the language
tb                       # interactive prompt (REPL)
tb hello.tb              # run a program
tb test                  # run every *_test.tb file below the current folder
tb doc                   # list the standard libraries
tb doc http              # reference for one library
```

A first program:

```tb
let name = ask("What's your name? ")
say(f"Hello, {name}!")
```

## A tour of the language

```tb
import fs
import json
import net
import parallel

# Values: numbers, text, bytes, lists, maps, nil, true/false
let ports = [22, 80, 443]
let host = {"name": "web01", "ip": "192.0.2.10", "open": ports}
say(f"{host.name} has {len(host.open)} open ports")

# Functions, defaults, keyword arguments, lambdas
fn connect(host, port = 22, timeout = 5) {
  return net.tcp_check(host, port, timeout)
}
connect("192.0.2.10", port = 2222)
let secure = ports.filter(p => p == 443)

# Classes and inheritance
class Alert {
  fn init(rule, level) { self.rule = rule; self.level = level }
  fn text() { return f"[{self.level}] {self.rule}" }
}
class CriticalAlert extends Alert {
  fn init(rule) { super.init(rule, "critical") }
}

# Pattern matching
let alert = CriticalAlert("ssh brute force")
let action = match alert {
  CriticalAlert(a) => "page the on-call analyst"
  {"level": "high"} => "open a ticket"
  _ => "log it"
}

# Destructuring and spread
let [first, ...rest] = ports
let {name, ip, owner = "unknown"} = host

# Typed errors
try {
  let config = json.read("config.json")
} catch err: NotFoundError {
  say("No config file, using defaults")
} catch err: ParseError {
  say(f"Bad config: {err.message}")
} finally {
  say("ready")
}

# Generators and lazy pipelines
fn lines_matching(path, word) {
  for line in fs.lines(path) { if word in line { yield line } }
}
say(lines_matching("auth.log", "Failed").take(5).to_list())

# Concurrency
let hosts = ["192.0.2.10", "192.0.2.11"]
let results = parallel.map(h => net.tcp_check(h, 443), hosts, 16)          # up to 16 at a time
```

The complete guide is at https://code.tanisha.ceo/docs and the formal
specification is in [`spec/SPEC.md`](spec/SPEC.md).

## Standard library

| Area | Libraries |
|---|---|
| Core | `math` `text` `time` `datetime` `decimal` `random` `re` `os` `binary` |
| Files and data | `fs` `json` `csv` `yaml` `toml` `xml` `sqlite` `archive` `template` |
| Security operations | `logs` `detect` (incl. Sigma) `ioc` `hash` `encode` `crypto` `report` `pcap` `evtx` `pe` `elf` `scan` `attack` `stix` `siem` `intel` |
| Networking | `net` `http` `dns` `tls` `websocket` `server` |
| Concurrency | `parallel` |
| Desktop GUI | `gui` |
| Testing | `test` |

Every function is documented in the reference
(https://code.tanisha.ceo/library) and from the command line with
`tb doc <library>`.

## Tooling

| Command | Purpose |
|---|---|
| `tb file.tb [args]` | Run a program |
| `tb test [dir] [--coverage]` | Run `*_test.tb` files with the `test` library |
| `tb fmt [paths] [--check]` | Format source code in the canonical style |
| `tb check [paths] [--strict]` | Type check and lint |
| `tb doc <library\|file.tb\|dir>` | Show or generate documentation |
| `tb debug file.tb` | Step through a program with breakpoints |
| `tb build app.tb` | Package an application as a standalone folder |
| `tb init` / `tb add` / `tb remove` / `tb update` / `tb packages` | Manage project dependencies (`tb.toml`, `tb.lock`) |
| `tb lsp` | Language server (used by editors) |

**Editor support:** the VS Code extension (`tb-vscode-3.0.0.vsix`, from the
download page) provides syntax highlighting, completion, hover documentation,
diagnostics, go to definition, formatting and run/test commands. Any editor
with Language Server Protocol support can use `tb lsp`.

## Example applications

The [`apps/`](apps) folder contains complete programs, shipped with every
download. Sample data lives in `apps/data/`.

| Category | Applications |
|---|---|
| Desktop | `todo`, `notes`, `calculator`, `care`, `hash-checker`, `ioc-workbench`, `log-triage-gui`, `alerts-dashboard-gui`, `network-monitor`, `file-inspector-gui` |
| Detection and response | `log-triage`, `brute-force-hunter`, `sigma-runner`, `sigma-studio`, `pcap-triage`, `evtx-triage`, `file-inspector`, `ioc-extractor`, `ioc-feed`, `siem-forwarder` |
| Network and intelligence | `ip-reputation`, `port-check`, `uptime-monitor`, `dns-check`, `cert-check` |
| Web | `dashboard` (alerts dashboard served by the `server` library) |
| Concurrency | `parallel-scan` (checks ports on many hosts at once) |

Run one with `tb apps/log-triage/main.tb`.

## Packages

Third-party packages are TB projects with a `tb.toml` manifest:

```sh
tb init
tb add tb-tables
```

```tb
import "tb-tables" as tables
say(tables.table([{"host": "web01", "status": "up"}]))
```

The package index lives at https://code.tanisha.ceo/packages, which also
explains how to publish your own.

## Compatibility and versioning

TB follows semantic versioning. The current release is **3.0.0**. Breaking
changes between major versions are documented with migration steps; see
[`MIGRATION-3.0.md`](MIGRATION-3.0.md) for 2.x to 3.0.

## Implementation

The reference implementation is written in Python and has no third-party
runtime dependencies. Source is compiled to bytecode and executed by a
virtual machine; a tree-walking interpreter remains available with
`TB_ENGINE=tree` for comparison. The same runtime powers the in-browser
playground through Pyodide.

| Path | Contents |
|---|---|
| `tb.py` | Entry point, public API and command line |
| `tbcore/` | Lexer, parser, AST, compiler, virtual machine, runtime, type checker |
| `tblib/` | Standard library |
| `tbtools/` | Formatter, linter, language server, debugger, package manager, builder |
| `editors/vscode/` | VS Code extension |
| `spec/` | Language specification and conformance tests |
| `apps/`, `examples/`, `packages/` | Applications, examples and example packages |
| `tests/` | Test suite |
| `site/`, `scripts/` | Website and its build scripts |
| `packaging/` | Installer and release builds |

Building from source, running the test suite, releasing and deploying the
website are covered in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Known limitations

- Release binaries are not code-signed. Windows SmartScreen and macOS
  Gatekeeper ask for confirmation on first run; the download page explains
  the steps.
- macOS builds target Apple silicon. Intel Macs can use `tb-python.zip`.
- The browser playground cannot open windows, use the network, run
  processes or start threads; those programs need the installed runtime.
- TB is an interpreted language. It is well suited to tools, automation and
  applications, not to number crunching at native speed.

## License

TB is released under the MIT License. See [`LICENSE`](../LICENSE).
