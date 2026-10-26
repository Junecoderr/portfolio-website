# brute-force-hunter

Finds password guessing in Linux `auth.log` and Windows Security logs (JSON
from `Get-WinEvent | ConvertTo-Json`, or Event Viewer CSV), ranks the
sources, and says whether any of them then logged in - the moment a brute
force becomes an incident.

## Run it

```
tb apps/brute-force-hunter/main.tb                              # the sample logs
tb apps/brute-force-hunter/main.tb /var/log/auth.log            # your own log(s)
tb apps/brute-force-hunter/main.tb auth.log --attempts 10 --within 300
```

`--attempts N` is how many failures make a brute force (default 5);
`--within S` is the time window in seconds (default 600).

## What it teaches

- `detect.failed_logins(records)` and `detect.brute_force(records, attempts, within)`:
  sliding time windows, grouping by source IP, and `success_after` - a
  successful login from the same IP after the failures.
- One detection working on Linux and Windows because the logs library gives
  both the same fields (`ip`, `user`, `success`): sshd "Failed password" lines
  and Windows event 4625 look alike after parsing.
- Thresholds have blind spots: the "below the threshold" table shows sources
  that failed a few times only. Slow attackers stay quiet on purpose.

## On the sample data

203.0.113.42 (SSH, got in as `deploy`), 203.0.113.99 (Windows, got in as
`backup`) and 198.51.100.50 (Event Viewer CSV, got in as `jdoe`) are reported;
198.51.100.23 failed only 3 times over an hour and shows up below the threshold.
