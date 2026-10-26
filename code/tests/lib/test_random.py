from conftest import run_tb, tb_error


def test_seed_makes_results_repeat():
    src = "import random\nrandom.seed(42)\nsay(random.int(1, 100), random.number(), random.choice([1, 2, 3]), random.shuffle([1, 2, 3, 4]), random.sample([1, 2, 3], 2))"
    assert run_tb(src) == run_tb(src)


def test_ranges_and_types():
    src = """
import random
let ok = true
for i in range(500) {
  let n = random.int(1, 6)
  if n < 1 or n > 6 or type(n) != "number" or n != int(n) { ok = false }
  let x = random.number()
  if x < 0 or x >= 1 { ok = false }
}
say(ok, random.int(5, 5))
let xs = [1, 2, 3, 4, 5]
let s = random.shuffle(xs)
say(xs, sorted(s), len(random.sample(xs, 3)), random.choice("abc") in "abc")
"""
    assert run_tb(src) == ["true 5", "[1, 2, 3, 4, 5] [1, 2, 3, 4, 5] 3 true"]


def test_every_value_reachable():
    src = "import random\nlet seen = {}\nfor i in range(300) { seen[random.int(1, 3)] = true }\nsay(sorted(keys(seen)))"
    assert run_tb(src) == ["[1, 2, 3]"]


def test_random_does_not_change_care():
    a = run_tb("care()", seed=3)
    b = run_tb("import random\nrandom.seed(1)\nrandom.int(1, 9)\ncare()", seed=3)
    assert a == b


def test_errors():
    assert "random.int() needs low (5) to be no bigger than high (1)." in str(tb_error("import random\nrandom.int(5, 1)"))
    assert "needs low to be a whole number, not 1.5" in str(tb_error("import random\nrandom.int(1.5, 3)"))
    assert "can't pick from an empty list" in str(tb_error("import random\nrandom.choice([])"))
    assert "needs list to be a list or text, not a number" in str(tb_error("import random\nrandom.choice(5)"))
    assert "can't pick 5 items from a list of 2" in str(tb_error("import random\nrandom.sample([1, 2], 5)"))
    assert "needs list to be a list" in str(tb_error('import random\nrandom.shuffle("ab")'))
