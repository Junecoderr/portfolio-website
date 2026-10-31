"""Tests for the gui library.

Most tests swap in tblib.gui.FakeBackend, so they need no screen: they build
windows from TB code, then click buttons and type into inputs from Python.
The real-Tk smoke tests at the bottom are skipped when tkinter or a display
is missing (run them with a Python that has Tk, e.g. python3.13 on macOS).
"""

import json
import os
import sys

import pytest

from conftest import run_tb, tb_error

import tb
import tblib.gui as gui


@pytest.fixture
def fake():
    backend = gui.FakeBackend()
    previous = gui._use_backend(backend)
    yield backend
    gui._use_backend(previous)


def body(win):
    """The kinds of the widgets in a fake window, nested lists for containers."""
    def walk(container):
        out = []
        for child in container.children:
            if isinstance(child, gui.FakeContainer):
                out.append([child.kind] + walk(child))
            else:
                out.append(child.kind)
        return out
    return walk(win.body)


# -- building windows --------------------------------------------------------


def test_window_basics(fake):
    out = run_tb('import gui\nlet win = gui.window("Hello", {"width": 300, "height": 200, "theme": "dark"})\n'
                 'say(win.kind, win.title(), win.is_open())\nwin.title("Bye")\nwin.run()\nsay("after run")')
    assert out == ["window Hello true", "after run"]
    w = fake.window_
    assert (w.title, w.width, w.height, w.theme, w.ran) == ("Bye", 300, 200, "dark", True)


def test_default_size_and_theme(fake):
    run_tb('import gui\ngui.window("x")')
    w = fake.window_
    assert (w.width, w.height, w.theme, w.resizable) == (520, 400, "light", True)


def test_widgets_in_order_and_nested_containers(fake):
    src = '''import gui
let win = gui.window("Layout")
win.label("Title", {"style": "title"})
let r = win.row()
r.input({"placeholder": "Name"})
r.button("Go")
let c = r.column({"expand": true})
c.label("inside")
c.checkbox("Tick")
win.separator()
win.progress()
win.textarea()
win.list_box(["a", "b"])
win.table(["A", "B"])
win.select(["x", "y"])
say(r.kind, c.kind)
'''
    assert run_tb(src) == ["row column"]
    assert body(fake.window_) == [
        "label", ["row", "input", "button", ["column", "label", "checkbox"]],
        "separator", "progress", "textarea", "list_box", "table", "select",
    ]
    assert fake.window_.find("label", "Title").spec["style"] == "title"
    assert fake.window_.widgets("column")[0].spec == {"expand": True}


def test_clear_removes_widgets(fake):
    run_tb('import gui\nlet win = gui.window("x")\nlet r = win.row()\nr.label("a")\nr.label("b")\nr.clear()\nr.label("c")')
    assert body(fake.window_) == [["row", "label"]]
    assert fake.window_.find("label").text == "c"


def test_label_set_and_value(fake):
    out = run_tb('import gui\nlet win = gui.window("x")\nlet l = win.label(42)\nsay(l.value())\nl.set("hi")\nsay(l.value())')
    assert out == ["42", "hi"]


# -- events --------------------------------------------------------------------


def test_button_click_runs_tb_callback(fake):
    src = '''import gui
let win = gui.window("x")
let count = 0
let b = win.button("Clap", fn() {
  count = count + 1
  say("claps:", count)
}, {"primary": true})
'''
    out = run_tb(src)
    btn = fake.window_.find("button", "Clap")
    assert btn.spec["primary"] is True
    btn.click()
    btn.click()
    assert out == ["claps: 1", "claps: 2"]


def test_button_programmatic_click_set_and_disable(fake):
    src = '''import gui
let win = gui.window("x")
let b = win.button("A", fn() { say("clicked") })
b.click()
b.set("B")
say(b.value())
b.disable()
'''
    assert run_tb(src) == ["clicked", "B"]
    btn = fake.window_.find("button")
    assert btn.enabled is False
    btn.click()  # disabled buttons do nothing
    run_tb('import gui\nlet w = gui.window("y")\nlet b = w.button("plain")\nb.click()\nb.enable()')


def test_button_options_without_callback(fake):
    run_tb('import gui\nlet w = gui.window("y")\nw.button("Big", {"width": 12})')
    assert fake.window_.find("button").spec["width"] == 12


def test_input_value_set_enter_change(fake):
    src = '''import gui
let win = gui.window("x")
let box = win.input({"placeholder": "Your name", "value": "T", "on_enter": fn(text) { say("enter:", text) }, "on_change": fn() { say("changed") }})
say(box.value())
box.set("Tanisha")
say(box.value())
'''
    out = run_tb(src)
    assert out == ["T", "Tanisha"]
    field = fake.window_.find("input")
    assert field.spec["placeholder"] == "Your name"
    field.type("TB")
    field.enter()
    assert out[2:] == ["changed", "enter: TB"]


def test_input_clear_focus_secret(fake):
    run_tb('import gui\nlet w = gui.window("x")\nlet p = w.input({"secret": true, "value": 7})\np.focus()\nsay(p.value())\np.clear()\nsay(p.value() == "")')
    field = fake.window_.find("input")
    assert field.focused and field.spec["secret"] is True and field.text == ""


def test_textarea(fake):
    src = '''import gui
let win = gui.window("x")
let t = win.textarea({"value": "one", "mono": true, "on_change": fn(text) { say("len", len(text)) }})
t.append(" two")
say(t.value())
'''
    out = run_tb(src)
    assert out == ["one two"]
    fake.window_.find("textarea").type("hello")
    assert out[-1] == "len 5"


def test_checkbox(fake):
    src = '''import gui
let win = gui.window("x")
let c = win.checkbox("Done", {"checked": true, "on_change": fn(on) { say("now", on) }})
say(c.value())
c.set(false)
say(c.value())
'''
    out = run_tb(src)
    assert out == ["true", "false"]
    fake.window_.find("checkbox").toggle()
    assert out[-1] == "now true"


def test_select(fake):
    src = '''import gui
let win = gui.window("x")
let s = win.select(["happy", "calm", "tired"], {"value": "calm", "on_change": fn(v) { say("picked", v) }})
say(s.value(), s.options())
s.set("tired")
say(s.value())
s.set_options(["a", "b"])
say(s.value())
'''
    out = run_tb(src)
    assert out == ['calm ["happy", "calm", "tired"]', "tired", "a"]
    widget = fake.window_.find("select")
    widget.choose("b")
    assert out[-1] == "picked b"


def test_select_rejects_unknown_choice(fake):
    err = tb_error('import gui\nlet w = gui.window("x")\nlet s = w.select(["a", "b"])\ns.set("c")')
    assert 'select.set() needs one of the choices (a, b), not "c".' in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nw.select(["a"], {"value": "z"})')
    assert "select() needs one of the choices (a)" in str(err)


def test_table(fake):
    src = '''import gui
let win = gui.window("x")
let t = win.table(["Name", "Score"], {"on_select": fn(row, i) { say("selected", row, i) }, "on_open": fn(row) { say("open", row[0]) }})
t.add_row(["Tanisha", 10])
t.add_row(["TB", 2])
t.add_row(["short"])
say(t.rows(), t.count(), t.columns)
say(t.selected())
t.select(1)
say(t.selected(), t.selected_index())
t.remove(0)
say(t.rows())
'''
    out = run_tb(src)
    assert out[0] == '[["Tanisha", 10], ["TB", 2], ["short"]] 3 ["Name", "Score"]'
    assert out[1] == "nil"
    assert out[2] == '["TB", 2] 1'
    assert out[3] == '[["TB", 2], ["short"]]'
    widget = fake.window_.find("table")
    assert widget.rows == [["TB", "2"], ["short", ""]]  # shown as text, padded
    widget.choose(0)
    widget.open(1)
    assert out[4:] == ['selected ["TB", 2] 0', "open short"]


def test_table_set_rows_clear_and_errors(fake):
    src = '''import gui
let win = gui.window("x")
let t = win.table(["A"])
t.set_rows([[1], [2], [3]])
say(t.count())
t.clear()
say(t.rows())
'''
    assert run_tb(src) == ["3", "[]"]
    err = tb_error('import gui\nlet w = gui.window("x")\nlet t = w.table(["A"])\nt.add_row([1, 2])')
    assert "table.add_row() got a row with 2 values, but the table has 1 column." in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nlet t = w.table(["A"])\nt.remove(0)')
    assert "table.remove() got index 0, but there are 0 rows (the first is 0)." in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nw.table([])')
    assert "table() needs at least one column name" in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nw.table("A")')
    assert "table() needs columns to be a list, not text." in str(err)


def test_list_box(fake):
    src = '''import gui
let win = gui.window("x")
let l = win.list_box(["red", "green"], {"on_select": fn(item, i) { say("chose", item, i) }})
l.add("blue")
say(l.items(), l.count())
l.select(2)
say(l.selected())
l.remove(0)
say(l.items(), l.selected())
'''
    out = run_tb(src)
    assert out == ['["red", "green", "blue"] 3', "blue", '["green", "blue"] nil']
    widget = fake.window_.find("list_box")
    assert widget.items == ["green", "blue"]
    widget.choose(1)
    assert out[-1] == "chose blue 1"
    run_tb('import gui\nlet w = gui.window("y")\nlet l = w.list_box({"height": 3})\nsay(l.items())')


def test_progress_clamps(fake):
    out = run_tb('import gui\nlet w = gui.window("x")\nlet p = w.progress({"value": 30})\nsay(p.value())\np.set(150)\nsay(p.value())\np.set(-5)\nsay(p.value())')
    assert out == ["30", "100", "0"]
    assert "progress.set() needs value to be a number, not text." in str(
        tb_error('import gui\nlet w = gui.window("x")\nlet p = w.progress()\np.set("lots")'))


def test_image(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "star.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "photo.jpg").write_bytes(b"jpg")
    out = run_tb('import gui\nlet w = gui.window("x")\nlet i = w.image("star.png")\nsay(i.kind)')
    assert out == ["image"]
    assert fake.window_.find("image").text == "star.png"
    err = tb_error('import gui\nlet w = gui.window("x")\nw.image("nope.png")')
    assert "I couldn't find the picture 'nope.png'." in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nw.image("photo.jpg")')
    assert "image() shows PNG and GIF pictures, and 'photo.jpg' is a .jpg file." in str(err)


def test_image_next_to_the_script(fake, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "logo.gif").write_bytes(b"GIF89a")
    tb.run('import gui\nlet w = gui.window("x")\nw.image("logo.gif")', output=lambda t: None,
           path=str(tmp_path / "app" / "main.tb"))
    assert fake.window_.find("image").text == str(tmp_path / "app" / "logo.gif")


# -- window extras -------------------------------------------------------------


def test_menu(fake):
    src = '''import gui
let win = gui.window("x")
win.menu({"File": {"Open": fn() { say("open") }, "-": nil, "More": {"Deep": fn() { say("deep") }}, "Quit": win.close}})
'''
    out = run_tb(src)
    w = fake.window_
    assert [m[0] for m in w.menu] == ["File"]
    assert [i if i is None else i[0] for i in w.menu[0][1]] == ["Open", None, "More", "Quit"]
    w.choose_menu("File", "Open")
    w.choose_menu("File", "More", "Deep")
    assert out == ["open", "deep"]
    w.choose_menu("File", "Quit")
    assert w.open is False


def test_menu_errors(fake):
    err = tb_error('import gui\nlet w = gui.window("x")\nw.menu({"File": {"Open": 3}})')
    assert 'window.menu() needs a function (or a map for a sub-menu) for "Open", not number.' in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nw.menu({"File": 1})')
    assert 'window.menu() needs the "File" menu to be a map, not a number.' in str(err)


def test_status(fake):
    out = run_tb('import gui\nlet w = gui.window("x")\nlet s = w.status("Ready")\nsay(s.value())\ns.set("Saved")\nsay(w.status().value())')
    assert out == ["Ready", "Saved"]
    assert fake.window_.status_text == "Saved"


def test_timers(fake):
    src = '''import gui
let win = gui.window("x")
let n = 0
let t = win.every(100, fn() {
  n = n + 1
  say("tick", n)
  if n == 3 { t.stop() }
})
win.after(250, fn() { say("once") })
'''
    out = run_tb(src)
    fake.advance(1000)
    assert out == ["tick 1", "tick 2", "once", "tick 3"]


def test_timer_errors(fake):
    err = tb_error('import gui\nlet w = gui.window("x")\nw.every(1, fn() { })')
    assert "window.every() needs at least 10 milliseconds, not 1." in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nw.after(10, 5)')
    assert "window.after() needs fn to be a function, not a number." in str(err)


def test_timers_stop_when_window_closes(fake):
    out = run_tb('import gui\nlet w = gui.window("x")\nw.every(100, fn() { say("tick") })\nw.after(150, w.close)')
    fake.advance(1000)
    assert out == ["tick"]


def test_keys(fake):
    out = run_tb('import gui\nlet w = gui.window("x")\nw.key("Ctrl+S", fn() { say("saved") })\nw.key("+", fn() { say("plus") })')
    fake.window_.press("Ctrl+S")
    fake.window_.press("+")
    assert out == ["saved", "plus"]
    err = tb_error('import gui\nlet w = gui.window("x")\nw.key("Hyper+Q", fn() { })')
    assert 'window.key() doesn\'t understand "Hyper+Q".' in str(err)


@pytest.mark.parametrize("combo, seq", [
    ("Ctrl+S", "<Control-s>"), ("ctrl+shift+s", "<Control-S>"), ("Enter", "<Return>"),
    ("Escape", "<Escape>"), ("F5", "<F5>"), ("7", "<Key-7>"), ("+", "<plus>"), ("Ctrl++", "<Control-plus>"),
    ("Alt+Left", "<Alt-Left>"), ("Cmd+O", "<Command-o>"), ("Shift+Tab", "<Shift-Tab>"),
    ("Mod+Q", "<Command-q>" if sys.platform == "darwin" else "<Control-q>"),
    ("", None), ("Ctrl+", None), ("Super+S", None), ("F99", None), ("ab", None),
])
def test_key_sequence(combo, seq):
    assert gui.key_sequence(combo) == seq


# -- errors --------------------------------------------------------------------


def test_errors_in_callbacks_are_friendly(fake, capsys):
    src = '''import gui
let win = gui.window("x")
win.button("Boom", fn() {
  let x = 1 + nil
})
win.button("Fine", fn() { say("still here") })
'''
    out = run_tb(src)
    fake.window_.find("button", "Boom").click()
    err = capsys.readouterr().err
    assert err.startswith("Oops! On line 4:")
    assert "Traceback" not in err
    assert fake.dialogs[-1]["kind"] == "error" and fake.dialogs[-1]["title"] == "Oops!"
    assert fake.dialogs[-1]["message"].startswith("On line 4:")
    fake.window_.find("button", "Fine").click()  # the app keeps going
    assert out == ["still here"]


def test_thrown_error_in_callback(fake, capsys):
    run_tb('import gui\nlet w = gui.window("x")\nlet i = w.input({"on_enter": fn(t) { throw "Please type a number" }})')
    fake.window_.find("input").enter()
    assert "Oops! On line 3: Please type a number" in capsys.readouterr().err
    assert fake.dialogs[-1]["message"] == "On line 3: Please type a number"


def test_failing_timer_stops_after_one_error(fake, capsys):
    run_tb('import gui\nlet w = gui.window("x")\nw.every(100, fn() { throw "bad tick" })')
    fake.advance(1000)
    assert capsys.readouterr().err.count("bad tick") == 1
    assert len([d for d in fake.dialogs if d["kind"] == "error"]) == 1


def test_callback_with_too_many_params_reports_error(fake, capsys):
    run_tb('import gui\nlet w = gui.window("x")\nw.button("b", fn(a, b, c) { })')
    fake.window_.find("button").click()
    assert "needs 3 inputs, but got 0" in capsys.readouterr().err


def test_exit_in_callback(fake):
    run_tb('import gui\nlet w = gui.window("x")\nw.button("Quit", fn() { exit(3) })')
    with pytest.raises(tb.TBExit) as info:
        fake.window_.find("button").click()
    assert info.value.code == 3
    assert fake.window_.open is False


def test_exit_inside_running_loop_ends_run(fake):
    # exit() from a callback while win.run() is waiting: windows close, run() exits.
    class LoopWindow(gui.FakeWindow):
        def run(self):
            self.ran = True
            self.find("button").click()

    fake.window = lambda *a: fake.windows.append(LoopWindow(fake, *a)) or fake.windows[-1]
    with pytest.raises(tb.TBExit):
        run_tb('import gui\nlet w = gui.window("x")\nw.button("Quit", fn() { exit(0) })\nw.run()\nsay("not reached")')
    assert fake.window_.open is False


def test_closed_window(fake):
    err = tb_error('import gui\nlet w = gui.window("x")\nw.close()\nsay(w.is_open())\nw.label("late")')
    assert "That window is closed, so I can't add to it any more." in str(err)


def test_wrong_argument_types(fake):
    cases = [
        ('gui.window(nil)', "gui.window() needs title to be text or a number, not nil."),
        ('gui.window("x", "big")', "gui.window() needs options to be a map, not text."),
        ('gui.window("x", {"colour": "red"})', 'gui.window() doesn\'t know the option "colour". Options: width, height, theme, resizable, accent.'),
        ('gui.window("x", {"theme": "pink"})', 'gui.window() knows the themes "light", "dark" and "system", not "pink".'),
        ('gui.window("x", {"accent": "red"})', 'gui.window() needs the "accent" option to be a colour like "#b0123f"'),
        ('gui.window("x", {"width": 20})', "gui.window() needs a width between 100 and 5000 pixels, not 20."),
        ('gui.window("x", {"width": "wide"})', 'gui.window() needs the "width" option to be a whole number, not text.'),
        ('gui.window("x").button("b", 5)', "button() needs on_click to be a function or nil, not a number."),
        ('gui.window("x").input({"on_enter": "yes"})', 'input() needs the "on_enter" option to be a function, not text.'),
        ('gui.window("x").label("a", {"style": "huge"})', "label() knows the styles normal, title, heading"),
        ('gui.window("x").select("abc")', "select() needs choices to be a list, not text."),
        ('gui.window("x").label()', "needs"),
        ('gui.alert()', "needs"),
        ('gui.open_file(5)', "gui.open_file() needs filters to be a list"),
    ]
    for src, message in cases:
        assert message in str(tb_error("import gui\n" + src)), src


def test_errors_can_be_caught(fake):
    out = run_tb('import gui\ntry {\n  gui.window("x", {"theme": "pink"})\n} catch err {\n  say("caught:", err.line)\n}')
    assert out == ["caught: 3"]


# -- dialogs -------------------------------------------------------------------


def test_dialogs(fake):
    fake.answers = [False, "Tanisha", None, "/tmp/a.txt", "/tmp/out.txt", "/tmp"]
    src = '''import gui
gui.alert("Saved!")
say(gui.confirm("Delete it?"))
say(gui.ask("Your name?", "T"))
say(gui.ask("Again?"))
say(gui.open_file([".txt", "md"]))
say(gui.save_file("notes.txt", {"Text": "*.txt"}))
say(gui.pick_folder())
'''
    assert run_tb(src) == ["false", "Tanisha", "nil", "/tmp/a.txt", "/tmp/out.txt", "/tmp"]
    kinds = [d["kind"] for d in fake.dialogs]
    assert kinds == ["alert", "confirm", "ask", "ask", "open_file", "save_file", "pick_folder"]
    assert fake.dialogs[0]["message"] == "Saved!"
    assert fake.dialogs[4]["filters"] == [("Files", "*.txt *.md"), ("All files", "*")]
    assert fake.dialogs[5]["filters"] == [("Text", "*.txt"), ("All files", "*")]
    assert fake.dialogs[5]["name"] == "notes.txt"


def test_cancelled_dialogs_give_nil(fake):
    assert run_tb('import gui\nsay(gui.open_file(), gui.save_file(), gui.pick_folder())') == ["nil nil nil"]


def test_available(fake):
    assert run_tb("import gui\nsay(gui.available(), gui.THEMES)") == ['true ["dark", "light", "system"]']


# -- environments --------------------------------------------------------------


def test_browser_refuses(fake, monkeypatch):
    monkeypatch.setattr(sys, "platform", "emscripten")
    for src in ('gui.window("x")', 'gui.alert("hi")', "gui.open_file()"):
        err = tb_error("import gui\n" + src)
        assert "The gui library needs the TB app on your computer" in str(err)
    assert run_tb("import gui\nsay(gui.available())") == ["false"]


def test_no_tkinter_is_friendly(monkeypatch):
    def missing():
        raise gui.GuiUnavailable("The gui library needs tkinter, which isn't part of this Python.")

    previous = gui._use_backend(gui._TkBackend())
    monkeypatch.setattr(gui, "_tk", missing)
    try:
        err = tb_error('import gui\ngui.window("x")')
        assert "Oops! On line 2: The gui library needs tkinter" in str(err)
        assert run_tb("import gui\nsay(gui.available())") == ["false"]
    finally:
        gui._use_backend(previous)


def test_doc_loads_without_tkinter():
    info = tb.describe_library("gui")
    names = [f["name"] for f in info["functions"]]
    assert names == ["window", "alert", "confirm", "ask", "open_file", "save_file", "pick_folder", "available", "copy", "paste",
                     "notify", "theme", "system_theme", "accent", "scale", "on_main"]
    for fn in info["functions"]:
        assert fn["signature"].startswith(fn["name"] + "(") and fn["doc"]
    assert "button(text, on_click?, options?)" in info["functions"][0]["doc"]
    assert info["doc"] and info["values"][0]["doc"]


def test_autoclose_env(fake, monkeypatch):
    monkeypatch.setenv("TB_GUI_AUTOCLOSE_MS", "500")
    run_tb('import gui\nlet w = gui.window("x")\nw.run()')
    assert fake.window_.open
    fake.advance(499)
    assert fake.window_.open
    fake.advance(1)
    assert not fake.window_.open


# -- real Tk (skipped without tkinter or a screen) -----------------------------


def _tk_ready():
    try:
        gui._tk_root()  # the shared root the backend uses (never a second Tk())
        return True
    except Exception:
        return False


needs_tk = pytest.mark.skipif(not _tk_ready(), reason="tkinter or a display is not available")


@pytest.fixture
def real_tk():
    previous = gui._use_backend(gui._TkBackend())
    yield gui._get_backend()
    gui._use_backend(previous)


@needs_tk
def test_real_window_opens_and_closes(real_tk, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A 1x1 GIF, so image() has something real to load.
    (tmp_path / "dot.gif").write_bytes(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00"
        b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
    src = '''import gui
let win = gui.window("TB smoke test", {"theme": "dark"})
win.label("Hello", {"style": "title"})
let r = win.row()
let box = r.input({"placeholder": "Type here"})
r.button("Add", fn() { say("clicked") }, {"primary": true})
let c = win.checkbox("Done")
let s = win.select(["a", "b"], {"value": "b"})
let t = win.table(["Name", "Score"])
t.add_row(["Tanisha", 10])
t.select(0)
let l = win.list_box(["x", "y"])
let area = win.textarea({"value": "notes"})
area.append("!")
let p = win.progress({"value": 40})
win.image("dot.gif")
win.separator()
win.menu({"File": {"Quit": win.close}})
win.status("Ready")
win.key("Ctrl+Q", win.close)
box.set("typed")
c.set(true)
say(box.value(), c.value(), s.value(), t.selected(), area.value(), p.value(), l.items())
let ticks = 0
win.every(50, fn() { ticks = ticks + 1 })
win.after(300, win.close)
win.run()
say("closed", win.is_open(), ticks > 0)
'''
    out = run_tb(src)
    assert out == ['typed true b ["Tanisha", 10] notes! 40 ["x", "y"]', "closed false true"]


@needs_tk
def test_real_autoclose_and_second_window(real_tk, monkeypatch):
    monkeypatch.setenv("TB_GUI_AUTOCLOSE_MS", "200")
    out = run_tb('import gui\nlet a = gui.window("One")\na.label("first")\na.run()\n'
                 'let b = gui.window("Two", {"theme": "dark"})\nb.label("second")\nb.run()\nsay("both closed")')
    assert out == ["both closed"]


@needs_tk
def test_real_callback_error_is_friendly(real_tk, monkeypatch, capsys):
    shown = []
    monkeypatch.setattr(real_tk, "alert", lambda message, title, error=False: shown.append((title, message)))
    src = '''import gui
let win = gui.window("Errors")
let b = win.button("Boom", fn() { throw "kaboom" })
win.after(50, fn() { b.click() })
win.after(200, win.close)
win.run()
say("survived")
'''
    assert run_tb(src) == ["survived"]
    assert shown == [("Oops!", "On line 3: kaboom")]
    assert "Oops! On line 3: kaboom" in capsys.readouterr().err


# -- added for the SOC apps ------------------------------------------------------


def test_clipboard(fake):
    assert run_tb('import gui\ngui.copy("abc123")\nsay(gui.paste())') == ["abc123"]
    assert fake.clipboard == "abc123"


def test_label_width_and_style_change(fake):
    run_tb('import gui\nlet w = gui.window("x")\nlet l = w.label("ok", {"width": 8, "style": "mono"})\nl.style("success")')
    label = fake.window_.find("label")
    assert label.spec["width"] == 8 and label.spec["style"] == "success"
    err = tb_error('import gui\nlet w = gui.window("x")\nw.label("a").style("loud")')
    assert 'label.style() knows the styles' in str(err)


def test_readonly_textarea(fake):
    out = run_tb('import gui\nlet w = gui.window("x")\nlet t = w.textarea({"readonly": true})\nt.set("shown")\nt.append(" more")\nsay(t.value())')
    assert out == ["shown more"]
    assert fake.window_.find("textarea").spec["readonly"] is True


@needs_tk
def test_real_clipboard_and_styles(real_tk):
    src = '''import gui
let win = gui.window("Clip")
let l = win.label("x", {"width": 10})
l.style("danger")
let t = win.textarea({"readonly": true})
t.set("read only")
gui.copy("from TB")
say(gui.paste(), t.value())
win.after(100, win.close)
win.run()
'''
    assert run_tb(src) == ["from TB read only"]


# -- 3.0: containers -------------------------------------------------------------


def test_tabs(fake):
    src = '''import gui
let win = gui.window("x")
let t = win.tabs(["Overview", "Alerts"], {"on_change": fn(name) { say("now on", name) }})
t.page("Overview").label("hello")
t.page("Alerts").table(["A"])
say(t.pages(), t.selected())
t.select("Alerts")
say(t.selected())
let extra = t.add("More")
extra.button("b")
say(t.pages())
'''
    out = run_tb(src)
    assert out == ['["Overview", "Alerts"] Overview', "Alerts", '["Overview", "Alerts", "More"]']
    tabs = fake.window_.widgets("tabs")[0]
    assert [p.kind for p in tabs.children] == ["page", "page", "page"]
    assert fake.window_.find("label").text == "hello" and fake.window_.find("button").text == "b"
    tabs.choose("Overview")
    assert out[-1] == "now on Overview"
    err = tb_error('import gui\nlet w = gui.window("x")\nlet t = w.tabs(["A"])\nt.page("B")')
    assert 'tabs.page() knows the pages A, not "B".' in str(err)
    assert "tabs() needs at least one page name" in str(tb_error('import gui\ngui.window("x").tabs([])'))
    assert "every page name to be different" in str(tb_error('import gui\ngui.window("x").tabs(["A", "A"])'))


def test_card_scroll_splitter(fake):
    src = '''import gui
let win = gui.window("x")
let c = win.card({"title": "Totals"})
c.label("42", {"style": "display"})
let s = win.scroll({"height": 120})
s.label("long")
let sp = win.splitter({"direction": "vertical"})
let left = sp.pane({"weight": 2})
left.label("L")
sp.pane().label("R")
say(c.kind, s.kind, sp.kind, left.kind)
'''
    assert run_tb(src) == ["card scroll splitter pane"]
    assert body(fake.window_) == [["card", "label"], ["scroll", "label"], ["splitter", ["pane", "label"], ["pane", "label"]]]
    assert fake.window_.widgets("card")[0].spec["title"] == "Totals"
    assert fake.window_.widgets("splitter")[0].panes[0].spec["weight"] == 2
    assert 'splitter() needs direction to be "horizontal" or "vertical"' in str(
        tb_error('import gui\ngui.window("x").splitter({"direction": "diagonal"})'))


# -- 3.0: tree -------------------------------------------------------------------


def test_tree(fake):
    src = '''import gui
let win = gui.window("x")
let t = win.tree(["Name", "Count"], {"on_select": fn(values, id) { say("picked", values, id) }, "on_open": fn(values) { say("open", values[0]) }})
let crit = t.add(nil, ["critical", 2])
let a = t.add(crit, ["Brute force", 1])
let b = t.add(crit, ["Cleared log", 1], {"open": false})
let high = t.add(nil, "high")
say(t.count(), t.children(), t.children(crit), t.parent(a), t.values(b))
t.select(a)
say(t.selected(), t.selected_id())
t.set(a, ["Brute force", 7])
say(t.values(a), t.is_expanded(b))
t.expand(b)
t.collapse(crit)
say(t.is_expanded(b), t.is_expanded(crit))
t.remove(crit)
say(t.ids(), t.selected())
t.clear()
say(t.count())
'''
    out = run_tb(src)
    assert out == [
        '4 ["n1", "n4"] ["n2", "n3"] n1 ["Cleared log", 1]',
        '["Brute force", 1] n2',
        '["Brute force", 7] false',
        "true false",
        '["n4"] nil',
        "0",
    ]
    run_tb('''import gui
let win = gui.window("y")
let t = win.tree(["A"], {"on_select": fn(v, id) { say("sel", id) }, "on_open": fn(v, id) { say("opened", id) }})
t.add(nil, ["root"])
t.add("n1", ["child"])
''')
    widget = fake.window_.find("tree")
    assert widget.nodes["n2"] == {"parent": "n1", "values": ["child", ], "open": True}
    widget.choose("n2")
    widget.open("n1")
    err = tb_error('import gui\nlet w = gui.window("x")\nlet t = w.tree(["A"])\nt.add("zzz", ["x"])')
    assert 'tree.add() got the id "zzz", but there is no such row in the tree.' in str(err)
    err = tb_error('import gui\nlet w = gui.window("x")\nlet t = w.tree(["A"])\nt.add(nil, [1, 2])')
    assert "tree.add() got a row with 2 values, but the tree has 1 column." in str(err)


def test_tree_events_reach_tb(fake):
    out = run_tb('''import gui
let win = gui.window("y")
let t = win.tree(["A"], {"on_select": fn(v, id) { say("sel", v, id) }, "on_open": fn(v, id) { say("opened", id) }})
t.add(nil, ["root"])
''')
    widget = fake.window_.find("tree")
    widget.choose("n1")
    widget.open("n1")
    assert out == ['sel ["root"] n1', "opened n1"]


# -- 3.0: chart and canvas ------------------------------------------------------


def test_chart(fake):
    src = '''import gui
let win = gui.window("x")
let c = win.chart({"type": "bar", "labels": ["crit", "high"], "data": [2, 5], "title": "Alerts"})
say(c.type(), c.data(), c.labels())
c.update({"data": {"today": [1, 2], "yesterday": [3, 4]}, "type": "line"})
say(c.type(), c.data())
let pie = win.chart({"type": "pie", "data": [1, 1], "labels": ["a", "b"]})
say(pie.type())
let empty = win.chart()
say(empty.type(), empty.data())
'''
    out = run_tb(src)
    assert out == ['bar [2, 5] ["crit", "high"]', 'line {"today": [1, 2], "yesterday": [3, 4]}', "pie", "bar []"]
    chart = fake.window_.widgets("chart")[0]
    assert chart.data["type"] == "line" and chart.data["data"] == {"today": [1, 2], "yesterday": [3, 4]}
    assert chart.data["title"] == "Alerts"
    assert run_tb('import gui\nsay(gui.window("x").chart({"data": {"a": [1, nil]}}).data())') == ['{"a": [1, nil]}']
    assert 'chart() draws "bar", "line" or "pie" charts, not "donut".' in str(
        tb_error('import gui\ngui.window("x").chart({"type": "donut"})'))
    assert 'chart() needs each value in data to be a number or nil, not text.' in str(
        tb_error('import gui\ngui.window("x").chart({"data": ["x"]})'))
    assert "chart() draws a pie from one list of numbers, not several series." in str(
        tb_error('import gui\ngui.window("x").chart({"type": "pie", "data": {"a": [1], "b": [2]}})'))


def test_chart_helpers():
    assert gui.nice_ticks(0, 7) == [0, 2, 4, 6, 8]
    assert gui.nice_ticks(0, 2, 4) == [0, 0.5, 1, 1.5, 2] and gui.nice_ticks(0, 2, 4, whole=True) == [0, 1, 2]
    assert gui.nice_ticks(0, 100)[-1] == 100 and gui.nice_ticks(3, 3)
    assert gui.chart_series([1, 2], ["a"]) == (["a", "2"], [("", [1.0, 2.0])])
    labels, series = gui.chart_series({"x": [1], "y": [2, 3]}, None)
    assert labels == ["1", "2"] and [n for n, _ in series] == ["x", "y"]
    assert gui.chart_series({"up": [3, None, 4]}, None)[1] == [("up", [3.0, None, 4.0])]  # nil is a gap
    dark = gui.chart_colours(gui.THEMES["dark"]["accent"])
    assert dark[0] == gui.THEMES["dark"]["accent"] and "#b0123f" not in dark  # no near-twin of the accent
    assert gui.chart_colours("#2a7de1")[1] == "#b0123f"
    assert gui.label_step([80, 30, 60, 20, 90], 82) == 1  # short neighbours leave room for long labels
    assert gui.label_step([80] * 6, 50) == 2 and gui.label_step([], 10) == 1
    assert gui.pie_slices(["a", "b", "c"], [1, 2, 3], 5) == (["a", "b", "c"], [1, 2, 3])
    assert gui.pie_slices(["a", "b", "c", "d"], [1, 5, 3, None], 3) == (["b", "c", "Other"], [5, 3, 1])


def test_canvas(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dot.gif").write_bytes(b"GIF89a")
    src = '''import gui
let win = gui.window("x")
let cv = win.canvas(200, 100, {"on_click": fn(x, y) { say("click", x, y) }, "on_drag": fn(x, y) { say("drag", x, y) }, "background": "#fff"})
let a = cv.line(0, 0, 10, 10, {"color": "#ff0000", "width": 2})
let b = cv.rect(5, 5, 20, 10, {"fill": "#00ff00"})
cv.oval(0, 0, 4, 4)
cv.text(2, 2, "hi", {"size": 14, "bold": true, "anchor": "center"})
cv.image(1, 1, "dot.gif")
say(a, b, cv.size())
cv.move(b, 5, 5)
cv.remove(a)
say(len(cv.size()))
'''
    out = run_tb(src)
    assert out == ["1 2 [200, 100]", "2"]
    cv = fake.window_.find("canvas")
    kinds = [d[1] for d in cv.drawn]
    assert kinds == ["rect", "oval", "text", "image"]
    assert cv.drawn[0][2:4] == (10, 10)  # moved
    cv.click(3, 4)
    cv.drag(7, 8)
    assert out[-2:] == ["click 3 4", "drag 7 8"]
    cv.clear()
    assert cv.drawn == []
    assert 'canvas.line() needs the "color" option to be a colour like "#b0123f"' in str(
        tb_error('import gui\ngui.window("x").canvas().line(0, 0, 1, 1, {"color": "red"})'))
    assert "canvas.text() needs anchor to be one of" in str(
        tb_error('import gui\ngui.window("x").canvas().text(0, 0, "a", {"anchor": "up"})'))
    run_tb('import gui\nlet c = gui.window("x").canvas({"width": 50})\nsay(c.size())')


# -- 3.0: small inputs ----------------------------------------------------------


def test_slider_and_spinner(fake):
    src = '''import gui
let win = gui.window("x")
let s = win.slider(0, 10, {"value": 3, "step": 0.5, "on_change": fn(v) { say("slid", v) }, "show_value": true})
let n = win.spinner(1, 5, {"value": 2, "on_change": fn(v) { say("spun", v) }})
say(s.value(), s.min, s.max, n.value())
s.set(99)
n.set(-4)
say(s.value(), n.value())
'''
    out = run_tb(src)
    assert out == ["3 0 10 2", "10 1"]
    fake.window_.find("slider").slide(7)
    fake.window_.find("spinner").slide(4)
    assert out[-2:] == ["slid 7", "spun 4"]
    assert "slider() needs max to be bigger than min, not 5 to 5." in str(tb_error('import gui\ngui.window("x").slider(5, 5)'))
    assert "spinner() needs step to be more than 0, not 0." in str(tb_error('import gui\ngui.window("x").spinner(0, 5, {"step": 0})'))
    assert "slider.set() needs value to be a number, not text." in str(
        tb_error('import gui\ngui.window("x").slider(0, 1).set("big")'))


def test_date_picker(fake):
    import datetime
    src = '''import gui
let win = gui.window("x")
let d = win.date_picker({"value": "2026-03-14", "on_change": fn(v) { say("date", v) }})
say(d.value())
d.set("today")
say(len(d.value()))
'''
    out = run_tb(src)
    assert out == ["2026-03-14", "10"]
    assert fake.window_.find("date_picker").text == datetime.date.today().isoformat()
    fake.window_.find("date_picker").type("2027-01-02")
    assert out[-1] == "date 2027-01-02"
    assert 'date_picker.set() needs a date like "2026-03-14" (year-month-day), not "soon".' in str(
        tb_error('import gui\ngui.window("x").date_picker().set("soon")'))
    assert "date_picker() needs a date like" in str(tb_error('import gui\ngui.window("x").date_picker({"value": "14/03/2026"})'))


def test_color_link_badge(fake):
    fake.answers = ["#00ff00"]
    src = '''import gui
let win = gui.window("x")
let c = win.color({"value": "#abc", "on_change": fn(v) { say("colour", v) }})
say(c.value())
c.set("#123456")
let l = win.link("TB site", "https://code.tanisha.ceo", {"on_click": fn() { say("clicked link") }})
let plain = win.link("no url")
let b = win.badge("3 critical", {"style": "danger"})
b.set("none")
b.style("success")
say(c.value(), l.value(), b.value())
l.open()
'''
    out = run_tb(src)
    assert out == ["#aabbcc", "#123456 TB site none", "clicked link"]
    assert fake.opened_urls == ["https://code.tanisha.ceo"]
    fake.window_.find("color").click()
    assert out[-1] == "colour #00ff00" and fake.dialogs[-1]["kind"] == "pick_colour"
    assert fake.window_.find("badge").spec["style"] == "success"
    assert 'badge() knows the styles accent, success, danger, warning, muted, info, not "loud".' in str(
        tb_error('import gui\ngui.window("x").badge("x", {"style": "loud"})'))
    assert 'link() needs url to start with http://' in str(tb_error('import gui\ngui.window("x").link("a", "ftp://x")'))
    assert 'color.set() needs a colour like "#b0123f"' in str(tb_error('import gui\ngui.window("x").color().set("blue")'))


# -- 3.0: window extras -----------------------------------------------------------


def test_window_extras(fake, tmp_path):
    (tmp_path / "icon.png").write_bytes(b"\x89PNG")
    src = f'''import gui
let win = gui.window("x", {{"accent": "#2a7de1"}})
win.icon({json.dumps(str(tmp_path / 'icon.png'))})
win.always_on_top()
win.fullscreen(true)
say(win.is_fullscreen())
win.fullscreen(false)
win.center()
win.resize(800, 600)
say(win.size(), win.theme())
win.on_key(fn(key) {{ say("key", key) }})
win.bind_shortcut("F2", fn() {{ say("f2") }})
'''
    out = run_tb(src)
    assert out == ["true", "[800, 600] light"]
    w = fake.window_
    assert w.icon.endswith("icon.png") and w.topmost and not w.fullscreen and w.centered and w.accent == "#2a7de1"
    w.press("F2")
    w.press("a")
    assert out[-3:] == ["key F2", "f2", "key a"]
    assert "I couldn't find the icon 'nope.png'." in str(tb_error('import gui\ngui.window("x").icon("nope.png")'))
    assert "window.resize() needs a width between 100 and 5000 pixels, not 10." in str(
        tb_error('import gui\ngui.window("x").resize(10, 300)'))


def test_on_close_can_refuse(fake):
    out = run_tb('''import gui
let win = gui.window("x")
let dirty = true
win.on_close(fn() {
  if dirty { say("not yet"); return false }
  return true
})
''')
    w = fake.window_
    assert w.request_close() is False and w.open
    assert out == ["not yet"]
    run_tb('import gui\nlet w = gui.window("y")\nw.on_close(fn() { say("bye") })')
    assert fake.window_.request_close() is True and not fake.window_.open


def test_on_drop_and_paths_in_text(fake, tmp_path):
    a = tmp_path / "a.log"
    a.write_text("x")
    out = run_tb('import gui\nlet w = gui.window("x")\nw.on_drop(fn(paths) { say("dropped", len(paths), paths[0]) })')
    fake.window_.drop([str(a)])
    assert out == [f"dropped 1 {a}"]
    assert gui.paths_in_text(f"{a}\nnot/a/file\nfile://{a}\n") == [str(a), str(a)]
    assert gui.paths_in_text("") == []
    run_tb('import gui\nlet w = gui.window("x")\nw.on_paste_paths(fn(paths) { })')


def test_tray(fake):
    out = run_tb('''import gui
let win = gui.window("Monitor")
let tray = win.tray({"Show": fn() { tray.restore(); say("shown") }, "-": nil, "Quit": win.close}, {"title": "Mon"})
tray.minimize()
say(tray.visible(), win.is_open())
''')
    assert out == ["true true"]
    w = fake.window_
    assert w.hidden and w.tray_widget.title == "Mon"
    w.tray_widget.choose("Show")
    assert out[-1] == "shown" and not w.hidden and not w.tray_widget.shown
    w.tray_widget.choose("Quit")
    assert not w.open
    assert "window.tray() menus can't have sub-menus" in str(
        tb_error('import gui\ngui.window("x").tray({"More": {"A": fn() { }}})'))


def test_hide_show(fake):
    run_tb('import gui\nlet w = gui.window("x")\nw.hide()\nw.show()')
    assert not fake.window_.hidden


# -- 3.0: background work ----------------------------------------------------------


def test_run_in_background(fake):
    src = '''import gui
let win = gui.window("x")
let label = win.label("waiting")
win.run_in_background(fn() { return 6 * 7 }, fn(result) { label.set(f"got {result}") })
say(label.value())
'''
    out = run_tb(src)
    assert out == ["waiting"]
    fake.advance(0)
    assert fake.window_.find("label").text == "got 42"


def test_run_in_background_errors(fake, capsys):
    out = run_tb('''import gui
let win = gui.window("x")
win.run_in_background(fn() { throw "no network" }, fn(r) { say("never") }, fn(message) { say("failed:", message) })
win.run_in_background(fn() { let x = 1 + nil }, fn(r) { say("never") })
''')
    fake.advance(0)
    assert out == ["failed: no network"]
    err = capsys.readouterr().err
    assert "Oops! On line 4" in err and "Traceback" not in err
    assert fake.dialogs[-1]["kind"] == "error"
    assert "window.run_in_background() needs fn to be a function, not text." in str(
        tb_error('import gui\ngui.window("x").run_in_background("work")'))


def test_run_in_background_with_a_real_thread(fake):
    fake.threads = True
    out = run_tb('''import gui
let win = gui.window("x")
win.run_in_background(fn() { return "done" }, fn(r) { say("result", r) })
''')
    import time
    for _ in range(100):
        if fake.window_._pending:
            break
        time.sleep(0.01)
    fake.window_.deliver()
    assert out == ["result done"]


def test_on_main(fake):
    out = run_tb('import gui\nlet w = gui.window("x")\ngui.on_main(fn() { say("on main") })\nsay("first")')
    assert out == ["first"]
    fake.advance(0)
    assert out == ["first", "on main"]
    fake.window_.close()
    run_tb('import gui\ngui.on_main(fn() { say("queued") })')  # no open window: queued on the backend
    assert len(fake.main_queue) == 1


# -- 3.0: notify, theme, accent, scale ----------------------------------------------


def test_notify(fake):
    run_tb('import gui\ngui.notify("Alert", "3 new critical alerts")\ngui.notify("Just a title")')
    assert fake.notifications == [{"title": "Alert", "text": "3 new critical alerts"}, {"title": "Just a title", "text": ""}]


def test_notify_falls_back_to_alert(fake, monkeypatch):
    monkeypatch.setattr(fake, "notify", lambda title, text: False)
    run_tb('import gui\ngui.notify("Alert", "body")')
    assert fake.dialogs[-1] == {"kind": "alert", "title": "Alert", "message": "body"}


def test_os_notify_never_raises(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("no such program")

    monkeypatch.setattr(subprocess, "run", boom)
    assert gui._os_notify("t", "x") is False
    assert gui._applescript_text('say "hi"') == '"say \\"hi\\""'
    assert gui._xml_text("<a&b>") == "&lt;a&amp;b&gt;"


def test_theme_and_accent_defaults(fake, monkeypatch):
    monkeypatch.setattr(gui, "system_theme", lambda: "dark")
    src = '''import gui
say(gui.theme(), gui.accent())
gui.theme("system")
gui.accent("#2a7de1")
let a = gui.window("a")
gui.theme("light")
gui.accent(nil)
let b = gui.window("b", {"theme": "system"})
say(gui.theme(), gui.accent(), a.theme(), b.theme())
'''
    assert run_tb(src) == ["light nil", "light nil dark dark"]
    assert fake.windows[0].accent == "#2a7de1" and fake.windows[1].accent is None
    assert 'gui.theme() knows the themes "light", "dark" and "system", not "neon".' in str(tb_error('import gui\ngui.theme("neon")'))
    assert 'gui.accent() needs a colour like "#b0123f"' in str(tb_error('import gui\ngui.accent("teal")'))
    assert run_tb("import gui\nsay(gui.system_theme())") == ["dark"]


def test_scale(fake):
    assert run_tb("import gui\nsay(gui.scale())\nsay(gui.scale(1.5))\nsay(gui.scale())") == ["1", "1.5", "1.5"]
    assert "gui.scale() needs a factor between 0.5 and 4, not 9." in str(tb_error("import gui\ngui.scale(9)"))


def test_palette_helpers():
    assert gui._hex_rgb("#fff") == (255, 255, 255) and gui._rgb_hex((0, 128, 255)) == "#0080ff"
    assert gui._mix("#000000", "#ffffff", 0.5) == "#808080"
    assert gui.is_colour("#2a7de1") and not gui.is_colour("red") and not gui.is_colour(5)
    p = gui._palette("dark", "#2a7de1")
    assert p["accent"] == "#2a7de1" and p["accent_hover"] != p["accent"] and p["select"] != gui.THEMES["dark"]["select"]
    assert gui._palette("light", surface=True)["bg"] == gui.THEMES["light"]["surface"]
    assert gui._prefix("light") == "TbLight" and gui._prefix("dark", "#2a7de1", True) == "TbDarkA2a7de1S"
    assert gui.system_theme() in ("light", "dark")
    assert gui.badge_colours(gui.THEMES["light"], "danger")[1] == "#ffffff"


def test_combo_from_event():
    class E:
        def __init__(self, keysym, state=0):
            self.keysym, self.state = keysym, state

    assert gui.combo_from_event(E("a")) == "a"
    assert gui.combo_from_event(E("Return")) == "Enter"
    assert gui.combo_from_event(E("s", 0x4)) == "Ctrl+s"
    assert gui.combo_from_event(E("A", 0x1)) == "A"
    assert gui.combo_from_event(E("F5")) == "F5"
    assert gui.combo_from_event(E("KP_7")) == "7"
    assert gui.combo_from_event(E("plus")) == "+"


def test_new_widgets_inside_tabs_are_findable(fake):
    run_tb('''import gui
let win = gui.window("x")
let t = win.tabs(["A", "B"])
let c = t.page("B").card()
c.badge("ok")
t.page("A").tree(["N"]).add(nil, ["r"])
''')
    assert fake.window_.find("badge").text == "ok"
    assert fake.window_.find("tree").nodes["n1"]["values"] == ["r"]


# -- 3.0: real Tk ---------------------------------------------------------------------


@needs_tk
def test_real_new_widgets(real_tk, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dot.gif").write_bytes(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00"
        b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
    src = '''import gui
gui.accent("#2a7de1")
let win = gui.window("TB 3.0 smoke test", {"theme": "dark", "width": 900, "height": 700})
win.icon("dot.gif")
let tabs = win.tabs(["Overview", "Data", "Draw"], {"expand": true})
let over = tabs.page("Overview")
let cards = over.row()
let c1 = cards.card({"title": "Alerts"})
c1.label("12", {"style": "display"})
c1.badge("3 critical", {"style": "danger"})
let chart = over.chart({"type": "bar", "labels": ["crit", "high", "med"], "data": [3, 5, 4], "title": "By level"})
chart.update({"type": "line", "data": {"today": [1, 2, 3], "yesterday": [2, 2, 1]}})
over.chart({"type": "line", "data": {"gaps": [1, nil, 3, 4, nil], "none": [nil, nil, nil, nil, nil]}})
over.chart({"type": "bar", "data": [nil, 2]})
over.chart({"type": "pie", "data": [5, 4, 3, 3, 2, 2, 2, 1, 1, 1, 1, 1]})
let pie = over.chart({"type": "pie", "labels": ["a", "b"], "data": [1, 3]})
let data = tabs.page("Data")
let sp = data.splitter()
let tree = sp.pane().tree(["Name", "Count"])
let root = tree.add(nil, ["critical", 2])
tree.add(root, ["Brute force", 1])
tree.select(root)
let right = sp.pane()
let s = right.scroll({"height": 100})
s.label("scrolls")
right.slider(0, 100, {"value": 40, "show_value": true})
right.spinner(0, 9, {"value": 3})
right.date_picker({"value": "2026-03-14"})
right.color({"value": "#2a7de1"})
right.link("TB", "https://code.tanisha.ceo")
let cv = tabs.page("Draw").canvas(300, 200)
cv.rect(10, 10, 50, 30, {"fill": "#2a7de1"})
cv.line(0, 0, 100, 100)
cv.text(20, 60, "hello", {"bold": true})
cv.image(5, 5, "dot.gif")
tabs.select("Data")
win.always_on_top(true)
win.always_on_top(false)
win.center()
win.resize(880, 680)
win.on_key(fn(k) { })
win.on_close(fn() { return true })
win.on_drop(fn(paths) { })
let tray = win.tray({"Show": fn() { tray.restore() }, "Quit": win.close})
tray.minimize()
tray.restore()
let got = nil
win.run_in_background(fn() { return 21 * 2 }, fn(r) { got = r })
gui.on_main(fn() { say("main ran") })
let before = f"{tabs.selected()} {tree.selected()} {chart.type()} {pie.type()} {cv.size()} {win.size()[0] >= 100}"
win.after(600, win.close)
win.run()
say(before, got)
'''
    out = run_tb(src)
    assert out == ["main ran", 'Data ["critical", 2] line pie [300, 200] true 42']
    gui._get_backend()  # still the real backend
    run_tb("import gui\ngui.accent(nil)")


@needs_tk
def test_real_scale_and_system_theme(real_tk):
    out = run_tb('import gui\nlet s = gui.scale()\nsay(s > 0)\nsay(gui.system_theme() in ["light", "dark"])')
    assert out == ["true", "true"]


# -- native file drop and tray icons (tblib/_gui_native.py) --------------------


class _NoTkdnd:
    """Just enough of a Tk toplevel for enable_drop: tkdnd is not installed."""
    class tk:
        @staticmethod
        def call(*args):
            raise RuntimeError("can't find package tkdnd")


def test_native_hooks_fall_back_quietly(monkeypatch):
    from tblib import _gui_native as native
    monkeypatch.setattr(native.sys, "platform", "linux")
    assert native.enable_drop(_NoTkdnd(), lambda paths: None) is None
    assert native.make_tray(_NoTkdnd(), "TB", [("Quit", lambda: None)], lambda: None) is None


@needs_tk
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_real_windows_drop_and_tray(real_tk, tmp_path):
    """Sends the window a real HDROP, as Explorer does, and adds a tray icon."""
    import ctypes
    import struct
    from ctypes import wintypes as wt
    a = tmp_path / "dropped file.txt"
    a.write_text("x")
    w = real_tk.window("Drop test", 300, 200, "light", True)
    got, result = [], {}
    w.on_drop(got.append)
    assert w.drop_kind == "windows"
    tray = w.tray("TB tray", [("Show", lambda: None), None, ("Quit", w.close)])
    assert tray.native == "windows"

    def go():
        hook = w.top._tb_drop_state["hook"]
        result["hooked"] = hook is not None
        u, k = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
        get = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        get.restype, get.argtypes = ctypes.c_ssize_t, [wt.HWND, ctypes.c_int]
        result["accepts"] = bool(get(hook.hwnd, -20) & 0x10)    # GWL_EXSTYLE has WS_EX_ACCEPTFILES
        k.GlobalAlloc.restype, k.GlobalAlloc.argtypes = ctypes.c_void_p, [wt.UINT, ctypes.c_size_t]
        k.GlobalLock.restype, k.GlobalLock.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        names = (str(a) + "\0\0").encode("utf-16-le")
        h = k.GlobalAlloc(0x0042, 20 + len(names))             # GHND; DragFinish frees it
        p = k.GlobalLock(h)
        ctypes.memmove(p, struct.pack("<IiiII", 20, 0, 0, 0, 1), 20)   # DROPFILES, fWide = 1
        ctypes.memmove(p + 20, names, len(names))
        k.GlobalUnlock(h)
        u.PostMessageW.restype = wt.BOOL
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        # Posted, so Tk's own message loop dispatches it, as with a real drop.
        result["posted"] = bool(u.PostMessageW(hook.hwnd, 0x0233, h, 0))   # WM_DROPFILES
        tray.minimize()      # NIM_ADD may fail on a desktop without a taskbar; that is not an error
        result["hidden"] = not w.is_shown()
        u.PostMessageW(tray.icon.hwnd, 0x8051, 0, 0x0203)       # a double click on the icon restores
        w.top.after(500, finish)

    def finish():
        result["restored"] = w.is_shown() and not tray.shown
        w.close()

    w.top.after(300, go)
    w.run()
    assert result == {"hooked": True, "accepts": True, "posted": True, "hidden": True, "restored": True}
    assert got == [[str(a)]]
    assert tray.icon.hwnd is None    # close() removed the icon and its message window


@needs_tk
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_real_macos_drop_and_status_item(real_tk, tmp_path):
    """Calls the drag methods through the Objective-C runtime with a pasteboard
    holding a file URL, and drives the menu bar item's native menu."""
    import ctypes
    from tblib import _gui_native as native
    a = tmp_path / "dropped file.txt"
    a.write_text("x")
    w = real_tk.window("Drop test", 300, 200, "light", True)
    got, chosen, result = [], [], {}
    w.on_drop(got.append)
    assert w.drop_kind == "macos"
    tray = w.tray("TB tray", [("Show", lambda: chosen.append("show")), None,
                              ("Quit", lambda: chosen.append("quit"))])
    assert tray.native == "macos" and not tray.shown
    m = native._mac()
    pb = m.send(m.cls("NSPasteboard"), "pasteboardWithUniqueName")
    m.send(pb, "clearContents")
    url = m.send(m.cls("NSURL"), "fileURLWithPath:", m.string(str(a)), argtypes=(ctypes.c_void_p,))
    m.send(pb, "writeObjects:", m.send(m.cls("NSArray"), "arrayWithObject:", url, argtypes=(ctypes.c_void_p,)),
           restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,))
    cls = m.cls("TBTestDragInfo")
    if not cls:   # a stand-in for the NSDraggingInfo AppKit passes
        cls = m.objc.objc_allocateClassPair(m.cls("NSObject"), b"TBTestDragInfo", 0)
        imp = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(lambda s, c: m.test_pb)
        m.imps.append(imp)
        m.objc.class_addMethod(cls, m.sel("draggingPasteboard"), ctypes.cast(imp, ctypes.c_void_p), b"@@:")
        m.objc.objc_registerClassPair(cls)
    m.test_pb = pb
    sender = m.send(m.send(cls, "alloc"), "init")

    def go():
        win = w.top._tb_drop_state["window"]
        types = m.send(win, "registeredDraggedTypes")
        result["types"] = m.text(m.send(types, "objectAtIndex:", 0, argtypes=(ctypes.c_ulong,)))
        result["entered"] = m.send(win, "draggingEntered:", sender, restype=ctypes.c_ulong,
                                   argtypes=(ctypes.c_void_p,))
        tray.minimize()
        item = tray.icon.item
        result["visible"] = m.send(item, "isVisible", restype=ctypes.c_bool)
        result["title"] = m.text(m.send(m.send(item, "button"), "title"))
        menu = m.send(item, "menu")
        # Like AppKit, call the drop and the menu items from Cocoa's run loop
        # (inside Tk's event loop), not from inside a Python callback.
        later = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double)
        m.send(win, "performSelector:withObject:afterDelay:", m.sel("performDragOperation:"), sender, 0.0,
               restype=None, argtypes=later)
        for index in (2, 0):
            entry = m.send(menu, "itemAtIndex:", index, argtypes=(ctypes.c_long,))
            m.send(m.send(entry, "target"), "performSelector:withObject:afterDelay:", m.sel("tbChoose:"), entry,
                   0.05 * index, restype=None, argtypes=later)
        w.top.after(500, finish)

    def finish():
        result["hidden"] = not w.is_shown()
        tray.restore()
        w.close()

    w.top.after(300, go)
    w.run()
    assert result == {"types": "public.file-url", "entered": 1, "visible": True, "title": "TB tray", "hidden": True}
    assert got == [[str(a)]] and chosen == ["show", "quit"]
    assert tray.icon.item is None    # close() removed the menu bar item


@needs_tk
def test_real_tab_order_reaches_every_control(real_tk):
    """Tab visits the controls in the order they were added, links included,
    and skips display-only widgets; Enter on a focused link clicks it."""
    w = real_tk.window("Keyboard", 600, 400, "light", True)
    clicks = []
    added = {}
    for kind, spec in [("label", {"text": "Name"}), ("input", {}), ("button", {"text": "Go"}),
                       ("progress", {}), ("checkbox", {"text": "On"}),
                       ("link", {"text": "Help", "on_click": lambda: clicks.append("link")})]:
        added[str(w.body.add(kind, spec).widget)] = kind
    order = []

    def go():
        if not w.body_frame.winfo_viewable():   # Tab skips widgets that are not on screen yet
            w.top.after(100, go)
            return
        widget = w.body_frame
        for _ in range(len(added) + 2):
            widget = widget.tk_focusNext()
            if widget is None or added.get(str(widget)) in order:
                break
            order.append(added.get(str(widget), str(widget)))
        link = next(name for name, kind in added.items() if kind == "link")
        press(w.top.nametowidget(link), 10)

    def press(link, tries):
        # Key events go to the focused widget, and the OS may hand focus over late.
        if clicks or not tries:
            w.close()
            return
        link.focus_force()
        link.event_generate("<Return>")
        w.top.after(100, lambda: press(link, tries - 1))

    w.top.after(300, go)
    w.run()
    assert order == ["input", "button", "checkbox", "link"]
    assert clicks == ["link"]
