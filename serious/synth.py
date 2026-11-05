"""A small additive synthesizer: render pitches to audio, write WAV files, play them.

Waveforms are band-limited wavetables (only harmonics below the Nyquist
frequency), so high notes don't alias.  Frequencies are exact, so microtones
and spectra sound as written.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave as wavefile
from array import array
from dataclasses import dataclass
from functools import lru_cache

SAMPLE_RATE = 44100
_TABLE_SIZE = 8192  # power of two, so phase wraps with a bit mask
_MAX_HARMONICS = 64

WAVES = {
    "sine": lambda k: 1.0 if k == 1 else 0.0,
    "triangle": lambda k: (-1) ** ((k - 1) // 2) / k ** 2 if k % 2 else 0.0,
    "square": lambda k: 1 / k if k % 2 else 0.0,
    "saw": lambda k: 1 / k,
    "organ": lambda k: {1: 1.0, 2: 0.6, 3: 0.4, 4: 0.25, 6: 0.15, 8: 0.1}.get(k, 0.0),
}

_ATTACK = 0.01   # seconds
_RELEASE = 0.12


class SynthError(RuntimeError):
    pass


@dataclass(frozen=True)
class Note:
    start: float      # seconds
    freq: float       # Hz
    duration: float   # seconds, including the release
    amplitude: float = 1.0


@lru_cache(maxsize=None)
def _wavetable(wave: str, harmonics: int) -> tuple[float, ...]:
    weights = [(k, WAVES[wave](k)) for k in range(1, harmonics + 1)]
    weights = [(k, w) for k, w in weights if w]
    step = 2 * math.pi / _TABLE_SIZE
    table = [sum(w * math.sin(k * step * i) for k, w in weights) for i in range(_TABLE_SIZE)]
    peak = max(map(abs, table)) or 1.0
    return tuple(x / peak for x in table)


def tone(freq: float, duration: float, wave: str = "sine") -> list[float]:
    """One enveloped note, peak amplitude 1."""
    if wave not in WAVES:
        raise SynthError(f"unknown waveform {wave!r} (choose from {', '.join(WAVES)})")
    count = max(1, round(duration * SAMPLE_RATE))
    if freq <= 0 or freq >= SAMPLE_RATE / 2:
        return [0.0] * count  # inaudible or unrepresentable: silence
    harmonics = max(1, min(_MAX_HARMONICS, int(SAMPLE_RATE / 2 / freq)))
    table = _wavetable(wave, harmonics)
    increment = freq * _TABLE_SIZE / SAMPLE_RATE
    mask = _TABLE_SIZE - 1
    samples = [table[int(i * increment) & mask] for i in range(count)]
    return [s * e for s, e in zip(samples, _envelope(count))]


def _envelope(count: int) -> list[float]:
    attack = min(round(_ATTACK * SAMPLE_RATE), count // 2)
    release = min(round(_RELEASE * SAMPLE_RATE), count - attack)
    sustain = count - attack - release
    return (
        [i / attack for i in range(attack)]
        + [1.0] * sustain
        + [(release - i) / release for i in range(release)]
    )


def render(notes: list[Note], wave: str = "sine", volume: float = 0.5) -> list[float]:
    """Mix notes into one buffer, normalised so the loudest sample is ``volume``."""
    if not notes:
        return []
    length = max(round((n.start + n.duration) * SAMPLE_RATE) for n in notes)
    mix = [0.0] * length
    for note in notes:
        offset = round(note.start * SAMPLE_RATE)
        samples = tone(note.freq, note.duration, wave)
        segment = mix[offset:offset + len(samples)]
        mix[offset:offset + len(segment)] = [m + note.amplitude * s for m, s in zip(segment, samples)]
    peak = max(map(abs, mix)) or 1.0
    scale = max(0.0, min(volume, 1.0)) / peak
    return [m * scale for m in mix]


def sequence(freqs: list[float], together: bool, duration: float,
             amplitudes: list[float] | None = None) -> list[Note]:
    """Notes for a chord (all at once) or a melody (one after another)."""
    amplitudes = amplitudes or [1.0] * len(freqs)
    if together:
        return [Note(0.0, f, duration, a) for f, a in zip(freqs, amplitudes)]
    return [Note(i * duration, f, duration * 0.95, a) for i, (f, a) in enumerate(zip(freqs, amplitudes))]


def write_wav(path: str, samples: list[float]) -> None:
    pcm = array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
    if sys.byteorder == "big":
        pcm.byteswap()  # WAV is little-endian
    with wavefile.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm.tobytes())


def find_player() -> list[str] | None:
    """A command-line audio player available on this system."""
    for command in (["afplay"], ["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(command[0]):
            return command
    return None


def play(samples: list[float]) -> None:
    """Play through the speakers; blocks until the sound has finished."""
    if sys.platform == "win32":
        import winsound
        path = _temporary_wav(samples)
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME)
        finally:
            os.remove(path)
        return
    player = find_player()
    if not player:
        raise SynthError("no audio player found (afplay, paplay, aplay or ffplay); use --out FILE.wav instead")
    path = _temporary_wav(samples)
    try:
        result = subprocess.run([*player, path], check=False)
        if result.returncode != 0:
            raise SynthError(f"audio player {player[0]} exited with status {result.returncode}")
    finally:
        os.remove(path)


class Player:
    """Non-blocking playback for the interactive keyboard; overlapping sounds are fine.

    Sounds are written to temporary WAV files once (``load``) and can then be
    started instantly and repeatedly.  ``close`` stops everything and removes the files.
    """

    def __init__(self, mute: bool = False):
        self.command = None if mute else find_player()
        self.available = self.command is not None or (sys.platform == "win32" and not mute)
        self._files: list[str] = []
        self._processes: list[subprocess.Popen] = []

    def load(self, samples: list[float]) -> str | None:
        if not self.available:
            return None
        path = _temporary_wav(samples)
        self._files.append(path)
        return path

    def start(self, path: str | None) -> None:
        if path is None:
            return
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        self._processes = [p for p in self._processes if p.poll() is None]
        self._processes.append(subprocess.Popen(
            [*self.command, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def close(self) -> None:
        for process in self._processes:
            if process.poll() is None:
                process.terminate()
                process.wait()
        for path in self._files:
            os.remove(path)
        self._files.clear()

    def __enter__(self) -> Player:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _temporary_wav(samples: list[float]) -> str:
    handle, path = tempfile.mkstemp(prefix="serious-", suffix=".wav")
    os.close(handle)
    write_wav(path, samples)
    return path
