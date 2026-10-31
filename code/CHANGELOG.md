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
