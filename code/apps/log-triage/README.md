# log-triage

Reads log files, runs detection rules over them and prints a table of alerts,
most serious first, with notes an analyst would want (who got in, what an
encoded PowerShell command really says, which ports a scanner touched).
This is the first thing a SOC analyst does with a log: triage.

## Run it

```
tb apps/log-triage/main.tb                                 # all the sample logs in apps/data
tb apps/log-triage/main.tb /var/log/auth.log               # your own log (any format)
tb apps/log-triage/main.tb apps/data/access.log --html triage.html
tb test apps/log-triage                                    # the app's own tests
```

`--html FILE` also writes a self-contained HTML report you can open in a
browser or attach to a ticket.

## What it teaches

- `logs.read(path)` with the `"auto"` format: one call reads Linux auth.log,
  Apache/nginx, firewall (UFW) and Windows Security exports into records with
  the same field names (`time`, `ip`, `user`, `action`, `success`).
- Detection rules as data (`rules.tb`): a rule is a map with `where`,
  `count`, `within` and `by`. "15 404s within 120 seconds by IP" is a scanner.
  `unless` removes false positives (machine accounts ending in `$`).
- The built-in hunts `detect.brute_force` and `detect.port_scan`.
- `report.table`, `report.timeline` and `report.html` to present results.
- `import "rules.tb"` to split a program into files, and `*_test.tb` files
  with `import test` to check detections against known-bad samples
  (`tb test apps/log-triage`). Writing tests for detections is what detection
  engineers do every day.

## On the sample data

It finds an SSH brute force from 203.0.113.42 that succeeded (the `deploy`
account), the backdoor user `sysupdate` it then created, a web scanner and a
password spray in the access log, a port scan in the firewall log, and on the
Windows side a brute force that got in as `backup`, an encoded PowerShell
download cradle, a new admin account and a cleared Security log. The Event
Viewer CSV export adds a smaller brute force from 198.51.100.50 that got in as
`jdoe`.

It exits with 1 if none of the logs could be read and 2 for a wrong option,
so scripts and schedulers can tell when something went wrong.
