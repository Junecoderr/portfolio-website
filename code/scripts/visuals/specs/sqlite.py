# Pictures for the sqlite library (read with ast.literal_eval; never run).
# Databases live in memory (or in the build's scratch folder).
SPEC = {
    "open": {
        "args": {"path": '":memory:"'},
        "setup": ('let db = sqlite.open(":memory:")\n'
                  'db.run("create table logins (ip text, user text, ok int)")\n'
                  'db.execute_many("insert into logins values (?, ?, ?)", '
                  '[["203.0.113.42", "root", 0], ["203.0.113.42", "admin", 0], ["192.0.2.10", "alice", 1]])'),
        "show_call": 'sqlite.open() then db.all(...)',
        "call": 'db.all("select ip, count(*) as tries from logins where ok = 0 group by ip")',
        "nodes": [{"row": [{"param": "path", "icon": "db"}, {"param": "options"}]},
                  {"arrow": "sqlite.open"},
                  {"box": "db: then use it", "icon": "db", "kind": "plain",
                   "lines": ['db.run("create table logins (ip, user, ok)")', 'db.execute_many("insert ...", rows)',
                             'db.all("select ip, count(*) as tries', '        from logins where ok = 0', '        group by ip")']},
                  {"arrow": "db.all"},
                  {"result": True, "label": "rows come back as maps"}],
        "roles": {"path": "the database file (left out or \":memory:\": in memory only)",
                  "options": "{\"readonly\": true} refuses changes"},
        "note": "Real result: this database was made and queried with TB while the site was built.",
    },
    "escape": {
        "args": {"text": '"O\'Brien"'},
        "mode": "cells", "find_in": {},
        "nodes": [{"param": "text"}, {"arrow": True},
                  {"result": True, "label": "the quote doubled: safe inside '...'", "sub": "better: db.all(\"... where name = ?\", [name])"}],
        "roles": {"text": "text going inside single quotes in SQL"},
    },
    "quote_name": {
        "args": {"name": '"my table"'},
        "mode": "cells", "trace": [],
        "nodes": [{"param": "name"}, {"arrow": True}, {"result": True, "label": "a name SQL reads as one piece"}],
        "vary": {"param": "name", "values": ['"my table"', '"select"', '"odd\\"name"'], "labels": ["my table", "select", "odd\"name"]},
        "roles": {"name": "a table or column name (spaces, keywords are fine)"},
    },
}
