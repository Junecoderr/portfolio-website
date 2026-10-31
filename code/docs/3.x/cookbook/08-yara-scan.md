# Scan files with YARA-like rules

YARA rules find malware by the text and bytes in it. TB's `scan` library reads the same kind of rules and scans bytes, files and folders.

## Recipe

```tb
import scan

let rules = scan.rules("""
rule Packed_UPX {
  meta:
    description = "A section name that the UPX packer uses"
  strings:
    $upx = "UPX0"
    $mz = { 4D 5A }
  condition:
    $mz at 0 and $upx
}
""")

for hit in scan.folder("apps/data/samples", rules) {
  say(f"{hit.rule} matched {hit.path}: {hit.meta.description}")
}
```

## Scan text in memory

```tb
import scan

let rules = scan.rules("""
rule Encoded_PowerShell {
  strings:
    $ps = "powershell" nocase
    $enc = "-enc" nocase
  condition:
    all of them
}
""")
let line = "cmd /c PowerShell.exe -NoP -Enc SQBFAFgA"
for hit in scan.bytes(line.bytes(), rules) {
  say(hit.rule, "found", hit.strings.map(s => s.data))
}
```

## Look at a file you do not know

```tb
import scan
import fs

let data = fs.read_bytes("apps/data/samples/tiny.exe")
say(scan.file_type("apps/data/samples/tiny.exe").description)
say("entropy:", scan.entropy(data))
say(scan.strings(data, 4))
```

## How it works

- `scan.rules` reads text strings, hex strings with `??`, `nocase`, `at`, `all of them` and counts.
- `scan.check` finds problems in rules before you use them.
- A scan has limits on time and file size, so one bad rule or huge file cannot stop your tool.

See also: [scan reference](site:lib/scan.html), [Inspect an executable](site:cookbook/inspect-executables.html).
