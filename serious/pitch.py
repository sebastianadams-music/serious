"""Pitches in (almost) any notation.

Every token is parsed into a :class:`Pitch`, whose ``midi`` value is a float
(60.0 = middle C, fractions are microtones).  Formatting turns it back into
whichever notation is wanted, so "understanding every notation" boils down to
one parser and one formatter per notation.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

LETTERS = "CDEFGAB"
NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Default spellings (letter, alteration) for each pitch class.
_SPELLINGS = {
    "sharps": ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"],
    "flats": ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"],
    "auto": ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
}

FORMATS = ("name", "midi", "mc", "pc", "hz", "lily", "solfege")
FORMAT_ALIASES = {
    "name": "name", "names": "name", "note": "name", "notes": "name",
    "midi": "midi", "m": "midi",
    "mc": "mc", "midicent": "mc", "midicents": "mc", "cents": "mc",
    "pc": "pc", "pcs": "pc", "pitchclass": "pc", "pitch-class": "pc",
    "hz": "hz", "freq": "hz", "frequency": "hz",
    "lily": "lily", "ly": "lily", "lilypond": "lily", "dutch": "lily",
    "solfege": "solfege", "solfège": "solfege", "solf": "solfege",
    "all": "all",
}

SOLFEGE_OUT = {"C": "Do", "D": "Re", "E": "Mi", "F": "Fa", "G": "Sol", "A": "La", "B": "Si"}
_SOLFEGE_IN = [  # longest first, so "sol" wins over "so"
    ("sol", "G"), ("do", "C"), ("ut", "C"), ("ré", "D"), ("re", "D"), ("mi", "E"),
    ("fa", "F"), ("so", "G"), ("la", "A"), ("si", "B"), ("ti", "B"),
]

# Accidental spellings, tried longest first.  Values are in semitones.
_SYMBOL_ACCIDENTALS = [
    ("##", 2), ("#", 1), ("♯", 1), ("x", 2), ("𝄪", 2),
    ("b", -1), ("♭", -1), ("𝄫", -2), ("♮", 0),
    ("𝄲", 0.5), ("𝄳", -0.5), ("𝄰", 1.5), ("𝄱", -1.5),  # quarter / three-quarter tones
]
_LETTER_ACCIDENTALS = sorted(
    _SYMBOL_ACCIDENTALS + [
        ("-sharp", 1), ("-flat", -1), ("sharp", 1), ("flat", -1), ("natural", 0),
        # LilyPond Dutch / German
        ("is", 1), ("es", -1), ("ih", 0.5), ("eh", -0.5),
        # LilyPond English
        ("tqs", 1.5), ("tqf", -1.5), ("qs", 0.5), ("qf", -0.5), ("s", 1), ("f", -1),
    ],
    key=lambda item: -len(item[0]),
)
_SOLFEGE_ACCIDENTALS = sorted(
    _SYMBOL_ACCIDENTALS + [
        ("diesis", 1), ("bemolle", -1), ("dièse", 1), ("diese", 1),
        ("bémol", -1), ("bemol", -1), ("sostenido", 1),
    ],
    key=lambda item: -len(item[0]),
)
_DUTCH = {"is", "es", "ih", "eh"}

_NUM = re.compile(r"[+-]?\d+(?:\.\d+)?|[+-]?\.\d+")
_HZ = re.compile(r"(\d+(?:\.\d+)?)(k?hz)", re.I)
_MC = re.compile(r"([+-]?\d+(?:\.\d+)?)mc", re.I)
_PREFIX = re.compile(r"(midi|mc|pc|hz|freq|ly|lily|name|note)[:=](.+)", re.I)
_CENTS = re.compile(r"([+-]\d+(?:\.\d+)?)(?:c|ct|cents?|¢)?", re.I)
_TOKEN = re.compile(r"ly:[^\s;\[\]{}()<>|]+|[^\s,;\[\]{}()<>|]+", re.I)
_LILY_TOKEN = re.compile(r"[^\s;\[\]{}()<>|]+")


class PitchError(ValueError):
    """Raised when a token can't be read as a pitch."""


@dataclass(frozen=True)
class Settings:
    a4: float = 440.0
    middle_c: int = 4            # octave number of MIDI 60 (4 = scientific, 3 = Yamaha)
    spelling: str = "auto"       # auto | sharps | flats
    force: str | None = None     # read every token as this notation
    approx: int | None = None    # OpenMusic style: 2 = semitones, 4 = quarter tones, ...


@dataclass(frozen=True)
class Pitch:
    midi: float
    has_octave: bool = True      # False for pitch classes and octave-less names
    letter: str | None = None    # spelling, when known ("D" for D#)
    alter: int = 0
    source: str = "midi"         # the notation it was written in

    @property
    def pc(self) -> float:
        return round(self.midi, 6) % 12

    def transposed(self, semitones: float, steps: int | None = None) -> Pitch:
        """Transpose; ``steps`` (diatonic steps of a named interval) keeps the spelling right."""
        letter, alter = None, 0
        if steps is not None and self.letter and float(semitones).is_integer():
            letter = LETTERS[(LETTERS.index(self.letter) + steps) % 7]
            target = NATURAL_PC[self.letter] + self.alter + int(semitones)
            alter = (target - NATURAL_PC[letter] + 6) % 12 - 6
            if abs(alter) > 2:
                letter, alter = None, 0
        elif steps is None and self.letter:
            letter, alter = self.letter, self.alter
        return self._moved(self.midi + semitones, letter, alter)

    def inverted(self, axis: float) -> Pitch:
        return self._moved(2 * axis - self.midi)

    def with_midi(self, midi: float) -> Pitch:
        return self._moved(midi, self.letter, self.alter)

    def _moved(self, midi: float, letter: str | None = None, alter: int = 0) -> Pitch:
        if not self.has_octave:
            midi = wrap(midi)
        if letter:
            target = NATURAL_PC[letter] + alter
            deviation = midi - (target + 12 * round((midi - target) / 12))
            if abs(deviation) > 0.5 + 1e-9:
                letter, alter = None, 0
        return Pitch(midi, self.has_octave, letter, alter, self.source)


def wrap(midi: float) -> float:
    """Fold a pitch into the octave above middle C (used for octave-less pitches).

    The nearest semitone ends up in 60-71, so a slightly flat C stays just below 60.
    """
    return 60 + (round(midi - 60, 6) + 0.5) % 12 - 0.5


def hz_to_midi(freq: float, a4: float = 440.0) -> float:
    if freq <= 0:
        raise PitchError(f"frequency must be positive, got {freq}")
    return 69 + 12 * math.log2(freq / a4)


def midi_to_hz(midi: float, a4: float = 440.0) -> float:
    return a4 * 2 ** ((midi - 69) / 12)


def normalise(token: str) -> str:
    token = unicodedata.normalize("NFC", token.strip())
    return token.replace("−", "-")  # typographic minus


# --------------------------------------------------------------------------- parsing

def tokenize(text: str, lily: bool = False) -> list[str]:
    """Split free text into pitch tokens; commas, brackets and pipes are separators.

    In LilyPond mode commas are octave marks (``c,,``), so they don't separate.
    """
    raw = (_LILY_TOKEN if lily else _TOKEN).findall(normalise(text))
    tokens: list[str] = []
    for tok in raw:  # "440 Hz" -> "440Hz"
        if tokens and tok.lower() in ("hz", "khz", "mc") and _NUM.fullmatch(tokens[-1]):
            tokens[-1] += tok
        else:
            tokens.append(tok)
    return tokens


def parse_pitches(tokens: list[str], settings: Settings = Settings()) -> list[Pitch]:
    """Parse a sequence of tokens.

    The sequence is looked at as a whole for one ambiguity: bare numbers 0-11
    (plus t/e) are read as pitch classes, otherwise numbers are MIDI notes
    (below 128) or midicents (128 and up).
    """
    force = settings.force
    if force is None and looks_like_pcs(tokens):
        force = "pc"
    return [parse_pitch(tok, settings, force) for tok in tokens]


def looks_like_pcs(tokens: list[str]) -> bool:
    has_pc = False
    for tok in tokens:
        if tok in ("e", "E"):
            continue
        if tok in ("t", "T") or (_NUM.fullmatch(tok) and 0 <= float(tok) < 12):
            has_pc = True
            continue
        return False
    return has_pc


def parse_pitch(token: str, settings: Settings = Settings(), force: str | None = None) -> Pitch:
    tok = normalise(token)
    if m := _PREFIX.fullmatch(tok):
        force = FORMAT_ALIASES.get(m.group(1).lower(), m.group(1).lower())
        if force == "note":
            force = "name"
        tok = m.group(2)
    try:
        if force == "pc":
            return _from_pc(tok)
        if force == "midi":
            return Pitch(float(tok), source="midi")
        if force == "mc":
            return Pitch(float(re.sub(r"(?i)mc$", "", tok)) / 100, source="mc")
        if force == "hz":
            m = _HZ.fullmatch(tok)
            freq = float(m.group(1)) * (1000 if m.group(2).lower() == "khz" else 1) if m else float(tok)
            return Pitch(hz_to_midi(freq, settings.a4), source="hz")
    except ValueError:
        raise PitchError(f"{token!r} is not a valid {force} value") from None
    if force == "lily":
        return _parse_name(tok, settings, lily=True)
    if force == "name":
        return _parse_name(tok, settings)

    if m := _HZ.fullmatch(tok):
        freq = float(m.group(1)) * (1000 if m.group(2).lower() == "khz" else 1)
        return Pitch(hz_to_midi(freq, settings.a4), source="hz")
    if m := _MC.fullmatch(tok):
        return Pitch(float(m.group(1)) / 100, source="mc")
    if _NUM.fullmatch(tok):
        value = float(tok)
        if value >= 128:
            return Pitch(value / 100, source="mc")
        return Pitch(value, source="midi")
    return _parse_name(tok, settings)


def _from_pc(tok: str) -> Pitch:
    value = {"t": 10.0, "e": 11.0, "a": 10.0, "b": 11.0}.get(tok.lower())
    if value is None:
        value = float(tok)
    return Pitch(wrap(value), has_octave=False, source="pc")


def _parse_name(token: str, settings: Settings, lily: bool = False) -> Pitch:
    low = token.lower()
    source = "name"
    for syllable, letter in _SOLFEGE_IN:
        if low.startswith(syllable):
            rest = token[len(syllable):]
            upper, table, source = True, _SOLFEGE_ACCIDENTALS, "solfege"
            break
    else:
        if not token or token[0].upper() not in "CDEFGABH":
            raise _not_understood(token)
        letter = "B" if token[0].upper() == "H" else token[0].upper()  # German H = B natural
        upper = token[0].isupper()
        rest = token[1:]
        table = _LETTER_ACCIDENTALS
        if lily:
            source = "lily"

    # accidentals
    alter: float = 0
    if table is _LETTER_ACCIDENTALS and letter in "AE" and rest[:1].lower() == "s" \
            and not rest.lower().startswith("sharp"):
        alter, rest, source = -1, rest[1:], "lily"  # Dutch/German "es" (E flat), "as" (A flat)
    while rest:
        for text, value in table:
            if rest.lower().startswith(text):
                alter += value
                rest = rest[len(text):]
                if text in _DUTCH:
                    source = "lily"
                break
        else:
            break

    # octave: LilyPond/Helmholtz marks, or a number
    octave: int | None = None      # scientific octave (middle C = 4)
    marks = re.match(r"[',]+", rest)
    if marks or lily or source == "lily":
        ups = marks.group().count("'") if marks else 0
        downs = marks.group().count(",") if marks else 0
        if upper and not ups:
            octave = 2 - downs      # Helmholtz: C = C2, C, = C1
        else:
            octave = 3 + ups - downs  # LilyPond: c = C3, c' = C4
        rest = rest[marks.end():] if marks else rest
        rest = re.sub(r"^\d+\.*", "", rest)  # LilyPond duration, e.g. c'4.
        if source == "name":
            source = "lily"
    elif m := re.match(r"-?\d+", rest):
        number = int(m.group())
        if number >= settings.middle_c - 5:  # otherwise "C-14" means 14 cents flat
            octave = number - (settings.middle_c - 4)
            rest = rest[m.end():]

    cents = 0.0
    if rest:
        m = _CENTS.fullmatch(rest)
        if not m:
            raise _not_understood(token)
        cents = float(m.group(1))

    value = NATURAL_PC[letter] + alter + cents / 100
    if octave is None:
        midi, has_octave = wrap(value), False
    else:
        midi, has_octave = 12 * (octave + 1) + value, True
    spelled = float(alter).is_integer() and abs(alter) <= 2
    return Pitch(midi, has_octave, letter if spelled else None, int(alter) if spelled else 0, source)


def _not_understood(token: str) -> PitchError:
    return PitchError(
        f"can't read {token!r} as a pitch "
        "(try e.g. F#4, Gb, 66, 6600, 369.99hz, fis', Sol#4, pc:6 -- see `serious formats`)"
    )


# --------------------------------------------------------------------------- formatting

def approximate(pitch: Pitch, approx: int | None) -> Pitch:
    """Round to 1/approx of a whole tone, like OpenMusic's approx (2 = semitone)."""
    if not approx:
        return pitch
    step = 2 / approx
    midi = round(math.floor(pitch.midi / step + 0.5) * step, 6)
    return pitch if midi == pitch.midi else pitch.with_midi(midi)


def spell(pitch: Pitch, settings: Settings) -> tuple[str, int, int, float]:
    """Return (letter, alteration, scientific octave, deviation in semitones)."""
    if pitch.letter and (settings.spelling == "auto"
                         or (settings.spelling == "sharps" and pitch.alter >= 0)
                         or (settings.spelling == "flats" and pitch.alter <= 0)):
        target = NATURAL_PC[pitch.letter] + pitch.alter
        k = round((pitch.midi - target) / 12)
        deviation = pitch.midi - (target + 12 * k)
        if abs(deviation) <= 0.5 + 1e-9:
            return pitch.letter, pitch.alter, k - 1, deviation
    base = math.ceil(round(pitch.midi, 6) - 0.5)
    name = _SPELLINGS[settings.spelling][base % 12]
    alter = {"#": 1, "b": -1}.get(name[1:], 0)
    return name[0], alter, base // 12 - 1, pitch.midi - base


def _cents_suffix(deviation: float) -> str:
    cents = round(deviation * 100)
    return f"{cents:+d}c" if cents else ""


def number(x: float, places: int = 2) -> str:
    x = round(x, places)
    if x == int(x):
        return str(int(x))
    return f"{x:.{places}f}".rstrip("0").rstrip(".")


def format_pitch(pitch: Pitch, fmt: str, settings: Settings = Settings()) -> str:
    if fmt == "midi":
        return number(pitch.midi)
    if fmt == "mc":
        return str(round(pitch.midi * 100))
    if fmt == "pc":
        return number(pitch.pc)
    if fmt == "hz":
        return number(midi_to_hz(pitch.midi, settings.a4)) + "Hz"
    if fmt == "lily":
        return _format_lily(pitch, settings)
    letter, alter, octave, deviation = spell(pitch, settings)
    accidental = "#" * alter if alter > 0 else "b" * -alter
    octave_text = str(octave + settings.middle_c - 4) if pitch.has_octave else ""
    step = SOLFEGE_OUT[letter] if fmt == "solfege" else letter
    return f"{step}{accidental}{octave_text}{_cents_suffix(deviation)}"


_LILY_ALTER = {-2: "eses", -1.5: "eseh", -1: "es", -0.5: "eh", 0: "", 0.5: "ih", 1: "is", 1.5: "isih", 2: "isis"}


def _format_lily(pitch: Pitch, settings: Settings) -> str:
    """Dutch LilyPond names; microtones are rounded to the nearest quarter tone."""
    quarter = round(pitch.midi * 2) / 2
    if quarter != pitch.midi:
        pitch = Pitch(quarter, pitch.has_octave, source=pitch.source)
    letter, alter, octave, deviation = spell(pitch, settings)
    suffix = _LILY_ALTER.get(alter + round(deviation * 2) / 2)
    if suffix is None:  # e.g. a quarter tone above a double sharp: drop the spelling
        pitch = Pitch(pitch.midi, pitch.has_octave, source=pitch.source)
        letter, alter, octave, deviation = spell(pitch, settings)
        suffix = _LILY_ALTER[alter + round(deviation * 2) / 2]
    if letter in "AE" and suffix in ("es", "eses"):
        suffix = suffix[1:]  # es, as, eses, ases
    marks = ""
    if pitch.has_octave:
        marks = "'" * (octave - 3) if octave >= 3 else "," * (3 - octave)
    return letter.lower() + suffix + marks


def output_format(pitches: list[Pitch], requested: str | None) -> str:
    """The requested format, or else the notation the input was written in."""
    if requested:
        return requested
    sources = {p.source for p in pitches}
    return sources.pop() if len(sources) == 1 else "name"
