# ip-reputation

Looks up IP addresses, domains, URLs and file hashes in threat-intelligence
services and tells you whether they are known to be bad: the first thing a
SOC analyst does with an indicator from an alert.

```
$ tb main.tb 185.220.101.1 44d88612fea8a8f36de82e1278abb02f
185.220.101.1  (ip)
  abuseipdb   malicious   score 100   1234 reports, DE, Example Hosting
  virustotal  malicious   score  12   11 of 94 engines flagged it
44d88612fea8a8f36de82e1278abb02f  (hash)
  virustotal  malicious   score  88   62 of 70 engines flagged it

Summary (worst verdict per indicator):
  malicious   185.220.101.1
  malicious   44d88612fea8a8f36de82e1278abb02f
```

(Example output; real numbers change every day.)

## Usage

```
tb main.tb INDICATOR [INDICATOR ...]
tb main.tb --file indicators.txt      # one per line, # starts a comment
```

Defanged indicators are fine: `hxxp://evil[.]example` and `1.2.3[.]4` are
turned back into normal text before the lookup. Private addresses
(192.168.x.x, 10.x.x.x, ...) are skipped, since no one outside your network
knows them.

## API keys

The lookups need free API keys, read from environment variables:

| Service | Variable | Used for | Get a key |
|---|---|---|---|
| VirusTotal | `VT_API_KEY` | IPs, domains, URLs, hashes | https://www.virustotal.com/gui/my-apikey |
| AbuseIPDB | `ABUSEIPDB_API_KEY` | IPs | https://www.abuseipdb.com/account/api |
| URLhaus | `URLHAUS_API_KEY` (optional) | URLs | https://auth.abuse.ch/ |

```
export VT_API_KEY="your-key"           # mac / linux
setx VT_API_KEY "your-key"             # windows, then open a new terminal
```

Without any key the app explains this and stops. Keys are never printed.
Free keys allow only a few lookups per minute; when you hit the limit the
app says so and carries on with the next lookup.

## What it teaches

- The `intel` library: every lookup gives the same shape of map
  (`source`, `verdict`, `score`, `details`), so results from different
  services can be compared and combined.
- Telling indicator types apart with `net.is_ip` and `re.test`.
- Handling errors per lookup with `try { } catch err { }`, so one failed or
  rate-limited lookup doesn't stop the whole report.
- Keeping secrets in environment variables instead of in code.

Use it for defence: investigating indicators from your own alerts and logs.
