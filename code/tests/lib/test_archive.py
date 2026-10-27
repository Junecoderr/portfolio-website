"""archive library: zip/tar/gzip round trips and safe extraction."""

import io
import json
import os
import tarfile
import zipfile

import pytest

import tb
from conftest import run_tb, tb_error


def p(path):
    return str(path).replace("\\", "/")


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("alpha")
    (src / "sub" / "b.txt").write_text("beta " * 100)
    (tmp_path / "loose.txt").write_text("loose")
    return tmp_path


def test_zip_unzip_list(tree):
    out = run_tb(f'''import archive
import fs
say(archive.zip("{p(tree / "out.zip")}", ["{p(tree / "src")}", "{p(tree / "loose.txt")}"]))
let names = archive.list("{p(tree / "out.zip")}").map(fn(e) {{ return e.name }})
say(names)
let e = archive.list("{p(tree / "out.zip")}").filter(fn(e) {{ return e.name == "src/sub/b.txt" }})[0]
say(e.size, e.compressed < e.size, e.is_dir, len(e.modified))
let written = archive.unzip("{p(tree / "out.zip")}", "{p(tree / "dest")}")
say(len(written))
say(fs.read("{p(tree / "dest" / "src" / "sub" / "b.txt")}").starts_with("beta"), fs.read("{p(tree / "dest" / "loose.txt")}"))
say(archive.kind("{p(tree / "out.zip")}"))
''')
    assert out == [
        "3",
        '["src/", "src/sub/", "src/a.txt", "src/sub/b.txt", "loose.txt"]',
        "500 true false 19",
        "3",
        "true loose",
        "zip",
    ]


def test_zip_base_and_members_and_overwrite(tree):
    out = run_tb(f'''import archive
import fs
archive.zip("{p(tree / "b.zip")}", ["{p(tree / "src" / "a.txt")}", "{p(tree / "src" / "sub")}"], {{"base": "{p(tree / "src")}"}})
say(archive.list("{p(tree / "b.zip")}").map(fn(e) {{ return e.name }}))
say(len(archive.unzip("{p(tree / "b.zip")}", "{p(tree / "d2")}", {{"members": ["a.txt"]}})))
fs.write("{p(tree / "d2" / "a.txt")}", "changed")
archive.unzip("{p(tree / "b.zip")}", "{p(tree / "d2")}", {{"overwrite": false}})
say(fs.read("{p(tree / "d2" / "a.txt")}"))
archive.unzip("{p(tree / "b.zip")}", "{p(tree / "d2")}")
say(fs.read("{p(tree / "d2" / "a.txt")}"))
''')
    assert out == ['["a.txt", "sub/", "sub/b.txt"]', "1", "changed", "alpha"]


def evil_zip(path, name):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, "evil")
        zf.writestr("fine.txt", "ok")


@pytest.mark.parametrize("name,why", [
    ("../evil.txt", "climb out"),
    ("/tmp/evil.txt", "absolute path"),
    ("sub/../../evil.txt", "climb out"),
    ("C:\\evil.txt", "absolute path"),
])
def test_zip_slip_is_refused(tmp_path, name, why):
    evil_zip(tmp_path / "evil.zip", name)
    err = tb_error(f'import archive\narchive.unzip("{p(tmp_path / "evil.zip")}", "{p(tmp_path / "out")}")')
    assert why in str(err) and err.fields["reason"] == "unsafe"
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "out" / "fine.txt").exists()
    out = run_tb(f'import archive\nsay(archive.unzip("{p(tmp_path / "evil.zip")}", "{p(tmp_path / "out")}", {{"safe": false}}))')
    # unzip gives the real, OS-native paths of the files it wrote
    assert len(out) == 1 and json.loads(out[0]) == [os.path.join(os.path.realpath(tmp_path), "out", "fine.txt")]
    assert not (tmp_path / "evil.txt").exists()


def test_zip_symlink_member_is_refused(tmp_path):
    with zipfile.ZipFile(tmp_path / "link.zip", "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    err = tb_error(f'import archive\narchive.unzip("{p(tmp_path / "link.zip")}", "{p(tmp_path / "out")}")')
    assert "is a link" in str(err)


def test_too_big_is_refused(tmp_path, monkeypatch):
    with zipfile.ZipFile(tmp_path / "big.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\0" * 200_000)
    err = tb_error(f'import archive\narchive.unzip("{p(tmp_path / "big.zip")}", "{p(tmp_path / "out")}", {{"max_size": 1000}})')
    assert "would unpack to more than" in str(err) and err.fields["reason"] == "too_big"


def test_tar_untar_gz(tree):
    out = run_tb(f'''import archive
import fs
say(archive.tar("{p(tree / "t.tar.gz")}", ["{p(tree / "src")}"]))
say(archive.kind("{p(tree / "t.tar.gz")}"), archive.list("{p(tree / "t.tar.gz")}").map(fn(e) {{ return e.name }}))
let w = archive.untar("{p(tree / "t.tar.gz")}", "{p(tree / "td")}")
say(len(w), fs.read("{p(tree / "td" / "src" / "a.txt")}"))
say(archive.tar("{p(tree / "plain.tar")}", "{p(tree / "loose.txt")}"), archive.kind("{p(tree / "plain.tar")}"))
say(len(archive.extract("{p(tree / "plain.tar")}", "{p(tree / "te")}")), fs.read("{p(tree / "te" / "loose.txt")}"))
''')
    assert out == ["2", 'tar ["src", "src/sub", "src/a.txt", "src/sub/b.txt"]', "2 alpha", "1 tar", "1 loose"]


def test_tar_slip_and_links_refused(tmp_path):
    path = tmp_path / "evil.tar"
    with tarfile.open(path, "w") as tf:
        data = b"evil"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    err = tb_error(f'import archive\narchive.untar("{p(path)}", "{p(tmp_path / "out")}")')
    assert "climb out" in str(err) and err.fields["member"] == "../evil.txt"
    link = tmp_path / "link.tar"
    with tarfile.open(link, "w") as tf:
        info = tarfile.TarInfo("escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc"
        tf.addfile(info)
        data = b"x"
        f = tarfile.TarInfo("escape/x.txt")
        f.size = 1
        tf.addfile(f, io.BytesIO(data))
    err = tb_error(f'import archive\narchive.untar("{p(link)}", "{p(tmp_path / "out2")}")')
    assert "is a link" in str(err)
    out = run_tb(f'import archive\nsay(len(archive.untar("{p(link)}", "{p(tmp_path / "out3")}", {{"safe": false}})))')
    assert out == ["1"]
    assert not os.path.islink(tmp_path / "out3" / "escape")


def test_gzip_gunzip(tree):
    out = run_tb(f'''import archive
import fs
let g = archive.gzip("{p(tree / "loose.txt")}")
say(fs.name(g), archive.kind(g), fs.size(g) > 0)
fs.remove("{p(tree / "loose.txt")}")
say(fs.name(archive.gunzip(g)), fs.read("{p(tree / "loose.txt")}"))
say(fs.name(archive.gunzip(g, "{p(tree / "copy.txt")}")))
say(archive.extract(g, "{p(tree / "gd")}").map(fn(x) {{ return fs.name(x) }}))
''')
    assert out == ["loose.txt.gz gzip true", "loose.txt loose", "copy.txt", '["loose.txt"]']


@pytest.mark.parametrize("src,message", [
    ('archive.zip("x.zip", [])', "needs at least one file or folder"),
    ('archive.zip("x.zip", ["nope.txt"])', "I couldn't find 'nope.txt' to add"),
    ('archive.zip("x.zip", ["."], {"flat": true})', 'doesn\'t know the option "flat"'),
    ('archive.unzip("nope.zip", "out")', "I couldn't find 'nope.zip'"),
    ('archive.list("nope.zip")', "I couldn't find 'nope.zip'"),
    ('archive.untar("tb.py", "out")', "isn't a tar file I can read"),
    ('archive.unzip("tb.py", "out")', "isn't a zip file"),
    ('archive.list("tb.py")', "isn't a zip or tar archive I recognise"),
    ('archive.gunzip("tb.py")', "isn't a gzip file"),
    ('archive.gzip("tblib")', "is a folder; use tar()"),
    ('archive.tar("x.tar", ["tb.py"], {"compress": "zip"})', 'knows the compressions "gz", "bz2" and "xz"'),
    ('archive.unzip("x.zip", "out", {"safe": "yes"})', 'needs "safe" to be true or false'),
])
def test_archive_errors(src, message, monkeypatch, root, tmp_path):
    monkeypatch.chdir(root)
    err = tb_error("import archive\n" + src)
    assert message in str(err)
    assert not (root / "x.zip").exists() and not (root / "x.tar").exists()


def test_docs():
    info = tb.describe_library("archive")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
