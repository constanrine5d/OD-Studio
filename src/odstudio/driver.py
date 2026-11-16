"""High-level driver for the SpectraMax 250.

The command sequences follow what SOFTmax PRO 1.x itself sends (class
``CSFlashInterface``, SPF.EXE segment 68); the addresses in comments point at
the original routines.

    from odstudio import SpectraMax

    with SpectraMax("COM3") as sm:          # or "/dev/ttyUSB0", or "sim"
        print(sm.info.version, sm.temperature())
        plate = sm.read_endpoint([405, 650])
        print(plate.well("A1", 405))
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence, Union

from . import protocol as P
from .errors import (CommandFailed, ProtocolError, ReplyTimeout, SpectraMaxError,
                     TransportError)
from .protocol import Options, PlateData, Status
from .transport import DEFAULT_BAUD, Transport, open_transport

# SOFTmax PRO waits 60 "ticks" of 16 ms between bytes (seg68:17f3), 6 ticks
# for the first probe of a connection attempt (seg68:34dc).
REPLY_TIMEOUT = 1.0
PROBE_TIMEOUT = 0.25
POLL_INTERVAL = 0.64          # CSReaderChore period, 40 ticks (seg65:0153)
MAX_WAVELENGTHS = 6
MIN_KINETIC_INTERVAL = 2      # host default for OPTION field 10 (seg68:17de..)

Trace = Optional[Callable[[str, bytes], None]]


@dataclass
class InstrumentInfo:
    version: str = ""
    options: Options = field(default_factory=Options)

    @property
    def model(self) -> str:
        return self.options.name


@dataclass
class ReadSettings:
    """Everything that defines one read.

    ``mode``            "endpoint", "kinetic" or "spectrum"
    ``wavelengths``     1..6 wavelengths in nm (endpoint / kinetic)
    ``first_column``    first plate column to read, 1..12
    ``num_columns``     how many columns (strips of 8 wells) to read
    ``interval``        kinetic: seconds between reads
    ``reads``           kinetic: number of reads
    ``start, stop, step`` spectrum: sweep range in nm (stop inclusive)
    ``shake_before``    seconds of shaking before the first read (0 = none)
    ``shake_between``   kinetic: seconds of shaking before each later read
    ``autocalibrate``   let the instrument run its air calibration
    """

    mode: str = "endpoint"
    wavelengths: Sequence[Union[int, str]] = (405,)
    first_column: int = 1
    num_columns: int = 12
    interval: int = 0
    reads: int = 1
    start: int = 0
    stop: int = 0
    step: int = 10
    shake_before: int = 0
    shake_between: int = 0
    autocalibrate: bool = True

    @property
    def expected_plates(self) -> int:
        if self.mode == "kinetic":
            return self.reads
        if self.mode == "spectrum":
            return (self.stop - self.start) // self.step + 1
        return 1

    def validate(self, opt: Optional[Options] = None) -> None:
        lo, hi = (opt.wavelength_min, opt.wavelength_max) if opt else (250, 750)
        if self.mode not in ("endpoint", "kinetic", "spectrum"):
            raise ValueError(f"unknown mode {self.mode!r}")
        if not 1 <= self.first_column <= P.COLUMNS or self.num_columns < 1 \
                or self.first_column + self.num_columns - 1 > P.COLUMNS:
            raise ValueError("columns must lie within 1..12")
        if self.shake_before < 0 or self.shake_between < 0:
            raise ValueError("shake times cannot be negative")
        if self.mode == "spectrum":
            if self.step < 1 or self.stop < self.start:
                raise ValueError("spectrum needs start <= stop and step >= 1")
            if not lo <= self.start <= hi or not lo <= self.stop <= hi:
                raise ValueError(f"spectrum range must lie within {lo}..{hi} nm")
            return
        wls = list(self.wavelengths)
        if not 1 <= len(wls) <= MAX_WAVELENGTHS:
            raise ValueError(f"1 to {MAX_WAVELENGTHS} wavelengths are allowed")
        for w in wls:
            n = int(str(w).lstrip("Ff"))
            if not lo <= n <= hi:
                raise ValueError(f"wavelength {w} outside {lo}..{hi} nm")
        if self.mode == "kinetic":
            if self.reads < 1:
                raise ValueError("kinetic needs at least 1 read")
            if self.interval < MIN_KINETIC_INTERVAL:
                raise ValueError(f"kinetic interval must be >= {MIN_KINETIC_INTERVAL} s")
            if self.shake_between >= self.interval:
                raise ValueError("shake_between must be shorter than the interval")


class SpectraMax:
    def __init__(self, port: str = "sim", *, baudrate: int = DEFAULT_BAUD,
                 timeout: float = REPLY_TIMEOUT, transport: Optional[Transport] = None,
                 trace: Trace = None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.trace = trace
        self._tx: Optional[Transport] = transport
        self._rx = bytearray()
        self.info = InstrumentInfo()
        self.connected = False
        self.remote = False
        self.time_offset = 0.0   # wall clock minus instrument clock, set by a read

    # ------------------------------------------------------------ lifecycle
    def __enter__(self) -> "SpectraMax":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def open_port(self) -> None:
        if self._tx is None:
            self._tx = open_transport(self.port, self.baudrate)

    def connect(self, *, remote: bool = True, display: Optional[Sequence[str]] = None,
                attempts: int = 3) -> InstrumentInfo:
        """Open the port, check an instrument answers, read its identity.

        Same handshake as SOFTmax PRO (seg68:347c, 68:2684): empty command,
        REMOTE ON, DISPLAY, VER, OPTION.
        """
        self.open_port()
        last: Optional[Exception] = None
        for _ in range(max(1, attempts)):
            try:
                self.ping()
                last = None
                break
            except (ReplyTimeout, CommandFailed) as e:
                last = e
        if last is not None:
            raise SpectraMaxError(
                f"no SpectraMax answering on {self.port} ({last}). Check the cable "
                "(RS-232, 9600 baud 8N1), that the instrument is on, and the port name."
            ) from last
        self.connected = True
        if remote:
            self.set_remote(True)
            if display is not None:
                self.display(*display)
        version = P.lines(self.query("VER"))
        self.info.version = version[0].strip() if version else ""
        self.info.options = P.parse_option(self.query("OPTION"))
        return self.info

    def disconnect(self) -> None:
        if self._tx is None:
            return
        try:
            if self.connected and self.remote:
                self.command("REMOTE OFF")        # seg68:3e98, errors ignored there too
        except SpectraMaxError:
            pass
        finally:
            self._tx.close()
            self._tx = None
            self.connected = False
            self.remote = False

    # ------------------------------------------------------------- wire I/O
    def _log(self, direction: str, data: bytes) -> None:
        if self.trace and data:
            self.trace(direction, data)

    def _read_chunk(self, command: str, timeout: float) -> str:
        """Read up to and including the next '>' (inter-byte timeout, seg68:3a67..3afd)."""
        deadline = time.monotonic() + timeout
        while True:
            i = self._rx.find(P.PROMPT)
            if i >= 0:
                chunk = bytes(self._rx[:i])
                del self._rx[:i + 1]
                return chunk.decode("latin-1")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial = bytes(self._rx)
                self._rx.clear()
                raise ReplyTimeout(command, partial)
            data = self._tx.read(min(remaining, 0.1))
            if data:
                self._log("<-", data)
                self._rx += data
                deadline = time.monotonic() + timeout

    def _exchange(self, command: str, want_body: bool, timeout: Optional[float]) -> Optional[str]:
        if self._tx is None:
            raise TransportError("port is not open")
        t = self.timeout if timeout is None else timeout
        self._tx.reset_input()                    # host drains before every command
        self._rx.clear()
        out = P.frame(command)
        self._log("->", out)
        self._tx.write(out)
        status = self._read_chunk(command, t)
        if not status.strip():
            # tolerate a bare prompt arriving in front of the status text
            try:
                status = self._read_chunk(command, min(t, 0.3))
            except ReplyTimeout:
                pass
        P.check_status(command, status)
        if not want_body:
            return None
        try:
            body = self._read_chunk(command, t)
        except ReplyTimeout:
            # one RESEND, as SOFTmax PRO does (seg68:3dbd)
            out = P.frame("RESEND")
            self._log("->", out)
            self._tx.write(out)
            P.check_status("RESEND", self._read_chunk("RESEND", t))
            body = self._read_chunk(command, t)
        return P.clean_body(body)

    def command(self, command: str, *, timeout: Optional[float] = None) -> Optional[str]:
        """Send any command; returns its data text, or None for commands without data."""
        return self._exchange(command, P.expects_body(command), timeout)

    def query(self, command: str, *, timeout: Optional[float] = None) -> str:
        """Send a command that is known to return data."""
        return self._exchange(command, True, timeout) or ""

    def ping(self) -> None:
        """The empty command ``!\\r`` -- the instrument must answer OK (seg68:34ee)."""
        self._exchange("", False, max(PROBE_TIMEOUT, min(self.timeout, 1.0)))

    # ------------------------------------------------------- simple controls
    def set_remote(self, on: bool = True) -> None:
        self.command("REMOTE ON" if on else "REMOTE OFF")
        self.remote = on

    def display(self, line1: str = "", line2: str = "") -> None:
        """Write the two 40-character lines of the front-panel LCD (seg68:1b10)."""
        for pos, text in ((0, line1), (40, line2)):
            text = text.replace('"', "'").replace("\r", " ").replace(">", " ")[:40]
            self.command(f'DISPLAY {pos} "{text}"')

    def status(self) -> Status:
        return P.parse_status(self.query("STATUS"))

    def open_drawer(self) -> None:
        self.command("OPEN")

    def close_drawer(self) -> None:
        self.command("CLOSE")

    def reset(self) -> None:
        """Instrument reset, then back to remote mode (seg68:1d70)."""
        self.command("RESET", timeout=max(self.timeout, 10.0))
        if self.remote:
            self.set_remote(True)

    def temperature(self) -> tuple:
        """(setpoint, actual) in degC.  A setpoint of 0 means the incubator is off."""
        return P.parse_temp(self.query("TEMP"))

    def set_temperature(self, celsius: Optional[float]) -> None:
        """Set the incubator (None or 0 switches it off)."""
        c = 0.0 if not celsius else float(celsius)
        opt = self.info.options
        if c and not opt.temp_min <= c <= opt.temp_max:
            raise ValueError(f"temperature must be 0 (off) or {opt.temp_min:g}..{opt.temp_max:g} degC")
        self.command(f"TEMP {c:.1f}")

    def shake(self, on: bool = True) -> None:
        """Start / stop shaking right now (seg68:2256)."""
        self.command("SHAKE NOW" if on else "SHAKE STOP")

    def errors(self) -> str:
        """Text of the instrument's error log."""
        return self.query("ERROR").strip("\r\n ")

    def clear_errors(self) -> None:
        self.command("CLEAR")

    def filters(self) -> list:
        return P.parse_int_list(self.query("FILTER"))

    def instrument_time(self) -> int:
        return P.parse_int(self.query("TIME"), "TIME reply")

    def queue(self) -> int:
        """Number of finished reads waiting to be transferred."""
        return P.parse_int(self.query("QUEUE"), "QUEUE reply")

    def cancel(self) -> None:
        self.command("CANCEL")

    def transfer(self) -> list:
        """Fetch the oldest waiting read."""
        return P.parse_transfer(self.query("TRANSFER", timeout=max(self.timeout, 5.0)))

    # ------------------------------------------------------------------ reads
    def start_read(self, s: ReadSettings) -> None:
        """Send the set-up sequence and READ (same order as seg68:029a)."""
        s.validate(self.info.options if self.connected else None)
        if not self.remote:
            self.set_remote(True)
        self.command("CLEAR DATA")
        self.time_offset = time.time() - self.instrument_time()

        if s.mode == "spectrum":
            wls: Iterable = [s.start]
        else:
            wls = s.wavelengths
        # every entry is followed by a space, including the last (seg68:04fe)
        self.command("WAVELENGTH " + "".join(f"{str(w).upper()} " for w in wls))

        interval = s.interval if s.mode == "kinetic" else 0
        before = s.shake_before
        between = s.shake_between if s.mode == "kinetic" else 0
        self.command("SHAKE ON" if (before or between) else "SHAKE OFF")
        # SHAKE <before> <interval> <interval-between> <between> 0  (seg68:0660..08d4)
        self.command(f"SHAKE  {before} {interval} {interval - between} {between} 0")
        self.command(f"STRIP {s.first_column} {s.num_columns}")

        if s.mode == "kinetic":
            calibrate = "CALIBRATE ON" if s.autocalibrate else "CALIBRATE ONCE"
            mode = f"MODE KINETIC {int(s.interval)} {int(s.reads)}"
        else:
            calibrate = "CALIBRATE ONCE" if s.autocalibrate else "CALIBRATE OFF"
            if s.mode == "spectrum":
                mode = f"MODE SPECTRUM {int(s.start)} {int(s.step)} {s.expected_plates}"
            else:
                mode = "MODE ENDPOINT"
        self.command(calibrate)
        self.command(mode)
        self.command("READ", timeout=max(self.timeout, 5.0))

    def estimate_seconds(self, s: ReadSettings) -> float:
        """Rough duration of a read, for progress display and the default timeout."""
        per_pass = 2.0 + 0.75 * s.num_columns          # ~9-11 s for a full plate
        if s.mode == "kinetic":
            return s.shake_before + s.interval * (s.reads - 1) + per_pass * len(s.wavelengths) + 10
        if s.mode == "spectrum":
            return s.shake_before + s.expected_plates * per_pass + 10
        return s.shake_before + per_pass * len(s.wavelengths) + 10

    def collect(self, s: ReadSettings, *, on_plate: Optional[Callable] = None,
                on_status: Optional[Callable] = None, timeout: Optional[float] = None,
                poll_interval: float = POLL_INTERVAL, stall_timeout: float = 30.0) -> list:
        """Poll QUEUE / TRANSFER / STATUS until every expected read has arrived.

        Mirrors the host's polling routine (seg68:0ea2).  ``on_plate(plate,
        index, total)`` is called for each read as it arrives.
        """
        total = s.expected_plates
        plates: list = []
        if timeout is None:
            timeout = self.estimate_seconds(s) * 1.5 + 120
        deadline = time.monotonic() + timeout
        idle_since: Optional[float] = None
        try:
            while len(plates) < total:
                n = self.queue()
                if n > 0:
                    idle_since = None
                    for _ in range(min(n, 8, total - len(plates))):
                        got = self.transfer()
                        if not got:
                            raise ProtocolError("TRANSFER returned no data")
                        for p in got:
                            p.timestamp = self.time_offset + p.time_s
                            plates.append(p)
                            if on_plate:
                                on_plate(p, len(plates), total)
                    continue
                st = self.status()
                if on_status:
                    on_status(st)
                now = time.monotonic()
                if "CAL" in st.state or "MEAS" in st.state or st.state == "BUSY":
                    idle_since = None
                else:
                    # SOFTmax PRO raises its error 104 at once here; allow a short
                    # grace period for the instrument to get going.
                    idle_since = idle_since or now
                    if now - idle_since > stall_timeout:
                        raise SpectraMaxError(
                            f"instrument reports {st.state or 'no activity'} but only "
                            f"{len(plates)} of {total} reads arrived. Error log: "
                            f"{self._safe_errors()}")
                if now > deadline:
                    raise SpectraMaxError(
                        f"read timed out after {timeout:.0f} s with {len(plates)} of "
                        f"{total} reads received")
                if poll_interval > 0:
                    time.sleep(poll_interval)
        except BaseException:
            try:
                self.cancel()                      # as the host does on abort (seg68:161a)
            except SpectraMaxError:
                pass
            raise
        return plates

    def _safe_errors(self) -> str:
        try:
            return self.errors() or "(empty)"
        except SpectraMaxError as e:
            return f"(could not read: {e})"

    def read(self, settings: ReadSettings, *, open_when_done: bool = False, **collect_kw) -> list:
        """Run a complete read and return its :class:`PlateData` list."""
        self.start_read(settings)
        plates = self.collect(settings, **collect_kw)
        if open_when_done:
            self.open_drawer()                     # SOFTmax PRO always does (seg68:16a0)
        return plates

    def read_endpoint(self, wavelengths: Sequence = (405,), *, columns: tuple = (1, 12),
                      shake: int = 0, autocalibrate: bool = True, **kw) -> PlateData:
        """Single read at 1..6 wavelengths.  ``columns`` = (first, last), 1-based."""
        s = ReadSettings("endpoint", tuple(wavelengths), columns[0], columns[1] - columns[0] + 1,
                         shake_before=shake, autocalibrate=autocalibrate)
        return self.read(s, **kw)[0]

    def read_kinetic(self, wavelengths: Sequence = (405,), *, interval: int, reads: int = 0,
                     duration: float = 0, columns: tuple = (1, 12), shake: int = 0,
                     shake_between: int = 0, autocalibrate: bool = True, **kw) -> list:
        """Repeated reads every ``interval`` seconds; give ``reads`` or ``duration`` (s)."""
        if not reads:
            if duration <= 0:
                raise ValueError("give either reads or duration")
            reads = int(math.floor(duration / interval)) + 1   # as seg35:046b
        s = ReadSettings("kinetic", tuple(wavelengths), columns[0], columns[1] - columns[0] + 1,
                         interval=int(interval), reads=int(reads), shake_before=shake,
                         shake_between=shake_between, autocalibrate=autocalibrate)
        return self.read(s, **kw)

    def read_spectrum(self, start: int, stop: int, step: int = 10, *, columns: tuple = (1, 12),
                      shake: int = 0, autocalibrate: bool = True, **kw) -> list:
        """Wavelength scan; returns one :class:`PlateData` per wavelength."""
        s = ReadSettings("spectrum", (start,), columns[0], columns[1] - columns[0] + 1,
                         start=int(start), stop=int(stop), step=int(step),
                         shake_before=shake, autocalibrate=autocalibrate)
        return self.read(s, **kw)
