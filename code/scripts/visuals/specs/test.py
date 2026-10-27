# Pictures for the test library (read with ast.literal_eval; never run).
# The checks run for real: change the slider and see a check pass or fail.
SPEC = {
    "_default": {"roles": {"message": "your note, shown when the check fails"}},
    "equal": {
        "args": {"actual": "[1, 2] + [3]", "expected": "[1, 2, 3]"},
        "vary": {"param": "expected", "values": ["[1, 2, 3]", "[1, 2]", "[3, 2, 1]"]},
        "roles": {"actual": "what your code gave", "expected": "what it should give"},
        "note": "Real result: each check ran with TB while the site was built (a failing one also prints FAIL with both values).",
    },
    "not_equal": {
        "args": {"a": '"admin"', "b": '"guest"'},
        "vary": {"param": "b", "values": ['"guest"', '"admin"'], "labels": ["guest", "admin"]},
        "roles": {"a": "one value", "b": "a value that must be different"},
    },
    "true": {
        "args": {"value": '"10.0.0.7".starts_with("10.")'},
        "vary": {"param": "value", "values": ['"10.0.0.7".starts_with("10.")', "nil", "0", '""'],
                 "labels": ["starts_with → true", "nil", "0", "\"\""]},
        "roles": {"value": "must be true (anything but false and nil passes)"},
    },
    "false": {
        "args": {"value": '"8.8.8.8".starts_with("10.")'},
        "vary": {"param": "value", "values": ['"8.8.8.8".starts_with("10.")', "nil", "true"], "labels": ["starts_with → false", "nil", "true"]},
        "roles": {"value": "must be false or nil"},
    },
    "raises": {
        "args": {"fn": '() => num("x")', "kind": '"number"'},
        "vary": {"param": "kind", "values": ['"number"', '"too big"'], "labels": ["\"number\" (in the message)", "\"too big\" (not in it)"]},
        "roles": {"fn": "the code that should stop with an error", "kind": "an error class and/or text the message must contain",
                  "part": "more text the message must contain"},
    },
    "approx": {
        "args": {"actual": "0.1 + 0.2", "expected": "0.3", "tolerance": "0.000001"},
        "vary": {"param": "tolerance", "values": ["0.000001", "0.01", "0"], "labels": ["0.000001", "0.01", "0 (exact)"]},
        "roles": {"actual": "the number your code gave", "expected": "the number it should be", "tolerance": "how far apart still passes"},
    },
    "skip": {
        "run": False, "args": {"reason": '"needs the network"'},
        "nodes": [{"param": "reason"}, {"arrow": "test.skip"},
                  {"box": "the rest of this case", "icon": "cross", "lines": ["is not run; tb test counts", "it as skipped, not failed"]}],
        "roles": {"reason": "why it is skipped (shown in the summary)"},
    },
    "mock": {
        "run": False, "args": {"target": "http", "name": '"get"', "value": 'url => {"status": 200, "text": "hi"}'},
        "nodes": [
            {"row": [{"param": "target", "lines": ["http (the library)"]}, {"param": "name", "lines": ["\"get\""]}]},
            {"param": "value", "lines": ["url => {\"status\": 200, …}"]},
            {"arrow": "test.mock"},
            {"box": "inside this test case", "icon": "test", "lines": ["http.get(…) now gives", "{\"status\": 200, \"text\": \"hi\"}", "no network needed"]},
        ],
        "gives": "nothing; the real one comes back when the case ends",
        "roles": {"target": "a library, map or object", "name": "which function or key to replace", "value": "what to use instead (usually a fn)"},
    },
    "restore": {
        "run": False,
        "nodes": [{"box": "http.get", "icon": "test", "lines": ["→ the mock"]}, {"arrow": "test.restore()"},
                  {"box": "http.get", "icon": "globe", "lines": ["→ the real one again"]}],
        "gives": "nothing",
    },
    "fixture": {
        "run": False, "args": {"name": '"db"', "setup": '() => sqlite.open(":memory:")', "teardown": "db => db.close()"},
        "nodes": [
            {"param": "name", "lines": ["\"db\""]},
            {"row": [{"param": "setup", "lines": ["() => sqlite.open(…)"]}, {"param": "teardown", "lines": ["db => db.close()"]}]},
            {"arrow": "test.case(\"insert\", db => { … })"},
            {"box": "for each case with an input called db", "icon": "db", "lines": ["setup() → db → the case → teardown(db)"]},
        ],
        "gives": "nothing; it registers the fixture",
        "roles": {"name": "the input name that asks for it", "setup": "makes the value", "teardown": "cleans up after each case"},
    },
    "each": {
        "run": False, "args": {"cases": "[[1, 2, 3], [2, 2, 4]]", "fn": "(a, b, sum) => test.equal(a + b, sum)", "name": '"add"'},
        "nodes": [
            {"param": "cases"},
            {"param": "fn"},
            {"arrow": "one named test per case"},
            {"row": [{"box": "add 1", "p": "name", "icon": "check", "lines": ["fn(1, 2, 3)"]}, {"box": "add 2", "p": "name", "icon": "check", "lines": ["fn(2, 2, 4)"]}]},
        ],
        "gives": "nothing; each case passes or fails on its own",
        "roles": {"cases": "a list of cases; a list case is spread into fn's inputs", "fn": "the test for one case",
                  "name": "label prefix for each case (\"case\" if left out)"},
    },
    "case": {
        "run": False, "args": {"name": '"parses auth lines"', "fn": "() => test.equal(r.user, \"root\")"},
        "nodes": [{"param": "name"}, {"param": "fn"}, {"arrow": "test.case"},
                  {"box": "tb test", "icon": "test", "lines": ["✓ parses auth lines", "(an error counts as a failure;", "other cases still run)"]}],
        "gives": "nothing",
        "roles": {"name": "what the case is called in the report", "fn": "the checks to run"},
    },
    "results": {
        "setup": "test.equal(1 + 1, 2)\ntest.true(3 > 2)\ntest.equal(\"a\", \"b\")",
        "nodes": [{"box": "checks so far", "icon": "test", "lines": ["test.equal(1 + 1, 2)  ✓", "test.true(3 > 2)  ✓", "test.equal(\"a\", \"b\")  ✗"]},
                  {"arrow": True}, {"result": True}],
    },
}
