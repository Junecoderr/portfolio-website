# tb-colors

ANSI colour and style helpers for TB programs that print to a terminal.

```
tb add tb-colors
```

```tb
import "tb_packages/tb-colors/lib.tb" as colors

say(colors.green("ok"), colors.bold("Tanisha's report"), colors.dim("(3 ms)"))
say(colors.on("warning", "yellow"), colors.rgb("orange", 255, 136, 0), colors.hex("teal", "#008080"))
say(colors.status("FAIL"))                      # red; "ok" is green, "warn" is yellow
say(colors.width(colors.red("hello")))          # 5: escape codes don't count
```

## Functions

| Function | What it does |
| --- | --- |
| `red(x) green(x) yellow(x) blue(x) magenta(x) cyan(x) white(x) black(x) gray(x)` | Foreground colours |
| `paint(x, name)` / `on(x, name)` | Any colour by name (incl. `bright_red` ...), as text or background |
| `bold(x) dim(x) italic(x) underline(x) inverse(x) strike(x)` / `style(x, name)` | Styles |
| `rgb(x, r, g, b)` / `on_rgb(x, r, g, b)` / `hex(x, "#ff8800")` | 24-bit colours |
| `hex_to_rgb("#f80")` | `[255, 136, 0]` |
| `strip(x)` / `width(x)` | Remove escape codes / visible length |
| `status(word)` | Colour `ok`/`warn`/`fail`-style words the usual way |
| `enabled()` / `force(true|false|nil)` | Colours are off when `NO_COLOR` is set or `TB_COLOR=0`; `force` overrides |
| `demo()` | Print every colour and style |

Values that aren't text are turned into text first (`colors.green(42)`).

## Tests

```
tb test packages/tb-colors
```
