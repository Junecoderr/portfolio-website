# Testing and tools

A tool that finds attacks must not break quietly. Tests prove that your code still works after a change. TB also has tools that find mistakes, tidy your code and show where time goes.

## Write a test

The `test` library checks values. `test.equal` compares a result with the value you expect. `test.raises` checks that code fails the way it should.

```tb
import test

fn risk(failed) {
  if failed < 0 {
    throw ValueError("failed logins can't be negative")
  }
  return if failed >= 10 then "high" else "low"
}

test.equal(risk(3), "low", "a few failures are low risk")
test.equal(risk(25), "high", "many failures are high risk")
test.raises(fn() { risk(-1) }, "ValueError")
say(test.results())
```

## Test files and tb test

Put tests in files that end with `_test.tb`. Each `test.case` is one named test. `tb test` runs every test file in a folder and shows what failed.

```tb
import test

test.case("defang keeps the address readable", fn() {
  test.equal("203.0.113.42".replace(".", "[.]"), "203[.]0[.]113[.]42")
})
test.case("many inputs with each", fn() {
  test.each([[1, 2, 3], [2, 2, 4]], fn(a, b, total) {
    test.equal(a + b, total)
  })
})
say(test.results().passed, "passed")
```

```text
tb test              run every *_test.tb file under this folder
tb test --coverage   also show which lines the tests ran
```

## Find mistakes before you run

`tb check` reads your program and finds mistakes without running it: names that do not exist, the wrong number of inputs, unused variables. It is fast, so run it often.

```text
tb check triage.tb
triage.tb:4:7: error: I don't know what 'recods' is. Did you mean 'records'?
```

## Tidy your code

`tb fmt` puts every file in the same layout: spaces, line breaks and commas. Then a review shows only real changes.

```text
tb fmt .            tidy every .tb file here
tb fmt --check .    only list the files that would change
```

## Debug and profile

`tb debug` runs a program one line at a time. You can see every variable. `tb profile` shows which functions use the most time. Editors use the same debugger: the [VS Code extension](site:tools.html#vscode) has breakpoints.

```text
tb debug triage.tb
tb profile triage.tb --html
```

## Document your code

A comment that starts with `##` above a function is its documentation. `tb doc` shows it, like the documentation of the libraries.

```tb
## How bad a number of failed logins is.
## Returns "high" at 10 or more, else "low".
fn risk(failed) {
  return if failed >= 10 then "high" else "low"
}

say(risk(12))
```

```text
tb doc triage.tb
```

See [Tools](site:tools.html) for all the commands.

## What you learned

- `test` and `tb test` prove that your code works.
- `tb check` and `tb fmt` find mistakes and tidy code.
- `tb debug`, `tb profile` and `tb doc` help you understand a program.

Next, you use the network.
