"""TB 2.0 language additions: maps, dot access and methods, f-text, imports,
try/catch/throw, break/continue, compound assignment, 'in', new built-ins."""

import pytest

import tb
from conftest import run_tb, tb_error


# -- maps ---------------------------------------------------------------------

def test_map_literal_and_display():
    assert run_tb('let m = {"name": "Tanisha", "age": 21}\nsay(m)') == ['{"name": "Tanisha", "age": 21}']
    assert run_tb("say({})") == ["{}"]
    assert run_tb('say({1: "one", 2.5: [1, "a"], "n": nil, "b": true})') == [
        '{1: "one", 2.5: [1, "a"], "n": nil, "b": true}'
    ]


def test_map_trailing_comma_and_multiline():
    src = 'let m = {\n  "a": 1,\n  "b": 2,\n}\nsay(len(m), m)'
    assert run_tb(src) == ['2 {"a": 1, "b": 2}']


def test_nested_maps_and_str():
    assert run_tb('let m = {"x": {"y": [1, {"z": "w"}]}}\nsay(str(m))') == ['{"x": {"y": [1, {"z": "w"}]}}']


def test_map_index_read_and_write():
    src = 'let m = {"a": 1}\nm["b"] = 2\nm["a"] = 10\nsay(m, m["a"], m["b"])'
    assert run_tb(src) == ['{"a": 10, "b": 2} 10 2']


def test_map_number_keys_whole_floats_match():
    assert run_tb('let m = {1: "x"}\nsay(m[1.0])\nm[2.0] = "y"\nsay(m)') == ["x", '{1: "x", 2: "y"}']


def test_map_missing_key_error():
    err = tb_error('let m = {"a": 1, "b": 2}\nsay(m["k"])')
    assert err.line == 2
    assert "There's no key \"k\" in this map. It has: a, b." in str(err)
    assert "The map is empty." in str(tb_error('say({}["x"])'))
    assert "There's no key 3 in this map" in str(tb_error('say({1: 2}[3])'))


def test_map_missing_key_lists_only_some_keys():
    keys = ", ".join(f'"k{i}": {i}' for i in range(20))
    assert "and 8 more" in str(tb_error(f"let m = {{{keys}}}\nsay(m.nope)"))


def test_map_bad_keys():
    assert "Map keys must be text or numbers, not list." in str(tb_error("let m = {[1]: 2}"))
    assert "not true/false" in str(tb_error("let m = {}\nm[true] = 1"))
    assert "not nil" in str(tb_error("say({}[nil])"))


def test_map_bare_word_key_hint():
    err = tb_error("let m = {name: 1}")
    assert "Map keys that are words need quotes" in str(err)
    # a defined name is used as a value, like Python
    assert run_tb('let k = "a"\nsay({k: 1})') == ['{"a": 1}']


def test_map_missing_colon():
    err = tb_error('let m = {"a" 1}')
    assert "I expected ':' between a key and its value" in str(err)


def test_map_unclosed():
    err = tb_error('let m = {"a": 1,\n"b": 2\n')
    assert err.line == 1 and "'{' was never closed" in str(err)


def test_map_equality_is_deep_and_order_free():
    src = 'say({"a": [1, {"b": 2}]} == {"a": [1, {"b": 2}]}, {"a": 1, "b": 2} == {"b": 2, "a": 1}, {"a": 1} == {"a": 2}, {"a": 1} != {"b": 1}, {} == [])'
    assert run_tb(src) == ["true true false true false"]


def test_map_containing_itself():
    assert run_tb('let m = {"a": 1}\nm["me"] = m\nsay(m)\nsay(m == m)') == ['{"a": 1, "me": {...}}', "true"]


def test_for_over_map_iterates_keys_in_order():
    src = 'let m = {"z": 1, "a": 2, "m": 3}\nfor k in m { say(k, m[k]) }'
    assert run_tb(src) == ["z 1", "a 2", "m 3"]


def test_for_over_map_can_change_it():
    src = 'let m = {"a": 1}\nfor k in m { m[k + "!"] = 0 }\nsay(m)'
    assert run_tb(src) == ['{"a": 1, "a!": 0}']


def test_len_of_map():
    assert run_tb('say(len({"a": 1, "b": 2}), len({}))') == ["2 0"]


def test_maps_are_shared_references():
    assert run_tb('let a = {}\nlet b = a\nb["x"] = 1\nsay(a)') == ['{"x": 1}']


def test_map_at_statement_start():
    assert run_tb('{"a": 1}\nsay("ok")') == ["ok"]


def test_old_index_errors_mention_maps_gently():
    assert "Only lists and text can be indexed, not number" in str(tb_error("let x = 5\nsay(x[0])"))


# -- dot access -----------------------------------------------------------------

def test_dot_reads_and_sets_map_fields():
    src = 'let p = {"name": "T"}\np.name = "Tanisha"\np.age = 21\nsay(p.name, p.age, p)'
    assert run_tb(src) == ['Tanisha 21 {"name": "Tanisha", "age": 21}']


def test_dot_missing_field_error_with_hint():
    err = tb_error('let p = {"name": "T", "age": 2}\nsay(p.nmae)')
    assert err.line == 2
    assert "There's no key \"nmae\" in this map. It has: name, age. Did you mean 'name'?" in str(err)


def test_map_function_field_is_called_without_self():
    src = 'let obj = {"greet": fn(n) { return "hi " + n }}\nsay(obj.greet("T"))'
    assert run_tb(src) == ["hi T"]


def test_map_key_wins_over_method():
    assert run_tb('let m = {"keys": 5}\nsay(m.keys)') == ["5"]
    assert run_tb('let m = {"a": 1}\nsay(m.keys())') == ['["a"]']


def test_nested_dot_and_index():
    src = 'let d = {"user": {"tags": ["a", "b"]}}\nsay(d.user.tags[1])\nd.user.tags[0] = "z"\nsay(d)'
    assert run_tb(src) == ["b", '{"user": {"tags": ["z", "b"]}}']


def test_dot_on_nil_and_bool_and_function():
    assert "I can't use '.name' on nil" in str(tb_error("let x = nil\nsay(x.name)"))
    assert "I can't use '.x' on true/false" in str(tb_error("say(true.x)"))
    assert "I can't use '.x' on function" in str(tb_error("say(say.x)"))


def test_setting_field_on_non_map():
    assert "Only maps have fields you can set, not list" in str(tb_error("let xs = []\nxs.a = 1"))


def test_method_chain_across_lines():
    src = 'let r = [3, 1, 2]\n  .sort()\n  .map(fn(x) { return x * 10 })\nsay(r)'
    assert run_tb(src) == ["[10, 20, 30]"]


def test_keywords_allowed_after_dot():
    assert run_tb('let m = {"true": 1, "in": 2}\nsay(m.true, m.in)') == ["1 2"]


def test_dot_needs_a_name():
    assert "I expected a name after '.'" in str(tb_error("let xs = [1]\nsay(xs.0)"))
    assert "digit before the dot" in str(tb_error("say(.5)"))


def test_number_literal_methods():
    assert run_tb("say(3.7.round(), 10.abs(), -3.2.floor())") == ["4 10 -3"]


# -- methods ------------------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ('"Hi".upper()', "HI"),
    ('"Hi".lower()', "hi"),
    ('"  hi \\n".trim()', "hi"),
    ('"a,b,,c".split(",")', '["a", "b", "", "c"]'),
    ('"a  b c".split()', '["a", "b", "c"]'),
    ('"abc".split("")', '["a", "b", "c"]'),
    ('"a\\nb\\n".lines()', '["a", "b"]'),
    ('"hello".contains("ell")', "true"),
    ('"hello".starts_with("he")', "true"),
    ('"hello".ends_with("lo")', "true"),
    ('"a-b-c".replace("-", "+")', "a+b+c"),
    ('"hello".find("l")', "2"),
    ('"hello".find("z")', "-1"),
    ('"hello".slice(1, 3)', "el"),
    ('"hello".slice(-3)', "llo"),
    ('"ab".repeat(3)', "ababab"),
    ('" 4.5 ".is_number()', "true"),
    ('"4x".is_number()', "false"),
    ('"42".to_number() + 1', "43"),
    ('"hey".chars()', '["h", "e", "y"]'),
    ('"hey".len()', "3"),
])
def test_text_methods(expr, expected):
    assert run_tb(f"say({expr})") == [expected]


@pytest.mark.parametrize("expr,expected", [
    ("[1, 2].contains(2)", "true"),
    ("[1, [2]].contains([2])", "true"),
    ('["a", "b"].index_of("b")', "1"),
    ('["a"].index_of("z")', "-1"),
    ('[1, "a", true].join(", ")', "1, a, true"),
    ('["x", "y"].join()', "xy"),
    ("[3, 1, 2].sort()", "[1, 2, 3]"),
    ('["b", "a"].sort()', '["a", "b"]'),
    ('["ccc", "a", "bb"].sort_by(fn(w) { return len(w) })', '["a", "bb", "ccc"]'),
    ("[1, 2, 3].reverse()", "[3, 2, 1]"),
    ("[1, 2, 3].map(fn(x) { return x + 1 })", "[2, 3, 4]"),
    ("[1, 2, 3, 4].filter(fn(x) { return x % 2 == 0 })", "[2, 4]"),
    ("[1, 2, 3].reduce(fn(a, b) { return a + b }, 10)", "16"),
    ("[1, 2, 3, 4].slice(1, 3)", "[2, 3]"),
    ("[1, 2, 3].slice(-2)", "[2, 3]"),
    ("[5, 6].first()", "5"),
    ("[5, 6].last()", "6"),
    ("[].first()", "nil"),
    ("[].last()", "nil"),
    ("[].is_empty()", "true"),
    ("[1].is_empty()", "false"),
    ("[1, 2].len()", "2"),
    ("[1, 2.5].sum()", "3.5"),
    ("[].sum()", "0"),
    ("[3, 1, 2].min()", "1"),
    ('["b", "a"].max()', "b"),
    ("[1, 2, 1, [3], [3]].unique()", "[1, 2, [3]]"),
])
def test_list_methods(expr, expected):
    assert run_tb(f"say({expr})") == [expected]


def test_list_methods_that_change_the_list():
    src = """
let xs = [1, 2]
let same = xs.push(3)
say(xs, same == xs)
say(xs.pop(), xs)
xs.insert(0, "a")
xs.insert(-1, "b")
xs.insert(len(xs), "end")
say(xs)
say(xs.remove_at(1), xs)
"""
    assert run_tb(src) == ["[1, 2, 3] true", "3 [1, 2]", '["a", 1, "b", 2, "end"]', '1 ["a", "b", 2, "end"]']


def test_list_methods_that_make_new_lists():
    src = "let xs = [3, 1, 2]\nlet a = xs.sort()\nlet b = xs.reverse()\nlet c = xs.copy()\npush(c, 9)\nsay(xs, a, b, c)"
    assert run_tb(src) == ["[3, 1, 2] [1, 2, 3] [2, 1, 3] [3, 1, 2, 9]"]


def test_each_returns_nil_and_calls():
    assert run_tb('say([1, 2].each(fn(x) { say("item", x) }))') == ["item 1", "item 2", "nil"]


@pytest.mark.parametrize("src,message", [
    ("[].pop()", "pop() can't take from an empty list."),
    ("[1].insert(5, 2)", "insert() can't put something at position 5"),
    ("[1].insert(0.5, 2)", "insert() needs a whole number, not 0.5"),
    ("[1].remove_at(3)", "Position 3 is outside the list"),
    ('[1, "a"].sort()', "sort() can only sort a list of all numbers or all text."),
    ("[[1]].sort()", "all numbers or all text"),
    ("[].min()", "min() needs at least one value, but the list is empty."),
    ('[1, "a"].max()', "max() needs all numbers or all text."),
    ('["a"].sum()', "sum() can only add up numbers, not text."),
    ("[1].map(5)", "map() needs a function, not number."),
    ("[1].map(fn(a, b) { })", "anonymous() needs 2 inputs, but got 1."),
    ('[1].join(5)', "join() needs text, not number."),
    ('"abc".slice("1")', "slice() needs a whole number"),
    ('"abc".contains(1)', "contains() needs text, not number."),
    ('"abc".repeat(-1)', "whole number of times"),
    ('"abc".to_number()', 'I can\'t turn "abc" into a number.'),
    ('"abc".upper(1)', "upper() needs 0 inputs, but got 1."),
    ('[1, 2].sort_by(fn(x) { return [x] })', "sort_by() can only sort"),
])
def test_method_errors(src, message):
    assert message in str(tb_error(f"say({src})"))


def test_unknown_method_lists_methods_and_hints():
    err = tb_error("let xs = [1]\nxs.append(2)")
    assert err.line == 2
    assert "A list doesn't have a method called 'append'. Did you mean 'push'?" in str(err)
    assert "List methods: contains, copy, each," in str(err)
    assert "Did you mean 'upper'?" in str(tb_error('"a".uper()'))
    assert "Did you mean 'trim'?" in str(tb_error('"a".strip()'))
    assert "A number doesn't have a method called 'zap'." in str(tb_error("say(5.zap())"))
    assert "Text methods:" in str(tb_error('"a".qqqqq()'))


def test_map_methods():
    src = """
let m = {"a": 1, "b": 2}
say(m.keys(), m.values(), m.items())
say(m.has("a"), m.has("z"), m.get("a"), m.get("z"), m.get("z", 0))
say(m.remove("a"), m.remove("nope"), m)
say(m.set("c", 3), m.len(), m.is_empty(), {}.is_empty())
let n = m.merge({"c": 30, "d": 4})
say(n, m)
let c = m.copy()
c.x = 1
say(m.has("x"))
"""
    assert run_tb(src) == [
        '["a", "b"] [1, 2] [["a", 1], ["b", 2]]',
        "true false 1 nil 0",
        '1 nil {"b": 2}',
        '{"b": 2, "c": 3} 2 false true',
        '{"b": 2, "c": 30, "d": 4} {"b": 2, "c": 3}',
        "false",
    ]
    assert "merge() needs a map, not list." in str(tb_error("say({}.merge([]))"))
    assert "Map keys must be text or numbers" in str(tb_error("say({}.get([1]))"))


@pytest.mark.parametrize("expr,expected", [
    ("3.14159.round(2)", "3.14"),
    ("2.5.round()", "3"),
    ("(0 - 2.5).round()", "-3"),
    ("1234.round(-2)", "1200"),
    ("2.7.floor()", "2"),
    ("2.1.ceil()", "3"),
    ("(0 - 4).abs()", "4"),
    ("2.50.to_text()", "2.5"),
    ("7.to_text() + \"!\"", "7!"),
])
def test_number_methods(expr, expected):
    assert run_tb(f"say({expr})") == [expected]


def test_methods_are_values():
    assert run_tb('let up = "hi".upper\nsay(up(), up)') == ["HI <built-in fn upper>"]


# -- f-text -------------------------------------------------------------------------

def test_fstring_basics():
    src = 'let name = "Tanisha"\nlet xs = [1, 2, 3]\nsay(f"Hello {name}, you have {len(xs)} items, {xs[0] + 10}.")'
    assert run_tb(src) == ["Hello Tanisha, you have 3 items, 11."]


def test_fstring_values_use_say_formatting():
    assert run_tb('say(f"{nil} {true} {2.50} {[1, "a"]} { {"k": "v"} }")') == ['nil true 2.5 [1, "a"] {"k": "v"}']


def test_fstring_literal_braces_and_single_quotes():
    assert run_tb("let x = 1\nsay(f'{{x}} = {x}', f\"}}{{\")") == ["{x} = 1 }{"]


def test_fstring_nested_quotes_and_maps():
    src = 'let m = {"city": "Kolkata"}\nsay(f"{m["city"]} / {m.get(\'x\', "none")} / { {"a": 1}["a"] }")'
    assert run_tb(src) == ["Kolkata / none / 1"]


def test_fstring_escapes():
    assert run_tb('say(f"a\\tb\\"{1}\\"")') == ['a\tb"1"']


def test_plain_strings_never_interpolate():
    assert run_tb('let name = "x"\nsay("{name} {{")') == ["{name} {{"]


def test_f_as_a_name_still_works():
    assert run_tb('let f = fn(x) { return x }\nsay(f("a"), f ("b"))') == ["a b"]


@pytest.mark.parametrize("src,message", [
    ('say(f"hi {}")', "empty {}"),
    ('say(f"a } b")', "write it twice: }}"),
    ('say(f"a {x")', "'{' inside the f-text was never closed"),
    ('say(f"a {1 2}")', "I couldn't understand {1 2}"),
    ('say(f"abc)', "never ends"),
])
def test_fstring_errors(src, message):
    err = tb_error(src)
    assert message in str(err) and err.line == 1


def test_fstring_error_line_numbers():
    err = tb_error('say(1)\nsay(f"value: {missing}")')
    assert err.line == 2 and "I don't know what 'missing' is" in str(err)


def test_fstring_with_function_inside():
    assert run_tb('say(f"{[1, 2].map(fn(x) { return x * 2 })}")') == ["[2, 4]"]


# -- imports ------------------------------------------------------------------------

def run_file(tmp_path, files, main="main.tb", seed=0):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    lines = []
    tb.run((tmp_path / main).read_text(encoding="utf-8"), output=lines.append, path=str(tmp_path / main))
    return lines


def file_error(tmp_path, files, main="main.tb"):
    with pytest.raises(tb.TBError) as info:
        run_file(tmp_path, files, main)
    return info.value


def test_import_stdlib_and_alias():
    assert run_tb("import math\nsay(math.sqrt(9))") == ["3"]
    assert run_tb("import math as m\nsay(m.pi.round(2), type(m), m)") == ["3.14 module <library math>"]


def test_unknown_library_did_you_mean_and_list():
    err = tb_error("say(1)\nimport jsn")
    assert err.line == 2
    msg = str(err)
    assert "There's no library called 'jsn'. Did you mean 'json'?" in msg
    assert "Libraries: " in msg and "fs" in msg and "math" in msg
    assert "_api" not in msg
    assert "There's no library called '_api'" in str(tb_error("import _api"))


def test_unknown_library_member():
    err = tb_error("import math\nsay(math.sqroot(4))")
    assert "The math library has nothing called 'sqroot'. Did you mean 'sqrt'?" in str(err)
    assert "It has: " in str(err)


def test_library_fields_cannot_be_changed():
    assert "You can't change the math library's 'pi'." in str(tb_error("import math\nmath.pi = 3"))


def test_forgot_import_hint():
    assert "Did you forget 'import fs' at the top?" in str(tb_error('fs.read("x")'))


def test_import_member_syntax_error():
    assert "Import the whole library with 'import fs'" in str(tb_error("import fs.read"))


def test_bare_import_and_throw_need_something():
    assert "What should I import?" in str(tb_error("import"))
    assert "What should I throw?" in str(tb_error("throw\n"))


def test_libraries_are_cached_per_program():
    assert run_tb("import random\nimport random as r2\nsay(random == r2)") == ["true"]


def test_import_inside_function_and_block():
    src = "fn root(x) {\n  import math\n  return math.sqrt(x)\n}\nsay(root(16))\nif true { import text as t\nsay(t.reverse(\"ab\")) }"
    assert run_tb(src) == ["4", "ba"]


def test_import_tb_file(tmp_path):
    files = {
        "helpers.tb": '# Helpers.\nlet greeting = "hi"\nfn double(x) { return x * 2 }\nlet _secret = 1\nsay("loading helpers")\n',
        "main.tb": 'import "helpers.tb"\nimport "helpers.tb" as h\nsay(helpers.double(4), h.greeting, helpers == h, type(h))\n',
    }
    assert run_file(tmp_path, files) == ["loading helpers", "8 hi true module"]


def test_import_tb_file_private_names_hidden(tmp_path):
    files = {"helpers.tb": "let _secret = 1\nlet shown = 2\n", "main.tb": 'import "helpers.tb"\nsay(helpers._secret)'}
    err = file_error(tmp_path, files)
    assert "helpers.tb has nothing called '_secret'. It has: shown." in str(err)


def test_import_tb_file_without_extension_and_in_subfolder(tmp_path):
    files = {
        "lib/util.tb": 'import "more.tb"\nfn hi() { return "hi " + more.name }\n',
        "lib/more.tb": 'let name = "from more"\n',
        "main.tb": 'import "lib/util"\nsay(util.hi())\n',
    }
    assert run_file(tmp_path, files) == ["hi from more"]


def test_module_variables_are_live(tmp_path):
    files = {
        "counter.tb": "let count = 0\nfn bump() { count += 1 }\n",
        "main.tb": 'import "counter.tb"\ncounter.bump()\ncounter.bump()\nsay(counter.count)\n',
    }
    assert run_file(tmp_path, files) == ["2"]


def test_modules_do_not_see_main_program_names(tmp_path):
    files = {"m.tb": "fn peek() { return secret }\n", "main.tb": 'let secret = 1\nimport "m.tb"\nm.peek()\n'}
    err = file_error(tmp_path, files)
    assert "I don't know what 'secret' is" in str(err)


def test_import_file_not_found(tmp_path):
    err = file_error(tmp_path, {"main.tb": 'say(1)\nimport "nope.tb"'})
    assert err.line == 2 and "I couldn't find the file 'nope.tb' to import" in str(err)


def test_import_bad_stem_needs_alias(tmp_path):
    err = file_error(tmp_path, {"main.tb": 'import "my-helpers.tb"'})
    assert 'I can\'t make a name from \'my-helpers.tb\'. Give it one with: import "my-helpers.tb" as helpers' in str(err)
    files = {"my-helpers.tb": "let x = 1\n", "main.tb": 'import "my-helpers.tb" as mh\nsay(mh.x)'}
    assert run_file(tmp_path, files) == ["1"]


def test_import_cycle(tmp_path):
    files = {"a.tb": 'import "b.tb"\n', "b.tb": 'import "c.tb"\n', "c.tb": 'say(1)\nimport "a.tb"\n'}
    err = file_error(tmp_path, files, main="a.tb")
    msg = str(err)
    assert "These files import each other in a circle: a.tb -> b.tb -> c.tb -> a.tb." in msg
    assert msg.startswith("Oops! On line 2 of c.tb:")


def test_import_self_is_a_cycle(tmp_path):
    err = file_error(tmp_path, {"main.tb": 'import "main.tb"\n'})
    assert "in a circle: main.tb -> main.tb" in str(err)


def test_error_inside_module_names_the_file(tmp_path):
    files = {
        "helpers.tb": "fn boom() {\n  return nope\n}\nfn call(f) { return f() }\n",
        "main.tb": 'import "helpers.tb"\nsay(1)\nhelpers.boom()\n',
    }
    err = file_error(tmp_path, files)
    assert err.line == 2 and str(err).startswith("Oops! On line 2 of helpers.tb: I don't know what 'nope' is.")


def test_error_in_main_callback_called_by_module_has_no_file(tmp_path):
    files = {
        "helpers.tb": "fn call(f) {\n  return f()\n}\n",
        "main.tb": 'import "helpers.tb"\nhelpers.call(fn() {\n  return nope\n})\n',
    }
    err = file_error(tmp_path, files)
    assert str(err).startswith("Oops! On line 3: I don't know what 'nope' is.")


def test_library_error_called_from_module_points_at_module_line(tmp_path):
    files = {
        "helpers.tb": 'import json\nfn load() {\n  return json.parse("{")\n}\n',
        "main.tb": 'import "helpers.tb"\nhelpers.load()\n',
    }
    assert str(file_error(tmp_path, files)).startswith("Oops! On line 3 of helpers.tb: That text isn't valid JSON")


def test_syntax_error_in_module_names_the_file(tmp_path):
    files = {"bad.tb": "let x = \n", "main.tb": 'import "bad.tb"\n'}
    assert "of bad.tb:" in str(file_error(tmp_path, files))


def test_unquoted_import_of_own_file_hint(tmp_path, monkeypatch):
    (tmp_path / "helpers.tb").write_text("let x = 1\n")
    monkeypatch.chdir(tmp_path)
    err = tb_error("import helpers")
    assert 'To use your own file, put its name in quotes: import "helpers.tb"' in str(err)


def test_relative_imports_use_cwd_without_a_path(tmp_path, monkeypatch):
    (tmp_path / "h.tb").write_text("let x = 5\n")
    monkeypatch.chdir(tmp_path)
    assert run_tb('import "h.tb"\nsay(h.x)') == ["5"]


def test_module_runs_once_even_if_imported_twice(tmp_path):
    files = {
        "a.tb": 'import "shared.tb"\n',
        "shared.tb": 'say("shared loaded")\nlet value = 1\n',
        "main.tb": 'import "shared.tb"\nimport "a.tb"\nsay(a.shared == shared)\n',
    }
    assert run_file(tmp_path, files) == ["shared loaded", "true"]


def test_tb_written_library_docs(tmp_path):
    module_doc, docs = tb.tb_source_docs(
        "# Greetings.\n# Second line.\n\n# Say hi.\n# Politely.\nfn hi(name, x) { }\nfn bare() { }\n  fn nested() { }\n"
    )
    assert module_doc == "Greetings.\nSecond line."
    assert docs == {"hi": "hi(name, x)\nSay hi.\nPolitely.", "bare": "bare()"}


# -- try / catch / throw -----------------------------------------------------------------

def test_catch_builtin_error_map():
    src = 'try {\n  say(1 / 0)\n} catch err {\n  say(err.message, err.line, err.kind, type(err))\n}'
    assert run_tb(src) == ["You can't divide by zero. 2 error map"]


def test_catch_library_error():
    src = 'import json\ntry {\n  let data = json.parse("{nope")\n} catch err {\n  say(f"Bad file: {err.message} (line {err.line})")\n}'
    out = run_tb(src)
    assert out[0].startswith("Bad file: That text isn't valid JSON:") and out[0].endswith("(line 3)")


def test_throw_text_and_map_and_other_values():
    src = """
try { throw "Something went wrong" } catch e { say(e.message, e.line) }
try { throw {"message": "custom", "code": 7} } catch e { say(e.message, e.code, e.kind) }
try { throw {"message": "x", "kind": "auth"} } catch e { say(e.kind) }
try { throw 42 } catch e { say(e.message, type(e.message)) }
try { throw {"code": 1} } catch e { say(e.message) }
"""
    assert run_tb(src) == [
        "Something went wrong 2", "custom 7 error", "auth", "42 text", '{"code": 1}',
    ]


def test_uncaught_throw_prints_like_other_errors():
    err = tb_error('say(1)\nthrow "Something went wrong"')
    assert str(err) == "Oops! On line 2: Something went wrong"
    err = tb_error('throw {"message": "bad", "code": 3}')
    assert str(err) == "Oops! On line 1: bad" and err.fields == {"code": 3}


def test_catch_without_name():
    assert run_tb('try { throw "x" } catch { say("caught") }\nsay("after")') == ["caught", "after"]


def test_try_without_error_skips_catch():
    assert run_tb('try { say("fine") } catch e { say("no") }') == ["fine"]


def test_rethrow_keeps_original_line():
    src = 'try {\n  try {\n    say(nope)\n  } catch e {\n    throw e\n  }\n} catch outer {\n  say(outer.line, outer.message)\n}'
    assert run_tb(src) == ["3 I don't know what 'nope' is."]
    assert tb_error('try {\n  say(nope)\n} catch e {\n  throw e\n}').line == 2


def test_error_in_catch_handler_propagates():
    err = tb_error('try { throw "a" } catch e {\n  throw "b"\n}')
    assert err.message == "b" and err.line == 2


def test_catch_scope_and_try_scope():
    src = 'let x = 1\ntry { let y = 2\nx = 5\nthrow "z" } catch e { let z = 3 }\nsay(x)\nsay(y)'
    assert "I don't know what 'y' is" in str(tb_error(src))


def test_catch_recursion_limit_and_depth_recovers():
    src = 'fn f() { return f() }\ntry { f() } catch e { say("deep:", e.message.contains("too many times")) }\nfn g(n) { if n == 0 { return 0 } return g(n - 1) }\nsay(g(2000))'
    assert run_tb(src) == ["deep: true", "0"]


def test_throw_inside_function_caught_outside():
    src = 'fn check(x) {\n  if x < 0 { throw {"message": "negative", "value": x} }\n  return x\n}\ntry { check(-5) } catch e { say(e.message, e.value, e.line) }'
    assert run_tb(src) == ["negative -5 2"]


def test_exit_is_not_catchable():
    with pytest.raises(tb.TBExit) as info:
        run_tb('try { exit(3) } catch e { say("caught") }')
    assert info.value.code == 3


@pytest.mark.parametrize("src,message", [
    ('try { say(1) }\nsay(2)', "A 'try' block needs a 'catch' after it"),
    ('try { } catch (e) { }', "Write the error's name without brackets"),
    ('catch e { }', "This 'catch' doesn't belong to any 'try'."),
    ('catch { }', "doesn't belong to any 'try'"),
    ('try { say(1)', "block was never closed"),
])
def test_try_syntax_errors(src, message):
    assert message in str(tb_error(src))


def test_other_language_error_words_get_hints():
    assert "TB throws errors with 'throw'" in str(tb_error('raise "x"'))
    assert "try { ... } catch err" in str(tb_error("except ValueError"))
    assert "import fs" in str(tb_error("from os import path"))


def test_python_exception_in_library_becomes_tb_error():
    interp = tb.Interpreter(output=lambda s: None)

    def broken(line):
        raise ValueError("kaboom")

    interp.globals.vars["broken"] = tb.Builtin("broken", broken, (0, 0))
    tb.run('try { broken() } catch e { say(e.message) }', interpreter=interp, output=None)
    lines = []
    interp.output = lines.append
    tb.run('try { broken() } catch e { say(e.message) }', interpreter=interp)
    assert lines == ["Something went wrong inside broken(): kaboom"]


def test_plain_python_callables_can_be_called():
    interp = tb.Interpreter(output=lambda s: None)
    interp.globals.vars["twice"] = lambda x: x * 2
    tb.run("let r = twice(21)", interpreter=interp)
    assert interp.globals.vars["r"] == 42
    assert tb.to_text(lambda: 1).startswith("<fn ")


# -- break / continue ------------------------------------------------------------------

def test_break_and_continue_in_while_and_for():
    src = """
let i = 0
while true {
  i += 1
  if i == 2 { continue }
  if i > 4 { break }
  say("w", i)
}
for x in range(10) {
  if x % 2 == 1 { continue }
  if x > 5 { break }
  say("f", x)
}
"""
    assert run_tb(src) == ["w 1", "w 3", "w 4", "f 0", "f 2", "f 4"]


def test_break_only_leaves_inner_loop():
    src = "for a in [1, 2] {\n  for b in [1, 2, 3] {\n    if b == 2 { break }\n    say(a, b)\n  }\n}"
    assert run_tb(src) == ["1 1", "2 1"]


def test_break_inside_try_inside_loop():
    assert run_tb('for x in [1, 2, 3] {\n  try { if x == 2 { break } } catch e { }\n  say(x)\n}') == ["1"]


def test_break_outside_loop_is_an_error():
    err = tb_error("say(1)\nbreak")
    assert err.line == 2 and "'break' only works inside a loop (while or for)." in str(err)
    assert "'continue' only works inside a loop" in str(tb_error("if true { continue }"))
    # a function inside a loop is not inside the loop
    assert "only works inside a loop" in str(tb_error("for x in [1] {\n  let f = fn() { break }\n}"))


def test_contextual_words_still_work_as_names():
    src = 'let import = 1\nlet try = 2\nlet catch = 3\nlet throw = 4\nlet as = 5\nlet break = 6\nlet continue = 7\nsay(import + try + catch + throw + as + break + continue)'
    assert run_tb(src) == ["28"]


# -- compound assignment ----------------------------------------------------------------

def test_compound_assignment_on_names():
    src = 'let x = 10\nx += 5\nx -= 3\nx *= 2\nx /= 4\nsay(x)\nlet s = "a"\ns += "b"\ns += 1\nsay(s)\nlet xs = [1]\nxs += [2]\nsay(xs)'
    assert run_tb(src) == ["6", "ab1", "[1, 2]"]


def test_compound_assignment_on_slots_and_fields():
    src = 'let xs = [1, 2]\nxs[1] += 10\nlet m = {"n": 1, "inner": {"c": 0}}\nm["n"] += 1\nm.n *= 10\nm.inner.c -= 1\nsay(xs, m)'
    assert run_tb(src) == ['[1, 12] {"n": 20, "inner": {"c": -1}}']


def test_compound_assignment_evaluates_target_once():
    src = 'let calls = 0\nlet xs = [0, 0]\nfn i() { calls += 1\nreturn 1 }\nxs[i()] += 5\nsay(xs, calls)'
    assert run_tb(src) == ["[0, 5] 1"]


@pytest.mark.parametrize("src,message", [
    ("y += 1", "'y' doesn't exist yet"),
    ('let m = {}\nm.count += 1', 'There\'s no key "count" in this map'),
    ('let m = {}\nm["count"] += 1', 'There\'s no key "count" in this map'),
    ("let x = 1\nx /= 0", "You can't divide by zero."),
    ('let x = "a"\nx -= 1', "I can only subtract numbers"),
    ("1 += 2", "name or a list slot"),
    ("let x += 1", "'=' after 'let x'"),
])
def test_compound_assignment_errors(src, message):
    assert message in str(tb_error(src))


def test_compound_closure_counter():
    src = "fn counter() {\n  let n = 0\n  return fn() { n += 1\n return n }\n}\nlet c = counter()\nc()\nsay(c())"
    assert run_tb(src) == ["2"]


# -- in / not in -----------------------------------------------------------------------

def test_in_and_not_in():
    src = 'let m = {"a": 1, 2: "b"}\nsay(2 in [1, 2], 3 in [1, 2], [1] in [[1]], "ell" in "hello", "a" in m, 2 in m, "z" not in m, 5 not in [5])'
    assert run_tb(src) == ["true false true true true true true false"]


def test_in_precedence():
    assert run_tb('say(not 1 in [1], 1 in [1] and 2 in [2], 1 + 1 in [2], 1 in [1] == true)') == [
        "false true true true"
    ]


def test_in_errors():
    assert "'in' needs a list, text or a map on its right, not number." in str(tb_error("say(1 in 5)"))
    assert "the left side of 'in' must be text too, not number" in str(tb_error('say(1 in "123")'))
    assert run_tb('say([1] in {"a": 1})') == ["false"]


# -- new built-ins ----------------------------------------------------------------------

def test_type():
    src = 'import math\nsay(type(1), type(2.5), type("a"), type(true), type(nil), type([]), type({}), type(say), type(fn() { }), type(math), type("a".upper))'
    assert run_tb(src) == ["number number text bool nil list map function function module function"]


def test_keys_int_round():
    assert run_tb('say(keys({"b": 1, "a": 2}), keys({}))') == ['["b", "a"] []']
    assert run_tb('say(int(3.9), int(-3.9), int("12.7"), int(" 7 "), int(5))') == ["3 -3 12 7 5"]
    assert run_tb("say(round(2.5), round(3.5), round(-2.5), round(2.675, 2), round(1.005, 2), round(1234.5678, -2), round(7, 2))") == [
        "3 4 -3 2.68 1.01 1200 7"
    ]


def test_min_max_sum_sorted():
    assert run_tb('say(min(3, 1, 2), max([4, 9, 2]), min("b", "a"), max(5), sum([1, 2, 3.5]), sorted([3, 1, 2]), sorted(["b", "a"]))') == [
        '1 9 a 5 6.5 [1, 2, 3] ["a", "b"]'
    ]
    assert run_tb("let xs = [2, 1]\nlet ys = sorted(xs)\nsay(xs, ys)") == ["[2, 1] [1, 2]"]


@pytest.mark.parametrize("src,message", [
    ("keys([1])", "keys() needs a map, not list."),
    ("int([1])", "int() needs a number or text, not list."),
    ('int("abc")', 'I can\'t turn "abc" into a number.'),
    ("int(true)", "int() needs a number or text"),
    ("round(\"1\")", "round() needs a number, not text."),
    ("round(1, 0.5)", "round() needs a whole number of digits"),
    ("round(1, 99)", "from -15 to 15"),
    ("min([])", "min() needs at least one value, but the list is empty."),
    ('max(1, "a")', "max() needs all numbers or all text."),
    ("sum(5)", "sum() needs a list, not number."),
    ('sum([1, "a"])', "sum() can only add up numbers, not text."),
    ("sorted(nil)", "sorted() needs a list, not nil."),
    ('sorted([1, "a"])', "sorted() can only sort a list of all numbers or all text."),
    ('exit("x")', "exit() needs a whole number"),
    ("type()", "type() needs 1 input, but got 0."),
    ("min()", "min() needs at least 1 input, but got 0."),
])
def test_new_builtin_errors(src, message):
    assert message in str(tb_error(src))


def test_exit_codes():
    with pytest.raises(tb.TBExit) as info:
        run_tb('say("before")\nexit()')
    assert info.value.code == 0
    with pytest.raises(SystemExit) as info:
        run_tb("exit(2)")
    assert info.value.code == 2


def test_print_still_hints_say():
    assert "Did you mean 'say'?" in str(tb_error('print("hi")'))


def test_new_builtins_can_be_shadowed():
    assert run_tb("let type = 1\nlet min = 2\nsay(type + min)") == ["3"]


def test_builtins_live_outside_program_globals():
    interp = tb.run("let x = 1", output=lambda s: None)
    assert "x" in interp.globals.vars and "say" not in interp.globals.vars
    assert "say" in interp.builtins.vars


def test_error_map_and_str_for_module_errors():
    err = tb.TBError("bad", 3, {"code": 1})
    err.file = "/tmp/lib/helpers.tb"
    assert str(err) == "Oops! On line 3 of helpers.tb: bad"
    assert err.to_map() == {"message": "bad", "line": 3, "kind": "error", "code": 1, "file": "helpers.tb"}
    assert tb.TBError("x").to_map() == {"message": "x", "line": None, "kind": "error"}
