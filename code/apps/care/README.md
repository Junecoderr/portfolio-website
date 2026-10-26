# care

"How are you today?" A gentle mood check-in, made for Tanisha. Pick how you
feel, write a few words if you like, and save it: you get a kind message
back, and the check-in goes into a journal (`journal.json`, next to the app)
shown newest first. Double-click a journal entry to read its note.

```
tb apps/care/main.tb
```

What it shows:

- buttons laid out in two `row()`s inside a `column()`, each made by a
  function that remembers its mood (a closure);
- a `textarea`, labels in different styles (title, heading, muted, accent)
  and a `table` with an `on_open` event;
- dates with `time.now()` and `time.format`, a journal saved with `json`,
  and `random.choice` for the kind words.
