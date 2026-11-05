import contextlib
import io
import random
import unittest

from serious import generate
from serious.cli import main


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(argv))
    return code, out.getvalue().strip()


class RowTest(unittest.TestCase):
    def test_random_rows_are_rows(self):
        rng = random.Random(0)
        for _ in range(200):
            self.assertEqual(sorted(generate.random_row(rng)), list(range(12)))
        self.assertEqual(generate.random_row(rng, first=4)[0], 4)

    def test_all_interval_rows(self):
        rng = random.Random(0)
        for first in [None] * 50 + list(range(12)):
            row = generate.all_interval_row(rng, first)
            self.assertEqual(sorted(row), list(range(12)))
            intervals = [(b - a) % 12 for a, b in zip(row, row[1:])]
            self.assertEqual(sorted(intervals), list(range(1, 12)))
            if first is not None:
                self.assertEqual(row[0], first)

    def test_seed_repeats(self):
        self.assertEqual(generate.random_row(random.Random(9)), generate.random_row(random.Random(9)))


class InfinitySeriesTest(unittest.TestCase):
    def test_first_terms(self):  # OEIS A004718
        self.assertEqual(generate.infinity_series(32), [
            0, 1, -1, 2, 1, 0, -2, 3, -1, 2, 0, 1, 2, -1, -3, 4,
            1, 0, -2, 3, 0, 1, -1, 2, -2, 3, 1, 0, 3, -2, -4, 5,
        ])

    def test_self_similarity(self):
        a = generate.infinity_series(1024)
        self.assertEqual(a[::4], a[:256])                   # every 4th term: the series itself
        self.assertEqual(a[::2], [-x for x in a[:512]])     # every 2nd term: its inversion

    def test_edges(self):
        self.assertEqual(generate.infinity_series(0), [])
        self.assertEqual(generate.infinity_series(1), [0])


class CliTest(unittest.TestCase):
    def test_improvise(self):
        code, out = run("improvise", "--seed", "3", "--to", "pc")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(map(int, out.split())), list(range(12)))
        self.assertEqual(run("improvise", "--seed", "3", "--to", "pc"), (0, out))  # same seed, same row
        code, out = run("improvise", "--first", "E", "--count", "3")
        self.assertEqual([line.split()[0] for line in out.splitlines()], ["E", "E", "E"])

    def test_infinity(self):
        self.assertEqual(run("infinity", "8", "--start", "G3"), (0, "G3 Ab3 F#3 A3 Ab3 G3 F3 Bb3"))
        self.assertEqual(run("infinity", "6", "--start", "60", "--step", "2"), (0, "60 62 58 64 62 60"))
        self.assertEqual(run("infinity", "4", "--start", "6000", "--step", "50c"), (0, "6000 6050 5950 6100"))


if __name__ == "__main__":
    unittest.main()
