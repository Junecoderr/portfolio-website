# The TB Language Specification (3.0)

Status: **final for TB 3.0.0**. Every rule here was checked against the
reference implementation (`tbcore/`: lexer, parser, tree-walking interpreter
and bytecode VM). The open design questions of the draft were settled by
what `tbcore` actually does; those places are marked **(settled)** with the
answer. The conformance tests in `spec/*_test.tb` check the rules and run
with `tb test spec`.

TB is a small, friendly, dynamically typed language with gradual type
annotations (parsed and kept, ignored when the program runs; `tb check` uses
them). Programs are UTF-8 text files ending in `.tb`. The reference
implementation is in Python; `tb file.tb` runs a program.

Contents

1. Lexical structure
2. Grammar (EBNF)
3. Values and types
4. Expressions and operators
5. Statements
6. Functions
7. Classes, enums and modules
8. Errors
9. Generators, iteration and tasks
10. Patterns (`match`)
11. Formatting (`f"..."`, `format`)
12. Program structure, imports and packages
13. Compatibility with TB 2.x

---

## 1. Lexical structure

### 1.1 Source text

A program is a sequence of Unicode characters. Lines end in `\n`; a `\r` is
treated as white space, so `\r\n` files work. Indentation has no meaning.
Statements are separated by newlines or `;`. Newlines are otherwise not
significant inside an expression: a list, map or call may span several
lines, and an expression may continue on the next line after a binary
operator. Four places do look at line breaks:

- A `(` or `[` that starts a new line begins a new statement; it never calls
  or indexes the expression on the line before.
- `return`, `throw`, `yield`, `import` and `break`/`continue` take their value
  (or end) on the same line: `return` alone on a line returns nil.
- Inside a `match`, an operator at the start of a line ends the current arm
  (so `-1 => ...` on the next line is a new arm).
- A comment that follows code on the same line is that line's trailing
  comment (kept by `tb fmt`).

### 1.2 Comments

```
comment      = "#" { any character except newline } .
doc-comment  = "##" { any character except newline } .
```

Comments run to the end of the line. A `##` block directly above a `fn`,
`class` (or one of its members), `enum`, `const`, `let` or `export`
declaration is its documentation; `tb doc FILE.tb` and the language server
show it. Without a `##` block, the plain `#` block directly above is used.
The comment block at the very top of a file, followed by a blank line,
describes the file. The parser keeps comments on statement nodes
(`comments`, `blank_before`, `trailing_comment`) so `tb fmt` can put them
back.

### 1.3 Identifiers and keywords

```
identifier = ( letter | "_" ) { letter | digit | "_" } .
letter     = any Unicode letter .
digit      = "0" … "9" .
```

Reserved words — using one as a name is an error with a suggested new name
(`'match' is a keyword in TB 3.0; call it 'found' instead.`):

```
and      async    await    class    const    else     enum     export
extends  false    finally  fn       for      from     if       in
is       let      match    nil      not      or       return   self
static   super    then     true     while    yield
```

Statement words **(settled)**: `import as try catch throw break continue
defer assert` are *not* reserved. They are special only where they start a
statement (or, for `as`, inside an import), so 2.x programs that use them as
names keep working: `let import = 3` is fine.

Notes

- `done` is a built-in constant (the iterator sentinel), not a keyword; like
  every built-in it can be shadowed **(settled)**.
- `_` is an ordinary identifier except in a `match` pattern, where it
  matches anything without binding.
- Dot access is never affected by keywords: `re.match`, `obj.from` are fine.

### 1.4 Literals

```
number        = int-literal | float-literal .
int-literal   = dec-int | "0x" hex-digit { hex-digit | "_" }
              | "0b" bin-digit { bin-digit | "_" } | "0o" oct-digit { oct-digit | "_" } .
dec-int       = digit { digit | "_" } .
float-literal = dec-int "." digit { digit | "_" } [ exponent ] | dec-int exponent .
exponent      = ( "e" | "E" ) [ "+" | "-" ] digit { digit } .
```

`_` separates digits (`1_000_000`); a number may not end with `_`. `1e3` and
`2.5e-3` are floats. Int literals have unlimited size. A number needs a digit
before the dot (`0.5`, not `.5`). There is no unary `+`.

```
text-literal = [ prefix ] ( short-text | long-text ) .
prefix       = "f" | "r" | "b" | "rf" | "fr" | "rb" | "br"   (either case) .
short-text   = '"' { char | escape | hole } '"' | "'" { char | escape | hole } "'" .
long-text    = '"""' { char | newline | escape | hole } '"""'
             | "'''" { char | newline | escape | hole } "'''" .
escape       = "\n" | "\t" | "\r" | "\0" | "\\" | '\"' | "\'"
             | "\x" hex-digit hex-digit | "\u{" hex-digit { hex-digit } "}" .
hole         = "{" expression [ "!r" ] [ ":" format-spec ] "}" .     (f-text only)
```

- Plain text: escapes are processed; any other `\c` is kept as typed (so
  regular expressions like `"\d+"` work). A short text may not contain a
  raw newline.
- `r"..."` raw text: no escapes (`r"\d+"` is three characters).
- `f"..."`: holes are evaluated and inserted; `{{` and `}}` are literal
  braces; an empty `{}` is an error. A hole may contain quotes and brackets.
- `b"..."` bytes: ASCII characters and escapes only; `b"é"` is an error.
- `\u{...}` takes 1 to 6 hex digits, up to `10FFFF`.

### 1.5 Operators and punctuation

```
+  -  *  /  //  %  **     &  |  ^  ~  <<  >>     ==  !=  <  <=  >  >=
=  +=  -=  *=  /=  //=  %=  **=  &=  |=  ^=  <<=  >>=
..  ..=  ...  =>  ->  .  ,  :  ;  (  )  [  ]  {  }  !
```

Word operators: `and or not is in`, and the two-word `not in` and `is not`
**(settled: both exist)**. `!x` is accepted as a tight-binding `not x`
(`!a == b` is `(not a) == b`). `&&` and `||` are errors that point to
`and`/`or`.

---

## 2. Grammar (EBNF)

Conventions: `{ x }` repetition, `[ x ]` optional, `( a | b )` choice,
terminals in quotes. `SEP` is a newline or `;` (several in a row are fine).

```
program        = { statement SEP } EOF .
block          = "{" { statement SEP } "}" .

statement      = let-stmt | assign-stmt | multi-assign | expr-stmt | return-stmt
               | if-stmt | while-stmt | for-stmt | try-stmt | throw-stmt
               | break-stmt | continue-stmt | defer-stmt | assert-stmt
               | import-stmt | from-stmt | export-stmt
               | fn-decl | class-decl | enum-decl .

(* declarations *)
let-stmt       = ( "let" | "const" ) binding "=" expression .
binding        = identifier [ type-ann ]
               | identifier "," destructure { "," destructure }   (* let a, b = f() *)
               | list-destructure | map-destructure .
destructure    = identifier | list-destructure | map-destructure .
list-destructure = "[" [ destructure { "," destructure } ] [ "..." identifier ] [ "," ] "]" .
map-destructure  = "{" [ map-entry { "," map-entry } [ "," ] ] "}" .
map-entry      = identifier [ ":" destructure ] [ "=" expression ]
               | text-literal ":" destructure [ "=" expression ] .

assign-stmt    = target assign-op expression .
multi-assign   = target "," target { "," target } "=" expression { "," expression } .
target         = identifier | postfix "." identifier | postfix "[" expression "]" .
assign-op      = "=" | "+=" | "-=" | "*=" | "/=" | "//=" | "%=" | "**="
               | "&=" | "|=" | "^=" | "<<=" | ">>=" .

expr-stmt      = expression .
return-stmt    = "return" [ expression { "," expression } ] .     (* only inside fn *)
throw-stmt     = "throw" expression .
break-stmt     = "break" .                                        (* only inside a loop *)
continue-stmt  = "continue" .
defer-stmt     = "defer" block .                                  (* only inside fn *)
assert-stmt    = "assert" expression [ "," expression ] .

import-stmt    = "import" ( identifier | text-literal ) [ "as" identifier ] .
from-stmt      = "from" ( identifier | text-literal ) "import" import-name { "," import-name } .
import-name    = identifier [ "as" identifier ] .
export-stmt    = "export" ( fn-decl | class-decl | enum-decl | let-stmt ) .  (* top level, one name *)

(* control flow *)
if-stmt        = "if" expression block { "else" "if" expression block } [ "else" block ] .
while-stmt     = "while" expression block .
for-stmt       = "for" for-target { "," for-target } "in" expression block .
for-target     = identifier | list-destructure | map-destructure .
try-stmt       = "try" block { catch-clause } [ "finally" block ] .   (* at least one of them *)
catch-clause   = "catch" [ identifier [ ":" type-ref { "or" type-ref } ] ] block .
type-ref       = identifier { "." identifier } .

(* functions *)
fn-decl        = [ "async" ] "fn" identifier params [ "->" type ] block .
params         = "(" [ param { "," param } [ "," ] ] ")" .
param          = ( identifier | list-destructure | map-destructure ) [ type-ann ] [ "=" expression ]
               | "..." identifier
               | "**" identifier .                                  (* last *)

(* classes and enums *)
class-decl     = "class" identifier [ "extends" type-ref ] "{" { class-member [ ";" ] } "}" .
class-member   = [ "static" ] ( "let" | "const" ) identifier [ type-ann ] [ "=" expression ]
               | [ "static" ] [ "async" ] "fn" identifier params [ "->" type ] block .
enum-decl      = "enum" identifier "{" identifier { "," identifier } [ "," ] "}" .

(* types: parsed and kept on the nodes, ignored at run time *)
type-ann       = ":" type .
type           = single-type { "|" single-type } .
single-type    = type-ref [ "<" type { "," type } ">" ]
               | "fn" "(" [ type { "," type } ] ")" [ "->" type ]
               | "(" type ")" | "nil" | "class" .

(* expressions, lowest precedence first *)
expression     = "if" expression "then" expression "else" expression
               | "yield" [ expression ]                              (* only inside fn *)
               | or-expr .
or-expr        = and-expr { "or" and-expr } .
and-expr       = not-expr { "and" not-expr } .
not-expr       = "not" not-expr | equality .
equality       = comparison { ( "==" | "!=" ) comparison } .
comparison     = range-expr { comp-op range-expr | ( "is" | "is" "not" ) type-ref } .
comp-op        = "<" | "<=" | ">" | ">=" | "in" | "not" "in" .
range-expr     = bit-or [ ( ".." | "..=" ) bit-or ] .
bit-or         = bit-xor { "|" bit-xor } .
bit-xor        = bit-and { "^" bit-and } .
bit-and        = shift { "&" shift } .
shift          = additive { ( "<<" | ">>" ) additive } .
additive       = term { ( "+" | "-" ) term } .
term           = unary { ( "*" | "/" | "//" | "%" ) unary } .
unary          = ( "-" | "~" | "!" | "not" | "await" ) unary | power .
power          = postfix [ "**" unary ] .                            (* right-associative *)
postfix        = primary { call | index | slice | member } .
call           = "(" [ argument { "," argument } [ "," ] ] ")" .     (* "(" on the same line *)
argument       = expression | "..." expression | identifier "=" expression .
index          = "[" expression "]" .                                (* "[" on the same line *)
slice          = "[" [ expression ] ":" [ expression ] [ ":" [ expression ] ] "]" .
member         = "." ( identifier | keyword ) .

primary        = number | text-literal | "true" | "false" | "nil" | identifier
               | "self" | "super" "." identifier
               | list-literal | map-literal | fn-expr | lambda | match-expr
               | "(" expression ")" .
list-literal   = "[" [ item { "," item } [ "," ] ] "]" .
item           = expression | "..." expression .
map-literal    = "{" [ map-item { "," map-item } [ "," ] ] "}" .
map-item       = expression ":" expression | "..." expression .
fn-expr        = [ "async" ] "fn" params [ "->" type ] block .
lambda         = ( identifier | params ) "=>" ( expression | block ) .

(* match *)
match-expr     = "match" or-expr "{" { match-arm [ "," ] } "}" .
match-arm      = pattern [ "if" expression ] "=>" ( expression | block ) .
pattern        = single-pattern { "or" single-pattern } .
single-pattern = literal [ ( ".." | "..=" ) literal ]              (* ranges: numbers only *)
               | "_" | identifier                                    (* binds *)
               | identifier "." identifier { "." identifier }        (* a value: Level.low *)
               | "[" [ pattern { "," pattern } ] [ "..." [ identifier ] ] [ "," ] "]"
               | "{" [ map-pat { "," map-pat } [ "," ] ] "}"
               | type-ref "(" [ pattern ] ")"                        (* "(" on the same line *)
               | type-ref "{" [ field-pat { "," field-pat } ] "}"    (* "{" on the same line *)
               | "is" type-ref
               | "(" pattern ")" .
literal        = [ "-" ] number | text-literal | "true" | "false" | "nil" .
map-pat        = ( text-literal | number | identifier ) [ ":" pattern ] .
field-pat      = identifier [ ":" pattern ] .
```

Ambiguities resolved **(settled)**

- `{` right after the condition of `if`/`while`, the iterable of `for`, the
  subject of `match`, a parameter list or a class name starts a block. A map
  literal *inside* the condition is fine (`if m == {"a": 1} { … }`); a map
  as the whole condition needs parentheses.
- A statement starting with `{` is a map literal (there are no bare blocks).
- After `=>` (a lambda or a match arm), `{` starts a block unless it looks
  like a map: `{}`, `{...`, or a first key (text, number, name or f-text)
  followed by `:`. Write `x => ({a + 1: 2})` for other maps.
- `x => …` is a lambda when a name, or a parenthesised list followed by `=>`,
  starts an operand.
- `fn name(…) { }` declares; `fn(…) { }` is an expression.
- `a..b step n` is not syntax; use `(a..b).step(n)`.

---

## 3. Values and types

| `type(x)` | Values | Notes |
| --- | --- | --- |
| `"number"` | ints (unlimited) and floats (IEEE double) | `x.is_int()`, `x.is_float()`, `int(x)`, `float(x)`, `x.to_int()` |
| `"text"` | immutable Unicode text | `t[i]`, `t[a:b]`, negative indexes |
| `"bytes"` | immutable byte sequences | `b"..."`, `"é".bytes()`, `data[i]` is an int |
| `"bool"` | `true`, `false` | |
| `"nil"` | `nil` | |
| `"list"` | ordered, mutable | |
| `"map"` | insertion-ordered, mutable; keys are text or numbers | anything else as a key is an error |
| `"function"` | fns, lambdas, methods, built-ins | `.name`, `.arity`, `.call(list)`, `.bind(...)` |
| `"class"` | classes, including `Error` and its family | |
| *class name* | instances: `type(Dog("Rex"))` is `"Dog"` | |
| `"module"` | imported libraries and files | |
| `"range"` | `a..b`, `a..=b` (lazy) | **(settled)** |
| `"generator"` | generator calls and lazy iterators (`[1].iter()`) | **(settled)** |
| `"enum"` | an enum itself (`type(Level)`) | |
| *enum name* | enum values: `type(Level.low)` is `"Level"` | **(settled)** `.name`, `.index` |
| `"done"` | the iterator sentinel `done` | |
| `"Task"` | what calling an `async fn` gives | §9 |

Truthiness: `false` and `nil` are false; everything else (including `0`,
`""`, `[]`) is true (as in 2.x).

Equality `==`: numbers by value (`1 == 1.0`), text/bytes by content, lists
and maps item by item, ranges by bounds, functions/modules/classes by
identity, instances by identity unless the class defines `equals(other)`,
enum values by identity. Values of different kinds are never equal
(`1 == "1"` is false; `nil == false` is false).

Ordering `< <= > >=` **(settled)**: numbers with numbers, text with text
(code-point order), instances through `compare(other)`. Everything else —
lists included — is an error ("I can't compare list with list.").

### 3.1 Numbers

- `+ - *` on two ints give an int; `/` always divides exactly (`7 / 2` is
  `3.5`); `//` floor division; `%` floor modulo (the sign of the divisor:
  `-7 % 3` is `2`, `10 % -3` is `-2`); `**` power (`2 ** -1` is `0.5`).
  Dividing by zero is an error.
- Display is tidy: a float with no fractional part prints like an int
  (`6 / 3` shows `2`), so 2.x programs print the same.
- Bitwise `& | ^ ~ << >>` need whole numbers.
- Comparisons chain: `0 < x <= 10` means `0 < x and x <= 10` (x once).
- Guards: float overflow and `**` results beyond about 10 000 bits stop with
  "That number got too big for me to work with."
- `+` with text on either side joins as text (`"x" + 1` is `"x1"`, as in 2.x).

### 3.2 Text, lists and bytes

Indexing and slicing follow Python's rules: `t[0]` first, `t[-1]` last,
`t[a:b]` from `a` up to (not including) `b`, `t[::2]`, `t[::-1]`; an index
out of range is an error, a slice out of range is clipped. Text can't be
changed in place (`s[0] = "x"` is an error); lists can. `list * n` and
`text * n` repeat (n must be 0 or more).

Bytes methods: `len() hex() slice(a, b?) to_text(encoding?) at(i)
starts_with(b) find(b) split(b) join(list) reverse() to_list() copy()`;
`+` concatenates; `for byte in data` gives ints 0–255; `b"b" in b"abc"`.

---

## 4. Expressions and operators

Precedence, tightest first (the grammar in §2 is exact):

1. member `.`, call `()`, index `[]`, slice
2. `**` (right-associative; its left side is a postfix expression, so
   `-2 ** 2` is `-4`)
3. unary `-` `~` `!` `await` (and `not` inside an operand: `a == not b`)
4. `* / // %`
5. `+ -`
6. `<< >>`
7. `&`
8. `^`
9. `|`
10. `.. ..=` (`1 + 2..5` is `3..5`) **(settled: ranges sit below the bit operators)**
11. `< <= > >= in not in is is not` (chained)
12. `== !=`
13. `not`
14. `and`
15. `or`
16. `if … then … else …`, `yield`, lambdas

`and`/`or` short-circuit and give back an operand (`nil or "x"` is `"x"`,
`0 or 1` is `0`). `not x` is a bool. `x is T` is true when `x` has the
built-in type `T` (`number int float text bool nil list map bytes function
class module range generator enum any`) or is an instance of class `T` or a
subclass. `x in y`: an item of a list, a number in a range, a key of a map,
a substring of text, a sub-sequence of bytes, or `contains(x)` on an
instance.

Spread: `f(...xs)` passes the items as arguments; `[...a, ...b]`;
`{...m, "k": 1}` (later keys win).

`if c then a else b` evaluates only the chosen branch.

---

## 5. Statements

- `let` declares in the current scope. Blocks (`if`, `while`, `for`,
  `match` arms, `try`/`catch`/`finally`, function bodies) open scopes; a name
  declared inside is gone after the block. Declaring a name again in the
  same scope replaces it **(settled: not an error)**. Assigning to a name
  that was never declared is an error with a hint (`'x' doesn't exist yet.
  Use 'let x = ...' first.`).
- Destructuring: `let [a, b, ...rest] = xs`; `let {name, ip: address, port =
  22} = rec`; a missing key without a default is an error naming the key;
  nesting is free. The same patterns work in `for` and in parameters.
- `let a, b = f()` unpacks a list with exactly that many items (more or
  fewer is an error suggesting `...rest`). `let a, b = 1, 2` is not allowed;
  write `let [a, b] = [1, 2]`.
- `return a, b` returns the list `[a, b]`.
- `a, b = b, a` evaluates every value before assigning; targets may be
  names, fields or list slots.
- `x op= e` is `x = x op e` (the target is evaluated once).
- `const NAME = v` (or a destructuring pattern): assigning to it later is an
  error.
- `for x in` lists, maps (keys; `for k, v in m` gives pairs), text
  (characters), ranges, bytes (ints), generators and instances with
  `iter()`. `for i, x in xs.enumerate()`.
- `defer { … }` (inside a function) runs when the function returns or
  throws, last registered first.
- `assert cond` / `assert cond, "message"` throws `AssertionError`.
- `return` outside a function, `break`/`continue` outside a loop and `defer`
  outside a function are errors when the program is read.

---

## 6. Functions

```
fn greet(name, greeting = "Hello", ...rest, **opts) -> text { … }
```

- Parameters: positional, with defaults (evaluated at call time; a default
  may use earlier parameters: `fn add(a, b = a * 2)`), one `...rest` (a list
  of extra positionals; inputs after it need defaults), and a last `**opts`
  (a map of named arguments that match no parameter). Any parameter can be
  passed by name: `greet("Tanisha", greeting = "Hi")`. A positional
  argument after a named one, the same parameter given twice, a missing
  required argument, or an unknown name (without `**opts`) is an error.
  Standard-library (native) functions take named arguments too (TB 3.1):
  their parameter names are the ones in the doc signature's first line
  (`map(fn, items, workers?)`), so `parallel.map(f, xs, workers = 8)`
  works, with the same errors. A left-out optional parameter before a
  named one gets the library's default. Functions taking any number of
  arguments (`join(part, ...)`) are positional only.
- Lambdas: `x => x * 2`, `(a, b) => a + b`, `() => 1`, and `x => { … }`
  whose value is what an explicit `return` gives, else nil **(settled)**.
- `fn(x) { … }` and `async fn(x) { … }` expressions.
- Closures capture variables, not values: a later change is seen.
- Function values: `.name` (the declared name; `"anonymous"` for `fn`
  expressions and lambdas **(settled)**), `.arity` (number of positional
  parameters), `.call(list)`, `.bind(...args)` (fixes arguments from the
  left).
- Recursion depth is limited; going deeper is a friendly error.

---

## 7. Classes, enums and modules

### 7.1 Classes

```
class Animal {
  let sound = "..."          # instance field default (evaluated for each instance)
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

- `self` is implicit in methods. `super.method(...)` calls the parent's
  version with the same `self`. `self` can't be assigned.
- Calling a class makes an instance: field defaults first (parent first),
  then `init` if there is one. Without `init`, any argument is an error.
- Single inheritance. Fields live on the instance; `x.fields()` gives a map
  copy. Reading a missing field or method is an error with "Did you mean".
- Special methods: `text()` (used by `say`, `str`, f-text), `equals(other)`
  (`==`), `compare(other)` (negative/zero/positive; `<` and sorting),
  `get(i)` / `set(i, v)` (indexing), `call(...)` (calling an instance),
  `iter()` / `next()` (iteration; `next()` returns `done` at the end),
  `len()`, `contains(x)` (`in`), and the operator methods `add sub mul div
  floor_div mod pow` (and `radd` …) for `+ - * / // % **`.
- `type(instance)` is the class name; `type(Dog)` is `"class"`;
  `Dog.name` is `"Dog"` **(settled)**; `d is Animal`.
- Display without `text()`: `Dog {name: "Rex", tricks: []}`.
- Private by convention: `_name` (no enforcement; `tb check` warns).

### 7.2 Enums

`enum Level { low, medium, high }` defines `Level.low` … Values compare by
identity, display as `Level.low`, have `.name` and `.index`, and
`Level.values()` lists them in order. They work in `match` (as dotted
value patterns). They are not map keys **(settled: keys stay text or
numbers)**.

### 7.3 Modules and exports

A `.tb` file is a module. `import "helpers.tb"` (relative to the importing
file) binds `helpers`; `import fs` loads a standard library; `import x as y`
renames. If a module uses `export` anywhere, only exported names can be used
from outside (using another is an error that lists the exported ones);
otherwise every top-level name is public. `from m import a, b as c` binds
names directly. A module runs once per program; importing in a circle is an
error that shows the circle.

---

## 8. Errors

- `Error` is a built-in class: `Error(message, fields?)` with `message`,
  `line`, `file`, `kind` (the class name in snake case: `"error"`,
  `"value_error"`, `"my_err"` for `class MyErr extends Error`) and `stack`
  (list of `{fn, line, file}`), plus any fields from the map:
  `throw Error("msg", {"code": 7})` then `err.code`.
- `throw "text"` throws an `Error`; `throw {"message": …, …}` copies the
  map's fields (2.x compatibility).
- Built-in subclasses: `TypeError ValueError FileError ParseError
  NetworkError PermissionError NotFoundError TimeoutError StopIteration
  AssertionError`. Errors made by the language itself (an index out of
  range, dividing by zero) are plain `Error`s with kind `"error"`
  **(settled)**; libraries use the subclasses.
- `try { } catch err: FileError { } catch err: ParseError or ValueError { }
  catch err { } finally { }`: the first matching clause runs; `finally`
  always runs (also after `return`, `break` or an uncaught error).
- An uncaught error prints `Oops! On line N: <message>` (with the file name
  when it happened in an imported file), plus a short call trace when
  functions were running. A Python traceback is never shown.

---

## 9. Generators, iteration and tasks

- A function containing `yield` returns a generator when called; its body
  runs lazily. `g.next()` gives the next value, then `done` forever.
  `for x in g`; `.to_list() .map(fn) .filter(fn) .take(n) .zip(other)
  .enumerate()` (all lazy except `to_list`). There is no `yield from`.
- `list.iter()`, `map.iter()` and ranges are lazy iterables too.
- Iteration protocol: a value with `next()` returning `done` at the end is an
  iterator; a value with `iter()` returning one is iterable.
- `async fn` (declaration, method or expression): calling it starts a
  `Task` running on another thread and returns it at once. `await t` waits
  for the task and gives its value (or rethrows its error); `await` on a
  plain value gives the value. An `async fn` can't `yield`. See the
  `parallel` library for `parallel.run`, `parallel.all`, queues and locks.

---

## 10. Patterns (`match`)

```
let label = match level {
  "critical" or "high" => "act now"
  n if n is number and n > 7 => f"score {n}"
  [first, ...rest] => f"starts with {first}"
  {"kind": "alert", "level": lv} => lv
  1..=5 => "low range"
  Level.low => "an enum value"
  Alert(a) => a.name
  is text => "some text"
  _ => "unknown"
}
```

- Arms are tried in order; the first whose pattern matches and whose guard
  is true wins. Bindings belong to the arm. Arms are separated by newlines
  (or `,`).
- Literal patterns compare with `==`; negative numbers are allowed.
- A bare name always binds the value **(settled: even when a `const` or a
  class has that name — use `Name(...)` or a dotted name to compare)**;
  `_` matches without binding. A dotted name (`Level.low`, `config.MAX`) is
  a value compared with `==`.
- List patterns match lists of exactly that length, or of at least the fixed
  items when `...rest` (or a bare `...`) is present.
- Map patterns match maps that have every listed key with a matching value;
  extra keys are fine. `{name}` is shorthand for `{"name": name}`. There are
  no defaults in match map patterns **(settled)** (destructuring `let` has
  them).
- Range patterns are numbers only **(settled)**: `1..5`, `1..=5`, `-3..0`.
- `Name(p)` matches instances of the class `Name` (or a subclass) and
  matches `p` against the instance; `Name {field: p, other}` matches fields.
- `is T` matches by type, like the `is` operator.
- `or` combines alternatives; an alternative need not bind the same names
  **(settled)**.
- No arm matching → error "Nothing in this match fits <value>. Add a
  '_ => ...' arm for everything else."
- `match` is an expression (a block arm's value is its last expression
  statement, else nil) and can stand as a statement.

---

## 11. Formatting

In f-text, `{value!r}` shows the value as code (text quoted), `{value:spec}`
applies a format spec, and `{value!r:>20}` does both. The free function
`format(value, spec)` and the method `value.format(spec)` on numbers and
text do the same as a hole **(settled)**.

```
format-spec = [ [ fill ] align ] [ sign ] [ "0" ] [ width ] [ "," ] [ "." precision ] [ kind ] .
align       = "<" | ">" | "^" .
sign        = "+" | "-" | " " .
kind        = "f" | "d" | "x" | "X" | "b" | "o" | "%" | "e" | "s" .
```

Examples: `{x:.2f}` → `3.14`, `{n:>5}`, `{n:<5}`, `{s:^6}`, `{n:08.3f}` →
`0007.000`, `{n:,}` → `1,234,567`, `{n:x}` → `ff`, `{p:.1%}` → `25.0%`,
`{s:.3}` → `hel` (truncate), `{s:*^5}` → `**s**`. Text aligns left by
default, numbers right. An unknown spec is a friendly error that names it
and lists the common ones.

---

## 12. Program structure, imports and packages

How `import` finds things:

1. Quoted names are files relative to the importing file:
   `import "helpers.tb"`, `from "lib/x.tb" import f`. A quoted name with
   no extension and no folder (`import "tb-colors"`) is a file `tb-colors.tb`
   next to the importer if there is one, else a package; the bound name
   replaces `-` with `_` (`tb_colors`) **(settled)**.
2. A bare name is a standard library (`fs`, `json`, …), else a package.
3. Packages live in `tb_packages/<name>/`, found by walking up from the
   importing file's folder to the nearest folder with a `tb.toml`; the entry
   is `lib.tb`, `<name>.tb`, or `[package] main` in the package's own
   `tb.toml`. `tb add`, `tb remove`, `tb update`, `tb packages`, `tb init`
   and `tb publish` manage `tb.toml`, `tb.lock` and `tb_packages/`.

Packages are TB-only (no native code).

A program runs its statements top to bottom. `os.args` holds its arguments;
`exit(code)` stops it. Output goes through `say`; input through `ask`.

---

## 13. Compatibility with TB 2.x

Breaking changes (see `MIGRATION-3.0.md`):

- The new reserved words in §1.3 (`class`, `match`, `const`, `from`, `is`,
  `self`, `then`, …) can't be names any more; the error suggests another.
- `/` always divides exactly and gives a float value (display unchanged);
  use `//` for whole division.

Everything else from 2.x keeps working: `fn(x) { }`, `say`, `ask`, the map
and list methods, `import "file.tb"`, `try { } catch err { }` (and the
statement words used as names), `_test.tb` tests.

---

## Appendix A — Conformance tests

`spec/*_test.tb` hold the conformance tests, one file per area:
`lexical_test.tb`, `numbers_test.tb`, `text_test.tb`, `collections_test.tb`,
`statements_test.tb`, `functions_test.tb`, `classes_test.tb`,
`errors_test.tb`, `generators_test.tb`, `match_test.tb`, `format_test.tb`
and `imports_test.tb` (with the helper modules in `spec/support/`). Each
check cites the section it covers, e.g. `# §3.1 floor modulo`. Run them with:

```
tb test spec
TB_ENGINE=tree tb test spec      # the same rules on the tree-walking interpreter
```
