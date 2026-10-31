# Enrich IP addresses

A list of addresses means more with context: is the address inside your network, which range is it in, which service did it touch? Add this context before you look anything up online.

## Recipe

```tb
import logs
import net

let ranges = {"192.0.2.0/24": "office", "198.51.100.0/24": "partner VPN", "10.0.0.0/8": "data centre"}

fn where(ip) {
  for cidr, name in ranges {
    if net.in_cidr(ip, cidr) {
      return name
    }
  }
  return "internet"
}

let records = logs.read("apps/data/firewall.log")
let rows = {}
for r in records.filter(r => r.ip) {
  let row = rows.get(r.ip, {"ip": r.ip, "zone": where(r.ip), "hits": 0, "ports": []})
  row.hits += 1
  if r.dst_port and r.dst_port not in row.ports {
    row.ports.push(r.dst_port)
  }
  rows[r.ip] = row
}
for row in rows.values().sort_by(r => -r.hits).slice(0, 5) {
  let services = row.ports.slice(0, 3).map(p => net.common_ports.get(p, str(p)))
  say(f"{row.ip:<16} {row.zone:<12} {row.hits} hits, ports {services}")
}
```

## Address facts

`net.ip_info` tells you the version and the kind of address.

```tb
import net

for ip in ["192.168.1.20", "127.0.0.1", "8.8.8.8", "2001:db8::1"] {
  let info = net.ip_info(ip)
  say(f"{ip}: v{info.version}, private {info.private}, loopback {info.loopback}, global {info.global}")
}
```

## Names and reputation

With the TB app and the network, add the DNS name and the reputation.

```tb norun
import dns
import intel

for ip in ["198.51.100.10", "203.0.113.42"] {
  let name = dns.reverse(ip)
  let rep = intel.greynoise(ip)
  say(ip, name, rep.verdict)
}
```

See also: [net reference](site:lib/net.html), [the ports and reputation tutorial](site:guide-ports-and-reputation.html), [Look up indicators](site:cookbook/threat-intel-lookup.html).
