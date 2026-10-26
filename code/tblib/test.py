"""test: checks for *_test.tb files (run them all with `tb test`)."""

from __future__ import annotations

from tblib._api import Module, check_type

import tb as _tb


def state_of(interpreter) -> dict:
    """The pass/fail counts for this program (tb test reads them)."""
    state = getattr(interpreter, "test_state", None)
    if not isinstance(state, dict):
        state = {"passed": 0, "failed": 0}
        interpreter.test_state = state
    return state


def load(api):
    m = Module(
        "test",
        "Checks for your own tests. Put them in files ending in _test.tb and run `tb test`.",
    )
    state = state_of(api.interpreter)

    def passed():
        state["passed"] += 1
        return True

    def failed(line, what, msg):
        state["failed"] += 1
        note = f" ({_tb.to_text(msg)})" if msg is not None else ""
        api.output(f"  FAIL on line {line}: {what}{note}")
        return False

    def show(value):
        return _tb.to_text(value, nested=True)

    @m.fn("equal", 2, 3, doc="equal(actual, expected, message?) -> bool\nCheck that two values are the same (lists and maps are compared item by item).")
    def equal(line, actual, expected, msg=None):
        if _tb.tb_equal(actual, expected):
            return passed()
        return failed(line, f"expected {show(expected)}, but got {show(actual)}", msg)

    @m.fn("not_equal", 2, 3, doc="not_equal(a, b, message?) -> bool\nCheck that two values are different.")
    def not_equal(line, a, b, msg=None):
        if not _tb.tb_equal(a, b):
            return passed()
        return failed(line, f"expected something other than {show(b)}", msg)

    @m.fn("true", 1, 2, doc="true(value, message?) -> bool\nCheck that a value is true (anything but false and nil).")
    def true(line, value, msg=None):
        if _tb.truthy(value):
            return passed()
        return failed(line, f"expected something true, but got {show(value)}", msg)

    @m.fn("false", 1, 2, doc="false(value, message?) -> bool\nCheck that a value is false or nil.")
    def false(line, value, msg=None):
        if not _tb.truthy(value):
            return passed()
        return failed(line, f"expected false or nil, but got {show(value)}", msg)

    @m.fn("raises", 1, 2, doc="raises(fn, part?) -> map or nil\nCheck that calling fn() stops with an error (whose message contains part, if given). Gives back the error map.")
    def raises(line, fn, part=None):
        check_type(line, fn, "function", "test.raises", "fn")
        if part is not None:
            check_type(line, part, "text", "test.raises", "part")
        try:
            api.call(fn, [], line)
        except _tb.TBError as err:
            if part is not None and part not in err.message:
                failed(line, f'expected an error about "{part}", but got: {err.message}', None)
                return err.to_map()
            passed()
            return err.to_map()
        failed(line, "expected an error, but there wasn't one", None)
        return None

    @m.fn("case", 2, doc="case(name, fn)\nRun one named test. If fn stops with an error, that counts as a failure and the other cases still run.")
    def case(line, name, fn):
        check_type(line, fn, "function", "test.case", "fn")
        before = state["failed"]
        try:
            api.call(fn, [], line)
        except _tb.TBError as err:
            failed(err.line if err.line is not None else line,
                   f"'{_tb.to_text(name)}' stopped with an error: {err.message}", None)
        api.output(f"  {'ok  ' if state['failed'] == before else 'FAIL'} {_tb.to_text(name)}")
        return None

    @m.fn("results", 0, doc="results() -> map\nHow many checks passed and failed so far: {\"passed\", \"failed\"}.")
    def results(line):
        return {"passed": state["passed"], "failed": state["failed"]}

    return m
