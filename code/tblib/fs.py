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
            # Windows files end lines with \r\n; TB text always uses \n.
            return read_text(path).replace("\r\n", "\n")
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

    @m.fn("read_bytes", 1, doc="read_bytes(path) -> bytes\nThe whole file as bytes (for binary files: images, executables, captures).")
    def read_bytes(line, path):
        path = path_arg(line, path, "read_bytes")
        need_file(line, path, "read_bytes")
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError as err:
            fail_os(line, err, path, "read")

    @m.fn("write_bytes", 2, doc="write_bytes(path, data)\nSave bytes in a file, replacing what was there (makes the file if needed).")
    def write_bytes(line, path, data):
        path = path_arg(line, path, "write_bytes")
        need_file(line, path, "write_bytes")
        check_type(line, data, "bytes", "fs.write_bytes", "data")
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as err:
            fail_os(line, err, path, "write to")
        return None

    modes = {"r": "r", "w": "w", "a": "a", "rb": "rb", "wb": "wb", "ab": "ab",
             "r+": "r+", "r+b": "r+b", "rb+": "r+b"}

    def make_stream(line, path, mode):
        binary = "b" in mode
        try:
            if binary:
                handle = open(path, modes[mode])
            else:
                handle = open(path, modes[mode], encoding="utf-8", newline="")
        except OSError as err:
            fail_os(line, err, path, "open")
        stream = {"path": path, "mode": mode}

        def need_open(line, fn):
            if handle.closed:
                fail(line, f"file.{fn}() can't run: the file '{path}' was closed.", cls="FileError")

        def add(name, lo, hi, func, doc):
            stream[name] = _tb.Builtin(f"file.{name}", func, (lo, hi), doc)

        def io(line, fn, work):
            need_open(line, fn)
            try:
                return work()
            except OSError as err:
                fail_os(line, err, path, fn)
            except UnicodeDecodeError:
                fail(line, f"'{path}' isn't UTF-8 text; open it with mode \"rb\" for bytes.",
                     cls="ValueError")
            except Exception as err:  # io.UnsupportedOperation and friends
                fail(line, f"file.{fn}() isn't possible in mode \"{mode}\" ({err}).", cls="FileError")

        def read_(line, n=None):
            if n is not None:
                n = check_type(line, n, "whole", "file.read", "n")
            data = io(line, "read", lambda: handle.read() if n is None else handle.read(n))
            return data.replace("\r\n", "\n") if isinstance(data, str) else data

        def read_line(line):
            data = io(line, "read_line", handle.readline)
            if data in ("", b""):
                return None
            if isinstance(data, str):
                return data.rstrip("\r\n")
            return data.rstrip(b"\r\n")

        def write_(line, value):
            if binary:
                check_type(line, value, "bytes", "file.write", "data")
            elif not isinstance(value, str):
                value = _tb.to_text(value)
            io(line, "write", lambda: handle.write(value))
            return None

        def seek(line, pos):
            pos = check_type(line, pos, "whole", "file.seek", "pos")
            return io(line, "seek", lambda: handle.seek(pos))

        def tell(line):
            return io(line, "tell", handle.tell)

        def close(line):
            if not handle.closed:
                handle.close()
            return None

        add("read", 0, 1, read_, "read(n?) -> text or bytes\nRead n characters (bytes in \"rb\" mode), or everything that's left.")
        add("read_line", 0, 0, read_line, "read_line() -> text or nil\nThe next line without its line ending, or nil at the end of the file.")
        add("write", 1, 1, write_, "write(value)\nWrite text (bytes in a \"b\" mode) at the current position.")
        add("seek", 1, 1, seek, "seek(pos)\nMove to byte position pos (0 is the start).")
        add("tell", 0, 0, tell, "tell() -> number\nThe current position.")
        add("close", 0, 0, close, "close()\nClose the file (safe to call twice).")
        add("closed", 0, 0, lambda line: handle.closed, "closed() -> bool\nTrue once the file is closed.")
        return stream, handle

    def mode_arg(line, mode, fn):
        check_type(line, mode, "text", f"fs.{fn}", "mode")
        if mode not in modes:
            fail(line, f"fs.{fn}() doesn't know the mode \"{mode}\". Use \"r\" (read), \"w\" (write), "
                       "\"a\" (append), or add b for bytes: \"rb\", \"wb\", \"ab\".", cls="ValueError")
        return mode

    @m.fn("open", 1, 2, doc="open(path, mode?) -> file\nOpen a file to read or write it a piece at a time. mode: \"r\" (default), \"w\", \"a\", or with b for bytes (\"rb\", \"wb\", \"ab\"). The file has read(n?), read_line(), write(x), seek(pos), tell() and close(). See fs.with_file to have it closed for you.")
    def open_(line, path, mode="r"):
        path = path_arg(line, path, "open")
        mode = mode_arg(line, mode, "open")
        need_file(line, path, "open")
        return make_stream(line, path, mode)[0]

    @m.fn("with_file", 3, doc="with_file(path, mode, fn) -> value\nOpen the file, call fn(file), and close it again even if fn stops with an error. Gives back what fn gave back.")
    def with_file(line, path, mode, fn):
        path = path_arg(line, path, "with_file")
        mode = mode_arg(line, mode, "with_file")
        check_type(line, fn, "function", "fs.with_file", "fn")
        need_file(line, path, "with_file")
        stream, handle = make_stream(line, path, mode)
        try:
            return api.call(fn, [stream], line)
        finally:
            handle.close()

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
            if os.path.exists(target) and os.path.samefile(source, target):
                fail(line, "fs.copy() can't copy a file onto itself.")
        except OSError:
            pass
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

    from tblib._watch import install as _install_watch  # fs.watch / fs.watch_stop

    _install_watch(m, api)
    return m
