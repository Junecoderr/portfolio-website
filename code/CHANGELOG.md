# Changelog

Every TB release, newest first. The website's Releases page
(code.tanisha.ceo/releases) and its feed are built from this file, and the
release workflow uses a version's section as its release notes.

How to write an entry:

- Start a release with `## <version> - <YYYY-MM-DD>`, then one short paragraph
  that sums it up.
- Group changes under `### Added`, `### Changed`, `### Fixed`, `### Removed`,
  `### Breaking` or `### Security`, one `- ` bullet per change. Inline
  `code`, **bold** and [links](https://code.tanisha.ceo/docs) work.
- `## Unreleased` collects changes for the next release. It never appears on
  the website, so you can fill it in as you go and rename it on release day.
- The newest released version must match `__version__` in tb.py (the build
  checks this).
- Add ` (not published)` after the date for a version that was built but
  never offered as a download.

## Unreleased

### Added

- A faster engine: code that runs often is translated into a quicker form, more than twice as fast as TB 3.0 on the benchmark set. `TB_JIT=off` turns it off.
- `tb profile` shows where a program spends its time, per function and per line, with `--html` for a flame graph and a list of the largest values.
- `parallel.loop` runs `async fn` tasks, servers and network calls on one event loop, holding thousands of connections without a thread each. `parallel.group` and `parallel.timeout` cancel work cleanly.
- `parallel.processes` spreads CPU-heavy work across processes, with `parallel.shared_queue` to pass values between them.
- `logs.stream`, `logs.each`, `pcap.each` and `evtx.each` read files of any size with flat memory, and `.gz` and `.zip` files are read directly.
- The `macho` library reads macOS executables; `pe` adds imphash, the Rich header, resources and code-signature details; `elf` adds symbol versions and a hardening report. file-inspector shows all of them.
- `scan` accepts much more of the YARA language: `xor`, `base64`, `wide` and `nocase` regexes, counts and offsets, integer readers, loops and the `pe`, `elf` and `math` modules.
- Sigma correlation rules and `| near` convert for Splunk, KQL and the new ES|QL backend, with field maps for more Sentinel tables.
- Official MITRE ATT&CK data (version 20.0), and new intel providers (AlienVault OTX, GreyNoise, Shodan) and SIEM senders (QRadar, Chronicle).
- Editor support: rename and find references across files, quick fixes, organise imports, `[fmt]` options in `tb.toml`, and debugging from VS Code through `tb debug --dap`.
- Desktop apps accept files dropped on the window and show a tray icon on Windows and macOS; every control can be reached with Tab.
- The playground and Run buttons run in a background worker: output appears as it is printed, Stop works at once, and `ask()` reads answers inline.

### Changed

- `evtx` reads real Windows logs the way Windows shows them: `record_id` is the event's own record ID and empty values are kept. Damaged chunks and records in slack space can be recovered with `{"recover": true}`.
- The legacy ATT&CK tactic name `defense-evasion` covers both of its v20 successors, Stealth and Defense Impairment.

### Security

- Hostile files and rules are bounded: packet captures, PE, Mach-O, EVTX and compressed logs have size and time limits, and YARA refuses patterns that could run for minutes.
- Sigma field names are checked and quoted, so a rule can't add commands to a converted query.
- API keys and tokens are never sent on to another site when a server redirects.

## 3.1.0 - 2026-10-31

Named inputs for standard-library functions, packet-capture stream reassembly with HTTP file extraction, seven more log formats for security teams, and clearer messages across the tools.

### Added

- Standard-library functions take named inputs, like functions you write: `parallel.map(scan, hosts, workers = 8)`, `round(x, digits = 2)`. A misspelled name gets a "Did you mean" hint, and `tb check` and the language server know the names too.
- `pcap.streams` reassembles TCP connections (out-of-order, repeated and missing segments), `pcap.defragment` rebuilds fragmented IPv4 and IPv6 packets, and `pcap.http_exchanges` and `pcap.http_objects` pair HTTP requests with responses and pull out transferred files with their SHA-256 and MD5. The pcap-triage app lists files sent over HTTP.
- `logs.read` recognises Zeek (TSV and JSON), Suricata EVE, AWS CloudTrail, Okta System Log, Microsoft 365 audit logs (JSON and CSV), CEF and LEEF, and gives Sysmon events common field names such as `process`, `command_line` and `hashes`.
- Descriptions for Sysmon events and PowerShell script and module logging (4103, 4104) when reading EVTX files.
- A `globals` command in `tb debug`.
- JSON parse errors carry `data_line` and `data_column`, so a program can say where a config file is broken.

### Changed

- `locals` in `tb debug` shows only the current function's inputs and variables.
- `tb add` prints the short import line, such as `import "tb-tables" as tb_tables`.
- `let a, b = 0, 1` explains how to write it: `let a, b = [0, 1]`.

### Fixed

- Sigma rules converted to KQL use real columns of Microsoft Sentinel's `Syslog` table, reading the user, address and action out of `SyslogMessage`, and filter on the auth facility.

## 3.0.0 - 2026-10-27

The biggest TB release so far: classes, pattern matching, generators and tasks
in the same friendly language, a bytecode virtual machine, a full set of
developer tools and 43 standard libraries. Read [What's new in TB 3.0](https://code.tanisha.ceo/whats-new) for examples you can run.

### Added

- `class` with fields, methods, `static` members, `extends` and `super`, plus `enum` and `match` with value, range, list, map, type and class patterns.
- Default, named and `...rest` arguments, lambdas, destructuring, spread, `const`, and `if ... then ... else` as an expression.
- Whole numbers and fractions kept apart, hex and binary literals, format specs in f-strings, ranges, slices and a `bytes` type.
- Errors with a kind (`catch err: ParseError`), `finally`, `defer`, `assert`, generators with `yield`, and tasks with `async fn` and `await`.
- `export` and `from ... import` for modules.
- A bytecode virtual machine that runs programs faster; the tree-walking engine stays available with `TB_ENGINE=tree`.
- Developer tools: `tb fmt`, `tb check`, `tb debug`, `tb test --coverage`, `tb doc`, `tb build`, `tb lsp` and a VS Code extension.
- Packages: `tb add`, `tb publish` and the [TB Package Index](https://code.tanisha.ceo/packages).
- 23 new libraries, for 43 in all, covering binary formats, packet captures, SIEM queries, threat intelligence, data and networking.
- New desktop and security apps in the [examples gallery](https://code.tanisha.ceo/examples).

### Breaking

- New reserved words (`class`, `match`, `from`, `is`, `const` and others) can't be used as names any more; TB suggests another name.
- `/` always gives an exact fraction; it still displays as before.
- Errors raised by libraries now have specific kinds. See [Moving from 2.x](https://code.tanisha.ceo/migration) for every change and its fix.

## 2.0.1 - 2026-10-26

A documentation release with no language changes.

### Changed

- Sample programs and documentation are neutral and ready for everyone.
- The first program is now `welcome.tb`, a short tour of TB.

## 2.0.0 - 2026-10-26 (not published)

TB becomes a real language for desktop apps and security work. This version was never offered as a download; 2.0.1, released the same day, was the first public 2.x release.

### Added

- Maps, methods, f-strings, modules and `import`.
- `try`, `catch` and `throw` for error handling.
- Twenty standard libraries, including a desktop GUI, logs, detection rules, IOCs, hashing, networking and threat intelligence.
- A Windows installer and a set of ready-made security apps.
