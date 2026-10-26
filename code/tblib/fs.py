"""fs: files and folders."""

from __future__ import annotations

import os
import shutil

from tblib._api import MANY, Module, check_type, fail, fail_os

import tb as _tb

MAX_WALK = 100_000


def read_text(path: str) -> str:
    """Read a text file the forgiving way TB reads programs (UTF-8 with or
    without BOM, UTF-16, then the Windows code page). Other tblib modules
    can use this too."""
    return _tb.read_source(path)


def load(api):
    m = Module(
        "fs",
        "Files and folders: read, write, list, walk, copy and remove. "
        "Relative paths start from the folder you ran tb in; fs.here(name) "
        "gives a path next to your program.",
    )

    def path_arg(line, path, fn, arg="path"):
        check_type(line, path, "text", f"fs.{fn}", arg)
        if path == "":
            fail(line, f"fs.{fn}() needs a {arg}, but got empty text.")
        return os.path.expanduser(path)

    def need_file(line, path, fn):
        if os.path.isdir(path):
            fail(line, f"'{path}' is a folder, not a file.", path=path)

    @m.fn("read", 1, doc="read(path) -> text\nThe whole text file.")
    def read(line, path):
        path = path_arg(line, path, "read")
        need_file(line, path, "read")
        try:
            return read_text(path)
        except OSError as err:
            fail_os(line, err, path, "read")

    @m.fn("lines", 1, doc="lines(path) -> list\nThe text file as a list of lines (without line endings).")
    def lines(line, path):
        return read(line, path).splitlines()

    def put(line, path, content, fn, mode):
        path = path_arg(line, path, fn)
        need_file(line, path, fn)
        text = content if isinstance(content, str) else _tb.to_text(content)
        try:
            with open(path, mode, encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    @m.fn("write", 2, doc="write(path, text)\nSave text in a file, replacing what was there (makes the file if needed).")
    def write(line, path, content):
        return put(line, path, content, "write", "w")

    @m.fn("append", 2, doc="append(path, text)\nAdd text to the end of a file (makes the file if needed). Add \"\\n\" yourself for new lines.")
    def append(line, path, content):
        return put(line, path, content, "append", "a")

    @m.fn("exists", 1, doc="exists(path) -> bool\nTrue if there is a file or folder there.")
    def exists(line, path):
        return os.path.exists(path_arg(line, path, "exists"))

    @m.fn("is_dir", 1, doc="is_dir(path) -> bool\nTrue if path is a folder.")
    def is_dir(line, path):
        return os.path.isdir(path_arg(line, path, "is_dir"))

    @m.fn("is_file", 1, doc="is_file(path) -> bool\nTrue if path is a file.")
    def is_file(line, path):
        return os.path.isfile(path_arg(line, path, "is_file"))

    @m.fn("list", 0, 1, doc="list(folder?) -> list\nThe names of the files and folders inside (sorted). Default: the current folder.")
    def list_(line, folder="."):
        folder = path_arg(line, folder, "list", "folder")
        try:
            return sorted(os.listdir(folder))
        except OSError as err:
            fail_os(line, err, folder, "list")

    @m.fn("walk", 0, 1, doc="walk(folder?) -> list\nThe paths of every file inside the folder and its sub-folders (sorted).")
    def walk(line, folder="."):
        folder = path_arg(line, folder, "walk", "folder")
        if not os.path.isdir(folder):
            if os.path.exists(folder):
                fail(line, f"'{folder}' is a file, not a folder.", path=folder)
            fail(line, f"I couldn't find the folder '{folder}'.", path=folder)
        found = []
        for here, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                found.append(os.path.join(here, name))
                if len(found) > MAX_WALK:
                    fail(line, f"fs.walk() found more than {MAX_WALK:,} files; pick a smaller folder.")
        return found

    @m.fn("mkdir", 1, doc="mkdir(path)\nMake a folder (and any missing folders above it). Fine if it already exists.")
    def mkdir(line, path):
        path = path_arg(line, path, "mkdir")
        try:
            os.makedirs(path, exist_ok=True)
        except FileExistsError:
            fail(line, f"'{path}' already exists as a file.", path=path)
        except OSError as err:
            fail_os(line, err, path, "make")
        return None

    @m.fn("remove", 1, doc="remove(path)\nDelete a file. (It won't delete folders, to keep you safe.)")
    def remove(line, path):
        path = path_arg(line, path, "remove")
        if os.path.isdir(path):
            fail(line, f"fs.remove() only deletes files, and '{path}' is a folder.", path=path)
        try:
            os.remove(path)
        except OSError as err:
            fail_os(line, err, path, "remove")
        return None

    @m.fn("copy", 2, doc="copy(from, to)\nCopy a file. If to is a folder, the copy goes inside it.")
    def copy(line, source, target):
        source = path_arg(line, source, "copy", "from")
        target = path_arg(line, target, "copy", "to")
        need_file(line, source, "copy")
        try:
            shutil.copy2(source, target)
        except shutil.SameFileError:
            fail(line, "fs.copy() can't copy a file onto itself.")
        except OSError as err:
            fail_os(line, err, source, "copy")
        return None

    @m.fn("size", 1, doc="size(path) -> number\nThe file's size in bytes.")
    def size(line, path):
        path = path_arg(line, path, "size")
        try:
            return os.path.getsize(path)
        except OSError as err:
            fail_os(line, err, path, "look at")

    @m.fn("modified", 1, doc="modified(path) -> number\nWhen the file last changed, as a timestamp (see the time library).")
    def modified(line, path):
        path = path_arg(line, path, "modified")
        try:
            return os.path.getmtime(path)
        except OSError as err:
            fail_os(line, err, path, "look at")

    @m.fn("home", 0, doc="home() -> text\nYour home folder.")
    def home(line):
        return os.path.expanduser("~")

    @m.fn("cwd", 0, doc="cwd() -> text\nThe current folder (where relative paths start).")
    def cwd(line):
        return os.getcwd()

    @m.fn("here", 0, 1, doc="here(name?) -> text\nThe folder your program is in, or the path of name inside it: fs.read(fs.here(\"data.txt\")).")
    def here(line, name=None):
        if name is None:
            return api.base_dir
        check_type(line, name, "text", "fs.here", "name")
        return os.path.join(api.base_dir, name)

    @m.fn("join", 1, MANY, doc="join(part, ...) -> text\nJoin path parts with the right slash for this computer: join(\"logs\", \"auth.log\").")
    def join(line, *parts):
        for p in parts:
            check_type(line, p, "text", "fs.join", "each part")
        return os.path.join(*parts)

    @m.fn("name", 1, doc="name(path) -> text\nThe last part of a path: name(\"logs/auth.log\") is \"auth.log\".")
    def name(line, path):
        check_type(line, path, "text", "fs.name", "path")
        return os.path.basename(path.rstrip("/\\")) or path

    @m.fn("ext", 1, doc="ext(path) -> text\nThe file extension with its dot: ext(\"auth.log\") is \".log\" (\"\" if none).")
    def ext(line, path):
        check_type(line, path, "text", "fs.ext", "path")
        return os.path.splitext(path)[1]

    @m.fn("parent", 1, doc="parent(path) -> text\nThe folder that holds path: parent(\"logs/auth.log\") is \"logs\".")
    def parent(line, path):
        check_type(line, path, "text", "fs.parent", "path")
        return os.path.dirname(path.rstrip("/\\"))

    return m
