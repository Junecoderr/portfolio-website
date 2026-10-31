# Files and data

Security work is mostly reading files: logs, exports, configs and evidence. TB reads and writes the common formats with one line each.

## Read and write text files

`fs.read` gives the whole file as text. `fs.lines` gives a list of lines. `fs.write` makes or replaces a file. `fs.append` adds to the end.

```tb
import fs

fs.write("notes.txt", "first line\n")
fs.append("notes.txt", "second line\n")
say(fs.read("notes.txt"))
say(fs.lines("notes.txt"), fs.exists("notes.txt"), fs.size("notes.txt"))
say(fs.lines("apps/data/auth.log").slice(0, 2))
```

## Folders and paths

`fs.join` builds a path that works on Windows, macOS and Linux. `fs.list` shows what is in a folder.

```tb
import fs

let path = fs.join("apps", "data", "sigma")
say(fs.list(path).sort())
say(fs.name("apps/data/auth.log"), fs.ext("apps/data/auth.log"), fs.parent("apps/data/auth.log"))
```

## CSV and JSON

`csv.read` gives one map per row, keyed by the header. `json.read` gives TB values.

```tb
import csv
import json

let rows = csv.read("apps/data/m365_audit.csv")
say(len(rows), "rows; columns:", rows[0].keys().slice(0, 4))

let events = json.read("apps/data/cloudtrail.json")
say(type(events))
json.write("summary.json", {"rows": len(rows)})
say(json.read("summary.json"))
```

## YAML, TOML and XML

Config files and rule files use YAML and TOML. Some tools export XML.

```tb
import yaml
import toml
import xml

let rule = yaml.read("apps/data/sigma/ssh_brute_force.yml")
say(rule.title, rule.level)

let config = toml.parse("[alerts]\nlevel = \"high\"\nports = [22, 3389]\n")
say(config.alerts.ports)

let scan = xml.parse("<scan><port id=\"22\" state=\"open\"/><port id=\"80\" state=\"closed\"/></scan>")
for p in scan.find_all("port") {
  say(p.attrs.id, p.attrs.state)
}
```

## A small database

`sqlite` keeps data in a database file, or in memory. Use `?` for values. Never build SQL text from data.

```tb
import sqlite

let db = sqlite.open(":memory:")
db.run("CREATE TABLE logins (user TEXT, ip TEXT, ok INTEGER)")
db.execute_many("INSERT INTO logins VALUES (?, ?, ?)", [
  ["alice", "192.0.2.10", 1],
  ["root", "203.0.113.42", 0],
  ["admin", "203.0.113.42", 0],
])
for row in db.all("SELECT ip, COUNT(*) AS failures FROM logins WHERE ok = 0 GROUP BY ip") {
  say(f"{row.ip}: {row.failures} failed logins")
}
db.close()
```

## Dates and times

Logs use many time formats and zones. `datetime` reads them and changes zones. `time` works with plain timestamps (seconds since 1970).

```tb
import datetime
import time

let t = datetime.parse("2026-03-12T09:14:05Z")
say(t.format("%Y-%m-%d %H:%M"), t.weekday)
say(t.to_zone("Asia/Kolkata").format("%H:%M %Z"))
say(t.add({"hours": 2}).diff(t))

let ts = time.parse("2026-03-12 09:30", "%Y-%m-%d %H:%M")
say(time.format(ts + 3600, "%H:%M"))
```

## Bytes and archives

Some files are not text. `fs.read_bytes` gives bytes. `binary` reads numbers out of bytes. `archive` makes and opens zip and tar files. It refuses files that try to escape the target folder.

```tb
import fs
import binary
import archive

let data = fs.read_bytes("apps/data/sample.pcap")
say(len(data), "bytes; magic number:", binary.to_hex(data.slice(0, 4)))
say(binary.sniff(data))

fs.write("evidence.txt", "auth.log excerpt")
archive.zip("evidence.zip", ["evidence.txt"])
for entry in archive.list("evidence.zip") {
  say(entry.name, entry.size)
}
```

## What you learned

- `fs` reads, writes and lists files.
- `csv`, `json`, `yaml`, `toml`, `xml` and `sqlite` handle data formats.
- `datetime` and `time` handle times. `binary` and `archive` handle bytes.

Next, you make your program do many things at the same time.
