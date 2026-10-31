# Look up indicators in threat intelligence

Is this address known to be bad? Check it against a threat-intelligence feed. Partners often share feeds as STIX bundles. TB reads them, and turns them into detection rules.

## Recipe

```tb
import stix
import logs

let feed = stix.read("apps/data/intel/sample-feed.stix.json")
let bad = stix.iocs(feed)
say("The feed has", len(bad.ips), "addresses and", len(bad.domains), "domain(s)")

let records = logs.read("apps/data/auth.log", "auth")
let seen = records.map(r => r.ip).filter(ip => ip).unique()
for ip in seen.filter(ip => ip in bad.ips) {
  say("Known bad address in the log:", ip)
}
```

## Why it is bad

Each indicator in the feed has a name, a confidence and ATT&CK tags.

```tb
import stix

let feed = stix.read("apps/data/intel/sample-feed.stix.json")
for ind in stix.indicators(feed) {
  say(f"{ind.name} (confidence {ind.confidence}): {ind.iocs.map(i => i.value)}")
}
```

## Turn the feed into rules

```tb
import stix
import detect
import logs

let rules = stix.to_detect_rules(stix.read("apps/data/intel/sample-feed.stix.json"))
let records = logs.read("apps/data/auth.log", "auth")
let hits = detect.run(rules, records)
say(len(hits), "hit(s)")
for hit in hits.slice(0, 3) {
  say(hit.rule)
}
```

## Ask an online service

With an API key, `intel` asks VirusTotal, AbuseIPDB, GreyNoise, OTX and others. Put the key in an environment variable, for example `ABUSEIPDB_API_KEY`.

```tb norun
import intel

let r = intel.abuseipdb("203.0.113.42")
say(f"{r.source}: {r.verdict} (score {r.score})")
let v = intel.virustotal_file("44d88612fea8a8f36de82e1278abb02f")
say(f"{v.source}: {v.verdict}")
```

See also: [stix reference](site:lib/stix.html), [intel reference](site:lib/intel.html), [Enrich IP addresses](site:cookbook/enrich-ips.html).
