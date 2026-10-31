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

## Unreleased

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

## 3.0.0 - 2026-10-26

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

## 2.0.0 - 2026-10-26

TB becomes a real language for desktop apps and security work.

### Added

- Maps, methods, f-strings, modules and `import`.
- `try`, `catch` and `throw` for error handling.
- Twenty standard libraries, including a desktop GUI, logs, detection rules, IOCs, hashing, networking and threat intelligence.
- A Windows installer and a set of ready-made security apps.
