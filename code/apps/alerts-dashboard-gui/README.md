# alerts-dashboard-gui

**TB Alerts Dashboard**, a SOC-style overview of one or more logs. Open logs
(or add the samples from `apps/data`); each one is read and triaged in the
background with the detection rules from `apps/log-triage/rules.tb`, so the
window never freezes on a big file. The dashboard then shows the alerts three
ways:

- **Overview**: summary cards (records, alerts, critical, high), coloured
  badges per level, and charts of alerts by level, by rule and by the busiest
  sources (hover a bar or slice for the number).
- **Alerts**: a tree grouped by level, then rule, then who did it, next to a
  details pane with the accounts tried, ports probed, "then logged in"
  escalations and sample raw lines.
- **Timeline**: records and alerts per hour as a line chart, plus the busiest
  hours as a table.
- **Sources**: the open logs (double-click one to close it).

**Export HTML report…** saves a self-contained page signed by the analyst
(`--analyst NAME`, default Tanisha).

```
tb apps/alerts-dashboard-gui/main.tb                           # the sample auth.log, dark theme
tb apps/alerts-dashboard-gui/main.tb /var/log/auth.log web.log # your own logs
tb apps/alerts-dashboard-gui/main.tb --light --analyst Tanisha
```

Keyboard: Ctrl+O open, Ctrl+E export (Cmd on a Mac). You can also drop log
files on the window (Windows and macOS; Linux needs the tkdnd Tk extension),
on the app's Dock icon on a Mac, or paste file paths into the window.

Shows: `tabs`, `card`, `badge`, `tree`, `splitter`, bar/pie/line `chart`s
with `.update()`, `win.run_in_background` with `on_done`/`on_error`,
`win.on_drop`, `logs` + `detect` rules shared with the command-line
`log-triage`, and `report.html`.
