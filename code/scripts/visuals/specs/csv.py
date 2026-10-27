# Pictures for the csv library (read with ast.literal_eval; never run).
SPEC = {
    "parse": {
        "args": {"text": '"name,role\\nAda,admin\\nBo,user"', "options": "{}"},
        "call": 'csv.parse("name,role\\nAda,admin\\nBo,user", $options)',
        "modes": {"text": "lines"},
        "nodes": [{"row": [{"param": "text", "icon": "doc"}, {"param": "options"}]}, {"arrow": True},
                  {"result": True, "label": "one row per line"}],
        "vary": {"param": "options", "values": ["{}", '{"header": false}'], "labels": ["header row (default)", "header: false"]},
        "roles": {"text": "CSV text: the first line names the columns", "options": "delimiter, header"},
    },
    "read": {
        "args": {"path": '"data/users.csv"'},
        "nodes": [{"row": [{"param": "path", "icon": "file"},
                           {"box": "data/users.csv", "lines": ["name,role", "Ada,admin", "Bo,user"], "kind": "plain"}]},
                  {"arrow": True}, {"result": True, "label": "a list of maps, values as text"}],
        "roles": {"path": "the .csv file", "options": "delimiter, header (like parse)"},
    },
    "stringify": {
        "args": {"rows": '[{"name": "Ada", "role": "admin"}, {"name": "Bo", "role": "user"}]'},
        "nodes": [{"param": "rows"}, {"arrow": True}, {"result": True, "label": "CSV text: header row, then the rows", "mode": "lines"}],
        "roles": {"rows": "a list of maps (keys become the header) or lists"},
    },
    "write": {
        "args": {"path": '"team.csv"', "rows": '[{"name": "Ada", "role": "admin"}, {"name": "Bo", "role": "user"}]'},
        "imports": ["fs"],
        "call": 'csv.write("team.csv", [{"name": "Ada", "role": "admin"}, {"name": "Bo", "role": "user"}]) or fs.read("team.csv")',
        "show_call": 'csv.write("team.csv", [{"name": "Ada", "role": "admin"}, {"name": "Bo", "role": "user"}])', "hide_result_text": True,
        "nodes": [{"param": "path", "icon": "file"}, {"param": "rows"}, {"arrow": "csv.write"},
                  {"result": True, "label": "team.csv afterwards (read back)", "mode": "lines"}],
        "roles": {"path": "the .csv file to (re)write", "rows": "maps (keys become the header) or lists"},
    },
}
