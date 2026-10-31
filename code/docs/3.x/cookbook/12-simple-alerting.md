# Simple alerting

Run your rules on a log, and send an alert for each hit to your SIEM or a chat channel. Start with a dry run: TB shows what it would send, and sends nothing.

## Recipe

```tb
import logs
import detect
import siem
import attack

let rules = detect.sigma_folder("apps/data/sigma")
let records = logs.read("apps/data/auth.log", "auth") + logs.read("apps/data/windows_security.json")
let alerts = detect.run(rules, records)
for alert in alerts {
  say(f"[{alert.level}] {alert.rule} {attack.tags(alert)}")
  let line = siem.syslog_line(alert, {"host": "tb-alerts", "app": "tb", "time": alert.first})
  say("   " + line.slice(0, 70) + "...")
}

let hec = siem.splunk("https://splunk.example:8088", "not-a-real-token", {"dry_run": true})
let preview = hec.send(alerts)
say("Would send", len(alerts), "alert(s) to", preview.url)
```

## Only new alerts

A tool that runs every five minutes must not send the same alert again. Keep the alerts you sent in a file.

```tb
import json
import fs
import logs
import detect

fn key(alert) {
  return f"{alert.rule}|{alert.by}|{alert.first}"
}

let sent = if fs.exists("sent.json") then json.read("sent.json") else []
let alerts = detect.brute_force(logs.read("apps/data/auth.log", "auth"))
let fresh = alerts.filter(a => key(a) not in sent)
for a in fresh {
  say("NEW:", a.rule, a.by)
  sent.push(key(a))
}
json.write("sent.json", sent)
let again = detect.brute_force(logs.read("apps/data/auth.log", "auth")).filter(a => key(a) not in sent)
say("Second run finds", len(again), "new alert(s)")
```

## Send for real

Remove `dry_run` and give a real token from an environment variable. Run the program on a timer with cron or the Windows Task Scheduler.

```tb norun
import siem
import os

let hec = siem.splunk("https://splunk.example:8088", os.env("SPLUNK_HEC_TOKEN"))
hec.send([{"rule": "Brute force", "level": "high", "by": "203.0.113.42"}])
```

See also: [siem reference](site:lib/siem.html), [attack reference](site:lib/attack.html), [Convert a Sigma rule](site:cookbook/sigma-convert.html).
