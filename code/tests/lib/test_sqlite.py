"""sqlite library: in-memory and file databases, friendly SQL errors."""

import pytest

import tb
from conftest import run_tb, tb_error

SETUP = '''import sqlite
let db = sqlite.open()
db.run("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, age INTEGER)")
db.run("INSERT INTO users (name, age) VALUES (?, ?)", ["Tanisha", 29])
db.run("INSERT INTO users (name, age) VALUES (:name, :age)", {"name": "Sam", "age": 41})
'''


def test_run_all_one_value():
    src = SETUP + '''
let r = db.run("INSERT INTO users (name, age) VALUES (?, ?)", ["Priya", 35])
say(r.changes, r.last_id)
say(db.all("SELECT name, age FROM users ORDER BY id"))
say(db.one("SELECT name FROM users WHERE age > ?", [40]))
say(db.one("SELECT name FROM users WHERE age > 100"))
say(db.value("SELECT count(*) FROM users"), db.value("SELECT avg(age) FROM users"))
say(db.tables(), db.columns("users"))
'''
    assert run_tb(src) == [
        "1 3",
        '[{"name": "Tanisha", "age": 29}, {"name": "Sam", "age": 41}, {"name": "Priya", "age": 35}]',
        '{"name": "Sam"}',
        "nil",
        "3 35",
        '["users"] ["id", "name", "age"]',
    ]


def test_execute_many_and_script_and_schema():
    src = SETUP + '''
say(db.execute_many("INSERT INTO users (name, age) VALUES (?, ?)", [["A", 1], ["B", 2], ["C", 3]]))
say(db.execute_many("INSERT INTO users (name, age) VALUES (?, ?)", []))
db.script("CREATE TABLE a (x); CREATE TABLE b (y); INSERT INTO a VALUES (1)")
say(db.tables(), db.value("SELECT x FROM a"))
say(len(db.schema()), db.schema("a")[0])
'''
    assert run_tb(src) == ["3", "0", '["a", "b", "users"] 1', "3 CREATE TABLE a (x)"]


def test_transaction_commits_and_rolls_back():
    src = SETUP + '''
let got = db.transaction(fn(d) {
  d.run("INSERT INTO users (name) VALUES ('X')")
  return d.value("SELECT count(*) FROM users")
})
say(got, db.value("SELECT count(*) FROM users"))
try {
  db.transaction(fn(d) {
    d.run("INSERT INTO users (name) VALUES ('Y')")
    throw "stop"
  })
} catch err {
  say("caught", err.message)
}
say(db.value("SELECT count(*) FROM users"))
'''
    assert run_tb(src) == ["3 3", "caught stop", "3"]


def test_file_database_persists(tmp_path):
    path = str(tmp_path / "data.db").replace("\\", "/")
    src = f'''import sqlite
let db = sqlite.open("{path}")
db.run("CREATE TABLE IF NOT EXISTS notes (text TEXT)")
db.run("INSERT INTO notes VALUES (?)", ["hello"])
db.close()
say(db.is_open())
let again = sqlite.open("{path}")
say(again.value("SELECT text FROM notes"))
let ro = sqlite.open("{path}", {{"readonly": true}})
say(ro.value("SELECT count(*) FROM notes"))
try {{ ro.run("DELETE FROM notes") }} catch err {{ say(err.reason) }}
'''
    assert run_tb(src) == ["false", "hello", "1", "readonly"]


def test_values_round_trip():
    src = '''import sqlite
let db = sqlite.open()
db.run("CREATE TABLE t (a, b, c, d, e)")
db.run("INSERT INTO t VALUES (?, ?, ?, ?, ?)", [1.5, true, nil, "x", [1, 2]])
say(db.one("SELECT * FROM t"))
say(db.value("SELECT 2.0"))
'''
    assert run_tb(src) == ['{"a": 1.5, "b": 1, "c": nil, "d": "x", "e": "[1, 2]"}', "2"]


@pytest.mark.parametrize("sql,message,reason", [
    ('db.all("SELECT * FROM nope")', "There is no table called 'nope'.", "no_table"),
    ('db.all("SELECT zzz FROM users")', "There is no column called 'zzz'.", "no_column"),
    ('db.run("SELEC 1")', 'The SQL has a mistake near "SELEC".', "syntax"),
    ('db.run("INSERT INTO users (name) VALUES (?)", ["Tanisha"])', "already there (UNIQUE rule)", "unique"),
    ('db.run("INSERT INTO users (age) VALUES (1)")', "must not be empty (NOT NULL rule)", "not_null"),
    ('db.run("INSERT INTO users (name, age) VALUES (?, ?)", ["Z"])', "don't match", "params"),
    ('db.run("CREATE TABLE users (x)")', "The table 'users' already exists.", "exists"),
    ('db.columns("nope")', "There is no table called 'nope'.", "no_table"),
])
def test_friendly_sql_errors(sql, message, reason):
    err = tb_error(SETUP + sql)
    assert message in str(err)
    assert err.fields["reason"] == reason


def test_type_errors_and_closed():
    assert "db.run() needs sql to be text" in str(tb_error(SETUP + "db.run(5)"))
    assert "needs params to be a list or a map" in str(tb_error(SETUP + 'db.run("SELECT 1", 5)'))
    assert "can't store function values" in str(tb_error(SETUP + 'db.run("SELECT ?", [fn() {}])'))
    err = tb_error(SETUP + 'db.close()\ndb.all("SELECT 1")')
    assert "this database was closed" in str(err) and err.fields["reason"] == "closed"
    assert "one SQL statement at a time" in str(tb_error(SETUP + 'db.run("SELECT 1; SELECT 2")'))
    assert "is a folder, not a database file" in str(tb_error('import sqlite\nsqlite.open(".")'))
    assert "I couldn't find 'missing.db'" in str(tb_error('import sqlite\nsqlite.open("missing.db", {"readonly": true})'))


def test_helpers_and_docs():
    out = run_tb('import sqlite\nsay(sqlite.escape("it\'s"), sqlite.quote_name("my table"), sqlite.MEMORY)')
    assert out == ["it''s \"my table\" :memory:"]
    info = tb.describe_library("sqlite")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
    db_doc = run_tb('import sqlite\nlet db = sqlite.open()\nsay(db.run)')
    assert db_doc == ["<built-in fn db.run>"]
