# dashboard

A local web dashboard of the alerts that [log-triage](../log-triage/) finds.
It reads log files, runs log-triage's detection rules on them, keeps the
alerts in a SQLite database and serves a page plus a small JSON API.
Built with the `server`, `sqlite` and `template` libraries.

```
$ tb main.tb
auth.log: 101 records, 4 new alerts
access.log: 114 records, 4 new alerts
firewall.log: 39 records, 1 new alerts
windows_security.json: 31 records, 6 new alerts
Alerts dashboard listening on http://127.0.0.1:8000 (Ctrl+C to stop)
```

Open http://127.0.0.1:8000 in a browser. The cards filter by level; the
"Mark handled" button records that the analyst (Tanisha in the sample) has
dealt with an alert.

## Usage

```
tb main.tb                              # the sample logs in apps/data
tb main.tb /var/log/auth.log access.log # your own logs
tb main.tb --port 9000                  # another port (0 picks a free one)
tb main.tb --db alerts.db               # keep alerts in a file between runs
```

Loading the same log again into the same `--db` adds no duplicates.

## API

| Request | Answer |
| --- | --- |
| `GET /api/alerts` | every alert, most serious first; filter with `?level=high`, `?file=auth.log`, `?open=true` |
| `GET /api/alerts/{id}` | one alert (404 if there is none) |
| `POST /api/alerts/{id}/ack` | mark it handled; body `{"by": "Tanisha"}` (optional) |
| `GET /api/summary` | `{"total", "open", "levels": {...}, "files": [...]}` |

## Safety

The server listens on 127.0.0.1, so only this computer can reach it. To
open it to your network on purpose, add `--host 0.0.0.0`. Text from the logs
is HTML-escaped by the template, so a log line can't inject script into the
page, and `/assets` can't serve files outside its folder.
