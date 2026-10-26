"""Tests for the gui library.

Most tests swap in tblib.gui.FakeBackend, so they need no screen: they build
windows from TB code, then click buttons and type into inputs from Python.
The real-Tk smoke tests at the bottom are skipped when tkinter or a display
is missing (run them with a Python that has Tk, e.g. python3.13 on macOS).
"""

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
let b = win.button("Hug", fn() {
  count = count + 1
  say("hugs:", count)
}, {"primary": true})
'''
    out = run_tb(src)
    btn = fake.window_.find("button", "Hug")
    assert btn.spec["primary"] is True
    btn.click()
    btn.click()
    assert out == ["hugs: 1", "hugs: 2"]


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
    (tmp_path / "heart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "photo.jpg").write_bytes(b"jpg")
    out = run_tb('import gui\nlet w = gui.window("x")\nlet i = w.image("heart.png")\nsay(i.kind)')
    assert out == ["image"]
    assert fake.window_.find("image").text == "heart.png"
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
        ('gui.window("x", {"colour": "red"})', 'gui.window() doesn\'t know the option "colour". Options: width, height, theme, resizable.'),
        ('gui.window("x", {"theme": "pink"})', 'gui.window() knows the themes "light" and "dark", not "pink".'),
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
    assert run_tb("import gui\nsay(gui.available(), gui.THEMES)") == ['true ["dark", "light"]']


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
    assert names == ["window", "alert", "confirm", "ask", "open_file", "save_file", "pick_folder", "available", "copy", "paste"]
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
