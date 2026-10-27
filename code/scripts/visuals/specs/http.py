# Pictures for the http library (read with ast.literal_eval; never run).
# Web requests need the TB app and the network, so results are examples.
SPEC = {
    "_default": {"t": "net", "run": False, "port_label": "443", "port_title": "https", "icon": "server",
                 "host_title": "web server", "extra": ["options"],
                 "roles": {"url": "the web address: which server, and which page on it",
                           "options": "headers, params, json, timeout (seconds), user_agent, max_bytes"}},
    "get": {
        "args": {"url": '"https://api.example.com/v1/ip"', "options": '{"timeout": 5}'},
        "host_param": "url", "host_label": "api.example.com",
        "request": "GET /v1/ip", "response": "200 OK + body",
        "static": '{"status": 200, "ok": true, "json": {"ip": "203.0.113.9"}, "headers": {"content-type": "application/json"}, "elapsed": 84}',
        "keys": ["status", "ok", "json", "elapsed"],
    },
    "post": {
        "args": {"url": '"https://api.example.com/v1/notes"', "body": '{"text": "hello"}'},
        "host_param": "url", "host_label": "api.example.com", "extra": ["body"],
        "request": "POST /v1/notes", "response": "201 Created",
        "static": '{"status": 201, "ok": true, "json": {"id": 7}, "elapsed": 112}',
        "keys": ["status", "ok", "json", "elapsed"],
        "roles": {"url": "where to send it", "body": "text, or a map/list sent as JSON",
                  "options": "headers, params, timeout, user_agent, max_bytes"},
    },
    "request": {
        "args": {"method": '"DELETE"', "url": '"https://api.example.com/v1/notes/7"'},
        "host_param": "url", "host_label": "api.example.com", "extra": ["method"],
        "request": "DELETE /v1/notes/7", "response": "204 No Content",
        "static": '{"status": 204, "ok": true, "text": "", "elapsed": 61}',
        "keys": ["status", "ok", "elapsed"],
        "roles": {"method": "GET, POST, PUT, PATCH, DELETE, HEAD or OPTIONS", "url": "the web address",
                  "options": "body, headers, params, json, timeout"},
    },
    "download": {
        "args": {"url": '"https://example.com/feed.json"', "path": '"feed.json"'},
        "host_param": "url", "host_label": "example.com", "extra": ["path"],
        "request": "GET /feed.json", "response": "the bytes → a file",
        "static": '{"path": "/home/you/feed.json", "bytes": 48213, "status": 200}',
        "roles": {"url": "what to fetch", "path": "the file it is saved in", "options": "headers, timeout, max_bytes (default 100 MB)"},
    },
    "encode_params": {
        "t": "flow", "run": True, "args": {"map": '{"q": "ssh brute", "n": 2}'},
        "roles": {"map": "names and values for after the ? in a web address"},
    },
}
