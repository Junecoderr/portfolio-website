"""`tb lsp`: a scripted JSON-RPC session with the real server in a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tbtools.lsp import path_to_uri  # noqa: E402


class Client:
    def __init__(self, cwd):
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tb.py"), "lsp"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.next_id = 0
        self.notifications: list[dict] = []

    def send(self, message: dict) -> None:
        message["jsonrpc"] = "2.0"
        body = json.dumps(message).encode("utf-8")
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.proc.stdin.flush()

    def read(self) -> dict:
        length = None
        while True:
            line = self.proc.stdout.readline()
            assert line, "the server closed its output: " + self.proc.stderr.read().decode()
            line = line.strip()
            if not line:
                break
            name, _, value = line.decode().partition(":")
            if name.lower() == "content-length":
                length = int(value)
        return json.loads(self.proc.stdout.read(length))

    def request(self, method: str, params=None):
        self.next_id += 1
        my_id = self.next_id
        self.send({"id": my_id, "method": method, "params": params or {}})
        while True:
            message = self.read()
            if message.get("id") == my_id and "method" not in message:
                assert "error" not in message, message
                return message.get("result")
            self.notifications.append(message)

    def notify(self, method: str, params=None) -> None:
        self.send({"method": method, "params": params or {}})

    def diagnostics_for(self, uri: str) -> list:
        while True:
            for k, note in enumerate(self.notifications):
                if note.get("method") == "textDocument/publishDiagnostics" and note["params"]["uri"] == uri:
                    del self.notifications[k]
                    return note["params"]["diagnostics"]
            self.notifications.append(self.read())

    def close(self):
        try:
            self.request("shutdown")
            self.notify("exit")
            code = self.proc.wait(timeout=10)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.stdout.close()
            self.proc.stderr.close()
            self.proc.stdin.close()
        return code


MAIN = '''import fs
import "helpers.tb" as helpers
from "helpers.tb" import shout

## Adds one.
fn add_one(n) {
  return n + 1
}

class Dog {
  let sound = "woof"
  fn speak() { return self.sound }
}

let items = [1, 2, 3]
let total = add_one(2)
say(helpers.greet("Tanisha"), shout("hi"))
fs.
'''

HELPERS = '''## Greets someone.
fn greet(name) {
  return f"Hello, {name}!"
}

fn shout(text) {
  return text.upper()
}
'''


@pytest.fixture
def session(tmp_path):
    (tmp_path / "helpers.tb").write_text(HELPERS, encoding="utf-8")
    main = tmp_path / "main.tb"
    main.write_text(MAIN, encoding="utf-8")
    client = Client(tmp_path)
    init = client.request("initialize", {"processId": None, "rootUri": path_to_uri(str(tmp_path)),
                                          "capabilities": {}})
    client.notify("initialized", {})
    uri = path_to_uri(str(main))
    client.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "tb", "version": 1,
                                                            "text": MAIN}})
    yield client, init, uri, tmp_path
    if client.proc.poll() is None:
        client.close()


def pos(text: str, needle: str, offset: int = 0) -> dict:
    index = text.index(needle) + offset
    line = text.count("\n", 0, index)
    return {"line": line, "character": index - (text.rfind("\n", 0, index) + 1)}


def test_initialize_lists_capabilities(session):
    _client, init, _uri, _tmp = session
    caps = init["capabilities"]
    for key in ("completionProvider", "hoverProvider", "signatureHelpProvider", "definitionProvider",
                "documentSymbolProvider", "documentFormattingProvider", "renameProvider"):
        assert caps[key], key
    assert init["serverInfo"]["name"] == "tb lsp"


def test_diagnostics_for_a_parse_error(session):
    client, _init, uri, _tmp = session
    first = client.diagnostics_for(uri)     # the open document ends with "fs." (incomplete)
    assert any(d["severity"] == 1 for d in first)
    good = MAIN.replace("fs.\n", "say(fs.exists(\"x\"))\n")
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": good}]})
    assert [d for d in client.diagnostics_for(uri) if d["severity"] == 1] == []
    bad = good + "let = 5\n"
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 3},
                                             "contentChanges": [{"text": bad}]})
    errors = [d for d in client.diagnostics_for(uri) if d["severity"] == 1]
    assert errors and errors[0]["range"]["start"]["line"] == bad.count("\n") - 1
    assert "name" in errors[0]["message"]


def test_completion_after_library_dot(session):
    client, _init, uri, _tmp = session
    good = MAIN.replace("fs.\n", "say(1)\n")
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": good}]})
    client.diagnostics_for(uri)
    typing = good + "fs."
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 3},
                                             "contentChanges": [{"text": typing}]})
    end = {"line": typing.count("\n"), "character": 3}
    result = client.request("textDocument/completion", {"textDocument": {"uri": uri}, "position": end})
    labels = {item["label"] for item in result["items"]}
    assert {"read", "write", "exists"} <= labels
    assert "say" not in labels
    # plain completion: keywords, builtins, libraries and the file's own names
    plain = client.request("textDocument/completion", {"textDocument": {"uri": uri},
                                                       "position": pos(good, "say(1)")})
    labels = {item["label"] for item in plain["items"]}
    assert {"let", "say", "fs", "add_one", "Dog", "items"} <= labels
    # methods by receiver type, and members of an imported file
    lines = good + "items.\n"
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 4},
                                             "contentChanges": [{"text": lines}]})
    result = client.request("textDocument/completion", {"textDocument": {"uri": uri},
                                                        "position": {"line": lines.count("\n") - 1, "character": 6}})
    assert "push" in {item["label"] for item in result["items"]}
    lines = good + "helpers.\n"
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 5},
                                             "contentChanges": [{"text": lines}]})
    result = client.request("textDocument/completion", {"textDocument": {"uri": uri},
                                                        "position": {"line": lines.count("\n") - 1, "character": 8}})
    assert {"greet", "shout"} <= {item["label"] for item in result["items"]}


def test_hover_on_builtin_library_and_own_function(session):
    client, _init, uri, _tmp = session
    hover = client.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": pos(MAIN, "say(")})
    assert "say(" in hover["contents"]["value"]
    hover = client.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                  "position": pos(MAIN, "add_one(2)", 2)})
    assert "fn add_one(n)" in hover["contents"]["value"] and "Adds one." in hover["contents"]["value"]
    hover = client.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                  "position": pos(MAIN, "greet(\"Tanisha\")")})
    assert "Greets someone." in hover["contents"]["value"]


def test_signature_help(session):
    client, _init, uri, _tmp = session
    text = MAIN.replace("fs.\n", "add_one(\nfs.read(\"a\", \n")
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": text}]})
    help1 = client.request("textDocument/signatureHelp", {"textDocument": {"uri": uri},
                                                          "position": pos(text, "add_one(\n", 8)})
    assert help1["signatures"][0]["label"].startswith("fn add_one(n)")
    help2 = client.request("textDocument/signatureHelp", {"textDocument": {"uri": uri},
                                                          "position": pos(text, "fs.read(\"a\", ", 13)})
    assert "read(" in help2["signatures"][0]["label"]


def test_signature_help_follows_named_inputs(session):
    client, _init, uri, _tmp = session
    text = MAIN.replace("fs.\n", "import text\ntext.pad_left(\"a\", fill = \n")
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": text}]})
    found = client.request("textDocument/signatureHelp", {"textDocument": {"uri": uri},
                                                          "position": pos(text, "fill = ", 7)})
    params = [p["label"] for p in found["signatures"][0]["parameters"]]
    assert params[found["activeParameter"]].startswith("fill")


def test_definition_same_file_and_across_files(session):
    client, _init, uri, tmp = session
    loc = client.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                     "position": pos(MAIN, "add_one(2)", 1)})
    assert loc["uri"] == uri and loc["range"]["start"]["line"] == MAIN[:MAIN.index("fn add_one")].count("\n")
    loc = client.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                     "position": pos(MAIN, "greet(\"Tanisha\")", 1)})
    assert loc["uri"] == path_to_uri(str(tmp / "helpers.tb"))
    assert loc["range"]["start"] == {"line": 1, "character": 3}
    loc = client.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                     "position": pos(MAIN, "shout(\"hi\")", 1)})
    assert loc["uri"].endswith("helpers.tb") and loc["range"]["start"]["line"] == 5
    loc = client.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                     "position": pos(MAIN, "self.sound", 6)})
    assert loc["range"]["start"]["line"] == MAIN[:MAIN.index("let sound")].count("\n")


def test_document_symbols(session):
    client, _init, uri, _tmp = session
    good = MAIN.replace("fs.\n", "")
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": good}]})
    symbols = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    names = [s["name"] for s in symbols]
    assert names == ["add_one", "Dog", "items", "total"]
    dog = symbols[1]
    assert dog["kind"] == 5 and [c["name"] for c in dog["children"]] == ["sound", "speak"]


def test_formatting_and_rename(session):
    client, _init, uri, _tmp = session
    messy = "let   x=1\nfn f(a,b){return a+b}\nsay(f(x,x))\n"
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": messy}]})
    edits = client.request("textDocument/formatting", {"textDocument": {"uri": uri},
                                                       "options": {"tabSize": 2, "insertSpaces": True}})
    assert edits[0]["newText"] == "let x = 1\nfn f(a, b) {\n  return a + b\n}\nsay(f(x, x))\n"
    rename = client.request("textDocument/rename", {"textDocument": {"uri": uri},
                                                    "position": pos(messy, "x=1"), "newName": "count"})
    edits = rename["changes"][uri]
    assert len(edits) == 3


def test_shutdown_and_exit(session):
    client, _init, _uri, _tmp = session
    assert client.close() == 0


def test_library_docs_hover_as_markdown_lists():
    import tb
    from tbtools.lsp import doc_markdown
    doc = next(f["doc"] for f in tb.describe_library("detect")["functions"] if f["name"] == "rule")
    md = doc_markdown(doc)
    assert "**Fields**" in md and "\n- `level`: how serious" in md and "\n  - " in md
    assert "```tb\nlet r = detect.rule({" in md
    own = "Adds one.\n\n* first\n* second"
    assert doc_markdown(own) == own


def _open(client, path, text):
    uri = path_to_uri(str(path))
    client.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "tb", "version": 1,
                                                            "text": text}})
    client.diagnostics_for(uri)
    return uri


def _lines(changes_or_refs):
    if isinstance(changes_or_refs, dict):
        return {u: sorted(e["range"]["start"]["line"] for e in edits) for u, edits in changes_or_refs.items()}
    found: dict = {}
    for loc in changes_or_refs:
        found.setdefault(loc["uri"], []).append(loc["range"]["start"]["line"])
    return {u: sorted(v) for u, v in found.items()}


OTHER = 'from "helpers.tb" import greet\nfn greet_all() {\n  let shout = 1\n  return greet("all")\n}\n'
PKG = '## Paints text.\nfn paint(t) {\n  return t\n}\nsay(paint("own"))\n'
USES_PKG = 'import tb_colors\nfrom "tb-colors" import paint as p\nsay(tb_colors.paint("a"), p("b"))\n'


def test_references_and_rename_across_files_and_packages(session):
    client, _init, uri, tmp = session
    (tmp / "other.tb").write_text(OTHER, encoding="utf-8")
    (tmp / "tb_packages" / "tb-colors").mkdir(parents=True)
    (tmp / "tb_packages" / "tb-colors" / "lib.tb").write_text(PKG, encoding="utf-8")
    (tmp / "app.tb").write_text(USES_PKG, encoding="utf-8")
    helpers, other, app = (path_to_uri(str(tmp / n)) for n in ("helpers.tb", "other.tb", "app.tb"))
    lib = path_to_uri(str(tmp / "tb_packages" / "tb-colors" / "lib.tb"))
    where = pos(MAIN, "greet(\"Tanisha\")", 1)
    # from the use in main.tb (helpers.greet): the definition and the uses in other files
    refs = client.request("textDocument/references", {"textDocument": {"uri": uri}, "position": where,
                                                      "context": {"includeDeclaration": True}})
    assert _lines(refs) == {helpers: [1], uri: [16], other: [0, 3]}
    refs = client.request("textDocument/references", {"textDocument": {"uri": uri}, "position": where,
                                                      "context": {"includeDeclaration": False}})
    assert helpers not in _lines(refs)
    # rename from a `from ... import` use: the definition, the import and the use; the local
    # `let shout` in other.tb is a different name and stays
    rename = client.request("textDocument/rename", {"textDocument": {"uri": uri},
                                                    "position": pos(MAIN, "shout(\"hi\")", 1), "newName": "yell"})
    assert _lines(rename["changes"]) == {helpers: [5], uri: [2, 16]}
    # a package in tb_packages/: `import tb_colors` and `from "tb-colors" import paint as p`
    assert _open(client, tmp / "app.tb", USES_PKG) == app
    rename = client.request("textDocument/rename", {"textDocument": {"uri": app},
                                                    "position": pos(USES_PKG, "paint(\"a\")", 1),
                                                    "newName": "colour"})
    assert _lines(rename["changes"]) == {lib: [1, 4], app: [1, 2]}
    assert {e["range"]["start"]["character"] for e in rename["changes"][app]} == {24, 14}
    # renaming the alias `p` only renames the alias, in this file
    rename = client.request("textDocument/rename", {"textDocument": {"uri": app},
                                                    "position": pos(USES_PKG, "p(\"b\")"), "newName": "q"})
    assert _lines(rename["changes"]) == {app: [1, 2]}
    # a local name still renames within its file only
    rename = client.request("textDocument/rename", {"textDocument": {"uri": uri},
                                                    "position": pos(MAIN, "items = "), "newName": "things"})
    assert list(rename["changes"]) == [uri]


def test_cross_file_rename_respects_shadowing_and_unparsed_files(tmp_path):
    from tbtools import lsp

    (tmp_path / "helper.tb").write_text("fn twice(x) {\n  return x * 2\n}\n", encoding="utf-8")
    main_text = ('import "helper.tb" as h\nsay(h.twice(2))\nfn other(h) {\n  return h.twice\n}\n'
                 'say(other({"twice": 3}))\n')
    (tmp_path / "main.tb").write_text(main_text, encoding="utf-8")
    server = lsp.Server()
    notes = []
    server.notify = lambda method, params: notes.append((method, params))
    server.on_initialize({"rootUri": path_to_uri(str(tmp_path))})
    uris = {}
    for name in ("helper.tb", "main.tb"):
        uris[name] = path_to_uri(str(tmp_path / name))
        server.on_textDocument_didOpen({"textDocument": {
            "uri": uris[name], "text": (tmp_path / name).read_text(encoding="utf-8"), "languageId": "tb",
            "version": 1}})
    at_def = {"textDocument": {"uri": uris["helper.tb"]}, "position": {"line": 0, "character": 4}}
    # `h.twice` inside other(h) is the parameter's field, not the import
    rename = server.on_textDocument_rename({**at_def, "newName": "double"})
    assert _lines(rename["changes"]) == {uris["helper.tb"]: [0], uris["main.tb"]: [1]}
    assert server._target(uris["main.tb"], pos(main_text, "h.twice\n", 2)) is None
    assert server._target(uris["main.tb"], pos(main_text, "h.twice(", 2)) is not None
    refs = server.on_textDocument_references({**at_def, "context": {"includeDeclaration": True}})
    assert _lines(refs) == {uris["helper.tb"]: [0], uris["main.tb"]: [1]}
    # main.tb now has a new first line and doesn't parse: its old analysis must not be used
    broken = "# a new first line\n" + main_text + "let x = (1 +\n\n"
    server.on_textDocument_didChange({"textDocument": {"uri": uris["main.tb"]}, "contentChanges": [{"text": broken}]})
    with pytest.raises(ValueError, match="Fix the errors in main.tb first"):
        server.on_textDocument_rename({**at_def, "newName": "double"})
    refs = server.on_textDocument_references({**at_def, "context": {"includeDeclaration": True}})
    assert _lines(refs) == {uris["helper.tb"]: [0]}
    assert any(m == "window/showMessage" and "main.tb" in p["message"] for m, p in notes)


def test_code_actions_fix_typos_add_imports_and_organise(session):
    client, _init, uri, _tmp = session
    text = ('import "helpers.tb" as helpers\nimport text\n\nimport fs\nimport fs\n'
            'let total = 1\nsay(totl)\nsay(json.parse("1"))\n')
    client.diagnostics_for(uri)   # the ones for the opened file
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": text}]})
    diags = client.diagnostics_for(uri)
    everything = {"start": {"line": 0, "character": 0}, "end": {"line": 8, "character": 0}}
    actions = client.request("textDocument/codeAction", {"textDocument": {"uri": uri}, "range": everything,
                                                         "context": {"diagnostics": diags}})
    by_title = {a["title"]: a for a in actions}
    fix = by_title["Change 'totl' to 'total'"]["edit"]["changes"][uri][0]
    assert fix["newText"] == "total" and fix["range"]["start"] == {"line": 6, "character": 4}
    add = by_title["Add 'import json'"]["edit"]["changes"][uri][0]
    assert add["newText"] == "import json\n" and add["range"]["start"] == {"line": 5, "character": 0}
    organise = by_title["Organise imports"]
    assert organise["kind"] == "source.organizeImports"
    edit = organise["edit"]["changes"][uri][0]
    assert edit["newText"] == 'import fs\nimport text\nimport "helpers.tb" as helpers'
    assert edit["range"] == {"start": {"line": 0, "character": 0}, "end": {"line": 4, "character": 9}}
    only = client.request("textDocument/codeAction", {"textDocument": {"uri": uri}, "range": everything,
                                                      "context": {"diagnostics": diags,
                                                                  "only": ["source.organizeImports"]}})
    assert [a["title"] for a in only] == ["Organise imports"]


def test_formatting_uses_tb_toml_fmt_options(session):
    client, _init, uri, tmp = session
    (tmp / "tb.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n\n[fmt]\nindent = 4\n',
                                 encoding="utf-8")
    messy = "fn f(a,b){return a+b}\n"
    client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                             "contentChanges": [{"text": messy}]})
    edits = client.request("textDocument/formatting", {"textDocument": {"uri": uri},
                                                       "options": {"tabSize": 2, "insertSpaces": True}})
    assert edits[0]["newText"] == "fn f(a, b) {\n    return a + b\n}\n"
    (tmp / "tb.toml").write_text('[fmt]\nindent = "wide"\n', encoding="utf-8")
    assert client.request("textDocument/formatting", {"textDocument": {"uri": uri}, "options": {}}) is None
    note = next(n for n in client.notifications if n.get("method") == "window/showMessage")
    assert "indent" in note["params"]["message"]
