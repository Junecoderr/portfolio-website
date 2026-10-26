"""The example GUI apps in apps/, run headless with the fake gui backend.

Each app is copied to a temporary folder first (they save files next to
themselves), run as a real program, and then driven like a person would:
typing, clicking, picking menu items.
"""

import json
import random
import shutil

import pytest

from conftest import ROOT

import tb
import tblib.gui as gui


@pytest.fixture
def fake():
    backend = gui.FakeBackend()
    previous = gui._use_backend(backend)
    yield backend
    gui._use_backend(previous)


def run_app(name, tmp_path, args=None):
    folder = tmp_path / name
    shutil.copytree(ROOT / "apps" / name, folder)
    main = folder / "main.tb"
    lines = []
    interp = tb.Interpreter(output=lines.append, input_fn=lambda prompt: "", rng=random.Random(0))
    tb.run(main.read_text(encoding="utf-8"), interpreter=interp, path=str(main), args=args or [])
    return folder, lines


def test_every_app_has_a_readme():
    for name in ("todo", "care", "notes", "calculator", "hash-checker", "log-triage-gui", "ioc-workbench"):
        readme = (ROOT / "apps" / name / "README.md").read_text(encoding="utf-8")
        assert f"tb apps/{name}/main.tb" in readme


# -- todo -----------------------------------------------------------------------


def test_todo_add_tick_delete_and_save(fake, tmp_path):
    folder, lines = run_app("todo", tmp_path)
    win = fake.window_
    assert win.title == "To-do" and win.ran
    assert win.status_text == "Nothing to do. Enjoy your day!"
    box = win.find("input")
    box.type("Buy flowers")
    box.enter()
    box.type("Call home")
    win.find("button", "Add").click()
    assert box.text == ""
    box.type("   ")
    box.enter()  # blank tasks are ignored
    table = win.find("table")
    assert table.rows == [["", "Buy flowers"], ["", "Call home"]]
    assert win.status_text == "2 to do, 0 done"

    table.open(0)  # double-click ticks it
    assert table.rows[0] == ["✓", "Buy flowers"]
    assert win.status_text == "1 to do, 1 done"
    saved = json.loads((folder / "todo.json").read_text())
    assert saved == [{"text": "Buy flowers", "done": True}, {"text": "Call home", "done": False}]

    table.select(1)
    fake.answers = [True]  # yes, delete it
    win.find("button", "Delete").click()
    assert table.rows == [["✓", "Buy flowers"]]
    assert fake.dialogs[-1] == {"kind": "confirm", "title": "TB", "message": 'Delete "Call home"?'}

    win.find("button", "Clear done").click()
    assert table.rows == []
    assert json.loads((folder / "todo.json").read_text()) == []
    assert lines == []


def test_todo_needs_a_selection(fake, tmp_path):
    run_app("todo", tmp_path)
    fake.window_.find("button", "Tick").click()
    assert fake.dialogs[-1]["message"] == "Pick a task in the list first."
    assert not any(d["kind"] == "error" for d in fake.dialogs)


def test_todo_loads_saved_list(fake, tmp_path):
    folder = tmp_path / "todo"
    shutil.copytree(ROOT / "apps" / "todo", folder)
    (folder / "todo.json").write_text(json.dumps([{"text": "Water plants", "done": False}]))
    lines = []
    tb.run((folder / "main.tb").read_text(encoding="utf-8"), output=lines.append, path=str(folder / "main.tb"))
    assert fake.window_.find("table").rows == [["", "Water plants"]]


def test_todo_survives_a_broken_file(fake, tmp_path):
    folder = tmp_path / "todo"
    shutil.copytree(ROOT / "apps" / "todo", folder)
    (folder / "todo.json").write_text("{not json")
    lines = []
    tb.run((folder / "main.tb").read_text(encoding="utf-8"), output=lines.append, path=str(folder / "main.tb"))
    assert lines and lines[0].startswith("I couldn't read")
    assert fake.window_.find("table").rows == []


# -- care -----------------------------------------------------------------------


def test_care_check_in(fake, tmp_path):
    folder, lines = run_app("care", tmp_path)
    win = fake.window_
    assert win.find("label", "How are you today?")
    assert win.status_text == "0 check-ins in your journal"
    # Saving before picking a mood asks gently.
    win.find("button", "Save today's check-in").click()
    assert fake.dialogs[-1]["message"] == "Pick how you feel first."

    win.find("button", "✿  Calm").click()
    assert win.find("label", "✿  Feeling calm today")
    win.find("textarea").type("Tea and a long walk.")
    win.find("button", "Save today's check-in").click()
    message = [w for w in win.widgets("label") if w.spec.get("style") == "accent"][0].text
    assert message.startswith("Calm looks good on you.")
    assert win.find("textarea").text == ""
    assert win.status_text == "1 check-in in your journal"

    win.find("button", "☂  Sad").click()
    win.find("button", "Save today's check-in").click()
    table = win.find("table")
    assert [row[1:] for row in table.rows] == [["Sad", ""], ["Calm", "Tea and a long walk."]]  # newest first
    saved = json.loads((folder / "journal.json").read_text())
    assert [e["mood"] for e in saved] == ["Calm", "Sad"]
    assert len(saved[0]["date"]) == len("2026-10-26 21:30")

    table.open(1)
    assert fake.dialogs[-1]["title"] == "Journal"
    assert "Tea and a long walk." in fake.dialogs[-1]["message"]
    assert not any(d["kind"] == "error" for d in fake.dialogs)
    assert lines == []


def test_care_has_six_moods(fake, tmp_path):
    run_app("care", tmp_path)
    moods = [w.text for w in fake.window_.widgets("button") if "  " in w.text]
    assert [m.split("  ")[1] for m in moods] == ["Happy", "Calm", "Okay", "Tired", "Sad", "Worried"]


# -- notes ----------------------------------------------------------------------


def test_notes_type_count_save_open(fake, tmp_path):
    folder, lines = run_app("notes", tmp_path)
    win = fake.window_
    assert [m[0] for m in win.menu] == ["File", "Help"]
    assert win.status_text == "0 words, 0 characters"
    editor = win.find("textarea")
    editor.type("Hello there Alex")
    assert win.status_text == "3 words, 16 characters"
    assert win.title == "Untitled • - Notes"

    target = str(tmp_path / "hello.txt")
    fake.answers = [target]
    win.press("Mod+S")
    assert open(target).read() == "Hello there Alex"
    assert fake.dialogs[-1]["kind"] == "save_file" and fake.dialogs[-1]["name"] == "Untitled.txt"
    assert win.title == "hello.txt - Notes"

    other = tmp_path / "other.md"
    other.write_text("one\ntwo")
    fake.answers = [str(other)]
    win.choose_menu("File", "Open…")
    assert editor.text == "one\ntwo"
    assert win.status_text == "2 words, 7 characters"
    assert win.title == "other.md - Notes"

    editor.type("changed")
    fake.answers = [False]  # don't throw the changes away
    win.choose_menu("File", "New")
    assert editor.text == "changed"
    fake.answers = [True]
    win.find("button", "New").click()
    assert editor.text == "" and win.title == "Untitled - Notes"
    assert not any(d["kind"] == "error" for d in fake.dialogs)


def test_notes_open_from_args_and_quit(fake, tmp_path):
    note = tmp_path / "a.txt"
    note.write_text("one two")
    run_app("notes", tmp_path, args=[str(note)])
    win = fake.window_
    assert win.find("textarea").text == "one two"
    assert win.status_text == "2 words, 7 characters"
    win.choose_menu("Help", "About Notes")
    assert "a tiny editor written in TB." in fake.dialogs[-1]["message"]
    win.choose_menu("File", "Quit")
    assert not win.open


def test_notes_bad_file_is_friendly(fake, tmp_path):
    run_app("notes", tmp_path)
    fake.answers = [str(tmp_path / "missing.txt")]
    fake.window_.find("button", "Open…").click()
    assert fake.dialogs[-1]["title"] == "Couldn't open"
    assert "I couldn't find" in fake.dialogs[-1]["message"]


# -- calculator -----------------------------------------------------------------


def calc(fake, keys):
    win = fake.window_
    for key in keys:
        win.find("button", key).click()
    labels = win.widgets("label")
    return labels[1].text, labels[0].text  # display, history


def test_calculator_grid(fake, tmp_path):
    run_app("calculator", tmp_path)
    win = fake.window_
    assert win.theme == "dark"
    rows = [c for c in win.widgets("row")]
    assert [[b.text for b in r.children] for r in rows] == [
        ["C", "±", "%", "÷"], ["7", "8", "9", "×"], ["4", "5", "6", "−"], ["1", "2", "3", "+"], ["0", ".", "⌫", "="],
    ]
    assert win.find("button", "=").spec["primary"] is True


@pytest.mark.parametrize("keys, display", [
    ("12+3=", "15"),
    ("7×6=", "42"),
    ("9−12=", "-3"),
    ("1÷4=", "0.25"),
    (".1+.2=", "0.3"),
    ("2+3×4=", "20"),        # one step at a time, like a pocket calculator
    ("50%", "0.5"),
    ("5±", "-5"),
    ("123⌫", "12"),
    ("12C", "0"),
    ("1÷3=", "0.3333333333"),
])
def test_calculator_sums(fake, tmp_path, keys, display):
    run_app("calculator", tmp_path)
    assert calc(fake, list(keys))[0] == display


def test_calculator_history_and_divide_by_zero(fake, tmp_path):
    run_app("calculator", tmp_path)
    assert calc(fake, list("8+")) == ("8", "8 +")
    calc(fake, ["C"])
    shown, history = calc(fake, list("7÷0="))
    assert (shown, history) == ("Oops", "You can't divide by zero.")
    assert calc(fake, list("+%"))[0] == "Oops"  # ignored until a new number
    assert calc(fake, list("2+2="))[0] == "4"
    assert not any(d["kind"] == "error" for d in fake.dialogs)


def test_calculator_keyboard(fake, tmp_path):
    run_app("calculator", tmp_path)
    win = fake.window_
    for combo in ["1", "2", "*", "3", "Enter"]:
        win.press(combo)
    assert win.widgets("label")[1].text == "36"
    win.press("Escape")
    assert win.widgets("label")[1].text == "0"


# -- hash-checker ---------------------------------------------------------------


def test_hash_checker(fake, tmp_path, monkeypatch):
    monkeypatch.delenv("VT_API_KEY", raising=False)
    sample = tmp_path / "setup.exe"
    sample.write_bytes(b"hello")
    folder, lines = run_app("hash-checker", tmp_path)
    win = fake.window_
    win.find("button", "Hash it").click()
    assert fake.dialogs[-1]["message"].startswith("Choose a file first")

    fake.answers = [str(sample)]
    win.find("button", "Choose file…").click()
    mono = [w.text for w in win.widgets("label") if w.spec.get("style") == "mono"]
    assert mono == ["5d41402abc4b2a76b9719d911017c592", "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d",
                    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"]
    assert win.find("label", "setup.exe")
    assert any(w.text.startswith("5 bytes") for w in win.widgets("label"))

    verdict = [w for w in win.widgets("label") if w.spec.get("style") == "bold" and w.text == " "][0]
    expected = win.widgets("input")[1]
    expected.type("  2CF24DBA5FB0A30E26E83B2AC5B9E29E1B161E5C1FA7425E73043362938B9824 ")
    assert verdict.text.startswith("✓  Match: the SHA-256") and verdict.spec["style"] == "success"
    expected.type("0" * 32)
    assert verdict.text.startswith("✗  No match") and verdict.spec["style"] == "danger"

    [b for b in win.widgets("button") if b.text == "Copy"][0].click()  # the MD5 one
    assert fake.clipboard == "5d41402abc4b2a76b9719d911017c592"
    fake.clipboard = "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"
    win.find("button", "Paste").click()
    assert verdict.text.startswith("✓  Match: the SHA-1")

    win.find("button", "Look up on VirusTotal").click()
    assert fake.dialogs[-1]["title"] == "VirusTotal needs an API key"
    assert "VT_API_KEY" in fake.dialogs[-1]["message"]
    assert not any(d["kind"] == "error" for d in fake.dialogs)


def test_hash_checker_missing_file_and_args(fake, tmp_path):
    sample = tmp_path / "a.txt"
    sample.write_text("hi")
    run_app("hash-checker", tmp_path, args=[str(sample)])
    win = fake.window_
    assert win.find("label", "a.txt")
    box = win.widgets("input")[0]
    box.type(str(tmp_path / "nope.bin"))
    box.enter()
    assert fake.dialogs[-1]["title"] == "No such file"


# -- log-triage-gui -------------------------------------------------------------


def test_log_triage_gui_auth_log(fake, tmp_path):
    folder, lines = run_app_with_data("log-triage-gui", tmp_path)
    win = fake.window_
    assert win.theme == "dark"
    table = win.find("table")
    assert [r[:4] for r in table.rows] == [
        ["CRITICAL", "Brute force", "203.0.113.42", "22"],
        ["HIGH", "New user account created", "sysupdate", "1"],
        ["HIGH", "User added to a group", "sudo", "1"],
        ["MEDIUM", "sudo used to manage accounts or get a shell", "deploy", "2"],
    ]
    details = win.find("textarea")
    assert details.spec["readonly"] is True
    assert details.text.startswith("CRITICAL  Brute force")
    assert "then logged in as" in details.text and "Sample log lines:" in details.text
    table.choose(1)
    assert details.text.startswith("HIGH  New user account created")
    assert "4 alerts:  1 critical,  2 high,  1 medium,  0 low" in [w.text for w in win.widgets("label")]
    assert win.status_text.startswith("101 records")
    assert win.title == "auth.log - TB Log Triage"
    assert lines == []


def test_log_triage_gui_samples_open_and_export(fake, tmp_path):
    folder, lines = run_app_with_data("log-triage-gui", tmp_path, args=["--light"])
    win = fake.window_
    assert win.theme == "light"
    win.find("select").choose("windows_security.json")
    rules = [r[1] for r in win.find("table").rows]
    assert "Security log cleared" in rules and "Encoded PowerShell command" in rules
    win.find("table").choose(rules.index("Encoded PowerShell command"))
    assert "Decoded PowerShell:" in win.find("textarea").text

    fake.answers = [str(tmp_path / "data" / "firewall.log")]
    win.choose_menu("File", "Open log…")
    assert [r[1] for r in win.find("table").rows] == ["Port scan"]
    assert "Ports probed:" in win.find("textarea").text

    out = tmp_path / "report.html"
    fake.answers = [str(out)]
    win.find("button", "Export HTML report…").click()
    page = out.read_text()
    assert "<html" in page.lower() and "Port scan" in page and "TB Log Triage: firewall.log" in page
    assert win.status_text.startswith("Report saved to")

    quiet = tmp_path / "quiet.log"
    quiet.write_text("Mar 12 08:00:00 web01 CRON[1]: nothing to see\n")
    fake.answers = [str(quiet)]
    win.find("button", "Open log…").click()
    assert win.find("table").rows == []
    assert "No alerts. This log looks quiet." in [w.text for w in win.widgets("label")]
    assert not any(d["kind"] == "error" for d in fake.dialogs)


def run_app_with_data(name, tmp_path, args=None):
    """Apps that read ../data and ../log-triage need those folders beside them."""
    shutil.copytree(ROOT / "apps" / "data", tmp_path / "data")
    shutil.copytree(ROOT / "apps" / "log-triage", tmp_path / "log-triage")
    return run_app(name, tmp_path, args)


# -- ioc-workbench --------------------------------------------------------------


def test_ioc_workbench(fake, tmp_path):
    folder, lines = run_app_with_data("ioc-workbench", tmp_path)
    win = fake.window_
    table = win.find("table")
    types = [r[0] for r in table.rows]
    assert {"IP", "Domain", "URL", "Email", "MD5", "SHA-1"} <= set(types)
    url_row = [r for r in table.rows if r[0] == "URL"][0]
    assert url_row[1].startswith("http") and url_row[2].startswith("hxxp")
    assert "IP" in [w for w in win.widgets("label") if w.spec.get("wrap") == 560][0].text

    win.find("button", "Copy as JSON").click()
    data = json.loads(fake.clipboard)
    assert len(data) == len(table.rows) and set(data[0]) == {"type", "value", "defanged"}
    win.find("button", "Copy as Markdown").click()
    assert fake.clipboard.startswith("| type")
    table.open(0)
    assert fake.clipboard == table.rows[0][2]

    text = win.find("textarea")
    text.type("Callback to http://evil.example.com/x and 10.1.2.3")
    win.find("button", "Defang text").click()
    assert text.text == "Callback to hxxp://evil[.]example[.]com/x and 10[.]1[.]2[.]3"
    win.find("button", "Extract IOCs").click()
    assert ["URL", "http://evil.example.com/x", "hxxp://evil[.]example[.]com/x"] in table.rows
    win.find("button", "Refang text").click()
    assert text.text == "Callback to http://evil.example.com/x and 10.1.2.3"

    for name, check in (("iocs.json", lambda s: json.loads(s)), ("iocs.md", lambda s: s.startswith("| type")),
                        ("iocs.csv", lambda s: s.startswith("type,value,defanged"))):
        fake.answers = [str(tmp_path / name)]
        win.find("button", "Save…").click()
        assert check((tmp_path / name).read_text()), name
    assert not any(d["kind"] == "error" for d in fake.dialogs)


def test_ioc_workbench_empty(fake, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("nothing here")
    run_app("ioc-workbench", tmp_path, args=[str(empty)])
    win = fake.window_
    assert win.find("table").rows == []
    assert "No indicators found." in [w.text for w in win.widgets("label")]
    win.find("button", "Copy as JSON").click()
    assert fake.dialogs[-1]["message"] == "Extract some IOCs first."
    fake.clipboard = "see 8.8.8.8"
    win.find("button", "Paste").click()
    assert win.find("table").rows == [["IP", "8.8.8.8", "8[.]8[.]8[.]8"]]
