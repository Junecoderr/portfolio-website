"""`tb lsp`: the TB language server (LSP 3.17 over stdin/stdout, standard
library only).

Features:
  * diagnostics - parse errors, plus `tbcore.types.check_source` (the
    checker behind `tb check`) when it is available
  * completion - keywords, built-in functions, standard libraries and their
    members (after `fs.`), methods by receiver type (after `items.`), members
    of your classes (after `self.` / `Dog.`), names from imported .tb files,
    and the names defined in the file
  * hover - signatures and docs of built-ins, library functions, methods,
    keywords and your own fn/class/const (their `##` doc comments)
  * signature help - inside `f(...)`, with the active input highlighted
  * go to definition - in the same file and across `import "file.tb"` /
    `from "file.tb" import name`
  * document symbols (outline), formatting (`tb fmt`), rename within a file

Documents are synced in full. Everything is computed from the text the editor
sends, so unsaved changes count.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

__all__ = ["main", "Server"]

KEYWORD_DOCS = {
    "let": "let name = value - make a new name.",
    "const": "const NAME = value - a name that can't be changed.",
    "fn": "fn name(inputs) { ... } - make a function.",
    "return": "return value - hand a value back from a function.",
    "if": "if cond { ... } else { ... }  (or the choice: if cond then a else b)",
    "else": "else { ... } - what runs when the if's condition is false.",
    "then": "if cond then a else b - a choice between two values.",
    "while": "while cond { ... } - repeat while cond is true.",
    "for": "for x in items { ... } - repeat for every item (for k, v in a map).",
    "in": "x in items - is x one of the items? Also: for x in items.",
    "match": "match value { pattern => result ... } - pick a result by the value's shape.",
    "class": "class Name extends Base { let field = 1  fn method() { ... } }",
    "extends": "class Dog extends Animal { ... } - Dog gets everything Animal has.",
    "self": "self - the object a method was called on.",
    "super": "super.method() - call the base class's version of a method.",
    "static": "static let / static fn - belongs to the class, not each object.",
    "enum": "enum Level { low, high } - a fixed set of named values.",
    "export": "export fn/let/const/class - share this name with files that import this one.",
    "from": "from fs import read, write  /  from \"helpers.tb\" import greet",
    "yield": "yield value - hand out one value from a generator function.",
    "async": "async fn name() { ... } - calling it starts a task; await gets its result.",
    "await": "await task - wait for an async fn (or a parallel task) and get its value.",
    "is": "x is Type - is x a number, text, list, ... or an instance of a class?",
    "finally": "try { } catch err { } finally { ... } - runs whether or not there was an error.",
    "and": "a and b - true when both are true.",
    "or": "a or b - true when either is true.",
    "not": "not x - the opposite of x.",
    "true": "true - yes.", "false": "false - no.", "nil": "nil - nothing.",
    "import": "import fs  /  import \"helpers.tb\" as h - use a library or your own file.",
    "try": "try { ... } catch err { ... } - handle errors.",
    "catch": "catch err: FileError { ... } - what to do when the try block fails.",
    "throw": "throw Error(\"message\") - stop with an error.",
    "break": "break - leave the loop.",
    "continue": "continue - go to the loop's next round.",
    "defer": "defer { ... } - run this block when the function finishes.",
    "assert": "assert cond, \"message\" - stop with an error if cond is false.",
}

SYMBOL_KIND = {"fn": 12, "class": 5, "method": 6, "field": 8, "enum": 10, "member": 22,
               "let": 13, "const": 14, "param": 13, "import": 2}
COMPLETION_KIND = {"fn": 3, "method": 2, "class": 7, "field": 5, "enum": 13, "member": 20,
                   "let": 6, "const": 21, "param": 6, "import": 9, "module": 9, "keyword": 14,
                   "builtin": 3, "value": 21}


# =============================================================================
# Text positions (LSP counts UTF-16 code units)
# =============================================================================


class TextIndex:
    def __init__(self, text: str):
        self.text = text
        self.starts = [0]
        for m in re.finditer("\n", text):
            self.starts.append(m.end())

    def pos(self, offset: int) -> dict:
        import bisect

        line = bisect.bisect_right(self.starts, offset) - 1
        start = self.starts[line]
        prefix = self.text[start:offset]
        return {"line": line, "character": len(prefix.encode("utf-16-le")) // 2}

    def offset(self, position: dict) -> int:
        line = max(0, min(position.get("line", 0), len(self.starts) - 1))
        start = self.starts[line]
        end = self.starts[line + 1] - 1 if line + 1 < len(self.starts) else len(self.text)
        units = position.get("character", 0)
        k = start
        while k < end and units > 0:
            units -= 2 if ord(self.text[k]) > 0xFFFF else 1
            k += 1
        return k

    def range(self, a: int, b: int) -> dict:
        return {"start": self.pos(a), "end": self.pos(b)}

    def line_range(self, line0: int) -> dict:
        line0 = max(0, min(line0, len(self.starts) - 1))
        start = self.starts[line0]
        end = self.starts[line0 + 1] - 1 if line0 + 1 < len(self.starts) else len(self.text)
        text = self.text[start:end]
        lead = len(text) - len(text.lstrip())
        return self.range(start + lead, end)


def uri_to_path(uri: str) -> str | None:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    if os.name == "nt" and re.match(r"^\\[A-Za-z]:", path):
        path = path[1:]
    return path


def path_to_uri(path: str) -> str:
    path = os.path.abspath(path)
    if os.name == "nt":
        return "file:///" + urllib.parse.quote(path.replace("\\", "/"))
    return "file://" + urllib.parse.quote(path)


# =============================================================================
# Analysis of one document
# =============================================================================


class Symbol:
    __slots__ = ("name", "kind", "tok", "node", "scope", "detail", "doc", "container", "children",
                 "end_line", "line")

    def __init__(self, name, kind, tok, node=None, scope=None, detail="", doc="", container=None):
        self.name, self.kind, self.tok, self.node = name, kind, tok, node
        self.scope = scope            # (start, end) token range where it is visible, None = file
        self.detail, self.doc, self.container = detail, doc, container
        self.children: list[Symbol] = []
        self.end_line = 0
        self.line = 0


class Analysis:
    """Parse result, token offsets and the symbols of one TB source text."""

    def __init__(self, text: str, path: str | None):
        from tbtools.fmt import Printer, SpanParser, scan_spans
        from tbcore.lexer import tokenize

        self.text = text
        self.path = path
        self.index = TextIndex(text)
        tokens = tokenize(text)
        spans, _cols = scan_spans(text)
        self.offsets = {id(tok): span for tok, span in zip(tokens, spans)}
        parser = SpanParser(tokens, os.path.dirname(path) if path else None)
        self.program = parser.parse_program()
        self.program.uses_export = parser.saw_export
        self.tokens = parser.tokens
        raw_by_id = {id(tok): text[a:b] for tok, (a, b) in zip(tokens, spans)}
        self.printer = Printer(text, self.tokens, raw_by_id, {})
        self.symbols: list[Symbol] = []      # every definition, in source order
        self.top: list[Symbol] = []          # top-level declarations (outline)
        self.imports: dict[str, dict] = {}   # alias -> {"kind": "library"|"file", "target", "names"?}
        self._collect(self.program.body, None, self.top)

    # -- helpers ----------------------------------------------------------------

    def tok_offsets(self, idx: int) -> tuple[int, int] | None:
        if 0 <= idx < len(self.tokens):
            return self.offsets.get(id(self.tokens[idx]))
        return None

    def find_ident(self, start: int, end: int, name: str) -> int:
        for k in range(max(0, start), min(end, len(self.tokens))):
            tok = self.tokens[k]
            if tok.kind in ("IDENT", "KEYWORD") and tok.value == name:
                return k
        return -1

    def token_at(self, offset: int) -> int:
        """Index of the token that covers offset (or ends right at it)."""
        best = -1
        for k, tok in enumerate(self.tokens):
            span = self.offsets.get(id(tok))
            if span is None:
                continue
            if span[0] <= offset <= span[1]:
                best = k
                if offset < span[1]:
                    return k
            elif span[0] > offset:
                break
        return best

    def render(self, doc) -> str:
        from tbtools.fmt import render

        return render(doc, width=10_000)

    def signature(self, decl) -> str:
        from tbcore import ast as A

        if isinstance(decl, A.FnDecl):
            text = ("async " if decl.is_async else "") + "fn " + decl.name + self.render(self.printer.params(decl.params))
            if decl.returns:
                text += " -> " + decl.returns
            return text
        if isinstance(decl, A.ClassDecl):
            text = "class " + decl.name
            if decl.base is not None:
                text += " extends " + self.printer.type_ref(decl.base)
            init = next((m for m in decl.methods if m.name == "init"), None)
            if init is not None:
                text += "  // " + decl.name + self.render(self.printer.params(init.params))
            return text
        if isinstance(decl, A.EnumDecl):
            return f"enum {decl.name} {{ {', '.join(decl.members)} }}"
        return ""

    # -- collecting definitions -----------------------------------------------------

    def _add(self, name, kind, tok, node=None, scope=None, detail="", doc="", container=None, out=None):
        sym = Symbol(name, kind, tok, node, scope, detail, doc, container)
        sym.line = self.tokens[tok].line if tok >= 0 else 0
        self.symbols.append(sym)
        if out is not None:
            out.append(sym)
        return sym

    def _pattern_names(self, pattern) -> list[str]:
        from tbcore import ast as A

        if isinstance(pattern, str):
            return [pattern]
        if isinstance(pattern, A.NamePattern):
            return [pattern.name]
        if isinstance(pattern, A.ListPattern):
            names = [n for p in pattern.items for n in self._pattern_names(p)]
            return names + ([pattern.rest] if pattern.rest else [])
        if isinstance(pattern, A.MapPattern):
            return [n for _k, sub, _d in pattern.entries for n in self._pattern_names(sub)]
        return []

    def _collect(self, body, scope, out):
        from tbcore import ast as A
        from tbtools.docgen import doc_comment

        for stmt in body:
            span = getattr(stmt, "_span", None) or (0, 0)
            decl = stmt.decl if isinstance(stmt, A.Export) else stmt
            doc = doc_comment(getattr(stmt, "comments", None))
            start, end = span
            if isinstance(decl, A.FnDecl):
                tok = self.find_ident(start + 1, end, decl.name)
                sym = self._add(decl.name, "fn", tok, decl, scope, self.signature(decl), doc, out=out)
                sym.end_line = getattr(stmt, "end_line", 0)
                self._function(decl, span)
            elif isinstance(decl, A.ClassDecl):
                tok = self.find_ident(start + 1, end, decl.name)
                sym = self._add(decl.name, "class", tok, decl, scope, self.signature(decl), doc, out=out)
                sym.end_line = getattr(stmt, "end_line", 0)
                self._class(decl, sym)
            elif isinstance(decl, A.EnumDecl):
                tok = self.find_ident(start + 1, end, decl.name)
                sym = self._add(decl.name, "enum", tok, decl, scope, self.signature(decl), doc, out=out)
                sym.end_line = getattr(stmt, "end_line", 0)
                for member in decl.members:
                    mtok = self.find_ident(tok + 1, end, member)
                    sym.children.append(self._add(member, "member", mtok, None, scope,
                                                  f"{decl.name}.{member}", container=decl.name))
            elif isinstance(decl, A.Let):
                kind = "const" if decl.is_const else "let"
                for name in self._pattern_names(decl.target):
                    tok = self.find_ident(start + 1, end, name)
                    value = ""
                    if decl.name is not None:
                        try:
                            value = self.render(self.printer.expr(decl.value)).split("\n")[0]
                        except Exception:
                            value = ""
                        if len(value) > 60:
                            value = value[:57] + "..."
                    detail = f"{kind} {name}" + (f": {decl.annotation}" if decl.annotation else "") + \
                        (f" = {value}" if value else "")
                    sym = self._add(name, kind, tok, decl, scope, detail, doc, out=out)
                    sym.end_line = getattr(stmt, "end_line", 0)
                self._expressions(decl.value, scope)
            elif isinstance(decl, A.Import):
                tok = end - 1
                if decl.is_file:
                    self.imports[decl.alias] = {"kind": "file", "target": decl.target,
                                                "package": getattr(decl, "package_name", None)}
                else:
                    self.imports[decl.alias] = {"kind": "library", "target": decl.target}
                self._add(decl.alias, "import", tok, decl, scope, f"import {decl.target}", out=None)
            elif isinstance(decl, A.FromImport):
                for name, alias in decl.names:
                    tok = self.find_ident(start + 3, end, alias)
                    self.imports[alias] = {"kind": "file-name" if decl.is_file else "library-name",
                                           "target": decl.target, "name": name}
                    self._add(alias, "import", tok, decl, scope, f"from {decl.target} import {name}")
            elif isinstance(decl, A.For):
                for target in decl.targets:
                    for name in self._pattern_names(target):
                        self._add(name, "let", self.find_ident(start + 1, end, name), decl, span,
                                  f"for {name} in ...")
                self._expressions(decl.iterable, scope)
                self._collect(decl.body, scope, None)
            elif isinstance(decl, A.If):
                for cond, block in decl.branches:
                    self._expressions(cond, scope)
                    self._collect(block, scope, None)
                if decl.otherwise:
                    self._collect(decl.otherwise, scope, None)
            elif isinstance(decl, A.While):
                self._expressions(decl.cond, scope)
                self._collect(decl.body, scope, None)
            elif isinstance(decl, A.Try):
                self._collect(decl.body, scope, None)
                for catch in decl.catches:
                    if catch.name:
                        k = self.find_ident(start, end, catch.name)
                        self._add(catch.name, "let", k, catch, scope, f"catch {catch.name}")
                    self._collect(catch.body, scope, None)
                if decl.finally_body:
                    self._collect(decl.finally_body, scope, None)
            elif isinstance(decl, A.Defer):
                self._collect(decl.body, scope, None)
            else:
                self._expressions(decl, scope)

    def _function(self, fn, span):
        for p in fn.params:
            target = p.target
            for name in self._pattern_names(target):
                tok = self.find_ident(span[0], span[1], name)
                self._add(name, "param", tok, fn, span, f"(input) {name}")
        self._collect(fn.body, span, None)

    def _class(self, decl, sym):
        from tbtools.docgen import doc_comment

        open_idx = -1
        if hasattr(decl, "_span"):
            start, end = decl._span
            open_idx = start
            while open_idx < end and not self.printer.tok_is(open_idx, "{"):
                open_idx += 1
        for kind, is_static, obj, m_start, m_end in self.printer.class_member_order(decl, open_idx, -1):
            comments = list(self.tokens[m_start].comments_before) if m_start is not None else []
            if kind == "field":
                name, value, annotation = obj
                tok = self.find_ident(m_start or 0, m_end or 0, name) if m_start is not None else -1
                detail = ("static " if is_static else "") + f"let {name}" + (f": {annotation}" if annotation else "")
                child = self._add(name, "field", tok, None, None, detail, doc_comment(comments), container=decl.name)
            else:
                tok = self.find_ident(m_start or 0, m_end or 0, obj.name) if m_start is not None else -1
                detail = ("static " if is_static else "") + self.signature(obj)
                child = self._add(obj.name, "method", tok, obj, None, detail,
                                  doc_comment(obj.comments or comments), container=decl.name)
                child.end_line = self.tokens[m_end - 1].end_line if m_end else obj.line
                fspan = (m_start, m_end) if m_start is not None else (0, 0)
                self._function(obj, fspan)
            sym.children.append(child)

    def _expressions(self, node, scope):
        """Find function values (fn(x) { }, x => ...) inside an expression, for their inputs."""
        from tbcore import ast as A

        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, A.FnExpr):
                span = getattr(n, "_span", None) or scope or (0, len(self.tokens))
                for p in n.params:
                    for name in self._pattern_names(p.target):
                        self._add(name, "param", self.find_ident(span[0], span[1], name), n, span,
                                  f"(input) {name}")
                body = n.body
                if body and not (len(body) == 1 and isinstance(body[0], A.Return) and
                                 "_span" not in body[0].__dict__):
                    self._collect(body, span, None)
                else:
                    stack.extend(x.value for x in body if isinstance(x, A.Return) and x.value is not None)
                continue
            if isinstance(n, A.Match):
                stack.append(n.subject)
                for arm in n.arms:
                    if arm.is_block:
                        self._collect(arm.body, scope, None)
                    else:
                        stack.append(arm.body)
                continue
            if isinstance(n, (list, tuple)):
                stack.extend(n)
            elif isinstance(n, A.Node):
                stack.extend(v for v in vars(n).values() if isinstance(v, (A.Node, list, tuple)))

    # -- lookups ---------------------------------------------------------------------

    def visible(self, name: str, at_tok: int) -> Symbol | None:
        """The definition of name that the code at token at_tok sees."""
        best = None
        best_size = None
        for sym in self.symbols:
            if sym.name != name or sym.kind in ("field", "method", "member"):
                continue
            if sym.scope is None:
                size = len(self.tokens) + 1
            else:
                if not (sym.scope[0] <= at_tok < sym.scope[1]):
                    continue
                size = sym.scope[1] - sym.scope[0]
            if best is None or size < best_size or (size == best_size and sym.tok <= at_tok and
                                                    (best.tok > at_tok or sym.tok > best.tok)):
                best, best_size = sym, size
        return best

    def class_at(self, tok: int):
        for sym in self.symbols:
            if sym.kind == "class" and sym.node is not None and hasattr(sym.node, "_span"):
                s, e = sym.node._span
                if s <= tok < e:
                    return sym
        return None

    def class_named(self, name: str):
        return next((s for s in self.symbols if s.kind in ("class", "enum") and s.name == name), None)

    def top_level(self) -> list[Symbol]:
        uses_export = bool(getattr(self.program, "uses_export", False))
        result = []
        for stmt, sym in self._top_pairs():
            if uses_export:
                from tbcore import ast as A

                if not isinstance(stmt, A.Export):
                    continue
            if sym.name.startswith("_"):
                continue
            result.append(sym)
        return result

    def _top_pairs(self):
        from tbcore import ast as A

        pairs = []
        by_node = {}
        for sym in self.top:
            by_node.setdefault(id(sym.node), []).append(sym)
        for stmt in self.program.body:
            decl = stmt.decl if isinstance(stmt, A.Export) else stmt
            for sym in by_node.get(id(decl), []):
                pairs.append((stmt, sym))
        return pairs


# =============================================================================
# Library and built-in knowledge
# =============================================================================


class Knowledge:
    def __init__(self):
        self._libs: dict[str, dict | None] = {}
        self._names: list[str] | None = None
        self._builtins = None

    @property
    def tb(self):
        import tb

        return tb

    def library_names(self) -> list[str]:
        if self._names is None:
            try:
                self._names = list(self.tb.list_libraries())
            except Exception:
                self._names = []
        return self._names

    def library(self, name: str) -> dict | None:
        if name not in self._libs:
            try:
                self._libs[name] = self.tb.describe_library(name)
            except Exception:
                self._libs[name] = None
        return self._libs[name]

    def builtins(self) -> dict:
        if self._builtins is None:
            try:
                self._builtins = self.tb.describe_builtins()
            except Exception:
                self._builtins = {"functions": [], "values": [], "errors": [], "methods": {}}
        return self._builtins

    def builtin(self, name: str) -> dict | None:
        info = self.builtins()
        for group in ("functions", "values", "errors"):
            for entry in info.get(group, []):
                if entry["name"] == name:
                    return entry
        return None

    def methods(self, kind: str | None = None) -> list[tuple[str, dict]]:
        info = self.builtins().get("methods", {})
        kinds = [kind] if kind in info else list(info)
        found = []
        for k in kinds:
            for entry in info.get(k, []):
                found.append((k, entry))
        return found


# =============================================================================
# The server
# =============================================================================


class Server:
    def __init__(self, reader=None, writer=None):
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        self.docs: dict[str, str] = {}
        self.analyses: dict[str, Analysis] = {}
        self.knowledge = Knowledge()
        self.shutdown = False
        self.running = True
        self.file_cache: dict[str, tuple[float, Analysis | None]] = {}

    # -- transport -------------------------------------------------------------------

    def read_message(self) -> dict | None:
        length = None
        while True:
            line = self.reader.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                if length is not None:
                    break
                continue
            name, _, value = line.decode("ascii", "replace").partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
        body = self.reader.read(length)
        if body is None or len(body) < length:
            return None
        return json.loads(body.decode("utf-8"))

    def send(self, message: dict) -> None:
        message["jsonrpc"] = "2.0"
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self.writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        self.writer.flush()

    def notify(self, method: str, params) -> None:
        self.send({"method": method, "params": params})

    # -- dispatch --------------------------------------------------------------------

    def serve(self) -> int:
        while self.running:
            try:
                message = self.read_message()
            except (ValueError, json.JSONDecodeError):
                self.send({"id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if message is None:
                return 0 if self.shutdown else 1
            self.handle(message)
        return 0 if self.shutdown else 1

    def handle(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        has_id = "id" in message
        if method is None:
            return   # a response to something we never ask
        handler = getattr(self, "on_" + method.replace("/", "_").replace("$", "dollar"), None)
        if handler is None:
            if has_id:
                self.send({"id": message["id"], "error": {"code": -32601, "message": f"Unknown method {method}"}})
            return
        if self.shutdown and method not in ("exit",) and has_id:
            self.send({"id": message["id"], "error": {"code": -32600, "message": "The server is shutting down"}})
            return
        try:
            result = handler(params)
        except Exception as err:  # never crash the editor's server
            if has_id:
                self.send({"id": message["id"], "error": {"code": -32603, "message": f"{type(err).__name__}: {err}"}})
            return
        if has_id:
            self.send({"id": message["id"], "result": result})

    # -- lifecycle ----------------------------------------------------------------------

    def on_initialize(self, params):
        try:
            import tb

            version = tb.__version__
        except Exception:
            version = "3"
        return {
            "capabilities": {
                "positionEncoding": "utf-16",
                "textDocumentSync": {"openClose": True, "change": 1, "save": {"includeText": False}},
                "completionProvider": {"triggerCharacters": ["."], "resolveProvider": False},
                "hoverProvider": True,
                "signatureHelpProvider": {"triggerCharacters": ["(", ","], "retriggerCharacters": [","]},
                "definitionProvider": True,
                "documentSymbolProvider": True,
                "documentFormattingProvider": True,
                "renameProvider": {"prepareProvider": True},
            },
            "serverInfo": {"name": "tb lsp", "version": version},
        }

    def on_initialized(self, params):
        return None

    def on_shutdown(self, params):
        self.shutdown = True
        return None

    def on_exit(self, params):
        self.running = False
        return None

    def on_dollar_cancelRequest(self, params):
        return None

    def on_dollar_setTrace(self, params):
        return None

    # -- documents ------------------------------------------------------------------------

    def on_textDocument_didOpen(self, params):
        doc = params["textDocument"]
        self.docs[doc["uri"]] = doc.get("text", "")
        self.refresh(doc["uri"])

    def on_textDocument_didChange(self, params):
        uri = params["textDocument"]["uri"]
        text = self.docs.get(uri, "")
        for change in params.get("contentChanges", []):
            if "range" in change and change["range"] is not None:
                index = TextIndex(text)
                a = index.offset(change["range"]["start"])
                b = index.offset(change["range"]["end"])
                text = text[:a] + change["text"] + text[b:]
            else:
                text = change["text"]
        self.docs[uri] = text
        self.refresh(uri)

    def on_textDocument_didSave(self, params):
        return None

    def on_textDocument_didClose(self, params):
        uri = params["textDocument"]["uri"]
        self.docs.pop(uri, None)
        self.analyses.pop(uri, None)
        self.notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []})

    def analysis(self, uri: str) -> Analysis | None:
        return self.analyses.get(uri)

    def refresh(self, uri: str) -> None:
        text = self.docs.get(uri, "")
        path = uri_to_path(uri)
        error = None
        try:
            self.analyses[uri] = Analysis(text, path)
        except Exception as err:
            error = err
            repaired = self.repair(text, path, err)
            if repaired is not None:
                self.analyses[uri] = repaired
        self.notify("textDocument/publishDiagnostics",
                    {"uri": uri, "diagnostics": self.diagnostics(text, path, error)})

    def repair(self, text: str, path: str | None, err) -> Analysis | None:
        """While the user types, the text often doesn't parse: blank out the
        broken lines (keeping every offset) so completion, hover and the
        outline keep working."""
        lines = text.split("\n")
        for _attempt in range(4):
            line = getattr(err, "line", None)
            if not line or not 1 <= line <= len(lines) or not lines[line - 1].strip():
                return None
            lines[line - 1] = " " * len(lines[line - 1])
            try:
                result = Analysis("\n".join(lines), path)
            except Exception as again:
                err = again
                continue
            result.text = text
            result.index = TextIndex(text)
            return result
        return None

    def diagnostics(self, text: str, path: str | None, error) -> list[dict]:
        index = TextIndex(text)
        found: list[dict] = []
        checker = None
        try:
            from tbcore import types as tbtypes   # written by the type checker; optional

            checker = getattr(tbtypes, "check_source", None)
        except Exception:
            checker = None
        if checker is not None:
            try:
                for d in checker(text, path):
                    found.append(self._diag(index, d))
            except Exception:
                checker = None
        if error is not None and not any(d["severity"] == 1 for d in found):
            message = getattr(error, "message", None) or str(error)
            line = (getattr(error, "line", None) or 1) - 1
            found.append({"range": index.line_range(line), "severity": 1, "source": "tb",
                          "message": message})
        return found

    def _diag(self, index: TextIndex, d) -> dict:
        get = (lambda k, default=None: d.get(k, default)) if isinstance(d, dict) else \
            (lambda k, default=None: getattr(d, k, default))
        line = max(0, (get("line") or 1) - 1)
        col = get("col") or get("column")
        severity = {"error": 1, "warning": 2, "info": 3, "information": 3, "hint": 4}.get(
            str(get("severity", "error")).lower(), 1)
        if col:
            start = index.offset({"line": line, "character": max(0, col - 1)})
            end_line, end_col = get("end_line"), get("end_col")
            if end_line and end_col:
                end = index.offset({"line": end_line - 1, "character": max(0, end_col - 1)})
            else:
                m = re.match(r"\w+", index.text[start:])
                end = start + (len(m.group(0)) if m else 1)
            rng = index.range(start, max(start, end))
        else:
            rng = index.line_range(line)
        message = get("message", "")
        hint = get("hint")
        if hint:
            message += f"\n{hint}"
        result = {"range": rng, "severity": severity, "source": "tb check", "message": message}
        if get("code"):
            result["code"] = get("code")
        return result

    # -- helpers for requests ---------------------------------------------------------------

    def _context(self, params):
        uri = params["textDocument"]["uri"]
        text = self.docs.get(uri, "")
        index = TextIndex(text)
        offset = index.offset(params["position"])
        return uri, text, index, offset

    def _word_at(self, text: str, offset: int) -> tuple[int, int]:
        a = offset
        while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "_"):
            a -= 1
        b = offset
        while b < len(text) and (text[b].isalnum() or text[b] == "_"):
            b += 1
        return a, b

    def _receiver(self, text: str, start: int) -> str | None:
        """The name before '.' in 'name.word' (word starting at start)."""
        k = start - 1
        while k >= 0 and text[k] in " \t":
            k -= 1
        if k < 0 or text[k] != ".":
            return None
        k -= 1
        while k >= 0 and text[k] in " \t":
            k -= 1
        end = k + 1
        while k >= 0 and (text[k].isalnum() or text[k] == "_"):
            k -= 1
        name = text[k + 1:end]
        if not name:
            if end > 0 and text[end - 1] in ")]\"'":
                return "?" + text[end - 1]
            return None
        return name

    def file_analysis(self, path: str) -> Analysis | None:
        path = os.path.abspath(path)
        uri = path_to_uri(path)
        if uri in self.analyses:
            return self.analyses[uri]
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self.file_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            with open(path, encoding="utf-8") as f:
                result = Analysis(f.read(), path)
        except Exception:
            result = None
        self.file_cache[path] = (mtime, result)
        return result

    def import_path(self, analysis: Analysis, info: dict) -> str | None:
        if not analysis.path:
            return None
        target = os.path.join(os.path.dirname(analysis.path), info["target"])
        if os.path.isfile(target):
            return target
        package = info.get("package") or (os.path.splitext(info["target"])[0]
                                          if "/" not in info["target"] else None)
        if package:
            try:
                from tbcore.interp import find_package, package_entry

                folder = find_package(package, os.path.dirname(analysis.path))
                if folder:
                    return package_entry(folder)
            except Exception:
                return None
        return None

    def receiver_kind(self, analysis: Analysis, name: str, tok: int) -> str | None:
        """A guess at the type of a name: text, list, map, number, bytes, or a class name."""
        from tbcore import ast as A

        if name.startswith("?"):
            return {'"': "text", "'": "text", "]": "list"}.get(name[1:])
        sym = analysis.visible(name, tok)
        if sym is None or not isinstance(sym.node, A.Let) or sym.node.name != name:
            return None
        value = sym.node.value
        if sym.node.annotation:
            word = sym.node.annotation.split("<")[0].strip()
            return {"int": "number", "float": "number"}.get(word, word)
        if isinstance(value, A.ListLit):
            return "list"
        if isinstance(value, A.MapLit):
            return "map"
        if isinstance(value, A.FString):
            return "text"
        if isinstance(value, A.Literal):
            v = value.value
            if isinstance(v, bool) or v is None:
                return None
            if isinstance(v, str):
                return "text"
            if isinstance(v, bytes):
                return "bytes"
            return "number"
        if isinstance(value, A.RangeExpr):
            return "range"
        if isinstance(value, A.Call) and isinstance(value.callee, A.Name):
            if analysis.class_named(value.callee.name):
                return value.callee.name
        return None

    # -- completion -------------------------------------------------------------------------

    def on_textDocument_completion(self, params):
        uri, text, index, offset = self._context(params)
        analysis = self.analysis(uri)
        start, _end = self._word_at(text, offset)
        prefix = text[start:offset]
        receiver = self._receiver(text, start)
        items: list[dict] = []
        seen: set[str] = set()

        def add(label, kind, detail="", doc=""):
            if label in seen or not label.startswith(prefix[:1]) and prefix and not label.lower().startswith(prefix.lower()):
                return
            seen.add(label)
            item = {"label": label, "kind": COMPLETION_KIND.get(kind, 1)}
            if detail:
                item["detail"] = detail
            if doc:
                item["documentation"] = {"kind": "markdown", "value": doc}
            items.append(item)

        tok = analysis.token_at(offset) if analysis else -1
        if receiver is not None:
            self._member_completions(analysis, receiver, tok, add)
            return {"isIncomplete": False, "items": items}
        for word in sorted(KEYWORD_DOCS):
            add(word, "keyword", "keyword", KEYWORD_DOCS[word])
        info = self.knowledge.builtins()
        for entry in info.get("functions", []):
            add(entry["name"], "builtin", entry["signature"], entry["doc"])
        for entry in info.get("values", []) + info.get("errors", []):
            add(entry["name"], "value", entry.get("signature", ""), entry.get("doc", ""))
        for name in self.knowledge.library_names():
            add(name, "module", f"library {name} (import {name})")
        if analysis is not None:
            for sym in analysis.symbols:
                if sym.kind in ("field", "method", "member"):
                    continue
                if sym.scope is not None and not (sym.scope[0] <= max(tok, 0) < sym.scope[1]):
                    continue
                add(sym.name, sym.kind, sym.detail, sym.doc)
        return {"isIncomplete": False, "items": items}

    def _member_completions(self, analysis, receiver, tok, add):
        imports = analysis.imports if analysis else {}
        info = imports.get(receiver)
        if info and info["kind"] == "library" or (info is None and receiver in self.knowledge.library_names()
                                                  and (analysis is None or analysis.visible(receiver, tok) is None)):
            lib = self.knowledge.library(info["target"] if info else receiver)
            if lib:
                for entry in lib["functions"]:
                    add(entry["name"], "fn", f"{lib['name']}.{entry['signature']}", entry["doc"])
                for entry in lib["values"]:
                    add(entry["name"], "value", f"{lib['name']}.{entry['name']} = {entry['value']}", entry["doc"])
            return
        if info and info["kind"] == "file":
            path = self.import_path(analysis, info)
            other = self.file_analysis(path) if path else None
            if other:
                for sym in other.top_level():
                    add(sym.name, sym.kind, sym.detail, sym.doc)
            return
        if analysis is not None:
            if receiver == "self":
                cls = analysis.class_at(tok)
                if cls:
                    self._class_members(analysis, cls, add, static=False)
                    return
            cls = analysis.class_named(receiver)
            if cls and (analysis.visible(receiver, tok) is cls):
                if cls.kind == "enum":
                    for child in cls.children:
                        add(child.name, "member", child.detail)
                    add("values", "method", "values() -> list")
                else:
                    self._class_members(analysis, cls, add, static=True)
                return
            kind = self.receiver_kind(analysis, receiver, tok)
            if kind and analysis.class_named(kind):
                self._class_members(analysis, analysis.class_named(kind), add, static=False)
                return
            if kind in ("text", "list", "map", "number", "bytes", "range"):
                for _k, entry in self.knowledge.methods(kind):
                    add(entry["name"], "method", entry["signature"], entry["doc"])
                return
        for k, entry in self.knowledge.methods():
            add(entry["name"], "method", f"({k}) {entry['signature']}", entry["doc"])

    def _class_members(self, analysis, cls, add, static: bool):
        seen_classes = set()
        while cls is not None and cls.name not in seen_classes:
            seen_classes.add(cls.name)
            for child in cls.children:
                if static and not child.detail.startswith("static") and child.kind == "field":
                    continue
                add(child.name, child.kind, child.detail, child.doc)
            base = getattr(cls.node, "base", None)
            base_name = getattr(base, "name", None)
            cls = analysis.class_named(base_name) if base_name else None

    # -- hover ------------------------------------------------------------------------------

    def on_textDocument_hover(self, params):
        uri, text, index, offset = self._context(params)
        analysis = self.analysis(uri)
        start, end = self._word_at(text, offset)
        word = text[start:end]
        if not word:
            return None
        found = self.describe(analysis, text, start, word)
        if not found:
            return None
        signature, doc = found
        value = f"```tb\n{signature}\n```" if signature else ""
        if doc:
            value += ("\n\n" if value else "") + doc
        return {"contents": {"kind": "markdown", "value": value}, "range": index.range(start, end)}

    def describe(self, analysis, text, start, word) -> tuple[str, str] | None:
        receiver = self._receiver(text, start)
        tok = analysis.token_at(start) if analysis else -1
        if receiver is not None:
            imports = analysis.imports if analysis else {}
            info = imports.get(receiver)
            lib_name = None
            if info and info["kind"] == "library":
                lib_name = info["target"]
            elif info is None and receiver in self.knowledge.library_names():
                lib_name = receiver
            if lib_name:
                lib = self.knowledge.library(lib_name)
                if lib:
                    for entry in lib["functions"]:
                        if entry["name"] == word:
                            return f"{lib_name}.{entry['signature']}", entry["doc"]
                    for entry in lib["values"]:
                        if entry["name"] == word:
                            return f"{lib_name}.{word} = {entry['value']}", entry["doc"]
                return None
            if info and info["kind"] == "file" and analysis:
                path = self.import_path(analysis, info)
                other = self.file_analysis(path) if path else None
                if other:
                    for sym in other.top_level():
                        if sym.name == word:
                            return sym.detail, sym.doc
                return None
            if analysis:
                for sym in analysis.symbols:
                    if sym.kind in ("field", "method", "member") and sym.name == word:
                        return f"{sym.container}.{sym.detail}" if sym.kind == "member" else sym.detail, sym.doc
            kind = self.receiver_kind(analysis, receiver, tok) if analysis else None
            for k, entry in self.knowledge.methods(kind if kind in ("text", "list", "map", "number", "bytes", "range") else None):
                if entry["name"] == word:
                    return f"({k}) {entry['signature']}", entry["doc"]
            return None
        if analysis:
            sym = analysis.visible(word, tok)
            if sym is not None:
                if sym.kind == "import":
                    info = analysis.imports.get(word, {})
                    if info.get("kind") == "library":
                        lib = self.knowledge.library(info["target"])
                        return f"import {info['target']}", (lib or {}).get("doc", "")
                    if info.get("kind") == "library-name":
                        lib = self.knowledge.library(info["target"])
                        for entry in (lib or {}).get("functions", []):
                            if entry["name"] == info["name"]:
                                return f"{info['target']}.{entry['signature']}", entry["doc"]
                return sym.detail, sym.doc
        if word in KEYWORD_DOCS:
            return word, KEYWORD_DOCS[word]
        entry = self.knowledge.builtin(word)
        if entry:
            return entry.get("signature", word), entry.get("doc", "")
        if word in self.knowledge.library_names():
            lib = self.knowledge.library(word)
            return f"import {word}", (lib or {}).get("doc", "")
        return None

    # -- signature help ------------------------------------------------------------------------

    def on_textDocument_signatureHelp(self, params):
        uri, text, index, offset = self._context(params)
        analysis = self.analysis(uri)
        depth = 0
        commas = 0
        k = offset - 1
        in_string = None
        while k >= 0:
            ch = text[k]
            if ch == "\n" and depth == 0 and k < offset - 2000:
                return None
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                if depth == 0:
                    if ch != "(":
                        return None
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                commas += 1
            k -= 1
        if k < 0:
            return None
        name_end = k
        while name_end > 0 and text[name_end - 1] in " \t":
            name_end -= 1
        a, b = self._word_at(text, name_end)
        word = text[a:name_end]
        if not word:
            return None
        found = self.describe(analysis, text, a, word)
        if not found or "(" not in found[0]:
            return None
        signature, doc = found
        label = signature
        inside = label[label.index("(") + 1:label.rfind(")")] if ")" in label else ""
        params_out = []
        depth = 0
        current = ""
        for ch in inside:
            if ch in "([{<":
                depth += 1
            elif ch in ")]}>":
                depth -= 1
            if ch == "," and depth == 0:
                params_out.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            params_out.append(current.strip())
        active = min(commas, max(0, len(params_out) - 1))
        for i, p in enumerate(params_out):
            if p.startswith("...") and commas >= i:
                active = i
                break
        return {"signatures": [{"label": label, "documentation": {"kind": "markdown", "value": doc},
                                "parameters": [{"label": p} for p in params_out]}],
                "activeSignature": 0, "activeParameter": active}

    # -- definition -----------------------------------------------------------------------------

    def _location(self, analysis: Analysis, sym: Symbol) -> dict | None:
        span = analysis.tok_offsets(sym.tok)
        if span is None:
            return None
        uri = path_to_uri(analysis.path) if analysis.path else None
        return {"uri": uri, "range": analysis.index.range(*span)}

    def on_textDocument_definition(self, params):
        uri, text, index, offset = self._context(params)
        analysis = self.analysis(uri)
        if analysis is None:
            return None
        start, end = self._word_at(text, offset)
        word = text[start:end]
        tok = analysis.token_at(start)
        if 0 <= tok < len(analysis.tokens) and analysis.tokens[tok].kind == "STRING":
            # on the "file.tb" of an import
            for info in analysis.imports.values():
                if info.get("kind") in ("file", "file-name"):
                    if info["target"].startswith(analysis.tokens[tok].value):
                        path = self.import_path(analysis, info)
                        if path:
                            return {"uri": path_to_uri(path), "range": {"start": {"line": 0, "character": 0},
                                                                        "end": {"line": 0, "character": 0}}}
            return None
        if not word:
            return None
        receiver = self._receiver(text, start)
        here_uri = uri

        def located(an, sym):
            loc = self._location(an, sym)
            if loc and loc["uri"] is None:
                loc["uri"] = here_uri
            return loc

        if receiver is not None:
            info = analysis.imports.get(receiver)
            if info and info["kind"] == "file":
                path = self.import_path(analysis, info)
                other = self.file_analysis(path) if path else None
                if other:
                    for sym in other.top:
                        if sym.name == word:
                            return located(other, sym)
                return None
            members = [s for s in analysis.symbols if s.kind in ("field", "method", "member") and s.name == word]
            if receiver == "self":
                cls = analysis.class_at(tok)
                if cls:
                    own = [s for s in members if s.container == cls.name]
                    members = own or members
            else:
                cls = analysis.class_named(receiver)
                if cls:
                    members = [s for s in members if s.container == cls.name] or members
            if members:
                return located(analysis, members[0])
            return None
        sym = analysis.visible(word, tok)
        if sym is None:
            return None
        if sym.kind == "import":
            info = analysis.imports.get(word, {})
            if info.get("kind") == "file-name":
                path = self.import_path(analysis, info)
                other = self.file_analysis(path) if path else None
                if other:
                    for target in other.top:
                        if target.name == info["name"]:
                            return located(other, target)
            if info.get("kind") == "file":
                path = self.import_path(analysis, info)
                if path:
                    return {"uri": path_to_uri(path), "range": {"start": {"line": 0, "character": 0},
                                                                "end": {"line": 0, "character": 0}}}
        return located(analysis, sym)

    # -- outline -----------------------------------------------------------------------------------

    def on_textDocument_documentSymbol(self, params):
        uri = params["textDocument"]["uri"]
        analysis = self.analysis(uri)
        if analysis is None:
            return []

        def convert(sym: Symbol) -> dict | None:
            span = analysis.tok_offsets(sym.tok)
            if span is None:
                return None
            sel = analysis.index.range(*span)
            end_line = max(sym.end_line or sym.line, sym.line)
            end_range = analysis.index.line_range(end_line - 1)["end"]
            full = {"start": analysis.index.line_range(sym.line - 1)["start"], "end": end_range}
            if (full["end"]["line"], full["end"]["character"]) < (sel["end"]["line"], sel["end"]["character"]):
                full["end"] = sel["end"]
            if (full["start"]["line"], full["start"]["character"]) > (sel["start"]["line"], sel["start"]["character"]):
                full["start"] = sel["start"]
            item = {"name": sym.name, "kind": SYMBOL_KIND.get(sym.kind, 13), "range": full,
                    "selectionRange": sel}
            if sym.detail:
                item["detail"] = sym.detail
            children = [c for c in (convert(ch) for ch in sym.children) if c]
            if children:
                item["children"] = children
            return item

        return [s for s in (convert(sym) for sym in analysis.top) if s]

    # -- formatting --------------------------------------------------------------------------------

    def on_textDocument_formatting(self, params):
        from tbtools.fmt import FormatError, format_source
        from tbcore.errors import TBError

        uri = params["textDocument"]["uri"]
        text = self.docs.get(uri)
        if text is None:
            return None
        try:
            formatted = format_source(text)
        except (TBError, FormatError):
            return None
        if formatted == text:
            return []
        index = TextIndex(text)
        return [{"range": index.range(0, len(text)), "newText": formatted}]

    # -- rename --------------------------------------------------------------------------------------

    def _rename_targets(self, uri, params):
        analysis = self.analysis(uri)
        text = self.docs.get(uri, "")
        if analysis is None or analysis.text != text:
            return None, None, None
        index = analysis.index
        offset = index.offset(params["position"])
        start, end = self._word_at(text, offset)
        word = text[start:end]
        tok = analysis.token_at(start)
        if not word or tok < 0 or analysis.tokens[tok].kind != "IDENT" or analysis.tokens[tok].value != word:
            return None, None, None
        member = tok > 0 and analysis.tokens[tok - 1].kind == "OP" and analysis.tokens[tok - 1].value == "."
        spans = []
        for k, t in enumerate(analysis.tokens):
            if t.kind != "IDENT" or t.value != word:
                continue
            after_dot = k > 0 and analysis.tokens[k - 1].kind == "OP" and analysis.tokens[k - 1].value == "."
            if after_dot != member and not (member is False and self._is_member_decl(analysis, k)):
                continue
            span = analysis.tok_offsets(k)
            if span:
                spans.append(span)
        return analysis, word, spans

    def _is_member_decl(self, analysis, k) -> bool:
        return False

    def on_textDocument_prepareRename(self, params):
        uri = params["textDocument"]["uri"]
        analysis, word, spans = self._rename_targets(uri, params)
        if not spans:
            return None
        offset = analysis.index.offset(params["position"])
        for a, b in spans:
            if a <= offset <= b:
                return {"range": analysis.index.range(a, b), "placeholder": word}
        return None

    def on_textDocument_rename(self, params):
        from tbcore.lexer import KEYWORDS
        from tbcore.parser import NAME_RE

        uri = params["textDocument"]["uri"]
        new_name = params.get("newName", "")
        if not NAME_RE.fullmatch(new_name) or new_name in KEYWORDS:
            raise ValueError(f"'{new_name}' can't be a TB name.")
        analysis, word, spans = self._rename_targets(uri, params)
        if not spans:
            return None
        edits = [{"range": analysis.index.range(a, b), "newText": new_name} for a, b in spans]
        return {"changes": {uri: edits}}


def main(args: list[str] | None = None) -> int:
    args = list(args or [])
    if any(a in ("-h", "--help") for a in args):
        print("tb lsp - the TB language server. Editors start it and talk to it on stdin/stdout "
              "(LSP 3.17). Options: --stdio (the default).")
        return 0
    return Server().serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
