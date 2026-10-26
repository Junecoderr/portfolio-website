# tb-tables

Pretty text tables, Markdown tables and CSV for TB, from lists of maps or lists.

```
tb add tb-tables
```

```tb
import "tb_packages/tb-tables/lib.tb" as tables

let rows = [
  {"host": "web01", "alerts": 12, "status": "ok"},
  {"host": "db-primary", "alerts": 3, "status": "warn"},
]
say(tables.table(rows))
```

```
╭────────────┬────────┬────────╮
│ host       │ alerts │ status │
├────────────┼────────┼────────┤
│ web01      │     12 │ ok     │
│ db-primary │      3 │ warn   │
╰────────────┴────────┴────────╯
```

Columns come from the map keys in order of first appearance; number columns
are right-aligned; cells coloured with tb-colors are measured without their
escape codes, so they still line up.

## Functions

| Function | What it does |
| --- | --- |
| `table(rows)` / `render(rows, options)` | Text table (styles `rounded`, `box`, `ascii`, `plain`) |
| `md(rows)` / `markdown(rows, options)` | GitHub-flavoured Markdown table |
| `to_csv(rows)` / `csv(rows, options)` | CSV text with quoting |
| `record_of(map)` / `record(map, options)` | A two-column Field/Value table for one map |
| `show(rows, options)` | `say(render(rows, options))` |
| `columns_of(rows)` / `cells_of(row, columns)` / `strip_codes(text)` | Building blocks |

Options (a map; TB 2.x has no optional inputs, so pass `{}` for the defaults):

| Key | Example |
| --- | --- |
| `columns` | `["host", "alerts"]` pick and order columns (a list given as the whole options means this) |
| `headers` | `{"host": "Host"}` or `["Host", "Alerts"]` |
| `style` | `"rounded"` (default), `"box"`, `"ascii"`, `"plain"` |
| `align` | `{"alerts": "right"}` with `left`, `right`, `center` |
| `max_width` | `20` truncates long cells with `…` |
| `title` | text centred above the table |
| `footer` | a row shown under a separator (totals) |

List rows (`[["Tanisha", 91]]`) have columns `"0"`, `"1"`, ...; give them names with `headers`.

## Tests

```
tb test packages/tb-tables
```
