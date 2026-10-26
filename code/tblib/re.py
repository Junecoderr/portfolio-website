"""re: regular expressions (patterns for finding text)."""

from __future__ import annotations

import re as _re

from tblib._api import Module, check_type, fail

import tb as _tb


def compile_pattern(line, pattern, fn):
    """Compile a TB pattern with a friendly error. Other tblib modules may
    reuse this."""
    check_type(line, pattern, "text", f"re.{fn}", "pattern")
    try:
        return _re.compile(pattern)
    except _re.error as err:
        where = f" at position {err.pos}" if err.pos is not None else ""
        fail(line, f"That pattern doesn't work: {err.msg}{where}. (Pattern: {pattern})", cls="ParseError")


def match_map(found) -> dict:
    """A match as a TB map: 0 is the whole match, 1, 2, ... the groups,
    plus named groups by name, and "start"/"end" positions."""
    result: dict = {0: found.group(0)}
    for i, group in enumerate(found.groups(), start=1):
        result[i] = group
    for name, group in found.groupdict().items():
        result[name] = group
    result.setdefault("start", found.start())
    result.setdefault("end", found.end())
    return result


def load(api):
    m = Module(
        "re",
        "Regular expressions: search text with patterns like \"\\d+\" (digits) or \"\\w+@\\w+\". "
        "Uses Python's pattern syntax.",
    )

    def text_arg(line, text, fn):
        return check_type(line, text, "text", f"re.{fn}", "text")

    @m.fn("test", 2, doc="test(pattern, text) -> bool\nTrue if the pattern appears anywhere in the text.")
    def test(line, pattern, text):
        regex = compile_pattern(line, pattern, "test")
        return regex.search(text_arg(line, text, "test")) is not None

    @m.fn("match", 2, doc="match(pattern, text) -> map or nil\nThe first place the pattern matches: m[0] is the whole match, m[1], m[2]... the groups in (), named groups (?P<name>...) by name, plus \"start\" and \"end\". nil if there is no match.")
    def match(line, pattern, text):
        regex = compile_pattern(line, pattern, "match")
        found = regex.search(text_arg(line, text, "match"))
        return None if found is None else match_map(found)

    @m.fn("find_all", 2, doc="find_all(pattern, text) -> list\nEvery match. With one group in () you get that group's text; with several, a list of groups for each match.")
    def find_all(line, pattern, text):
        regex = compile_pattern(line, pattern, "find_all")
        results = regex.findall(text_arg(line, text, "find_all"))
        return [list(r) if isinstance(r, tuple) else r for r in results]

    @m.fn("replace", 3, doc="replace(pattern, text, with) -> text\nSwap every match for with. In with, \\1 means group 1. with can also be a function that gets the match map and gives back text.")
    def replace(line, pattern, text, replacement):
        regex = compile_pattern(line, pattern, "replace")
        text = text_arg(line, text, "replace")
        if isinstance(replacement, str):
            try:
                return regex.sub(replacement, text)
            except (_re.error, IndexError) as err:
                fail(line, f"re.replace() can't use that replacement text: {err}.", cls="ValueError")
        check_type(line, replacement, "function", "re.replace", "with")
        return regex.sub(lambda found: _tb.to_text(api.call(replacement, [match_map(found)], line)), text)

    @m.fn("split", 2, doc="split(pattern, text) -> list\nCut the text at every match: split(\",\\s*\", \"a, b,c\") is [\"a\", \"b\", \"c\"].")
    def split(line, pattern, text):
        regex = compile_pattern(line, pattern, "split")
        return [p for p in regex.split(text_arg(line, text, "split")) if p is not None]

    @m.fn("escape", 1, doc="escape(text) -> text\nThe text with pattern characters made safe, to search for it exactly.")
    def escape(line, text):
        return _re.escape(text_arg(line, text, "escape"))

    return m
