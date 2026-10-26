"""Entry point of tblaunch / tblaunchw (tblaunch.exe / tblaunchw.exe on Windows).

`tb build app.tb` copies this program next to the bundled app and renames it
after the app (tblaunch.exe -> app.exe; tblaunchw.exe -> app.exe for --gui).
When started, it runs `<its own name>.tb` from the folder it lives in with the
TB runtime it shares with tb.exe (the same _internal/ folder), passing its
command-line arguments on to the program (os.args):

    app.exe --verbose x.log    ==    tb app.tb -- --verbose x.log

tblaunchw is the same program built without a console window (for GUI apps);
tb.py then shows errors in a dialog instead of printing them.
"""

from __future__ import annotations

import os
import sys


def script_path() -> str:
    exe = os.path.abspath(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
    stem = os.path.splitext(os.path.basename(exe))[0]
    return os.path.join(os.path.dirname(exe), stem + ".tb")


def main() -> int:
    script = script_path()
    import tb  # the bundled TB runtime

    if not os.path.isfile(script):
        message = (f"Oops! I couldn't find {os.path.basename(script)} next to this program "
                   f"({os.path.dirname(script)}). Keep the app's .tb file and the program together, "
                   "with the same name.")
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        else:  # windowed launcher: no console to print to
            tb.main([script])  # tb.py reports the missing file in its error dialog
        return 1
    return tb.main([script, "--", *sys.argv[1:]])


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
