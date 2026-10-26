# calculator

A calculator with a grid of buttons, in the dark theme. It works one step at
a time like a pocket calculator (2 + 3 × 4 = 20), and you can type on the
keyboard too: digits, `+ - * / . %`, Enter for =, Escape to clear and
Backspace to rub out a digit. Dividing by zero shows a message instead of
crashing.

```
tb apps/calculator/main.tb
```

What it shows:

- a grid made of `row({"expand": true})`s whose buttons all use
  `{"expand": true}`, so they share the space and grow with the window;
- big right-aligned text with `label(text, {"style": "display", "align": "right"})`;
- one function, `press(key)`, behind every button and every keyboard
  shortcut (`win.key`);
- `throw` and `try`/`catch` for the divide-by-zero case.
