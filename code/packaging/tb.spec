# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 6.11.1 spec for TB: one-file console app named "tb".
#
# Build (from the repo root):
#   uv run --group build python packaging/build.py
# or directly:
#   uv run --group build pyinstaller packaging/tb.spec --distpath dist --workpath build
#
# PyInstaller cannot cross-compile: build on each OS you want a binary for.

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is set by PyInstaller)
VERSION = (1, 0, 0, 0)
VERSION_TEXT = "1.0.0"

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

# tb.py only uses the standard library; drop big optional modules to keep the
# download small.
EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc", "doctest", "http",
    "xml", "xmlrpc", "sqlite3", "_sqlite3", "lib2to3", "pdb", "ensurepip",
    "venv", "idlelib", "turtle", "turtledemo", "curses",
]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "tb.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)
