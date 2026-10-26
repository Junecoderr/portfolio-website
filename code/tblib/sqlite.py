"""sqlite: a real database in one file (or in memory), with friendly errors."""

from __future__ import annotations

import os
import re
import sqlite3
import threading

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_ROWS = 1_000_000          # all() refuses to build a bigger list than this
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def friendly_sql_error(err: Exception, sql: str | None = None) -> tuple[str, str]:
    """(message, reason) for an sqlite3 error, in plain English."""
    text = str(err)
    low = text.lower()
    tail = f' SQL: {sql.strip()}' if sql else ""
    m = re.match(r"no such table: (.+)", text)
    if m:
        return f"There is no table called '{m.group(1)}'.{tail}", "no_table"
    m = re.match(r"no such column: (.+)", text)
    if m:
        return f"There is no column called '{m.group(1)}'.{tail}", "no_column"
    m = re.match(r"table (.+) already exists", text)
    if m:
        return f"The table '{m.group(1)}' already exists. Use CREATE TABLE IF NOT EXISTS to skip it.", "exists"
    m = re.match(r'near "(.*)": syntax error', text)
    if m:
        return f"The SQL has a mistake near \"{m.group(1)}\".{tail}", "syntax"
    if "incomplete input" in low:
        return f"The SQL stops too early (incomplete input).{tail}", "syntax"
    if "unique constraint failed" in low:
        cols = text.split(":", 1)[1].strip() if ":" in text else ""
        return f"A row with that {cols or 'value'} is already there (UNIQUE rule).", "unique"
    if "not null constraint failed" in low:
        cols = text.split(":", 1)[1].strip() if ":" in text else "a column"
        return f"{cols} must not be empty (NOT NULL rule).", "not_null"
    if "foreign key constraint failed" in low:
        return "That row points at a row that doesn't exist (FOREIGN KEY rule).", "foreign_key"
    if "check constraint failed" in low:
        return f"A CHECK rule of the table rejected the row: {text}.", "check"
    if "database is locked" in low:
        return "The database is busy (locked by another program or connection). Try again in a moment.", "locked"
    if "readonly" in low or "read-only" in low:
        return "The database file is read-only, so it can't be changed.", "readonly"
    if "unable to open database file" in low:
        return "I couldn't open the database file. Does its folder exist, and can you write there?", "open"
    if "file is not a database" in low or "not a database" in low:
        return "That file isn't an SQLite database.", "not_database"
    if "wrong number of bindings" in low or "incorrect number of bindings" in low:
        return f"The SQL and its parameters don't match: {text}.{tail}", "params"
    if "you did not supply a value for binding" in low or "binding parameter" in low:
        return f"A named parameter is missing: {text}.{tail}", "params"
    if "no such function" in low:
        return f"SQLite doesn't have that function: {text}.{tail}", "no_function"
    if "one statement at a time" in low:
        return f"This runs one SQL statement at a time; use db.script() for several.{tail}", "sql"
    if "closed database" in low:
        return "This database was closed. Open it again to use it.", "closed"
    if isinstance(err, sqlite3.ProgrammingError) and "supported type" in low:
        return f"A parameter has a type SQLite can't store: {text}.", "params"
    return f"SQLite said: {text}.{tail}", "sql"


def to_sql_value(value, line, fn):
    """A TB value as something sqlite3 can bind."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return int(value) if isinstance(value, bool) else value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, (list, dict)):
        from tblib import json as _json  # lists and maps are stored as JSON text
        return _json.dump(value, line)
    fail(line, f"{fn}() can't store {_tb.type_name(value)} values in the database; use text, numbers, true/false or nil.")


def from_sql_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, float):
        return _tb.tidy_number(value)
    return value


def load(api):
    m = Module(
        "sqlite",
        "A real database in a single file (or in memory) using SQLite, which is built into Python. "
        "sqlite.open(path) gives a db with run(), all(), one(), value(), tables(), columns(), "
        "execute_many(), transaction() and close(). Use ? or :name placeholders for values; "
        "never paste values into the SQL text yourself.",
    )

    def method(obj, name, lo, hi, func, doc):
        obj[name] = _tb.Builtin(f"db.{name}", func, (lo, hi), doc)

    def params_arg(line, params, fn):
        if params is None:
            return []
        if isinstance(params, list):
            return [to_sql_value(v, line, fn) for v in params]
        if isinstance(params, dict):
            return {str(k): to_sql_value(v, line, fn) for k, v in params.items()}
        check_type(line, params, "list|map", fn, "params")

    def make_db(line, path, conn):
        state = {"open": True, "lock": threading.RLock(), "in_transaction": False}
        db = {"path": path}

        def need_open(line, fn):
            if not state["open"]:
                fail(line, f"db.{fn}() can't run: this database was closed. Open it again with sqlite.open().",
                     reason="closed")

        def sql_arg(line, sql, fn):
            check_type(line, sql, "text", f"db.{fn}", "sql")
            if not sql.strip():
                fail(line, f"db.{fn}() needs some SQL, but got empty text.")
            return sql

        def execute(line, fn, sql, params, many=False):
            need_open(line, fn)
            try:
                with state["lock"]:
                    cur = conn.cursor()
                    if many:
                        cur.executemany(sql, params)
                    else:
                        cur.execute(sql, params)
                    if not state["in_transaction"]:
                        conn.commit()
                    return cur
            except (sqlite3.Error, OverflowError) as err:
                message, reason = friendly_sql_error(err, sql)
                fail(line, message, reason=reason, sql=sql)

        def rows_of(cur, limit=None):
            names = [d[0] for d in cur.description] if cur.description else []
            out = []
            for row in cur:
                out.append({name: from_sql_value(v) for name, v in zip(names, row)})
                if limit is not None and len(out) >= limit:
                    break
                if len(out) > MAX_ROWS:
                    fail(None, f"db.all() would give back more than {MAX_ROWS:,} rows; add a LIMIT.")
            return out

        @_tb_doc("run(sql, params?) -> map\nRun a statement that changes things (CREATE, INSERT, UPDATE, DELETE). "
                 "params is a list for ? placeholders or a map for :name ones. Gives {\"changes\": rows affected, "
                 "\"last_id\": the new row's id after an INSERT}.")
        def run(line, sql, params=None):
            sql = sql_arg(line, sql, "run")
            cur = execute(line, "run", sql, params_arg(line, params, "db.run"))
            return {"changes": max(cur.rowcount, 0), "last_id": cur.lastrowid}

        @_tb_doc("all(sql, params?) -> list\nRun a SELECT and give every row as a map of column name -> value.")
        def all_(line, sql, params=None):
            sql = sql_arg(line, sql, "all")
            return rows_of(execute(line, "all", sql, params_arg(line, params, "db.all")))

        @_tb_doc("one(sql, params?) -> map\nThe first row of a SELECT as a map, or nil when there is none.")
        def one(line, sql, params=None):
            sql = sql_arg(line, sql, "one")
            rows = rows_of(execute(line, "one", sql, params_arg(line, params, "db.one")), limit=1)
            return rows[0] if rows else None

        @_tb_doc("value(sql, params?) -> any\nThe first column of the first row, or nil: "
                 "db.value(\"SELECT count(*) FROM users\").")
        def value(line, sql, params=None):
            sql = sql_arg(line, sql, "value")
            cur = execute(line, "value", sql, params_arg(line, params, "db.value"))
            row = cur.fetchone()
            return from_sql_value(row[0]) if row else None

        @_tb_doc("execute_many(sql, rows) -> number\nRun one statement for every item in rows (each a list or map "
                 "of params), all in one go: db.execute_many(\"INSERT INTO t VALUES (?, ?)\", rows). Gives the "
                 "number of rows affected.")
        def execute_many(line, sql, rows):
            sql = sql_arg(line, sql, "execute_many")
            check_type(line, rows, "list", "db.execute_many", "rows")
            params = [params_arg(line, r, "db.execute_many") for r in rows]
            if not params:
                return 0
            cur = execute(line, "execute_many", sql, params, many=True)
            return max(cur.rowcount, 0)

        @_tb_doc("script(sql)\nRun several statements separated by semicolons (for setting up tables).")
        def script(line, sql):
            sql = sql_arg(line, sql, "script")
            need_open(line, "script")
            try:
                with state["lock"]:
                    conn.executescript(sql)
                    if not state["in_transaction"]:
                        conn.commit()
            except sqlite3.Error as err:
                message, reason = friendly_sql_error(err, sql)
                fail(line, message, reason=reason, sql=sql)
            return None

        @_tb_doc("tables() -> list\nThe names of the tables in the database (sorted).")
        def tables(line):
            cur = execute(line, "tables", "SELECT name FROM sqlite_master WHERE type = 'table' "
                          "AND name NOT LIKE 'sqlite_%' ORDER BY name", [])
            return [row[0] for row in cur]

        @_tb_doc("columns(table) -> list\nThe column names of a table, in order. Error if the table doesn't exist.")
        def columns(line, table):
            check_type(line, table, "text", "db.columns", "table")
            if not NAME.match(table):
                fail(line, f"db.columns() needs a plain table name, not '{table}'.")
            cur = execute(line, "columns", f'PRAGMA table_info("{table}")', [])
            names = [row[1] for row in cur]
            if not names:
                fail(line, f"There is no table called '{table}'.", reason="no_table")
            return names

        @_tb_doc("schema(table?) -> list\nThe CREATE statements of the database (or of one table), as text.")
        def schema(line, table=None):
            if table is None:
                cur = execute(line, "schema", "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name", [])
            else:
                check_type(line, table, "text", "db.schema", "table")
                cur = execute(line, "schema", "SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL",
                              [table])
            return [row[0] for row in cur]

        @_tb_doc("transaction(fn) -> any\nRun fn(db) with every change saved together at the end; if fn throws "
                 "an error, all its changes are undone (rolled back) and the error is passed on. Gives what fn gives.")
        def transaction(line, fn):
            check_type(line, fn, "function", "db.transaction", "fn")
            need_open(line, "transaction")
            if state["in_transaction"]:
                fail(line, "db.transaction() is already running; SQLite can't nest transactions.")
            with state["lock"]:
                try:
                    conn.execute("BEGIN")
                except sqlite3.Error as err:
                    message, reason = friendly_sql_error(err)
                    fail(line, message, reason=reason)
                state["in_transaction"] = True
                try:
                    result = api.call(fn, [db], line)
                except BaseException:
                    state["in_transaction"] = False
                    try:
                        conn.rollback()
                    except sqlite3.Error:
                        pass
                    raise
                state["in_transaction"] = False
                try:
                    conn.commit()
                except sqlite3.Error as err:
                    conn.rollback()
                    message, reason = friendly_sql_error(err)
                    fail(line, message, reason=reason)
            return result

        @_tb_doc("close()\nClose the database. Changes are already saved after every run(); closing frees the file.")
        def close(line):
            if state["open"]:
                state["open"] = False
                with state["lock"]:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
            return None

        @_tb_doc("is_open() -> bool\nTrue until close() is called.")
        def is_open(line):
            return state["open"]

        for name, lo, hi, func in [
            ("run", 1, 2, run), ("all", 1, 2, all_), ("one", 1, 2, one), ("value", 1, 2, value),
            ("execute_many", 2, 2, execute_many), ("script", 1, 1, script), ("tables", 0, 0, tables),
            ("columns", 1, 1, columns), ("schema", 0, 1, schema), ("transaction", 1, 1, transaction),
            ("close", 0, 0, close), ("is_open", 0, 0, is_open),
        ]:
            method(db, name, lo, hi, func, func.__tb_doc__)
        return db

    @m.fn("open", 0, 2, doc=(
        "open(path?, options?) -> db\n"
        "Open (or create) a database file. Without a path, or with \":memory:\", the database lives in memory "
        "and vanishes when the program ends. options: {\"readonly\": true} to refuse changes. "
        "The db has run, all, one, value, execute_many, script, tables, columns, schema, transaction, close."))
    def open_(line, path=":memory:", options=None):
        check_type(line, path, "text", "sqlite.open", "path")
        if options is not None:
            check_type(line, options, "map", "sqlite.open", "options")
        readonly = bool(options and options.get("readonly"))
        if path.strip() == "":
            path = ":memory:"
        target = path
        if path != ":memory:":
            target = os.path.expanduser(path)
            if os.path.isdir(target):
                fail(line, f"'{path}' is a folder, not a database file.", path=path)
            if readonly and not os.path.exists(target):
                fail(line, f"I couldn't find '{path}'. Is the name spelled right?", path=path)
        try:
            if readonly and target != ":memory:":
                from urllib.request import pathname2url
                conn = sqlite3.connect(f"file:{pathname2url(os.path.abspath(target))}?mode=ro", uri=True,
                                       check_same_thread=False)
            else:
                conn = sqlite3.connect(target, check_same_thread=False)
            conn.isolation_level = None  # we manage transactions ourselves
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as err:
            message, reason = friendly_sql_error(err)
            fail(line, f"I couldn't open the database '{path}': {message}", reason=reason, path=path)
        except OSError as err:
            fail_os(line, err, path)
        return make_db(line, path, conn)

    @m.fn("escape", 1, doc=(
        "escape(text) -> text\n"
        "Text made safe to put inside single quotes in SQL (doubles the quotes). Prefer ? placeholders."))
    def escape(line, value):
        check_type(line, value, "text", "sqlite.escape", "text")
        return value.replace("'", "''")

    @m.fn("quote_name", 1, doc=(
        "quote_name(name) -> text\n"
        "A table or column name quoted for SQL: quote_name(\"my table\") is \"\\\"my table\\\"\"."))
    def quote_name(line, value):
        check_type(line, value, "text", "sqlite.quote_name", "name")
        return '"' + value.replace('"', '""') + '"'

    m.value("version", sqlite3.sqlite_version, doc="The version of the SQLite engine, like \"3.45.1\".")
    m.value("MEMORY", ":memory:", doc="The path that means \"a database in memory\".")
    return m


def _tb_doc(doc):
    def mark(func):
        func.__tb_doc__ = doc
        return func
    return mark
