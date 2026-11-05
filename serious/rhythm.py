"""Rhythms in (almost) any notation, and operations on them.

Every duration is parsed into a :class:`Duration`, an exact fraction of a whole
note (1/4 = a quarter note), so augmentations, tuplets and long cycles never
drift.  As with pitches, results are written back in the notation of the input.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from fractions import Fraction

from .pitch import normalise

RHYTHM_FORMAT_ALIASES = {
    "name": "name", "names": "name", "fraction": "fraction", "fractions": "fraction", "frac": "fraction",
    "lily": "lily", "ly": "lily", "lilypond": "lily", "beats": "beats", "beat": "beats",
    "ms": "ms", "time": "ms", "seconds": "ms", "pattern": "pattern", "x": "pattern", "all": "all",
}
RHYTHM_COLUMNS = ["fraction", "lily", "name", "beats", "ms"]

_NAMES = {"w": Fraction(1), "h": Fraction(1, 2), "q": Fraction(1, 4), "e": Fraction(1, 8),
          "s": Fraction(1, 16), "t": Fraction(1, 32)}
_WORDS = {
    "whole": "w", "semibreve": "w", "half": "h", "minim": "h", "quarter": "q", "crotchet": "q",
    "eighth": "e", "8th": "e", "quaver": "e", "sixteenth": "s", "16th": "s", "semiquaver": "s",
    "thirtysecond": "t", "thirty-second": "t", "32nd": "t", "demisemiquaver": "t",
}
_POWERS = [1, 2, 4, 8, 16, 32, 64, 128]

_NAME = re.compile(r"(?P<name>[a-z]+|8th|16th|32nd|thirty-second)(?P<tuplet>[3-9])?(?P<dots>\.*)")
_LILY = re.compile(r"(?P<base>1|2|4|8|16|32|64|128)(?P<dots>\.*)(?:\*(?P<num>\d+)(?:/(?P<den>\d+))?)?")
_FRACTION = re.compile(r"(\d+)/(\d+)(\.*)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_CLOCK = re.compile(r"(\d+(?:\.\d+)?|\.\d+)\s*(ms|s|sec|secs|seconds?)", re.I)
_PROPORTIONS = re.compile(r"\d+(?::\d+)+")
_PATTERN = re.compile(r"[xX.\-_]+")
_TOKEN = re.compile(r"[^\s,;\[\]{}()<>|]+")


class RhythmError(ValueError):
    pass


@dataclass(frozen=True)
class Duration:
    value: Fraction            # length in whole notes: 1/4 = a quarter note
    rest: bool = False
    source: str = "fraction"   # the notation it was written in

    @property
    def beats(self) -> Fraction:
        """Length in quarter notes."""
        return self.value * 4


@dataclass(frozen=True)
class Timing:
    tempo: Fraction = Fraction(120)          # quarter notes per minute
    pulse: Fraction = Fraction(1, 16)        # grid for x..x patterns and 3:2:2 proportions
    meter: tuple[int, int] = (4, 4)
    force: str | None = None                 # "lily" or "beats" for bare numbers

    def seconds(self, value: Fraction) -> float:
        return float(value * 4 * 60 / self.tempo)


# --------------------------------------------------------------------------- parsing

def tokenize(text: str) -> list[str]:
    """Split on spaces, commas, brackets and barlines (``q e e | h``)."""
    return _TOKEN.findall(normalise(text))


def parse_rhythm(tokens: list[str], timing: Timing = Timing()) -> list[Duration]:
    """Parse duration tokens.

    Bare numbers are beats (quarter note = 1), unless the sequence contains an
    unmistakable LilyPond duration (``4.``, ``8*2/3``) or ``--from lily`` is
    given; then bare powers of two are LilyPond durations (4 = quarter note).
    """
    lily = timing.force == "lily" or (
        timing.force is None and any(_is_marked_lily(t.lstrip("rR")) for t in tokens))
    durations = []
    for token in tokens:
        durations.extend(parse_token(token, timing, lily))
    return durations


def _is_marked_lily(token: str) -> bool:
    m = _LILY.fullmatch(token)
    return bool(m and (m.group("dots") or m.group("num")))


def parse_token(token: str, timing: Timing = Timing(), lily: bool = False) -> list[Duration]:
    tok = normalise(token)
    if _PATTERN.fullmatch(tok) and ("x" in tok.lower() or len(tok) > 1):
        return pattern_durations([c in "xX" for c in tok], timing.pulse)
    rest = len(tok) > 1 and tok[0] in "rR" and not tok.lower().startswith("rest")
    core = tok[1:] if rest else tok
    if _PROPORTIONS.fullmatch(core):
        values = [int(n) * timing.pulse for n in core.split(":")]
        if any(v <= 0 for v in values):
            raise RhythmError(f"durations must be longer than zero: {token!r}")
        return [Duration(v, rest, "pattern") for v in values]
    value, source = _parse_value(core, timing, lily)
    if value <= 0:
        raise RhythmError(f"durations must be longer than zero: {token!r}")
    return [Duration(value, rest, source)]


def _parse_value(core: str, timing: Timing, lily: bool) -> tuple[Fraction, str]:
    low = core.lower()
    if m := _CLOCK.fullmatch(core):
        seconds = Fraction(m.group(1)) / (1000 if m.group(2).lower() == "ms" else 1)
        return seconds * timing.tempo / 60 / 4, "ms"
    if m := _FRACTION.fullmatch(core):
        if int(m.group(2)) == 0:
            raise RhythmError(f"can't read {core!r} as a duration (division by zero)")
        return _dotted(Fraction(int(m.group(1)), int(m.group(2))), len(m.group(3))), "fraction"
    if m := _LILY.fullmatch(core):
        if m.group("dots") or m.group("num") or lily:
            if int(m.group("den") or 1) == 0:
                raise RhythmError(f"can't read {core!r} as a duration (division by zero)")
            value = _dotted(Fraction(1, int(m.group("base"))), len(m.group("dots")))
            if m.group("num"):
                value *= Fraction(int(m.group("num")), int(m.group("den") or 1))
            return value, "lily"
    if _NUMBER.fullmatch(core):
        return Fraction(core) / 4, "beats"
    dots, tuplet = 0, None
    for prefix, kind in (("double-dotted-", 2), ("dotted-", 1), ("triplet-", 3)):
        if low.startswith(prefix):
            low = low[len(prefix):]
            if kind == 3:
                tuplet = 3
            else:
                dots = kind
            break
    if m := _NAME.fullmatch(low):
        name = _WORDS.get(m.group("name"), m.group("name"))
        if name in _NAMES:
            value = _dotted(_NAMES[name], dots + len(m.group("dots")))
            n = int(m.group("tuplet")) if m.group("tuplet") else tuplet
            return (value * _tuplet_factor(n) if n else value), "name"
    raise RhythmError(
        f"can't read {core!r} as a duration "
        "(try q, e., s3, 1/4, 3/8, 4., 8*2/3, 1.5 (beats), 250ms, x..x.x.., 3:2:2:1, rq)"
    )


def _dotted(value: Fraction, dots: int) -> Fraction:
    return value * (2 - Fraction(1, 2 ** dots))


def _tuplet_factor(n: int) -> Fraction:
    """3 -> 2/3 (triplet), 5 -> 4/5, 6 -> 2/3, 7 -> 4/7: n notes in the time of the power of two below."""
    return Fraction(2 ** int(math.log2(n)), n)


def pattern_durations(onsets: list[bool], pulse: Fraction) -> list[Duration]:
    """Durations from an onset pattern: each note lasts until the next onset; leading silence is a rest."""
    durations, count, started = [], 0, False
    for onset in onsets:
        if onset:
            if count:
                durations.append(Duration(count * pulse, rest=not started, source="pattern"))
            count, started = 1, True
        else:
            count += 1
    if count:
        durations.append(Duration(count * pulse, rest=not started, source="pattern"))
    return durations


# --------------------------------------------------------------------------- formatting

def format_duration(d: Duration, fmt: str, timing: Timing = Timing()) -> str:
    prefix = "r" if d.rest else ""
    v = d.value
    if fmt == "fraction":
        return prefix + _fraction(v)
    if fmt == "beats":
        return prefix + _number(d.beats)
    if fmt == "ms":
        return prefix + _number(Fraction(timing.seconds(v) * 1000), 1) + "ms"
    if fmt == "lily":
        return prefix + _lily(v)
    if fmt == "name":
        return prefix + (_name(v) or _fraction(v))
    raise RhythmError(f"unknown rhythm format {fmt!r}")


def format_rhythm(durations: list[Duration], fmt: str, timing: Timing = Timing()) -> str:
    if fmt == "pattern":
        return to_pattern(durations, timing.pulse)
    return " ".join(format_duration(d, fmt, timing) for d in durations)


def to_pattern(durations: list[Duration], pulse: Fraction) -> str:
    pieces = []
    started = False
    for d in durations:
        if d.rest and started:
            raise RhythmError("patterns can't hold rests after notes; try another format")
        started = started or not d.rest
        pulses = d.value / pulse
        if pulses.denominator != 1:
            raise RhythmError(f"{d.value} doesn't fit a grid of {pulse} pulses; try another --pulse")
        pieces.append(("." if d.rest else "x") + "." * (int(pulses) - 1))
    return "".join(pieces)


def _fraction(v: Fraction) -> str:
    """1/4, 3/8; whole numbers as 1/1, 2/1, since a bare number would be read back as beats."""
    return f"{v.numerator}/{v.denominator}"


def _number(x: Fraction, places: int = 4) -> str:
    x = round(float(x), places)
    return str(int(x)) if x == int(x) else f"{x:.{places}f}".rstrip("0").rstrip(".")


def _lily(v: Fraction) -> str:
    for base in _POWERS:
        for dots in range(4):
            if _dotted(Fraction(1, base), dots) == v:
                return f"{base}{'.' * dots}"
    for base in _POWERS:  # a whole number of some note value: 16*5
        n = v * base
        if n.denominator == 1:
            return f"{base}*{n.numerator}"
    base = 1
    while v * base <= Fraction(1, 2):  # tuplets: 8*2/3
        base *= 2
    return f"{base}*{v * base}"


def _name(v: Fraction) -> str | None:
    for tuplet in (None, 3, 5, 6, 7):
        factor = _tuplet_factor(tuplet) if tuplet else 1
        for name, base in _NAMES.items():
            for dots in range(3):
                if _dotted(base, dots) * factor == v:
                    return f"{name}{tuplet or ''}{'.' * dots}"
    return None


def output_format(durations: list[Duration], requested: str | None) -> str:
    if requested:
        return requested
    sources = {d.source for d in durations}
    return sources.pop() if len(sources) == 1 else "fraction"


# --------------------------------------------------------------------------- operations

def scale(durations: list[Duration], ratio: Fraction) -> list[Duration]:
    if ratio <= 0:
        raise RhythmError("the ratio must be positive")
    return [replace(d, value=d.value * ratio) for d in durations]


def rotate(durations: list[Duration], n: int) -> list[Duration]:
    n %= len(durations)
    return durations[n:] + durations[:n]


def palindrome(durations: list[Duration], centre: Duration | None = None) -> list[Duration]:
    """A non-retrogradable rhythm: the durations, a central value, then the durations reversed.

    Without a centre, the last duration becomes the centre.
    """
    if centre is not None:
        return durations + [centre] + durations[::-1]
    return durations + durations[-2::-1]


def is_palindrome(durations: list[Duration]) -> bool:
    shape = [(d.value, d.rest) for d in durations]
    return shape == shape[::-1]


def add_value(durations: list[Duration], value: Duration, at: int | None = None) -> list[Duration]:
    """Messiaen's added value as a note or a rest, inserted before position ``at`` (default: the end)."""
    at = len(durations) if at is None else at
    return durations[:at] + [value] + durations[at:]


def add_dot(durations: list[Duration], index: int) -> list[Duration]:
    """Messiaen's added value as a dot: lengthen one duration by half."""
    d = durations[index]
    return durations[:index] + [replace(d, value=d.value * Fraction(3, 2))] + durations[index + 1:]


def euclid(onsets: int, steps: int, rotation: int = 0) -> list[bool]:
    """Bjorklund's algorithm: ``onsets`` spread as evenly as possible over ``steps`` pulses."""
    if not 0 <= onsets <= steps or steps < 1:
        raise RhythmError("need 0 <= onsets <= steps, e.g. `euclid 3 8`")
    if onsets == 0:
        return [False] * steps
    front, back = [[True]] * onsets, [[False]] * (steps - onsets)
    while len(back) > 1:
        pairs = min(len(front), len(back))
        front, back = [front[i] + back[i] for i in range(pairs)], front[pairs:] or back[pairs:]
    pattern = [x for group in front + back for x in group]
    rotation %= steps
    return pattern[rotation:] + pattern[:rotation]


def infinity_rhythm(length: int, pulse: Fraction) -> list[Duration]:
    """Durations of |a(n)| + 1 pulses, where a is Nørgård's infinity series."""
    from .generate import infinity_series
    return [Duration((abs(a) + 1) * pulse, source="pattern") for a in infinity_series(length)]


def total(durations: list[Duration]) -> Fraction:
    return sum((d.value for d in durations), Fraction(0))


def onsets(durations: list[Duration]) -> list[Fraction]:
    """Start times (in whole notes) of the notes, skipping rests."""
    times, t = [], Fraction(0)
    for d in durations:
        if not d.rest:
            times.append(t)
        t += d.value
    return times


def bar_position(time: Fraction, meter: tuple[int, int]) -> str:
    """'bar:beat' (both from 1) of a time, counting beats in the meter's unit."""
    count, unit = meter
    bar_length = Fraction(count, unit)
    bar = int(time // bar_length)
    beat = (time - bar * bar_length) * unit + 1
    return f"{bar + 1}:{_number(beat, 3)}"


def lcm_time(a: Fraction, b: Fraction) -> Fraction:
    """Least common multiple of two durations."""
    denominator = math.lcm(a.denominator, b.denominator)
    return Fraction(math.lcm(a.numerator * (denominator // a.denominator),
                             b.numerator * (denominator // b.denominator)), denominator)
