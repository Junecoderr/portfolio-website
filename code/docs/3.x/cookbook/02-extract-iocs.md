# Extract IOCs

A phishing email, a report or a chat message has addresses, domains, links and hashes in it. `ioc.extract` finds them all, also when they are defanged like `hxxp://evil[.]example`.

## Recipe

```tb
import fs
import ioc

let email = fs.read("apps/data/phishing_email.txt")
let found = ioc.extract(email)
for kind in ["urls", "domains", "ips", "emails", "sha256", "md5"] {
  if found[kind] {
    say(f"{kind}: {found[kind]}")
  }
}
```

## One row per indicator

`ioc.rows` gives one map per indicator, with its type. That is the shape a CSV file or a SIEM lookup table wants.

```tb
import fs
import ioc
import csv

let found = ioc.extract(fs.read("apps/data/phishing_email.txt"))
let rows = ioc.rows(found)
csv.write("iocs.csv", rows)
say(len(rows), "indicators written to iocs.csv")
say(rows[0])
```

## How it works

- `ioc.extract` refangs text first, so `[.]` and `hxxp` are found.
- Each list has every value once, in the order it first appears.
- `ioc.kind(text)` tells you what one value is: `"ip"`, `"domain"`, `"url"`, `"sha256"` and so on.

```tb
import ioc

for value in ["203.0.113.42", "login-verify.example.net", "44d88612fea8a8f36de82e1278abb02f", "hello"] {
  say(value, "->", ioc.kind(value))
}
```

See also: [Defang indicators](site:cookbook/defang.html), [the phishing tutorial](site:guide-phishing-iocs.html), [ioc reference](site:lib/ioc.html).
