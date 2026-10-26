# Moving from TB 2.x to TB 3.0

Almost every 2.x program runs unchanged on 3.0 and prints the same thing. This
page lists what can break and how to fix it. Everything new is described in
`README.md` ("New in 3.0") and the contract is `DESIGN-3.0.md`.

## 1. New reserved words

These words are keywords now and can't be used as variable, function or
parameter names:

    class self super static extends match const export
    from yield async await is enum finally then

TB tells you which one and suggests another name:

    Oops! On line 1: 'match' is a keyword in TB 3.0; call it 'found' instead.

Fix: rename the variable (`match` -> `found`, `from` -> `source`, `is` ->
`flag`, ...). Dot access is not affected: `re.match(...)`, `rec.from`,
`m.class` still work, and map keys can be anything.

## 2. Numbers: whole numbers and fractions are separate

- `/` always divides exactly and gives a fraction (a float). Display is
  unchanged: `6 / 3` still prints `2` and `7 / 2` prints `3.5`.
- New `//` divides and rounds down to a whole number; `%` is the matching
  remainder (`-7 % 3` is `2`, as before).
- `x.is_int()`, `x.is_float()`, `int(x)`, `float(x)` tell them apart;
  `type(x)` is still `"number"` for both.

Nothing should break here: a float with no fraction still works as a list
index or a `range()` size (`xs[4 / 2]`, `range(6 / 2)`), just as in 2.x. Use
`//` when you want a whole number on purpose (`xs[len(xs) // 2]`).

## 3. Errors are typed

`catch err` binds an `Error` object instead of a plain map. `err.message`,
`err.line`, `err.kind` and any extra fields work exactly as before, and so
does `err["message"]`.

What changes is `err.kind` for errors raised by TB itself and by the
standard library. It used to be `"error"` for nearly everything; now it is
the snake_case name of the error class:

| Situation | 2.x `kind` | 3.0 `kind` (class) |
|---|---|---|
| a missing file (`fs.read`, `fs.read_bytes`, ...) | `error` | `not_found_error` (NotFoundError, a FileError) |
| not allowed to read/write a file | `error` | `permission_error` (PermissionError, a FileError) |
| other file problems | `error` | `file_error` (FileError) |
| bad JSON / CSV / regex / base64 / hex | `error` | `parse_error` (ParseError) |
| http: no connection, DNS failure | `error` | `network_error` (NetworkError) |
| http: no answer in time | `error` | `timeout_error` (TimeoutError) |
| a library given the wrong type (`fs.read(5)`) | `error` | `type_error` (TypeError) |
| `"a" - 1`, `-"a"`, calling a non-function | `error` | `type_error` (TypeError) |
| `int("abc")`, `num("abc")`, dividing by zero | `error` | `value_error` (ValueError) |
| `assert` failing | - | `assertion_error` (AssertionError) |

Fix: if you compared `err.kind == "error"`, catch by class instead:
`catch err: FileError { }`, or test `err is Error`. Your own
`throw "text"` and `throw {"message": ...}` still give kind `"error"` (or
the `kind` you put in the map).

## 4. `list * n` repeats the list

`[0] * 3` is now `[0, 0, 0]` (like text: `"ab" * 2`). In 2.x it was an
error. `[1] * [2]` now says a list can only be repeated a whole number of
times.

## 5. Two loop variables over a map give key and value

`for k, v in m { }` iterates pairs; `for k in m { }` still gives keys only,
as in 2.x.

## 6. `type()` knows more kinds

`type(x)` can now also give `"bytes"`, `"range"`, `"generator"`, `"class"`,
`"enum"`, an enum's name (for its values) or a class name (for objects).
Code that assumed `type()` only gives the 2.x names should add a branch.

## 7. Text and bytes literals

- `b"..."`, `r"..."` and `f"..."` (and `rb`, `rf`) are literal prefixes. A
  variable named `b`, `r` or `f` followed directly by text without an
  operator was already a syntax error, so nothing breaks.
- Escapes `\0 \xNN \u{HEX} \'` are new; other unknown escapes like `\q`
  still keep the backslash.
- `"""..."""` is a multi-line text literal.

## 8. Library changes

- `test.results()` has a `"skipped"` count; `test.raises(fn, part)` still
  works and `test.raises(fn, ErrorClass, part?)` is new.
- `hash.md5/sha1/sha256/sha512/hmac_sha256/compare` accept bytes as well as
  text; the wrong-type message says "text or bytes".
- Native (Python) libraries: `fail()` takes an optional `cls=` and there are
  `fail_type/fail_value/fail_file/fail_parse/fail_network/fail_permission/
  fail_not_found/fail_timeout` helpers; `check_type` knows `"bytes"`;
  `native_class` / `method` / `make_object` build objects with methods and
  operators (see `tblib/_api.py`).
- `decimal` values are `Decimal` objects now (they were maps): operators
  work (`decimal.of("0.1") + decimal.of("0.2") == decimal.of("0.3")`) and
  `type(d)` is `"Decimal"`; `.text`, `.number` and every method are as before.

## 9. Embedding TB from Python

- The source is split into `tb.py` plus the `tbcore/` package; bundlers must
  ship `tbcore/` next to `tb.py` (file list in `tbcore/__init__.py`).
  `import tb` still gives the same public API.
- `Interpreter.frames()` is a method (the debugger API: list of `{fn, line,
  file, locals}`); the internal frame list is `Interpreter.call_stack`.
- `Interpreter.trace = fn(event, line, file, env)` and `Interpreter.eval(source,
  env)` are new; tokens carry `comments_before` (with `""` for blank lines
  between comments), `blank_lines_before` and `trailing_comment`.

## Updated in this repository

Sample name in greetings, examples and docs: `welcome.tb`, `tests/welcome.out`,
`examples/hello.tb/.out`, `examples/care_bot.in/.out`, `site/examples.html`,
`scripts/lib_examples/csv.tb`, `README.md`, `tblib/README.md`. No example,
app, library example or playground example needed a keyword rename (every
`.tb` file in the repository parses under 3.0).
