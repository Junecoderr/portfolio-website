# Triage a Windows event log

You have an `.evtx` file from a Windows computer. Find the failed logons, new accounts, cleared logs and new services in a few lines. TB reads `.evtx` files on any system, also on macOS and Linux.

## Recipe

```tb
import evtx

let records = evtx.read("apps/data/sample.evtx")
let s = evtx.summary(records)
say(f"{s.records} events from {s.first_when} to {s.last_when}")
say("Actions:", s.actions)

let watch = ["user_created", "group_member_added", "audit_log_cleared", "service_installed"]
for r in records.filter(r => r.action in watch) {
  say(f"{r.when}  {r.EventID}  {r.action}  by {r.subject_user} on {r.computer}")
}
```

## Failed logons by account

```tb
import evtx
import logs

let records = evtx.read("apps/data/sample.evtx")
let failed = records.filter(r => r.action == "failed_login")
say(len(failed), "failed logons")
for row in logs.top(failed, "target_user", 5) {
  say("  ", row)
}
```

## How it works

- Every event has the Windows fields (`EventID`, `computer`, `provider`) and the TB fields that every log has (`when`, `user`, `ip`, `action`).
- `action` names what happened in plain words, so you do not need to remember event IDs.
- `evtx.to_json` gives one event as JSON, for a ticket or a SIEM.

See also: [evtx reference](site:lib/evtx.html), [Convert a Sigma rule](site:cookbook/sigma-convert.html).
