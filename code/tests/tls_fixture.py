"""A self-signed certificate for localhost (valid for ~100 years), used by the
tls, websocket, server and cert-check tests. Test-only key: it protects nothing."""

import ssl
import tempfile
import os

CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIDTTCCAjWgAwIBAgIUXBXsxOAIPFatGYFHnCdqNlSGD40wDQYJKoZIhvcNAQEL
BQAwJzESMBAGA1UEAwwJbG9jYWxob3N0MREwDwYDVQQKDAhUQiBUZXN0czAgFw0y
NjEwMjYwNzI0MDJaGA8yMTI2MTAwMjA3MjQwMlowJzESMBAGA1UEAwwJbG9jYWxo
b3N0MREwDwYDVQQKDAhUQiBUZXN0czCCASIwDQYJKoZIhvcNAQEBBQADggEPADCC
AQoCggEBAKwS1FI51SklVfq2GVw82cEiq417XmGW7e1nyZ7SdzmNKO2Ie6Maq1WN
8kcLiOvmp7HQ/lGkU7eeSIKlIFVamLLY/JOYIinPWxA6PHd6C3zcx0QODfxUN9SS
cLXtz+3hO6THWJkkk0svJaH97ZQmKtNBeLhrNmdP/7v43o9LmLieCF7Mf3/kyXfV
JSgA7EqsHM3/AHtAF/2mfcUHUssclYDuEx54UbkSPGuoLaBygMhCS73kEafvQ4Kp
9ce3da8fKN83e8MULDw2dSZRyr5Y6etC1GyyLCG5jt1wOEn4eG94AqBOXxdCyD6u
dDAxZ197lf4Ndbn7fjoM7xkYvFOeKbcCAwEAAaNvMG0wHQYDVR0OBBYEFHCzPi+c
fkhSceaJD2GZwjFU/zFvMB8GA1UdIwQYMBaAFHCzPi+cfkhSceaJD2GZwjFU/zFv
MA8GA1UdEwEB/wQFMAMBAf8wGgYDVR0RBBMwEYIJbG9jYWxob3N0hwR/AAABMA0G
CSqGSIb3DQEBCwUAA4IBAQBpJVw8OAEy6wgikAkpXAMyS6kBTZYZAwmb6wypdRbp
zyF+waG0uNZyPdVpPQk4MevQfnidS2zfcyLInufhMLBE4rNoDImNM9aX0e7LscW9
RG/MBjjyBDYVl6kXQCWu87lynFPDZPZnWj+QjYGNSfYUR9xy3ozjU/qtVbU+XxZ5
4gVuGi0rfS8Kz813dtWhONfxq74E8Ybobwltsl7DgbYxmu5qU8KYz6nZVuyivg5m
8NO4jrKVLqZ5n9l2e6cGVrhYAMv7n++gmLb4ig/VrTmmgKVlIUHG7KMRWmsedoo+
L8ndbWsftAGRGVarL4pZ+7W07YkZTklICWAnDkK7tRXh
-----END CERTIFICATE-----
"""

KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCsEtRSOdUpJVX6
thlcPNnBIquNe15hlu3tZ8me0nc5jSjtiHujGqtVjfJHC4jr5qex0P5RpFO3nkiC
pSBVWpiy2PyTmCIpz1sQOjx3egt83MdEDg38VDfUknC17c/t4Tukx1iZJJNLLyWh
/e2UJirTQXi4azZnT/+7+N6PS5i4nghezH9/5Ml31SUoAOxKrBzN/wB7QBf9pn3F
B1LLHJWA7hMeeFG5EjxrqC2gcoDIQku95BGn70OCqfXHt3WvHyjfN3vDFCw8NnUm
Ucq+WOnrQtRssiwhuY7dcDhJ+HhveAKgTl8XQsg+rnQwMWdfe5X+DXW5+346DO8Z
GLxTnim3AgMBAAECggEARBbrV2I1HCP/Asc9/8bAb/FVKxYPQdSkb5XrLWKg9PAL
qvqf2em6tI8HuN8yng5We0F08T9yNS14CeJnif7gf5znTUwc8Khxo1k4BhsQIqeG
XkKlfByjPwV3fjYNWlcO7+RGBct4UX7liFVfxojX12KH74nVLEp2qp4PC2rhkuOn
yHJgw0GAS4ZEscd6FoR7duLKZNOAoHmqkqLz9p882qI3AannQQXCVIpCiI6rDlKK
KVSamn6qq2/w3XCHKlpufaVfHiRSSWp6j8zrQiptQe7Z5gEuld7UGzhFlJ5cuB54
dhfwPtAYo5nlIMe2bnemcFOn/N/qjWwq0s+mEN4egQKBgQDXkquQ6pJxwQdvCsmm
oaGl34mtZAKNA7SEZ/qCrZfSZ/0T3ni8AvlwwCJCZ2b9/ZG3vOn+4SpBfi7sF35K
lHc7VPj84CfdIcaJqW4+r9K8Hjfg6hyOqDYOusS1LDUfeMocHvcbVw0/PBduaCa/
kJqhBnp+SG0n+Vw2/JPf9RLQdwKBgQDMV9HoZbzrt/Z9H/z2QNcxE5KuD7CNZSXW
xTrJ9ao9Zdmsjo6xsCn1dBkiF0g5mpboP8/VXUJipkUdwOmMSob86Mkk/ZoGaVVQ
73k6vlwrzadbvuOiZmr9VhtrI6WpCYW1Gis+brc7zCmeT4LUhk/V5sMHO55as5Gc
grmL9ckAwQKBgEc2yrqa4zn0/JGwTKFhb9cuG3tOOJGBEddTyiv1UNxPQgmYSuZB
TUn0Dt1E738kj0jVfa+XjrAIEVQpBq14klLlZUOUgVbJ256KfEL1Lna6vNbuP/TY
mNMQ2/7F1d7JtmrHx/YeMeAb59mvSm2qRYrAayLgpj64X8BCfE9V1JtNAoGBAMRm
zBwAXRp6kHEnI61UGQDUfTDLZ7gfNcU9gAnJEz07/YG66y+h0FJYqid4FTU4fj7v
xkSfBEVo5hl454CtyRygaWQiU75B03ydDvoFKPX5DhhCWuwmYELqViQd+QWFX3v3
kQTKyVayHnZImcjG50kCKQV0qfknipbRlqENNYqBAoGARANggennGdtAaIV+KmDq
OuSBptCQO52tH8rH8eNiUVqbb3O8hdlCpl7QDiL30jX2wR7+uIaPw1LhgOkGI5aP
GiaSp26j7JifPcmNSWRLq0EOIsA45VQm7GeV7IHsbDklDCSGuHMvAbwpwHgobwPj
PCkfnzL32qVRqwHuX+GRy+Q=
-----END PRIVATE KEY-----
"""


def write_pair(folder=None):
    """Write cert.pem and key.pem into a folder; returns (cert_path, key_path)."""
    folder = folder or tempfile.mkdtemp(prefix="tb-tls-")
    cert = os.path.join(folder, "cert.pem")
    key = os.path.join(folder, "key.pem")
    with open(cert, "w") as f:
        f.write(CERT_PEM)
    with open(key, "w") as f:
        f.write(KEY_PEM)
    return cert, key


def server_context(folder=None):
    cert, key = write_pair(folder)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx
