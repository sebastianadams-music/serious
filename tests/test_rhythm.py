import contextlib
import io
import unittest
from fractions import Fraction as F
from unittest import mock

from serious import rhythm, synth
from serious.cli import main
from serious.rhythm import Duration, Timing


def values(text, **timing):
    return [d.value for d in rhythm.parse_rhythm(rhythm.tokenize(text), Timing(**timing))]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue().strip(), err.getvalue().strip()


class ParsingTest(unittest.TestCase):
    def test_notations(self):
        q, e, s = F(1, 4), F(1, 8), F(1, 16)
        cases = {
            "q e e h": [q, e, e, F(1, 2)],
            "q. e.. s3": [F(3, 8), F(7, 32), F(1, 24)],
            "dotted-quarter triplet-eighth crotchet quaver": [F(3, 8), F(1, 12), q, e],
            "1/4 3/8 1/12": [q, F(3, 8), F(1, 12)],
            "4. 8 16 8*2/3 16*5": [F(3, 8), e, s, F(1, 12), F(5, 16)],
            "1 0.5 1.5": [q, e, F(3, 8)],      # beats
            "4 8": [1, 2],                     # still beats: nothing marks them as LilyPond
            "250ms 0.5s": [e, q],              # at 120 bpm
            "x..x..x.": [F(3, 16), F(3, 16), e],
            "3:2:1": [F(3, 16), e, s],
            "q e | e q": [q, e, e, q],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(values(text), expected)
        self.assertEqual(values("4 8", force="lily"), [q, e])
        self.assertEqual(values("500ms", tempo=F(60)), [e])
        self.assertEqual(values("x.x.", pulse=F(1, 8)), [q, q])

    def test_rests(self):
        durations = rhythm.parse_rhythm(rhythm.tokenize("rq e r1/8 ..x"))
        self.assertEqual([(d.value, d.rest) for d in durations],
                         [(F(1, 4), True), (F(1, 8), False), (F(1, 8), True), (F(1, 8), True), (F(1, 16), False)])

    def test_errors(self):
        for text in ("z", "q0", "0", "1/0x", "r"):
            with self.subTest(text=text), self.assertRaises(rhythm.RhythmError):
                rhythm.parse_rhythm(rhythm.tokenize(text))


class FormattingTest(unittest.TestCase):
    def test_formats(self):
        durations = [Duration(v) for v in (F(3, 8), F(1, 12), F(5, 16), F(2))]
        self.assertEqual(rhythm.format_rhythm(durations, "name"), "q. e3 5/16 2/1")  # no name for a breve
        self.assertEqual(rhythm.format_rhythm(durations, "lily"), "4. 8*2/3 16*5 1*2")
        self.assertEqual(rhythm.format_rhythm(durations, "fraction"), "3/8 1/12 5/16 2/1")
        self.assertEqual(rhythm.format_rhythm(durations[:1], "beats"), "1.5")
        self.assertEqual(rhythm.format_rhythm(durations[:1], "ms"), "750ms")
        self.assertEqual(rhythm.format_rhythm([Duration(F(1, 8), rest=True)], "lily"), "r8")

    def test_round_trips(self):
        """Exact notations read back exactly."""
        for value in (F(1, 4), F(3, 8), F(7, 32), F(1, 12), F(1, 20), F(5, 16), F(1, 6), F(3, 2), F(1, 3), F(1), F(2)):
            for fmt in ("fraction", "lily", "name"):
                with self.subTest(value=value, fmt=fmt):
                    text = rhythm.format_duration(Duration(value), fmt)
                    self.assertEqual(values(text, force="lily" if fmt == "lily" else None), [value])

    def test_pattern(self):
        self.assertEqual(rhythm.to_pattern(rhythm.parse_rhythm(rhythm.tokenize("e. e. e")), F(1, 16)), "x..x..x.")
        with self.assertRaises(rhythm.RhythmError):
            rhythm.to_pattern([Duration(F(1, 12))], F(1, 16))


class OperationsTest(unittest.TestCase):
    def test_euclid_matches_toussaint(self):
        known = {(2, 5): "x.x..", (3, 8): "x..x..x.", (4, 9): "x.x.x.x..", (5, 8): "x.xx.xx.",
                 (5, 12): "x..x.x..x.x.", (5, 16): "x..x..x..x..x...", (7, 12): "x.xx.x.xx.x.",
                 (7, 16): "x..x.x.x..x.x.x.", (9, 16): "x.xx.x.x.xx.x.x."}
        for (k, n), pattern in known.items():
            with self.subTest(k=k, n=n):
                self.assertEqual("".join("x" if b else "." for b in rhythm.euclid(k, n)), pattern)

    def test_euclid_is_maximally_even(self):
        for n in range(1, 25):
            for k in range(1, n + 1):
                pattern = rhythm.euclid(k, n)
                self.assertEqual(sum(pattern), k)
                gaps = [d.value * 16 for d in rhythm.pattern_durations(pattern, F(1, 16))]
                self.assertLessEqual(max(gaps) - min(gaps), 1, (k, n))

    def test_euclid_rotation_and_errors(self):
        self.assertEqual("".join("x" if b else "." for b in rhythm.euclid(3, 8, 2)), ".x..x.x.")
        with self.assertRaises(rhythm.RhythmError):
            rhythm.euclid(9, 8)

    def test_messiaen(self):
        s, e, q = Duration(F(1, 16)), Duration(F(1, 8)), Duration(F(1, 4))
        self.assertEqual(rhythm.palindrome([s, e], q), [s, e, q, e, s])
        self.assertEqual(rhythm.palindrome([s, e, q]), [s, e, q, e, s])
        self.assertTrue(rhythm.is_palindrome(rhythm.palindrome([s, e, q, s])))
        self.assertFalse(rhythm.is_palindrome([s, e]))
        self.assertEqual(rhythm.add_value([q, q], s), [q, q, s])
        self.assertEqual(rhythm.add_dot([q, q], 1)[1].value, F(3, 8))

    def test_scale(self):
        self.assertEqual([d.value for d in rhythm.scale([Duration(F(1, 4))], F(3, 2))], [F(3, 8)])

    def test_infinity_rhythm_self_similar(self):
        r = [d.value for d in rhythm.infinity_rhythm(256, F(1, 16))]
        self.assertEqual(r[:8], [F(n, 16) for n in (1, 2, 2, 3, 2, 1, 3, 4)])
        self.assertEqual(r[::2], r[:128])  # |a(2n)| = |a(n)|: every other duration repeats the rhythm

    def test_timing(self):
        durations = rhythm.parse_rhythm(rhythm.tokenize("q e e q. e"))
        self.assertEqual(rhythm.total(durations), F(1))
        self.assertEqual([rhythm.bar_position(t, (3, 4)) for t in rhythm.onsets(durations)],
                         ["1:1", "1:2", "1:2.5", "1:3", "2:1.5"])
        self.assertEqual(rhythm.lcm_time(F(5, 2), F(3)), F(15))
        self.assertEqual(rhythm.lcm_time(F(3, 4), F(1, 6)), F(3, 2))


class CliTest(unittest.TestCase):
    def assertOutput(self, argv, expected):
        code, out, err = run(*argv)
        self.assertEqual((code, out), (0, expected), err)

    def test_commands_answer_in_the_input_notation(self):
        self.assertOutput(["rhythm", "augment", "3/2", "q", "e", "e"], "q. e. e.")
        self.assertOutput(["rhythm", "diminish", "2", "4.", "8", "4"], "8. 16 8")
        self.assertOutput(["rhythm", "augment", "2", "1/4", "1/12"], "1/2 1/6")
        self.assertOutput(["rhythm", "euclid", "5", "8"], "x.xx.xx.")
        self.assertOutput(["rhythm", "euclid", "3", "8", "--to", "name"], "e. e. e")
        self.assertOutput(["rhythm", "palindrome", "s", "e", "q."], "s e q. e s")
        self.assertOutput(["rhythm", "add-value", "dot", "--at", "2", "q", "q", "q"], "q q. q")
        self.assertOutput(["rhythm", "add-value", "rs", "--at", "2", "q", "q"], "q rs q")
        self.assertOutput(["rhythm", "retrograde", "q", "e", "s"], "s e q")
        self.assertOutput(["rhythm", "rotate", "-n", "2", "q", "e", "s", "s"], "s s q e")
        self.assertOutput(["rhythm", "infinity", "8"], "xx.x.x..x.xx..x...")  # 1 2 2 3 2 1 3 4 pulses

    def test_show(self):
        code, out, _ = run("rhythm", "q", "e", "e", "q", "--meter", "3/4")
        self.assertIn("in 3/4      1 bar", out)
        self.assertIn("1:1 1:2 1:2.5 1:3", out)
        self.assertIn("yes: non-retrogradable", out)

    def notes_played(self, *argv):
        with mock.patch.object(synth, "render", wraps=synth.render) as render, mock.patch.object(synth, "play"):
            code, _, err = run(*argv)
        self.assertEqual(code, 0, err)
        return render.call_args.args[0]

    def test_play_with_rhythm(self):
        notes = self.notes_played("play", "C4", "E4", "G4", "C5", "--rhythm", "q e e", "--tempo", "60")
        self.assertEqual([n.start for n in notes], [0, 1, 1.5, 2])  # the rhythm repeats for the 4th note
        notes = self.notes_played("play", "C4", "E4", "--rhythm", "rq q", "--tempo", "60")
        self.assertEqual([n.start for n in notes], [1, 3])          # rests take time but no pitch
        notes = self.notes_played("chord", "C", "--play", "--rhythm", "h q")
        self.assertEqual(sorted({n.start for n in notes}), [0, 1])  # the chord on every duration
        self.assertEqual(len(notes), 6)

    def test_rhythm_plays_on_one_pitch(self):
        notes = self.notes_played("rhythm", "euclid", "3", "8", "--play", "--on", "A4", "--tempo", "60")
        self.assertEqual([n.start for n in notes], [0, 0.75, 1.5])
        self.assertTrue(all(abs(n.freq - 440) < 1e-6 for n in notes))


class IsorhythmTest(unittest.TestCase):
    """Messiaen, Liturgie de cristal (Quartet for the End of Time), as usually described:
    the piano runs a color of 29 chords against a talea of 17 durations, the cello
    a color of 5 pitches against a talea of 15 durations.  The material here is a
    stand-in; the structure is Messiaen's."""

    PIANO_COLOR = "$(serious infinity 29)"  # replaced below
    PIANO_TALEA = "s e s e. q s e. e q. e e. s q e. s e s"  # non-retrogradable, 17 values
    CELLO_COLOR = "C3 E3 D3 F#3 Bb3"
    CELLO_TALEA = "q e. e q. s s e q e. h e q e. s q"      # 15 values

    def setUp(self):
        _, self.piano_color, _ = run("infinity", "29")

    def test_the_talea_is_non_retrogradable(self):
        talea = rhythm.parse_rhythm(rhythm.tokenize(self.PIANO_TALEA))
        self.assertEqual(len(talea), 17)
        self.assertTrue(rhythm.is_palindrome(talea))
        _, built, _ = run("rhythm", "palindrome", "s", "e", "s", "e.", "q", "s", "e.", "e", "--centre", "q.")
        self.assertEqual(built, self.PIANO_TALEA)

    def test_liturgie_de_cristal_structure(self):
        code, out, err = run("isorhythm", "--color", self.piano_color, "--talea", self.PIANO_TALEA,
                             "--color", self.CELLO_COLOR, "--talea", self.CELLO_TALEA)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith(
            "voice 1: 29 pitches × 17 durations → they realign after 493 notes (17 × color, 29 × talea)"), lines[0])
        self.assertIn("290 beats", lines[0])
        piano = lines[1].split()
        self.assertEqual(len(piano), 493)
        # color and talea only start together again after all 493 notes
        color, talea = self.piano_color.split(), self.PIANO_TALEA.split()
        self.assertEqual(piano[0], f"{color[0]}:{talea[0]}")
        self.assertEqual(piano[29 * 3], f"{color[0]}:{talea[29 * 3 % 17]}")
        self.assertTrue(lines[3].startswith(
            "voice 2: 5 pitches × 15 durations → they realign after 15 notes (3 × color, 1 × talea)"), lines[3])
        self.assertEqual(lines[-1], "all voices realign after 1740 beats = 14:30 at ♩ = 120")

    def test_cycle_repeats_exactly(self):
        # 29 distinct pitches against 17 distinct durations, so each event shows its (color, talea) position
        color = " ".join(f"midi:{48 + i}" for i in range(29))
        talea = " ".join(f"{n}/64" for n in range(1, 18))
        code, out, err = run("isorhythm", "--color", color, "--talea", talea, "--notes", "494", "--to", "midi")
        self.assertEqual(code, 0, err)
        events = out.splitlines()[1].split()
        self.assertEqual(len(set(events[:493])), 493)  # every pairing occurs exactly once per cycle
        self.assertEqual(events[493], events[0])        # ...and then the cycle starts again

    def test_shorter_voices_keep_cycling_when_played(self):
        with mock.patch.object(synth, "render", wraps=synth.render) as render, mock.patch.object(synth, "play"):
            code, _, err = run("isorhythm", "--color", "C4 D4 E4", "--talea", "q q q q",
                               "--color", "C3", "--talea", "h", "--play", "--tempo", "60")
        self.assertEqual(code, 0, err)
        notes = render.call_args.args[0]
        low = [n.start for n in notes if n.freq < 200]
        self.assertEqual(low, [0, 2, 4, 6, 8, 10])  # the 12-beat upper voice; the bass repeats under it

    def test_chords_and_rests(self):
        code, out, err = run("isorhythm", "--color", "<C4 E4> G4", "--talea", "q rq e")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[1], "<C4 E4>:q r:q G4:e")
        code, _, err = run("isorhythm", "--color", "C4", "--talea", "q", "--color", "D4")
        self.assertEqual(code, 2)
        self.assertIn("in pairs", err)


if __name__ == "__main__":
    unittest.main()
