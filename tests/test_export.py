import contextlib
import io
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction as F

from serious import export
from serious.cli import main
from serious.pitch import Pitch, Settings, parse_pitch
from serious.score import Event, Score


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue().strip(), err.getvalue().strip()


def notes(*names_and_values):
    """Event list from ("C4", 1/4) pairs; None is a rest, a tuple of names is a chord."""
    events = []
    for names, value in names_and_values:
        if names is None:
            pitches = []
        elif isinstance(names, tuple):
            pitches = [parse_pitch(n) for n in names]
        else:
            pitches = [parse_pitch(names)]
        events.append(Event(pitches, F(value)))
    return events


def read_midi(data: bytes):
    """A minimal MIDI reader: (ticks per quarter, [track events as (tick, status, data bytes)])."""
    assert data[:4] == b"MThd"
    _, fmt, ntracks, tpq = struct.unpack(">IHHH", data[4:14])
    pos, tracks = 14, []
    for _ in range(ntracks):
        assert data[pos:pos + 4] == b"MTrk"
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        chunk, pos, i, tick, events = data[pos + 8:pos + 8 + length], pos + 8 + length, 0, 0, []
        while i < len(chunk):
            delta = 0
            while True:
                byte = chunk[i]
                i += 1
                delta = (delta << 7) | (byte & 0x7F)
                if byte < 0x80:
                    break
            tick += delta
            status = chunk[i]
            if status == 0xFF:
                kind, size = chunk[i + 1], chunk[i + 2]
                events.append((tick, 0xFF, bytes([kind]) + chunk[i + 3:i + 3 + size]))
                i += 3 + size
            else:
                size = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
                events.append((tick, status, chunk[i + 1:i + 1 + size]))
                i += 1 + size
        tracks.append(events)
    return fmt, tpq, tracks


class NotationTest(unittest.TestCase):
    def test_note_values(self):
        self.assertEqual(export._note_values(F(3, 8)), (None, [(F(1, 4), 1)]))
        self.assertEqual(export._note_values(F(5, 16)), (None, [(F(1, 4), 0), (F(1, 16), 0)]))
        self.assertEqual(export._note_values(F(7, 16)), (None, [(F(1, 4), 2)]))
        self.assertEqual(export._note_values(F(1, 12)), ((3, 2), [(F(1, 8), 0)]))
        self.assertEqual(export._note_values(F(1, 20)), ((5, 4), [(F(1, 16), 0)]))

    def test_barlines_split_and_tie(self):
        bars = export.measures(notes(("C4", F(1, 2)), ("D4", F(3, 4))), (3, 4), F(5, 4))
        self.assertEqual(len(bars), 2)
        first, second = bars
        self.assertEqual([(s.value, s.tie_start, s.tie_stop) for s in first],
                         [(F(1, 2), False, False), (F(1, 4), True, False)])
        self.assertEqual([(s.value, s.tie_start, s.tie_stop) for s in second[:1]], [(F(1, 2), False, True)])
        self.assertTrue(second[-1].pitches == [])  # padded with a rest to fill the bar
        for bar in bars:
            self.assertEqual(sum(s.value for s in bar), F(3, 4))

    def test_rests_merge(self):
        bars = export.measures(notes((None, F(1, 8)), (None, F(1, 8)), ("C4", F(3, 4))), (4, 4), F(1))
        # the two eighth rests become one quarter rest; the 3/4 note fits as a dotted half
        self.assertEqual([(bool(s.pitches), s.value, s.dots) for s in bars[0]], [(False, F(1, 4), 0), (True, F(3, 4), 1)])

    def test_grand_staff_for_wide_voices(self):
        layout = export.staves(notes((("C2", "E4", "G5"), 1)))
        self.assertEqual([clef for clef, _ in layout], ["treble", "bass"])
        self.assertEqual([len(events[0].pitches) for _, events in layout], [2, 1])
        self.assertEqual([c for c, _ in export.staves(notes(("C3", 1), ("E3", 1)))], ["bass"])


class MidiTest(unittest.TestCase):
    def test_notes_and_timing(self):
        score = Score([notes(("C4", F(1, 4)), (None, F(1, 4)), (("E4", "G4"), F(1, 2)))], F(90), (3, 4))
        fmt, tpq, tracks = read_midi(export.to_midi(score))
        self.assertEqual((fmt, tpq, len(tracks)), (1, 480, 2))
        meta = {e[2][0]: e[2][1:] for e in tracks[0] if e[1] == 0xFF}
        self.assertEqual(int.from_bytes(meta[0x51], "big"), round(60_000_000 / 90))  # tempo
        self.assertEqual(meta[0x58][:2], bytes([3, 2]))                               # 3/4
        ons = [(t, d[0]) for t, status, d in tracks[1] if status & 0xF0 == 0x90]
        offs = [(t, d[0]) for t, status, d in tracks[1] if status & 0xF0 == 0x80]
        self.assertEqual(ons, [(0, 60), (960, 64), (960, 67)])
        self.assertEqual(sorted(offs), [(480, 60), (1920, 64), (1920, 67)])

    def test_microtones_use_pitch_bend(self):
        quarter_sharp, low_seventh = Pitch(60.5), Pitch(69.69)
        score = Score([[Event([quarter_sharp, low_seventh, Pitch(64)], F(1))]])
        _, _, tracks = read_midi(export.to_midi(score))
        on = {d[0]: status & 0x0F for _, status, d in tracks[1] if status & 0xF0 == 0x90}
        bends = {status & 0x0F: (d[1] << 7 | d[0]) for _, status, d in tracks[1] if status & 0xF0 == 0xE0}
        self.assertEqual(len(set(on.values())), 3)          # every note on its own channel
        self.assertNotIn(9, on.values())                    # never the drum channel
        self.assertEqual(bends[on[61] if 61 in on else on[60]], 8192 + round(0.5 / 2 * 8192))  # C4+50c
        self.assertEqual(bends[on[70]], 8192 + round(-0.31 / 2 * 8192))                       # Bb4-31c
        self.assertNotIn(on[64], bends)                     # E4 needs no bend

    def test_out_of_range(self):
        with self.assertRaises(export.ExportError):
            export.to_midi(Score([[Event([Pitch(140)], F(1, 4))]]))


class MusicXmlTest(unittest.TestCase):
    def parse(self, score):
        return ET.fromstring(export.to_musicxml(score, Settings()).split("\n", 2)[2])

    def test_every_bar_adds_up(self):
        voice = notes(("C4", F(3, 8)), ("D4", F(1, 12)), ("E4", F(1, 12)), ("F4", F(1, 12)), ("G4", F(3, 4)),
                      ("A4", F(5, 16)), (None, F(1, 4)), (("C2", "B4"), F(1)))
        root = self.parse(Score([voice, notes(("C3", F(7, 4)))], meter=(3, 4)))
        divisions = int(root.find(".//divisions").text)
        bar = F(3, 4) * 4 * divisions
        for part in root.iter("part"):
            for measure in part.iter("measure"):
                per_staff = {}
                for note in measure.iter("note"):
                    if note.find("chord") is None:
                        staff = note.findtext("staff", "1")
                        per_staff[staff] = per_staff.get(staff, 0) + int(note.findtext("duration"))
                self.assertTrue(all(total == bar for total in per_staff.values()),
                                (part.get("id"), measure.get("number"), per_staff))
        self.assertEqual(len(root.find("part").findall("measure")), len(root.findall("part")[1].findall("measure")))

    def test_ties_tuplets_accidentals_microtones(self):
        root = self.parse(Score([notes(("F#4", F(1, 12)), ("F#4", F(1, 12)), ("F4", F(1, 12)),
                                       ("C4+50c", F(7, 8)), ("C#4", F(3, 8)))]))  # C4+50c crosses the barline
        steps = root.findall(".//note")
        self.assertEqual([n.findtext("accidental") for n in steps[:4]], ["sharp", None, "natural", "quarter-sharp"])
        self.assertEqual(steps[0].find(".//tuplet").get("type"), "start")
        self.assertEqual(steps[2].find(".//tuplet").get("type"), "stop")
        self.assertEqual(steps[0].findtext("time-modification/actual-notes"), "3")
        self.assertEqual(steps[3].findtext("pitch/alter"), "0.5")
        tied = [n.find("tie").get("type") for n in root.iter("note") if n.find("tie") is not None]
        self.assertIn("start", tied)
        self.assertIn("stop", tied)

    def test_mxl(self):
        score = Score([notes(("C4", F(1, 4)))])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "a.mxl")
            export.to_mxl(score, path, Settings())
            with zipfile.ZipFile(path) as z:
                first = z.infolist()[0]
                self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))
                self.assertIn('full-path="score.musicxml"', z.read("META-INF/container.xml").decode())
                ET.fromstring(z.read("score.musicxml").decode().split("\n", 2)[2])


@unittest.skipUnless(os.environ.get("SERIOUS_MUSICXML_XSD") and shutil.which("xmllint"),
                     "set SERIOUS_MUSICXML_XSD to a local musicxml.xsd (see the README) to run")
class MusicXmlSchemaTest(unittest.TestCase):
    """Validate exports against the official MusicXML 4.0 schema with xmllint."""

    def test_exports_are_valid(self):
        scores = {
            "tuplets-ties-meter": Score([notes(("C4", F(3, 8)), ("D4", F(1, 12)), ("E4", F(1, 12)), ("F4", F(1, 12)),
                                               ("G4", F(3, 4)), ("C4+50c", F(5, 16)), (None, F(1, 4)),
                                               ("A4", F(1, 20)), ("B4", F(19, 20)))], meter=(5, 8)),
            "grand-staff-voices": Score([notes((("C2", "E4", "Bb4-31c", "G5"), F(2))), notes(("C3", F(7, 4)))]),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, score in scores.items():
                path = os.path.join(directory, name + ".musicxml")
                with open(path, "w") as f:
                    f.write(export.to_musicxml(score, Settings()))
                result = subprocess.run(["xmllint", "--noout", "--nonet", "--schema",
                                         os.environ["SERIOUS_MUSICXML_XSD"], path],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)


class LilyPondTest(unittest.TestCase):
    SCORE = Score([notes(("C4", F(3, 8)), ("D4", F(1, 12)), ("E4", F(1, 12)), ("F4", F(1, 12)),
                         ("G4", F(3, 4)), ("C4+50c", F(5, 16)), (None, F(1, 4))),
                   notes((("C2", "E4", "G5"), F(2)))], meter=(3, 4))

    def test_source(self):
        text = export.to_lilypond(self.SCORE, Settings())
        self.assertIn("\\time 3/4", text)
        self.assertIn("\\tuplet 3/2 { d'8 e'8 f'8 }", text)
        self.assertIn("cih'", text)        # quarter tone
        self.assertIn("\\new PianoStaff", text)
        self.assertIn("g'8~ |", text)       # tied over the barline

    @unittest.skipUnless(shutil.which("lilypond"), "LilyPond not installed")
    def test_lilypond_compiles_it_without_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "score.ly")
            with open(path, "w") as f:
                f.write(export.to_lilypond(self.SCORE, Settings()))
            result = subprocess.run(["lilypond", "-l", "WARNING", "-o", os.path.join(directory, "score"), path],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr.strip(), "")
            self.assertTrue(os.path.exists(os.path.join(directory, "score.pdf")))


class TextTest(unittest.TestCase):
    def test_rows(self):
        text = export.to_text(Score([notes(("C4", F(1, 4)), (None, F(1, 8)), (("E4", "G4"), F(3, 8)))]), Settings())
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("# Serious"))
        self.assertEqual(lines[1].split("\t"), ["voice", "bar:beat", "onset", "duration", "pitch", "midi", "hz"])
        self.assertEqual(lines[2].split("\t"), ["1", "1:1", "0", "q", "C4", "60", "261.63"])
        self.assertEqual(lines[3].split("\t")[3:5], ["e", "rest"])
        self.assertEqual(lines[4].split("\t")[1:6], ["1:2.5", "1.5", "q.", "E4 G4", "64 67"])


class CliExportTest(unittest.TestCase):
    def test_out_by_extension(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [os.path.join(d, f"row.{ext}") for ext in ("mid", "ly", "musicxml", "mxl", "txt", "wav")]
            args = ["play", "C4", "E4", "G4", "--rhythm", "q e e"]
            for path in paths:
                args += ["--out", path]
            code, _, err = run(*args)
            self.assertEqual(code, 0, err)
            for path in paths:
                self.assertGreater(os.path.getsize(path), 0, path)
            with open(paths[4]) as f:
                self.assertEqual([line.split("\t")[3] for line in f.read().splitlines()[2:]], ["q", "e", "e"])

    def test_isorhythm_exports_every_voice(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "iso.mid")
            code, _, err = run("isorhythm", "--color", "C4 D4 E4", "--talea", "q e", "--color", "C3",
                               "--talea", "h", "--out", path)
            self.assertEqual(code, 0, err)
            with open(path, "rb") as f:
                _, _, tracks = read_midi(f.read())
            self.assertEqual(len(tracks), 3)  # tempo track + 2 voices

    def test_unknown_extension(self):
        code, _, err = run("play", "C4", "--out", "x.pdf")
        self.assertEqual(code, 2)
        self.assertIn(".musicxml", err)


if __name__ == "__main__":
    unittest.main()
