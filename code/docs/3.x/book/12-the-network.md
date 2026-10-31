# The network

Many security tasks talk to other computers: check a port, read a certificate, ask a threat-intelligence service. TB has libraries for each of these.

Only test computers that you own or that you have permission to test. Most blocks in this chapter use the network, so they need the TB app on your computer.

## Addresses and ranges

The `net` library knows IP addresses and CIDR ranges. These functions work everywhere, also in the playground.

```tb
import net

say(net.is_ip("203.0.113.42"), net.is_private("192.168.1.20"))
say(net.in_cidr("10.1.2.3", "10.0.0.0/8"))
let info = net.cidr_info("192.0.2.0/28")
say(info.first, info.last, info.size)
say(net.common_ports[22], net.common_ports[443])
```

## Ports and names

`net.check_ports` tries ports on a host. `dns.lookup` asks for DNS records.

```tb norun
import net
import dns

say(net.check_ports("127.0.0.1", [22, 80, 443]))
for r in dns.lookup("example.com", "MX") {
  say(r.priority, r.host)
}
say(dns.reverse("192.0.2.1"))
```

## Web requests

`http.get` and `http.post` talk to web servers and APIs. The answer has a `status`, the `text` and, for JSON, `json`. Always set a timeout.

```tb norun
import http

let answer = http.get("https://example.com", {"timeout": 10})
say(answer.status, answer.ok)
say(answer.text.slice(0, 60))
```

## Certificates

`tls.check` reads the certificate of a server and finds problems, for example a certificate that ends soon.

```tb norun
import tls

let c = tls.check("example.com")
say(c.ok, c.days_left, c.issuer.common_name)
for issue in c.issues {
  say("issue:", issue)
}
```

## Threat intelligence

The `intel` library asks VirusTotal, AbuseIPDB, GreyNoise and others about an address or a file hash. The API keys come from environment variables, never from your code.

```tb norun
import intel

if intel.has_key("abuseipdb") {
  let r = intel.abuseipdb("203.0.113.42")
  say(f"{r.source}: {r.verdict} (score {r.score})")
} else {
  say(intel.key_help())
}
```

## Live connections

`websocket.connect` keeps a connection open to send and receive messages. Use it for live feeds.

```tb norun
import websocket

let ws = websocket.connect("wss://echo.example.com/socket")
ws.send({"hello": "soc"})
say(ws.receive(5))
ws.close()
```

## Your own web server

The `server` library makes a small web server. Use it for a dashboard or an API on your own computer. It listens only on this computer unless you say otherwise.

```tb norun
import server

let app = server.create()
let alerts = [{"id": 1, "rule": "SSH brute force", "ip": "203.0.113.42"}]
app.get("/", fn(req) {
  return server.html("<h1>Alerts</h1>")
})
app.get("/alerts", fn(req) {
  return server.json(alerts)
})
app.listen(8000)   # http://127.0.0.1:8000
```

## What you learned

- `net` handles addresses, ranges and ports. `dns` asks for names.
- `http`, `tls` and `websocket` talk to servers. `intel` asks threat-intelligence services.
- `server` makes your own small web server.

Next, you put everything together and build a SOC tool.
