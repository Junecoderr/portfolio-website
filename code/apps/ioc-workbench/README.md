# ioc-workbench

Paste an email, an alert or a threat report (or open a file; it starts with
the sample phishing email in `apps/data`) and the IOC Workbench lists every
indicator of compromise in it: IP addresses, domains, URLs, email addresses,
MD5/SHA-1/SHA-256 hashes and CVE IDs, with a count per type. Defanged
indicators (`hxxp://`, `1.2.3[.]4`, `[@]`) are found too.

- **Defang text** / **Refang text** rewrite the text itself, so it is safe to
  paste into a chat or a ticket (or usable again).
- **Copy as Markdown**, **Copy as JSON** and **Copy defanged list** put the
  indicators on the clipboard; double-click one to copy just that one.
- **Save…** writes them to a `.md`, `.json`, `.csv` or `.txt` file.

```
tb apps/ioc-workbench/main.tb
tb apps/ioc-workbench/main.tb alert.txt
```

Shows: `ioc.extract`, `ioc.defang` / `ioc.refang`, `report.markdown` /
`report.csv`, `json.stringify`, `gui.copy` / `gui.paste`, two panes side by
side with `row({"expand": true})` and `column({"expand": true})`.
