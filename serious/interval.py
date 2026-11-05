"""Intervals: semitones, cents, frequency ratios and interval names (P5, m3, A4...)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .pitch import LETTERS, NATURAL_PC, Pitch, normalise

_MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
_PERFECT_STEPS = {0, 3, 4}  # unison, fourth, fifth
_SIMPLE_NAMES = ["P1", "m2", "M2", "m3", "M3", "P4", "TT", "P5", "m6", "M6", "m7", "M7"]
_QUALITY_WORDS = {"maj": "M", "min": "m", "aug": "A", "dim": "d", "perf": "P"}

_SEMITONES = re.compile(r"(\d+(?:\.\d+)?|\.\d+)\s*(?:st|semis?|semitones?)?", re.I)
_CENTS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:c|ct|cents?|¢)", re.I)
_RATIO = re.compile(r"(\d+(?:\.\d+)?)[/:](\d+(?:\.\d+)?)")
_NAMED = re.compile(r"(maj|min|aug|dim|perf|P|M|m|A+|d+)(\d+)")


class IntervalError(ValueError):
    pass


@dataclass(frozen=True)
class Interval:
    semitones: float
    steps: int | None = None  # diatonic steps when the interval was named (P5 -> 4)


def parse_interval(text: str) -> Interval:
    """Read +7, -3, 1.5, 700c, 3/2, 5:4, P5, -m3, M10, A4, d7, TT..."""
    t = normalise(text)
    sign = 1
    if t[:1] in "+-":
        sign = -1 if t[0] == "-" else 1
        t = t[1:]
    if t.lower() in ("tt", "tritone"):
        return Interval(6.0 * sign)
    if m := _SEMITONES.fullmatch(t):
        return Interval(float(m.group(1)) * sign)
    if m := _CENTS.fullmatch(t):
        return Interval(float(m.group(1)) / 100 * sign)
    if m := _RATIO.fullmatch(t):
        num, den = float(m.group(1)), float(m.group(2))
        if num <= 0 or den <= 0:
            raise IntervalError(f"ratio must be positive: {text!r}")
        return Interval(12 * math.log2(num / den) * sign)
    if m := _NAMED.fullmatch(t):
        quality = _QUALITY_WORDS.get(m.group(1), m.group(1))
        size = int(m.group(2))
        if size < 1:
            raise IntervalError(f"interval numbers start at 1 (unison): {text!r}")
        steps = size - 1
        simple, octaves = steps % 7, steps // 7
        if simple in _PERFECT_STEPS:
            offsets = {"P": 0}
        else:
            offsets = {"M": 0, "m": -1}
        if quality[0] == "A":
            offset = len(quality)
        elif quality[0] == "d":
            offset = -len(quality) - (0 if simple in _PERFECT_STEPS else 1)
        elif quality in offsets:
            offset = offsets[quality]
        else:
            kind = "perfect" if simple in _PERFECT_STEPS else "major/minor"
            raise IntervalError(f"{text!r}: a {size} is {kind}, not {quality!r}")
        semitones = _MAJOR_SCALE[simple] + 12 * octaves + offset
        return Interval(float(semitones * sign), steps * sign)
    raise IntervalError(
        f"can't read {text!r} as an interval "
        "(try 7, -3, 1.5, 700c, 3/2, P5, -m3, M10, TT)"
    )


def name_interval(semitones: float, steps: int | None = None) -> str:
    """Name the (unsigned) size of an interval; ``steps`` gives the exact spelling."""
    semitones, steps = abs(semitones), (abs(steps) if steps is not None else None)
    nearest = round(semitones)
    cents = round((semitones - nearest) * 100)
    suffix = f" {cents:+d}c" if cents else ""
    if steps is not None and not cents:
        named = _spelled_name(nearest, steps)
        if named:
            return named
    simple, octaves = nearest % 12, nearest // 12
    name = _SIMPLE_NAMES[simple]
    if simple == 0 and octaves:
        name = {1: "P8", 2: "P15"}.get(octaves, f"{octaves} octaves")
    elif octaves == 1:  # compound: 9ths, 10ths, 11ths...
        name = "A11" if name == "TT" else f"{name[0]}{int(name[1:]) + 7}"
    elif octaves:
        name = f"{name} + {octaves} oct"
    return name + suffix


def _spelled_name(semitones: int, steps: int) -> str | None:
    simple, octaves = steps % 7, steps // 7
    offset = semitones - (_MAJOR_SCALE[simple] + 12 * octaves)
    if simple in _PERFECT_STEPS:
        quality = {0: "P", 1: "A", 2: "AA", -1: "d", -2: "dd"}.get(offset)
    else:
        quality = {0: "M", -1: "m", 1: "A", 2: "AA", -2: "d", -3: "dd"}.get(offset)
    return f"{quality}{steps + 1}" if quality else None


def between(a: Pitch, b: Pitch) -> tuple[float, str]:
    """Signed semitones from a to b, and the interval's name (spelled when possible)."""
    semitones = round(b.midi - a.midi, 6)
    steps = None
    if a.letter and b.letter and a.has_octave and b.has_octave:
        steps = _letter_index(b) - _letter_index(a)
        if (steps < 0) != (semitones < 0) and semitones and steps:
            steps = None  # e.g. B#3 -> Cb4: spelling direction disagrees with pitch
    return semitones, name_interval(semitones, steps)


def _letter_index(p: Pitch) -> int:
    """Diatonic position (letter + 7 * octave) of a spelled pitch."""
    target = NATURAL_PC[p.letter] + p.alter
    octave = round((p.midi - target) / 12) - 1
    return LETTERS.index(p.letter) + 7 * octave
