# Pictures for the tls library (read with ast.literal_eval; never run).
SPEC = {
    "_default": {"t": "net", "run": False, "port_title": "https", "icon": "lock", "host_title": "server",
                 "roles": {"host": "the server (or host:port, or an https:// URL)", "port": "the port (443 if left out)",
                           "options": "timeout, server_name (SNI), ca_file"}},
    "certificate": {"args": {"host": '"example.com"', "port": "443"}, "request": "TLS hello", "response": "its certificate",
                    "static": '{"subject": {"common_name": "example.com"}, "issuer": {"organization": "DigiCert Inc"}, "days_left": 74, "expired": false, "version": "TLSv1.3"}',
                    "keys": ["subject", "issuer", "days_left", "expired", "version"]},
    "versions": {"args": {"host": '"example.com"', "port": "443"}, "request": "try 1.0, 1.1, 1.2, 1.3", "response": "accepted?",
                 "static": '{"TLSv1.0": false, "TLSv1.1": false, "TLSv1.2": true, "TLSv1.3": true}'},
    "check": {"args": {"host": '"example.com"', "options": '{"warn_days": 30}'}, "request": "TLS hello", "response": "certificate",
              "extra": ["options"], "static": '{"ok": true, "issues": [], "days_left": 74, "verified": true, "version": "TLSv1.3"}',
              "keys": ["ok", "issues", "days_left", "verified", "version"],
              "roles": {"options": "warn_days (warn when it expires sooner), timeout, server_name"}},
    "parse_pem": {"t": "flow", "args": {"pem": "fs.read(\"cert.pem\")"}, "static": '{"subject": {"common_name": "example.com"}, "issuer": {"common_name": "R11"}, "not_after": "2027-01-09", "days_left": 74, "self_signed": false}',
                  "nodes": [{"param": "pem", "icon": "doc", "lines": ["-----BEGIN CERTIFICATE-----", "MIIF…", "-----END CERTIFICATE-----"]}, {"arrow": True}, {"result": True, "mode": "tree"}],
                  "roles": {"pem": "certificate text that starts with -----BEGIN CERTIFICATE-----"}},
}
