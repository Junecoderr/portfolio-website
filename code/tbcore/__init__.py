"""tbcore: the implementation of the TB language.

`tb.py` (the entry point and public API) imports everything from here, so a
TB program never needs to import tbcore directly. Packaging (PyInstaller,
Pyodide) must ship this whole folder next to `tb.py`, exactly like `tblib/`.

Files (all pure Python 3.10+, standard library only):

    __init__.py   this list
    errors.py     TBError, TBExit, size guards and limits (MAX_DEPTH, ...)
    lexer.py      Token and tokenize(): keywords, numbers, text, bytes,
                  f-text with format specs, comments kept on tokens
    ast.py        the syntax tree node classes (statements, expressions,
                  patterns, type annotations)
    parser.py     Parser and parse(): source -> Program
    runtime.py    runtime values (Env, Function, Module, classes, enums,
                  ranges, generators, bytes), to_text, equality, the methods
                  of built-in values, format specs, the Error class family
    tasks.py      Task, spawn(), run_pool(): TB code on other threads (async fn, parallel)
    interp.py     Interpreter: the tree-walking engine (TB_ENGINE=tree):
                  runs a Program, calls, modules and packages, the trace
                  hook for the debugger
    compiler.py   the bytecode compiler: syntax tree -> Code objects
    vm.py         VMInterpreter: the default engine, a stack VM that runs
                  the compiled code (same API and messages as interp.py)
    types.py      the gradual type checker used by `tb check` and the LSP
"""

FILES = [
    "__init__.py",
    "errors.py",
    "lexer.py",
    "ast.py",
    "parser.py",
    "runtime.py",
    "interp.py",
    "tasks.py",
    "compiler.py",
    "vm.py",
    "types.py",
]
