import unittest
from collections import Counter, defaultdict
from itertools import combinations

from serious import harmony, pcset
from serious.interval import IntervalError, between, name_interval, parse_interval
from serious.pitch import Settings, format_pitch, parse_pitch


class IntervalTest(unittest.TestCase):
    def test_parse(self):
        cases = {"7": 7, "-3": -3, "1.5": 1.5, "700c": 7, "-50c": -0.5, "2/1": 12, "P5": 7, "-m3": -3,
                 "M10": 16, "A4": 6, "d5": 6, "d7": 9, "TT": 6, "P8": 12, "m9": 13, "AA4": 7, "dd7": 8}
        for text, semitones in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_interval(text).semitones, semitones)
        self.assertAlmostEqual(parse_interval("3/2").semitones, 7.0196, places=4)
        self.assertEqual(parse_interval("M10").steps, 9)

    def test_parse_errors(self):
        for text in ("M5", "P3", "Q4", "x", "m0"):
            with self.subTest(text=text), self.assertRaises(IntervalError):
                parse_interval(text)

    def test_names(self):
        self.assertEqual(name_interval(7), "P5")
        self.assertEqual(name_interval(-3), "m3")
        self.assertEqual(name_interval(16), "M10")
        self.assertEqual(name_interval(24), "P15")
        self.assertEqual(name_interval(3.86), "M3 -14c")
        self.assertEqual(between(parse_pitch("C4"), parse_pitch("D#4"))[1], "A2")
        self.assertEqual(between(parse_pitch("C4"), parse_pitch("Eb4"))[1], "m3")
        self.assertEqual(between(parse_pitch("B#3"), parse_pitch("Cb4")), (-1, "m2"))  # spelling disagrees with direction


class SetTheoryTest(unittest.TestCase):
    def test_forte_catalogue_is_complete(self):
        catalogue = pcset.catalogue()
        every = {pcset.prime_form(c) for k in range(13) for c in combinations(range(12), k)}
        self.assertEqual(set(catalogue), every)
        self.assertEqual(len(set(catalogue.values())), 224)
        sizes = Counter(len(pf) for pf in catalogue)
        self.assertEqual([sizes[k] for k in range(13)], [1, 1, 6, 12, 29, 38, 50, 38, 29, 12, 6, 1, 1])

    def test_table_holds_prime_forms(self):
        for pf in pcset.catalogue():
            self.assertEqual(pcset.prime_form(pf), pf)

    def test_z_names_match_interval_vectors(self):
        by_vector = defaultdict(list)
        for pf, name in pcset.catalogue().items():
            by_vector[len(pf), pcset.interval_vector(pf)].append(name)
        for names in by_vector.values():
            for name in names:
                self.assertEqual("Z" in name, len(names) == 2, names)
            if len(names) == 2:  # Z pairs: same cardinality-number pairing scheme, e.g. 6-Z3 / 6-Z36
                self.assertEqual(len(names), 2)

    def test_known_sets(self):
        self.assertEqual(pcset.normal_form([7, 0, 4]), (0, 4, 7))
        self.assertEqual(pcset.prime_form([0, 4, 7]), (0, 3, 7))
        self.assertEqual(pcset.tn_type([0, 4, 7]), "3-11B")
        self.assertEqual(pcset.tn_type([0, 3, 7]), "3-11A")
        self.assertEqual(pcset.forte_name([0, 1, 4, 6]), "4-Z15")
        self.assertEqual(pcset.z_partner([0, 1, 4, 6]), "4-Z29")
        self.assertEqual(pcset.interval_vector([0, 1, 4, 6]), (1, 1, 1, 1, 1, 1))
        self.assertEqual(pcset.forte_name([0, 2, 4, 6, 8, 10]), "6-35")
        self.assertEqual(pcset.forte_name([0, 2, 4, 5, 7, 9, 11]), "7-35")  # diatonic
        self.assertEqual(pcset.forte_name([0, 1, 3, 4, 6, 7, 9, 10]), "8-28")  # octatonic
        self.assertEqual(pcset.forte_name([0, 1, 2, 3, 4, 5]), "6-1")
        self.assertEqual(pcset.forte_name([0, 2, 3, 6, 7, 9]), "6-Z29")
        self.assertEqual(pcset.forte_name([0, 4, 8]), "3-12")
        self.assertEqual(pcset.forte_name([0, 2, 4, 7, 9]), "5-35")  # pentatonic
        self.assertEqual(pcset.forte_name([0, 4, 7, 10]), "4-27")  # dominant seventh
        self.assertEqual(pcset.forte_name([0, 4, 7, 11]), "4-20")  # major seventh
        self.assertEqual(pcset.forte_name([0, 1, 4, 5, 8, 9]), "6-20")  # hexatonic
        self.assertEqual(pcset.symmetry([0, 3, 6, 9]), ([0, 3, 6, 9], [0, 3, 6, 9]))

    def test_microtones_rejected(self):
        with self.assertRaises(pcset.SetError):
            pcset.pcs_of([parse_pitch("C4+50c")])


class HarmonyTest(unittest.TestCase):
    def names(self, text):
        return [format_pitch(p, "name") for p in harmony.build(text, Settings())]

    def test_build(self):
        self.assertEqual(self.names("C"), ["C4", "E4", "G4"])
        self.assertEqual(self.names("Bbm7b5"), ["Bb4", "Db5", "Fb5", "Ab5"])
        self.assertEqual(self.names("F#7"), ["F#4", "A#4", "C#5", "E5"])
        self.assertEqual(self.names("C/E"), ["E3", "C4", "E4", "G4"])
        self.assertEqual(self.names("Eb6/9"), ["Eb4", "G4", "Bb4", "C5", "F5"])

    def test_identify(self):
        pitches = [parse_pitch(t) for t in ("E3", "G3", "C4")]
        self.assertEqual(harmony.identify(pitches, Settings()), ["C/E"])
        pitches = [parse_pitch(t) for t in ("C", "E", "G", "A")]
        self.assertEqual(harmony.identify(pitches, Settings()), ["C6", "Am7/C"])

    def test_scales(self):
        d_dorian = harmony.scale(parse_pitch("D4"), harmony.scale_name(["dorian"]))
        self.assertEqual([format_pitch(p, "name") for p in d_dorian], ["D4", "E4", "F4", "G4", "A4", "B4", "C5"])
        f_sharp = harmony.scale(parse_pitch("F#"), "major")
        self.assertEqual([format_pitch(p, "name") for p in f_sharp], ["F#", "G#", "A#", "B", "C#", "D#", "E#"])
        self.assertEqual(harmony.scale_name(["harmonic", "minor"]), "harmonic-minor")
        self.assertEqual(harmony.scale_name(["Messiaen", "2"]), "octatonic")


if __name__ == "__main__":
    unittest.main()
