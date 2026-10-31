# Values and types

Every value has a type. The type tells TB what you can do with the value. In this chapter you meet numbers, text, true and false, and `nil`.

## Remember a value with let

`let` gives a value a name. Use the name later to get the value back. You can give a name a new value with `=`.

```tb
let analyst = "Tanisha"
let alerts = 3
say(analyst, "has", alerts, "alerts")
alerts = alerts + 1
say("Now there are", alerts)
```

## The types

`type()` tells you the type of a value.

```tb
say(type(42), type(2.5), type("hi"), type(true), type(nil))
say(type([1, 2]), type({"a": 1}))
```

- A **number** is a whole number like `42` or a number with a point like `2.5`.
- **Text** is letters between quotes.
- A **bool** is `true` or `false`.
- `nil` means "no value".

## Numbers and maths

TB does maths with `+`, `-`, `*`, `/`, `//` (whole division), `%` (the rest after division) and `**` (power). The `math` library has more.

```tb
import math

say(7 / 2, 7 // 2, 7 % 2, 2 ** 10)
say(math.sqrt(81), math.round(math.pi, 3), math.max(4, 9, 2))
say(1_000_000, 0xff, 0b1010)
```

## Exact money and percentages

Numbers with a point are not always exact: `0.1 + 0.2` is not exactly `0.3`. Use the `decimal` library when every digit counts.

```tb
import decimal

say(0.1 + 0.2)
say(decimal.of("0.1").add("0.2").text)
say(decimal.money("19.999"))
```

## Text

Join text with `+`. Put values into text with an f-string: write `f` before the quote and the value in `{ }`. Text has methods: you call them with a dot.

```tb
import text

let host = "web01"
let tries = 12
say("Host " + host + " had " + str(tries) + " failed logins")
say(f"Host {host} had {tries} failed logins")
say("  ERROR  ".trim().lower(), "a,b,c".split(","))
say(text.pad_left("7", 3, "0"), text.title("brute force attempt"))
```

## Change a value to another type

`str()` makes text. `num()` makes a number from text. `int()` drops the part after the point. `float()` adds a point.

```tb
let port = num("443")
say(port + 1, str(port) + "/tcp", int(3.9), float(2))
```

## What you learned

- `let` names a value.
- Numbers, text, bools and `nil` are the basic types.
- `math` and `decimal` help with numbers. `text` and text methods help with words.

Next, you make your program choose and repeat.
