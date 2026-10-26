# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 6.11.1 spec for TB: a one-folder ("onedir") console app.
#
# Output: dist/tb/
#   tb / tb.exe     the console program (what people type: tb file.tb)
#   tbw.exe         Windows only: same program without a console window,
#                   used by the installer's shortcuts to the GUI apps
#   tblaunch(.exe)  the launcher `tb build` copies next to a bundled app and
#                   renames after it: runs <its own name>.tb next to itself
#                   (packaging/tblaunch.py)
#   tblaunchw.exe   Windows only: the same launcher without a console (GUI apps)
#   _internal/      Python, tbcore, tbtools, the standard library (tblib),
#                   Tcl/Tk, ... shared by all of the programs above
#
# Onedir instead of one-file: a one-file app unpacks itself into a new temp
# folder on every start, and the OS re-scans those fresh libraries each time
# (about 4 s per start on macOS). A folder is scanned once.
#
# Build (from code/):
#   uv run --group build python packaging/build.py
# or directly:
#   uv run --group build pyinstaller packaging/tb.spec --distpath dist --workpath build
#
# PyInstaller cannot cross-compile: build on each OS you want a binary for.

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is set by PyInstaller)
VERSION = (3, 0, 0, 0)
VERSION_TEXT = "3.0.0"
ICON = os.path.join(ROOT, "packaging", "windows", "tb.ico")
LAUNCHER = os.path.join(ROOT, "packaging", "tblaunch.py")

# Windows "Details" tab (right-click tb.exe > Properties). Ignored elsewhere.
version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=VERSION, prodvers=VERSION),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [
                    StringStruct("CompanyName", "TB"),
                    StringStruct("FileDescription", "TB - a small, friendly programming language"),
                    StringStruct("FileVersion", VERSION_TEXT),
                    StringStruct("InternalName", "tb"),
                    StringStruct("LegalCopyright", "TB - a friendly programming language."),
                    StringStruct("OriginalFilename", "tb.exe"),
                    StringStruct("ProductName", "TB"),
                    StringStruct("ProductVersion", VERSION_TEXT),
                ])
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

# TB itself ships inside the app, compiled into the archive:
# - tbcore (lexer, parser, compiler, VM, interpreter...), tbtools (`tb fmt`,
#   `tb check`, `tb build`, ...) and the native libraries tblib/*.py are
#   hidden imports, because tb.py imports tools and libraries by name at
#   runtime. collect_submodules picks up modules added later; the directory
#   listing below is belt and braces (for a module whose import failed on the
#   build machine);
# - tblib/data/ (for example the ATT&CK table attack.json, found next to
#   attack.py through __file__) is copied to <bundle>/tblib/data/;
# - TB-written modules (tblib/*.tb) and tblib/README.md are copied as data to
#   <bundle>/tblib/, which tb.library_dirs() finds through sys._MEIPASS.
# There is deliberately no tblib/__init__.py in the data copy, so tb.py falls
# back to the compiled package instead of loading .py files from disk.
from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, ROOT)
TBLIB = os.path.join(ROOT, "tblib")
HIDDEN = {"tb"}  # tblaunch imports tb as a module
for package in ("tbcore", "tbtools", "tblib"):
    HIDDEN.add(package)
    HIDDEN.update(collect_submodules(package))
    HIDDEN.update(
        f"{package}.{f[:-3]}"
        for f in os.listdir(os.path.join(ROOT, package))
        if f.endswith(".py") and f != "__init__.py"
    )

# Standard modules TB (or its libraries) import lazily, inside functions, or
# only on some Pythons. PyInstaller usually finds them anyway; listing them
# makes sure a newer build Python (3.13) still bundles them.
import importlib.util

for mod in ("sqlite3", "zoneinfo", "tomllib", "ssl", "decimal", "difflib", "calendar", "bisect",
            "webbrowser", "ctypes", "csv", "gzip", "zipfile", "tarfile", "xml.etree.ElementTree",
            "html", "mimetypes", "secrets", "hmac", "uuid", "ipaddress", "heapq", "shlex",
            "textwrap", "pkgutil", "concurrent.futures"):
    if importlib.util.find_spec(mod) is not None:
        HIDDEN.add(mod)
HIDDEN = sorted(HIDDEN)

DATAS = [
    (os.path.join(TBLIB, f), "tblib")
    for f in sorted(os.listdir(TBLIB))
    if f.endswith(".tb") or f == "README.md"
]
for folder, _dirs, files in os.walk(os.path.join(TBLIB, "data")):
    _dirs[:] = [d for d in _dirs if not d.startswith((".", "__"))]
    rel = os.path.relpath(folder, ROOT)
    DATAS += [(os.path.join(folder, f), rel) for f in sorted(files) if not f.startswith(".")]

# tkinter stays in: the gui library needs it, and PyInstaller's tkinter hook
# then bundles the Tcl/Tk runtime and its data on Windows, macOS and Linux.
# Drop only modules nothing in TB uses, to keep the download small.
# (http, email, xml stay: urllib.request / the http and intel libraries need
# them; sqlite3 stays: the sqlite library and the dashboard app use it.)
EXCLUDES = [
    "unittest", "pydoc", "doctest", "xmlrpc",
    "lib2to3", "pdb", "ensurepip", "venv", "idlelib", "turtle", "turtledemo",
    "curses",
]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "tb.py")],
    pathex=[ROOT],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

# Trim the Tcl/Tk data PyInstaller's tkinter hook collects. A one-file app
# unpacks every data file on every start, so fewer files = faster start.
# TB's gui needs the Tcl/Tk scripts (tk.tcl, ttk/, dialogs) but not:
#   - translations (_tcl_data/msgs, _tk_data/msgs): dialogs fall back to English
#   - time zone data (_tcl_data/tzdata): TB's time library is Python's, not Tcl's
#   - Tk demos and demo images
#   - most text encodings: utf-8 and unicode are built into Tcl; keep the
#     few .enc files a Western system may report as its encoding.
KEEP_TCL_ENCODINGS = {"ascii.enc", "cp1252.enc", "iso8859-1.enc", "unicode.enc", "utf-8.enc"}
DROP_PREFIXES = (
    "_tcl_data/msgs/", "_tk_data/msgs/", "_tcl_data/tzdata/",
    "_tk_data/demos/", "_tk_data/images/",
)


def _keep_data(dest):
    dest = dest.replace("\\", "/")
    if dest.startswith(DROP_PREFIXES):
        return False
    if dest.startswith("_tcl_data/encoding/"):
        return dest.rsplit("/", 1)[1] in KEEP_TCL_ENCODINGS
    if dest == "_tk_data/tkAppInit.c":
        return False
    return True


_before = len(a.datas)
a.datas = [entry for entry in a.datas if _keep_data(entry[0])]
print(f"tb.spec: trimmed Tcl/Tk data: {_before} -> {len(a.datas)} data files")

pyz = PYZ(a.pure)  # noqa: F821

# The launchers reuse the same archive and runtime hooks; only the entry
# script differs (a.scripts ends with tb.py, the main script).
launcher_scripts = list(a.scripts[:-1]) + [("tblaunch", LAUNCHER, "PYSOURCE")]


def make_exe(name, console, scripts=None):
    return EXE(  # noqa: F821
        pyz,
        scripts if scripts is not None else a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        version=version_info,
        # .ico icons work on Windows without Pillow; elsewhere executables have none.
        icon=ICON if sys.platform == "win32" and os.path.isfile(ICON) else None,
    )


exes = [make_exe("tb", console=True), make_exe("tblaunch", console=True, scripts=launcher_scripts)]
if sys.platform == "win32":
    exes.append(make_exe("tbw", console=False))
    exes.append(make_exe("tblaunchw", console=False, scripts=launcher_scripts))

coll = COLLECT(  # noqa: F821
    *exes,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tb",
)
