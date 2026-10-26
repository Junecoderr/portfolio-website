# notes

A small plain-text editor. Open and save files with the **File** menu, the
buttons at the top, or the keyboard (Ctrl+O, Ctrl+S, Ctrl+N; Cmd on a Mac).
The status bar counts words and characters as you type, and the title shows
a dot when there are unsaved changes. It asks before throwing changes away.

```
tb apps/notes/main.tb
tb apps/notes/main.tb some-file.txt    # open a file straight away
```

What it shows:

- `win.menu` with a separator and a Help menu, `win.key` shortcuts;
- `gui.open_file` / `gui.save_file` with file filters, `gui.confirm`;
- a `textarea` with `on_change`, `win.status`, and `win.title` changing as
  you work;
- reading and writing files with `fs`, with friendly messages when that
  goes wrong (`try`/`catch`).
