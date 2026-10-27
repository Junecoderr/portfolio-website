# Pictures for the text library (read with ast.literal_eval; never run).
# Every example runs through TB while the site builds; sliders run each value.
SPEC = {
    "pad_left": {
        "args": {"value": "7", "width": "3", "fill": '"0"'},
        "pad": "fill", "pad_of": "value", "mode": "cells", "bracket": "width",
        "vary": {"param": "width", "values": ["1", "3", "5", "8"], "default": "3"},
        "roles": {"value": "what gets padded (numbers become text)", "width": "how many letters long the result is",
                  "fill": "what is added on the left (a space if left out)"},
    },
    "pad_right": {
        "args": {"value": '"id"', "width": "6", "fill": '"."'},
        "pad": "fill", "pad_of": "value", "mode": "cells", "bracket": "width",
        "vary": {"param": "width", "values": ["2", "4", "6", "9"], "default": "6"},
        "roles": {"value": "what gets padded", "width": "how many letters long the result is",
                  "fill": "what is added on the right (a space if left out)"},
    },
    "center": {
        "args": {"value": '"TB"', "width": "8", "fill": '"*"'},
        "pad": "fill", "pad_of": "value", "mode": "cells", "bracket": "width",
        "vary": {"param": "width", "values": ["4", "6", "8", "11"], "default": "8"},
        "roles": {"value": "the text in the middle", "width": "how many letters long the result is",
                  "fill": "what goes on both sides (a space if left out)"},
    },
    "wrap": {
        "args": {"text": '"Failed password for root from 203.0.113.42 port 50611"', "width": "20"},
        "mode": "lines",
        "vary": {"param": "width", "values": ["12", "20", "32"], "default": "20"},
        "roles": {"text": "the long text", "width": "the most letters on one line"},
    },
    "is_digit": {
        "args": {"text": '"2026"'},
        "vary": {"param": "text", "values": ['"2026"', '"20x6"', '""', '"-3"'], "labels": ["2026", "20x6", "(empty)", "-3"]},
        "roles": {"text": "the text to check"},
    },
    "format_number": {
        "args": {"n": "1234567.891", "decimals": "2"},
        "vary": {"param": "decimals", "values": ["0", "1", "2", "4"], "default": "2"},
        "roles": {"n": "the number", "decimals": "digits after the dot (none if left out)"},
    },
    "random_id": {
        "args": {"length": "8"}, "mode": "cells", "bracket": "length",
        "vary": {"param": "length", "values": ["4", "8", "12", "16"], "default": "8"},
        "roles": {"length": "how many letters and digits"},
        "note": "Real result from a run while building the site; yours will be different every time.",
    },
    "reverse": {"args": {"text": '"stressed"'}, "mode": "cells", "roles": {"text": "the text to turn around"}},
    "title": {"args": {"text": '"failed login from new device"'}, "roles": {"text": "the text to capitalise"}},
    "count": {
        "args": {"text": '"GET /a GET /b POST /c GET /d"', "part": '"GET"'},
        "find_in": {"text": "part"},
        "vary": {"param": "part", "values": ['"GET"', '"POST"', '"/"', '"PUT"'], "labels": ["GET", "POST", "/", "PUT"]},
        "roles": {"text": "the text to search", "part": "what to count"},
    },
}
