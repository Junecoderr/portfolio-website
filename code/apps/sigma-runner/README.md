# sigma-runner

Runs [Sigma](https://sigmahq.io) detection rules - the shared YAML format
detection engineers use to write rules once for any SIEM - against log files.

## Run it

```
tb apps/sigma-runner/main.tb                            # sample rules on the sample logs
tb apps/sigma-runner/main.tb my-rules/ /var/log/auth.log
tb apps/sigma-runner/main.tb rule.yml access.log        # one rule file
```

The first argument is a rules folder (or one `.yml` file); the rest are logs.

## What it teaches

- `detect.sigma(path)` loads a rule: `title`, `level`, `logsource`, and
  `detection` with named selections, field modifiers (`|contains`,
  `|endswith`, `|all`, `|re`...), conditions like `selection and not filter`,
  `all of selection_*`, and `| count() by ip > 10` with `timeframe: 5m`.
  Unsupported Sigma features give a clear error instead of a wrong answer.
- `logsource` matters: a `product: windows` rule only looks at Windows records.
- `try`/`catch` so one broken rule file doesn't stop the others.
- The sample rules in `apps/data/sigma/` are small, readable examples to copy:
  SSH brute force (with a threshold), web path traversal / SQL injection /
  scanner user agents, encoded PowerShell, and new account creation (with a
  filter for machine accounts).

## On the sample data

Four alerts: the SSH brute force from 203.0.113.42, 21 web attack requests from
198.51.100.77, the encoded PowerShell command and the new `helpdesk2` account.
