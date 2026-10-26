# ioc-feed

Turns threat intelligence into detections: read a STIX 2.1 bundle (or download
one from a TAXII 2.1 server), make detection rules from its indicators, and
check your logs for them.

## Run it

```
tb apps/ioc-feed/main.tb                                    # sample feed on the sample logs
tb apps/ioc-feed/main.tb feed.json /var/log/auth.log
tb apps/ioc-feed/main.tb --taxii https://intel.example/taxii2/ --collection list
tb apps/ioc-feed/main.tb --taxii https://intel.example/taxii2/ --collection 91a7b528 --added-after 2026-03-01T00:00:00Z auth.log
tb apps/ioc-feed/main.tb --by-type --query splunk           # one rule per IOC type, and its SPL
```

TAXII credentials are read from the environment only - `TAXII_TOKEN`, or
`TAXII_USER` and `TAXII_PASSWORD` - and are never printed.

## What it teaches

- `stix.read(path)` / `stix.taxii(url, auth).objects(collection)` give a
  bundle; `stix.indicators(bundle)` pulls IOCs out of STIX patterns such as
  `[ipv4-addr:value = '203.0.113.42']`. Expired and revoked indicators are
  skipped; patterns that aren't exact (`LIKE`, `MATCHES`) are reported, not
  guessed at.
- `stix.to_detect_rules(bundle)` makes ordinary `detect` rules (IP fields,
  CIDR ranges with `|cidr`, domains, URLs, hashes), so `detect.run()`,
  `detect.sigma_to_query()` and `attack.map()` all work on them.

## On the sample data

`apps/data/intel/sample-feed.stix.json` has five active indicators and one
expired one. Three hit the sample logs: the SSH password-guessing address
203.0.113.42 (high), the web scanner 198.51.100.77, and the 198.51.100.0/28
range seen by the firewall. The feed's ATT&CK references map them to T1110.001
and T1595.002.
