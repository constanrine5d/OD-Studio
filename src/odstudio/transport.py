"""Byte transports: a real serial port (any OS), a TCP socket, or the in-process simulator.

Port strings understood by :func:`open_transport`:

``sim`` / ``sim:faithful`` / ``sim:realtime``
    the built-in SpectraMax 250 simulator, no hardware needed
``sim:thermomax`` / ``sim:uvmax`` / ``sim:vmax`` / ``sim:emax`` (``:realtime`` may be added)
    a simulated older filter reader (:mod:`odstudio.tmaxsim`)
``COM3``, ``/dev/ttyUSB0``, ``/dev/cu.usbserial-1420`` ...
    a serial port (9600 8N1, as SOFTmax PRO opens it)
``socket://host:port``, ``rfc2217://host:port``, ``loop://`` ...
    anything else pyserial's ``serial_for_url`` accepts (serial-over-network
    boxes, or ``odstudio sim --tcp``)
"""
from __future__ import annotations

import time
from typing import Optional

from .errors import TransportError
from .simulator import FlashSimulator, WireSession

# SOFTmax PRO builds "COMn:9600,n,8,1" for BuildCommDCB (SPF.EXE seg63:0ff9).
DEFAULT_BAUD = 9600


class Transport:
    """Minimal interface the driver needs."""

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read(self, timeout: float) -> bytes:
        """Return whatever arrives within ``timeout`` seconds (b"" if nothing)."""
        raise NotImplementedError

    def reset_input(self) -> None:
        """Throw away unread input."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    def send_break(self, duration: float = 0.06) -> None:
        """Hold the line in BREAK for ``duration`` seconds (used to resynchronise old readers)."""

    name = "?"


class SerialTransport(Transport):
    def __init__(self, port: str, baudrate: int = DEFAULT_BAUD, *,
                 xonxoff: bool = False, rtscts: bool = False, exclusive: bool = False):
        try:
            import serial
        except ImportError as e:  # pragma: no cover
            raise TransportError("pyserial is required: pip install pyserial") from e
        self.name = port
        try:
            self._ser = serial.serial_for_url(
                port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                timeout=0.05, write_timeout=5, xonxoff=xonxoff, rtscts=rtscts,
                **({"exclusive": True} if exclusive else {}))
        except Exception as e:
            raise TransportError(f"cannot open {port}: {e}") from e

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
            self._ser.flush()
        except Exception as e:
            raise TransportError(f"write to {self.name} failed: {e}") from e

    def read(self, timeout: float) -> bytes:
        try:
            self._ser.timeout = max(0.0, timeout)
            first = self._ser.read(1)
            if not first:
                return b""
            waiting = self._ser.in_waiting
            return first + (self._ser.read(waiting) if waiting else b"")
        except Exception as e:
            raise TransportError(f"read from {self.name} failed: {e}") from e

    def reset_input(self) -> None:
        try:
            self._ser.reset_input_buffer()
            # network/loop transports may still have bytes in flight
            self._ser.timeout = 0
            while self._ser.read(4096):
                pass
        except Exception as e:
            raise TransportError(f"{self.name}: {e}") from e

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass

    def send_break(self, duration: float = 0.06) -> None:
        try:
            self._ser.send_break(duration)
        except Exception as e:
            raise TransportError(f"{self.name}: {e}") from e


class SimulatorTransport(Transport):
    """The simulator wired directly to the driver, no OS resources involved."""

    name = "sim"

    def __init__(self, sim: Optional[FlashSimulator] = None, **session_kw):
        self.sim = sim or FlashSimulator()
        self.session = WireSession(self.sim, **session_kw)
        self._rx = bytearray()

    def write(self, data: bytes) -> None:
        self._rx += self.session.feed(data)

    def read(self, timeout: float) -> bytes:
        if not self._rx:
            if timeout > 0:
                time.sleep(min(timeout, 0.001))
            return b""
        data = bytes(self._rx)
        self._rx.clear()
        return data

    def reset_input(self) -> None:
        self._rx.clear()


def open_transport(port: str, baudrate: int = DEFAULT_BAUD, **kw) -> Transport:
    p = port.strip()
    if p.lower() == "sim" or p.lower().startswith("sim:"):
        opts = {o for o in p.lower().split(":")[1:] if o}
        from .tmaxsim import MODELS as TMAX_MODELS, TMaxSimTransport
        old = opts & set(TMAX_MODELS)
        if old:
            unknown = opts - old - {"realtime"}
            if len(old) > 1 or unknown:
                raise TransportError(f"unknown simulator option(s): {', '.join(sorted(opts))}")
            return TMaxSimTransport(old.pop(), realtime="realtime" in opts)
        unknown = opts - {"faithful", "realtime"}
        if unknown:
            raise TransportError(f"unknown simulator option(s): {', '.join(sorted(unknown))}")
        return SimulatorTransport(FlashSimulator(faithful="faithful" in opts,
                                                 realtime="realtime" in opts))
    return SerialTransport(p, baudrate, **kw)


def list_ports() -> list:
    """(device, description) for every serial port the OS knows about."""
    return [(p["device"], p["description"]) for p in list_ports_info()]


# Ports that are never a plate reader: probing them is pointless (and Bluetooth may try to
# connect to a phone).
_NOT_READERS = ("bluetooth", "debug-console", "wlan-debug", "iphone", "airpods")


def list_ports_info() -> list:
    """Serial ports with what is known about each (USB adapter identity, if any)."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:  # pragma: no cover
        return []
    out = []
    for p in sorted(lp.comports()):
        desc = p.description if p.description and p.description != "n/a" else ""
        name = f"{p.device} {desc} {p.hwid or ''}".lower()
        out.append({"device": p.device, "description": desc, "hwid": p.hwid or "",
                    "vid": p.vid, "pid": p.pid, "serial_number": p.serial_number or "",
                    "manufacturer": p.manufacturer or "", "location": p.location or "",
                    "usb": p.vid is not None,
                    "likely_reader": not any(k in name for k in _NOT_READERS),
                    # ticked by default in Find Reader: USB adapters and built-in serial ports
                    "suggest": not any(k in name for k in _NOT_READERS) and (
                        p.vid is not None or "pnp0501" in name or "/dev/ttys" in name)})
    return out


def port_identity(device: str) -> Optional[dict]:
    """What identifies the adapter behind ``device`` independently of its name."""
    for p in list_ports_info():
        if p["device"] == device and p["usb"]:
            return {k: p[k] for k in ("vid", "pid", "serial_number", "description", "location")}
    return None


def find_port(identity: dict) -> Optional[str]:
    """The current name of the adapter ``identity`` describes, if it is plugged in.

    A USB serial number pins the adapter down; without one, the same type of adapter
    counts only when exactly one is plugged in."""
    if not identity:
        return None
    ports = [p for p in list_ports_info() if p["usb"]
             and (p["vid"], p["pid"]) == (identity.get("vid"), identity.get("pid"))]
    if identity.get("serial_number"):
        ports = [p for p in ports if p["serial_number"] == identity["serial_number"]]
    return ports[0]["device"] if len(ports) == 1 else None
