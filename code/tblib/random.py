"""random: random numbers and choices."""

from __future__ import annotations

import random as _random

from tblib._api import Module, check_type, fail


def load(api):
    m = Module("random", "Random numbers, picks and shuffles (use seed(n) to repeat them).")
    rng = _random.Random()

    @m.fn("int", 2, doc="int(low, high) -> number\nA random whole number from low to high (both included).")
    def rand_int(line, low, high):
        low = check_type(line, low, "whole", "random.int", "low")
        high = check_type(line, high, "whole", "random.int", "high")
        if low > high:
            fail(line, f"random.int() needs low ({low}) to be no bigger than high ({high}).")
        return rng.randint(low, high)

    @m.fn("number", 0, doc="number() -> number\nA random number from 0 up to (not including) 1.")
    def number(line):
        return rng.random()

    @m.fn("choice", 1, doc="choice(list) -> value\nOne item picked at random.")
    def choice(line, items):
        check_type(line, items, "list|text", "random.choice", "list")
        if not items:
            fail(line, "random.choice() can't pick from an empty list.")
        return rng.choice(items)

    @m.fn("shuffle", 1, doc="shuffle(list) -> list\nA new list with the same items in a random order.")
    def shuffle(line, items):
        check_type(line, items, "list", "random.shuffle", "list")
        copy = list(items)
        rng.shuffle(copy)
        return copy

    @m.fn("sample", 2, doc="sample(list, n) -> list\nn different items picked at random.")
    def sample(line, items, n):
        check_type(line, items, "list", "random.sample", "list")
        n = check_type(line, n, "whole", "random.sample", "n")
        if not 0 <= n <= len(items):
            fail(line, f"random.sample() can't pick {n} items from a list of {len(items)}.")
        return rng.sample(items, n)

    @m.fn("seed", 1, doc="seed(n)\nStart the random numbers from n, so the program picks the same ones every run.")
    def seed(line, n):
        rng.seed(check_type(line, n, "number|text", "random.seed", "n"))
        return None

    return m
