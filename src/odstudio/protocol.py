"""Wire format of the SpectraMax 250 ("Flash") serial protocol: framing + parsers.

Pure functions only -- no I/O.  See docs/PROTOCOL.md for where each fact comes
from in SPF.EXE.

Summary
-------
* Host sends ``!`` + command + CR.                         (seg68:38f6..3987)
* Instrument answers with text ending in the prompt ``>``.  (seg68:3ac2)
  The first chunk is the status: it contains ``OK`` or ``FAIL`` + error number.
* Commands that return data send a *second* chunk, again ending in ``>``.
  SOFTmax PRO discards the first two characters of that chunk (seg68:3d87).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .errors import CommandFailed, ProtocolError

PROMPT = b">"
ROW_LETTERS = "ABCDEFGH"
ROWS = 8
COLUMNS = 12

# Commands whose reply has a second (data) chunk when sent without arguments.
QUERY_COMMANDS = {
    "BAUD", "ERROR", "FILTER", "HELP", "MODE", "OPTION", "QUEUE", "STATUS", "STRIP",
    "TEMP", "TIME", "TRANSFER", "VER", "WAVELENGTH",
}


def frame(command: str) -> bytes:
    """Bytes to put on the wire for ``command`` (given without ``!`` / CR)."""
    command = command.strip()
    if command.startswith("!"):
        command = command[1:]
    return b"!" + command.encode("latin-1") + b"\r"


def expects_body(command: str) -> bool:
    """True when ``command`` (as typed, e.g. ``"TEMP"`` or ``"TEMP 37"``) returns data."""
    parts = command.strip().lstrip("!").upper().split()
    if not parts:
        return False
    name, args = parts[0], parts[1:]
    if name == "TRANSFER":
        return True
    if name == "ERROR":
        return True
    return name in QUERY_COMMANDS and not args


_FAIL_RE = re.compile(r"FAIL\D{0,4}(\d{1,4})?")


def check_status(command: str, chunk: str) -> None:
    """Raise :class:`CommandFailed` unless the status chunk says OK.

    Mirrors seg68:3f90: upper-case the text, look for ``OK``; otherwise look
    for ``FAIL`` and the number after it.
    """
    up = chunk.upper()
    if "OK" in up:
        return
    m = _FAIL_RE.search(up)
    if m:
        raise CommandFailed(command, int(m.group(1)) if m.group(1) else 0, chunk)
    raise CommandFailed(command, None, chunk)


def clean_body(chunk: str) -> str:
    """Strip the two throw-away lead characters / stray CR-LF from a data chunk."""
    return chunk.lstrip("\r\n \x00")


def lines(text: str) -> list:
    """Split instrument text into non-empty lines (CR, LF or CRLF terminated)."""
    return [ln for ln in re.split(r"[\r\n]+", text) if ln.strip(" \t")]


def fields(line: str) -> list:
    return [f.strip() for f in line.strip().split("\t") if f.strip() != ""]


# ------------------------------------------------------------------- STATUS
@dataclass
class Status:
    drawer: str            # "OPEN" / "CLOSED"
    state: str             # e.g. "IDLE", "MEAS", "CAL", "BUSY"
    raw: list = field(default_factory=list)

    @property
    def drawer_open(self) -> bool:
        return self.drawer.startswith("OPEN")

    @property
    def busy(self) -> bool:
        return self.state not in ("", "IDLE", "READY")


def parse_status(body: str) -> Status:
    f = re.split(r"[\t\r\n ]+", body.strip())
    f = [x for x in f if x]
    if not f:
        raise ProtocolError(f"empty STATUS reply {body!r}")
    return Status(f[0].upper(), f[1].upper() if len(f) > 1 else "", f)


# --------------------------------------------------------------------- TEMP
def parse_temp(body: str) -> tuple:
    """``TEMP`` -> (setpoint, actual) in degrees C.  Setpoint 0 = incubator off."""
    f = re.split(r"[\t\r\n ]+", body.strip())
    try:
        return float(f[0]), float(f[1])
    except (IndexError, ValueError):
        raise ProtocolError(f"cannot parse TEMP reply {body!r}") from None


def parse_int(body: str, what: str = "reply") -> int:
    m = re.search(r"-?\d+", body)
    if not m:
        raise ProtocolError(f"cannot parse {what} {body!r}")
    return int(m.group())


def parse_int_list(body: str) -> list:
    return [int(x) for x in re.findall(r"-?\d+", body)]


# ------------------------------------------------------------------- OPTION
@dataclass
class Options:
    """Instrument capabilities as reported by ``OPTION``."""

    name: str = ""
    wavelength_min: int = 250
    wavelength_max: int = 750
    wavelength_step: int = 1
    temp_min: float = 15.0
    temp_max: float = 45.0
    extra: list = field(default_factory=list)   # 4th line, meaning not established
    raw: str = ""


def parse_option(body: str) -> Options:
    ls = lines(body)
    opt = Options(raw=body)
    try:
        if ls:
            opt.name = ls[0].strip()
        if len(ls) > 1:
            f = fields(ls[1])
            opt.wavelength_min, opt.wavelength_max = int(float(f[0])), int(float(f[1]))
            if len(f) > 2:
                opt.wavelength_step = int(float(f[2]))
        if len(ls) > 2:
            f = fields(ls[2])
            opt.temp_min, opt.temp_max = float(f[0]), float(f[1])
        if len(ls) > 3:
            opt.extra = fields(ls[3])
    except (ValueError, IndexError):
        raise ProtocolError(f"cannot parse OPTION reply {body!r}") from None
    return opt


# ----------------------------------------------------------------- TRANSFER
@dataclass
class PlateData:
    """One transferred 'plate': a single read at one or more wavelengths.

    ``values[wavelength][row][column]`` with row 0..7 = A..H, column 0..11 =
    1..12.  Wells that were not read (outside the strip) are ``None``;
    saturated wells are ``inf``.
    """

    time_s: float
    temperature: float
    wavelengths: list
    values: dict = field(default_factory=dict)
    timestamp: Optional[float] = None    # wall-clock time of the read (epoch seconds)

    @property
    def columns(self) -> list:
        """1-based plate columns that hold data."""
        cols = set()
        for grid in self.values.values():
            for row in grid:
                cols.update(c + 1 for c, v in enumerate(row) if v is not None)
        return sorted(cols)

    def well(self, name: str, wavelength: Optional[int] = None) -> Optional[float]:
        """Value of well ``"A1"`` .. ``"H12"``."""
        m = re.fullmatch(r"([A-Ha-h])(\d{1,2})", name.strip())
        if not m or not 1 <= int(m.group(2)) <= COLUMNS:
            raise ValueError(f"bad well name {name!r}")
        wl = self.wavelengths[0] if wavelength is None else wavelength
        return self.values[wl][ROW_LETTERS.index(m.group(1).upper())][int(m.group(2)) - 1]

    def grid(self, wavelength: Optional[int] = None) -> list:
        return self.values[self.wavelengths[0] if wavelength is None else wavelength]


_COL_RE = re.compile(r"^\s*(\d+)\s*:\s*(.*)$")


def _value(tok: str) -> float:
    try:
        return float(tok)
    except ValueError:
        return math.inf if "SAT" in tok.upper() else math.nan


def _empty_grid() -> list:
    return [[None] * COLUMNS for _ in range(ROWS)]


def parse_transfer(body: str) -> list:
    """Parse the data chunk of ``TRANSFER`` into a list of :class:`PlateData`.

    Layout per plate (seg69:2a36 writes it, seg68:41ce reads it)::

        <seconds> TAB <temperature> CR
        L: TAB <wl1> TAB <wl2> ... CR          wavelength list
        L: TAB <wl1> CR                        then one block per wavelength
        <col>: TAB <A> TAB <B> ... <H> TAB CR  one line per column, rows A..H
        ...
        CR
    """
    plates: list = []
    cur: Optional[PlateData] = None
    have_list = False
    block_wl: Optional[int] = None
    for ln in lines(body):
        m = _COL_RE.match(ln)
        if m:
            if cur is None or block_wl is None:
                raise ProtocolError(f"column data before a wavelength header: {ln!r}")
            col = int(m.group(1))
            vals = [_value(t) for t in re.split(r"[\t ]+", m.group(2).strip()) if t]
            if not 1 <= col <= COLUMNS or len(vals) > ROWS:
                raise ProtocolError(f"bad column line {ln!r}")
            grid = cur.values[block_wl]
            for row, v in enumerate(vals):
                grid[row][col - 1] = v
            continue
        if ln.lstrip().upper().startswith("L:"):
            if cur is None:
                raise ProtocolError(f"wavelength line before a plate header: {ln!r}")
            wls = [int(float(t)) for t in re.split(r"[\t ]+", ln.split(":", 1)[1].strip()) if t]
            if not have_list:
                cur.wavelengths = wls
                have_list = True
                if len(wls) == 1:
                    # tolerate firmware that omits the per-wavelength line
                    block_wl = wls[0]
                    cur.values.setdefault(block_wl, _empty_grid())
            else:
                if not wls:
                    raise ProtocolError(f"empty wavelength block header {ln!r}")
                block_wl = wls[0]
                cur.values.setdefault(block_wl, _empty_grid())
            continue
        f = fields(ln)
        try:
            cur = PlateData(float(f[0]), float(f[1]) if len(f) > 1 else math.nan, [])
        except (ValueError, IndexError):
            raise ProtocolError(f"unexpected line in TRANSFER data: {ln!r}") from None
        plates.append(cur)
        have_list, block_wl = False, None
    return plates


# ------------------------------------------------------------------- ERROR
def parse_error_numbers(body: str) -> list:
    """``ERROR NUMBER`` -> list of pending error codes."""
    return [int(x) for x in re.findall(r"\d+", body)]
