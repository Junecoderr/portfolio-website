# todo

A to-do list in a window. Type a task and press Enter (or click **Add**),
double-click a task to tick it off, and delete tasks or clear the finished ones with
the buttons underneath. The list is saved in `todo.json` next to the app,
so it is still there next time.

```
tb apps/todo/main.tb
```

What it shows:

- a window with a title, a hint, a row holding an `input` and a primary
  `button`, a `table`, a row of buttons and a status bar;
- `on_enter` and `on_open` (double-click) events calling TB functions;
- `gui.confirm` before deleting, `gui.alert` when nothing is selected;
- saving and loading with `json` and `fs.here`, and a `try`/`catch` so a
  broken file never stops the app.
