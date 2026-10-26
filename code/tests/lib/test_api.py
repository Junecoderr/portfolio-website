"""The native-module helper API (tblib/_api.py) other libraries build on."""

import sys

import pytest

import tb
from tblib import _api
from tblib._api import MANY, Module, check_type, fail, fail_os


def test_module_registration_and_docs():
    m = Module("demo", "A demo.")

    @m.fn("hello", 1, 2, doc="hello(name, end?) -> text\nSay hi.")
    def hello(line, name, end="!"):
        return f"Hi {name}{end}"

    @m.fn("many", 0, MANY, doc="many(...) -> number")
    def many(line, *args):
        return len(args)

    m.value("VERSION", "1.0", doc="The version.")
    assert m.fields["hello"].arity == (1, 2) and m.fields["hello"].name == "demo.hello"
    assert m.fields["many"].arity == (0, MANY)
    assert m.docs == {"hello": "hello(name, end?) -> text\nSay hi.", "many": "many(...) -> number", "VERSION": "The version."}
    interp = tb.Interpreter(output=lambda s: None)
    interp.globals.vars["demo"] = m
    tb.run('let a = demo.hello("T")\nlet b = demo.many(1, 2, 3)\nlet c = demo.VERSION', interpreter=interp)
    assert [interp.globals.vars[k] for k in "abc"] == ["Hi T!", 3, "1.0"]
    with pytest.raises(tb.TBError) as info:
        tb.run("demo.hello()", interpreter=interp)
    assert "demo.hello() needs 1 to 2 inputs, but got 0." in str(info.value)
    with pytest.raises(tb.TBError) as info:
        tb.run("demo.many(1, 2)\ndemo.hello(1, 2, 3)", interpreter=interp)
    assert "demo.hello() needs 1 to 2 inputs, but got 3." in str(info.value)


def test_fn_default_max_is_min():
    m = Module("d")
    m.fn("one", 1)(lambda line, x: x)
    assert m.fields["one"].arity == (1, 1)


def test_fail_and_fields():
    with pytest.raises(tb.TBError) as info:
        fail(3, "Not found", status=404)
    assert info.value.line == 3 and info.value.fields == {"status": 404}
    assert str(info.value) == "Oops! On line 3: Not found"


@pytest.mark.parametrize("err,message", [
    (FileNotFoundError(2, "x"), "I couldn't find 'p'. Is the name spelled right?"),
    (IsADirectoryError(21, "x"), "'p' is a folder, not a file."),
    (NotADirectoryError(20, "x"), "'p' is not a folder."),
    (PermissionError(13, "x"), "I'm not allowed to read 'p'."),
    (FileExistsError(17, "x"), "'p' already exists."),
    (OSError(5, "Input/output error"), "I couldn't read 'p': Input/output error."),
])
def test_fail_os(err, message):
    with pytest.raises(tb.TBError) as info:
        fail_os(1, err, "p", "read")
    assert info.value.message == message and info.value.fields == {"path": "p"}


@pytest.mark.parametrize("value,kind,ok", [
    ("a", "text", True), (1, "text", False), (1, "number", True), (True, "number", False),
    (2.0, "whole", True), (2.5, "whole", False), (True, "bool", True), ([], "list", True),
    ({}, "map", True), (None, "nil", True), (None, "text|nil", True), ("x", "list|text", True),
    (tb.Builtin("f", None, (0, 0)), "function", True), (len, "function", True), (Module("m"), "function", False),
    (object(), "any", True),
])
def test_check_type_kinds(value, kind, ok):
    if ok:
        check_type(1, value, kind, "lib.f", "x")
    else:
        with pytest.raises(tb.TBError):
            check_type(1, value, kind, "lib.f", "x")


def test_check_type_messages_and_conversion():
    assert check_type(1, 3.0, "whole", "f", "n") == 3 and isinstance(check_type(1, 3.0, "whole", "f", "n"), int)
    with pytest.raises(tb.TBError) as info:
        check_type(7, 5, "text", "fs.read", "path")
    assert str(info.value) == "Oops! On line 7: fs.read() needs path to be text, not a number."
    with pytest.raises(tb.TBError) as info:
        check_type(7, 2.5, "whole", "f", "n")
    assert "needs n to be a whole number, not 2.5." in str(info.value)
    with pytest.raises(tb.TBError) as info:
        check_type(7, None, "list|map", "f", "rows")
    assert "needs rows to be a list or a map, not nil." in str(info.value)
    with pytest.raises(ValueError):
        check_type(1, 1, "banana", "f", "x")


def test_api_object():
    lines, prompts = [], []
    interp = tb.Interpreter(output=lines.append, input_fn=lambda p: prompts.append(p) or "  typed  ",
                            base_dir="/some/where", args=["x"])
    api = interp.api
    assert api is interp.api and api.interpreter is interp
    api.output("hello")
    api.output([1, "a"])
    assert lines == ["hello", '[1, "a"]']
    assert api.input("? ") == "typed" and prompts == ["? "]
    assert api.base_dir == "/some/where" and api.args == ["x"]
    assert api.in_browser is False
    assert api.to_text({"a": nil_value()}) == '{"a": nil}'
    assert api.type_of(True) == "bool"


def nil_value():
    return None


def test_api_input_end_of_input():
    def eof(prompt):
        raise EOFError
    assert tb.Interpreter(input_fn=eof).api.input() == ""


def test_api_base_dir_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert tb.Interpreter().api.base_dir == str(tmp_path)


def test_api_call_and_wrap():
    interp = tb.run("fn add(a, b) { return a + b }\nfn bad() { return nope }", output=lambda s: None)
    api = interp.api
    add = interp.globals.vars["add"]
    assert api.call(add, [2, 3]) == 5
    assert api.call(interp.builtins.vars["len"], [[1, 2]], 1) == 2
    wrapped = api.wrap(add, 9)
    assert wrapped(4, 5) == 9 and wrapped.__name__ == "add"
    with pytest.raises(tb.TBError) as info:
        api.wrap(interp.globals.vars["bad"])()
    assert "I don't know what 'nope' is" in str(info.value)
    with pytest.raises(tb.TBError) as info:
        api.call(add, [1], 4)
    assert info.value.line == 4 and "add() needs 2 inputs, but got 1." in str(info.value)


def test_need_desktop(monkeypatch):
    api = tb.Interpreter().api
    api.need_desktop(1, "The gui library")  # fine on a computer
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert api.in_browser
    with pytest.raises(tb.TBError) as info:
        api.need_desktop(2, "The gui library")
    assert str(info.value) == ("Oops! On line 2: The gui library needs the TB app on your computer; "
                               "it can't work inside the browser playground.")


def test_reexports():
    assert _api.Module is tb.Module and _api.TBError is tb.TBError and _api.MANY == tb.MANY


def test_broken_library_load_is_friendly(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("tblib.broken_demo")

    def load(api):
        raise RuntimeError("no tkinter")

    fake.load = load
    monkeypatch.setitem(sys.modules, "tblib.broken_demo", fake)
    monkeypatch.setattr(tb, "_native_spec", lambda name: object() if name == "broken_demo" else None)
    with pytest.raises(tb.TBError) as info:
        tb.run("import broken_demo", output=lambda s: None)
    assert "The broken_demo library couldn't start: no tkinter" in str(info.value)
    fake.load = lambda api: "not a module"
    with pytest.raises(tb.TBError) as info:
        tb.run("import broken_demo", output=lambda s: None)
    assert "load() didn't give a Module" in str(info.value)


def test_foreign_tblib_package_is_replaced(monkeypatch):
    import types
    impostor = types.ModuleType("tblib")
    monkeypatch.setitem(sys.modules, "tblib", impostor)
    pkg = tb._tblib_package()
    assert getattr(pkg, "TB_STDLIB", False) and sys.modules["tblib"] is pkg
