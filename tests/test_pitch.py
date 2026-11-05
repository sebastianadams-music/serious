import unittest

from serious.pitch import (
    FORMATS, Pitch, PitchError, Settings, approximate, format_pitch, parse_pitch, parse_pitches, tokenize,
)


def midi(text, **settings):
    return parse_pitches(tokenize(text), Settings(**settings))[0].midi


class ParsingTest(unittest.TestCase):
    def test_note_names(self):
        cases = {
            "C4": 60, "c4": 60, "C#4": 61, "Db4": 61, "F♯4": 66, "E♭4": 63, "Cx4": 62, "C##4": 62,
            "Ebb4": 62, "B#3": 60, "Cb4": 59, "Csharp4": 61, "E-flat4": 63, "H4": 71, "C-1": 0, "G9": 127,
            "A4+50c": 69.5, "A4-14c": 68.86, "A4+31¢": 69.31, "Cqs4": 60.5, "Ctqf4": 58.5, "C𝄲4": 60.5,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(midi(text), expected)

    def test_octaveless_names_are_pitch_classes(self):
        p = parse_pitch("F#")
        self.assertFalse(p.has_octave)
        self.assertEqual(p.pc, 6)
        self.assertAlmostEqual(midi("C-14"), 59.86)  # 14 cents flat, not octave -14

    def test_lilypond_and_helmholtz(self):
        cases = {"c'": 60, "c''": 72, "c": 60, "fis'": 66, "es'": 63, "as'": 68, "eses'": 62,
                 "cisih'": 61.5, "eeh'": 63.5, "ly:bes,": 46, "ly:C,": 24}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(midi(text), expected)
        # outside LilyPond mode commas separate pitches; inside, they're octave marks
        self.assertEqual(len(tokenize("C, E, G")), 3)
        tokens = tokenize("c,, e'4 g'2.", lily=True)
        self.assertEqual([p.midi for p in parse_pitches(tokens, Settings(force="lily"))], [24, 64, 67])

    def test_solfege(self):
        for text, expected in {"Do4": 60, "do#4": 61, "Sib3": 58, "Sol4": 67, "Ré4": 62, "Ti4": 71, "La": 69}.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(midi(text), expected)

    def test_numbers(self):
        self.assertEqual(midi("60"), 60)
        self.assertEqual(midi("61.5"), 61.5)
        self.assertEqual(midi("6000"), 60)
        self.assertEqual(midi("6150mc"), 61.5)
        self.assertAlmostEqual(midi("440hz"), 69)
        self.assertAlmostEqual(midi("440 Hz"), 69)
        self.assertAlmostEqual(midi("1.76kHz"), 93, places=2)
        self.assertEqual(midi("midi:7"), 7)
        self.assertEqual(midi("hz:440"), 69)

    def test_pitch_class_lists(self):
        pitches = parse_pitches(tokenize("[0, 4, t, e]"))
        self.assertEqual([p.pc for p in pitches], [0, 4, 10, 11])
        self.assertTrue(all(p.source == "pc" for p in pitches))
        # anything above 11 means MIDI
        self.assertEqual([p.midi for p in parse_pitches(tokenize("4 60"))], [4, 60])

    def test_settings(self):
        self.assertEqual(midi("C3", middle_c=3), 60)
        self.assertAlmostEqual(midi("442hz", a4=442), 69)

    def test_errors(self):
        for text in ("nonsense", "Q4", "C4x7", "pc:z"):
            with self.subTest(text=text), self.assertRaises(PitchError):
                parse_pitches(tokenize(text))


class FormattingTest(unittest.TestCase):
    def test_formats(self):
        p = parse_pitch("C#4")
        expected = {"name": "C#4", "midi": "61", "mc": "6100", "pc": "1", "hz": "277.18Hz",
                    "lily": "cis'", "solfege": "Do#4"}
        for fmt, text in expected.items():
            self.assertEqual(format_pitch(p, fmt), text)

    def test_spelling(self):
        self.assertEqual(format_pitch(parse_pitch("Db4"), "name"), "Db4")
        self.assertEqual(format_pitch(parse_pitch("B#3"), "name"), "B#3")
        self.assertEqual(format_pitch(parse_pitch("Db4"), "name", Settings(spelling="sharps")), "C#4")
        self.assertEqual(format_pitch(Pitch(61), "name", Settings(spelling="flats")), "Db4")
        self.assertEqual(format_pitch(Pitch(63), "lily"), "es'")

    def test_microtones(self):
        self.assertEqual(format_pitch(Pitch(60.5), "name"), "C4+50c")
        self.assertEqual(format_pitch(Pitch(69.69), "name"), "Bb4-31c")
        self.assertEqual(format_pitch(Pitch(62.5), "lily"), "dih'")
        self.assertEqual(format_pitch(approximate(Pitch(69.69), 4), "midi"), "69.5")
        self.assertEqual(format_pitch(approximate(Pitch(62.5), 2), "midi"), "63")

    def test_round_trips(self):
        """Whatever Serious prints, it can read back."""
        for value in (0, 21, 59.5, 60, 61, 63.25, 69.69, 70, 108, 127):
            for fmt in ("name", "midi", "mc", "solfege"):
                with self.subTest(value=value, fmt=fmt):
                    text = format_pitch(Pitch(value), fmt)
                    self.assertAlmostEqual(parse_pitch(text).midi, value, places=2)
            text = format_pitch(Pitch(value), "hz")
            self.assertAlmostEqual(parse_pitch(text).midi, value, places=1)
            text = format_pitch(Pitch(value), "lily")
            self.assertAlmostEqual(parse_pitch(text).midi, round(value * 2) / 2)

    def test_formats_cover_all(self):
        p = Pitch(61.5)
        for fmt in FORMATS:
            self.assertTrue(format_pitch(p, fmt))


class TransformTest(unittest.TestCase):
    def test_transposition_keeps_spelling(self):
        p = parse_pitch("Eb4").transposed(4, 2)  # major third
        self.assertEqual(format_pitch(p, "name"), "G4")
        p = parse_pitch("C#4").transposed(4, 2)
        self.assertEqual(format_pitch(p, "name"), "E#4")

    def test_octaveless_stays_in_range(self):
        p = parse_pitch("B").transposed(2)
        self.assertEqual((p.midi, p.has_octave), (61, False))

    def test_inversion(self):
        self.assertEqual(Pitch(60).inverted(64).midi, 68)


if __name__ == "__main__":
    unittest.main()
