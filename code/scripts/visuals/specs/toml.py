# Pictures for the toml library (read with ast.literal_eval; never run).
SPEC = {
    "parse": {
        "args": {"text": '"title = \\"TB\\"\\n[server]\\nport = 8080\\ntls = true"'},
        "modes": {"text": "lines"},
        "nodes": [{"param": "text", "icon": "doc"}, {"arrow": True},
                  {"result": True, "label": "[server] becomes a nested map", "mode": "tree"}],
        "roles": {"text": "TOML text, like a config file"},
    },
    "stringify": {
        "args": {"map": '{"title": "TB", "server": {"port": 8080, "tls": true}}'},
        "modes": {"map": "tree"},
        "nodes": [{"param": "map"}, {"arrow": True},
                  {"result": True, "label": "plain values first, then [tables]", "mode": "lines"}],
        "roles": {"map": "the settings to write (nested maps become [tables])"},
    },
    "read": {
        "args": {"path": '"config.toml"'},
        "nodes": [{"row": [{"param": "path", "icon": "file"},
                           {"box": "config.toml", "lines": ['title = "TB"', "[server]", "port = 8080"], "kind": "plain"}]},
                  {"arrow": True}, {"result": True, "label": "a TB map", "mode": "tree"}],
        "roles": {"path": "the .toml file"},
    },
    "write": {
        "args": {"path": '"app.toml"', "map": '{"name": "notes", "window": {"width": 640, "height": 480}}'},
        "imports": ["fs"],
        "call": 'toml.write("app.toml", {"name": "notes", "window": {"width": 640, "height": 480}}) or fs.read("app.toml")',
        "show_call": 'toml.write("app.toml", {"name": "notes", "window": {"width": 640, "height": 480}})', "hide_result_text": True,
        "modes": {"map": "tree"},
        "nodes": [{"param": "path", "icon": "file"}, {"param": "map"}, {"arrow": "toml.write"},
                  {"result": True, "label": "app.toml afterwards (read back)", "mode": "lines"}],
        "roles": {"path": "the file to (re)write", "map": "the settings to save"},
    },
}
