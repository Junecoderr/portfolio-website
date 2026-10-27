# Pictures for the websocket library (read with ast.literal_eval; never run).
SPEC = {
    "connect": {"t": "net", "run": False, "args": {"url": '"wss://chat.example.com/live"', "options": '{"timeout": 10}'},
                "host_param": "url", "host_label": "chat.example.com", "port_label": "443", "port_title": "wss", "extra": ["options"],
                "request": "upgrade: websocket", "response": "messages both ways", "static": "<ws>",
                "gives": "ws: .send(text) .receive() .close()",
                "roles": {"url": "ws:// or wss:// (encrypted) address", "options": "headers, timeout, ca_file, insecure"}},
    "is_url": {"run": True, "args": {"x": '"wss://chat.example.com/live"'},
               "vary": {"param": "x", "values": ['"wss://chat.example.com/live"', '"ws://localhost:8080"', '"https://example.com"'],
                        "labels": ["wss://chat...", "ws://localhost:8080", "https://..."]},
               "roles": {"x": "any value"}},
}
