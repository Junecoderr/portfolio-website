# Pictures for the encode library (read with ast.literal_eval; never run).
# Every example runs through TB while the site builds.
SPEC = {
    "base64": {
        "args": {"text": '"whoami"', "encoding": '"utf-8"'},
        "vary": {"param": "encoding", "values": ['"utf-8"', '"utf-16"'], "labels": ["utf-8", "utf-16 (PowerShell -enc)"]},
        "roles": {"text": "the text to encode", "encoding": "how the text becomes bytes first: utf-8, or utf-16 like PowerShell"},
    },
    "unbase64": {
        "args": {"text": '"dwBoAG8AYQBtAGkA"'},
        "vary": {"param": "text", "values": ['"dwBoAG8AYQBtAGkA"', '"d2hvYW1p"', '"d2hvYW1p\\n"'], "labels": ["UTF-16 (PowerShell)", "UTF-8", "with a newline"]},
        "roles": {"text": "base64 text (normal or URL-safe; padding is optional)",
                  "encoding": "force utf-8 or utf-16 instead of guessing"},
    },
    "hex": {"args": {"text": '"hi!"'}, "roles": {"text": "the text; its UTF-8 bytes are written as hex"}},
    "unhex": {
        "args": {"text": '"68 69"'},
        "vary": {"param": "text", "values": ['"68 69"', '"\\\\x68\\\\x69"', '"0x68:0x69"'], "labels": ["68 69", "\\x68\\x69", "0x68:0x69"]},
        "roles": {"text": "hex text; spaces, colons, \\x and 0x are ignored"},
    },
    "url": {"args": {"text": '"a b&c=d/e"'}, "roles": {"text": "text to make safe inside a link"}},
    "unurl": {
        "args": {"text": '"%252e%252e%252fetc"'},
        "vary": {"param": "text", "values": ['"%252e%252e%252fetc"', '"%2e%2e%2fetc"', '"a+b%26c"'], "labels": ["double-encoded", "encoded once", "a+b%26c"]},
        "roles": {"text": "URL-encoded text; run it twice on double-encoded attacks"},
    },
    "rot13": {
        "args": {"text": '"Software"'}, "mode": "cells",
        "vary": {"param": "text", "values": ['"Software"', '"Fbsgjner"'], "labels": ["Software", "Fbsgjner (twice: back again)"]},
        "roles": {"text": "the text; every letter moves 13 places"},
    },
    "entropy": {
        "args": {"text": '"aaaa"'},
        "vary": {"param": "text", "values": ['"aaaa"', '"password"', '"x9$Kq2!vL0pZ"', '"SGVsbG8gd29ybGQh"'],
                 "labels": ["aaaa", "password", "x9$Kq2!vL0pZ", "base64 text"]},
        "roles": {"text": "the text to measure: higher means more random-looking"},
    },
}
