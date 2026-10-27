# Pictures for the re library (read with ast.literal_eval; never run).
# Matches are lit up in the text with the pattern's colour; sliders run each value for real.
SPEC = {
    "_default": {"roles": {"pattern": "the regular expression to look for", "text": "the text to search"}},
    "test": {
        "args": {"pattern": r'"\\d+ failed"', "text": '"login: 3 failed attempts"'},
        "vary": {"param": "text", "values": ['"login: 3 failed attempts"', '"login: ok"', '"12 failed, 2 ok"'],
                 "labels": ["3 failed", "ok", "12 failed"]},
        "roles": {"pattern": "the pattern: digits, then \" failed\"", "text": "the text to look in"},
    },
    "match": {
        "args": {"pattern": r'"(\\w+)@([\\w.]+)"', "text": '"mail ada@example.com now"'},
        "mode": "tree",
        "vary": {"param": "text", "values": ['"mail ada@example.com now"', '"from bo@test.org"', '"no address here"'],
                 "labels": ["ada@...", "bo@...", "no match"]},
        "roles": {"pattern": "the pattern; each ( ) is a group: m[1], m[2]", "text": "the text to search"},
    },
    "find_all": {
        "args": {"pattern": r'"\\d+\\.\\d+\\.\\d+\\.\\d+"', "text": '"from 10.0.0.7 to 203.0.113.9 via 10.0.0.1"'},
        "vary": {"param": "pattern", "values": [r'"\\d+\\.\\d+\\.\\d+\\.\\d+"', r'"\\d+"', r'"10\\.[\\d.]+"'],
                 "labels": ["IPv4", "numbers", "10.x"]},
        "roles": {"pattern": "what one match looks like", "text": "the text to search"},
    },
    "replace": {
        "args": {"pattern": r'"\\d"', "text": '"card 4111 2222"', "with": '"#"'},
        "mark_out": ["with"],
        "vary": {"param": "with", "values": ['"#"', '"*"', '"[n]"'], "labels": ["#", "*", "[n]"]},
        "roles": {"pattern": "what to swap out", "text": "the text to change", "with": "what goes in instead (\\1 is group 1)"},
    },
    "split": {
        "args": {"pattern": r'",\\s*"', "text": '"a, b,c,   d"'},
        "find_in": {"text": "pattern"},
        "vary": {"param": "pattern", "values": [r'",\\s*"', r'","', r'"\\s+"'], "labels": [",\\s*", ",", "\\s+"]},
        "roles": {"pattern": "where to cut", "text": "the text to cut up"},
    },
    "escape": {
        "args": {"text": '"1+1=2 (really?)"'}, "mode": "cells",
        "roles": {"text": "text to search for exactly; + ( ) ? get a backslash"},
    },
}
