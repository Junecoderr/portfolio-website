# Functions

A function is a named block of code. You write it once and call it many times. Functions make programs shorter and easier to read.

## Make a function

`fn` makes a function. The names in brackets are its inputs. `return` gives a value back.

```tb
fn risk(failed_logins) {
  if failed_logins >= 10 {
    return "high"
  }
  return "low"
}

say(risk(3), risk(25))
```

## Default and named inputs

An input can have a default value. When you call the function, you can give an input by its name.

```tb
fn connect(host, port = 22, timeout = 5) {
  return f"{host}:{port} (wait {timeout}s)"
}

say(connect("192.0.2.10"))
say(connect("192.0.2.10", 443))
say(connect("192.0.2.10", timeout = 1))
```

## Any number of inputs

`...rest` collects the extra inputs in a list. `**opts` collects extra named inputs in a map.

```tb
fn total(first, ...rest) {
  return first + rest.sum()
}

fn tag(text, **opts) {
  return f"{text} {opts}"
}

say(total(1, 2, 3, 4))
say(tag("alert", level = "high"))
```

## Short functions

`x => x * 2` is a short function with one input. Short functions are useful with list methods like `map` and `filter`.

```tb
let double = x => x * 2
let add = (a, b) => a + b
say(double(21), add(2, 3))
say([3, 1, 2].map(x => x * 10))
say([5, 12, 8].filter(n => n > 6))
```

## Functions that remember

A function made inside another function can use the names of the outer function. It keeps them after the outer function ends. This is a closure.

```tb
fn make_counter() {
  let count = 0
  return fn() {
    count += 1
    return count
  }
}

let next_id = make_counter()
next_id()
next_id()
say("Next ticket:", next_id())
```

## Functions that call themselves

A function can call itself. Make sure there is a case that stops.

```tb
fn depth(path) {
  if "/" not in path {
    return 0
  }
  return 1 + depth(path.split("/").slice(1).join("/"))
}

say(depth("var/log/nginx/access.log"))
```

## Generators

A function with `yield` gives values one at a time. The next value is made only when you ask for it.

```tb
fn evens() {
  let n = 0
  while true {
    yield n
    n += 2
  }
}

say(evens().take(5).to_list())
```

## What you learned

- `fn` makes a function. `return` gives back a value.
- Inputs can have defaults and names.
- Short functions (`x => ...`), closures and generators make code shorter.

Next, you keep many values together in lists and maps.
