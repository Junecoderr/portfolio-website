# Pictures for the ioc library (read with ast.literal_eval; never run).
# Every example runs through TB while the site builds.
SPEC = {
    "_default": {"modes": {"text": "lines"}},
    "extract": {
        "args": {"text": '"Beacon to hxxp://evil[.]example[.]com/a.php from 203.0.113.9; mail bob@bad.example about CVE-2024-3094"',
                 "options": '{"refang": true}'},
        "mode": "tree",
        "nodes": [
            {"param": "text", "icon": "mail"},
            {"param": "options"},
            {"arrow": "ioc.extract"},
            {"result": True, "label": "result · every IOC, grouped by kind", "mode": "tree"},
        ],
        "vary": {"param": "options", "values": ['{"refang": true}', '{"refang": false}'], "labels": ["refang: true", "refang: false"]},
        "roles": {"text": "any text: an email, a report, a chat message",
                  "options": "refang: false keeps defanged IOCs as they are written"},
    },
    "rows": {
        "args": {"found": '"Beacon to hxxp://evil[.]example[.]com from 203.0.113.9"'},
        "nodes": [
            {"param": "found", "mode": "lines"},
            {"arrow": "ioc.rows"},
            {"result": True, "label": "result · one {type, value} row per IOC"},
        ],
        "roles": {"found": "the map extract() gave, or the text itself"},
    },
    "defang": {
        "args": {"text": '"http://evil.example.com/a 203.0.113.9 bob@bad.example"'},
        "nodes": [
            {"param": "text", "icon": "alert"},
            {"arrow": "ioc.defang"},
            {"result": True, "label": "result · safe to paste: links won't work", "icon": "shield"},
        ],
        "roles": {"text": "text with live links, IPs and emails"},
    },
    "refang": {
        "args": {"text": '"hxxp://evil[.]example[.]com from 203[.]0[.]113[.]9"'},
        "nodes": [
            {"param": "text", "icon": "shield"},
            {"arrow": "ioc.refang"},
            {"result": True, "label": "result · real IOCs again, ready for tools"},
        ],
        "roles": {"text": "defanged text: hxxp, [.], [@]..."},
    },
    "is_ip": {
        "args": {"text": '"203.0.113.9"'},
        "vary": {"param": "text", "values": ['"203.0.113.9"', '"203[.]0[.]113[.]9"', '"999.1.1.1"', '"2001:db8::1"'],
                 "labels": ["203.0.113.9", "203[.]0[.]113[.]9", "999.1.1.1", "2001:db8::1"]},
        "roles": {"text": "the text to check"},
    },
    "is_domain": {
        "args": {"text": '"evil.example.com"'},
        "vary": {"param": "text", "values": ['"evil.example.com"', '"report.pdf"', '"evil[.]example[.]com"', '"localhost"'],
                 "labels": ["evil.example.com", "report.pdf", "evil[.]example[.]com", "localhost"]},
        "roles": {"text": "the text to check"},
    },
    "is_hash": {
        "args": {"text": '"5d41402abc4b2a76b9719d911017c592"'},
        "vary": {"param": "text", "values": ['"5d41402abc4b2a76b9719d911017c592"', '"5d41402abc4b2a76b9719d91101"', '"zz41402abc4b2a76b9719d911017c592"'],
                 "labels": ["32 hex (MD5)", "26 hex", "not hex"]},
        "roles": {"text": "the text to check: hex of MD5/SHA-1/SHA-256/SHA-512 length"},
    },
    "kind": {
        "args": {"text": '"CVE-2024-3094"'},
        "vary": {"param": "text", "values": ['"CVE-2024-3094"', '"203.0.113.9"', '"hxxp://evil[.]example"', '"bob@bad.example"',
                                            '"5d41402abc4b2a76b9719d911017c592"', '"hello"'],
                 "labels": ["CVE-2024-3094", "203.0.113.9", "hxxp://evil[.]example", "bob@bad.example", "an MD5", "hello"]},
        "roles": {"text": "one IOC"},
    },
}
