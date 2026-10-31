# Pull files out of a packet capture

A packet capture shows that a computer downloaded something over plain HTTP. Get the file back, with its hash, to scan it or look it up.

## Recipe

```tb
import pcap
import fs

let packets = pcap.read("apps/data/sample.pcap")
let files = pcap.http_objects(packets)
fs.mkdir("carved")
for f in files {
  fs.write_bytes(fs.join("carved", f.name), f.data)
  say(f"{f.name}: {f.size} bytes from {f.url}")
  say(f"   sha256 {f.sha256}")
}
say(fs.list("carved").sort())
```

## The requests around it

```tb
import pcap

let packets = pcap.read("apps/data/sample.pcap")
for r in pcap.http_requests(packets) {
  say(f"{r.when} {r.src} {r.method} {r.url}")
}
say(pcap.summary(packets).packets, "packets in the capture")
```

## How it works

- `pcap.http_objects` puts the TCP streams back together, then reads the HTTP answers. It undoes gzip and chunked transfer.
- Each file has `name`, `url`, `size`, `data` (the bytes) and its `sha256` and `md5`.
- TB reads pcap and pcapng files. Only HTTP without TLS can be carved: HTTPS is encrypted.

Carved files can be malware. Keep them in a folder that no program opens on its own. Scan them with [a YARA-like rule](site:cookbook/yara-scan.html).

See also: [pcap reference](site:lib/pcap.html).
