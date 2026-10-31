# Collections

Real data comes in groups: a list of log lines, a map of fields in one event. TB has lists, maps and ranges for this.

## Lists

A list keeps values in order. The first item is at position 0. A negative position counts from the end.

```tb
let ports = [22, 80, 443]
ports.push(8080)
say(ports, len(ports))
say(ports[0], ports[-1], ports[1:3])
say(443 in ports)
```

## Work with a whole list

List methods return a new value. You can chain them.

```tb
let failures = [3, 12, 0, 25, 7]
say(failures.sum(), failures.max(), failures.sort())
say(failures.filter(n => n > 5).map(n => n * 2))
say(failures.sort().reverse().slice(0, 3))
say(["b", "a", "c"].sort().join(" < "))
```

## Maps

A map keeps values by name. The names are keys. Use `.key` or `["key"]` to get a value. `get` gives a default when the key is not there.

```tb
let event = {"user": "root", "ip": "203.0.113.42", "port": 22}
say(event.user, event["ip"])
event.action = "failed_login"
say(event.get("country", "unknown"))
say(event.keys())
for key, value in event {
  say(f"{key} = {value}")
}
```

## Count things

A map is a good counter. This counts failed logins per address.

```tb
let ips = ["203.0.113.42", "198.51.100.7", "203.0.113.42", "203.0.113.42"]
let counts = {}
for ip in ips {
  counts[ip] = counts.get(ip, 0) + 1
}
say(counts)
let worst = counts.keys().sort_by(ip => -counts[ip])[0]
say("Most failures:", worst)
```

## Unpack and spread

`let [a, b] = list` takes items out of a list. `let {name} = map` takes values out of a map. `...` spreads a list or map into a new one.

```tb
let [first, second, ...rest] = [22, 80, 443, 8080]
say(first, second, rest)

let host = {"name": "web01", "ip": "192.0.2.10"}
let {name, ip: address, owner = "nobody"} = host
say(name, address, owner)
say({...host, "owner": "soc"})
```

## Lists and maps as JSON

JSON is the most common way to store and send data. The `json` library turns TB values into JSON text and back.

```tb
import json

let alert = {"rule": "brute force", "count": 12, "ips": ["203.0.113.42"]}
let text = json.stringify(alert)
say(text)
say(json.parse(text).ips[0])
say(json.stringify(alert, true))
```

## What you learned

- Lists keep items in order. Maps keep values by key.
- `filter`, `map`, `sort` and `sum` work on whole lists.
- `json` turns lists and maps into text and back.

Next, you learn what to do when something goes wrong.
