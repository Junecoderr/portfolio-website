# tblib: TB's standard library

`import name` in a TB program looks here: first for `tblib/name.py` (a
*native* library, written in Python), then for `tblib/name.tb` (a library
written in TB). Names starting with `_` are private and can't be imported.

`tb doc` lists every library; `tb doc name` shows one. The website's library
pages are generated from the same doc strings, so every library and every
function needs one.

## A native library (Python)

Create `tblib/greet.py`:

```python
"""greet: friendly greetings."""

from __future__ import annotations

from tblib._api import MANY, Module, check_type, fail, fail_os


def load(api):
    m = Module("greet", "Friendly greetings.")  # first sentence shows in `tb doc`

    @m.fn("hello", 1, 2, doc="hello(name, end?) -> text\nSay hi to someone.")
    def hello(line, name, end="!"):
        check_type(line, name, "text", "greet.hello", "name")
        return f"Hi {name}{end}"

    @m.fn("all", 1, MANY, doc="all(name, ...) -> list\nGreet everyone.")
    def all_(line, *names):
        return [hello(line, n) for n in names]

    @m.fn("each", 2, doc="each(list, fn)\nCall fn(item) for every item.")
    def each(line, items, fn):
        check_type(line, items, "list", "greet.each", "list")
        check_type(line, fn, "function", "greet.each", "fn")
        for item in items:
            api.call(fn, [item], line)  # call back into TB

    @m.fn("load_names", 1, doc="load_names(path) -> list\nOne name per line.")
    def load_names(line, path):
        check_type(line, path, "text", "greet.load_names", "path")
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError as err:
            fail_os(line, err, path, "read")  # "I couldn't find 'x'. ..."

    m.value("VERSION", "1.0", doc="The library's version.")
    return m
```

Then in TB: `import greet` and `say(greet.hello("Tanisha"))`.

The rules:

* `load(api)` runs once per program, the first time it imports the library.
  Keep imports of heavy or optional things (like `tkinter`) inside functions
  or inside `load`, and make `load` itself never fail, so `tb doc` works on
  every computer and in the browser.
* `@m.fn(name, min_args, max_args=None, doc=...)` registers a function. It is
  called as `fn(line, *args)`: `line` is the TB line of the call, for errors.
  `max_args=None` means exactly `min_args`; `MANY` means any number.
* The `doc` text: first line is the signature (`read(path) -> text`, with
  `?` after optional inputs), then plain-English sentences. Write for a
  beginner.
* `m.value(name, value, doc=...)` adds a constant like `math.pi`.
* Values: nil is `None`, true/false are `bool`, numbers are `int`/`float`,
  text is `str`, lists are `list`, maps are `dict` (keys `str` or numbers).
  Return only those: turn tuples into lists, bytes into text, sets into
  sorted lists. TB functions arrive as objects you call with `api.call`.
* Errors: never let a Python exception escape. Use
  `fail(line, "Plain English, ending with a full stop.")`. It raises a
  `TBError` that TB code can catch (`try { } catch err { }`) and that prints
  as `Oops! On line N: ...` otherwise. Extra keyword arguments become fields
  of the caught error map: `fail(line, "Not found.", status=404)` gives
  `err.status`. `fail_os(line, err, path, "read")` handles `OSError`s.
  (As a safety net TB turns a leaked exception into "Something went wrong
  inside greet.hello(): ...", but that message is not friendly.)
* `check_type(line, value, kind, "lib.fn", "arg")` checks an input and gives
  it back. Kinds: `text number whole bool list map function nil any`, or
  several joined with `|` (`"text|nil"`). `"whole"` gives back an `int`.

### The `api` object

| | |
|---|---|
| `api.call(fn, args, line)` | call a TB function (or built-in) with a list of arguments |
| `api.wrap(fn, line)` | a plain Python callable that calls `fn` (for tkinter callbacks); TB errors still raise `TBError` |
| `api.output(text)` | print a line through TB (tests and the playground capture it) |
| `api.input(prompt)` | read a line like `ask()` (`""` at end of input) |
| `api.base_dir` | folder of the running script (current folder in the REPL) |
| `api.args` | the program's command-line arguments |
| `api.interpreter` | the running `tb.Interpreter` |
| `api.in_browser` | `True` in the web playground (Pyodide) |
| `api.need_desktop(line, "The gui library")` | fail with the standard "needs the TB app on your computer" message when in the browser |
| `api.to_text(value)`, `api.type_of(value)` | how TB shows a value; what `type(x)` says |

Other helpers worth reusing: `tblib.json.to_plain/parse_text`,
`tblib.re.compile_pattern`, `tblib.csv.parse_rows/rows_to_text`,
`tblib.fs.read_text` (reads UTF-8, UTF-16 and Windows files), and from
`tb`: `to_text`, `tb_equal`, `truthy`, `tidy_number`, `tb_round`.

### Testing a native library

Write `tests/lib/test_greet.py`:

```python
from conftest import run_tb, tb_error

def test_hello():
    assert run_tb('import greet\nsay(greet.hello("T"))') == ["Hi T!"]

def test_hello_needs_text():
    assert "greet.hello() needs name to be text, not a number." in str(tb_error("import greet\ngreet.hello(1)"))
```

`tests/test_cli2.py::test_every_stdlib_function_is_documented` checks that
every function in the libraries it lists has a doc string.

## A library written in TB

Create `tblib/kind.tb`:

```
# Kind words for hard days.
# (The comment lines at the top describe the library.)

let WORDS = ["brave", "kind", "clever"]

# Pick a kind word for someone.
fn word_for(name) {
  return f"{name}, you are {WORDS[len(name) % len(WORDS)]}."
}

fn _helper() { }   # names starting with _ stay private
```

Every top-level `let` and `fn` becomes a field (`kind.word_for("T")`,
`kind.WORDS`). The comment lines right above a top-level `fn` become its
documentation for `tb doc kind`. Errors inside the file are reported with its
name: `Oops! On line 7 of kind.tb: ...`.

Your own programs can import their own files the same way, with quotes:
`import "helpers.tb"` (relative to the importing file) or
`import "lib/helpers.tb" as h`.
