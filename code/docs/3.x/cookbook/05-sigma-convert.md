# Convert a Sigma rule

Sigma is a shared format for detection rules. Run a Sigma rule on your logs with TB, or convert it to a query for Splunk or Elastic.

## Recipe

```tb
import detect

let rule = detect.sigma("apps/data/sigma/ssh_brute_force.yml")
say(rule.name, "-", rule.level)
say("Splunk:")
say(detect.sigma_to_query(rule, "splunk"))
say("Elastic (ES|QL):")
say(detect.sigma_to_query(rule, "elastic"))
```

## Run every rule in a folder

```tb
import detect
import logs

let rules = detect.sigma_folder("apps/data/sigma")
say(len(rules), "rules")
let records = logs.read("apps/data/auth.log", "auth") + logs.read("apps/data/access.log")
for hit in detect.run(rules, records) {
  say(f"[{hit.level}] {hit.rule} - {hit.by}")
}
```

## Write a rule in TB

You can also write the rule as a TB map. This is the same brute-force rule.

```tb
import detect
import logs

let rule = detect.rule({
  "name": "Many failed SSH logins",
  "level": "high",
  "where": {"action": "failed_login"},
  "count": 10,
  "within": 300,
  "by": "ip",
})
for hit in detect.run([rule], logs.read("apps/data/auth.log", "auth")) {
  say(hit.rule, hit.by, hit.count)
}
```

See also: [the Sigma tutorial](site:guide-sigma.html), [detect reference](site:lib/detect.html).
