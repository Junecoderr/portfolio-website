"""Editor support beyond VS Code (issue #42): the tree-sitter grammar in
editors/tree-sitter-tb, the Neovim and Helix setups, the JetBrains plugin and
the Marketplace / Open VSX release steps.

Tests that need the tree-sitter CLI, nvim or hx skip when it isn't installed;
CI's "Editors" job installs them on Linux."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EDITORS = ROOT / "editors"
GRAMMAR = EDITORS / "tree-sitter-tb"
NEOVIM = EDITORS / "neovim"
HELIX = EDITORS / "helix"
JETBRAINS = EDITORS / "jetbrains"
WORKFLOWS = ROOT.parent / ".github" / "workflows"

TREE_SITTER = shutil.which("tree-sitter")
NVIM = shutil.which("nvim")
HX = shutil.which("hx")
needs_tree_sitter = pytest.mark.skipif(not TREE_SITTER, reason="the tree-sitter CLI is not installed")

QUERIES = ["highlights.scm", "locals.scm", "folds.scm", "indents.scm"]


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("timeout", 120)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)


def tb_files() -> list[Path]:
    skip = {"node_modules", ".venv", "dist", "build"}
    return sorted(p for p in ROOT.rglob("*.tb") if not skip & set(p.relative_to(ROOT).parts))


# -- files that need no tools --------------------------------------------------------------


def test_neovim_queries_match_the_grammar():
    for name in QUERIES:
        canonical = (GRAMMAR / "queries" / name).read_text(encoding="utf-8")
        assert (NEOVIM / "queries" / "tb" / name).read_text(encoding="utf-8") == canonical, (
            f"copy editors/tree-sitter-tb/queries/{name} to editors/neovim/queries/tb/"
        )


HELIX_NAMES = {
    "function.call": "function",
    "function.method.call": "function.method",
    "variable.member": "variable.other.member",
    "module": "namespace",
    "comment.documentation": "comment.line.documentation",
    "number": "constant.numeric",
    "boolean": "constant.builtin.boolean",
    "character.special": "special",
    "keyword.return": "keyword.control.return",
    "keyword.conditional": "keyword.control.conditional",
    "keyword.repeat": "keyword.control.repeat",
    "keyword.import": "keyword.control.import",
    "keyword.exception": "keyword.control.exception",
    "keyword.coroutine": "keyword.control",
}


def captures(text: str) -> list[str]:
    return re.findall(r"@([a-z.]+)", text)


def test_helix_highlights_follow_the_grammar():
    """Helix's highlights.scm is the grammar's, pattern for pattern, with
    Helix scope names (and no Neovim priorities)."""
    canonical = (GRAMMAR / "queries" / "highlights.scm").read_text(encoding="utf-8")
    helix = (HELIX / "queries" / "tb" / "highlights.scm").read_text(encoding="utf-8")
    want = [HELIX_NAMES.get(c, c) for c in captures(canonical)]
    got = captures(helix)
    # Helix splits the plain keywords into storage types/modifiers.
    storage = {"keyword.storage.type", "keyword.storage.modifier"}
    assert [c for c in got if c not in storage] == want
    assert storage <= set(got)
    assert "priority" not in helix


def test_helix_languages_toml():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+

    config = tomllib.loads((HELIX / "languages.toml").read_text(encoding="utf-8"))
    (lang,) = config["language"]
    assert lang["name"] == "tb" and lang["file-types"] == ["tb"]
    assert lang["language-servers"] == ["tb-lsp"]
    assert config["language-server"]["tb-lsp"] == {"command": "tb", "args": ["lsp"]}
    assert config["grammar"][0]["name"] == "tb" and "path" in config["grammar"][0]["source"]
    for name in ("highlights.scm", "indents.scm", "textobjects.scm"):
        assert (HELIX / "queries" / "tb" / name).is_file()


def test_neovim_plugin_files():
    lsp = (NEOVIM / "lsp" / "tb.lua").read_text(encoding="utf-8")
    assert "'tb', 'lsp'" in lsp and "filetypes = { 'tb' }" in lsp
    plugin = (NEOVIM / "plugin" / "tb.lua").read_text(encoding="utf-8")
    assert "extension = { tb = 'tb' }" in plugin and "vim.lsp.enable('tb')" in plugin
    assert "vim.treesitter.start" in (NEOVIM / "ftplugin" / "tb.lua").read_text(encoding="utf-8")


def test_grammar_sources_are_generated():
    grammar_json = json.loads((GRAMMAR / "src" / "grammar.json").read_text(encoding="utf-8"))
    assert grammar_json["name"] == "tb"
    for name in ("parser.c", "scanner.c", "node-types.json", "tree_sitter/parser.h"):
        assert (GRAMMAR / "src" / name).is_file(), name
    # Every reserved word of the real lexer is a keyword of the grammar.
    sys.path.insert(0, str(ROOT))
    from tbcore.lexer import KEYWORDS

    text = (GRAMMAR / "grammar.js").read_text(encoding="utf-8")
    missing = [w for w in sorted(KEYWORDS) if f"'{w}'" not in text]
    assert not missing


def test_jetbrains_plugin_files():
    plugin_xml = (JETBRAINS / "src" / "main" / "resources" / "META-INF" / "plugin.xml").read_text(encoding="utf-8")
    assert "<depends>com.redhat.devtools.lsp4ij</depends>" in plugin_xml
    assert 'patterns="*.tb"' in plugin_xml
    factory = re.search(r'factoryClass="([\w.]+)"', plugin_xml).group(1)
    source = JETBRAINS / "src" / "main" / "kotlin" / (factory.replace(".", "/") + ".kt")
    assert source.is_file() and '"lsp"' in source.read_text(encoding="utf-8")
    build = (JETBRAINS / "build.gradle.kts").read_text(encoding="utf-8")
    assert "com.redhat.devtools.lsp4ij" in build


def test_vscode_marketplace_metadata_and_release_steps():
    package = json.loads((EDITORS / "vscode" / "package.json").read_text(encoding="utf-8"))
    assert package["publisher"] == "tb-lang"
    for key in ("name", "displayName", "description", "version", "license", "icon", "engines", "categories"):
        assert package.get(key), key
    release = (WORKFLOWS / "tb-release.yml").read_text(encoding="utf-8")
    # Publishing runs only when the owner has added the tokens; otherwise it is skipped.
    assert "secrets.VSCE_PAT" in release and "secrets.OVSX_PAT" in release
    assert "vsce publish" in release and re.search(r"ovsx@[\d.]+ publish", release)
    assert "has_vsce_pat == 'true'" in release and "has_ovsx_pat == 'true'" in release


# -- tree-sitter -----------------------------------------------------------------------------


@needs_tree_sitter
def test_tree_sitter_corpus_and_highlight_tests():
    result = run([TREE_SITTER, "test"], cwd=GRAMMAR, timeout=300)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "failed parses: 0" in out and "assertions" in out


@needs_tree_sitter
def test_every_tb_file_in_the_repo_parses_without_errors(tmp_path):
    files = tb_files()
    assert len(files) > 100
    listing = tmp_path / "files.txt"
    listing.write_text("\n".join(str(p) for p in files) + "\n", encoding="utf-8")
    result = run([TREE_SITTER, "parse", "--quiet", "--stat", "--paths", str(listing)], cwd=GRAMMAR, timeout=300)
    out = result.stdout + result.stderr
    found = re.search(r"Total parses: (\d+); successful parses: (\d+); failed parses: (\d+)", out)
    assert found, out
    total, ok, failed = map(int, found.groups())
    assert (total, ok, failed) == (len(files), len(files), 0), out


@needs_tree_sitter
def test_every_query_compiles_against_the_grammar():
    sample = NEOVIM / "test" / "sample.tb"
    queries = [GRAMMAR / "queries" / q for q in QUERIES] + sorted((HELIX / "queries" / "tb").glob("*.scm"))
    for query in queries:
        result = run([TREE_SITTER, "query", str(query), str(sample)], cwd=GRAMMAR)
        assert result.returncode == 0 and "capture:" in result.stdout, f"{query.name}: {result.stderr}"


@needs_tree_sitter
def test_generated_parser_is_up_to_date(tmp_path):
    result = run([TREE_SITTER, "generate", "--abi", "14", "-o", str(tmp_path), "grammar.js"], cwd=GRAMMAR, timeout=300)
    assert result.returncode == 0, result.stderr
    for name in ("grammar.json", "node-types.json", "parser.c"):
        # The first line of parser.c names the CLI build, which differs between installs.
        fresh, kept = ((d / name).read_text(encoding="utf-8").split("\n", 1)[-1] for d in (tmp_path, GRAMMAR / "src"))
        assert fresh == kept, (
            f"src/{name} is stale: run `tree-sitter generate --abi 14` in editors/tree-sitter-tb"
        )


@pytest.fixture(scope="module")
def built_parser(tmp_path_factory):
    if not TREE_SITTER:
        pytest.skip("the tree-sitter CLI is not installed")
    rt = tmp_path_factory.mktemp("nvim-runtime")
    (rt / "parser").mkdir()
    result = run([TREE_SITTER, "build", "-o", str(rt / "parser" / "tb.so"), str(GRAMMAR)], timeout=300)
    assert result.returncode == 0, result.stderr
    return rt


# -- Neovim ------------------------------------------------------------------------------------


@pytest.mark.skipif(not NVIM, reason="nvim is not installed")
def test_neovim_highlighting_and_lsp(built_parser):
    """Opens editors/neovim/test/sample.tb headless: tree-sitter captures, and
    `tb lsp` attached with hover, go-to-definition and a diagnostic."""
    lsp_cmd = json.dumps([sys.executable, str(ROOT / "tb.py"), "lsp"])
    rtp = f"{NEOVIM.as_posix()},{built_parser.as_posix()}"
    result = run(
        [NVIM, "--headless", "--clean",
         "--cmd", f"set rtp^={rtp}",
         "--cmd", f"lua vim.g.tb_lsp_cmd = vim.json.decode([=[{lsp_cmd}]=])",
         "-c", f"luafile {(NEOVIM / 'test' / 'check.lua').as_posix()}"],
        timeout=120,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert lines, result.stdout + result.stderr
    report = json.loads(lines[-1])
    assert report["ok"], report["failures"]
    assert result.returncode == 0
    assert report["filetype"] == "tb" and report["highlighter"] and not report["parse_error"]
    assert report["lsp_attached"] and report["server"]["name"] == "tb lsp"
    assert report["cmd"][0] == sys.executable, report["cmd"]  # this checkout, not some other tb
    assert "Adds two numbers" in report["hover"]
    assert report["definition_line"] == 1
    assert any("totl" in d for d in report["diagnostics"])


# -- Helix -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def helix_config(tmp_path_factory):
    """A private Helix config dir (XDG_CONFIG_HOME) with TB set up as the
    docs describe, the language server being this checkout's tb.py."""
    if not HX:
        pytest.skip("hx is not installed")
    if os.name == "nt":
        pytest.skip("Helix reads its config from %AppData% on Windows")
    home = tmp_path_factory.mktemp("helix-config")
    shutil.copytree(HELIX / "queries", home / "helix" / "runtime" / "queries")
    (home / "helix" / "runtime" / "grammars").mkdir()
    text = (HELIX / "languages.toml").read_text(encoding="utf-8")
    text = text.replace("/path/to/code/editors/tree-sitter-tb", GRAMMAR.as_posix())
    text = text.replace('command = "tb"\nargs = ["lsp"]',
                        f'command = "{Path(sys.executable).as_posix()}"\nargs = ["{(ROOT / "tb.py").as_posix()}", "lsp"]')
    (home / "helix" / "languages.toml").write_text('use-grammars = { only = ["tb"] }\n' + text, encoding="utf-8")
    env = dict(os.environ, XDG_CONFIG_HOME=str(home))
    result = run([HX, "--grammar", "build"], env=env, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    return env


def test_helix_health(helix_config):
    result = run([HX, "--health", "tb"], env=helix_config)
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + result.stderr)
    assert result.returncode == 0, out
    assert f"✓ tb-lsp: {Path(sys.executable).as_posix()}" in out, out
    for what in ("Tree-sitter parser", "Highlight queries", "Textobject queries", "Indent queries"):
        assert f"{what}: ✓" in out, out


class Terminal:
    """Helix running in a pseudo-terminal; keys in, screen bytes out."""

    def __init__(self, argv, env, cwd):
        import fcntl
        import pty
        import struct
        import termios

        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = dict(env, TERM="xterm-256color")
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd,
                                     start_new_session=True)
        os.close(slave)
        self.screen = b""

    def read(self, seconds: float) -> None:
        import select
        import time

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    self.screen += os.read(self.master, 65536)
                except OSError:
                    return

    def keys(self, text: str, expect: str, seconds: float = 10.0, settle: float = 0.0) -> str:
        """Type keys and wait until the new screen output shows `expect` (a
        regex on the text with escape codes removed), then `settle` seconds
        more. Returns that text."""
        import time

        start = len(self.screen)
        os.write(self.master, text.encode())
        end = time.monotonic() + seconds
        while True:
            self.read(0.2)
            shown = re.sub(rb"\x1b\[[0-9;?]*[ A-Za-z]", b" ", self.screen[start:]).decode("utf-8", "replace")
            if re.search(expect, shown) or time.monotonic() > end:
                break
        self.read(settle)
        return re.sub(rb"\x1b\[[0-9;?]*[ A-Za-z]", b" ", self.screen[start:]).decode("utf-8", "replace")

    def close(self) -> None:
        os.write(self.master, b"\x1b\x1b:q!\r")
        for _ in range(30):  # keep reading so Helix never blocks on a full terminal
            self.read(0.1)
            if self.proc.poll() is not None:
                break
        os.close(self.master)
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX pseudo-terminal")
def test_helix_highlighting_and_lsp(helix_config):
    """Opens editors/neovim/test/sample.tb in real Helix: asks it for the
    highlight scope at several places, then for hover, go-to-definition and
    the diagnostics list from `tb lsp`."""
    sample = (NEOVIM / "test" / "sample.tb").relative_to(ROOT).as_posix()
    term = Terminal([HX, sample], helix_config, cwd=ROOT)
    try:
        term.read(2)
        scopes = [  # 1-based line and column -> the query's Helix scope
            (1, 1, "comment.line.documentation"),
            (2, 1, "keyword.function"),
            (2, 4, "function"),
            (2, 8, "variable.parameter"),
            (3, 3, "keyword.control.return"),
            (6, 13, "function"),
            (6, 17, "constant.numeric"),
            (7, 1, "function.builtin"),
        ]
        seen = {}
        for line, col, scope in scopes:
            move = f"{line}gggh" + (f"{col - 1}l" if col > 1 else "")
            reply = rf"\b{line}:{col}\s+([a-z][\w.]*)"
            # The last match: while the command is typed, its completions show too.
            found = re.findall(reply, term.keys(move + ":tree-sitter-highlight-name\r", reply, settle=0.6))
            found = found and found[-1]
            seen[f"{line}:{col}"] = found
            # Helix names the nearest scope its theme knows: keyword.function
            # shows as "keyword" in the default theme.
            assert found and (scope + ".").startswith(found + "."), f"{scope}: {seen}"
        print("helix scopes:", seen)

        shown = term.keys("6gggh12l k", r"Adds two numbers", 20)  # hover on add(1, 2)
        assert "Adds two numbers" in shown, shown[-500:]
        term.keys("\x1b", r"$^", 0.5)  # alone, or Escape + g would read as Alt-g
        shown = term.keys("gd", r"\b2:4\b")  # go to the definition of add
        assert re.search(r"\b2:4\b", shown), shown[-500:]
        shown = term.keys(" d", r"undefined-name")  # the diagnostics picker
        assert "undefined-name" in shown, shown[-500:]
    finally:
        term.close()
