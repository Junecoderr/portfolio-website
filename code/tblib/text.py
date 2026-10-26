"""text: text helpers that the built-in text methods don't cover."""

from __future__ import annotations

import secrets
import string
import textwrap

from tblib._api import Module, check_type, fail

import tb as _tb


def load(api):
    m = Module("text", "Text helpers: padding, wrapping, number formatting and random ids.")

    def pad_args(line, fn, value, width, fill):
        value = _tb.to_text(value)
        width = check_type(line, width, "whole", f"text.{fn}", "width")
        check_type(line, fill, "text", f"text.{fn}", "fill")
        if len(fill) != 1:
            fail(line, f"text.{fn}() needs fill to be exactly one letter, like \" \" or \"0\".")
        if width > 1_000_000:
            fail(line, f"text.{fn}() can't make text that wide.")
        return value, width

    @m.fn("pad_left", 2, 3, doc="pad_left(value, width, fill?) -> text\nAdd fill (a space by default) on the left until it is width letters: pad_left(7, 3, \"0\") is \"007\".")
    def pad_left(line, value, width, fill=" "):
        value, width = pad_args(line, "pad_left", value, width, fill)
        return value.rjust(width, fill)

    @m.fn("pad_right", 2, 3, doc="pad_right(value, width, fill?) -> text\nAdd fill (a space by default) on the right until it is width letters.")
    def pad_right(line, value, width, fill=" "):
        value, width = pad_args(line, "pad_right", value, width, fill)
        return value.ljust(width, fill)

    @m.fn("center", 2, 3, doc="center(value, width, fill?) -> text\nPut the text in the middle of width letters.")
    def center(line, value, width, fill=" "):
        value, width = pad_args(line, "center", value, width, fill)
        return value.center(width, fill)

    @m.fn("wrap", 2, doc="wrap(text, width) -> text\nBreak long text into lines of at most width letters (between words).")
    def wrap(line, value, width):
        check_type(line, value, "text", "text.wrap", "text")
        width = check_type(line, width, "whole", "text.wrap", "width")
        if width < 1:
            fail(line, "text.wrap() needs a width of at least 1.")
        paragraphs = value.split("\n")
        return "\n".join(textwrap.fill(p, width) if p.strip() else "" for p in paragraphs)

    @m.fn("is_digit", 1, doc="is_digit(text) -> bool\nTrue if the text is only the digits 0-9 (and not empty).")
    def is_digit(line, value):
        check_type(line, value, "text", "text.is_digit", "text")
        return value != "" and all(c in string.digits for c in value)

    @m.fn("format_number", 1, 2, doc="format_number(n, decimals?) -> text\nThe number with commas between thousands: format_number(1234.5, 2) is \"1,234.50\".")
    def format_number(line, n, decimals=0):
        check_type(line, n, "number", "text.format_number", "n")
        decimals = check_type(line, decimals, "whole", "text.format_number", "decimals")
        if not 0 <= decimals <= 20:
            fail(line, "text.format_number() needs decimals from 0 to 20.")
        value = _tb.tb_round(n, decimals, line)
        return f"{value:,.{decimals}f}"

    @m.fn("random_id", 0, 1, doc="random_id(length?) -> text\nA random id of letters and digits (8 by default), safe for secrets.")
    def random_id(line, length=8):
        length = check_type(line, length, "whole", "text.random_id", "length")
        if not 1 <= length <= 1000:
            fail(line, "text.random_id() needs a length from 1 to 1000.")
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @m.fn("reverse", 1, doc="reverse(text) -> text\nThe text backwards.")
    def reverse(line, value):
        return check_type(line, value, "text", "text.reverse", "text")[::-1]

    @m.fn("title", 1, doc="title(text) -> text\nEvery Word Starting With A Capital.")
    def title(line, value):
        check_type(line, value, "text", "text.title", "text")
        return string.capwords(value)

    @m.fn("count", 2, doc="count(text, part) -> number\nHow many times part appears in the text.")
    def count(line, value, part):
        check_type(line, value, "text", "text.count", "text")
        check_type(line, part, "text", "text.count", "part")
        if part == "":
            fail(line, "text.count() needs a part that isn't empty.")
        return value.count(part)

    return m
