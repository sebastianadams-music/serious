import contextlib
import io
import os
import tempfile
import unittest
import wave
from array import array
from unittest import mock

from serious import synth
from serious.cli import main


def measured_frequency(samples):
    """Frequency from interpolated upward zero crossings."""
    crossings = [i + s0 / (s0 - s1) for i, (s0, s1) in enumerate(zip(samples, samples[1:])) if s0 < 0 <= s1]
    return (len(crossings) - 1) / (crossings[-1] - crossings[0]) * synth.SAMPLE_RATE


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue().strip(), err.getvalue().strip()


class SynthTest(unittest.TestCase):
    def test_pitch_is_exact(self):
        for freq in (440.0, 457.84, 27.5, 4186.0):
            with self.subTest(freq=freq):
                self.assertAlmostEqual(measured_frequency(synth.tone(freq, 2.0)), freq, delta=freq * 0.0005)

    def test_band_limited(self):
        # a saw at 15 kHz has no harmonics below Nyquist, so it is a pure sine
        self.assertEqual(synth._wavetable("saw", 1), synth._wavetable("sine", 1))
        self.assertEqual(len(synth.tone(15000, 0.1, "saw")), 4410)

    def test_envelope_starts_and_ends_silent(self):
        samples = synth.tone(220, 0.5, "square")
        self.assertEqual(samples[0], 0.0)
        self.assertLess(abs(samples[-1]), 0.01)

    def test_render_and_sequence(self):
        melody = synth.sequence([220, 330, 440], together=False, duration=0.5)
        self.assertEqual([n.start for n in melody], [0.0, 0.5, 1.0])
        chord = synth.sequence([220, 330, 440], together=True, duration=2.0)
        self.assertTrue(all(n.start == 0 for n in chord))
        samples = synth.render(chord, "organ", volume=0.8)
        self.assertEqual(len(samples), 2 * synth.SAMPLE_RATE)
        self.assertAlmostEqual(max(map(abs, samples)), 0.8)

    def test_unknown_wave(self):
        with self.assertRaises(synth.SynthError):
            synth.tone(440, 1, "kazoo")

    def test_write_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "a.wav")
            synth.write_wav(path, synth.tone(440, 0.25))
            with wave.open(path) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 44100))
                frames = array("h", w.readframes(w.getnframes()))
            self.assertEqual(len(frames), 11025)
            self.assertGreater(max(frames), 30000)


class SynthCliTest(unittest.TestCase):
    def test_out_writes_wav_and_still_prints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chord.wav")
            code, out, err = run("chord", "Cmaj7", "--out", path)
            self.assertEqual((code, out), (0, "C4 E4 G4 B4"), err)
            with wave.open(path) as w:
                # a chord defaults to a whole note (2 s at ♩ = 120), sounding for 95% of it
                self.assertAlmostEqual(w.getnframes() / w.getframerate(), 1.9)

    def test_play_command(self):
        with mock.patch.object(synth, "play") as play:
            code, out, err = run("play", "C4", "E4", "G4", "--dur", "0.2")
        self.assertEqual(code, 0, err)
        samples = play.call_args.args[0]
        self.assertAlmostEqual(len(samples) / synth.SAMPLE_RATE, 0.2 * 2 + 0.19, places=2)  # melody

    def test_play_flag_on_any_command(self):
        with mock.patch.object(synth, "play") as play:
            code, out, _ = run("transpose", "P5", "C4", "--play", "--chord")
        self.assertEqual((code, out), (0, "G4"))
        play.assert_called_once()

    def test_harmonics_play_as_a_scale(self):
        with mock.patch.object(synth, "render", wraps=synth.render) as render, mock.patch.object(synth, "play"):
            run("harmonics", "C2", "4", "--play")
            notes = render.call_args.args[0]
            self.assertEqual([n.start for n in notes], [0, 0.5, 1, 1.5])   # one after another
            self.assertEqual({n.amplitude for n in notes}, {1.0})          # equal levels in a scale
            self.assertEqual([n.freq for n in notes], sorted(n.freq for n in notes))
            run("harmonics", "C2", "4", "--play", "--chord")
            notes = render.call_args.args[0]
            self.assertEqual({n.start for n in notes}, {0})
            self.assertEqual([n.amplitude for n in notes], [1, 1 / 2, 1 / 3, 1 / 4])

    def test_missing_player(self):
        with mock.patch.object(synth, "find_player", return_value=None), \
                mock.patch.object(synth.sys, "platform", "darwin"):
            code, _, err = run("play", "C4")
        self.assertEqual(code, 2)
        self.assertIn("--out", err)


if __name__ == "__main__":
    unittest.main()
