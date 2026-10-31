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
