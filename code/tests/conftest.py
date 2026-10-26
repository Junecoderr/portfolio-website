"""Shared helpers for the TB test suite."""

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tb  # noqa: E402


def run_tb(source, inputs=None, seed=0):
    """Run TB source and return the list of output lines."""
    lines = []
    answers = list(inputs or [])
    prompts = []

    def input_fn(prompt):
        prompts.append(prompt)
        if not answers:
            raise EOFError
        return answers.pop(0)

    interp = tb.Interpreter(output=lines.append, input_fn=input_fn, rng=random.Random(seed))
    tb.run(source, interpreter=interp)
    run_tb.last_prompts = prompts
    return lines


def tb_error(source, inputs=None):
    """Run TB source that must fail; return the TBError."""
    with pytest.raises(tb.TBError) as info:
        run_tb(source, inputs)
    return info.value


@pytest.fixture
def root():
    return ROOT


@pytest.fixture(autouse=True)
def keep_ctrl_c_handler():
    """Some tests exercise code that ignores Ctrl+C; put pytest's handler back."""
    import signal
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)
