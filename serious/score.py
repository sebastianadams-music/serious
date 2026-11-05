"""A tiny score model: voices of timed events, shared by the synth and the exporters."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from . import synth
from .pitch import Pitch, midi_to_hz


@dataclass
class Event:
    pitches: list[Pitch]                   # empty for a rest
    duration: Fraction                     # in whole notes (1/4 = a quarter note)
    amplitudes: list[float] | None = None  # relative levels, one per pitch

    @property
    def rest(self) -> bool:
        return not self.pitches


@dataclass
class Score:
    voices: list[list[Event]]
    tempo: Fraction = Fraction(120)        # quarter notes per minute
    meter: tuple[int, int] = (4, 4)
    title: str = "Serious"

    @property
    def length(self) -> Fraction:
        return max((sum((e.duration for e in voice), Fraction(0)) for voice in self.voices), default=Fraction(0))

    def seconds(self, value: Fraction) -> float:
        return float(value * 4 * 60 / self.tempo)

    def timed(self, voice: list[Event]) -> list[tuple[Fraction, Event]]:
        """(onset in whole notes, event) pairs."""
        out, t = [], Fraction(0)
        for event in voice:
            out.append((t, event))
            t += event.duration
        return out


def synth_notes(score: Score, a4: float) -> list[synth.Note]:
    """Notes for the synthesizer; each note is slightly detached (95% of its value)."""
    notes = []
    for voice in score.voices:
        for onset, event in score.timed(voice):
            start, length = score.seconds(onset), score.seconds(event.duration)
            amplitudes = event.amplitudes or [1.0] * len(event.pitches)
            for pitch, amplitude in zip(event.pitches, amplitudes):
                notes.append(synth.Note(start, midi_to_hz(pitch.midi, a4), max(length * 0.95, 0.02), amplitude))
    return notes
