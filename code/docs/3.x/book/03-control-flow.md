# Control flow

A program does not always run every line once. It can choose between lines with `if` and `match`, and it can repeat lines with `while` and `for`. This is control flow.

## Choose with if and else

`if` runs a block when a condition is true. `else if` checks one more condition. `else` runs when no condition is true. A block goes between `{` and `}`.

```tb
let failed = 7
if failed >= 10 {
  say("Block the address")
} else if failed >= 5 {
  say("Watch the address")
} else {
  say("Nothing to do")
}
```

## Compare and combine

Compare with `==`, `!=`, `<`, `<=`, `>` and `>=`. Combine conditions with `and`, `or` and `not`. Use `in` to look for a value in a list or in text.

```tb
let port = 22
let internal = false
say(port == 22 and not internal)
say(port in [22, 3389], "ssh" in "openssh-server")
say(0 < port <= 1024)
```

## A value from a choice

`if ... then ... else` gives a value. Use it when you choose between two values.

```tb
let score = 82
let level = if score >= 80 then "high" else "low"
say(f"Score {score} is {level}")
```

## Repeat with while

`while` runs a block again and again while a condition is true. Make sure the condition becomes false, or the loop never stops.

```tb
let tries = 3
while tries > 0 {
  say(f"Try again ({tries} left)")
  tries -= 1
}
say("Locked")
```

## Repeat with for

`for` runs a block once for each item in a list, each letter of text, or each number in a range. `1..4` is 1, 2 and 3. `1..=4` also has 4.

```tb
for host in ["web01", "web02", "db01"] {
  say("Checking", host)
}
for n in 1..=3 {
  say("Round", n)
}
for i, port in [22, 80].enumerate() {
  say(i, port)
}
```

## Skip and stop

`continue` goes to the next item. `break` stops the loop.

```tb
for n in range(1, 50) {
  if n % 7 != 0 {
    continue
  }
  if n > 30 {
    break
  }
  say(n)
}
```

## Random choices

The `random` library picks numbers and items for you. Use it for tests and sample data, never for passwords or keys (use `crypto` for those).

```tb
import random

random.seed(42)
let hosts = ["web01", "web02", "db01", "mail01"]
say(random.int(1, 6), random.choice(hosts))
say(random.sample(hosts, 2))
```

## What you learned

- `if`, `else if` and `else` choose.
- `while` and `for` repeat. `break` and `continue` change the loop.
- `random` makes random choices.

Next, you put code in functions so you can use it again.
