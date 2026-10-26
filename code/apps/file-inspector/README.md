# file-inspector

What is this file, really? Looks inside files **without running them**:

* the real type, from the first bytes, not the name (`scan.file_type`)
* size, MD5 / SHA-1 / SHA-256 and entropy (near 8 means packed or encrypted)
* Windows PE and Linux ELF details: architecture, compile time, entry point,
  sections with entropy and permissions, imports and exports, signed or not
* suspicious indicators (writable+executable sections, packer section names,
  injection imports, odd entry points, cut-off files...)
* matches of the YARA-style rules in `rules.yar` (edit them, or pass your own)
* interesting strings: URLs, IPs, emails, paths, registry keys, DLLs, commands

```
tb apps/file-inspector/main.tb                      # an overview of apps/data
tb apps/file-inspector/main.tb suspicious.exe       # one file in detail
tb apps/file-inspector/main.tb ~/Downloads          # every file in a folder
tb apps/file-inspector/main.tb x.bin --rules my.yar --strings --json out.json
```

The verdict ("suspicious", "worth a look", "nothing found") is a hint for a
person, not proof either way. `inspect.tb` holds the engine; the GUI variant
`apps/file-inspector-gui` uses the same file.

Shows: `scan.file_type` / `scan.entropy` / `scan.strings` /
`scan.interesting` / `scan.load` / `scan.file`, `pe.parse`, `elf.parse`,
`hash.file`, importing another TB file (`import "inspect.tb"`).
