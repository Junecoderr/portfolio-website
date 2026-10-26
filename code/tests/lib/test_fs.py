import os

import pytest

from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_write_read_append_lines(tmp_path):
    src = 'import fs\nfs.write("a.txt", "one\\n")\nfs.append("a.txt", "two\\n")\nsay(fs.read("a.txt") == "one\\ntwo\\n", fs.lines("a.txt"))\nfs.write("n.txt", 42)\nsay(fs.read("n.txt"))'
    assert run_tb(src) == ['true ["one", "two"]', "42"]
    assert (tmp_path / "a.txt").read_bytes() == b"one\ntwo\n"


def test_read_accepts_bom_and_utf16(tmp_path):
    (tmp_path / "bom.txt").write_bytes(b"\xef\xbb\xbfhello")
    (tmp_path / "u16.txt").write_text("café", encoding="utf-16")
    assert run_tb('import fs\nsay(fs.read("bom.txt"), fs.read("u16.txt"))') == ["hello café"]


def test_exists_is_dir_is_file_size_modified(tmp_path):
    (tmp_path / "f.txt").write_text("12345")
    (tmp_path / "d").mkdir()
    src = 'import fs\nsay(fs.exists("f.txt"), fs.exists("nope"), fs.is_dir("d"), fs.is_dir("f.txt"), fs.is_file("f.txt"), fs.size("f.txt"))\nsay(fs.modified("f.txt") > 0)'
    assert run_tb(src) == ["true false true false true 5", "true"]


def test_list_walk_mkdir(tmp_path):
    src = 'import fs\nfs.mkdir("x/y/z")\nfs.mkdir("x/y/z")\nfs.write("x/b.txt", "")\nfs.write("x/y/a.txt", "")\nsay(fs.list("x"))\nsay(fs.walk("x"))'
    out = run_tb(src)
    assert out[0] == '["b.txt", "y"]'
    shown = [os.path.join("x", "b.txt"), os.path.join("x", "y", "a.txt")]
    # Inside a list, text is shown the way you'd type it, so \ is doubled.
    assert out[1] == "[" + ", ".join('"' + p.replace("\\", "\\\\") + '"' for p in shown) + "]"
    assert run_tb('import fs\nsay(len(fs.list()) > 0)') == ["true"]


def test_copy_and_remove(tmp_path):
    src = 'import fs\nfs.write("a.txt", "hi")\nfs.mkdir("dir")\nfs.copy("a.txt", "b.txt")\nfs.copy("a.txt", "dir")\nsay(fs.read("b.txt"), fs.read("dir/a.txt"))\nfs.remove("a.txt")\nsay(fs.exists("a.txt"))'
    assert run_tb(src) == ["hi hi", "false"]


def test_paths(tmp_path):
    src = 'import fs\nsay(fs.join("logs", "auth.log"), fs.name("logs/auth.log"), fs.ext("auth.log"), fs.ext("README"), fs.parent("logs/auth.log"), fs.name("logs/"))'
    assert run_tb(src) == [f'{os.path.join("logs", "auth.log")} auth.log .log  logs logs']
    assert run_tb("import fs\nsay(fs.cwd() == fs.here(), fs.home() != \"\")") == ["true true"]


def test_here_is_next_to_the_script(tmp_path):
    import tb
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "data.txt").write_text("next to me")
    lines = []
    tb.run('import fs\nsay(fs.read(fs.here("data.txt")))', output=lines.append, path=str(tmp_path / "app" / "main.tb"))
    assert lines == ["next to me"]


@pytest.mark.parametrize("src,message", [
    ('fs.read("missing.txt")', "I couldn't find 'missing.txt'. Is the name spelled right?"),
    ('fs.read("d")', "'d' is a folder, not a file."),
    ("fs.read(5)", "fs.read() needs path to be text, not a number."),
    ('fs.read("")', "fs.read() needs a path, but got empty text."),
    ('fs.remove("d")', "fs.remove() only deletes files, and 'd' is a folder."),
    ('fs.remove("missing.txt")', "I couldn't find 'missing.txt'"),
    ('fs.list("missing")', "I couldn't find 'missing'"),
    ('fs.list("f.txt")', "'f.txt' is not a folder."),
    ('fs.walk("missing")', "I couldn't find the folder 'missing'."),
    ('fs.walk("f.txt")', "'f.txt' is a file, not a folder."),
    ('fs.mkdir("f.txt")', "'f.txt' already exists as a file."),
    ('fs.copy("missing.txt", "x")', "I couldn't find 'missing.txt'"),
    ('fs.copy("f.txt", "f.txt")', "can't copy a file onto itself"),
    ('fs.size("missing.txt")', "I couldn't find 'missing.txt'"),
    ('fs.write("d", "x")', "'d' is a folder, not a file."),
    ('fs.write("nofolder/x.txt", "x")', "I couldn't find 'nofolder/x.txt'"),
    ('fs.join("a", 1)', "fs.join() needs each part to be text"),
])
def test_fs_errors(tmp_path, src, message):
    (tmp_path / "d").mkdir()
    (tmp_path / "f.txt").write_text("x")
    err = tb_error(f"import fs\n{src}")
    assert message in str(err) and err.line == 2


def test_fs_error_map_has_path(tmp_path):
    assert run_tb('import fs\ntry { fs.read("gone.txt") } catch e { say(e.path, e.kind) }') == ["gone.txt not_found_error"]  # 3.0: typed errors
