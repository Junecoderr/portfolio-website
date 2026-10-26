# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 6.11.1 spec for TB: a one-folder ("onedir") console app.
#
# Output: dist/tb/
#   tb / tb.exe     the console program (what people type: tb file.tb)
#   tbw.exe         Windows only: same program without a console window,
#                   used by the installer's shortcuts to the GUI apps
#   _internal/      Python, the standard library (tblib), Tcl/Tk, ...
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
VERSION = (2, 0, 0, 0)
VERSION_TEXT = "2.0.0"

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
                    StringStruct("LegalCopyright", "Made with love, for Tanisha."),
                    StringStruct("OriginalFilename", "tb.exe"),
                    StringStruct("ProductName", "TB"),
                    StringStruct("ProductVersion", VERSION_TEXT),
                ])
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

# The standard library (tblib/) ships inside the app:
# - native modules (tblib/*.py) are compiled into the archive as the "tblib"
#   package (hidden imports, because tb.py imports them by name at runtime);
# - TB-written modules (tblib/*.tb) and tblib/README.md are copied as data to
#   <bundle>/tblib/, which tb.library_dirs() finds through sys._MEIPASS.
# There is deliberately no tblib/__init__.py in the data copy, so tb.py falls
# back to the compiled package instead of loading .py files from disk.
from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, ROOT)
TBLIB = os.path.join(ROOT, "tblib")
HIDDEN = set(collect_submodules("tblib"))
# Belt and braces: every tblib/*.py, even if collect_submodules missed one
# (for example because importing it failed on the build machine).
HIDDEN.update(
    "tblib." + f[:-3] for f in os.listdir(TBLIB) if f.endswith(".py") and f != "__init__.py"
)
HIDDEN.add("tblib")
HIDDEN = sorted(HIDDEN)

DATAS = [
    (os.path.join(TBLIB, f), "tblib")
    for f in sorted(os.listdir(TBLIB))
    if f.endswith(".tb") or f == "README.md"
]

# tkinter stays in: the gui library needs it, and PyInstaller's tkinter hook
# then bundles the Tcl/Tk runtime and its data on Windows, macOS and Linux.
# Drop only modules nothing in TB uses, to keep the download small.
# (http, email, xml stay: urllib.request / the http and intel libraries need them.)
EXCLUDES = [
    "unittest", "pydoc", "doctest", "xmlrpc", "sqlite3", "_sqlite3",
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

def make_exe(name, console):
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
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
    )


exes = [make_exe("tb", console=True)]
if sys.platform == "win32":
    exes.append(make_exe("tbw", console=False))

coll = COLLECT(  # noqa: F821
    *exes,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tb",
)
