# Defang indicators

Before you paste a bad link into a ticket or a chat, defang it. Then nobody can click it by mistake, and mail filters do not block your message. Refang it again when a tool needs the real value.

## Recipe

```tb
import ioc

let note = "Beacon to http://evil.example.com/gate.php from 203.0.113.42, mail from bad@phish.example"
let safe = ioc.defang(note)
say(safe)
say(ioc.refang(safe) == note)
```

## Defang a whole file

```tb
import fs
import ioc

let email = fs.read("apps/data/phishing_email.txt")
fs.write("phishing_email.defanged.txt", ioc.defang(email))
let first = fs.lines("phishing_email.defanged.txt").filter(l => "[.]" in l)[0]
say(first)
```

## How it works

- `ioc.defang` changes `http` to `hxxp`, `.` to `[.]` in addresses and domains, and `@` to `[@]` in email addresses.
- Normal words stay the same, so the text is still easy to read.
- `ioc.refang` undoes all of this. `ioc.extract` refangs for you.

See also: [Extract IOCs](site:cookbook/extract-iocs.html), [ioc reference](site:lib/ioc.html).
