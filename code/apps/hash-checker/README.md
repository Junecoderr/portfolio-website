# hash-checker

Is this download really the file you think it is? Pick a file (or paste its
path and press Enter) and Hash Checker shows its size and its MD5, SHA-1 and
SHA-256, each with a **Copy** button. Paste the hash the download page or the
threat report gave you: a green tick means it matches, a red cross means it
doesn't (and you shouldn't open it). The comparison uses `hash.compare`, so
case and stray spaces don't matter.

**Look up on VirusTotal** sends only the SHA-256 (never the file). It needs a
free API key in the `VT_API_KEY` environment variable; without one the button
explains how to get and set it.

```
tb apps/hash-checker/main.tb
tb apps/hash-checker/main.tb path/to/file.exe    # hash a file straight away
```

Shows: `hash.file`, `hash.compare`, `intel.virustotal_file` / `intel.has_key`,
`gui.open_file`, `gui.copy` / `gui.paste`, labels that change colour
(`label.style("success")` / `"danger"`).
