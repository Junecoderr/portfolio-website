TB - run it with Python
=======================

This folder has everything TB needs:

  tb.py            starts TB (version 3.0.0)
  tbcore\          the TB language itself       (keep these three folders
  tbtools\         tb fmt, tb check, tb build... next to tb.py)
  tblib\           TB's standard library
  welcome.tb       a short tour of TB, written in TB
  examples\        small TB programs to try
  apps\            bigger apps: desktop windows (calculator, to-do, notes,
                   care, alerts dashboard, network monitor, file
                   inspector...) and security tools (log triage, IOC
                   extractor, a web dashboard...)
  packages\        example TB packages
  spec\            the language reference (SPEC.md) and its tests
  README.txt       this file

TB is written in Python, so your computer needs Python 3.10 or newer
first. You only have to set this up once.


STEP 1 - Unzip this folder
--------------------------

Windows: if you are looking at this inside the zip, TB will not work from
here yet. Close this, find tb-python.zip in your Downloads folder,
RIGHT-CLICK it and choose "Extract All...", then click "Extract".
Use the new tb-python folder that opens (not the zip).

Mac: double-click tb-python.zip. A tb-python folder appears next to it.


STEP 2 - Install Python 3
-------------------------

Windows:
  1. Go to https://www.python.org/downloads/ and click the big yellow
     "Download" button. (Or open the Microsoft Store app and install
     "Python install manager".)
  2. Open the file you downloaded and click "Install".
  3. If you see a box that says "Add python.exe to PATH" (or anything
     with "Add ... to PATH"), TICK IT before you continue. Some versions
     do not show this box; that is fine.
  4. The first time you use Python, a black window may ask questions
     like "install the latest runtime?" or "add a directory to PATH?".
     Type  y  and press Enter each time.

Mac:
  1. Go to https://www.python.org/downloads/ and download Python.
  2. Open the file you downloaded and click through the installer.

Linux:
  Python 3 is usually already installed. If not, install "python3"
  with your system's software app. For the window apps also install
  "python3-tk".

Window apps (anything that uses  import gui ) need Python's "Tk" part.
The python.org downloads for Windows and Mac include it already.

Time zones (Windows only): Windows has no time-zone database that Python
can read, so the datetime library knows only UTC and offsets like "+05:30"
until you add the official one from the Python team. In the black window
of STEP 3, type this once and press Enter:

    python -m pip install tzdata

(Mac and Linux already have it.)


STEP 3 - Open a terminal in this folder
---------------------------------------

Windows:
  Open the tb-python folder in File Explorer, click in the address bar at
  the top (where the folder path is), type  cmd  and press Enter.
  A black window opens, already in this folder.

Mac:
  Open the Terminal app (press Cmd+Space, type Terminal, press Enter).
  Type  cd  followed by a space (do not press Enter yet), then drag the
  tb-python folder from Finder into the Terminal window, and press Enter.
  (Or right-click the folder and choose "New Terminal at Folder". If you
  don't see it, turn it on in System Settings > Keyboard >
  Keyboard Shortcuts > Services > Files and Folders.)

Linux:
  Right-click inside this folder and choose "Open in Terminal".


STEP 4 - Run a TB program
-------------------------

Windows - type this and press Enter:

    python tb.py welcome.tb

Mac or Linux - type this and press Enter:

    python3 tb.py welcome.tb

To try another program, change the last part, for example:

    python tb.py examples\hello.tb        (Windows)
    python3 tb.py examples/hello.tb       (Mac / Linux)

(Look inside the examples folder to see which programs are there.)

Try the apps (each folder in apps has a README.md that explains it):

    python tb.py apps\calculator\main.tb       (a window opens - Windows)
    python3 tb.py apps/calculator/main.tb      (Mac / Linux)
    python tb.py apps\log-triage\main.tb       (security log triage)

See every library TB comes with, and what one can do:

    python tb.py doc
    python tb.py doc gui

Tools that come with TB (Mac / Linux: python3 instead of python):

    python tb.py check mine.tb       look for mistakes without running
    python tb.py fmt mine.tb         tidy up the layout
    python tb.py test spec           run the language's own tests
    python tb.py build mine.tb       bundle a program into a folder to share

To play with TB line by line, leave out the file name:

    python tb.py          (Windows)
    python3 tb.py         (Mac / Linux)

Type  exit  to leave.


Write your own
--------------

Windows: open Notepad and write some TB. Choose File > Save As, go to the
tb-python folder, and BEFORE saving change "Save as type" to "All files (*.*)"
and type the name  mine.tb  . (If you skip this, Notepad secretly saves it
as mine.tb.txt and TB will not find it.)

Mac: open TextEdit, choose Format > Make Plain Text, write some TB, and save
it in the tb-python folder as  mine.tb  (if it asks, choose "Use .tb").

Then run it:

    python tb.py mine.tb       (Windows)
    python3 tb.py mine.tb      (Mac / Linux)

A tiny program to start with:

    let name = ask("What's your name? ")
    say("Hello, " + name + "!")
    care(name)


If something goes wrong
-----------------------

"python is not recognized" (Windows): try  py tb.py welcome.tb  instead.
If that also fails, run the Python installer again and make sure the
"Add ... to PATH" box is ticked, then close and reopen the black window.

"can't open file 'tb.py'": the terminal is not in the tb-python folder,
or you are still inside the zip. Do STEP 1 and STEP 3 again.

"There's no library called ..." or "No module named 'tbcore'": tb.py
cannot see its folders. Keep tb.py, tbcore, tbtools and tblib together in
the same folder.

A Python "SyntaxError" as soon as you start tb.py: your Python is older
than 3.10. Install a newer one (STEP 2), then check with  python --version

A window app says it needs Tk / "No module named tkinter": your Python has
no Tk. On Windows or Mac, install Python from python.org (STEP 2). On Linux,
install "python3-tk".

"This computer has no time-zone database; install the 'tzdata' package"
(Windows): type  python -m pip install tzdata  and press Enter (see the
"Time zones" note in STEP 2).

"I couldn't find the file 'mine.tb'": Notepad probably saved it as
mine.tb.txt. Save it again with "Save as type: All files".

TB - a friendly programming language.
