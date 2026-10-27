"""archive: zip, tar and gzip files, with safe extraction."""

from __future__ import annotations

import gzip
import os
import shutil
import stat
import tarfile
import zipfile

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

MAX_MEMBERS = 100_000                 # files one archive may hold
DEFAULT_MAX_SIZE = 2 * 1024 ** 3      # unpacked bytes an extraction may produce (2 GB)
MAX_RATIO = 1000                      # compression ratio above which we call it a bomb
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


class Unsafe(Exception):
    """An archive member that must not be extracted."""


def kind_of(path: str) -> str:
    """"zip", "tar", "gzip" or "unknown", by magic bytes (falls back to the name)."""
    try:
        with open(path, "rb") as f:
            head = f.read(265)
    except OSError:
        head = b""
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:2] == b"\x1f\x8b":
        low = path.lower()
        return "tar" if low.endswith((".tar.gz", ".tgz")) else "gzip"
    if head[257:262] == b"ustar" or (head[:3] == b"BZh" and path.lower().endswith((".bz2", ".tbz2"))) or \
            head[:6] == b"\xfd7zXZ\x00":
        return "tar"
    low = path.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(TAR_SUFFIXES):
        return "tar"
    if low.endswith(".gz"):
        return "gzip"
    return "unknown"


def safe_member_path(name: str, dest: str) -> str:
    """The real path a member may be written to, or raise Unsafe for
    absolute paths, drive letters and anything escaping dest (zip-slip)."""
    if not name or name.endswith("/") and len(name.strip("/")) == 0:
        raise Unsafe("empty name")
    clean = name.replace("\\", "/")
    if clean.startswith("/") or (len(clean) > 1 and clean[1] == ":"):
        raise Unsafe(f"'{name}' is an absolute path")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise Unsafe(f"'{name}' tries to climb out of the folder with '..'")
    if not parts:
        raise Unsafe("empty name")
    target = os.path.realpath(os.path.join(dest, *parts))
    root = os.path.realpath(dest)
    if target != root and not target.startswith(root + os.sep):
        raise Unsafe(f"'{name}' would land outside the destination folder")
    return target


def check_no_symlink_in_path(dest: str, target: str):
    """Refuse to write through a symlink that an earlier member created."""
    root = os.path.realpath(dest)
    current = os.path.dirname(target)
    while len(current) > len(root):
        if os.path.islink(current):
            raise Unsafe(f"'{os.path.relpath(current, root)}' is a link; I don't write through links")
        current = os.path.dirname(current)


def load(api):
    m = Module(
        "archive",
        "Make and open .zip, .tar(.gz) and .gz files. Extraction is safe by default: members that would "
        "land outside the destination (zip-slip), absolute paths, links and device files are refused, and "
        "archives that would unpack to more than 2 GB (or compress suspiciously well) are stopped. "
        "archive.list(path) shows what is inside without extracting.",
    )

    def path_arg(line, path, fn, arg="path"):
        check_type(line, path, "text", f"archive.{fn}", arg)
        if not path.strip():
            fail(line, f"archive.{fn}() needs a {arg}, but got empty text.")
        return os.path.expanduser(path)

    def options_arg(line, options, fn, allowed):
        if options is None:
            return {}
        check_type(line, options, "map", f"archive.{fn}", "options")
        for key in options:
            if key not in allowed:
                fail(line, f'archive.{fn}() doesn\'t know the option "{key}". Options: {", ".join(allowed)}.')
        return options

    def paths_arg(line, paths, fn):
        if isinstance(paths, str):
            paths = [paths]
        check_type(line, paths, "list", f"archive.{fn}", "paths")
        if not paths:
            fail(line, f"archive.{fn}() needs at least one file or folder to add.")
        out = []
        for p in paths:
            p = path_arg(line, p, fn, "each path")
            if not os.path.exists(p):
                fail(line, f"I couldn't find '{p}' to add to the archive.", path=p)
            out.append(p)
        return out

    def members_of(line, roots, base, fn):
        """[(disk_path, archive_name)] for files and folders, sorted."""
        items = []
        for root in roots:
            if os.path.isdir(root):
                top = os.path.basename(os.path.normpath(root)) if base is None else os.path.relpath(root, base)
                if top in (".", ""):
                    top = ""
                items.append((root, top, True))
                for here, dirs, files in os.walk(root):
                    dirs.sort()
                    rel = os.path.relpath(here, root)
                    for d in dirs:
                        full = os.path.join(here, d)
                        if os.path.islink(full):
                            continue
                        items.append((full, join_arc(top, rel, d), True))
                    for f in sorted(files):
                        full = os.path.join(here, f)
                        if os.path.islink(full) or not os.path.isfile(full):
                            continue
                        items.append((full, join_arc(top, rel, f), False))
                        if len(items) > MAX_MEMBERS:
                            fail(line, f"archive.{fn}() adds at most {MAX_MEMBERS:,} files.")
            else:
                name = os.path.basename(root) if base is None else os.path.relpath(root, base)
                if name.startswith(".."):
                    fail(line, f"archive.{fn}(): '{root}' is outside the base folder '{base}'.")
                items.append((root, name.replace(os.sep, "/"), False))
        return [(p, n, d) for p, n, d in items if n]

    def join_arc(top, rel, name):
        parts = [top] if top else []
        if rel not in (".", ""):
            parts.append(rel)
        parts.append(name)
        return "/".join(p.replace(os.sep, "/") for p in parts)

    def base_arg(line, options, fn):
        base = options.get("base")
        if base is None:
            return None
        base = path_arg(line, base, fn, "base")
        if not os.path.isdir(base):
            fail(line, f"archive.{fn}(): the base '{base}' isn't a folder.")
        return base

    # -- zip ------------------------------------------------------------------

    @m.fn("zip", 2, 3, doc=(
        "zip(target, paths, options?) -> number\n"
        "Make a .zip file holding the files and folders in paths. Folders are added with everything inside.\n"
        "Replaces an existing target. Gives back the number of files added.\n"
        "\n"
        "Options:\n"
        "- base: a folder. Paths in the zip are kept relative to it.\n"
        "  Without it, names in the zip are just the file or folder names."))
    def zip_(line, target, paths, options=None):
        target = path_arg(line, target, "zip", "target")
        roots = paths_arg(line, paths, "zip")
        o = options_arg(line, options, "zip", ("base",))
        base = base_arg(line, o, "zip")
        items = members_of(line, roots, base, "zip")
        count = 0
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for disk, name, is_dir in items:
                    if is_dir:
                        zf.write(disk, name + "/")
                    else:
                        if os.path.realpath(disk) == os.path.realpath(target):
                            continue
                        zf.write(disk, name)
                        count += 1
        except OSError as err:
            fail_os(line, err, target, "write")
        return count

    def size_guard(line, total, compressed, fn):
        if total > state["max_size"]:
            fail(line, f"archive.{fn}() stopped: the archive would unpack to more than "
                       f"{state['max_size'] // 1024 ** 2:,} MB. Pass {{\"max_size\": bytes}} to allow more.",
                 reason="too_big")
        if compressed > 0 and total / compressed > MAX_RATIO and total > 10 * 1024 ** 2:
            fail(line, f"archive.{fn}() stopped: the archive compresses suspiciously well "
                       f"({total // compressed}:1), which is how zip bombs look.", reason="bomb")

    state = {"max_size": DEFAULT_MAX_SIZE}

    def extraction_options(line, options, fn):
        o = options_arg(line, options, fn, ("safe", "overwrite", "max_size", "members"))
        state["max_size"] = DEFAULT_MAX_SIZE
        if o.get("max_size") is not None:
            state["max_size"] = check_type(line, o["max_size"], "whole", f"archive.{fn}", '"max_size"')
        safe = o.get("safe", True)
        check_type(line, safe, "bool", f"archive.{fn}", '"safe"')
        overwrite = o.get("overwrite", True)
        check_type(line, overwrite, "bool", f"archive.{fn}", '"overwrite"')
        members = o.get("members")
        if members is not None:
            check_type(line, members, "list", f"archive.{fn}", '"members"')
        return safe, overwrite, members

    def ready_dest(line, dest, fn):
        dest = path_arg(line, dest, fn, "dest")
        if os.path.exists(dest) and not os.path.isdir(dest):
            fail(line, f"archive.{fn}(): '{dest}' exists and isn't a folder.", path=dest)
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as err:
            fail_os(line, err, dest, "make")
        return dest

    @m.fn("unzip", 2, 3, doc=(
        "unzip(path, dest, options?) -> list\n"
        "Extract a .zip into the folder dest (made if needed) and give back the paths written.\n"
        "It is safe by default.\n"
        "\n"
        "Refused by default:\n"
        "- Members with absolute paths or '..' in them (zip-slip).\n"
        "- Links.\n"
        "- Archives that unpack to more than 2 GB.\n"
        "\n"
        "Options:\n"
        "- safe: false only skips the dangerous members, instead of stopping.\n"
        "- overwrite: false keeps files that already exist.\n"
        "- max_size: the most bytes the archive may unpack to.\n"
        "- members: a list of names, to extract only some."))
    def unzip(line, path, dest, options=None):
        path = path_arg(line, path, "unzip")
        safe, overwrite, wanted = extraction_options(line, options, "unzip")
        dest = ready_dest(line, dest, "unzip")
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            fail(line, f"'{path}' isn't a zip file (or it is damaged).", reason="format", path=path)
        except OSError as err:
            fail_os(line, err, path, "open")
        written = []
        with zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS:
                fail(line, f"archive.unzip() opens archives of at most {MAX_MEMBERS:,} files.", reason="too_big")
            total = sum(i.file_size for i in infos)
            compressed = sum(i.compress_size for i in infos)
            size_guard(line, total, compressed, "unzip")
            for info in infos:
                if wanted is not None and info.filename not in wanted:
                    continue
                try:
                    target = safe_member_path(info.filename, dest)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise Unsafe(f"'{info.filename}' is a link")
                    check_no_symlink_in_path(dest, target)
                except Unsafe as why:
                    if safe:
                        fail(line, f"archive.unzip() refused '{path}': {why}. Pass {{\"safe\": false}} to skip "
                                   "such members instead.", reason="unsafe", member=info.filename)
                    continue
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if os.path.exists(target) and not overwrite:
                    continue
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                except OSError as err:
                    fail_os(line, err, target, "write")
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as err:
                    fail(line, f"I couldn't extract '{info.filename}' from '{path}': {err}.", reason="format")
                written.append(target)
        return written

    def list_zip(line, path):
        try:
            with zipfile.ZipFile(path) as zf:
                out = []
                for info in zf.infolist():
                    d = info.date_time
                    out.append({
                        "name": info.filename, "size": info.file_size, "compressed": info.compress_size,
                        "is_dir": info.is_dir(),
                        "modified": f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d} {d[3]:02d}:{d[4]:02d}:{d[5]:02d}",
                    })
                return out
        except zipfile.BadZipFile:
            fail(line, f"'{path}' isn't a zip file (or it is damaged).", reason="format", path=path)

    # -- tar ------------------------------------------------------------------

    def tar_mode(target: str, compress) -> str:
        low = target.lower()
        if compress is None:
            compress = "gz" if low.endswith((".gz", ".tgz")) else "bz2" if low.endswith((".bz2", ".tbz2")) \
                else "xz" if low.endswith((".xz", ".txz")) else ""
        if compress not in ("", "gz", "bz2", "xz"):
            fail(None, f'archive.tar() knows the compressions "gz", "bz2" and "xz", not "{compress}".')
        return "w:" + compress if compress else "w"

    @m.fn("tar", 2, 3, doc=(
        "tar(target, paths, options?) -> number\n"
        "Make a .tar file. Links are skipped. Gives back the number of files added.\n"
        "\n"
        "It is compressed when the name ends in .tar.gz or .tgz, .tar.bz2 or .tar.xz.\n"
        "\n"
        "Options:\n"
        "- compress: like \"gz\", to compress whatever the name is.\n"
        "- base: works like in zip(), and so do paths."))
    def tar_(line, target, paths, options=None):
        target = path_arg(line, target, "tar", "target")
        roots = paths_arg(line, paths, "tar")
        o = options_arg(line, options, "tar", ("base", "compress"))
        base = base_arg(line, o, "tar")
        compress = o.get("compress")
        if compress is not None:
            check_type(line, compress, "text", "archive.tar", '"compress"')
        try:
            mode = tar_mode(target, compress)
        except _tb.TBError as err:
            fail(line, err.message)
        items = members_of(line, roots, base, "tar")
        count = 0
        try:
            with tarfile.open(target, mode) as tf:
                for disk, name, is_dir in items:
                    if os.path.realpath(disk) == os.path.realpath(target):
                        continue
                    tf.add(disk, arcname=name, recursive=False)
                    if not is_dir:
                        count += 1
        except OSError as err:
            fail_os(line, err, target, "write")
        return count

    def open_tar(line, path, fn):
        try:
            return tarfile.open(path, "r:*")
        except tarfile.ReadError:
            fail(line, f"'{path}' isn't a tar file I can read (or it is damaged).", reason="format", path=path)
        except OSError as err:
            fail_os(line, err, path, "open")

    @m.fn("untar", 2, 3, doc=(
        "untar(path, dest, options?) -> list\n"
        "Extract a .tar / .tar.gz / .tar.bz2 / .tar.xz into dest and give the paths written. Same safety and "
        "options as unzip(); links and device files are refused (or skipped with {\"safe\": false})."))
    def untar(line, path, dest, options=None):
        path = path_arg(line, path, "untar")
        safe, overwrite, wanted = extraction_options(line, options, "untar")
        dest = ready_dest(line, dest, "untar")
        written = []
        with open_tar(line, path, "untar") as tf:
            total = 0
            members = []
            for info in tf:
                members.append(info)
                total += info.size
                if len(members) > MAX_MEMBERS:
                    fail(line, f"archive.untar() opens archives of at most {MAX_MEMBERS:,} files.", reason="too_big")
            try:
                compressed = os.path.getsize(path)
            except OSError:
                compressed = 0
            size_guard(line, total, compressed, "untar")
            for info in members:
                if wanted is not None and info.name not in wanted:
                    continue
                try:
                    if not (info.isfile() or info.isdir()):
                        kind = "link" if info.issym() or info.islnk() else "device file or special file"
                        raise Unsafe(f"'{info.name}' is a {kind}")
                    target = safe_member_path(info.name, dest)
                    check_no_symlink_in_path(dest, target)
                except Unsafe as why:
                    if safe:
                        fail(line, f"archive.untar() refused '{path}': {why}. Pass {{\"safe\": false}} to skip "
                                   "such members instead.", reason="unsafe", member=info.name)
                    continue
                if info.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if os.path.exists(target) and not overwrite:
                    continue
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    src = tf.extractfile(info)
                    if src is None:
                        continue
                    with src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.chmod(target, (info.mode & 0o777) | 0o600)
                except OSError as err:
                    fail_os(line, err, target, "write")
                except tarfile.TarError as err:
                    fail(line, f"I couldn't extract '{info.name}' from '{path}': {err}.", reason="format")
                written.append(target)
        return written

    def list_tar(line, path):
        import datetime as _dt
        with open_tar(line, path, "list") as tf:
            out = []
            for info in tf:
                kind = "file" if info.isfile() else "dir" if info.isdir() else "link" if info.issym() or info.islnk() \
                    else "special"
                out.append({
                    "name": info.name, "size": info.size, "compressed": None, "is_dir": info.isdir(),
                    "kind": kind,
                    "modified": _dt.datetime.fromtimestamp(info.mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
                if len(out) > MAX_MEMBERS:
                    fail(line, f"archive.list() shows at most {MAX_MEMBERS:,} members.")
            return out

    # -- gzip -----------------------------------------------------------------

    @m.fn("gzip", 1, 2, doc=(
        "gzip(path, target?) -> text\n"
        "Compress one file with gzip. target defaults to path + \".gz\". Gives the target path."))
    def gzip_(line, path, target=None):
        path = path_arg(line, path, "gzip")
        if os.path.isdir(path):
            fail(line, f"archive.gzip() compresses one file, and '{path}' is a folder; use tar() for folders.")
        target = path + ".gz" if target is None else path_arg(line, target, "gzip", "target")
        try:
            with open(path, "rb") as src, gzip.open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        except OSError as err:
            fail_os(line, err, path if not os.path.exists(path) else target, "read" if not os.path.exists(path) else "write")
        return target

    @m.fn("gunzip", 1, 2, doc=(
        "gunzip(path, target?) -> text\n"
        "Uncompress a .gz file. target defaults to the name without \".gz\". Stops if the result would be "
        "bigger than 2 GB. Gives the target path."))
    def gunzip(line, path, target=None):
        path = path_arg(line, path, "gunzip")
        if target is None:
            target = path[:-3] if path.lower().endswith(".gz") else path + ".out"
        else:
            target = path_arg(line, target, "gunzip", "target")
        total = 0
        try:
            with gzip.open(path, "rb") as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > DEFAULT_MAX_SIZE:
                        out.close()
                        os.remove(target)
                        fail(line, "archive.gunzip() stopped: the file would unpack to more than 2 GB.",
                             reason="too_big")
                    out.write(chunk)
        except gzip.BadGzipFile:
            fail(line, f"'{path}' isn't a gzip file.", reason="format", path=path)
        except EOFError:
            fail(line, f"'{path}' is cut short; the gzip data is incomplete.", reason="format", path=path)
        except OSError as err:
            fail_os(line, err, path, "read")
        return target

    # -- inspection -------------------------------------------------------------

    @m.fn("list", 1, doc=(
        "list(path) -> list\n"
        "What is inside a .zip or .tar archive, without extracting: maps with \"name\", \"size\" (unpacked "
        "bytes), \"compressed\" (zip only), \"is_dir\" and \"modified\"."))
    def list_(line, path):
        path = path_arg(line, path, "list")
        if not os.path.exists(path):
            fail(line, f"I couldn't find '{path}'. Is the name spelled right?", path=path)
        kind = kind_of(path)
        if kind == "zip":
            return list_zip(line, path)
        if kind == "tar":
            return list_tar(line, path)
        if kind == "gzip":
            fail(line, f"'{path}' is a plain gzip file (one compressed file, no listing); use gunzip().",
                 reason="format")
        fail(line, f"'{path}' isn't a zip or tar archive I recognise.", reason="format", path=path)

    @m.fn("kind", 1, doc="kind(path) -> text\n\"zip\", \"tar\", \"gzip\" or \"unknown\", by looking at the file's first bytes.")
    def kind(line, path):
        path = path_arg(line, path, "kind")
        if not os.path.exists(path):
            fail(line, f"I couldn't find '{path}'. Is the name spelled right?", path=path)
        return kind_of(path)

    @m.fn("extract", 2, 3, doc=(
        "extract(path, dest, options?) -> list\n"
        "unzip() or untar() depending on what the file is (gunzip for a lone .gz)."))
    def extract(line, path, dest, options=None):
        p = path_arg(line, path, "extract")
        if not os.path.exists(p):
            fail(line, f"I couldn't find '{p}'. Is the name spelled right?", path=p)
        kind = kind_of(p)
        if kind == "zip":
            return unzip(line, path, dest, options)
        if kind == "tar":
            return untar(line, path, dest, options)
        if kind == "gzip":
            dest = ready_dest(line, dest, "extract")
            name = os.path.basename(p)
            name = name[:-3] if name.lower().endswith(".gz") else name + ".out"
            return [gunzip(line, path, os.path.join(dest, name))]
        fail(line, f"'{p}' isn't an archive I recognise (zip, tar or gzip).", reason="format", path=p)

    m.value("max_size", DEFAULT_MAX_SIZE, doc="How many unpacked bytes an extraction allows by default (2 GB).")
    return m
