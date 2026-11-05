"""Pitch-class set theory: normal form, prime form (Rahn), Forte names, interval vectors."""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations

from .pitch import Pitch

# Forte numbers for trichords to hexachords, with Rahn prime forms.
# Septachords to nonachords are named after their complements (7-n <-> 5-n etc.).
_FORTE = """
3-1 012  3-2 013  3-3 014  3-4 015  3-5 016  3-6 024  3-7 025  3-8 026  3-9 027
3-10 036  3-11 037  3-12 048
4-1 0123  4-2 0124  4-3 0134  4-4 0125  4-5 0126  4-6 0127  4-7 0145  4-8 0156
4-9 0167  4-10 0235  4-11 0135  4-12 0236  4-13 0136  4-14 0237  4-Z15 0146
4-16 0157  4-17 0347  4-18 0147  4-19 0148  4-20 0158  4-21 0246  4-22 0247
4-23 0257  4-24 0248  4-25 0268  4-26 0358  4-27 0258  4-28 0369  4-Z29 0137
5-1 01234  5-2 01235  5-3 01245  5-4 01236  5-5 01237  5-6 01256  5-7 01267
5-8 02346  5-9 01246  5-10 01346  5-11 02347  5-Z12 01356  5-13 01248
5-14 01257  5-15 01268  5-16 01347  5-Z17 01348  5-Z18 01457  5-19 01367
5-20 01568  5-21 01458  5-22 01478  5-23 02357  5-24 01357  5-25 02358
5-26 02458  5-27 01358  5-28 02368  5-29 01368  5-30 01468  5-31 01369
5-32 01469  5-33 02468  5-34 02469  5-35 02479  5-Z36 01247  5-Z37 03458
5-Z38 01258
6-1 012345  6-2 012346  6-Z3 012356  6-Z4 012456  6-5 012367  6-Z6 012567
6-7 012678  6-8 023457  6-9 012357  6-Z10 013457  6-Z11 012457  6-Z12 012467
6-Z13 013467  6-14 013458  6-15 012458  6-16 014568  6-Z17 012478  6-18 012578
6-Z19 013478  6-20 014589  6-21 023468  6-22 012468  6-Z23 023568  6-Z24 013468
6-Z25 013568  6-Z26 013578  6-27 013469  6-Z28 013569  6-Z29 023679  6-30 013679
6-31 014579  6-32 024579  6-33 023579  6-34 013579  6-35 02468t  6-Z36 012347
6-Z37 012348  6-Z38 012378  6-Z39 023458  6-Z40 012358  6-Z41 012368
6-Z42 012369  6-Z43 012568  6-Z44 012569  6-Z45 023469  6-Z46 012469
6-Z47 012479  6-Z48 012579  6-Z49 013479  6-Z50 014679
"""


class SetError(ValueError):
    pass


def pcs_of(pitches: list[Pitch]) -> tuple[int, ...]:
    """Sorted, de-duplicated pitch classes (12-tone equal temperament only)."""
    pcs = set()
    for p in pitches:
        pc = p.pc
        if abs(pc - round(pc)) > 1e-6:
            raise SetError("pitch-class set theory needs 12-tone pitches; use --approx 2 to round")
        pcs.add(round(pc) % 12)
    return tuple(sorted(pcs))


def _packing_key(ordering: tuple[int, ...]) -> tuple:
    """Rahn's criteria: smallest span, then most packed towards the start (from the right)."""
    first = ordering[0]
    spans = [(pc - first) % 12 for pc in ordering]
    return (spans[-1], *reversed(spans[1:-1]))


def normal_form(pcs) -> tuple[int, ...]:
    pcs = sorted(set(pc % 12 for pc in pcs))
    if not pcs:
        return ()
    rotations = [tuple(pcs[i:] + pcs[:i]) for i in range(len(pcs))]
    return min(rotations, key=lambda r: (_packing_key(r), r[0]))


def transpose_to_zero(pcs) -> tuple[int, ...]:
    return tuple((pc - pcs[0]) % 12 for pc in pcs) if pcs else ()


def prime_form(pcs) -> tuple[int, ...]:
    if not pcs:
        return ()
    original = transpose_to_zero(normal_form(pcs))
    inverted = transpose_to_zero(normal_form([-pc for pc in pcs]))
    return min(original, inverted, key=_packing_key)


def interval_vector(pcs) -> tuple[int, ...]:
    vector = [0] * 6
    for a, b in combinations(sorted(set(pcs)), 2):
        ic = min((a - b) % 12, (b - a) % 12)
        vector[ic - 1] += 1
    return tuple(vector)


def complement(pcs) -> tuple[int, ...]:
    return tuple(pc for pc in range(12) if pc not in set(pcs))


def symmetry(pcs) -> tuple[list[int], list[int]]:
    """Transposition levels n with Tn(S) = S, and inversion indices n with TnI(S) = S."""
    s = set(pcs)
    tn = [n for n in range(12) if {(pc + n) % 12 for pc in s} == s]
    tni = [n for n in range(12) if {(n - pc) % 12 for pc in s} == s]
    return tn, tni


def row_matrix(row: list[int]) -> list[list[int]]:
    """Twelve-tone matrix: the row along the top, its inversion down the left."""
    return [[(pc - start + row[0]) % 12 for pc in row] for start in row]


def compact(pcs) -> str:
    """(0158)-style notation, with t and e for 10 and 11."""
    return "".join("te"[pc - 10] if pc >= 10 else str(pc) for pc in pcs)


@lru_cache(maxsize=None)
def catalogue() -> dict[tuple[int, ...], str]:
    """Every set class (prime form -> Forte name), cardinalities 0 to 12."""
    names: dict[tuple[int, ...], str] = {(): "0-1", (0,): "1-1", tuple(range(12)): "12-1"}
    names[tuple(range(11))] = "11-1"
    for n in range(1, 7):
        names[(0, n)] = f"2-{n}"
    fields = _FORTE.split()
    for name, digits in zip(fields[::2], fields[1::2]):
        names[tuple(int(d) if d.isdigit() else 10 + "te".index(d) for d in digits)] = name
    for pf, name in list(names.items()):
        size, number = name.split("-")
        if 2 <= int(size) <= 5:
            names[prime_form(complement(pf))] = f"{12 - int(size)}-{number}"
    return names


def forte_name(pcs) -> str:
    return catalogue()[prime_form(pcs)]


def tn_type(pcs) -> str:
    """Forte name with A/B suffix (A = prime form itself, B = its inversion)."""
    name = forte_name(pcs)
    tn, tni = symmetry(pcs)
    if tni:
        return name
    return name + ("A" if transpose_to_zero(normal_form(pcs)) == prime_form(pcs) else "B")


def z_partner(pcs) -> str | None:
    pf = prime_form(pcs)
    vector = interval_vector(pf)
    for other, name in catalogue().items():
        if len(other) == len(pf) and other != pf and interval_vector(other) == vector:
            return name
    return None
