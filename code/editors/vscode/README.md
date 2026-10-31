# TB for Visual Studio Code

Support for the TB programming language (`.tb` files): highlighting, snippets,
run / format / check commands and the TB language server.

## What you get

- **Syntax highlighting** for all of TB 3.0: classes, `match`, f-text with
  `{holes:.2f}`, raw `r"..."`, multi-line `"""..."""` and bytes `b"..."`
  text, numbers like `1_000`, `0x1F`, `0b1010`, `2.5e-3`, type annotations,
  `##` doc comments.
- **Snippets**: `fn`, `class`, `match`, `for`, `try`, `test`, `main` and more.
- **Brackets and comments**: auto-closing `{} [] () "" ''`, `#` toggling with
  Cmd+/ (Ctrl+/), smart indentation.
- **TB: Run File** (Cmd+Alt+N / Ctrl+Alt+N, or the play button): runs the
  file in a terminal with `tb`.
- **TB: Check**: runs `tb check` and shows the findings in the Problems panel.
- **TB: Format**: formats the file with `tb fmt`.
- **TB: Run Tests**: `tb test` in the folder.
- **Language server** (`tb lsp`, TB 3.0 and newer): errors as you type,
  completion for keywords, built-ins, libraries and your own names, hover
  documentation, signature help, go to definition, document symbols,
  formatting (following `[fmt]` in `tb.toml`), find references and rename
  across files and packages, quick fixes for "Did you mean ..." and missing
  imports, and Organize Imports.
- **Debugger** (`tb debug --dap`, TB 3.2 and newer): set breakpoints in the margin and press
  F5. Step over, into and out of functions, see variables and the call
  stack, watch expressions and evaluate them in the Debug Console. With no
  `launch.json` F5 debugs the open file; Run > Add Configuration offers
  **TB: Debug a file** (`"type": "tb"`, `program`, `args`, `cwd`,
  `stopOnEntry`).

## Setup

1. Install TB from <https://code.tanisha.ceo/download.html> so that `tb`
   works in a terminal (`tb --version`).
2. Install this extension (`tb-vscode-3.1.0.vsix` from the Downloads page:
   Extensions view > `...` > *Install from VSIX...*).
3. Open a `.tb` file.

If `tb` isn't on your PATH, set **TB: Path** (`tb.path`) to the full path of
the program. The extension says so once if the language server can't start;
highlighting and snippets keep working either way. With TB 2.x the server,
Format and Check are unavailable (they need TB 3.0), Run File still works.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `tb.path` | `tb` | The tb program to run |
| `tb.languageServer.enabled` | `true` | Start `tb lsp` |
| `tb.languageServer.arguments` | `[]` | Extra arguments for `tb lsp` |
| `tb.check.onSave` | `false` | Run `tb check` on save when the server isn't running |
| `tb.run.saveBeforeRun` | `true` | Save before TB: Run File |
| `tb.trace.server` | `off` | Log client/server messages |

## Building the extension yourself

```
cd editors/vscode
npm install
npm run package        # writes tb-vscode-3.1.0.vsix
```

The client is plain JavaScript (`client/extension.js`), no bundler; the only
runtime dependency is `vscode-languageclient`.

## Example

```tb
import fs

class Alert {
  fn init(host, level) { self.host = host; self.level = level }
  fn text() { return f"{self.host}: {self.level}" }
}

let alerts = fs.read_lines("alerts.txt").map(line => Alert(line, "high"))
let label = match alerts.len() {
  0 => "all quiet"
  n if n < 5 => f"{n} alerts"
  _ => "busy day"
}
say(label)
```
