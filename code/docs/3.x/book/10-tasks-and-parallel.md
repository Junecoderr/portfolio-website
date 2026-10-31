# Tasks and parallel work

Some jobs wait a lot: for the network, for a disk, for another program. While one job waits, another job can run. TB has tasks, `async` and the `parallel` library for this.

The examples in this chapter use threads. The browser playground has no threads, so these blocks need the TB app on your computer.

## Run a function in the background

`parallel.run` starts a function and gives you a task at once. `wait()` gives you the result when it is ready.

```tb desktop
import parallel

fn slow_square(x) {
  parallel.sleep(0.2)
  return x * x
}

let task = parallel.run(slow_square, 7)
say("working...")
say(task.wait())
```

## Do the same work on many items

`parallel.map` runs a function on every item with many workers. The results stay in the same order as the items.

```tb desktop
import parallel

fn check(host) {
  parallel.sleep(0.1)
  return f"{host}: ok"
}

let hosts = ["web01", "web02", "db01", "mail01"]
for line in parallel.map(check, hosts, 4) {
  say(line)
}
```

## async and await

An `async fn` gives back a task. `await` waits for it. Start many tasks first, then wait for them.

```tb
async fn lookup(host) {
  return f"{host} looks fine"
}

let tasks = ["web01", "web02", "db01"].map(h => lookup(h))
for t in tasks {
  say(await t)
}
```

## Pass work through a queue

A queue moves items safely from one task to another. `close()` tells the reader that no more items come.

```tb desktop
import parallel

let q = parallel.Queue()
parallel.run(fn() {
  for line in ["login ok", "login failed", "logout"] {
    q.put(line)
  }
  q.close()
})
for item in q {
  say("got", item)
}
```

## Share a value safely

Two tasks that change the same value at the same time can lose an update. A lock lets only one task in at a time.

```tb desktop
import parallel

let lock = parallel.Lock()
let count = {"n": 0}
parallel.each(fn(i) {
  lock.hold(fn() {
    count.n += 1
  })
}, range(100))
say(count.n)
```

## Errors in tasks

An error in a task comes back when you wait for it, with its class. Catch it as usual.

```tb desktop
import parallel

let bad = parallel.run(fn() {
  throw ValueError("no thanks")
})
try {
  bad.wait()
} catch err: ValueError {
  say("caught:", err.message)
}
```

## Measure the time

`time.timestamp()` gives the time in seconds. Take it before and after a job to see how long the job took.

```tb desktop
import parallel
import time

let start = time.timestamp()
parallel.map(fn(x) { parallel.sleep(0.2) }, range(8), 8)
let took = time.timestamp() - start
say("8 jobs of 0.2 s, side by side, took less than 1 s:", took < 1)
```

## What you learned

- `parallel.run`, `parallel.map` and `async fn` run work at the same time.
- Queues and locks share data safely.
- Errors in tasks come back when you wait.

Next, you test your code and use TB's tools.
