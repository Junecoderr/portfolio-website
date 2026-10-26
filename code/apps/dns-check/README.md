# dns-check

Looks up a domain's DNS records and checks its basic hygiene, using TB's
`dns` library (a DNS client written in TB's standard library; no `dig`
needed).

```
$ tb main.tb example.com
DNS check for example.com

A     93.184.215.14  (ttl 300)
MX    0 .  (ttl 86400)
NS    a.iana-servers.net  (ttl 86400)
...
[ok] Name servers  2 name server(s); two or more keep the domain up if one fails
[ok] SPF           present and ends in -all/~all
[!!] CAA           no CAA: any certificate authority may issue for this domain
...
5 of 7 checks passed.
```

## Usage

```
tb main.tb DOMAIN                 # ask this computer's DNS servers
tb main.tb DOMAIN --server 1.1.1.1
tb main.tb DOMAIN --json          # everything as JSON
```

## Checks

| Check | Passes when |
| --- | --- |
| Name servers | two or more NS records |
| Addresses | at least one A or AAAA record |
| Mail servers | at least one MX record |
| SPF | exactly one `v=spf1` TXT record, ending in `-all` or `~all` |
| DMARC | a `v=DMARC1` record at `_dmarc.DOMAIN` with a policy other than `p=none` |
| CAA | at least one CAA record |
| Reverse DNS | every address has a PTR name |

The exit code is 1 when the domain doesn't exist, 2 for a usage mistake.
