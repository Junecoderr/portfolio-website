# Modules and packages

A long program is easier to read in parts. TB has three kinds of parts: the libraries that come with TB, your own files, and packages that other people share.

## Use a library

`import` brings in a library. Use its functions with a dot. `as` gives the library a shorter name. `from ... import` brings in only the names you need.

```tb
import re
import encode as enc
from text import pad_left, title

say(re.find_all(r"\d+\.\d+\.\d+\.\d+", "from 203.0.113.42 to 198.51.100.7"))
say(enc.base64("secret message"))
say(pad_left("7", 3, "0"), title("incident report"))
```

TB has 44 libraries. The [library reference](site:library.html) lists every function.

## Split a program into files

Put `export` before a function or constant that other files can use. Then import the file by its name. TB looks for the file next to the program that imports it.

```tb
import fs

# Make a small helper file (normally you write it in your editor).
fs.write("helpers.tb", 'export fn shout(t) { return t.upper() + "!" }\nexport const GREETING = "Hello"\n')

from "helpers.tb" import shout, GREETING as hi
say(shout(hi))
```

A name without `export` stays private to its file. This keeps each file small and clear.

## Settings from outside

`os.env` reads an environment variable. Give a default for when it is not set. Keep secrets like API keys in environment variables, never in your code.

```tb
import os

say(os.env("TB_BOOK_LEVEL", "medium"))
say(type(os.args))
```

## Text from a template

The `template` library fills holes in text. It escapes HTML for you, so data from a log cannot change your page.

```tb
import template

let page = "<h1>{{title}}</h1>{{#alerts}}<li>{{rule}} from {{ip}}</li>{{/alerts}}"
say(template.render(page, {
  "title": "Alerts",
  "alerts": [{"rule": "Brute force", "ip": "203.0.113.42"}, {"rule": "<script>", "ip": "x"}],
}))
```

## Packages

A package is a folder of TB files that someone shares. Start a project with `tb init`, then add a package with `tb add`. TB puts the package in the `tb_packages` folder and writes its name and version to `tb.toml`.

```text
tb init triage-tool
tb add tb-tables
```

Then import the package file:

```tb norun
import "tb_packages/tb-tables/lib.tb" as tables

let rows = [{"host": "web01", "alerts": 12}, {"host": "db01", "alerts": 3}]
say(tables.table(rows))
```

See [Packages](site:packages.html) for the packages you can add, and how to publish your own.

## What you learned

- `import`, `as` and `from ... import` bring in libraries and files.
- `export` shares a name from a file.
- `os.env` reads settings. `template` fills text safely. `tb add` adds packages.

Next, you read and write files in many formats.
