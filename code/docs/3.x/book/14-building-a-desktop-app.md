# Building a desktop app

Some people do not like the terminal. Give your tool a window, with buttons, a table and a status line. The `gui` library makes real windows on Windows, macOS and Linux. Windows need the TB app on your computer, so these blocks have no Run button.

## A window

`gui.window` makes a window. Add parts to it from top to bottom. `win.run()` shows the window and waits until it closes.

```tb norun
import gui

let win = gui.window("Hello", {"width": 360, "height": 200})
win.label("What's your name?")
let box = win.input({"placeholder": "Type here"})
win.button("Say hello", fn() {
  gui.alert(f"Hello, {box.value()}!")
})
win.run()
```

## Keep the logic outside the window

Put the real work in plain functions. Then you can test them without a window, and the window code stays short. This function is the core of the app below. It runs anywhere.

```tb
import logs
import detect

fn find_attacks(path) {
  let records = logs.read(path)
  return detect.brute_force(records).map(a => [a.by, str(a.count), a.level])
}

say(find_attacks("apps/data/auth.log"))
```

## A triage window

This app asks for a log file, finds brute-force attacks and shows them in a table. The status line tells the user what happened.

```tb norun
import gui
import logs
import detect
import fs

fn find_attacks(path) {
  let records = logs.read(path)
  return detect.brute_force(records).map(a => [a.by, str(a.count), a.level])
}

let win = gui.window("SSH triage", {"width": 560, "height": 420})
win.label("Find brute-force attacks in an SSH log", {"style": "title"})
let table = win.table(["Address", "Failures", "Level"])
let status = win.status("Open a log file to start.")

win.button("Open log...", fn() {
  let path = gui.open_file([["Log files", "*.log"]])
  if not path {
    return
  }
  try {
    let rows = find_attacks(path)
    table.set_rows(rows)
    status.set(f"{len(rows)} attack(s) in {fs.name(path)}")
  } catch err {
    gui.alert(err.message, "Could not read the log")
  }
}, {"primary": true})

win.run()
```

## Slow work without a frozen window

A window must answer the user all the time. Run slow work with `parallel.run`. Change the window only on the main thread, with `gui.on_main`.

```tb norun
import gui
import parallel

let win = gui.window("Scan", {"width": 320, "height": 160})
let status = win.status("Ready")
win.button("Start", fn() {
  status.set("Working...")
  parallel.run(fn() {
    parallel.sleep(2)
    gui.on_main(fn() { status.set("Done") })
  })
})
win.run()
```

## Share your app

`tb build` puts your app and the TB runtime in one folder. People can run it without installing TB.

```text
tb build triage.tb
```

The [desktop app tutorial](site:guide-desktop-app.html) builds a to-do app with saved data. The [gui reference](site:lib/gui.html) lists every widget.

## What you learned

- `gui.window` and its parts make a real window.
- Keep the logic in plain functions, and test it without a window.
- `parallel.run` and `gui.on_main` keep the window fast. `tb build` shares the app.

This is the end of the book. Keep going with the [SOC cookbook](site:cookbook/index.html).
