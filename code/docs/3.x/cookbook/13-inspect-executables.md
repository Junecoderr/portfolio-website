# Inspect an executable

Before you run or upload a strange file, look inside it. TB reads Windows (PE), Linux (ELF) and macOS (Mach-O) programs. It does not run them.

## Recipe

```tb
import binary
import pe
import elf
import macho

for name in ["tiny.exe", "tiny.elf", "tiny.macho"] {
  let path = "apps/data/samples/" + name
  let kind = binary.file_type(path).type
  let summary = match kind {
    "pe" => pe.summary(path)
    "elf" => elf.summary(path)
    "macho" => macho.summary(path)
    _ => "not a program"
  }
  say(f"{name}: {summary}")
}
```

## Warning signs

`indicators` lists what looks bad: a packer, a section that can be written and run, no signature.

```tb
import pe

let info = pe.parse("apps/data/samples/tiny.exe")
say(f"{info.format} for {info.architecture}, built {info.compiled}")
for s in info.sections {
  say(f"  section {s.name}: {s.size} bytes, entropy {s.entropy}")
}
for hit in pe.indicators(info) {
  say(f"  [{hit.level}] {hit.detail}")
}
```

## Linux and macOS programs

```tb
import elf
import macho

let e = elf.parse("apps/data/samples/tiny.elf")
say(e.type, e.architecture, "entry", e.entry_hex, "needs", elf.needed("apps/data/samples/tiny.elf"))
let m = macho.parse("apps/data/samples/tiny.macho")
say(m.type, m.architecture, "PIE", m.is_pie)
for hit in macho.indicators(m) {
  say(f"  [{hit.level}] {hit.detail}")
}
```

## How it works

- The samples in `apps/data/samples/` are tiny programs made for these docs. They do nothing.
- A damaged file gives a clear error, not a crash. Catch it with `catch err: ParseError`.
- Hash the file too: see [Hash files](site:cookbook/hash-files.html).

See also: [pe](site:lib/pe.html), [elf](site:lib/elf.html) and [macho](site:lib/macho.html) reference, [Scan files with YARA-like rules](site:cookbook/yara-scan.html).
