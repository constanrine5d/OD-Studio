# Command line, Python and simulator

Everything the program does is also available without the window. Back to the [README](../README.md).

## Install for the command line

From the OD Studio folder (needs Python 3.9+):

```bash
pip install -e .
```

## Try it without an instrument

```bash
odstudio -p sim info
```

```bash
odstudio -p sim read endpoint -w 405 650
```

## Command line

Give the port with `-p`, or set the environment variable `ODSTUDIO_PORT`.

| Port | Meaning |
|---|---|
| `COM3`, `/dev/ttyUSB0`, `/dev/cu.usbserial-…` | serial port (`odstudio ports` lists them) |
| `sim`, `sim:realtime`, `sim:faithful` | built-in simulator |
| `socket://host:port` | TCP, e.g. a serial-to-Ethernet box or `odstudio sim` |

| Command | What it does |
|---|---|
| `ports` | list serial ports |
| `info` | model, firmware, ranges, temperature, drawer |
| `status` | drawer and activity |
| `temp` / `temp 37` / `temp off` | show or set the incubator |
| `open` / `close` | drawer |
| `shake on` / `shake off` | shake now |
| `errors [--clear]` | instrument error log |
| `read endpoint -w 405 650` | single read, 1–6 wavelengths |
| `read kinetic -w 405 -i 30 -n 20` | 20 reads, 30 s apart (`-d` gives a duration instead) |
| `read spectrum --start 300 --stop 700 --step 10` | wavelength scan |
| `send STATUS "TEMP 37"` | raw commands |
| `terminal` | interactive prompt |
| `sim` | run the simulator for other programs |

Read options: `-c 3-6` columns, `--shake S`, `--shake-between S` (kinetic), `--no-calibrate`,
`--temp C`, `--open`, `-o FILE`, `-f plate|csv|json`. Add `-v` before the command to see every
byte on the wire.

## Python

```python
from odstudio import SpectraMax

with SpectraMax("COM3") as sm:              # or "/dev/ttyUSB0", or "sim"
    print(sm.info.model, sm.info.version)
    sm.set_temperature(37)
    plate = sm.read_endpoint([405, 650], columns=(1, 12), shake=5)
    print(plate.well("A1", 405))

    reads = sm.read_kinetic([405], interval=30, reads=20)
    curve = [(p.time_s, p.well("B3")) for p in reads]
```

`plate.values[wavelength][row][column]` holds the 8 × 12 grid (`None` = not read, `inf` =
saturated). `odstudio.export.render(plates, "csv")` formats results.

## Simulator

```bash
odstudio sim --tcp 127.0.0.1:2500
```

- `--pty` creates a pseudo-terminal (Linux/macOS).
- `--serial PORT` answers on a real or virtual serial port, e.g. one end of a null-modem cable
  with SOFTmax PRO on the other.
- `--realtime` makes reads take realistic time.
- `--faithful` reproduces the original simulator exactly, quirks included.

## Development

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

| Path | Contents |
|---|---|
| `src/odstudio/protocol.py` | framing and reply parsers |
| `src/odstudio/driver.py` | `SpectraMax` class |
| `src/odstudio/simulator.py` | instrument simulator |
| `src/odstudio/transport.py`, `serve.py` | serial / TCP / pty plumbing |
| `src/odstudio/cli.py` | command line |
| `src/odstudio/gui/` | the program (local web server + browser interface) |
| `src/odstudio/legacy.py` | reader for original SOFTmax PRO `.pda` / `.ppr` files |
| `tools/` | helpers used for the reverse engineering (they need your own copy of SOFTmax PRO) |
| [PROTOCOL.md](PROTOCOL.md) | serial protocol reference |
| [FEATURES.md](FEATURES.md) | every SOFTmax PRO 1.x feature and where it is in OD Studio |
