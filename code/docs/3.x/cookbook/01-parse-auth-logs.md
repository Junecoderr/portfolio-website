# Parse auth logs

You have a Linux `auth.log` and want to know who logged in, who failed, and from where. `logs.read` turns each line into a map with the same fields for every log format.

## Recipe

```tb
import logs

let records = logs.read("apps/data/auth.log", "auth")
let failed = records.filter(r => r.action == "failed_login")
let ok = records.filter(r => r.action == "login")

say(f"{len(records)} lines, {len(failed)} failed logins, {len(ok)} good logins")
say("Top failing addresses:")
for row in logs.top(failed, "ip", 3) {
  say("  ", row)
}
say("Users that the attackers tried:", failed.map(r => r.user).unique().slice(0, 6))
for r in ok {
  say(f"Login: {r.user} from {r.ip} at {r.when}")
}
```

## How it works

- The second input of `logs.read` names the format. Leave it out and TB guesses it from the first lines.
- Every record has `when`, `host`, `program`, `user`, `ip`, `action` and `raw` (the original line).
- `logs.top` counts the values of one field and gives the most common first.

## Big files

For a log of many gigabytes, use `logs.each`. It reads one line at a time and does not keep the file in memory.

```tb
import logs

let counts = {}
logs.each("apps/data/auth.log", fn(r) {
  let action = r.action or "other"
  counts[action] = counts.get(action, 0) + 1
}, "auth")
say(counts)
```

See also: [logs reference](site:lib/logs.html), [Timeline from logs](site:cookbook/timeline-from-logs.html).
