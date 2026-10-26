"""fs.watch / fs.watch_stop (tblib/_watch.py): polling watcher on a temp folder."""

import json
import time

import pytest

import tblib._watch as watchlib
import tblib.fs as fslib
from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def fs_with_watch(monkeypatch):
    """Until fs.py calls _watch.install itself, add it here (install is idempotent)."""
    original = fslib.load
    monkeypatch.setattr(fslib, "load", lambda api: watchlib.install(original(api), api))
    yield
    watchlib.stop_all()


def test_snapshot_and_changes(tmp_path):
    (tmp_path / "a.log").write_text("1")
    before = watchlib.snapshot(str(tmp_path))
    (tmp_path / "a.log").write_text("12")
    (tmp_path / "b.log").write_text("x")
    after = watchlib.snapshot(str(tmp_path))
    kinds = [(e["kind"], e["path"].rsplit("/", 1)[-1]) for e in watchlib.changes(before, after)]
    assert kinds == [("changed", "a.log"), ("created", "b.log")]
    assert watchlib.changes(after, {}) [0]["kind"] == "deleted"


def test_watch_folder_reports_created_changed_deleted(tmp_path):
    d = json.dumps(str(tmp_path))
    out = run_tb(f'''
import fs
let seen = []
let w = fs.watch({d}, fn(e) {{ seen.push(e.kind + " " + fs.name(e.path)) }}, 0.05)
say(w.running)
fs.write(fs.join({d}, "new.txt"), "hello")
w.wait(0.3)
fs.write(fs.join({d}, "new.txt"), "hello again")
w.wait(0.3)
fs.remove(fs.join({d}, "new.txt"))
w.wait(0.3)
w.stop()
say(w.running)
for s in seen {{ say(s) }}
''')
    assert out[:2] == ["true", "false"]
    assert out[2:] == ["created new.txt", "changed new.txt", "deleted new.txt"]


def test_returning_false_stops_and_errors_are_not_fatal(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("a")
    p = json.dumps(str(f))
    out = run_tb(f'''
import fs
let w = fs.watch({p}, fn(e) {{ return false }}, 0.05)
fs.write({p}, "bb")
say(w.wait(2))
let w2 = fs.watch({p}, fn(e) {{ throw "handler broke" }}, 0.05)
fs.write({p}, "ccc")
w2.wait(0.4)
fs.watch_stop()
say(w2.running)
''')
    assert out[0] == "true"
    assert "fs.watch: handler broke" in out
    assert out[-1] == "false"


def test_watch_errors_are_friendly(tmp_path):
    assert "couldn't find" in tb_error('import fs\nfs.watch("no/such/place", fn(e) { })').message
    assert "interval" in tb_error(f'import fs\nfs.watch({json.dumps(str(tmp_path))}, fn(e) {{ }}, 0)').message
    assert "made by fs.watch" in tb_error('import fs\nfs.watch_stop({"a": 1})').message
