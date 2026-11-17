"""SpectraMax 250 ("Flash") instrument simulator.

This is a port of ``CSFlashSim`` -- the instrument simulator Molecular Devices
compiled into SOFTmax PRO 1.x (SPF.EXE code segment 69, SOURCES\\TESTINTR.CPP
neighbourhood).  Every behaviour below was read out of the disassembly; the
``seg69:xxxx`` notes give the address of the original routine.

Two personalities:

``faithful=True``
    Behaves exactly like the original: a READ produces all its data instantly,
    SHAKE/CALIBRATE/BEEP/... log error 4 but still answer ``ok``, QUEUE never
    reports more than 3, STATUS always says ``MEAS``.

``faithful=False`` (default)
    Same command set and reply formats, but usable as a stand-in instrument:
    SHAKE, CALIBRATE, BEEP, VOLUME ... are accepted, reads can take real time
    (``realtime=True``), STATUS reports IDLE/MEAS, and the drawer must be
    closed... is *not* enforced (the real instrument closes it by itself).

The class itself is transport-agnostic: ``process("READ")`` returns
``(status_chunk, body_chunk)``.  ``WireSession`` turns that into the byte
stream seen on the serial line.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .errors import SIM_ERRORS

# (name, help) -- seg148:0c4e, 32 entries of 14 bytes; order = dispatch index.
COMMANDS = [
    ("AT", "       Schedules a command to be executed at a later time"),
    ("BAUD", "     Gets or sets the baud rate (1200, 9600,19200 or 38400)"),
    ("BEEP", "     Beeps now or sets the beep state"),
    ("BURNIN", "   Gets or sets the burn-in state"),
    ("CALIBRATE", "Gets or sets the calibrate state"),
    ("CANCEL", "   Cancels the current read"),
    ("CLEAR", "    Clears the error queue or AT queue"),
    ("CLOSE", "    Closes the drawer"),
    ("DIAG", "     Runs a diagnostic"),
    ("DISPLAY", "  Displays text on the lcd"),
    ("ERROR", "    Displays any errors"),
    ("FILTER", "    Displays filters installed"),
    ("HELP", "     Displays this help information"),
    ("MODE", "     Gets or sets the mode (kinetic/endpoint) for the next read"),
    ("OPEN", "     Opens the drawer"),
    ("OPTION", "   Gets various option parameters"),
    ("QUEUE", "    Finds out how many 'plates' of data are waiting to be transferred"),
    ("READ", "     Stars the read"),
    ("REMOTE", "   Gets or sets the remote status (nop) "),
    ("RESET", "    Resets the state of the instrument (just like power on"),
    ("SHAKE", "    Gets or sets the shake state"),
    ("RETRY", "    Resend the last response"),
    ("STATUS", "   TBD"),
    ("STEP", "     Move the plate (nop)"),
    ("STRIP", "    Set the columns to be read"),
    ("STTY", "     Set tty info (nop"),
    ("TEMP", "     Get or set the temperature"),
    ("TIME", "     Get or set the current time"),
    ("TRANSFER", " Transfer waiting 'plates'"),
    ("VER", "      Display the version number of the Flash (hw & sw)"),
    ("VOLUME", "   Get or set the volume in each well"),
    ("WAVELENGTH", "Get or set the wavelengths to be used"),
]
_INDEX = {name: i for i, (name, _) in enumerate(COMMANDS)}

E_UNKNOWN, E_NPARAMS, E_RANGE, E_NOTIMPL, E_OVERFLOW = 1, 2, 3, 4, 5
MAX_ERRORS = 64            # seg69:143b
MAX_ARGS = 6               # seg69:1030
ROWS = 8
COLUMNS = 12
WL_MIN, WL_MAX = 250, 800  # seg69:2e6f / 2e75  (OPTION advertises 250..750)
TEMP_MIN, TEMP_MAX = 15.0, 45.0   # seg148:0e6e / 0e6a
AMBIENT = 21.3             # seg148:0e5e
KINETIC_MIN_INTERVAL = 6.0  # seg148:0e1e
KINETIC_MAX_READS = 10000  # seg69:1c9e

MODE_ENDPOINT, MODE_KINETIC, MODE_SWEEP = 0, 1, 2

OPTION_TEXT = "Simulator\r\n250\t750\t1\r\n15\t45\r\n400\t225\t5\t2\r\n"   # seg69:2eb0
VERSION_TEXT = "Version 0.1 of the simulator\r"                            # seg69:2d59
# What the simulator hands back to the instrument-detection probe (seg69:088b)
PROBE_TEXT = "0.2\rSpectramax Simulator.\r"


def _c_int(text: str) -> int:
    """C ``atol`` semantics: leading whitespace, optional sign, digits; junk -> 0."""
    s = text.lstrip(" \t")
    i = 0
    if i < len(s) and s[i] in "+-":
        i += 1
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    if j == i:
        return 0
    return int(s[:j])


def _c_float(text: str) -> float:
    """C ``atof`` semantics (longest valid numeric prefix; junk -> 0.0)."""
    s = text.lstrip(" \t")
    best = 0.0
    for end in range(len(s), 0, -1):
        try:
            best = float(s[:end])
            break
        except ValueError:
            continue
    return best


@dataclass
class PlateRecord:
    """One 'plate' of data waiting in the instrument's transfer queue."""

    time_s: int
    temperature: float
    wavelengths: list
    first_col: int          # 0-based
    num_cols: int
    # values[wavelength_index][column 0..11][row 0..7]
    values: list = field(default_factory=list)

    def __post_init__(self):
        if not self.values:
            self.values = [[[0.0] * ROWS for _ in range(COLUMNS)] for _ in self.wavelengths]


class FlashSimulator:
    def __init__(self, *, faithful: bool = False, realtime: bool = False,
                 seed: Optional[int] = None,
                 clock: Callable[[], float] = time.time,
                 log: Optional[Callable[[str], None]] = None):
        self.faithful = faithful
        self.realtime = realtime and not faithful
        self.clock = clock
        self.log = log
        self.rng = random.Random(seed)
        self.queue: list[PlateRecord] = []
        self._pending: list[tuple[float, PlateRecord]] = []   # (ready_at, record) -- realtime only
        self.display = ["", ""]
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Power-on state (seg69:0746)."""
        self.errors: list[int] = []
        self.baud = 19200
        self.drawer_closed = False
        self.temp_setpoint = 0.0            # 0 = incubator off
        self.time_base = int(self.clock())
        self.mode = MODE_ENDPOINT
        self.kinetic_interval = 0.0
        self.kinetic_reads = 1
        self.sweep_start, self.sweep_step, self.sweep_count = 250, 1, 1
        self.wavelengths = [405]
        self.filters = [260, 652]
        self.strip_first, self.strip_count = 0, COLUMNS
        self.queue.clear()
        self._pending.clear()
        # extras that only exist in the non-faithful personality
        self.shake = "OFF"
        self.calibrate = "ON"
        self.remote = False

    # ------------------------------------------------------- simulated optics
    def _noise(self, v: float) -> float:
        """seg69:1e76 -- +/-0.025 OD of noise, clamped at 0."""
        v += (self.rng.randint(0, 32767) / 32767.0 - 0.5) * 0.05
        return v if v > 0 else 0.0

    @staticmethod
    def _trapezoid(ramp: float, top: float, centre: float, x: float) -> float:
        """seg69:1ee9 -- 1 on a plateau `top` wide, linear skirts `ramp/2` wide."""
        d = abs(centre - x)
        if top / 2.0 >= d:
            return 1.0
        if (top + ramp) / 2.0 >= d:
            return ((top + ramp) / 2.0 - d) / (ramp / 2.0)
        return 0.0

    def spectrum_value(self, wl: int, row: int, col: int) -> float:
        """seg69:1f95 -- absorbance used for MODE SPECTRUM sweeps.

        A band centred on the nearest multiple of 100 nm (shifted 5 nm per row),
        wider at long wavelengths, weaker at long wavelengths and in high columns.
        """
        top = float(int((wl - 250) / 20))
        centre = float(int((wl + 50.0) / 100.0)) * 100.0 + row * 5
        r = self._trapezoid(10.0, top, centre, float(wl))
        scale = (800.0 - centre) / 550.0
        return r * scale * (2.0 - col / 10.0) + 0.1

    def well_value(self, read_idx: int, wl_idx: int, row: int, col: int) -> float:
        """seg69:208b -- absorbance for endpoint / kinetic reads.

        Wavelengths 700..706 nm select built-in test patterns.
        """
        v = (math.exp((col + row * 2.0 + read_idx) / 10.0) + wl_idx) / 10.0
        wl = self.wavelengths[wl_idx]
        if wl == 700:
            v = -v
        elif wl == 701:
            v = v - read_idx * 0.02
        elif wl == 702:
            v = -(v - read_idx * 0.02)
        elif wl == 703:
            if row * 12 + col == read_idx:
                v = 2.0
        elif wl == 704:
            if row * 12 + col == read_idx:
                v = 0.0
        elif wl == 705:
            v = math.sin(read_idx / 12.0 + wl_idx / 6.0) * (wl_idx + 1)
        elif wl == 706:
            v = self._noise(v)
        return v

    def actual_temperature(self) -> float:
        """seg69:27de -- setpoint (or 21.3 ambient) minus up to ~1 degC of jitter."""
        base = AMBIENT if self.temp_setpoint == 0 else self.temp_setpoint
        return base - self.rng.randint(-32767, 32767) / 32000.0

    # --------------------------------------------------------------- protocol
    def process(self, command: str) -> tuple[str, str]:
        """Handle one command line (without the trailing CR).

        Returns ``(status, body)``: ``status`` is ``"ok\\t"`` or ``"fail\\t<n>"``
        and ``body`` the data text (may be empty).  seg69:09e0.
        """
        if self.log and command.strip("! \t").upper() not in ("TEMP", "STATUS"):
            self.log("Sim got: " + command)
        self._tick()
        line = command.lstrip(" ").upper()
        if line.startswith("!"):
            line = line[1:]
        ok = "ok\t" if self.faithful else "OK\t"
        if line.strip(" \t\r") == "":
            return ok, ""
        name, args = self._split(line)
        self._body = ""
        if name in ("CALIGN", "ADC_DIAG") and not self.faithful:
            self._body = self._service(name)        # factory commands (SPF.EXE seg70)
            return ok, self._body
        idx = _INDEX.get(name, -1)
        err = E_UNKNOWN if idx < 0 else self._dispatch(name, args)
        if err:
            self._push_error(err)
            if self.faithful:
                return f"fail\t{err}", ""
            # SOFTmax PRO reads the number from characters 6..8 of the reply
            # (seg68:4051), which the original simulator's "fail\t<n>" misses.
            return f"FAIL\t {err}", ""
        return ok, self._body

    @staticmethod
    def _split(line: str):
        """Tokenise on space/tab/CR; at most 6 arguments are kept (seg69:0de0)."""
        toks = [t for t in line.replace("\t", " ").replace("\r", " ").split(" ") if t]
        return toks[0], toks[1:1 + MAX_ARGS]

    def _push_error(self, code: int) -> None:
        if len(self.errors) < MAX_ERRORS:      # seg69:141c
            self.errors.append(code)

    def _dispatch(self, name: str, a: list) -> int:
        handler = getattr(self, "_cmd_" + name.lower(), None)
        if handler is not None:
            return handler(a) or 0
        if name == "AT":
            return E_NOTIMPL                  # seg69:13ce
        # BEEP BURNIN CALIBRATE DIAG SHAKE RETRY STEP STTY VOLUME: seg69:13bc
        if self.faithful:
            self._push_error(E_NOTIMPL)       # logged, but the reply is still "ok"
            return 0
        return self._extra(name, a)

    # --- commands the original implements -----------------------------------
    def _cmd_baud(self, a):                   # seg69:1475
        if not a:
            self._body = f"{self.baud}\r"
        else:
            self.baud = _c_int(a[0])

    def _cmd_close(self, a):                  # seg69:14fe
        self.drawer_closed = True

    def _cmd_open(self, a):                   # seg69:1de4
        self.drawer_closed = False

    def _cmd_cancel(self, a):                 # seg69:1521
        self.queue.clear()
        self._pending.clear()

    def _cmd_clear(self, a):                  # seg69:1566
        if not a or a[0] == "ERR" or (not self.faithful and a[0].startswith("ERR")):
            self.errors.clear()
        elif a[0] == "DATA":
            self.queue.clear()
            self._pending.clear()
        # "AT" and anything else: nothing

    def _cmd_error(self, a):                  # seg69:169a
        overflow = len(self.errors) == MAX_ERRORS
        if not a:
            out = f"Overflow error: {E_OVERFLOW}\r" if overflow else "\r"
            for i, code in enumerate(self.errors):
                out += f"{i}) {self._errtext(code)}\r"
            self._body = out
        elif a[0] == "LIST":
            self._body = "\rList of possible errors:\r" + "".join(
                self._errtext(i) + "\r" for i in range(1, 6))
        elif a[0] == "NUMBER":
            out = f"{E_OVERFLOW}\r" if overflow else ""
            self._body = out + "".join(f"{code}\r" for code in self.errors)
        elif a[0][0].isdigit():
            n = _c_int(a[0])
            code = self.errors[n] if n < len(self.errors) else 0
            self._body = f"{n}) {self._errtext(code)} ({code})\r"

    @staticmethod
    def _errtext(code: int) -> str:
        text = SIM_ERRORS.get(code, "No error")
        return text if code == 0 else f"{text} ({code})"

    def _cmd_help(self, a):                   # seg69:19a5
        self._body = "\r" + "".join(f"{n}:{h}\r" for n, h in COMMANDS)

    def _cmd_mode(self, a):                   # seg69:1a5f
        if not a:
            if self.mode == MODE_ENDPOINT:
                self._body = "Endpoint\r"
            elif self.mode == MODE_KINETIC:
                self._body = f"Kinetic\t{int(self.kinetic_interval)}/{self.kinetic_reads}\r"
            else:
                self._body = f"Sweep\t{self.sweep_start}\t{self.sweep_step}\t{self.sweep_count}\r"
            return 0
        kind = a[0][0]
        if kind == "E":
            self.mode, self.kinetic_interval, self.kinetic_reads = MODE_ENDPOINT, 0.0, 1
        elif kind == "K":
            self.mode = MODE_KINETIC          # (set before validation, as in the original)
            if len(a) != 3:
                return E_NPARAMS
            interval = _c_float(a[1])
            if interval < (KINETIC_MIN_INTERVAL if self.faithful else 1):
                return E_RANGE
            reads = _c_int(a[2])
            if not 0 < reads <= KINETIC_MAX_READS:
                return E_RANGE
            self.kinetic_interval, self.kinetic_reads = interval, reads
        elif kind == "S":
            self.mode = MODE_SWEEP
            if len(a) != 4:
                return E_NPARAMS
            start = int(_c_float(a[1]))
            if start < WL_MIN:
                return E_RANGE
            step = _c_int(a[2])
            if step < 1:
                return E_RANGE
            count = _c_int(a[3])
            if count < 1 or step * count + start > WL_MAX:
                return E_RANGE
            self.sweep_start, self.sweep_step, self.sweep_count = start, step, count
        else:
            return E_RANGE
        return 0

    def _cmd_queue(self, a):                  # seg69:1e07
        n = len(self.queue)
        if self.faithful and n > 3:
            n = 3
        self._body = f"{n}\r"

    def _cmd_read(self, a):                   # seg69:2254
        elapsed = int(self.clock()) - self.time_base
        now = self.clock()
        if self.mode in (MODE_ENDPOINT, MODE_KINETIC):
            nreads = self.kinetic_reads if self.mode == MODE_KINETIC else 1
            for r in range(nreads):
                t = elapsed
                if not self.faithful:
                    t = elapsed + int(round(r * self.kinetic_interval))
                rec = PlateRecord(t, self.actual_temperature(), list(self.wavelengths),
                                  self.strip_first, self.strip_count)
                for w in range(len(self.wavelengths)):
                    for col in range(self.strip_first, self.strip_first + self.strip_count):
                        for row in range(ROWS):
                            rec.values[w][col][row] = self.well_value(r, w, row, col)
                self._enqueue(rec, now + self._read_time() + r * self.kinetic_interval)
        else:
            k = 0
            wl = self.sweep_start
            while self.sweep_count * self.sweep_step + self.sweep_start > wl:
                self.wavelengths[0] = wl      # the original clobbers wavelength #1 too
                rec = PlateRecord(elapsed, self.actual_temperature(), [wl],
                                  self.strip_first, self.strip_count)
                for col in range(self.strip_first, self.strip_first + self.strip_count):
                    for row in range(ROWS):
                        rec.values[0][col][row] = self.spectrum_value(wl, row, col)
                k += 1
                self._enqueue(rec, now + k * self._read_time() / 4)
                wl += self.sweep_step
        return 0

    def _read_time(self) -> float:
        # A SpectraMax 250 reads a full plate in ~9 s; scale by columns.
        return 9.0 * self.strip_count / COLUMNS * max(1, len(self.wavelengths))

    def _enqueue(self, rec: PlateRecord, ready_at: float) -> None:
        if self.realtime:
            self._pending.append((ready_at, rec))
        else:
            self.queue.append(rec)

    def _tick(self) -> None:
        if self._pending:
            now = self.clock()
            ready = [p for p in self._pending if p[0] <= now]
            self._pending = [p for p in self._pending if p[0] > now]
            self.queue.extend(rec for _, rec in sorted(ready, key=lambda p: p[0]))

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def _cmd_reset(self, a):                  # seg69:259f
        self.reset()

    def _cmd_strip(self, a):                  # seg69:25cc
        if not a:
            self._body = f"{self.strip_first + 1}\t{self.strip_count}\r"
            return 0
        if len(a) < 2:
            return E_NPARAMS
        first = _c_int(a[0]) - 1
        count = _c_int(a[1])
        if first < 0 or first >= COLUMNS or first + count > COLUMNS:
            return E_RANGE
        if not self.faithful and count < 1:
            return E_RANGE
        self.strip_first, self.strip_count = first, count
        return 0

    def _cmd_filter(self, a):                 # seg69:26da
        if not a:
            self._body = "".join(f"{f}\r\n" for f in self.filters) + "\r\n"
            return 0
        for i in range(2):
            v = _c_int(a[i]) if i < len(a) else 0
            if v != 0 and not WL_MIN <= v <= WL_MAX:
                return E_RANGE
            self.filters[i] = v
        return 0

    def _cmd_temp(self, a):                   # seg69:285c
        if not a:
            self._body = f"{self.temp_setpoint:.1f}\t{self.actual_temperature():.1f}\r"
            return 0
        t = _c_float(a[0])
        if TEMP_MIN <= t <= TEMP_MAX or t == 0:
            self.temp_setpoint = t
            return 0
        return E_RANGE

    def _cmd_time(self, a):                   # seg69:2958
        now = int(self.clock())
        if not a:
            self._body = f"{now - self.time_base}\r"
        else:
            self.time_base = now - _c_int(a[0])

    def _cmd_transfer(self, a):               # seg69:2a36
        n = _c_int(a[0]) if a else 1
        if n == 0:
            n = 1
        if n > len(self.queue):
            return E_RANGE
        out = []
        for _ in range(n):
            rec = self.queue.pop(0)
            out.append(self.format_record(rec))
        self._body = "".join(out)
        return 0

    def format_record(self, rec: PlateRecord) -> str:
        first, count = self.strip_first, self.strip_count
        if not self.faithful:               # the original uses the *current* strip here
            first, count = rec.first_col, rec.num_cols
        s = f"{rec.time_s}\t{rec.temperature:.1f}\r"
        s += "L:\t" + "".join(f"{wl}\t" for wl in rec.wavelengths) + "\r"
        for w, wl in enumerate(rec.wavelengths):
            s += f"L:\t{wl}\r"
            for col in range(first, first + count):
                s += f"{col + 1}:\t" + "".join(f"{rec.values[w][col][row]:.4f}\t"
                                               for row in range(ROWS)) + "\r"
            s += "\r"
        return s

    def _cmd_ver(self, a):                    # seg69:2d77
        self._body = VERSION_TEXT

    def _cmd_wavelength(self, a):             # seg69:2daa
        if not a:
            self._body = "".join(f"{wl}\t" for wl in self.wavelengths) + "\r"
            return 0
        new = []
        for tok in a:
            wl = _c_int(tok)
            if not WL_MIN <= wl <= WL_MAX:
                if self.faithful:             # the count and earlier entries are already stored
                    self.wavelengths = (new + self.wavelengths[len(new):] + [405] * 6)[:len(a)]
                return E_RANGE
            new.append(wl)
        self.wavelengths = new
        return 0

    def _cmd_option(self, a):                 # seg69:2edb
        self._body = OPTION_TEXT

    def _cmd_status(self, a):                 # seg69:2f3c
        drawer = "CLOSED\t" if self.drawer_closed else "OPEN\t"
        state = "MEAS\t" if (self.faithful or self.busy) else "IDLE\t"
        self._body = drawer + state + "\r\n"

    def _cmd_display(self, a):                # seg69:2feb (no-op)
        return 0

    def _cmd_remote(self, a):                 # seg69:3004 (no-op)
        if a:
            self.remote = a[0] == "ON"
        return 0

    # --- factory service commands --------------------------------------------
    def _service(self, name: str) -> str:
        """Replies to the factory commands the host's test interface sends (seg70).

        The original simulator does not know them and their real reply format is not
        documented: these replies are invented so the program can be tried."""
        if name == "CALIGN":
            return "ALIGN 0.00 OK\r"
        # Laid out exactly as the host parses it (driver.parse_adc_diag); the labels and
        # numbers are invented.
        lines = ["ADC DIAGNOSTIC (simulated)"]
        w = (8, 8, 7, 7, 9, 9, 9, 9)
        for ch in range(8):
            vals = [30000 - 40 * (k + ch) + self.rng.randint(-25, 25) for k in range(8)]
            label = f"{ch + 1} "
            lines.append(label + "".join(f"{vals[k]:{w[k]}d}" for k in (0, 2, 4, 6)))
            lines.append(label + "".join(f"{vals[k]:{w[k]}d}" for k in (1, 3, 5, 7)))
        for i, label in enumerate(("D1", "D2", "D3", "D4", "R1 ", "R2 ")):
            lines.append(label + f"{1000 + i:8d}{2000 + i:8d}{300 + i:7d}")
        return "\r\n".join(lines) + "\r\n"

    # --- commands the original leaves "not implemented" ---------------------
    def _extra(self, name: str, a: list) -> int:
        if name == "SHAKE":
            if not a:
                self._body = f"{self.shake}\r"
            else:
                self.shake = " ".join(a)
        elif name == "CALIBRATE":
            if not a:
                self._body = f"{self.calibrate}\r"
            elif a[0] in ("ON", "OFF", "ONCE"):
                self.calibrate = a[0]
            else:
                return E_RANGE
        elif name == "RETRY":
            return E_NOTIMPL
        # BEEP BURNIN DIAG STEP STTY VOLUME: accepted, no effect
        return 0


class WireSession:
    """Byte-level framing around a :class:`FlashSimulator`.

    Host -> instrument:  ``!COMMAND args\\r``
    Instrument -> host:  ``<status>\\r>`` and, when the command produced data,
    a second chunk ``<lead><body>>``.

    SOFTmax PRO reads the status chunk up to the first ``>`` (SPF.EXE
    seg68:37ae), then -- for commands that return data -- reads a second chunk up
    to the next ``>`` and throws away its first two characters (seg68:3d87).
    ``lead`` supplies those two characters.
    """

    def __init__(self, sim: FlashSimulator, *, lead: str = "\r\n", echo: bool = False):
        self.sim = sim
        self.lead = lead
        self.echo = echo
        self._buf = bytearray()
        self._last = b""

    def feed(self, data: bytes) -> bytes:
        """Give the session received bytes; returns the bytes to transmit."""
        out = bytearray()
        for b in data:
            if self.echo:
                out.append(b)
            if b in (0x0D, 0x0A):
                if b == 0x0A and not self._buf:
                    continue
                line = self._buf.decode("latin-1")
                self._buf.clear()
                out += self.reply(line)
            else:
                self._buf.append(b)
        return bytes(out)

    def reply(self, line: str) -> bytes:
        if line.strip("! \t").upper() == "RESEND":
            return self._last
        status, body = self.sim.process(line)
        if line.strip("! \t").upper() == "ADC_DIAG" and body:
            # the factory host reads this reply as one data block with no status first
            # (SPF.EXE seg70:0f6c), so it comes alone
            self._last = (self.lead + body + ">").encode("latin-1")
            return self._last
        text = status + "\r>"
        if body:
            text += self.lead + body + ">"
        self._last = text.encode("latin-1")
        return self._last
