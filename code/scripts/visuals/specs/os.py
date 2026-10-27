# Pictures for the os library (read with ast.literal_eval; never run).
# Nothing here runs other programs at build time; values are examples.
SPEC = {
    "env": {"run": False, "args": {"name": '"HOME"', "default": '"/tmp"'}, "static": '"/home/you"',
            "nodes": [{"row": [{"param": "name"}, {"param": "default"}]}, {"arrow": True},
                      {"box": "environment variables", "icon": "gear", "lines": ["HOME=/home/you", "PATH=/usr/bin:…"]}, {"arrow": "HOME is set"}, {"result": True}],
            "roles": {"name": "the variable's name", "default": "given back when it isn't set (nil if left out)"}},
    "run": {"run": False, "args": {"command": '"ls logs"', "options": '{"timeout": 10}'},
            "static": '{"code": 0, "out": "app.log\\nold\\n", "err": ""}',
            "nodes": [{"row": [{"param": "command", "icon": "terminal"}, {"param": "options"}]}, {"arrow": True},
                      {"box": "another program runs", "icon": "terminal", "lines": ["$ ls logs", "app.log", "old"]}, {"arrow": "waits for it to finish"}, {"result": True}],
            "roles": {"command": "the command line (text) or a list of words", "options": "timeout, cwd, input, shell, env"}},
    "quote": {"args": {"text": '"my file.txt"'},
              "vary": {"param": "text", "values": ['"my file.txt"', '"a;rm -rf x"', '"plain"'], "labels": ["my file.txt", "a;rm -rf x", "plain"]},
              "roles": {"text": "text to put safely inside a shell command"}},
    "exit": {"run": False, "args": {"code": "1"},
             "nodes": [{"param": "code"}, {"arrow": "os.exit"}, {"box": "the program stops here", "icon": "cross", "lines": ["exit code 1 tells scripts", "something went wrong (0 = fine)"]}],
             "roles": {"code": "the exit code: 0 (default) means all went well"}},
}
