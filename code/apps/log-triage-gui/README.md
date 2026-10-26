# log-triage-gui

**TB Log Triage**, the desktop version of `apps/log-triage`. Open a log (it
starts with the sample `apps/data/auth.log`) and TB works out the format
(syslog, auth.log, Apache/nginx, JSON lines, CSV, Windows Security exports),
runs the detection rules from `apps/log-triage/rules.tb` (brute force with
"then logged in" escalation, port scans, web attacks and scanners, new
accounts and group changes, cleared logs, encoded PowerShell...) and lists the
alerts, most serious first.

The **Alerts** tab lists them: click an alert to see its details: who, how
many, when, the accounts tried or ports probed, decoded PowerShell, and sample
raw log lines. The **Overview** tab draws the alerts by level and by rule and
the log's records per hour (hover a bar or point for the number). The status
bar shows how many records and what time span the log covers.
**Export HTML report…** saves a self-contained HTML page (`report.html`) to
share with your team.

```
tb apps/log-triage-gui/main.tb                    # the sample auth.log, dark theme
tb apps/log-triage-gui/main.tb /var/log/auth.log  # your own log
tb apps/log-triage-gui/main.tb --light            # light theme
```

Keyboard: Ctrl+O open, Ctrl+E export (Cmd on a Mac).

Shows: `logs.sniff` / `logs.read`, `detect` rules imported from another TB
file (`import "../log-triage/rules.tb" as rules`), `tabs`, bar/pie/line
`chart`s with `.update()`, a `table` with `on_select`, a read-only `textarea`,
`report.html` + `report.save`, `gui.save_file`, menus and shortcuts, and the
dark theme.
