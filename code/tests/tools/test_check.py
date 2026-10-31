"""`tb check` and tbcore.types.check_source: small programs with the exact
problems (line, column, severity, code) they should produce, programs that
must stay clean (no false positives), the CLI, and a repo-wide run: every
.tb program shipped in the repo has no errors."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tbcore.types import Diagnostic, check_source  # noqa: E402
from tbtools import check as check_cli  # noqa: E402


def problems(source, path=None):
    return [(d.line, d.col, d.severity, d.code) for d in check_source(source, path)]


def messages(source, path=None):
    return [d.message for d in check_source(source, path)]


def codes(source, path=None):
    return [d.code for d in check_source(source, path)]


def errors(source, path=None):
    return [d for d in check_source(source, path) if d.severity == "error"]


# -- the Diagnostic shape --------------------------------------------------------


def test_diagnostic_fields_and_format():
    [d] = check_source("say(nmae)\n", "prog.tb")
    assert isinstance(d, Diagnostic)
    assert (d.line, d.col, d.end_line, d.end_col) == (1, 5, 1, 9)
    assert d.severity == "error" and d.code == "undefined-name"
    assert d.format() == "prog.tb:1:5: error: I don't know what 'nmae' is."
    data = d.to_dict()
    assert set(data) >= {"file", "line", "col", "end_line", "end_col", "severity", "code", "message", "hint"}


# -- parse errors and statements outside their place ----------------------------------


def test_parse_error_is_one_diagnostic():
    [d] = check_source("say(1, 2\n")
    assert d.code == "parse-error" and d.severity == "error"
    assert d.message == "This '(' was never closed. Did you forget a ')'?"


def test_return_outside_function():
    assert problems("say(1)\nreturn 5\n") == [(2, 1, "error", "return-outside-fn")]


def test_break_and_continue_outside_loop():
    assert codes("break\n") == ["break-outside-loop"]
    assert codes("fn f() {\n  continue\n}\n") == ["break-outside-loop"]


# -- names --------------------------------------------------------------------------


def test_undefined_name_with_did_you_mean():
    src = 'let name = "Tanisha"\nsay(nmae)\n'
    assert problems(src) == [(2, 5, "error", "undefined-name")]
    assert messages(src) == ["I don't know what 'nmae' is. Did you mean 'name'?"]


def test_undefined_name_other_language_hint_and_forgotten_import():
    assert messages("print(1)\n") == ["I don't know what 'print' is. Did you mean 'say'?"]
    assert messages('say(fs.read("a"))\n') == ["I don't know what 'fs' is. Did you forget 'import fs' at the top?"]


def test_used_before_defined_at_top_level():
    src = "greet()\nfn greet() { say(1) }\n"
    assert problems(src) == [(1, 1, "error", "used-before-defined")]


def test_function_bodies_may_use_later_names():
    src = "fn main() { helper(LIMIT) }\nfn helper(n) { say(n) }\nlet LIMIT = 3\nmain()\n"
    assert problems(src) == []


def test_block_scoped_names_are_not_visible_outside():
    src = "if true {\n  let inner = 1\n  say(inner)\n}\nsay(inner)\n"
    assert problems(src) == [(5, 5, "error", "undefined-name")]


def test_assign_to_name_that_does_not_exist():
    src = "count = 1\n"
    assert problems(src) == [(1, 1, "error", "undefined-name")]
    assert messages(src) == ["'count' doesn't exist yet. Use 'let count = ...' first."]


def test_map_key_word_without_quotes():
    assert messages("let m = {name: 1}\nsay(m)\n") == [
        "I don't know what 'name' is. Map keys that are words need quotes: {\"name\": ...}"
    ]


def test_several_occurrences_on_one_line_get_their_own_columns():
    assert problems("say(zz, zz)\n") == [(1, 5, "error", "undefined-name"), (1, 9, "error", "undefined-name")]


def test_names_inside_f_text_holes_are_checked():
    assert problems('let who = 1\nsay(f"hi {whoo}!")\n') == [(2, 11, "error", "undefined-name")]


def test_names_inside_strings_and_comments_are_ignored_for_columns():
    src = 'say("zz") # zz\nsay(zz)\n'
    assert problems(src) == [(2, 5, "error", "undefined-name")]


def test_self_outside_a_class():
    assert problems("fn f() {\n  return self.x\n}\n") == [(2, 10, "error", "self-outside-class")]
    assert codes("say(super.x())\n") == ["self-outside-class"]


def test_self_inside_lambdas_in_methods_is_fine():
    src = """class Counter {
  fn init() { self.n = 0 }
  fn add_all(xs) { xs.each(x => { self.n += x }) }
}
let c = Counter()
c.add_all([1, 2])
say(c.n)
"""
    assert problems(src) == []


# -- unused things -----------------------------------------------------------------


def test_unused_local_variable_is_a_warning():
    src = "fn f() {\n  let temp = 5\n  return 1\n}\nsay(f())\n"
    assert problems(src) == [(2, 7, "warning", "unused-variable")]


def test_underscore_names_are_exempt():
    assert problems("fn f() {\n  let _temp = 5\n  return 1\n}\nsay(f())\n") == []


def test_unused_import_is_a_warning():
    assert problems("import json\nimport fs\nsay(fs.exists(\"x\"))\n") == [(1, 8, "warning", "unused-import")]


def test_top_level_variables_are_not_reported_as_unused():
    # Top-level names are what other files import, so they count as used.
    assert problems("let VERSION = 1\n") == []


def test_unused_from_import():
    assert codes("from fs import read, exists\nsay(read)\n") == ["unused-import"]


# -- arity -------------------------------------------------------------------------


def test_user_function_arity_with_defaults():
    src = 'fn greet(who, greeting = "Hi") { return greeting + who }\nsay(greet())\nsay(greet(1, 2, 3))\nsay(greet("a"))\n'
    assert problems(src) == [(2, 5, "error", "wrong-arity"), (3, 5, "error", "wrong-arity")]
    assert messages(src) == ["greet() needs 1 to 2 inputs, but got 0.", "greet() needs 1 to 2 inputs, but got 3."]


def test_varargs_and_kwargs_are_understood():
    src = """fn log(level, ...parts) { say(level, parts) }
fn opts(a, **more) { say(a, more) }
log()
log(1, 2, 3, 4)
opts(1, colour = "red", size = 2)
"""
    assert messages(src) == ["log() needs at least 1 input, but got 0."]


def test_unknown_and_duplicate_keyword_inputs():
    src = "fn area(width, height = 1) { return width * height }\nsay(area(2, hieght = 3))\nsay(area(2, width = 3))\nsay(area(height = 2, width = 3))\n"
    assert messages(src) == [
        "area() has no input called 'hieght'. Did you mean 'height'? Its inputs are: width, height.",
        "area() got 'width' twice: once in order and once by name.",
    ]
    assert problems(src)[0] == (2, 13, "error", "unknown-keyword")


def test_spread_skips_the_count_check():
    assert problems("fn f(a, b) { return a + b }\nlet xs = [1, 2]\nsay(f(...xs))\n") == []


def test_builtin_and_library_arity():
    src = 'import fs\nsay(len(1, 2))\nsay(fs.read())\nsay(fs.read("a", encoding = "x"))\n'
    assert problems(src) == [
        (2, 5, "error", "wrong-arity"), (3, 8, "error", "wrong-arity"), (4, 18, "error", "unknown-keyword"),
    ]
    assert messages(src)[0] == "len() needs 1 input, but got 2."
    assert messages(src)[2] == "fs.read() has no input called 'encoding'. Its inputs are: path."


def test_method_and_constructor_arity():
    src = """class Dog {
  fn init(name) { self.name = name }
  fn speak(times = 1) { return self.name * times }
}
let d = Dog()
say(d.speak(1, 2))
class Plain { }
let p = Plain(1)
"""
    assert messages(src) == [
        "Dog.init() needs 1 input, but got 0.",
        "Dog.speak() needs 0 to 1 inputs, but got 2.",
        "Plain has no init method, so Plain() takes no inputs (got 1).",
    ]


def test_builtin_value_methods_arity():
    assert problems('say("a,b".split(",", 2, 3))\n') == [(1, 11, "error", "wrong-arity")]


def test_shadowed_builtin_uses_the_user_signature():
    assert problems("fn len(a, b) { return a + b }\nsay(len(1, 2))\n") == []


# -- unknown methods and members ---------------------------------------------------------


def test_unknown_text_method():
    src = 'let name = "Tanisha"\nsay(name.uper())\n'
    assert problems(src) == [(2, 10, "error", "unknown-method")]
    assert messages(src) == ["Text doesn't have a method called 'uper'. Did you mean 'upper'?"]


def test_unknown_method_hint_from_other_languages():
    assert messages("let xs = [1]\nxs.append(2)\n") == ["A list doesn't have a method called 'append'. Did you mean 'push'?"]


def test_method_return_kinds_flow_on():
    src = 'let words = "a b".split(" ")\nsay(words.lenght())\nsay(" x ".trim().upper().bogus())\n'
    assert codes(src) == ["unknown-method", "unknown-method"]


def test_unknown_methods_on_numbers_bytes_bool_nil():
    src = 'say(5.floor().nope())\nsay(b"ab".hexx())\nlet flag = true\nsay(flag.x)\nlet nothing = nil\nsay(nothing.size)\n'
    assert codes(src) == ["unknown-method"] * 4


def test_maps_allow_any_key_by_dot():
    assert problems('let m = {"a": 1}\nsay(m.a, m.anything, m.keys())\n') == []


def test_unknown_library_member_and_library_return_kinds():
    src = 'import fs\nsay(fs.reed("x"))\nsay(fs.read("x").uppr())\n'
    assert problems(src) == [(2, 8, "error", "unknown-member"), (3, 18, "error", "unknown-method")]
    assert messages(src)[0] == "The fs library has nothing called 'reed'. Did you mean 'read'?"


def test_unknown_library():
    assert codes("import nosuchlib\nsay(nosuchlib)\n") == ["unknown-library"]


def test_from_import_unknown_member():
    assert problems("from fs import reed\nsay(reed)\n") == [(1, 16, "error", "unknown-member")]


def test_enum_members():
    src = "enum Level { low, high }\nsay(Level.lwo)\nsay(Level.low.name, Level.values(), Level.high.index)\n"
    assert messages(src) == ["The enum Level has no member called 'lwo'. Did you mean 'low'? Its members: low, high."]


def test_unknown_field_or_method_on_object():
    src = """class Dog {
  let tricks = []
  fn init(name) { self.name = name }
  fn speak() { return self.name }
}
let d = Dog("Rex")
d.age = 3
say(d.name, d.tricks, d.age, d.speak(), d.fields())
say(d.bark())
"""
    assert problems(src) == [(9, 7, "error", "unknown-member")]


def test_unknown_member_on_self_is_a_warning():
    # A subclass in another file could add it, so it is only a warning.
    src = "class Dog {\n  fn init(name) { self.name = name }\n  fn speak() { return self.nmae }\n}\n"
    assert problems(src) == [(3, 28, "warning", "unknown-member")]
    assert "Did you mean 'name'?" in messages(src)[0]


def test_methods_defined_by_subclasses_are_fine_on_self():
    src = """class Shape {
  fn describe() { return f"area {self.area()}" }
}
class Square extends Shape {
  fn init(side) { self.side = side }
  fn area() { return self.side * self.side }
}
say(Square(2).describe())
"""
    assert problems(src) == []


def test_static_members():
    src = """class Box {
  static let count = 0
  fn open() { return 1 }
  static fn make() { Box.count += 1; return Box() }
}
say(Box.make(), Box.count, Box.name)
say(Box.open())
say(Box.missing)
"""
    assert codes(src) == ["unknown-member", "unknown-member"]
    assert "is a method of Box objects" in messages(src)[0]


def test_super_checks():
    src = """class Animal {
  fn speak() { return "..." }
}
class Dog extends Animal {
  fn speak() { return super.speak() + super.shout() }
}
class Cat {
  fn speak() { return super.speak() }
}
"""
    assert codes(src) == ["unknown-member", "no-super"]


def test_classes_extending_unknown_bases_stay_quiet():
    src = """class Oops extends Error {
  fn init(msg) { super.init(msg); self.code = 7 }
}
let e = Oops("x")
say(e.message, e.code, e.anything)
"""
    assert problems(src) == []


# -- const, calls, unreachable, match -----------------------------------------------------


def test_assign_to_const():
    src = "const LIMIT = 10\nLIMIT = 20\nLIMIT += 1\n"
    assert problems(src) == [(2, 1, "error", "const-assign"), (3, 1, "error", "const-assign")]


def test_const_destructuring():
    assert codes("const [a, b] = [1, 2]\na = 3\nsay(b)\n") == ["const-assign"]


def test_calling_a_non_function():
    src = 'let x = 5\nx()\nlet name = "a"\nsay(name(1))\nimport fs\nfs()\n'
    assert problems(src) == [(2, 1, "error", "not-callable"), (4, 5, "error", "not-callable"),
                             (6, 1, "error", "not-callable")]
    assert messages(src)[0] == "I can only call functions, not a number."


def test_reassigned_variables_lose_their_type():
    assert problems("let x = 5\nx = fn() { return 1 }\nsay(x())\n") == []


def test_unreachable_code():
    src = """fn f(x) {
  if x {
    throw "bad"
    say("never")
  }
  return 1
  say("never either")
}
for i in range(3) {
  break
  say(i)
}
say(f(false))
"""
    assert problems(src) == [(4, 5, "warning", "unreachable"), (7, 3, "warning", "unreachable"),
                             (11, 3, "warning", "unreachable")]


def test_match_over_enum_missing_members():
    src = """enum Level { low, medium, high }
let lv = Level.low
let a = match lv {
  Level.low => "l"
  Level.medium => "m"
}
let b = match lv {
  Level.low or Level.medium => "x"
  Level.high => "h"
}
let c = match lv {
  Level.low => "l"
  _ => "other"
}
say(a, b, c)
"""
    assert problems(src) == [(3, 9, "warning", "non-exhaustive-match")]
    assert "doesn't handle Level.high" in messages(src)[0]


def test_match_bindings_are_scoped_to_their_arm():
    src = """let v = [1, 2, 3]
let r = match v {
  [first, ...rest] => first + len(rest)
  n if n > 3 => n
  _ => 0
}
say(r)
"""
    assert problems(src) == []


# -- types -------------------------------------------------------------------------------


def test_annotation_mismatch_on_let():
    src = 'let n: number = "five"\nlet ok: number | nil = nil\nlet t: list<number> = [1]\nlet a: any = 1\nlet f: fn(number) -> number = x => x\n'
    assert problems(src) == [(1, 5, "error", "type-mismatch")]
    assert messages(src) == ["'n' should be number, but this is text."]


def test_nil_for_a_non_nil_annotation_is_only_a_warning():
    assert problems("let conn: map = nil\n") == [(1, 5, "warning", "nil-annotation")]


def test_annotated_variable_reassigned_with_wrong_type():
    assert codes('let count: number = 0\ncount = "lots"\n') == ["type-mismatch"]


def test_annotated_parameters_and_return():
    src = """fn shout(word: text, times: number = 1) -> text {
  if times > 3 {
    return 0
  }
  return word.upper() * times
}
say(shout(5))
say(shout("hi", times = "x"))
"""
    assert problems(src) == [(3, 5, "error", "type-mismatch"), (7, 5, "error", "type-mismatch"),
                             (8, 17, "error", "type-mismatch")]
    assert messages(src)[0] == "shout() should give back text, but this is a number."
    assert messages(src)[1] == "shout()'s input 'word' should be text, but this is a number."


def test_annotated_return_kind_flows_into_callers():
    src = "fn label() -> text { return \"x\" }\nsay(label().nope())\n"
    assert codes(src) == ["unknown-method"]


def test_class_annotations_and_instances():
    src = """class Point {
  let x: number = 0
  fn init(x) { self.x = x }
}
class Line { }
let p: Point = Point(1)
let q: Point = Line()
p.x = "far"
fn dist(a: Point) -> number { return a.x }
say(dist(Line()), q)
"""
    assert problems(src) == [(7, 5, "error", "type-mismatch"), (8, 3, "error", "type-mismatch"),
                             (10, 5, "error", "type-mismatch")]


def test_class_field_default_annotation():
    assert codes('class C {\n  let count: number = "zero"\n}\nsay(C())\n') == ["type-mismatch"]


def test_operators_that_can_never_work():
    src = 'let total = 5 - "a"\nlet x = [1] + 2\nlet y = 1 < "b"\nlet z = -"x"\nsay("a" + 1, [1] * 2, "ab" * 2)\n'
    assert problems(src) == [(1, 15, "error", "type-mismatch"), (2, 13, "error", "type-mismatch"),
                             (3, 11, "error", "type-mismatch"), (4, 9, "error", "type-mismatch")]
    assert messages(src)[:2] == ["I can only subtract numbers, not number and text.", "I can't add list and number."]


def test_index_and_in_mismatches():
    src = 'let s = "abc"\ns[0] = "z"\nsay(5 in 7)\nsay(1 in "abc")\nlet xs = [1]\nsay(xs["a"])\n'
    assert codes(src) == ["type-mismatch"] * 4


def test_stdlib_return_kinds_are_used():
    src = "import math\nlet r = math.sqrt(4) + \"x\"\nlet q = math.sqrt(4) - [1]\n"
    assert codes(src) == ["type-mismatch"]


def test_unknown_types_stay_quiet():
    # Nothing is known about these values, so nothing is reported.
    src = """import json
fn f(a, b) { return a - b + a.whatever() }
let data = json.parse("[1]")
say(data.anything(), f(1, 2), ask().upper())
"""
    assert problems(src) == []


# -- private names and other files ---------------------------------------------------------


def test_private_names_and_exports_of_other_files(tmp_path):
    (tmp_path / "helpers.tb").write_text(
        "let _secret = 1\nlet shown = 2\nfn double(x) { return x * 2 }\n"
        "class Widget {\n  fn init() { self._hidden = 1 }\n}\n"
    )
    (tmp_path / "lib2.tb").write_text("export fn pub() { return 1 }\nfn internal() { return 2 }\n")
    main = tmp_path / "main.tb"
    src = """import "helpers.tb"
import "lib2.tb"
say(helpers.shown, helpers.double(1, 2), helpers._secret, helpers.nope)
let w = helpers.Widget()
say(w._hidden)
say(lib2.pub(), lib2.internal())
from "helpers.tb" import shown
say(shown)
"""
    got = problems(src, str(main))
    assert got == [
        (3, 28, "error", "wrong-arity"),
        (3, 50, "error", "private-access"),
        (3, 67, "error", "unknown-member"),
        (5, 7, "warning", "private-access"),
        (6, 22, "error", "not-exported"),
    ]


def test_missing_file_import(tmp_path):
    main = tmp_path / "main.tb"
    assert codes('import "nothere.tb"\nsay(nothere)\n', str(main)) == ["unknown-library"]


# -- things that must stay clean -----------------------------------------------------------

CLEAN_PROGRAMS = {
    "closures": """fn counter() {
  let n = 0
  return fn() { n += 1; return n }
}
let next = counter()
say(next(), next())
""",
    "mutual_recursion": """fn is_even(n) { if n == 0 { return true } return is_odd(n - 1) }
fn is_odd(n) { if n == 0 { return false } return is_even(n - 1) }
say(is_even(4))
""",
    "destructuring": """let [a, b, ...rest] = [1, 2, 3, 4]
let {name, port = 22} = {"name": "box"}
for [k, v] in [[1, 2]] { say(k, v) }
for k, v in {"x": 1} { say(k, v) }
let x, y = [1, 2]
x, y = y, x
fn first([h, ...t]) { return h }
say(a, b, rest, name, port, x, y, first([1]))
""",
    "generators": """fn count(n) {
  let i = 0
  while i < n { yield i; i += 1 }
}
for v in count(3) { say(v) }
say(count(2).to_list(), count(5).take(2).to_list())
""",
    "classes": """class Animal {
  static let made = 0
  let sound = "..."
  fn init(name) { self.name = name; Animal.made += 1 }
  fn speak() { return f"{self.name} says {self.sound}" }
  fn text() { return self.name }
}
class Dog extends Animal {
  let sound = "woof"
  fn init(name) { super.init(name); self.tricks = [] }
  fn speak() { return super.speak() + "!" }
}
let d = Dog("Rex")
say(d.speak(), d is Animal, type(d), Animal.made, d.tricks.len())
""",
    "errors": """try {
  throw ValueError("bad", {"code": 7})
} catch err: ValueError or TypeError {
  say(err.message, err.code, err.kind, err.line)
} catch err {
  say(err)
} finally {
  say("done")
}
""",
    "enum_full_match": """enum Color { red, green }
fn name(c) {
  return match c {
    Color.red => "r"
    Color.green => "g"
  }
}
say(name(Color.red))
""",
    "lambdas_and_ranges": """let sq = x => x * x
let add = (a, b) => a + b
say(sq(3), add(1, 2), (0..10).step(2).to_list(), [1, 2].map(sq), 1..=3)
for i in 0..3 { say(i * 2) }
""",
    "conditional_and_is": """let v = 3
say(if v > 2 then "big" else "small", v is number, v is not text)
""",
    "async": """async fn work(x) { return x * 2 }
let t = work(2)
say(await t)
""",
    "defer_and_assert": """fn f() {
  defer { say("bye") }
  assert 1 < 2, "math works"
  return 1
}
say(f())
""",
    "number_text_mix": """let n = 3
say("n = " + n, n + 0.5, 7 // 2, 2 ** 10, 5 % 3, 1 << 2, ~1)
""",
    "field_defaults_use_later_names": """class Config {
  let limit = default_limit()
}
fn default_limit() { return 5 }
say(Config().limit)
""",
    "keyword_rest": """fn tag(name, **attrs) { return name + str(attrs) }
say(tag("a", href = "x"))
""",
    "shadowing": """let x = 1
fn f(x) { return x + 1 }
if true { let x = "inner"; say(x.upper()) }
say(f(2), x)
""",
    "bytes": """let data = b"abc"
say(data.hex(), data[0] + 1, data.len(), len(data))
""",
}


@pytest.mark.parametrize("name", sorted(CLEAN_PROGRAMS))
def test_clean_programs_have_no_problems(name):
    assert check_source(CLEAN_PROGRAMS[name]) == [], name


@pytest.mark.parametrize("name", sorted(CLEAN_PROGRAMS))
def test_clean_programs_really_run(name):
    import tb
    tb.run(CLEAN_PROGRAMS[name], output=lambda text: None)


def test_ignore_comment_silences_a_line():
    src = 'say(nope) # tb-check: ignore\nsay(nope2)\n'
    assert problems(src) == [(2, 5, "error", "undefined-name")]


def test_check_source_never_raises_on_garbage():
    for src in ["", "}", "let", "fn (", '"never ends', "#only a comment", "let x = [" * 50]:
        result = check_source(src)
        assert all(isinstance(d, Diagnostic) for d in result)


# -- the CLI -------------------------------------------------------------------------------


def run_cli(args, capsys):
    code = check_cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_reports_and_exits_1_on_errors(tmp_path, capsys):
    bad = tmp_path / "bad.tb"
    bad.write_text('let name = "x"\nsay(nmae)\nfn f() {\n  let t = 1\n  return 2\n}\nsay(f())\n')
    code, out, _ = run_cli([str(bad)], capsys)
    assert code == 1
    lines = out.splitlines()
    assert lines[0] == f"{bad}:2:5: error: I don't know what 'nmae' is. Did you mean 'name'?"
    assert lines[1] == f"{bad}:4:7: warning: The variable 't' is never used."
    assert lines[-1] == "Found 1 error and 1 warning in 1 of 1 file. Fix the errors and run tb check again."


def test_cli_clean_folder_and_strict(tmp_path, capsys):
    (tmp_path / "ok.tb").write_text('say("hi")\n')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "warn.tb").write_text("import json\n")
    (tmp_path / "tb_packages").mkdir()
    (tmp_path / "tb_packages" / "skip.tb").write_text("say(nope)\n")
    code, out, _ = run_cli([str(tmp_path)], capsys)
    assert code == 0
    assert out.splitlines()[-1] == "Checked 2 files: no errors, 1 warning."
    code, out, _ = run_cli([str(tmp_path), "--strict"], capsys)
    assert code == 1
    code, out, _ = run_cli([str(tmp_path / "ok.tb")], capsys)
    assert (code, out.strip()) == (0, "All good! Checked 1 file: no problems found.")


def test_cli_json(tmp_path, capsys):
    bad = tmp_path / "bad.tb"
    bad.write_text("say(nope)\n")
    code, out, _ = run_cli(["--json", str(bad)], capsys)
    assert code == 1
    data = json.loads(out)
    assert data["files"] == 1 and data["errors"] == 1 and data["warnings"] == 0
    [d] = data["diagnostics"]
    assert (d["file"], d["line"], d["col"], d["code"], d["severity"]) == (str(bad), 1, 5, "undefined-name", "error")


def test_cli_missing_path_and_bad_option(tmp_path, capsys):
    code, _, err = run_cli([str(tmp_path / "nope.tb")], capsys)
    assert code == 2 and "can't find" in err
    code, _, err = run_cli(["--bogus"], capsys)
    assert code == 2 and "--bogus" in err


def test_cli_help(capsys):
    code, out, _ = run_cli(["--help"], capsys)
    assert code == 0
    assert out.startswith("tb check - find mistakes in TB programs without running them")
    assert "--json" in out and "--strict" in out


def test_tb_check_through_the_tb_command(tmp_path):
    import subprocess
    bad = tmp_path / "bad.tb"
    bad.write_text("say(nope)\n")
    result = subprocess.run([sys.executable, str(ROOT / "tb.py"), "check", str(bad)],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert result.returncode == 1
    assert f"{bad}:1:5: error: I don't know what 'nope' is." in result.stdout
    result = subprocess.run([sys.executable, str(ROOT / "tb.py"), "check", "--help"],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert result.returncode == 0 and "tb check" in result.stdout


# -- the whole repo ------------------------------------------------------------------------

REPO_DIRS = ["examples", "apps", "packages", "scripts/lib_examples", "spec"]


def repo_programs():
    files = []
    for folder in REPO_DIRS:
        base = ROOT / folder
        if base.is_dir():
            files.extend(sorted(str(p) for p in base.rglob("*.tb")))
    return files


def test_every_repo_program_checks_without_errors():
    files = repo_programs()
    assert len(files) > 50
    found = []
    for path in files:
        source = Path(path).read_text(encoding="utf-8")
        for d in check_source(source, path):
            if d.severity == "error":
                found.append(d.format(os.path.relpath(path, ROOT)))
    assert found == []
