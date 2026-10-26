"""TB 3.0 Part A9 (classes, enums, modules) and A10 (typed errors)."""

import subprocess
import sys
from pathlib import Path

import pytest

import tb
from conftest import run_tb, tb_error

ROOT = Path(__file__).resolve().parents[2]

ANIMALS = '''class Animal {
  let sound = "..."
  static let count = 0
  fn init(name) { self.name = name; Animal.count += 1 }
  fn speak() { return f"{self.name} says {self.sound}" }
  static fn make(name) { return Animal(name) }
}
class Dog extends Animal {
  let sound = "woof"
  fn init(name) { super.init(name); self.tricks = [] }
  fn speak() { return super.speak() + "!" }
}
'''


def test_classes_inheritance_super_and_statics():
    src = ANIMALS + '''let d = Dog("Rex")
say(d.speak())
say(d is Animal, d is Dog, type(d), type(Dog), Animal.count, Animal.make("Kit").speak(), Animal.count)
say(d)
say(d.fields())'''
    assert run_tb(src) == [
        "Rex says woof!",
        "true true Dog class 1 Kit says ... 2",
        'Dog {sound: "woof", name: "Rex", tricks: []}',
        '{"sound": "woof", "name": "Rex", "tricks": []}',
    ]


def test_field_defaults_are_per_instance():
    src = "class Bag { let items = [] }\nlet a = Bag()\nlet b = Bag()\na.items.push(1)\nsay(a.items, b.items)"
    assert run_tb(src) == ["[1] []"]


def test_missing_field_did_you_mean():
    err = tb_error(ANIMALS + 'let d = Dog("Rex")\nsay(d.nme)')
    assert "has no field or method called 'nme'" in str(err)
    assert "Did you mean 'name'?" in str(err)


def test_class_without_init_rejects_arguments():
    assert "takes no inputs" in str(tb_error("class E {}\nE(1)"))
    assert run_tb("class E {}\nsay(E())") == ["E {}"]


def test_special_methods():
    src = '''class P {
  fn init(x) { self.x = x }
  fn text() { return f"P({self.x})" }
  fn equals(o) { return o is P and self.x == o.x }
  fn compare(o) { return self.x - o.x }
  fn get(i) { return i * self.x }
  fn set(i, v) { self.x = v }
  fn call(a) { return a + self.x }
  fn len() { return 99 }
  fn contains(v) { return v == self.x }
}
let p = P(2)
say(p, str(p), f"{p}", P(2) == P(2), P(2) != P(3), P(1) < P(2), [P(3), P(1)].sort())
say(p[5], p(1), len(p), 2 in p, 3 in p)
p[0] = 7
say(p.x)'''
    assert run_tb(src) == ["P(2) P(2) P(2) true true true [P(1), P(3)]", "10 3 99 true false", "7"]


def test_iter_protocol_with_done():
    src = '''class Countdown {
  fn init(n) { self.n = n }
  fn iter() { return self }
  fn next() {
    if self.n <= 0 { return done }
    self.n -= 1
    return self.n + 1
  }
}
for x in Countdown(3) { say(x) }'''
    assert run_tb(src) == ["3", "2", "1"]


def test_instances_cannot_be_map_keys():
    assert "Oops!" in str(tb_error("class A {}\nlet m = {}\nm[A()] = 1"))


def test_self_outside_method():
    assert "Oops!" in str(tb_error("say(self)"))


def test_enums():
    src = '''enum Level { low, medium, high }
say(Level.low, Level.low.name, Level.high.index, Level.values(), type(Level.low))
say(Level.low == Level.low, Level.low == Level.high, Level.low is Level)
say(match Level.medium { Level.low => "l"
 Level.medium => "m"
 _ => "?" })'''
    assert run_tb(src) == [
        "Level.low low 2 [Level.low, Level.medium, Level.high] Level",
        "true false true",
        "m",
    ]


def test_unknown_enum_member():
    assert "Oops!" in str(tb_error("enum L { a }\nsay(L.b)"))


def test_class_patterns_in_match():
    src = '''class Alert { fn init(name) { self.name = name } }
say(match Alert("x") { Alert(a) => a.name
 _ => "?" })
say(match Alert("y") { Alert {name: n} => n
 _ => "?" })
say(match 5 { Alert(a) => "alert"
 _ => "not" })'''
    assert run_tb(src) == ["x", "y", "not"]


def test_export_from_import_and_packages(tmp_path):
    (tmp_path / "helpers.tb").write_text(
        "export fn double(x) { return x * 2 }\nexport let NAME = \"h\"\nfn secret() { return 1 }\n")
    (tmp_path / "plain.tb").write_text("fn triple(x) { return x * 3 }\n")
    (tmp_path / "tb.toml").write_text('[package]\nname = "demo"\n')
    pkg = tmp_path / "tb_packages" / "tb-colors"
    pkg.mkdir(parents=True)
    (pkg / "lib.tb").write_text('fn red(t) { return "<" + t + ">" }\n')
    pkg2 = tmp_path / "tb_packages" / "tables"
    pkg2.mkdir(parents=True)
    (pkg2 / "tb.toml").write_text('[package]\nname = "tables"\nmain = "src/main.tb"\n')
    (pkg2 / "src").mkdir()
    (pkg2 / "src" / "main.tb").write_text("fn row(x) { return [x] }\n")
    main = tmp_path / "main.tb"
    main.write_text('''from "helpers.tb" import double, NAME as label
import "helpers.tb"
import "plain.tb"
from math import sqrt
import "tb-colors"
import tb_colors as colours_again
import tables
say(double(2), label, sqrt(16), plain.triple(2), tb_colors.red("x"), tables.row(1))
say(helpers.double(5))
helpers.secret()
''')
    lines = []
    with pytest.raises(tb.TBError) as info:
        interp = tb.Interpreter(output=lines.append)
        tb.run(main.read_text(), interpreter=interp, path=str(main))
    assert lines == ["4 h 4 6 <x> [1]", "10"]
    assert "secret" in str(info.value)


def test_from_import_unknown_name():
    assert "nope" in str(tb_error("from math import nope"))


# -- A10 typed errors -----------------------------------------------------------

def test_catch_by_class_and_finally():
    src = '''class MyErr extends Error {}
try { throw MyErr("bad", {"code": 7}) }
catch err: FileError { say("file") }
catch err: MyErr or ValueError { say("mine", err.message, err.code, err.kind, err.line, err is Error) }
finally { say("fin") }'''
    assert run_tb(src) == ["mine bad 7 my_err 2 true", "fin"]


def test_throw_text_and_map_still_work():
    src = '''try { throw "text" } catch e { say(e.message, e.kind, type(e), e is Error) }
try { throw {"message": "m", "x": 1} } catch e { say(e.message, e.x) }'''
    assert run_tb(src) == ["text error Error true", "m 1"]


def test_finally_runs_on_return_and_rethrow():
    src = '''fn f() {
  try { return 1 } finally { say("cleanup") }
}
say(f())
try {
  try { throw ValueError("v") } finally { say("inner finally") }
} catch e: ValueError { say("outer", e.kind) }'''
    assert run_tb(src) == ["cleanup", "1", "inner finally", "outer value_error"]


def test_uncaught_class_falls_through():
    err = tb_error('try { throw ValueError("v") } catch e: FileError { say("no") }')
    assert "v" in str(err)


def test_error_stack():
    src = '''fn a() { b() }
fn b() { throw Error("deep") }
try { a() } catch e { say(e.stack) }'''
    assert run_tb(src) == ['[{"fn": "b", "line": 2, "file": nil}, {"fn": "a", "line": 1, "file": nil}]']


def test_uncaught_error_prints_trace(tmp_path):
    prog = tmp_path / "t.tb"
    prog.write_text("fn a() { b() }\nfn b() { throw Error(\"deep\") }\na()\n")
    done = subprocess.run([sys.executable, str(ROOT / "tb.py"), str(prog)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert done.stderr.splitlines() == ["Oops! On line 2: deep", "  in b() ← a()"]


def test_stdlib_errors_are_typed(tmp_path):
    src = f'''import fs
import json
try {{ fs.read("{tmp_path.as_posix()}/missing.txt") }} catch e: FileError {{ say(e.kind, e is NotFoundError) }}
try {{ json.parse("{{") }} catch e: ParseError {{ say(e.kind) }}
try {{ fs.read(5) }} catch e: TypeError {{ say(e.kind) }}'''
    assert run_tb(src) == ["not_found_error true", "parse_error", "type_error"]


def test_error_class_family():
    src = "for c in [TypeError, ValueError, FileError, ParseError, NetworkError, PermissionError, NotFoundError, TimeoutError, StopIteration, AssertionError] { say(c(\"x\") is Error, c(\"x\").kind) }"
    out = run_tb(src)
    assert out[0] == "true type_error"
    assert out[-1] == "true assertion_error"
    assert len(out) == 10


def test_api_typed_fail_helpers():
    from tblib import _api

    for helper, kind in [(_api.fail_type, "type_error"), (_api.fail_value, "value_error"),
                         (_api.fail_file, "file_error"), (_api.fail_parse, "parse_error"),
                         (_api.fail_network, "network_error"), (_api.fail_timeout, "timeout_error"),
                         (_api.fail_permission, "permission_error"), (_api.fail_not_found, "not_found_error")]:
        with pytest.raises(tb.TBError) as info:
            helper(3, "msg", extra=1)
        assert info.value.kind == kind and info.value.line == 3 and info.value.fields == {"extra": 1}
    with pytest.raises(tb.TBError) as info:
        _api.fail(1, "m", cls="ParseError", where="x")
    assert info.value.kind == "parse_error" and info.value.fields == {"where": "x"}


# -- operator overloading and Python-backed (native) classes -----------------------

def test_operator_methods_on_classes():
    src = '''class V {
  fn init(x) { self.x = x }
  fn add(o) { return V(self.x + o.x) }
  fn mul(k) { return V(self.x * k) }
  fn rmul(k) { return V(self.x * k * 100) }
  fn negate() { return V(-self.x) }
  fn text() { return f"V({self.x})" }
}
let v = V(1)
v += V(4)
say(V(1) + V(2), V(2) * 3, 3 * V(2), -V(5), v, "v=" + V(1))'''
    assert run_tb(src) == ["V(3) V(6) V(600) V(-5) V(5) v=V(1)"]
    assert "can only subtract numbers, not V and V" in str(tb_error(src + "\nsay(V(1) - V(2))"))


def test_native_class_protocol_and_decimal_operators():
    src = '''import decimal
let a = decimal.of("0.1") + decimal.of("0.2")
say(a, a == decimal.of("0.3"), a == 0.3, 0.3 == a, type(a), 1 + a, a * 2, -a, a > decimal.of("0.2"))
say([decimal.of("2"), decimal.of("1")].sort(), f"{a}", a.text, 10 - a, decimal.of("7") // 2, decimal.of("7") % 2)'''
    assert run_tb(src) == ["0.3 true true true Decimal 1.3 0.6 -0.3 true", "[1, 2] 0.3 0.3 9.7 3 1"]


def test_api_native_class():
    from tblib import _api

    Money = _api.native_class("Money", "cents")

    @_api.method(Money, "add", 1)
    def add(line, me, other):
        return _api.make_object(Money, {"cents": me.fields["cents"] + other.fields["cents"]})

    @_api.method(Money, "text", 0)
    def text(line, me):
        return f"${me.fields['cents'] / 100:.2f}"

    lines = []
    interp = tb.Interpreter(output=lines.append)
    interp.globals.vars["a"] = _api.make_object(Money, {"cents": 150})
    interp.globals.vars["b"] = _api.make_object(Money, {"cents": 275})
    tb.run("say(a + b, type(a), (a + b).cents)", interpreter=interp)
    assert lines == ["$4.25 Money 425"]
