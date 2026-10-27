# Pictures for the template library (read with ast.literal_eval; never run).
SPEC = {
    "render": {
        "args": {"text": '"<p>Hi {{name}}, {{count}} new alerts</p>"', "values": '{"name": "<Ada>", "count": 3}', "options": "{}"},
        "call": 'template.render("<p>Hi {{name}}, {{count}} new alerts</p>", {"name": "<Ada>", "count": 3}, $options)',
        "modes": {"text": "lines"},
        "nodes": [{"param": "text", "icon": "doc"}, {"row": [{"param": "values"}, {"param": "options"}]},
                  {"arrow": True}, {"result": True, "label": "the gaps filled in", "mode": "lines"}],
        "vary": {"param": "options", "values": ["{}", '{"escape": false}'], "labels": ["escaped (default)", "escape: false"]},
        "roles": {"text": "the template: {{name}} marks a gap", "values": "a map: what goes in each gap",
                  "options": "{\"escape\": false} for plain text, emails, configs"},
    },
    "file": {
        "args": {"path": '"page.html"', "values": '{"title": "Weekly report"}'},
        "nodes": [{"row": [{"param": "path", "icon": "file"},
                           {"box": "page.html holds", "lines": ["<h1>{{title}}</h1>"], "kind": "plain"}]},
                  {"row": [{"param": "values"}, {"param": "options"}]},
                  {"arrow": True}, {"result": True, "label": "the file, filled in", "mode": "lines"}],
        "roles": {"path": "the template file", "values": "what goes in each gap", "options": "same as render()"},
    },
    "check": {
        "args": {"text": '"Hi {{name}}"'},
        "modes": {"text": "lines"},
        "nodes": [{"param": "text", "icon": "doc"}, {"arrow": True}, {"result": True, "label": "problems found (empty: fine)"}],
        "vary": {"param": "text", "values": ['"Hi {{name}}"', '"{{#items}} open"', '"{{name|shout}}"'],
                 "labels": ["Hi {{name}}", "{{#items}} open", "{{name|shout}}"]},
        "roles": {"text": "a template to check without filling it"},
    },
    "names": {
        "args": {"text": '"{{title}}: {{#alerts}}{{rule}} {{/alerts}} by {{user.name}}"'},
        "modes": {"text": "lines"},
        "nodes": [{"param": "text", "icon": "doc"}, {"arrow": True}, {"result": True, "label": "the top-level names it needs"}],
        "roles": {"text": "the template"},
    },
    "escape": {
        "args": {"text": '"Tom & <Jerry>"'},
        "nodes": [{"param": "text"}, {"arrow": True}, {"result": True, "label": "safe inside HTML", "mode": "lines"}],
        "roles": {"text": "text that may hold < > & \""},
    },
}
