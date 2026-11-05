"""The ``serious`` command line interface."""

from __future__ import annotations

import argparse
import difflib
import itertools
import math
import random
import re
import sys
from dataclasses import dataclass, replace
from fractions import Fraction

from . import __version__, export, generate, harmony, pcset, rhythm, spectral, synth
from .score import Event, Score, synth_notes
from .interval import IntervalError, between, name_interval, parse_interval
from .rhythm import Duration, RhythmError, Timing
from .pitch import (
    FORMAT_ALIASES, Pitch, PitchError, Settings, approximate, format_pitch, hz_to_midi,
    midi_to_hz, normalise, number, output_format, parse_pitches, tokenize,
)

DESCRIPTION = """\
Serious: computer-aided composition on the command line.

Write pitches however you like (C#4, Db, 61, 6100, 277.18hz, cis', Do#4,
pc:1, C4+31c...) and Serious answers in the same notation, unless you ask
for another one with --to.
"""

EXAMPLES = """\
examples:
  serious F# Bb4 6250 440hz          what are these? (all notations)
  serious convert C E G --to midi    60 64 67
  serious transpose M3 Eb4 G4 Bb4    G4 B4 D5 (spelled correctly)
  serious transpose -- -700c 6000    5300
  serious invert --axis E4 C4 D4 G4
  serious intervals C4 E4 G4 C5
  serious set 0 1 4 6                normal/prime form, Forte name, interval vector
  serious matrix 0 e 3 4 8 7 9 5 6 1 2 t
  serious chord E G C                C/E      serious chord Bbm7b5   Bb4 Db5 Fb5 Ab5
  serious scale D dorian             serious scale Eb messiaen 3
  serious harmonics C2 16 --approx 4
  serious fm C4 G4 --index 3
  serious play C4 E4 G4 Bb4 --wave saw    hear it (add --play to any command)
  serious harmonics C2 12 --play         serious chord Cmaj9 --out cmaj9.wav
  serious keyboard | serious matrix      click a row on a piano, then see its matrix
  serious improvise --rhythm "q e e" --out row.mid --out row.ly --out row.musicxml --out row.txt
  serious improvise --all-interval       a random all-interval row
  serious infinity 32 --start G3 --play  Nørgård's infinity series
  serious rhythm q e. s q h              rhythms: `serious rhythm -h` for augment, euclid, palindrome...
  serious play C4 E4 G4 --rhythm "q e e" --tempo 90
  serious isorhythm --color "C4 E4 D4 F#4 Bb4" --talea "q e. s q" --play
  echo "C D E" | serious transpose P5 | serious retrograde --to lily

run `serious formats` for every notation Serious understands.
"""

FORMATS_TEXT = """\
Serious reads all of these, and you can mix them freely:

  note names     C  F#  Bb  Ebb  Cx  C##  B#3  F♯4  E♭  c#4  H (German B)
                 C#4 = scientific octave (middle C = C4; change with --middle-c 3)
                 words: Csharp, E-flat; quarter tones: C𝄲 / Cqs / cih
  microtones     C4+50c  Bb3-14c  A4+31¢  (cents after the note)
  LilyPond       c'  fis''  bes,  es  as  cisih  (Dutch names, c' = middle C)
                 --from lily reads commas as octave marks (c,,) and ignores durations
  Helmholtz      C,  c  c'  c''   (uppercase C = C2)
  solfège        Do Re Mi Fa Sol La Si/Ti, Do#4, Sib3, Ré, Ut
  MIDI           60  61.5  (numbers below 128; decimals are microtones)
  midicents      6000  6150  6150mc  (numbers 128 and above, or with "mc")
  pitch classes  0 4 7  t e  (a list of only 0-11 / t / e is read as pitch classes)
  frequency      440hz  261.63Hz  1.2kHz  "440 Hz"
  prefixes       midi:7  pc:7  mc:6000  hz:440  ly:c,,  force one reading
  --from FORMAT  forces a reading for every pitch (midi, mc, pc, hz, name, lily)

Separators: spaces, commas, semicolons, brackets and pipes: "[0, 4, 7]", "<c e g>".
Pitches can also come from stdin, so commands can be piped together.

Intervals (transpose): 7  -3  1.5  700c  3/2  5:4  P5  m3  -M6  A4  d7  M10  TT

Output (--to): name, midi, mc, pc, hz, lily, solfege, or all.
Sound: `serious play …`, or --play / --out FILE.wav on any command.
Rhythms (q e. s, 1/4, 4., x..x, 250ms...): see `serious rhythm -h`.
"""


RHYTHM_HELP = """\
Rhythms in any notation, and operations on them. Results come back in the
notation you used, unless you ask for another one with --to.

notations (mix freely; spaces, commas and | barlines separate):
  names       w h q e s t, dotted q. e.., tuplets e3 (triplet eighth) s5;
              words: quarter, dotted-quarter, triplet-eighth, crotchet, quaver...
  fractions   1/4 3/8 1/12  (of a whole note)
  LilyPond    4. 8 16 8*2/3  (once one of these appears, bare 4 8 16 are LilyPond too;
              or use --from lily)
  beats       1 0.5 1.5  (bare numbers: quarter note = 1)
  time        250ms 0.5s  (at --tempo)
  patterns    x..x..x.  (x = onset, . = pulse, on a --pulse grid; default 1/16)
  proportions 3:2:2:1  (in pulses)
  rests       r before any duration: rq r1/8 r4. r250ms

examples:
  serious rhythm q e. s q h                    every notation, length, bar positions
  serious rhythm augment 3/2 q e e             q. e. e.
  serious rhythm euclid 5 8                    x.xx.xx.
  serious rhythm palindrome s e q.             s e q. e s (non-retrogradable)
  serious rhythm add-value s q q q             q q q s
  serious rhythm add-value dot --at 2 q q q    q q. q
  serious rhythm infinity 16 --play
"""


class CliError(Exception):
    pass


@dataclass
class Sound:
    """What a command would sound like with --play: its result, or the material analysed."""
    pitches: list[Pitch]
    together: bool = False                 # chord rather than melody
    amplitudes: list[float] | None = None  # relative levels, used when sounding together
    durations: list[Duration] | None = None  # a rhythm of its own (rests take time, not pitches)
    score: Score | None = None               # a fully worked-out score (isorhythm voices)


# --------------------------------------------------------------------------- helpers

def _format_type(value: str) -> str:
    fmt = FORMAT_ALIASES.get(value.lower())
    if not fmt:
        raise argparse.ArgumentTypeError(f"unknown format {value!r} (name, midi, mc, pc, hz, lily, solfege, all)")
    return fmt


def _input_format_type(value: str) -> str:
    fmt = _format_type(value)
    if fmt in ("all", "solfege"):
        raise argparse.ArgumentTypeError(f"--from {value}: use name, midi, mc, pc, hz or lily")
    return fmt


def _float(value: str) -> float:
    return float(normalise(value))


def _int(value: str) -> int:
    return int(normalise(value))


def settings_from(args) -> Settings:
    sharps, flats = getattr(args, "sharps", False), getattr(args, "flats", False)
    spelling = "sharps" if sharps else "flats" if flats else "auto"
    return Settings(a4=getattr(args, "a4", 440.0), middle_c=getattr(args, "middle_c", 4), spelling=spelling,
                    force=getattr(args, "force", None), approx=getattr(args, "approx", None))


def timing_from(args) -> Timing:
    return Timing(tempo=Fraction(str(getattr(args, "tempo", 120))), pulse=getattr(args, "pulse", Fraction(1, 16)),
                  meter=getattr(args, "meter", (4, 4)), force=getattr(args, "rhythm_force", None))


def _rhythm_format_type(value: str) -> str:
    fmt = rhythm.RHYTHM_FORMAT_ALIASES.get(value.lower())
    if not fmt:
        raise argparse.ArgumentTypeError(f"unknown rhythm format {value!r} (name, fraction, lily, beats, ms, pattern, all)")
    return fmt


def _pulse_type(value: str) -> Fraction:
    try:
        durations = rhythm.parse_token(value, Timing(), lily=True)
    except RhythmError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return durations[0].value


def _meter_type(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)/(\d+)", value)
    if not m or int(m.group(1)) < 1 or int(m.group(2)) < 1:
        raise argparse.ArgumentTypeError(f"meters look like 4/4, 3/4, 7/8 (got {value!r})")
    return int(m.group(1)), int(m.group(2))


def _tempo_type(value: str) -> float:
    tempo = _float(value)
    if tempo <= 0:
        raise argparse.ArgumentTypeError("the tempo must be positive")
    return tempo


def _dur_type(value: str) -> float:
    dur = _float(value)
    if dur <= 0:
        raise argparse.ArgumentTypeError("the duration must be positive")
    return dur


def _ratio(text: str) -> Fraction:
    try:
        ratio = Fraction(normalise(text))
    except (ValueError, ZeroDivisionError):
        raise CliError(f"can't read {text!r} as a ratio (try 2, 3/2, 1.5)") from None
    if ratio <= 0:
        raise CliError("the ratio must be positive")
    return ratio


def _clock(seconds: float) -> str:
    seconds = round(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def read_tokens(words: list[str], settings: Settings) -> list[str]:
    """Pitch tokens from the arguments, or from stdin when there are none."""
    if words:
        text = " ".join(words)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""
    return tokenize(text, lily=settings.force == "lily")


def read_pitches(words: list[str], settings: Settings, minimum: int = 1) -> list[Pitch]:
    pitches = parse_pitches(read_tokens(words, settings), settings)
    if len(pitches) < minimum:
        need = "a pitch" if minimum == 1 else f"at least {minimum} pitches"
        raise CliError(f"give {need}, as arguments or on stdin (see `serious formats`)")
    return pitches


def read_tempered(words: list[str], settings: Settings, minimum: int = 1) -> list[Pitch]:
    """Pitches for 12-tone analysis, rounded first when --approx is given."""
    return [approximate(p, settings.approx) for p in read_pitches(words, settings, minimum)]


def read_one(text: str, settings: Settings) -> Pitch:
    pitches = parse_pitches(tokenize(text, lily=settings.force == "lily"), settings)
    if len(pitches) != 1:
        raise CliError(f"expected a single pitch, got {text!r}")
    return pitches[0]


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    lines = ["  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)).rstrip() for row in [headers, *rows]]
    return "\n".join(lines)


ALL_COLUMNS = ["name", "midi", "mc", "pc", "hz", "lily", "solfege"]


def _row(p: Pitch, settings: Settings) -> list[str]:
    return [format_pitch(p, fmt, settings) for fmt in ALL_COLUMNS]


def emit(pitches: list[Pitch], args, settings: Settings, default: str | None = None) -> list[Pitch]:
    """Print pitches; returns them as printed (after --approx)."""
    fmt = args.to or default or output_format(pitches, None)
    pitches = [approximate(p, settings.approx) for p in pitches]
    if fmt == "all":
        print(table(ALL_COLUMNS, [_row(p, settings) for p in pitches]))
    else:
        print(" ".join(format_pitch(p, fmt, settings) for p in pitches))
    return pitches


# --------------------------------------------------------------------------- commands

def cmd_convert(args, settings):
    tokens = read_tokens(args.pitches, settings)
    if not tokens:
        raise CliError("give a pitch, as arguments or on stdin (see `serious formats`)")
    pitches = parse_pitches(tokens, settings)
    if args.to:
        return Sound(emit(pitches, args, settings))
    rows = [[tok, *_row(approximate(p, settings.approx), settings)] for tok, p in zip(tokens, pitches)]
    print(table(["input", *ALL_COLUMNS], rows))
    if any(p.source == "pc" for p in pitches) and not settings.force:
        print("(numbers 0-11 were read as pitch classes; write midi:7 or use --from midi for MIDI notes)",
              file=sys.stderr)
    return Sound([approximate(p, settings.approx) for p in pitches])


def cmd_transpose(args, settings):
    interval = parse_interval(args.interval)
    pitches = read_pitches(args.pitches, settings)
    return Sound(emit([p.transposed(interval.semitones, interval.steps) for p in pitches], args, settings))


def cmd_invert(args, settings):
    pitches = read_pitches(args.pitches, settings)
    axis = read_one(args.axis, settings).midi if args.axis else pitches[0].midi
    return Sound(emit([p.inverted(axis) for p in pitches], args, settings))


def cmd_retrograde(args, settings):
    pitches = read_pitches(args.pitches, settings)
    return Sound(emit(pitches[::-1], args, settings))


def cmd_rotate(args, settings):
    pitches = read_pitches(args.pitches, settings)
    n = args.by % len(pitches)
    return Sound(emit(pitches[n:] + pitches[:n], args, settings))


def cmd_intervals(args, settings):
    pitches = read_tempered(args.pitches, settings, minimum=2)
    fmt = output_format(pitches, args.to if args.to != "all" else None)
    octaveless = not all(p.has_octave for p in pitches)
    rows, sizes = [], []
    for a, b in zip(pitches, pitches[1:]):
        semitones, name = between(a, b)
        if octaveless:  # ordered pitch-class interval and interval class
            semitones = round(semitones % 12, 6)
            ic = min(semitones, 12 - semitones)
            rows.append([format_pitch(a, fmt, settings), "→", format_pitch(b, fmt, settings),
                         number(semitones), f"ic{number(ic)}", name_interval(semitones)])
            sizes.append(number(semitones))
        else:
            direction = "↑" if semitones > 0 else "↓" if semitones < 0 else "="
            signed = ("+" if semitones > 0 else "") + number(semitones)
            rows.append([format_pitch(a, fmt, settings), "→", format_pitch(b, fmt, settings),
                         signed, f"{name} {direction}"])
            sizes.append(signed)
    if args.semitones:
        print(" ".join(sizes))
    else:
        headers = ["from", "", "to", "pc interval", "class", "ascending"] if octaveless else \
            ["from", "", "to", "semitones", "interval"]
        print(table(headers, rows))
    return Sound(pitches)


def cmd_set(args, settings):
    pitches = read_tempered(args.pitches, settings)
    pcs = pcset.pcs_of(pitches)
    show_names = output_format(pitches, args.to if args.to != "all" else None) in ("name", "lily", "solfege")

    def as_set(values, brackets="{}"):
        text = brackets[0] + ",".join(pcset.compact([v]) for v in values) + brackets[1]
        if show_names and values:
            text += "   " + " ".join(harmony.root_name(v, pitches, settings) for v in values)
        return text

    normal = pcset.normal_form(pcs)
    prime = pcset.prime_form(pcs)
    comp = pcset.complement(pcs)
    tn, tni = pcset.symmetry(pcs)
    rows = [
        ["pitch classes", as_set(pcs)],
        ["normal form", as_set(normal, "[]")],
        ["prime form", f"({pcset.compact(prime)})"],
        ["Forte name", pcset.tn_type(pcs)],
        ["interval vector", "<" + "".join(str(n) for n in pcset.interval_vector(pcs)) + ">"],
        ["complement", as_set(comp) + (f"   [{pcset.forte_name(comp)}]" if comp else "")],
        ["Z-related to", pcset.z_partner(pcs) or "-"],
        ["invariant under", ", ".join([f"T{n}" for n in tn] + [f"T{n}I" for n in tni])],
    ]
    width = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"{label.ljust(width)}  {value}")
    return Sound(pitches, together=True)


def cmd_matrix(args, settings):
    pitches = read_tempered(args.pitches, settings, minimum=2)
    pcset.pcs_of(pitches)  # rejects microtones
    row = [round(p.pc) % 12 for p in pitches]
    if len(set(row)) != len(row):
        raise CliError("a row can't repeat pitch classes")
    first = row[0]
    grid = pcset.row_matrix(row)
    names = output_format(pitches, args.to if args.to != "all" else None) in ("name", "lily", "solfege")

    def cell(pc):
        return harmony.root_name(pc, pitches, settings) if names else pcset.compact([pc])

    def label(pc):
        return str((pc - first) % 12 if args.zero else pc)

    top = ["", *[f"I{label(pc)}" for pc in grid[0]], ""]
    body = [[f"P{label(r[0])}", *[cell(pc) for pc in r], f"R{label(r[0])}"] for r in grid]
    bottom = ["", *[f"RI{label(pc)}" for pc in grid[0]], ""]
    rows = [top, *body, bottom]
    widths = [max(len(r[i]) for r in rows) for i in range(len(top))]
    for r in rows:
        print("  ".join(c.rjust(w) for c, w in zip(r, widths)).rstrip())
    return Sound(pitches)


def cmd_chord(args, settings):
    words = args.pitches
    if len(words) == 1 and len(tokenize(words[0])) == 1:
        return Sound(emit(harmony.build(normalise(words[0]), settings, args.octave), args, settings), together=True)
    pitches = read_tempered(words, settings)
    names = harmony.identify(pitches, settings)
    pcs = pcset.pcs_of(pitches)
    set_class = f"set class {pcset.tn_type(pcs)} ({pcset.compact(pcset.prime_form(pcs))})"
    if not names:
        print(f"no common chord name; {set_class}")
    else:
        print(names[0] + (f"   (also {', '.join(names[1:])})" if len(names) > 1 else ""))
        print(set_class)
    return Sound(pitches, together=True)


def cmd_scale(args, settings):
    if args.list:
        width = max(map(len, harmony.SCALES))
        aliases: dict[str, list[str]] = {}
        for alias, target in harmony.SCALE_ALIASES.items():
            aliases.setdefault(target, []).append(alias)
        for name, steps in harmony.SCALES.items():
            also = f"  (= {', '.join(aliases[name])})" if name in aliases else ""
            print(f"{name.ljust(width)}  {' '.join(map(str, steps))}{also}")
        return
    if not args.root:
        raise CliError("give a root and a scale name, e.g. `serious scale D dorian` (or --list)")
    root = read_one(args.root, settings)
    name = harmony.scale_name(args.name or ["major"])
    return Sound(emit(harmony.scale(root, name), args, settings))


def _spectrum(freqs: list[float], args, settings, together=True, amplitudes=None) -> Sound:
    pitches = [Pitch(hz_to_midi(f, settings.a4), source="hz") for f in freqs]
    return Sound(emit(pitches, args, settings, default="name"), together, amplitudes)


def cmd_harmonics(args, settings):
    fundamental = midi_to_hz(read_one(args.fundamental, settings).midi, settings.a4)
    partials = spectral.harmonics(fundamental, args.count, args.sub, args.stretch)
    amplitudes = [1 / k for k, _ in partials]  # natural 1/k rolloff when played as a chord
    if args.to:
        return _spectrum([f for _, f in partials], args, settings, False, amplitudes)
    rows, pitches = [], []
    for k, freq in partials:
        p = approximate(Pitch(hz_to_midi(freq, settings.a4), source="hz"), settings.approx)
        pitches.append(p)
        rows.append([str(k), number(freq), *[format_pitch(p, f, settings) for f in ("name", "midi", "mc")]])
    print(table(["undertone" if args.sub else "partial", "Hz", "name", "midi", "mc"], rows))
    return Sound(pitches, together=False, amplitudes=amplitudes)  # a scale; --chord for the spectrum


def cmd_ring(args, settings):
    pitches = read_pitches(args.pitches, settings, minimum=2)
    return _spectrum(spectral.ring([midi_to_hz(p.midi, settings.a4) for p in pitches]), args, settings)


def cmd_fm(args, settings):
    carrier = midi_to_hz(read_one(args.carrier, settings).midi, settings.a4)
    modulator = midi_to_hz(read_one(args.modulator, settings).midi, settings.a4)
    return _spectrum(spectral.fm(carrier, modulator, args.index), args, settings)


def cmd_play(args, settings):
    args.play = True
    return Sound([approximate(p, settings.approx) for p in read_pitches(args.pitches, settings)])


def rhythm_events(pitches: list[Pitch], durations: list[Duration], together: bool):
    """Pair pitches with a rhythm: a chord on every duration, or the rhythm cycling under a melody."""
    if all(d.rest for d in durations):
        raise CliError("the rhythm has only rests")
    if together:
        return [(None if d.rest else pitches, d) for d in durations]
    per_cycle = sum(not d.rest for d in durations)
    full_cycles, rem = divmod(len(pitches), per_cycle)
    events, i = [], 0
    for k, d in enumerate(itertools.cycle(durations)):
        if i == len(pitches) and (rem or k >= full_cycles * len(durations)):
            return events
        if d.rest:
            events.append((None, d))
        else:
            events.append(([pitches[i]], d))
            i += 1


def build_score(result: Sound, args) -> Score:
    """The score a command's result makes: what --play plays and --out writes."""
    timing = timing_from(args)
    title = "Serious: " + " ".join(filter(None, [args.command, getattr(args, "rhythm_command", None)]))
    if result.score is not None:
        result.score.title = title
        return result.score
    together = result.together if args.chord is None else args.chord
    durations = result.durations
    if durations is None and getattr(args, "rhythm", None):
        durations = rhythm.parse_rhythm(rhythm.tokenize(args.rhythm), timing)
    if durations:
        events = [Event(chord or [], d.value) for chord, d in rhythm_events(result.pitches, durations, together)]
    else:
        if args.dur:  # seconds, at the tempo
            value = Fraction(str(args.dur)) * timing.tempo / 60 / 4
        else:
            value = Fraction(1) if together else Fraction(1, 4)  # a whole-note chord, or quarter notes
        amplitudes = result.amplitudes if together else None
        if together:
            events = [Event(list(result.pitches), value, amplitudes)]
        else:
            events = [Event([p], value) for p in result.pitches]
    return Score([events], timing.tempo, timing.meter, title)


def sound(result: Sound, args, settings: Settings) -> None:
    """Write a command's result to files (--out: audio, MIDI, LilyPond, MusicXML, text) and/or play it."""
    score = build_score(result, args)
    samples = None

    def audio():
        nonlocal samples
        if samples is None:
            samples = synth.render(synth_notes(score, settings.a4), args.wave, args.volume)
        return samples

    for path in args.out or []:
        if path.lower().endswith(".wav"):
            synth.write_wav(path, audio())
            print(f"wrote {path} ({len(audio()) / synth.SAMPLE_RATE:.1f}s)", file=sys.stderr)
        else:
            export.export(score, path, settings)
            print(f"wrote {path}", file=sys.stderr)
    if args.play:
        sys.stdout.flush()
        synth.play(audio())


def cmd_improvise(args, settings):
    rng = random.Random(args.seed)
    first = round(read_one(args.first, settings).pc) % 12 if args.first else None
    make = generate.all_interval_row if args.all_interval else generate.random_row
    played = []
    for _ in range(args.count):
        row = make(rng, first)
        played += emit([Pitch(60 + pc, False, source="name") for pc in row], args, settings)
    return Sound(played)


def cmd_infinity(args, settings):
    start = read_one(args.start, settings)
    step = parse_interval(args.step)
    pitches = [start.with_midi(start.midi + step.semitones * a) for a in generate.infinity_series(args.length)]
    pitches = [p if p.has_octave else Pitch(p.midi, True, source=p.source) for p in pitches]
    return Sound(emit(pitches, args, settings))


def cmd_keyboard(args, settings):
    try:
        from . import keyboard
    except ImportError:  # no curses, e.g. Windows without the windows-curses package
        raise CliError("the keyboard needs the curses module (on Windows: pip install windows-curses)") from None
    pcs = keyboard.run(settings, args.wave, args.dur or 0.6, args.volume, args.octave, args.mute)
    if pcs is None:
        return None
    return Sound(emit([Pitch(60 + pc, False, source="name") for pc in pcs], args, settings))


# --------------------------------------------------------------------------- rhythm commands

def read_rhythm_tokens(words: list[str]) -> list[str]:
    if words:
        text = " ".join(words)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""
    tokens = rhythm.tokenize(text)
    if not tokens:
        raise CliError("give some durations, as arguments or on stdin (e.g. `q e e h`; see `serious rhythm -h`)")
    return tokens


def read_durations(words: list[str], timing: Timing) -> list[Duration]:
    return rhythm.parse_rhythm(read_rhythm_tokens(words), timing)


def emit_rhythm(durations: list[Duration], args, timing: Timing, default: str | None = None) -> list[Duration]:
    """Print durations (in the input's notation unless --to says otherwise); returns them."""
    fmt = args.to or default or rhythm.output_format(durations, None)
    if fmt == "all":
        rows = [[rhythm.format_duration(d, f, timing) for f in rhythm.RHYTHM_COLUMNS] for d in durations]
        print(table(rhythm.RHYTHM_COLUMNS, rows))
        return durations
    try:
        print(rhythm.format_rhythm(durations, fmt, timing))
    except RhythmError:
        if args.to:
            raise
        print(rhythm.format_rhythm(durations, "fraction", timing))  # e.g. a pattern augmented off its grid
    return durations


def rhythm_result(durations: list[Duration], args, settings: Settings) -> Sound:
    """Rhythms are played on a single pitch (--on)."""
    pitch = read_one(args.on, settings)
    return Sound([pitch] * sum(not d.rest for d in durations), durations=durations)


def cmd_rhythm_show(args, settings):
    timing = timing_from(args)
    tokens = read_rhythm_tokens(args.durations)
    durations = rhythm.parse_rhythm(tokens, timing)
    if args.to:
        return rhythm_result(emit_rhythm(durations, args, timing), args, settings)
    headers = list(rhythm.RHYTHM_COLUMNS)
    rows = [[rhythm.format_duration(d, f, timing) for f in headers] for d in durations]
    if len(tokens) == len(durations):
        headers.insert(0, "input")
        rows = [[tok, *row] for tok, row in zip(tokens, rows)]
    print(table(headers, rows))
    print()
    length = rhythm.total(durations)
    count, unit = timing.meter
    bars, remainder = divmod(length, Fraction(count, unit))
    in_meter = f"{bars} bar{'s' if bars != 1 else ''}" + (f" + {remainder}" if remainder else "")
    lines = [
        ("length", f"{length} = {number(float(length * 4), 4)} beats = "
                   f"{number(timing.seconds(length), 2)} s at ♩ = {number(float(timing.tempo))}"),
        (f"in {count}/{unit}", in_meter),
        ("onsets", " ".join(rhythm.bar_position(t, timing.meter) for t in rhythm.onsets(durations)) + "   (bar:beat)"),
    ]
    try:
        lines.append(("pattern", f"{rhythm.to_pattern(durations, timing.pulse)}   (pulse {timing.pulse})"))
    except RhythmError:
        pass
    lines.append(("palindrome", "yes: non-retrogradable" if rhythm.is_palindrome(durations) else "no"))
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        print(f"{label.ljust(width)}  {value}")
    return rhythm_result(durations, args, settings)


def cmd_rhythm_augment(args, settings):
    timing = timing_from(args)
    durations = rhythm.scale(read_durations(args.durations, timing), _ratio(args.ratio))
    return rhythm_result(emit_rhythm(durations, args, timing), args, settings)


def cmd_rhythm_diminish(args, settings):
    timing = timing_from(args)
    durations = rhythm.scale(read_durations(args.durations, timing), 1 / _ratio(args.ratio))
    return rhythm_result(emit_rhythm(durations, args, timing), args, settings)


def cmd_rhythm_retrograde(args, settings):
    timing = timing_from(args)
    durations = read_durations(args.durations, timing)[::-1]
    return rhythm_result(emit_rhythm(durations, args, timing), args, settings)


def cmd_rhythm_rotate(args, settings):
    timing = timing_from(args)
    durations = rhythm.rotate(read_durations(args.durations, timing), args.by)
    return rhythm_result(emit_rhythm(durations, args, timing), args, settings)


def cmd_rhythm_palindrome(args, settings):
    timing = timing_from(args)
    durations = read_durations(args.durations, timing)
    centre = rhythm.parse_rhythm(rhythm.tokenize(args.centre), timing)[0] if args.centre else None
    return rhythm_result(emit_rhythm(rhythm.palindrome(durations, centre), args, timing), args, settings)


def cmd_rhythm_add_value(args, settings):
    timing = timing_from(args)
    durations = read_durations(args.durations, timing)
    if args.value.lower() == "dot":
        if args.at is None or not 1 <= args.at <= len(durations):
            raise CliError(f"say which duration to dot with --at 1..{len(durations)}")
        result = rhythm.add_dot(durations, args.at - 1)
    else:
        value = rhythm.parse_rhythm(rhythm.tokenize(args.value), timing)[0]
        at = len(durations) if args.at is None else args.at - 1
        if not 0 <= at <= len(durations):
            raise CliError(f"--at must be between 1 and {len(durations) + 1}")
        result = rhythm.add_value(durations, value, at)
    return rhythm_result(emit_rhythm(result, args, timing), args, settings)


def cmd_rhythm_euclid(args, settings):
    timing = timing_from(args)
    durations = rhythm.pattern_durations(rhythm.euclid(args.onsets, args.steps, args.rotate), timing.pulse)
    return rhythm_result(emit_rhythm(durations, args, timing, default="pattern"), args, settings)


def cmd_rhythm_infinity(args, settings):
    timing = timing_from(args)
    durations = rhythm.infinity_rhythm(args.length, timing.pulse)
    return rhythm_result(emit_rhythm(durations, args, timing, default="pattern"), args, settings)


# --------------------------------------------------------------------------- isorhythm

def parse_color(text: str, settings: Settings) -> list[list[Pitch]]:
    """Pitches and <chords> of a color: "C4 <E4 G4> D4"."""
    lily = settings.force == "lily"
    chords = []
    for m in re.finditer(r"<([^<>]*)>|([^<>]+)", text):
        if m.group(1) is not None:
            chord = [approximate(p, settings.approx) for p in parse_pitches(tokenize(m.group(1), lily=lily), settings)]
            if chord:
                chords.append(chord)
        else:
            chords.extend([approximate(p, settings.approx)] for p in parse_pitches(tokenize(m.group(2), lily=lily), settings))
    return chords


def cmd_isorhythm(args, settings):
    timing = timing_from(args)
    colors, taleas = args.color or [], args.talea or []
    if not colors or len(colors) != len(taleas):
        raise CliError("give each voice a --color (pitches) and a --talea (rhythm), in pairs")
    tempo = number(float(timing.tempo))
    voices, cycle_lengths = [], []
    for n, (color_text, talea_text) in enumerate(zip(colors, taleas), start=1):
        color = parse_color(color_text, settings)
        talea = rhythm.parse_rhythm(rhythm.tokenize(talea_text), timing)
        if not color or not talea:
            raise CliError(f"voice {n}: the color and the talea both need something in them")
        per_talea = sum(not d.rest for d in talea)
        if not per_talea:
            raise CliError(f"voice {n}: the talea has only rests")
        realign = math.lcm(len(color), per_talea)
        statements = realign // per_talea
        cycle = rhythm.total(talea) * statements
        cycle_lengths.append(cycle)

        events, i = [], 0
        for d in (itertools.cycle(talea) if args.notes else talea * statements):
            if args.notes and i >= args.notes:
                break
            if d.rest:
                events.append((None, d))
            else:
                events.append((color[i % len(color)], d))
                i += 1
        voices.append(events)

        kind = "chords" if any(len(c) > 1 for c in color) else "pitches"
        print(f"voice {n}: {len(color)} {kind} × {len(talea)} durations → they realign after {realign} notes "
              f"({realign // len(color)} × color, {statements} × talea), "
              f"{number(float(cycle * 4), 4)} beats = {_clock(timing.seconds(cycle))} at ♩ = {tempo}")
        pitch_fmt = output_format([p for c in color for p in c], args.to if args.to != "all" else None)
        rhythm_fmt = rhythm.output_format(talea, None)

        def show(chord):
            if chord is None:
                return "r"
            names = [format_pitch(p, pitch_fmt, settings) for p in chord]
            return names[0] if len(names) == 1 else "<" + " ".join(names) + ">"

        try:
            line = [f"{show(c)}:{rhythm.format_duration(replace(d, rest=False), rhythm_fmt, timing)}" for c, d in events]
        except RhythmError:  # e.g. pattern notation: write durations as fractions
            line = [f"{show(c)}:{rhythm.format_duration(replace(d, rest=False), 'fraction')}" for c, d in events]
        print(" ".join(line))
        print()
    # when played (or written out), shorter voices keep cycling until the longest one has finished
    horizon = max(rhythm.total([d for _, d in events]) for events in voices)
    parts = []
    for events in voices:
        played, t = [], Fraction(0)
        for chord, d in itertools.cycle(events):
            if t + d.value > horizon:
                break
            played.append(Event(chord or [], d.value))
            t += d.value
        parts.append(played)
    if len(cycle_lengths) > 1:
        whole = cycle_lengths[0]
        for length in cycle_lengths[1:]:
            whole = rhythm.lcm_time(whole, length)
        print(f"all voices realign after {number(float(whole * 4), 4)} beats = "
              f"{_clock(timing.seconds(whole))} at ♩ = {tempo}")
    return Sound([], score=Score(parts, timing.tempo, timing.meter))


def cmd_formats(args, settings):
    print(FORMATS_TEXT, end="")


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("notation options")
    g.add_argument("-t", "--to", type=_format_type, metavar="FORMAT",
                   help="output notation: name, midi, mc, pc, hz, lily, solfege or all (default: same as input)")
    g.add_argument("--from", dest="force", type=_input_format_type, metavar="FORMAT",
                   help="read every pitch as this notation: name, midi, mc, pc, hz or lily")
    spelling = g.add_mutually_exclusive_group()
    spelling.add_argument("--sharps", action="store_true", help="spell black keys with sharps")
    spelling.add_argument("--flats", action="store_true", help="spell black keys with flats")
    g.add_argument("--approx", type=int, choices=[1, 2, 4, 8, 16], metavar="N",
                   help="round output to 1/N tone, as in OpenMusic: 2 = semitones, 4 = quarter tones, 8 = eighth tones")
    g.add_argument("--a4", type=_float, default=440.0, metavar="HZ", help="tuning reference (default 440)")
    g.add_argument("--middle-c", type=_int, default=4, metavar="OCTAVE",
                   help="octave number of middle C (default 4; Yamaha uses 3)")
    synth_options = argparse.ArgumentParser(add_help=False)
    s = synth_options.add_argument_group("synth options")
    s.add_argument("--play", action="store_true", help="play the result through the speakers")
    s.add_argument("--out", action="append", metavar="FILE",
                   help="write the result to a file, by extension: .wav (audio), .mid, .ly, .musicxml, .mxl, .txt "
                        "(repeat for several)")
    s.add_argument("--wave", choices=list(synth.WAVES), default="triangle", help="waveform (default triangle)")
    s.add_argument("--dur", type=_dur_type, metavar="SECONDS",
                   help="length of each note, in seconds (default: a quarter note, or a whole note for chords)")
    s.add_argument("--volume", type=_float, default=0.5, help="peak level from 0 to 1 (default 0.5)")
    together = s.add_mutually_exclusive_group()
    together.add_argument("--chord", dest="chord", action="store_const", const=True,
                          help="sound the pitches together")
    together.add_argument("--melody", dest="chord", action="store_const", const=False,
                          help="sound the pitches one after another")
    s.add_argument("--tempo", type=_tempo_type, default=120.0, metavar="BPM",
                   help="quarter notes per minute, for rhythms (default 120)")
    s.add_argument("--meter", type=_meter_type, default=(4, 4),
                   help="meter for bar positions and exported scores (default 4/4)")
    pitch_rhythm = argparse.ArgumentParser(add_help=False)
    pitch_rhythm.add_argument_group("rhythm options").add_argument(
        "--rhythm", metavar="DURATIONS", help='play with this rhythm, repeating as needed, e.g. "q e e h"')

    rhythm_options = argparse.ArgumentParser(add_help=False)
    r = rhythm_options.add_argument_group("rhythm options")
    r.add_argument("-t", "--to", type=_rhythm_format_type, metavar="FORMAT",
                   help="output notation: name, fraction, lily, beats, ms, pattern or all (default: same as input)")
    r.add_argument("--from", dest="rhythm_force", choices=["lily", "beats"],
                   help="read bare numbers as LilyPond durations (4 = quarter) or as beats (the default)")
    r.add_argument("--pulse", type=_pulse_type, default=Fraction(1, 16), metavar="DURATION",
                   help="grid for x..x patterns and 3:2:1 proportions (default 1/16)")
    r.add_argument("--on", default="C5", metavar="PITCH", help="pitch to play rhythms on (default C5)")

    parser = argparse.ArgumentParser(
        prog="serious", description=DESCRIPTION, epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"serious {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    def add(name, func, help, aliases=(), pitches=True):
        p = sub.add_parser(name, aliases=list(aliases), parents=[common, synth_options, pitch_rhythm],
                           help=help, description=help)
        p.set_defaults(func=func)
        if pitches:
            p.add_argument("pitches", nargs="*", help="pitches in any notation (or on stdin)")
        return p

    add("convert", cmd_convert, "show pitches in every notation (or one, with --to)", ["c"])
    p = add("transpose", cmd_transpose, "transpose by an interval: 7, -3, 700c, 3/2, P5, -m3...", ["t"], pitches=False)
    p.add_argument("interval")
    p.add_argument("pitches", nargs="*", help="pitches in any notation (or on stdin)")
    p = add("invert", cmd_invert, "mirror pitches around an axis (default: the first pitch)", ["i"])
    p.add_argument("--axis", help="pitch to invert around, e.g. E4 or midi:61.5")
    add("retrograde", cmd_retrograde, "reverse the order", ["r", "reverse"])
    p = add("rotate", cmd_rotate, "rotate the order", ["rot"])
    p.add_argument("-n", "--by", type=_int, default=1, help="positions to rotate left (default 1)")
    p = add("intervals", cmd_intervals, "the intervals between successive pitches", ["int"])
    p.add_argument("--semitones", action="store_true", help="print only the semitone sizes, on one line")
    add("set", cmd_set, "pitch-class set analysis: normal & prime form, Forte name, interval vector", ["pcset", "analyse", "analyze"])
    p = add("matrix", cmd_matrix, "twelve-tone matrix (P, I, R, RI) of a row", ["row"])
    p.add_argument("--zero", action="store_true", help="label the given row P0 instead of by its first pitch class")
    p = add("chord", cmd_chord, "name a chord (C E G) or spell a chord symbol (Cmaj7, F#m7b5, Bb7/D)")
    p.add_argument("--octave", type=_int, default=4, help="octave of the root when spelling a symbol (default 4)")
    p = add("scale", cmd_scale, "a scale or mode from a root, e.g. D dorian", pitches=False)
    p.add_argument("root", nargs="?", help="root pitch, e.g. D, Eb3, 62")
    p.add_argument("name", nargs="*", help="scale name (default major)")
    p.add_argument("--list", action="store_true", help="list the known scales")
    p = add("harmonics", cmd_harmonics, "harmonic (or undertone) series of a fundamental", ["partials"], pitches=False)
    p.add_argument("fundamental")
    p.add_argument("count", nargs="?", type=_int, default=16, help="number of partials (default 16)")
    p.add_argument("--sub", action="store_true", help="undertone series instead of overtones")
    p.add_argument("--stretch", type=_float, default=1.0, help="partial k = f * k^STRETCH (default 1 = harmonic)")
    add("ring", cmd_ring, "ring modulation: sum and difference tones of every pair of pitches")
    p = add("fm", cmd_fm, "frequency modulation spectrum: carrier ± k * modulator", pitches=False)
    p.add_argument("carrier")
    p.add_argument("modulator")
    p.add_argument("-i", "--index", type=_int, default=3, help="number of sideband pairs (default 3)")
    add("play", cmd_play, "play pitches through the speakers (--chord to sound them together)", ["p"])
    p = add("improvise", cmd_improvise, "make a random twelve-tone row", ["random"], pitches=False)
    p.add_argument("--all-interval", action="store_true", help="a row whose 11 intervals are all different")
    p.add_argument("--first", metavar="PITCH", help="start the row on this pitch class")
    p.add_argument("--count", type=_int, default=1, help="how many rows (default 1), one per line")
    p.add_argument("--seed", type=_int, help="random seed, to get the same rows again")
    p = add("infinity", cmd_infinity, "Per Nørgård's infinity series", ["norgard", "nørgård"], pitches=False)
    p.add_argument("length", nargs="?", type=_int, default=32, help="number of notes (default 32)")
    p.add_argument("--start", default="C4", metavar="PITCH", help="first note (default C4)")
    p.add_argument("--step", default="1", metavar="INTERVAL",
                   help="interval for each step of the series: 1 (default), 2, m3, 50c...")
    p = add("keyboard", cmd_keyboard, "click a row on an interactive one-octave keyboard (prints it when done)",
            ["kb", "piano"], pitches=False)
    p.add_argument("--octave", type=_int, default=4, help="octave the keys sound in (default 4)")
    p.add_argument("--mute", action="store_true", help="no sound")
    p = add("isorhythm", cmd_isorhythm, "run pitch series (colors) against rhythm series (taleas), in voices",
            ["iso"], pitches=False)
    p.add_argument("--color", action="append", metavar="PITCHES",
                   help='a voice\'s pitch series; <...> makes a chord: "C4 <E4 G4> D4" (repeat for more voices)')
    p.add_argument("--talea", action="append", metavar="DURATIONS",
                   help='the voice\'s rhythm series, e.g. "q e. s q" (one per --color)')
    p.add_argument("--notes", type=_int, metavar="N", help="stop each voice after N notes (default: when it realigns)")
    p.add_argument("--pulse", type=_pulse_type, default=Fraction(1, 16), metavar="DURATION",
                   help="grid for x..x patterns in taleas (default 1/16)")
    add("formats", cmd_formats, "list every notation Serious understands", ["notations"], pitches=False)

    rp = sub.add_parser("rhythm", aliases=["rh"], help="rhythms: show, augment, euclid, palindrome... (serious rhythm -h)",
                        description=RHYTHM_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    rsub = rp.add_subparsers(dest="rhythm_command", metavar="operation")

    def radd(name, func, help, aliases=(), durations=True):
        p = rsub.add_parser(name, aliases=list(aliases), parents=[rhythm_options, synth_options],
                            help=help, description=help)
        p.set_defaults(func=func)
        if durations:
            p.add_argument("durations", nargs="*", help="durations in any notation (or on stdin)")
        return p

    radd("show", cmd_rhythm_show, "show durations in every notation, with length, bar positions and symmetry")
    p = radd("augment", cmd_rhythm_augment, "multiply every duration by a ratio: 2, 3/2, 1.5", ["aug"], durations=False)
    p.add_argument("ratio")
    p.add_argument("durations", nargs="*", help="durations in any notation (or on stdin)")
    p = radd("diminish", cmd_rhythm_diminish, "divide every duration by a ratio: 2, 3/2, 1.5", ["dim"], durations=False)
    p.add_argument("ratio")
    p.add_argument("durations", nargs="*", help="durations in any notation (or on stdin)")
    radd("retrograde", cmd_rhythm_retrograde, "reverse the order", ["r", "reverse"])
    p = radd("rotate", cmd_rhythm_rotate, "rotate the order", ["rot"])
    p.add_argument("-n", "--by", type=_int, default=1, help="positions to rotate left (default 1)")
    p = radd("palindrome", cmd_rhythm_palindrome,
             "Messiaen's non-retrogradable rhythm: the durations, a centre, then the durations backwards",
             ["nonretro", "non-retrogradable"])
    p.add_argument("--centre", "--center", metavar="DURATION",
                   help="central value (default: the last duration becomes the centre)")
    p = radd("add-value", cmd_rhythm_add_value,
             "Messiaen's added value: insert a short note or rest (rs), or add a dot to one duration",
             ["added-value"], durations=False)
    p.add_argument("value", help='the added value, e.g. s, rs (a rest), 1/16, or "dot"')
    p.add_argument("durations", nargs="*", help="durations in any notation (or on stdin)")
    p.add_argument("--at", type=_int, metavar="N",
                   help="insert before duration N (default: at the end); with dot, the duration to dot")
    p = radd("euclid", cmd_rhythm_euclid, "Euclidean rhythm: K onsets spread evenly over N pulses", durations=False)
    p.add_argument("onsets", type=_int)
    p.add_argument("steps", type=_int)
    p.add_argument("--rotate", type=_int, default=0, help="start the pattern N pulses later")
    p = radd("infinity", cmd_rhythm_infinity, "durations of |a(n)|+1 pulses from Nørgård's infinity series",
             ["norgard"], durations=False)
    p.add_argument("length", nargs="?", type=_int, default=16, help="number of durations (default 16)")

    parser.commands = sub.choices  # command names and aliases -> subparsers
    parser.rhythm_commands = rsub.choices
    return parser


def _protect_negatives(argv: list[str], options: set[str]) -> list[str]:
    """Let "-m3", "-700c" or "-1.5" through argparse as values instead of options."""
    out = []
    for arg in argv:
        if len(arg) > 1 and arg[0] == "-" and arg[1] != "-" and arg not in options:
            try:
                parse_interval(arg)
                arg = "−" + arg[1:]
            except IntervalError:
                pass
        out.append(arg)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    commands = set(parser.commands)
    implicit = argv[0] not in commands and argv[0] not in ("-h", "--help", "-V", "--version")
    if implicit:
        argv.insert(0, "convert")
    if argv[0] in ("rhythm", "rh"):
        if len(argv) == 1:
            parser.commands[argv[0]].print_help()
            return 0
        if argv[1] not in parser.rhythm_commands and argv[1] not in ("-h", "--help"):
            argv.insert(1, "show")  # `serious rhythm q e e` shows the rhythm
    options = {opt for action in parser._actions for opt in action.option_strings}
    for subparser in [*parser.commands.values(), *parser.rhythm_commands.values()]:
        options |= {opt for action in subparser._actions for opt in action.option_strings}
    args = parser.parse_args(_protect_negatives(argv, options))
    try:
        settings = settings_from(args)
        result = args.func(args, settings)
        if result and (args.play or args.out):
            sound(result, args, settings)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return 0
    except (PitchError, IntervalError, pcset.SetError, CliError, RhythmError, export.ExportError, RuntimeError,
            ValueError, OSError) as error:
        message = str(error)
        if implicit and isinstance(error, PitchError):
            close = difflib.get_close_matches(argv[1], commands, n=1)
            if close:
                message += f"\n(did you mean `serious {close[0]}`?)"
        print(f"serious: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
