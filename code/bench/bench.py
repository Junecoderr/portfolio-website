"""TB benchmarks: the tree-walker vs the bytecode VM vs plain Python.

Run from code/:  uv run --python /opt/homebrew/bin/python3.12 python bench/bench.py [--repeat N] [names...]

Each program runs in a fresh process (so the times include TB's start-up, as
a user would see it); the table shows the best of N runs. The VM and the
tree-walker must print the same output, and the Python version is a straight
translation of each program, as a baseline.
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


def timed(cmd, env) -> tuple[float, str]:
    start = time.perf_counter()
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    elapsed = time.perf_counter() - start
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{done.stdout}{done.stderr}")
    return elapsed, done.stdout.strip()


def best(cmd, env, repeat) -> tuple[float, str]:
    results = [timed(cmd, env) for _ in range(repeat)]
    return min(r[0] for r in results), results[0][1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=3, help="runs per cell (best is shown)")
    parser.add_argument("names", nargs="*", default=BENCHMARKS)
    opts = parser.parse_args(argv)
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


if __name__ == "__main__":
    sys.exit(main())
