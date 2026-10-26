# pcap-triage

Read a network capture (classic pcap or pcapng, from Wireshark or tcpdump)
and get the analyst's first look: who talked the most, port scans, DNS
lookups (with random-looking DGA names and abused top-level domains flagged),
plain-HTTP requests (downloads of scripts, bare-IP hosts, old browser
user agents), TLS server names (SNI) with regular "beacon" timing, and the
longest conversations.

```
tb apps/pcap-triage/main.tb                                  # the sample capture
tb apps/pcap-triage/main.tb capture.pcapng                   # your own capture
tb apps/pcap-triage/main.tb capture.pcap --html report.html  # also an HTML report
tb apps/pcap-triage/main.tb capture.pcap --save flagged.pcap # just the flagged hosts' packets
```

The sample `apps/data/sample.pcap` is synthetic (made by
`tests/lib/binfixtures.py`) and uses documentation addresses only
(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32).

Shows: `pcap.read`, `pcap.summary`, `pcap.flows`, `pcap.top_talkers`,
`pcap.dns_queries`, `pcap.http_requests`, `pcap.sni`, `pcap.port_scans`,
`pcap.filter` + `pcap.write`, `report.table` / `report.html`.

Limits: TCP streams are not reassembled (an HTTP request or TLS hello split
over several packets is only seen if its first packet holds the headers);
IP fragments are not joined; no decryption.
