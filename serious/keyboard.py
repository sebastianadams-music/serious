"""Interactive one-octave keyboard for entering a twelve-tone row (curses)."""

from __future__ import annotations

import curses
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import pcset, synth
from .pitch import Pitch, Settings, format_pitch, midi_to_hz

WHITE_PCS = [0, 2, 4, 5, 7, 9, 11]
BLACK_PCS = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}  # black key -> index of the white key to its left
SHORTCUTS = {"a": 0, "w": 1, "s": 2, "e": 3, "d": 4, "f": 5, "t": 6, "g": 7, "y": 8, "h": 9, "u": 10, "j": 11}
SHORTCUT_OF = {pc: key for key, pc in SHORTCUTS.items()}

WHITE_W, WHITE_H = 7, 10   # white keys share their border columns
BLACK_W, BLACK_H = 5, 6
KEYBOARD_W = 7 * WHITE_W + 1
ORIGIN = (2, 2)            # top-left corner of the keyboard
MIN_W, MIN_H = KEYBOARD_W + 4, 24
MATRIX_GAP = 5
FLASH_SECONDS = 0.25

# Mouse: ask for button reporting (1000) in SGR encoding (1006), and decode it ourselves.
# Terminals that ignore 1006 send the old X10 encoding, which ncurses decodes (KEY_MOUSE).
MOUSE_ON, MOUSE_OFF = "\033[?1000h\033[?1006h", "\033[?1006l\033[?1000l"
_SGR_MOUSE = re.compile(r"\[<(\d+);(\d+);(\d+)([Mm])")

HELP = [
    "click a key, or type  a w s e d f t g y h u j",
    "⌫ undo · c clear · r retrograde · i invert · p play row · ⏎ done · q quit",
]


class KeyboardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Key:
    pc: int
    x: int
    y: int
    w: int
    h: int
    black: bool


def layout(ox: int, oy: int) -> list[Key]:
    """Key rectangles, black keys first (they sit on top, so they win hit tests)."""
    blacks = [Key(pc, ox + (left + 1) * WHITE_W - BLACK_W // 2, oy, BLACK_W, BLACK_H, True)
              for pc, left in BLACK_PCS.items()]
    whites = [Key(pc, ox + i * WHITE_W, oy, WHITE_W + 1, WHITE_H, False) for i, pc in enumerate(WHITE_PCS)]
    return blacks + whites


def key_at(keys: list[Key], x: int, y: int) -> int | None:
    for key in keys:
        if key.x <= x < key.x + key.w and key.y <= y < key.y + key.h:
            return key.pc
    return None


@dataclass
class Row:
    pcs: list[int] = field(default_factory=list)

    def add(self, pc: int) -> bool:
        if pc in self.pcs:
            return False
        self.pcs.append(pc)
        return True

    def undo(self) -> None:
        if self.pcs:
            self.pcs.pop()

    def clear(self) -> None:
        self.pcs.clear()

    def retrograde(self) -> None:
        self.pcs.reverse()

    def invert(self) -> None:
        if self.pcs:
            first = self.pcs[0]
            self.pcs = [(2 * first - pc) % 12 for pc in self.pcs]

    @property
    def complete(self) -> bool:
        return len(self.pcs) == 12

    def intervals(self) -> list[int]:
        return [(b - a) % 12 for a, b in zip(self.pcs, self.pcs[1:])]

    def all_interval(self) -> bool:
        return self.complete and sorted(self.intervals()) == list(range(1, 12))


class KeyboardApp:
    def __init__(self, settings: Settings, player: synth.Player, wave: str, duration: float,
                 volume: float, octave: int):
        self.settings = settings
        self.player = player
        self.wave, self.duration, self.volume = wave, duration, volume
        self.base_midi = 12 * (octave + 1)
        self.row = Row()
        self.keys = layout(*ORIGIN)
        self.message, self.message_is_warning = "", False
        self.flash_pc, self.flash_until = None, 0.0
        self.sounds = {pc: player.load(self._render([self._freq(pc)], duration)) for pc in range(12)}

    # ---------------------------------------------------------------- sound

    def _freq(self, pc: int) -> float:
        return midi_to_hz(self.base_midi + pc, self.settings.a4)

    def _render(self, freqs: list[float], duration: float) -> list[float]:
        notes = synth.sequence(freqs, together=False, duration=duration)
        return synth.render(notes, self.wave, self.volume)

    def play_row(self) -> None:
        if not self.row.pcs:
            return self.say("the row is empty", warning=True)
        freqs = [self._freq(pc) for pc in self.row.pcs]
        self.player.start(self.player.load(self._render(freqs, min(self.duration, 0.4))))
        self.say("playing the row")

    # ---------------------------------------------------------------- input

    def name(self, pc: int) -> str:
        return format_pitch(Pitch(60 + pc, has_octave=False), "name", self.settings)

    def say(self, message: str, warning: bool = False) -> None:
        self.message, self.message_is_warning = message, warning

    def press(self, pc: int) -> None:
        self.player.start(self.sounds[pc])
        self.flash_pc, self.flash_until = pc, time.monotonic() + FLASH_SECONDS
        if self.row.add(pc):
            if self.row.all_interval():
                self.say("complete — an all-interval row! ⏎ to finish")
            elif self.row.complete:
                self.say("row complete — ⏎ to finish")
            else:
                self.say(f"added {self.name(pc)}")
        else:
            self.say(f"{self.name(pc)} is already in the row (⌫ to undo)", warning=True)

    def click(self, x: int, y: int) -> None:
        pc = key_at(self.keys, x, y)
        if pc is not None:
            self.press(pc)

    def handle_escape(self, sequence: str) -> bool | None:
        """Handle what followed an ESC: nothing (quit), an SGR mouse report, or something unknown."""
        if not sequence:
            return False
        if m := _SGR_MOUSE.fullmatch(sequence):
            button, x, y, kind = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
            if kind == "M" and button & 0b11100011 == 0:  # left-button press, ignoring modifier keys
                self.click(x - 1, y - 1)
            return None
        self.say(f"unrecognised input: ESC {sequence!r}", warning=True)
        return None

    def handle(self, ch: int) -> list[int] | bool | None:
        """Process one key/mouse event; returns the row when done, False to quit."""
        if ch == curses.KEY_MOUSE:  # X10-encoded mouse report, decoded by ncurses
            try:
                _, x, y, _, state = curses.getmouse()
            except curses.error:
                return None
            if state & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                self.click(x, y)
            return None
        if ch in (curses.KEY_ENTER, 10, 13):
            if self.row.pcs:
                return self.row.pcs
            self.say("click some keys first", warning=True)
            return None
        if ch in (curses.KEY_BACKSPACE, curses.KEY_DC, 127, 8):
            self.row.undo()
            self.say("undone")
            return None
        if not 0 <= ch < 256:
            return None
        key = chr(ch).lower()
        if key in SHORTCUTS:
            self.press(SHORTCUTS[key])
        elif key == "z":
            self.row.undo()
            self.say("undone")
        elif key == "c":
            self.row.clear()
            self.say("cleared")
        elif key == "r":
            self.row.retrograde()
            self.say("retrograde")
        elif key == "i":
            self.row.invert()
            self.say("inverted around the first note")
        elif key in ("p", " "):
            self.play_row()
        elif key == "q":
            return False
        return None

    def run(self, screen) -> list[int] | None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)
        screen.keypad(True)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)  # report presses immediately
        screen.timeout(50)
        self.colors = _init_colors()
        if not self.player.available:
            self.say("no audio player found: the keyboard is silent", warning=True)
        screen.refresh()
        _write_terminal(MOUSE_ON)
        try:
            while True:
                self.draw(screen)
                ch = screen.getch()
                if ch == -1:
                    continue
                result = self.handle_escape(_read_sequence(screen)) if ch == 27 else self.handle(ch)
                if result is False:
                    return None
                if result:
                    return list(result)
        finally:
            _write_terminal(MOUSE_OFF)

    # ---------------------------------------------------------------- drawing

    def draw(self, screen) -> None:
        screen.erase()
        height, width = screen.getmaxyx()
        if height < MIN_H or width < MIN_W:
            _put(screen, 0, 0, f"Make the window at least {MIN_W}×{MIN_H} (it is {width}×{height}).")
            screen.refresh()
            return
        c = self.colors
        _put(screen, 0, 2, " SERIOUS ", c["title"] | curses.A_BOLD)
        _put(screen, 0, 12, "row builder", curses.A_BOLD)
        self._draw_keyboard(screen)
        self._draw_info(screen, ORIGIN[1] + WHITE_H + 2)
        if width >= ORIGIN[0] + KEYBOARD_W + MATRIX_GAP + 54:
            self._draw_matrix(screen, ORIGIN[0] + KEYBOARD_W + MATRIX_GAP, ORIGIN[1])
        for i, line in enumerate(HELP):
            _put(screen, height - len(HELP) + i, 2, line, curses.A_DIM)
        screen.refresh()

    def _key_attr(self, key: Key) -> int:
        c = self.colors
        if key.pc == self.flash_pc and time.monotonic() < self.flash_until:
            return c["flash"]
        if key.pc in self.row.pcs:
            return c["chosen"]
        return c["black"] if key.black else c["white"]

    def _draw_keyboard(self, screen) -> None:
        ox, oy = ORIGIN
        whites = [k for k in self.keys if not k.black]
        blacks = [k for k in self.keys if k.black]
        for key in whites:
            attr = self._key_attr(key)
            for dy in range(key.h):
                _put(screen, key.y + dy, key.x + 1, " " * (WHITE_W - 1), attr)
            self._label(screen, key, key.y + WHITE_H - 2, self.name(key.pc), attr | curses.A_BOLD)
            self._label(screen, key, key.y + BLACK_H + 1, self._order(key.pc), attr | curses.A_BOLD)
        border = self.colors["white"]
        for i in range(8):  # key borders, drawn in the white-key colours so they show on any theme
            for dy in range(WHITE_H):
                _put(screen, oy + dy, ox + i * WHITE_W, "│", border)
        for key in blacks:
            attr = self._key_attr(key)
            for dy in range(key.h):
                _put(screen, key.y + dy, key.x, " " * key.w, attr)
            self._label(screen, key, key.y + BLACK_H - 2, self.name(key.pc), attr | curses.A_BOLD)
            self._label(screen, key, key.y + 1, self._order(key.pc), attr | curses.A_BOLD)
        for key in self.keys:  # typing shortcuts under the keyboard
            centre = key.x + key.w // 2
            _put(screen, oy + WHITE_H, centre, SHORTCUT_OF[key.pc], curses.A_DIM)

    def _order(self, pc: int) -> str:
        return str(self.row.pcs.index(pc) + 1) if pc in self.row.pcs else ""

    @staticmethod
    def _label(screen, key: Key, y: int, text: str, attr: int) -> None:
        if text:
            inner_x, inner_w = (key.x, key.w) if key.black else (key.x + 1, WHITE_W - 1)
            _put(screen, y, inner_x + (inner_w - len(text)) // 2, text, attr)

    def _draw_info(self, screen, y: int) -> None:
        c, pcs = self.colors, self.row.pcs
        label = curses.A_BOLD

        def cells(values):
            return "".join(str(v).ljust(4) for v in values)

        _put(screen, y, 2, "row", label)
        _put(screen, y, 13, cells(self.name(pc) for pc in pcs) or "—", c["accent"] | curses.A_BOLD)
        _put(screen, y + 1, 2, "pcs", label)
        _put(screen, y + 1, 13, cells(pcset.compact([pc]) for pc in pcs))
        _put(screen, y + 2, 2, "intervals", label)
        _put(screen, y + 2, 15, cells(pcset.compact([i]) for i in self.row.intervals()))
        _put(screen, y + 3, 2, "missing", label)
        missing = [self.name(pc) for pc in range(12) if pc not in pcs]
        _put(screen, y + 3, 13, " ".join(missing) if missing else "none", curses.A_DIM)
        status = f"{len(pcs)}/12"
        if self.row.complete:
            first, second = pcset.forte_name(pcs[:6]), pcset.forte_name(pcs[6:])
            status += f"   hexachords {first} / {second}"
        _put(screen, y + 4, 2, status, label)
        if self.message:
            _put(screen, y + 6, 2, self.message, c["warning"] if self.message_is_warning else c["accent"])

    def _draw_matrix(self, screen, x: int, y: int) -> None:
        _put(screen, y - 1, x, "matrix", curses.A_BOLD)
        pcs = self.row.pcs
        if len(pcs) < 2:
            _put(screen, y + 1, x, "appears as you build the row", curses.A_DIM)
            return
        grid = pcset.row_matrix(pcs)
        _put(screen, y, x + 5, "".join(f"I{pc}".ljust(4) for pc in grid[0]), curses.A_DIM)
        for i, line in enumerate(grid):
            _put(screen, y + 1 + i, x, f"P{line[0]}".ljust(5), curses.A_DIM)
            _put(screen, y + 1 + i, x + 5, "".join(pcset.compact([pc]).ljust(4) for pc in line),
                 curses.A_BOLD if i == 0 else 0)


def _read_sequence(screen) -> str:
    """The rest of an escape sequence after ESC ('' if ESC was pressed on its own)."""
    screen.timeout(25)
    sequence = ""
    try:
        while len(sequence) < 32:
            ch = screen.getch()
            if ch == -1 or not 0 <= ch < 256:
                break
            sequence += chr(ch)
            if len(sequence) > 1 and (sequence[0] != "[" or chr(ch).isalpha() or ch == ord("~")):
                break  # a CSI sequence ends with a letter (or ~)
    finally:
        screen.timeout(50)
    return sequence


def _write_terminal(sequence: str) -> None:
    """Send a control sequence straight to the terminal, bypassing curses."""
    sys.stdout.flush()
    os.write(sys.stdout.fileno(), sequence.encode())


def _init_colors() -> dict[str, int]:
    attrs = {"white": curses.A_REVERSE, "black": curses.A_NORMAL, "chosen": curses.A_BOLD | curses.A_REVERSE,
             "flash": curses.A_STANDOUT, "title": curses.A_REVERSE, "accent": curses.A_BOLD,
             "warning": curses.A_BOLD}
    if not curses.has_colors():
        return attrs
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    bright_white = 15 if curses.COLORS >= 16 else curses.COLOR_WHITE
    pairs = {
        "white": (curses.COLOR_BLACK, bright_white),
        "black": (bright_white, curses.COLOR_BLACK),
        "chosen": (curses.COLOR_BLACK, curses.COLOR_CYAN),
        "flash": (curses.COLOR_BLACK, curses.COLOR_YELLOW),
        "title": (curses.COLOR_BLACK, curses.COLOR_CYAN),
        "accent": (curses.COLOR_CYAN, background),
        "warning": (curses.COLOR_YELLOW, background),
    }
    for number, (name, (fg, bg)) in enumerate(pairs.items(), start=1):
        curses.init_pair(number, fg, bg)
        attrs[name] = curses.color_pair(number)
    return attrs


def _put(screen, y: int, x: int, text: str, attr: int = 0) -> None:
    """addstr that ignores drawing outside the window (and the bottom-right cell)."""
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width:
        try:
            screen.addstr(y, x, text[: width - x], attr)
        except curses.error:
            pass


@contextmanager
def _terminal():
    """Draw on the terminal even when stdout is piped, keeping the pipe for the result."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        yield
        return
    try:
        tty = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        raise KeyboardError("the keyboard needs an interactive terminal") from None
    sys.stdout.flush()
    saved = os.dup(0), os.dup(1)
    os.dup2(tty, 0)
    os.dup2(tty, 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved[0], 0)
        os.dup2(saved[1], 1)
        for fd in (*saved, tty):
            os.close(fd)


def run(settings: Settings, wave: str = "triangle", duration: float = 0.6, volume: float = 0.5,
        octave: int = 4, mute: bool = False) -> list[int] | None:
    """Show the keyboard; returns the row's pitch classes, or None if the user quit."""
    os.environ.setdefault("ESCDELAY", "25")
    with synth.Player(mute) as player, _terminal():
        app = KeyboardApp(settings, player, wave, duration, volume, octave)
        return curses.wrapper(app.run)
