# ioc-extractor

Pulls indicators of compromise (IOCs) out of any text file - a phishing
email, a threat report, a ticket - and prints them defanged, as a Markdown
table or JSON, ready to paste into a ticket or feed into a block list.

## Run it

```
tb apps/ioc-extractor/main.tb                          # the sample phishing email
tb apps/ioc-extractor/main.tb report.txt               # your own file
tb apps/ioc-extractor/main.tb report.txt --json        # JSON instead of Markdown
tb apps/ioc-extractor/main.tb report.txt --raw         # don't defang
tb apps/ioc-extractor/main.tb report.txt --out iocs.md # also save the output
```

## What it teaches

- `ioc.extract(text)` finds IPv4/IPv6 addresses, domains, URLs, email
  addresses, MD5/SHA-1/SHA-256 hashes and CVE IDs, without repeats, in the
  order they appear. It understands defanged text (`hxxps://`, `[.]`, `(.)`,
  `[@]`) and doesn't mistake file names like `report.pdf` or `tb.py` for
  domains.
- `ioc.defang(value)` makes an IOC safe to share (`203[.]0[.]113[.]55`,
  `hxxp://`), so nobody clicks a malicious link by accident.
- Building output with maps and lists, `report.markdown` and
  `json.stringify`, and simple command-line flags with `os.args`.

## On the sample data

The email in `apps/data/phishing_email.txt` gives 22 IOCs: the sending server
203.0.113.55, the look-alike domains (`paypa1-secure.example.org`,
`account-verify.example.net`), the phishing URLs, the attachment's hashes and
CVE-2023-23397. The decoy file names in the text are correctly ignored, and
the Message-ID header is not mistaken for an email address. IPv6 addresses
are defanged as `2001[:]db8[:]4b[:][:]17`.
