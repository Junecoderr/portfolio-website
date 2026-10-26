# evtx-triage

Triage a binary Windows event log (`.evtx`, the files in
`C:\Windows\System32\winevt\Logs`) on any computer, no Windows needed. The
`evtx` library parses the file itself (chunks, records, binary XML templates
and substitutions) and gives the same records `logs` gives for Windows
exports, so the detection rules from `apps/log-triage/rules.tb` run unchanged:
brute force (and "then logged in"), new accounts, group changes, encoded
PowerShell (decoded for you), cleared logs. It adds two checks of its own:
services installed from user-writable folders and Sysmon process starts from
Temp or Downloads.

```
tb apps/evtx-triage/main.tb                                   # the sample log
tb apps/evtx-triage/main.tb Security.evtx                     # your own log
tb apps/evtx-triage/main.tb Security.evtx --html report.html --json events.jsonl
```

It also reports the file's health: version, chunk count, whether the CRC32
checksums match (a mismatch means damage or tampering) and whether the log
was closed cleanly.

The sample `apps/data/sample.evtx` is synthetic (written by
`tests/lib/evtxwriter.py`) with documentation IPs and the sample user
"tanisha".

How faithful is it? The notes at the top of `tblib/evtx.py` list what the
parser handles. The main gap: event message text is not rendered (Windows
builds it from the provider's message DLL, which isn't in the file), so
records carry the description from the `logs` EventID table instead. The
parser is tested against synthetic files, not yet a corpus of real ones.
