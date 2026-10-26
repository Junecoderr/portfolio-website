# cert-check

Checks the TLS certificates of servers you run: who issued them, when they
expire, whether this computer trusts them and whether they are for the name
you connected to. Uses TB's `tls` library.

```
$ tb main.tb example.com
example.com:443  OK
  subject   example.com
  issuer    DigiCert Global G3 TLS ECC SHA384 2020 CA1
  expires   2026-01-15 23:59:59 (81 days left)
  trusted   true, name matches true, self-signed false
  protocol  TLSv1.3 TLS_AES_256_GCM_SHA384

1 of 1 servers OK.
```

## Usage

```
tb main.tb HOST[:PORT] ...                 # port 443 unless given; https:// URLs work too
tb main.tb --file hosts.txt                # one host per line, # for comments
tb main.tb example.com --warn 21           # flag certificates expiring within 21 days (default 30)
tb main.tb example.com --versions          # also list the TLS versions the server accepts
tb main.tb intranet:8443 --ca-file ca.pem  # trust your own certificate authority
tb main.tb example.com --json
```

The exit code is 1 when any server has an issue (expired, expiring soon,
untrusted, wrong name, old TLS version, or unreachable), so it works as a
scheduled check. Only check servers you run or have permission to test.
