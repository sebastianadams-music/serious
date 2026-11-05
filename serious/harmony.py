"""Chords (naming and building) and scales."""

from __future__ import annotations

import re

from .interval import parse_interval
from .pcset import pcs_of
from .pitch import NATURAL_PC, Pitch, PitchError, Settings, format_pitch, parse_pitch

# symbol, aliases, intervals above the root
CHORDS = [
    ("", ("maj", "M", "major"), "P1 M3 P5"),
    ("m", ("min", "-", "minor"), "P1 m3 P5"),
    ("dim", ("°", "o"), "P1 m3 d5"),
    ("aug", ("+",), "P1 M3 A5"),
    ("sus4", ("sus",), "P1 P4 P5"),
    ("sus2", (), "P1 M2 P5"),
    ("5", (), "P1 P5"),
    ("7", ("dom7",), "P1 M3 P5 m7"),
    ("maj7", ("M7", "Δ", "Δ7", "ma7"), "P1 M3 P5 M7"),
    ("m7", ("min7", "-7"), "P1 m3 P5 m7"),
    ("m7b5", ("ø", "ø7", "min7b5", "-7b5"), "P1 m3 d5 m7"),
    ("dim7", ("°7", "o7"), "P1 m3 d5 d7"),
    ("mMaj7", ("mM7", "m(maj7)", "minMaj7", "-Δ7"), "P1 m3 P5 M7"),
    ("6", (), "P1 M3 P5 M6"),
    ("m6", ("min6", "-6"), "P1 m3 P5 M6"),
    ("aug7", ("+7", "7#5"), "P1 M3 A5 m7"),
    ("augMaj7", ("+maj7", "maj7#5"), "P1 M3 A5 M7"),
    ("7b5", (), "P1 M3 d5 m7"),
    ("7sus4", ("7sus",), "P1 P4 P5 m7"),
    ("add9", ("add2",), "P1 M3 P5 M9"),
    ("madd9", (), "P1 m3 P5 M9"),
    ("6/9", ("69",), "P1 M3 P5 M6 M9"),
    ("9", (), "P1 M3 P5 m7 M9"),
    ("maj9", ("M9",), "P1 M3 P5 M7 M9"),
    ("m9", ("min9", "-9"), "P1 m3 P5 m7 M9"),
    ("7b9", (), "P1 M3 P5 m7 m9"),
    ("7#9", (), "P1 M3 P5 m7 A9"),
    ("7#11", (), "P1 M3 P5 m7 A11"),
    ("maj7#11", (), "P1 M3 P5 M7 A11"),
    ("11", (), "P1 M3 P5 m7 M9 P11"),
    ("m11", ("min11", "-11"), "P1 m3 P5 m7 M9 P11"),
    ("13", (), "P1 M3 P5 m7 M9 M13"),
    ("maj13", ("M13",), "P1 M3 P5 M7 M9 M13"),
]
_BY_SYMBOL = {alias: chord for chord in CHORDS for alias in (chord[0], *chord[1])}


def _pc_set(intervals: str) -> frozenset[int]:
    return frozenset(int(parse_interval(i).semitones) % 12 for i in intervals.split())


def root_name(pc: int, pitches: list[Pitch], settings: Settings) -> str:
    """Name a pitch class, reusing the input's spelling when it has one."""
    for p in pitches:
        if p.letter and round(p.pc) % 12 == pc:
            return format_pitch(Pitch(p.midi, False, p.letter, p.alter), "name", settings)
    return format_pitch(Pitch(60 + pc, False), "name", settings)


def identify(pitches: list[Pitch], settings: Settings) -> list[str]:
    """All chord symbols matching the pitch classes exactly, root position first."""
    bass = min(pitches, key=lambda p: p.midi) if all(p.has_octave for p in pitches) else pitches[0]
    bass_pc = round(bass.pc) % 12
    pcs = set(pcs_of(pitches))
    found = []
    for root in sorted(pcs, key=lambda pc: pc != bass_pc):
        relative = frozenset((pc - root) % 12 for pc in pcs)
        for symbol, _, intervals in CHORDS:
            if _pc_set(intervals) == relative:
                name = root_name(root, pitches, settings) + symbol
                if root != bass_pc:
                    name += "/" + root_name(bass_pc, pitches, settings)
                found.append(name)
    return found


def build(symbol: str, settings: Settings, octave: int = 4) -> list[Pitch]:
    """Pitches of a chord symbol such as Cmaj7, F#m7b5, Bb7/D or Eb6/9."""
    m = re.fullmatch(r"([A-H])(#{1,2}|b{1,2}|♯|♭|x)?(.*)", symbol)
    if not m:
        raise PitchError(f"can't read {symbol!r} as a chord symbol (try Cmaj7, F#m7b5, Bb7/D)")
    root_text, quality = m.group(1) + (m.group(2) or ""), m.group(3)
    bass_text = None
    if "/" in quality:
        head, tail = quality.rsplit("/", 1)
        if re.fullmatch(r"[A-H](#{1,2}|b{1,2}|♯|♭|x)?", tail):
            quality, bass_text = head, tail
    if quality not in _BY_SYMBOL:
        known = ", ".join(c[0] or "(major)" for c in CHORDS)
        raise PitchError(f"unknown chord quality {quality!r} in {symbol!r}; known: {known}")
    root = _at_octave(root_text, octave, settings)
    tones = []
    for name in _BY_SYMBOL[quality][2].split():
        interval = parse_interval(name)
        tones.append(root.transposed(interval.semitones, interval.steps))
    if bass_text:
        bass = _at_octave(bass_text, octave, settings)
        while bass.midi >= root.midi:
            bass = bass.transposed(-12, -7)
        tones.insert(0, bass)
    return tones


def _at_octave(name: str, octave: int, settings: Settings) -> Pitch:
    p = parse_pitch(name, settings, "name")
    midi = 12 * (octave - settings.middle_c + 5) + NATURAL_PC[p.letter] + p.alter
    return Pitch(midi, True, p.letter, p.alter, "name")


SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "harmonic-minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic-minor": (0, 2, 3, 5, 7, 9, 11),
    "harmonic-major": (0, 2, 4, 5, 7, 8, 11),
    "phrygian-dominant": (0, 1, 4, 5, 7, 8, 10),
    "lydian-dominant": (0, 2, 4, 6, 7, 9, 10),
    "altered": (0, 1, 3, 4, 6, 8, 10),
    "hungarian-minor": (0, 2, 3, 6, 7, 8, 11),
    "double-harmonic": (0, 1, 4, 5, 7, 8, 11),
    "neapolitan-major": (0, 1, 3, 5, 7, 9, 11),
    "neapolitan-minor": (0, 1, 3, 5, 7, 8, 11),
    "pentatonic": (0, 2, 4, 7, 9),
    "minor-pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "hirajoshi": (0, 2, 3, 7, 8),
    "in": (0, 1, 5, 7, 8),
    "whole-tone": (0, 2, 4, 6, 8, 10),
    "octatonic": (0, 1, 3, 4, 6, 7, 9, 10),
    "whole-half": (0, 2, 3, 5, 6, 8, 9, 11),
    "hexatonic": (0, 1, 4, 5, 8, 9),
    "messiaen-3": (0, 2, 3, 4, 6, 7, 8, 10, 11),
    "messiaen-4": (0, 1, 2, 5, 6, 7, 8, 11),
    "messiaen-5": (0, 1, 5, 6, 7, 11),
    "messiaen-6": (0, 2, 4, 5, 6, 8, 10, 11),
    "messiaen-7": (0, 1, 2, 3, 5, 6, 7, 8, 9, 11),
    "chromatic": tuple(range(12)),
}
SCALE_ALIASES = {
    "ionian": "major", "aeolian": "minor", "natural-minor": "minor",
    "major-pentatonic": "pentatonic", "super-locrian": "altered",
    "messiaen-1": "whole-tone", "half-whole": "octatonic", "messiaen-2": "octatonic",
    "diminished": "octatonic", "augmented": "hexatonic",
}


def scale_name(words: list[str]) -> str:
    name = "-".join(words).lower().replace("_", "-").replace(" ", "-")
    name = SCALE_ALIASES.get(name, name)
    if name not in SCALES:
        raise PitchError(f"unknown scale {name!r}; see `serious scale --list`")
    return name


def scale(root: Pitch, name: str) -> list[Pitch]:
    """A scale from ``root``; seven-note scales are spelled with one letter per degree."""
    steps = SCALES[name]
    tones = []
    for degree, semitones in enumerate(steps):
        spelled = root.transposed(semitones, degree if len(steps) == 7 else None)
        # rebuild from the root so the scale ascends even when octaves aren't shown
        tones.append(Pitch(root.midi + semitones, root.has_octave, spelled.letter, spelled.alter, root.source))
    return tones
