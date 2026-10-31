# TB for JetBrains IDEs

A small plugin for IntelliJ IDEA, PyCharm, WebStorm, GoLand and the other
JetBrains IDEs (2024.3 or newer, Community editions included):

- highlighting from the VS Code extension's TextMate grammar
  (`../vscode/syntaxes/tb.tmLanguage.json`, copied in at build time);
- the TB language server, `tb lsp`, through the
  [LSP4IJ](https://plugins.jetbrains.com/plugin/23257-lsp4ij) plugin:
  diagnostics, completion, hover, go to definition, references, rename,
  formatting and quick fixes.

`tb` must be on the IDE's PATH, or set `TB_PATH` to the full path of `tb`.

## Build

Needs JDK 21 and Gradle 8.10 or newer (or open this folder in IntelliJ IDEA,
which imports the Gradle build).

```
cd code/editors/jetbrains
gradle buildPlugin          # -> build/distributions/tb-jetbrains-3.2.0.zip
gradle runIde               # a sandbox IDE with the plugin, to try it
```

Install the zip with Settings > Plugins > gear icon > Install Plugin from
Disk; the IDE offers to install LSP4IJ, which the plugin needs.

## Check it works

Open a folder with a `.tb` file (for example `code/editors/neovim/test/sample.tb`):

1. keywords, strings and comments are coloured;
2. hovering `add` in `add(1, 2)` shows "Adds two numbers.";
3. Ctrl/Cmd-click on `add` jumps to line 2;
4. `totl` on the last line is underlined: "I don't know what 'totl' is";
5. View > Tool Windows > Language Servers lists TB as running.

Without the plugin, highlighting alone also works: Settings > Editor >
TextMate Bundles > + and pick the `code/editors/vscode` folder.
