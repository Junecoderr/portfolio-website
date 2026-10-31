# Classes and match

A class is your own kind of value. It keeps data and the functions that work on that data together. `match` chooses what to do by the shape of a value.

## Make a class

`init` runs when you make a new value. `self` is the value itself. Other functions in the class are methods.

```tb
class Alert {
  let level = "low"

  fn init(rule, ip) {
    self.rule = rule
    self.ip = ip
  }

  fn text() {
    return f"[{self.level}] {self.rule} from {self.ip}"
  }
}

let a = Alert("ssh brute force", "203.0.113.42")
a.level = "high"
say(a.text())
say(type(a), a is Alert)
```

## Build on a class

`extends` makes a class from another class. `super` calls the method of the parent class.

```tb
class Finding {
  fn init(title) { self.title = title }
  fn describe() { return f"Finding: {self.title}" }
}

class IpFinding extends Finding {
  fn init(title, ip) {
    super.init(title)
    self.ip = ip
  }
  fn describe() { return super.describe() + f" ({self.ip})" }
}

let f = IpFinding("port scan", "198.51.100.7")
say(f.describe())
say(f is Finding, f.fields())
```

## A fixed set of choices

`enum` makes a type with a fixed list of values. TB checks that you only use values from the list.

```tb
enum Level { low, medium, high }

let now = Level.medium
say(now, now.name, now.index)
say(now == Level.high)
for level in Level.values() {
  say(level.index, level.name)
}
```

## Choose by shape

`match` compares a value with patterns, from top to bottom. The first pattern that fits wins. `_` fits everything.

```tb
fn describe(x) {
  return match x {
    0 => "zero"
    1..=9 => "a small number"
    "critical" or "high" => "act now"
    is text => f"some text: {x}"
    [] => "an empty list"
    [first, ...rest] => f"a list that starts with {first}"
    {"ip": ip, "port": 22} => f"SSH on {ip}"
    _ => "something else"
  }
}

for x in [0, 7, "high", "hello", [], [5, 6], {"ip": "192.0.2.10", "port": 22}, nil] {
  say(describe(x))
}
```

## Match on your own types

Patterns also work with enums and classes.

```tb
enum Level { low, medium, high }

class Alert {
  fn init(rule, level) {
    self.rule = rule
    self.level = level
  }
}

for a in [Alert("port scan", Level.medium), Alert("new admin", Level.high), Alert("login", Level.low)] {
  let action = match a.level {
    Level.high => "page someone"
    Level.medium => "open a ticket"
    _ => "log it"
  }
  say(f"{a.rule}: {action}")
}
```

## What you learned

- `class` makes your own type. `init`, `self` and methods give it data and behavior.
- `extends` and `super` build on a class. `enum` makes a fixed set of values.
- `match` chooses by the shape of a value.

Next, you split a program into files and use libraries.
