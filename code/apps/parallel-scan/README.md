# parallel-scan

Checks TCP ports on one or more hosts **at the same time** with
`parallel.map`, then does the same checks one at a time and prints how long
each way took. By default it only looks at this computer (127.0.0.1).

> Only check computers you own or have written permission to test. Checking
> other people's systems can be against the law. Any host other than this
> computer needs `--authorised` to confirm you have that permission.

```
$ tb main.tb --latency 50
Checking 29 ports on 1 host(s) with 16 workers...

HOST              PORT   SERVICE        STATE
127.0.0.1         5432   postgres       OPEN

1 open, 28 closed or filtered.
parallel (16 workers): 0.114s
one at a time:       1.540s
speed-up:            13.5x
```

## Usage

```
tb main.tb                                  # this computer, common ports
tb main.tb 127.0.0.1,localhost 22,80,8000-8010
tb main.tb 127.0.0.1 1-1024 --workers 64    # more checks at once
tb main.tb --latency 50                     # pretend the network is slow
tb main.tb 192.168.1.20 --authorised        # a machine on your network you own
tb main.tb --no-compare                     # skip the one-at-a-time run
```

On 127.0.0.1 a closed port answers "no" instantly, so the two runs take
about the same time. Real networks are different: a filtered port makes you
wait for the whole timeout. `--latency MS` adds that kind of wait to every
check so you can see the difference on your own machine; on a real network
you get it for free.

## What it teaches

- `parallel.map(fn, items, workers)` calls `fn(item)` for every item on
  several threads and gives back the results **in the same order** as the
  items, so `results[k]` belongs to `jobs[k]`.
- Work that mostly *waits* (network, disk, `parallel.sleep`) gets faster
  with more workers. Work that only computes doesn't: TB runs one line of TB
  at a time, whichever thread it's on.
- `net.tcp_check(host, port, timeout)` is a normal TCP connect: true if
  something answers.
- If any call inside `parallel.map` throws, `map` stops and the same error
  (class, message and line) happens where you called it; here `check`
  catches its own errors so one bad host doesn't stop the scan.
