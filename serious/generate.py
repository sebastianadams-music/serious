"""Generators: random twelve-tone rows and Per Nørgård's infinity series."""

from __future__ import annotations

import random


def random_row(rng: random.Random, first: int | None = None) -> list[int]:
    """A random ordering of the twelve pitch classes, optionally starting on ``first``."""
    rest = [pc for pc in range(12) if pc != first]
    rng.shuffle(rest)
    return [first, *rest] if first is not None else rest


def all_interval_row(rng: random.Random, first: int | None = None) -> list[int]:
    """A random row whose eleven successive intervals are all different (randomised backtracking)."""
    row = [rng.randrange(12) if first is None else first]
    used_intervals: set[int] = set()

    def extend() -> bool:
        if len(row) == 12:
            return True
        intervals = [i for i in range(1, 12) if i not in used_intervals]
        rng.shuffle(intervals)
        for interval in intervals:
            pc = (row[-1] + interval) % 12
            if pc in row:
                continue
            row.append(pc)
            used_intervals.add(interval)
            if extend():
                return True
            row.pop()
            used_intervals.discard(interval)
        return False

    extend()
    return row


def infinity_series(length: int) -> list[int]:
    """Nørgård's infinity series: a(0) = 0, a(2n) = -a(n), a(2n+1) = a(n) + 1.

    0 1 -1 2 1 0 -2 3 -1 2 0 1 2 -1 -3 4 ...  (OEIS A004718).  Every other term
    is the series inverted, and every fourth term is the series itself.
    """
    if length < 0:
        raise ValueError("the length can't be negative")
    series = [0] * length
    for n in range(1, length):
        series[n] = -series[n // 2] if n % 2 == 0 else series[n // 2] + 1
    return series
