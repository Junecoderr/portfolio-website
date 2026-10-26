# siem-forwarder

Reads a log (or keeps watching one) and forwards its records - or only the
alerts found in it - to a SIEM as CEF, LEEF, syslog, Splunk HEC,
Elasticsearch bulk or Microsoft Sentinel.

**Dry run is the default**: it prints exactly what would be sent. Add `--send`
to really send, and only send to systems you run.

## Run it

```
tb apps/siem-forwarder/main.tb                                  # sample auth.log as CEF, dry run
tb apps/siem-forwarder/main.tb auth.log --format leef --alerts  # only alerts
tb apps/siem-forwarder/main.tb auth.log --format syslog --send --to 127.0.0.1:5514 [--tcp]
tb apps/siem-forwarder/main.tb auth.log --format hec --url https://splunk.example:8088 --send
tb apps/siem-forwarder/main.tb /var/log/auth.log --follow       # tail: forward new lines as they arrive
```

Secrets come from the environment and are never printed: `SPLUNK_HEC_TOKEN`
(hec), `ELASTIC_API_KEY` (elastic), `SENTINEL_WORKSPACE_ID` and `SENTINEL_KEY`
(sentinel). `--limit N` caps the first batch (default 20, `0` for all);
`--polls N` stops `--follow` after N one-second checks.

## What it teaches

- `siem.cef`, `siem.leef`, `siem.syslog_line` format any map; alerts from
  `detect.run()` get a proper name, severity and source fields.
- `siem.send_syslog`, `siem.splunk(...).send`, `siem.elastic(...).index` and
  `siem.sentinel(...).send` all take `{"dry_run": true}` and then give back the
  exact payload (with the credential hidden).
- `logs.parse_line` turns each new line into a record while tailing.

## On the sample data

With `--alerts` the sample auth.log gives two alerts (the Sigma SSH
brute-force rule and `detect.brute_force`), both about 203.0.113.42.
