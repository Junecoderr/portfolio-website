# Errors

Things go wrong: a file is missing, a log line is damaged, a number is really text. TB tells you what went wrong in plain words. Your program can catch the error and carry on.

## Read an error

When TB stops with an error, it shows the line and a hint. Read the message first. It usually tells you what to change.

```tb norun
let name = "Tanisha"
say(nmae)   # tb-check: ignore
```

TB says: `I don't know what 'nmae' is. Did you mean 'name'?`

## Catch an error

Put risky code in `try`. If it fails, TB runs the `catch` block. The error has a `message` and a `kind`.

```tb
try {
  let n = num("twelve")
  say(n)
} catch err {
  say("That did not work:", err.message)
}
say("The program carries on.")
```

## Catch one kind of error

Errors have classes, like `ParseError`, `NotFoundError` and `ValueError`. Catch only the kind you expect. Other errors still stop the program, so you see them.

```tb
import json
import fs

try {
  json.parse("{not json")
} catch err: ParseError {
  say("bad JSON:", err.kind)
}

try {
  fs.read("no-such-file.txt")
} catch err: NotFoundError {
  say("missing file:", err.kind, err is FileError)
}
```

## Always clean up

A `finally` block always runs, also after an error. `defer` runs a block when the function ends.

```tb
fn read_report() {
  defer { say("closing the report") }
  say("reading the report")
  return "done"
}

try {
  say(read_report())
} finally {
  say("finished")
}
```

## Make your own errors

`throw` stops the code and sends an error up. Make your own error class with `extends Error`.

```tb
class QuotaError extends Error { }

fn lookup(count) {
  if count > 100 {
    throw QuotaError("Too many lookups at once", {"limit": 100})
  }
  return count
}

try {
  lookup(500)
} catch err: QuotaError {
  say(err.message, "- the limit is", err.limit)
}
```

## Check what must be true

`assert` stops the program when a condition is false. Use it to find mistakes early.

```tb
let ports = [22, 443]
assert len(ports) == 2, "expected two ports"
try {
  assert ports[0] == 80, "the first port should be 80"
} catch err: AssertionError {
  say("caught:", err.message)
}
```

## Damaged input

Real logs have bad lines. Skip a bad line, count it, and go on. Do not let one bad line stop the whole job.

```tb
import json

let lines = ["{\"ip\": \"203.0.113.42\"}", "{broken", "{\"ip\": \"198.51.100.7\"}"]
let good = []
let bad = 0
for line in lines {
  try {
    good.push(json.parse(line).ip)
  } catch err: ParseError {
    bad += 1
  }
}
say(good, f"({bad} bad line skipped)")
```

## What you learned

- Read the message: it tells you what to fix.
- `try`, `catch`, `finally` and `defer` handle errors and clean up.
- `throw` and your own error classes report problems.

Next, you make your own types with classes, and choose by shape with `match`.
