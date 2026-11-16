"""Command-line front end:  ``odstudio --help``  (or ``python -m odstudio``)."""
from __future__ import annotations

import argparse
import os
import sys
import threading

from . import __version__
from .driver import ReadSettings, SpectraMax
from .errors import SpectraMaxError
from .readers import READER_KINDS, open_reader
from .tmax import MODEL_NAMES, TMaxReader
from .export import FORMATS, render
from .simulator import FlashSimulator
from .transport import DEFAULT_BAUD, list_ports

ENV_PORT = "ODSTUDIO_PORT"
OLD_ENV_PORT = "SPECTRAMAX_PORT"   # name used before the rename; still read


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _trace(direction: str, data: bytes) -> None:
    _err(f"  {direction} {data!r}")


def _connect(args, **kw) -> SpectraMax:
    if not args.port:
        raise SpectraMaxError(
            f"no port given. Use -p PORT (e.g. -p COM3, -p /dev/ttyUSB0, -p sim) or set "
            f"{ENV_PORT}. 'odstudio ports' lists the serial ports on this computer.")
    trace = _trace if args.verbose else None
    kind = getattr(args, "reader", "auto")
    filters = getattr(args, "filters", None) or ()
    old_sim = any(o in MODEL_NAMES for o in args.port.lower().split(":")[1:])
    if kind in MODEL_NAMES or old_sim:
        return open_reader(args.port, kind=kind if kind in MODEL_NAMES else "auto",
                           filters=filters, trace=trace)
    sm = SpectraMax(args.port, baudrate=args.baud, timeout=args.timeout, trace=trace)
    try:
        sm.connect(**kw)
        return sm
    except SpectraMaxError:
        sm.disconnect()
        if kind != "auto":
            raise
        try:                       # not a SpectraMax: maybe an older filter reader
            return _filter_reader(args, filters, trace)
        except SpectraMaxError:
            pass
        raise


def _filter_reader(args, filters, trace):
    r = TMaxReader(args.port, filters=filters, trace=trace)
    r.connect()
    return r


def _columns(text: str) -> tuple:
    try:
        a, _, b = text.partition("-")
        first, last = int(a), int(b or a)
    except ValueError:
        raise argparse.ArgumentTypeError("columns look like 1-12 or 3") from None
    if not 1 <= first <= last <= 12:
        raise argparse.ArgumentTypeError("columns must lie within 1-12")
    return first, last


def _write(args, plates) -> None:
    text = render(plates, args.format)
    if args.output:
        with open(args.output, "w", newline="") as f:
            f.write(text)
        _err(f"wrote {len(plates)} read(s) to {args.output}")
    else:
        sys.stdout.write(text)


# ------------------------------------------------------------------ commands
def cmd_ports(args) -> int:
    ports = list_ports()
    if not ports:
        print("no serial ports found (the simulator is always available as: -p sim)")
    for dev, desc in ports:
        print(f"{dev}\t{desc}")
    return 0


def cmd_info(args) -> int:
    with _connect(args) as sm:
        o = sm.info.options
        caps = sm.capabilities
        setpoint, actual = sm.temperature()
        st = sm.status()
        print(f"port         {args.port}")
        print(f"model        {sm.info.model}")
        print(f"firmware     {sm.info.version}")
        if caps["filters"] is None:
            print(f"wavelengths  {o.wavelength_min}-{o.wavelength_max} nm, step {o.wavelength_step}")
        else:
            print(f"filters      {', '.join(str(f) for f in caps['filters']) or 'none given (use --filters)'} nm")
        print(f"read modes   {', '.join(caps['modes'])}")
        if caps["temperature"]:
            print(f"incubator    {o.temp_min:g}-{o.temp_max:g} C")
        if o.extra:
            print(f"timing       {' '.join(o.extra)}")
        if caps["temperature"] and actual is not None:
            print(f"temperature  {actual:.1f} C (setpoint {'off' if setpoint == 0 else f'{setpoint:.1f} C'})")
        print(f"drawer       {st.drawer.lower()}")
        print(f"state        {st.state.lower() or '?'}")
    return 0


def cmd_status(args) -> int:
    with _connect(args) as sm:
        st = sm.status()
        print(f"drawer {st.drawer.lower()}, state {st.state.lower() or '?'}")
    return 0


def cmd_temp(args) -> int:
    with _connect(args) as sm:
        if args.value is not None:
            v = 0.0 if args.value.lower() == "off" else float(args.value)
            sm.set_temperature(v)
        setpoint, actual = sm.temperature()
        if actual is None:
            raise SpectraMaxError(f"the {sm.info.model} has no incubator")
        print(f"actual {actual:.1f} C, setpoint {'off' if setpoint == 0 else f'{setpoint:.1f} C'}")
    return 0


def cmd_drawer(args) -> int:
    with _connect(args) as sm:
        sm.open_drawer() if args.cmd == "open" else sm.close_drawer()
    return 0


def cmd_shake(args) -> int:
    with _connect(args) as sm:
        sm.shake(args.state == "on")
    return 0


def cmd_errors(args) -> int:
    with _connect(args) as sm:
        text = sm.errors()
        print(text.replace("\r", "\n") if text else "no errors logged")
        if args.clear:
            sm.clear_errors()
            _err("error log cleared")
    return 0


def cmd_send(args) -> int:
    with _connect(args, remote=False) as sm:
        for c in args.command:
            body = sm.command(c)
            print("OK" if body is None else body.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n"))
    return 0


def cmd_terminal(args) -> int:
    with _connect(args, remote=False) as sm:
        _err(f"connected to {sm.info.model or 'instrument'} ({sm.info.version}). "
             "Type commands without the leading '!'; blank line or Ctrl-D to quit.")
        while True:
            try:
                line = input("odstudio> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                break
            try:
                body = sm.command(line)
                print("OK" if body is None else body.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n"))
            except SpectraMaxError as e:
                _err(f"error: {e}")
    return 0


def cmd_read(args) -> int:
    first, last = args.columns
    common = dict(first_column=first, num_columns=last - first + 1,
                  shake_before=args.shake, autocalibrate=not args.no_calibrate)
    if args.mode == "endpoint":
        s = ReadSettings("endpoint", tuple(args.wavelength), **common)
    elif args.mode == "kinetic":
        reads = args.reads or (int(args.duration // args.interval) + 1 if args.duration else 0)
        if reads < 1:
            raise SpectraMaxError("kinetic read needs --reads or --duration")
        s = ReadSettings("kinetic", tuple(args.wavelength), interval=args.interval, reads=reads,
                         shake_between=args.shake_between, **common)
    else:
        s = ReadSettings("spectrum", (args.start,), start=args.start, stop=args.stop,
                         step=args.step, **common)
    try:
        s.validate()
    except ValueError as e:
        raise SpectraMaxError(str(e)) from None

    def on_plate(p, i, total):
        t = "" if p.temperature != p.temperature else f", {p.temperature:.1f} C"   # NaN: no sensor
        _err(f"read {i}/{total} received (t={p.time_s:g} s{t})")

    with _connect(args) as sm:
        if args.temp is not None:
            sm.set_temperature(args.temp)
        _err(f"starting {args.mode} read, about {sm.estimate_seconds(s):.0f} s ...")
        plates = sm.read(s, on_plate=on_plate, open_when_done=args.open,
                         poll_interval=0 if args.port.lower().startswith("sim") else 0.64)
    _write(args, plates)
    return 0


def cmd_gui(args) -> int:
    from .gui.server import run

    run(port=args.http_port, instrument=args.port, open_browser=not args.no_browser)
    return 0


def cmd_sim(args) -> int:
    from . import serve

    sim = FlashSimulator(faithful=args.faithful, realtime=args.realtime,
                         log=_err if args.verbose else None)
    log = _err if args.verbose else None
    stop = threading.Event()
    try:
        if args.serial:
            _err(f"simulator answering on serial port {args.serial} at {args.baud} baud; Ctrl-C stops")
            serve.serve_serial(args.serial, args.baud, sim, log, stop)
        elif args.pty:
            serve.serve_pty(sim, log, lambda name: _err(
                f"simulator answering on pseudo-terminal {name}\n"
                f"  try:  odstudio -p {name} info\nCtrl-C stops"), stop)
        else:
            host, _, port = args.tcp.rpartition(":")
            srv = serve.SimulatorTCPServer(host or "127.0.0.1", int(port), sim, log)
            _err(f"simulator listening on {srv.url}\n"
                 f"  try:  odstudio -p {srv.url} info\nCtrl-C stops")
            srv.serve_forever()
    except KeyboardInterrupt:
        _err("\nsimulator stopped")
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="odstudio",
        description="OD Studio: drive a Molecular Devices SpectraMax 250 plate reader (or an older "
                    "ThermoMax, UVmax, Vmax or Emax) over its serial port.",
        epilog=f"The port can also be given in the environment variable {ENV_PORT}. "
               "Use '-p sim' to try everything without an instrument.")
    p.add_argument("-p", "--port", default=os.environ.get(ENV_PORT) or os.environ.get(OLD_ENV_PORT),
                   help="serial port (COM3, /dev/ttyUSB0, socket://host:port) or 'sim'")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="baud rate (default 9600)")
    p.add_argument("--timeout", type=float, default=1.0,
                   help="seconds to wait between reply bytes (default 1)")
    p.add_argument("-v", "--verbose", action="store_true", help="show every byte sent and received")
    p.add_argument("--reader", choices=READER_KINDS, default="auto",
                   help="reader type (default: find out). ThermoMax, UVmax, Vmax and Emax are the "
                        "older filter readers")
    p.add_argument("--filters", type=lambda t: [int(x or 0) for x in t.split(",")], default=None,
                   metavar="NM,NM,...",
                   help="filter wavelengths by wheel position for the older readers, "
                        "e.g. 405,450,490,0,570,650 (0 = empty)")
    p.add_argument("--version", action="version", version=f"odstudio {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    sub.add_parser("ports", help="list serial ports on this computer").set_defaults(fn=cmd_ports)
    sub.add_parser("info", help="identify the instrument and show its state").set_defaults(fn=cmd_info)
    sub.add_parser("status", help="drawer and activity state").set_defaults(fn=cmd_status)

    t = sub.add_parser("temp", help="show the temperature, or set the incubator")
    t.add_argument("value", nargs="?", help="setpoint in C, or 'off'")
    t.set_defaults(fn=cmd_temp)

    sub.add_parser("open", help="open the drawer").set_defaults(fn=cmd_drawer)
    sub.add_parser("close", help="close the drawer").set_defaults(fn=cmd_drawer)

    s = sub.add_parser("shake", help="start or stop shaking now")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn=cmd_shake)

    e = sub.add_parser("errors", help="show the instrument's error log")
    e.add_argument("--clear", action="store_true", help="clear the log afterwards")
    e.set_defaults(fn=cmd_errors)

    c = sub.add_parser("send", help="send raw commands, e.g.  send STATUS 'TEMP 37'")
    c.add_argument("command", nargs="+")
    c.set_defaults(fn=cmd_send)

    sub.add_parser("terminal", help="interactive command prompt").set_defaults(fn=cmd_terminal)

    r = sub.add_parser("read", help="read a plate")
    r.set_defaults(fn=cmd_read)
    modes = r.add_subparsers(dest="mode", metavar="MODE", required=True)

    def common(m):
        m.add_argument("-c", "--columns", type=_columns, default=(1, 12),
                       help="plate columns to read, e.g. 1-12 (default) or 3-6")
        m.add_argument("--shake", type=int, default=0, metavar="S",
                       help="shake S seconds before the first read")
        m.add_argument("--no-calibrate", action="store_true", help="skip the automatic calibration")
        m.add_argument("--temp", type=float, metavar="C", help="set the incubator first (0 = off)")
        m.add_argument("--open", action="store_true", help="open the drawer when finished")
        m.add_argument("-o", "--output", help="write results to this file instead of the screen")
        m.add_argument("-f", "--format", choices=sorted(FORMATS), default="plate",
                       help="output layout (default: plate)")

    m = modes.add_parser("endpoint", help="single read")
    m.add_argument("-w", "--wavelength", type=int, nargs="+", default=[405], metavar="NM",
                   help="1 to 6 wavelengths (default 405)")
    common(m)
    m = modes.add_parser("kinetic", help="repeated reads over time")
    m.add_argument("-w", "--wavelength", type=int, nargs="+", default=[405], metavar="NM")
    m.add_argument("-i", "--interval", type=int, required=True, metavar="S", help="seconds between reads")
    m.add_argument("-n", "--reads", type=int, default=0, help="number of reads")
    m.add_argument("-d", "--duration", type=float, default=0, metavar="S",
                   help="total run time in seconds (instead of --reads)")
    m.add_argument("--shake-between", type=int, default=0, metavar="S",
                   help="shake S seconds before each later read")
    common(m)
    m = modes.add_parser("spectrum", help="wavelength scan")
    m.add_argument("--start", type=int, required=True, metavar="NM")
    m.add_argument("--stop", type=int, required=True, metavar="NM")
    m.add_argument("--step", type=int, default=10, metavar="NM", help="default 10")
    common(m)

    gui = sub.add_parser("gui", help="open the graphical program (the SOFTmax PRO replacement)")
    gui.add_argument("--http-port", type=int, default=8250, help="local port for the window (default 8250)")
    gui.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    gui.set_defaults(fn=cmd_gui)

    x = sub.add_parser("sim", help="run the instrument simulator for other programs to connect to")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--tcp", default="127.0.0.1:2500", metavar="HOST:PORT",
                   help="listen on TCP (default 127.0.0.1:2500)")
    g.add_argument("--serial", metavar="PORT", help="answer on a serial port (null-modem / virtual pair)")
    g.add_argument("--pty", action="store_true", help="create a pseudo-terminal (Linux/macOS)")
    x.add_argument("--faithful", action="store_true",
                   help="behave exactly like the simulator built into SOFTmax PRO")
    x.add_argument("--realtime", action="store_true", help="make reads take realistic time")
    x.set_defaults(fn=cmd_sim)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 2
    try:
        return args.fn(args)
    except SpectraMaxError as e:
        _err(f"error: {e}")
        return 1
    except ValueError as e:
        _err(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        _err("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
