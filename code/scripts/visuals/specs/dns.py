# Pictures for the dns library (read with ast.literal_eval; never run).
# Queries need the network, so results are examples.
SPEC = {
    "_default": {"t": "net", "run": False, "port_label": "53", "port_title": "dns", "icon": "globe", "host_title": "DNS server",
                 "roles": {"server": "which DNS server to ask (this computer's if left out)",
                           "options": "timeout, tcp, retries", "type": "A, AAAA, MX, TXT, NS, CNAME, PTR, SOA, SRV or CAA"}},
    "lookup": {"args": {"name": '"example.com"', "type": '"MX"', "server": '"1.1.1.1"'}, "host_param": "server", "extra": ["name", "type"],
               "request": "MX example.com?", "response": "records", "static": '[{"name": "example.com", "type": "MX", "ttl": 3600, "data": "10 mail.example.com"}]',
               "keys": ["type", "ttl", "data"], "roles": {"name": "the name to look up"}},
    "query": {"args": {"name": '"no-such.example"', "type": '"A"', "server": '"8.8.8.8"'}, "host_param": "server", "extra": ["name", "type"],
              "request": "A no-such.example?", "response": "full answer", "static": '{"status": "nxdomain", "answers": [], "authority": [{"type": "SOA"}], "server": "8.8.8.8"}',
              "roles": {"name": "the name to ask about"}},
    "reverse": {"args": {"ip": '"8.8.4.4"', "server": '"1.1.1.1"'}, "host_param": "server", "extra": ["ip"],
                "request": "PTR 4.4.8.8.in-addr.arpa?", "response": "a name", "static": '"dns.google"',
                "roles": {"ip": "the address to name"}},
    "reverse_name": {"t": "flow", "run": True, "args": {"ip": '"8.8.4.4"'},
                     "vary": {"param": "ip", "values": ['"8.8.4.4"', '"192.168.1.10"', '"2001:db8::1"'], "labels": ["8.8.4.4", "192.168.1.10", "2001:db8::1"]},
                     "roles": {"ip": "the address; its parts come out backwards"}},
    "servers": {"t": "flow", "static": '["192.168.1.1", "1.1.1.1"]',
                "nodes": [{"box": "this computer's settings", "icon": "gear", "lines": ["/etc/resolv.conf or", "the Windows registry"]}, {"arrow": True}, {"result": True}]},
    "is_name": {"t": "flow", "run": True, "args": {"x": '"mail.example.com"'},
                "vary": {"param": "x", "values": ['"mail.example.com"', '"bad name!"', '"localhost"'], "labels": ["mail.example.com", "bad name!", "localhost"]},
                "roles": {"x": "any value"}},
}
