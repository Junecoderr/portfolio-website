# tb-progress

Progress bars, spinners, step counters and time estimates for TB command-line programs.

```
tb add tb-progress
```

```tb
import "tb_packages/tb-progress/lib.tb" as progress

say(progress.bar(3, 10, 20))              # [██████              ]  30%
say(progress.ascii_bar(5, 10, 10))        # [#####-----]  50%
say(progress.step(3, 10), "hashing")      # [ 3/10] hashing
say(progress.spinner(i, "dots"))          # ⠋ ⠙ ⠹ ... pick a frame each loop

let t = progress.tracker(files.len(), "Hashing")
for f in files {
  hash_one(f)
  t.tick()                                # prints "Hashing [███   ] 10% (1/10) 2s (about 18s left)"
}
t.finish()
```

The rendering functions are pure (they give back text); the tracker keeps
state for a loop. TB's `say()` always ends the line, so a tracker prints a
fresh line each time instead of redrawing one; use `every_n(25)` on big loops.

## Functions

| Function | What it does |
| --- | --- |
| `bar(done, total, width)` | `[██████              ]  30%` |
| `ascii_bar(done, total, width)` | `[######--------------]  30%` |
| `bar_with(done, total, options)` | Options: `width fill empty left right label show_count show_percent` |
| `render_bar(done, total, width, fill, empty)` | Just the bar characters |
| `fraction(done, total)` / `percent(done, total)` | 0..1 / 0..100, clamped |
| `spinner(step, style)` | One frame of `dots`, `line`, `arrow`, `bounce` or `clock` |
| `step(done, total)` | `[ 3/10]` |
| `duration(seconds)` / `eta(done, total, elapsed)` | `1m 05s` / seconds left (nil before the first step) |
| `tracker(total, label)` | A map with `tick() set(n) finish() line() every_n(n) quiet_mode()` and fields `done total printed` |

Sample: a tracker labelled `"Hashing Tanisha's files"` prints `Hashing Tanisha's files [██████████] 100% (3/3) 0s`.

## Tests

```
tb test packages/tb-progress
```
