"""Export a Score as MIDI, LilyPond, MusicXML (.musicxml / compressed .mxl) or plain text."""

from __future__ import annotations

import math
import os
import struct
import zipfile
from dataclasses import dataclass
from fractions import Fraction
from xml.sax.saxutils import escape

from . import __version__
from .pitch import Pitch, Settings, format_pitch, midi_to_hz, number, spell
from .rhythm import Duration, format_duration
from .score import Event, Score

FORMATS = {
    ".mid": "midi", ".midi": "midi", ".ly": "lilypond", ".musicxml": "musicxml", ".xml": "musicxml",
    ".mxl": "mxl", ".txt": "text",
}


class ExportError(ValueError):
    pass


def export(score: Score, path: str, settings: Settings) -> None:
    kind = FORMATS.get(os.path.splitext(path)[1].lower())
    if kind is None:
        raise ExportError(f"don't know how to write {path!r}; use .wav, .mid, .ly, .musicxml, .mxl or .txt")
    if kind == "midi":
        data = to_midi(score)
        with open(path, "wb") as f:
            f.write(data)
    elif kind == "mxl":
        to_mxl(score, path, settings)
    else:
        text = {"lilypond": to_lilypond, "musicxml": to_musicxml, "text": to_text}[kind](score, settings)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def _with_octave(p: Pitch) -> Pitch:
    """Pitch classes and octave-less names are written where they sound (around middle C)."""
    return p if p.has_octave else Pitch(p.midi, True, p.letter, p.alter, p.source)


# --------------------------------------------------------------------------- notation: bars, ties, tuplets

@dataclass
class Segment:
    pitches: list[Pitch]       # empty for a rest
    value: Fraction            # sounding length, in whole notes
    written: Fraction          # written note value (a power of two)
    dots: int
    tuplet: tuple[int, int] | None  # (actual, normal), e.g. (3, 2) for triplets
    tie_start: bool = False    # tied into the next segment
    tie_stop: bool = False     # tied from the previous segment


def _note_values(length: Fraction) -> tuple[tuple[int, int] | None, list[tuple[Fraction, int]]]:
    """Written values (with dots) adding up to ``length``, inside a tuplet if it isn't a binary length."""
    odd = length.denominator
    while odd % 2 == 0:
        odd //= 2
    tuplet = None
    written = length
    if odd > 1:
        normal = 2 ** int(math.log2(odd))
        tuplet = (odd, normal)
        written = length * odd / normal
    parts = []
    while written > 0:
        base = Fraction(2)
        while base > written:
            base /= 2
        if base < Fraction(1, 256):
            raise ExportError(f"a duration of {length} is too short to write down")
        dots = 2 if base * Fraction(7, 4) <= written else 1 if base * Fraction(3, 2) <= written else 0
        parts.append((base, dots))
        written -= base * (2 - Fraction(1, 2 ** dots))
    return tuplet, parts


def measures(voice: list[Event], meter: tuple[int, int], length: Fraction) -> list[list[Segment]]:
    """Split a voice into bars: notes crossing barlines (or of unwritable lengths) become tied notes.

    The voice is padded with rests to ``length``, rounded up to whole bars.
    """
    bar = Fraction(*meter)
    bars = max(1, math.ceil(length / bar))
    events = []
    for event in voice:  # merge consecutive rests
        if event.rest and events and events[-1].rest:
            events[-1] = Event([], events[-1].duration + event.duration)
        else:
            events.append(event)
    filled = sum((e.duration for e in events), Fraction(0))
    if filled < bars * bar:
        events.append(Event([], bars * bar - filled))
    result: list[list[Segment]] = [[] for _ in range(bars)]
    t = Fraction(0)
    for event in events:
        remaining, first = event.duration, True
        while remaining > 0:
            index = int(t // bar)
            piece = min(remaining, (index + 1) * bar - t)
            tuplet, parts = _note_values(piece)
            for i, (written, dots) in enumerate(parts):
                value = written * (2 - Fraction(1, 2 ** dots))
                if tuplet:
                    value = value * tuplet[1] / tuplet[0]
                last = remaining == piece and i == len(parts) - 1
                tied = not event.rest
                result[index].append(Segment(event.pitches, value, written, dots, tuplet,
                                             tie_start=tied and not last, tie_stop=tied and not first))
                first = False
            t += piece
            remaining -= piece
    return result


def _clef(voice: list[Event]) -> str:
    midis = [p.midi for e in voice for p in e.pitches]
    return "bass" if midis and sum(midis) / len(midis) < 57 else "treble"


def staves(voice: list[Event]) -> list[tuple[str, list[Event]]]:
    """(clef, events) for each staff: one staff, or a grand staff split at middle C for wide voices."""
    midis = [p.midi for e in voice for p in e.pitches]
    if midis and min(midis) < 53 and max(midis) > 67:
        upper = [Event([p for p in e.pitches if p.midi >= 60], e.duration) for e in voice]
        lower = [Event([p for p in e.pitches if p.midi < 60], e.duration) for e in voice]
        return [("treble", upper), ("bass", lower)]
    return [(_clef(voice), voice)]


# --------------------------------------------------------------------------- LilyPond

def _lily_duration(written: Fraction, dots: int) -> str:
    return ("\\breve" if written == 2 else str(int(1 / written))) + "." * dots


def _lily_staff(events: list[Event], clef: str, score: Score, settings: Settings, header: str) -> str:
    count, unit = score.meter
    bars = []
    for segments in measures(events, score.meter, score.length):
            tokens, open_tuplet = [], None
            for seg in segments:
                if seg.tuplet != open_tuplet:
                    if open_tuplet:
                        tokens.append("}")
                    if seg.tuplet:
                        tokens.append(f"\\tuplet {seg.tuplet[0]}/{seg.tuplet[1]} {{")
                    open_tuplet = seg.tuplet
                duration = _lily_duration(seg.written, seg.dots)
                if seg.pitches:
                    names = [format_pitch(_with_octave(p), "lily", settings) for p in seg.pitches]
                    note = names[0] if len(names) == 1 else "<" + " ".join(names) + ">"
                    tokens.append(note + duration + ("~" if seg.tie_start else ""))
                else:
                    tokens.append("r" + duration)
            if open_tuplet:
                tokens.append("}")
            bars.append(" ".join(tokens))
    body = " |\n      ".join(bars)
    return (f"    \\new Staff {header}{{\n      \\clef {clef} \\time {count}/{unit}\n"
            f"      {body} \\bar \"|.\"\n    }}")


def to_lilypond(score: Score, settings: Settings) -> str:
    parts = []
    for n, voice in enumerate(score.voices, start=1):
        name = f'\\with {{ instrumentName = "{n}" }} ' if len(score.voices) > 1 else ""
        layout = staves(voice)
        if len(layout) == 1:
            parts.append(_lily_staff(layout[0][1], layout[0][0], score, settings, name))
        else:
            inner = "\n".join(_lily_staff(events, clef, score, settings, "") for clef, events in layout)
            parts.append(f"    \\new PianoStaff {name}<<\n{inner}\n    >>")
    # the tempo mark goes on the first staff
    parts[0] = parts[0].replace("\\time", f"\\tempo 4 = {round(float(score.tempo))} \\time", 1)
    staves_text = "\n".join(parts)
    title = score.title.replace('"', "'")
    return (f'\\version "2.24.0"\n'
            f"% written by Serious {__version__}\n"
            f'\\header {{ title = "{title}" tagline = ##f }}\n\n'
            f"\\score {{\n  <<\n" + staves_text + "\n  >>\n  \\layout { }\n}\n")


# --------------------------------------------------------------------------- MusicXML

_TYPES = {Fraction(2): "breve", Fraction(1): "whole", Fraction(1, 2): "half", Fraction(1, 4): "quarter",
          Fraction(1, 8): "eighth", Fraction(1, 16): "16th", Fraction(1, 32): "32nd", Fraction(1, 64): "64th",
          Fraction(1, 128): "128th", Fraction(1, 256): "256th"}
_ACCIDENTALS = {-2: "flat-flat", -1.5: "three-quarters-flat", -1: "flat", -0.5: "quarter-flat", 0: "natural",
                0.5: "quarter-sharp", 1: "sharp", 1.5: "three-quarters-sharp", 2: "double-sharp"}


def to_musicxml(score: Score, settings: Settings) -> str:
    # per voice (part): a list of staves, each (clef, bars)
    layouts = [[(clef, measures(events, score.meter, score.length)) for clef, events in staves(voice)]
               for voice in score.voices]
    divisions = 1
    for layout in layouts:
        for _, bars in layout:
            for segments in bars:
                for seg in segments:
                    divisions = math.lcm(divisions, (seg.value * 4).denominator)
    bar_divisions = int(Fraction(*score.meter) * 4 * divisions)
    count, unit = score.meter
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="4.0">',
        f"  <work><work-title>{escape(score.title)}</work-title></work>",
        f"  <identification><encoding><software>Serious {__version__}</software></encoding></identification>",
        "  <part-list>",
    ]
    for n in range(1, len(score.voices) + 1):
        out.append(f'    <score-part id="P{n}"><part-name>Voice {n}</part-name></score-part>')
    out.append("  </part-list>")
    for n, layout in enumerate(layouts, start=1):
        out.append(f'  <part id="P{n}">')
        grand = len(layout) > 1
        for index in range(len(layout[0][1])):
            out.append(f'    <measure number="{index + 1}">')
            if index == 0:
                clefs = "".join(_xml_clef(clef, k if grand else None) for k, (clef, _) in enumerate(layout, start=1))
                out.append(f"      <attributes><divisions>{divisions}</divisions><key><fifths>0</fifths></key>"
                           f"<time><beats>{count}</beats><beat-type>{unit}</beat-type></time>"
                           + (f"<staves>{len(layout)}</staves>" if grand else "") + f"{clefs}</attributes>")
                if n == 1:
                    tempo = number(float(score.tempo))
                    out.append('      <direction placement="above"><direction-type><metronome>'
                               f"<beat-unit>quarter</beat-unit><per-minute>{tempo}</per-minute></metronome>"
                               f'</direction-type><sound tempo="{tempo}"/></direction>')
            for k, (_, bars) in enumerate(layout, start=1):
                if k > 1:  # back to the start of the bar for the next staff
                    out.append(f"      <backup><duration>{bar_divisions}</duration></backup>")
                segments = bars[index]
                shown: dict[tuple[str, int], float] = {}  # accidentals in force in this bar
                for i, seg in enumerate(segments):
                    tuplet_starts = seg.tuplet and (i == 0 or segments[i - 1].tuplet != seg.tuplet)
                    tuplet_stops = seg.tuplet and (i == len(segments) - 1 or segments[i + 1].tuplet != seg.tuplet)
                    for c, pitch in enumerate(seg.pitches or [None]):
                        out.append("      " + _xml_note(seg, pitch, c > 0, divisions, settings, shown,
                                                        tuplet_starts, tuplet_stops, k if grand else None))
            out.append("    </measure>")
        out[-1] = out[-1].replace("</measure>", '<barline location="right"><bar-style>light-heavy</bar-style>'
                                                "</barline></measure>")
        out.append("  </part>")
    out.append("</score-partwise>")
    return "\n".join(out) + "\n"


def _xml_clef(clef: str, staff: int | None) -> str:
    sign, line = ("F", 4) if clef == "bass" else ("G", 2)
    number_attribute = f' number="{staff}"' if staff else ""
    return f"<clef{number_attribute}><sign>{sign}</sign><line>{line}</line></clef>"


def _xml_note(seg: Segment, pitch: Pitch | None, chord: bool, divisions: int, settings: Settings,
              shown: dict, tuplet_starts: bool, tuplet_stops: bool, staff: int | None = None) -> str:
    parts = ["<note>"]
    if chord:
        parts.append("<chord/>")
    accidental = None
    if pitch is None:
        parts.append("<rest/>")
    else:
        letter, alter, octave, deviation = spell(_with_octave(pitch), settings)
        total_alter = round(alter + deviation, 2)
        written_alter = alter + round(deviation * 2) / 2  # the nearest quarter tone, for the accidental
        parts.append(f"<pitch><step>{letter}</step>"
                     + (f"<alter>{number(total_alter)}</alter>" if total_alter else "")
                     + f"<octave>{octave}</octave></pitch>")
        if not seg.tie_stop:
            if shown.get((letter, octave), 0) != written_alter:
                accidental = _ACCIDENTALS.get(written_alter)
            shown[(letter, octave)] = written_alter
    parts.append(f"<duration>{int(seg.value * 4 * divisions)}</duration>")
    if seg.tie_stop:
        parts.append('<tie type="stop"/>')
    if seg.tie_start:
        parts.append('<tie type="start"/>')
    parts.append(f"<voice>{staff or 1}</voice>")
    parts.append(f"<type>{_TYPES[seg.written]}</type>" + "<dot/>" * seg.dots)
    if accidental:
        parts.append(f"<accidental>{accidental}</accidental>")
    if seg.tuplet:
        parts.append(f"<time-modification><actual-notes>{seg.tuplet[0]}</actual-notes>"
                     f"<normal-notes>{seg.tuplet[1]}</normal-notes></time-modification>")
    if staff:
        parts.append(f"<staff>{staff}</staff>")
    notations = []
    if seg.tie_stop:
        notations.append('<tied type="stop"/>')
    if seg.tie_start:
        notations.append('<tied type="start"/>')
    if tuplet_starts and not chord:
        notations.append('<tuplet type="start" bracket="yes"/>')
    if tuplet_stops and not chord:
        notations.append('<tuplet type="stop"/>')
    if notations:
        parts.append("<notations>" + "".join(notations) + "</notations>")
    parts.append("</note>")
    return "".join(parts)


def to_mxl(score: Score, path: str, settings: Settings) -> None:
    """Compressed MusicXML: a zip with a container file pointing at the score."""
    xml = to_musicxml(score, settings)
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                 '<rootfile full-path="score.musicxml" media-type="application/vnd.recordare.musicxml+xml"/>'
                 "</rootfiles></container>\n")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.recordare.musicxml")  # stored, first
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("score.musicxml", xml, compress_type=zipfile.ZIP_DEFLATED)


# --------------------------------------------------------------------------- MIDI

TICKS_PER_QUARTER = 480
_BEND_RANGE = 2  # semitones, the General MIDI default (set explicitly too)


def _vlq(n: int) -> bytes:
    n = max(0, n)
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _track(events: list[tuple[int, int, bytes]]) -> bytes:
    """A track chunk from (tick, order, message) triples; order sorts messages at the same tick."""
    data, last = bytearray(), 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        data += _vlq(tick - last) + message
        last = tick
    data += b"\x00\xff\x2f\x00"  # end of track
    return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)


def _ticks(value: Fraction) -> int:
    return round(value * 4 * TICKS_PER_QUARTER)


def to_midi(score: Score) -> bytes:
    """A type-1 MIDI file: a tempo track, then one track per voice.

    Microtonal pitches get their own channel with a pitch bend, so every note
    sounds at its exact pitch, even in chords.
    """
    count, unit = score.meter
    tempo = round(60_000_000 / float(score.tempo))
    if not 1 <= tempo <= 0xFFFFFF:
        raise ExportError(f"MIDI can't hold a tempo of {number(float(score.tempo))} BPM")
    conductor = [
        (0, 0, b"\xff\x03" + _vlq(len(score.title.encode())) + score.title.encode()),
        (0, 0, b"\xff\x51\x03" + tempo.to_bytes(3, "big")),
        (0, 0, bytes([0xFF, 0x58, 0x04, count, int(math.log2(unit)), 24, 8])),
    ]
    notes = []  # (onset, offset, midi key, bend, velocity, voice index)
    for v, voice in enumerate(score.voices):
        for onset, event in score.timed(voice):
            amplitudes = event.amplitudes or [1.0] * len(event.pitches)
            loudest = max(amplitudes, default=1.0) or 1.0
            for pitch, amplitude in zip(event.pitches, amplitudes):
                key = round(pitch.midi)
                if not 0 <= key <= 127:
                    raise ExportError(f"MIDI can't hold a pitch that far out (MIDI {number(pitch.midi)})")
                bend = pitch.midi - key
                velocity = max(1, min(127, round(30 + 70 * amplitude / loudest)))
                start = _ticks(onset)
                notes.append((start, max(start + 1, _ticks(onset + event.duration)), key, bend, velocity, v))
    microtonal = any(abs(n[3]) > 1e-6 for n in notes)
    channels = [c for c in range(16) if c != 9]  # channel 10 is drums
    tracks = [[] for _ in score.voices]
    if microtonal:
        for c in channels:  # pitch-bend range RPN, then reset the RPN
            for cc, value in ((101, 0), (100, 0), (6, _BEND_RANGE), (38, 0), (101, 127), (100, 127)):
                conductor.append((0, 1, bytes([0xB0 | c, cc, value])))
        free_at = {c: 0 for c in channels}
        bend_of = {c: 0.0 for c in channels}
        for onset, offset, key, bend, velocity, v in sorted(notes):
            idle = [c for c in channels if free_at[c] <= onset]
            pool = idle or sorted(channels, key=lambda c: free_at[c])[:1]
            channel = next((c for c in pool if abs(bend_of[c] - bend) < 1e-6), pool[0])
            if abs(bend_of[channel] - bend) > 1e-6 or not idle:
                value = max(0, min(16383, 8192 + round(bend / _BEND_RANGE * 8192)))
                tracks[v].append((onset, 1, bytes([0xE0 | channel, value & 0x7F, value >> 7])))
                bend_of[channel] = bend
            free_at[channel] = offset
            tracks[v].append((onset, 2, bytes([0x90 | channel, key, velocity])))
            tracks[v].append((offset, 0, bytes([0x80 | channel, key, 0])))
    else:
        for onset, offset, key, bend, velocity, v in notes:
            channel = channels[v % len(channels)]
            tracks[v].append((onset, 2, bytes([0x90 | channel, key, velocity])))
            tracks[v].append((offset, 0, bytes([0x80 | channel, key, 0])))
    chunks = [_track(conductor)]
    for v, events in enumerate(tracks, start=1):
        name = f"Voice {v}".encode()
        chunks.append(_track([(0, -1, b"\xff\x03" + _vlq(len(name)) + name), *events]))
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), TICKS_PER_QUARTER)
    return header + b"".join(chunks)


# --------------------------------------------------------------------------- text

def to_text(score: Score, settings: Settings) -> str:
    """One event per line, tab-separated: easy to read, and easy to load into a spreadsheet."""
    count, unit = score.meter
    lines = [f"# {score.title} · written by Serious {__version__} · ♩ = {number(float(score.tempo))} · {count}/{unit}",
             "\t".join(["voice", "bar:beat", "onset", "duration", "pitch", "midi", "hz"])]
    bar = Fraction(count, unit)
    for v, voice in enumerate(score.voices, start=1):
        for onset, event in score.timed(voice):
            bar_index = int(onset // bar)
            beat = (onset - bar_index * bar) * unit + 1
            position = f"{bar_index + 1}:{number(float(beat), 3)}"
            pitches = [_with_octave(p) for p in event.pitches]
            row = [str(v), position, number(float(onset * 4), 4), format_duration(Duration(event.duration), "name"),
                   " ".join(format_pitch(p, "name", settings) for p in pitches) or "rest",
                   " ".join(number(p.midi) for p in pitches),
                   " ".join(number(midi_to_hz(p.midi, settings.a4)) for p in pitches)]
            lines.append("\t".join(row))
    return "\n".join(lines) + "\n"
