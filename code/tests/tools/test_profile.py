"""`tb profile`: the sampling profiler, its report, the flame graph page and
the memory snapshot."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tbtools import profile  # noqa: E402

TB = [sys.executable, str(ROOT / "tb.py")]

HOT = """fn light(n) {
  return n + 1
}
fn heavy(n) {
  let total = 0
  for i in 0..n {
    total += i % 7
  }
  return total
}
let keep = []
for i in 0..2000 { keep.push(f"item {i}") }
let s = 0
for k in 0..30 {
  s += heavy(20000)
  s += light(k)
}
say(s)
"""


def run_tb(args, cwd, env=None):
    return subprocess.run(TB + args, cwd=cwd, capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, **(env or {})))


def test_profile_points_at_the_hot_function_and_line(tmp_path):
    (tmp_path / "hot.tb").write_text(HOT, encoding="utf-8")
    result = run_tb(["profile", "--html", "flame.html", "hot.tb"], tmp_path, {"TB_ENGINE": "vm"})
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.splitlines()[0].isdigit()  # the program's own output comes first
    functions = out.split("Functions", 1)[1].split("Busiest lines")[0]
    first = [line for line in functions.splitlines() if "%" in line][0]
    assert first.rstrip().endswith("heavy  (hot.tb:4)")
    lines = out.split("Busiest lines", 1)[1].split("Largest values")[0]
    assert "hot.tb:7  total += i % 7" in lines
    assert "keep  (list of 2,000 items)" in out
    page = (tmp_path / "flame.html").read_text(encoding="utf-8")
    assert "<script" not in page and "http" not in page  # one self-contained file
    assert 'class="bar"' in page and "heavy" in page and "<title>TB profile: hot.tb</title>" in page


def test_profile_on_the_tree_engine_and_without_the_fast_tier(tmp_path):
    (tmp_path / "hot.tb").write_text(HOT.replace("20000", "4000"), encoding="utf-8")
    for env in ({"TB_ENGINE": "tree"}, {"TB_JIT": "off"}):
        result = run_tb(["profile", "hot.tb"], tmp_path, env)
        assert result.returncode == 0, result.stderr
        functions = result.stdout.split("Functions", 1)[1].split("Busiest lines")[0]
        assert " heavy" in [line for line in functions.splitlines() if "%" in line][0]


def test_profiling_log_triage_points_at_its_hot_functions(tmp_path):
    result = run_tb(["profile", str(ROOT / "apps" / "log-triage" / "main.tb")], tmp_path)
    assert result.returncode == 0, result.stderr
    report = result.stdout.split("Profile of main.tb", 1)[1]
    functions = report.split("Functions", 1)[1].split("Busiest lines")[0]
    # loading the libraries, reading the logs and running the detections are where the time goes
    # (any of them, as a slow machine takes few samples of a short run)
    assert any(name in functions for name in ("logs.read (library)", "import logs (library)", "triage  (rules.tb:",
                                              "detect.", "span  (main.tb:"))


def test_a_failing_program_still_gets_its_profile(tmp_path):
    (tmp_path / "bad.tb").write_text("fn f(n) { return n / 0 }\nlet i = 0\nwhile i < 20000 { i += 1 }\nf(1)\n",
                                     encoding="utf-8")
    result = run_tb(["profile", "bad.tb"], tmp_path)
    assert result.returncode == 1
    assert "divide by zero" in result.stderr
    assert "Profile of bad.tb" in result.stdout


def test_profile_usage_errors(tmp_path):
    assert run_tb(["profile"], tmp_path).returncode == 2
    missing = run_tb(["profile", "nope.tb"], tmp_path)
    assert missing.returncode == 1 and "couldn't find" in missing.stderr
    assert run_tb(["profile", "--html"], tmp_path).returncode == 2


def test_memory_sizes_count_what_is_inside():
    items = [str(i) * 1000 for i in range(10)]
    assert profile.deep_size(items) > 10 * 1000
    shared = "y" * 5000
    assert profile.deep_size([shared, shared]) < 2 * 5000  # counted once
    loop = []
    loop.append(loop)
    assert profile.deep_size(loop) > 0
    assert profile.describe({"a": 1}) == "map of 1 key"
    assert profile.human_bytes(2048) == "2.0 KB"


def test_flame_tree_adds_up_the_stacks():
    tree = profile.flame_tree({("<main>", "a"): 2.0, ("<main>", "b"): 1.0, ("<main>",): 1.0})
    main = tree["children"]["<main>"]
    assert tree["value"] == main["value"] == 4.0
    assert main["children"]["a"]["value"] == 2.0 and main["children"]["b"]["value"] == 1.0
