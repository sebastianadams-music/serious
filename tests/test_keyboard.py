import os
import select
import sys
import time
import unittest

try:
    import curses  # noqa: F401
    from serious import keyboard
except ImportError:  # pragma: no cover - no curses on this platform
    keyboard = None

from serious import synth
from serious.pitch import Settings


@unittest.skipIf(keyboard is None, "curses not available")
class LayoutTest(unittest.TestCase):
    def test_every_key_is_clickable_at_its_centre(self):
        keys = keyboard.layout(2, 2)
        self.assertEqual(sorted(k.pc for k in keys), list(range(12)))
        for key in keys:
            self.assertEqual(keyboard.key_at(keys, key.x + key.w // 2, key.y + key.h - 1), key.pc)

    def test_black_keys_sit_on_top(self):
        keys = keyboard.layout(0, 0)
        boundary = keyboard.WHITE_W  # between C and D
        self.assertEqual(keyboard.key_at(keys, boundary, 1), 1)                    # C#
        self.assertEqual(keyboard.key_at(keys, boundary - 1, keyboard.WHITE_H - 1), 0)  # C below it
        self.assertIsNone(keyboard.key_at(keys, 3, keyboard.WHITE_H))              # under the keyboard


@unittest.skipIf(keyboard is None, "curses not available")
class RowTest(unittest.TestCase):
    def test_editing(self):
        row = keyboard.Row()
        self.assertTrue(row.add(0))
        self.assertTrue(row.add(4))
        self.assertFalse(row.add(0))  # no repeats in a row
        self.assertTrue(row.add(7))
        row.retrograde()
        self.assertEqual(row.pcs, [7, 4, 0])
        row.invert()
        self.assertEqual(row.pcs, [7, 10, 2])
        row.undo()
        self.assertEqual(row.intervals(), [3])
        row.clear()
        self.assertEqual(row.pcs, [])

    def test_all_interval(self):
        lyric_suite = keyboard.Row([5, 4, 0, 9, 7, 2, 8, 1, 3, 6, 10, 11])  # Berg
        self.assertTrue(lyric_suite.all_interval())
        self.assertFalse(keyboard.Row(list(range(12))).all_interval())

    def test_typing(self):
        with synth.Player(mute=True) as player:
            app = keyboard.KeyboardApp(Settings(), player, "sine", 0.1, 0.5, 4)
            app.handle(ord("a"))
            app.handle(ord("d"))
            self.assertEqual(app.row.pcs, [0, 4])
            app.handle(ord("a"))
            self.assertIn("already", app.message)
            app.handle(127)
            self.assertEqual(app.row.pcs, [0])
            app.handle(ord("j"))
            self.assertEqual(app.handle(13), [0, 11])  # Enter returns the row
            self.assertIs(app.handle(ord("q")), False)   # q quits

    def test_sgr_mouse_reports(self):
        with synth.Player(mute=True) as player:
            app = keyboard.KeyboardApp(Settings(), player, "sine", 0.1, 0.5, 4)
            e = next(k for k in app.keys if k.pc == 4)
            x, y = e.x + 3 + 1, e.y + e.h - 1 + 1  # SGR coordinates are 1-based
            app.handle_escape(f"[<0;{x};{y}m")       # a release: ignored
            app.handle_escape(f"[<2;{x};{y}M")       # right button: ignored
            app.handle_escape(f"[<64;{x};{y}M")      # scroll wheel: ignored
            self.assertEqual(app.row.pcs, [])
            app.handle_escape(f"[<0;{x};{y}M")       # left press
            app.handle_escape(f"[<4;{x};{y}M")       # shift + left press still counts (already chosen)
            self.assertEqual(app.row.pcs, [4])
            self.assertIn("already", app.message)
            self.assertIsNone(app.handle_escape("[Z"))
            self.assertIn("unrecognised", app.message)
            self.assertIs(app.handle_escape(""), False)  # Esc on its own quits


@unittest.skipIf(keyboard is None or not hasattr(os, "fork"), "needs curses and a pty")
class EndToEndTest(unittest.TestCase):
    """Run `serious keyboard` in a pseudo-terminal, click keys with the mouse, read the piped row.

    Clicks are sent in both encodings: SGR (what modern terminals send once asked
    with mode 1006) and the older X10 one.
    """

    def test_click_a_row_and_pipe_it(self):
        import fcntl
        import pty
        import struct
        import termios

        read_end, write_end = os.pipe()
        pid, fd = pty.fork()
        if pid == 0:  # child: stdout is a pipe, the terminal is the pty
            os.dup2(write_end, 1)
            os.environ["TERM"] = "xterm-256color"
            os.execv(sys.executable, [sys.executable, "-m", "serious", "keyboard", "--mute", "--to", "pc"])
        os.close(write_end)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 120, 0, 0))
        screen = self._read_until(fd, b"row builder")
        screen += self._read_until(fd, b"", 0.3)
        self.assertIn(b"row builder", screen)

        self.assertIn(b"\x1b[?1006h", screen)  # asked the terminal for SGR mouse reports

        def click_x10(x, y):  # press + release
            os.write(fd, b"\x1b[M" + bytes([32, 33 + x, 33 + y]) + b"\x1b[M" + bytes([35, 33 + x, 33 + y]))
            self._read_until(fd, b"", 0.3)

        def click_sgr(x, y):
            os.write(fd, f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m".encode())
            self._read_until(fd, b"", 0.3)

        keys = {k.pc: k for k in keyboard.layout(*keyboard.ORIGIN)}
        for pc, click in ((0, click_sgr), (11, click_x10), (3, click_sgr), (6, click_x10)):
            key = keys[pc]
            click(key.x + key.w // 2, key.y + key.h - 1)
        os.write(fd, b"\r")
        self._read_until(fd, b"", 1.0)
        _, status = os.waitpid(pid, 0)
        output = os.read(read_end, 1024).decode()
        os.close(read_end)
        os.close(fd)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertEqual(output.strip(), "0 11 3 6")

    @staticmethod
    def _read_until(fd, marker, timeout=5.0):
        data, end = b"", time.monotonic() + timeout
        while time.monotonic() < end and (not marker or marker not in data):
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    data += os.read(fd, 65536)
                except OSError:
                    break
        return data


if __name__ == "__main__":
    unittest.main()
