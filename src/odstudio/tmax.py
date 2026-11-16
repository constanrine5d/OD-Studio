"""Driver for the older Molecular Devices filter readers: ThermoMax, UVmax, Vmax and Emax.

The command sequences follow what SOFTmax PRO 1.x sends to them (class ``CSTMaxInterface``
and its subclasses, SPF.EXE segments 66, 67 and 63); see ``docs/PROTOCOL-TMAX.md``.
**Not yet tried on a real reader.**

Differences from the SpectraMax 250:

* 9600 8N1 like the SpectraMax, but every command character is echoed and there is no
  terminator;
* a 6-position filter wheel instead of a monochromator, with no way to ask the reader
  which filters are fitted, so their wavelengths come from the user (Preferences);
* one wavelength per read, or one measurement/reference pair (the reader subtracts the
  reference itself and returns one value per well);
* always the whole plate; no spectrum mode; kinetic reads are timed by the host.

    from odstudio.tmax import TMaxReader

    with TMaxReader("COM1", filters=[405, 450, 490, 540, 570, 650]) as r:
        print(r.info.model)
        plate = r.read_endpoint([405])
"""
from __future__ import annotations

import struct
import time
from typing import Callable, Optional, Sequence

from .driver import InstrumentInfo, ReadSettings, Trace
from .errors import CommandFailed, ProtocolError, ReplyTimeout, SpectraMaxError, TransportError
from .protocol import COLUMNS, ROWS, Options, PlateData, Status
from .readers import capabilities
from .transport import DEFAULT_BAUD, Transport, open_transport

TICK = 0.016                     # SOFTmax PRO tick (GetTickCount >> 4)
ECHO_TIMEOUT = 0.25              # host: 6 ticks (0.1 s) for each echo but the last; a little
                                 # more here for USB adapters
LAST_ECHO_TIMEOUT = 2.0          # host: 60 ticks (DS:0bb0)
SHAKE_ECHO_TIMEOUT = 30.0        # host: 1800 ticks while "SKO3 " runs (66:194a)
LINE_TIMEOUT = 1.0               # per character of a text reply (66:1e98)
DATA_TIMEOUT = 30.0              # per byte of a data block (66:2d96)
MISSING = -1000                  # milli-OD meaning "no value" (DS:0bde)
BEL = 0x07

MODEL_NAMES = {"thermomax": "ThermoMax", "uvmax": "UVmax", "vmax": "Vmax", "emax": "Emax"}
# What each model can do (vtable differences, PROTOCOL-TMAX.md "Per-model differences")
MODEL_CAPS = {
    "thermomax": {"kinetic": True, "shake": True, "incubator": True},
    "uvmax": {"kinetic": True, "shake": True, "incubator": False},
    "vmax": {"kinetic": True, "shake": True, "incubator": False},
    "emax": {"kinetic": False, "shake": False, "incubator": False},
}
TEMP_MIN, TEMP_MAX = 15.0, 42.0          # DS:0bd6, DS:0bda


def min_interval(dual: bool, automix_between: bool) -> int:
    """Shortest kinetic interval SOFTmax PRO allows (66:1e05): 6, 9, 14 or 19 s."""
    return (14 if dual else 6) + (((5 if dual else 3)) if automix_between else 0)


class ReaderError(CommandFailed):
    """The reader refused a command (BEL + number), or the model lacks a feature.

    ``code`` follows SOFTmax PRO: 300 + the reader's number, 301 = not available."""

    def __init__(self, message: str, code: int):
        SpectraMaxError.__init__(self, message)
        self.command, self.code, self.raw = "", code, ""


class TMaxReader:
    """ThermoMax / UVmax / Vmax / Emax.  ``model=None`` detects it as SOFTmax PRO does."""

    def __init__(self, port: str = "sim:vmax", *, model: Optional[str] = None,
                 filters: Sequence[int] = (), byteorder: str = "auto",
                 baudrate: int = DEFAULT_BAUD, transport: Optional[Transport] = None,
                 trace: Trace = None):
        if model is not None and model not in MODEL_NAMES:
            raise ValueError(f"unknown model {model!r}")
        if byteorder not in ("auto", "big", "little"):
            raise ValueError("byteorder must be auto, big or little")
        self.port = port
        self.model = model
        self.filters = [int(f or 0) for f in list(filters)[:6]] + [0] * (6 - len(list(filters)[:6]))
        self.byteorder = byteorder
        self.baudrate = baudrate
        self.trace = trace
        self._tx = transport
        self._rx = bytearray()
        self.info = InstrumentInfo()
        self.connected = False
        self.time_offset = 0.0
        self._drawer_open = True             # the host assumes open until told (66:0112)
        self._reading: Optional[ReadSettings] = None

    # ------------------------------------------------------------ lifecycle
    def __enter__(self) -> "TMaxReader":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def open_port(self) -> None:
        if self._tx is None:
            self._tx = open_transport(self.port, self.baudrate)

    def connect(self, *, display=None, **_ignored) -> InstrumentInfo:
        """Wake the reader and find out which model it is (66:1fc0, 64:0cea)."""
        self.open_port()
        try:
            self._flush()
            try:
                self._send("\r", last_timeout=0.5, retry=False)
            except SpectraMaxError:
                self._resync()
            self._send("LCL")
            version = ""
            if self.model is None or self.model == "thermomax":
                model, version = self._identify()
                self.model = self.model or model
            self.connected = True
        except SpectraMaxError:
            self.disconnect()
            raise
        name = MODEL_NAMES[self.model]
        self.info = InstrumentInfo(version=version or "unknown", options=Options(
            name=name, wavelength_min=0, wavelength_max=0, temp_min=TEMP_MIN, temp_max=TEMP_MAX,
            raw=f"{name} (filter reader)"))
        return self.info

    def _identify(self) -> tuple:
        """VER, then SHK, as the host's detection does (64:0cea)."""
        model, version = None, ""
        try:
            self._send("VER")
            line1 = self._line()
            self._line()
            head, _, rest = line1.partition("; ")
            name, sep, ver = (rest if _ else line1).partition(" VER ")
            version = ver.strip()
            key = name.strip()[:2]
            model = {"TH": "thermomax", "UV": "uvmax", "Em": "emax"}.get(key)
        except CommandFailed as e:
            if e.code == 301:
                raise SpectraMaxError("This reader answers like a SpectraMax, not an older "
                                      "filter reader. Choose SpectraMax 250 as the reader type.")
        except SpectraMaxError:
            pass
        if model is None:
            try:
                self._send("SHK")
                model = "vmax"
            except SpectraMaxError:
                model = "emax"
        return model, version

    def disconnect(self) -> None:
        if self._tx is not None:
            try:
                if self.connected:
                    self._send("LCL", retry=False)
            except SpectraMaxError:
                pass
            self._tx.close()
        self._tx = None
        self.connected = False

    # ---------------------------------------------------------- capabilities
    @property
    def caps(self) -> dict:
        return MODEL_CAPS[self.model or "vmax"]

    @property
    def capabilities(self) -> dict:
        c = self.caps
        return capabilities(MODEL_NAMES[self.model or "vmax"], family="tmax",
                            modes=["endpoint", "kinetic"] if c["kinetic"] else ["endpoint"],
                            max_wavelengths=2, filters=[f for f in self.filters if f],
                            wl_min=0, wl_max=0, temperature=c["incubator"],
                            temp_min=TEMP_MIN, temp_max=TEMP_MAX, shake=c["shake"],
                            calibrate=True, column_range=False, min_interval=6)

    # ------------------------------------------------------------- the wire
    def _log(self, direction: str, data: bytes) -> None:
        if self.trace and data:
            self.trace(direction, data)

    def _flush(self) -> None:
        if self._tx is None:
            raise TransportError("port not open")
        self._tx.reset_input()
        self._rx.clear()

    def _byte(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while not self._rx:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ReplyTimeout("no answer from the reader (check cable, port and power)")
            chunk = self._tx.read(min(left, 0.2))
            self._log("<", chunk)
            self._rx += chunk
        b = self._rx[0]
        del self._rx[0]
        return b

    def _error_after_bel(self) -> "ReaderError":
        """The reader sent BEL: read '<number> CR <one more byte>' (66:23b2)."""
        text = ""
        try:
            for _ in range(20):
                c = self._byte(LINE_TIMEOUT)
                if c == 0x0D:
                    break
                text += chr(c)
            self._byte(LINE_TIMEOUT)
        except ReplyTimeout:
            pass
        try:
            n = int(text.strip() or "0")
        except ValueError:
            n = 0
        return ReaderError(f"The reader refused the command (its error {n}).", 300 + n)

    def _send_once(self, cmd: str, last_timeout: float) -> None:
        for i, ch in enumerate(cmd):
            data = ch.encode("latin-1")
            self._log(">", data)
            self._tx.write(data)
            last = i == len(cmd) - 1
            c = self._byte(last_timeout if last else ECHO_TIMEOUT)
            if c == BEL:
                raise self._error_after_bel()
            if c != data[0]:
                raise ProtocolError(f"echo mismatch sending {cmd!r}: got {c:#04x}")

    def _resync(self) -> None:
        """BREAK, then a lone CR answered by 'BEL n CR x' (66:11bc)."""
        self._tx.send_break(0.06)
        time.sleep(0.16)
        self._flush()
        self._log(">", b"\r")
        self._tx.write(b"\r")
        try:
            c = self._byte(1.0)
            if c == BEL:
                for _ in range(21):
                    if self._byte(LINE_TIMEOUT) == 0x0D:
                        self._byte(LINE_TIMEOUT)
                        break
        except ReplyTimeout:
            pass
        self._flush()

    def _send(self, cmd: str, *, last_timeout: float = LAST_ECHO_TIMEOUT, retry: bool = True) -> None:
        """Send a command with per-character echo; one retry after a resync (66:2276)."""
        self._flush()
        try:
            self._send_once(cmd, last_timeout)
        except SpectraMaxError:                     # any failure, as the host does
            if not retry:
                raise
            self._resync()
            self._send_once(cmd, last_timeout)

    def _line(self) -> str:
        out = bytearray()
        while True:
            c = self._byte(LINE_TIMEOUT)
            out.append(c)
            if c == 0x0A:
                return out.decode("latin-1")

    def _query(self, cmd: str) -> str:
        self._send(cmd)
        return self._line()

    def command(self, command: str, *, timeout: Optional[float] = None) -> Optional[str]:
        """Send a raw command (no terminator; it is echoed character by character)."""
        cmd = command.strip().upper()
        if cmd[:3] in ("RDP", "RNE", "STK", "NXK", "RDR", "RNR"):
            raise SpectraMaxError("Use Read for plate reads.")
        self._send(cmd, last_timeout=timeout or LAST_ECHO_TIMEOUT)
        if cmd in ("VER",):
            return self._line() + self._line()
        if cmd in ("RDT", "RDS", "STS"):
            return self._line()
        return None

    # ----------------------------------------------------------- instrument
    def _need(self, what: str) -> None:
        if not self.caps[what]:
            label = {"incubator": "has no incubator", "shake": "cannot shake the plate",
                     "kinetic": "cannot do kinetic reads"}[what]
            raise ReaderError(f"The {MODEL_NAMES[self.model]} {label}.", 301)

    def status(self) -> Status:
        if self.caps["incubator"]:                 # only the ThermoMax reports (66:1626)
            line = self._query("STS")
            try:
                bits = int(line[0])
            except (ValueError, IndexError):
                raise ProtocolError(f"cannot read STS reply {line!r}") from None
            if bits & 2:
                self._drawer_open = True
            if bits & 4:
                self._drawer_open = False
            raw = [line.strip()]
        else:
            self._send("LCL")                       # the host's idle keep-alive
            raw = []
        state = "MEAS" if self._reading else "IDLE"
        return Status(drawer="OPEN" if self._drawer_open else "CLOSED", state=state, raw=raw)

    def open_drawer(self) -> None:
        self._send("RMT")
        self._send("OPN")
        self._send("LCL")
        self._drawer_open = True

    def close_drawer(self) -> None:
        self._send("RMT")
        self._send("CLS")
        self._send("LCL")
        self._drawer_open = False

    @staticmethod
    def _temp_text(line: str) -> Optional[float]:
        try:
            return float(line[1:5])                 # the host keeps characters 1..4 (66:17ba)
        except ValueError:
            return None

    def temperature(self) -> tuple:
        """(setpoint, actual) in degC; setpoint 0 = incubator off."""
        if not self.caps["incubator"]:
            return 0.0, None
        sp = self._temp_text(self._query("RDS")) or 0.0
        actual = self._temp_text(self._query("RDT"))
        return sp, actual

    def set_temperature(self, celsius: Optional[float]) -> None:
        self._need("incubator")
        self._send("RMT")
        if not celsius:
            self._send("CFF")
        else:
            if not TEMP_MIN <= celsius <= TEMP_MAX:
                raise ValueError(f"temperature must be {TEMP_MIN:g}-{TEMP_MAX:g} degC")
            self._send("TMP" + f"{celsius:4.1f}"[:4])
            self._send("CON")
        self._send("LCL")

    def shake(self, on: bool = True) -> None:
        """Shake now ("SKO3 ", 66:194a).  The reader stops by itself; there is no 'off'."""
        self._need("shake")
        if on:
            self._send("SKO3 ", last_timeout=SHAKE_ECHO_TIMEOUT)

    def errors(self) -> str:
        return "These older readers keep no error log."

    def clear_errors(self) -> None:
        pass

    # ----------------------------------------------------------------- reads
    def _position(self, wl) -> int:
        nm = int(str(wl).lstrip("Ff"))
        if nm in self.filters:
            return self.filters.index(nm) + 1
        have = ", ".join(str(f) for f in self.filters if f) or "none entered"
        raise SpectraMaxError(f"No {nm} nm filter. Filters in Preferences: {have}.")

    def _check(self, s: ReadSettings) -> None:
        if s.mode not in ("endpoint", "kinetic"):
            raise SpectraMaxError(f"The {MODEL_NAMES[self.model]} cannot do {s.mode} reads.")
        if s.mode == "kinetic":
            self._need("kinetic")
        if not 1 <= len(s.wavelengths) <= 2:
            raise SpectraMaxError("These readers read one filter, or one filter with a reference.")
        for w in s.wavelengths:
            self._position(w)
        if s.mode == "kinetic":
            need = min_interval(len(s.wavelengths) == 2, bool(s.shake_between) and self.caps["shake"])
            if s.interval < need:
                raise SpectraMaxError(f"Read interval is too short. Minimum {need} seconds.")

    def start_read(self, s: ReadSettings) -> None:
        """Preparation the host sends before every read (66:25aa) and the filter (66:2762)."""
        self._check(s)
        self._send("RMT")
        if self.caps["shake"]:
            self._send("SHK" if s.shake_before else "DSH")
        pos = [self._position(w) for w in s.wavelengths]
        self._send(f"SWV{pos[0]}" if len(pos) == 1 else f"DWV{pos[0]},{pos[1]}")
        self._reading = s

    def estimate_seconds(self, s: ReadSettings) -> float:
        one = 10.0 if len(s.wavelengths) == 2 else 5.0          # DS:0b56
        if s.mode == "kinetic":
            return s.interval * (s.reads - 1) + one + 10
        return one + 10

    def _block(self) -> list:
        """Lead byte + 96 signed 16-bit milli-OD values, row by row (66:2d96, 66:2965)."""
        c = self._byte(DATA_TIMEOUT)
        if c == BEL:
            raise self._error_after_bel()
        raw = bytes(self._byte(DATA_TIMEOUT) for _ in range(192))
        return self._decode(raw)

    def _decode(self, raw: bytes) -> list:
        orders = [self.byteorder] if self.byteorder != "auto" else ["big", "little"]
        best = None
        for order in orders:
            vals = struct.unpack((">" if order == "big" else "<") + "96h", raw)
            odd = sum(1 for v in vals if v != MISSING and not -500 <= v <= 5000)
            if best is None or odd < best[0]:
                best = (odd, order, vals)
        odd, order, vals = best
        if self.byteorder == "auto" and odd == 0 and any(v not in (0, MISSING) for v in vals):
            self.byteorder = order                   # settle it once a plate makes sense
        return list(vals)

    def _plate(self, vals: list, s: ReadSettings, t: float, temp: Optional[float]) -> PlateData:
        wl = int(str(s.wavelengths[0]).lstrip("Ff"))
        grid = [[None if v is None or v == MISSING else v / 1000.0
                 for v in vals[r * COLUMNS:(r + 1) * COLUMNS]] for r in range(ROWS)]
        p = PlateData(time_s=t, temperature=float("nan") if temp is None else temp,
                      wavelengths=[wl], values={wl: grid})
        p.timestamp = time.time()
        return p

    def _after_block(self) -> Optional[float]:
        if self.caps["incubator"]:                 # ThermoMax reads RDT after each block (66:2b22)
            try:
                return self._temp_text(self._query("RDT"))
            except SpectraMaxError:
                return None
        return None

    def collect(self, s: ReadSettings, *, on_plate: Optional[Callable] = None,
                on_status: Optional[Callable] = None, timeout: Optional[float] = None,
                poll_interval: float = 0.5, **_ignored) -> list:
        """Do the read(s) prepared by :meth:`start_read`.  Kinetic reads are timed here,
        as SOFTmax PRO times them (66:2ff8): a read more than 10% + 1 s late is skipped."""
        if self._reading is None:
            self.start_read(s)
        dual = len(s.wavelengths) == 2
        total = s.expected_plates
        plates: list = []

        def status():
            if on_status:
                on_status(Status(drawer="CLOSED", state="MEAS"))

        try:
            t0 = time.monotonic()
            if s.mode == "endpoint":
                self._send("RDP" if s.autocalibrate else "RNE")
                status()
                vals = self._block()
                p = self._plate(vals, s, 0.0, self._after_block())
                plates.append(p)
                if on_plate:
                    on_plate(p, 1, total)
                return plates
            self._send("RDR" if dual else "STK")
            t_cmd = time.monotonic()
            status()
            vals = self._block()
            p = self._plate(vals, s, 0.0, self._after_block())
            plates.append(p)
            if on_plate:
                on_plate(p, 1, total)
            if self.caps["shake"]:
                self._send("SHK" if s.shake_between else "DSH")
            nxt = "RNR" if dual else "NXK"
            k = 1
            while k < total:
                due = t_cmd + k * s.interval
                while time.monotonic() < due:
                    status()
                    time.sleep(min(poll_interval, max(0.0, due - time.monotonic())))
                late = time.monotonic() - due
                if late > 0.1 * s.interval + 1.0:   # too late: record a missing read
                    p = self._plate([MISSING] * 96, s, k * s.interval, None)
                    plates.append(p)
                    k += 1
                    if on_plate:
                        on_plate(p, len(plates), total)
                    continue
                self._send(nxt)
                vals = self._block()
                p = self._plate(vals, s, k * s.interval, self._after_block())
                plates.append(p)
                k += 1
                if on_plate:
                    on_plate(p, len(plates), total)
                status()
            return plates
        finally:
            self._reading = None
            self.time_offset = time.time() - time.monotonic()

    def read(self, settings: ReadSettings, *, open_when_done: bool = False, **collect_kw) -> list:
        self.start_read(settings)
        plates = self.collect(settings, **collect_kw)
        if open_when_done:
            self.open_drawer()
        return plates

    def read_endpoint(self, wavelengths: Sequence = (405,), *, calibrate: bool = True,
                      shake: int = 0, **kw) -> PlateData:
        s = ReadSettings("endpoint", tuple(wavelengths), autocalibrate=calibrate, shake_before=shake)
        return self.read(s, **kw)[0]

    def read_kinetic(self, wavelengths: Sequence = (405,), *, interval: int, reads: int,
                     shake: int = 0, shake_between: int = 0, **kw) -> list:
        s = ReadSettings("kinetic", tuple(wavelengths), interval=interval, reads=reads,
                         shake_before=shake, shake_between=shake_between)
        return self.read(s, **kw)
