# file-inspector-gui

**TB File Inspector**, the desktop version of `apps/file-inspector`. Open a
file (or drop one on the window, or pick a sample) and see what it really is,
without running it. A verdict badge and level counts sit at the top; the tabs
show:

* **Overview**: type, size, entropy, hashes, and PE/ELF/Mach-O facts
  (architecture, entry point, compile time, subsystem, libraries it needs,
  imphash, Rich header, who signed it, resources, the hardening report and
  symbol versions)
* **Findings**: suspicious indicators and YARA-style rule matches, most
  serious first; click one for the details
* **Sections**: name, size, entropy and permissions (rwx)
* **Imports**: the functions it gets from DLLs / shared libraries
* **Strings**: every readable string, with a filter box

**Copy SHA-256** puts the hash on the clipboard for a VirusTotal search;
**Save JSON report…** writes everything to a file.

```
tb apps/file-inspector-gui/main.tb                 # empty, dark theme
tb apps/file-inspector-gui/main.tb suspicious.exe  # open a file straight away
tb apps/file-inspector-gui/main.tb --light         # light theme
```

Keyboard: Ctrl+O open (Cmd on a Mac).

Shows: `tabs`, `table` with `on_select`, `badge`, `textarea`, `on_drop`,
`gui.open_file` / `gui.save_file` / `gui.copy`, and sharing code with the CLI
app through `import "../file-inspector/inspect.tb"`.
