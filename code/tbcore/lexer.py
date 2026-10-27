"""The lexer: turns TB source text into tokens.

Tokens keep the comments and blank lines that came before them
(`comments_before`, `blank_lines_before`) and a comment that followed on the
same line (`trailing_comment`), so the formatter can put them back.
`blank_lines_before` counts the blank lines above the first comment (or above
the token when there are no comments); a blank line between two comments, or
between the last comment and the token, shows up as an "" entry in
`comments_before` (real comments always start with "#").
"""

from __future__ import annotations

from .errors import TOO_BIG, TBError, check_size

KEYWORDS = {
    # 1.0 / 2.0
    "let", "fn", "return", "if", "else", "while", "for", "in",
    "true", "false", "nil", "and", "or", "not",
    # 3.0
    "class", "self", "super", "static", "extends", "match", "const", "export",
    "from", "yield", "async", "await", "is", "enum", "finally", "then",
}
NEW_KEYWORDS = {
    "class", "self", "super", "static", "extends", "match", "const", "export",
    "from", "yield", "async", "await", "is", "enum", "finally", "then",
}
# import, as, try, catch, throw, break, continue, defer and assert are not
# reserved words: they only mean something special where they start a
# statement, so programs that use them as names keep working.

# What to suggest instead of a reserved word used as a name.
KEYWORD_ALTERNATIVES = {
    "match": "found", "class": "kind", "self": "me", "super": "parent", "static": "fixed",
    "extends": "ext", "const": "constant", "export": "exported", "from": "source",
    "yield": "produced", "async": "is_async", "await": "waited", "is": "is_it",
    "enum": "options", "finally": "at_last", "then": "after", "in": "inside",
    "fn": "func", "let": "value", "if": "cond", "for": "each", "while": "during",
    "and": "both", "or": "either", "not": "negated", "return": "result",
    "true": "yes", "false": "no", "nil": "nothing", "else": "otherwise",
}

ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "**=", "//=", "%=", "&=", "|=", "^=", "<<=", ">>="}
THREE_CHAR_OPS = {"**=", "//=", "<<=", ">>=", "..=", "..."}
TWO_CHAR_OPS = {
    "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "**", "//", "<<", ">>", "..", "=>", "->",
}
ONE_CHAR_OPS = set("+-*/%<>=!(){}[],;.:&|^~")


class Token:
    __slots__ = ("kind", "value", "line", "comments_before", "blank_lines_before",
                 "trailing_comment", "end_line")

    def __init__(self, kind: str, value, line: int, end_line: int | None = None):
        self.kind = kind    # NUMBER, STRING, FSTRING, BYTES, IDENT, KEYWORD, OP, EOF
        self.value = value
        self.line = line
        self.end_line = line if end_line is None else end_line
        self.comments_before: list[str] = []
        self.blank_lines_before = 0
        self.trailing_comment: str | None = None

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, line {self.line})"


DIGITS = "0123456789"
HEX_DIGITS = "0123456789abcdefABCDEF"

# Friendly hints for characters people often type by accident.
CHAR_HINTS = {
    "“": "That's a curly quote. TB needs straight quotes: \" or '.",
    "”": "That's a curly quote. TB needs straight quotes: \" or '.",
    "‘": "That's a curly quote. TB needs straight quotes: \" or '.",
    "’": "That's a curly quote. TB needs straight quotes: \" or '.",
    "$": "TB names don't start with '$'. Just write the name, like: let total = 5",
    "@": "TB doesn't use '@'.",
    "`": "TB uses straight quotes for text: \" or '.",
    "?": "TB has no '?' operator. For a choice, write: if cond then a else b",
}


def _never_ends(quote, line):
    return TBError(f"This text never ends. Did you forget a closing {quote}?", line)


def _read_escape(source: str, i: int, line: int):
    """Read the escape at source[i] == '\\'. Returns (text, new_i).
    Unknown escapes are kept as typed (\\q stays \\q) so regexes survive."""
    n = len(source)
    nxt = source[i + 1]
    simple = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "'": "'", "\\": "\\"}
    if nxt in simple:
        return simple[nxt], i + 2
    if nxt == "x":
        hexs = source[i + 2:i + 4]
        if len(hexs) == 2 and all(c in HEX_DIGITS for c in hexs):
            return chr(int(hexs, 16)), i + 4
        raise TBError("'\\x' needs two hex digits after it, like \\x41 for 'A'.", line)
    if nxt == "u":
        if source[i + 2:i + 3] != "{":
            raise TBError("'\\u' needs a code in braces, like \\u{1F600}.", line)
        j = source.find("}", i + 3)
        code = source[i + 3:j] if j != -1 else ""
        if j == -1 or not code or not all(c in HEX_DIGITS for c in code) or len(code) > 6:
            raise TBError("'\\u{...}' needs 1 to 6 hex digits inside the braces, like \\u{E9}.", line)
        value = int(code, 16)
        if value > 0x10FFFF:
            raise TBError(f"\\u{{{code}}} is not a valid character code (the biggest is 10FFFF).", line)
        return chr(value), j + 1
    return "\\" + nxt, i + 2


def _read_string_body(source: str, i: int, line: int, quote: str, triple: bool, raw: bool):
    """Read text after the opening quote(s). Returns (text, new_i, new_line)."""
    n = len(source)
    start_line = line
    chars: list[str] = []
    end = quote * 3 if triple else quote
    while True:
        if i >= n:
            raise _never_ends(end, start_line)
        ch = source[i]
        if triple:
            if source.startswith(end, i):
                return "".join(chars), i + 3, line
        elif ch == quote:
            return "".join(chars), i + 1, line
        if ch == "\n":
            if not triple:
                raise _never_ends(quote, start_line)
            line += 1
            chars.append(ch)
            i += 1
            continue
        if ch == "\\" and not raw and i + 1 < n and source[i + 1] != "\n":
            text, i = _read_escape(source, i, line)
            chars.append(text)
            continue
        chars.append(ch)
        i += 1


def _lex_fstring(source: str, i: int, line: int, triple: bool, raw: bool = False):
    """Read f"..." starting at the quote. Returns (parts, new_i, new_line): parts
    is a list of plain text pieces and (expression source, line, spec, conv)
    tuples; spec is the text after ':' (or None), conv is "r" for {x!r}."""
    n = len(source)
    quote = source[i]
    i += 3 if triple else 1
    start_line = line
    end = quote * 3 if triple else quote
    parts: list = []
    buf: list[str] = []
    while True:
        if i >= n:
            raise _never_ends(end, start_line)
        ch = source[i]
        if ch == "\n":
            if not triple:
                raise _never_ends(quote, start_line)
            line += 1
            buf.append(ch)
            i += 1
            continue
        if triple and source.startswith(end, i):
            i += 3
            break
        if not triple and ch == quote:
            i += 1
            break
        if ch == "\\" and not raw and i + 1 < n and source[i + 1] != "\n":
            text, i = _read_escape(source, i, line)
            buf.append(text)
            continue
        if ch == "{":
            if source[i + 1:i + 2] == "{":
                buf.append("{")
                i += 2
                continue
            i += 1
            start = i
            depth = 0
            spec = None
            conv = None
            expr_line = line
            while True:
                if i >= n or (source[i] == "\n" and not triple):
                    raise TBError(
                        "This '{' inside the f-text was never closed. Did you forget a '}'?", expr_line
                    )
                c = source[i]
                if c == "\n":
                    line += 1
                    i += 1
                    continue
                if c in "\"'":
                    j = i + 1
                    while j < n and source[j] != c and source[j] != "\n":
                        j += 2 if source[j] == "\\" else 1
                    if j >= n or source[j] != c:
                        raise TBError(
                            "This '{' inside the f-text was never closed. Did you forget a '}'?", expr_line
                        )
                    i = j + 1
                    continue
                if c in "([{":
                    depth += 1
                elif c in ")]":
                    depth -= 1
                elif c == "}":
                    if depth == 0:
                        break
                    depth -= 1
                elif c == "!" and depth == 0 and source[i + 1:i + 2] == "r" and source[i + 2:i + 3] in ("}", ":"):
                    conv = "r"
                    expr_end = i
                    i += 2
                    if source[i] == ":":
                        j = source.find("}", i)
                        if j == -1:
                            raise TBError("This '{' inside the f-text was never closed. Did you forget a '}'?", expr_line)
                        spec = source[i + 1:j]
                        i = j
                    break
                elif c == ":" and depth == 0:
                    expr_end = i
                    j = i + 1
                    while j < n and source[j] not in "}\n":
                        j += 1
                    if j >= n or source[j] != "}":
                        raise TBError("This '{' inside the f-text was never closed. Did you forget a '}'?", expr_line)
                    spec = source[i + 1:j]
                    i = j
                    break
                i += 1
            if conv is None and spec is None:
                expr_end = i
            expr = source[start:expr_end]
            i += 1
            if not expr.strip():
                raise TBError(
                    "There's an empty {} in this f-text. Put a name inside, like {name}, "
                    "or write {{ and }} to show real braces.",
                    expr_line,
                )
            parts.append("".join(buf))
            buf = []
            parts.append((expr, expr_line, spec, conv))
            continue
        if ch == "}":
            if source[i + 1:i + 2] == "}":
                buf.append("}")
                i += 2
                continue
            raise TBError("To show a '}' in f-text, write it twice: }}", line)
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p != ""], i, line


def _lex_number(source: str, i: int, line: int):
    """Read a number literal at source[i] (a digit). Returns (value, new_i)."""
    n = len(source)
    start = i
    if source[i] == "0" and i + 1 < n and source[i + 1] in "xXbBoO":
        base = {"x": 16, "b": 2, "o": 8}[source[i + 1].lower()]
        allowed = {16: HEX_DIGITS, 2: "01", 8: "01234567"}[base]
        i += 2
        digits_start = i
        while i < n and (source[i] in allowed or source[i] == "_"):
            i += 1
        text = source[digits_start:i].replace("_", "")
        if not text or (i < n and (source[i].isalnum() or source[i] == "_")):
            kind = {16: "hex (0-9, a-f)", 2: "binary (0 or 1)", 8: "octal (0-7)"}[base]
            raise TBError(f"'{source[start:i + 1]}' isn't a valid number. After {source[start:start + 2]} write {kind} digits.", line)
        return check_size(int(text, base), line), i
    while i < n and (source[i] in DIGITS or source[i] == "_"):
        i += 1
    is_float = False
    if i + 1 < n and source[i] == "." and source[i + 1] in DIGITS:
        is_float = True
        i += 1
        while i < n and (source[i] in DIGITS or source[i] == "_"):
            i += 1
    if i < n and source[i] in "eE":
        j = i + 1
        if j < n and source[j] in "+-":
            j += 1
        if j < n and source[j] in DIGITS:
            is_float = True
            i = j
            while i < n and source[i] in DIGITS:
                i += 1
    text = source[start:i].replace("_", "")
    if text.endswith("_") or source[i - 1] == "_":
        raise TBError("A number can't end with '_'.", line)
    if is_float:
        value = float(text)
        if value == float("inf"):
            raise TBError(TOO_BIG, line)
        return check_size(value, line), i
    if len(text) > 400:
        raise TBError(TOO_BIG, line)
    return check_size(int(text), line), i


def _string_prefix(source: str, i: int) -> int:
    """How many prefix letters (f, r, b, or two of them) start a quoted text
    at source[i]; 0 if this isn't a prefixed text."""
    j = i
    while j < len(source) and j - i < 2 and source[j] in "fFrRbB":
        j += 1
    if j == i or j >= len(source) or source[j] not in "\"'":
        return 0
    prefix = source[i:j].lower()
    if len(set(prefix)) != len(prefix) or prefix in ("fb", "bf"):
        return 0
    return j - i


def tokenize(source: str, line: int = 1) -> list[Token]:
    if "\r\n" in source:
        # Windows line endings: a text literal that spans lines holds "\n",
        # never "\r\n", whichever editor saved the file.
        source = source.replace("\r\n", "\n")
    tokens: list[Token] = []
    i = 0
    n = len(source)
    pending_comments: list[str] = []
    pending_blank = 0
    newlines = 0          # newlines since the last token or comment
    seen_token = False

    def add(tok: Token) -> None:
        nonlocal pending_comments, pending_blank, newlines, seen_token
        if pending_comments:
            pending_comments.extend([""] * max(0, newlines - 1))
            tok.comments_before = pending_comments
            tok.blank_lines_before = pending_blank
            pending_comments = []
        elif seen_token:
            tok.blank_lines_before = max(0, newlines - 1)
        else:
            tok.blank_lines_before = max(0, newlines - 1) if newlines else 0
        newlines = 0
        seen_token = True
        tokens.append(tok)

    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            newlines += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            start = i
            while i < n and source[i] != "\n":
                i += 1
            text = source[start:i].rstrip()
            if tokens and newlines == 0 and tokens[-1].end_line == line:
                tokens[-1].trailing_comment = text
            else:
                if not pending_comments:
                    pending_blank = max(0, newlines - 1) if (seen_token or newlines) else 0
                else:
                    pending_comments.extend([""] * max(0, newlines - 1))
                pending_comments.append(text)
                newlines = 0
        elif c in DIGITS:
            value, i = _lex_number(source, i, line)
            add(Token("NUMBER", value, line))
        elif c in "fFrRbB" and _string_prefix(source, i):
            # Prefixed text: f"..." (holes), r"..." (raw), b"..." (bytes); rf/fr/rb/br mix.
            size = _string_prefix(source, i)
            prefix = source[i:i + size].lower()
            j = i + size
            quote = source[j]
            triple = source.startswith(quote * 3, j)
            raw = "r" in prefix
            start_line = line
            if "f" in prefix:
                parts, i, line = _lex_fstring(source, j, line, triple, raw)
                add(Token("FSTRING", parts, start_line, line))
            else:
                text, i, line = _read_string_body(source, j + (3 if triple else 1), line, quote, triple, raw)
                if "b" in prefix:
                    try:
                        if not source[j:i].isascii():   # \xNN escapes are fine, é is not
                            raise UnicodeEncodeError("ascii", "", 0, 1, "not ascii")
                        value = text.encode("latin-1")
                    except UnicodeEncodeError:
                        raise TBError(
                            "Bytes text can only hold plain ASCII characters and \\xNN escapes. "
                            'For other characters use "text".bytes().', start_line
                        ) from None
                    add(Token("BYTES", value, start_line, line))
                else:
                    add(Token("STRING", text, start_line, line))
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            word = source[start:i]
            add(Token("KEYWORD" if word in KEYWORDS else "IDENT", word, line))
        elif c in "\"'":
            quote = c
            start_line = line
            triple = source.startswith(quote * 3, i)
            text, i, line = _read_string_body(source, i + (3 if triple else 1), line, quote, triple, False)
            add(Token("STRING", text, start_line, line))
        elif source[i:i + 2] in ("&&", "||"):
            word = "and" if c == "&" else "or"
            both = "both" if c == "&" else "either"
            raise TBError(f"To say '{both}', TB uses the word '{word}'.", line)
        elif source[i:i + 3] in THREE_CHAR_OPS:
            add(Token("OP", source[i:i + 3], line))
            i += 3
        elif source[i:i + 2] in TWO_CHAR_OPS:
            add(Token("OP", source[i:i + 2], line))
            i += 2
        elif c in ONE_CHAR_OPS:
            add(Token("OP", c, line))
            i += 1
        else:
            hint = CHAR_HINTS.get(c, "")
            raise TBError(f"I don't understand the character {c!r}. {hint}".rstrip(), line)
    eof = Token("EOF", None, line)
    if pending_comments:
        pending_comments.extend([""] * max(0, newlines - 1))
        eof.comments_before = pending_comments
        eof.blank_lines_before = pending_blank
    else:
        eof.blank_lines_before = max(0, newlines - 1)
    tokens.append(eof)
    return tokens
