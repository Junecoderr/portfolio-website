"""Named inputs for library (native) functions, TB 3.1: the names come from
each function's doc signature, on both engines, in `tb check` and in the
language server's signature help."""

from __future__ import annotations

import pytest

import tb
from tbcore.interp import Interpreter as TreeInterpreter
from tbcore.runtime import Builtin, Module, doc_params
from tbcore.types import check_source
from tbcore.vm import VMInterpreter

ENGINES = {"tree": TreeInterpreter, "vm": VMInterpreter}


def run(engine, source):
    lines = []
    tb.run(source, interpreter=ENGINES[engine](output=lines.append, input_fn=lambda p: ""))
    return lines


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    return request.param


def test_named_inputs_reach_library_functions(engine):
    src = """import parallel
import text
import math
import crypto
say(parallel.map(fn(x) { return x * 2 }, [1, 2, 3], workers = 8))
say(parallel.map(fn(x) { return x + 1 }, items = [1, 2]))
say(text.pad_left("7", 3, fill = "0"))
say(text.pad_left(width = 4, value = "ab"))
say(math.round(3.14159, digits = 2))
say(len(crypto.scrypt("pw", "salt", length = 16)))
let pad = text.pad_left.bind("x")
say(pad(width = 3, fill = "."))
"""
    assert run(engine, src) == ["[2, 4, 6]", "[2, 3]", "007", "  ab", "3.14", "32", "..x"]


def test_positional_calls_are_unchanged(engine):
    assert run(engine, 'import text\nsay(text.pad_left("7", 3, "0"))\nsay(text.pad_left("7", 3))\n') == ["007", "  7"]


def test_builtins_and_methods_take_names_from_their_docs_too(engine):
    src = 'say(round(3.14159, digits = 1))\nsay("a-b".split(sep = "-"))\n'
    assert run(engine, src) == ["3.1", '["a", "b"]']
    assert check_source(src) == []


@pytest.mark.parametrize("call,message", [
    ('text.pad_left("7", 3, fil = "0")',
     "text.pad_left() has no input called 'fil'. Did you mean 'fill'? Its inputs are: value, width, fill."),
    ('text.pad_left("7", value = "x")', "text.pad_left() got 'value' twice: once in order and once by name."),
    ("text.pad_left(width = 3)", "text.pad_left() is missing its input 'value'."),
    ('text.pad_left(value = "7")', "text.pad_left() is missing its input 'width'."),
    ("math.min(1, a = 3)", "math.min() doesn't take named inputs like a = ...; give its inputs in order."),
    ('text.pad_left("7", 3, "0", fill = "1")', "text.pad_left() got 'fill' twice: once in order and once by name."),
])
def test_friendly_errors(engine, call, message):
    with pytest.raises(tb.TBError) as info:
        run(engine, f"import text\nimport math\n{call}\n")
    assert info.value.line == 3
    assert str(info.value) == f"Oops! On line 3: {message}"


def test_skipping_an_input_without_a_python_default():
    m = Module("demo")

    @m.fn("f", 1, 3, doc="f(a, b?, c?) -> list")
    def f(line, *args):
        return list(args)

    for engine in ENGINES:
        interp = ENGINES[engine](output=lambda s: None)
        interp.globals.vars["demo"] = m
        with pytest.raises(tb.TBError) as info:
            tb.run("demo.f(1, c = 3)", interpreter=interp)
        assert "demo.f() can't leave out 'b' when 'c' is given by name; give b too." in str(info.value)
        tb.run("let r = demo.f(1, b = 2)", interpreter=interp)
        assert interp.globals.vars["r"] == [1, 2]


def test_doc_params():
    assert doc_params("map(fn, items, workers = 4) -> list\nmore") == (["fn", "items", "workers"], 2, False)
    assert doc_params("map(fn, items, workers?) -> list") == (["fn", "items", "workers"], 2, False)
    assert doc_params("join(part, ...) -> text") == (["part"], 1, True)
    assert doc_params("run(fn, ...args) -> Task") == (["fn"], 1, True)
    assert doc_params("now() -> map") == ([], 0, False)
    assert doc_params("A sentence (not a signature).") is None
    assert doc_params("f(a?, b) -> x") is None   # required after optional
    assert doc_params("f(a, a) -> x") is None
    assert doc_params("") is None


def _library_functions():
    interp = tb.Interpreter(output=lambda s: None, input_fn=lambda p: "")
    for lib in tb.list_libraries():
        module = interp.load_library(lib)
        for name, value in module.fields.items():
            if isinstance(value, Builtin):
                yield lib, name, value


def test_every_library_signature_matches_its_inputs():
    """Each native function's doc signature names it, parses, and declares as
    many inputs as it really takes, so named inputs map onto the right places."""
    seen = 0
    for lib, name, fn in _library_functions():
        seen += 1
        parsed = doc_params(fn.doc)
        assert parsed is not None, (lib, name, fn.doc.partition("\n")[0])
        assert fn.doc.startswith(name + "("), (lib, name)
        names, required, rest = parsed
        lo, hi = fn.arity
        if rest:
            assert hi == tb.MANY, (lib, name)
        else:
            assert (required, len(names)) == (lo, hi), (lib, name, fn.doc.partition("\n")[0], fn.arity)
            assert fn.params == tuple(names)
    assert seen > 300


def test_check_understands_library_named_inputs():
    clean = 'import parallel\nimport text\nsay(parallel.map(fn(x) { return x }, [1], workers = 2))\nsay(text.pad_left("a", width = 3))\n'
    assert [d.message for d in check_source(clean)] == []
    src = ('import text\nsay(text.pad_left("a", 3, fil = "0"))\nsay(text.pad_left("a", value = "b"))\n'
           'say(text.pad_left(width = 3))\n')
    assert [(d.line, d.code, d.message) for d in check_source(src)] == [
        (2, "unknown-keyword",
         "text.pad_left() has no input called 'fil'. Did you mean 'fill'? Its inputs are: value, width, fill."),
        (3, "wrong-arity", "text.pad_left() got 'value' twice: once in order and once by name."),
        (4, "wrong-arity", "text.pad_left() is missing its input 'value'."),
    ]
    assert [d.code for d in check_source("import math\nsay(math.min(1, a = 2))\n")] == ["unknown-keyword"]
