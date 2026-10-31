"""bench/bench.py's regression check (run by CI): the threshold, and that it
refuses a folder that isn't a TB checkout."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tb_bench", ROOT / "bench" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_more_than_the_allowed_slowdown_fails():
    assert bench.verdict(1.0, 1.10, 10)
    assert bench.verdict(2.0, 1.0, 10)
    assert not bench.verdict(1.0, 1.11, 10)
    assert not bench.verdict(1.0, 1.06, 5)


def test_the_check_needs_another_checkout(tmp_path):
    with pytest.raises(SystemExit, match="not a TB checkout"):
        bench.main(["--against", str(tmp_path), "fib"])


def test_each_side_runs_its_own_benchmarks_and_output_changes_are_listed(tmp_path, capsys):
    """Fake checkouts whose tb.py prints the program's text: a benchmark whose
    output changed is reported (not failed), one only the new side has is skipped."""
    for side, files in (("old", {"same": "1", "edited": "old answer"}),
                        ("new", {"same": "1", "edited": "new answer", "added": "2"})):
        (tmp_path / side / "bench").mkdir(parents=True)
        (tmp_path / side / "tb.py").write_text("import sys\nprint(open(sys.argv[1]).read())\n", encoding="utf-8")
        for name, text in files.items():
            (tmp_path / side / "bench" / f"{name}.tb").write_text(text, encoding="utf-8")
    code = bench.main(["--against", str(tmp_path / "old"), "--new", str(tmp_path / "new"), "--repeat", "1",
                       "--max-slowdown", "100000", "same", "edited", "added"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "New benchmarks (not compared): added." in out
    assert "edited: old printed 'old answer', new prints 'new answer'" in out
    assert "same:" not in out.split("Output changed", 1)[1]
