"""The VS Code extension in editors/vscode/: its JSON files parse, the
TextMate grammar covers every keyword the TB 3.0 design lists, the snippets
and commands are wired up, and the icon is a real PNG."""

from __future__ import annotations

import json
import re
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXT = ROOT / "editors" / "vscode"
DESIGN = ROOT / "DESIGN-3.0.md"


def load(name: str):
    return json.loads((EXT / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def grammar():
    return load("syntaxes/tb.tmLanguage.json")


@pytest.fixture(scope="module")
def package():
    return load("package.json")


def all_regexes(node, out=None):
    """Every match/begin/end regex in the grammar, flattened."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("match", "begin", "end") and isinstance(value, str):
                out.append(value)
            else:
                all_regexes(value, out)
    elif isinstance(node, list):
        for item in node:
            all_regexes(item, out)
    return out


def word_alternatives(regex: str) -> set[str]:
    """The words in alternations like \\b(?:if|else|then)\\b."""
    words = set()
    for group in re.findall(r"\(\?:([A-Za-z_|]+)\)", regex):
        words.update(group.split("|"))
    return words


def grammar_words(grammar) -> set[str]:
    words = set()
    for regex in all_regexes(grammar):
        words |= word_alternatives(regex)
    return words


# --- the files parse ---------------------------------------------------------

@pytest.mark.parametrize("name", [
    "package.json",
    "language-configuration.json",
    "syntaxes/tb.tmLanguage.json",
    "snippets/tb.json",
])
def test_json_files_parse(name):
    data = load(name)
    assert isinstance(data, dict) and data


def test_grammar_regexes_compile(grammar):
    """Every regex must at least be a valid Python regex (close to Oniguruma
    for the constructs we use); backreferences to the begin group are allowed."""
    for regex in all_regexes(grammar):
        candidate = regex.replace("\\2", "x")  # end-pattern backreference to begin
        try:
            re.compile(candidate)
        except re.error as err:
            pytest.fail(f"bad regex {regex!r}: {err}")


# --- keyword coverage from the design --------------------------------------------

def design_new_keywords() -> list[str]:
    text = DESIGN.read_text(encoding="utf-8")
    match = re.search(r"New keywords: `([^`]+)`", text)
    assert match, "DESIGN-3.0.md A1 should list the new keywords in backticks"
    return match.group(1).split()


def test_design_keywords_are_highlighted(grammar):
    words = grammar_words(grammar)
    # `self`, `super` are variable.language rules on their own (no alternation)
    words |= {"self", "super"}
    missing = [kw for kw in design_new_keywords() if kw not in words]
    assert not missing, f"3.0 keywords missing from the grammar: {missing}"


def test_2x_keywords_and_statement_words(grammar):
    import tb

    words = grammar_words(grammar)
    missing = sorted(kw for kw in tb.KEYWORDS if kw not in words)
    assert not missing, f"2.x keywords missing: {missing}"
    statement_words = ["import", "as", "try", "catch", "throw", "break", "continue", "finally",
                       "defer", "assert", "yield", "await", "match", "then", "export", "from"]
    missing = [w for w in statement_words if w not in words]
    assert not missing, f"statement words missing: {missing}"


def test_type_names_and_error_classes(grammar):
    words = grammar_words(grammar)
    for name in "number int float text bool nil list map bytes function class module any".split():
        assert name in words, f"type name {name} not in grammar"
    for name in ("Error", "TypeError", "ValueError", "FileError", "ParseError", "NetworkError",
                 "PermissionError", "NotFoundError", "TimeoutError", "StopIteration"):
        assert name in words, f"error class {name} not in grammar"


def test_builtins_are_highlighted(grammar):
    import tb

    words = grammar_words(grammar)
    builtins = [entry["signature"].split("(")[0] for entry in tb.describe_builtins()["functions"]]
    missing = [b for b in builtins if b not in words]
    assert not missing, f"builtins missing from the grammar: {missing}"


def test_constants(grammar):
    words = grammar_words(grammar)
    assert {"true", "false", "nil", "done"} <= words


# --- the literal rules exist ----------------------------------------------------

def test_string_and_number_rules_present(grammar):
    repo = grammar["repository"]
    for key in ("string-fstring", "string-fstring-triple", "string-raw", "string-raw-triple",
                "string-bytes", "string-triple", "string-double", "string-single", "fstring-hole"):
        assert key in repo, key
    assert repo["string-fstring"]["begin"].startswith("(f)")
    assert repo["string-raw"]["begin"].startswith("(r)")
    assert repo["string-bytes"]["begin"].startswith("(b)")
    hole = json.dumps(repo["fstring-hole"])
    assert "!r" in hole and "format-spec" in hole and "\\\\{\\\\{|\\\\}\\\\}" in hole
    names = {p["name"] for p in repo["numbers"]["patterns"]}
    assert {"constant.numeric.hex.tb", "constant.numeric.binary.tb", "constant.numeric.octal.tb",
            "constant.numeric.float.tb", "constant.numeric.integer.tb"} <= names


@pytest.mark.parametrize("sample,rule", [
    ("0x1F", "constant.numeric.hex.tb"),
    ("0b1010", "constant.numeric.binary.tb"),
    ("0o17", "constant.numeric.octal.tb"),
    ("1_000_000", "constant.numeric.integer.tb"),
    ("1e3", "constant.numeric.float.tb"),
    ("2.5e-3", "constant.numeric.float.tb"),
    ("3.25", "constant.numeric.float.tb"),
])
def test_number_regexes_match_samples(grammar, sample, rule):
    patterns = {p["name"]: p["match"] for p in grammar["repository"]["numbers"]["patterns"]}
    assert re.fullmatch(patterns[rule], sample), f"{sample} should match {rule}"
    # and earlier (more specific) rules don't steal it
    order = [p["name"] for p in grammar["repository"]["numbers"]["patterns"]]
    for earlier in order[:order.index(rule)]:
        assert not re.fullmatch(patterns[earlier], sample), f"{sample} wrongly matches {earlier}"


def test_comments_and_declarations(grammar):
    repo = grammar["repository"]
    names = [p["name"] for p in repo["comments"]["patterns"]]
    assert "comment.line.number-sign.tb" in names and "comment.block.documentation.tb" in names
    assert re.search(repo["function-declaration"]["match"], "export static async fn make(name) {")
    m = re.search(repo["class-declaration"]["match"], "class Dog extends Animal {")
    assert m and m.group(2) == "Dog" and m.group(4) == "Animal"
    assert re.search(repo["enum-declaration"]["match"], "enum Level { low, medium, high }")


# --- package.json wiring --------------------------------------------------------------

def test_package_metadata(package):
    assert package["name"] == "tb-vscode"
    assert package["publisher"] == "tb-lang"
    assert package["version"] == "3.1.0"
    assert package["main"] == "./client/extension.js"
    assert "tb-vscode-3.1.0.vsix" in package["scripts"]["package"]
    assert package["icon"] == "images/icon.png"
    assert "vscode-languageclient" in package["dependencies"]


def test_contributions_point_at_existing_files(package):
    contributes = package["contributes"]
    lang = next(l for l in contributes["languages"] if l["id"] == "tb")
    assert ".tb" in lang["extensions"]
    assert (EXT / lang["configuration"]).is_file()
    grammar = contributes["grammars"][0]
    assert grammar["language"] == "tb" and grammar["scopeName"] == "source.tb"
    assert (EXT / grammar["path"]).is_file()
    assert (EXT / contributes["snippets"][0]["path"]).is_file()
    assert (EXT / package["icon"]).is_file()
    for f in ("README.md", "CHANGELOG.md", "LICENSE", ".vscodeignore"):
        assert (EXT / f).is_file(), f


def test_commands_exist_in_client(package):
    commands = {c["command"] for c in package["contributes"]["commands"]}
    assert {"tb.runFile", "tb.format", "tb.check"} <= commands
    titles = {c["title"] for c in package["contributes"]["commands"]}
    assert {"TB: Run File", "TB: Format", "TB: Check"} <= titles
    source = (EXT / "client" / "extension.js").read_text(encoding="utf-8")
    for command in commands:
        assert f'"{command}"' in source, f"{command} is contributed but never registered"
    assert '"lsp"' in source  # starts `tb lsp`
    assert "warnServerOnce" in source  # degrades gracefully, one message


def test_debugger_contribution(package):
    contributes = package["contributes"]
    assert {"language": "tb"} in contributes["breakpoints"]   # breakpoints can be set in .tb files
    debugger = next(d for d in contributes["debuggers"] if d["type"] == "tb")
    assert debugger["languages"] == ["tb"]
    launch = debugger["configurationAttributes"]["launch"]
    assert launch["required"] == ["program"]
    assert {"program", "args", "cwd", "stopOnEntry"} <= set(launch["properties"])
    first = debugger["initialConfigurations"][0]
    assert first["type"] == "tb" and first["request"] == "launch" and first["program"] == "${file}"
    assert debugger["configurationSnippets"][0]["body"]["type"] == "tb"
    assert "onDebugResolve:tb" in package["activationEvents"]
    source = (EXT / "client" / "extension.js").read_text(encoding="utf-8")
    assert "registerDebugAdapterDescriptorFactory" in source and '["debug", "--dap"]' in source


FAKE_VSCODE = r"""
const Module = require("module");
const made = [];
const vscode = {
  DebugAdapterExecutable: function (command, args, options) { Object.assign(this, { command, args, options }); },
  workspace: {
    getConfiguration: () => ({ get: (key, fallback) => (key === "path" ? "/opt/tb/tb" : fallback) }),
    isTrusted: true,
  },
  window: { activeTextEditor: { document: { languageId: "tb", uri: { fsPath: "/work/app/main.tb" } } },
            showInformationMessage: (m) => made.push(m) },
};
const load = Module._load;
Module._load = function (request, ...rest) {
  if (request === "vscode") return vscode;
  if (request === "vscode-languageclient/node") return {};
  return load.call(this, request, ...rest);
};
const ext = require(process.argv[2]);
const adapter = ext.debugAdapterFactory.createDebugAdapterDescriptor(
  { configuration: { cwd: "/work/app" }, workspaceFolder: undefined });
const blank = ext.debugConfigurationProvider.resolveDebugConfiguration(undefined, {});
const missing = ext.debugConfigurationProvider.resolveDebugConfiguration(
  undefined, { type: "tb", request: "launch", name: "x" });
console.log(JSON.stringify({ adapter, blank, missing: missing === undefined, made }));
"""


def test_debug_adapter_factory_runs_tb_debug_dap(tmp_path):
    """The DebugAdapterDescriptorFactory, run under Node with a stand-in `vscode` module."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node isn't installed")
    script = tmp_path / "fake.js"
    script.write_text(FAKE_VSCODE, encoding="utf-8")
    result = subprocess.run([node, str(script), str(EXT / "client" / "extension.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    assert found["adapter"] == {"command": "/opt/tb/tb", "args": ["debug", "--dap"],
                                "options": {"cwd": "/work/app"}}
    assert found["blank"]["type"] == "tb" and found["blank"]["program"] == "/work/app/main.tb"
    assert found["missing"] is True and "program" in found["made"][0]


def test_settings_have_defaults(package):
    props = package["contributes"]["configuration"]["properties"]
    assert props["tb.path"]["default"] == "tb"
    assert props["tb.languageServer.enabled"]["default"] is True


def test_snippets_are_well_formed():
    snippets = load("snippets/tb.json")
    prefixes = {s["prefix"] for s in snippets.values()}
    assert {"fn", "class", "match", "for", "try", "test", "main"} <= prefixes
    for name, snippet in snippets.items():
        assert isinstance(snippet["body"], list) and snippet["body"], name
        assert snippet["description"], name


def test_language_configuration():
    config = load("language-configuration.json")
    assert config["comments"]["lineComment"] == "#"
    assert ["{", "}"] in config["brackets"]
    opens = {p["open"] for p in config["autoClosingPairs"]}
    assert {"{", "[", "(", '"', "'"} <= opens


def test_icon_is_png():
    data = (EXT / "images" / "icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert width == height and width >= 128
    # the IHDR CRC checks out, so it's not a truncated/odd file
    assert zlib.crc32(data[12:29]) & 0xFFFFFFFF == struct.unpack(">I", data[29:33])[0]
