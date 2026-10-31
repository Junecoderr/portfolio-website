# Building a SOC tool

In this chapter you build a triage tool, step by step. It reads an SSH log, finds a brute-force attack, maps it to MITRE ATT&CK, and writes a report that a SIEM and a person can read. Each step adds to the one before it.

## Step 1: read the log

`logs.read` reads a log file and gives one map per line. Every map has the same fields: `when`, `user`, `ip`, `action` and more. You do not write a parser.

```tb
import logs

let records = logs.read("apps/data/auth.log", "auth")
say(len(records), "records")
let failed = records.filter(r => r.action == "failed_login")
say(len(failed), "failed logins")
for row in logs.top(failed, "ip", 3) {
  say(row)
}
```

## Step 2: find the attack

`detect.brute_force` finds many failures from one address in a short time. A Sigma rule does the same thing in a format that other tools share.

```tb
import logs
import detect

let records = logs.read("apps/data/auth.log", "auth")
for alert in detect.brute_force(records) {
  say(f"{alert.rule}: {alert.count} failures from {alert.by}, {alert.first_when} to {alert.last_when}")
}

let rule = detect.sigma("apps/data/sigma/ssh_brute_force.yml")
for hit in detect.run([rule], records) {
  say(f"Sigma: {hit.rule} ({hit.level}) from {hit.by}")
}
```

## Step 3: add context

An alert is more useful with context. `attack` maps the Sigma tags to ATT&CK techniques. `ioc` checks that the address is a real indicator.

```tb
import logs
import detect
import attack
import ioc

let records = logs.read("apps/data/auth.log", "auth")
let rule = detect.sigma("apps/data/sigma/ssh_brute_force.yml")
let alert = detect.run([rule], records)[0]
say("Tags:", attack.tags(alert))
for tactic in attack.map([alert]).tactics {
  say(f"{tactic.name}: {tactic.techniques}")
}
say(alert.by, "is an", ioc.kind(alert.by), "- shared as", ioc.defang(alert.by))
```

## Step 4: write the report

`report` makes tables in text, Markdown, CSV and HTML. Save a hash of the report with it, so you can prove later that nobody changed it.

```tb
import logs
import detect
import report
import hash
import fs

let records = logs.read("apps/data/auth.log", "auth")
let alerts = detect.brute_force(records)
let rows = alerts.map(a => {"ip": a.by, "failures": a.count, "first": a.first_when, "level": a.level})
say(report.table(rows))

let text = "# SSH triage\n\n" + report.markdown(rows)
fs.write("triage.md", text)
say("triage.md sha256:", hash.file_sha256("triage.md").slice(0, 16) + "...")
```

## Step 5: send it on

A SIEM wants events in its own format. `siem` makes CEF, LEEF and syslog lines, and sends them to Splunk, Elastic or Sentinel. A dry run shows what TB would send. `stix` shares the indicators with other teams.

```tb
import logs
import detect
import siem
import stix

let records = logs.read("apps/data/auth.log", "auth")
let alert = detect.brute_force(records)[0]
say(siem.cef(alert))

let hec = siem.splunk("https://splunk.example:8088", "not-a-real-token", {"dry_run": true})
say(hec.send([alert]).body.slice(0, 80) + "...")

let bundle = stix.bundle([alert.by])
say(stix.indicators(bundle)[0].pattern)
```

## Step 6: one program

Put the steps in one file, `triage.tb`, and give it the log file as an input. `os.args` has the inputs after the file name. Now you can run `tb triage.tb /var/log/auth.log` on a real server.

```tb
import os
import logs
import detect
import report

fn triage(path) {
  let records = logs.read(path)
  let alerts = detect.brute_force(records)
  if alerts.is_empty() {
    return "No brute force found."
  }
  let rows = alerts.map(a => {"ip": a.by, "failures": a.count, "level": a.level})
  return report.table(rows)
}

let path = if len(os.args) > 0 then os.args[0] else "apps/data/auth.log"
say(triage(path))
```

The [SSH brute force tutorial](site:guide-ssh-brute-force.html) goes further, and the [SOC cookbook](site:cookbook/index.html) has a recipe for each step.

## What you learned

- `logs` and `detect` turn a log into alerts.
- `attack`, `ioc`, `report` and `hash` add context and make a report you can trust.
- `siem` and `stix` send the result to other tools.

Next, you give a tool a window.
