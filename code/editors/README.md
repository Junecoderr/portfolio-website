# Editor support

| Folder | What | Checked by |
|---|---|---|
| `vscode/` | VS Code extension (TextMate grammar, `tb lsp`, debugger) | `tests/tools/test_vscode_grammar.py` |
| `tree-sitter-tb/` | tree-sitter grammar + queries (highlights, locals, folds, indents) | `tree-sitter test`; `tests/tools/test_editors.py` parses every `.tb` file in the repo |
| `neovim/` | Neovim 0.11+ runtime folder: filetype, `tb lsp`, tree-sitter highlighting | `test_editors.py` runs `neovim/test/check.lua` headless |
| `helix/` | `languages.toml` + queries for Helix 25.07+ | `test_editors.py` drives real Helix in a terminal |
| `jetbrains/` | JetBrains plugin: `tb lsp` through LSP4IJ, TextMate highlighting | file checks only (needs Gradle + an IDE) |

User setup is on the site: `site/tools.html#editors`. The editor folders
ship in `tb-python.zip` (`packaging/make_fallback_zip.py`).

## Changing the grammar

The reference is `tbcore/lexer.py` and `tbcore/parser.py`. After editing
`tree-sitter-tb/grammar.js` or `src/scanner.c`:

```
cd code/editors/tree-sitter-tb
tree-sitter generate --abi 14   # ABI 14 loads in Neovim 0.10+ and Helix
tree-sitter test                # corpus (test/corpus) and highlights (test/highlight)
```

Commit `src/` too: Neovim and Helix build from `src/parser.c` without the
CLI. After editing `queries/*.scm`, copy them to `neovim/queries/tb/` and
carry highlight changes over to `helix/queries/tb/highlights.scm` (Helix
scope names; the test checks the two match pattern for pattern).

## Publishing the VS Code extension (owner)

`tb-release.yml` (job `vscode-publish`) publishes the release's `.vsix`
after the GitHub release. Each store is skipped until its token exists:

1. **VS Code Marketplace.** Sign in at marketplace.visualstudio.com/manage
   with a Microsoft account and create the publisher `tb-lang` (the id in
   `vscode/package.json`). In Azure DevOps (dev.azure.com), create a
   personal access token: organisation "All accessible organizations",
   scope Marketplace > Manage. Add it as the repository secret `VSCE_PAT`.
2. **Open VSX.** Sign in at open-vsx.org (Eclipse account), sign the
   publisher agreement, create an access token, then once:
   `npx ovsx create-namespace tb-lang -p <token>`. Add the token as the
   repository secret `OVSX_PAT`.
3. Push the next `tb-v*` tag. The job logs a notice for a store without a
   token and publishes to the other.
