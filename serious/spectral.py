"""Spectral tools: harmonic series, ring modulation and FM spectra (all in Hz)."""

from __future__ import annotations

from itertools import combinations


def harmonics(fundamental: float, count: int, sub: bool = False, stretch: float = 1.0) -> list[tuple[int, float]]:
    """Partials k = 1..count of ``fundamental``: f * k**stretch (or f / k**stretch for undertones).

    ``stretch`` other than 1 gives a compressed (< 1) or stretched (> 1) spectrum.
    """
    if count < 1:
        raise ValueError("need at least one partial")
    return [(k, fundamental / k ** stretch if sub else fundamental * k ** stretch) for k in range(1, count + 1)]


def ring(freqs: list[float]) -> list[float]:
    """Sum and difference tones of every pair of frequencies."""
    if len(freqs) < 2:
        raise ValueError("ring modulation needs at least two pitches")
    out = set()
    for a, b in combinations(freqs, 2):
        out.add(round(a + b, 6))
        if abs(a - b) > 1e-6:
            out.add(round(abs(a - b), 6))
    return sorted(out)


def fm(carrier: float, modulator: float, index: int) -> list[float]:
    """Carrier plus sidebands c ± k·m for k = 1..index (negative frequencies fold back)."""
    if index < 0:
        raise ValueError("the FM index can't be negative")
    out = {round(carrier, 6)}
    for k in range(1, index + 1):
        for f in (carrier + k * modulator, carrier - k * modulator):
            if abs(f) > 1e-6:
                out.add(round(abs(f), 6))
    return sorted(out)
