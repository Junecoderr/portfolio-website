# The TB Language Specification (3.0, draft)

Status: **draft for wave 1**. This document is written from `DESIGN-3.0.md`
Part A. Wave 2 (once the 3.0 parser in `tbcore/` is final) checks every rule
against the implementation, adds the `spec/*_test.tb` conformance tests and
removes the "(design)" markers. Where the design leaves a choice open, the
choice taken here is marked **(choice)** so the core-agent can confirm or
correct it.

TB is a small, friendly, dynamically typed language with gradual (ignored at
runtime) type annotations. Programs are UTF-8 text files ending in `.tb`.
The reference implementation is in Python; `tb file.tb` runs a program.

Contents

1. Lexical structure
2. Grammar (EBNF)
3. Values and types
4. Expressions and operators
5. Statements
6. Functions
7. Classes, enums and modules
8. Errors
9. Generators and iteration
10. Patterns (`match`)
11. Formatting (`f"..."`, `format`)
12. Program structure, imports and packages
13. Compatibility with TB 2.x

---

## 1. Lexical structure

### 1.1 Source text

A program is a sequence of Unicode characters. Lines end in `\n` (`\r\n` is
accepted). Indentation has no meaning. Statements are separated by newlines
or `;`. A newline inside `( )`, `[ ]` or `{ }` of a literal or call does not
end a statement **(choice: as in 2.x)**.

### 1.2 Comments

```
comment      = "#" { any character except newline } .
doc-comment  = "##" { any character except newline } .
```

Comments run to the end of the line. A `##` comment directly above a `fn`,
`class`, `enum`, `const` or `export` declaration is its documentation
(`tb doc` shows it; the parser keeps comments on statement nodes as
`comments`, `blank_before` and `trailing_comment` so the formatter can
preserve them).

### 1.3 Identifiers and keywords

```
identifier = ( letter | "_" ) { letter | digit | "_" } .
letter     = "A" … "Z" | "a" … "z" | any Unicode letter .
digit      = "0" … "9" .
```

Reserved words (cannot be used as names; using one is an error with a hint):

```
and      as       assert   async    await    break    catch    class
const    continue defer    done     else     enum     export   extends
false    finally  fn       for      from     if       import   in
is       let      match    nil      not      or       return   self
static   super    then     throw    true     try      while    yield
```

Notes

- `done` is a global constant (the iterator sentinel), reserved like `true`.
- `_` is an ordinary identifier except as a pattern, where it matches
  anything without binding.
- Dot access is never affected: `re.match`, `obj.match`, `m.from` are fine.
- In 2.x `import as try catch throw break continue` were only special at
  the start of a statement; in 3.0 they are reserved (Breaking, see §13).

### 1.4 Literals

```
number      = int-literal | float-literal .
int-literal = dec-int | "0x" hex-digit { hex-digit | "_" }
            | "0b" bin-digit { bin-digit | "_" }
            | "0o" oct-digit { oct-digit | "_" } .
dec-int     = digit { digit | "_" } .
float-literal = dec-int "." dec-int [ exponent ] | dec-int exponent .
exponent    = ( "e" | "E" ) [ "+" | "-" ] dec-int .
```

Underscores are separators only (`1_000_000`), never leading, trailing or
doubled. `1e3` and `2.5e-3` are floats. Int literals have unlimited size.

```
text-literal   = [ "f" | "r" | "b" ] ( short-text | long-text ) .
short-text     = '"' { text-char | escape | hole } '"'
               | "'" { text-char | escape | hole } "'" .
long-text      = '"""' { any character | escape | hole } '"""' .
escape         = "\n" | "\t" | "\r" | "\0" | "\\" | '\"' | "\'"
               | "\x" hex-digit hex-digit | "\u{" hex-digit { hex-digit } "}" .
hole           = "{" expression [ "!r" ] [ ":" format-spec ] "}" .    (only in f-text)
```

- Plain text: escapes are processed; a short text may not contain a raw
  newline (use `"""` or `\n`).
- `r"..."` raw text: no escapes at all (`r"\d+"` is four characters).
- `f"..."`: holes `{expr}` are evaluated and inserted; `{{` and `}}` are
  literal braces; see §11 for `!r` and format specs. An empty `{}` is an
  error. `f"""..."""` and `r"""..."""` combine the prefixes with long text.
- `b"..."`: bytes. Only ASCII characters and the escapes `\n \t \r \0 \\
  \" \' \xNN`; a non-ASCII character is an error.

### 1.5 Operators and punctuation

```
+  -  *  /  //  %  **     &  |  ^  ~  <<  >>     ==  !=  <  <=  >  >=
=  +=  -=  *=  /=  //=  %=  **=  &=  |=  ^=  <<=  >>=
..  ..=  ...  =>  ->  .  ,  :  ;  (  )  [  ]  {  }
```

Word operators: `and or not is in`. `not in` is written `not (x in y)` or
`x not in y`? **(choice: `not x in y` parses as `not (x in y)`; there is no
`not in` token.)**

---

## 2. Grammar (EBNF)

Conventions: `{ x }` repetition, `[ x ]` optional, `( a | b )` choice,
terminals in quotes. `NEWLINE` is a statement separator (a newline not
inside brackets, or `;`). `EOF` ends the program.

```
program        = { statement } EOF .

statement      = simple-stmt NEWLINE
               | compound-stmt .

simple-stmt    = let-stmt | const-stmt | assign-stmt | expr-stmt
               | return-stmt | break-stmt | continue-stmt | throw-stmt
               | import-stmt | from-stmt | export-stmt | assert-stmt
               | yield-stmt .

compound-stmt  = if-stmt | while-stmt | for-stmt | match-stmt | try-stmt
               | fn-decl | class-decl | enum-decl | defer-stmt | block .

block          = "{" { statement } "}" .

(* declarations *)
let-stmt       = "let" binding-list "=" expr-list .
const-stmt     = "const" identifier [ type-ann ] "=" expression .
binding-list   = binding { "," binding } .
binding        = identifier [ type-ann ] | list-pattern | map-pattern .
expr-list      = expression { "," expression } .           (* a, b = f() *)

assign-stmt    = target-list assign-op expr-list .
target-list    = target { "," target } .
target         = postfix-expr .                              (* name, a.b, a[i] *)
assign-op      = "=" | "+=" | "-=" | "*=" | "/=" | "//=" | "%=" | "**="
               | "&=" | "|=" | "^=" | "<<=" | ">>=" .

expr-stmt      = expression .
return-stmt    = "return" [ expr-list ] .
break-stmt     = "break" .
continue-stmt  = "continue" .
throw-stmt     = "throw" expression .
assert-stmt    = "assert" expression [ "," expression ] .
yield-stmt     = "yield" [ expression ] .
defer-stmt     = "defer" block .

import-stmt    = "import" ( identifier | text-literal ) [ "as" identifier ] .
from-stmt      = "from" ( identifier | text-literal ) "import" import-name { "," import-name } .
import-name    = identifier [ "as" identifier ] .
export-stmt    = "export" ( fn-decl | class-decl | enum-decl | let-stmt | const-stmt ) .

(* control flow *)
if-stmt        = "if" expression block { "else" "if" expression block } [ "else" block ] .
while-stmt     = "while" expression block .
for-stmt       = "for" binding-list "in" expression block .
match-stmt     = match-expr .
try-stmt       = "try" block { catch-clause } [ "finally" block ] .
catch-clause   = "catch" [ identifier [ ":" type-name { "or" type-name } ] ] block .

(* functions *)
fn-decl        = [ "async" ] "fn" identifier params [ "->" type ] block .
params         = "(" [ param { "," param } ] ")" .
param          = binding [ "=" expression ]
               | "..." identifier
               | "**" identifier .
lambda         = lambda-params "=>" ( expression | block ) .
lambda-params  = identifier | "(" [ param { "," param } ] ")" .
fn-expr        = [ "async" ] "fn" params [ "->" type ] block .

(* classes *)
class-decl     = "class" identifier [ "extends" identifier ] "{" { class-member } "}" .
class-member   = [ "static" ] ( "let" identifier [ type-ann ] [ "=" expression ] NEWLINE
                              | [ "async" ] "fn" identifier params [ "->" type ] block ) .
enum-decl      = "enum" identifier "{" identifier { "," identifier } [ "," ] "}" .

(* types: parsed, kept on nodes, ignored at runtime *)
type-ann       = ":" type .
type           = union-type .
union-type     = simple-type { "|" simple-type } .
simple-type    = type-name [ "<" type { "," type } ">" ]
               | "fn" "(" [ type { "," type } ] ")" [ "->" type ] .
type-name      = identifier .      (* number int float text bool nil list map bytes
                                      function class module any, class names, Error family *)

(* expressions, lowest precedence first *)
expression     = cond-expr .
cond-expr      = "if" or-expr "then" cond-expr "else" cond-expr
               | lambda
               | or-expr .
or-expr        = and-expr { "or" and-expr } .
and-expr       = not-expr { "and" not-expr } .
not-expr       = "not" not-expr | comparison .
comparison     = bit-or { comp-op bit-or } .              (* chained: 0 < x <= 10 *)
comp-op        = "==" | "!=" | "<" | "<=" | ">" | ">=" | "in" | "not" "in" | "is" .
bit-or         = bit-xor { "|" bit-xor } .
bit-xor        = bit-and { "^" bit-and } .
bit-and        = shift { "&" shift } .
shift          = range-expr { ( "<<" | ">>" ) range-expr } .
range-expr     = additive [ ( ".." | "..=" ) additive ] .
additive       = term { ( "+" | "-" ) term } .
term           = unary { ( "*" | "/" | "//" | "%" ) unary } .
unary          = ( "-" | "+" | "~" ) unary | power .
power          = postfix-expr [ "**" unary ] .            (* right-associative *)
postfix-expr   = primary { call | index | slice | member } .
call           = "(" [ argument { "," argument } [ "," ] ] ")" .
argument       = expression | identifier "=" expression | "..." expression .
index          = "[" expression "]" .
slice          = "[" [ expression ] ":" [ expression ] [ ":" [ expression ] ] "]" .
member         = "." identifier .

primary        = number | text-literal | "true" | "false" | "nil" | "done"
               | "self" | "super" | identifier
               | list-literal | map-literal | fn-expr | match-expr
               | "(" expression ")" .
list-literal   = "[" [ list-item { "," list-item } [ "," ] ] "]" .
list-item      = expression | "..." expression .
map-literal    = "{" [ map-item { "," map-item } [ "," ] ] "}" .
map-item       = expression ":" expression | "..." expression .

(* match *)
match-expr     = "match" expression "{" { match-arm } "}" .
match-arm      = pattern [ "if" expression ] "=>" ( expression | block ) NEWLINE .
pattern        = or-pattern .
or-pattern     = simple-pattern { "or" simple-pattern } .
simple-pattern = literal-pattern | range-pattern | "_" | identifier
               | list-pattern | map-pattern | class-pattern | "is" type-name .
literal-pattern = number | [ "-" ] number | text-literal | "true" | "false" | "nil" .
range-pattern  = literal-pattern ( ".." | "..=" ) literal-pattern .
list-pattern   = "[" [ pattern-item { "," pattern-item } ] "]" .
pattern-item   = pattern | "..." [ identifier ] .
map-pattern    = "{" [ map-pat-item { "," map-pat-item } ] "}" .
map-pat-item   = text-literal ":" pattern | identifier [ "=" expression ] | identifier ":" pattern | "..." [ identifier ] .
class-pattern  = identifier "(" [ pattern ] ")" | identifier "{" { map-pat-item } "}" .
```

Ambiguities resolved

- `{` after `match x`, `if c`, `while c`, `for … in e`, `fn (…)` and
  `class X` begins a block, never a map literal. A map literal in those
  positions needs parentheses: `if (m == {"a": 1}) { … }` **(choice)**.
- A statement starting with `{` is a block, not a map **(choice)**.
- A lambda `x => …` is recognised when an identifier (or parenthesised
  parameter list) is followed by `=>`.
- `a..b step n` is not syntax; use `(a..b).step(n)`.
- `fn f(a, b)` declares; `fn(a, b) { }` is an expression.

---

## 3. Values and types

| `type(x)` | Values | Notes |
| --- | --- | --- |
| `"number"` | ints (unlimited) and floats (IEEE double) | `x.is_int()`, `x.is_float()`, `int(x)`, `float(x)`, `x.to_int()` |
| `"text"` | immutable Unicode text | indexing `t[i]`, slicing `t[a:b]`, negative indexes |
| `"bytes"` | immutable byte sequences | `b"..."`, `text.bytes()`, `data[i]` is an int |
| `"bool"` | `true`, `false` | |
| `"nil"` | `nil` | |
| `"list"` | ordered, mutable | |
| `"map"` | insertion-ordered, mutable; keys are text or numbers | instances are not allowed as keys |
| `"function"` | fns, lambdas, methods, built-ins | `.name`, `.arity`, `.call(list)`, `.bind(...)` |
| `"class"` | classes | `type(Dog)` |
| *class name* | instances, e.g. `"Dog"` | |
| `"module"` | imported libraries and files | |
| `"range"` | `a..b`, `a..=b` lazy | iterable, `in`, `len()`, `to_list()`, `.step(n)` **(choice: type name `"range"`)** |
| `"generator"` | result of calling a `yield`ing fn | iterator with `next()` **(choice: type name `"generator"`)** |
| enum values | `Level.low` | `type(Level.low)` is `"Level"` **(choice)**; `.name`, `.index` |

Truthiness: `false` and `nil` are false; everything else (including `0`,
`""`, `[]`) is true **(as in 2.x)**.

Equality `==`: numbers by value (`1 == 1.0`), text/bytes by content, lists
and maps item by item, functions/modules/classes by identity, instances by
identity unless the class defines `equals(other)`; enum values by identity.

Ordering `< <= > >=`: numbers, text (code-point order), lists (lexicographic)
**(choice)**, instances via `compare(other)`. Mixing kinds is an error.

### 3.1 Numbers

- `+ - *` on two ints give an int; `/` always gives a float; `//` floor
  division; `%` floor modulo (sign of the divisor); `**` power (an int
  result stays an int when the exponent is a non-negative int).
- Display is tidy: a float with no fractional part prints as an int (`6 / 3`
  → `2`, `7 / 2` → `3.5`), so 2.x programs print the same.
- Bitwise `& | ^ ~ << >>` need ints; a float operand is an error.
- Comparisons chain: `0 < x <= 10` means `0 < x and x <= 10` (x evaluated once).
- Guards: a float overflow (beyond about 1e308) and `**` results beyond
  ~10 000 bits stop with a friendly "too big" error.

### 3.2 Text and bytes

Indexing and slicing follow Python's rules: `t[0]` first character, `t[-1]`
last, `t[a:b]` from `a` up to (not including) `b`, `t[::2]` every second,
`t[::-1]` reversed; out-of-range indexes are errors, out-of-range slices
are clipped. The same applies to lists and bytes.

Bytes methods: `len() hex() slice(a, b?) to_text(encoding="utf-8") at(i)
starts_with(b) find(b) split(b) join(list) reverse() to_list() copy()`;
`+` concatenates; `for byte in data` yields ints 0–255.

---

## 4. Expressions and operators

Precedence, highest first (see the EBNF for the exact shape):

1. member `.`, call `()`, index `[]`, slice
2. `**` (right-assoc; binds tighter than unary minus on its left: `-2 ** 2` is `-4`)
3. unary `- + ~`
4. `* / // %`
5. `+ -`
6. `.. ..=`
7. `<< >>`
8. `&`
9. `^`
10. `|`
11. comparisons `== != < <= > >= in is` (chained)
12. `not`
13. `and` (short-circuit)
14. `or` (short-circuit; the value is the first truthy operand or the last operand)
15. `if … then … else …`, lambdas `=>`

`and`/`or` return an operand, not a bool **(as in 2.x)**. `x is T` is true
when `type(x)` is `T` or, for classes, when `x` is an instance of `T` or a
subclass. `x in y` means: item of a list/range, key of a map, substring of
text, sub-sequence of bytes, or `contains(x)` on an instance.

Spread: `f(...xs)` passes items as arguments; `[...a, ...b]`; `{...m, "k": 1}`
(later keys win).

Conditional expression `if c then a else b` evaluates only the chosen branch.

---

## 5. Statements

- `let` declares in the current scope (block scoped: `if`, `while`, `for`,
  `match` arms, `fn` bodies and `{ }` blocks each open a scope). Re-declaring
  a name in the same scope is an error; assigning to an undeclared name is
  an error with a hint ("Did you mean let x = …?") **(as in 2.x)**.
- Destructuring `let [a, b, ...rest] = xs`, `let {name, ip: address, port =
  22} = rec` (missing key without a default → error), nested freely.
- `let a, b = f()` unpacks a list of exactly two items; `return a, b`
  returns a list.
- Swap: `a, b = b, a` evaluates the right side fully before assigning.
- Compound assignment `x op= e` is `x = x op e` (target evaluated once).
- `const NAME = v`: reassignment anywhere is an error.
- `if`/`else if`/`else`, `while`, `for x in iterable` (lists, maps → keys;
  `for k, v in map` → pairs; text → characters; ranges; bytes → ints;
  generators; instances with `iter()`), `break`, `continue`.
- `defer { … }` inside a function body registers a block to run when the
  function returns or throws, in reverse order of registration.
- `assert cond, "message"` throws `AssertionError` when `cond` is false.
- `yield` makes the enclosing `fn` a generator (§9).
- `return` outside a function is an error.

---

## 6. Functions

```
fn greet(name, greeting = "Hello", ...rest, **opts) -> text { … }
```

- Parameters: positional, with defaults (evaluated at call time, left to
  right), one `...rest` (list of extra positionals), and one trailing
  `**opts` (map of named arguments not matching a parameter). Any parameter
  may be passed by name at the call: `greet("Sam", greeting = "Hi")`.
  Positional after named, duplicate names, missing required or unexpected
  arguments are errors.
- Lambdas `x => x * 2`, `(a, b) => a + b`, `() => 1`, `x => { … }` (block
  body: value is the explicit `return`, else nil **(choice)**).
- `fn(x) { … }` expressions still work.
- Closures capture variables by reference (a counter closure works).
- Function values: `.name` (text, `"<lambda>"` for lambdas), `.arity`
  (number of required parameters), `.call(list)`, `.bind(...args)` (partial
  application from the left).
- Recursion depth is limited (friendly "too deep" error).

---

## 7. Classes, enums and modules

### 7.1 Classes

```
class Animal {
  let sound = "..."          # instance field default (evaluated per instance)
  static let count = 0       # class field: Animal.count
  fn init(name) { self.name = name; Animal.count += 1 }
  fn speak() { return f"{self.name} says {self.sound}" }
  static fn make(name) { return Animal(name) }
  fn text() { return f"Animal({self.name})" }
}
class Dog extends Animal {
  let sound = "woof"
  fn init(name) { super.init(name); self.tricks = [] }
  fn speak() { return super.speak() + "!" }
}
```

- `self` is implicit in methods (not a parameter). `super.method(...)`
  calls the parent's method with the same `self`.
- Construction: `Dog("Rex")` makes an instance, sets field defaults (parent
  first), then calls `init` if defined. Without `init`, positional
  arguments are an error.
- Single inheritance. Method lookup: own class, then parents. Fields live on
  the instance; `instance.fields()` returns a copy as a map. Reading a
  missing field is an error with "Did you mean".
- Special methods: `text()` (used by `say`/`str`/f-text), `equals(other)`
  (`==`), `compare(other)` (returns negative/zero/positive; used by `<` and
  `sort`), `get(i)`/`set(i, v)` (indexing), `call(...)` (callable instance),
  `iter()` (returns an iterator: any value with `next()` that returns `done`
  when finished), `len()`, `contains(x)` (`in`).
- `type(instance)` is the class name; `type(Dog)` is `"class"`; `d is Animal`.
- Display without `text()`: `Dog {name: "Rex", tricks: []}`.
- Private by convention: `_name` (no enforcement; `tb check` warns).
- Classes are first-class values; `Dog.name` is `"Dog"` **(choice)**.

### 7.2 Enums

`enum Level { low, medium, high }` defines `Level.low` … Values compare by
identity, display as `Level.low`, have `.name` and `.index`, and
`Level.values()` lists them in order. Usable as `match` patterns and map
keys? **(choice: not as map keys — keys stay text/number.)**

### 7.3 Modules and exports

A `.tb` file is a module. `import "helpers.tb"` (relative to the importing
file) binds `helpers`; `import fs` loads a standard library; `import x as y`
renames. If a module uses `export` anywhere, only exported names are public;
otherwise every top-level name is public (2.x behaviour). `from m import a,
b as c` copies names into the current scope. A module is evaluated once per
program (cached by path); import cycles are an error.

---

## 8. Errors

- `Error` is a built-in class with fields `message`, `line`, `file`, `kind`
  (class name in snake case, `"error"` for the base) and `stack` (list of
  `{fn, line, file}`), plus any extra fields given as a map:
  `throw Error("msg", {"code": 7})`.
- `throw "text"` becomes `Error("text")`; `throw {"message": …}` copies the
  map's fields (2.x compatibility).
- Standard subclasses: `TypeError ValueError FileError ParseError
  NetworkError PermissionError NotFoundError TimeoutError StopIteration
  AssertionError`. User classes extend `Error`.
- `try { } catch err: FileError { } catch err: ParseError or ValueError { }
  catch err { } finally { }` — the first matching clause runs; `finally`
  always runs (even after `return`/`break`). `err is FileError` works.
- Uncaught errors print `Oops! On line N: <message>` with a hint where
  known, and a short `in f() ← g()` trace line when the stack has frames.
  Runtime errors from the implementation never show a Python traceback.

---

## 9. Generators and iteration

- A function containing `yield` returns a generator when called; the body
  runs lazily. `g.next()` gives the next value or `done`; `for x in g`;
  `.to_list() .map(fn) .filter(fn) .take(n) .zip(other) .enumerate()`
  return lazy generators (except `to_list`). A finished generator keeps
  returning `done`.
- There is no `yield from`; write `for x in other { yield x }`.
- `list.iter()`, `map.iter()`, ranges and `logs.each` are lazy too.
- Iteration protocol: a value with `fn next()` returning `done` at the end
  is an iterator; a value with `fn iter()` returning one is iterable.
- `for i, x in xs.enumerate()` and `for k, v in m` (two targets on a map
  iterate pairs; one target iterates keys).

---

## 10. Patterns (`match`)

```
let label = match level {
  "critical" or "high" => "act now"
  n if n is number and n > 7 => f"score {n}"
  [first, ...rest] => f"starts with {first}"
  {"kind": "alert", "level": lv} => lv
  1..=5 => "low range"
  Alert(a) => a.name
  _ => "unknown"
}
```

Semantics

- Arms are tried in order; the first whose pattern matches and whose guard
  (if any) is true is chosen. Bindings are block-scoped to the arm.
- Literal patterns compare with `==`; a bare name binds the value (a name
  that is a class, enum value or `const` in scope is compared instead
  **(choice)**); `_` matches anything.
- List patterns match lists of the same length, or at least the fixed
  items when `...rest` is present (`rest` gets the remaining list).
- Map patterns match maps that have every listed key with a matching value;
  extra keys are allowed. `{name}` is shorthand for `{"name": name}`;
  `{port = 22}` gives a default when the key is missing **(choice)**.
- Range patterns use `..`/`..=` on numbers (or text **(choice)**).
- `Name(p)` matches instances of class `Name` (or subclasses) and matches
  `p` against the instance; `Name {field: p}` matches fields.
- `is T` matches by type name.
- `or` combines alternatives; names bound in one alternative must be bound
  in all **(choice)**.
- No arm matching and no `_` → error "Nothing in this match fits <value>".
- `match` is an expression (the arm's value; a block arm's value is its
  last expression, else nil) and also usable as a statement.

---

## 11. Formatting

Inside f-text, `{value!r}` shows the value as typed (quoted text, nested
containers as code); `{value:spec}` applies a format spec; both can combine
as `{value!r:>20}`. `text.format(spec)`? No: the method is on values:
`value.format(spec)` and the free function `format(value, spec)` do the
same as a hole **(choice: `format(value, spec)` and `x.format(spec)` for
numbers and text)**.

Format spec (a subset of Python's mini-language):

```
format-spec = [ [ fill ] align ] [ sign ] [ "0" ] [ width ] [ "," ] [ "." precision ] [ kind ] .
align       = "<" | ">" | "^" .
sign        = "+" | "-" | " " .
kind        = "f" | "d" | "x" | "X" | "b" | "o" | "%" | "e" | "s" .
```

Examples: `{x:.2f}`, `{n:8}`, `{n:>8}`, `{n:<8}`, `{n:^8}`, `{n:08.3f}`,
`{n:,}`, `{n:x}`, `{n:X}`, `{n:b}`, `{n:o}`, `{p:.1%}`, `{s:.5}` (truncate
text), `{s:*^9}` (fill). Text defaults to left alignment, numbers to right.
An unknown or inapplicable spec is a friendly error naming the spec.

---

## 12. Program structure, imports and packages

Resolution of `import name` / `from name import …`:

1. A quoted path (`import "helpers.tb"`, `from "./lib/x.tb" import f`) is
   relative to the importing file; `.tb` is added when missing.
2. A bare name is a standard library (`fs`, `json`, …).
3. Otherwise a package: starting from the importing file's folder, walk up
   to the nearest folder containing `tb.toml` (the project root) and load
   `tb_packages/<name>/lib.tb`, or `tb_packages/<name>/<name>.tb`, or the
   file named by `[package] main` in `tb_packages/<name>/tb.toml`. A
   package name may contain `-`; `import tb-colors`? No — write `import
   "tb_packages/tb-colors/lib.tb" as colors` or `from "tb-colors" import …`
   **(choice: core-agent to confirm how dashed names are imported; the
   package manager installs under the dashed folder name)**.

Packages are TB-only (no native code). `tb add`, `tb remove`, `tb update`,
`tb packages`, `tb init`, `tb publish` manage `tb.toml`, `tb.lock` and
`tb_packages/` (see `tbtools/pkg.py`).

Program entry: statements run top to bottom; `os.args` holds the arguments;
`exit(code)` stops. Output goes through `say` only.

---

## 13. Compatibility with TB 2.x (Breaking changes)

- New reserved words (§1.3). A 2.x program using `match`, `class`, `const`,
  `from`, `then`, `is`, `self`, … as a name must rename it (the error
  message suggests a new name).
- `/` always produces a float value (display unchanged); use `//` for whole
  division.
- `import`/`as`/`try`/`catch`/`throw`/`break`/`continue` are reserved
  everywhere, not only at the start of a statement.
- Everything else in 2.x keeps working: `fn(x) { }`, `say`, `ask`, map and
  list methods, `import "file.tb"`, `try { } catch err { }`, `_test.tb` tests.

`MIGRATION-3.0.md` (core-agent) lists these with fixes.

---

## Appendix A — Conformance tests (wave 2)

`spec/*_test.tb` will hold one file per section (`lexical_test.tb`,
`numbers_test.tb`, `text_test.tb`, `bytes_test.tb`, `destructuring_test.tb`,
`functions_test.tb`, `ranges_test.tb`, `match_test.tb`, `classes_test.tb`,
`errors_test.tb`, `generators_test.tb`, `format_test.tb`,
`imports_test.tb`), runnable with `tb test spec`. Each test cites the
section it checks in a comment, e.g. `# §3.1 floor modulo`.
