# sigma-studio

A small workbench for [Sigma](https://sigmahq.io) rules: check them, turn them
into queries for your SIEM, and run them on log files.

## Run it

```
tb apps/sigma-studio/main.tb                                  # tour: validate + convert + run the samples
tb apps/sigma-studio/main.tb validate my-rules/               # is every rule readable? what does it read?
tb apps/sigma-studio/main.tb convert rule.yml --to kql        # splunk, elastic, esql, kql or all (default)
tb apps/sigma-studio/main.tb run my-rules/ auth.log access.log
```

## What it teaches

- `detect.sigma_all(path)` loads every rule in a file, including Sigma
  correlation rules (`correlation: type: event_count / value_count /
  temporal / temporal_ordered`).
- `detect.sigma_to_query(rule, backend)` writes SPL for Splunk, a Lucene
  query string for Elasticsearch / Kibana, or KQL for Microsoft Sentinel.
  The rule's `logsource` picks the source type, index filter or table, and
  field names are mapped (`EventID` -> `EventCode` in Splunk, `Image` ->
  `FolderPath` in Defender tables...). Aggregations become `| stats` /
  `| summarize`; Lucene can't count, so that backend explains why it refuses.
- `detect.logsource(map)` says which record formats a rule reads.
- `attack.tags(rule)` and `attack.technique(id)` name the ATT&CK techniques
  in a rule's tags; `attack.map(alerts)` groups alerts by tactic.

## On the sample data

All four sample rules validate. 11 queries are written (the SSH brute-force
rule counts, so it has no Lucene version). Running them finds the SSH brute
force from 203.0.113.42 (22 failed logins), 21 web attack requests, the
encoded PowerShell command and the new local account, covering Initial
Access, Execution, Persistence and Credential Access.
