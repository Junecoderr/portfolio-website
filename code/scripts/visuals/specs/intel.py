# Pictures for the intel library (read with ast.literal_eval; never run).
# Every function calls a threat-intelligence service over the internet, so
# these pictures are illustrations with example answers.
SPEC = {
    "_default": {"t": "net", "run": False, "port_param": None, "host_param": None, "icon": "cloud", "extra": ["key"],
                 "keys": ["source", "verdict", "score", "details"], "modes": {"hash": "lines", "url": "lines", "ip": "lines", "domain": "lines", "service": "lines"},
                 "roles": {"key": "your API key (read from the environment if left out)"}},
    "virustotal_file": {
        "args": {"hash": '"44d88612fea8a8f36de82e1278abb02f"'}, "extra": ["hash", "key"], "host_title": "VirusTotal",
        "host_label": "virustotal.com", "request": "GET /files/<hash>", "response": "engine verdicts",
        "static": '{"source": "virustotal", "indicator": "44d88612fea8a8f36de82e1278abb02f", "verdict": "malicious", "score": 89, "details": {"malicious": 61, "suspicious": 0, "harmless": 0, "name": "eicar.com"}}',
        "roles": {"hash": "an MD5, SHA-1 or SHA-256 (the file is never uploaded)", "key": "your VirusTotal API key"},
    },
    "virustotal_ip": {
        "args": {"ip": '"203.0.113.42"'}, "extra": ["ip", "key"], "host_title": "VirusTotal", "host_label": "virustotal.com",
        "request": "GET /ip_addresses/<ip>", "response": "verdicts, owner",
        "static": '{"source": "virustotal", "indicator": "203.0.113.42", "verdict": "suspicious", "score": 30, "details": {"malicious": 3, "country": "NL", "owner": "Example Hosting BV"}}',
        "roles": {"ip": "the IP address to look up", "key": "your VirusTotal API key"},
    },
    "virustotal_domain": {
        "args": {"domain": '"login-verify.example.net"'}, "extra": ["domain", "key"], "host_title": "VirusTotal", "host_label": "virustotal.com",
        "request": "GET /domains/<domain>", "response": "verdicts, registrar",
        "static": '{"source": "virustotal", "indicator": "login-verify.example.net", "verdict": "malicious", "score": 60, "details": {"malicious": 9, "registrar": "Example Registrar"}}',
        "roles": {"domain": "the domain name", "key": "your VirusTotal API key"},
    },
    "virustotal_url": {
        "args": {"url": '"http://login-verify.example.net/reset"'}, "extra": ["url", "key"], "host_title": "VirusTotal", "host_label": "virustotal.com",
        "request": "GET /urls/<id>", "response": "existing report",
        "static": '{"source": "virustotal", "indicator": "http://login-verify.example.net/reset", "verdict": "malicious", "score": 70, "details": {"malicious": 12}}',
        "roles": {"url": "the web address (it is never submitted for scanning)", "key": "your VirusTotal API key"},
    },
    "abuseipdb": {
        "args": {"ip": '"203.0.113.42"', "days": "90"}, "host_title": "AbuseIPDB", "host_label": "abuseipdb.com",
        "request": "GET /check?ip=...", "response": "reports, score", "extra": ["ip", "key", "days"],
        "static": '{"source": "abuseipdb", "indicator": "203.0.113.42", "verdict": "malicious", "score": 100, "details": {"reports": 2841, "country": "NL", "isp": "Example Hosting BV"}}',
        "roles": {"ip": "the IP address to check", "key": "your AbuseIPDB API key", "days": "how many days of reports (90)"},
    },
    "urlhaus_url": {
        "args": {"url": '"http://198.51.100.14/inv/INV-20817.zip"'}, "extra": ["url", "key"], "host_title": "URLhaus", "host_label": "urlhaus.abuse.ch",
        "request": "POST /url/", "response": "listed or not",
        "static": '{"source": "urlhaus", "indicator": "http://198.51.100.14/inv/INV-20817.zip", "verdict": "malicious", "score": 100, "details": {"listed": true, "status": "online", "threat": "malware_download"}}',
        "roles": {"url": "the web address to check", "key": "your abuse.ch Auth-Key"},
    },
    "otx": {
        "args": {"indicator": '"203.0.113.42"'}, "extra": ["indicator"], "host_title": "AlienVault OTX",
        "host_label": "otx.alienvault.com", "request": "GET /indicators/IPv4/<ip>/general", "response": "pulses, tags",
        "static": '{"source": "otx", "indicator": "203.0.113.42", "verdict": "malicious", "score": 70, "details": {"pulses": 7, "tags": ["ssh", "bruteforce"], "country": "Netherlands"}}',
        "roles": {"indicator": "an IP, domain, URL or file hash (key from OTX_API_KEY)"},
    },
    "greynoise": {
        "args": {"ip": '"203.0.113.42"'}, "extra": ["ip"], "host_title": "GreyNoise", "host_label": "api.greynoise.io",
        "request": "GET /v3/community/<ip>", "response": "noise, classification",
        "static": '{"source": "greynoise", "indicator": "203.0.113.42", "verdict": "malicious", "score": 90, "details": {"noise": true, "riot": false, "classification": "malicious"}}',
        "roles": {"ip": "the IP address (GREYNOISE_API_KEY is optional)"},
    },
    "shodan": {
        "args": {"ip": '"198.51.100.14"'}, "extra": ["ip"], "host_title": "Shodan", "host_label": "api.shodan.io",
        "request": "GET /shodan/host/<ip>", "response": "ports, vulns",
        "static": '{"source": "shodan", "indicator": "198.51.100.14", "verdict": "suspicious", "score": 20, "details": {"ports": [22, 80, 443], "org": "Example Hosting BV", "vulns": ["CVE-2023-38408", "CVE-2024-6387"]}}',
        "roles": {"ip": "the IP address (key from SHODAN_API_KEY)"},
    },
    "has_key": {
        "t": "flow", "run": True, "extra": [],
        "args": {"service": '"virustotal"'},
        "nodes": [{"param": "service"}, {"box": "environment variables", "icon": "key", "lines": ["VT_API_KEY / ABUSEIPDB_API_KEY ...", "(the key itself is never shown)"]},
                  {"arrow": "intel.has_key"}, {"result": True}],
        "vary": {"param": "service", "values": ['"virustotal"', '"abuseipdb"', '"urlhaus"'], "labels": ["virustotal", "abuseipdb", "urlhaus"]},
        "note": "Real result from the computer that built this site, which has no keys set; yours depends on your environment.",
        "roles": {"service": "virustotal, abuseipdb or urlhaus"},
    },
    "key_help": {
        "t": "flow", "run": True, "extra": [], "mode": "lines",
        "nodes": [{"arrow": "intel.key_help"}, {"result": True, "label": "result · how to get and set the keys"}],
        "call": "intel.key_help()",
    },
}
