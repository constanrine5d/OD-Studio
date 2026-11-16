"""Simulator of the older Molecular Devices filter readers: ThermoMax, UVmax, Vmax, Emax.

SOFTmax PRO 1.x has no built-in simulator for these readers (it only has one for the
SpectraMax 250), so this one is written from what the host program expects
(``docs/PROTOCOL-TMAX.md``).  Where the host does not pin a detail down -- the exact
VER text, the byte that precedes a data block, the byte order of the data, error
numbers -- the choices here are marked *[guess]*; a real reader may differ.

Wire behaviour:

* Every received character is echoed.  A command has no terminator: the reader knows
  it by its fixed length (3-letter mnemonic, ``TMP`` + 4 characters, ``SWV`` + 1,
  ``DWV`` + 3, ``SKO`` + 2).  The echo of the last character is sent once the command
  has been carried out, so the host's longer timeout for it covers the work.
* A rejected command gets ``BEL <number> CR LF`` instead of the last echo.
* Read commands are followed by one lead byte and 96 signed 16-bit values in milli-OD,
  row by row (A1..A12, B1..H12).
* A lone CR is echoed, except right after a BREAK, when it is answered with
  ``BEL 0 CR LF``.
"""
from __future__ import annotations

import math
import random
import struct
import time
from typing import Optional

from .transport import Transport

# name used in the VER reply [guess], kinetic, shaking, incubator + status
MODELS = {
    "thermomax": {"name": "ThermoMax", "ver": "THERMOmax", "kinetic": True, "shake": True, "incubator": True},
    "uvmax": {"name": "UVmax", "ver": "UVmax", "kinetic": True, "shake": True, "incubator": False},
    "vmax": {"name": "Vmax", "ver": "Vmax", "kinetic": True, "shake": True, "incubator": False},
    "emax": {"name": "Emax", "ver": "Emax", "kinetic": False, "shake": False, "incubator": False},
}
DEFAULT_FILTERS = {"uvmax": [340, 405, 450, 490, 550, 650]}
FILTERS = [405, 450, 490, 540, 570, 650]

LENGTHS = {"TMP": 7, "SWV": 4, "DWV": 6, "SKO": 5}
SIMPLE = {"RMT", "LCL", "OPN", "CLS", "SHK", "DSH", "CON", "CFF", "VER", "RDT", "RDS", "STS",
          "RDP", "RNE", "STK", "NXK", "RDR", "RNR"}
READS = {"RDP", "RNE", "STK", "NXK", "RDR", "RNR"}
LEAD = b"\x02"                  # byte before a data block [guess]
AMBIENT = 23.0

# BEL error numbers [guess]
E_UNKNOWN, E_NOT_AVAILABLE, E_BAD_ARG, E_NO_START = 2, 4, 5, 6


class TMaxSimulator:
    def __init__(self, model: str = "vmax", *, realtime: bool = False, byteorder: str = "big",
                 seed: int = 1):
        if model not in MODELS:
            raise ValueError(f"unknown model {model!r}")
        self.model = model
        self.spec = MODELS[model]
        self.realtime = realtime
        self.byteorder = byteorder        # the original program was a Mac (68k, big-endian)
        self.filters = DEFAULT_FILTERS.get(model, FILTERS)
        self.rng = random.Random(seed)
        self.drawer_open = True
        self.remote = False
        self.automix = False
        self.setpoint = 0.0
        self.incubator_on = False
        self.filter_a = 0
        self.filter_b: Optional[int] = None
        self.kinetic_index = 0
        self.started = time.monotonic()
        self.after_break = False
        self._buf = ""

    # ------------------------------------------------------------------ optics
    def well_od(self, read_idx: int, row: int, col: int) -> float:
        """Plausible absorbance: rises across the plate, down the rows and with time."""
        wl = self.filters[self.filter_a]
        v = (math.exp((col + row * 2.0 + read_idx) / 10.0) + (wl % 7) / 7.0) / 10.0
        if self.filter_b is not None:
            v -= 0.04                                     # reference filter background
        return max(-0.5, min(v, 3.5))

    def temperature(self) -> float:
        if not self.incubator_on or self.setpoint <= 0:
            return AMBIENT
        if not self.realtime:
            return self.setpoint
        warm = (time.monotonic() - self._heat_from) / 60.0        # about 1 degC per minute
        return min(self.setpoint, AMBIENT + warm) if self.setpoint > AMBIENT else self.setpoint

    # -------------------------------------------------------------------- wire
    def on_break(self) -> None:
        self._buf = ""
        self.after_break = True

    def feed(self, data: bytes) -> list:
        """Bytes from the host -> list of (delay seconds, bytes to send)."""
        out: list = []
        for b in data:
            ch = chr(b)
            if ch == "\r" and not self._buf:
                if self.after_break:
                    self.after_break = False
                    out.append((0.0, b"\x070\r\n"))
                else:
                    out.append((0.0, b"\r"))
                continue
            if ch in "\r\n":
                continue
            self.after_break = False
            self._buf += ch.upper()
            need = LENGTHS.get(self._buf[:3], 3) if len(self._buf) >= 3 else 99
            if len(self._buf) < 3 or (len(self._buf) < need):
                out.append((0.0, bytes([b])))                 # echo at once
                continue
            cmd, self._buf = self._buf, ""
            delay, reply = self.execute(cmd)
            if reply.startswith(b"\x07"):
                out.append((delay, reply))
            else:
                out.append((delay, bytes([b]) + reply))     # echo of the last character + reply
        return out

    def _err(self, n: int) -> tuple:
        return 0.0, f"\x07{n}\r\n".encode()

    def execute(self, cmd: str) -> tuple:
        """Carry out one command; returns (seconds it takes, bytes after the last echo)."""
        name = cmd[:3]
        if name not in SIMPLE and name not in LENGTHS:
            return self._err(E_UNKNOWN)
        if name == "RMT":
            self.remote = True
        elif name == "LCL":
            self.remote = False
        elif name == "OPN":
            self.drawer_open = True
            return (1.5 if self.realtime else 0.0), b""
        elif name == "CLS":
            self.drawer_open = False
            return (1.5 if self.realtime else 0.0), b""
        elif name in ("SHK", "DSH"):
            if not self.spec["shake"]:
                return self._err(E_NOT_AVAILABLE)
            self.automix = name == "SHK"
        elif name == "SKO":
            if not self.spec["shake"]:
                return self._err(E_NOT_AVAILABLE)
            return (3.0 if self.realtime else 0.0), b""
        elif name in ("TMP", "CON", "CFF", "RDT", "RDS", "STS"):
            if not self.spec["incubator"]:
                return self._err(E_NOT_AVAILABLE)
            if name == "TMP":
                try:
                    t = float(cmd[3:])
                except ValueError:
                    return self._err(E_BAD_ARG)
                if not 15.0 <= t <= 42.0:
                    return self._err(E_BAD_ARG)
                self.setpoint = t
            elif name == "CON":
                self.incubator_on = True
                self._heat_from = time.monotonic()
            elif name == "CFF":
                self.incubator_on = False
            elif name == "RDT":
                return 0.0, f" {self.temperature():4.1f} \r\n".encode()
            elif name == "RDS":
                return 0.0, f" {self.setpoint if self.incubator_on else 0.0:4.1f} \r\n".encode()
            else:
                bits = (1 if self.incubator_on else 0) | (2 if self.drawer_open else 4)
                return 0.0, f"{bits}\r\n".encode()
        elif name == "VER":
            return 0.0, (f"Molecular Devices; {self.spec['ver']} VER 1.0\r\n"
                         f"OD Studio simulator\r\n").encode()
        elif name in ("SWV", "DWV"):
            try:
                a = int(cmd[3]) - 1
                b = int(cmd[5]) - 1 if name == "DWV" else None
            except (ValueError, IndexError):
                return self._err(E_BAD_ARG)
            if not 0 <= a < 6 or (b is not None and (not 0 <= b < 6 or cmd[4] != ",")):
                return self._err(E_BAD_ARG)
            self.filter_a, self.filter_b = a, b
        elif name in READS:
            if name in ("STK", "NXK", "RDR", "RNR") and not self.spec["kinetic"]:
                return self._err(E_NOT_AVAILABLE)
            if name in ("NXK", "RNR") and self.kinetic_index == 0:
                return self._err(E_NO_START)
            if name in ("RDP", "RNE"):
                idx = 0
            elif name in ("STK", "RDR"):
                self.kinetic_index = idx = 1
            else:
                self.kinetic_index += 1
                idx = self.kinetic_index
            self.drawer_open = False
            vals = [int(round(self.well_od(idx, r, c) * 1000)) for r in range(8) for c in range(12)]
            fmt = (">" if self.byteorder == "big" else "<") + "96h"
            seconds = (5.0 if self.filter_b is None else 10.0) if self.realtime else 0.0
            return seconds, LEAD + struct.pack(fmt, *vals)
        return 0.0, b""


class TMaxSimTransport(Transport):
    """The simulator wired straight to the driver; replies are released after their delay."""

    def __init__(self, model: str = "vmax", *, realtime: bool = False, byteorder: str = "big"):
        self.sim = TMaxSimulator(model, realtime=realtime, byteorder=byteorder)
        self.name = f"sim:{model}"
        self._queue: list = []          # [release time, bytes]
        self._busy_until = 0.0

    def write(self, data: bytes) -> None:
        now = time.monotonic()
        for delay, chunk in self.sim.feed(data):
            at = max(now, self._busy_until) + delay
            self._busy_until = at
            self._queue.append([at, chunk])

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            now = time.monotonic()
            out = bytearray()
            while self._queue and self._queue[0][0] <= now:
                out += self._queue.pop(0)[1]
            if out:
                return bytes(out)
            if now >= deadline:
                return b""
            wait = deadline - now
            if self._queue:
                wait = min(wait, max(0.0, self._queue[0][0] - now))
            time.sleep(min(wait, 0.05) if wait > 0 else 0)

    def reset_input(self) -> None:
        now = time.monotonic()
        self._queue = [q for q in self._queue if q[0] > now]

    def send_break(self, duration: float = 0.06) -> None:
        self._queue.clear()
        self.sim.on_break()
