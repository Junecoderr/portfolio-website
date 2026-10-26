# uptime-monitor

Checks a list of websites and prints whether each one is up, its HTTP status
and how long it took to answer.

```
$ tb main.tb
Uptime check, 2026-10-26 10:29:11
SITE                  STATE  STATUS      TIME  NOTE
TB website            UP     200       689 ms
Portfolio             UP     200      1913 ms
All 2 sites are up.
```

## Usage

```
tb main.tb                    # the sites in sites.json (next to main.tb)
tb main.tb my-sites.json      # another list
tb main.tb --every 60         # keep checking every 60 seconds (Ctrl+C to stop)
```

The list is JSON. The sample `sites.json` checks https://code.tanisha.ceo
and https://example.com:

```json
{
  "timeout": 10,
  "slow_ms": 2000,
  "sites": [
    {"name": "TB website", "url": "https://code.tanisha.ceo"},
    {"name": "Example site", "url": "https://example.com"}
  ]
}
```

A plain list of URLs works too: `["https://example.com"]`. `timeout` is in
seconds per site; a site slower than `slow_ms` milliseconds is marked "slow".

The exit code is 0 when every site is up and 1 when any is down, so a
scheduled job (cron, Task Scheduler) can raise an alert.

## What it teaches

- `http.get(url, {"timeout": ...})` and the answer map: `status`, `ok`,
  `elapsed` (milliseconds).
- A 404 or 500 is an *answer*, not an error; a missing site, refused
  connection, timeout or certificate problem is an error you catch with
  `try { } catch err { }` and show with `err.message`.
- Reading a config file with `json.read` and handling both a map and a list.
- Exit codes, and loops with `time.sleep` for a simple monitor.

Monitoring is a daily SOC job: an outage or a sudden slow-down can be the
first sign of an attack (or just a bad deploy).
