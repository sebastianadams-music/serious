import contextlib
import io
import unittest

from serious.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue().strip(), err.getvalue().strip()


class CliTest(unittest.TestCase):
    def assertOutput(self, argv, expected):
        code, out, err = run(*argv)
        self.assertEqual((code, out), (0, expected), err)

    def test_answers_in_the_input_notation(self):
        self.assertOutput(["transpose", "M3", "Eb4", "G4", "Bb4"], "G4 B4 D5")
        self.assertOutput(["transpose", "7", "60", "64"], "67 71")
        self.assertOutput(["transpose", "7", "0", "4"], "7 11")
        self.assertOutput(["transpose", "-700c", "6000"], "5300")
        self.assertOutput(["transpose", "-m3", "fis'"], "dis'")
        self.assertOutput(["transpose", "P5", "Do4"], "Sol4")

    def test_to(self):
        self.assertOutput(["convert", "C", "E", "G", "--to", "midi"], "60 64 67")
        self.assertOutput(["C4", "--to", "hz"], "261.63Hz")
        self.assertOutput(["440hz", "-t", "name"], "A4")

    def test_implicit_convert_table(self):
        code, out, _ = run("F#4")
        self.assertEqual(code, 0)
        self.assertIn("F#4", out)
        self.assertIn("369.99Hz", out)
        self.assertIn("fis'", out)

    def test_transformations(self):
        self.assertOutput(["invert", "--axis", "E4", "C4", "D4", "G4"], "Ab4 F#4 C#4")
        self.assertOutput(["retrograde", "C", "D", "E"], "E D C")
        self.assertOutput(["rotate", "-n", "2", "C", "D", "E", "F"], "E F C D")
        self.assertOutput(["intervals", "--semitones", "C4", "E4", "G4", "C4"], "+4 +3 -7")

    def test_analysis(self):
        code, out, _ = run("set", "0", "1", "4", "6")
        self.assertIn("4-Z15A", out)
        self.assertIn("<111111>", out)
        self.assertOutput(["chord", "E3", "G3", "C4"], "C/E\nset class 3-11B (037)")
        self.assertOutput(["chord", "Cmaj7", "--to", "midi"], "60 64 67 71")
        self.assertOutput(["scale", "D", "dorian"], "D E F G A B C")
        code, out, _ = run("matrix", "0", "e", "3", "4", "8", "7", "9", "5", "6", "1", "2", "t")
        self.assertEqual(out.splitlines()[1].split(), ["P0", "0", "e", "3", "4", "8", "7", "9", "5", "6", "1", "2", "t", "R0"])

    def test_spectral(self):
        code, out, _ = run("harmonics", "C2", "4", "--to", "midi")
        self.assertEqual(out, "36 48 55.02 60")
        self.assertOutput(["fm", "A4", "A4", "-i", "1"], "A4 A5")
        self.assertOutput(["ring", "220hz", "330hz", "--to", "hz"], "110Hz 550Hz")

    def test_errors(self):
        code, _, err = run("tranpose", "M3", "C")
        self.assertEqual(code, 2)
        self.assertIn("did you mean `serious transpose`", err)
        code, out, _ = run("harmonics", "A1", "6", "--to", "mc")
        code, out, _ = run("set", "--approx", "2", *out.split())
        self.assertIn("(037)", out)  # A, E, C# (5th partial rounds to C#)
        code, _, err = run("set", "C4+50c")
        self.assertEqual(code, 2)
        self.assertIn("12-tone", err)


if __name__ == "__main__":
    unittest.main()
