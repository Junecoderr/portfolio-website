# Build a timeline from logs

During an incident you need one timeline from many sources: the SSH log, the web server, the firewall and Windows. TB reads each format, and every record gets the same `time` field. Sort by it and you have the timeline.

## Recipe

```tb
import logs
import evtx
import report

let records = logs.read("apps/data/auth.log", "auth")
  + logs.read("apps/data/access.log")
  + logs.read("apps/data/firewall.log")
  + evtx.read("apps/data/sample.evtx")
let around = records.filter(r => r.get("ip") == "203.0.113.42" or r.get("action") in ["user_created", "audit_log_cleared"])
let timeline = around.sort_by(r => r.time)
say(len(timeline), "events")
say(report.timeline(timeline.slice(0, 8)))
```

## Save it for the report

```tb
import logs
import report
import fs

let records = logs.read("apps/data/auth.log", "auth").filter(r => r.action in ["login", "failed_login"])
let rows = records.sort_by(r => r.time).map(r => {"when": r.when, "user": r.user, "ip": r.ip, "action": r.action})
fs.write("timeline.csv", report.csv(rows))
fs.write("timeline.html", report.html("SSH timeline", rows))
say(len(rows), "rows;", fs.lines("timeline.csv")[0])
```

## How it works

- Not every format has every field. `r.get("action")` gives `nil` when a record has no `action`.
- `when` is the local time shown in the log. `when_utc` and `time` are in UTC, so sources in other zones line up.
- `report.timeline` writes one line per event with its time and source.
- `report.html` escapes every value, so a log line cannot change the page.

See also: [report reference](site:lib/report.html), [Parse auth logs](site:cookbook/parse-auth-logs.html).
