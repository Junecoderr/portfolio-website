# Pictures for the hash library (read with ast.literal_eval; never run).
# Every example runs through TB while the site builds. The sliders change one
# letter of the input: the whole fingerprint changes (the avalanche effect).
SPEC = {
    "_default": {"mode": "lines"},
    "md5": {
        "args": {"text": '"hello"'},
        "nodes": [{"param": "text"}, {"arrow": "hash.md5"}, {"result": True, "label": "result · 32 hex characters", "icon": "fp"}],
        "vary": {"param": "text", "values": ['"hello"', '"hellp"', '"Hello"', 'b"MZ"'], "labels": ["hello", "hellp", "Hello", "b\"MZ\" (bytes)"]},
        "roles": {"text": "the text or bytes to fingerprint"},
    },
    "sha1": {
        "args": {"text": '"hello"'},
        "nodes": [{"param": "text"}, {"arrow": "hash.sha1"}, {"result": True, "label": "result · 40 hex characters", "icon": "fp"}],
        "vary": {"param": "text", "values": ['"hello"', '"hellp"', '"Hello"'], "labels": ["hello", "hellp", "Hello"]},
        "roles": {"text": "the text or bytes to fingerprint"},
    },
    "sha256": {
        "args": {"text": '"hello"'},
        "nodes": [{"param": "text"}, {"arrow": "hash.sha256"}, {"result": True, "label": "result · 64 hex characters", "icon": "fp"}],
        "vary": {"param": "text", "values": ['"hello"', '"hellp"', '"Hello"', '""'], "labels": ["hello", "hellp", "Hello", "(empty)"]},
        "roles": {"text": "the text or bytes to fingerprint: one changed letter changes everything"},
    },
    "sha512": {
        "args": {"text": '"hello"'},
        "nodes": [{"param": "text"}, {"arrow": "hash.sha512"}, {"result": True, "label": "result · 128 hex characters", "icon": "fp"}],
        "vary": {"param": "text", "values": ['"hello"', '"hellp"'], "labels": ["hello", "hellp"]},
        "roles": {"text": "the text or bytes to fingerprint"},
    },
    "file_md5": {
        "args": {"path": '"notes.txt"'},
        "nodes": [{"param": "path", "icon": "file", "lines": ["notes.txt (30 bytes)", "read a piece at a time"]},
                  {"arrow": "hash.file_md5"}, {"result": True, "icon": "fp"}],
        "roles": {"path": "the file whose bytes are hashed"},
    },
    "file_sha1": {
        "args": {"path": '"notes.txt"'},
        "nodes": [{"param": "path", "icon": "file", "lines": ["notes.txt (30 bytes)"]}, {"arrow": "hash.file_sha1"}, {"result": True, "icon": "fp"}],
        "roles": {"path": "the file whose bytes are hashed"},
    },
    "file_sha256": {
        "args": {"path": '"notes.txt"'},
        "nodes": [{"param": "path", "icon": "file", "lines": ["notes.txt (30 bytes)"]}, {"arrow": "hash.file_sha256"},
                  {"result": True, "icon": "fp", "sub": "search it on VirusTotal to see if a file is known malware"}],
        "roles": {"path": "the file whose bytes are hashed"},
    },
    "file_sha512": {
        "args": {"path": '"notes.txt"'},
        "nodes": [{"param": "path", "icon": "file", "lines": ["notes.txt (30 bytes)"]}, {"arrow": "hash.file_sha512"}, {"result": True, "icon": "fp"}],
        "roles": {"path": "the file whose bytes are hashed"},
    },
    "file": {
        "args": {"path": '"notes.txt"'},
        "mode": None,
        "nodes": [{"param": "path", "icon": "file", "lines": ["notes.txt", "one read of the file"]}, {"arrow": "hash.file"},
                  {"result": True, "label": "result · every common hash at once"}],
        "roles": {"path": "the file to fingerprint"},
    },
    "hmac_sha256": {
        "args": {"key": '"s3cret"', "text": '"{\\"event\\": \\"push\\"}"'},
        "nodes": [{"row": [{"param": "key", "icon": "key"}, {"param": "text", "icon": "doc", "mode": "lines"}]},
                  {"arrow": "hash.hmac_sha256"}, {"result": True, "label": "result · signature (hex)", "icon": "fp"}],
        "vary": {"param": "key", "values": ['"s3cret"', '"s3creT"'], "labels": ["s3cret", "s3creT (wrong key)"]},
        "roles": {"key": "the shared secret: a different key gives a different signature",
                  "text": "the message, like a webhook body"},
    },
    "compare": {
        "args": {"a": '"82017e44486e567046c1"', "b": '"82017e44486e567046c1"'},
        "mode": None,
        "nodes": [{"row": [{"param": "a", "mode": "lines"}, {"param": "b", "mode": "lines"}]},
                  {"box": "every character checked", "icon": "clock", "lines": ["takes the same time", "wherever they differ"]},
                  {"arrow": "hash.compare"}, {"result": True}],
        "vary": {"param": "b", "values": ['"82017e44486e567046c1"', '"82017e44486e567046c2"', '"92017e44486e567046c1"'],
                 "labels": ["same", "last differs", "first differs"]},
        "roles": {"a": "the expected secret or signature", "b": "what someone sent you"},
    },
}
