"""TB benchmarks: the tree-walker vs the bytecode VM vs plain Python.

Run from code/:  uv run --python /opt/homebrew/bin/python3.12 python bench/bench.py [--repeat N] [names...]

Each program runs in a fresh process (so the times include TB's start-up, as
a user would see it); the table shows the best of N runs. The VM and the
tree-walker must print the same output, and the Python version is a straight
translation of each program, as a baseline.

Regression check (CI):  python bench/bench.py --against OLD/code [--max-slowdown 10]

compares the VM of this checkout with the VM of another checkout of TB (the
commit a pull request starts from, or the previous commit on main) on the
same machine, and fails if any benchmark got more than --max-slowdown percent
slower. Timing on shared CI machines is noisy, so:

* both versions run in the same job, taking turns (old, new, old, new, ...),
  so a busy neighbour or a slow runner slows both alike;
* each run is measured in CPU time of the child process where the system
  reports it (not wall time, which includes waiting for the CPU);
* each side keeps its best run (noise only ever adds time);
* a benchmark that looks too slow is measured again with three times the
  runs, and only fails if it is still too slow.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BENCHMARKS = ["fib", "loop", "strings", "maps", "methods", "generators"]

try:
    import resource
except ImportError:  # Windows: wall time only
    resource = None


def timed(cmd, env, cwd=ROOT) -> tuple[float, str]:
    start = time.perf_counter()
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd)
    elapsed = time.perf_counter() - start
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{done.stdout}{done.stderr}")
    return elapsed, done.stdout.strip()


def cpu_timed(cmd, env, cwd) -> tuple[float, str]:
    """Like timed(), but the child's CPU time (user + system) where the OS reports it."""
    if resource is None:
        return timed(cmd, env, cwd)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    _wall, out = timed(cmd, env, cwd)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return cpu, out


def best(cmd, env, repeat) -> tuple[float, str]:
    results = [timed(cmd, env) for _ in range(repeat)]
    return min(r[0] for r in results), results[0][1]


def table(opts) -> int:
    tb = os.path.join(ROOT, "tb.py")
    rows = []
    for name in opts.names:
        program = os.path.join(HERE, name + ".tb")
        env = dict(os.environ)
        env["TB_ENGINE"] = "tree"
        tree, tree_out = best([sys.executable, tb, program], env, opts.repeat)
        env["TB_ENGINE"] = "vm"
        vm, vm_out = best([sys.executable, tb, program], env, opts.repeat)
        py, py_out = best([sys.executable, os.path.join(HERE, name + ".py")], env, opts.repeat)
        if tree_out != vm_out:
            raise SystemExit(f"{name}: the engines disagree: tree {tree_out!r}, vm {vm_out!r}")
        if py_out != vm_out:
            print(f"note: {name}: python printed {py_out!r}, TB printed {vm_out!r}")
        rows.append((name, tree, vm, py))
        print(f"  {name}: tree {tree:.2f}s  vm {vm:.2f}s  python {py:.2f}s", flush=True)
    print()
    print(f"{'benchmark':<12} {'tree (s)':>9} {'vm (s)':>8} {'python (s)':>11} {'vm speed-up':>12}")
    for name, tree, vm, py in rows:
        print(f"{name:<12} {tree:>9.2f} {vm:>8.2f} {py:>11.2f} {tree / vm:>11.1f}x")
    if rows:
        mean = sum(t / v for _n, t, v, _p in rows) / len(rows)
        print(f"{'mean':<12} {'':>9} {'':>8} {'':>11} {mean:>11.1f}x")
    print(f"(best of {opts.repeat}; times include start-up; python {sys.version.split()[0]})")
    return 0


# -- the regression check ---------------------------------------------------------------

def measure_pair(name, old_root, new_root, rounds, env) -> tuple[float, float]:
    """Best CPU time of one benchmark on the old and the new checkout, taking turns."""
    program = os.path.join(new_root, "bench", name + ".tb")
    old_best = new_best = float("inf")
    outs = set()
    for _ in range(rounds):
        for side, root in enumerate((old_root, new_root)):
            t, out = cpu_timed([sys.executable, os.path.join(root, "tb.py"), program], env, root)
            outs.add(out)
            if side == 0:
                old_best = min(old_best, t)
            else:
                new_best = min(new_best, t)
    if len(outs) != 1:
        raise SystemExit(f"{name}: the old and the new version print different things: {sorted(outs)!r}")
    return old_best, new_best


def verdict(old: float, new: float, max_slowdown: float) -> bool:
    """True when new is at most max_slowdown percent slower than old."""
    return new <= old * (1 + max_slowdown / 100)


def regression_check(opts) -> int:
    old_root = os.path.abspath(opts.against)
    new_root = os.path.abspath(opts.new or ROOT)
    if not os.path.isfile(os.path.join(old_root, "tb.py")):
        raise SystemExit(f"{old_root} is not a TB checkout (no tb.py there).")
    env = dict(os.environ)
    env["TB_ENGINE"] = "vm"
    failed = []
    print(f"old: {old_root}\nnew: {new_root}\n(best of {opts.repeat} runs each, CPU time"
          f"{'' if resource else ' (wall time on this system)'}; fails above +{opts.max_slowdown:g}%)\n")
    print(f"{'benchmark':<12} {'old (s)':>8} {'new (s)':>8} {'change':>8}")
    for name in opts.names:
        if not os.path.isfile(os.path.join(new_root, "bench", name + ".tb")):
            raise SystemExit(f"There is no benchmark called {name}.")
        old, new = measure_pair(name, old_root, new_root, opts.repeat, env)
        note = ""
        if not verdict(old, new, opts.max_slowdown):  # measure again before blaming the change
            old2, new2 = measure_pair(name, old_root, new_root, opts.repeat * 3, env)
            old, new = min(old, old2), min(new, new2)
            note = "  (measured twice)"
        change = 100 * (new / old - 1)
        ok = verdict(old, new, opts.max_slowdown)
        if not ok:
            failed.append(name)
        print(f"{name:<12} {old:>8.2f} {new:>8.2f} {change:>+7.1f}%{'' if ok else '  TOO SLOW'}{note}", flush=True)
    if failed:
        print(f"\n{', '.join(failed)}: more than {opts.max_slowdown:g}% slower than before.")
        return 1
    print(f"\nNo benchmark is more than {opts.max_slowdown:g}% slower.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=None, help="runs per cell (best is shown)")
    parser.add_argument("--against", metavar="OLD", help="regression check: the code/ folder of the old version")
    parser.add_argument("--new", metavar="NEW", help="with --against: the new version's code/ (default: this one)")
    parser.add_argument("--max-slowdown", type=float, default=10.0, metavar="PCT",
                        help="with --against: fail when a benchmark is more than PCT%% slower (default 10)")
    parser.add_argument("names", nargs="*", default=BENCHMARKS)
    opts = parser.parse_args(argv)
    if opts.against:
        opts.repeat = opts.repeat or 5
        return regression_check(opts)
    opts.repeat = opts.repeat or 3
    return table(opts)


if __name__ == "__main__":
    sys.exit(main())
