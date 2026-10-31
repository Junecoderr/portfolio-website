# network-monitor

**TB Network Monitor** keeps an eye on the services you run: every few
seconds it tries a TCP connection to each host and port in `targets.json`
(next to this file) and shows whether it answered and how long it took. The
checks run in the background, so the window stays smooth, and a live line
chart draws the latency of every target over the last minutes.

- A **table** with the state of each target (up/down badge colours in the
  summary cards), latency, how many checks ran and the uptime percentage.
- A **slider** sets how often to check; **Pause** stops the checks; type
  `host:port` and press Enter to watch something new.
- **Notify me** sends a desktop notification (`gui.notify`) when a target goes
  down or comes back.
- **Minimize to tray** hides the window behind a tray icon (Windows), a menu
  bar item (macOS) or a small always-on-top chip (Linux). Click it for the
  menu; double-click (Windows, Linux) or click the Dock icon (Mac) to bring
  the window back.

```
tb apps/network-monitor/main.tb                   # targets.json next to the script
tb apps/network-monitor/main.tb my-targets.json   # another list
```

`targets.json` looks like
`{"interval": 5, "timeout": 2, "targets": [{"name": "Web", "host": "127.0.0.1", "port": 8000}]}`.
Only check hosts you own or are allowed to test.

Shows: `net.tcp_check` timed with `time.timestamp`, `win.every` +
`win.run_in_background`, a live line `chart`, `slider`, `badge`, `card`,
`checkbox`, `gui.notify`, `win.tray`, and `win.on_close`.
