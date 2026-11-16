"""Expose the simulator to other programs: over TCP, a serial port, or a Unix pty.

* TCP -- connect the driver with ``socket://127.0.0.1:2500``.
* serial port -- put the simulator on one end of a null-modem cable (or a
  virtual COM pair such as com0com) and let any host program, including the
  original SOFTmax PRO, talk to it.
* pty (Linux/macOS) -- a pseudo-terminal that looks like a serial device.
"""
from __future__ import annotations

import os
import socketserver
import sys
import threading
from typing import Callable, Optional

from .simulator import FlashSimulator, WireSession

Log = Optional[Callable[[str], None]]


def _trace(log: Log, direction: str, data: bytes) -> None:
    if log and data:
        log(f"{direction} {data!r}")


class _TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        session = WireSession(srv.sim, **srv.session_kw)
        if srv.log:
            srv.log(f"client connected: {self.client_address[0]}:{self.client_address[1]}")
        while True:
            try:
                data = self.request.recv(4096)
            except OSError:
                break
            if not data:
                break
            _trace(srv.log, "<-", data)
            with srv.lock:
                out = session.feed(data)
            _trace(srv.log, "->", out)
            try:
                self.request.sendall(out)
            except OSError:
                break
        if srv.log:
            srv.log("client disconnected")


class SimulatorTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 2500,
                 sim: Optional[FlashSimulator] = None, log: Log = None, **session_kw):
        self.sim = sim or FlashSimulator()
        self.log = log
        self.session_kw = session_kw
        self.lock = threading.Lock()
        super().__init__((host, port), _TCPHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"socket://{host}:{port}"

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return t


def serve_serial(port: str, baudrate: int = 9600, sim: Optional[FlashSimulator] = None,
                 log: Log = None, stop: Optional[threading.Event] = None, **session_kw) -> None:
    """Answer commands arriving on a real (or virtual) serial port until ``stop`` is set."""
    import serial

    session = WireSession(sim or FlashSimulator(), **session_kw)
    with serial.serial_for_url(port, baudrate=baudrate, bytesize=8, parity="N",
                               stopbits=1, timeout=0.1) as ser:
        while not (stop and stop.is_set()):
            data = ser.read(1)
            if not data:
                continue
            data += ser.read(ser.in_waiting or 0)
            _trace(log, "<-", data)
            out = session.feed(data)
            _trace(log, "->", out)
            ser.write(out)
            ser.flush()


def serve_pty(sim: Optional[FlashSimulator] = None, log: Log = None,
              announce: Callable[[str], None] = print,
              stop: Optional[threading.Event] = None, **session_kw) -> None:
    """Create a pseudo-terminal and answer commands on it (Linux/macOS only)."""
    if sys.platform.startswith("win"):
        raise OSError("pseudo-terminals are not available on Windows; "
                      "use --tcp, or --serial with a virtual COM pair (com0com)")
    import pty
    import select
    import termios
    import tty

    master, slave = pty.openpty()
    tty.setraw(slave)
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    announce(os.ttyname(slave))
    session = WireSession(sim or FlashSimulator(), **session_kw)
    try:
        while not (stop and stop.is_set()):
            r, _, _ = select.select([master], [], [], 0.2)
            if not r:
                continue
            try:
                data = os.read(master, 4096)
            except OSError:
                continue          # no client has the slave open right now
            _trace(log, "<-", data)
            out = session.feed(data)
            _trace(log, "->", out)
            os.write(master, out)
    finally:
        os.close(master)
        os.close(slave)
