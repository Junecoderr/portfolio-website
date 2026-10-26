"""test: checks for *_test.tb files (run them all with `tb test`)."""

from __future__ import annotations

import math

from tblib._api import MANY, Module, check_type, fail

import tb as _tb


class SkipTest(_tb.TBError):
    """test.skip(reason): stops the current case (or the rest of the file)."""


def state_of(interpreter) -> dict:
    """The pass/fail counts for this program (tb test reads them)."""
    state = getattr(interpreter, "test_state", None)
    if not isinstance(state, dict):
        state = {"passed": 0, "failed": 0, "skipped": 0}
        interpreter.test_state = state
    state.setdefault("skipped", 0)
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

    def article(name):
        return ("an " if name[:1] in "AEIOU" else "a ") + name

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

    @m.fn("raises", 1, 3, doc="raises(fn, kind?, part?) -> map or nil\nCheck that calling fn() stops with an error. kind may be an error class (test.raises(fn, ParseError)) and/or text the message must contain (test.raises(fn, \"too big\")). Gives back the error map (message, line, kind, ...).")
    def raises(line, fn, kind=None, part=None):
        check_type(line, fn, "function", "test.raises", "fn")
        if isinstance(kind, str) and part is None:
            kind, part = None, kind
        if kind is not None and not (isinstance(kind, _tb.TBClass) and kind.is_error):
            fail(line, "test.raises() needs an error class (like ValueError) or text as its second input.",
                 cls="TypeError")
        if part is not None:
            check_type(line, part, "text", "test.raises", "part")
        try:
            api.call(fn, [], line)
        except SkipTest:
            raise
        except _tb.TBError as err:
            if kind is not None and not _tb.error_class(err.cls).is_subclass_of(kind):
                failed(line, f"expected {article(kind.name)}, but got {article(_tb.error_class(err.cls).name)}: {err.message}", None)
                return err.to_map()
            if part is not None and part not in err.message:
                failed(line, f'expected an error about "{part}", but got: {err.message}', None)
                return err.to_map()
            passed()
            return err.to_map()
        what = article(kind.name) if kind is not None else "an error"
        failed(line, f"expected {what}, but there wasn't one", None)
        return None

    @m.fn("approx", 2, 4, doc="approx(actual, expected, tolerance?, message?) -> bool\nCheck that two numbers (or lists of numbers) are equal give or take tolerance. Without a tolerance: within 0.000001, scaled up for big numbers (a millionth of their size).")
    def approx(line, actual, expected, tolerance=None, msg=None):
        relative = tolerance is None
        if relative:
            tolerance = 1e-6
        if not _tb.is_number(tolerance) or tolerance < 0:
            fail(line, "test.approx() needs a tolerance that is a number 0 or more.", cls="ValueError")

        def close(a, b):
            if isinstance(a, list) and isinstance(b, list):
                return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
            if _tb.is_number(a) and _tb.is_number(b):
                if relative:
                    return math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)
                return abs(a - b) <= tolerance
            return False

        if close(actual, expected):
            return passed()
        return failed(line, f"expected about {show(expected)} (give or take {show(_tb.tidy_number(tolerance) if isinstance(tolerance, float) else tolerance)}), but got {show(actual)}", msg)

    @m.fn("skip", 0, 1, doc="skip(reason?)\nSkip the rest of this test case (or, outside a case, the rest of the file). tb test counts it as skipped, not failed.")
    def skip(line, reason=None):
        raise SkipTest(_tb.to_text(reason) if reason is not None else "skipped", line)

    # -- mocks --------------------------------------------------------------

    mocks: list = []   # (fields dict, name, had_it, old value), newest last

    def fields_of(line, target):
        if isinstance(target, _tb.Module):
            return target.fields
        if isinstance(target, dict):
            return target
        if isinstance(target, _tb.Instance):
            return target.fields
        if isinstance(target, _tb.TBClass):
            return target.statics
        fail(line, f"test.mock() can replace things in a library, map, object or class, not {_tb.type_name(target)}.",
             cls="TypeError")

    def restore_to(depth):
        while len(mocks) > depth:
            fields, name, had, old = mocks.pop()
            if had:
                fields[name] = old
            else:
                fields.pop(name, None)

    @m.fn("mock", 3, doc="mock(target, name, value)\nReplace target.name (a library function, a map key, an object field) with value, usually a fn. It goes back to the real one when the current test.case ends, or when you call test.restore(). Example: test.mock(http, \"get\", url => {\"status\": 200, \"body\": \"hi\"})")
    def mock(line, target, name, value):
        check_type(line, name, "text", "test.mock", "name")
        fields = fields_of(line, target)
        if isinstance(target, _tb.Module) and name not in fields:
            fail(line, f"test.mock(): {target.label} has nothing called '{name}' to replace.", cls="NotFoundError")
        mocks.append((fields, name, name in fields, fields.get(name)))
        fields[name] = value
        return None

    @m.fn("restore", 0, doc="restore()\nPut back everything test.mock() replaced.")
    def restore(line):
        restore_to(0)
        return None

    # -- fixtures -----------------------------------------------------------

    fixtures: dict = {}   # name -> (setup fn, teardown fn or None)

    @m.fn("fixture", 2, 3, doc="fixture(name, setup, teardown?)\nRegister a fixture. A test.case function with an input called name gets setup()'s value; teardown(value) runs after the case, even if it failed. Example: test.fixture(\"db\", () => sqlite.open(\":memory:\"), db => db.close()) then test.case(\"insert\", db => { ... })")
    def fixture(line, name, setup, teardown=None):
        check_type(line, name, "text", "test.fixture", "name")
        check_type(line, setup, "function", "test.fixture", "setup")
        if teardown is not None:
            check_type(line, teardown, "function", "test.fixture", "teardown")
        fixtures[name] = (setup, teardown)
        return None

    def param_names(fn):
        params = getattr(fn, "params", None) or []
        return [getattr(p, "name", None) for p in params if getattr(p, "kind", "pos") == "pos"]

    def run_case(line, name, fn, args=None):
        """Run fn as one named case: fixtures, mocks restored, errors counted."""
        before = state["failed"]
        depth = len(mocks)
        used = []
        skipped = None
        try:
            call_args = list(args or [])
            if args is None:
                for pname in param_names(fn):
                    if pname in fixtures:
                        setup, teardown = fixtures[pname]
                        value = api.call(setup, [], line)
                        used.append((teardown, value))
                        call_args.append(value)
                    else:
                        break
            api.call(fn, call_args, line)
        except SkipTest as err:
            skipped = err.message
        except _tb.TBError as err:
            failed(err.line if err.line is not None else line,
                   f"'{_tb.to_text(name)}' stopped with an error: {err.message}", None)
        finally:
            restore_to(depth)
            for teardown, value in reversed(used):
                if teardown is not None:
                    try:
                        api.call(teardown, [value], line)
                    except _tb.TBError as err:
                        failed(line, f"the teardown for '{_tb.to_text(name)}' stopped with an error: {err.message}", None)
        if skipped is not None:
            state["skipped"] += 1
            api.output(f"  skip {_tb.to_text(name)} ({skipped})")
            return None
        api.output(f"  {'ok  ' if state['failed'] == before else 'FAIL'} {_tb.to_text(name)}")
        return None

    @m.fn("each", 2, 3, doc="each(cases, fn, name?)\nRun fn once per case, each as its own named test. A case that is a list is spread into fn's inputs: test.each([[1, 2, 3], [2, 2, 4]], (a, b, sum) => test.equal(a + b, sum)). name is a label prefix (default \"case\").")
    def each(line, cases, fn, label="case"):
        check_type(line, cases, "list", "test.each", "cases")
        check_type(line, fn, "function", "test.each", "fn")
        for i, case_value in enumerate(cases):
            args = case_value if isinstance(case_value, list) else [case_value]
            run_case(line, f"{_tb.to_text(label)} {i + 1}: {_tb.to_text(case_value, nested=True)}", fn, args)
        return None

    @m.fn("case", 2, doc="case(name, fn)\nRun one named test. If fn stops with an error, that counts as a failure and the other cases still run. Inputs of fn named like a test.fixture get the fixture's value; mocks made inside are undone at the end.")
    def case(line, name, fn):
        check_type(line, fn, "function", "test.case", "fn")
        return run_case(line, name, fn)

    @m.fn("results", 0, doc="results() -> map\nHow many checks passed and failed so far, and how many cases were skipped: {\"passed\", \"failed\", \"skipped\"}.")
    def results(line):
        return {"passed": state["passed"], "failed": state["failed"], "skipped": state["skipped"]}

    return m
