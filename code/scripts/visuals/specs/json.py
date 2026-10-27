# Pictures for the json library (read with ast.literal_eval; never run).
SPEC = {
    "parse": {
        "args": {"text": '"{\\"port\\": 8080, \\"tags\\": [\\"web\\", \\"tls\\"], \\"debug\\": true}"'},
        "modes": {"text": "lines"},
        "nodes": [{"param": "text", "icon": "doc"}, {"arrow": True},
                  {"result": True, "label": "TB values: objects become maps, arrays lists", "mode": "tree"}],
        "roles": {"text": "JSON text, from a file, a web API or a log line"},
    },
    "stringify": {
        "args": {"value": '{"port": 8080, "tags": ["web", "tls"], "debug": true}', "pretty": "false"},
        "modes": {"value": "tree"},
        "nodes": [{"row": [{"param": "value"}, {"param": "pretty"}]}, {"arrow": True},
                  {"result": True, "label": "JSON text", "mode": "lines"}],
        "vary": {"param": "pretty", "values": ["false", "true"], "labels": ["false: one line", "true: indented"]},
        "roles": {"value": "a map, list, text, number, true/false or nil", "pretty": "true for indented lines (one line if left out)"},
    },
    "read": {
        "args": {"path": '"config.json"'},
        "nodes": [{"row": [{"param": "path", "icon": "file"},
                           {"box": "config.json holds", "lines": ['{"port": 8080,', ' "debug": true}'], "kind": "plain"}]},
                  {"arrow": True}, {"result": True, "label": "a TB map you can use: cfg.port", "mode": "tree"}],
        "roles": {"path": "the .json file to read"},
    },
    "write": {
        "args": {"path": '"settings.json"', "value": '{"port": 8080, "tags": ["web"]}', "pretty": "true"},
        "imports": ["fs"],
        "call": 'json.write("settings.json", {"port": 8080, "tags": ["web"]}, $pretty) or fs.read("settings.json")',
        "show_call": 'json.write("settings.json", {"port": 8080, "tags": ["web"]}, $pretty)', "hide_result_text": True,
        "modes": {"value": "tree"},
        "nodes": [{"row": [{"param": "path", "icon": "file"}, {"param": "pretty"}]}, {"param": "value"},
                  {"arrow": "json.write"}, {"result": True, "label": "settings.json afterwards (read back)", "mode": "lines"}],
        "vary": {"param": "pretty", "values": ["true", "false"], "labels": ["true (default)", "false: one line"]},
        "roles": {"path": "the file to (re)write", "value": "what to save", "pretty": "indented unless false"},
    },
}
