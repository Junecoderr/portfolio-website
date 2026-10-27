# Pictures for the random library (read with ast.literal_eval; never run).
# Built with a fixed seed, so the site shows the same picks every time; your program's differ.
SPEC = {
    "_default": {"note": "Real result from a seeded run while building the site; your program picks differently each time (unless you call random.seed)."},
    "int": {"args": {"low": "1", "high": "6"}, "vary": {"param": "high", "values": ["6", "20", "100"]},
            "roles": {"low": "the smallest it can be", "high": "the biggest it can be (included)"}},
    "number": {"nodes": [{"box": "from 0 up to 1", "icon": "dice", "lines": ["0 ≤ n < 1"]}, {"arrow": True}, {"result": True}]},
    "choice": {"args": {"list": '["rock", "paper", "scissors"]'}, "roles": {"list": "the items to pick from"}},
    "shuffle": {"args": {"list": "[1, 2, 3, 4, 5]"},
                "roles": {"list": "the items; a new list comes back in a random order"}},
    "sample": {"args": {"list": '["a", "b", "c", "d", "e"]', "n": "2"}, "vary": {"param": "n", "values": ["1", "2", "3", "5"], "default": "2"},
               "roles": {"list": "the items to pick from", "n": "how many different ones to pick"}},
    "seed": {"setup": "random.seed(42)", "call": "[random.int(1, 6), random.int(1, 6), random.int(1, 6)]", "args": {"n": "42"},
             "nodes": [{"param": "n"}, {"arrow": "random.seed(42), then 3 × random.int(1, 6)"}, {"result": True, "label": "the same three every run"}],
             "roles": {"n": "any number: the same seed gives the same picks"},
             "note": "Real result: seeding with 42 always gives these picks."},
}
