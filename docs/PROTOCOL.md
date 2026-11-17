# SpectraMax 250 serial protocol

Reverse-engineered from SOFTmax PRO 1.x for Windows 3.1 (`binary/SOFTPRO/SPF.EXE`, 16-bit NE,
Borland C++ 1994). Inside the program the SpectraMax 250 is called **"Flash"**.

Addresses such as `68:37ae` mean *code segment 68, offset 0x37ae* of SPF.EXE. Regenerate the
listings with the tools in `tools/` (see the end of this file).

**Confidence marks**

- **[code]** read directly from the disassembly
- **[sim]** behaviour of the simulator Molecular Devices compiled into SPF.EXE (`CSFlashSim`,
  segment 69) — what the vendor's own stand-in instrument answers
- **[later models]** matches the publicly documented behaviour of later SpectraMax models
  (384 Plus, M5), which kept this command family
- **[unknown]** not determinable without capturing a real instrument; see
  [Open questions](#open-questions)

## Where things are in SPF.EXE

| Segment | Source file | Class | Role |
|---|---|---|---|
| 63 | `PLATFORM\SERIALCM.CPP` | `CSSerialComm` | Win16 COMM wrapper |
| 64 | `SOURCES\RDR.CPP` | `CSReader` | facade, instrument detection, vtables |
| 65 | | `CSReaderInterface`, `CSReaderChore` | base class, 0.64 s poll timer |
| 66 | `SOURCES\TMAXINTE.CPP` | Tmax/UVmax/Vmax/Emax | older instruments, different protocol: [PROTOCOL-TMAX.md](PROTOCOL-TMAX.md) |
| 68 | `SOURCES\FLASHINT.CPP` | `CSFlashInterface` | **SpectraMax host side** |
| 69 | | `CSFlashSim` | **built-in instrument simulator** |
| 70 | `SOURCES\TESTINTR.CPP` | `CSTestInterface` | factory test variant (`CALIGN`, `!ADC_DIAG`) |
| 117 | `SOURCES\MCSTRING.CPP` | `CString` | MFC-like string class, 0-based |

Class chain: `CSSerialComm → CSReaderInterface → CSFlashInterface → CSFlashSim / CSTestInterface`.
The interface object *is* the serial port object.

## Serial line

- 9600 baud, 8 data bits, no parity, 1 stop bit. The host builds `"COMn:9600,n,8,1"` for
  `BuildCommDCB` and applies it unmodified (`63:103c`, `63:113a`). **[code]**
- No XON/XOFF and no hardware handshake are configured. **[code: no DCB patching]**
- The host never changes baud rate. The firmware has a `BAUD` command (1200, 9600, 19200,
  38400 per the help text at `69:00f5`), which the host never sends. **[code]**
- Receive queue 8192 bytes, transmit queue 512 bytes (`63:1166`). **[code]**

## Framing

**Host → instrument:** `!` + command + CR (0x0D). No LF. (`68:38f6`–`68:3987`) **[code]**

- Before each command the host discards any unread input (`68:39ac`–`68:3a2a`).
- An empty command, bytes `21 0D`, is the "are you there" probe and must be answered OK.
- Commands are upper-case words with space-separated arguments. The simulator upper-cases its
  input and splits on space, tab and CR, keeping at most 6 arguments (`69:0de0`). **[sim]**

**Instrument → host:** text ending in the prompt character `>` (0x3E).

- **Block 1 (status), every command.** The host reads bytes until `>` (`68:3ac2`), upper-cases
  the text and looks for `OK` (`68:3f90`). **[code]**
  - If `FAIL` is present instead, the error is `1000 + atoi(reply.Mid(6, 3))` (`68:4051`):
    a number of up to three digits at characters 6–8.
  - If neither is present: host error 201.
  - The simulator sends `ok<TAB><CR>>` or `fail<TAB><n><CR>>` (`69:0bf2`–`69:0c44`). **[sim]**
- **Block 2 (data), only for commands that return data.** The host reads again up to the next
  `>` and **discards the first two characters** (`Mid(2)`, `68:3d87`). **[code]**
  - Data-returning commands as used by the host: `VER`, `OPTION`, `TEMP` (no argument), `TIME`
    (no argument), `FILTER`, `STATUS`, `QUEUE`, `TRANSFER`, `ERROR`.
  - If block 2 times out, the host sends `!RESEND<CR>` once (expects OK) and reads again
    (`68:3dbd`).
- Fields are separated by TAB, lines by CR or CR LF. The host cuts fields at exact separators
  (`115:036c`); a missing separator yields an empty field.

**Timing** **[code]**

- A "tick" is 16 ms: `ASIPORT2.TICKCOUNT` is `GetTickCount() >> 4` (ASIPORT2 `17:1e46`).
- Inter-byte timeout: 60 ticks ≈ 0.96 s (`68:17f3`); 6 ticks ≈ 0.1 s for the connect probe
  (`68:34dc`). There is no overall deadline.
- Host error 101 = nothing received; 105 = partial reply without `>` (`68:3b06`).
- The host polls every 40 ticks ≈ 0.64 s (`65:0153`).

## Commands

The simulator's command table (`DS:0c4e`, 32 entries, help text at `69:00b3`…) lists what the
firmware understands:

`AT BAUD BEEP BURNIN CALIBRATE CANCEL CLEAR CLOSE DIAG DISPLAY ERROR FILTER HELP MODE OPEN
OPTION QUEUE READ REMOTE RESET SHAKE RETRY STATUS STEP STRIP STTY TEMP TIME TRANSFER VER VOLUME
WAVELENGTH`

Commands the host actually sends, with the simulator's reply:

| Command | Data block **[sim]** | Notes |
|---|---|---|
| *(empty)* | – | probe |
| `REMOTE ON` / `REMOTE OFF` | – | sent at connect / disconnect (`68:1b10`, `68:3e98`) |
| `DISPLAY 0 "text"` / `DISPLAY 40 "text"` | – | the two LCD lines; text in double quotes |
| `VER` | `Version 0.1 of the simulator<CR>` | host keeps text up to the first CR (`68:2684`) |
| `OPTION` | see below | capabilities |
| `STATUS` | `OPEN<TAB>MEAS<TAB><CR><LF>` | field 1 drawer (`OPEN`/`CLOSED`), field 2 activity |
| `TEMP` | `<setpoint><TAB><actual><CR>`, `%1.1f` | setpoint 0 = incubator off |
| `TEMP <t>` | – | 0, or 15.0–45.0 °C; host sends the user's text, e.g. `37.0` |
| `OPEN` / `CLOSE` | – | drawer |
| `SHAKE NOW` / `SHAKE STOP` | – | manual shake (`68:2256`) |
| `RESET` | – | power-on state; host follows with `REMOTE ON` |
| `FILTER` | `<n1><CR><LF><n2><CR><LF><CR><LF>` | host parses `<n1><TAB><n2><CR>` (`68:2f14`) |
| `ERROR` | error log text | free text |
| `CLEAR` | – | clears the error log; `CLEAR DATA` clears the read queue |
| `TIME` | `<seconds><CR>` | instrument clock; `TIME <n>` sets it |
| `WAVELENGTH w1 w2 …` | – | 1–6 integers, nm |
| `STRIP <first> <count>` | – | columns, first is 1-based |
| `CALIBRATE ON` / `ONCE` / `OFF` | – | |
| `MODE ENDPOINT` | – | |
| `MODE KINETIC <interval_s> <reads>` | – | simulator: interval ≥ 6, reads 1–10000 |
| `MODE SPECTRUM <start> <step> <points>` | – | simulator: start ≥ 250, start + step·points ≤ 800 |
| `READ` | – | starts the read |
| `QUEUE` | `<n><CR>` | finished reads waiting |
| `TRANSFER` | one read, see below | removes it from the queue |
| `CANCEL` | – | aborts and empties the queue |
| `RESEND` | – | repeat the last reply (the simulator's table calls it `RETRY`) |

Query forms the host does not use but the simulator answers: `WAVELENGTH` → `405<TAB><CR>`;
`STRIP` → `1<TAB>12<CR>`; `MODE` → `Endpoint<CR>`, `Kinetic<TAB>10/5<CR>` or
`Sweep<TAB>300<TAB>10<TAB>20<CR>`; `BAUD` → `19200<CR>`; `HELP` → the command list;
`ERROR LIST`, `ERROR NUMBER`, `ERROR <n>`.

### OPTION

Simulator reply (`69:2eb0`): `Simulator\r\n250\t750\t1\r\n15\t45\r\n400\t225\t5\t2\r\n`

Host parse order (`68:2807`–`68:2d3d`) **[code]**:

| Field | Ends with | Meaning | Host default |
|---|---|---|---|
| model name | CR LF | shown to the user | |
| λ min, λ max | TAB | nm | 250, 750 |
| λ step | CR LF | nm | 1 |
| T min | TAB | °C | 15 |
| T max | CR | °C | 45 |
| a | TAB | ms to read one strip (inferred from `68:30ce`) | 400 |
| b | TAB | ms per strip move (inferred) | 225 |
| c | TAB | ms per nm of wavelength change (inferred) | 5 |
| d | TAB | minimum read time, s (inferred) | 2 |
| e | CR LF | fixed ms per wavelength pass (inferred) | 500 |

Fields a–e are only parsed when the `VER` text does not contain `1.0`. The simulator sends
four of the five.

### Starting a read

Exact order sent by SOFTmax PRO (`68:029a`) **[code]**; every command must be answered OK:

1. `REMOTE ON`, `DISPLAY 0 "…"`, `DISPLAY 40 "…"`
2. `CLEAR DATA`
3. `TIME` — the host remembers (its clock − instrument clock) to timestamp the reads
4. `WAVELENGTH 405 650 ` — each value `%ld` followed by a space, including the last. An entry
   may carry an `F` prefix (`F405`, flag from the plate settings, `68:04ba`); its meaning is
   **[unknown]**, presumably "use a filter". In spectrum mode only one wavelength is sent.
5. `SHAKE ON` if any shaking is requested, else `SHAKE OFF`
6. `SHAKE  A B C D 0` — always sent, two spaces after `SHAKE`, integers:
   A = seconds of shaking before the first read (0 if none), B = kinetic interval,
   C = B − D, D = seconds of shaking before each later read (0 if none)
7. `STRIP <first> <count>`
8. `CALIBRATE …`
   - endpoint / spectrum: `ONCE` if auto-calibrate is enabled, else `OFF`
   - kinetic: `ON` if auto-calibrate is enabled, else `ONCE`
9. `MODE ENDPOINT` | `MODE KINETIC <interval> <reads>` | `MODE SPECTRUM <start> <step> <points>`
   - reads = run time / interval + 1; points = (stop − start) / step + 1
10. `READ`

### Collecting the data

Each poll (`68:0ea2`) **[code]**:

1. `QUEUE` → n.
2. If n > 0: up to 8 × `TRANSFER`, one read each.
3. If n = 0: `STATUS`. Field 2 must contain `CAL` (then an integer follows before CR — a
   calibration countdown) or `MEAS`; anything else is host error 104.
4. When all expected reads have arrived, the host sends `OPEN`. On user abort it sends `CANCEL`
   first.

### TRANSFER data

Written by the simulator at `69:2a36`, read by the host at `68:1146` and `68:41ce`:

```
<seconds> TAB <temperature %1.1f> CR
L: TAB <wl1> TAB <wl2> TAB ... CR              wavelength list (host skips this line)
L: TAB <wl1> CR                                one block per wavelength
<col>: TAB <A> TAB <B> TAB ... <H> TAB CR      one line per column; 8 values, rows A..H, %1.4f
...
CR                                             blank line ends the block
```

- `<seconds>` is on the instrument's `TIME` clock. The host rejects temperatures outside
  0–50 °C.
- Well order is column-major: each line is one strip of 8 wells.
- Later models use the same layout with CR LF line ends and `#SAT` for saturated wells.
  **[later models]**

### Error numbers

- Simulator (`69:0027`): 1 unknown command, 2 wrong number of parameters, 3 parameter out of
  range, 4 command not implemented, 5 error overflow. The log holds 64 entries.
- Real firmware codes are three digits (the host reads three characters). The table in
  `src/odstudio/errors.py` uses the meanings documented for later models (100 command not
  found … 505 NVRAM stage offset error). **[later models]**

## Connecting and detecting the instrument

SpectraMax path (`68:347c`, `68:2684`) **[code]**:

`!<CR>` → `REMOTE ON` → `DISPLAY 0 "…"` → `DISPLAY 40 "…"` → `VER` → `OPTION` → `TEMP`

While idle the host repeats `!<CR>` + `STATUS` every 0.64 s; the first four characters of the
STATUS data (`OPEN`) give the drawer state.

Detection (`64:0cea`): a device that answers `!<CR>` with a block containing `OK` is a
SpectraMax (type 5). Otherwise the host falls back to the older instruments' echo protocol
(`VER`, `SHK`): `TH` → ThermoMax, `UV` → UVmax, `Em` → Emax, `SHK` accepted → Vmax. The full
sequence and the older readers' protocol are in [PROTOCOL-TMAX.md](PROTOCOL-TMAX.md).

## Factory service commands

SOFTmax PRO only offers these when it runs as a factory test station (`CSTestInterface`,
segment 70, interface type 7): the Instrument Settings popup *Normal / Diagnostic / Align*
(menu 1126) picks what Read does (`70:0b16` → plate read type 1, 2 or 3).

- **Diagnostic** (`70:0f6c`): drains the input, writes the raw bytes `!ADC_DIAG<CR>` and reads
  **one** `>`-terminated block (first two characters dropped, `68:3d87`) with **no** status block
  before it. The parser (`70:135c`) fixes the layout exactly **[code]**:

  ```
  line 1        header, ignored
  lines 2..17   two lines per detector channel 1..8 (= plate row):
                  2-char label + values for columns 1,3,5,7 (widths 8,7,9,9)
                  2-char label + values for columns 2,4,6,8 (widths 8,7,9,9)
  lines 18..23  label of 2,2,2,2,3,3 chars + three values (widths 8,8,7)
                -> rows 1..6, columns 10..12
  ```

  Widths come from `DS:0e7c` (`2 8 8 7 7 9 9 9 9`) and `DS:0e8e` (`2 2 2 2 3 3`); each field
  goes through `strtod` (so a non-number reads as 0) and is stored as one plate read. Column 9
  and rows 7–8 of columns 10–12 are never filled: the reply is a table of eight values per
  detector channel plus 18 other figures, drawn on the plate grid. What the values mean is
  **[unknown]**.
- **Align** (`70:0cc8`): sends `CALIGN` with a 6000-tick (96 s) timeout, reads the reply, keeps
  the **second space-separated word** as a number (`strtod`) and stores it as a single value.
  **[code]** What the reader does is **[unknown]**; the name and the later models' error 505
  "NVRAM stage offset error" suggest it adjusts stored alignment, so OD Studio puts it behind an
  explicit acknowledgement.
- The original's built-in simulator does not know either command; OD Studio's simulator
  answers with invented text so the feature can be tried.

## The built-in simulator

`CSFlashSim` (segment 69) replaces the wire functions of `CSFlashInterface` with an in-memory
instrument. `src/odstudio/simulator.py` is a port of it. Details worth knowing **[sim]**:

- Power-on state (`69:0746`): wavelength 405, strip 1–12, endpoint mode, incubator off, drawer
  open, baud 19200, filters 260 and 652.
- `READ` produces all data at once. `QUEUE` never reports more than 3. `STATUS` always says
  `MEAS`.
- `BEEP BURNIN CALIBRATE DIAG SHAKE RETRY STEP STTY VOLUME` log error 4 but still answer `ok`
  (`69:13bc`). `AT` answers `fail 4`.
- Simulated absorbance, endpoint and kinetic (`69:208b`):
  `(exp((column + 2·row + read) / 10) + wavelength_index) / 10`, all indices 0-based.
- Wavelengths 700–706 nm select test patterns: negated, falling 0.02 per read, both, a moving
  2.0 well, a moving 0.0 well, a sine, and ±0.025 noise.
- Simulated spectrum (`69:1f95`): a trapezoid band centred on the nearest multiple of 100 nm,
  shifted 5 nm per row, plus 0.1.
- Temperature (`69:27de`): setpoint (or 21.3 °C when off) ± about 1 °C.

## Open questions

These need a capture from a real SpectraMax 250 (`odstudio -v -p PORT info` prints every byte):

1. **The two characters the host drops** from each data block — probably CR LF.
2. **Exact status text.** `Mid(6, 3)` puts the error number at characters 6–8, which fits
   `FAIL<TAB><space>nnn` or a one-character lead before `FAIL<TAB>nnn`. The driver accepts
   either.
3. **Idle STATUS word** (`IDLE` on later models) and the format of the number after `CAL`.
4. **Meaning of the `F` wavelength prefix** and of the fifth `SHAKE` argument (always 0).
5. **`RESEND` vs `RETRY`**: the host sends `RESEND`, the simulator's table lists `RETRY`.
6. **OPTION last line**: five values expected by the host, four sent by the simulator.

## Regenerating the listings

```bash
python tools/ne.py info binary/SOFTPRO/SPF.EXE
```

```bash
python tools/nedis.py binary/SOFTPRO/SPF.EXE 68 binary/SOFTPRO/ASIPORT2.DLL > seg68.asm
```

```bash
python tools/condense.py seg68.asm tools/names.txt > seg68.txt
```

`nedis.py` needs the `capstone` package. `condense.py` folds argument pushes into calls and
drops compiler boilerplate, which makes the listing about three times shorter.
