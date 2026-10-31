# Pictures for the parallel library (read with ast.literal_eval; never run).
# map/each show work spread over worker lanes; the workers slider redraws the lanes.
SPEC = {
    "_default": {"roles": {"fn": "your function, run on another thread", "items": "the things to work through",
                           "workers": "how many run at the same time (8 if left out)", "tasks": "tasks from parallel.run()",
                           "timeout": "the most seconds to wait (no limit if left out)", "ms": "milliseconds",
                           "seconds": "how long to pause (fractions are fine)"}},
    "map": {
        "t": "lanes", "run": True, "args": {"fn": "x => x * 10", "items": "[1, 2, 3, 4, 5, 6]", "workers": "2"},
        "items": [1, 2, 3, 4, 5, 6], "durations": [3, 2, 4, 2, 3, 2], "workers": [1, 2, 3, 6], "workers_default": 2,
        "items_title": "items",
    },
    "each": {
        "t": "lanes", "run": False, "args": {"fn": "host => check(host)", "items": '["a", "b", "c", "d", "e"]', "workers": "3"},
        "items": ["a", "b", "c", "d", "e"], "durations": [3, 2, 4, 2, 3], "workers": [1, 2, 3, 5], "workers_default": 3,
        "items_title": "hosts", "gives": "nothing; it waits until every call is done",
    },
    "run": {
        "run": False, "args": {"fn": "fetch", "args": '"https://example.com"'}, "static": "<Task>",
        "nodes": [
            {"row": [{"param": "fn", "lines": ["fetch"]}, {"param": "args", "lines": ["\"https://example.com\""]}]},
            {"arrow": "parallel.run"},
            {"row": [{"box": "your program", "icon": "code", "lines": ["keeps going"]}, {"box": "the task", "icon": "cpu", "lines": ["fetch(…) runs", "at the same time"]}]},
            {"arrow": "task.wait()"},
            {"result": True},
        ],
        "gives": "a Task: .wait() .done() .cancel() .on_done(fn)",
        "roles": {"fn": "the function to start", "args": "inputs passed on to fn"},
    },
    "all": {
        "run": False, "args": {"tasks": "[t1, t2, t3]", "timeout": "10"}, "static": '["page 1", "page 2", "page 3"]',
        "nodes": [
            {"row": [{"box": "t1", "p": "tasks", "icon": "cpu", "lines": ["2 s"]}, {"box": "t2", "p": "tasks", "icon": "cpu", "lines": ["5 s"]}, {"box": "t3", "p": "tasks", "icon": "cpu", "lines": ["1 s"]}], "cols": 3},
            {"param": "timeout", "lines": ["10 s for all of them together"]},
            {"arrow": "waits for the slowest (5 s)"},
            {"result": True, "label": "results, in task order"},
        ],
    },
    "race": {
        "run": False, "args": {"tasks": "[t1, t2, t3]", "timeout": "10"}, "static": '"mirror 3"',
        "nodes": [
            {"row": [{"box": "t1", "p": "tasks", "icon": "cpu", "lines": ["2 s"]}, {"box": "t2", "p": "tasks", "icon": "cpu", "lines": ["5 s"]}, {"box": "t3 wins", "p": "tasks", "icon": "cpu", "lines": ["1 s"]}], "cols": 3},
            {"param": "timeout", "lines": ["stop waiting after 10 s"]},
            {"arrow": "the first to finish"},
            {"result": True},
        ],
    },
    "sleep": {
        "t": "timeline", "run": False, "args": {"seconds": "0.25"},
        "marks": [{"label": "sleep(0.25)", "at": 0.2}, {"label": "goes on", "at": 0.6}],
        "spans": [{"from": 0.2, "to": 0.6, "label": "paused 0.25 s", "p": "seconds"}],
        "gives": "nothing; cancel() inside a task wakes it up",
    },
    "cpu_count": {"run": False, "static": "8",
                  "nodes": [{"box": "this computer", "icon": "cpu", "lines": ["processor cores"]}, {"arrow": True}, {"result": True}]},
    "Queue": {
        "run": False, "args": {"size": "2"}, "static": "<Queue>",
        "nodes": [
            {"box": "a task", "icon": "cpu", "lines": ["q.put(job)"]},
            {"arrow": "into the queue"},
            {"param": "size", "lines": ["[job 1] [job 2]  ← full: put() waits"]},
            {"arrow": "q.get()"},
            {"box": "another task", "icon": "cpu", "lines": ["for job in q { … }"]},
        ],
        "roles": {"size": "how many it holds before put() waits (no limit if left out)"},
    },
    "Lock": {
        "run": False, "static": "<Lock>",
        "nodes": [
            {"row": [{"box": "task A", "icon": "cpu", "lines": ["lock.hold(fn)", "→ runs now"]}, {"box": "task B", "icon": "cpu", "lines": ["lock.hold(fn)", "→ waits its turn"]}]},
            {"arrow": "one at a time"},
            {"box": "shared list / map / counter", "icon": "lock", "lines": ["never changed by two at once"]},
        ],
    },
    "every": {
        "t": "timeline", "run": False, "args": {"ms": "1000", "fn": "() => ping()"},
        "marks": [{"label": "0", "at": 0.05}, {"label": "fn()", "at": 0.3, "p": "fn", "under": "1000 ms"},
                  {"label": "fn()", "at": 0.55, "p": "fn", "under": "2000 ms"}, {"label": "fn()", "at": 0.8, "p": "fn", "under": "3000 ms"}],
        "spans": [{"from": 0.05, "to": 0.3, "label": "every ms", "p": "ms"}],
        "gives": "a Timer: .stop() (or fn gives back false)",
        "roles": {"ms": "milliseconds between calls", "fn": "runs each time, on its own thread"},
    },
    "after": {
        "t": "timeline", "run": False, "args": {"ms": "1500", "fn": "() => say(\"time!\")"},
        "marks": [{"label": "now", "at": 0.05}, {"label": "fn()", "at": 0.6, "p": "fn", "under": "once"}],
        "spans": [{"from": 0.05, "to": 0.6, "label": "1500 ms later", "p": "ms"}],
        "gives": "a Timer: .stop() before then cancels it",
        "roles": {"ms": "milliseconds to wait", "fn": "runs once, on its own thread"},
    },
    "loop": {
        "run": True, "args": {"fn": "(a, b) => a + b", "args": "2, 3"},
        "nodes": [
            {"row": [{"param": "fn", "lines": ["main function"]}, {"param": "args", "lines": ["its inputs"]}]},
            {"arrow": "parallel.loop"},
            {"row": [{"box": "task A", "icon": "cpu", "lines": ["runs until it waits"]},
                     {"box": "task B", "icon": "cpu", "lines": ["then takes its turn"]},
                     {"box": "server", "icon": "code", "lines": ["10,000 connections,", "one thread"]}], "cols": 3},
            {"arrow": "fn and every task it started are done"},
            {"result": True},
        ],
        "roles": {"fn": "the loop's main function", "args": "inputs passed on to fn"},
    },
    "group": {
        "run": True, "args": {"fn": "g => [g.run(() => 1), g.run(() => 2)]"},
        "nodes": [
            {"param": "fn", "lines": ["g.run(task 1)", "g.run(task 2)"]},
            {"row": [{"box": "task 1", "icon": "cpu", "lines": ["1"]}, {"box": "task 2", "icon": "cpu", "lines": ["2"]}]},
            {"arrow": "waits for all; one fails → the rest are cancelled"},
            {"result": True, "label": "results, in start order"},
        ],
        "roles": {"fn": "gets the group; g.run(f, ...args) starts its tasks"},
    },
    "timeout": {
        "t": "timeline", "run": False, "args": {"seconds": "2", "fn": "() => slow_job()"},
        "marks": [{"label": "start", "at": 0.05}, {"label": "cancelled", "at": 0.6, "p": "fn", "under": "TimeoutError"}],
        "spans": [{"from": 0.05, "to": 0.6, "label": "at most 2 s", "p": "seconds"}],
        "gives": "fn's result, or a TimeoutError (err.cancelled is true)",
        "roles": {"seconds": "the most seconds fn may take", "fn": "the work to limit"},
    },
    "processes": {
        "t": "lanes", "run": False, "args": {"fn": "fib", "items": "[30, 30, 30, 30]", "workers": "4"},
        "items": [30, 30, 30, 30], "durations": [4, 4, 4, 4], "workers": [1, 2, 4], "workers_default": 4,
        "items_title": "items", "gives": "the results in order; each process has a core to itself",
        "roles": {"fn": "your function, copied into each process", "items": "plain values to work through",
                  "workers": "how many processes (one per core if left out)"},
    },
    "shared_queue": {
        "run": False, "args": {"size": "100"}, "static": "<shared queue 0 waiting>",
        "nodes": [
            {"box": "a worker process", "icon": "cpu", "lines": ["q.put(result)"]},
            {"arrow": "across processes"},
            {"param": "size", "lines": ["plain values only"]},
            {"arrow": "q.get()"},
            {"box": "your program", "icon": "code", "lines": ["for item in q { … }"]},
        ],
        "roles": {"size": "how many it holds before put() waits (no limit if left out)"},
    },
}
